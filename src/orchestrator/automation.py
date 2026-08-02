from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID

from .api_schemas import (
    AutomatedRunOutput,
    AutomationStatus,
    CancelAutomatedRunInput,
    CancelAutomatedRunOutput,
    StartAutomatedRunInput,
    StartAutomatedRunOutput,
)
from .automation_store import AutomationRunRecord, AutomationStore
from .control_service import RunControlService
from .errors import ConcurrentUpdateError, OrchestratorError
from .mcp_schemas import (
    ApproveDeliveryInput,
    ApprovePlanInput,
    ApprovePublishInput,
    CancelRunInput,
)
from .schemas import RunStatus
from .settings import Settings

logger = logging.getLogger(__name__)


class IdempotencyConflictError(OrchestratorError):
    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            f"Idempotency-Key {idempotency_key!r} was already used for a "
            "different request"
        )


@dataclass(slots=True)
class AutomationCoordinator:
    store: AutomationStore
    service: RunControlService
    settings: Settings

    async def start_run(
        self,
        request: StartAutomatedRunInput,
        *,
        idempotency_key: str,
    ) -> StartAutomatedRunOutput:
        request_hash = self._request_hash(request)
        record, created = await self.store.create_or_get(
            request=request,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            max_attempts=self.settings.max_attempts_per_task,
        )
        if record.request_hash != request_hash:
            raise IdempotencyConflictError(idempotency_key)
        run = await self.service.get_run(record.run_id)
        return StartAutomatedRunOutput(
            run_id=record.run_id,
            status=run.status,
            version=run.version,
            automation_status=record.status,
            idempotent_replay=not created,
            status_url=f"{self.settings.api_prefix}/runs/{record.run_id}",
        )

    async def get_run(self, run_id: UUID) -> AutomatedRunOutput:
        record = await self.store.get(run_id)
        run = await self.service.get_run(run_id)
        return self._output(record, run)

    async def cancel_run(
        self,
        run_id: UUID,
        request: CancelAutomatedRunInput,
    ) -> CancelAutomatedRunOutput:
        output = await self.service.cancel_run(
            CancelRunInput(
                run_id=run_id,
                expected_version=request.expected_version,
                reason=request.reason,
            )
        )
        await self.store.set_status(
            run_id,
            AutomationStatus.CANCELED,
            last_error=request.reason,
        )
        return CancelAutomatedRunOutput(
            run_id=output.run_id,
            status=output.status,
            version=output.version,
            automation_status=AutomationStatus.CANCELED,
            canceled_task_ids=output.canceled_task_ids,
            message=output.message,
        )

    @staticmethod
    def _request_hash(request: StartAutomatedRunInput) -> str:
        payload = json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _output(record: AutomationRunRecord, run: object) -> AutomatedRunOutput:
        from .mcp_schemas import GetRunOutput

        validated_run = GetRunOutput.model_validate(run)
        return AutomatedRunOutput(
            run=validated_run,
            execution_mode=record.execution_mode,
            automation_status=record.status,
            last_error=record.last_error,
            commit_sha=record.commit_sha,
            branch=record.branch,
            pull_request_url=record.pull_request_url,
            pull_request_number=record.pull_request_number,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class AutoPrDriver:
    def __init__(
        self,
        store: AutomationStore,
        service: RunControlService,
        settings: Settings,
    ) -> None:
        self._store = store
        self._service = service
        self._settings = settings
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="orchestrator-auto-pr-driver",
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                records = await self._store.list_active(
                    limit=self._settings.api_max_active_runs
                )
                for record in records:
                    await self.advance(record)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Auto-PR driver iteration failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._settings.api_poll_interval_seconds,
                )
            except TimeoutError:
                continue

    async def advance(self, record: AutomationRunRecord) -> None:
        try:
            run = await self._service.get_run(record.run_id)
            status = run.status
            if status is RunStatus.AWAITING_PLAN_APPROVAL:
                await self._service.approve_plan(
                    ApprovePlanInput(
                        run_id=record.run_id,
                        expected_version=run.version,
                        notes="Automatically approved by the authenticated auto_pr API.",
                    )
                )
                await self._store.record_error(record.run_id, None)
            elif status is RunStatus.AWAITING_DELIVERY_APPROVAL:
                await self._service.approve_delivery(
                    ApproveDeliveryInput(
                        run_id=record.run_id,
                        expected_version=run.version,
                        commit_message=record.commit_message,
                        notes="Automatically approved after deterministic verification.",
                    )
                )
                await self._store.record_error(record.run_id, None)
            elif status is RunStatus.AWAITING_PUBLISH_APPROVAL:
                await self._service.approve_publish(
                    ApprovePublishInput(
                        run_id=record.run_id,
                        expected_version=run.version,
                        title=record.pull_request_title,
                        body=record.pull_request_body,
                        draft=True,
                        notes="Automatically approved for Draft PR publication.",
                    )
                )
                await self._store.record_error(record.run_id, None)
            elif status is RunStatus.COMPLETED:
                await self._store.capture_publication(record.run_id)
            elif status is RunStatus.CANCELED:
                await self._store.set_status(
                    record.run_id,
                    AutomationStatus.CANCELED,
                )
            elif status in {RunStatus.FAILED, RunStatus.AWAITING_REVISION}:
                await self._store.set_status(
                    record.run_id,
                    AutomationStatus.FAILED,
                    last_error=f"Automatic execution stopped in state {status.value}.",
                )
        except ConcurrentUpdateError:
            logger.debug(
                "Run version moved while auto-approving",
                extra={"run_id": str(record.run_id)},
            )
        except Exception as exc:
            logger.exception(
                "Auto-PR advancement failed",
                extra={"run_id": str(record.run_id)},
            )
            await self._store.record_error(record.run_id, str(exc))
