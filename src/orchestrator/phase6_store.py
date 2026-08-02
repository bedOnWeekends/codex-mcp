from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from .db_models import ApprovalModel, EventModel, RunModel, TaskModel
from .github_publisher import PublishResult, PublishTaskPayload
from .phase5_store import DeliveryCompletionOutcome, Phase5Store
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
class PublishCompletionOutcome:
    publish_task: Task
    run: Run
    result: TaskResult


class Phase6Store(Phase5Store):
    """Phase-6 publication approval and GitHub pull-request workflow."""

    async def claim_next_task(self) -> Task | None:
        active = [
            RunStatus.PLANNING.value,
            RunStatus.EXECUTING.value,
            RunStatus.VERIFYING.value,
            RunStatus.DELIVERING.value,
            RunStatus.PUBLISHING.value,
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

    async def complete_delivery_task(
        self,
        task_id: UUID,
        *,
        commit_sha: str | None,
        changed_files: list[str],
        commands_run: list[str],
    ) -> DeliveryCompletionOutcome:
        if commit_sha is not None and (
            len(commit_sha) != 40
            or any(
                character not in "0123456789abcdef" for character in commit_sha.lower()
            )
        ):
            raise ValueError("commit_sha must be a 40-character hexadecimal Git SHA")
        summary = (
            f"Created verified local commit {commit_sha}."
            if commit_sha is not None
            else "Verified fake-mode delivery completed with no file changes."
        )
        event_type = (
            "run.delivery_committed" if commit_sha is not None else "run.delivery_noop"
        )
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
                summary,
                True,
                changed_files=changed_files,
                commands_run=commands_run,
            )
            session.add(result)
            run = await self._locked_run(session, task.run_id)
            ensure_run_transition(
                RunStatus(run.status),
                RunStatus.AWAITING_PUBLISH_APPROVAL,
            )
            run.status = RunStatus.AWAITING_PUBLISH_APPROVAL.value
            run.current_task_id = task_id
            run.version += 1
            self._event(
                session,
                run_id=run.id,
                task_id=task_id,
                event_type=event_type,
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

    async def approve_publish_and_queue_task(
        self,
        run_id: UUID,
        *,
        expected_version: int,
        title: str,
        body: str,
        draft: bool,
        notes: str | None,
        max_attempts: int,
        worktree_path: Path,
        allow_noop: bool,
    ) -> tuple[Run, Task, Approval]:
        async with self._session_factory.begin() as session:
            run_model = await self._locked_run(session, run_id)
            self._check_version(run_model, expected_version)
            ensure_run_transition(
                RunStatus(run_model.status),
                RunStatus.PUBLISHING,
            )
            delivery_event = await session.scalar(
                select(EventModel)
                .where(
                    EventModel.run_id == run_id,
                    EventModel.event_type.in_(
                        ["run.delivery_committed", "run.delivery_noop"]
                    ),
                )
                .order_by(EventModel.created_at.desc())
                .limit(1)
            )
            if delivery_event is None:
                raise ValueError("run has no completed delivery to publish")
            raw_commit_sha = delivery_event.payload.get("commit_sha")
            commit_sha = raw_commit_sha if isinstance(raw_commit_sha, str) else None
            if commit_sha is None and not allow_noop:
                raise ValueError("live publication requires a delivered local commit")

            instruction = PublishTaskPayload(
                title=title,
                body=body,
                draft=draft,
                expected_commit_sha=commit_sha,
            ).model_dump_json()
            approval = ApprovalModel(
                run_id=run_id,
                type=ApprovalType.PUBLISH.value,
                approved=True,
                notes=notes,
                expected_version=expected_version,
            )
            task = self._queued_task(
                run_id=run_id,
                kind=TaskKind.PUBLISH,
                instruction=instruction,
                model_tier=ModelTier.DEFAULT,
                max_attempts=max_attempts,
                priority=60,
                worktree_path=worktree_path,
            )
            session.add_all([approval, task])
            await session.flush()
            run_model.status = RunStatus.PUBLISHING.value
            run_model.current_task_id = task.id
            run_model.version += 1
            self._event(
                session,
                run_id=run_id,
                task_id=task.id,
                event_type="run.publish_approved",
                payload={
                    "approval_notes": notes,
                    "title": title,
                    "draft": draft,
                    "commit_sha": commit_sha,
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

    async def finish_without_publish(
        self,
        run_id: UUID,
        *,
        expected_version: int,
        notes: str | None,
    ) -> tuple[Run, Approval]:
        async with self._session_factory.begin() as session:
            run_model = await self._locked_run(session, run_id)
            self._check_version(run_model, expected_version)
            ensure_run_transition(
                RunStatus(run_model.status),
                RunStatus.COMPLETED,
            )
            approval = ApprovalModel(
                run_id=run_id,
                type=ApprovalType.PUBLISH.value,
                approved=False,
                notes=notes,
                expected_version=expected_version,
            )
            session.add(approval)
            run_model.status = RunStatus.COMPLETED.value
            run_model.version += 1
            self._event(
                session,
                run_id=run_id,
                task_id=run_model.current_task_id,
                event_type="run.publish_skipped",
                payload={"notes": notes},
            )
            await session.flush()
            await session.refresh(run_model)
            await session.refresh(approval)
            return self._run(run_model), self._approval(approval)

    async def complete_publish_task(
        self,
        task_id: UUID,
        *,
        publication: PublishResult,
    ) -> PublishCompletionOutcome:
        async with self._session_factory.begin() as session:
            task = await self._locked_task(session, task_id)
            if TaskKind(task.kind) is not TaskKind.PUBLISH:
                raise ValueError("complete_publish_task requires a PUBLISH task")
            self._complete_task(
                task,
                codex_thread_id=None,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=Decimal("0"),
            )
            result = self._result(
                task_id,
                publication.summary(),
                True,
                commands_run=publication.commands_run,
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
                event_type=(
                    "run.pull_request_published"
                    if publication.mode == "live"
                    else "run.publish_simulated"
                ),
                payload={
                    "repository": publication.repository,
                    "branch": publication.branch,
                    "commit_sha": publication.commit_sha,
                    "pull_request_url": publication.pull_request_url,
                    "pull_request_number": publication.pull_request_number,
                    "created": publication.created,
                    "mode": publication.mode,
                },
            )
            await session.flush()
            await session.refresh(task)
            await session.refresh(run)
            await session.refresh(result)
            return PublishCompletionOutcome(
                publish_task=self._task(task),
                run=self._run(run),
                result=self._task_result(result),
            )
