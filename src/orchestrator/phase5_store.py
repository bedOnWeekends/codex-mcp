from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select

from .db_models import ApprovalModel, RunModel, TaskModel
from .phase4_store import Phase4Store, ReviewCompletionOutcome
from .schemas import (
    Approval,
    ApprovalType,
    ModelTier,
    Run,
    RunStatus,
    Task,
    TaskKind,
    TaskResult,
    TaskStatus,
)
from .state_machine import ensure_run_transition, ensure_task_transition


@dataclass(frozen=True, slots=True)
class DeliveryCompletionOutcome:
    delivery_task: Task
    run: Run
    result: TaskResult


class Phase5Store(Phase4Store):
    """Phase-5 delivery approval and local commit workflow operations."""

    async def claim_next_task(self) -> Task | None:
        active = [
            RunStatus.PLANNING.value,
            RunStatus.EXECUTING.value,
            RunStatus.VERIFYING.value,
            RunStatus.DELIVERING.value,
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
                ensure_run_transition(
                    current,
                    RunStatus.AWAITING_DELIVERY_APPROVAL,
                )
                run.status = RunStatus.AWAITING_DELIVERY_APPROVAL.value
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

    async def approve_delivery_and_queue_task(
        self,
        run_id: UUID,
        *,
        expected_version: int,
        commit_message: str,
        notes: str | None,
        max_attempts: int,
        worktree_path: Path,
    ) -> tuple[Run, Task, Approval]:
        async with self._session_factory.begin() as session:
            run_model = await self._locked_run(session, run_id)
            self._check_version(run_model, expected_version)
            ensure_run_transition(
                RunStatus(run_model.status),
                RunStatus.DELIVERING,
            )
            approval = ApprovalModel(
                run_id=run_id,
                type=ApprovalType.DELIVERY.value,
                approved=True,
                notes=notes,
                expected_version=expected_version,
            )
            task = self._queued_task(
                run_id=run_id,
                kind=TaskKind.DELIVER,
                instruction=commit_message,
                model_tier=ModelTier.DEFAULT,
                max_attempts=max_attempts,
                priority=70,
                worktree_path=worktree_path,
            )
            session.add_all([approval, task])
            await session.flush()
            run_model.status = RunStatus.DELIVERING.value
            run_model.current_task_id = task.id
            run_model.version += 1
            self._event(
                session,
                run_id=run_id,
                task_id=task.id,
                event_type="run.delivery_approved",
                payload={
                    "approval_notes": notes,
                    "commit_message": commit_message,
                },
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

    async def complete_delivery_task(
        self,
        task_id: UUID,
        *,
        commit_sha: str,
        changed_files: list[str],
        commands_run: list[str],
    ) -> DeliveryCompletionOutcome:
        if len(commit_sha) != 40 or any(
            character not in "0123456789abcdef" for character in commit_sha.lower()
        ):
            raise ValueError("commit_sha must be a 40-character hexadecimal Git SHA")
        async with self._session_factory.begin() as session:
            task = await self._locked_task(session, task_id)
            if TaskKind(task.kind) is not TaskKind.DELIVER:
                raise ValueError("complete_delivery_task requires a DELIVER task")
            self._complete_task(
                task,
                codex_thread_id=None,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=Decimal("0"),
            )
            result = self._result(
                task_id,
                f"Created verified local commit {commit_sha}.",
                True,
                changed_files=changed_files,
                commands_run=commands_run,
            )
            session.add(result)
            run = await self._locked_run(session, task.run_id)
            ensure_run_transition(RunStatus(run.status), RunStatus.COMPLETED)
            run.status = RunStatus.COMPLETED.value
            run.current_task_id = task_id
            run.version += 1
            self._event(
                session,
                run_id=run.id,
                task_id=task_id,
                event_type="run.delivery_committed",
                payload={
                    "commit_sha": commit_sha,
                    "changed_files": changed_files,
                },
            )
            await session.flush()
            await session.refresh(task)
            await session.refresh(run)
            await session.refresh(result)
            return DeliveryCompletionOutcome(
                delivery_task=self._task(task),
                run=self._run(run),
                result=self._task_result(result),
            )
