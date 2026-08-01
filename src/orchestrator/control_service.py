from __future__ import annotations

from uuid import UUID

from .mcp_schemas import (
    CancelRunInput,
    CancelRunOutput,
    CreateRunInput,
    CreateRunOutput,
    GetRunOutput,
    ListRepositoriesOutput,
    RepositorySummary,
    TaskSummary,
)
from .schemas import ModelTier, RiskLevel, RunCreate
from .settings import Settings
from .store import Store


class RunControlService:
    """Application service used by MCP tools and future local clients."""

    def __init__(self, store: Store, settings: Settings) -> None:
        self._store = store
        self._settings = settings

    async def list_repositories(self) -> ListRepositoriesOutput:
        repositories = await self._store.list_repositories()
        return ListRepositoriesOutput(
            repositories=[
                RepositorySummary(
                    id=repository.id,
                    name=repository.name,
                    default_branch=repository.default_branch,
                )
                for repository in repositories
            ]
        )

    async def create_run(self, request: CreateRunInput) -> CreateRunOutput:
        repository = await self._store.get_repository_by_name(request.repository)
        model_tier = (
            ModelTier.CRITICAL
            if request.risk_level is RiskLevel.CRITICAL
            else ModelTier.DEFAULT
        )
        run_data = RunCreate(
            repository_id=repository.id,
            goal=request.goal,
            constraints=request.constraints,
            risk_level=request.risk_level,
            max_cost_usd=request.max_cost_usd,
        )
        run, task = await self._store.create_run_with_initial_task(
            run_data,
            plan_instruction=self._build_plan_instruction(
                goal=run_data.goal,
                constraints=run_data.constraints,
            ),
            model_tier=model_tier,
            max_attempts=self._settings.max_attempts_per_task,
        )
        return CreateRunOutput(
            run_id=run.id,
            repository=repository.name,
            status=run.status,
            version=run.version,
            plan_task_id=task.id,
            plan_task_status=task.status,
            message="Run created and planning task queued.",
        )

    async def get_run(self, run_id: UUID) -> GetRunOutput:
        run = await self._store.get_run(run_id)
        repository = await self._store.get_repository(run.repository_id)
        tasks = await self._store.list_tasks_for_run(run.id)
        return GetRunOutput(
            run_id=run.id,
            repository=repository.name,
            goal=run.goal,
            constraints=run.constraints,
            risk_level=run.risk_level,
            status=run.status,
            version=run.version,
            max_cost_usd=run.max_cost_usd,
            spent_cost_usd=run.spent_cost_usd,
            current_task_id=run.current_task_id,
            plan=run.plan,
            tasks=[
                TaskSummary(
                    id=task.id,
                    kind=task.kind,
                    status=task.status,
                    model_tier=task.model_tier,
                    attempt=task.attempt,
                    max_attempts=task.max_attempts,
                    input_tokens=task.input_tokens,
                    output_tokens=task.output_tokens,
                    estimated_cost_usd=task.estimated_cost_usd,
                    started_at=task.started_at,
                    completed_at=task.completed_at,
                )
                for task in tasks
            ],
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    async def cancel_run(self, request: CancelRunInput) -> CancelRunOutput:
        run, canceled_task_ids = await self._store.cancel_run(
            request.run_id,
            expected_version=request.expected_version,
            reason=request.reason,
        )
        return CancelRunOutput(
            run_id=run.id,
            status=run.status,
            version=run.version,
            canceled_task_ids=canceled_task_ids,
            message="Run canceled.",
        )

    @staticmethod
    def _build_plan_instruction(*, goal: str, constraints: list[str]) -> str:
        constraint_lines = (
            "\n".join(f"- {item}" for item in constraints)
            if constraints
            else "- No additional constraints."
        )
        return (
            "Inspect the registered repository in read-only mode and produce an "
            "implementation plan. Do not modify files.\n\n"
            f"Goal:\n{goal}\n\n"
            f"Constraints:\n{constraint_lines}\n\n"
            "Return relevant files, current architecture, implementation steps, "
            "risks, and acceptance criteria."
        )
