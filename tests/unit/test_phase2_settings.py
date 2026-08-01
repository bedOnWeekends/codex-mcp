from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.settings import Settings


def test_mcp_path_is_normalized() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
        mcp_path="/control/mcp/",
    )
    assert settings.mcp_path == "/control/mcp"


def test_mcp_path_must_be_absolute() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            database_url="postgresql+asyncpg://u:p@localhost/test",
            mcp_path="mcp",
        )
