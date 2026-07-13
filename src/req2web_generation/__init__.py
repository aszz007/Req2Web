"""Deterministic PageSpec generation for Req2Web."""

from .builder import PageSpecBuilder
from .consistency import (
    CHECK_STATUSES,
    CONSISTENCY_REPORT_SCHEMA_VERSION,
    ConsistencyCheck,
    ConsistencyReport,
    MinimalConsistencyChecker,
)
from .renderer import (
    RENDER_MANIFEST_SCHEMA_VERSION,
    SUPPORTED_COMPONENT_TYPES,
    DeterministicPageRenderer,
    RenderResult,
)
from .result_package import (
    RESULT_PACKAGE_SCHEMA_VERSION,
    RESULT_SUMMARY_SCHEMA_VERSION,
    DeterministicResultPackager,
    ResultPackage,
    ResultPackageError,
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
    "CHECK_STATUSES",
    "ComponentSpec",
    "CONSISTENCY_REPORT_SCHEMA_VERSION",
    "ConsistencyCheck",
    "ConsistencyReport",
    "ConstraintSpec",
    "DeterministicPageRenderer",
    "EvidenceReference",
    "InteractionSpec",
    "LayoutSpec",
    "MinimalConsistencyChecker",
    "PageSpec",
    "PageSpecBuilder",
    "PageState",
    "PageUseCase",
    "RENDER_MANIFEST_SCHEMA_VERSION",
    "RESULT_PACKAGE_SCHEMA_VERSION",
    "RESULT_SUMMARY_SCHEMA_VERSION",
    "RenderResult",
    "ResultPackage",
    "ResultPackageError",
    "SectionSpec",
    "SUPPORTED_COMPONENT_TYPES",
    "DeterministicResultPackager",
    "TraceabilitySpec",
    "UseCaseTrace",
]
