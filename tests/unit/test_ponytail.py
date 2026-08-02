from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from orchestrator.context_builder import build_task_prompt
from orchestrator.multi_agent import build_agent_prompt
from orchestrator.ponytail import PONYTAIL_SOURCE_REVISION, implementation_policy
from orchestrator.schemas import (
    AgentAssignment,
    AgentRole,
    Repository,
    RiskLevel,
    Run,
    Task,
    TaskKind,
)


def repository(tmp_path: Path) -> Repository:
    return cast(
        Repository,
        SimpleNamespace(
            name="example",
            root_path=tmp_path,
            default_branch="main",
        ),
    )


def run() -> Run:
    return cast(
        Run,
        SimpleNamespace(
            goal="Fix the shared parser",
            constraints=[],
            plan="Change the shared parser and add one regression test.",
            risk_level=RiskLevel.NORMAL,
        ),
    )


def assignment(role: AgentRole) -> AgentAssignment:
    return cast(
        AgentAssignment,
        SimpleNamespace(
            role=role,
            owned_paths=["src"] if role is AgentRole.IMPLEMENTER else [],
            instruction="Complete the focused change.",
        ),
    )


def task(kind: TaskKind) -> Task:
    return cast(
        Task,
        SimpleNamespace(
            kind=kind,
            instruction="Complete the requested task.",
        ),
    )


def test_pinned_policy_is_compact_and_preserves_safety() -> None:
    policy = implementation_policy()
    assert PONYTAIL_SOURCE_REVISION == "16f29800fd2681bdf24f3eb4ccffe38be3baec6b"
    assert len(policy) < 1_600
    assert "reuse an existing helper" in policy
    assert "standard library" in policy
    assert "trust-boundary validation" in policy
    assert "security" in policy


def test_implementer_receives_full_policy(tmp_path: Path) -> None:
    prompt = build_agent_prompt(
        repository(tmp_path),
        run(),
        assignment(AgentRole.IMPLEMENTER),
        dependency_context=[],
    )
    assert "Ponytail full policy:" in prompt
    assert "smallest correct diff" in prompt
    assert "Do not commit, merge, push" in prompt


def test_conditional_reviewer_keeps_correctness_and_adds_complexity_check(
    tmp_path: Path,
) -> None:
    prompt = build_agent_prompt(
        repository(tmp_path),
        run(),
        assignment(AgentRole.REVIEWER),
        dependency_context=[],
    )
    assert "concrete correctness or safety defects" in prompt
    assert "Ponytail complexity check:" in prompt
    assert "correctness, safety, contract, and test review as the primary task" in prompt


def test_fix_gets_policy_but_plan_scout_does_not(tmp_path: Path) -> None:
    fix_prompt = build_task_prompt(
        repository(tmp_path),
        run(),
        task(TaskKind.FIX),
        workspace=tmp_path,
    )
    plan_prompt = build_task_prompt(
        repository(tmp_path),
        run(),
        task(TaskKind.PLAN),
        workspace=tmp_path,
    )
    assert "Ponytail full policy:" in fix_prompt
    assert "Ponytail full policy:" not in plan_prompt
