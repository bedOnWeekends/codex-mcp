from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.codex_client import CodexClient, FakeCodexClient


@pytest.mark.asyncio
async def test_fake_codex_client_never_calls_external_model(tmp_path: Path) -> None:
    result = await FakeCodexClient().run(
        prompt="Task kind: plan\nInspect only.",
        cwd=tmp_path,
    )
    assert result.thread_id.startswith("fake-thread-")
    assert "No external model call" in result.text
    assert result.input_tokens == 0
    assert result.output_tokens == 0


@pytest.mark.asyncio
async def test_live_adapter_uses_official_async_sdk_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeApprovalMode:
        deny_all = "deny-all"
        auto_review = "auto-review"

    class FakeSandbox:
        read_only = "read-only-enum"
        workspace_write = "workspace-write-enum"

    class FakeUsageBreakdown:
        input_tokens = 17
        output_tokens = 5

    class FakeUsage:
        last = FakeUsageBreakdown()

    class FakeResult:
        final_response = "implemented"
        usage = FakeUsage()

    class FakeThread:
        id = "thr-new"

        async def run(self, prompt: str, **kwargs: object) -> FakeResult:
            calls.append(("run", (prompt, kwargs)))
            return FakeResult()

    class FakeAsyncCodex:
        async def __aenter__(self) -> "FakeAsyncCodex":
            calls.append(("enter", None))
            return self

        async def __aexit__(self, *args: object) -> None:
            calls.append(("exit", None))

        async def thread_start(self, **kwargs: object) -> FakeThread:
            calls.append(("thread_start", kwargs))
            return FakeThread()

    monkeypatch.setitem(
        sys.modules,
        "openai_codex",
        SimpleNamespace(
            ApprovalMode=FakeApprovalMode,
            AsyncCodex=FakeAsyncCodex,
            Sandbox=FakeSandbox,
        ),
    )
    result = await CodexClient(
        model="gpt-test",
        approval_policy="never",
        sandbox_mode="workspace-write",
    ).run(prompt="make a safe change", cwd=tmp_path)

    assert result.thread_id == "thr-new"
    assert result.text == "implemented"
    assert result.input_tokens == 17
    assert result.output_tokens == 5
    assert [name for name, _ in calls] == ["enter", "thread_start", "run", "exit"]


@pytest.mark.asyncio
async def test_live_adapter_resumes_existing_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resumed: list[str] = []

    class FakeApprovalMode:
        deny_all = "deny-all"
        auto_review = "auto-review"

    class FakeSandbox:
        read_only = "read-only-enum"
        workspace_write = "workspace-write-enum"

    class FakeThread:
        id = "thr-existing"

        async def run(self, _prompt: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(final_response="continued", usage=None)

    class FakeAsyncCodex:
        async def __aenter__(self) -> "FakeAsyncCodex":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def thread_resume(
            self,
            thread_id: str,
            **_kwargs: object,
        ) -> FakeThread:
            resumed.append(thread_id)
            return FakeThread()

    monkeypatch.setitem(
        sys.modules,
        "openai_codex",
        SimpleNamespace(
            ApprovalMode=FakeApprovalMode,
            AsyncCodex=FakeAsyncCodex,
            Sandbox=FakeSandbox,
        ),
    )
    result = await CodexClient(
        model=None,
        approval_policy="auto_review",
        sandbox_mode="read-only",
    ).run(prompt="continue", cwd=tmp_path, thread_id="thr-existing")

    assert resumed == ["thr-existing"]
    assert result.thread_id == "thr-existing"
    assert result.text == "continued"
