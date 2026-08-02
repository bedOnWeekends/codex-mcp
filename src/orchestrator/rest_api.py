from __future__ import annotations

import secrets
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from .api_schemas import (
    AutomatedRunOutput,
    CancelAutomatedRunInput,
    CancelAutomatedRunOutput,
    StartAutomatedRunInput,
    StartAutomatedRunOutput,
)
from .automation import IdempotencyConflictError
from .errors import (
    ConcurrentUpdateError,
    EntityNotFoundError,
    OrchestratorError,
    RunNotCancelableError,
)
from .settings import Settings

_BEARER = HTTPBearer(auto_error=False)


class AutomationApi(Protocol):
    async def start_run(
        self,
        request: StartAutomatedRunInput,
        *,
        idempotency_key: str,
    ) -> StartAutomatedRunOutput: ...

    async def get_run(self, run_id: UUID) -> AutomatedRunOutput: ...

    async def cancel_run(
        self,
        run_id: UUID,
        request: CancelAutomatedRunInput,
    ) -> CancelAutomatedRunOutput: ...


def build_api_router(settings: Settings, coordinator: AutomationApi) -> APIRouter:
    router = APIRouter(tags=["orchestration"])

    async def require_api_key(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(_BEARER),
        ],
    ) -> None:
        configured = settings.api_key
        supplied = credentials.credentials if credentials is not None else ""
        expected = configured.get_secret_value() if configured is not None else ""
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not expected
            or not secrets.compare_digest(supplied, expected)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    auth = [Depends(require_api_key)]

    @router.post(
        "/runs",
        operation_id="startOrchestrationRun",
        response_model=StartAutomatedRunOutput,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=auth,
        summary="Start an automatic Draft PR run",
    )
    async def start_run(
        request: StartAutomatedRunInput,
        idempotency_key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=8,
                max_length=200,
                description="Unique key used to replay the same request safely.",
            ),
        ],
    ) -> StartAutomatedRunOutput:
        normalized_key = idempotency_key.strip()
        if len(normalized_key) < 8:
            raise HTTPException(
                status_code=422,
                detail="Idempotency-Key must contain at least 8 non-space characters.",
            )
        try:
            return await coordinator.start_run(
                request,
                idempotency_key=normalized_key,
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except EntityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (OrchestratorError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(
        "/runs/{run_id}",
        operation_id="getOrchestrationRun",
        response_model=AutomatedRunOutput,
        dependencies=auth,
        summary="Read automatic orchestration status",
    )
    async def get_run(run_id: UUID) -> AutomatedRunOutput:
        try:
            return await coordinator.get_run(run_id)
        except EntityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/runs/{run_id}/cancel",
        operation_id="cancelOrchestrationRun",
        response_model=CancelAutomatedRunOutput,
        dependencies=auth,
        summary="Cancel a non-terminal automatic run",
    )
    async def cancel_run(
        run_id: UUID,
        request: CancelAutomatedRunInput,
    ) -> CancelAutomatedRunOutput:
        try:
            return await coordinator.cancel_run(run_id, request)
        except EntityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ConcurrentUpdateError, RunNotCancelableError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OrchestratorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
