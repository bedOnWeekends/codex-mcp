from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from .automation import AutomationCoordinator, AutoPrDriver
from .automation_store import AutomationStore
from .control_service import RunControlService
from .database import Database, create_database
from .mcp_tools import register_mcp_tools
from .phase7_store import Phase7Store
from .rest_api import build_api_router
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
    automation_store: AutomationStore
    automation_coordinator: AutomationCoordinator
    auto_pr_driver: AutoPrDriver
    mcp: FastMCP[Any]
    asgi_app: FastAPI


def build_application(settings: Settings) -> OrchestratorApplication:
    settings.ensure_runtime_directories()
    database = create_database(settings)
    store = Phase7Store(database.session_factory)
    service = RunControlService(store, settings)
    automation_store = AutomationStore(database.session_factory)
    automation_coordinator = AutomationCoordinator(
        automation_store,
        service,
        settings,
    )
    auto_pr_driver = AutoPrDriver(automation_store, service, settings)

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
                "rest_api": settings.api_enabled,
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

    mcp_app = mcp.streamable_http_app()
    mcp_lifespan = mcp_app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        settings.ensure_runtime_directories()
        try:
            async with mcp_lifespan(mcp_app):
                if settings.api_enabled:
                    await auto_pr_driver.start()
                try:
                    yield
                finally:
                    await auto_pr_driver.stop()
        finally:
            await database.close()

    app = FastAPI(
        title="Codex Orchestrator API",
        version="0.8.0",
        description=(
            "Authenticated API for starting bounded auto_pr orchestration runs. "
            "The API may create Draft pull requests but never merges them."
        ),
        docs_url=(
            f"{settings.api_prefix}/docs"
            if settings.api_enabled and settings.api_docs_enabled
            else None
        ),
        redoc_url=None,
        openapi_url=(f"{settings.api_prefix}/openapi.json" if settings.api_enabled else None),
        lifespan=lifespan,
    )
    if settings.api_enabled:
        app.include_router(
            build_api_router(settings, automation_coordinator),
            prefix=settings.api_prefix,
        )

        @app.middleware("http")
        async def protect_mcp_endpoint(request: Request, call_next: Any) -> Any:
            if request.url.path == settings.mcp_path or request.url.path.startswith(
                f"{settings.mcp_path}/"
            ):
                authorization = request.headers.get("Authorization", "")
                scheme, _, supplied = authorization.partition(" ")
                expected = (
                    settings.api_key.get_secret_value() if settings.api_key else ""
                )
                if (
                    scheme.lower() != "bearer"
                    or not supplied
                    or not secrets.compare_digest(supplied, expected)
                ):
                    return JSONResponse(
                        {"detail": "Invalid or missing bearer token."},
                        status_code=401,
                        headers={"WWW-Authenticate": "Bearer"},
                    )
            return await call_next(request)

    app.mount("/", mcp_app)

    return OrchestratorApplication(
        settings=settings,
        database=database,
        store=store,
        service=service,
        automation_store=automation_store,
        automation_coordinator=automation_coordinator,
        auto_pr_driver=auto_pr_driver,
        mcp=mcp,
        asgi_app=app,
    )
