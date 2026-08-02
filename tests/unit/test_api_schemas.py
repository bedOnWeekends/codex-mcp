from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.api_schemas import StartAutomatedRunInput
from orchestrator.schemas import RiskLevel


def test_auto_pr_accepts_normal_risk_and_normalizes_constraints() -> None:
    request = StartAutomatedRunInput(
        repository="toss-trader",
        goal="Add a safe API client.",
        constraints=[" no live orders ", "no live orders", ""],
        commit_message="feat(api): add safe client",
        pull_request_title="feat(api): add safe client",
    )

    assert request.risk_level is RiskLevel.NORMAL
    assert request.constraints == ["no live orders"]


@pytest.mark.parametrize("risk_level", [RiskLevel.HIGH, RiskLevel.CRITICAL])
def test_auto_pr_rejects_high_risk_runs(risk_level: RiskLevel) -> None:
    with pytest.raises(ValidationError, match="only low or normal"):
        StartAutomatedRunInput(
            repository="toss-trader",
            goal="Change production infrastructure.",
            risk_level=risk_level,
        )


def test_auto_pr_requires_conventional_commit_titles() -> None:
    with pytest.raises(ValidationError, match="Conventional Commits"):
        StartAutomatedRunInput(
            repository="toss-trader",
            goal="Add validation.",
            commit_message="add validation",
        )
