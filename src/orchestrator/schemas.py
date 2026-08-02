from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RunStatus(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    SUPERVISING = "supervising"
    EXECUTING = "executing"
    INTEGRATING = "integrating"
    VERIFYING = "verifying"
    AWAITING_REVISION = "awaiting_revision"
    AWAITING_DELIVERY_APPROVAL = "awaiting_delivery_approval"
    DELIVERING = "delivering"
    AWAITING_PUBLISH_APPROVAL = "awaiting_publish_approval"
    PUBLISHING = "publishing"
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
    SUPERVISE = "supervise"
    AGENT = "agent"
    INTEGRATE = "integrate"
    EXPLORE = "explore"
    IMPLEMENT = "implement"
    FIX = "fix"
    REVIEW = "review"
    DELIVER = "deliver"
    PUBLISH = "publish"


class AgentRole(StrEnum):
    EXPLORER = "explorer"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"


class ExecutionMode(StrEnum):
    SINGLE = "single"
    PARALLEL = "parallel"


class AgentAssignmentStatus(StrEnum):
    BLOCKED = "blocked"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class ModelTier(StrEnum):
    CHEAP = "cheap"
    DEFAULT = "default"
    CRITICAL = "critical"


class ApprovalType(StrEnum):
    PLAN = "plan"
    DELIVERY = "delivery"
    PUBLISH = "publish"
    MERGE = "merge"


class ArtifactKind(StrEnum):
    PLAN = "plan"
    AGENT_PLAN = "agent_plan"
    AGENT_DIFF = "agent_diff"
    INTEGRATION_RECEIPT = "integration_receipt"
    DIFF = "diff"
    STDOUT = "stdout"
    STDERR = "stderr"
    TEST_RESULT = "test_result"
    WORKER_RESULT = "worker_result"
    DELIVERY_RECEIPT = "delivery_receipt"
    PUBLISH_RECEIPT = "publish_receipt"
    OTHER = "other"


class OrchestratorModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AgentSpec(OrchestratorModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    role: AgentRole
    instruction: str = Field(min_length=1, max_length=8_000)
    depends_on: list[str] = Field(default_factory=list, max_length=4)
    owned_paths: list[str] = Field(default_factory=list, max_length=24)

    @field_validator("depends_on")
    @classmethod
    def normalize_dependencies(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in normalized:
                normalized.append(item)
        return normalized

    @field_validator("owned_paths")
    @classmethod
    def normalize_owned_paths(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            candidate = value.strip().replace("\\", "/").removeprefix("./")
            path = PurePosixPath(candidate)
            if (
                not candidate
                or path.is_absolute()
                or ".." in path.parts
                or ".git" in path.parts
                or any(
                    token in candidate for token in ("\x00", ":", "*", "?", "[", "]")
                )
            ):
                raise ValueError(
                    "owned paths must be safe repository-relative prefixes"
                )
            item = path.as_posix().rstrip("/")
            if item not in normalized:
                normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def validate_role_contract(self) -> AgentSpec:
        if self.role is AgentRole.IMPLEMENTER and not self.owned_paths:
            raise ValueError("implementer agents require at least one owned path")
        if self.role is not AgentRole.IMPLEMENTER and self.owned_paths:
            raise ValueError("read-only agents cannot own paths")
        return self


class AgentPlan(OrchestratorModel):
    mode: ExecutionMode
    confidence: float = Field(ge=0, le=1)
    requires_llm_review: bool = False
    rationale: str = Field(min_length=1, max_length=600)
    assignments: list[AgentSpec] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_dag_and_ownership(self) -> AgentPlan:
        by_key = {item.key: item for item in self.assignments}
        if len(by_key) != len(self.assignments):
            raise ValueError("agent assignment keys must be unique")

        explorers = [
            item for item in self.assignments if item.role is AgentRole.EXPLORER
        ]
        implementers = [
            item for item in self.assignments if item.role is AgentRole.IMPLEMENTER
        ]
        reviewers = [
            item for item in self.assignments if item.role is AgentRole.REVIEWER
        ]
        if explorers:
            raise ValueError("the supervisor scout replaces separate explorer agents")
        if not implementers:
            raise ValueError("agent plan requires at least one implementer")
        if len(reviewers) > 1:
            raise ValueError("agent plan allows at most one LLM reviewer")
        if self.mode is ExecutionMode.SINGLE and len(implementers) != 1:
            raise ValueError("single mode requires exactly one implementer")
        if self.mode is ExecutionMode.PARALLEL and not 2 <= len(implementers) <= 3:
            raise ValueError("parallel mode requires two or three implementers")
        if self.requires_llm_review != bool(reviewers):
            raise ValueError(
                "requires_llm_review must match the presence of one reviewer"
            )

        for item in self.assignments:
            unknown = [key for key in item.depends_on if key not in by_key]
            if unknown:
                raise ValueError(
                    f"agent {item.key!r} has unknown dependencies: {unknown}"
                )
            if item.key in item.depends_on:
                raise ValueError(f"agent {item.key!r} cannot depend on itself")
        for implementer in implementers:
            if implementer.depends_on:
                raise ValueError(
                    "implementers must be independent; use one implementer when work "
                    "cannot be split cleanly"
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise ValueError("agent dependency graph contains a cycle")
            if key in visited:
                return
            visiting.add(key)
            for dependency in by_key[key].depends_on:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in by_key:
            visit(key)

        implementer_keys = {item.key for item in implementers}
        for reviewer in reviewers:
            if set(reviewer.depends_on) != implementer_keys:
                raise ValueError(
                    "the reviewer must depend on exactly every implementer assignment"
                )

        ownership: list[tuple[str, str]] = []
        for assignment in implementers:
            for path in assignment.owned_paths:
                for owner_key, owner_path in ownership:
                    if _path_prefixes_overlap(path, owner_path):
                        raise ValueError(
                            "implementer path ownership overlaps between "
                            f"{owner_key!r} and {assignment.key!r}"
                        )
                ownership.append((assignment.key, path))
        return self

    def topological_order(self) -> list[AgentSpec]:
        by_key = {item.key: item for item in self.assignments}
        remaining = set(by_key)
        ordered: list[AgentSpec] = []
        completed: set[str] = set()
        while remaining:
            ready = sorted(
                key
                for key in remaining
                if set(by_key[key].depends_on).issubset(completed)
            )
            if not ready:
                raise ValueError("agent dependency graph contains a cycle")
            for key in ready:
                ordered.append(by_key[key])
                completed.add(key)
                remaining.remove(key)
        return ordered


class AgentHandoff(OrchestratorModel):
    summary: str = Field(min_length=1, max_length=800)
    risks: list[str] = Field(default_factory=list, max_length=5)
    tests: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("risks", "tests")
    @classmethod
    def trim_entries(cls, values: list[str]) -> list[str]:
        return [item.strip()[:240] for item in values if item.strip()]


def _path_prefixes_overlap(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts
    shorter = min(len(left_parts), len(right_parts))
    return left_parts[:shorter] == right_parts[:shorter]


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


class AgentAssignment(OrchestratorModel):
    id: UUID
    run_id: UUID
    task_id: UUID | None = None
    key: str
    role: AgentRole
    status: AgentAssignmentStatus
    instruction: str
    depends_on: list[str]
    owned_paths: list[str]
    model_tier: ModelTier
    worktree_path: Path | None = None
    codex_thread_id: str | None = None
    commit_sha: str | None = None
    changed_files: list[str]
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
