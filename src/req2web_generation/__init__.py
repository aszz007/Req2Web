"""Deterministic PageSpec generation for Req2Web."""

from .builder import PageSpecBuilder
from .schema import (
    PAGE_SPEC_SCHEMA_VERSION,
    AcceptanceCheck,
    ComponentSpec,
    ConstraintSpec,
    EvidenceReference,
    InteractionSpec,
    LayoutSpec,
    PageSpec,
    PageState,
    PageUseCase,
    SectionSpec,
    TraceabilitySpec,
    UseCaseTrace,
)

__all__ = [
    "PAGE_SPEC_SCHEMA_VERSION",
    "AcceptanceCheck",
    "ComponentSpec",
    "ConstraintSpec",
    "EvidenceReference",
    "InteractionSpec",
    "LayoutSpec",
    "PageSpec",
    "PageSpecBuilder",
    "PageState",
    "PageUseCase",
    "SectionSpec",
    "TraceabilitySpec",
    "UseCaseTrace",
]
