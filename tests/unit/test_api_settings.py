from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.settings import Settings


def base_kwargs() -> dict[str, str]:
    return {
        "environment": "test",
        "database_url": "postgresql+asyncpg://u:p@localhost/test",
    }


def test_api_is_disabled_by_default() -> None:
    settings = Settings(**base_kwargs())
    assert settings.api_enabled is False


def test_api_requires_long_bearer_key_when_enabled() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(**base_kwargs(), api_enabled=True, api_key="short")


def test_api_prefix_is_normalized() -> None:
    settings = Settings(
        **base_kwargs(),
        api_enabled=True,
        api_key="x" * 32,
        api_prefix="/chatgpt/v1/",
    )
    assert settings.api_prefix == "/chatgpt/v1"
