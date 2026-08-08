"""Phase 5 formal-holdout no-action contracts.

This module is intentionally limited to synthetic in-memory fixtures and
opaque metadata. It has no filesystem reader, model/provider adapter, runtime
launcher, network client, GPU interface, package writer, or evaluator-gold
consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from itertools import combinations
from typing import Mapping, Sequence
import unicodedata


SYNTHETIC_FIXTURE_SCHEMA_VERSION = "req2web.phase5.synthetic_holdout_fixture.v1"
PHASE5_NO_ACTION_BUNDLE_SCHEMA_VERSION = "req2web.phase5.no_action.bundle.v1"
PHASE5_DECLARATION_SCHEMA_VERSION = "req2web.phase5.no_action.declaration.v1"
PHASE5_CUSTODY_SCHEMA_VERSION = "req2web.phase5.evaluator_custody.interface.v1"
PHASE5_DUPLICATE_AUDIT_SCHEMA_VERSION = "req2web.phase5.duplicate_audit.synthetic.v1"
PHASE5_HOLDOUT_MANIFEST_SCHEMA_VERSION = "req2web.phase5.holdout_manifest.opaque.v1"
PHASE5_METRIC_CONTRACT_SCHEMA_VERSION = "req2web.phase5.metric_contract.no_action.v1"
PHASE5_ARTIFACT_INVENTORY_SCHEMA_VERSION = "req2web.phase5.artifact_inventory.no_action.v1"
PHASE5_EVALUATION_MATRIX_SCHEMA_VERSION = "req2web.phase5.evaluation_matrix.no_action.v1"
PHASE5_EXECUTION_POLICY_SCHEMA_VERSION = "req2web.phase5.execution_policy.no_action.v1"
PHASE5_OPENING_GATE_SCHEMA_VERSION = "req2web.phase5.h1_opening_gate.no_action.v1"
PHASE5_REPLAY_SCHEMA_VERSION = "req2web.phase5.result_replay.no_action.v1"

PHASE4_EXIT_COMMIT = "9cd02e9040c2aaa41b4ddc44a3b0146770370b2b"
PHASE4_EXIT_HANDOFF_SHA256 = "f6fa260c75c3505a123d5000f4cb9505911e198adcd73d00f3d014d72d764f1d"
PHASE4_STABILITY_SUMMARY_FILE_SHA256 = "6683ab9bd44d09e7382ae130a8020850d0b0487050d03c9e7b817af98d0ba411"
PHASE4_STABILITY_SUMMARY_CANONICAL_SHA256 = "e0e166bc9a2a537d1adcf3e49293b3281bf051ac59ecb87200450e8452b5676c"
PHASE4_STABILITY_RUN_ID = "p4-05-remote-qwen-stability-full-direct-94bc6fd05f644c13"

_SYNTHETIC_FIXTURE_ID = "phase5-synthetic-holdout-structure-v1"
_SYNTHETIC_EXPERIMENT_SEED = "phase5-synthetic-order-v1"
_MODEL_NODES = ("F1", "F2", "F3", "F4")
_GROUP_ORDER = {"G0": 0, "G1": 1, "G2": 2}
_ADJUDICATION_LABELS = (
    "exact_duplicate",
    "near_duplicate",
    "same_domain_nonduplicate",
    "unrelated",
)
_STOP_RULES = (
    "phase4_exit_handoff_missing_or_drifted",
    "owner_action_approval_missing",
    "path3_non_h1_route_selected",
    "real_h1_or_gold_content_exposed",
    "duplicate_audit_or_dual_annotation_incomplete",
    "manifest_release_serializer_parity_or_metric_not_frozen",
    "matrix_order_or_cap_not_frozen",
    "raw_first_evidence_or_attempt_accounting_unavailable",
    "retry_or_budget_reset_requested",
    "h1_result_driven_change_requested",
    "identity_hash_call_time_cost_storage_or_resource_cap_reached",
    "formal_main_matrix_row_missing_terminal_record",
)
_EVIDENCE_CONTROL_METRICS = (
    "candidate_attribution_coverage",
    "deep_label_attribution_correctness",
    "controlled_influence_accuracy",
    "intervention_locality",
    "artifact_provenance_completeness",
)
_ERROR_RECOVERY_METRICS = (
    "fault_localization_accuracy",
    "one_repair_success",
    "repair_locality",
    "fallback_delivery",
)
_FUNCTIONAL_ACCEPTANCE_METRICS = (
    "independent_acceptance_coverage",
    "executable_pass_rate",
    "all_gold_criterion_success",
    "requirement_outcome",
)
_SUPPORTING_METRICS = (
    "page_spec_first_pass_validity",
    "page_spec_recovered_validity",
    "delivery_route_distribution",
    "reproducibility",
    "latency",
    "cost",
    "vram_ram",
    "tokens",
    "annotation_agreement",
)
_ADJUDICATION_ENTRY_FIELDS = (
    "left_subject_id",
    "right_subject_id",
    "verdict",
    "reason_code",
    "adjudicator_ref",
    "decision_sha256",
)
_ARTIFACT_SLOTS = (
    ("g0_prefreeze_reference", "req2web.phase5.g0_prefreeze_reference.v1"),
    ("provider_raw_response", "opaque_raw_bytes_sha256_length"),
    ("node_attempt_ledger", "req2web.phase5.node_attempt_ledger.v1"),
    ("candidate_chain", "raw_semantic_assembled_hash_chain"),
    ("gate_and_acceptance_reports", "req2web.phase5.gate_acceptance_refs.v1"),
    ("result_package", "req2web.result.package.v1_or_v2"),
    ("evaluation_result_row", "req2web.phase5.evaluation_result_row.v1"),
    ("replay_receipt", PHASE5_REPLAY_SCHEMA_VERSION),
)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _record_id(prefix: str, value: object) -> str:
    return f"{prefix}-{_sha256_json(value)}"


def _exact_mapping(value: object, keys: Sequence[str], field_name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    if set(value) != set(keys):
        raise ValueError(f"{field_name} must have exact keys {tuple(keys)}")
    return dict(value)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_exact_text(value: object, expected: str, field_name: str) -> str:
    value = _require_text(value, field_name)
    if value != expected:
        raise ValueError(f"{field_name} must be {expected!r}")
    return value


def _require_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _require_false(value: object, field_name: str) -> None:
    if _require_bool(value, field_name) is not False:
        raise ValueError(f"{field_name} must remain false in the no-action contract")


def _require_true(value: object, field_name: str) -> None:
    if _require_bool(value, field_name) is not True:
        raise ValueError(f"{field_name} must be true")


def _require_int(value: object, field_name: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{field_name} is outside the allowed range")
    return value


def _require_sha256(value: object, field_name: str) -> str:
    value = _require_text(value, field_name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _require_optional_sha256(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, field_name)


def _require_optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _require_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    return list(value)


def _sorted_unique_texts(value: object, field_name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    values = _require_list(value, field_name)
    if not allow_empty and not values:
        raise ValueError(f"{field_name} must not be empty")
    result: list[str] = []
    previous: str | None = None
    for index, item in enumerate(values):
        text = _require_text(item, f"{field_name}[{index}]")
        if previous is not None and text <= previous:
            raise ValueError(f"{field_name} must be sorted and unique")
        result.append(text)
        previous = text
    return tuple(result)


def _normalize_exact_text(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    normalized = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    if not normalized:
        raise ValueError(f"{field_name} normalizes to empty")
    return normalized


def _fixture_seed(case_id: str, experiment_seed: str) -> int:
    material = f"{experiment_seed}\0{case_id}\0phase5-no-action".encode("utf-8")
    return int(sha256(material).hexdigest()[:8], 16)


def _dependency_order(block_dependencies: Mapping[str, tuple[str, ...]], experiment_seed: str) -> tuple[str, ...]:
    remaining = set(block_dependencies)
    completed: set[str] = set()
    ordered: list[str] = []
    while remaining:
        ready = [
            block_id
            for block_id in remaining
            if set(block_dependencies[block_id]).issubset(completed)
        ]
        if not ready:
            raise ValueError("evaluation matrix block dependencies contain a cycle or unknown dependency")
        ready.sort(key=lambda block_id: sha256(f"{experiment_seed}\0{block_id}".encode("utf-8")).hexdigest())
        selected = ready[0]
        ordered.append(selected)
        completed.add(selected)
        remaining.remove(selected)
    return tuple(ordered)


def _validate_synthetic_fixture(value: object) -> dict[str, object]:
    fixture = _exact_mapping(
        value,
        (
            "schema_version",
            "fixture_id",
            "fixture_kind",
            "experiment_seed",
            "selected_case_ids",
            "candidates",
        ),
        "fixture",
    )
    _require_exact_text(
        fixture["schema_version"],
        SYNTHETIC_FIXTURE_SCHEMA_VERSION,
        "fixture.schema_version",
    )
    _require_exact_text(
        fixture["fixture_id"],
        _SYNTHETIC_FIXTURE_ID,
        "fixture.fixture_id",
    )
    _require_exact_text(
        fixture["fixture_kind"],
        "synthetic_only_no_real_h1",
        "fixture.fixture_kind",
    )
    _require_exact_text(
        fixture["experiment_seed"],
        _SYNTHETIC_EXPERIMENT_SEED,
        "fixture.experiment_seed",
    )
    selected_case_ids = _sorted_unique_texts(fixture["selected_case_ids"], "fixture.selected_case_ids")

    candidate_rows = _require_list(fixture["candidates"], "fixture.candidates")
    if len(candidate_rows) < 3:
        raise ValueError("fixture.candidates must contain at least two selected cases and one duplicate probe")
    candidate_ids: list[str] = []
    normalized_candidates: list[dict[str, object]] = []
    for index, raw_candidate in enumerate(candidate_rows):
        candidate = _exact_mapping(
            raw_candidate,
            ("case_slot_id", "requirement_text", "structured_signature", "coverage_tags"),
            f"fixture.candidates[{index}]",
        )
        case_slot_id = _require_text(candidate["case_slot_id"], f"fixture.candidates[{index}].case_slot_id")
        if not case_slot_id.startswith("synthetic-"):
            raise ValueError("fixture candidate case_slot_id must remain synthetic")
        signature = _sorted_unique_texts(
            candidate["structured_signature"],
            f"fixture.candidates[{index}].structured_signature",
        )
        coverage_tags = _sorted_unique_texts(
            candidate["coverage_tags"],
            f"fixture.candidates[{index}].coverage_tags",
        )
        requirement_text = _require_text(
            candidate["requirement_text"],
            f"fixture.candidates[{index}].requirement_text",
        )
        candidate_ids.append(case_slot_id)
        normalized_candidates.append(
            {
                "case_slot_id": case_slot_id,
                "requirement_text": requirement_text,
                "structured_signature": list(signature),
                "coverage_tags": list(coverage_tags),
            }
        )
    if candidate_ids != sorted(candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("fixture.candidates must be sorted by case_slot_id and unique")
    if not set(selected_case_ids).issubset(candidate_ids):
        raise ValueError("fixture.selected_case_ids must reference fixture candidates")
    if len(selected_case_ids) < 2:
        raise ValueError("fixture.selected_case_ids must contain at least two synthetic cases")
    return {
        **fixture,
        "selected_case_ids": list(selected_case_ids),
        "candidates": normalized_candidates,
    }


def _build_duplicate_audit(fixture: Mapping[str, object]) -> dict[str, object]:
    subjects: list[dict[str, object]] = []
    normalized_by_id: dict[str, str] = {}
    signature_hash_by_id: dict[str, str] = {}
    for candidate in fixture["candidates"]:
        if not isinstance(candidate, Mapping):
            raise ValueError("normalized fixture candidate must be an object")
        subject_id = str(candidate["case_slot_id"])
        normalized_text = _normalize_exact_text(candidate["requirement_text"], f"{subject_id}.requirement_text")
        normalized_text_sha256 = _sha256_bytes(normalized_text.encode("utf-8"))
        structured_signature_sha256 = _sha256_json(candidate["structured_signature"])
        normalized_by_id[subject_id] = normalized_text_sha256
        signature_hash_by_id[subject_id] = structured_signature_sha256
        subjects.append(
            {
                "subject_id": subject_id,
                "material_class": "synthetic_fixture",
                "normalized_text_sha256": normalized_text_sha256,
                "structured_signature_sha256": structured_signature_sha256,
                "raw_text_retained": False,
                "real_h1_content": False,
                "gold_content": False,
            }
        )

    pairs: list[dict[str, object]] = []
    for left_subject_id, right_subject_id in combinations(sorted(normalized_by_id), 2):
        exact_text_match = normalized_by_id[left_subject_id] == normalized_by_id[right_subject_id]
        signature_match = signature_hash_by_id[left_subject_id] == signature_hash_by_id[right_subject_id]
        verdict = "exact_duplicate" if exact_text_match else "unadjudicated_no_threshold"
        pairs.append(
            {
                "left_subject_id": left_subject_id,
                "right_subject_id": right_subject_id,
                "exact_text_match": exact_text_match,
                "structured_signature_match": signature_match,
                "near_duplicate_score": None,
                "verdict": verdict,
                "evaluator_adjudication_performed": False,
            }
        )

    body = {
        "schema_version": PHASE5_DUPLICATE_AUDIT_SCHEMA_VERSION,
        "status": "synthetic_exact_audit_complete_near_threshold_pending",
        "input_scope": "synthetic_fixture_only",
        "exact_normalizer": "unicode_nfkc_casefold_whitespace_v1",
        "exact_digest": "sha256_utf8",
        "near_duplicate_method": "char_ngram_tfidf_plus_structured_use_case_signature_v1",
        "near_duplicate_threshold": None,
        "threshold_status": "development_calibration_required_before_real_holdout",
        "adjudication_labels": list(_ADJUDICATION_LABELS),
        "manual_adjudication_interface": {
            "status": "interface_only_no_real_subject_access",
            "entry_fields": list(_ADJUDICATION_ENTRY_FIELDS),
            "subject_content_visible": False,
            "candidate_outputs_visible": False,
            "entries": [],
        },
        "candidate_outputs_visible": False,
        "real_h1_audit_executed": False,
        "subjects": subjects,
        "pairs": pairs,
    }
    return {"audit_id": _record_id("phase5-duplicate-audit", body), **body}


def _build_manifest(
    fixture: Mapping[str, object],
    duplicate_audit: Mapping[str, object],
) -> dict[str, object]:
    selected_case_ids = tuple(fixture["selected_case_ids"])
    subjects = {
        str(subject["subject_id"]): subject
        for subject in duplicate_audit["subjects"]
        if isinstance(subject, Mapping)
    }
    exact_duplicate_pairs = {
        frozenset((str(pair["left_subject_id"]), str(pair["right_subject_id"])))
        for pair in duplicate_audit["pairs"]
        if isinstance(pair, Mapping) and pair["exact_text_match"] is True
    }
    for left_case_id, right_case_id in combinations(selected_case_ids, 2):
        if frozenset((left_case_id, right_case_id)) in exact_duplicate_pairs:
            raise ValueError("selected synthetic manifest contains an exact duplicate")

    candidate_by_id = {
        str(candidate["case_slot_id"]): candidate
        for candidate in fixture["candidates"]
        if isinstance(candidate, Mapping)
    }
    case_rows: list[dict[str, object]] = []
    for case_slot_id in selected_case_ids:
        subject = subjects[case_slot_id]
        candidate = candidate_by_id[case_slot_id]
        commitment_material = {
            "case_slot_id": case_slot_id,
            "coverage_tags": candidate["coverage_tags"],
            "normalized_text_sha256": subject["normalized_text_sha256"],
            "structured_signature_sha256": subject["structured_signature_sha256"],
        }
        case_rows.append(
            {
                "case_slot_id": case_slot_id,
                "custody_case_ref": f"synthetic://phase5/{case_slot_id}",
                "case_commitment_sha256": _sha256_json(commitment_material),
                "coverage_tags": list(candidate["coverage_tags"]),
                "annotation_state": "synthetic_fixture_not_dual_annotated",
                "duplicate_status": "exact_unique_within_selected_synthetic_fixture",
                "real_content_present": False,
                "gold_content_present": False,
                "eligible_for_real_h1": False,
            }
        )
    body = {
        "schema_version": PHASE5_HOLDOUT_MANIFEST_SCHEMA_VERSION,
        "status": "synthetic_fixture_only_not_h1",
        "case_set_kind": "synthetic_fixture",
        "core_case_target": 12,
        "reserve_case_cap": 8,
        "deep_label_case_min": 4,
        "deep_label_case_max": 6,
        "real_h1_case_count": 0,
        "real_gold_item_count": 0,
        "selected_synthetic_case_count": len(case_rows),
        "cases": case_rows,
        "sealed": False,
        "h1_opened": False,
        "used_test_set": False,
    }
    return {"manifest_id": _record_id("phase5-holdout-manifest", body), **body}


def _build_metric_contract() -> dict[str, object]:
    body = {
        "schema_version": PHASE5_METRIC_CONTRACT_SCHEMA_VERSION,
        "status": "definitions_only_not_executed",
        "evidence_control_metrics": list(_EVIDENCE_CONTROL_METRICS),
        "error_recovery_metrics": list(_ERROR_RECOVERY_METRICS),
        "functional_acceptance_metrics": list(_FUNCTIONAL_ACCEPTANCE_METRICS),
        "supporting_metrics": list(_SUPPORTING_METRICS),
        "missing_outcomes": ["fail", "not_supported", "unknown"],
        "not_applicable_excluded_only_with_reason": True,
        "not_supported_retained_in_applicable_denominator": True,
        "no_valid_candidate_attribution_coverage_zero": True,
        "deep_label_no_valid_candidate_precision_undefined_recall_zero": True,
        "case_macro_first": True,
        "candidate_output_cannot_define_gold_or_denominator": True,
        "path3_formal_h1_eligible": False,
        "path3_evidence_influence_metrics_applicable": False,
        "threshold_status": "not_frozen_requires_development_and_owner_approval",
        "formal_metrics_executed": False,
        "real_gold_consumed": False,
    }
    return {"metric_contract_id": _record_id("phase5-metric-contract", body), **body}


def _build_artifact_inventory() -> dict[str, object]:
    artifacts = [
        {
            "artifact_slot": artifact_slot,
            "expected_record_type": expected_record_type,
            "availability": "not_created_no_action",
            "artifact_id": None,
            "sha256": None,
            "byte_length": None,
        }
        for artifact_slot, expected_record_type in _ARTIFACT_SLOTS
    ]
    body = {
        "schema_version": PHASE5_ARTIFACT_INVENTORY_SCHEMA_VERSION,
        "status": "template_only_no_runtime_artifacts",
        "g0_prefreeze_reference_required": True,
        "same_case_g0_only": True,
        "raw_first_required": True,
        "generate_start_consumes_attempt": True,
        "automatic_retry_allowed": False,
        "budget_reset_allowed": False,
        "source_result_rewrite_allowed": False,
        "normalization_counts_as_raw_success": False,
        "repair_counts_as_raw_success": False,
        "replay_counts_as_raw_success": False,
        "composition_counts_as_raw_success": False,
        "fallback_counts_as_raw_success": False,
        "artifacts": artifacts,
    }
    return {"inventory_id": _record_id("phase5-artifact-inventory", body), **body}


def _build_opening_gate() -> dict[str, object]:
    conditions = {
        "phase4_exit_intake_complete": True,
        "owner_action_approval_present": False,
        "approved_d17_path_1_or_2": False,
        "provider_visible_view_license_serializer_parity_frozen": False,
        "duplicate_audit_and_manual_adjudication_complete": False,
        "dual_annotation_and_gold_commitment_complete": False,
        "manifest_and_release_candidate_frozen": False,
        "metric_thresholds_and_scripts_frozen": False,
        "matrix_order_budget_time_cost_caps_frozen": False,
        "raw_first_result_inventory_ready": False,
    }
    body = {
        "schema_version": PHASE5_OPENING_GATE_SCHEMA_VERSION,
        "status": "closed_no_action",
        "conditions": conditions,
        "h1_open_allowed": False,
        "real_h1_read_allowed": False,
        "real_gold_read_allowed": False,
        "holdout_execution_allowed": False,
        "model_action_allowed": False,
        "external_action_allowed": False,
        "training_allowed": False,
        "formal_quality_claim_allowed": False,
    }
    return {"gate_id": _record_id("phase5-h1-opening-gate", body), **body}


def _matrix_row(
    *,
    case_slot_id: str,
    group: str,
    seed: int,
    block_id: str,
    depends_on_block_ids: tuple[str, ...],
    planned_initial_model_calls: int,
) -> dict[str, object]:
    if group == "G0":
        artifact_route = "frozen_g0_v2_not_executed"
        package_schema = "req2web.result.package.v2"
        recovery_action = "not_applicable_g0"
    elif group == "G1":
        artifact_route = "evaluation_only_v1_if_future_approved"
        package_schema = "req2web.result.package.v1"
        recovery_action = "not_applicable_first_pass"
    else:
        artifact_route = "recovery_or_same_case_g0_if_future_approved"
        package_schema = "req2web.result.package.v1_or_frozen_v2"
        recovery_action = "not_executed_no_action"
    material = {
        "case_slot_id": case_slot_id,
        "group": group,
        "role": "full_guidance",
        "intervention": "none",
        "repeat_index": 0,
        "seed": seed,
        "block_id": block_id,
        "depends_on_block_ids": list(depends_on_block_ids),
        "schema_version": PHASE5_EVALUATION_MATRIX_SCHEMA_VERSION,
    }
    return {
        "row_id": _record_id("phase5-matrix-row", material),
        "case_slot_id": case_slot_id,
        "group": group,
        "role": "full_guidance",
        "intervention": "none",
        "repeat_index": 0,
        "seed": seed,
        "block_id": block_id,
        "depends_on_block_ids": list(depends_on_block_ids),
        "applicable": True,
        "pre_registered_exclusion_reason": None,
        "expected_run_count": 1,
        "actual_run_count": 0,
        "planned_initial_model_calls": planned_initial_model_calls,
        "planned_model_repair_calls": 0,
        "candidate_run_id": None,
        "recovery_action": recovery_action,
        "artifact_route": artifact_route,
        "package_schema": package_schema,
        "package_id": None,
        "package_sha256": None,
        "fallback_source_package_id": None,
        "fallback_source_package_sha256": None,
        "influence_claim": "not_evaluated_no_action",
        "d17_path": "not_selected_no_action",
        "status": "not_executed_no_action",
    }


def _build_matrix(fixture: Mapping[str, object], manifest: Mapping[str, object]) -> dict[str, object]:
    experiment_seed = str(fixture["experiment_seed"])
    rows: list[dict[str, object]] = []
    dependencies: dict[str, tuple[str, ...]] = {}
    for case in manifest["cases"]:
        if not isinstance(case, Mapping):
            raise ValueError("manifest case must be an object")
        case_slot_id = str(case["case_slot_id"])
        case_seed = _fixture_seed(case_slot_id, experiment_seed)
        g0_block_id = f"g0-{case_slot_id}"
        candidate_block_id = f"candidate-{case_slot_id}-seed-{case_seed}"
        dependencies[g0_block_id] = ()
        dependencies[candidate_block_id] = (g0_block_id,)
        rows.append(
            _matrix_row(
                case_slot_id=case_slot_id,
                group="G0",
                seed=case_seed,
                block_id=g0_block_id,
                depends_on_block_ids=(),
                planned_initial_model_calls=0,
            )
        )
        rows.append(
            _matrix_row(
                case_slot_id=case_slot_id,
                group="G1",
                seed=case_seed,
                block_id=candidate_block_id,
                depends_on_block_ids=(g0_block_id,),
                planned_initial_model_calls=len(_MODEL_NODES),
            )
        )
        rows.append(
            _matrix_row(
                case_slot_id=case_slot_id,
                group="G2",
                seed=case_seed,
                block_id=candidate_block_id,
                depends_on_block_ids=(g0_block_id,),
                planned_initial_model_calls=0,
            )
        )
    block_order = _dependency_order(dependencies, experiment_seed)
    block_position = {block_id: index for index, block_id in enumerate(block_order)}
    rows.sort(
        key=lambda row: (
            block_position[str(row["block_id"])],
            _GROUP_ORDER[str(row["group"])],
            str(row["row_id"]),
        )
    )
    body = {
        "schema_version": PHASE5_EVALUATION_MATRIX_SCHEMA_VERSION,
        "status": "synthetic_template_not_executed",
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": _sha256_json(manifest),
        "model_nodes": list(_MODEL_NODES),
        "experiment_seed": experiment_seed,
        "order_algorithm": "dependency_aware_sha256_block_order_v1",
        "block_order": list(block_order),
        "block_order_sha256": _sha256_json(list(block_order)),
        "rows": rows,
        "real_h1_rows_present": False,
        "candidate_outputs_present": False,
    }
    return {"matrix_id": _record_id("phase5-evaluation-matrix", body), **body}


def _build_execution_policy(matrix: Mapping[str, object]) -> dict[str, object]:
    initial_node_call_cap = sum(
        int(row["planned_initial_model_calls"])
        for row in matrix["rows"]
        if isinstance(row, Mapping)
    )
    budget = {
        "status": "synthetic_formula_example_only_not_approved_for_h1",
        "formula": (
            "initial_node_call_cap + candidate_model_repair_call_cap + "
            "fault_copy_model_repair_call_cap + provider_retry_call_cap"
        ),
        "initial_node_call_cap": initial_node_call_cap,
        "candidate_model_repair_call_cap": 0,
        "fault_copy_model_repair_call_cap": 0,
        "provider_retry_call_cap": 0,
        "total_model_call_cap": initial_node_call_cap,
        "calls_consumed": 0,
        "gpu_time_cap_seconds": None,
        "cost_cap_minor_units": None,
        "generate_start_consumes_call": True,
        "automatic_retry_allowed": False,
        "budget_reset_allowed": False,
        "owner_approved_for_h1": False,
        "model_action_authorized": False,
    }
    body = {
        "schema_version": PHASE5_EXECUTION_POLICY_SCHEMA_VERSION,
        "status": "no_action_policy_only",
        "matrix_id": matrix["matrix_id"],
        "matrix_sha256": _sha256_json(matrix),
        "blinding": {
            "candidate_outputs_hidden_until_manifest_frozen": True,
            "evaluator_gold_hidden_from_runtime": True,
            "block_order_frozen_before_candidate_output": True,
            "prompt_system_metrics_thresholds_frozen_before_h1": True,
            "candidate_output_used_to_select_or_promote_case": False,
        },
        "budget": budget,
        "stop_rules": list(_STOP_RULES),
        "on_incomplete_main_matrix": "incomplete_experiment_descriptive_only",
        "h1_first_open_marks_used_test_set": True,
        "resume_requires_identical_system_config_data_order_and_caps": True,
        "result_driven_changes_allowed": False,
        "real_execution_authorized": False,
    }
    return {"policy_id": _record_id("phase5-execution-policy", body), **body}


def _build_replay(
    duplicate_audit: Mapping[str, object],
    manifest: Mapping[str, object],
    metric_contract: Mapping[str, object],
    artifact_inventory: Mapping[str, object],
    matrix: Mapping[str, object],
    execution_policy: Mapping[str, object],
    opening_gate: Mapping[str, object],
) -> dict[str, object]:
    body = {
        "schema_version": PHASE5_REPLAY_SCHEMA_VERSION,
        "status": "not_executed_no_action",
        "duplicate_audit_id": duplicate_audit["audit_id"],
        "duplicate_audit_sha256": _sha256_json(duplicate_audit),
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": _sha256_json(manifest),
        "metric_contract_id": metric_contract["metric_contract_id"],
        "metric_contract_sha256": _sha256_json(metric_contract),
        "artifact_inventory_id": artifact_inventory["inventory_id"],
        "artifact_inventory_sha256": _sha256_json(artifact_inventory),
        "matrix_id": matrix["matrix_id"],
        "matrix_sha256": _sha256_json(matrix),
        "policy_id": execution_policy["policy_id"],
        "policy_sha256": _sha256_json(execution_policy),
        "opening_gate_id": opening_gate["gate_id"],
        "opening_gate_sha256": _sha256_json(opening_gate),
        "h1_opened": False,
        "h1_used_test_set": False,
        "model_invoked": False,
        "gpu_used": False,
        "external_service_used": False,
        "training_executed": False,
        "raw_result_count": 0,
        "normalization_count": 0,
        "repair_count": 0,
        "fallback_count": 0,
        "failed_closed_count": 0,
        "result_rows": [],
        "structural_preparation_only": True,
        "formal_quality_claimed": False,
        "prohibited_claims": [
            "formal_quality",
            "h1_result",
            "independent_generalization",
            "production_proof",
            "real_browser_quality",
        ],
    }
    return {"replay_id": _record_id("phase5-result-replay", body), **body}


def _validate_declaration(value: object) -> None:
    declaration = _exact_mapping(
        value,
        (
            "schema_version",
            "phase",
            "historical_alias",
            "status",
            "phase4_exit_commit",
            "phase4_exit_handoff_present",
            "phase4_exit_handoff_sha256",
            "phase4_stability_summary_file_sha256",
            "phase4_stability_summary_canonical_sha256",
            "phase4_stability_run_id",
            "real_h1_read_authorized",
            "real_gold_read_authorized",
            "holdout_selection_authorized",
            "holdout_annotation_authorized",
            "holdout_execution_authorized",
            "model_action_authorized",
            "external_action_authorized",
            "training_authorized",
            "h1_driven_system_change_authorized",
        ),
        "bundle.declaration",
    )
    _require_exact_text(
        declaration["schema_version"],
        PHASE5_DECLARATION_SCHEMA_VERSION,
        "bundle.declaration.schema_version",
    )
    _require_exact_text(declaration["phase"], "phase5", "bundle.declaration.phase")
    _require_exact_text(declaration["historical_alias"], "M4", "bundle.declaration.historical_alias")
    _require_exact_text(
        declaration["status"],
        "entered_no_action_foundation",
        "bundle.declaration.status",
    )
    _require_exact_text(
        declaration["phase4_exit_commit"],
        PHASE4_EXIT_COMMIT,
        "bundle.declaration.phase4_exit_commit",
    )
    _require_true(
        declaration["phase4_exit_handoff_present"],
        "bundle.declaration.phase4_exit_handoff_present",
    )
    if _require_sha256(
        declaration["phase4_exit_handoff_sha256"],
        "bundle.declaration.phase4_exit_handoff_sha256",
    ) != PHASE4_EXIT_HANDOFF_SHA256:
        raise ValueError("bundle.declaration.phase4_exit_handoff_sha256 drifted")
    if _require_sha256(
        declaration["phase4_stability_summary_file_sha256"],
        "bundle.declaration.phase4_stability_summary_file_sha256",
    ) != PHASE4_STABILITY_SUMMARY_FILE_SHA256:
        raise ValueError("bundle.declaration.phase4_stability_summary_file_sha256 drifted")
    if _require_sha256(
        declaration["phase4_stability_summary_canonical_sha256"],
        "bundle.declaration.phase4_stability_summary_canonical_sha256",
    ) != PHASE4_STABILITY_SUMMARY_CANONICAL_SHA256:
        raise ValueError("bundle.declaration.phase4_stability_summary_canonical_sha256 drifted")
    _require_exact_text(
        declaration["phase4_stability_run_id"],
        PHASE4_STABILITY_RUN_ID,
        "bundle.declaration.phase4_stability_run_id",
    )
    for key in (
        "real_h1_read_authorized",
        "real_gold_read_authorized",
        "holdout_selection_authorized",
        "holdout_annotation_authorized",
        "holdout_execution_authorized",
        "model_action_authorized",
        "external_action_authorized",
        "training_authorized",
        "h1_driven_system_change_authorized",
    ):
        _require_false(declaration[key], f"bundle.declaration.{key}")


def _validate_custody(value: object) -> None:
    custody = _exact_mapping(
        value,
        (
            "schema_version",
            "status",
            "custody_mode",
            "custodian_role",
            "core_case_target",
            "reserve_case_cap",
            "deep_label_case_min",
            "deep_label_case_max",
            "real_case_count",
            "real_gold_item_count",
            "content_exposed",
            "gold_exposed",
            "case_body_fields_allowed",
            "gold_fields_allowed",
            "opaque_commitments_allowed",
            "dual_annotation_required",
            "adjudication_required",
            "real_dual_annotation_complete",
            "annotation_payload_exposed",
            "read_authorized",
            "selection_authorized",
            "annotation_authorized",
            "execution_authorized",
        ),
        "bundle.custody",
    )
    _require_exact_text(
        custody["schema_version"],
        PHASE5_CUSTODY_SCHEMA_VERSION,
        "bundle.custody.schema_version",
    )
    _require_exact_text(custody["status"], "synthetic_only_no_real_h1", "bundle.custody.status")
    _require_exact_text(
        custody["custody_mode"],
        "synthetic_only_and_opaque_metadata",
        "bundle.custody.custody_mode",
    )
    _require_exact_text(
        custody["custodian_role"],
        "independent_evaluator",
        "bundle.custody.custodian_role",
    )
    if _require_int(custody["core_case_target"], "bundle.custody.core_case_target") != 12:
        raise ValueError("bundle.custody.core_case_target must remain 12")
    if _require_int(custody["reserve_case_cap"], "bundle.custody.reserve_case_cap") != 8:
        raise ValueError("bundle.custody.reserve_case_cap must remain 8")
    if _require_int(custody["deep_label_case_min"], "bundle.custody.deep_label_case_min") != 4:
        raise ValueError("bundle.custody.deep_label_case_min must remain 4")
    if _require_int(custody["deep_label_case_max"], "bundle.custody.deep_label_case_max") != 6:
        raise ValueError("bundle.custody.deep_label_case_max must remain 6")
    if _require_int(custody["real_case_count"], "bundle.custody.real_case_count") != 0:
        raise ValueError("bundle.custody.real_case_count must remain zero")
    if _require_int(custody["real_gold_item_count"], "bundle.custody.real_gold_item_count") != 0:
        raise ValueError("bundle.custody.real_gold_item_count must remain zero")
    for key in (
        "content_exposed",
        "gold_exposed",
        "case_body_fields_allowed",
        "gold_fields_allowed",
        "read_authorized",
        "selection_authorized",
        "annotation_authorized",
        "execution_authorized",
    ):
        _require_false(custody[key], f"bundle.custody.{key}")
    _require_true(custody["opaque_commitments_allowed"], "bundle.custody.opaque_commitments_allowed")
    _require_true(custody["dual_annotation_required"], "bundle.custody.dual_annotation_required")
    _require_true(custody["adjudication_required"], "bundle.custody.adjudication_required")
    _require_false(
        custody["real_dual_annotation_complete"],
        "bundle.custody.real_dual_annotation_complete",
    )
    _require_false(
        custody["annotation_payload_exposed"],
        "bundle.custody.annotation_payload_exposed",
    )


def _validate_duplicate_audit(
    value: object,
) -> tuple[dict[str, tuple[str, str]], set[frozenset[str]]]:
    audit = _exact_mapping(
        value,
        (
            "audit_id",
            "schema_version",
            "status",
            "input_scope",
            "exact_normalizer",
            "exact_digest",
            "near_duplicate_method",
            "near_duplicate_threshold",
            "threshold_status",
            "adjudication_labels",
            "manual_adjudication_interface",
            "candidate_outputs_visible",
            "real_h1_audit_executed",
            "subjects",
            "pairs",
        ),
        "bundle.duplicate_audit",
    )
    audit_id = _require_text(audit["audit_id"], "bundle.duplicate_audit.audit_id")
    body = {key: audit[key] for key in audit if key != "audit_id"}
    if audit_id != _record_id("phase5-duplicate-audit", body):
        raise ValueError("bundle.duplicate_audit.audit_id does not match canonical content")
    _require_exact_text(
        audit["schema_version"],
        PHASE5_DUPLICATE_AUDIT_SCHEMA_VERSION,
        "bundle.duplicate_audit.schema_version",
    )
    _require_exact_text(
        audit["status"],
        "synthetic_exact_audit_complete_near_threshold_pending",
        "bundle.duplicate_audit.status",
    )
    _require_exact_text(
        audit["input_scope"],
        "synthetic_fixture_only",
        "bundle.duplicate_audit.input_scope",
    )
    _require_exact_text(
        audit["exact_normalizer"],
        "unicode_nfkc_casefold_whitespace_v1",
        "bundle.duplicate_audit.exact_normalizer",
    )
    _require_exact_text(
        audit["exact_digest"],
        "sha256_utf8",
        "bundle.duplicate_audit.exact_digest",
    )
    _require_exact_text(
        audit["near_duplicate_method"],
        "char_ngram_tfidf_plus_structured_use_case_signature_v1",
        "bundle.duplicate_audit.near_duplicate_method",
    )
    if audit["near_duplicate_threshold"] is not None:
        raise ValueError("bundle.duplicate_audit.near_duplicate_threshold must remain null")
    _require_exact_text(
        audit["threshold_status"],
        "development_calibration_required_before_real_holdout",
        "bundle.duplicate_audit.threshold_status",
    )
    labels = tuple(_require_list(audit["adjudication_labels"], "bundle.duplicate_audit.adjudication_labels"))
    if labels != _ADJUDICATION_LABELS:
        raise ValueError("bundle.duplicate_audit.adjudication_labels drifted")
    adjudication = _exact_mapping(
        audit["manual_adjudication_interface"],
        (
            "status",
            "entry_fields",
            "subject_content_visible",
            "candidate_outputs_visible",
            "entries",
        ),
        "bundle.duplicate_audit.manual_adjudication_interface",
    )
    _require_exact_text(
        adjudication["status"],
        "interface_only_no_real_subject_access",
        "bundle.duplicate_audit.manual_adjudication_interface.status",
    )
    if tuple(
        _require_list(
            adjudication["entry_fields"],
            "bundle.duplicate_audit.manual_adjudication_interface.entry_fields",
        )
    ) != _ADJUDICATION_ENTRY_FIELDS:
        raise ValueError("bundle.duplicate_audit manual adjudication entry fields drifted")
    _require_false(
        adjudication["subject_content_visible"],
        "bundle.duplicate_audit.manual_adjudication_interface.subject_content_visible",
    )
    _require_false(
        adjudication["candidate_outputs_visible"],
        "bundle.duplicate_audit.manual_adjudication_interface.candidate_outputs_visible",
    )
    if _require_list(
        adjudication["entries"],
        "bundle.duplicate_audit.manual_adjudication_interface.entries",
    ):
        raise ValueError("bundle.duplicate_audit manual adjudication entries must remain empty")
    _require_false(
        audit["candidate_outputs_visible"],
        "bundle.duplicate_audit.candidate_outputs_visible",
    )
    _require_false(
        audit["real_h1_audit_executed"],
        "bundle.duplicate_audit.real_h1_audit_executed",
    )

    subject_ids: list[str] = []
    subject_fingerprints: dict[str, tuple[str, str]] = {}
    for index, raw_subject in enumerate(_require_list(audit["subjects"], "bundle.duplicate_audit.subjects")):
        subject = _exact_mapping(
            raw_subject,
            (
                "subject_id",
                "material_class",
                "normalized_text_sha256",
                "structured_signature_sha256",
                "raw_text_retained",
                "real_h1_content",
                "gold_content",
            ),
            f"bundle.duplicate_audit.subjects[{index}]",
        )
        subject_ids.append(_require_text(subject["subject_id"], f"bundle.duplicate_audit.subjects[{index}].subject_id"))
        _require_exact_text(
            subject["material_class"],
            "synthetic_fixture",
            f"bundle.duplicate_audit.subjects[{index}].material_class",
        )
        normalized_text_sha256 = _require_sha256(
            subject["normalized_text_sha256"],
            f"bundle.duplicate_audit.subjects[{index}].normalized_text_sha256",
        )
        structured_signature_sha256 = _require_sha256(
            subject["structured_signature_sha256"],
            f"bundle.duplicate_audit.subjects[{index}].structured_signature_sha256",
        )
        subject_fingerprints[subject_ids[-1]] = (
            normalized_text_sha256,
            structured_signature_sha256,
        )
        for key in ("raw_text_retained", "real_h1_content", "gold_content"):
            _require_false(subject[key], f"bundle.duplicate_audit.subjects[{index}].{key}")
    if subject_ids != sorted(subject_ids) or len(subject_ids) != len(set(subject_ids)):
        raise ValueError("bundle.duplicate_audit.subjects must be sorted and unique")

    expected_pairs = list(combinations(subject_ids, 2))
    observed_pairs: list[tuple[str, str]] = []
    exact_duplicate_pairs: set[frozenset[str]] = set()
    for index, raw_pair in enumerate(_require_list(audit["pairs"], "bundle.duplicate_audit.pairs")):
        pair = _exact_mapping(
            raw_pair,
            (
                "left_subject_id",
                "right_subject_id",
                "exact_text_match",
                "structured_signature_match",
                "near_duplicate_score",
                "verdict",
                "evaluator_adjudication_performed",
            ),
            f"bundle.duplicate_audit.pairs[{index}]",
        )
        left = _require_text(pair["left_subject_id"], f"bundle.duplicate_audit.pairs[{index}].left_subject_id")
        right = _require_text(pair["right_subject_id"], f"bundle.duplicate_audit.pairs[{index}].right_subject_id")
        if left >= right or left not in subject_ids or right not in subject_ids:
            raise ValueError("bundle.duplicate_audit pair identity/order is invalid")
        exact_match = _require_bool(
            pair["exact_text_match"],
            f"bundle.duplicate_audit.pairs[{index}].exact_text_match",
        )
        signature_match = _require_bool(
            pair["structured_signature_match"],
            f"bundle.duplicate_audit.pairs[{index}].structured_signature_match",
        )
        expected_exact_match = subject_fingerprints[left][0] == subject_fingerprints[right][0]
        expected_signature_match = subject_fingerprints[left][1] == subject_fingerprints[right][1]
        if exact_match != expected_exact_match or signature_match != expected_signature_match:
            raise ValueError("bundle.duplicate_audit pair flags do not match subject hashes")
        if pair["near_duplicate_score"] is not None:
            raise ValueError("near_duplicate_score must remain null before threshold calibration")
        expected_verdict = "exact_duplicate" if exact_match else "unadjudicated_no_threshold"
        _require_exact_text(
            pair["verdict"],
            expected_verdict,
            f"bundle.duplicate_audit.pairs[{index}].verdict",
        )
        _require_false(
            pair["evaluator_adjudication_performed"],
            f"bundle.duplicate_audit.pairs[{index}].evaluator_adjudication_performed",
        )
        observed_pairs.append((left, right))
        if exact_match:
            exact_duplicate_pairs.add(frozenset((left, right)))
    if observed_pairs != expected_pairs:
        raise ValueError("bundle.duplicate_audit.pairs must cover every canonical subject pair")
    return subject_fingerprints, exact_duplicate_pairs


def _validate_manifest(
    value: object,
    subject_fingerprints: Mapping[str, tuple[str, str]],
    exact_duplicate_pairs: set[frozenset[str]],
) -> set[str]:
    manifest = _exact_mapping(
        value,
        (
            "manifest_id",
            "schema_version",
            "status",
            "case_set_kind",
            "core_case_target",
            "reserve_case_cap",
            "deep_label_case_min",
            "deep_label_case_max",
            "real_h1_case_count",
            "real_gold_item_count",
            "selected_synthetic_case_count",
            "cases",
            "sealed",
            "h1_opened",
            "used_test_set",
        ),
        "bundle.holdout_manifest",
    )
    manifest_id = _require_text(manifest["manifest_id"], "bundle.holdout_manifest.manifest_id")
    body = {key: manifest[key] for key in manifest if key != "manifest_id"}
    if manifest_id != _record_id("phase5-holdout-manifest", body):
        raise ValueError("bundle.holdout_manifest.manifest_id does not match canonical content")
    _require_exact_text(
        manifest["schema_version"],
        PHASE5_HOLDOUT_MANIFEST_SCHEMA_VERSION,
        "bundle.holdout_manifest.schema_version",
    )
    _require_exact_text(
        manifest["status"],
        "synthetic_fixture_only_not_h1",
        "bundle.holdout_manifest.status",
    )
    _require_exact_text(
        manifest["case_set_kind"],
        "synthetic_fixture",
        "bundle.holdout_manifest.case_set_kind",
    )
    expected_counts = {
        "core_case_target": 12,
        "reserve_case_cap": 8,
        "deep_label_case_min": 4,
        "deep_label_case_max": 6,
        "real_h1_case_count": 0,
        "real_gold_item_count": 0,
    }
    for key, expected in expected_counts.items():
        if _require_int(manifest[key], f"bundle.holdout_manifest.{key}") != expected:
            raise ValueError(f"bundle.holdout_manifest.{key} must remain {expected}")
    for key in ("sealed", "h1_opened", "used_test_set"):
        _require_false(manifest[key], f"bundle.holdout_manifest.{key}")

    case_ids: list[str] = []
    cases = _require_list(manifest["cases"], "bundle.holdout_manifest.cases")
    if _require_int(
        manifest["selected_synthetic_case_count"],
        "bundle.holdout_manifest.selected_synthetic_case_count",
    ) != len(cases):
        raise ValueError("bundle.holdout_manifest selected count does not match cases")
    for index, raw_case in enumerate(cases):
        case = _exact_mapping(
            raw_case,
            (
                "case_slot_id",
                "custody_case_ref",
                "case_commitment_sha256",
                "coverage_tags",
                "annotation_state",
                "duplicate_status",
                "real_content_present",
                "gold_content_present",
                "eligible_for_real_h1",
            ),
            f"bundle.holdout_manifest.cases[{index}]",
        )
        case_slot_id = _require_text(
            case["case_slot_id"],
            f"bundle.holdout_manifest.cases[{index}].case_slot_id",
        )
        case_ids.append(case_slot_id)
        if case_slot_id not in subject_fingerprints:
            raise ValueError("bundle.holdout_manifest case is absent from duplicate audit")
        _require_exact_text(
            case["custody_case_ref"],
            f"synthetic://phase5/{case_slot_id}",
            f"bundle.holdout_manifest.cases[{index}].custody_case_ref",
        )
        case_commitment_sha256 = _require_sha256(
            case["case_commitment_sha256"],
            f"bundle.holdout_manifest.cases[{index}].case_commitment_sha256",
        )
        coverage_tags = _sorted_unique_texts(
            case["coverage_tags"],
            f"bundle.holdout_manifest.cases[{index}].coverage_tags",
        )
        expected_commitment = _sha256_json(
            {
                "case_slot_id": case_slot_id,
                "coverage_tags": list(coverage_tags),
                "normalized_text_sha256": subject_fingerprints[case_slot_id][0],
                "structured_signature_sha256": subject_fingerprints[case_slot_id][1],
            }
        )
        if case_commitment_sha256 != expected_commitment:
            raise ValueError("bundle.holdout_manifest case commitment binding failed")
        _require_exact_text(
            case["annotation_state"],
            "synthetic_fixture_not_dual_annotated",
            f"bundle.holdout_manifest.cases[{index}].annotation_state",
        )
        _require_exact_text(
            case["duplicate_status"],
            "exact_unique_within_selected_synthetic_fixture",
            f"bundle.holdout_manifest.cases[{index}].duplicate_status",
        )
        for key in ("real_content_present", "gold_content_present", "eligible_for_real_h1"):
            _require_false(case[key], f"bundle.holdout_manifest.cases[{index}].{key}")
    if case_ids != sorted(case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("bundle.holdout_manifest.cases must be sorted and unique")
    for left_case_id, right_case_id in combinations(case_ids, 2):
        if frozenset((left_case_id, right_case_id)) in exact_duplicate_pairs:
            raise ValueError("bundle.holdout_manifest selected cases contain an exact duplicate")
    return set(case_ids)


def _validate_metric_contract(value: object) -> None:
    contract = _exact_mapping(
        value,
        (
            "metric_contract_id",
            "schema_version",
            "status",
            "evidence_control_metrics",
            "error_recovery_metrics",
            "functional_acceptance_metrics",
            "supporting_metrics",
            "missing_outcomes",
            "not_applicable_excluded_only_with_reason",
            "not_supported_retained_in_applicable_denominator",
            "no_valid_candidate_attribution_coverage_zero",
            "deep_label_no_valid_candidate_precision_undefined_recall_zero",
            "case_macro_first",
            "candidate_output_cannot_define_gold_or_denominator",
            "path3_formal_h1_eligible",
            "path3_evidence_influence_metrics_applicable",
            "threshold_status",
            "formal_metrics_executed",
            "real_gold_consumed",
        ),
        "bundle.metric_contract",
    )
    contract_id = _require_text(
        contract["metric_contract_id"],
        "bundle.metric_contract.metric_contract_id",
    )
    body = {key: contract[key] for key in contract if key != "metric_contract_id"}
    if contract_id != _record_id("phase5-metric-contract", body):
        raise ValueError("bundle.metric_contract.metric_contract_id does not match canonical content")
    _require_exact_text(
        contract["schema_version"],
        PHASE5_METRIC_CONTRACT_SCHEMA_VERSION,
        "bundle.metric_contract.schema_version",
    )
    _require_exact_text(
        contract["status"],
        "definitions_only_not_executed",
        "bundle.metric_contract.status",
    )
    expected_metric_lists = (
        ("evidence_control_metrics", _EVIDENCE_CONTROL_METRICS),
        ("error_recovery_metrics", _ERROR_RECOVERY_METRICS),
        ("functional_acceptance_metrics", _FUNCTIONAL_ACCEPTANCE_METRICS),
        ("supporting_metrics", _SUPPORTING_METRICS),
    )
    for key, expected in expected_metric_lists:
        if tuple(_require_list(contract[key], f"bundle.metric_contract.{key}")) != expected:
            raise ValueError(f"bundle.metric_contract.{key} drifted")
    if tuple(_require_list(contract["missing_outcomes"], "bundle.metric_contract.missing_outcomes")) != (
        "fail",
        "not_supported",
        "unknown",
    ):
        raise ValueError("bundle.metric_contract.missing_outcomes drifted")
    for key in (
        "not_applicable_excluded_only_with_reason",
        "not_supported_retained_in_applicable_denominator",
        "no_valid_candidate_attribution_coverage_zero",
        "deep_label_no_valid_candidate_precision_undefined_recall_zero",
        "case_macro_first",
        "candidate_output_cannot_define_gold_or_denominator",
    ):
        _require_true(contract[key], f"bundle.metric_contract.{key}")
    for key in (
        "path3_formal_h1_eligible",
        "path3_evidence_influence_metrics_applicable",
        "formal_metrics_executed",
        "real_gold_consumed",
    ):
        _require_false(contract[key], f"bundle.metric_contract.{key}")
    _require_exact_text(
        contract["threshold_status"],
        "not_frozen_requires_development_and_owner_approval",
        "bundle.metric_contract.threshold_status",
    )


def _validate_artifact_inventory(value: object) -> None:
    inventory = _exact_mapping(
        value,
        (
            "inventory_id",
            "schema_version",
            "status",
            "g0_prefreeze_reference_required",
            "same_case_g0_only",
            "raw_first_required",
            "generate_start_consumes_attempt",
            "automatic_retry_allowed",
            "budget_reset_allowed",
            "source_result_rewrite_allowed",
            "normalization_counts_as_raw_success",
            "repair_counts_as_raw_success",
            "replay_counts_as_raw_success",
            "composition_counts_as_raw_success",
            "fallback_counts_as_raw_success",
            "artifacts",
        ),
        "bundle.artifact_inventory",
    )
    inventory_id = _require_text(
        inventory["inventory_id"],
        "bundle.artifact_inventory.inventory_id",
    )
    body = {key: inventory[key] for key in inventory if key != "inventory_id"}
    if inventory_id != _record_id("phase5-artifact-inventory", body):
        raise ValueError("bundle.artifact_inventory.inventory_id does not match canonical content")
    _require_exact_text(
        inventory["schema_version"],
        PHASE5_ARTIFACT_INVENTORY_SCHEMA_VERSION,
        "bundle.artifact_inventory.schema_version",
    )
    _require_exact_text(
        inventory["status"],
        "template_only_no_runtime_artifacts",
        "bundle.artifact_inventory.status",
    )
    for key in (
        "g0_prefreeze_reference_required",
        "same_case_g0_only",
        "raw_first_required",
        "generate_start_consumes_attempt",
    ):
        _require_true(inventory[key], f"bundle.artifact_inventory.{key}")
    for key in (
        "automatic_retry_allowed",
        "budget_reset_allowed",
        "source_result_rewrite_allowed",
        "normalization_counts_as_raw_success",
        "repair_counts_as_raw_success",
        "replay_counts_as_raw_success",
        "composition_counts_as_raw_success",
        "fallback_counts_as_raw_success",
    ):
        _require_false(inventory[key], f"bundle.artifact_inventory.{key}")

    artifacts = _require_list(inventory["artifacts"], "bundle.artifact_inventory.artifacts")
    if len(artifacts) != len(_ARTIFACT_SLOTS):
        raise ValueError("bundle.artifact_inventory.artifacts count drifted")
    for index, (raw_artifact, expected) in enumerate(zip(artifacts, _ARTIFACT_SLOTS, strict=True)):
        artifact = _exact_mapping(
            raw_artifact,
            (
                "artifact_slot",
                "expected_record_type",
                "availability",
                "artifact_id",
                "sha256",
                "byte_length",
            ),
            f"bundle.artifact_inventory.artifacts[{index}]",
        )
        _require_exact_text(
            artifact["artifact_slot"],
            expected[0],
            f"bundle.artifact_inventory.artifacts[{index}].artifact_slot",
        )
        _require_exact_text(
            artifact["expected_record_type"],
            expected[1],
            f"bundle.artifact_inventory.artifacts[{index}].expected_record_type",
        )
        _require_exact_text(
            artifact["availability"],
            "not_created_no_action",
            f"bundle.artifact_inventory.artifacts[{index}].availability",
        )
        if artifact["artifact_id"] is not None or artifact["sha256"] is not None or artifact["byte_length"] is not None:
            raise ValueError("bundle.artifact_inventory runtime artifact fields must remain null")


def _validate_opening_gate(value: object) -> None:
    gate = _exact_mapping(
        value,
        (
            "gate_id",
            "schema_version",
            "status",
            "conditions",
            "h1_open_allowed",
            "real_h1_read_allowed",
            "real_gold_read_allowed",
            "holdout_execution_allowed",
            "model_action_allowed",
            "external_action_allowed",
            "training_allowed",
            "formal_quality_claim_allowed",
        ),
        "bundle.opening_gate",
    )
    gate_id = _require_text(gate["gate_id"], "bundle.opening_gate.gate_id")
    body = {key: gate[key] for key in gate if key != "gate_id"}
    if gate_id != _record_id("phase5-h1-opening-gate", body):
        raise ValueError("bundle.opening_gate.gate_id does not match canonical content")
    _require_exact_text(
        gate["schema_version"],
        PHASE5_OPENING_GATE_SCHEMA_VERSION,
        "bundle.opening_gate.schema_version",
    )
    _require_exact_text(gate["status"], "closed_no_action", "bundle.opening_gate.status")
    conditions = _exact_mapping(
        gate["conditions"],
        (
            "phase4_exit_intake_complete",
            "owner_action_approval_present",
            "approved_d17_path_1_or_2",
            "provider_visible_view_license_serializer_parity_frozen",
            "duplicate_audit_and_manual_adjudication_complete",
            "dual_annotation_and_gold_commitment_complete",
            "manifest_and_release_candidate_frozen",
            "metric_thresholds_and_scripts_frozen",
            "matrix_order_budget_time_cost_caps_frozen",
            "raw_first_result_inventory_ready",
        ),
        "bundle.opening_gate.conditions",
    )
    _require_true(
        conditions["phase4_exit_intake_complete"],
        "bundle.opening_gate.conditions.phase4_exit_intake_complete",
    )
    for key in (
        "owner_action_approval_present",
        "approved_d17_path_1_or_2",
        "provider_visible_view_license_serializer_parity_frozen",
        "duplicate_audit_and_manual_adjudication_complete",
        "dual_annotation_and_gold_commitment_complete",
        "manifest_and_release_candidate_frozen",
        "metric_thresholds_and_scripts_frozen",
        "matrix_order_budget_time_cost_caps_frozen",
        "raw_first_result_inventory_ready",
    ):
        _require_false(conditions[key], f"bundle.opening_gate.conditions.{key}")
    for key in (
        "h1_open_allowed",
        "real_h1_read_allowed",
        "real_gold_read_allowed",
        "holdout_execution_allowed",
        "model_action_allowed",
        "external_action_allowed",
        "training_allowed",
        "formal_quality_claim_allowed",
    ):
        _require_false(gate[key], f"bundle.opening_gate.{key}")


def _validate_matrix(value: object, manifest: Mapping[str, object], case_ids: set[str]) -> None:
    matrix = _exact_mapping(
        value,
        (
            "matrix_id",
            "schema_version",
            "status",
            "manifest_id",
            "manifest_sha256",
            "model_nodes",
            "experiment_seed",
            "order_algorithm",
            "block_order",
            "block_order_sha256",
            "rows",
            "real_h1_rows_present",
            "candidate_outputs_present",
        ),
        "bundle.evaluation_matrix",
    )
    matrix_id = _require_text(matrix["matrix_id"], "bundle.evaluation_matrix.matrix_id")
    body = {key: matrix[key] for key in matrix if key != "matrix_id"}
    if matrix_id != _record_id("phase5-evaluation-matrix", body):
        raise ValueError("bundle.evaluation_matrix.matrix_id does not match canonical content")
    _require_exact_text(
        matrix["schema_version"],
        PHASE5_EVALUATION_MATRIX_SCHEMA_VERSION,
        "bundle.evaluation_matrix.schema_version",
    )
    _require_exact_text(
        matrix["status"],
        "synthetic_template_not_executed",
        "bundle.evaluation_matrix.status",
    )
    if matrix["manifest_id"] != manifest["manifest_id"]:
        raise ValueError("bundle.evaluation_matrix manifest id binding failed")
    if _require_sha256(
        matrix["manifest_sha256"],
        "bundle.evaluation_matrix.manifest_sha256",
    ) != _sha256_json(manifest):
        raise ValueError("bundle.evaluation_matrix manifest hash binding failed")
    if tuple(_require_list(matrix["model_nodes"], "bundle.evaluation_matrix.model_nodes")) != _MODEL_NODES:
        raise ValueError("bundle.evaluation_matrix.model_nodes drifted")
    experiment_seed = _require_text(matrix["experiment_seed"], "bundle.evaluation_matrix.experiment_seed")
    _require_exact_text(
        matrix["order_algorithm"],
        "dependency_aware_sha256_block_order_v1",
        "bundle.evaluation_matrix.order_algorithm",
    )
    _require_false(
        matrix["real_h1_rows_present"],
        "bundle.evaluation_matrix.real_h1_rows_present",
    )
    _require_false(
        matrix["candidate_outputs_present"],
        "bundle.evaluation_matrix.candidate_outputs_present",
    )

    rows = _require_list(matrix["rows"], "bundle.evaluation_matrix.rows")
    row_ids: set[str] = set()
    block_dependencies: dict[str, tuple[str, ...]] = {}
    groups_by_case: dict[str, list[str]] = {case_id: [] for case_id in case_ids}
    candidate_blocks: dict[tuple[str, int], str] = {}
    observed_rows: list[dict[str, object]] = []
    for index, raw_row in enumerate(rows):
        row = _exact_mapping(
            raw_row,
            (
                "row_id",
                "case_slot_id",
                "group",
                "role",
                "intervention",
                "repeat_index",
                "seed",
                "block_id",
                "depends_on_block_ids",
                "applicable",
                "pre_registered_exclusion_reason",
                "expected_run_count",
                "actual_run_count",
                "planned_initial_model_calls",
                "planned_model_repair_calls",
                "candidate_run_id",
                "recovery_action",
                "artifact_route",
                "package_schema",
                "package_id",
                "package_sha256",
                "fallback_source_package_id",
                "fallback_source_package_sha256",
                "influence_claim",
                "d17_path",
                "status",
            ),
            f"bundle.evaluation_matrix.rows[{index}]",
        )
        case_slot_id = _require_text(row["case_slot_id"], f"matrix.rows[{index}].case_slot_id")
        if case_slot_id not in case_ids:
            raise ValueError("matrix row references unknown case")
        group = _require_text(row["group"], f"matrix.rows[{index}].group")
        if group not in _GROUP_ORDER:
            raise ValueError("matrix row group is unsupported")
        groups_by_case[case_slot_id].append(group)
        _require_exact_text(row["role"], "full_guidance", f"matrix.rows[{index}].role")
        _require_exact_text(row["intervention"], "none", f"matrix.rows[{index}].intervention")
        if _require_int(row["repeat_index"], f"matrix.rows[{index}].repeat_index") != 0:
            raise ValueError("synthetic matrix repeat_index must remain zero")
        seed = _require_int(row["seed"], f"matrix.rows[{index}].seed")
        if seed != _fixture_seed(case_slot_id, experiment_seed):
            raise ValueError("matrix row seed does not match the frozen synthetic seed rule")
        block_id = _require_text(row["block_id"], f"matrix.rows[{index}].block_id")
        expected_block_id = (
            f"g0-{case_slot_id}"
            if group == "G0"
            else f"candidate-{case_slot_id}-seed-{seed}"
        )
        if block_id != expected_block_id:
            raise ValueError("matrix row block_id does not match case/group/seed")
        dependencies = _sorted_unique_texts(
            row["depends_on_block_ids"],
            f"matrix.rows[{index}].depends_on_block_ids",
            allow_empty=True,
        )
        existing_dependencies = block_dependencies.setdefault(block_id, dependencies)
        if existing_dependencies != dependencies:
            raise ValueError("matrix rows sharing a block must share dependencies")
        _require_true(row["applicable"], f"matrix.rows[{index}].applicable")
        if row["pre_registered_exclusion_reason"] is not None:
            raise ValueError("synthetic applicable rows must not have an exclusion reason")
        if _require_int(row["expected_run_count"], f"matrix.rows[{index}].expected_run_count") != 1:
            raise ValueError("synthetic expected_run_count must remain one")
        if _require_int(row["actual_run_count"], f"matrix.rows[{index}].actual_run_count") != 0:
            raise ValueError("matrix actual_run_count must remain zero")
        planned_calls = _require_int(
            row["planned_initial_model_calls"],
            f"matrix.rows[{index}].planned_initial_model_calls",
        )
        expected_calls = len(_MODEL_NODES) if group == "G1" else 0
        if planned_calls != expected_calls:
            raise ValueError("matrix planned initial model calls do not match the no-action formula")
        if _require_int(
            row["planned_model_repair_calls"],
            f"matrix.rows[{index}].planned_model_repair_calls",
        ) != 0:
            raise ValueError("matrix planned model repair calls must remain zero")
        if row["candidate_run_id"] is not None:
            raise ValueError("matrix candidate_run_id must remain null")
        expected_route_fields = {
            "G0": (
                "not_applicable_g0",
                "frozen_g0_v2_not_executed",
                "req2web.result.package.v2",
            ),
            "G1": (
                "not_applicable_first_pass",
                "evaluation_only_v1_if_future_approved",
                "req2web.result.package.v1",
            ),
            "G2": (
                "not_executed_no_action",
                "recovery_or_same_case_g0_if_future_approved",
                "req2web.result.package.v1_or_frozen_v2",
            ),
        }[group]
        _require_exact_text(
            row["recovery_action"],
            expected_route_fields[0],
            f"matrix.rows[{index}].recovery_action",
        )
        _require_exact_text(
            row["artifact_route"],
            expected_route_fields[1],
            f"matrix.rows[{index}].artifact_route",
        )
        _require_exact_text(
            row["package_schema"],
            expected_route_fields[2],
            f"matrix.rows[{index}].package_schema",
        )
        _require_optional_text(row["package_id"], f"matrix.rows[{index}].package_id")
        _require_optional_sha256(row["package_sha256"], f"matrix.rows[{index}].package_sha256")
        _require_optional_text(
            row["fallback_source_package_id"],
            f"matrix.rows[{index}].fallback_source_package_id",
        )
        _require_optional_sha256(
            row["fallback_source_package_sha256"],
            f"matrix.rows[{index}].fallback_source_package_sha256",
        )
        if any(
            row[key] is not None
            for key in (
                "package_id",
                "package_sha256",
                "fallback_source_package_id",
                "fallback_source_package_sha256",
            )
        ):
            raise ValueError("matrix package/result bindings must remain absent")
        _require_exact_text(
            row["influence_claim"],
            "not_evaluated_no_action",
            f"matrix.rows[{index}].influence_claim",
        )
        _require_exact_text(
            row["d17_path"],
            "not_selected_no_action",
            f"matrix.rows[{index}].d17_path",
        )
        _require_exact_text(
            row["status"],
            "not_executed_no_action",
            f"matrix.rows[{index}].status",
        )
        material = {
            "case_slot_id": case_slot_id,
            "group": group,
            "role": row["role"],
            "intervention": row["intervention"],
            "repeat_index": row["repeat_index"],
            "seed": seed,
            "block_id": block_id,
            "depends_on_block_ids": list(dependencies),
            "schema_version": PHASE5_EVALUATION_MATRIX_SCHEMA_VERSION,
        }
        row_id = _require_text(row["row_id"], f"matrix.rows[{index}].row_id")
        if row_id != _record_id("phase5-matrix-row", material) or row_id in row_ids:
            raise ValueError("matrix row id is invalid or duplicated")
        row_ids.add(row_id)
        if group in {"G1", "G2"}:
            key = (case_slot_id, seed)
            prior = candidate_blocks.setdefault(key, block_id)
            if prior != block_id:
                raise ValueError("G1/G2 pair must share one indivisible block")
        observed_rows.append(row)

    if any(sorted(groups) != ["G0", "G1", "G2"] for groups in groups_by_case.values()):
        raise ValueError("each synthetic case must contain exactly one G0, G1, and G2 row")
    block_ids = set(block_dependencies)
    for block_id, dependencies in block_dependencies.items():
        if any(dependency not in block_ids or dependency == block_id for dependency in dependencies):
            raise ValueError("matrix block dependency is invalid")
    for case_id in case_ids:
        g0_blocks = {
            str(row["block_id"])
            for row in observed_rows
            if row["case_slot_id"] == case_id and row["group"] == "G0"
        }
        candidate_rows = [
            row
            for row in observed_rows
            if row["case_slot_id"] == case_id and row["group"] in {"G1", "G2"}
        ]
        if len(g0_blocks) != 1 or any(tuple(row["depends_on_block_ids"]) != tuple(g0_blocks) for row in candidate_rows):
            raise ValueError("candidate block must depend on the same-case G0 block")

    expected_order = _dependency_order(block_dependencies, experiment_seed)
    block_order = tuple(_require_list(matrix["block_order"], "bundle.evaluation_matrix.block_order"))
    if block_order != expected_order:
        raise ValueError("bundle.evaluation_matrix.block_order is not the canonical dependency-aware order")
    if _require_sha256(
        matrix["block_order_sha256"],
        "bundle.evaluation_matrix.block_order_sha256",
    ) != _sha256_json(list(expected_order)):
        raise ValueError("bundle.evaluation_matrix.block_order_sha256 is invalid")
    block_position = {block_id: index for index, block_id in enumerate(expected_order)}
    expected_rows = sorted(
        observed_rows,
        key=lambda row: (
            block_position[str(row["block_id"])],
            _GROUP_ORDER[str(row["group"])],
            str(row["row_id"]),
        ),
    )
    if observed_rows != expected_rows:
        raise ValueError("bundle.evaluation_matrix.rows are not in canonical block/group order")


def _validate_execution_policy(value: object, matrix: Mapping[str, object]) -> None:
    policy = _exact_mapping(
        value,
        (
            "policy_id",
            "schema_version",
            "status",
            "matrix_id",
            "matrix_sha256",
            "blinding",
            "budget",
            "stop_rules",
            "on_incomplete_main_matrix",
            "h1_first_open_marks_used_test_set",
            "resume_requires_identical_system_config_data_order_and_caps",
            "result_driven_changes_allowed",
            "real_execution_authorized",
        ),
        "bundle.execution_policy",
    )
    policy_id = _require_text(policy["policy_id"], "bundle.execution_policy.policy_id")
    body = {key: policy[key] for key in policy if key != "policy_id"}
    if policy_id != _record_id("phase5-execution-policy", body):
        raise ValueError("bundle.execution_policy.policy_id does not match canonical content")
    _require_exact_text(
        policy["schema_version"],
        PHASE5_EXECUTION_POLICY_SCHEMA_VERSION,
        "bundle.execution_policy.schema_version",
    )
    _require_exact_text(policy["status"], "no_action_policy_only", "bundle.execution_policy.status")
    if policy["matrix_id"] != matrix["matrix_id"]:
        raise ValueError("bundle.execution_policy matrix id binding failed")
    if _require_sha256(
        policy["matrix_sha256"],
        "bundle.execution_policy.matrix_sha256",
    ) != _sha256_json(matrix):
        raise ValueError("bundle.execution_policy matrix hash binding failed")

    blinding = _exact_mapping(
        policy["blinding"],
        (
            "candidate_outputs_hidden_until_manifest_frozen",
            "evaluator_gold_hidden_from_runtime",
            "block_order_frozen_before_candidate_output",
            "prompt_system_metrics_thresholds_frozen_before_h1",
            "candidate_output_used_to_select_or_promote_case",
        ),
        "bundle.execution_policy.blinding",
    )
    for key in (
        "candidate_outputs_hidden_until_manifest_frozen",
        "evaluator_gold_hidden_from_runtime",
        "block_order_frozen_before_candidate_output",
        "prompt_system_metrics_thresholds_frozen_before_h1",
    ):
        _require_true(blinding[key], f"bundle.execution_policy.blinding.{key}")
    _require_false(
        blinding["candidate_output_used_to_select_or_promote_case"],
        "bundle.execution_policy.blinding.candidate_output_used_to_select_or_promote_case",
    )

    budget = _exact_mapping(
        policy["budget"],
        (
            "status",
            "formula",
            "initial_node_call_cap",
            "candidate_model_repair_call_cap",
            "fault_copy_model_repair_call_cap",
            "provider_retry_call_cap",
            "total_model_call_cap",
            "calls_consumed",
            "gpu_time_cap_seconds",
            "cost_cap_minor_units",
            "generate_start_consumes_call",
            "automatic_retry_allowed",
            "budget_reset_allowed",
            "owner_approved_for_h1",
            "model_action_authorized",
        ),
        "bundle.execution_policy.budget",
    )
    _require_exact_text(
        budget["status"],
        "synthetic_formula_example_only_not_approved_for_h1",
        "bundle.execution_policy.budget.status",
    )
    _require_exact_text(
        budget["formula"],
        (
            "initial_node_call_cap + candidate_model_repair_call_cap + "
            "fault_copy_model_repair_call_cap + provider_retry_call_cap"
        ),
        "bundle.execution_policy.budget.formula",
    )
    expected_initial = sum(
        int(row["planned_initial_model_calls"])
        for row in matrix["rows"]
        if isinstance(row, Mapping)
    )
    initial = _require_int(
        budget["initial_node_call_cap"],
        "bundle.execution_policy.budget.initial_node_call_cap",
    )
    if initial != expected_initial:
        raise ValueError("bundle.execution_policy.budget initial cap does not match matrix")
    zero_caps = (
        "candidate_model_repair_call_cap",
        "fault_copy_model_repair_call_cap",
        "provider_retry_call_cap",
        "calls_consumed",
    )
    for key in zero_caps:
        if _require_int(budget[key], f"bundle.execution_policy.budget.{key}") != 0:
            raise ValueError(f"bundle.execution_policy.budget.{key} must remain zero")
    if _require_int(
        budget["total_model_call_cap"],
        "bundle.execution_policy.budget.total_model_call_cap",
    ) != initial:
        raise ValueError("bundle.execution_policy.budget total cap does not match formula")
    if budget["gpu_time_cap_seconds"] is not None or budget["cost_cap_minor_units"] is not None:
        raise ValueError("GPU time and cost caps must remain unset in the no-action bundle")
    _require_true(
        budget["generate_start_consumes_call"],
        "bundle.execution_policy.budget.generate_start_consumes_call",
    )
    for key in (
        "automatic_retry_allowed",
        "budget_reset_allowed",
        "owner_approved_for_h1",
        "model_action_authorized",
    ):
        _require_false(budget[key], f"bundle.execution_policy.budget.{key}")

    if tuple(_require_list(policy["stop_rules"], "bundle.execution_policy.stop_rules")) != _STOP_RULES:
        raise ValueError("bundle.execution_policy.stop_rules drifted")
    _require_exact_text(
        policy["on_incomplete_main_matrix"],
        "incomplete_experiment_descriptive_only",
        "bundle.execution_policy.on_incomplete_main_matrix",
    )
    _require_true(
        policy["h1_first_open_marks_used_test_set"],
        "bundle.execution_policy.h1_first_open_marks_used_test_set",
    )
    _require_true(
        policy["resume_requires_identical_system_config_data_order_and_caps"],
        "bundle.execution_policy.resume_requires_identical_system_config_data_order_and_caps",
    )
    _require_false(
        policy["result_driven_changes_allowed"],
        "bundle.execution_policy.result_driven_changes_allowed",
    )
    _require_false(
        policy["real_execution_authorized"],
        "bundle.execution_policy.real_execution_authorized",
    )


def _validate_replay(
    value: object,
    duplicate_audit: Mapping[str, object],
    manifest: Mapping[str, object],
    metric_contract: Mapping[str, object],
    artifact_inventory: Mapping[str, object],
    matrix: Mapping[str, object],
    execution_policy: Mapping[str, object],
    opening_gate: Mapping[str, object],
) -> None:
    replay = _exact_mapping(
        value,
        (
            "replay_id",
            "schema_version",
            "status",
            "duplicate_audit_id",
            "duplicate_audit_sha256",
            "manifest_id",
            "manifest_sha256",
            "metric_contract_id",
            "metric_contract_sha256",
            "artifact_inventory_id",
            "artifact_inventory_sha256",
            "matrix_id",
            "matrix_sha256",
            "policy_id",
            "policy_sha256",
            "opening_gate_id",
            "opening_gate_sha256",
            "h1_opened",
            "h1_used_test_set",
            "model_invoked",
            "gpu_used",
            "external_service_used",
            "training_executed",
            "raw_result_count",
            "normalization_count",
            "repair_count",
            "fallback_count",
            "failed_closed_count",
            "result_rows",
            "structural_preparation_only",
            "formal_quality_claimed",
            "prohibited_claims",
        ),
        "bundle.replay_skeleton",
    )
    replay_id = _require_text(replay["replay_id"], "bundle.replay_skeleton.replay_id")
    body = {key: replay[key] for key in replay if key != "replay_id"}
    if replay_id != _record_id("phase5-result-replay", body):
        raise ValueError("bundle.replay_skeleton.replay_id does not match canonical content")
    _require_exact_text(
        replay["schema_version"],
        PHASE5_REPLAY_SCHEMA_VERSION,
        "bundle.replay_skeleton.schema_version",
    )
    _require_exact_text(
        replay["status"],
        "not_executed_no_action",
        "bundle.replay_skeleton.status",
    )
    bindings = (
        ("duplicate_audit", duplicate_audit, "audit_id"),
        ("manifest", manifest, "manifest_id"),
        ("metric_contract", metric_contract, "metric_contract_id"),
        ("artifact_inventory", artifact_inventory, "inventory_id"),
        ("matrix", matrix, "matrix_id"),
        ("policy", execution_policy, "policy_id"),
        ("opening_gate", opening_gate, "gate_id"),
    )
    for prefix, target, id_key in bindings:
        if replay[f"{prefix}_id"] != target[id_key]:
            raise ValueError(f"bundle.replay_skeleton {prefix} id binding failed")
        if _require_sha256(
            replay[f"{prefix}_sha256"],
            f"bundle.replay_skeleton.{prefix}_sha256",
        ) != _sha256_json(target):
            raise ValueError(f"bundle.replay_skeleton {prefix} hash binding failed")
    for key in (
        "h1_opened",
        "h1_used_test_set",
        "model_invoked",
        "gpu_used",
        "external_service_used",
        "training_executed",
        "formal_quality_claimed",
    ):
        _require_false(replay[key], f"bundle.replay_skeleton.{key}")
    for key in (
        "raw_result_count",
        "normalization_count",
        "repair_count",
        "fallback_count",
        "failed_closed_count",
    ):
        if _require_int(replay[key], f"bundle.replay_skeleton.{key}") != 0:
            raise ValueError(f"bundle.replay_skeleton.{key} must remain zero")
    if _require_list(replay["result_rows"], "bundle.replay_skeleton.result_rows"):
        raise ValueError("bundle.replay_skeleton.result_rows must remain empty")
    _require_true(
        replay["structural_preparation_only"],
        "bundle.replay_skeleton.structural_preparation_only",
    )
    prohibited_claims = tuple(
        _require_list(replay["prohibited_claims"], "bundle.replay_skeleton.prohibited_claims")
    )
    if prohibited_claims != (
        "formal_quality",
        "h1_result",
        "independent_generalization",
        "production_proof",
        "real_browser_quality",
    ):
        raise ValueError("bundle.replay_skeleton.prohibited_claims drifted")


def _validate_bundle_payload(value: object) -> dict[str, object]:
    bundle = _exact_mapping(
        value,
        (
            "schema_version",
            "bundle_id",
            "status",
            "declaration",
            "custody",
            "duplicate_audit",
            "holdout_manifest",
            "metric_contract",
            "artifact_inventory",
            "evaluation_matrix",
            "execution_policy",
            "opening_gate",
            "replay_skeleton",
        ),
        "bundle",
    )
    _require_exact_text(
        bundle["schema_version"],
        PHASE5_NO_ACTION_BUNDLE_SCHEMA_VERSION,
        "bundle.schema_version",
    )
    _require_exact_text(
        bundle["status"],
        "phase5_no_action_structural_preparation_only",
        "bundle.status",
    )
    bundle_id = _require_text(bundle["bundle_id"], "bundle.bundle_id")
    body = {key: bundle[key] for key in bundle if key != "bundle_id"}
    if bundle_id != _record_id("phase5-no-action-bundle", body):
        raise ValueError("bundle.bundle_id does not match canonical content")

    _validate_declaration(bundle["declaration"])
    _validate_custody(bundle["custody"])
    duplicate_audit = _exact_mapping(
        bundle["duplicate_audit"],
        tuple(bundle["duplicate_audit"].keys()) if isinstance(bundle["duplicate_audit"], Mapping) else (),
        "bundle.duplicate_audit",
    )
    subject_fingerprints, exact_duplicate_pairs = _validate_duplicate_audit(
        duplicate_audit
    )
    manifest = _exact_mapping(
        bundle["holdout_manifest"],
        tuple(bundle["holdout_manifest"].keys()) if isinstance(bundle["holdout_manifest"], Mapping) else (),
        "bundle.holdout_manifest",
    )
    case_ids = _validate_manifest(
        manifest,
        subject_fingerprints,
        exact_duplicate_pairs,
    )
    metric_contract = _exact_mapping(
        bundle["metric_contract"],
        tuple(bundle["metric_contract"].keys()) if isinstance(bundle["metric_contract"], Mapping) else (),
        "bundle.metric_contract",
    )
    _validate_metric_contract(metric_contract)
    artifact_inventory = _exact_mapping(
        bundle["artifact_inventory"],
        tuple(bundle["artifact_inventory"].keys()) if isinstance(bundle["artifact_inventory"], Mapping) else (),
        "bundle.artifact_inventory",
    )
    _validate_artifact_inventory(artifact_inventory)
    matrix = _exact_mapping(
        bundle["evaluation_matrix"],
        tuple(bundle["evaluation_matrix"].keys()) if isinstance(bundle["evaluation_matrix"], Mapping) else (),
        "bundle.evaluation_matrix",
    )
    _validate_matrix(matrix, manifest, case_ids)
    execution_policy = _exact_mapping(
        bundle["execution_policy"],
        tuple(bundle["execution_policy"].keys()) if isinstance(bundle["execution_policy"], Mapping) else (),
        "bundle.execution_policy",
    )
    _validate_execution_policy(execution_policy, matrix)
    opening_gate = _exact_mapping(
        bundle["opening_gate"],
        tuple(bundle["opening_gate"].keys()) if isinstance(bundle["opening_gate"], Mapping) else (),
        "bundle.opening_gate",
    )
    _validate_opening_gate(opening_gate)
    _validate_replay(
        bundle["replay_skeleton"],
        duplicate_audit,
        manifest,
        metric_contract,
        artifact_inventory,
        matrix,
        execution_policy,
        opening_gate,
    )
    return bundle


@dataclass(frozen=True)
class Phase5NoActionBundle:
    """Immutable canonical JSON wrapper for the Phase 5 no-action bundle."""

    canonical_json: str
    digest_sha256: str

    @classmethod
    def from_dict(cls, value: object) -> "Phase5NoActionBundle":
        validated = _validate_bundle_payload(value)
        canonical = _canonical_json_bytes(validated)
        bundle = cls(
            canonical_json=canonical.decode("utf-8"),
            digest_sha256=_sha256_bytes(canonical),
        )
        bundle.validate()
        return bundle

    @classmethod
    def from_json_bytes(cls, value: bytes) -> "Phase5NoActionBundle":
        if not isinstance(value, bytes) or not value:
            raise ValueError("bundle JSON must be non-empty bytes")
        try:
            parsed = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("bundle JSON is invalid UTF-8 JSON") from exc
        canonical = _canonical_json_bytes(parsed)
        if canonical != value:
            raise ValueError("bundle JSON bytes are not canonical")
        return cls.from_dict(parsed)

    def validate(self) -> None:
        try:
            parsed = json.loads(self.canonical_json)
        except json.JSONDecodeError as exc:
            raise ValueError("stored bundle canonical_json is invalid") from exc
        canonical = _canonical_json_bytes(parsed)
        if canonical.decode("utf-8") != self.canonical_json:
            raise ValueError("stored bundle canonical_json is not canonical")
        if _sha256_bytes(canonical) != _require_sha256(
            self.digest_sha256,
            "bundle.digest_sha256",
        ):
            raise ValueError("stored bundle digest does not match canonical bytes")
        _validate_bundle_payload(parsed)

    def to_dict(self) -> dict[str, object]:
        self.validate()
        parsed = json.loads(self.canonical_json)
        if not isinstance(parsed, dict):
            raise ValueError("stored bundle root is not an object")
        return parsed

    def canonical_json_bytes(self) -> bytes:
        self.validate()
        return self.canonical_json.encode("utf-8")

    def sha256(self) -> str:
        self.validate()
        return self.digest_sha256


def build_synthetic_phase5_no_action_bundle(fixture: object) -> Phase5NoActionBundle:
    """Build a strict no-action bundle from a synthetic in-memory fixture."""

    normalized_fixture = _validate_synthetic_fixture(fixture)
    duplicate_audit = _build_duplicate_audit(normalized_fixture)
    manifest = _build_manifest(normalized_fixture, duplicate_audit)
    metric_contract = _build_metric_contract()
    artifact_inventory = _build_artifact_inventory()
    matrix = _build_matrix(normalized_fixture, manifest)
    execution_policy = _build_execution_policy(matrix)
    opening_gate = _build_opening_gate()
    replay = _build_replay(
        duplicate_audit,
        manifest,
        metric_contract,
        artifact_inventory,
        matrix,
        execution_policy,
        opening_gate,
    )
    declaration = {
        "schema_version": PHASE5_DECLARATION_SCHEMA_VERSION,
        "phase": "phase5",
        "historical_alias": "M4",
        "status": "entered_no_action_foundation",
        "phase4_exit_commit": PHASE4_EXIT_COMMIT,
        "phase4_exit_handoff_present": True,
        "phase4_exit_handoff_sha256": PHASE4_EXIT_HANDOFF_SHA256,
        "phase4_stability_summary_file_sha256": PHASE4_STABILITY_SUMMARY_FILE_SHA256,
        "phase4_stability_summary_canonical_sha256": PHASE4_STABILITY_SUMMARY_CANONICAL_SHA256,
        "phase4_stability_run_id": PHASE4_STABILITY_RUN_ID,
        "real_h1_read_authorized": False,
        "real_gold_read_authorized": False,
        "holdout_selection_authorized": False,
        "holdout_annotation_authorized": False,
        "holdout_execution_authorized": False,
        "model_action_authorized": False,
        "external_action_authorized": False,
        "training_authorized": False,
        "h1_driven_system_change_authorized": False,
    }
    custody = {
        "schema_version": PHASE5_CUSTODY_SCHEMA_VERSION,
        "status": "synthetic_only_no_real_h1",
        "custody_mode": "synthetic_only_and_opaque_metadata",
        "custodian_role": "independent_evaluator",
        "core_case_target": 12,
        "reserve_case_cap": 8,
        "deep_label_case_min": 4,
        "deep_label_case_max": 6,
        "real_case_count": 0,
        "real_gold_item_count": 0,
        "content_exposed": False,
        "gold_exposed": False,
        "case_body_fields_allowed": False,
        "gold_fields_allowed": False,
        "opaque_commitments_allowed": True,
        "dual_annotation_required": True,
        "adjudication_required": True,
        "real_dual_annotation_complete": False,
        "annotation_payload_exposed": False,
        "read_authorized": False,
        "selection_authorized": False,
        "annotation_authorized": False,
        "execution_authorized": False,
    }
    body = {
        "schema_version": PHASE5_NO_ACTION_BUNDLE_SCHEMA_VERSION,
        "status": "phase5_no_action_structural_preparation_only",
        "declaration": declaration,
        "custody": custody,
        "duplicate_audit": duplicate_audit,
        "holdout_manifest": manifest,
        "metric_contract": metric_contract,
        "artifact_inventory": artifact_inventory,
        "evaluation_matrix": matrix,
        "execution_policy": execution_policy,
        "opening_gate": opening_gate,
        "replay_skeleton": replay,
    }
    root = {"bundle_id": _record_id("phase5-no-action-bundle", body), **body}
    return Phase5NoActionBundle.from_dict(root)


def build_synthetic_phase5_no_action_bundle_from_json_bytes(value: bytes) -> Phase5NoActionBundle:
    """Parse a synthetic fixture from bytes without exposing a filesystem API."""

    if not isinstance(value, bytes) or not value:
        raise ValueError("synthetic fixture must be non-empty bytes")
    try:
        fixture = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("synthetic fixture is invalid UTF-8 JSON") from exc
    return build_synthetic_phase5_no_action_bundle(fixture)
