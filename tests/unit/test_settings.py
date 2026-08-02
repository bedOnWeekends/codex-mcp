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


def test_fake_codex_is_safe_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ORCH_CODEX_MODE", raising=False)
    monkeypatch.delenv("ORCH_CODEX_APPROVAL_POLICY", raising=False)
    monkeypatch.delenv("ORCH_CODEX_SANDBOX_MODE", raising=False)

    settings = Settings.model_validate({"runtime_dir": tmp_path / "runtime"})

    assert settings.codex_mode == "fake"
    assert settings.codex_approval_policy == "deny_all"
    assert settings.codex_sandbox_mode == "workspace-write"


def test_legacy_never_approval_policy_is_normalized(tmp_path: Path) -> None:
    settings = Settings.model_validate(
        {
            "runtime_dir": tmp_path / "runtime",
            "codex_approval_policy": "never",
        }
    )
    assert settings.codex_approval_policy == "deny_all"
