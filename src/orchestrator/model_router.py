from __future__ import annotations

from .schemas import ModelTier, RiskLevel, TaskKind
from .settings import Settings


def choose_model(*, settings: Settings, tier: ModelTier, risk: RiskLevel, kind: TaskKind) -> str | None:
    if risk is RiskLevel.CRITICAL or kind is TaskKind.REVIEW or tier is ModelTier.CRITICAL:
        return settings.codex_model_critical or settings.codex_model_default
    if tier is ModelTier.CHEAP:
        return settings.codex_model_cheap or settings.codex_model_default
    return settings.codex_model_default
