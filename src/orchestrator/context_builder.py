from __future__ import annotations

from .schemas import Repository, Run, Task


def build_task_prompt(repository: Repository, run: Run, task: Task) -> str:
    constraints = "\n".join(f"- {item}" for item in run.constraints) or "- None"
    return (
        "You are a worker in a durable coding orchestration system.\n"
        f"Repository: {repository.name}\n"
        f"Repository path: {repository.root_path}\n"
        f"Default branch: {repository.default_branch}\n"
        f"Run goal: {run.goal}\n"
        f"Risk level: {run.risk_level.value}\n"
        f"Constraints:\n{constraints}\n\n"
        f"Task kind: {task.kind.value}\n"
        f"Task instruction:\n{task.instruction}\n"
    )
