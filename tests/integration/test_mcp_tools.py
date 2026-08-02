from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

pytest.importorskip("mcp")

from mcp.server.fastmcp import FastMCP

from orchestrator.control_service import RunControlService
from orchestrator.mcp_schemas import ListRepositoriesOutput, RepositorySummary
from orchestrator.mcp_tools import register_mcp_tools


@pytest.mark.mcp
@pytest.mark.asyncio
async def test_tools_publish_structured_output_and_annotations() -> None:
    service = AsyncMock(spec=RunControlService)
    service.list_repositories.return_value = ListRepositoriesOutput(
        repositories=[
            RepositorySummary(
                id=uuid4(),
                name="toss-trader",
                default_branch="main",
                verification_commands=["pytest"],
            )
        ]
    )
    mcp = FastMCP("test", json_response=True, stateless_http=True)
    register_mcp_tools(mcp, cast(RunControlService, service))
    tools = {tool.name: tool for tool in await mcp.list_tools()}

    assert set(tools) == {
        "list_repositories",
        "create_run",
        "approve_plan",
        "approve_delivery",
        "approve_publish",
        "finish_run",
        "get_run",
        "cancel_run",
    }
    list_annotations = tools["list_repositories"].annotations
    plan_annotations = tools["approve_plan"].annotations
    delivery_annotations = tools["approve_delivery"].annotations
    publish_annotations = tools["approve_publish"].annotations
    finish_annotations = tools["finish_run"].annotations
    cancel_annotations = tools["cancel_run"].annotations
    assert list_annotations is not None
    assert plan_annotations is not None
    assert delivery_annotations is not None
    assert publish_annotations is not None
    assert finish_annotations is not None
    assert cancel_annotations is not None
    assert list_annotations.readOnlyHint is True
    assert plan_annotations.destructiveHint is True
    assert delivery_annotations.destructiveHint is True
    assert publish_annotations.destructiveHint is True
    assert publish_annotations.openWorldHint is True
    assert finish_annotations.destructiveHint is False
    assert cancel_annotations.destructiveHint is True
    assert tools["create_run"].outputSchema is not None
    assert tools["approve_delivery"].outputSchema is not None
    assert tools["approve_publish"].outputSchema is not None

    result = await mcp.call_tool("list_repositories", {})
    assert isinstance(result, tuple)
    content, structured_content = result
    assert isinstance(content, list)
    assert isinstance(structured_content, dict)
    repositories = structured_content["repositories"]
    assert repositories[0]["name"] == "toss-trader"
    assert repositories[0]["verification_commands"] == ["pytest"]
