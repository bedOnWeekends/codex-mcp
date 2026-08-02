from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse
from uuid import UUID

import httpx
from pydantic import Field

from .errors import InvalidRepositoryError
from .schemas import OrchestratorModel, Repository
from .settings import Settings

_HTTPS_REMOTE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_SCP_REMOTE = re.compile(
    r"^(?:git@)?github\.com:(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?$"
)
_SSH_REMOTE = re.compile(
    r"^ssh://git@github\.com/(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


class PublishTaskPayload(OrchestratorModel):
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=60_000)
    draft: bool = True
    expected_commit_sha: str | None = Field(
        default=None,
        pattern=r"^[a-fA-F0-9]{40}$",
    )


@dataclass(frozen=True, slots=True)
class GitHubRepositoryRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True)
class PublishResult:
    mode: str
    repository: str
    branch: str
    commit_sha: str | None
    pull_request_url: str | None
    pull_request_number: int | None
    created: bool
    commands_run: list[str]

    def summary(self) -> str:
        if self.mode == "fake":
            return (
                "Simulated GitHub publication in fake mode. No branch was pushed and "
                "no pull request was created."
            )
        action = "Created" if self.created else "Reused"
        return (
            f"{action} draft pull request #{self.pull_request_number}: "
            f"{self.pull_request_url}"
        )


class Publisher(Protocol):
    async def publish(
        self,
        *,
        repository: Repository,
        run_id: UUID,
        worktree: Path,
        payload: PublishTaskPayload,
    ) -> PublishResult: ...


class FakeGitHubPublisher:
    async def publish(
        self,
        *,
        repository: Repository,
        run_id: UUID,
        worktree: Path,
        payload: PublishTaskPayload,
    ) -> PublishResult:
        del run_id, worktree, payload
        return PublishResult(
            mode="fake",
            repository=repository.name,
            branch="fake/orchestrator-run",
            commit_sha=None,
            pull_request_url=None,
            pull_request_number=None,
            created=False,
            commands_run=[],
        )


class LiveGitHubPublisher:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if settings.github_token is None:
            raise ValueError("ORCH_GITHUB_TOKEN is required in live publish mode")
        self.settings = settings
        self.transport = transport

    async def publish(
        self,
        *,
        repository: Repository,
        run_id: UUID,
        worktree: Path,
        payload: PublishTaskPayload,
    ) -> PublishResult:
        branch = await self._git_text(worktree, "branch", "--show-current")
        if not branch or not branch.startswith(self.settings.worktree_branch_prefix):
            raise InvalidRepositoryError(
                str(worktree),
                "publication requires the isolated orchestrator run branch",
            )

        status = await self._git_text(
            worktree,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        if status:
            raise InvalidRepositoryError(
                str(worktree),
                "publication requires a clean delivered worktree",
            )

        commit_sha = await self._git_text(worktree, "rev-parse", "HEAD")
        if payload.expected_commit_sha is None:
            raise InvalidRepositoryError(
                str(worktree),
                "live publication requires a delivered commit",
            )
        if commit_sha.lower() != payload.expected_commit_sha.lower():
            raise InvalidRepositoryError(
                str(worktree),
                "worktree HEAD does not match the approved delivery commit",
            )

        commit_body = await self._git_text(worktree, "log", "-1", "--format=%B")
        if f"Orchestrator-Run: {run_id}" not in commit_body.splitlines():
            raise InvalidRepositoryError(
                str(worktree),
                "delivery commit is missing the orchestrator run trailer",
            )

        remote_url = await self._git_text(
            worktree,
            "remote",
            "get-url",
            self.settings.github_remote_name,
        )
        remote = parse_github_remote(remote_url)
        await self._git(
            worktree,
            "push",
            "--set-upstream",
            self.settings.github_remote_name,
            branch,
        )

        token = self.settings.github_token.get_secret_value()
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": self.settings.github_api_version,
            "User-Agent": self.settings.app_name,
        }
        timeout = httpx.Timeout(self.settings.github_request_timeout_seconds)
        async with httpx.AsyncClient(
            base_url=self.settings.github_api_url,
            headers=headers,
            timeout=timeout,
            transport=self.transport,
        ) as client:
            existing = await client.get(
                f"/repos/{remote.owner}/{remote.name}/pulls",
                params={
                    "state": "open",
                    "head": f"{remote.owner}:{branch}",
                    "base": repository.default_branch,
                },
            )
            existing.raise_for_status()
            items = existing.json()
            if items:
                item = items[0]
                return self._result(
                    remote=remote,
                    branch=branch,
                    commit_sha=commit_sha,
                    item=item,
                    created=False,
                )

            response = await client.post(
                f"/repos/{remote.owner}/{remote.name}/pulls",
                json={
                    "title": payload.title,
                    "head": branch,
                    "base": repository.default_branch,
                    "body": payload.body,
                    "draft": payload.draft,
                },
            )
            response.raise_for_status()
            return self._result(
                remote=remote,
                branch=branch,
                commit_sha=commit_sha,
                item=response.json(),
                created=True,
            )

    @staticmethod
    def _result(
        *,
        remote: GitHubRepositoryRef,
        branch: str,
        commit_sha: str,
        item: object,
        created: bool,
    ) -> PublishResult:
        if not isinstance(item, dict):
            raise RuntimeError("GitHub returned an invalid pull request response")
        url = item.get("html_url")
        number = item.get("number")
        if not isinstance(url, str) or not isinstance(number, int):
            raise RuntimeError("GitHub pull request response is missing URL or number")
        return PublishResult(
            mode="live",
            repository=remote.full_name,
            branch=branch,
            commit_sha=commit_sha,
            pull_request_url=url,
            pull_request_number=number,
            created=created,
            commands_run=[
                f"git push --set-upstream origin {branch}",
                "GitHub REST: list/create pull request",
            ],
        )

    async def _git_text(self, cwd: Path, *args: str) -> str:
        stdout, _ = await self._git(cwd, *args)
        return stdout.rstrip()

    async def _git(self, cwd: Path, *args: str) -> tuple[str, str]:
        command = ["git", "-C", str(cwd), *args]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        stdout_text = stdout.decode(errors="replace")
        stderr_text = stderr.decode(errors="replace")
        if process.returncode != 0:
            raise InvalidRepositoryError(
                str(cwd),
                stderr_text.strip() or stdout_text.strip() or "Git command failed",
            )
        return stdout_text, stderr_text


def parse_github_remote(value: str) -> GitHubRepositoryRef:
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme == "https" and (parsed.username or parsed.password):
        raise ValueError("credential-bearing Git remote URLs are not supported")
    for pattern in (_HTTPS_REMOTE, _SCP_REMOTE, _SSH_REMOTE):
        match = pattern.fullmatch(normalized)
        if match is not None:
            return GitHubRepositoryRef(
                owner=match.group("owner"),
                name=match.group("name"),
            )
    raise ValueError("only github.com HTTPS or SSH remotes are supported")


def publisher_from_settings(settings: Settings) -> Publisher:
    if settings.github_publish_mode == "fake":
        return FakeGitHubPublisher()
    return LiveGitHubPublisher(settings)
