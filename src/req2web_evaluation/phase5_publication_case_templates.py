"""Phase 5 publication case-template validation without execution authority."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Mapping, Sequence

from req2web_evaluation.phase5_formal_holdout_plan import (
    PUBLICATION_SCOPE_DESCRIPTOR_REVISION,
)
from req2web_generation.publication_language import contains_cjk_text


SCHEMA_VERSION = "req2web.phase5.publication_case_templates.v1"
TEMPLATE_MATRIX_SCHEMA_VERSION = (
    "req2web.phase5.publication_case_template_matrix.v1"
)
_ALLOWED_ROLES = {
    "requirement",
    "ui_reference",
    "interaction_flow",
    "implementation",
    "validation",
}
_EXPECTED_CORE_SLOTS = (
    "information_retrieval",
    "structured_form_transaction",
    "media_async_processing",
    "stateful_recovery_responsive",
)
_EXPECTED_RESERVE_SLOTS = (
    "appointment_scheduling",
    "settings_administration",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_id(prefix: str, value: object) -> str:
    return f"{prefix}-{sha256(_canonical(value)).hexdigest()}"


def _exact(value: object, keys: Sequence[str], name: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ValueError(f"{name} has invalid keys")
    return dict(value)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    if contains_cjk_text(value):
        raise ValueError(f"{name} must remain English-only")
    return value


def _texts(
    value: object,
    name: str,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError(f"{name} must contain at least {minimum} items")
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{name} must contain at most {maximum} items")
    result = [_text(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise ValueError(f"{name} contains duplicates")
    return result


def _false(value: object, name: str) -> None:
    if type(value) is not bool or value:
        raise ValueError(f"{name} must remain false")


def _integer(value: object, name: str, expected: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise ValueError(f"{name} must equal {expected}")


def _validate_template(value: object, index: int) -> dict[str, object]:
    template = _exact(
        value,
        (
            "template_id",
            "slot_id",
            "slot_kind",
            "title",
            "requirement_outline",
            "target_device",
            "task_type",
            "use_case_outlines",
            "required_state_patterns",
            "acceptance_observables",
            "critical_role_recommendation",
            "criticality_basis",
            "irrelevant_evidence_outline",
            "content_status",
            "real_h1_or_gold",
        ),
        f"templates[{index}]",
    )
    for key in (
        "template_id",
        "slot_id",
        "slot_kind",
        "title",
        "requirement_outline",
        "target_device",
        "task_type",
        "criticality_basis",
        "irrelevant_evidence_outline",
        "content_status",
    ):
        _text(template[key], f"templates[{index}].{key}")
    if template["slot_kind"] not in {"core", "reserve"}:
        raise ValueError(f"templates[{index}].slot_kind is invalid")
    if template["target_device"] not in {"desktop", "mobile", "responsive"}:
        raise ValueError(f"templates[{index}].target_device is invalid")
    if template["content_status"] != "draft_template_not_sealed":
        raise ValueError(f"templates[{index}].content_status drifted")
    _texts(
        template["use_case_outlines"],
        f"templates[{index}].use_case_outlines",
        minimum=2,
        maximum=4,
    )
    _texts(
        template["required_state_patterns"],
        f"templates[{index}].required_state_patterns",
        minimum=2,
    )
    _texts(
        template["acceptance_observables"],
        f"templates[{index}].acceptance_observables",
        minimum=2,
    )
    role = _text(
        template["critical_role_recommendation"],
        f"templates[{index}].critical_role_recommendation",
    )
    if role not in _ALLOWED_ROLES:
        raise ValueError(f"templates[{index}].critical role is invalid")
    _false(template["real_h1_or_gold"], f"templates[{index}].real_h1_or_gold")
    return template


def validate_phase5_publication_case_templates(
    value: object,
) -> dict[str, object]:
    """Validate project-authored draft templates without reading H1/gold."""

    fixture = _exact(
        value,
        (
            "fixture_id",
            "schema_version",
            "status",
            "language",
            "scope_descriptor_revision",
            "core_template_count",
            "reserve_template_count",
            "templates",
            "condition_template_policy",
            "action_state",
        ),
        "publication case-template fixture",
    )
    if fixture["schema_version"] != SCHEMA_VERSION:
        raise ValueError("publication case-template schema drifted")
    if fixture["status"] != "project_authored_case_templates_no_action":
        raise ValueError("publication case-template status drifted")
    if fixture["language"] != "en":
        raise ValueError("publication case-template language drifted")
    if fixture["scope_descriptor_revision"] != PUBLICATION_SCOPE_DESCRIPTOR_REVISION:
        raise ValueError("publication scope descriptor revision drifted")
    _integer(fixture["core_template_count"], "core_template_count", 4)
    _integer(fixture["reserve_template_count"], "reserve_template_count", 2)

    if not isinstance(fixture["templates"], list):
        raise ValueError("templates must be an array")
    templates = [
        _validate_template(item, index)
        for index, item in enumerate(fixture["templates"])
    ]
    if len(templates) != 6:
        raise ValueError("publication case-template count drifted")
    template_ids = [str(item["template_id"]) for item in templates]
    if len(template_ids) != len(set(template_ids)):
        raise ValueError("publication case-template IDs must be unique")
    core_slots = tuple(
        str(item["slot_id"]) for item in templates if item["slot_kind"] == "core"
    )
    reserve_slots = tuple(
        str(item["slot_id"]) for item in templates if item["slot_kind"] == "reserve"
    )
    if core_slots != _EXPECTED_CORE_SLOTS:
        raise ValueError("publication core slot order drifted")
    if reserve_slots != _EXPECTED_RESERVE_SLOTS:
        raise ValueError("publication reserve slot order drifted")

    condition_policy = _exact(
        fixture["condition_template_policy"],
        (
            "condition_ids",
            "irrelevant_item_count_per_case",
            "irrelevant_payloads_frozen",
            "critical_role_ids_frozen",
            "reserve_replacement_after_opening_allowed",
            "result_driven_template_change_allowed",
        ),
        "condition_template_policy",
    )
    if condition_policy["condition_ids"] != [
        "none",
        "irrelevant_evidence",
        "remove_critical_role",
    ]:
        raise ValueError("condition template IDs drifted")
    _integer(
        condition_policy["irrelevant_item_count_per_case"],
        "irrelevant_item_count_per_case",
        1,
    )
    for key in (
        "irrelevant_payloads_frozen",
        "critical_role_ids_frozen",
        "reserve_replacement_after_opening_allowed",
        "result_driven_template_change_allowed",
    ):
        _false(condition_policy[key], f"condition_template_policy.{key}")

    action_state = _exact(
        fixture["action_state"],
        (
            "real_case_content_frozen",
            "gold_content_present",
            "h1_opened",
            "model_action_authorized",
            "gpu_or_remote_action_authorized",
            "formal_evaluation_authorized",
            "formal_quality_claimed",
        ),
        "action_state",
    )
    for key, item in action_state.items():
        _false(item, f"action_state.{key}")

    canonical_text = _canonical(fixture).decode("utf-8")
    for prohibited in (
        "gold_label",
        "expected_page_spec",
        "provider_raw_response",
        "real_h1_content",
    ):
        if prohibited in canonical_text:
            raise ValueError(f"publication templates contain prohibited field {prohibited}")

    body = {key: item for key, item in fixture.items() if key != "fixture_id"}
    if fixture["fixture_id"] != _record_id("phase5-publication-templates", body):
        raise ValueError("publication case-template fixture ID drifted")
    return fixture


def build_phase5_publication_case_templates_from_json_bytes(
    value: bytes,
) -> dict[str, object]:
    """Parse and validate the English draft-template fixture from bytes."""

    if not isinstance(value, bytes) or not value:
        raise ValueError("publication case-template JSON must be non-empty bytes")
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("publication case-template JSON is invalid") from exc
    return validate_phase5_publication_case_templates(parsed)


def _expected_template_matrix_body(
    fixture: dict[str, object],
) -> dict[str, object]:
    conditions = ["none", "irrelevant_evidence", "remove_critical_role"]
    rows: list[dict[str, object]] = []
    core_templates = [
        item
        for item in fixture["templates"]
        if item["slot_kind"] == "core"
    ]
    for template in core_templates:
        for condition_id in conditions:
            row_body = {
                "template_id": template["template_id"],
                "slot_id": template["slot_id"],
                "condition_id": condition_id,
                "model_node_ids": ["F1", "F2", "F3", "F4"],
                "generate_call_cap": 4,
                "status": "not_executed_draft_template_only",
                "terminal_status": None,
            }
            rows.append(
                {
                    "row_id": _record_id(
                        "phase5-publication-template-row",
                        row_body,
                    ),
                    **row_body,
                }
            )
    return {
        "schema_version": TEMPLATE_MATRIX_SCHEMA_VERSION,
        "status": "draft_template_matrix_no_action",
        "fixture_id": fixture["fixture_id"],
        "scope_descriptor_revision": fixture["scope_descriptor_revision"],
        "core_template_count": 4,
        "condition_count": 3,
        "runtime_row_count": 12,
        "node_generate_call_cap": 48,
        "rows": rows,
        "action_state": {
            "case_identities_frozen": False,
            "condition_payloads_frozen": False,
            "h1_opened": False,
            "model_action_authorized": False,
            "gpu_or_remote_action_authorized": False,
            "formal_evaluation_authorized": False,
            "formal_quality_claimed": False,
        },
    }


def validate_phase5_publication_template_matrix(
    value: object,
    case_templates: object,
) -> dict[str, object]:
    """Validate the 4-by-3 draft matrix without creating formal rows."""

    fixture = validate_phase5_publication_case_templates(case_templates)
    matrix = _exact(
        value,
        (
            "matrix_id",
            "schema_version",
            "status",
            "fixture_id",
            "scope_descriptor_revision",
            "core_template_count",
            "condition_count",
            "runtime_row_count",
            "node_generate_call_cap",
            "rows",
            "action_state",
        ),
        "publication template matrix",
    )
    body = {key: item for key, item in matrix.items() if key != "matrix_id"}
    expected = _expected_template_matrix_body(fixture)
    if body != expected:
        raise ValueError("publication template matrix content drifted")
    if matrix["matrix_id"] != _record_id("phase5-publication-template-matrix", body):
        raise ValueError("publication template matrix ID drifted")
    return matrix


def build_phase5_publication_template_matrix(
    case_templates: object,
) -> dict[str, object]:
    """Build the 12-row draft template matrix with every action gate closed."""

    fixture = validate_phase5_publication_case_templates(case_templates)
    body = _expected_template_matrix_body(fixture)
    matrix = {
        "matrix_id": _record_id("phase5-publication-template-matrix", body),
        **body,
    }
    return validate_phase5_publication_template_matrix(matrix, fixture)


__all__ = [
    "SCHEMA_VERSION",
    "TEMPLATE_MATRIX_SCHEMA_VERSION",
    "build_phase5_publication_case_templates_from_json_bytes",
    "build_phase5_publication_template_matrix",
    "validate_phase5_publication_case_templates",
    "validate_phase5_publication_template_matrix",
]
