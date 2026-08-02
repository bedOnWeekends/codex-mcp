from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .errors import InvalidRepositoryError
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


class GitWorktreeManager:
    """Creates isolated run worktrees and inspects their uncommitted changes."""

    def __init__(self, *, branch_prefix: str) -> None:
        self.branch_prefix = branch_prefix

    async def ensure(
        self,
        *,
        repository: Repository,
        run_id: UUID,
        path: Path,
    ) -> WorktreeInfo:
        root = repository.root_path.resolve()
        destination = path.expanduser().resolve()
        if not root.is_dir():
            raise InvalidRepositoryError(str(root), "repository directory is missing")

        branch = f"{self.branch_prefix}{run_id.hex}"
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

    async def changed_files(self, path: Path) -> list[str]:
        output = await self._git_text(path, "status", "--porcelain=v1")
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
