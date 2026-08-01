from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from orchestrator.schemas import RepositoryCreate, RunCreate


def test_run_constraints_are_trimmed_deduplicated_and_empty_removed() -> None:
    model = RunCreate(
        repository_id=uuid4(),
        goal="Implement phase one",
        constraints=[" read-only ", "", "read-only", "run tests"],
    )
    assert model.constraints == ["read-only", "run tests"]


def test_run_cost_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        RunCreate(
            repository_id=uuid4(),
            goal="x",
            max_cost_usd=Decimal("0"),
        )


def test_repository_path_is_normalized(tmp_path: Path) -> None:
    model = RepositoryCreate(name="demo", root_path=tmp_path / "repo")
    assert model.root_path.is_absolute()
