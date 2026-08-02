from orchestrator.db_models import Base


def test_phase_one_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        "repositories",
        "runs",
        "tasks",
        "task_results",
        "approvals",
        "artifacts",
        "events",
        "agent_assignments",
    }
