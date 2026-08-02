from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from orchestrator.multi_agent import (
    enforce_adaptive_policy,
    fake_agent_plan,
    fit_plan_to_budget,
    parse_agent_plan,
    validate_agent_changes,
)
from orchestrator.schemas import (
    AgentAssignment,
    AgentAssignmentStatus,
    AgentPlan,
    AgentRole,
    AgentSpec,
    ExecutionMode,
    ModelTier,
    RiskLevel,
    Run,
    RunStatus,
)
from orchestrator.settings import Settings


def test_fake_agent_plan_uses_single_agent_cheapest_path() -> None:
    plan = fake_agent_plan()
    assert plan.mode is ExecutionMode.SINGLE
    assert plan.requires_llm_review is False
    assert [item.key for item in plan.topological_order()] == ["implement-change"]


def test_agent_plan_rejects_sequential_implementers() -> None:
    with pytest.raises(ValidationError, match="independent"):
        AgentPlan(
            mode=ExecutionMode.PARALLEL,
            confidence=0.9,
            rationale="Attempted sequential split.",
            assignments=[
                AgentSpec(
                    key="implement-api",
                    role=AgentRole.IMPLEMENTER,
                    instruction="Implement API.",
                    owned_paths=["src/api"],
                ),
                AgentSpec(
                    key="implement-ui",
                    role=AgentRole.IMPLEMENTER,
                    instruction="Implement UI.",
                    depends_on=["implement-api"],
                    owned_paths=["src/ui"],
                ),
            ],
        )


def test_agent_plan_rejects_overlapping_implementer_paths() -> None:
    with pytest.raises(ValidationError, match="overlaps"):
        AgentPlan(
            mode=ExecutionMode.PARALLEL,
            confidence=0.9,
            rationale="Paths overlap.",
            assignments=[
                AgentSpec(
                    key="implement-api",
                    role=AgentRole.IMPLEMENTER,
                    instruction="Implement API.",
                    owned_paths=["src/api"],
                ),
                AgentSpec(
                    key="implement-api-client",
                    role=AgentRole.IMPLEMENTER,
                    instruction="Implement client.",
                    owned_paths=["src/api/client"],
                ),
            ],
        )


def test_reviewer_must_depend_on_exactly_all_implementers() -> None:
    with pytest.raises(ValidationError, match="exactly every implementer"):
        AgentPlan(
            mode=ExecutionMode.PARALLEL,
            confidence=0.5,
            requires_llm_review=True,
            rationale="Review required.",
            assignments=[
                AgentSpec(
                    key="implement-api",
                    role=AgentRole.IMPLEMENTER,
                    instruction="Implement API.",
                    owned_paths=["src/api"],
                ),
                AgentSpec(
                    key="implement-ui",
                    role=AgentRole.IMPLEMENTER,
                    instruction="Implement UI.",
                    owned_paths=["src/ui"],
                ),
                AgentSpec(
                    key="review-core",
                    role=AgentRole.REVIEWER,
                    instruction="Review.",
                    depends_on=["implement-api"],
                ),
            ],
        )


def test_low_confidence_plan_gets_one_reviewer() -> None:
    plan = AgentPlan(
        mode=ExecutionMode.SINGLE,
        confidence=0.4,
        rationale="Repository evidence is incomplete.",
        assignments=[
            AgentSpec(
                key="implement-core",
                role=AgentRole.IMPLEMENTER,
                instruction="Implement.",
                owned_paths=["src"],
            )
        ],
    )
    routed = enforce_adaptive_policy(
        plan,
        make_run(RiskLevel.NORMAL),
        confidence_threshold=0.72,
    )
    assert routed.requires_llm_review is True
    assert [item.role for item in routed.topological_order()] == [
        AgentRole.IMPLEMENTER,
        AgentRole.REVIEWER,
    ]


def test_high_risk_parallel_plan_collapses_to_preserve_reviewer_slot() -> None:
    routed = enforce_adaptive_policy(
        parallel_plan(),
        make_run(RiskLevel.HIGH),
        confidence_threshold=0.72,
        max_agents=2,
    )
    assert routed.mode is ExecutionMode.SINGLE
    assert routed.requires_llm_review is True
    assert [item.role for item in routed.topological_order()] == [
        AgentRole.IMPLEMENTER,
        AgentRole.REVIEWER,
    ]


def test_high_confidence_low_risk_plan_drops_optional_reviewer() -> None:
    plan = AgentPlan(
        mode=ExecutionMode.SINGLE,
        confidence=0.95,
        requires_llm_review=True,
        rationale="The change is localized.",
        assignments=[
            AgentSpec(
                key="implement-core",
                role=AgentRole.IMPLEMENTER,
                instruction="Implement.",
                owned_paths=["src"],
            ),
            AgentSpec(
                key="review-core",
                role=AgentRole.REVIEWER,
                instruction="Review.",
                depends_on=["implement-core"],
            ),
        ],
    )
    routed = enforce_adaptive_policy(
        plan,
        make_run(RiskLevel.LOW),
        confidence_threshold=0.72,
    )
    assert routed.requires_llm_review is False
    assert len(routed.assignments) == 1


def test_parallel_plan_collapses_when_projected_tokens_exceed_budget(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path, max_tokens=100_000)
    fitted = fit_plan_to_budget(
        parallel_plan(),
        make_run(RiskLevel.NORMAL),
        settings,
        used_tokens=0,
        spent_cost_usd=Decimal("0"),
    )
    assert fitted.mode is ExecutionMode.SINGLE
    assert fitted.requires_llm_review is False
    assert [item.key for item in fitted.assignments] == ["implement-combined"]
    assert fitted.assignments[0].owned_paths == ["src/api", "src/ui"]


def test_required_reviewer_is_not_removed_to_fit_budget(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_tokens=100_000)
    routed = enforce_adaptive_policy(
        AgentPlan(
            mode=ExecutionMode.SINGLE,
            confidence=0.4,
            rationale="Low confidence requires independent review.",
            assignments=[
                AgentSpec(
                    key="implement-core",
                    role=AgentRole.IMPLEMENTER,
                    instruction="Implement.",
                    owned_paths=["src"],
                )
            ],
        ),
        make_run(RiskLevel.NORMAL),
        confidence_threshold=0.72,
    )
    with pytest.raises(ValueError, match="cannot preserve its quality gates"):
        fit_plan_to_budget(
            routed,
            make_run(RiskLevel.NORMAL),
            settings,
            used_tokens=0,
            spent_cost_usd=Decimal("0"),
        )


def test_parse_agent_plan_accepts_markdown_json_fence() -> None:
    payload = fake_agent_plan().model_dump_json(indent=2)
    parsed = parse_agent_plan(f"```json\n{payload}\n```")
    assert parsed == fake_agent_plan()


def test_implementer_change_must_stay_inside_owned_paths(tmp_path: Path) -> None:
    assignment = make_assignment(
        tmp_path,
        role=AgentRole.IMPLEMENTER,
        owned_paths=["src/api"],
    )
    assert validate_agent_changes(
        assignment,
        ["src/api/routes.py", "src/api/models.py"],
    ) == ["src/api/models.py", "src/api/routes.py"]
    with pytest.raises(ValueError, match="outside owned paths"):
        validate_agent_changes(assignment, ["src/ui/page.tsx"])


def test_read_only_agent_cannot_modify_files(tmp_path: Path) -> None:
    assignment = make_assignment(tmp_path, role=AgentRole.REVIEWER, owned_paths=[])
    with pytest.raises(ValueError, match="read-only"):
        validate_agent_changes(assignment, ["README.md"])


def parallel_plan() -> AgentPlan:
    return AgentPlan(
        mode=ExecutionMode.PARALLEL,
        confidence=0.95,
        rationale="API and UI paths are independent.",
        assignments=[
            AgentSpec(
                key="implement-api",
                role=AgentRole.IMPLEMENTER,
                instruction="Implement API.",
                owned_paths=["src/api"],
            ),
            AgentSpec(
                key="implement-ui",
                role=AgentRole.IMPLEMENTER,
                instruction="Implement UI.",
                owned_paths=["src/ui"],
            ),
        ],
    )


def make_settings(tmp_path: Path, *, max_tokens: int) -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
        runtime_dir=tmp_path / "runtime",
        max_tokens_per_run=max_tokens,
    )


def make_run(risk: RiskLevel) -> Run:
    now = datetime.now(UTC)
    return Run(
        id=uuid4(),
        repository_id=uuid4(),
        goal="Implement a focused change",
        constraints=[],
        risk_level=risk,
        max_cost_usd=Decimal("3"),
        status=RunStatus.SUPERVISING,
        spent_cost_usd=Decimal("0"),
        version=1,
        created_at=now,
        updated_at=now,
    )


def make_assignment(
    tmp_path: Path,
    *,
    role: AgentRole,
    owned_paths: list[str],
) -> AgentAssignment:
    now = datetime.now(UTC)
    return AgentAssignment(
        id=uuid4(),
        run_id=uuid4(),
        key="implement-core" if role is AgentRole.IMPLEMENTER else "review-core",
        role=role,
        status=AgentAssignmentStatus.RUNNING,
        instruction="Complete the assigned scope.",
        depends_on=[],
        owned_paths=owned_paths,
        model_tier=(
            ModelTier.DEFAULT
            if role is AgentRole.IMPLEMENTER
            else ModelTier.CRITICAL
        ),
        worktree_path=tmp_path / "worktree",
        changed_files=[],
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=Decimal("0"),
        started_at=now,
        created_at=now,
        updated_at=now,
    )
