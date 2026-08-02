from __future__ import annotations

from pathlib import Path

from .schemas import Repository, Run, Task, TaskKind


def build_task_prompt(
    repository: Repository,
    run: Run,
    task: Task,
    *,
    workspace: Path,
) -> str:
    constraints = "\n".join(f"- {item}" for item in run.constraints) or "- None"
    plan_section = ""
    if task.kind in {TaskKind.IMPLEMENT, TaskKind.FIX}:
        plan_section = (
            "\nApproved implementation plan:\n"
            f"{run.plan or 'No plan text is available.'}\n"
        )
    safety = (
        "Read the repository only. Do not modify files."
        if task.kind is TaskKind.PLAN
        else (
            "Modify files only inside the supplied Git worktree. Do not commit, "
            "merge, push, deploy, or access live trading credentials."
        )
    )
    return (
        "You are a worker in a durable coding orchestration system.\n"
        f"Repository: {repository.name}\n"
        f"Registered repository: {repository.root_path}\n"
        f"Workspace: {workspace}\n"
        f"Default branch: {repository.default_branch}\n"
        f"Run goal: {run.goal}\n"
        f"Risk level: {run.risk_level.value}\n"
        f"Constraints:\n{constraints}\n"
        f"Safety boundary: {safety}\n"
        f"{plan_section}\n"
        f"Task kind: {task.kind.value}\n"
        f"Task instruction:\n{task.instruction}\n"
    )
