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
from .errors import NoChangesToCommitError
from .github_publisher import (
    Publisher,
    PublishTaskPayload,
    publisher_from_settings,
)
from .model_router import choose_model
from .multi_agent import (
    agent_commit_message,
    build_agent_prompt,
    build_supervisor_prompt,
    fake_agent_plan,
    parse_agent_plan,
    validate_agent_changes,
)
from .phase7_store import Phase7Store
from .schemas import (
    AgentRole,
    ArtifactKind,
    Repository,
    Run,
    Task,
    TaskKind,
)
from .settings import Settings
from .verification import VerificationRunner
from .worktree import GitWorktreeManager

logger = logging.getLogger(__name__)
ClientFactory = Callable[[Task, Run], CodexRunner]


class CodexWorker:
    def __init__(
        self,
        store: Phase7Store,
        settings: Settings,
        *,
        worktrees: GitWorktreeManager | None = None,
        verifier: VerificationRunner | None = None,
        artifacts: ArtifactWriter | None = None,
        publisher: Publisher | None = None,
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
        self.publisher = publisher or publisher_from_settings(settings)
        self.client_factory = client_factory

    async def process_one(self) -> bool:
        task = await self.store.claim_next_task()
        if task is None:
            return False
        try:
            run = await self.store.get_run(task.run_id)
            repository = await self.store.get_repository(run.repository_id)
            if task.kind is TaskKind.SUPERVISE:
                await self._process_supervisor(task, run, repository)
            elif task.kind is TaskKind.AGENT:
                await self._process_agent(task, run, repository)
            elif task.kind is TaskKind.INTEGRATE:
                await self._process_integration(task, run, repository)
            elif task.kind is TaskKind.REVIEW:
                await self._process_review(task, run, repository)
            elif task.kind is TaskKind.DELIVER:
                await self._process_delivery(task, run, repository)
            elif task.kind is TaskKind.PUBLISH:
                await self._process_publish(task, run, repository)
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

    async def _process_supervisor(
        self,
        task: Task,
        run: Run,
        repository: Repository,
    ) -> None:
        if self.settings.codex_mode == "fake":
            plan = fake_agent_plan()
            thread_id = None
            input_tokens = 0
            output_tokens = 0
        else:
            client = self._client_for(task, run, read_only=True)
            result = await client.run(
                prompt=(
                    build_supervisor_prompt(
                        repository,
                        run,
                        max_agents=self.settings.max_agents_per_run,
                    )
                    + f"\n\nApproval context:\n{task.instruction}"
                ),
                cwd=repository.root_path,
                thread_id=task.codex_thread_id,
            )
            plan = parse_agent_plan(result.text)
            thread_id = result.thread_id or None
            input_tokens = result.input_tokens
            output_tokens = result.output_tokens
        if len(plan.assignments) > self.settings.max_agents_per_run:
            raise ValueError(
                "supervisor produced more assignments than max_agents_per_run"
            )

        plan_json = plan.model_dump_json(indent=2)
        await self._record_artifact(
            run_id=run.id,
            task_id=task.id,
            kind=ArtifactKind.AGENT_PLAN,
            filename="agent-plan.json",
            content=plan_json,
        )
        assert self.settings.worktrees_dir is not None
        await self.store.complete_supervision_task(
            task.id,
            plan=plan,
            summary=(
                f"Created a validated DAG with {len(plan.assignments)} agent "
                "assignments."
            ),
            codex_thread_id=thread_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=Decimal("0"),
            agents_root=self.settings.worktrees_dir / str(run.id) / "agents",
            max_attempts=self.settings.max_attempts_per_task,
        )

    async def _process_agent(
        self,
        task: Task,
        run: Run,
        repository: Repository,
    ) -> None:
        assignment = await self.store.get_agent_assignment_for_task(task.id)
        if assignment.worktree_path is None:
            raise ValueError("agent assignment has no worktree path")
        workspace_info = await self.worktrees.ensure_agent(
            repository=repository,
            run_id=run.id,
            assignment_key=assignment.key,
            path=assignment.worktree_path,
        )
        dependency_commits = await self.store.dependency_commits_for_assignment(
            assignment.id
        )
        await self.worktrees.apply_commits(workspace_info.path, dependency_commits)
        dependency_context = await self.store.dependency_context_for_assignment(
            assignment.id
        )
        client = self._client_for(
            task,
            run,
            read_only=assignment.role is not AgentRole.IMPLEMENTER,
        )
        result = await client.run(
            prompt=build_agent_prompt(
                repository,
                run,
                assignment,
                dependency_context=dependency_context,
            ),
            cwd=workspace_info.path,
            thread_id=assignment.codex_thread_id,
        )
        changed_files = validate_agent_changes(
            assignment,
            await self.worktrees.changed_files(workspace_info.path),
        )
        diff = await self.worktrees.diff(workspace_info.path)
        await self._record_artifact(
            run_id=run.id,
            task_id=task.id,
            kind=ArtifactKind.AGENT_DIFF,
            filename=f"agent-{assignment.key}.diff",
            content=diff,
        )

        commit_sha: str | None = None
        if assignment.role is AgentRole.IMPLEMENTER:
            try:
                commit = await self.worktrees.commit_agent_changes(
                    workspace_info.path,
                    message=agent_commit_message(assignment.key),
                    run_id=run.id,
                    assignment_id=assignment.id,
                )
                commit_sha = commit.sha
                changed_files = commit.changed_files
            except NoChangesToCommitError:
                if self.settings.codex_mode != "fake":
                    raise

        assert self.settings.worktrees_dir is not None
        await self.store.complete_agent_task(
            task.id,
            summary=result.text,
            changed_files=changed_files,
            commit_sha=commit_sha,
            codex_thread_id=result.thread_id or None,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost_usd=Decimal("0"),
            integration_worktree_path=self.settings.worktrees_dir / str(run.id),
            max_attempts=self.settings.max_attempts_per_task,
        )

    async def _process_integration(
        self,
        task: Task,
        run: Run,
        repository: Repository,
    ) -> None:
        if task.worktree_path is None:
            raise ValueError("integration task has no worktree path")
        workspace_info = await self.worktrees.ensure(
            repository=repository,
            run_id=run.id,
            path=task.worktree_path,
        )
        commits = await self.store.integration_commits(run.id)
        integration = await self.worktrees.apply_commits(
            workspace_info.path,
            commits,
        )
        receipt = (
            f"branch={integration.branch}\n"
            "applied_commits=\n"
            + "\n".join(f"- {item}" for item in integration.applied_commits)
            + "\nchanged_files=\n"
            + "\n".join(f"- {item}" for item in integration.changed_files)
            + "\n"
        )
        await self._record_artifact(
            run_id=run.id,
            task_id=task.id,
            kind=ArtifactKind.INTEGRATION_RECEIPT,
            filename="integration-receipt.txt",
            content=receipt,
        )
        await self.store.complete_integration_task(
            task.id,
            summary=(
                f"Integrated {len(integration.applied_commits)} agent commits into "
                f"{integration.branch}."
            ),
            changed_files=integration.changed_files,
            applied_commits=integration.applied_commits,
            review_instruction=self._build_review_instruction(
                integration.changed_files
            ),
            review_max_attempts=self.settings.max_attempts_per_task,
        )

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

    async def _process_delivery(
        self,
        task: Task,
        run: Run,
        repository: Repository,
    ) -> None:
        if task.worktree_path is None:
            raise ValueError("delivery task has no worktree path")
        workspace_info = await self.worktrees.ensure(
            repository=repository,
            run_id=run.id,
            path=task.worktree_path,
        )
        snapshot_before = await self.worktrees.snapshot(workspace_info.path)
        verification = await self.verifier.run(
            cwd=workspace_info.path,
            configured_commands=repository.verification_config,
        )
        verification_summary = verification.summary()
        await self._record_artifact(
            run_id=run.id,
            task_id=task.id,
            kind=ArtifactKind.TEST_RESULT,
            filename="delivery-verification.txt",
            content=verification_summary,
        )
        if not verification.success:
            raise RuntimeError("delivery verification failed")
        snapshot_after = await self.worktrees.snapshot(workspace_info.path)
        if snapshot_after != snapshot_before:
            raise RuntimeError("verification commands modified the delivery worktree")

        try:
            commit = await self.worktrees.commit_verified_changes(
                workspace_info.path,
                message=task.instruction,
                run_id=run.id,
            )
        except NoChangesToCommitError:
            if self.settings.codex_mode != "fake":
                raise
            receipt = (
                "commit_sha=none\n"
                f"branch={workspace_info.branch}\n"
                "noop=true\n"
                "pushed=false\n"
                "merged=false\n"
            )
            await self._record_artifact(
                run_id=run.id,
                task_id=task.id,
                kind=ArtifactKind.DELIVERY_RECEIPT,
                filename="delivery-receipt.txt",
                content=receipt,
            )
            await self.store.complete_delivery_task(
                task.id,
                commit_sha=None,
                changed_files=[],
                commands_run=verification.commands_run,
            )
            return

        receipt = (
            f"commit_sha={commit.sha}\n"
            f"branch={commit.branch}\n"
            f"reused={str(commit.reused).lower()}\n"
            "noop=false\n"
            "pushed=false\n"
            "merged=false\n"
            "changed_files=\n"
            + "\n".join(f"- {item}" for item in commit.changed_files)
            + "\n"
        )
        await self._record_artifact(
            run_id=run.id,
            task_id=task.id,
            kind=ArtifactKind.DELIVERY_RECEIPT,
            filename="delivery-receipt.txt",
            content=receipt,
        )
        await self.store.complete_delivery_task(
            task.id,
            commit_sha=commit.sha,
            changed_files=commit.changed_files,
            commands_run=verification.commands_run,
        )

    async def _process_publish(
        self,
        task: Task,
        run: Run,
        repository: Repository,
    ) -> None:
        if task.worktree_path is None:
            raise ValueError("publish task has no worktree path")
        payload = PublishTaskPayload.model_validate_json(task.instruction)
        workspace_info = await self.worktrees.ensure(
            repository=repository,
            run_id=run.id,
            path=task.worktree_path,
        )
        publication = await self.publisher.publish(
            repository=repository,
            run_id=run.id,
            worktree=workspace_info.path,
            payload=payload,
        )
        receipt = (
            f"mode={publication.mode}\n"
            f"repository={publication.repository}\n"
            f"branch={publication.branch}\n"
            f"commit_sha={publication.commit_sha or 'none'}\n"
            f"pull_request_url={publication.pull_request_url or 'none'}\n"
            f"pull_request_number={publication.pull_request_number or 'none'}\n"
            f"created={str(publication.created).lower()}\n"
            "merged=false\n"
        )
        await self._record_artifact(
            run_id=run.id,
            task_id=task.id,
            kind=ArtifactKind.PUBLISH_RECEIPT,
            filename="publish-receipt.txt",
            content=receipt,
        )
        await self.store.complete_publish_task(
            task.id,
            publication=publication,
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

    def _client_for(
        self,
        task: Task,
        run: Run,
        *,
        read_only: bool = False,
    ) -> CodexRunner:
        if self.client_factory is not None:
            return self.client_factory(task, run)
        if self.settings.codex_mode == "fake":
            return FakeCodexClient(delay_seconds=self.settings.fake_codex_delay_seconds)
        model = choose_model(
            settings=self.settings,
            tier=task.model_tier,
            risk=run.risk_level,
            kind=task.kind,
        )
        sandbox_mode = (
            "read-only"
            if read_only or task.kind in {TaskKind.PLAN, TaskKind.SUPERVISE}
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
            "integration worktree. Do not modify files.\n\nChanged files:\n"
            f"{files}"
        )

    @staticmethod
    def _build_fix_instruction(verification_summary: str) -> str:
        clipped = verification_summary[-20_000:]
        return (
            "Fix only the failures reported by the latest verification run. Preserve "
            "the approved plan and integrated agent changes. Do not commit, merge, "
            "push, deploy, or access live credentials.\n\nVerification output:\n"
            f"{clipped}"
        )
