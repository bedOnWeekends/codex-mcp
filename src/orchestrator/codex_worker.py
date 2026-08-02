from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from .artifacts import ArtifactWriter
from .codex_client import CodexClient, CodexRunner, FakeCodexClient
from .context_builder import build_task_prompt
from .model_router import choose_model
from .schemas import ArtifactKind, Repository, Run, Task, TaskKind
from .settings import Settings
from .store import Store
from .verification import VerificationRunner
from .worktree import GitWorktreeManager

logger = logging.getLogger(__name__)
ClientFactory = Callable[[Task, Run], CodexRunner]


class CodexWorker:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        *,
        worktrees: GitWorktreeManager | None = None,
        verifier: VerificationRunner | None = None,
        artifacts: ArtifactWriter | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.worktrees = worktrees or GitWorktreeManager(
            branch_prefix=settings.worktree_branch_prefix
        )
        self.verifier = verifier or VerificationRunner(
            default_timeout_seconds=settings.verification_timeout_seconds
        )
        assert settings.artifacts_dir is not None
        self.artifacts = artifacts or ArtifactWriter(settings.artifacts_dir)
        self.client_factory = client_factory

    async def process_one(self) -> bool:
        task = await self.store.claim_next_task()
        if task is None:
            return False
        try:
            run = await self.store.get_run(task.run_id)
            repository = await self.store.get_repository(run.repository_id)
            if task.kind is TaskKind.REVIEW:
                await self._process_review(task, run, repository)
            else:
                await self._process_codex_task(task, run, repository)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("worker task failed", extra={"task_id": str(task.id)})
            await self.store.fail_or_retry_task(
                task.id,
                error_summary=f"{type(exc).__name__}: {exc}",
            )
        return True

    async def _process_codex_task(
        self,
        task: Task,
        run: Run,
        repository: Repository,
    ) -> None:
        workspace = await self._workspace_for(task, run, repository)
        client = self._client_for(task, run)
        result = await client.run(
            prompt=build_task_prompt(
                repository,
                run,
                task,
                workspace=workspace,
            ),
            cwd=workspace,
            thread_id=task.codex_thread_id,
        )
        estimated_cost = Decimal("0")
        if task.kind is TaskKind.PLAN:
            await self.store.complete_plan_task(
                task.id,
                summary=result.text,
                codex_thread_id=result.thread_id or None,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                estimated_cost_usd=estimated_cost,
            )
            return
        if task.kind not in {TaskKind.IMPLEMENT, TaskKind.FIX}:
            raise ValueError(f"Unsupported Codex task kind: {task.kind.value}")

        changed_files = await self.worktrees.changed_files(workspace)
        diff = await self.worktrees.diff(workspace)
        await self._record_artifact(
            run_id=run.id,
            task_id=task.id,
            kind=ArtifactKind.DIFF,
            filename="changes.diff",
            content=diff,
        )
        await self.store.complete_write_task_and_queue_review(
            task.id,
            summary=result.text,
            changed_files=changed_files,
            codex_thread_id=result.thread_id or None,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost_usd=estimated_cost,
            review_instruction=self._build_review_instruction(changed_files),
            review_max_attempts=self.settings.max_attempts_per_task,
        )

    async def _process_review(
        self,
        task: Task,
        run: Run,
        repository: Repository,
    ) -> None:
        if task.worktree_path is None:
            raise ValueError("review task has no worktree path")
        workspace_info = await self.worktrees.ensure(
            repository=repository,
            run_id=run.id,
            path=task.worktree_path,
        )
        verification = await self.verifier.run(
            cwd=workspace_info.path,
            configured_commands=repository.verification_config,
        )
        summary = verification.summary()
        await self._record_artifact(
            run_id=run.id,
            task_id=task.id,
            kind=ArtifactKind.TEST_RESULT,
            filename="verification.txt",
            content=summary,
        )
        await self.store.complete_review_task(
            task.id,
            success=verification.success,
            summary=summary,
            commands_run=verification.commands_run,
            fix_instruction=self._build_fix_instruction(summary),
            max_fix_cycles=self.settings.max_fix_cycles,
            fix_max_attempts=self.settings.max_attempts_per_task,
        )

    async def _workspace_for(
        self,
        task: Task,
        run: Run,
        repository: Repository,
    ) -> Path:
        if task.kind is TaskKind.PLAN:
            return repository.root_path
        if task.kind not in {TaskKind.IMPLEMENT, TaskKind.FIX}:
            raise ValueError(f"Task kind does not use a Codex workspace: {task.kind}")
        if task.worktree_path is None:
            raise ValueError("write task has no worktree path")
        info = await self.worktrees.ensure(
            repository=repository,
            run_id=run.id,
            path=task.worktree_path,
        )
        return info.path

    def _client_for(self, task: Task, run: Run) -> CodexRunner:
        if self.client_factory is not None:
            return self.client_factory(task, run)
        if self.settings.codex_mode == "fake":
            return FakeCodexClient(
                delay_seconds=self.settings.fake_codex_delay_seconds
            )
        model = choose_model(
            settings=self.settings,
            tier=task.model_tier,
            risk=run.risk_level,
            kind=task.kind,
        )
        sandbox_mode = (
            "read-only"
            if task.kind is TaskKind.PLAN
            else self.settings.codex_sandbox_mode
        )
        return CodexClient(
            model=model,
            approval_policy=self.settings.codex_approval_policy,
            sandbox_mode=sandbox_mode,
        )

    async def _record_artifact(
        self,
        *,
        run_id: UUID,
        task_id: UUID,
        kind: ArtifactKind,
        filename: str,
        content: str,
    ) -> None:
        try:
            data = await self.artifacts.write_text(
                run_id=run_id,
                task_id=task_id,
                kind=kind,
                filename=filename,
                content=content,
            )
            await self.store.create_artifact(data)
        except Exception:
            logger.exception(
                "artifact recording failed",
                extra={"run_id": str(run_id), "task_id": str(task_id)},
            )

    @staticmethod
    def _build_review_instruction(changed_files: list[str]) -> str:
        files = "\n".join(f"- {item}" for item in changed_files) or "- None"
        return (
            "Run the administrator-registered verification commands in the isolated "
            "worktree. Do not modify files.\n\nChanged files:\n"
            f"{files}"
        )

    @staticmethod
    def _build_fix_instruction(verification_summary: str) -> str:
        clipped = verification_summary[-20_000:]
        return (
            "Fix only the failures reported by the latest verification run. Preserve "
            "the approved plan and do not commit, merge, push, deploy, or access live "
            "credentials.\n\nVerification output:\n"
            f"{clipped}"
        )
