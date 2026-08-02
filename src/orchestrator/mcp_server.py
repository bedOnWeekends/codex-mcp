from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from .control_service import RunControlService
from .database import Database, create_database
from .mcp_tools import register_mcp_tools
from .phase7_store import Phase7Store
from .settings import Settings

SERVER_INSTRUCTIONS = """
Use list_repositories before create_run. create_run queues a read-only planning task.
Use get_run for the authoritative version, cost, task state, and agent assignments.
Only call approve_plan after awaiting_plan_approval. Approval queues a bounded low-cost
scout that defaults to one Terra implementer and selects two or three parallel
implementers only for proven non-overlapping scopes. Sol review is conditional on risk,
low confidence, or retry. Agents use isolated worktrees and compact structured
handoffs. Model calls are blocked by run token and cost budgets. Integration and
registered verification are deterministic. Delivery and publication remain separately
approved. The orchestrator never force-pushes, merges, deploys, or trades. All approvals
and cancel_run require the latest run version. Workers execute queued work outside the
MCP server process.
""".strip()


@dataclass(slots=True)
class OrchestratorApplication:
    settings: Settings
    database: Database
    store: Phase7Store
    service: RunControlService
    mcp: FastMCP[Any]
    asgi_app: Starlette


def build_application(settings: Settings) -> OrchestratorApplication:
    settings.ensure_runtime_directories()
    database = create_database(settings)
    store = Phase7Store(database.session_factory)
    service = RunControlService(store, settings)
    mcp: FastMCP[Any] = FastMCP(
        name=settings.app_name,
        instructions=SERVER_INSTRUCTIONS,
        debug=settings.environment == "development",
        log_level=cast(
            Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            settings.log_level.upper(),
        ),
        host=settings.server_host,
        port=settings.server_port,
        streamable_http_path=settings.mcp_path,
        json_response=settings.mcp_json_response,
        stateless_http=settings.mcp_stateless_http,
    )
    register_mcp_tools(mcp, service)

    @mcp.custom_route("/health/live", methods=["GET"], include_in_schema=False)
    async def health_live(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": settings.app_name,
                "environment": settings.environment,
            }
        )

    @mcp.custom_route("/health/ready", methods=["GET"], include_in_schema=False)
    async def health_ready(_: Request) -> JSONResponse:
        database_ready = await database.healthcheck()
        return JSONResponse(
            {
                "status": "ready" if database_ready else "not_ready",
                "database": database_ready,
            },
            status_code=200 if database_ready else 503,
        )

    app = mcp.streamable_http_app()
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(starlette_app: Starlette) -> AsyncIterator[None]:
        settings.ensure_runtime_directories()
        try:
            async with original_lifespan(starlette_app):
                yield
        finally:
            await database.close()

    app.router.lifespan_context = lifespan
    return OrchestratorApplication(
        settings=settings,
        database=database,
        store=store,
        service=service,
        mcp=mcp,
        asgi_app=app,
    )
