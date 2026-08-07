"""Deterministic minimal Agent chain for the Req2Web demo."""

from .chain import MinimalAgentChain, build_retrieval_queries
from .prompt_authority import (
    NODE_ORDER as PROMPT_NODE_ORDER,
    PROMPT_AUTHORITY_IDENTITY,
    PROMPT_AUTHORITY_REVISION,
    PROMPT_AUTHORITY_SCHEMA_VERSION,
    PROMPT_SCHEMA_VERSION,
    PromptAuthorityError,
    build_canonical_f3_interaction_plan,
    build_canonical_f1_f4_prompt,
    prompt_authority_manifest,
    validate_canonical_prompt,
)
from .schema import (
    AGENT_BUNDLE_SCHEMA_VERSION,
    AgentContextBundle,
    RequirementUnderstanding,
    UseCase,
)
from .understanding import (
    DeterministicRequirementProvider,
    RequirementUnderstandingProvider,
)

__all__ = [
    "AGENT_BUNDLE_SCHEMA_VERSION",
    "AgentContextBundle",
    "DeterministicRequirementProvider",
    "MinimalAgentChain",
    "PROMPT_AUTHORITY_IDENTITY",
    "PROMPT_AUTHORITY_REVISION",
    "PROMPT_AUTHORITY_SCHEMA_VERSION",
    "PROMPT_NODE_ORDER",
    "PROMPT_SCHEMA_VERSION",
    "PromptAuthorityError",
    "RequirementUnderstanding",
    "RequirementUnderstandingProvider",
    "UseCase",
    "build_retrieval_queries",
    "build_canonical_f3_interaction_plan",
    "build_canonical_f1_f4_prompt",
    "prompt_authority_manifest",
    "validate_canonical_prompt",
]
