from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


class OrchestratorError(Exception):
    """Base exception for expected orchestrator failures."""


@dataclass(slots=True)
class EntityNotFoundError(OrchestratorError):
    entity: str
    entity_id: UUID | str

    def __str__(self) -> str:
        return f"{self.entity} not found: {self.entity_id}"


@dataclass(slots=True)
class InvalidStateTransitionError(OrchestratorError):
    entity: str
    current: str
    target: str

    def __str__(self) -> str:
        return (
            f"Invalid {self.entity} state transition: "
            f"{self.current!r} -> {self.target!r}"
        )


@dataclass(slots=True)
class ConcurrentUpdateError(OrchestratorError):
    entity: str
    entity_id: UUID | str
    expected_version: int
    actual_version: int

    def __str__(self) -> str:
        return (
            f"Concurrent update detected for {self.entity} {self.entity_id}: "
            f"expected version {self.expected_version}, actual version "
            f"{self.actual_version}"
        )


@dataclass(slots=True)
class DuplicateEntityError(OrchestratorError):
    entity: str
    key: str

    def __str__(self) -> str:
        return f"Duplicate {self.entity}: {self.key}"


@dataclass(slots=True)
class RunNotCancelableError(OrchestratorError):
    run_id: UUID
    status: str

    def __str__(self) -> str:
        return f"Run {self.run_id} cannot be canceled from status {self.status!r}"


@dataclass(slots=True)
class InvalidRepositoryError(OrchestratorError):
    path: str
    reason: str

    def __str__(self) -> str:
        return f"Invalid repository {self.path!r}: {self.reason}"
