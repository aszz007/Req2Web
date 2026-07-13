"""Deterministic PageSpec generation for Req2Web."""

from .builder import PageSpecBuilder
from .renderer import (
    RENDER_MANIFEST_SCHEMA_VERSION,
    SUPPORTED_COMPONENT_TYPES,
    DeterministicPageRenderer,
    RenderResult,
)
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
    "DeterministicPageRenderer",
    "EvidenceReference",
    "InteractionSpec",
    "LayoutSpec",
    "PageSpec",
    "PageSpecBuilder",
    "PageState",
    "PageUseCase",
    "RENDER_MANIFEST_SCHEMA_VERSION",
    "RenderResult",
    "SectionSpec",
    "SUPPORTED_COMPONENT_TYPES",
    "TraceabilitySpec",
    "UseCaseTrace",
]
