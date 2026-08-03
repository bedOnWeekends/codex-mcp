from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .mcp_schemas import GetRunOutput, validate_conventional_title
from .schemas import OrchestratorModel, RiskLevel, RunStatus


class ApiExecutionMode(StrEnum):
    AUTO_PR = "auto_pr"


class AutomationStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class StartAutomatedRunInput(OrchestratorModel):
    repository: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=20_000)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=100)
    max_cost_usd: Decimal = Field(default=Decimal("3.00"), gt=0, le=100)
    risk_level: RiskLevel = RiskLevel.NORMAL
    execution_mode: Literal[ApiExecutionMode.AUTO_PR] = ApiExecutionMode.AUTO_PR
    commit_message: str = Field(
        default="feat: implement requested change",
        min_length=1,
        max_length=100,
    )
    pull_request_title: str = Field(
        default="feat: implement requested change",
        min_length=1,
        max_length=256,
    )
    pull_request_body: str = Field(default="", max_length=60_000)

    @field_validator("constraints")
    @classmethod
    def normalize_constraints(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if item and item not in seen:
                normalized.append(item)
                seen.add(item)
        return normalized

    @field_validator("acceptance_criteria")
    @classmethod
    def normalize_acceptance_criteria(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item or item in seen:
                continue
            if len(item) > 2_000:
                raise ValueError("acceptance criteria must be at most 2000 characters")
            normalized.append(item)
            seen.add(item)
        if not normalized:
            raise ValueError(
                "auto_pr requires at least one explicit acceptance criterion"
            )
        return normalized

    @field_validator("commit_message", "pull_request_title")
    @classmethod
    def validate_conventional_titles(cls, value: str) -> str:
        return validate_conventional_title(value)

    @model_validator(mode="after")
    def reject_high_risk_automation(self) -> StartAutomatedRunInput:
        if self.risk_level not in {RiskLevel.LOW, RiskLevel.NORMAL}:
            raise ValueError("auto_pr accepts only low or normal risk runs")
        return self


class StartAutomatedRunOutput(OrchestratorModel):
    run_id: UUID
    status: RunStatus
    version: int
    automation_status: AutomationStatus
    idempotent_replay: bool
    status_url: str


class AutomatedRunOutput(OrchestratorModel):
    run: GetRunOutput
    execution_mode: ApiExecutionMode
    automation_status: AutomationStatus
    last_error: str | None = None
    commit_sha: str | None = None
    branch: str | None = None
    pull_request_url: str | None = None
    pull_request_number: int | None = None
    created_at: datetime
    updated_at: datetime


class CancelAutomatedRunInput(OrchestratorModel):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2_000)


class CancelAutomatedRunOutput(OrchestratorModel):
    run_id: UUID
    status: RunStatus
    version: int
    automation_status: AutomationStatus
    canceled_task_ids: list[UUID]
    message: str
