from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from orchestrator.codex_client import CodexRunResult
from orchestrator.codex_worker import CodexWorker
from orchestrator.phase5_store import Phase5Store
from orchestrator.schemas import (
    ModelTier,
    Repository,
    RiskLevel,
    Run,
    RunStatus,
    Task,
    TaskKind,
    TaskStatus,
)
from orchestrator.settings import Settings
from orchestrator.verification import CommandResult, VerificationResult
from orchestrator.worktree import DeliveryCommit, WorktreeInfo


class StaticClient:
    def __init__(self, result: CodexRunResult | Exception) -> None:
        self.result = result

    async def run(
        self,
        *,
        prompt: str,
        cwd: Path,
        thread_id: str | None = None,
    ) -> CodexRunResult:
        del prompt, cwd, thread_id
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_objects(
    tmp_path: Path,
    *,
    kind: TaskKind,
    run_status: RunStatus,
) -> tuple[Repository, Run, Task]:
    now = datetime.now(UTC)
    repository = Repository(
        id=uuid4(),
        name="toss-trader",
        root_path=tmp_path / "repo",
        default_branch="main",
        verification_config=[],
        created_at=now,
        updated_at=now,
    )
    run = Run(
        id=uuid4(),
        repository_id=repository.id,
        goal="Implement quote lookup",
        constraints=[],
        risk_level=RiskLevel.NORMAL,
        max_cost_usd=Decimal("3"),
        spent_cost_usd=Decimal("0"),
        plan="Approved plan",
        status=run_status,
        version=3,
        created_at=now,
        updated_at=now,
    )
    worktree_path = None if kind is TaskKind.PLAN else tmp_path / "worktree"
    task = Task(
        id=uuid4(),
        run_id=run.id,
        kind=kind,
        instruction="Do the task",
        model_tier=ModelTier.DEFAULT,
        max_attempts=2,
        priority=100,
        worktree_path=worktree_path,
        status=TaskStatus.RUNNING,
        attempt=1,
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=Decimal("0"),
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    return repository, run, task


def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
        runtime_dir=tmp_path / "runtime",
        max_parallel_workers=1,
    )


@pytest.mark.asyncio
async def test_plan_task_is_completed_without_real_codex_usage(tmp_path: Path) -> None:
    repository, run, task = make_objects(
        tmp_path,
        kind=TaskKind.PLAN,
        run_status=RunStatus.PLANNING,
    )
    store = AsyncMock(spec=Phase5Store)
    store.claim_next_task.return_value = task
    store.get_run.return_value = run
    store.get_repository.return_value = repository
    client = StaticClient(CodexRunResult(thread_id="thread-1", text="plan"))
    worker = CodexWorker(
        store,
        settings(tmp_path),
        client_factory=lambda _task, _run: client,
    )
    assert await worker.process_one() is True
    store.complete_plan_task.assert_awaited_once()
    store.fail_or_retry_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_retries_failure_without_terminating_loop(tmp_path: Path) -> None:
    repository, run, task = make_objects(
        tmp_path,
        kind=TaskKind.PLAN,
        run_status=RunStatus.PLANNING,
    )
    store = AsyncMock(spec=Phase5Store)
    store.claim_next_task.return_value = task
    store.get_run.return_value = run
    store.get_repository.return_value = repository
    worker = CodexWorker(
        store,
        settings(tmp_path),
        client_factory=lambda _task, _run: StaticClient(RuntimeError("boom")),
    )
    assert await worker.process_one() is True
    store.fail_or_retry_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_task_runs_verification_and_completes_review(
    tmp_path: Path,
) -> None:
    repository, run, task = make_objects(
        tmp_path,
        kind=TaskKind.REVIEW,
        run_status=RunStatus.VERIFYING,
    )
    assert task.worktree_path is not None
    task.worktree_path.mkdir(parents=True)
    store = AsyncMock(spec=Phase5Store)
    store.claim_next_task.return_value = task
    store.get_run.return_value = run
    store.get_repository.return_value = repository
    worktrees = AsyncMock()
    worktrees.ensure.return_value = WorktreeInfo(
        path=task.worktree_path,
        branch="orchestrator/run-test",
    )
    verifier = AsyncMock()
    verifier.run.return_value = VerificationResult(
        commands=[
            CommandResult(
                name="git diff check",
                command=["git", "diff", "--check"],
                returncode=0,
                output="",
            )
        ]
    )
    worker = CodexWorker(
        store,
        settings(tmp_path),
        worktrees=worktrees,
        verifier=verifier,
    )
    assert await worker.process_one() is True
    store.complete_review_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_delivery_reverifies_and_creates_local_commit(tmp_path: Path) -> None:
    repository, run, task = make_objects(
        tmp_path,
        kind=TaskKind.DELIVER,
        run_status=RunStatus.DELIVERING,
    )
    assert task.worktree_path is not None
    task.worktree_path.mkdir(parents=True)
    task = task.model_copy(update={"instruction": "feat: add quote lookup"})
    store = AsyncMock(spec=Phase5Store)
    store.claim_next_task.return_value = task
    store.get_run.return_value = run
    store.get_repository.return_value = repository
    worktrees = AsyncMock()
    worktrees.ensure.return_value = WorktreeInfo(
        path=task.worktree_path,
        branch="orchestrator/run-test",
    )
    worktrees.snapshot.side_effect = ["snapshot", "snapshot"]
    worktrees.commit_verified_changes.return_value = DeliveryCommit(
        sha="a" * 40,
        branch="orchestrator/run-test",
        changed_files=["src/quote.py"],
    )
    verifier = AsyncMock()
    verifier.run.return_value = VerificationResult(
        commands=[
            CommandResult(
                name="git diff check",
                command=["git", "diff", "--check"],
                returncode=0,
                output="",
            )
        ]
    )
    worker = CodexWorker(
        store,
        settings(tmp_path),
        worktrees=worktrees,
        verifier=verifier,
    )
    assert await worker.process_one() is True
    worktrees.commit_verified_changes.assert_awaited_once_with(
        task.worktree_path,
        message="feat: add quote lookup",
        run_id=run.id,
    )
    store.complete_delivery_task.assert_awaited_once()
    store.fail_or_retry_task.assert_not_awaited()
