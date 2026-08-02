from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .db_models import (
    ApprovalModel,
    ArtifactModel,
    EventModel,
    RepositoryModel,
    RunModel,
    TaskModel,
    TaskResultModel,
)
from .errors import (
    ConcurrentUpdateError,
    DuplicateEntityError,
    EntityNotFoundError,
    RunNotCancelableError,
)
from .schemas import (
    Approval,
    ApprovalCreate,
    ApprovalType,
    Artifact,
    ArtifactCreate,
    ArtifactKind,
    Event,
    EventCreate,
    ModelTier,
    Repository,
    RepositoryCreate,
    RiskLevel,
    Run,
    RunCreate,
    RunStatus,
    Task,
    TaskCreate,
    TaskKind,
    TaskResult,
    TaskResultCreate,
    TaskStatus,
    VerificationCommandSpec,
)
from .state_machine import (
    ensure_run_transition,
    ensure_task_transition,
    is_terminal_run_status,
)


class Store:
    """Persistence operations and transaction boundaries."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_repository(self, data: RepositoryCreate) -> Repository:
        model = RepositoryModel(
            name=data.name,
            root_path=str(data.root_path),
            default_branch=data.default_branch,
            verification_config=[
                item.model_dump(mode="json") for item in data.verification_config
            ],
        )
        try:
            async with self._session_factory.begin() as session:
                session.add(model)
                await session.flush()
                await session.refresh(model)
        except IntegrityError as exc:
            raise DuplicateEntityError("repository", data.name) from exc
        return self._repository(model)

    async def get_repository(self, repository_id: UUID) -> Repository:
        async with self._session_factory() as session:
            model = await session.get(RepositoryModel, repository_id)
            if model is None:
                raise EntityNotFoundError("repository", repository_id)
            return self._repository(model)

    async def get_repository_by_name(self, name: str) -> Repository:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(RepositoryModel).where(RepositoryModel.name == name)
            )
            if model is None:
                raise EntityNotFoundError("repository", name)
            return self._repository(model)

    async def list_repositories(self) -> list[Repository]:
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    select(RepositoryModel).order_by(RepositoryModel.name.asc())
                )
            )
            return [self._repository(model) for model in models]

    async def create_run(self, data: RunCreate) -> Run:
        model = self._new_run_model(data)
        try:
            async with self._session_factory.begin() as session:
                session.add(model)
                await session.flush()
                await session.refresh(model)
        except IntegrityError as exc:
            raise EntityNotFoundError("repository", data.repository_id) from exc
        return self._run(model)

    async def create_run_with_initial_task(
        self,
        data: RunCreate,
        *,
        plan_instruction: str,
        model_tier: ModelTier,
        max_attempts: int,
    ) -> tuple[Run, Task]:
        """Create a run and queue its first planning task atomically."""
        run_model = self._new_run_model(data)
        try:
            async with self._session_factory.begin() as session:
                session.add(run_model)
                await session.flush()

                task_model = TaskModel(
                    run_id=run_model.id,
                    kind=TaskKind.PLAN.value,
                    status=TaskStatus.CREATED.value,
                    model_tier=model_tier.value,
                    instruction=plan_instruction,
                    max_attempts=max_attempts,
                    priority=100,
                )
                session.add(task_model)
                await session.flush()

                ensure_task_transition(TaskStatus.CREATED, TaskStatus.QUEUED)
                task_model.status = TaskStatus.QUEUED.value

                ensure_run_transition(RunStatus.CREATED, RunStatus.PLANNING)
                run_model.status = RunStatus.PLANNING.value
                run_model.current_task_id = task_model.id
                run_model.version += 1

                session.add(
                    EventModel(
                        run_id=run_model.id,
                        task_id=task_model.id,
                        event_type="run.created",
                        payload={
                            "status": RunStatus.PLANNING.value,
                            "initial_task_kind": TaskKind.PLAN.value,
                        },
                    )
                )
                await session.flush()
                await session.refresh(run_model)
                await session.refresh(task_model)
                return self._run(run_model), self._task(task_model)
        except IntegrityError as exc:
            raise EntityNotFoundError("repository", data.repository_id) from exc

    async def get_run(self, run_id: UUID) -> Run:
        async with self._session_factory() as session:
            model = await session.get(RunModel, run_id)
            if model is None:
                raise EntityNotFoundError("run", run_id)
            return self._run(model)

    async def transition_run(
        self,
        run_id: UUID,
        *,
        expected_version: int,
        target: RunStatus,
        plan: str | None = None,
        current_task_id: UUID | None = None,
    ) -> Run:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(RunModel).where(RunModel.id == run_id).with_for_update()
            )
            if model is None:
                raise EntityNotFoundError("run", run_id)
            if model.version != expected_version:
                raise ConcurrentUpdateError(
                    "run", run_id, expected_version, model.version
                )

            current = RunStatus(model.status)
            ensure_run_transition(current, target)
            model.status = target.value
            model.version += 1
            if plan is not None:
                model.plan = plan
            if current_task_id is not None:
                model.current_task_id = current_task_id
            await session.flush()
            await session.refresh(model)
            return self._run(model)

    async def cancel_run(
        self,
        run_id: UUID,
        *,
        expected_version: int,
        reason: str | None = None,
    ) -> tuple[Run, list[UUID]]:
        """Cancel a run and all active tasks in one transaction."""
        async with self._session_factory.begin() as session:
            run_model = await session.scalar(
                select(RunModel).where(RunModel.id == run_id).with_for_update()
            )
            if run_model is None:
                raise EntityNotFoundError("run", run_id)

            current = RunStatus(run_model.status)
            if current is RunStatus.CANCELED:
                return self._run(run_model), []
            if is_terminal_run_status(current):
                raise RunNotCancelableError(run_id, current.value)
            if run_model.version != expected_version:
                raise ConcurrentUpdateError(
                    "run", run_id, expected_version, run_model.version
                )

            task_models = list(
                await session.scalars(
                    select(TaskModel)
                    .where(
                        TaskModel.run_id == run_id,
                        TaskModel.status.in_(
                            [
                                TaskStatus.CREATED.value,
                                TaskStatus.QUEUED.value,
                                TaskStatus.RUNNING.value,
                            ]
                        ),
                    )
                    .with_for_update()
                )
            )
            now = datetime.now(UTC)
            canceled_task_ids: list[UUID] = []
            for task_model in task_models:
                task_status = TaskStatus(task_model.status)
                ensure_task_transition(task_status, TaskStatus.CANCELED)
                task_model.status = TaskStatus.CANCELED.value
                task_model.completed_at = now
                canceled_task_ids.append(task_model.id)

            ensure_run_transition(current, RunStatus.CANCELED)
            run_model.status = RunStatus.CANCELED.value
            run_model.version += 1
            session.add(
                EventModel(
                    run_id=run_model.id,
                    event_type="run.canceled",
                    payload={
                        "reason": reason,
                        "canceled_task_ids": [str(item) for item in canceled_task_ids],
                    },
                )
            )
            await session.flush()
            await session.refresh(run_model)
            return self._run(run_model), canceled_task_ids

    async def add_run_cost(
        self,
        run_id: UUID,
        *,
        amount_usd: Decimal,
    ) -> Run:
        if amount_usd < 0:
            raise ValueError("amount_usd must be non-negative")
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(RunModel).where(RunModel.id == run_id).with_for_update()
            )
            if model is None:
                raise EntityNotFoundError("run", run_id)
            model.spent_cost_usd += amount_usd
            model.version += 1
            await session.flush()
            await session.refresh(model)
            return self._run(model)

    async def create_task(self, data: TaskCreate) -> Task:
        model = TaskModel(
            run_id=data.run_id,
            kind=data.kind.value,
            status=TaskStatus.CREATED.value,
            model_tier=data.model_tier.value,
            instruction=data.instruction,
            max_attempts=data.max_attempts,
            priority=data.priority,
            worktree_path=str(data.worktree_path) if data.worktree_path else None,
        )
        try:
            async with self._session_factory.begin() as session:
                session.add(model)
                await session.flush()
                await session.refresh(model)
        except IntegrityError as exc:
            raise EntityNotFoundError("run", data.run_id) from exc
        return self._task(model)

    async def get_task(self, task_id: UUID) -> Task:
        async with self._session_factory() as session:
            model = await session.get(TaskModel, task_id)
            if model is None:
                raise EntityNotFoundError("task", task_id)
            return self._task(model)

    async def list_tasks_for_run(self, run_id: UUID) -> list[Task]:
        async with self._session_factory() as session:
            run_exists = await session.scalar(
                select(RunModel.id).where(RunModel.id == run_id)
            )
            if run_exists is None:
                raise EntityNotFoundError("run", run_id)
            models = list(
                await session.scalars(
                    select(TaskModel)
                    .where(TaskModel.run_id == run_id)
                    .order_by(TaskModel.created_at.asc(), TaskModel.id.asc())
                )
            )
            return [self._task(model) for model in models]

    async def transition_task(
        self,
        task_id: UUID,
        *,
        target: TaskStatus,
        codex_thread_id: str | None = None,
    ) -> Task:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(TaskModel).where(TaskModel.id == task_id).with_for_update()
            )
            if model is None:
                raise EntityNotFoundError("task", task_id)

            current = TaskStatus(model.status)
            ensure_task_transition(current, target)
            model.status = target.value
            if codex_thread_id is not None:
                model.codex_thread_id = codex_thread_id
            now = datetime.now(UTC)
            if target is TaskStatus.QUEUED:
                model.completed_at = None
            if target is TaskStatus.RUNNING:
                model.started_at = now
                model.completed_at = None
                model.attempt += 1
            if target in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELED,
            }:
                model.completed_at = now
            await session.flush()
            await session.refresh(model)
            return self._task(model)

    async def claim_next_task(self) -> Task | None:
        """Atomically claim the next queued task using PostgreSQL SKIP LOCKED."""
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(TaskModel)
                .where(
                    TaskModel.status == TaskStatus.QUEUED.value,
                    TaskModel.attempt < TaskModel.max_attempts,
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
            await session.flush()
            await session.refresh(model)
            return self._task(model)

    async def record_task_usage(
        self,
        task_id: UUID,
        *,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: Decimal,
    ) -> Task:
        if input_tokens < 0 or output_tokens < 0 or estimated_cost_usd < 0:
            raise ValueError("usage values must be non-negative")
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(TaskModel).where(TaskModel.id == task_id).with_for_update()
            )
            if model is None:
                raise EntityNotFoundError("task", task_id)
            model.input_tokens += input_tokens
            model.output_tokens += output_tokens
            model.estimated_cost_usd += estimated_cost_usd
            await session.flush()
            await session.refresh(model)
            return self._task(model)

    async def create_task_result(self, data: TaskResultCreate) -> TaskResult:
        model = TaskResultModel(
            task_id=data.task_id,
            summary=data.summary,
            changed_files=data.changed_files,
            commands_run=data.commands_run,
            success=data.success,
        )
        try:
            async with self._session_factory.begin() as session:
                session.add(model)
                await session.flush()
                await session.refresh(model)
        except IntegrityError as exc:
            raise DuplicateEntityError("task_result", str(data.task_id)) from exc
        return self._task_result(model)

    async def create_approval(self, data: ApprovalCreate) -> Approval:
        model = ApprovalModel(
            run_id=data.run_id,
            type=data.type.value,
            approved=data.approved,
            notes=data.notes,
            expected_version=data.expected_version,
        )
        async with self._session_factory.begin() as session:
            session.add(model)
            await session.flush()
            await session.refresh(model)
        return self._approval(model)

    async def create_artifact(self, data: ArtifactCreate) -> Artifact:
        model = ArtifactModel(
            run_id=data.run_id,
            task_id=data.task_id,
            kind=data.kind.value,
            path=str(data.path),
            sha256=data.sha256.lower(),
            size_bytes=data.size_bytes,
        )
        async with self._session_factory.begin() as session:
            session.add(model)
            await session.flush()
            await session.refresh(model)
        return self._artifact(model)

    async def append_event(self, data: EventCreate) -> Event:
        model = EventModel(
            run_id=data.run_id,
            task_id=data.task_id,
            event_type=data.event_type,
            payload=data.payload,
        )
        async with self._session_factory.begin() as session:
            session.add(model)
            await session.flush()
            await session.refresh(model)
        return self._event(model)

    @staticmethod
    def _new_run_model(data: RunCreate) -> RunModel:
        return RunModel(
            repository_id=data.repository_id,
            goal=data.goal,
            constraints=data.constraints,
            status=RunStatus.CREATED.value,
            risk_level=data.risk_level.value,
            max_cost_usd=data.max_cost_usd,
            spent_cost_usd=Decimal("0"),
            version=1,
        )

    @staticmethod
    def _repository(model: RepositoryModel) -> Repository:
        return Repository(
            id=model.id,
            name=model.name,
            root_path=Path(model.root_path),
            default_branch=model.default_branch,
            verification_config=[
                VerificationCommandSpec.model_validate(item)
                for item in model.verification_config
            ],
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _run(model: RunModel) -> Run:
        return Run(
            id=model.id,
            repository_id=model.repository_id,
            goal=model.goal,
            constraints=model.constraints,
            status=RunStatus(model.status),
            risk_level=RiskLevel(model.risk_level),
            max_cost_usd=model.max_cost_usd,
            spent_cost_usd=model.spent_cost_usd,
            plan=model.plan,
            current_task_id=model.current_task_id,
            version=model.version,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _task(model: TaskModel) -> Task:
        return Task(
            id=model.id,
            run_id=model.run_id,
            kind=TaskKind(model.kind),
            instruction=model.instruction,
            model_tier=ModelTier(model.model_tier),
            max_attempts=model.max_attempts,
            priority=model.priority,
            worktree_path=Path(model.worktree_path) if model.worktree_path else None,
            status=TaskStatus(model.status),
            attempt=model.attempt,
            codex_thread_id=model.codex_thread_id,
            input_tokens=model.input_tokens,
            output_tokens=model.output_tokens,
            estimated_cost_usd=model.estimated_cost_usd,
            started_at=model.started_at,
            completed_at=model.completed_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _task_result(model: TaskResultModel) -> TaskResult:
        return TaskResult.model_validate(model)

    @staticmethod
    def _approval(model: ApprovalModel) -> Approval:
        return Approval(
            id=model.id,
            run_id=model.run_id,
            type=ApprovalType(model.type),
            approved=model.approved,
            notes=model.notes,
            expected_version=model.expected_version,
            created_at=model.created_at,
        )

    @staticmethod
    def _artifact(model: ArtifactModel) -> Artifact:
        return Artifact(
            id=model.id,
            run_id=model.run_id,
            task_id=model.task_id,
            kind=ArtifactKind(model.kind),
            path=Path(model.path),
            sha256=model.sha256,
            size_bytes=model.size_bytes,
            created_at=model.created_at,
        )

    @staticmethod
    def _event(model: EventModel) -> Event:
        return Event.model_validate(model)
