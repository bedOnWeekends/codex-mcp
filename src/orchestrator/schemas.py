from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RunStatus(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    AWAITING_REVISION = "awaiting_revision"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class TaskStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class RiskLevel(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TaskKind(StrEnum):
    PLAN = "plan"
    EXPLORE = "explore"
    IMPLEMENT = "implement"
    FIX = "fix"
    REVIEW = "review"


class ModelTier(StrEnum):
    CHEAP = "cheap"
    DEFAULT = "default"
    CRITICAL = "critical"


class ApprovalType(StrEnum):
    PLAN = "plan"
    MERGE = "merge"


class ArtifactKind(StrEnum):
    PLAN = "plan"
    DIFF = "diff"
    STDOUT = "stdout"
    STDERR = "stderr"
    TEST_RESULT = "test_result"
    WORKER_RESULT = "worker_result"
    OTHER = "other"


class OrchestratorModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class VerificationCommandSpec(OrchestratorModel):
    """Trusted local verification command registered by an administrator."""

    name: str = Field(min_length=1, max_length=120)
    command: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("command")
    @classmethod
    def normalize_command(cls, values: list[str]) -> list[str]:
        normalized = [item.strip() for item in values]
        if any(not item for item in normalized):
            raise ValueError("verification command arguments must be non-empty")
        return normalized


class RepositoryCreate(OrchestratorModel):
    name: str = Field(min_length=1, max_length=120)
    root_path: Path
    default_branch: str = Field(default="main", min_length=1, max_length=200)
    verification_config: list[VerificationCommandSpec] = Field(default_factory=list)

    @field_validator("root_path")
    @classmethod
    def normalize_root_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()


class Repository(RepositoryCreate):
    id: UUID
    created_at: datetime
    updated_at: datetime


class RunCreate(OrchestratorModel):
    repository_id: UUID
    goal: str = Field(min_length=1, max_length=20_000)
    constraints: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.NORMAL
    max_cost_usd: Decimal = Field(default=Decimal("3.00"), gt=0)

    @field_validator("constraints")
    @classmethod
    def normalize_constraints(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in values:
            stripped = item.strip()
            if stripped and stripped not in seen:
                normalized.append(stripped)
                seen.add(stripped)
        return normalized


class Run(RunCreate):
    id: UUID
    status: RunStatus
    spent_cost_usd: Decimal
    plan: str | None = None
    current_task_id: UUID | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class TaskCreate(OrchestratorModel):
    run_id: UUID
    kind: TaskKind
    instruction: str = Field(min_length=1, max_length=40_000)
    model_tier: ModelTier = ModelTier.DEFAULT
    max_attempts: int = Field(default=2, ge=1, le=10)
    priority: int = Field(default=0, ge=-100, le=100)
    worktree_path: Path | None = None
    codex_thread_id: str | None = None


class Task(TaskCreate):
    id: UUID
    status: TaskStatus
    attempt: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TaskResultCreate(OrchestratorModel):
    task_id: UUID
    summary: str = Field(min_length=1)
    changed_files: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    success: bool


class TaskResult(TaskResultCreate):
    id: UUID
    created_at: datetime


class ApprovalCreate(OrchestratorModel):
    run_id: UUID
    type: ApprovalType
    approved: bool
    notes: str | None = None
    expected_version: int = Field(ge=1)


class Approval(ApprovalCreate):
    id: UUID
    created_at: datetime


class ArtifactCreate(OrchestratorModel):
    run_id: UUID
    task_id: UUID | None = None
    kind: ArtifactKind
    path: Path
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    size_bytes: int = Field(ge=0)


class Artifact(ArtifactCreate):
    id: UUID
    created_at: datetime


class EventCreate(OrchestratorModel):
    run_id: UUID
    task_id: UUID | None = None
    event_type: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


class Event(EventCreate):
    id: UUID
    created_at: datetime
