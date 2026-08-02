from pathlib import Path

from orchestrator.model_router import choose_model_profile
from orchestrator.schemas import AgentRole, ModelTier, RiskLevel, TaskKind
from orchestrator.settings import Settings


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
        runtime_dir=tmp_path / "runtime",
    )


def test_supervisor_uses_luna_low(tmp_path: Path) -> None:
    profile = choose_model_profile(
        settings=make_settings(tmp_path),
        tier=ModelTier.CHEAP,
        risk=RiskLevel.NORMAL,
        kind=TaskKind.SUPERVISE,
    )
    assert profile.model == "gpt-5.6-luna"
    assert profile.tier is ModelTier.CHEAP
    assert profile.effort == "low"


def test_normal_implementer_uses_terra_high(tmp_path: Path) -> None:
    profile = choose_model_profile(
        settings=make_settings(tmp_path),
        tier=ModelTier.DEFAULT,
        risk=RiskLevel.NORMAL,
        kind=TaskKind.AGENT,
        role=AgentRole.IMPLEMENTER,
    )
    assert profile.model == "gpt-5.6-terra"
    assert profile.tier is ModelTier.DEFAULT
    assert profile.effort == "high"


def test_reviewer_uses_sol_medium(tmp_path: Path) -> None:
    profile = choose_model_profile(
        settings=make_settings(tmp_path),
        tier=ModelTier.CRITICAL,
        risk=RiskLevel.NORMAL,
        kind=TaskKind.AGENT,
        role=AgentRole.REVIEWER,
    )
    assert profile.model == "gpt-5.6-sol"
    assert profile.tier is ModelTier.CRITICAL
    assert profile.effort == "medium"


def test_retry_escalates_implementer_to_sol(tmp_path: Path) -> None:
    profile = choose_model_profile(
        settings=make_settings(tmp_path),
        tier=ModelTier.DEFAULT,
        risk=RiskLevel.NORMAL,
        kind=TaskKind.AGENT,
        attempt=2,
        role=AgentRole.IMPLEMENTER,
    )
    assert profile.model == "gpt-5.6-sol"
    assert profile.tier is ModelTier.CRITICAL
    assert profile.effort == "medium"


def test_critical_implementer_uses_sol_high(tmp_path: Path) -> None:
    profile = choose_model_profile(
        settings=make_settings(tmp_path),
        tier=ModelTier.DEFAULT,
        risk=RiskLevel.CRITICAL,
        kind=TaskKind.AGENT,
        role=AgentRole.IMPLEMENTER,
    )
    assert profile.model == "gpt-5.6-sol"
    assert profile.effort == "high"
