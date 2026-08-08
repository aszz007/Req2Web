"""Validate complete project-authored Phase 5 case drafts without execution."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Mapping, Sequence

from req2web_evaluation.phase5_publication_case_templates import (
    validate_phase5_publication_case_templates,
    validate_phase5_publication_intervention_freeze,
)
from req2web_generation.publication_language import contains_cjk_text


SCHEMA_VERSION = "req2web.phase5.publication_case_drafts.v1"


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
    lowered = value.lower()
    if any(
        marker in lowered
        for marker in ("http://", "https://", "file://", "/root/", "../", "d:\\")
    ):
        raise ValueError(f"{name} must not contain a URI or source path")
    return value


def _texts(
    value: object,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{name} must contain {minimum} to {maximum} items")
    result = [_text(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise ValueError(f"{name} contains duplicates")
    return result


def _false(value: object, name: str) -> None:
    if type(value) is not bool or value:
        raise ValueError(f"{name} must remain false")


def validate_phase5_publication_case_drafts(
    value: object,
    case_templates: object,
    intervention_freeze: object,
) -> dict[str, object]:
    """Validate four active English drafts while H1 and every action remain closed."""

    templates = validate_phase5_publication_case_templates(case_templates)
    freeze = validate_phase5_publication_intervention_freeze(
        intervention_freeze,
        templates,
    )
    draft = _exact(
        value,
        (
            "draft_id",
            "schema_version",
            "status",
            "language",
            "template_fixture_id",
            "intervention_freeze_manifest_id",
            "core_case_count",
            "reserve_case_count",
            "cases",
            "action_state",
        ),
        "publication case drafts",
    )
    if draft["schema_version"] != SCHEMA_VERSION:
        raise ValueError("publication case-draft schema drifted")
    if draft["status"] != "complete_project_authored_case_drafts_no_action":
        raise ValueError("publication case-draft status drifted")
    if draft["language"] != "en":
        raise ValueError("publication case-draft language drifted")
    if draft["template_fixture_id"] != templates["fixture_id"]:
        raise ValueError("publication case-draft template binding drifted")
    if draft["intervention_freeze_manifest_id"] != freeze["manifest_id"]:
        raise ValueError("publication case-draft intervention binding drifted")
    if draft["core_case_count"] != 4 or draft["reserve_case_count"] != 0:
        raise ValueError("publication case-draft counts drifted")
    if not isinstance(draft["cases"], list) or len(draft["cases"]) != 4:
        raise ValueError("publication case drafts must contain four active core cases")

    template_rows = [
        item for item in templates["templates"] if item["slot_kind"] == "core"
    ]
    freeze_rows = [
        item for item in freeze["rows"] if item["slot_kind"] == "core"
    ]
    case_refs: list[str] = []
    for index, raw_case in enumerate(draft["cases"]):
        case = _exact(
            raw_case,
            (
                "case_ref",
                "template_id",
                "slot_kind",
                "requirement_text",
                "target_device",
                "task_type",
                "use_cases",
                "constraints",
                "intervention_binding",
                "content_status",
                "real_h1_or_gold",
            ),
            f"cases[{index}]",
        )
        template = template_rows[index]
        freeze_row = freeze_rows[index]
        case_ref = _text(case["case_ref"], f"cases[{index}].case_ref")
        case_refs.append(case_ref)
        if case["template_id"] != template["template_id"]:
            raise ValueError("publication case-draft template order drifted")
        if case["slot_kind"] != template["slot_kind"]:
            raise ValueError("publication case-draft slot kind drifted")
        if case["target_device"] != template["target_device"]:
            raise ValueError("publication case-draft target device drifted")
        if case["task_type"] != template["task_type"]:
            raise ValueError("publication case-draft task type drifted")
        _text(case["requirement_text"], f"cases[{index}].requirement_text")
        _texts(
            case["constraints"],
            f"cases[{index}].constraints",
            minimum=2,
            maximum=6,
        )
        if not isinstance(case["use_cases"], list) or not 2 <= len(case["use_cases"]) <= 4:
            raise ValueError(f"cases[{index}].use_cases must contain 2 to 4 items")
        use_case_ids: list[str] = []
        for use_case_index, raw_use_case in enumerate(case["use_cases"]):
            use_case = _exact(
                raw_use_case,
                ("use_case_id", "description"),
                f"cases[{index}].use_cases[{use_case_index}]",
            )
            use_case_ids.append(
                _text(
                    use_case["use_case_id"],
                    f"cases[{index}].use_cases[{use_case_index}].use_case_id",
                )
            )
            _text(
                use_case["description"],
                f"cases[{index}].use_cases[{use_case_index}].description",
            )
        if len(use_case_ids) != len(set(use_case_ids)):
            raise ValueError("publication case-draft use-case IDs must be unique")

        binding = _exact(
            case["intervention_binding"],
            ("critical_role_id", "irrelevant_evidence_sha256"),
            f"cases[{index}].intervention_binding",
        )
        if binding["critical_role_id"] != freeze_row["critical_role_id"]:
            raise ValueError("publication case-draft critical role drifted")
        if (
            binding["irrelevant_evidence_sha256"]
            != freeze_row["irrelevant_evidence_sha256"]
        ):
            raise ValueError("publication case-draft irrelevant evidence drifted")
        if case["content_status"] != "complete_draft_not_sealed":
            raise ValueError("publication case-draft content status drifted")
        _false(case["real_h1_or_gold"], f"cases[{index}].real_h1_or_gold")

    if len(case_refs) != len(set(case_refs)):
        raise ValueError("publication case refs must be unique")

    action_state = _exact(
        draft["action_state"],
        (
            "case_drafts_sealed",
            "gold_content_present",
            "h1_opened",
            "model_action_authorized",
            "gpu_or_remote_action_authorized",
            "formal_evaluation_authorized",
            "formal_quality_claimed",
        ),
        "publication case-draft action state",
    )
    for key, item in action_state.items():
        _false(item, f"action_state.{key}")

    canonical_text = _canonical(draft).decode("utf-8")
    for prohibited in (
        "gold_label",
        "expected_page_spec",
        "provider_raw_response",
        "real_h1_content",
    ):
        if prohibited in canonical_text:
            raise ValueError(f"publication case drafts contain prohibited field {prohibited}")

    body = {key: item for key, item in draft.items() if key != "draft_id"}
    if draft["draft_id"] != _record_id("phase5-publication-case-drafts", body):
        raise ValueError("publication case-draft ID drifted")
    return draft


def build_phase5_publication_case_drafts_from_json_bytes(
    value: bytes,
    case_templates: object,
    intervention_freeze: object,
) -> dict[str, object]:
    """Parse and validate complete English case drafts from JSON bytes."""

    if not isinstance(value, bytes) or not value:
        raise ValueError("publication case-draft JSON must be non-empty bytes")
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("publication case-draft JSON is invalid") from exc
    return validate_phase5_publication_case_drafts(
        parsed,
        case_templates,
        intervention_freeze,
    )


__all__ = [
    "SCHEMA_VERSION",
    "build_phase5_publication_case_drafts_from_json_bytes",
    "validate_phase5_publication_case_drafts",
]
