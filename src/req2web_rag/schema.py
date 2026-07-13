from __future__ import annotations

from typing import Any


DOCUMENT_SCHEMA_VERSION = "req2web.rag.document.v1"
ALLOWED_ROLES = {
    "requirement",
    "ui_reference",
    "interaction_flow",
    "implementation",
    "validation",
}


def validate_document(document: dict[str, Any]) -> None:
    """Validate the stable outer envelope used by every RAG record."""

    required_types = {
        "schema_version": str,
        "doc_id": str,
        "role": str,
        "dataset": str,
        "subset": str,
        "sample_id": str,
        "title": str,
        "summary": str,
        "content": str,
        "tags": list,
        "references": list,
        "source": dict,
        "metadata": dict,
    }
    missing = [key for key in required_types if key not in document]
    if missing:
        raise ValueError(f"RAG document is missing fields: {missing}")
    for key, expected_type in required_types.items():
        if not isinstance(document[key], expected_type):
            raise TypeError(
                f"RAG document field {key!r} must be {expected_type.__name__}, "
                f"got {type(document[key]).__name__}"
            )
    if document["schema_version"] != DOCUMENT_SCHEMA_VERSION:
        raise ValueError(f"unsupported document schema: {document['schema_version']}")
    if document["role"] not in ALLOWED_ROLES:
        raise ValueError(f"unsupported RAG role: {document['role']}")
    if not document["doc_id"] or not document["content"].strip():
        raise ValueError("doc_id and content must not be empty")
    if any(not isinstance(tag, str) for tag in document["tags"]):
        raise TypeError("all tags must be strings")
    for reference in document["references"]:
        if not isinstance(reference, dict) or not isinstance(reference.get("kind"), str):
            raise TypeError("each reference must contain a string kind")
        if not isinstance(reference.get("uri"), str) or not reference["uri"]:
            raise TypeError("each reference must contain a non-empty string uri")
