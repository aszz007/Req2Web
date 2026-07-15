from __future__ import annotations

"""Bounded validation signals derived from existing GHPR RAG documents.

The adapter runs while a validation document is converted to a compact
retrieval result. It does not read a source JSONL, an asset, or ``data/raw``:
its sole input is the already-loaded unified document. A signal is emitted
only when the document's structured category and an existing Issue/PR
reference jointly satisfy one of the finite mappings below.
"""

import hashlib
from typing import Any


VALIDATION_SIGNAL_SCHEMA_VERSION = "req2web.validation.signal.v1"
_ALLOWED_REFERENCE_KINDS = {"issue", "pull_request"}

# Source-data categories, not case IDs, page titles, or business rules.
# Their values are the existing guided-builder controlled set.
VALIDATION_CATEGORY_TO_CONTROLLED_VALUE = {
    "input_error": "retry_recovery",
    "auth_access": "permission_recovery",
}
SUPPORTED_VALIDATION_VALUES = frozenset({"empty_state", "retry_recovery", "permission_recovery"})


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _whitelisted_reference(document: dict[str, Any]) -> str | None:
    references = document.get("references")
    if not isinstance(references, list):
        return None
    for reference in references:
        if not isinstance(reference, dict) or reference.get("kind") not in _ALLOWED_REFERENCE_KINDS:
            continue
        uri = _text(reference.get("uri"))
        if uri:
            return uri
    return None


def _signal_id(doc_id: str, value: str, source_value: str, reference_uri: str, rule: str) -> str:
    payload = "|".join((doc_id, value, source_value, reference_uri, rule))
    return "validation-signal-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def build_validation_signals(document: dict[str, Any]) -> list[dict[str, str]]:
    """Return deterministic compact signals for one unified validation document.

    Unknown categories are retained as an audit-only record. They are never
    converted into a guided-builder value. Missing identity, structured
    category, or whitelisted Issue/PR reference yields no signal; the legacy
    regression-evidence path remains available to the caller.
    """
    if document.get("role") != "validation":
        return []
    doc_id = _text(document.get("doc_id"))
    metadata = document.get("metadata")
    category = _text(metadata.get("category")) if isinstance(metadata, dict) else None
    reference_uri = _whitelisted_reference(document)
    if not doc_id or not category or not reference_uri:
        return []

    controlled_value = VALIDATION_CATEGORY_TO_CONTROLLED_VALUE.get(category)
    if controlled_value is None:
        outcome, value = "audit_only", "evidence_only"
        rule = "github_validation_category_adapter:v1:unsupported_category"
    else:
        outcome, value = "candidate", controlled_value
        rule = f"github_validation_category_adapter:v1:{category}_to_{value}"
    return [{
        "schema_version": VALIDATION_SIGNAL_SCHEMA_VERSION,
        "signal_id": _signal_id(doc_id, value, category, reference_uri, rule),
        "outcome": outcome,
        "value": value,
        "source_field": "metadata.category",
        "source_value": category,
        "reference_uri": reference_uri,
        "adapter_rule": rule,
    }]


def validated_compact_validation_signals(result: dict[str, Any]) -> list[dict[str, str]]:
    """Accept only self-consistent adapter signals already present in a result.

    Guidance consumes persisted compact Agent results. A forged field, value,
    reference, or rule becomes no candidate and follows the evidence-only
    fallback. Old compact results omit the optional field and remain valid.
    """
    raw = result.get("validation_signals")
    if raw is None or not isinstance(raw, list):
        return []
    doc_id = _text(result.get("doc_id"))
    references = result.get("references")
    reference_uris = {
        reference.get("uri")
        for reference in references
        if isinstance(reference, dict)
        and reference.get("kind") in _ALLOWED_REFERENCE_KINDS
        and _text(reference.get("uri"))
    } if isinstance(references, list) else set()
    expected_fields = {
        "schema_version", "signal_id", "outcome", "value", "source_field",
        "source_value", "reference_uri", "adapter_rule",
    }
    accepted: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_signal in raw:
        if not isinstance(raw_signal, dict) or set(raw_signal) != expected_fields:
            continue
        signal = {name: _text(raw_signal.get(name)) for name in expected_fields}
        if not all(signal.values()) or signal["schema_version"] != VALIDATION_SIGNAL_SCHEMA_VERSION:
            continue
        source_value = signal["source_value"]
        mapped = VALIDATION_CATEGORY_TO_CONTROLLED_VALUE.get(source_value)
        if mapped is None:
            expected_outcome, expected_value = "audit_only", "evidence_only"
            expected_rule = "github_validation_category_adapter:v1:unsupported_category"
        else:
            expected_outcome, expected_value = "candidate", mapped
            expected_rule = f"github_validation_category_adapter:v1:{source_value}_to_{mapped}"
        expected_id = _signal_id(doc_id or "", expected_value, source_value, signal["reference_uri"], expected_rule)
        if (
            signal["source_field"] != "metadata.category"
            or signal["reference_uri"] not in reference_uris
            or signal["outcome"] != expected_outcome
            or signal["value"] != expected_value
            or signal["adapter_rule"] != expected_rule
            or signal["signal_id"] != expected_id
        ):
            continue
        if signal["outcome"] == "candidate" and signal["value"] not in SUPPORTED_VALIDATION_VALUES:
            continue
        if signal["signal_id"] not in seen:
            accepted.append({name: signal[name] for name in expected_fields})
            seen.add(signal["signal_id"])
    return accepted
