from __future__ import annotations

import logging
from decimal import Decimal

from .codex_client import CodexClient
from .context_builder import build_task_prompt
from .model_router import choose_model
from .schemas import RunStatus, TaskKind, TaskResultCreate, TaskStatus
from .settings import Settings
from .store import Store

logger = logging.getLogger(__name__)


class CodexWorker:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings

    async def process_one(self) -> bool:
        task = await self.store.claim_next_task()
        if task is None:
            return False
        run = await self.store.get_run(task.run_id)
        repository = await self.store.get_repository(run.repository_id)
        model = choose_model(settings=self.settings, tier=task.model_tier, risk=run.risk_level, kind=task.kind)
        client = CodexClient(model=model, approval_policy=self.settings.codex_approval_policy, sandbox_mode="read-only" if task.kind is TaskKind.PLAN else self.settings.codex_sandbox_mode)
        try:
            result = await client.run(
                prompt=build_task_prompt(repository, run, task),
                cwd=repository.root_path,
                thread_id=task.codex_thread_id,
            )
            await self.store.transition_task(task.id, target=TaskStatus.COMPLETED, codex_thread_id=result.thread_id or None)
            await self.store.record_task_usage(task.id, input_tokens=result.input_tokens, output_tokens=result.output_tokens, estimated_cost_usd=Decimal("0"))
            await self.store.create_task_result(TaskResultCreate(task_id=task.id, summary=result.text, success=True))
            if task.kind is TaskKind.PLAN:
                latest = await self.store.get_run(run.id)
                await self.store.transition_run(
                    run.id, expected_version=latest.version,
                    target=RunStatus.AWAITING_PLAN_APPROVAL,
                    plan=result.text, current_task_id=task.id,
                )
            return True
        except Exception:
            logger.exception("worker task failed", extra={"task_id": str(task.id)})
            await self.store.transition_task(task.id, target=TaskStatus.FAILED)
            raise
