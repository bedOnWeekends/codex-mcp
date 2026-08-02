from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

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
    now = datetime.now(UTC)
    repository = Repository(
        id=uuid4(),
        name="toss-trader",
        root_path=tmp_path / "repo",
        default_branch="main",
        verification_config=[],
        created_at=now,
        updated_at=now,
    )
    result = await FakeGitHubPublisher().publish(
        repository=repository,
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
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
        runtime_dir=tmp_path / "runtime",
        github_publish_mode="live",
        github_token=SecretStr("test-token"),
    )
    publisher = LiveGitHubPublisher(settings)
    assert publisher.settings.github_token is not None
    assert publisher.settings.github_token.get_secret_value() == "test-token"
