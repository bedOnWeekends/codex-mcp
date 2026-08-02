from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
_MODEL_DEFAULTS = {
    "codex_model_cheap": "gpt-5.6-luna",
    "codex_model_default": "gpt-5.6-terra",
    "codex_model_critical": "gpt-5.6-sol",
}


class Settings(BaseSettings):
    """Process configuration loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ORCH_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "codex-orchestrator"
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["critical", "error", "warning", "info", "debug"] = "info"
    server_host: str = "127.0.0.1"
    server_port: int = Field(default=8000, ge=1, le=65_535)
    mcp_path: str = "/mcp"
    mcp_json_response: bool = True
    mcp_stateless_http: bool = True
    database_url: str = Field(
        default="postgresql+asyncpg://orchestrator:orchestrator@127.0.0.1:5433/orchestrator",
        repr=False,
    )
    database_echo: bool = False
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=300)
    runtime_dir: Path = Path("runtime")
    worktrees_dir: Path | None = None
    artifacts_dir: Path | None = None
    logs_dir: Path | None = None
    max_parallel_workers: int = Field(default=3, ge=1, le=16)
    max_agents_per_run: int = Field(default=4, ge=2, le=4)
    max_tokens_per_run: int = Field(default=250_000, ge=10_000, le=5_000_000)
    max_dependency_summary_chars: int = Field(default=1_200, ge=256, le=4_000)
    scout_review_confidence_threshold: float = Field(default=0.72, ge=0, le=1)
    max_attempts_per_task: int = Field(default=2, ge=1, le=10)
    max_replans: int = Field(default=1, ge=0, le=5)
    max_fix_cycles: int = Field(default=2, ge=0, le=10)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_id: str = "local-worker"

    codex_mode: Literal["fake", "live"] = "fake"
    fake_codex_delay_seconds: float = Field(default=0.0, ge=0, le=30)
    codex_model_cheap: str = _MODEL_DEFAULTS["codex_model_cheap"]
    codex_model_default: str = _MODEL_DEFAULTS["codex_model_default"]
    codex_model_critical: str = _MODEL_DEFAULTS["codex_model_critical"]
    codex_effort_scout: ReasoningEffort = "low"
    codex_effort_plan: ReasoningEffort = "medium"
    codex_effort_default: ReasoningEffort = "high"
    codex_effort_critical: ReasoningEffort = "medium"
    codex_effort_retry: ReasoningEffort = "high"
    codex_approval_policy: str = "never"
    codex_sandbox_mode: str = "workspace-write"

    codex_price_cheap_input_per_mtok: Decimal = Field(
        default=Decimal("1.00"), ge=0
    )
    codex_price_cheap_cached_input_per_mtok: Decimal = Field(
        default=Decimal("0.10"), ge=0
    )
    codex_price_cheap_output_per_mtok: Decimal = Field(
        default=Decimal("6.00"), ge=0
    )
    codex_price_default_input_per_mtok: Decimal = Field(
        default=Decimal("2.50"), ge=0
    )
    codex_price_default_cached_input_per_mtok: Decimal = Field(
        default=Decimal("0.25"), ge=0
    )
    codex_price_default_output_per_mtok: Decimal = Field(
        default=Decimal("15.00"), ge=0
    )
    codex_price_critical_input_per_mtok: Decimal = Field(
        default=Decimal("5.00"), ge=0
    )
    codex_price_critical_cached_input_per_mtok: Decimal = Field(
        default=Decimal("0.50"), ge=0
    )
    codex_price_critical_output_per_mtok: Decimal = Field(
        default=Decimal("30.00"), ge=0
    )
    codex_cache_write_multiplier: Decimal = Field(
        default=Decimal("1.25"), ge=1
    )
    projected_call_cost_usd_cheap: Decimal = Field(
        default=Decimal("0.05"), ge=0
    )
    projected_call_cost_usd_default: Decimal = Field(
        default=Decimal("0.45"), ge=0
    )
    projected_call_cost_usd_critical: Decimal = Field(
        default=Decimal("1.00"), ge=0
    )
    projected_call_tokens_cheap: int = Field(default=12_000, ge=1_000, le=500_000)
    projected_call_tokens_default: int = Field(
        default=60_000, ge=1_000, le=1_000_000
    )
    projected_call_tokens_critical: int = Field(
        default=100_000, ge=1_000, le=2_000_000
    )
    budget_reserve_usd: Decimal = Field(default=Decimal("0.05"), ge=0)

    verification_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    worktree_branch_prefix: str = "orchestrator/run-"
    github_publish_mode: Literal["fake", "live"] = "fake"
    github_token: SecretStr | None = Field(default=None, repr=False)
    github_remote_name: str = "origin"
    github_api_url: str = "https://api.github.com"
    github_api_version: str = "2026-03-10"
    github_request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    @field_validator("codex_approval_policy")
    @classmethod
    def validate_codex_approval_policy(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        if normalized == "never":
            return "deny_all"
        if normalized not in {"deny_all", "auto_review"}:
            raise ValueError(
                "codex_approval_policy must be 'deny_all' or 'auto_review'"
            )
        return normalized

    @field_validator("codex_sandbox_mode")
    @classmethod
    def validate_codex_sandbox_mode(cls, value: str) -> str:
        normalized = value.strip().lower().replace("_", "-")
        if normalized not in {"read-only", "workspace-write"}:
            raise ValueError(
                "codex_sandbox_mode must be 'read-only' or 'workspace-write'"
            )
        return normalized

    @field_validator(
        "codex_model_cheap",
        "codex_model_default",
        "codex_model_critical",
        mode="before",
    )
    @classmethod
    def normalize_model_name(cls, value: object, info: ValidationInfo) -> str:
        normalized = str(value or "").strip()
        return normalized or _MODEL_DEFAULTS[info.field_name]

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "database_url must use the postgresql+asyncpg:// async driver"
            )
        return value

    @field_validator("mcp_path")
    @classmethod
    def validate_mcp_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("/"):
            raise ValueError("mcp_path must start with '/'")
        if normalized != "/" and normalized.endswith("/"):
            normalized = normalized.rstrip("/")
        return normalized

    @field_validator("runtime_dir", mode="before")
    @classmethod
    def expand_runtime_dir(cls, value: str | Path) -> Path:
        return Path(value).expanduser()

    @field_validator("worktree_branch_prefix")
    @classmethod
    def validate_branch_prefix(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("worktree_branch_prefix must not be empty")
        if normalized.startswith("-") or " " in normalized:
            raise ValueError("worktree_branch_prefix is not a safe Git ref prefix")
        return normalized

    @field_validator("github_remote_name")
    @classmethod
    def validate_github_remote_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or normalized.startswith("-"):
            raise ValueError("github_remote_name must be a safe Git remote name")
        if any(character.isspace() for character in normalized):
            raise ValueError("github_remote_name must not contain whitespace")
        return normalized

    @field_validator("github_api_url")
    @classmethod
    def validate_github_api_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if normalized != "https://api.github.com":
            raise ValueError("Phase 6 supports only https://api.github.com")
        return normalized

    @field_validator("github_api_version")
    @classmethod
    def validate_github_api_version(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != "2026-03-10":
            raise ValueError("Phase 6 requires GitHub REST API version 2026-03-10")
        return normalized

    @model_validator(mode="after")
    def derive_runtime_paths(self) -> Settings:
        runtime = self.runtime_dir.resolve()
        self.runtime_dir = runtime
        self.worktrees_dir = (self.worktrees_dir or runtime / "worktrees").resolve()
        self.artifacts_dir = (self.artifacts_dir or runtime / "artifacts").resolve()
        self.logs_dir = (self.logs_dir or runtime / "logs").resolve()
        return self

    def ensure_runtime_directories(self) -> None:
        for path in (
            self.runtime_dir,
            self.worktrees_dir,
            self.artifacts_dir,
            self.logs_dir,
        ):
            assert path is not None
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
