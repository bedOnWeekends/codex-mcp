from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from orchestrator.api_schemas import AutomationStatus
from orchestrator.automation import AutoPrDriver
from orchestrator.schemas import RunStatus
from orchestrator.settings import Settings


class FakeStore:
    def __init__(self) -> None:
        self.captured: list[object] = []
        self.statuses: list[tuple[object, AutomationStatus, str | None]] = []
        self.errors: list[tuple[object, str | None]] = []

    async def list_active(self, *, limit: int = 100) -> list[object]:
        return []

    async def capture_publication(self, run_id: object) -> None:
        self.captured.append(run_id)

    async def set_status(
        self,
        run_id: object,
        status: AutomationStatus,
        *,
        last_error: str | None = None,
    ) -> None:
        self.statuses.append((run_id, status, last_error))

    async def record_error(self, run_id: object, error: str | None) -> None:
        self.errors.append((run_id, error))


class FakeService:
    def __init__(self, status: RunStatus) -> None:
        self.run = SimpleNamespace(status=status, version=7)
        self.plan_requests: list[Any] = []
        self.delivery_requests: list[Any] = []
        self.publish_requests: list[Any] = []

    async def get_run(self, run_id: object) -> object:
        return self.run

    async def approve_plan(self, request: object) -> None:
        self.plan_requests.append(request)

    async def approve_delivery(self, request: object) -> None:
        self.delivery_requests.append(request)

    async def approve_publish(self, request: object) -> None:
        self.publish_requests.append(request)


def make_record() -> SimpleNamespace:
    return SimpleNamespace(
        run_id=uuid4(),
        commit_message="feat: implement requested change",
        pull_request_title="feat: implement requested change",
        pull_request_body="Automated change.",
    )


def make_settings() -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
    )


@pytest.mark.asyncio
async def test_driver_auto_approves_each_safe_boundary() -> None:
    record = make_record()
    store = FakeStore()
    service = FakeService(RunStatus.AWAITING_PLAN_APPROVAL)
    driver = AutoPrDriver(store, service, make_settings())  # type: ignore[arg-type]

    await driver.advance(record)  # type: ignore[arg-type]
    assert len(service.plan_requests) == 1

    service.run.status = RunStatus.AWAITING_DELIVERY_APPROVAL
    await driver.advance(record)  # type: ignore[arg-type]
    assert len(service.delivery_requests) == 1

    service.run.status = RunStatus.AWAITING_PUBLISH_APPROVAL
    await driver.advance(record)  # type: ignore[arg-type]
    assert len(service.publish_requests) == 1
    assert service.publish_requests[0].draft is True


@pytest.mark.asyncio
async def test_driver_captures_completed_publication() -> None:
    record = make_record()
    store = FakeStore()
    service = FakeService(RunStatus.COMPLETED)
    driver = AutoPrDriver(store, service, make_settings())  # type: ignore[arg-type]

    await driver.advance(record)  # type: ignore[arg-type]

    assert store.captured == [record.run_id]
