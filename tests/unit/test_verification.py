from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.schemas import VerificationCommandSpec
from orchestrator.verification import VerificationRunner


def initialize_repository(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main", str(path)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test User"], check=True
    )
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
@pytest.mark.asyncio
async def test_verification_runs_git_check_and_registered_commands(
    tmp_path: Path,
) -> None:
    initialize_repository(tmp_path)
    result = await VerificationRunner(default_timeout_seconds=5).run(
        cwd=tmp_path,
        configured_commands=[
            VerificationCommandSpec(
                name="python smoke",
                command=[sys.executable, "-c", "print('ok')"],
                timeout_seconds=5,
            )
        ],
    )
    assert result.success is True
    assert len(result.commands) == 2
    assert "ok" in result.summary()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
@pytest.mark.asyncio
async def test_verification_stops_after_first_failure(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    result = await VerificationRunner(default_timeout_seconds=5).run(
        cwd=tmp_path,
        configured_commands=[
            VerificationCommandSpec(
                name="failure",
                command=[sys.executable, "-c", "raise SystemExit(3)"],
                timeout_seconds=5,
            ),
            VerificationCommandSpec(
                name="must not run",
                command=[sys.executable, "-c", "print('unexpected')"],
                timeout_seconds=5,
            ),
        ],
    )
    assert result.success is False
    assert [item.name for item in result.commands] == ["git diff check", "failure"]
