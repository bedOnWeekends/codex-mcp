from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from orchestrator.settings import Settings

DATABASE_URL = "postgresql+asyncpg://u:p@localhost/test"


def test_api_is_disabled_by_default() -> None:
    settings = Settings(environment="test", database_url=DATABASE_URL)
    assert settings.api_enabled is False


def test_api_requires_long_bearer_key_when_enabled() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(
            environment="test",
            database_url=DATABASE_URL,
            api_enabled=True,
            api_key=SecretStr("short"),
        )


def test_api_prefix_is_normalized() -> None:
    settings = Settings(
        environment="test",
        database_url=DATABASE_URL,
        api_enabled=True,
        api_key=SecretStr("x" * 32),
        api_prefix="/chatgpt/v1/",
    )
    assert settings.api_prefix == "/chatgpt/v1"
