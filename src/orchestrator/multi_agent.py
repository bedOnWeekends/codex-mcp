from __future__ import annotations

import json
from pathlib import PurePosixPath

from .schemas import (
    AgentAssignment,
    AgentPlan,
    AgentRole,
    AgentSpec,
    ModelTier,
    Repository,
    Run,
)


def parse_agent_plan(text: str) -> AgentPlan:
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
        raise ValueError("supervisor output does not contain a JSON object")
    return AgentPlan.model_validate_json(normalized[start : end + 1])


def fake_agent_plan() -> AgentPlan:
    return AgentPlan(
        assignments=[
            AgentSpec(
                key="explore-codebase",
                role=AgentRole.EXPLORER,
                instruction=(
                    "Inspect the codebase and identify the smallest set of files and "
                    "contracts relevant to the approved plan. Do not modify files."
                ),
                model_tier=ModelTier.CHEAP,
            ),
            AgentSpec(
                key="implement-source",
                role=AgentRole.IMPLEMENTER,
                instruction=(
                    "Implement the approved production-code changes within the owned "
                    "source tree while preserving unrelated behavior."
                ),
                depends_on=["explore-codebase"],
                owned_paths=["src"],
                model_tier=ModelTier.DEFAULT,
            ),
            AgentSpec(
                key="implement-tests",
                role=AgentRole.IMPLEMENTER,
                instruction=(
                    "Implement or update focused tests for the approved plan within "
                    "the owned test tree."
                ),
                depends_on=["explore-codebase"],
                owned_paths=["tests"],
                model_tier=ModelTier.DEFAULT,
            ),
            AgentSpec(
                key="review-integration",
                role=AgentRole.REVIEWER,
                instruction=(
                    "Review all implementer results for contract mismatches, missing "
                    "coverage, and integration risks. Do not modify files."
                ),
                depends_on=["implement-source", "implement-tests"],
                model_tier=ModelTier.CRITICAL,
            ),
        ]
    )


def build_supervisor_prompt(
    repository: Repository,
    run: Run,
    *,
    max_agents: int,
) -> str:
    constraints = "\n".join(f"- {item}" for item in run.constraints) or "- None"
    schema = json.dumps(AgentPlan.model_json_schema(), indent=2)
    return (
        "You are the supervisor for a durable multi-agent coding run. Decompose the "
        "approved implementation plan into a small dependency DAG. Return only one "
        "JSON object that validates against the supplied schema.\n\n"
        "Rules:\n"
        f"- Use between 3 and {max_agents} assignments.\n"
        "- Include at least one explorer, one implementer, and one reviewer.\n"
        "- Explorers and reviewers are read-only and must have no owned_paths.\n"
        "- Implementers must have non-overlapping repository-relative owned_paths.\n"
        "- Every reviewer must depend on every implementer.\n"
        "- Keep assignments independently executable and minimize shared context.\n"
        "- Do not include merge, deployment, credential, or trading actions.\n\n"
        f"Repository: {repository.name}\n"
        f"Default branch: {repository.default_branch}\n"
        f"Goal:\n{run.goal}\n\n"
        f"Constraints:\n{constraints}\n\n"
        f"Approved plan:\n{run.plan or 'No approved plan text.'}\n\n"
        f"JSON schema:\n{schema}"
    )


def build_agent_prompt(
    repository: Repository,
    run: Run,
    assignment: AgentAssignment,
    *,
    dependency_context: list[str],
) -> str:
    dependencies = "\n\n".join(dependency_context) or "No dependency output."
    ownership = (
        "\n".join(f"- {item}" for item in assignment.owned_paths)
        if assignment.owned_paths
        else "- Read-only assignment; no files may be modified."
    )
    role_rules = {
        AgentRole.EXPLORER: (
            "Inspect and report concrete findings. Do not modify files, create commits, "
            "or broaden the task."
        ),
        AgentRole.IMPLEMENTER: (
            "Modify only files covered by owned_paths. Do not commit, merge, push, "
            "deploy, or access credentials; the orchestrator creates the local commit."
        ),
        AgentRole.REVIEWER: (
            "Review the dependency results and integrated code visible in this "
            "worktree. Do not modify files. Report actionable defects and risks."
        ),
    }[assignment.role]
    return (
        f"You are agent {assignment.key!r} with role {assignment.role.value}.\n"
        f"Repository: {repository.name}\n"
        f"Run goal: {run.goal}\n\n"
        f"Role rules:\n{role_rules}\n\n"
        f"Owned paths:\n{ownership}\n\n"
        f"Assignment:\n{assignment.instruction}\n\n"
        f"Dependency context:\n{dependencies}\n\n"
        "Return a concise summary of findings or changes and any risks."
    )


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


def _normalize_changed_path(value: str) -> str:
    candidate = value.strip().replace("\\", "/").removeprefix("./")
    path = PurePosixPath(candidate)
    if not candidate or path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise ValueError(f"unsafe changed file path: {value!r}")
    return path.as_posix()


def _is_owned_path(path: str, prefix: str) -> bool:
    path_parts = PurePosixPath(path).parts
    prefix_parts = PurePosixPath(prefix).parts
    return path_parts[: len(prefix_parts)] == prefix_parts
