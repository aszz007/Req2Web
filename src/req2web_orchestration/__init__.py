"""Synthetic/mock-only orchestration boundaries for Req2Web M3."""

from .pre_route import (
    PRE_ROUTE_OUTCOME_SCHEMA_VERSION,
    PreRouteFailure,
    PreRouteOutcome,
    PreRouteOutcomeError,
    SyntheticMockPreRouteOrchestrator,
    validate_serialized_pre_route_outcome,
)

__all__ = [
    "PRE_ROUTE_OUTCOME_SCHEMA_VERSION",
    "PreRouteFailure",
    "PreRouteOutcome",
    "PreRouteOutcomeError",
    "SyntheticMockPreRouteOrchestrator",
    "validate_serialized_pre_route_outcome",
]