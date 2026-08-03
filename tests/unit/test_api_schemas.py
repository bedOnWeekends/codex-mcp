from __future__ import annotations

import pytest

from orchestrator.api_schemas import StartAutomatedRunInput
from orchestrator.schemas import RiskLevel


def test_auto_pr_accepts_normal_risk_and_normalizes_constraints() -> None:
    request = StartAutomatedRunInput(
        repository="toss-trader",
        goal="Add a safe API client.",
        constraints=[" no live orders ", "no live orders", ""],
        acceptance_criteria=[
            " preserve numeric values ",
            "preserve numeric values",
        ],
        commit_message="feat(api): add safe client",
        pull_request_title="feat(api): add safe client",
    )

    assert request.risk_level is RiskLevel.NORMAL
    assert request.constraints == ["no live orders"]
    assert request.acceptance_criteria == ["preserve numeric values"]


def test_auto_pr_requires_acceptance_criteria_field() -> None:
    with pytest.raises(ValueError, match="acceptance_criteria"):
        StartAutomatedRunInput(
            repository="toss-trader",
            goal="Add validation.",
        )


def test_auto_pr_rejects_blank_acceptance_criteria() -> None:
    with pytest.raises(ValueError, match="explicit acceptance criterion"):
        StartAutomatedRunInput(
            repository="toss-trader",
            goal="Add validation.",
            acceptance_criteria=["  ", ""],
        )


def test_auto_pr_openapi_marks_acceptance_criteria_required() -> None:
    schema = StartAutomatedRunInput.model_json_schema()

    assert "acceptance_criteria" in schema["required"]
    assert schema["properties"]["acceptance_criteria"]["minItems"] == 1


@pytest.mark.parametrize("risk_level", [RiskLevel.HIGH, RiskLevel.CRITICAL])
def test_auto_pr_rejects_high_risk_runs(risk_level: RiskLevel) -> None:
    with pytest.raises(ValueError, match="only low or normal"):
        StartAutomatedRunInput(
            repository="toss-trader",
            goal="Change production infrastructure.",
            acceptance_criteria=[
                "The requested change satisfies the explicit contract."
            ],
            risk_level=risk_level,
        )


def test_auto_pr_requires_conventional_commit_titles() -> None:
    with pytest.raises(ValueError, match="Conventional Commits"):
        StartAutomatedRunInput(
            repository="toss-trader",
            goal="Add validation.",
            acceptance_criteria=[
                "The requested change satisfies the explicit contract."
            ],
            commit_message="add validation",
        )
