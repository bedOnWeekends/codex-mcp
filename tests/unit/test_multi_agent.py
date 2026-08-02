from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from orchestrator.multi_agent import (
    fake_agent_plan,
    parse_agent_plan,
    validate_agent_changes,
)
from orchestrator.schemas import (
    AgentAssignment,
    AgentAssignmentStatus,
    AgentPlan,
    AgentRole,
    AgentSpec,
    ModelTier,
)


def test_fake_agent_plan_fans_out_parallel_implementers() -> None:
    plan = fake_agent_plan()
    assert [item.key for item in plan.topological_order()] == [
        "explore-codebase",
        "implement-source",
        "implement-tests",
        "review-integration",
    ]
    reviewer = plan.assignments[-1]
    assert set(reviewer.depends_on) == {"implement-source", "implement-tests"}


def test_agent_plan_rejects_cycles() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        AgentPlan(
            assignments=[
                AgentSpec(
                    key="explore-codebase",
                    role=AgentRole.EXPLORER,
                    instruction="Explore.",
                    depends_on=["review-core"],
                ),
                AgentSpec(
                    key="implement-core",
                    role=AgentRole.IMPLEMENTER,
                    instruction="Implement.",
                    depends_on=["explore-codebase"],
                    owned_paths=["src"],
                ),
                AgentSpec(
                    key="review-core",
                    role=AgentRole.REVIEWER,
                    instruction="Review.",
                    depends_on=["implement-core", "explore-codebase"],
                ),
            ]
        )


def test_agent_plan_rejects_overlapping_implementer_paths() -> None:
    with pytest.raises(ValidationError, match="overlaps"):
        AgentPlan(
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
                AgentSpec(
                    key="review-core",
                    role=AgentRole.REVIEWER,
                    instruction="Review.",
                    depends_on=["implement-api", "implement-api-client"],
                ),
            ]
        )


def test_reviewer_must_depend_on_all_implementers() -> None:
    with pytest.raises(ValidationError, match="every implementer"):
        AgentPlan(
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
            ]
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
        model_tier=ModelTier.DEFAULT,
        worktree_path=tmp_path / "worktree",
        changed_files=[],
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=Decimal("0"),
        started_at=now,
        created_at=now,
        updated_at=now,
    )
