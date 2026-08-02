from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .settings import ReasoningEffort


class CodexSdkUnavailableError(RuntimeError):
    pass


@dataclass(slots=True)
class CodexRunResult:
    thread_id: str
    text: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0


class CodexRunner(Protocol):
    async def run(
        self,
        *,
        prompt: str,
        cwd: Path,
        thread_id: str | None = None,
        output_schema: dict[str, object] | None = None,
    ) -> CodexRunResult: ...


class CodexClient:
    """Typed adapter around the official asynchronous OpenAI Codex SDK."""

    def __init__(
        self,
        *,
        model: str,
        effort: ReasoningEffort,
        approval_policy: str,
        sandbox_mode: str,
    ) -> None:
        self.model = model
        self.effort = effort
        self.approval_policy = approval_policy
        self.sandbox_mode = sandbox_mode

    async def run(
        self,
        *,
        prompt: str,
        cwd: Path,
        thread_id: str | None = None,
        output_schema: dict[str, object] | None = None,
    ) -> CodexRunResult:
        try:
            from openai_codex import ApprovalMode, AsyncCodex, Sandbox
        except ImportError as exc:
            raise CodexSdkUnavailableError(
                "Could not import the official openai-codex SDK. Reinstall project "
                "dependencies with `python -m pip install -e .`."
            ) from exc

        approval_mode = self._approval_mode(
            value=self.approval_policy,
            deny_all=ApprovalMode.deny_all,
            auto_review=ApprovalMode.auto_review,
        )
        sandbox = self._sandbox(
            value=self.sandbox_mode,
            read_only=Sandbox.read_only,
            workspace_write=Sandbox.workspace_write,
        )
        cwd_text = str(cwd.resolve())

        async with AsyncCodex() as codex:
            if thread_id:
                thread = await codex.thread_resume(
                    thread_id,
                    approval_mode=approval_mode,
                    cwd=cwd_text,
                    model=self.model,
                    sandbox=sandbox,
                )
            else:
                thread = await codex.thread_start(
                    approval_mode=approval_mode,
                    cwd=cwd_text,
                    model=self.model,
                    sandbox=sandbox,
                )

            result = await thread.run(
                prompt,
                approval_mode=approval_mode,
                cwd=cwd_text,
                effort=self.effort,
                model=self.model,
                output_schema=output_schema,
                sandbox=sandbox,
            )

        usage = result.usage.last if result.usage is not None else None
        return CodexRunResult(
            thread_id=thread.id,
            text=result.final_response or "",
            input_tokens=usage.input_tokens if usage is not None else 0,
            cached_input_tokens=(
                getattr(usage, "cached_input_tokens", 0) if usage is not None else 0
            ),
            cache_write_tokens=(
                getattr(usage, "cache_write_tokens", 0) if usage is not None else 0
            ),
            output_tokens=usage.output_tokens if usage is not None else 0,
        )

    @staticmethod
    def _approval_mode[ApprovalT](
        *,
        value: str,
        deny_all: ApprovalT,
        auto_review: ApprovalT,
    ) -> ApprovalT:
        normalized = value.strip().lower().replace("-", "_")
        if normalized in {"never", "deny_all"}:
            return deny_all
        if normalized in {"auto_review", "on_request"}:
            return auto_review
        raise ValueError(
            "Unsupported Codex approval policy. Use 'deny_all' or 'auto_review'."
        )

    @staticmethod
    def _sandbox[SandboxT](
        *,
        value: str,
        read_only: SandboxT,
        workspace_write: SandboxT,
    ) -> SandboxT:
        normalized = value.strip().lower().replace("_", "-")
        if normalized == "read-only":
            return read_only
        if normalized == "workspace-write":
            return workspace_write
        raise ValueError(
            "Unsupported Codex sandbox mode. Use 'read-only' or 'workspace-write'."
        )


class FakeCodexClient:
    """Deterministic, zero-cost Codex substitute for local end-to-end testing."""

    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds

    async def run(
        self,
        *,
        prompt: str,
        cwd: Path,
        thread_id: str | None = None,
        output_schema: dict[str, object] | None = None,
    ) -> CodexRunResult:
        del output_schema
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        task_kind = "unknown"
        for line in prompt.splitlines():
            if line.startswith("Task kind: "):
                task_kind = line.partition(": ")[2].strip()
                break
        return CodexRunResult(
            thread_id=thread_id or f"fake-thread-{uuid4()}",
            text=(
                f"Fake Codex completed task kind '{task_kind}' in {cwd}. "
                "No external model call was made and no files were modified."
            ),
        )
