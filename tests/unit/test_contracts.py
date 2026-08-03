from orchestrator.contracts import (
    ACCEPTANCE_CRITERION_PREFIX,
    SEMANTIC_REVIEW_REQUIRED,
    auto_pr_run_constraints,
    requires_semantic_review,
)


def test_auto_pr_contract_preserves_criteria_and_requires_semantic_review() -> None:
    constraints = auto_pr_run_constraints(
        ["Do not enable live trading.", "Do not enable live trading."],
        [
            "Use 0.5 / 0.75 / 1.0 x Opening Range Width.",
            "Use 1.5R / 2.0R / 2.5R exits.",
        ],
    )

    assert constraints == [
        "Do not enable live trading.",
        SEMANTIC_REVIEW_REQUIRED,
        f"{ACCEPTANCE_CRITERION_PREFIX}Use 0.5 / 0.75 / 1.0 x Opening Range Width.",
        f"{ACCEPTANCE_CRITERION_PREFIX}Use 1.5R / 2.0R / 2.5R exits.",
    ]
    assert requires_semantic_review(constraints) is True


def test_unmarked_run_keeps_optional_semantic_review() -> None:
    assert requires_semantic_review(["Run pytest."]) is False
