from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


class CodexSdkUnavailableError(RuntimeError):
    pass


@dataclass(slots=True)
class CodexRunResult:
    thread_id: str
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class CodexClient:
    """Thin adapter around openai-codex. SDK imports are delayed for CLI startup."""

    def __init__(self, *, model: str | None, approval_policy: str, sandbox_mode: str) -> None:
        self.model = model
        self.approval_policy = approval_policy
        self.sandbox_mode = sandbox_mode

    async def run(
        self,
        *,
        prompt: str,
        cwd: Path,
        thread_id: str | None = None,
    ) -> CodexRunResult:
        try:
            from codex import Codex  # type: ignore[import-not-found]
        except ImportError:
            try:
                from openai_codex import Codex  # type: ignore[import-not-found,no-redef]
            except ImportError as exc:
                raise CodexSdkUnavailableError(
                    "Could not import the openai-codex SDK. Reinstall project dependencies "
                    "and verify the package's import name in the installed version."
                ) from exc



        options: dict[str, Any] = {
            "approval_policy": self.approval_policy,
            "sandbox_mode": self.sandbox_mode,
        }

        if self.model:
            options["model"] = self.model

        client = cast(Any, Codex(options))

        thread = (
            await client.resume_thread(thread_id)
            if thread_id
            else await client.start_thread()
        )

        result = await thread.run(prompt)

        resolved_thread_id = str(getattr(result, "thread_id", None) or "")
        text = str(getattr(result, "text", None) or result)
        usage = getattr(result, "usage", None)
        return CodexRunResult(
            thread_id=resolved_thread_id,
            text=text,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )
