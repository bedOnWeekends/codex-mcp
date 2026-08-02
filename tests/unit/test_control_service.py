from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from orchestrator.control_service import RunControlService
from orchestrator.mcp_schemas import (
    ApproveDeliveryInput,
    ApprovePlanInput,
    ApprovePublishInput,
    CancelRunInput,
    CreateRunInput,
    FinishRunInput,
)
from orchestrator.phase7_store import Phase7Store
from orchestrator.schemas import (
    AgentAssignment,
    Approval,
    ApprovalType,
    ModelTier,
    Repository,
    RiskLevel,
    Run,
    RunStatus,
    Task,
    TaskKind,
    TaskResult,
    TaskStatus,
)
from orchestrator.settings import Settings


class FakeStore:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.repository = Repository(
            id=uuid4(),
            name="toss-trader",
            root_path=Path("/tmp/toss-trader"),
            default_branch="main",
            verification_config=[],
            created_at=now,
            updated_at=now,
        )
        self.run = Run(
            id=uuid4(),
            repository_id=self.repository.id,
            goal="Implement quote lookup",
            constraints=["read-only"],
            risk_level=RiskLevel.NORMAL,
            max_cost_usd=Decimal("3.00"),
            status=RunStatus.PLANNING,
            spent_cost_usd=Decimal("0"),
            current_task_id=None,
            version=2,
            created_at=now,
            updated_at=now,
        )
        self.task = Task(
            id=uuid4(),
            run_id=self.run.id,
            kind=TaskKind.PLAN,
            instruction="Plan the implementation",
            model_tier=ModelTier.DEFAULT,
            max_attempts=2,
            priority=100,
            status=TaskStatus.QUEUED,
            attempt=0,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=Decimal("0"),
            created_at=now,
            updated_at=now,
        )
        self.run = self.run.model_copy(update={"current_task_id": self.task.id})
        self.received_plan_instruction: str | None = None
        self.supervisor_instruction: str | None = None
        self.delivery_worktree_path: Path | None = None
        self.delivery_commit_message: str | None = None
        self.publish_worktree_path: Path | None = None
        self.publish_title: str | None = None

    async def list_repositories(self) -> list[Repository]:
        return [self.repository]

    async def get_repository_by_name(self, name: str) -> Repository:
        assert name == self.repository.name
        return self.repository

    async def create_run_with_initial_task(
        self,
        data: object,
        *,
        plan_instruction: str,
        model_tier: ModelTier,
        max_attempts: int,
    ) -> tuple[Run, Task]:
        del data
        self.received_plan_instruction = plan_instruction
        assert model_tier is ModelTier.DEFAULT
        assert max_attempts == 2
        return self.run, self.task

    async def get_run(self, run_id: UUID) -> Run:
        assert run_id == self.run.id
        return self.run

    async def get_repository(self, repository_id: UUID) -> Repository:
        assert repository_id == self.repository.id
        return self.repository

    async def list_tasks_for_run(self, run_id: UUID) -> list[Task]:
        assert run_id == self.run.id
        return [self.task]

    async def list_task_results_for_run(self, run_id: UUID) -> list[TaskResult]:
        assert run_id == self.run.id
        return []

    async def list_agent_assignments(self, run_id: UUID) -> list[AgentAssignment]:
        assert run_id == self.run.id
        return []

    async def approve_plan_and_queue_supervision(
        self,
        run_id: UUID,
        *,
        expected_version: int,
        notes: str | None,
        instruction: str,
        model_tier: ModelTier,
        max_attempts: int,
    ) -> tuple[Run, Task, Approval]:
        assert run_id == self.run.id
        assert expected_version == self.run.version
        assert notes == "approved"
        assert "cheapest reliable" in instruction
        assert model_tier is ModelTier.CHEAP
        assert max_attempts == 2
        self.supervisor_instruction = instruction
        return self._queued_outcome(
            kind=TaskKind.SUPERVISE,
            status=RunStatus.SUPERVISING,
            instruction=instruction,
            worktree_path=None,
            approval_type=ApprovalType.PLAN,
            notes=notes,
            expected_version=expected_version,
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
        assert run_id == self.run.id
        assert expected_version == self.run.version
        assert notes == "ship it"
        assert max_attempts == 2
        self.delivery_worktree_path = worktree_path
        self.delivery_commit_message = commit_message
        return self._queued_outcome(
            kind=TaskKind.DELIVER,
            status=RunStatus.DELIVERING,
            instruction=commit_message,
            worktree_path=worktree_path,
            approval_type=ApprovalType.DELIVERY,
            notes=notes,
            expected_version=expected_version,
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
        assert run_id == self.run.id
        assert expected_version == self.run.version
        assert title == "feat: add quote lookup"
        assert body == "Generated change"
        assert draft is True
        assert notes == "publish it"
        assert max_attempts == 2
        assert allow_noop is True
        self.publish_worktree_path = worktree_path
        self.publish_title = title
        return self._queued_outcome(
            kind=TaskKind.PUBLISH,
            status=RunStatus.PUBLISHING,
            instruction=title,
            worktree_path=worktree_path,
            approval_type=ApprovalType.PUBLISH,
            notes=notes,
            expected_version=expected_version,
        )

    async def finish_without_publish(
        self,
        run_id: UUID,
        *,
        expected_version: int,
        notes: str | None,
    ) -> tuple[Run, Approval]:
        assert run_id == self.run.id
        assert expected_version == self.run.version
        assert notes == "keep local"
        now = datetime.now(UTC)
        completed = self.run.model_copy(
            update={
                "status": RunStatus.COMPLETED,
                "version": self.run.version + 1,
            }
        )
        approval = Approval(
            id=uuid4(),
            run_id=run_id,
            type=ApprovalType.PUBLISH,
            approved=False,
            notes=notes,
            expected_version=expected_version,
            created_at=now,
        )
        return completed, approval

    async def cancel_run(
        self,
        run_id: UUID,
        *,
        expected_version: int,
        reason: str | None,
    ) -> tuple[Run, list[UUID]]:
        assert run_id == self.run.id
        assert expected_version == self.run.version
        assert reason == "user requested"
        canceled = self.run.model_copy(
            update={"status": RunStatus.CANCELED, "version": self.run.version + 1}
        )
        return canceled, [self.task.id]

    def _queued_outcome(
        self,
        *,
        kind: TaskKind,
        status: RunStatus,
        instruction: str,
        worktree_path: Path | None,
        approval_type: ApprovalType,
        notes: str | None,
        expected_version: int,
    ) -> tuple[Run, Task, Approval]:
        now = datetime.now(UTC)
        queued = self.task.model_copy(
            update={
                "id": uuid4(),
                "kind": kind,
                "instruction": instruction,
                "status": TaskStatus.QUEUED,
                "worktree_path": worktree_path,
            }
        )
        updated_run = self.run.model_copy(
            update={
                "status": status,
                "version": self.run.version + 1,
                "current_task_id": queued.id,
            }
        )
        approval = Approval(
            id=uuid4(),
            run_id=self.run.id,
            type=approval_type,
            approved=True,
            notes=notes,
            expected_version=expected_version,
            created_at=now,
        )
        return updated_run, queued, approval


def make_service(fake: FakeStore, *, runtime_dir: Path) -> RunControlService:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
        runtime_dir=runtime_dir,
    )
    return RunControlService(cast(Phase7Store, fake), settings)


@pytest.mark.asyncio
async def test_create_run_queues_a_planning_task(tmp_path: Path) -> None:
    fake = FakeStore()
    output = await make_service(fake, runtime_dir=tmp_path).create_run(
        CreateRunInput(
            repository="toss-trader",
            goal="Implement quote lookup",
            constraints=["read-only"],
        )
    )
    assert output.run_id == fake.run.id
    assert output.status is RunStatus.PLANNING
    assert output.plan_task_status is TaskStatus.QUEUED
    assert "Do not modify files" in (fake.received_plan_instruction or "")


@pytest.mark.asyncio
async def test_approve_plan_queues_low_cost_supervisor_task(tmp_path: Path) -> None:
    fake = FakeStore()
    fake.run = fake.run.model_copy(
        update={"status": RunStatus.AWAITING_PLAN_APPROVAL, "plan": "Approved plan"}
    )
    output = await make_service(fake, runtime_dir=tmp_path).approve_plan(
        ApprovePlanInput(
            run_id=fake.run.id,
            expected_version=fake.run.version,
            notes="approved",
        )
    )
    assert output.status is RunStatus.SUPERVISING
    assert output.supervisor_task_status is TaskStatus.QUEUED
    assert "low-cost scout" in output.message
    assert fake.supervisor_instruction is not None


@pytest.mark.asyncio
async def test_approve_delivery_queues_local_commit_task(tmp_path: Path) -> None:
    fake = FakeStore()
    fake.run = fake.run.model_copy(
        update={"status": RunStatus.AWAITING_DELIVERY_APPROVAL}
    )
    output = await make_service(fake, runtime_dir=tmp_path).approve_delivery(
        ApproveDeliveryInput(
            run_id=fake.run.id,
            expected_version=fake.run.version,
            commit_message="feat: add quote lookup",
            notes="ship it",
        )
    )
    assert output.status is RunStatus.DELIVERING
    assert output.delivery_task_status is TaskStatus.QUEUED
    assert fake.delivery_commit_message == "feat: add quote lookup"


@pytest.mark.asyncio
async def test_approve_publish_queues_publication_task(tmp_path: Path) -> None:
    fake = FakeStore()
    fake.run = fake.run.model_copy(
        update={"status": RunStatus.AWAITING_PUBLISH_APPROVAL}
    )
    output = await make_service(fake, runtime_dir=tmp_path).approve_publish(
        ApprovePublishInput(
            run_id=fake.run.id,
            expected_version=fake.run.version,
            title="feat: add quote lookup",
            body="Generated change",
            draft=True,
            notes="publish it",
        )
    )
    assert output.status is RunStatus.PUBLISHING
    assert output.publish_task_status is TaskStatus.QUEUED
    assert fake.publish_title == "feat: add quote lookup"
    assert fake.publish_worktree_path == tmp_path.resolve() / "worktrees" / str(
        fake.run.id
    )


@pytest.mark.asyncio
async def test_finish_run_keeps_delivery_local(tmp_path: Path) -> None:
    fake = FakeStore()
    fake.run = fake.run.model_copy(
        update={"status": RunStatus.AWAITING_PUBLISH_APPROVAL}
    )
    output = await make_service(fake, runtime_dir=tmp_path).finish_run(
        FinishRunInput(
            run_id=fake.run.id,
            expected_version=fake.run.version,
            notes="keep local",
        )
    )
    assert output.status is RunStatus.COMPLETED
    assert output.version == fake.run.version + 1


@pytest.mark.asyncio
async def test_get_run_returns_task_and_agent_summaries(tmp_path: Path) -> None:
    fake = FakeStore()
    output = await make_service(fake, runtime_dir=tmp_path).get_run(fake.run.id)
    assert output.repository == "toss-trader"
    assert output.version == 2
    assert output.tasks[0].kind is TaskKind.PLAN
    assert output.tasks[0].result_summary is None
    assert output.agents == []


@pytest.mark.asyncio
async def test_cancel_run_returns_new_version_and_canceled_tasks(
    tmp_path: Path,
) -> None:
    fake = FakeStore()
    output = await make_service(fake, runtime_dir=tmp_path).cancel_run(
        CancelRunInput(
            run_id=fake.run.id,
            expected_version=fake.run.version,
            reason="user requested",
        )
    )
    assert output.status is RunStatus.CANCELED
    assert output.version == fake.run.version + 1
    assert output.canceled_task_ids == [fake.task.id]
