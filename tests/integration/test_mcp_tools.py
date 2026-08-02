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
        "get_run",
        "cancel_run",
    }
    assert tools["list_repositories"].annotations.readOnlyHint is True
    assert tools["approve_plan"].annotations.destructiveHint is True
    assert tools["cancel_run"].annotations.destructiveHint is True
    assert tools["create_run"].outputSchema is not None

    result = await mcp.call_tool("list_repositories", {})
    assert isinstance(result, tuple)
    content, structured_content = result
    assert isinstance(content, list)
    assert isinstance(structured_content, dict)
    repositories = structured_content["repositories"]
    assert repositories[0]["name"] == "toss-trader"
    assert repositories[0]["verification_commands"] == ["pytest"]
