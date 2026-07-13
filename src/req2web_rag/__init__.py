"""Minimal local RAG utilities for the Req2Web demo."""

from .corpus import ROLE_ORDER, build_unified_documents
from .index import TfidfIndex, build_tfidf_index
from .schema import DOCUMENT_SCHEMA_VERSION, validate_document

__all__ = [
    "DOCUMENT_SCHEMA_VERSION",
    "ROLE_ORDER",
    "TfidfIndex",
    "build_tfidf_index",
    "build_unified_documents",
    "validate_document",
]
