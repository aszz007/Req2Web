"""Minimal local RAG utilities for the Req2Web demo."""

from .corpus import ROLE_ORDER, build_unified_documents
from .index import TfidfIndex, build_tfidf_index
from .retriever import (
    DEFAULT_RETRIEVER_REGISTRY,
    Retriever,
    RetrieverConfig,
    RetrieverRegistry,
    TfidfRetriever,
    create_retriever,
)
from .schema import DOCUMENT_SCHEMA_VERSION, validate_document

__all__ = [
    "DOCUMENT_SCHEMA_VERSION",
    "DEFAULT_RETRIEVER_REGISTRY",
    "ROLE_ORDER",
    "Retriever",
    "RetrieverConfig",
    "RetrieverRegistry",
    "TfidfIndex",
    "TfidfRetriever",
    "build_tfidf_index",
    "build_unified_documents",
    "create_retriever",
    "validate_document",
]
