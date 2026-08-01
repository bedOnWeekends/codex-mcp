from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from starlette.testclient import TestClient

from orchestrator.mcp_server import build_application
from orchestrator.settings import Settings


@pytest.mark.mcp
def test_liveness_route_does_not_require_database_connection(tmp_path) -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@127.0.0.1:1/unavailable",
        runtime_dir=tmp_path / "runtime",
    )
    application = build_application(settings)

    with TestClient(application.asgi_app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
