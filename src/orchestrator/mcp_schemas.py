from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from .schemas import (
    ModelTier,
    OrchestratorModel,
    RiskLevel,
    RunStatus,
    TaskKind,
    TaskStatus,
)

CONVENTIONAL_COMMIT_PATTERN = re.compile(
    r"^(feat|fix|refactor|test|docs|chore|ci)"
    r"(?:\([a-z0-9][a-z0-9._/-]*\))?!?: [^\r\n]{1,72}$"
)


def validate_conventional_title(value: str) -> str:
    normalized = value.strip()
    if not CONVENTIONAL_COMMIT_PATTERN.fullmatch(normalized):
        raise ValueError(
            "value must follow Conventional Commits using one of "
            "feat, fix, refactor, test, docs, chore, or ci"
        )
    return normalized


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


class ApproveDeliveryInput(OrchestratorModel):
    run_id: UUID
    expected_version: int = Field(ge=1)
    commit_message: str = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=4_000)

    @field_validator("commit_message")
    @classmethod
    def validate_commit_message(cls, value: str) -> str:
        return validate_conventional_title(value)


class ApproveDeliveryOutput(OrchestratorModel):
    run_id: UUID
    status: RunStatus
    version: int
    delivery_task_id: UUID
    delivery_task_status: TaskStatus
    message: str


class ApprovePublishInput(OrchestratorModel):
    run_id: UUID
    expected_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=60_000)
    draft: Literal[True] = True
    notes: str | None = Field(default=None, max_length=4_000)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return validate_conventional_title(value)


class ApprovePublishOutput(OrchestratorModel):
    run_id: UUID
    status: RunStatus
    version: int
    publish_task_id: UUID
    publish_task_status: TaskStatus
    message: str


class FinishRunInput(OrchestratorModel):
    run_id: UUID
    expected_version: int = Field(ge=1)
    notes: str | None = Field(default=None, max_length=4_000)


class FinishRunOutput(OrchestratorModel):
    run_id: UUID
    status: RunStatus
    version: int
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
