from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from .api_schemas import ApiExecutionMode, AutomationStatus, StartAutomatedRunInput
from .contracts import auto_pr_run_constraints
from .db_models import Base, EventModel, RepositoryModel, RunModel, TaskModel
from .errors import DuplicateEntityError, EntityNotFoundError
from .schemas import ModelTier, RiskLevel, RunStatus, TaskKind, TaskStatus


class AutomationRunModel(Base):
    __tablename__ = "automation_runs"
    __table_args__ = (
        Index("ix_automation_runs_status_created_at", "status", "created_at"),
    )

    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=AutomationStatus.ACTIVE.value
    )
    commit_message: Mapped[str] = mapped_column(String(100), nullable=False)
    pull_request_title: Mapped[str] = mapped_column(String(256), nullable=False)
    pull_request_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pull_request_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pull_request_number: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class AutomationRunRecord:
    __slots__ = (
        "run_id",
        "idempotency_key",
        "request_hash",
        "execution_mode",
        "status",
        "commit_message",
        "pull_request_title",
        "pull_request_body",
        "last_error",
        "commit_sha",
        "branch",
        "pull_request_url",
        "pull_request_number",
        "created_at",
        "updated_at",
    )

    def __init__(self, model: AutomationRunModel) -> None:
        self.run_id = model.run_id
        self.idempotency_key = model.idempotency_key
        self.request_hash = model.request_hash
        self.execution_mode = ApiExecutionMode(model.execution_mode)
        self.status = AutomationStatus(model.status)
        self.commit_message = model.commit_message
        self.pull_request_title = model.pull_request_title
        self.pull_request_body = model.pull_request_body
        self.last_error = model.last_error
        self.commit_sha = model.commit_sha
        self.branch = model.branch
        self.pull_request_url = model.pull_request_url
        self.pull_request_number = model.pull_request_number
        self.created_at = model.created_at
        self.updated_at = model.updated_at


class AutomationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_or_get(
        self,
        *,
        request: StartAutomatedRunInput,
        idempotency_key: str,
        request_hash: str,
        max_attempts: int,
    ) -> tuple[AutomationRunRecord, bool]:
        try:
            async with self._session_factory.begin() as session:
                existing = await session.scalar(
                    select(AutomationRunModel).where(
                        AutomationRunModel.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    return AutomationRunRecord(existing), False

                repository = await session.scalar(
                    select(RepositoryModel).where(
                        RepositoryModel.name == request.repository
                    )
                )
                if repository is None:
                    raise EntityNotFoundError("repository", request.repository)

                run_constraints = auto_pr_run_constraints(
                    request.constraints,
                    request.acceptance_criteria,
                )
                run = RunModel(
                    id=uuid4(),
                    repository_id=repository.id,
                    goal=request.goal,
                    constraints=run_constraints,
                    status=RunStatus.CREATED.value,
                    risk_level=request.risk_level.value,
                    max_cost_usd=request.max_cost_usd,
                    spent_cost_usd=Decimal("0"),
                    version=1,
                )
                session.add(run)
                await session.flush()

                task = TaskModel(
                    run_id=run.id,
                    kind=TaskKind.PLAN.value,
                    status=TaskStatus.QUEUED.value,
                    model_tier=self._model_tier(request.risk_level).value,
                    instruction=self._plan_instruction(
                        goal=request.goal,
                        constraints=run_constraints,
                    ),
                    max_attempts=max_attempts,
                    priority=100,
                )
                session.add(task)
                await session.flush()

                run.status = RunStatus.PLANNING.value
                run.current_task_id = task.id
                run.version += 1
                automation = AutomationRunModel(
                    run_id=run.id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    execution_mode=ApiExecutionMode.AUTO_PR.value,
                    status=AutomationStatus.ACTIVE.value,
                    commit_message=request.commit_message,
                    pull_request_title=request.pull_request_title,
                    pull_request_body=request.pull_request_body,
                )
                session.add_all(
                    [
                        automation,
                        EventModel(
                            run_id=run.id,
                            task_id=task.id,
                            event_type="run.created",
                            payload={
                                "status": RunStatus.PLANNING.value,
                                "initial_task_kind": TaskKind.PLAN.value,
                                "source": "rest_api",
                                "execution_mode": ApiExecutionMode.AUTO_PR.value,
                                "semantic_review_required": True,
                                "acceptance_criteria_count": len(
                                    request.acceptance_criteria
                                ),
                            },
                        ),
                    ]
                )
                await session.flush()
                await session.refresh(automation)
                return AutomationRunRecord(automation), True
        except IntegrityError:
            existing = await self.get_by_idempotency_key(idempotency_key)
            if existing is None:
                raise DuplicateEntityError(
                    "automation idempotency key", idempotency_key
                ) from None
            return existing, False

    async def get(self, run_id: UUID) -> AutomationRunRecord:
        async with self._session_factory() as session:
            model = await session.get(AutomationRunModel, run_id)
            if model is None:
                raise EntityNotFoundError("automation run", run_id)
            return AutomationRunRecord(model)

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> AutomationRunRecord | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(AutomationRunModel).where(
                    AutomationRunModel.idempotency_key == idempotency_key
                )
            )
            return AutomationRunRecord(model) if model is not None else None

    async def list_active(self, *, limit: int = 100) -> list[AutomationRunRecord]:
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    select(AutomationRunModel)
                    .where(AutomationRunModel.status == AutomationStatus.ACTIVE.value)
                    .order_by(AutomationRunModel.created_at.asc())
                    .limit(limit)
                )
            )
            return [AutomationRunRecord(model) for model in models]

    async def set_status(
        self,
        run_id: UUID,
        status: AutomationStatus,
        *,
        last_error: str | None = None,
    ) -> AutomationRunRecord:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(AutomationRunModel)
                .where(AutomationRunModel.run_id == run_id)
                .with_for_update()
            )
            if model is None:
                raise EntityNotFoundError("automation run", run_id)
            model.status = status.value
            model.last_error = last_error
            model.updated_at = datetime.now(UTC)
            await session.flush()
            await session.refresh(model)
            return AutomationRunRecord(model)

    async def record_error(
        self, run_id: UUID, error: str | None
    ) -> AutomationRunRecord:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(AutomationRunModel)
                .where(AutomationRunModel.run_id == run_id)
                .with_for_update()
            )
            if model is None:
                raise EntityNotFoundError("automation run", run_id)
            model.last_error = error[:4_000] if error else None
            model.updated_at = datetime.now(UTC)
            await session.flush()
            await session.refresh(model)
            return AutomationRunRecord(model)

    async def capture_publication(self, run_id: UUID) -> AutomationRunRecord:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(AutomationRunModel)
                .where(AutomationRunModel.run_id == run_id)
                .with_for_update()
            )
            if model is None:
                raise EntityNotFoundError("automation run", run_id)
            event = await session.scalar(
                select(EventModel)
                .where(
                    EventModel.run_id == run_id,
                    EventModel.event_type.in_(
                        ["run.pull_request_published", "run.publish_simulated"]
                    ),
                )
                .order_by(EventModel.created_at.desc(), EventModel.id.desc())
                .limit(1)
            )
            if event is not None:
                payload = event.payload
                model.commit_sha = self._optional_str(payload.get("commit_sha"))
                model.branch = self._optional_str(payload.get("branch"))
                model.pull_request_url = self._optional_str(
                    payload.get("pull_request_url")
                )
                number = payload.get("pull_request_number")
                model.pull_request_number = number if isinstance(number, int) else None
            model.status = AutomationStatus.COMPLETED.value
            model.last_error = None
            model.updated_at = datetime.now(UTC)
            await session.flush()
            await session.refresh(model)
            return AutomationRunRecord(model)

    @staticmethod
    def _model_tier(risk_level: RiskLevel) -> ModelTier:
        return (
            ModelTier.CRITICAL
            if risk_level is RiskLevel.CRITICAL
            else ModelTier.DEFAULT
        )

    @staticmethod
    def _plan_instruction(*, goal: str, constraints: list[str]) -> str:
        constraint_lines = (
            "\n".join(f"- {item}" for item in constraints)
            if constraints
            else "- No additional constraints."
        )
        return (
            "Inspect the registered repository in read-only mode and produce an "
            "implementation plan. Do not modify files. Treat the original goal, "
            "constraints, and acceptance criteria as authoritative. Preserve exact "
            "numeric values, formulas, enumerated options, and prohibited substitutions."
            "\n\n"
            f"Goal:\n{goal}\n\n"
            f"Constraints and acceptance criteria:\n{constraint_lines}\n\n"
            "Return relevant files, current architecture, implementation steps, "
            "risks, and explicit coverage of every acceptance criterion."
        )

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) and value else None
