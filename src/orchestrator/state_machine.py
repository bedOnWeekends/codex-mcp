from __future__ import annotations

from collections.abc import Mapping

from .errors import InvalidStateTransitionError
from .schemas import RunStatus, TaskStatus

RUN_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.PLANNING, RunStatus.CANCELED}),
    RunStatus.PLANNING: frozenset(
        {
            RunStatus.AWAITING_PLAN_APPROVAL,
            RunStatus.FAILED,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.AWAITING_PLAN_APPROVAL: frozenset(
        {
            RunStatus.PLANNING,
            RunStatus.EXECUTING,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.EXECUTING: frozenset(
        {
            RunStatus.VERIFYING,
            RunStatus.AWAITING_REVISION,
            RunStatus.FAILED,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.VERIFYING: frozenset(
        {
            RunStatus.EXECUTING,
            RunStatus.AWAITING_REVISION,
            RunStatus.AWAITING_DELIVERY_APPROVAL,
            RunStatus.FAILED,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.AWAITING_REVISION: frozenset(
        {
            RunStatus.EXECUTING,
            RunStatus.FAILED,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.AWAITING_DELIVERY_APPROVAL: frozenset(
        {RunStatus.DELIVERING, RunStatus.CANCELED}
    ),
    RunStatus.DELIVERING: frozenset(
        {
            RunStatus.AWAITING_PUBLISH_APPROVAL,
            RunStatus.FAILED,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.AWAITING_PUBLISH_APPROVAL: frozenset(
        {
            RunStatus.PUBLISHING,
            RunStatus.COMPLETED,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.PUBLISHING: frozenset(
        {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED}
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELED: frozenset(),
}

TASK_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELED}),
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELED}),
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.QUEUED}),
    TaskStatus.CANCELED: frozenset(),
}

TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED}
)
TERMINAL_TASK_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELED})


def ensure_run_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in RUN_TRANSITIONS[current]:
        raise InvalidStateTransitionError("run", current.value, target.value)


def ensure_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in TASK_TRANSITIONS[current]:
        raise InvalidStateTransitionError("task", current.value, target.value)


def is_terminal_run_status(status: RunStatus) -> bool:
    return status in TERMINAL_RUN_STATUSES


def is_terminal_task_status(status: TaskStatus) -> bool:
    return status in TERMINAL_TASK_STATUSES
