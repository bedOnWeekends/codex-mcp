from __future__ import annotations

import json
from decimal import Decimal
from pathlib import PurePosixPath

from .contracts import requires_semantic_review
from .costing import projected_call_cost, projected_call_tokens
from .ponytail import append_policy, implementation_policy, review_policy
from .schemas import (
    AgentAssignment,
    AgentHandoff,
    AgentPlan,
    AgentRole,
    AgentSpec,
    ExecutionMode,
    ModelTier,
    Repository,
    RiskLevel,
    Run,
)
from .settings import Settings


def parse_agent_plan(text: str) -> AgentPlan:
    payload = json.loads(_extract_json_object(text))
    if not isinstance(payload, dict):
        raise ValueError("supervisor output must be a JSON object")
    assignments = payload.get("assignments")
    if not isinstance(assignments, list):
        raise ValueError("supervisor output must contain an assignments array")

    implementers: list[object] = []
    for assignment in assignments:
        if not isinstance(assignment, dict):
            raise ValueError("supervisor assignments must be JSON objects")
        if assignment.get("role") == AgentRole.IMPLEMENTER.value:
            implementers.append(assignment)

    payload["assignments"] = implementers
    payload["requires_llm_review"] = False
    return AgentPlan.model_validate(payload)


def parse_agent_handoff(text: str) -> AgentHandoff:
    try:
        handoff = AgentHandoff.model_validate_json(_extract_json_object(text))
    except ValueError:
        normalized = " ".join(text.strip().split())
        handoff = AgentHandoff(
            summary=(normalized or "Agent completed without a summary.")[:800]
        )
    if handoff.risks:
        detail = "; ".join(handoff.risks)
        raise ValueError(f"agent reported unresolved blocking risks: {detail}")
    return handoff


def fake_agent_plan() -> AgentPlan:
    return AgentPlan(
        mode=ExecutionMode.SINGLE,
        confidence=0.95,
        requires_llm_review=False,
        rationale="Fake mode uses one no-op implementer to exercise the cheapest path.",
        assignments=[
            AgentSpec(
                key="implement-change",
                role=AgentRole.IMPLEMENTER,
                instruction=(
                    "Implement the approved change and focused tests in the smallest "
                    "possible scope."
                ),
                owned_paths=[
                    "src",
                    "tests",
                    "migrations",
                    "README.md",
                    ".env.example",
                    "pyproject.toml",
                ],
            )
        ],
    )


def enforce_adaptive_policy(
    plan: AgentPlan,
    run: Run,
    *,
    confidence_threshold: float,
    max_agents: int = 4,
) -> AgentPlan:
    implementers = [
        item for item in plan.assignments if item.role is AgentRole.IMPLEMENTER
    ]
    semantic_required = requires_semantic_review(run.constraints)
    desired_reviewer = (
        semantic_required
        or run.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        or plan.confidence < confidence_threshold
        or (plan.mode is ExecutionMode.PARALLEL and len(implementers) >= 3)
    )
    collapsed_for_review = desired_reviewer and len(implementers) + 1 > max_agents
    if collapsed_for_review:
        implementers = [_collapse_implementers(implementers)]

    assignments = list(implementers)
    if desired_reviewer:
        assignments.append(_reviewer_for(implementers))

    mode = ExecutionMode.SINGLE if len(implementers) == 1 else ExecutionMode.PARALLEL
    rationale = plan.rationale
    if (
        desired_reviewer != plan.requires_llm_review
        or collapsed_for_review
        or mode is not plan.mode
    ):
        suffix = (
            f" Deterministic policy set LLM review to {str(desired_reviewer).lower()}."
        )
        if semantic_required:
            suffix += " Authenticated auto-PR execution requires semantic review."
        if collapsed_for_review:
            suffix += " Parallel scopes were combined to preserve the Sol review slot."
        rationale = f"{rationale}{suffix}"[:600]
    return AgentPlan(
        mode=mode,
        confidence=plan.confidence,
        requires_llm_review=desired_reviewer,
        rationale=rationale,
        assignments=assignments,
    )


def fit_plan_to_budget(
    plan: AgentPlan,
    run: Run,
    settings: Settings,
    *,
    used_tokens: int,
    spent_cost_usd: Decimal,
) -> AgentPlan:
    if used_tokens < 0 or spent_cost_usd < 0:
        raise ValueError("used budget values must be non-negative")
    if _plan_fits(
        plan,
        run,
        settings,
        used_tokens=used_tokens,
        spent_cost_usd=spent_cost_usd,
    ):
        return plan
    if plan.mode is ExecutionMode.PARALLEL:
        implementers = [
            item for item in plan.assignments if item.role is AgentRole.IMPLEMENTER
        ]
        collapsed = _collapse_implementers(implementers)
        assignments = [collapsed]
        if plan.requires_llm_review:
            assignments.append(_reviewer_for([collapsed]))
        candidate = AgentPlan(
            mode=ExecutionMode.SINGLE,
            confidence=plan.confidence,
            requires_llm_review=plan.requires_llm_review,
            rationale=(
                f"{plan.rationale} Parallel execution exceeded projected budget, so "
                "the implementation scopes were combined without removing the quality "
                "review gate."
            )[:600],
            assignments=assignments,
        )
        if _plan_fits(
            candidate,
            run,
            settings,
            used_tokens=used_tokens,
            spent_cost_usd=spent_cost_usd,
        ):
            return candidate
    required_tokens, required_cost = projected_plan_budget(plan, run, settings)
    remaining_tokens = settings.max_tokens_per_run - used_tokens
    remaining_cost = run.max_cost_usd - spent_cost_usd - settings.budget_reserve_usd
    raise ValueError(
        "adaptive plan cannot preserve its quality gates within budget: "
        f"requires approximately {required_tokens} tokens and ${required_cost}, "
        f"remaining {remaining_tokens} tokens and ${remaining_cost}"
    )


def projected_plan_budget(
    plan: AgentPlan,
    run: Run,
    settings: Settings,
) -> tuple[int, Decimal]:
    tiers = [_projected_tier(item, run) for item in plan.assignments]
    return (
        sum(projected_call_tokens(settings, tier) for tier in tiers),
        sum(
            (projected_call_cost(settings, tier) for tier in tiers),
            start=Decimal("0"),
        ),
    )


def build_supervisor_prompt(
    repository: Repository,
    run: Run,
    *,
    max_agents: int,
) -> str:
    constraints = "; ".join(run.constraints) or "none"
    semantic_requirement = (
        "This run requires an independent semantic reviewer. The harness adds that "
        "reviewer after parsing, so reserve one assignment slot but do not return a "
        "reviewer assignment. "
        if requires_semantic_review(run.constraints)
        else ""
    )
    return (
        "Act as a low-cost repository scout and execution router. Inspect only enough "
        "code to choose the cheapest reliable implementation shape; use at most four "
        "focused repository inspection actions. Return only JSON matching the provided "
        "output schema.\n\n"
        "Default to single mode with one implementer. Choose parallel mode only when "
        "two or three independently editable, non-overlapping path groups are proven. "
        "Return implementer assignments only; never return a reviewer or explorer. Set "
        "requires_llm_review to false because the harness owns reviewer policy. "
        "Implementers must have no dependencies and must own precise repository-relative "
        "path prefixes. Keep "
        f"the total assignments at or below {max_agents}. {semantic_requirement}\n\n"
        f"Repository: {repository.name}\n"
        f"Risk: {run.risk_level.value}\n"
        f"Goal: {run.goal}\n"
        f"Constraints and acceptance criteria: {constraints}\n"
        f"Approved plan: {run.plan or 'none'}"
    )


def build_agent_prompt(
    repository: Repository,
    run: Run,
    assignment: AgentAssignment,
    *,
    dependency_context: list[str],
) -> str:
    dependencies = "\n".join(dependency_context) or "none"
    ownership = ", ".join(assignment.owned_paths) or "read-only"
    constraints = "\n".join(f"- {item}" for item in run.constraints) or "- None"
    if assignment.role is AgentRole.IMPLEMENTER:
        rules = (
            "Edit only owned paths. Make the smallest complete patch, run focused "
            "checks when useful, and stop once the assignment is satisfied. Preserve "
            "every exact numeric value, formula, enumerated option, and explicit "
            "prohibition from the original goal and acceptance criteria. The risks "
            "array is a blocking channel: leave it empty only when no unresolved "
            "contract defect remains. Do not commit, merge, push, deploy, or access "
            "credentials."
        )
        policy = implementation_policy()
    else:
        rules = (
            "Review only and do not modify files. Independently compare the combined "
            "implementation against the original goal, constraints, and acceptance "
            "criteria; those are authoritative when the approved plan conflicts. Audit "
            "exact numbers, formulas, option sets, forbidden substitutions, changed-file "
            "scope, tests, correctness, and safety. Report concrete correctness or "
            "safety defects and put every actionable contract mismatch in the risks "
            "array. An empty risks array is an explicit approval. Do not report "
            "style-only commentary."
        )
        policy = review_policy()
    prompt = (
        f"Role: {assignment.role.value}\n"
        f"Repository: {repository.name}\n"
        f"Goal: {run.goal}\n"
        f"Constraints and acceptance criteria:\n{constraints}\n"
        f"Approved plan (non-authoritative if it conflicts with the request):\n"
        f"{run.plan or 'none'}\n"
        f"Owned paths: {ownership}\n"
        f"Assignment: {assignment.instruction}\n"
        f"Dependency handoffs:\n{dependencies}\n"
        f"Rules: {rules}\n"
        "Return only JSON matching the provided handoff schema. Keep the summary under "
        "800 characters and list only material blocking risks and focused checks."
    )
    return append_policy(prompt, policy)


def validate_agent_changes(
    assignment: AgentAssignment,
    changed_files: list[str],
) -> list[str]:
    normalized = sorted({_normalize_changed_path(item) for item in changed_files})
    if assignment.role is not AgentRole.IMPLEMENTER:
        if normalized:
            raise ValueError(
                f"read-only agent {assignment.key!r} modified files: {normalized}"
            )
        return normalized

    violations = [
        path
        for path in normalized
        if not any(_is_owned_path(path, prefix) for prefix in assignment.owned_paths)
    ]
    if violations:
        raise ValueError(
            f"agent {assignment.key!r} modified files outside owned paths: {violations}"
        )
    return normalized


def agent_commit_message(key: str) -> str:
    return f"chore(agent): complete {key}"


def _plan_fits(
    plan: AgentPlan,
    run: Run,
    settings: Settings,
    *,
    used_tokens: int,
    spent_cost_usd: Decimal,
) -> bool:
    required_tokens, required_cost = projected_plan_budget(plan, run, settings)
    return (
        used_tokens + required_tokens <= settings.max_tokens_per_run
        and spent_cost_usd + required_cost + settings.budget_reserve_usd
        <= run.max_cost_usd
    )


def _projected_tier(assignment: AgentSpec, run: Run) -> ModelTier:
    if assignment.role is AgentRole.REVIEWER:
        return ModelTier.CRITICAL
    if run.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        return ModelTier.CRITICAL
    return ModelTier.DEFAULT


def _collapse_implementers(implementers: list[AgentSpec]) -> AgentSpec:
    if not implementers:
        raise ValueError("cannot collapse an empty implementer set")
    owned_paths: list[str] = []
    instructions: list[str] = []
    for item in implementers:
        instructions.append(f"[{item.key}] {item.instruction}")
        for path in item.owned_paths:
            if path not in owned_paths:
                owned_paths.append(path)
    if len(owned_paths) > 24:
        raise ValueError(
            "combined implementation exceeds the maximum safe ownership manifest"
        )
    instruction = (
        "Implement the following previously independent scopes as one coherent change:\n"
        + "\n".join(instructions)
    )[:8_000]
    return AgentSpec(
        key="implement-combined",
        role=AgentRole.IMPLEMENTER,
        instruction=instruction,
        owned_paths=owned_paths,
    )


def _reviewer_for(implementers: list[AgentSpec]) -> AgentSpec:
    return AgentSpec(
        key=_reviewer_key({item.key for item in implementers}),
        role=AgentRole.REVIEWER,
        instruction=(
            "Audit the combined implementer results against the original goal, every "
            "constraint, and every acceptance criterion. Treat exact values, formulas, "
            "enumerated choices, and forbidden substitutions as contract terms. Put "
            "each unresolved actionable mismatch in risks; leave risks empty only when "
            "the change is semantically approved."
        ),
        depends_on=[item.key for item in implementers],
    )


def _reviewer_key(existing: set[str]) -> str:
    key = "review-change"
    index = 2
    while key in existing:
        key = f"review-change-{index}"
        index += 1
    return key


def _extract_json_object(text: str) -> str:
    normalized = text.strip()
    if normalized.startswith("```"):
        lines = normalized.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            normalized = "\n".join(lines[1:-1]).strip()
            if normalized.startswith("json"):
                normalized = normalized[4:].lstrip()
    start = normalized.find("{")
    end = normalized.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model output does not contain a JSON object")
    return normalized[start : end + 1]


def _normalize_changed_path(value: str) -> str:
    candidate = value.strip().replace("\\", "/").removeprefix("./")
    path = PurePosixPath(candidate)
    if (
        not candidate
        or path.is_absolute()
        or ".." in path.parts
        or ".git" in path.parts
        or any(token in candidate for token in ("\x00", ":", "*", "?", "[", "]"))
    ):
        raise ValueError(f"unsafe changed file path: {value!r}")
    return path.as_posix()


def _is_owned_path(path: str, prefix: str) -> bool:
    path_parts = PurePosixPath(path).parts
    prefix_parts = PurePosixPath(prefix).parts
    return path_parts[: len(prefix_parts)] == prefix_parts
