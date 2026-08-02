from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.api_schemas import AutomationStatus, StartAutomatedRunOutput
from orchestrator.rest_api import build_api_router
from orchestrator.schemas import RunStatus
from orchestrator.settings import Settings


class FakeCoordinator:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def start_run(self, request: object, *, idempotency_key: str) -> object:
        self.keys.append(idempotency_key)
        return StartAutomatedRunOutput(
            run_id=uuid4(),
            status=RunStatus.PLANNING,
            version=2,
            automation_status=AutomationStatus.ACTIVE,
            idempotent_replay=False,
            status_url="/api/v1/runs/example",
        )


def make_app() -> tuple[FastAPI, FakeCoordinator]:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
        api_enabled=True,
        api_key="x" * 32,
    )
    coordinator = FakeCoordinator()
    app = FastAPI()
    app.include_router(
        build_api_router(settings, coordinator),  # type: ignore[arg-type]
        prefix=settings.api_prefix,
    )
    return app, coordinator


def request_body() -> dict[str, object]:
    return {
        "repository": "toss-trader",
        "goal": "Add a safe API client.",
        "commit_message": "feat(api): add safe client",
        "pull_request_title": "feat(api): add safe client",
    }


def test_api_rejects_missing_bearer_token() -> None:
    app, _ = make_app()
    client = TestClient(app)

    response = client.post(
        "/api/v1/runs",
        headers={"Idempotency-Key": "request-1234"},
        json=request_body(),
    )

    assert response.status_code == 401


def test_api_starts_run_with_authenticated_idempotency_key() -> None:
    app, coordinator = make_app()
    client = TestClient(app)

    response = client.post(
        "/api/v1/runs",
        headers={
            "Authorization": f"Bearer {'x' * 32}",
            "Idempotency-Key": "request-1234",
        },
        json=request_body(),
    )

    assert response.status_code == 202
    assert response.json()["status"] == "planning"
    assert coordinator.keys == ["request-1234"]


def test_openapi_exposes_stable_action_operation_id() -> None:
    app, _ = make_app()
    schema = app.openapi()

    operation = schema["paths"]["/api/v1/runs"]["post"]
    assert operation["operationId"] == "startOrchestrationRun"
    assert "HTTPBearer" in schema["components"]["securitySchemes"]
