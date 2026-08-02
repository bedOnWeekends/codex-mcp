from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select

from .db_models import (
    AgentAssignmentModel,
    ApprovalModel,
    RunModel,
    TaskModel,
    TaskResultModel,
)
from .errors import EntityNotFoundError
from .phase4_store import TaskFailureOutcome
from .phase6_store import Phase6Store
from .schemas import (
    AgentAssignment,
    AgentAssignmentStatus,
    AgentPlan,
    AgentRole,
    AgentSpec,
    Approval,
    ApprovalType,
    ExecutionMode,
    ModelTier,
    Run,
    RunStatus,
    Task,
    TaskKind,
    TaskResult,
    TaskStatus,
)
from .state_machine import ensure_run_transition, ensure_task_transition


class Phase7Store(Phase6Store):
    """Durable adaptive-agent and integration workflow operations."""

    async def approve_plan_and_queue_supervision(
        self,
        run_id: UUID,
        *,
        expected_version: int,
        notes: str | None,
        instruction: str,
        model_tier: ModelTier,
        max_attempts: int,
    ) -> tuple[Run, Task, Approval]:
        async with self._session_factory.begin() as session:
            run_model = await self._locked_run(session, run_id)
            self._check_version(run_model, expected_version)
            ensure_run_transition(
                RunStatus(run_model.status),
                RunStatus.SUPERVISING,
            )
            approval = ApprovalModel(
                run_id=run_id,
                type=ApprovalType.PLAN.value,
                approved=True,
                notes=notes,
                expected_version=expected_version,
            )
            task = self._queued_task(
                run_id=run_id,
                kind=TaskKind.SUPERVISE,
                instruction=instruction,
                model_tier=model_tier,
                max_attempts=max_attempts,
                priority=95,
            )
            session.add_all([approval, task])
            await session.flush()
            run_model.status = RunStatus.SUPERVISING.value
            run_model.current_task_id = task.id
            run_model.version += 1
            self._event(
                session,
                run_id=run_id,
                task_id=task.id,
                event_type="run.adaptive_plan_approved",
                payload={"approval_notes": notes},
            )
            await session.flush()
            await session.refresh(run_model)
            await session.refresh(task)
            await session.refresh(approval)
            return (
                self._run(run_model),
                self._task(task),
                self._approval(approval),
            )

    async def complete_supervision_task(
        self,
        task_id: UUID,
        *,
        plan: AgentPlan,
        summary: str,
        codex_thread_id: str | None,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: Decimal,
        agents_root: Path,
        max_attempts: int,
    ) -> tuple[Task, Run, TaskResult, list[AgentAssignment]]:
        self._validate_usage(input_tokens, output_tokens, estimated_cost_usd)
        async with self._session_factory.begin() as session:
            task = await self._locked_task(session, task_id)
            if TaskKind(task.kind) is not TaskKind.SUPERVISE:
                raise ValueError("complete_supervision_task requires a SUPERVISE task")
            self._complete_task(
                task,
                codex_thread_id=codex_thread_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost_usd,
            )
            result = self._result(task_id, summary, True)
            session.add(result)

            run = await self._locked_run(session, task.run_id)
            self._charge_run(run, estimated_cost_usd)
            ensure_run_transition(RunStatus(run.status), RunStatus.EXECUTING)
            assignments: list[AgentAssignmentModel] = []
            for spec in plan.topological_order():
                assignment = AgentAssignmentModel(
                    run_id=run.id,
                    key=spec.key,
                    role=spec.role.value,
                    status=AgentAssignmentStatus.BLOCKED.value,
                    instruction=spec.instruction,
                    depends_on=spec.depends_on,
                    owned_paths=spec.owned_paths,
                    model_tier=(
                        ModelTier.CRITICAL.value
                        if spec.role is AgentRole.REVIEWER
                        else ModelTier.DEFAULT.value
                    ),
                    worktree_path=str((agents_root / spec.key).resolve()),
                )
                session.add(assignment)
                assignments.append(assignment)
            await session.flush()

            queued_tasks: list[TaskModel] = []
            ready_assignments = [item for item in assignments if not item.depends_on]
            for assignment in ready_assignments:
                queued = self._queue_assignment_task(
                    assignment,
                    max_attempts=max_attempts,
                )
                session.add(queued)
                queued_tasks.append(queued)
            await session.flush()
            for assignment, queued in zip(
                ready_assignments,
                queued_tasks,
                strict=True,
            ):
                assignment.task_id = queued.id
                assignment.status = AgentAssignmentStatus.QUEUED.value

            run.status = RunStatus.EXECUTING.value
            run.current_task_id = queued_tasks[0].id
            run.version += 1
            self._event(
                session,
                run_id=run.id,
                task_id=task_id,
                event_type="run.adaptive_agent_plan_created",
                payload={
                    "mode": plan.mode.value,
                    "confidence": plan.confidence,
                    "requires_llm_review": plan.requires_llm_review,
                    "assignments": [item.key for item in assignments],
                    "ready": [item.key for item in ready_assignments],
                },
            )
            await session.flush()
            await session.refresh(task)
            await session.refresh(run)
            await session.refresh(result)
            for assignment in assignments:
                await session.refresh(assignment)
            return (
                self._task(task),
                self._run(run),
                self._task_result(result),
                [self._agent_assignment(item) for item in assignments],
            )

    async def claim_next_task(self) -> Task | None:
        active = [
            RunStatus.PLANNING.value,
            RunStatus.SUPERVISING.value,
            RunStatus.EXECUTING.value,
            RunStatus.INTEGRATING.value,
            RunStatus.VERIFYING.value,
            RunStatus.DELIVERING.value,
            RunStatus.PUBLISHING.value,
        ]
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(TaskModel)
                .join(RunModel, RunModel.id == TaskModel.run_id)
                .where(
                    TaskModel.status == TaskStatus.QUEUED.value,
                    TaskModel.attempt < TaskModel.max_attempts,
                    RunModel.status.in_(active),
                )
                .order_by(TaskModel.priority.desc(), TaskModel.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if model is None:
                return None
            ensure_task_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
            model.status = TaskStatus.RUNNING.value
            model.attempt += 1
            model.started_at = datetime.now(UTC)
            model.completed_at = None
            if TaskKind(model.kind) is TaskKind.AGENT:
                assignment = await session.scalar(
                    select(AgentAssignmentModel)
                    .where(AgentAssignmentModel.task_id == model.id)
                    .with_for_update()
                )
                if assignment is None:
                    raise EntityNotFoundError("agent_assignment_for_task", model.id)
                assignment.status = AgentAssignmentStatus.RUNNING.value
                assignment.started_at = model.started_at
            await session.flush()
            await session.refresh(model)
            return self._task(model)

    async def total_tokens_for_run(self, run_id: UUID) -> int:
        async with self._session_factory() as session:
            total = await session.scalar(
                select(
                    func.coalesce(
                        func.sum(TaskModel.input_tokens + TaskModel.output_tokens),
                        0,
                    )
                ).where(TaskModel.run_id == run_id)
            )
            return int(total or 0)

    async def get_agent_assignment_for_task(
        self,
        task_id: UUID,
    ) -> AgentAssignment:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(AgentAssignmentModel).where(
                    AgentAssignmentModel.task_id == task_id
                )
            )
            if model is None:
                raise EntityNotFoundError("agent_assignment_for_task", task_id)
            return self._agent_assignment(model)

    async def list_agent_assignments(self, run_id: UUID) -> list[AgentAssignment]:
        async with self._session_factory() as session:
            if await session.get(RunModel, run_id) is None:
                raise EntityNotFoundError("run", run_id)
            models = list(
                await session.scalars(
                    select(AgentAssignmentModel)
                    .where(AgentAssignmentModel.run_id == run_id)
                    .order_by(
                        AgentAssignmentModel.created_at.asc(),
                        AgentAssignmentModel.key.asc(),
                    )
                )
            )
            return [self._agent_assignment(model) for model in models]

    async def dependency_context_for_assignment(
        self,
        assignment_id: UUID,
        *,
        max_summary_chars: int,
    ) -> list[str]:
        async with self._session_factory() as session:
            assignment = await session.get(AgentAssignmentModel, assignment_id)
            if assignment is None:
                raise EntityNotFoundError("agent_assignment", assignment_id)
            if not assignment.depends_on:
                return []
            dependencies = list(
                await session.scalars(
                    select(AgentAssignmentModel).where(
                        AgentAssignmentModel.run_id == assignment.run_id,
                        AgentAssignmentModel.key.in_(assignment.depends_on),
                    )
                )
            )
            by_key = {item.key: item for item in dependencies}
            contexts: list[str] = []
            for key in assignment.depends_on:
                dependency = by_key.get(key)
                if dependency is None:
                    raise ValueError(f"missing agent dependency {key!r}")
                result = None
                if dependency.task_id is not None:
                    result = await session.scalar(
                        select(TaskResultModel).where(
                            TaskResultModel.task_id == dependency.task_id
                        )
                    )
                summary = result.summary if result is not None else "No result summary."
                contexts.append(
                    f"{key}|{dependency.role}|{dependency.commit_sha or 'none'}|"
                    f"files={','.join(dependency.changed_files)}|"
                    f"summary={summary[:max_summary_chars]}"
                )
            return contexts

    async def dependency_commits_for_assignment(
        self,
        assignment_id: UUID,
    ) -> list[str]:
        async with self._session_factory() as session:
            assignment = await session.get(AgentAssignmentModel, assignment_id)
            if assignment is None:
                raise EntityNotFoundError("agent_assignment", assignment_id)
            models = list(
                await session.scalars(
                    select(AgentAssignmentModel).where(
                        AgentAssignmentModel.run_id == assignment.run_id
                    )
                )
            )
            return self._dependency_commits(assignment, models)

    async def complete_agent_task(
        self,
        task_id: UUID,
        *,
        summary: str,
        changed_files: list[str],
        commit_sha: str | None,
        codex_thread_id: str | None,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: Decimal,
        integration_worktree_path: Path,
        max_attempts: int,
    ) -> tuple[Task, Run, TaskResult, AgentAssignment, list[Task]]:
        self._validate_usage(input_tokens, output_tokens, estimated_cost_usd)
        async with self._session_factory.begin() as session:
            task = await self._locked_task(session, task_id)
            if TaskKind(task.kind) is not TaskKind.AGENT:
                raise ValueError("complete_agent_task requires an AGENT task")
            assignment = await session.scalar(
                select(AgentAssignmentModel)
                .where(AgentAssignmentModel.task_id == task_id)
                .with_for_update()
            )
            if assignment is None:
                raise EntityNotFoundError("agent_assignment_for_task", task_id)
            if (
                AgentAssignmentStatus(assignment.status)
                is not AgentAssignmentStatus.RUNNING
            ):
                raise ValueError("agent assignment is not running")

            self._complete_task(
                task,
                codex_thread_id=codex_thread_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost_usd,
            )
            result = self._result(
                task_id,
                summary,
                True,
                changed_files=changed_files,
            )
            session.add(result)
            assignment.status = AgentAssignmentStatus.COMPLETED.value
            assignment.codex_thread_id = codex_thread_id
            assignment.commit_sha = commit_sha
            assignment.changed_files = changed_files
            assignment.input_tokens += input_tokens
            assignment.output_tokens += output_tokens
            assignment.estimated_cost_usd += estimated_cost_usd
            assignment.completed_at = datetime.now(UTC)

            run = await self._locked_run(session, task.run_id)
            self._charge_run(run, estimated_cost_usd)
            models = list(
                await session.scalars(
                    select(AgentAssignmentModel)
                    .where(AgentAssignmentModel.run_id == run.id)
                    .with_for_update()
                )
            )
            by_key = {item.key: item for item in models}
            newly_queued: list[TaskModel] = []
            for candidate in models:
                if (
                    AgentAssignmentStatus(candidate.status)
                    is not AgentAssignmentStatus.BLOCKED
                ):
                    continue
                if all(
                    AgentAssignmentStatus(by_key[key].status)
                    is AgentAssignmentStatus.COMPLETED
                    for key in candidate.depends_on
                ):
                    queued = self._queue_assignment_task(
                        candidate,
                        max_attempts=max_attempts,
                    )
                    session.add(queued)
                    await session.flush()
                    candidate.task_id = queued.id
                    candidate.status = AgentAssignmentStatus.QUEUED.value
                    newly_queued.append(queued)

            all_completed = all(
                AgentAssignmentStatus(item.status) is AgentAssignmentStatus.COMPLETED
                for item in models
            )
            if all_completed:
                ensure_run_transition(RunStatus(run.status), RunStatus.INTEGRATING)
                integration = self._queued_task(
                    run_id=run.id,
                    kind=TaskKind.INTEGRATE,
                    instruction="Integrate completed agent commits in dependency order.",
                    model_tier=ModelTier.DEFAULT,
                    max_attempts=max_attempts,
                    priority=85,
                    worktree_path=integration_worktree_path,
                )
                session.add(integration)
                await session.flush()
                run.status = RunStatus.INTEGRATING.value
                run.current_task_id = integration.id
                newly_queued.append(integration)
                event_type = "run.adaptive_agents_completed"
            else:
                run.current_task_id = newly_queued[0].id if newly_queued else task_id
                event_type = "agent.assignment_completed"
            run.version += 1
            self._event(
                session,
                run_id=run.id,
                task_id=task_id,
                event_type=event_type,
                payload={
                    "assignment_key": assignment.key,
                    "commit_sha": commit_sha,
                    "queued_task_ids": [str(item.id) for item in newly_queued],
                },
            )
            await session.flush()
            await session.refresh(task)
            await session.refresh(run)
            await session.refresh(result)
            await session.refresh(assignment)
            for queued in newly_queued:
                await session.refresh(queued)
            return (
                self._task(task),
                self._run(run),
                self._task_result(result),
                self._agent_assignment(assignment),
                [self._task(item) for item in newly_queued],
            )

    async def integration_commits(self, run_id: UUID) -> list[str]:
        assignments = await self.list_agent_assignments(run_id)
        specs = [
            AgentSpec(
                key=item.key,
                role=item.role,
                instruction=item.instruction,
                depends_on=item.depends_on,
                owned_paths=item.owned_paths,
            )
            for item in assignments
        ]
        implementers = [item for item in assignments if item.role is AgentRole.IMPLEMENTER]
        reviewers = [item for item in assignments if item.role is AgentRole.REVIEWER]
        order = AgentPlan(
            mode=(
                ExecutionMode.PARALLEL
                if len(implementers) > 1
                else ExecutionMode.SINGLE
            ),
            confidence=1,
            requires_llm_review=bool(reviewers),
            rationale="Reconstructed persisted adaptive plan for integration.",
            assignments=specs,
        ).topological_order()
        by_key = {item.key: item for item in assignments}
        commits: list[str] = []
        for spec in order:
            assignment = by_key[spec.key]
            if assignment.role is AgentRole.IMPLEMENTER and assignment.commit_sha:
                commits.append(assignment.commit_sha)
        return commits

    async def complete_integration_task(
        self,
        task_id: UUID,
        *,
        summary: str,
        changed_files: list[str],
        applied_commits: list[str],
        review_instruction: str,
        review_max_attempts: int,
    ) -> tuple[Task, Task, Run, TaskResult]:
        async with self._session_factory.begin() as session:
            task = await self._locked_task(session, task_id)
            if TaskKind(task.kind) is not TaskKind.INTEGRATE:
                raise ValueError("complete_integration_task requires an INTEGRATE task")
            if not task.worktree_path:
                raise ValueError("integration task has no worktree path")
            self._complete_task(
                task,
                codex_thread_id=None,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=Decimal("0"),
            )
            result = self._result(
                task_id,
                summary,
                True,
                changed_files=changed_files,
                commands_run=[f"git cherry-pick {sha}" for sha in applied_commits],
            )
            review = self._queued_task(
                run_id=task.run_id,
                kind=TaskKind.REVIEW,
                instruction=review_instruction,
                model_tier=ModelTier.DEFAULT,
                max_attempts=review_max_attempts,
                priority=80,
                worktree_path=Path(task.worktree_path),
            )
            session.add_all([result, review])
            await session.flush()
            run = await self._locked_run(session, task.run_id)
            ensure_run_transition(RunStatus(run.status), RunStatus.VERIFYING)
            run.status = RunStatus.VERIFYING.value
            run.current_task_id = review.id
            run.version += 1
            self._event(
                session,
                run_id=run.id,
                task_id=task_id,
                event_type="run.agent_commits_integrated",
                payload={
                    "review_task_id": str(review.id),
                    "applied_commits": applied_commits,
                },
            )
            await session.flush()
            await session.refresh(task)
            await session.refresh(review)
            await session.refresh(run)
            await session.refresh(result)
            return (
                self._task(task),
                self._task(review),
                self._run(run),
                self._task_result(result),
            )

    async def fail_or_retry_task(
        self,
        task_id: UUID,
        *,
        error_summary: str,
    ) -> TaskFailureOutcome:
        outcome = await super().fail_or_retry_task(
            task_id,
            error_summary=error_summary,
        )
        if outcome.task.kind is not TaskKind.AGENT:
            return outcome
        async with self._session_factory.begin() as session:
            assignment = await session.scalar(
                select(AgentAssignmentModel)
                .where(AgentAssignmentModel.task_id == task_id)
                .with_for_update()
            )
            if assignment is not None:
                assignment.status = (
                    AgentAssignmentStatus.QUEUED.value
                    if outcome.retried
                    else AgentAssignmentStatus.FAILED.value
                )
                assignment.started_at = (
                    None if outcome.retried else assignment.started_at
                )
                assignment.completed_at = (
                    None if outcome.retried else datetime.now(UTC)
                )
        return outcome

    async def cancel_run(
        self,
        run_id: UUID,
        *,
        expected_version: int,
        reason: str | None = None,
    ) -> tuple[Run, list[UUID]]:
        run, task_ids = await super().cancel_run(
            run_id,
            expected_version=expected_version,
            reason=reason,
        )
        async with self._session_factory.begin() as session:
            models = list(
                await session.scalars(
                    select(AgentAssignmentModel)
                    .where(
                        AgentAssignmentModel.run_id == run_id,
                        AgentAssignmentModel.status.in_(
                            [
                                AgentAssignmentStatus.BLOCKED.value,
                                AgentAssignmentStatus.QUEUED.value,
                                AgentAssignmentStatus.RUNNING.value,
                            ]
                        ),
                    )
                    .with_for_update()
                )
            )
            for model in models:
                model.status = AgentAssignmentStatus.CANCELED.value
                model.completed_at = datetime.now(UTC)
        return run, task_ids

    @staticmethod
    def _queue_assignment_task(
        assignment: AgentAssignmentModel,
        *,
        max_attempts: int,
    ) -> TaskModel:
        return Phase7Store._queued_task(
            run_id=assignment.run_id,
            kind=TaskKind.AGENT,
            instruction=f"Execute agent assignment {assignment.key}",
            model_tier=ModelTier(assignment.model_tier),
            max_attempts=max_attempts,
            priority=90 if assignment.role == AgentRole.IMPLEMENTER.value else 70,
            worktree_path=(
                Path(assignment.worktree_path) if assignment.worktree_path else None
            ),
        )

    @staticmethod
    def _dependency_commits(
        assignment: AgentAssignmentModel,
        models: list[AgentAssignmentModel],
    ) -> list[str]:
        by_key = {item.key: item for item in models}
        ordered: list[str] = []
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visited:
                return
            dependency = by_key.get(key)
            if dependency is None:
                raise ValueError(f"missing agent dependency {key!r}")
            if (
                AgentAssignmentStatus(dependency.status)
                is not AgentAssignmentStatus.COMPLETED
            ):
                raise ValueError(f"agent dependency {key!r} is not completed")
            for parent in dependency.depends_on:
                visit(parent)
            if dependency.commit_sha is not None:
                ordered.append(dependency.commit_sha)
            visited.add(key)

        for key in assignment.depends_on:
            visit(key)
        return ordered

    @staticmethod
    def _charge_run(run: RunModel, amount: Decimal) -> None:
        projected = run.spent_cost_usd + amount
        if projected > run.max_cost_usd:
            raise ValueError(
                f"run cost budget exceeded: {projected} > {run.max_cost_usd}"
            )
        run.spent_cost_usd = projected

    @staticmethod
    def _agent_assignment(model: AgentAssignmentModel) -> AgentAssignment:
        return AgentAssignment(
            id=model.id,
            run_id=model.run_id,
            task_id=model.task_id,
            key=model.key,
            role=AgentRole(model.role),
            status=AgentAssignmentStatus(model.status),
            instruction=model.instruction,
            depends_on=model.depends_on,
            owned_paths=model.owned_paths,
            model_tier=ModelTier(model.model_tier),
            worktree_path=Path(model.worktree_path) if model.worktree_path else None,
            codex_thread_id=model.codex_thread_id,
            commit_sha=model.commit_sha,
            changed_files=model.changed_files,
            input_tokens=model.input_tokens,
            output_tokens=model.output_tokens,
            estimated_cost_usd=model.estimated_cost_usd,
            started_at=model.started_at,
            completed_at=model.completed_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
