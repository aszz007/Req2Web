"""Synthetic/mock-only orchestration boundaries for Req2Web M3."""

from .pre_route import (
    PRE_ROUTE_OUTCOME_SCHEMA_VERSION,
    PreRouteFailure,
    PreRouteOutcome,
    PreRouteOutcomeError,
    SyntheticMockPreRouteOrchestrator,
    validate_serialized_pre_route_outcome,
)

_TIER_B_LIVE_READINESS_EXPORT_NAMES = (
    "TIER_B_LOCAL_READINESS_VALIDATION_RECORD_SCHEMA_VERSION",
    "TierBLocalReadinessArtifactChain",
    "TierBLocalReadinessBinding",
    "TierBLocalReadinessValidationError",
    "TierBLocalReadinessValidationRecord",
    "validate_tier_b_local_artifact_chain",
    "validate_tier_b_local_readiness_validation_record_bytes",
)
_TIER_B_LIVE_READINESS_EXPORT_SET = frozenset(_TIER_B_LIVE_READINESS_EXPORT_NAMES)


def __getattr__(name: str):
    if name in _TIER_B_LIVE_READINESS_EXPORT_SET:
        from . import tier_b_live_readiness
        return getattr(tier_b_live_readiness, name)
    raise AttributeError(name)


__all__ = (
    "PRE_ROUTE_OUTCOME_SCHEMA_VERSION",
    "PreRouteFailure",
    "PreRouteOutcome",
    "PreRouteOutcomeError",
    "SyntheticMockPreRouteOrchestrator",
    "validate_serialized_pre_route_outcome",
    *_TIER_B_LIVE_READINESS_EXPORT_NAMES,
)
