from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.settings import Settings


def test_runtime_paths_are_derived(tmp_path: Path) -> None:
    settings = Settings.model_validate(
        {
            "runtime_dir": tmp_path / "runtime",
            "database_url": "postgresql+asyncpg://orchestrator:orchestrator@localhost/orchestrator",
        }
    )
    settings.ensure_runtime_directories()
    assert settings.worktrees_dir == (tmp_path / "runtime" / "worktrees").resolve()
    assert settings.artifacts_dir == (tmp_path / "runtime" / "artifacts").resolve()
    assert settings.logs_dir == (tmp_path / "runtime" / "logs").resolve()
    assert settings.worktrees_dir is not None
    assert settings.worktrees_dir.exists()


def test_non_async_postgres_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"database_url": "postgresql://localhost/orchestrator"})


def test_fake_modes_and_adaptive_limits_are_safe_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "ORCH_CODEX_MODE",
        "ORCH_CODEX_APPROVAL_POLICY",
        "ORCH_CODEX_SANDBOX_MODE",
        "ORCH_GITHUB_PUBLISH_MODE",
        "ORCH_GITHUB_TOKEN",
        "ORCH_GITHUB_REMOTE_NAME",
        "ORCH_MAX_AGENTS_PER_RUN",
        "ORCH_MAX_TOKENS_PER_RUN",
        "ORCH_CODEX_MODEL_CHEAP",
        "ORCH_CODEX_MODEL_DEFAULT",
        "ORCH_CODEX_MODEL_CRITICAL",
        "ORCH_CODEX_CACHE_WRITE_MULTIPLIER",
        "ORCH_PROJECTED_CALL_TOKENS_CHEAP",
        "ORCH_PROJECTED_CALL_TOKENS_DEFAULT",
        "ORCH_PROJECTED_CALL_TOKENS_CRITICAL",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.model_validate({"runtime_dir": tmp_path / "runtime"})

    assert settings.codex_mode == "fake"
    assert settings.codex_approval_policy == "deny_all"
    assert settings.codex_sandbox_mode == "workspace-write"
    assert settings.github_publish_mode == "fake"
    assert settings.github_token is None
    assert settings.github_remote_name == "origin"
    assert settings.max_agents_per_run == 4
    assert settings.max_tokens_per_run == 250_000
    assert settings.codex_model_cheap == "gpt-5.6-luna"
    assert settings.codex_model_default == "gpt-5.6-terra"
    assert settings.codex_model_critical == "gpt-5.6-sol"
    assert settings.codex_effort_scout == "low"
    assert settings.codex_effort_default == "high"
    assert settings.codex_effort_critical == "medium"
    assert settings.codex_price_default_input_per_mtok == Decimal("2.50")
    assert settings.codex_cache_write_multiplier == Decimal("1.25")
    assert settings.projected_call_tokens_cheap == 12_000
    assert settings.projected_call_tokens_default == 60_000
    assert settings.projected_call_tokens_critical == 100_000


@pytest.mark.parametrize("value", [0, 1, 5])
def test_agent_limit_is_bounded(tmp_path: Path, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "runtime_dir": tmp_path / "runtime",
                "max_agents_per_run": value,
            }
        )


def test_legacy_never_approval_policy_is_normalized(tmp_path: Path) -> None:
    settings = Settings.model_validate(
        {
            "runtime_dir": tmp_path / "runtime",
            "codex_approval_policy": "never",
        }
    )
    assert settings.codex_approval_policy == "deny_all"


def test_empty_model_name_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "runtime_dir": tmp_path / "runtime",
                "codex_model_default": " ",
            }
        )


def test_non_github_api_url_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "runtime_dir": tmp_path / "runtime",
                "github_api_url": "https://example.com/api",
            }
        )
