from __future__ import annotations

SEMANTIC_REVIEW_REQUIRED = "[orchestrator] semantic review required"
ACCEPTANCE_CRITERION_PREFIX = "[acceptance] "


def auto_pr_run_constraints(
    constraints: list[str],
    acceptance_criteria: list[str],
) -> list[str]:
    """Preserve caller constraints and add the fail-closed auto-PR review contract."""
    combined: list[str] = []
    for value in [
        *constraints,
        SEMANTIC_REVIEW_REQUIRED,
        *(f"{ACCEPTANCE_CRITERION_PREFIX}{item}" for item in acceptance_criteria),
    ]:
        normalized = value.strip()
        if normalized and normalized not in combined:
            combined.append(normalized)
    return combined


def requires_semantic_review(constraints: list[str]) -> bool:
    return SEMANTIC_REVIEW_REQUIRED in constraints
