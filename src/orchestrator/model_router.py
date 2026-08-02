from __future__ import annotations

from dataclasses import dataclass

from .schemas import AgentRole, ModelTier, RiskLevel, TaskKind
from .settings import ReasoningEffort, Settings


@dataclass(frozen=True, slots=True)
class ModelProfile:
    model: str
    tier: ModelTier
    effort: ReasoningEffort


def choose_model_profile(
    *,
    settings: Settings,
    tier: ModelTier,
    risk: RiskLevel,
    kind: TaskKind,
    attempt: int = 1,
    role: AgentRole | None = None,
) -> ModelProfile:
    if kind is TaskKind.SUPERVISE:
        return ModelProfile(
            model=settings.codex_model_cheap,
            tier=ModelTier.CHEAP,
            effort=settings.codex_effort_scout,
        )
    if kind is TaskKind.PLAN:
        selected = ModelTier.CRITICAL if risk is RiskLevel.CRITICAL else ModelTier.DEFAULT
        return ModelProfile(
            model=_model(settings, selected),
            tier=selected,
            effort=(
                settings.codex_effort_critical
                if selected is ModelTier.CRITICAL
                else settings.codex_effort_plan
            ),
        )
    if role is AgentRole.REVIEWER:
        return ModelProfile(
            model=settings.codex_model_critical,
            tier=ModelTier.CRITICAL,
            effort=(
                settings.codex_effort_retry
                if risk is RiskLevel.CRITICAL
                else settings.codex_effort_critical
            ),
        )
    if kind is TaskKind.FIX:
        return ModelProfile(
            model=settings.codex_model_critical,
            tier=ModelTier.CRITICAL,
            effort=(
                settings.codex_effort_retry
                if attempt > 1 or risk is RiskLevel.CRITICAL
                else settings.codex_effort_critical
            ),
        )
    if risk is RiskLevel.CRITICAL:
        return ModelProfile(
            model=settings.codex_model_critical,
            tier=ModelTier.CRITICAL,
            effort=settings.codex_effort_retry,
        )
    if risk is RiskLevel.HIGH or attempt > 1 or tier is ModelTier.CRITICAL:
        return ModelProfile(
            model=settings.codex_model_critical,
            tier=ModelTier.CRITICAL,
            effort=settings.codex_effort_critical,
        )
    if tier is ModelTier.CHEAP:
        return ModelProfile(
            model=settings.codex_model_cheap,
            tier=ModelTier.CHEAP,
            effort=settings.codex_effort_scout,
        )
    return ModelProfile(
        model=settings.codex_model_default,
        tier=ModelTier.DEFAULT,
        effort=settings.codex_effort_default,
    )


def choose_model(
    *,
    settings: Settings,
    tier: ModelTier,
    risk: RiskLevel,
    kind: TaskKind,
) -> str:
    return choose_model_profile(
        settings=settings,
        tier=tier,
        risk=risk,
        kind=kind,
    ).model


def _model(settings: Settings, tier: ModelTier) -> str:
    if tier is ModelTier.CHEAP:
        return settings.codex_model_cheap
    if tier is ModelTier.CRITICAL:
        return settings.codex_model_critical
    return settings.codex_model_default
