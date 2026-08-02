import pytest

from orchestrator.errors import InvalidStateTransitionError
from orchestrator.schemas import RunStatus, TaskStatus
from orchestrator.state_machine import (
    ensure_run_transition,
    ensure_task_transition,
    is_terminal_run_status,
    is_terminal_task_status,
)


def test_valid_run_transition() -> None:
    ensure_run_transition(RunStatus.CREATED, RunStatus.PLANNING)
    ensure_run_transition(RunStatus.AWAITING_PLAN_APPROVAL, RunStatus.EXECUTING)
    ensure_run_transition(RunStatus.VERIFYING, RunStatus.COMPLETED)


def test_invalid_run_transition() -> None:
    with pytest.raises(InvalidStateTransitionError):
        ensure_run_transition(RunStatus.CREATED, RunStatus.COMPLETED)


def test_failed_task_can_be_requeued() -> None:
    ensure_task_transition(TaskStatus.FAILED, TaskStatus.QUEUED)


def test_terminal_status_helpers() -> None:
    assert is_terminal_run_status(RunStatus.COMPLETED)
    assert is_terminal_run_status(RunStatus.CANCELED)
    assert not is_terminal_run_status(RunStatus.EXECUTING)
    assert is_terminal_task_status(TaskStatus.COMPLETED)
    assert not is_terminal_task_status(TaskStatus.FAILED)
