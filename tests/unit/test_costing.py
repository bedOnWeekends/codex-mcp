from decimal import Decimal
from pathlib import Path

from orchestrator.costing import estimate_usage_cost, projected_call_cost
from orchestrator.schemas import ModelTier
from orchestrator.settings import Settings


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://u:p@localhost/test",
        runtime_dir=tmp_path / "runtime",
    )


def test_terra_cost_distinguishes_cached_input(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    usage = estimate_usage_cost(
        settings,
        tier=ModelTier.DEFAULT,
        input_tokens=1_000_000,
        cached_input_tokens=400_000,
        output_tokens=100_000,
    )
    assert usage.total_tokens == 1_100_000
    assert usage.amount_usd == Decimal("3.100000")


def test_cached_tokens_cannot_exceed_input_tokens(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    usage = estimate_usage_cost(
        settings,
        tier=ModelTier.CHEAP,
        input_tokens=1_000,
        cached_input_tokens=2_000,
        output_tokens=0,
    )
    assert usage.cached_input_tokens == 1_000
    assert usage.amount_usd == Decimal("0.000100")


def test_projected_call_cost_increases_by_tier(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    assert projected_call_cost(settings, ModelTier.CHEAP) < projected_call_cost(
        settings,
        ModelTier.DEFAULT,
    )
    assert projected_call_cost(settings, ModelTier.DEFAULT) < projected_call_cost(
        settings,
        ModelTier.CRITICAL,
    )
