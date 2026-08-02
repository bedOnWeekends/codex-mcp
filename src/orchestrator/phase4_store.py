from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .db_models import (
    ApprovalModel,
    EventModel,
    RepositoryModel,
    RunModel,
    TaskModel,
    TaskResultModel,
)
from .errors import ConcurrentUpdateError, EntityNotFoundError
from .schemas import (
    Approval,
    ApprovalType,
    ModelTier,
    Repository,
    RepositoryCreate,
    Run,
    RunStatus,
    Task,
    TaskKind,
    TaskResult,
    TaskStatus,
    VerificationCommandSpec,
)
from .state_machine import (
    ensure_run_transition,
    ensure_task_transition,
    is_terminal_run_status,
)
from .store import Store


@dataclass(frozen=True, slots=True)
class TaskFailureOutcome:
    task: Task
    run: Run
    retried: bool


@dataclass(frozen=True, slots=True)
class ReviewCompletionOutcome:
    review_task: Task
    run: Run
    next_task: Task | None


class Phase4Store(Store):
    """Phase-4 transactional workflow operations layered on the Phase-3 store."""

    async def create_repository(self, data: RepositoryCreate) -> Repository:
        normalized = data.model_copy(
            update={
                "verification_config": [
                    item.model_dump(mode="json") for item in data.verification_config
                ]
            }
        )
        return await super().create_repository(normalized)  # type: ignore[arg-type]

    async def update_repository_verification_config(
        self,
        name: str,
        commands: list[VerificationCommandSpec],
    ) -> Repository:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(RepositoryModel)
                .where(RepositoryModel.name == name)
                .with_for_update()
            )
            if model is None:
                raise EntityNotFoundError("repository", name)
            model.verification_config = [
                item.model_dump(mode="json") for item in commands
            ]
            await session.flush()
            await session.refresh(model)
            return self._repository(model)

    async def approve_plan_and_queue_implementation(
        self,
        run_id: UUID,
        *,
        expected_version: int,
        notes: str | None,
        instruction: str,
        model_tier: ModelTier,
        max_attempts: int,
        worktree_path: Path,
    ) -> tuple[Run, Task, Approval]:
        async with self._session_factory.begin() as session:
            run_model = await self._locked_run(session, run_id)
            self._check_version(run_model, expected_version)
            ensure_run_transition(RunStatus(run_model.status), RunStatus.EXECUTING)
            approval = ApprovalModel(
                run_id=run_id,
                type=ApprovalType.PLAN.value,
                approved=True,
                notes=notes,
                expected_version=expected_version,
            )
            task = self._queued_task(
                run_id=run_id,
                kind=TaskKind.IMPLEMENT,
                instruction=instruction,
                model_tier=model_tier,
                max_attempts=max_attempts,
                priority=90,
                worktree_path=worktree_path,
            )
            session.add_all([approval, task])
            await session.flush()
            run_model.status = RunStatus.EXECUTING.value
            run_model.current_task_id = task.id
            run_model.version += 1
            self._event(
                session,
                run_id=run_id,
                task_id=task.id,
                event_type="run.plan_approved",
                payload={"approval_notes": notes},
            )
            await session.flush()
            await session.refresh(run_model)
            await session.refresh(task)
            await session.refresh(approval)
            return (
                self._run(run_model),
                self._task(task),
                self._approval(approval),
            )

    async def list_task_results_for_run(self, run_id: UUID) -> list[TaskResult]:
        async with self._session_factory() as session:
            if await session.get(RunModel, run_id) is None:
                raise EntityNotFoundError("run", run_id)
            models = list(
                await session.scalars(
                    select(TaskResultModel)
                    .join(TaskModel, TaskModel.id == TaskResultModel.task_id)
                    .where(TaskModel.run_id == run_id)
                    .order_by(TaskResultModel.created_at.asc())
                )
            )
            return [self._task_result(model) for model in models]

    async def claim_next_task(self) -> Task | None:
        active = [
            RunStatus.PLANNING.value,
            RunStatus.EXECUTING.value,
            RunStatus.VERIFYING.value,
        ]
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(TaskModel)
                .join(RunModel, RunModel.id == TaskModel.run_id)
                .where(
                    TaskModel.status == TaskStatus.QUEUED.value,
                    TaskModel.attempt < TaskModel.max_attempts,
                    RunModel.status.in_(active),
                )
                .order_by(TaskModel.priority.desc(), TaskModel.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if model is None:
                return None
            ensure_task_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
            model.status = TaskStatus.RUNNING.value
            model.attempt += 1
            model.started_at = datetime.now(UTC)
            model.completed_at = None
            await session.flush()
            await session.refresh(model)
            return self._task(model)

    async def complete_plan_task(
        self,
        task_id: UUID,
        *,
        summary: str,
        codex_thread_id: str | None,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: Decimal,
    ) -> tuple[Task, Run, TaskResult]:
        self._validate_usage(input_tokens, output_tokens, estimated_cost_usd)
        async with self._session_factory.begin() as session:
            task = await self._locked_task(session, task_id)
            if TaskKind(task.kind) is not TaskKind.PLAN:
                raise ValueError("complete_plan_task requires a PLAN task")
            self._complete_task(
                task,
                codex_thread_id=codex_thread_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost_usd,
            )
            result = self._result(task_id, summary, True)
            session.add(result)
            run = await self._locked_run(session, task.run_id)
            ensure_run_transition(
                RunStatus(run.status), RunStatus.AWAITING_PLAN_APPROVAL
            )
            run.status = RunStatus.AWAITING_PLAN_APPROVAL.value
            run.plan = summary
            run.current_task_id = task_id
            run.spent_cost_usd += estimated_cost_usd
            run.version += 1
            self._event(
                session,
                run_id=run.id,
                task_id=task_id,
                event_type="task.plan_completed",
                payload={},
            )
            await session.flush()
            await session.refresh(task)
            await session.refresh(run)
            await session.refresh(result)
            return self._task(task), self._run(run), self._task_result(result)

    async def complete_write_task_and_queue_review(
        self,
        task_id: UUID,
        *,
        summary: str,
        changed_files: list[str],
        codex_thread_id: str | None,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: Decimal,
        review_instruction: str,
        review_max_attempts: int,
    ) -> tuple[Task, Task, Run, TaskResult]:
        self._validate_usage(input_tokens, output_tokens, estimated_cost_usd)
        async with self._session_factory.begin() as session:
            task = await self._locked_task(session, task_id)
            if TaskKind(task.kind) not in {TaskKind.IMPLEMENT, TaskKind.FIX}:
                raise ValueError("write completion requires IMPLEMENT or FIX")
            if not task.worktree_path:
                raise ValueError("write task has no worktree path")
            self._complete_task(
                task,
                codex_thread_id=codex_thread_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost_usd,
            )
            result = self._result(
                task_id,
                summary,
                True,
                changed_files=changed_files,
            )
            review = self._queued_task(
                run_id=task.run_id,
                kind=TaskKind.REVIEW,
                instruction=review_instruction,
                model_tier=ModelTier.CRITICAL,
                max_attempts=review_max_attempts,
                priority=80,
                worktree_path=Path(task.worktree_path),
                codex_thread_id=task.codex_thread_id,
            )
            session.add_all([result, review])
            await session.flush()
            run = await self._locked_run(session, task.run_id)
            ensure_run_transition(RunStatus(run.status), RunStatus.VERIFYING)
            run.status = RunStatus.VERIFYING.value
            run.current_task_id = review.id
            run.spent_cost_usd += estimated_cost_usd
            run.version += 1
            self._event(
                session,
                run_id=run.id,
                task_id=task_id,
                event_type="task.write_completed",
                payload={"review_task_id": str(review.id)},
            )
            await session.flush()
            await session.refresh(task)
            await session.refresh(review)
            await session.refresh(run)
            await session.refresh(result)
            return (
                self._task(task),
                self._task(review),
                self._run(run),
                self._task_result(result),
            )

    async def complete_review_task(
        self,
        task_id: UUID,
        *,
        success: bool,
        summary: str,
        commands_run: list[str],
        fix_instruction: str,
        max_fix_cycles: int,
        fix_max_attempts: int,
    ) -> ReviewCompletionOutcome:
        async with self._session_factory.begin() as session:
            review = await self._locked_task(session, task_id)
            if TaskKind(review.kind) is not TaskKind.REVIEW:
                raise ValueError("complete_review_task requires a REVIEW task")
            if not review.worktree_path:
                raise ValueError("review task has no worktree path")
            self._complete_task(
                review,
                codex_thread_id=None,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=Decimal("0"),
            )
            session.add(
                self._result(
                    task_id,
                    summary,
                    success,
                    commands_run=commands_run,
                )
            )
            run = await self._locked_run(session, review.run_id)
            current = RunStatus(run.status)
            next_task: TaskModel | None = None
            if success:
                ensure_run_transition(current, RunStatus.COMPLETED)
                run.status = RunStatus.COMPLETED.value
                run.current_task_id = task_id
                event_type = "run.verification_passed"
            else:
                fix_count = int(
                    await session.scalar(
                        select(func.count(TaskModel.id)).where(
                            TaskModel.run_id == run.id,
                            TaskModel.kind == TaskKind.FIX.value,
                        )
                    )
                    or 0
                )
                if fix_count < max_fix_cycles:
                    ensure_run_transition(current, RunStatus.EXECUTING)
                    next_task = self._queued_task(
                        run_id=run.id,
                        kind=TaskKind.FIX,
                        instruction=fix_instruction,
                        model_tier=ModelTier.CRITICAL,
                        max_attempts=fix_max_attempts,
                        priority=90,
                        worktree_path=Path(review.worktree_path),
                        codex_thread_id=review.codex_thread_id,
                    )
                    session.add(next_task)
                    await session.flush()
                    run.status = RunStatus.EXECUTING.value
                    run.current_task_id = next_task.id
                    event_type = "run.fix_queued"
                else:
                    ensure_run_transition(current, RunStatus.AWAITING_REVISION)
                    run.status = RunStatus.AWAITING_REVISION.value
                    event_type = "run.awaiting_revision"
            run.version += 1
            self._event(
                session,
                run_id=run.id,
                task_id=task_id,
                event_type=event_type,
                payload={"verification_success": success},
            )
            await session.flush()
            await session.refresh(review)
            await session.refresh(run)
            if next_task is not None:
                await session.refresh(next_task)
            return ReviewCompletionOutcome(
                review_task=self._task(review),
                run=self._run(run),
                next_task=self._task(next_task) if next_task is not None else None,
            )

    async def fail_or_retry_task(
        self,
        task_id: UUID,
        *,
        error_summary: str,
    ) -> TaskFailureOutcome:
        async with self._session_factory.begin() as session:
            task = await self._locked_task(session, task_id)
            run = await self._locked_run(session, task.run_id)
            task_status = TaskStatus(task.status)
            run_status = RunStatus(run.status)
            if task_status is TaskStatus.CANCELED or run_status is RunStatus.CANCELED:
                return TaskFailureOutcome(self._task(task), self._run(run), False)
            ensure_task_transition(task_status, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED.value
            task.completed_at = datetime.now(UTC)
            retried = task.attempt < task.max_attempts
            if retried:
                ensure_task_transition(TaskStatus.FAILED, TaskStatus.QUEUED)
                task.status = TaskStatus.QUEUED.value
                task.started_at = None
                task.completed_at = None
                event_type = "task.retry_queued"
            else:
                session.add(self._result(task_id, error_summary, False))
                if not is_terminal_run_status(run_status):
                    ensure_run_transition(run_status, RunStatus.FAILED)
                    run.status = RunStatus.FAILED.value
                event_type = "task.exhausted"
            run.current_task_id = task_id
            run.version += 1
            self._event(
                session,
                run_id=run.id,
                task_id=task_id,
                event_type=event_type,
                payload={"error": error_summary},
            )
            await session.flush()
            await session.refresh(task)
            await session.refresh(run)
            return TaskFailureOutcome(self._task(task), self._run(run), retried)

    async def _locked_run(self, session: AsyncSession, run_id: UUID) -> RunModel:
        model = await session.scalar(
            select(RunModel).where(RunModel.id == run_id).with_for_update()
        )
        if model is None:
            raise EntityNotFoundError("run", run_id)
        return model

    async def _locked_task(self, session: AsyncSession, task_id: UUID) -> TaskModel:
        model = await session.scalar(
            select(TaskModel).where(TaskModel.id == task_id).with_for_update()
        )
        if model is None:
            raise EntityNotFoundError("task", task_id)
        return model

    @staticmethod
    def _check_version(model: RunModel, expected: int) -> None:
        if model.version != expected:
            raise ConcurrentUpdateError("run", model.id, expected, model.version)

    @staticmethod
    def _queued_task(
        *,
        run_id: UUID,
        kind: TaskKind,
        instruction: str,
        model_tier: ModelTier,
        max_attempts: int,
        priority: int,
        worktree_path: Path | None = None,
        codex_thread_id: str | None = None,
    ) -> TaskModel:
        ensure_task_transition(TaskStatus.CREATED, TaskStatus.QUEUED)
        return TaskModel(
            run_id=run_id,
            kind=kind.value,
            status=TaskStatus.QUEUED.value,
            model_tier=model_tier.value,
            instruction=instruction,
            max_attempts=max_attempts,
            priority=priority,
            worktree_path=str(worktree_path) if worktree_path else None,
            codex_thread_id=codex_thread_id,
        )

    @staticmethod
    def _result(
        task_id: UUID,
        summary: str,
        success: bool,
        *,
        changed_files: list[str] | None = None,
        commands_run: list[str] | None = None,
    ) -> TaskResultModel:
        return TaskResultModel(
            task_id=task_id,
            summary=summary,
            success=success,
            changed_files=changed_files or [],
            commands_run=commands_run or [],
        )

    @staticmethod
    def _complete_task(
        model: TaskModel,
        *,
        codex_thread_id: str | None,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: Decimal,
    ) -> None:
        ensure_task_transition(TaskStatus(model.status), TaskStatus.COMPLETED)
        model.status = TaskStatus.COMPLETED.value
        if codex_thread_id is not None:
            model.codex_thread_id = codex_thread_id
        model.input_tokens += input_tokens
        model.output_tokens += output_tokens
        model.estimated_cost_usd += estimated_cost_usd
        model.completed_at = datetime.now(UTC)

    @staticmethod
    def _validate_usage(
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: Decimal,
    ) -> None:
        if input_tokens < 0 or output_tokens < 0 or estimated_cost_usd < 0:
            raise ValueError("usage values must be non-negative")

    @staticmethod
    def _event(
        session: AsyncSession,
        *,
        run_id: UUID,
        task_id: UUID | None,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        session.add(
            EventModel(
                run_id=run_id,
                task_id=task_id,
                event_type=event_type,
                payload=payload,
            )
        )
