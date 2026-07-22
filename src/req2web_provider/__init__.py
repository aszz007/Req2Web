"""Internal vendor-neutral Provider boundary for Req2Web M3."""

from .semantic_candidate import (
    MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION,
    PAGE_SPEC_ASSEMBLY_REPORT_SCHEMA_VERSION,
    PROVIDER_ERROR_SCHEMA_VERSION,
    PROVIDER_RAW_RESPONSE_SCHEMA_VERSION,
    AssembledPageSpec,
    CanonicalPageSpecAssembler,
    ModelSemanticCandidate,
    PageSpecAssemblyReport,
    ProviderError,
    ProviderProtocolError,
    ProviderRawResponse,
    parse_provider_raw_response,
)

__all__ = [
    "MODEL_SEMANTIC_CANDIDATE_SCHEMA_VERSION",
    "PAGE_SPEC_ASSEMBLY_REPORT_SCHEMA_VERSION",
    "PROVIDER_ERROR_SCHEMA_VERSION",
    "PROVIDER_RAW_RESPONSE_SCHEMA_VERSION",
    "AssembledPageSpec",
    "CanonicalPageSpecAssembler",
    "ModelSemanticCandidate",
    "PageSpecAssemblyReport",
    "ProviderError",
    "ProviderProtocolError",
    "ProviderRawResponse",
    "parse_provider_raw_response",
]
