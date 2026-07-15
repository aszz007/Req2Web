from __future__ import annotations

"""Bounded form-structure signals derived from compact UI-reference results."""

import hashlib
from typing import Any


UI_STRUCTURE_SIGNAL_SCHEMA_VERSION = "req2web.ui.structure.signal.v1"
FORM_STRUCTURE_CATEGORIES = frozenset({"form_input", "login_auth", "settings"})
_UI_REFERENCE_KINDS = frozenset({"screenshot", "semantic_image", "view_hierarchy", "semantic_annotation"})
_EXPECTED_FIELDS = frozenset({
    "schema_version", "signal_id", "outcome", "value", "source_doc_id",
    "source_fields", "source_values", "reference_uris", "adapter_rule",
})
_RULE = "rico_ui_structure_adapter:v1:category_and_input_to_form_structure"


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _references(document: dict[str, Any]) -> list[str]:
    references = document.get("references")
    if not isinstance(references, list):
        return []
    values = [
        uri for reference in references
        if isinstance(reference, dict)
        and reference.get("kind") in _UI_REFERENCE_KINDS
        and (uri := _text(reference.get("uri")))
    ]
    return list(dict.fromkeys(values))


def _signal_id(doc_id: str, category: str, input_count: int, reference_uris: list[str]) -> str:
    payload = "|".join((doc_id, category, str(input_count), *reference_uris, _RULE))
    return "ui-structure-signal-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def build_ui_structure_signals(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Emit only the finite, source-traceable form_structure candidate."""
    if document.get("role") != "ui_reference":
        return []
    doc_id = _text(document.get("doc_id"))
    metadata = document.get("metadata")
    if not doc_id or not isinstance(metadata, dict):
        return []
    category = _text(metadata.get("category"))
    labels = metadata.get("component_labels")
    input_count = labels.get("Input") if isinstance(labels, dict) else None
    reference_uris = _references(document)
    if (
        category not in FORM_STRUCTURE_CATEGORIES
        or isinstance(input_count, bool)
        or not isinstance(input_count, int)
        or input_count <= 0
        or not reference_uris
    ):
        return []
    source_fields = ["metadata.category", "metadata.component_labels.Input"]
    source_values = [category, input_count]
    return [{
        "schema_version": UI_STRUCTURE_SIGNAL_SCHEMA_VERSION,
        "signal_id": _signal_id(doc_id, category, input_count, reference_uris),
        "outcome": "candidate",
        "value": "form_structure",
        "source_doc_id": doc_id,
        "source_fields": source_fields,
        "source_values": source_values,
        "reference_uris": reference_uris,
        "adapter_rule": _RULE,
    }]


def validated_compact_ui_structure_signals(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only intact form signals already attached to a compact result."""
    raw = result.get("ui_structure_signals")
    if raw is None or not isinstance(raw, list):
        return []
    doc_id = _text(result.get("doc_id"))
    references = _references(result)
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != _EXPECTED_FIELDS or not doc_id:
            continue
        if (
            item.get("schema_version") != UI_STRUCTURE_SIGNAL_SCHEMA_VERSION
            or item.get("outcome") != "candidate"
            or item.get("value") != "form_structure"
            or item.get("source_doc_id") != doc_id
            or item.get("adapter_rule") != _RULE
        ):
            continue
        fields, values, uris = item.get("source_fields"), item.get("source_values"), item.get("reference_uris")
        if (
            fields != ["metadata.category", "metadata.component_labels.Input"]
            or not isinstance(values, list) or len(values) != 2
            or not isinstance(values[0], str) or values[0] not in FORM_STRUCTURE_CATEGORIES
            or isinstance(values[1], bool) or not isinstance(values[1], int) or values[1] <= 0
            or not isinstance(uris, list) or not uris or len(uris) != len(set(uris))
            or any(not isinstance(uri, str) or not uri or uri not in references for uri in uris)
        ):
            continue
        expected_id = _signal_id(doc_id, values[0], values[1], uris)
        if item.get("signal_id") != expected_id or expected_id in seen:
            continue
        accepted.append({name: item[name] for name in _EXPECTED_FIELDS})
        seen.add(expected_id)
    return accepted
