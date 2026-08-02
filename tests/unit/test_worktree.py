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
    subprocess.run(
        ["git", "init", "-b", "main", str(path)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test User"],
        check=True,
    )
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )


def make_repository(root: Path) -> Repository:
    now = datetime.now(UTC)
    return Repository(
        id=uuid4(),
        name="toss-trader",
        root_path=root,
        default_branch="main",
        verification_config=[],
        created_at=now,
        updated_at=now,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
@pytest.mark.asyncio
async def test_worktree_is_isolated_and_reports_changes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    initialize_repository(root)
    repository = make_repository(root)
    run_id = uuid4()
    destination = tmp_path / "worktrees" / str(run_id)
    manager = GitWorktreeManager(branch_prefix="orchestrator/run-")
    info = await manager.ensure(repository=repository, run_id=run_id, path=destination)
    (info.path / "README.md").write_text("changed\n", encoding="utf-8")
    (info.path / "new.py").write_text("print('new')\n", encoding="utf-8")

    assert info.path == destination.resolve()
    assert info.branch == f"orchestrator/run-{run_id.hex}"
    assert (
        await manager.ensure(repository=repository, run_id=run_id, path=destination)
        == info
    )
    assert await manager.changed_files(info.path) == ["README.md", "new.py"]
    diff = await manager.diff(info.path)
    assert "README.md" in diff
    assert "# - new.py" in diff
    assert (root / "README.md").read_text(encoding="utf-8") == "hello\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
@pytest.mark.asyncio
async def test_agent_retry_amends_and_integration_creates_one_delivery_commit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    initialize_repository(root)
    repository = make_repository(root)
    run_id = uuid4()
    assignment_id = uuid4()
    manager = GitWorktreeManager(branch_prefix="orchestrator/run-")

    agent = await manager.ensure_agent(
        repository=repository,
        run_id=run_id,
        assignment_key="implement-core",
        path=(tmp_path / "worktrees" / "agents" / str(run_id) / "implement-core"),
    )
    source = agent.path / "src"
    source.mkdir()
    (source / "feature.py").write_text("ENABLED = True\n", encoding="utf-8")
    first = await manager.commit_agent_changes(
        agent.path,
        message="chore(agent): complete implement-core",
        run_id=run_id,
        assignment_id=assignment_id,
    )
    (source / "retry.py").write_text("RETRIED = True\n", encoding="utf-8")
    amended = await manager.commit_agent_changes(
        agent.path,
        message="chore(agent): complete implement-core",
        run_id=run_id,
        assignment_id=assignment_id,
    )
    reused = await manager.commit_agent_changes(
        agent.path,
        message="chore(agent): complete implement-core",
        run_id=run_id,
        assignment_id=assignment_id,
    )

    integration = await manager.ensure(
        repository=repository,
        run_id=run_id,
        path=tmp_path / "worktrees" / str(run_id),
    )
    staged = await manager.integrate_commits(integration.path, [amended.sha])
    delivery = await manager.commit_verified_changes(
        integration.path,
        message="feat: integrate agent changes",
        run_id=run_id,
    )

    assert first.sha != amended.sha
    assert amended.changed_files == ["src/feature.py", "src/retry.py"]
    assert reused.sha == amended.sha
    assert reused.reused is True
    assert staged.applied_commits == [amended.sha]
    assert staged.changed_files == ["src/feature.py", "src/retry.py"]
    assert delivery.changed_files == ["src/feature.py", "src/retry.py"]
    assert (integration.path / "src" / "feature.py").exists()
    assert (integration.path / "src" / "retry.py").exists()
    assert not (root / "src" / "feature.py").exists()
    commit_count = subprocess.run(
        ["git", "-C", str(integration.path), "rev-list", "--count", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert commit_count == "2"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
@pytest.mark.asyncio
async def test_delivery_commit_is_local_and_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    initialize_repository(root)
    repository = make_repository(root)
    run_id = uuid4()
    destination = tmp_path / "worktrees" / str(run_id)
    manager = GitWorktreeManager(branch_prefix="orchestrator/run-")
    info = await manager.ensure(repository=repository, run_id=run_id, path=destination)
    (info.path / "feature.py").write_text("ENABLED = True\n", encoding="utf-8")

    commit = await manager.commit_verified_changes(
        info.path,
        message="feat: add delivery workflow",
        run_id=run_id,
    )
    repeated = await manager.commit_verified_changes(
        info.path,
        message="feat: add delivery workflow",
        run_id=run_id,
    )

    assert len(commit.sha) == 40
    assert commit.branch == info.branch
    assert commit.changed_files == ["feature.py"]
    assert commit.reused is False
    assert repeated.sha == commit.sha
    assert repeated.changed_files == ["feature.py"]
    assert repeated.reused is True
    assert not (root / "feature.py").exists()
    assert (
        subprocess.run(
            ["git", "-C", str(info.path), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )
    assert (
        subprocess.run(
            ["git", "-C", str(info.path), "log", "-1", "--format=%s"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "feat: add delivery workflow"
    )
