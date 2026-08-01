from __future__ import annotations

import logging
from decimal import Decimal
from collections.abc import Awaitable
from typing import Any, TypeVar
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import ValidationError

from .control_service import RunControlService
from .errors import OrchestratorError
from .mcp_schemas import (
    CancelRunInput,
    CancelRunOutput,
    CreateRunInput,
    CreateRunOutput,
    GetRunOutput,
    ListRepositoriesOutput,
)
from .schemas import RiskLevel

logger = logging.getLogger(__name__)
T = TypeVar("T")


async def _safe_call(operation: str, awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except (OrchestratorError, ValidationError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected MCP tool failure", extra={"operation": operation})
        raise RuntimeError("Internal orchestrator error.") from exc


def register_mcp_tools(
    mcp: FastMCP[Any],
    service: RunControlService,
) -> None:
    """Register the public control-plane tools on a FastMCP server."""

    @mcp.tool(
        name="list_repositories",
        title="List Registered Repositories",
        description=(
            "List repositories that the local administrator has explicitly registered "
            "for Codex orchestration."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def list_repositories() -> ListRepositoriesOutput:
        return await _safe_call("list_repositories", service.list_repositories())

    @mcp.tool(
        name="create_run",
        title="Create Orchestration Run",
        description=(
            "Create a run for a registered repository and atomically queue a read-only "
            "planning task. This does not start Codex in phase 2."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def create_run(
        repository: str,
        goal: str,
        constraints: list[str] | None = None,
        max_cost_usd: Decimal = Decimal("3.00"),
        risk_level: RiskLevel = RiskLevel.NORMAL,
    ) -> CreateRunOutput:
        request = CreateRunInput(
            repository=repository,
            goal=goal,
            constraints=constraints or [],
            max_cost_usd=max_cost_usd,
            risk_level=risk_level,
        )
        return await _safe_call("create_run", service.create_run(request))

    @mcp.tool(
        name="get_run",
        title="Get Orchestration Run",
        description=(
            "Read the authoritative PostgreSQL state of a run, including its version "
            "and task summaries."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def get_run(run_id: UUID) -> GetRunOutput:
        return await _safe_call("get_run", service.get_run(run_id))

    @mcp.tool(
        name="cancel_run",
        title="Cancel Orchestration Run",
        description=(
            "Cancel a non-terminal run and its active tasks. Pass the latest version "
            "returned by get_run to prevent stale writes."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def cancel_run(
        run_id: UUID,
        expected_version: int,
        reason: str | None = None,
    ) -> CancelRunOutput:
        request = CancelRunInput(
            run_id=run_id,
            expected_version=expected_version,
            reason=reason,
        )
        return await _safe_call("cancel_run", service.cancel_run(request))
