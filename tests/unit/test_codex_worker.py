from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from orchestrator.codex_client import CodexRunResult
from orchestrator.codex_worker import CodexWorker
from orchestrator.errors import NoChangesToCommitError
from orchestrator.github_publisher import PublishResult, PublishTaskPayload
from orchestrator.phase7_store import Phase7Store
from orchestrator.schemas import (
    AgentAssignment,
    AgentAssignmentStatus,
    AgentRole,
    ExecutionMode,
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
from orchestrator.worktree import DeliveryCommit, IntegrationResult, WorktreeInfo


class StaticClient:
    def __init__(self, result: CodexRunResult | Exception) -> None:
        self.result = result

    async def run(
        self,
        *,
        prompt: str,
        cwd: Path,
        thread_id: str | None = None,
        output_schema: dict[str, object] | None = None,
    ) -> CodexRunResult:
        del prompt, cwd, thread_id, output_schema
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class StaticPublisher:
    def __init__(self, result: PublishResult) -> None:
        self.result = result
        self.calls: list[tuple[Repository, UUID, Path, PublishTaskPayload]] = []

    async def publish(
        self,
        *,
        repository: Repository,
        run_id: UUID,
        worktree: Path,
        payload: PublishTaskPayload,
    ) -> PublishResult:
        self.calls.append((repository, run_id, worktree, payload))
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
    worktree_path = (
        None if kind in {TaskKind.PLAN, TaskKind.SUPERVISE} else tmp_path / "worktree"
    )
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


def make_assignment(task: Task, *, role: AgentRole) -> AgentAssignment:
    now = datetime.now(UTC)
    return AgentAssignment(
        id=uuid4(),
        run_id=task.run_id,
        task_id=task.id,
        key="implement-core" if role is AgentRole.IMPLEMENTER else "review-core",
        role=role,
        status=AgentAssignmentStatus.RUNNING,
        instruction="Complete the assigned scope.",
        depends_on=[],
        owned_paths=["src"] if role is AgentRole.IMPLEMENTER else [],
        model_tier=(
            ModelTier.DEFAULT
            if role is AgentRole.IMPLEMENTER
            else ModelTier.CRITICAL
        ),
        worktree_path=task.worktree_path,
        changed_files=[],
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=Decimal("0"),
        started_at=now,
        created_at=now,
        updated_at=now,
    )


def settings(
    tmp_path: Path,
    *,
    codex_mode: Literal["fake", "live"] = "fake",
) -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
        runtime_dir=tmp_path / "runtime",
        max_parallel_workers=1,
        codex_mode=codex_mode,
    )


def successful_verification() -> VerificationResult:
    return VerificationResult(
        commands=[
            CommandResult(
                name="git diff check",
                command=["git", "diff", "--check"],
                returncode=0,
                output="",
            )
        ]
    )


@pytest.mark.asyncio
async def test_plan_task_is_completed_without_real_codex_usage(tmp_path: Path) -> None:
    repository, run, task = make_objects(
        tmp_path,
        kind=TaskKind.PLAN,
        run_status=RunStatus.PLANNING,
    )
    store = AsyncMock(spec=Phase7Store)
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
async def test_fake_supervisor_chooses_single_agent_cheapest_path(
    tmp_path: Path,
) -> None:
    repository, run, task = make_objects(
        tmp_path,
        kind=TaskKind.SUPERVISE,
        run_status=RunStatus.SUPERVISING,
    )
    store = AsyncMock(spec=Phase7Store)
    store.claim_next_task.return_value = task
    store.get_run.return_value = run
    store.get_repository.return_value = repository
    worker = CodexWorker(store, settings(tmp_path))

    assert await worker.process_one() is True
    call = store.complete_supervision_task.await_args
    assert call is not None
    plan = call.kwargs["plan"]
    assert plan.mode is ExecutionMode.SINGLE
    assert [item.role for item in plan.assignments] == [AgentRole.IMPLEMENTER]
    assert call.kwargs["agents_root"] == (
        tmp_path / "runtime" / "worktrees" / "agents" / str(run.id)
    ).resolve()
    store.fail_or_retry_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_implementer_agent_uses_isolated_worktree_and_compact_handoff(
    tmp_path: Path,
) -> None:
    repository, run, task = make_objects(
        tmp_path,
        kind=TaskKind.AGENT,
        run_status=RunStatus.EXECUTING,
    )
    worktree_path = task.worktree_path
    assert worktree_path is not None
    assignment = make_assignment(task, role=AgentRole.IMPLEMENTER)
    store = AsyncMock(spec=Phase7Store)
    store.claim_next_task.return_value = task
    store.get_run.return_value = run
    store.get_repository.return_value = repository
    store.get_agent_assignment_for_task.return_value = assignment
    store.dependency_commits_for_assignment.return_value = []
    store.dependency_context_for_assignment.return_value = []
    worktrees = AsyncMock()
    worktrees.ensure_agent.return_value = WorktreeInfo(
        path=worktree_path,
        branch="orchestrator/run-test/agent-implement-core",
    )
    worktrees.changed_files.return_value = []
    worktrees.diff.return_value = ""
    worktrees.commit_agent_changes.side_effect = NoChangesToCommitError(
        str(worktree_path),
        "agent has no changes to commit",
    )
    worker = CodexWorker(
        store,
        settings(tmp_path, codex_mode="fake"),
        worktrees=worktrees,
        client_factory=lambda _task, _run: StaticClient(
            CodexRunResult(thread_id="agent-thread", text="No changes required.")
        ),
    )

    assert await worker.process_one() is True
    worktrees.ensure_agent.assert_awaited_once_with(
        repository=repository,
        run_id=run.id,
        assignment_key=assignment.key,
        path=worktree_path,
    )
    store.dependency_context_for_assignment.assert_awaited_once_with(
        assignment.id,
        max_summary_chars=1_200,
    )
    store.complete_agent_task.assert_awaited_once_with(
        task.id,
        summary='{"summary":"No changes required.","risks":[],"tests":[]}',
        changed_files=[],
        commit_sha=None,
        codex_thread_id="agent-thread",
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=Decimal("0.000000"),
        integration_worktree_path=(
            tmp_path / "runtime" / "worktrees" / str(run.id)
        ).resolve(),
        max_attempts=2,
    )
    store.fail_or_retry_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_integrator_stages_agent_commits_and_queues_review(tmp_path: Path) -> None:
    repository, run, task = make_objects(
        tmp_path,
        kind=TaskKind.INTEGRATE,
        run_status=RunStatus.INTEGRATING,
    )
    worktree_path = task.worktree_path
    assert worktree_path is not None
    commits = ["a" * 40, "b" * 40]
    store = AsyncMock(spec=Phase7Store)
    store.claim_next_task.return_value = task
    store.get_run.return_value = run
    store.get_repository.return_value = repository
    store.integration_commits.return_value = commits
    worktrees = AsyncMock()
    worktrees.ensure.return_value = WorktreeInfo(
        path=worktree_path,
        branch="orchestrator/run-test",
    )
    worktrees.integrate_commits.return_value = IntegrationResult(
        branch="orchestrator/run-test",
        applied_commits=commits,
        changed_files=["src/a.py", "src/b.py"],
    )
    worker = CodexWorker(store, settings(tmp_path), worktrees=worktrees)

    assert await worker.process_one() is True
    worktrees.integrate_commits.assert_awaited_once_with(worktree_path, commits)
    store.complete_integration_task.assert_awaited_once()
    store.fail_or_retry_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_retries_failure_without_terminating_loop(tmp_path: Path) -> None:
    repository, run, task = make_objects(
        tmp_path,
        kind=TaskKind.PLAN,
        run_status=RunStatus.PLANNING,
    )
    store = AsyncMock(spec=Phase7Store)
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
async def test_live_worker_stops_before_call_when_token_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    repository, run, task = make_objects(
        tmp_path,
        kind=TaskKind.PLAN,
        run_status=RunStatus.PLANNING,
    )
    store = AsyncMock(spec=Phase7Store)
    store.claim_next_task.return_value = task
    store.get_run.return_value = run
    store.get_repository.return_value = repository
    store.total_tokens_for_run.return_value = 250_000
    client = AsyncMock()
    worker = CodexWorker(
        store,
        settings(tmp_path, codex_mode="live"),
        client_factory=lambda _task, _run: client,
    )

    assert await worker.process_one() is True
    client.run.assert_not_awaited()
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
    worktree_path = task.worktree_path
    assert worktree_path is not None
    worktree_path.mkdir(parents=True)
    store = AsyncMock(spec=Phase7Store)
    store.claim_next_task.return_value = task
    store.get_run.return_value = run
    store.get_repository.return_value = repository
    worktrees = AsyncMock()
    worktrees.ensure.return_value = WorktreeInfo(
        path=worktree_path,
        branch="orchestrator/run-test",
    )
    verifier = AsyncMock()
    verifier.run.return_value = successful_verification()
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
    worktree_path = task.worktree_path
    assert worktree_path is not None
    worktree_path.mkdir(parents=True)
    task = task.model_copy(update={"instruction": "feat: add quote lookup"})
    store = AsyncMock(spec=Phase7Store)
    store.claim_next_task.return_value = task
    store.get_run.return_value = run
    store.get_repository.return_value = repository
    worktrees = AsyncMock()
    worktrees.ensure.return_value = WorktreeInfo(
        path=worktree_path,
        branch="orchestrator/run-test",
    )
    worktrees.snapshot.side_effect = ["snapshot", "snapshot"]
    worktrees.commit_verified_changes.return_value = DeliveryCommit(
        sha="a" * 40,
        branch="orchestrator/run-test",
        changed_files=["src/quote.py"],
    )
    verifier = AsyncMock()
    verifier.run.return_value = successful_verification()
    worker = CodexWorker(
        store,
        settings(tmp_path),
        worktrees=worktrees,
        verifier=verifier,
    )
    assert await worker.process_one() is True
    worktrees.commit_verified_changes.assert_awaited_once_with(
        worktree_path,
        message="feat: add quote lookup",
        run_id=run.id,
    )
    store.complete_delivery_task.assert_awaited_once_with(
        task.id,
        commit_sha="a" * 40,
        changed_files=["src/quote.py"],
        commands_run=["git diff --check"],
    )
    store.fail_or_retry_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_fake_delivery_completes_as_verified_noop(tmp_path: Path) -> None:
    repository, run, task = make_objects(
        tmp_path,
        kind=TaskKind.DELIVER,
        run_status=RunStatus.DELIVERING,
    )
    worktree_path = task.worktree_path
    assert worktree_path is not None
    worktree_path.mkdir(parents=True)
    task = task.model_copy(update={"instruction": "test: verify fake delivery"})
    store = AsyncMock(spec=Phase7Store)
    store.claim_next_task.return_value = task
    store.get_run.return_value = run
    store.get_repository.return_value = repository
    worktrees = AsyncMock()
    worktrees.ensure.return_value = WorktreeInfo(
        path=worktree_path,
        branch="orchestrator/run-test",
    )
    worktrees.snapshot.side_effect = ["snapshot", "snapshot"]
    worktrees.commit_verified_changes.side_effect = NoChangesToCommitError(
        str(worktree_path),
        "worktree has no changes to commit",
    )
    verifier = AsyncMock()
    verifier.run.return_value = successful_verification()
    worker = CodexWorker(
        store,
        settings(tmp_path, codex_mode="fake"),
        worktrees=worktrees,
        verifier=verifier,
    )
    assert await worker.process_one() is True
    store.complete_delivery_task.assert_awaited_once_with(
        task.id,
        commit_sha=None,
        changed_files=[],
        commands_run=["git diff --check"],
    )
    store.fail_or_retry_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_task_uses_injected_publisher(tmp_path: Path) -> None:
    repository, run, task = make_objects(
        tmp_path,
        kind=TaskKind.PUBLISH,
        run_status=RunStatus.PUBLISHING,
    )
    worktree_path = task.worktree_path
    assert worktree_path is not None
    worktree_path.mkdir(parents=True)
    payload = PublishTaskPayload(
        title="feat: add quote lookup",
        body="Generated by the orchestrator.",
        draft=True,
        expected_commit_sha="a" * 40,
    )
    task = task.model_copy(update={"instruction": payload.model_dump_json()})
    store = AsyncMock(spec=Phase7Store)
    store.claim_next_task.return_value = task
    store.get_run.return_value = run
    store.get_repository.return_value = repository
    worktrees = AsyncMock()
    worktrees.ensure.return_value = WorktreeInfo(
        path=worktree_path,
        branch="orchestrator/run-test",
    )
    publication = PublishResult(
        mode="fake",
        repository="toss-trader",
        branch="fake/orchestrator-run",
        commit_sha=None,
        pull_request_url=None,
        pull_request_number=None,
        created=False,
        commands_run=[],
    )
    publisher = StaticPublisher(publication)
    worker = CodexWorker(
        store,
        settings(tmp_path),
        worktrees=worktrees,
        publisher=publisher,
    )

    assert await worker.process_one() is True
    assert publisher.calls == [(repository, run.id, worktree_path, payload)]
    store.complete_publish_task.assert_awaited_once_with(
        task.id,
        publication=publication,
    )
    store.fail_or_retry_task.assert_not_awaited()
