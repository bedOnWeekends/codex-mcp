from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        default=(
            "postgresql+asyncpg://orchestrator:orchestrator@"
            "localhost:5432/orchestrator"
        ),
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
    max_attempts_per_task: int = Field(default=2, ge=1, le=10)
    max_replans: int = Field(default=1, ge=0, le=5)

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

    @model_validator(mode="after")
    def derive_runtime_paths(self) -> "Settings":
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
