from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from orchestrator.control_service import RunControlService
from orchestrator.mcp_schemas import CancelRunInput, CreateRunInput
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
from orchestrator.store import Store


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


def make_service(fake: FakeStore) -> RunControlService:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
    )
    return RunControlService(cast(Store, fake), settings)


@pytest.mark.asyncio
async def test_create_run_queues_a_planning_task() -> None:
    fake = FakeStore()
    service = make_service(fake)

    output = await service.create_run(
        CreateRunInput(
            repository="toss-trader",
            goal="Implement quote lookup",
            constraints=["read-only"],
        )
    )

    assert output.run_id == fake.run.id
    assert output.status is RunStatus.PLANNING
    assert output.plan_task_status is TaskStatus.QUEUED
    assert fake.received_plan_instruction is not None
    assert "Do not modify files" in fake.received_plan_instruction
    assert "- read-only" in fake.received_plan_instruction


@pytest.mark.asyncio
async def test_get_run_returns_task_summaries() -> None:
    fake = FakeStore()
    output = await make_service(fake).get_run(fake.run.id)

    assert output.repository == "toss-trader"
    assert output.version == 2
    assert len(output.tasks) == 1
    assert output.tasks[0].kind is TaskKind.PLAN
    assert output.tasks[0].status is TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_cancel_run_returns_new_version_and_canceled_tasks() -> None:
    fake = FakeStore()
    output = await make_service(fake).cancel_run(
        CancelRunInput(
            run_id=fake.run.id,
            expected_version=fake.run.version,
            reason="user requested",
        )
    )

    assert output.status is RunStatus.CANCELED
    assert output.version == fake.run.version + 1
    assert output.canceled_task_ids == [fake.task.id]


@pytest.mark.asyncio
async def test_list_repositories_hides_local_paths() -> None:
    fake = FakeStore()
    output = await make_service(fake).list_repositories()

    serialized = output.model_dump(mode="json")
    assert serialized["repositories"][0]["name"] == "toss-trader"
    assert "root_path" not in serialized["repositories"][0]
