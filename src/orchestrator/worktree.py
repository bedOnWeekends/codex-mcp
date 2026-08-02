from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .errors import InvalidRepositoryError, NoChangesToCommitError
from .schemas import Repository


class GitCommandError(RuntimeError):
    def __init__(self, command: list[str], stderr: str) -> None:
        self.command = command
        self.stderr = stderr
        super().__init__(f"Git command failed: {' '.join(command)}\n{stderr}")


@dataclass(frozen=True, slots=True)
class WorktreeInfo:
    path: Path
    branch: str


@dataclass(frozen=True, slots=True)
class DeliveryCommit:
    sha: str
    branch: str
    changed_files: list[str]
    reused: bool = False


@dataclass(frozen=True, slots=True)
class AgentCommit:
    sha: str
    branch: str
    changed_files: list[str]
    reused: bool = False


@dataclass(frozen=True, slots=True)
class IntegrationResult:
    branch: str
    applied_commits: list[str]
    changed_files: list[str]


class GitWorktreeManager:
    """Creates isolated run and agent worktrees and integrates verified commits."""

    def __init__(self, *, branch_prefix: str) -> None:
        self.branch_prefix = branch_prefix

    async def ensure(
        self,
        *,
        repository: Repository,
        run_id: UUID,
        path: Path,
    ) -> WorktreeInfo:
        return await self._ensure_named_worktree(
            repository=repository,
            path=path,
            branch=f"{self.branch_prefix}{run_id.hex}",
        )

    async def ensure_agent(
        self,
        *,
        repository: Repository,
        run_id: UUID,
        assignment_key: str,
        path: Path,
    ) -> WorktreeInfo:
        return await self._ensure_named_worktree(
            repository=repository,
            path=path,
            branch=f"{self.branch_prefix}{run_id.hex}/agent-{assignment_key}",
        )

    async def prepare_agent_attempt(self, path: Path) -> None:
        """Discard only uncommitted residue from a private agent worktree."""
        await self._verify_existing_worktree(path)
        await self._git(path, "reset", "--hard", "HEAD")
        await self._git(path, "clean", "-fd")

    async def _ensure_named_worktree(
        self,
        *,
        repository: Repository,
        path: Path,
        branch: str,
    ) -> WorktreeInfo:
        root = repository.root_path.resolve()
        destination = path.expanduser().resolve()
        if not root.is_dir():
            raise InvalidRepositoryError(str(root), "repository directory is missing")

        if destination.exists():
            await self._verify_existing_worktree(destination)
            current_branch = await self._git_text(
                destination, "branch", "--show-current"
            )
            if current_branch and current_branch != branch:
                raise InvalidRepositoryError(
                    str(destination),
                    f"worktree is on branch {current_branch!r}, expected {branch!r}",
                )
            return WorktreeInfo(path=destination, branch=current_branch or branch)

        destination.parent.mkdir(parents=True, exist_ok=True)
        await self._git(root, "worktree", "prune")
        await self._git(root, "rev-parse", "--verify", repository.default_branch)

        branch_exists = await self._git_succeeds(
            root,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
        )
        if branch_exists:
            await self._git(root, "worktree", "add", str(destination), branch)
        else:
            await self._git(
                root,
                "worktree",
                "add",
                "-b",
                branch,
                str(destination),
                repository.default_branch,
            )
        return WorktreeInfo(path=destination, branch=branch)

    async def apply_commits(
        self,
        path: Path,
        commits: list[str],
    ) -> IntegrationResult:
        """Commit dependency changes into an agent's private worktree."""
        await self._verify_existing_worktree(path)
        await self._ensure_clean(path)
        branch = await self._git_text(path, "branch", "--show-current")
        if not branch:
            raise InvalidRepositoryError(
                str(path), "commit integration requires a named worktree branch"
            )

        applied: list[str] = []
        changed_files: set[str] = set()
        for commit_sha in commits:
            await self._git(path, "rev-parse", "--verify", f"{commit_sha}^{{commit}}")
            changed_files.update(await self._commit_changed_files(path, commit_sha))
            if await self._git_succeeds(
                path,
                "merge-base",
                "--is-ancestor",
                commit_sha,
                "HEAD",
            ):
                continue
            try:
                await self._git(path, "cherry-pick", commit_sha)
            except GitCommandError as exc:
                await self._git_best_effort(path, "cherry-pick", "--abort")
                raise InvalidRepositoryError(
                    str(path),
                    f"agent dependency conflict for {commit_sha}: {exc.stderr}",
                ) from exc
            applied.append(commit_sha)

        await self._ensure_clean(path)
        return IntegrationResult(
            branch=branch,
            applied_commits=applied,
            changed_files=sorted(changed_files),
        )

    async def integrate_commits(
        self,
        path: Path,
        commits: list[str],
    ) -> IntegrationResult:
        """Stage all agent commits without committing the final run worktree."""
        await self._verify_existing_worktree(path)
        branch = await self._git_text(path, "branch", "--show-current")
        if not branch:
            raise InvalidRepositoryError(
                str(path), "final integration requires a named worktree branch"
            )

        await self._git(path, "reset", "--hard", "HEAD")
        await self._git(path, "clean", "-fd")
        applied: list[str] = []
        changed_files: set[str] = set()
        for commit_sha in commits:
            await self._git(path, "rev-parse", "--verify", f"{commit_sha}^{{commit}}")
            changed_files.update(await self._commit_changed_files(path, commit_sha))
            try:
                await self._git(path, "cherry-pick", "--no-commit", commit_sha)
            except GitCommandError as exc:
                await self._git_best_effort(path, "reset", "--hard", "HEAD")
                await self._git_best_effort(path, "clean", "-fd")
                raise InvalidRepositoryError(
                    str(path),
                    f"agent commit integration conflict for {commit_sha}: {exc.stderr}",
                ) from exc
            applied.append(commit_sha)

        await self._git(path, "diff", "--cached", "--check")
        staged = await self._git_text(path, "diff", "--cached", "--name-only")
        staged_files = sorted({item for item in staged.splitlines() if item})
        if staged_files != sorted(changed_files):
            raise InvalidRepositoryError(
                str(path),
                "staged integration files do not match the agent commit manifest",
            )
        return IntegrationResult(
            branch=branch,
            applied_commits=applied,
            changed_files=staged_files,
        )

    async def _commit_changed_files(self, path: Path, commit_sha: str) -> set[str]:
        names = await self._git_text(
            path,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            commit_sha,
        )
        return {item for item in names.splitlines() if item}

    async def changed_files(self, path: Path) -> list[str]:
        output = await self._git_text(
            path,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        files: list[str] = []
        for line in output.splitlines():
            if len(line) < 4:
                continue
            candidate = line[3:]
            if " -> " in candidate:
                candidate = candidate.rsplit(" -> ", 1)[1]
            files.append(candidate.strip('"'))
        return files

    async def diff(self, path: Path) -> str:
        diff = await self._git_text(
            path,
            "diff",
            "--no-ext-diff",
            "--binary",
            "HEAD",
        )
        untracked = await self._git_text(
            path,
            "ls-files",
            "--others",
            "--exclude-standard",
        )
        if not untracked:
            return diff
        suffix = "\n# Untracked files\n" + "\n".join(
            f"# - {item}" for item in untracked.splitlines()
        )
        return diff + suffix + "\n"

    async def snapshot(self, path: Path) -> str:
        status = await self._git_text(
            path,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        staged = await self._git_text(
            path,
            "diff",
            "--cached",
            "--binary",
            "HEAD",
        )
        unstaged = await self._git_text(path, "diff", "--binary")
        digest = hashlib.sha256()
        digest.update(b"status\0")
        digest.update(status.encode("utf-8", errors="replace"))
        digest.update(b"\0staged\0")
        digest.update(staged.encode("utf-8", errors="replace"))
        digest.update(b"\0unstaged\0")
        digest.update(unstaged.encode("utf-8", errors="replace"))
        for relative_path in sorted(await self.changed_files(path)):
            digest.update(b"\0path\0")
            digest.update(relative_path.encode("utf-8", errors="replace"))
            candidate = path / relative_path
            if candidate.is_symlink():
                digest.update(b"\0symlink\0")
                digest.update(
                    str(candidate.readlink()).encode("utf-8", errors="replace")
                )
            elif candidate.is_file():
                digest.update(b"\0file\0")
                digest.update(candidate.read_bytes())
            elif candidate.exists():
                digest.update(b"\0other\0")
            else:
                digest.update(b"\0missing\0")
        return digest.hexdigest()

    async def commit_agent_changes(
        self,
        path: Path,
        *,
        message: str,
        run_id: UUID,
        assignment_id: UUID,
    ) -> AgentCommit:
        await self._verify_existing_worktree(path)
        branch = await self._git_text(path, "branch", "--show-current")
        if not branch:
            raise InvalidRepositoryError(
                str(path), "agent commit requires a named worktree branch"
            )
        reusable = await self._is_agent_commit(
            path,
            run_id=run_id,
            assignment_id=assignment_id,
        )
        changed_files = await self.changed_files(path)
        if not changed_files:
            return await self._reuse_agent_commit(
                path,
                branch=branch,
                run_id=run_id,
                assignment_id=assignment_id,
            )

        await self._git(path, "diff", "--check")
        await self._git(path, "add", "--all")
        await self._git(path, "diff", "--cached", "--check")
        if not await self._git_text(path, "diff", "--cached", "--name-only"):
            raise NoChangesToCommitError(str(path), "agent has no staged changes")
        identity = [
            "-c",
            "user.name=Codex Orchestrator",
            "-c",
            "user.email=codex-orchestrator@localhost",
        ]
        if reusable:
            await self._git(
                path,
                *identity,
                "commit",
                "--amend",
                "--no-edit",
                "--no-gpg-sign",
            )
        else:
            await self._git(
                path,
                *identity,
                "commit",
                "--no-gpg-sign",
                "-m",
                message,
                "-m",
                f"Orchestrator-Run: {run_id}\nOrchestrator-Agent: {assignment_id}",
            )
        await self._ensure_clean(path)
        sha = await self._git_text(path, "rev-parse", "HEAD")
        cumulative = await self._commit_changed_files(path, sha)
        return AgentCommit(
            sha=sha,
            branch=branch,
            changed_files=sorted(cumulative),
        )

    async def _is_agent_commit(
        self,
        path: Path,
        *,
        run_id: UUID,
        assignment_id: UUID,
    ) -> bool:
        body = await self._git_text(path, "log", "-1", "--format=%B")
        lines = body.splitlines()
        return (
            f"Orchestrator-Run: {run_id}" in lines
            and f"Orchestrator-Agent: {assignment_id}" in lines
        )

    async def _reuse_agent_commit(
        self,
        path: Path,
        *,
        branch: str,
        run_id: UUID,
        assignment_id: UUID,
    ) -> AgentCommit:
        if not await self._is_agent_commit(
            path,
            run_id=run_id,
            assignment_id=assignment_id,
        ):
            raise NoChangesToCommitError(str(path), "agent has no changes to commit")
        sha = await self._git_text(path, "rev-parse", "HEAD")
        changed = await self._commit_changed_files(path, sha)
        return AgentCommit(
            sha=sha,
            branch=branch,
            changed_files=sorted(changed),
            reused=True,
        )

    async def commit_verified_changes(
        self,
        path: Path,
        *,
        message: str,
        run_id: UUID,
    ) -> DeliveryCommit:
        await self._verify_existing_worktree(path)
        branch = await self._git_text(path, "branch", "--show-current")
        if not branch:
            raise InvalidRepositoryError(
                str(path), "delivery commit requires a named worktree branch"
            )

        changed_files = await self.changed_files(path)
        if not changed_files:
            return await self._reuse_delivery_commit(
                path,
                branch=branch,
                run_id=run_id,
            )

        await self._git(path, "diff", "--check")
        await self._git(path, "diff", "--cached", "--check")
        await self._git(path, "add", "--all")
        await self._git(path, "diff", "--cached", "--check")
        staged_files = await self._git_text(path, "diff", "--cached", "--name-only")
        if not staged_files:
            raise NoChangesToCommitError(
                str(path), "no staged changes remain to commit"
            )

        await self._git(
            path,
            "-c",
            "user.name=Codex Orchestrator",
            "-c",
            "user.email=codex-orchestrator@localhost",
            "commit",
            "--no-gpg-sign",
            "-m",
            message,
            "-m",
            f"Orchestrator-Run: {run_id}",
        )
        sha = await self._git_text(path, "rev-parse", "HEAD")
        await self._ensure_clean(path)
        return DeliveryCommit(
            sha=sha,
            branch=branch,
            changed_files=changed_files,
        )

    async def _reuse_delivery_commit(
        self,
        path: Path,
        *,
        branch: str,
        run_id: UUID,
    ) -> DeliveryCommit:
        body = await self._git_text(path, "log", "-1", "--format=%B")
        if f"Orchestrator-Run: {run_id}" not in body.splitlines():
            raise NoChangesToCommitError(str(path), "worktree has no changes to commit")
        sha = await self._git_text(path, "rev-parse", "HEAD")
        changed = await self._commit_changed_files(path, sha)
        return DeliveryCommit(
            sha=sha,
            branch=branch,
            changed_files=sorted(changed),
            reused=True,
        )

    async def _ensure_clean(self, path: Path) -> None:
        status = await self._git_text(
            path,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        if status:
            raise InvalidRepositoryError(str(path), "worktree must be clean")

    async def _verify_existing_worktree(self, path: Path) -> None:
        top_level = await self._git_text(path, "rev-parse", "--show-toplevel")
        if Path(top_level).resolve() != path.resolve():
            raise InvalidRepositoryError(
                str(path), "existing path is not the expected Git worktree root"
            )

    async def _git_text(self, cwd: Path, *args: str) -> str:
        stdout, _ = await self._git(cwd, *args)
        return stdout.rstrip()

    async def _git_succeeds(self, cwd: Path, *args: str) -> bool:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
        return process.returncode == 0

    async def _git_best_effort(self, cwd: Path, *args: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd),
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.communicate()

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
            raise GitCommandError(command, stderr_text.strip() or stdout_text.strip())
        return stdout_text, stderr_text
