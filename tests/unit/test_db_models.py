from orchestrator.automation_store import AutomationRunModel
from orchestrator.db_models import Base


def test_orchestrator_tables_are_registered() -> None:
    assert AutomationRunModel.metadata is Base.metadata
    assert set(Base.metadata.tables) == {
        "repositories",
        "runs",
        "tasks",
        "task_results",
        "approvals",
        "artifacts",
        "events",
        "agent_assignments",
        "automation_runs",
    }
