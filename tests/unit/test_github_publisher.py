from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from orchestrator.github_publisher import (
    FakeGitHubPublisher,
    LiveGitHubPublisher,
    PublishTaskPayload,
    parse_github_remote,
)
from orchestrator.schemas import Repository
from orchestrator.settings import Settings


class StubLiveGitHubPublisher(LiveGitHubPublisher):
    def __init__(
        self,
        settings: Settings,
        *,
        run_id: UUID,
        transport: httpx.AsyncBaseTransport,
    ) -> None:
        super().__init__(settings, transport=transport)
        self.run_id = run_id
        self.git_commands: list[tuple[str, ...]] = []

    async def _git_text(self, cwd: Path, *args: str) -> str:
        del cwd
        if args == ("branch", "--show-current"):
            return "orchestrator/run-test"
        if args == ("status", "--porcelain=v1", "--untracked-files=all"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args == ("log", "-1", "--format=%B"):
            return f"feat: publish generated change\n\nOrchestrator-Run: {self.run_id}"
        if args == ("remote", "get-url", "origin"):
            return "https://github.com/owner/repo.git"
        raise AssertionError(f"Unexpected Git command: {args}")

    async def _git(self, cwd: Path, *args: str) -> tuple[str, str]:
        del cwd
        self.git_commands.append(args)
        return "", ""


def make_repository(tmp_path: Path) -> Repository:
    now = datetime.now(UTC)
    return Repository(
        id=uuid4(),
        name="toss-trader",
        root_path=tmp_path / "repo",
        default_branch="main",
        verification_config=[],
        created_at=now,
        updated_at=now,
    )


def live_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
        runtime_dir=tmp_path / "runtime",
        github_publish_mode="live",
        github_token=SecretStr("test-token"),
    )


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("ssh://git@github.com/owner/repo.git", "owner/repo"),
    ],
)
def test_parse_github_remote_accepts_supported_forms(
    remote: str,
    expected: str,
) -> None:
    assert parse_github_remote(remote).full_name == expected


@pytest.mark.parametrize(
    "remote",
    [
        "https://token@github.com/owner/repo.git",
        "https://example.com/owner/repo.git",
        "file:///tmp/repo.git",
    ],
)
def test_parse_github_remote_rejects_unsafe_or_unsupported_forms(remote: str) -> None:
    with pytest.raises(ValueError):
        parse_github_remote(remote)


@pytest.mark.asyncio
async def test_fake_publisher_never_returns_a_remote_side_effect(tmp_path: Path) -> None:
    result = await FakeGitHubPublisher().publish(
        repository=make_repository(tmp_path),
        run_id=uuid4(),
        worktree=tmp_path / "worktree",
        payload=PublishTaskPayload(
            title="feat: publish generated change",
            draft=True,
        ),
    )
    assert result.mode == "fake"
    assert result.pull_request_url is None
    assert result.commit_sha is None
    assert result.commands_run == []


def test_live_publisher_requires_a_token(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
        runtime_dir=tmp_path / "runtime",
        github_publish_mode="live",
        github_token=None,
    )
    with pytest.raises(ValueError, match="ORCH_GITHUB_TOKEN"):
        LiveGitHubPublisher(settings)


def test_live_publisher_accepts_secret_token(tmp_path: Path) -> None:
    publisher = LiveGitHubPublisher(live_settings(tmp_path))
    assert publisher.settings.github_token is not None
    assert publisher.settings.github_token.get_secret_value() == "test-token"


@pytest.mark.asyncio
async def test_live_publisher_pushes_run_branch_and_creates_draft_pr(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(
            201,
            json={
                "html_url": "https://github.com/owner/repo/pull/7",
                "number": 7,
                "draft": True,
            },
        )

    run_id = uuid4()
    publisher = StubLiveGitHubPublisher(
        live_settings(tmp_path),
        run_id=run_id,
        transport=httpx.MockTransport(handler),
    )
    result = await publisher.publish(
        repository=make_repository(tmp_path),
        run_id=run_id,
        worktree=tmp_path / "worktree",
        payload=PublishTaskPayload(
            title="feat: publish generated change",
            body="Generated by the orchestrator.",
            draft=True,
            expected_commit_sha="a" * 40,
        ),
    )

    assert publisher.git_commands == [
        ("push", "--set-upstream", "origin", "orchestrator/run-test")
    ]
    assert [request.method for request in requests] == ["GET", "POST"]
    posted = json.loads(requests[1].content)
    assert posted["draft"] is True
    assert result.mode == "live"
    assert result.repository == "owner/repo"
    assert result.pull_request_url == "https://github.com/owner/repo/pull/7"
    assert result.pull_request_number == 7
    assert result.created is True


@pytest.mark.asyncio
async def test_live_publisher_reuses_only_a_draft_pr(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "html_url": "https://github.com/owner/repo/pull/8",
                    "number": 8,
                    "draft": True,
                }
            ],
        )

    run_id = uuid4()
    publisher = StubLiveGitHubPublisher(
        live_settings(tmp_path),
        run_id=run_id,
        transport=httpx.MockTransport(handler),
    )
    result = await publisher.publish(
        repository=make_repository(tmp_path),
        run_id=run_id,
        worktree=tmp_path / "worktree",
        payload=PublishTaskPayload(
            title="feat: publish generated change",
            expected_commit_sha="a" * 40,
        ),
    )

    assert [request.method for request in requests] == ["GET"]
    assert result.pull_request_number == 8
    assert result.created is False
