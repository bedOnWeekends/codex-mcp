from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field

from .schemas import (
    ModelTier,
    OrchestratorModel,
    RiskLevel,
    RunStatus,
    TaskKind,
    TaskStatus,
)


class RepositorySummary(OrchestratorModel):
    id: UUID
    name: str
    default_branch: str
    verification_commands: list[str] = Field(default_factory=list)


class ListRepositoriesOutput(OrchestratorModel):
    repositories: list[RepositorySummary]


class CreateRunInput(OrchestratorModel):
    repository: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=20_000)
    constraints: list[str] = Field(default_factory=list)
    max_cost_usd: Decimal = Field(default=Decimal("3.00"), gt=0)
    risk_level: RiskLevel = RiskLevel.NORMAL


class CreateRunOutput(OrchestratorModel):
    run_id: UUID
    repository: str
    status: RunStatus
    version: int
    plan_task_id: UUID
    plan_task_status: TaskStatus
    message: str


class ApprovePlanInput(OrchestratorModel):
    run_id: UUID
    expected_version: int = Field(ge=1)
    notes: str | None = Field(default=None, max_length=4_000)


class ApprovePlanOutput(OrchestratorModel):
    run_id: UUID
    status: RunStatus
    version: int
    implementation_task_id: UUID
    implementation_task_status: TaskStatus
    message: str


class GetRunInput(OrchestratorModel):
    run_id: UUID


class TaskSummary(OrchestratorModel):
    id: UUID
    kind: TaskKind
    status: TaskStatus
    model_tier: ModelTier
    attempt: int
    max_attempts: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal
    codex_thread_id: str | None
    result_success: bool | None = None
    result_summary: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    started_at: datetime | None
    completed_at: datetime | None


class GetRunOutput(OrchestratorModel):
    run_id: UUID
    repository: str
    goal: str
    constraints: list[str]
    risk_level: RiskLevel
    status: RunStatus
    version: int
    max_cost_usd: Decimal
    spent_cost_usd: Decimal
    current_task_id: UUID | None
    plan: str | None
    tasks: list[TaskSummary]
    created_at: datetime
    updated_at: datetime


class CancelRunInput(OrchestratorModel):
    run_id: UUID
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2_000)


class CancelRunOutput(OrchestratorModel):
    run_id: UUID
    status: RunStatus
    version: int
    canceled_task_ids: list[UUID]
    message: str
