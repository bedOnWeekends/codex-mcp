from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .schemas import ModelTier
from .settings import Settings

_MILLION = Decimal("1000000")


@dataclass(frozen=True, slots=True)
class UsageCost:
    input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    amount_usd: Decimal

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def estimate_usage_cost(
    settings: Settings,
    *,
    tier: ModelTier,
    input_tokens: int,
    cached_input_tokens: int,
    cache_write_tokens: int,
    output_tokens: int,
) -> UsageCost:
    if (
        min(
            input_tokens,
            cached_input_tokens,
            cache_write_tokens,
            output_tokens,
        )
        < 0
    ):
        raise ValueError("token usage must be non-negative")
    cached = min(cached_input_tokens, input_tokens)
    cache_write = min(cache_write_tokens, input_tokens - cached)
    uncached = input_tokens - cached - cache_write
    input_rate, cached_rate, output_rate = _rates(settings, tier)
    cache_write_rate = input_rate * settings.codex_cache_write_multiplier
    amount = (
        Decimal(uncached) * input_rate
        + Decimal(cached) * cached_rate
        + Decimal(cache_write) * cache_write_rate
        + Decimal(output_tokens) * output_rate
    ) / _MILLION
    return UsageCost(
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        cache_write_tokens=cache_write,
        output_tokens=output_tokens,
        amount_usd=amount.quantize(Decimal("0.000001")),
    )


def projected_call_cost(settings: Settings, tier: ModelTier) -> Decimal:
    if tier is ModelTier.CHEAP:
        return settings.projected_call_cost_usd_cheap
    if tier is ModelTier.CRITICAL:
        return settings.projected_call_cost_usd_critical
    return settings.projected_call_cost_usd_default


def projected_call_tokens(settings: Settings, tier: ModelTier) -> int:
    if tier is ModelTier.CHEAP:
        return settings.projected_call_tokens_cheap
    if tier is ModelTier.CRITICAL:
        return settings.projected_call_tokens_critical
    return settings.projected_call_tokens_default


def _rates(
    settings: Settings,
    tier: ModelTier,
) -> tuple[Decimal, Decimal, Decimal]:
    if tier is ModelTier.CHEAP:
        return (
            settings.codex_price_cheap_input_per_mtok,
            settings.codex_price_cheap_cached_input_per_mtok,
            settings.codex_price_cheap_output_per_mtok,
        )
    if tier is ModelTier.CRITICAL:
        return (
            settings.codex_price_critical_input_per_mtok,
            settings.codex_price_critical_cached_input_per_mtok,
            settings.codex_price_critical_output_per_mtok,
        )
    return (
        settings.codex_price_default_input_per_mtok,
        settings.codex_price_default_cached_input_per_mtok,
        settings.codex_price_default_output_per_mtok,
    )
