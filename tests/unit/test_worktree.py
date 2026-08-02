from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from orchestrator.schemas import Repository
from orchestrator.worktree import GitWorktreeManager


def initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test User"], check=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "initial"], check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
@pytest.mark.asyncio
async def test_worktree_is_isolated_and_reports_changes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    initialize_repository(root)
    now = datetime.now(UTC)
    repository = Repository(
        id=uuid4(),
        name="toss-trader",
        root_path=root,
        default_branch="main",
        verification_config=[],
        created_at=now,
        updated_at=now,
    )
    run_id = uuid4()
    destination = tmp_path / "worktrees" / str(run_id)
    manager = GitWorktreeManager(branch_prefix="orchestrator/run-")
    info = await manager.ensure(repository=repository, run_id=run_id, path=destination)
    (info.path / "README.md").write_text("changed\n", encoding="utf-8")
    (info.path / "new.py").write_text("print('new')\n", encoding="utf-8")

    assert info.path == destination.resolve()
    assert info.branch == f"orchestrator/run-{run_id.hex}"
    assert await manager.ensure(repository=repository, run_id=run_id, path=destination) == info
    assert await manager.changed_files(info.path) == ["README.md", "new.py"]
    diff = await manager.diff(info.path)
    assert "README.md" in diff
    assert "# - new.py" in diff
    assert (root / "README.md").read_text(encoding="utf-8") == "hello\n"
