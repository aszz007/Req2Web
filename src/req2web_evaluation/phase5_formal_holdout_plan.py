"""Opaque Phase 5 P5-02/P5-03 formal-holdout no-action planning.

The module consumes only a validated P5-01 no-action bundle and an in-memory
synthetic opaque-slot fixture. It has no file, model, browser, network, GPU,
training, or real H1/gold access interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
import json
from typing import Mapping, Sequence

from req2web_evaluation.phase5_holdout_no_action import (
    PHASE4_EXIT_COMMIT,
    Phase5NoActionBundle,
)


OPAQUE_SLOT_FIXTURE_SCHEMA_VERSION = "req2web.phase5.formal_holdout.opaque_slot_fixture.v1"
FORMAL_NO_ACTION_PLAN_SCHEMA_VERSION = "req2web.phase5.formal_holdout.no_action_plan.v1"
CUSTODY_PLAN_SCHEMA_VERSION = "req2web.phase5.formal_holdout.custody_plan.v1"
FORMAL_MATRIX_SCHEMA_VERSION = "req2web.phase5.formal_holdout.matrix.v1"
BUDGET_PLAN_SCHEMA_VERSION = "req2web.phase5.formal_holdout.budget_plan.v1"
RESULT_INVENTORY_SCHEMA_VERSION = "req2web.phase5.formal_holdout.result_inventory.v1"
OPENING_GATE_SCHEMA_VERSION = "req2web.phase5.formal_holdout.opening_gate.v1"
FORMAL_REPLAY_SCHEMA_VERSION = "req2web.phase5.formal_holdout.replay.v1"
PUBLICATION_SCOPE_DESCRIPTOR_SCHEMA_VERSION = (
    "req2web.phase5.formal_holdout.publication_scope_descriptor.v1"
)
PUBLICATION_SCOPE_DESCRIPTOR_REVISION = (
    "req2web.phase5.publication_scope_candidate.no_action.v1"
)

P5_01_FOUNDATION_BUNDLE_SHA256 = "72086f9984ccbe892e32218b8170cb3f5196e6390ed94bd2c5690fb9ecbc203d"
P5_01_FOUNDATION_BUNDLE_ID = "phase5-no-action-bundle-5acf48bdbbac1d661f596034bfdacbe3c6703a2c10c9581cce957783e1cb26df"

_FIXTURE_ID = "phase5-formal-holdout-opaque-slots-v1"
_FIXTURE_KIND = "synthetic_opaque_slots_only_no_real_h1"
_EXPERIMENT_SEED = "phase5-formal-matrix-order-v1"
_MODEL_NODES = ("F1", "F2", "F3", "F4")
_CRITICAL_ROLES = frozenset(
    {"requirement", "ui_reference", "interaction_flow", "implementation", "validation"}
)
_REQUIRED_CORE_COVERAGE = frozenset(
    {
        "mobile",
        "desktop",
        "form",
        "search_list",
        "media_input",
        "dashboard",
        "settings_admin",
        "normal_success",
        "input_error",
        "permission_denial",
        "empty_result",
        "retry_recovery",
        "empty_error_recovery",
        "single_page",
        "medium_multistate",
    }
)
_INTERVENTIONS = ("none", "irrelevant_evidence", "remove_critical_role")
_PUBLICATION_CONDITION_SET_REVISION = (
    "req2web.phase5.publication_condition_ids.not_semantically_frozen.v1"
)
_GROUP_ORDER = {"G0": 0, "G1": 1, "G2": 2}
_TERMINAL_RESULT_STATUSES = (
    "first_pass_success",
    "recovered_success",
    "fallback_delivery",
    "failed_delivery",
    "upstream_failure",
    "not_invoked_invalid_upstream",
    "not_run_budget_cap",
    "provider_runtime_failure",
)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_json(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def _record_id(prefix: str, value: object) -> str:
    return f"{prefix}-{_sha256_json(value)}"


def _exact(value: object, keys: Sequence[str], field_name: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ValueError(f"{field_name} must have exact keys {tuple(keys)}")
    return dict(value)


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _exact_text(value: object, expected: str, field_name: str) -> str:
    value = _text(value, field_name)
    if value != expected:
        raise ValueError(f"{field_name} must be {expected!r}")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _false(value: object, field_name: str) -> None:
    if _boolean(value, field_name):
        raise ValueError(f"{field_name} must remain false")


def _true(value: object, field_name: str) -> None:
    if not _boolean(value, field_name):
        raise ValueError(f"{field_name} must be true")


def _integer(value: object, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field_name} must be an integer >= {minimum}")
    return value


def _digest(value: object, field_name: str) -> str:
    value = _text(value, field_name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _array(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    return list(value)


def _sorted_texts(value: object, field_name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    values = _array(value, field_name)
    if not allow_empty and not values:
        raise ValueError(f"{field_name} must not be empty")
    result = tuple(_text(item, f"{field_name}[]") for item in values)
    if result != tuple(sorted(set(result))):
        raise ValueError(f"{field_name} must be sorted and unique")
    return result


def _blind_case_ref(slot_id: str) -> str:
    return f"blind-case-{sha256(f'{_EXPERIMENT_SEED}\\0{slot_id}'.encode()).hexdigest()[:20]}"


def _seed(blind_case_ref: str, intervention: str, repeat_index: int) -> int:
    material = f"{_EXPERIMENT_SEED}\0{blind_case_ref}\0{intervention}\0{repeat_index}".encode()
    return int(sha256(material).hexdigest()[:8], 16)


def _dependency_order(dependencies: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    remaining = set(dependencies)
    completed: set[str] = set()
    order: list[str] = []
    while remaining:
        ready = [item for item in remaining if set(dependencies[item]).issubset(completed)]
        if not ready:
            raise ValueError("formal matrix block dependency cycle")
        ready.sort(key=lambda item: sha256(f"{_EXPERIMENT_SEED}\0{item}".encode()).hexdigest())
        selected = ready[0]
        remaining.remove(selected)
        completed.add(selected)
        order.append(selected)
    return tuple(order)


def _validate_foundation(foundation: Phase5NoActionBundle) -> dict[str, object]:
    foundation.validate()
    if foundation.sha256() != P5_01_FOUNDATION_BUNDLE_SHA256:
        raise ValueError("P5-01 foundation bundle hash drifted")
    payload = foundation.to_dict()
    if payload["bundle_id"] != P5_01_FOUNDATION_BUNDLE_ID:
        raise ValueError("P5-01 foundation bundle id drifted")
    if payload["declaration"]["phase4_exit_commit"] != PHASE4_EXIT_COMMIT:
        raise ValueError("P5-01 Phase 4 exit binding drifted")
    if payload["declaration"]["status"] != "entered_no_action_foundation":
        raise ValueError("P5-01 phase status is not entered_no_action_foundation")
    if payload["opening_gate"]["status"] != "closed_no_action":
        raise ValueError("P5-01 H1 opening gate must remain closed")
    return payload


def _validate_fixture(value: object) -> dict[str, object]:
    fixture = _exact(
        value,
        (
            "schema_version",
            "fixture_id",
            "fixture_kind",
            "experiment_seed",
            "deep_label_slot_ids",
            "slots",
        ),
        "fixture",
    )
    _exact_text(fixture["schema_version"], OPAQUE_SLOT_FIXTURE_SCHEMA_VERSION, "fixture.schema_version")
    _exact_text(fixture["fixture_id"], _FIXTURE_ID, "fixture.fixture_id")
    _exact_text(fixture["fixture_kind"], _FIXTURE_KIND, "fixture.fixture_kind")
    _exact_text(fixture["experiment_seed"], _EXPERIMENT_SEED, "fixture.experiment_seed")
    deep_ids = _sorted_texts(fixture["deep_label_slot_ids"], "fixture.deep_label_slot_ids")
    if len(deep_ids) != 4:
        raise ValueError("fixture must contain exactly four synthetic deep-label slots")

    slots: list[dict[str, object]] = []
    ids: list[str] = []
    for index, raw_slot in enumerate(_array(fixture["slots"], "fixture.slots")):
        slot = _exact(raw_slot, ("slot_id", "slot_kind", "coverage_tags", "critical_role"), f"fixture.slots[{index}]")
        slot_id = _text(slot["slot_id"], f"fixture.slots[{index}].slot_id")
        slot_kind = _text(slot["slot_kind"], f"fixture.slots[{index}].slot_kind")
        expected_prefix = "synthetic-h1-core-" if slot_kind == "core" else "synthetic-h1-reserve-"
        if slot_kind not in {"core", "reserve"} or not slot_id.startswith(expected_prefix):
            raise ValueError("fixture slot identity/kind is invalid")
        coverage_tags = _sorted_texts(slot["coverage_tags"], f"fixture.slots[{index}].coverage_tags")
        critical_role = _text(slot["critical_role"], f"fixture.slots[{index}].critical_role")
        if critical_role not in _CRITICAL_ROLES:
            raise ValueError("fixture critical_role is unsupported")
        ids.append(slot_id)
        slots.append(
            {
                "slot_id": slot_id,
                "slot_kind": slot_kind,
                "coverage_tags": list(coverage_tags),
                "critical_role": critical_role,
            }
        )
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValueError("fixture slots must be sorted and unique")
    core = [slot for slot in slots if slot["slot_kind"] == "core"]
    reserve = [slot for slot in slots if slot["slot_kind"] == "reserve"]
    if len(core) != 12 or len(reserve) != 8:
        raise ValueError("fixture must contain exactly 12 core and 8 reserve slots")
    if not set(deep_ids).issubset(slot["slot_id"] for slot in core):
        raise ValueError("deep-label slots must be core slots")
    core_coverage = {tag for slot in core for tag in slot["coverage_tags"]}
    if not _REQUIRED_CORE_COVERAGE.issubset(core_coverage):
        raise ValueError("synthetic core coverage contract is incomplete")
    return {**fixture, "deep_label_slot_ids": list(deep_ids), "slots": slots}


def _build_custody(fixture: Mapping[str, object]) -> dict[str, object]:
    deep_ids = set(fixture["deep_label_slot_ids"])
    slots: list[dict[str, object]] = []
    for source in fixture["slots"]:
        if not isinstance(source, Mapping):
            raise ValueError("normalized slot must be an object")
        commitment = _sha256_json(source)
        slots.append(
            {
                "slot_id": source["slot_id"],
                "blind_case_ref": _blind_case_ref(str(source["slot_id"])),
                "slot_kind": source["slot_kind"],
                "coverage_tags": source["coverage_tags"],
                "critical_role": source["critical_role"],
                "deep_label_slot": source["slot_id"] in deep_ids,
                "case_commitment_sha256": commitment,
                "duplicate_status": "synthetic_nonduplicate_fixture",
                "annotation_status": "not_performed_no_action",
                "gold_commitment_sha256": None,
                "real_case_content_present": False,
                "real_gold_content_present": False,
            }
        )
    body = {
        "schema_version": CUSTODY_PLAN_SCHEMA_VERSION,
        "status": "opaque_synthetic_custody_ready_no_real_h1",
        "core_case_target": 12,
        "reserve_case_cap": 8,
        "deep_label_case_count": 4,
        "slots": slots,
        "manual_duplicate_adjudication": {
            "entry_fields": [
                "left_case_commitment_sha256",
                "right_case_commitment_sha256",
                "verdict",
                "reason_code",
                "adjudicator_commitment_sha256",
                "decision_sha256",
            ],
            "entries": [],
            "real_subject_content_visible": False,
        },
        "dual_annotation": {
            "required_annotator_count": 2,
            "raw_annotation_commitments": [],
            "adjudication_commitments": [],
            "agreement_report_commitment_sha256": None,
            "real_annotation_content_visible": False,
            "complete": False,
        },
        "gold_custody": {
            "gold_commitments": [],
            "runtime_gold_access_allowed": False,
            "provider_gold_access_allowed": False,
            "real_gold_content_visible": False,
        },
        "real_h1_selected": False,
        "reserve_promotion_performed": False,
        "custodian_release_allowed": False,
    }
    return {"custody_plan_id": _record_id("phase5-custody-plan", body), **body}


def _matrix_row(
    *,
    blind_case_ref: str,
    deep_label_slot: bool,
    group: str,
    intervention: str,
    critical_role: str,
    repeat_index: int,
    block_id: str,
    dependencies: tuple[str, ...],
) -> dict[str, object]:
    row_seed = _seed(blind_case_ref, intervention, repeat_index)
    role = "full_guidance" if intervention == "none" else (
        critical_role if intervention == "remove_critical_role" else "irrelevant_evidence"
    )
    route = {
        "G0": ("frozen_g0_v2", "req2web.result.package.v2"),
        "G1": ("evaluation_only_first_pass_v1", "req2web.result.package.v1"),
        "G2": ("recovery_or_same_case_g0", "req2web.result.package.v1_or_v2"),
    }[group]
    identity = {
        "blind_case_ref": blind_case_ref,
        "group": group,
        "intervention": intervention,
        "repeat_index": repeat_index,
        "seed": row_seed,
        "block_id": block_id,
        "schema_version": FORMAL_MATRIX_SCHEMA_VERSION,
    }
    return {
        "row_id": _record_id("phase5-formal-row", identity),
        "blind_case_ref": blind_case_ref,
        "deep_label_slot": deep_label_slot,
        "group": group,
        "role": role,
        "intervention": intervention,
        "repeat_index": repeat_index,
        "seed": row_seed,
        "block_id": block_id,
        "depends_on_block_ids": list(dependencies),
        "applicable": True,
        "pre_registered_exclusion_reason": None,
        "expected_run_count": 1,
        "actual_run_count": 0,
        "planned_deterministic_g0_runs": 1 if group == "G0" else 0,
        "planned_candidate_generations": 1 if group == "G1" else 0,
        "planned_node_generate_calls": len(_MODEL_NODES) if group == "G1" else 0,
        "planned_model_repair_calls": 0,
        "fault_copy": False,
        "artifact_route": route[0],
        "package_schema": route[1],
        "result_status": "not_executed_no_action",
        "result_ref": None,
        "raw_first_status": "not_started_no_action",
    }


def _build_matrix(custody: Mapping[str, object]) -> dict[str, object]:
    core_slots = [slot for slot in custody["slots"] if isinstance(slot, Mapping) and slot["slot_kind"] == "core"]
    rows: list[dict[str, object]] = []
    dependencies: dict[str, tuple[str, ...]] = {}
    for slot in core_slots:
        blind = str(slot["blind_case_ref"])
        critical_role = str(slot["critical_role"])
        deep = bool(slot["deep_label_slot"])
        g0_full = f"g0-{blind}-none"
        dependencies[g0_full] = ()
        rows.append(
            _matrix_row(
                blind_case_ref=blind,
                deep_label_slot=deep,
                group="G0",
                intervention="none",
                critical_role=critical_role,
                repeat_index=0,
                block_id=g0_full,
                dependencies=(),
            )
        )
        interventions = ("none", "irrelevant_evidence", "remove_critical_role") if deep else ("none",)
        intervention_g0: dict[str, str] = {}
        for intervention in interventions:
            if intervention == "none":
                continue
            g0_block = f"g0-{blind}-{intervention}"
            dependencies[g0_block] = (g0_full,)
            intervention_g0[intervention] = g0_block
            rows.append(
                _matrix_row(
                    blind_case_ref=blind,
                    deep_label_slot=deep,
                    group="G0",
                    intervention=intervention,
                    critical_role=critical_role,
                    repeat_index=0,
                    block_id=g0_block,
                    dependencies=(g0_full,),
                )
            )
        for intervention in interventions:
            for repeat_index in range(3):
                candidate_seed = _seed(blind, intervention, repeat_index)
                block_id = f"candidate-{blind}-{intervention}-{candidate_seed}"
                deps = (g0_full,) if intervention == "none" else tuple(sorted((g0_full, intervention_g0[intervention])))
                dependencies[block_id] = deps
                for group in ("G1", "G2"):
                    rows.append(
                        _matrix_row(
                            blind_case_ref=blind,
                            deep_label_slot=deep,
                            group=group,
                            intervention=intervention,
                            critical_role=critical_role,
                            repeat_index=repeat_index,
                            block_id=block_id,
                            dependencies=deps,
                        )
                    )
    block_order = _dependency_order(dependencies)
    position = {block_id: index for index, block_id in enumerate(block_order)}
    rows.sort(key=lambda row: (position[str(row["block_id"])], _GROUP_ORDER[str(row["group"])], str(row["row_id"])))
    body = {
        "schema_version": FORMAL_MATRIX_SCHEMA_VERSION,
        "status": "synthetic_opaque_full_matrix_not_executed",
        "model_nodes": list(_MODEL_NODES),
        "core_case_count": 12,
        "reserve_case_count": 8,
        "deep_label_case_count": 4,
        "repeat_count": 3,
        "experiment_seed": _EXPERIMENT_SEED,
        "block_order_algorithm": "dependency_aware_sha256_block_order_v1",
        "block_order": list(block_order),
        "block_order_sha256": _sha256_json(list(block_order)),
        "rows": rows,
        "fault_copy_template": {
            "status": "empty_requires_evaluator_gold_before_real_h1",
            "rows": [],
            "planned_model_repair_calls": 0,
        },
        "formal_h1_rows_present": False,
        "candidate_outputs_present": False,
    }
    return {"matrix_id": _record_id("phase5-formal-matrix", body), **body}


def _build_budget(matrix: Mapping[str, object]) -> dict[str, object]:
    rows = [row for row in matrix["rows"] if isinstance(row, Mapping)]
    g0_runs = sum(int(row["planned_deterministic_g0_runs"]) for row in rows)
    candidate_generations = sum(int(row["planned_candidate_generations"]) for row in rows)
    node_calls = sum(int(row["planned_node_generate_calls"]) for row in rows)
    body = {
        "schema_version": BUDGET_PLAN_SCHEMA_VERSION,
        "status": "structural_formula_only_not_approved_for_h1",
        "matrix_id": matrix["matrix_id"],
        "matrix_sha256": _sha256_json(matrix),
        "candidate_generation_formula": "3 * core_case_count + 6 * deep_label_case_count",
        "node_call_formula": "4 * candidate_generation_cap",
        "deterministic_g0_run_cap": g0_runs,
        "candidate_generation_cap": candidate_generations,
        "node_generate_call_cap": node_calls,
        "candidate_model_repair_call_cap": 0,
        "fault_copy_model_repair_call_cap": 0,
        "provider_retry_call_cap": 0,
        "total_model_call_cap": node_calls,
        "calls_consumed": 0,
        "gpu_time_cap_seconds": None,
        "cost_cap_minor_units": None,
        "storage_cap_bytes": None,
        "generate_start_consumes_call": True,
        "automatic_retry_allowed": False,
        "budget_reset_allowed": False,
        "owner_approved": False,
        "model_action_authorized": False,
    }
    return {"budget_plan_id": _record_id("phase5-budget-plan", body), **body}


def _build_inventory(matrix: Mapping[str, object]) -> dict[str, object]:
    result_rows = [
        {
            "row_id": row["row_id"],
            "status": "not_executed_no_action",
            "terminal_status": None,
            "g0_prefreeze_ref": None,
            "raw_response_ref": None,
            "candidate_chain_ref": None,
            "gate_report_ref": None,
            "package_ref": None,
            "failure_ref": None,
            "normalization_applied": False,
            "repair_attempted": False,
            "fallback_attempted": False,
        }
        for row in matrix["rows"]
        if isinstance(row, Mapping)
    ]
    body = {
        "schema_version": RESULT_INVENTORY_SCHEMA_VERSION,
        "status": "empty_result_inventory_no_action",
        "matrix_id": matrix["matrix_id"],
        "matrix_sha256": _sha256_json(matrix),
        "expected_result_row_count": len(result_rows),
        "actual_terminal_result_count": 0,
        "allowed_terminal_statuses": list(_TERMINAL_RESULT_STATUSES),
        "result_rows": result_rows,
        "raw_first_required": True,
        "same_case_g0_prefreeze_required": True,
        "source_result_rewrite_allowed": False,
        "normalization_repair_replay_composition_fallback_count_as_raw_success": False,
    }
    return {"result_inventory_id": _record_id("phase5-result-inventory", body), **body}


def _build_gate() -> dict[str, object]:
    conditions = {
        "p5_00_phase4_intake_complete": True,
        "p5_01_foundation_complete": True,
        "p5_02_opaque_custody_interface_complete": True,
        "p5_03_full_matrix_template_complete": True,
        "owner_real_action_approval_present": False,
        "approved_path1_or_path2_runtime_frozen": False,
        "real_duplicate_audit_and_adjudication_complete": False,
        "real_dual_annotation_and_gold_commitments_complete": False,
        "real_manifest_release_metrics_thresholds_frozen": False,
        "real_time_cost_storage_caps_approved": False,
        "real_raw_first_result_inventory_ready": False,
    }
    body = {
        "schema_version": OPENING_GATE_SCHEMA_VERSION,
        "status": "closed_all_no_action_work_complete",
        "conditions": conditions,
        "h1_open_allowed": False,
        "real_h1_read_allowed": False,
        "real_gold_read_allowed": False,
        "holdout_execution_allowed": False,
        "model_action_allowed": False,
        "gpu_remote_paid_action_allowed": False,
        "training_lora_allowed": False,
        "formal_quality_claim_allowed": False,
    }
    return {"opening_gate_id": _record_id("phase5-formal-opening-gate", body), **body}


def _build_replay(
    custody: Mapping[str, object],
    matrix: Mapping[str, object],
    budget: Mapping[str, object],
    inventory: Mapping[str, object],
    gate: Mapping[str, object],
) -> dict[str, object]:
    body = {
        "schema_version": FORMAL_REPLAY_SCHEMA_VERSION,
        "status": "not_executed_no_action",
        "custody_plan_id": custody["custody_plan_id"],
        "custody_plan_sha256": _sha256_json(custody),
        "matrix_id": matrix["matrix_id"],
        "matrix_sha256": _sha256_json(matrix),
        "budget_plan_id": budget["budget_plan_id"],
        "budget_plan_sha256": _sha256_json(budget),
        "result_inventory_id": inventory["result_inventory_id"],
        "result_inventory_sha256": _sha256_json(inventory),
        "opening_gate_id": gate["opening_gate_id"],
        "opening_gate_sha256": _sha256_json(gate),
        "expected_main_matrix_rows": len(matrix["rows"]),
        "terminal_main_matrix_rows": 0,
        "incomplete_experiment_if_any_row_unterminal": True,
        "h1_opened": False,
        "h1_used_test_set": False,
        "model_invoked": False,
        "external_action_occurred": False,
        "training_executed": False,
        "formal_quality_claimed": False,
    }
    return {"replay_id": _record_id("phase5-formal-replay", body), **body}


def _validate_plan_payload(value: object) -> dict[str, object]:
    plan = _exact(
        value,
        (
            "schema_version",
            "plan_id",
            "status",
            "phase4_exit_commit",
            "foundation_bundle_id",
            "foundation_bundle_sha256",
            "custody_plan",
            "formal_matrix",
            "budget_plan",
            "result_inventory",
            "opening_gate",
            "replay",
        ),
        "plan",
    )
    _exact_text(plan["schema_version"], FORMAL_NO_ACTION_PLAN_SCHEMA_VERSION, "plan.schema_version")
    _exact_text(plan["status"], "p5_02_p5_03_complete_no_action", "plan.status")
    _exact_text(plan["phase4_exit_commit"], PHASE4_EXIT_COMMIT, "plan.phase4_exit_commit")
    _exact_text(plan["foundation_bundle_id"], P5_01_FOUNDATION_BUNDLE_ID, "plan.foundation_bundle_id")
    if _digest(plan["foundation_bundle_sha256"], "plan.foundation_bundle_sha256") != P5_01_FOUNDATION_BUNDLE_SHA256:
        raise ValueError("plan foundation hash drifted")
    body = {key: plan[key] for key in plan if key != "plan_id"}
    if plan["plan_id"] != _record_id("phase5-formal-no-action-plan", body):
        raise ValueError("plan id does not match canonical content")

    custody = _exact(
        plan["custody_plan"],
        (
            "custody_plan_id",
            "schema_version",
            "status",
            "core_case_target",
            "reserve_case_cap",
            "deep_label_case_count",
            "slots",
            "manual_duplicate_adjudication",
            "dual_annotation",
            "gold_custody",
            "real_h1_selected",
            "reserve_promotion_performed",
            "custodian_release_allowed",
        ),
        "plan.custody_plan",
    )
    custody_body = {key: custody[key] for key in custody if key != "custody_plan_id"}
    if custody["custody_plan_id"] != _record_id("phase5-custody-plan", custody_body):
        raise ValueError("custody plan id drifted")
    _exact_text(custody["schema_version"], CUSTODY_PLAN_SCHEMA_VERSION, "custody.schema_version")
    _exact_text(
        custody["status"],
        "opaque_synthetic_custody_ready_no_real_h1",
        "custody.status",
    )
    slots = custody["slots"]
    if len(slots) != 20:
        raise ValueError("custody slot count drifted")
    if sum(1 for slot in slots if slot["slot_kind"] == "core") != 12:
        raise ValueError("custody core slot count drifted")
    if sum(1 for slot in slots if slot["slot_kind"] == "reserve") != 8:
        raise ValueError("custody reserve slot count drifted")
    if sum(1 for slot in slots if slot["deep_label_slot"]) != 4:
        raise ValueError("custody deep-label slot count drifted")
    if len({slot["blind_case_ref"] for slot in slots}) != 20:
        raise ValueError("custody blind refs are not unique")
    for slot in slots:
        if slot["blind_case_ref"] != _blind_case_ref(slot["slot_id"]):
            raise ValueError("custody blind ref drifted")
        expected_commitment = _sha256_json(
            {
                "slot_id": slot["slot_id"],
                "slot_kind": slot["slot_kind"],
                "coverage_tags": slot["coverage_tags"],
                "critical_role": slot["critical_role"],
            }
        )
        if slot["case_commitment_sha256"] != expected_commitment:
            raise ValueError("custody case commitment drifted")
        if slot["gold_commitment_sha256"] is not None:
            raise ValueError("custody gold commitment must remain absent")
        _false(slot["real_case_content_present"], "custody.slot.real_case_content_present")
        _false(slot["real_gold_content_present"], "custody.slot.real_gold_content_present")
    if custody["manual_duplicate_adjudication"]["entries"]:
        raise ValueError("manual duplicate adjudication must remain empty")
    if (
        custody["dual_annotation"]["raw_annotation_commitments"]
        or custody["dual_annotation"]["adjudication_commitments"]
        or custody["dual_annotation"]["agreement_report_commitment_sha256"] is not None
    ):
        raise ValueError("dual annotation commitments must remain empty")
    if custody["gold_custody"]["gold_commitments"]:
        raise ValueError("gold commitments must remain empty")
    for key in ("real_h1_selected", "reserve_promotion_performed", "custodian_release_allowed"):
        _false(custody[key], f"custody.{key}")

    matrix = plan["formal_matrix"]
    if not isinstance(matrix, Mapping) or matrix["matrix_id"] != _record_id(
        "phase5-formal-matrix",
        {key: matrix[key] for key in matrix if key != "matrix_id"},
    ):
        raise ValueError("formal matrix identity drifted")
    _exact_text(matrix["schema_version"], FORMAL_MATRIX_SCHEMA_VERSION, "matrix.schema_version")
    _exact_text(
        matrix["status"],
        "synthetic_opaque_full_matrix_not_executed",
        "matrix.status",
    )
    if tuple(matrix["model_nodes"]) != _MODEL_NODES:
        raise ValueError("formal matrix model node list drifted")
    rows = matrix["rows"]
    if len(rows) != 140 or len(matrix["block_order"]) != 80:
        raise ValueError("formal matrix count drifted")
    if sum(1 for row in rows if row["group"] == "G0") != 20:
        raise ValueError("formal matrix G0 count drifted")
    if sum(1 for row in rows if row["group"] == "G1") != 60:
        raise ValueError("formal matrix G1 count drifted")
    if sum(1 for row in rows if row["group"] == "G2") != 60:
        raise ValueError("formal matrix G2 count drifted")
    if any(row["actual_run_count"] != 0 or row["result_status"] != "not_executed_no_action" for row in rows):
        raise ValueError("formal matrix contains an executed row")
    if matrix["formal_h1_rows_present"] is not False or matrix["candidate_outputs_present"] is not False:
        raise ValueError("formal matrix action flags drifted")
    row_ids: set[str] = set()
    dependencies: dict[str, tuple[str, ...]] = {}
    groups_by_block: dict[str, list[str]] = {}
    for row in rows:
        identity = {
            "blind_case_ref": row["blind_case_ref"],
            "group": row["group"],
            "intervention": row["intervention"],
            "repeat_index": row["repeat_index"],
            "seed": row["seed"],
            "block_id": row["block_id"],
            "schema_version": FORMAL_MATRIX_SCHEMA_VERSION,
        }
        if row["row_id"] != _record_id("phase5-formal-row", identity) or row["row_id"] in row_ids:
            raise ValueError("formal matrix row identity drifted")
        row_ids.add(row["row_id"])
        block_dependencies = tuple(row["depends_on_block_ids"])
        if row["block_id"] in dependencies and dependencies[row["block_id"]] != block_dependencies:
            raise ValueError("formal matrix block dependencies disagree")
        dependencies[row["block_id"]] = block_dependencies
        groups_by_block.setdefault(row["block_id"], []).append(row["group"])
    if any(
        sorted(groups) not in (["G0"], ["G1", "G2"])
        for groups in groups_by_block.values()
    ):
        raise ValueError("formal matrix block grouping drifted")
    expected_order = _dependency_order(dependencies)
    if tuple(matrix["block_order"]) != expected_order:
        raise ValueError("formal matrix block order drifted")
    if matrix["block_order_sha256"] != _sha256_json(list(expected_order)):
        raise ValueError("formal matrix block order hash drifted")
    position = {block_id: index for index, block_id in enumerate(expected_order)}
    expected_rows = sorted(
        rows,
        key=lambda row: (
            position[row["block_id"]],
            _GROUP_ORDER[row["group"]],
            row["row_id"],
        ),
    )
    if rows != expected_rows:
        raise ValueError("formal matrix row order drifted")

    budget = plan["budget_plan"]
    budget_body = {key: budget[key] for key in budget if key != "budget_plan_id"}
    if budget["budget_plan_id"] != _record_id("phase5-budget-plan", budget_body):
        raise ValueError("budget plan identity drifted")
    if budget["matrix_id"] != matrix["matrix_id"] or budget["matrix_sha256"] != _sha256_json(matrix):
        raise ValueError("budget matrix binding drifted")
    expected_budget = (20, 60, 240)
    observed_budget = (
        budget["deterministic_g0_run_cap"],
        budget["candidate_generation_cap"],
        budget["node_generate_call_cap"],
    )
    if observed_budget != expected_budget or budget["total_model_call_cap"] != 240:
        raise ValueError("budget formula drifted")
    for key in (
        "candidate_model_repair_call_cap",
        "fault_copy_model_repair_call_cap",
        "provider_retry_call_cap",
        "calls_consumed",
    ):
        if budget[key] != 0:
            raise ValueError("no-action budget contains a nonzero action count")
    for key in ("owner_approved", "model_action_authorized", "automatic_retry_allowed", "budget_reset_allowed"):
        _false(budget[key], f"budget.{key}")

    inventory = plan["result_inventory"]
    inventory_body = {
        key: inventory[key] for key in inventory if key != "result_inventory_id"
    }
    if inventory["result_inventory_id"] != _record_id(
        "phase5-result-inventory",
        inventory_body,
    ):
        raise ValueError("result inventory identity drifted")
    if inventory["matrix_sha256"] != _sha256_json(matrix) or len(inventory["result_rows"]) != 140:
        raise ValueError("result inventory binding/count drifted")
    if inventory["actual_terminal_result_count"] != 0:
        raise ValueError("result inventory must remain empty")
    if any(item["status"] != "not_executed_no_action" for item in inventory["result_rows"]):
        raise ValueError("result inventory contains an executed row")
    if [item["row_id"] for item in inventory["result_rows"]] != [
        row["row_id"] for row in rows
    ]:
        raise ValueError("result inventory row binding/order drifted")

    gate = plan["opening_gate"]
    gate_body = {key: gate[key] for key in gate if key != "opening_gate_id"}
    if gate["opening_gate_id"] != _record_id("phase5-formal-opening-gate", gate_body):
        raise ValueError("opening gate identity drifted")
    if gate["status"] != "closed_all_no_action_work_complete":
        raise ValueError("opening gate status drifted")
    for key in (
        "p5_00_phase4_intake_complete",
        "p5_01_foundation_complete",
        "p5_02_opaque_custody_interface_complete",
        "p5_03_full_matrix_template_complete",
    ):
        _true(gate["conditions"][key], f"gate.conditions.{key}")
    for key in (
        "owner_real_action_approval_present",
        "approved_path1_or_path2_runtime_frozen",
        "real_duplicate_audit_and_adjudication_complete",
        "real_dual_annotation_and_gold_commitments_complete",
        "real_manifest_release_metrics_thresholds_frozen",
        "real_time_cost_storage_caps_approved",
        "real_raw_first_result_inventory_ready",
    ):
        _false(gate["conditions"][key], f"gate.conditions.{key}")
    for key in (
        "h1_open_allowed",
        "real_h1_read_allowed",
        "real_gold_read_allowed",
        "holdout_execution_allowed",
        "model_action_allowed",
        "gpu_remote_paid_action_allowed",
        "training_lora_allowed",
        "formal_quality_claim_allowed",
    ):
        _false(gate[key], f"gate.{key}")

    replay = plan["replay"]
    replay_body = {key: replay[key] for key in replay if key != "replay_id"}
    if replay["replay_id"] != _record_id("phase5-formal-replay", replay_body):
        raise ValueError("formal replay identity drifted")
    replay_bindings = (
        ("custody_plan", custody, "custody_plan_id"),
        ("matrix", matrix, "matrix_id"),
        ("budget_plan", budget, "budget_plan_id"),
        ("result_inventory", inventory, "result_inventory_id"),
        ("opening_gate", gate, "opening_gate_id"),
    )
    for prefix, target, id_key in replay_bindings:
        if replay[f"{prefix}_id"] != target[id_key]:
            raise ValueError(f"formal replay {prefix} id binding drifted")
        if replay[f"{prefix}_sha256"] != _sha256_json(target):
            raise ValueError(f"formal replay {prefix} hash binding drifted")
    if replay["expected_main_matrix_rows"] != 140 or replay["terminal_main_matrix_rows"] != 0:
        raise ValueError("replay row accounting drifted")
    for key in (
        "h1_opened",
        "h1_used_test_set",
        "model_invoked",
        "external_action_occurred",
        "training_executed",
        "formal_quality_claimed",
    ):
        _false(replay[key], f"replay.{key}")
    return plan


@dataclass(frozen=True)
class Phase5FormalNoActionPlan:
    canonical_json: str
    digest_sha256: str

    @classmethod
    def from_dict(cls, value: object) -> "Phase5FormalNoActionPlan":
        payload = _validate_plan_payload(value)
        canonical = _canonical_json_bytes(payload)
        result = cls(canonical.decode("utf-8"), sha256(canonical).hexdigest())
        result.validate()
        return result

    @classmethod
    def from_json_bytes(cls, value: bytes) -> "Phase5FormalNoActionPlan":
        if not isinstance(value, bytes) or not value:
            raise ValueError("plan JSON must be non-empty bytes")
        try:
            parsed = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("plan JSON is invalid") from exc
        if _canonical_json_bytes(parsed) != value:
            raise ValueError("plan JSON bytes are not canonical")
        return cls.from_dict(parsed)

    def validate(self) -> None:
        parsed = json.loads(self.canonical_json)
        canonical = _canonical_json_bytes(parsed)
        if canonical.decode() != self.canonical_json:
            raise ValueError("stored plan JSON is not canonical")
        if sha256(canonical).hexdigest() != _digest(self.digest_sha256, "plan.digest_sha256"):
            raise ValueError("stored plan digest drifted")
        _validate_plan_payload(parsed)

    def to_dict(self) -> dict[str, object]:
        self.validate()
        value = json.loads(self.canonical_json)
        if not isinstance(value, dict):
            raise ValueError("stored plan root is not an object")
        return value

    def canonical_json_bytes(self) -> bytes:
        self.validate()
        return self.canonical_json.encode("utf-8")

    def sha256(self) -> str:
        self.validate()
        return self.digest_sha256


def build_phase5_formal_no_action_plan(
    foundation: Phase5NoActionBundle,
    opaque_fixture: object,
) -> Phase5FormalNoActionPlan:
    """Build the complete P5-02/P5-03 synthetic opaque no-action plan."""

    foundation_payload = _validate_foundation(foundation)
    fixture = _validate_fixture(opaque_fixture)
    custody = _build_custody(fixture)
    matrix = _build_matrix(custody)
    budget = _build_budget(matrix)
    inventory = _build_inventory(matrix)
    gate = _build_gate()
    replay = _build_replay(custody, matrix, budget, inventory, gate)
    body = {
        "schema_version": FORMAL_NO_ACTION_PLAN_SCHEMA_VERSION,
        "status": "p5_02_p5_03_complete_no_action",
        "phase4_exit_commit": PHASE4_EXIT_COMMIT,
        "foundation_bundle_id": foundation_payload["bundle_id"],
        "foundation_bundle_sha256": foundation.sha256(),
        "custody_plan": custody,
        "formal_matrix": matrix,
        "budget_plan": budget,
        "result_inventory": inventory,
        "opening_gate": gate,
        "replay": replay,
    }
    return Phase5FormalNoActionPlan.from_dict(
        {"plan_id": _record_id("phase5-formal-no-action-plan", body), **body}
    )


def build_phase5_formal_no_action_plan_from_json_bytes(
    foundation: Phase5NoActionBundle,
    opaque_fixture_bytes: bytes,
) -> Phase5FormalNoActionPlan:
    """Parse a synthetic opaque fixture from bytes; no filesystem API is exposed."""

    if not isinstance(opaque_fixture_bytes, bytes) or not opaque_fixture_bytes:
        raise ValueError("opaque fixture must be non-empty bytes")
    try:
        fixture = json.loads(opaque_fixture_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("opaque fixture is invalid") from exc
    return build_phase5_formal_no_action_plan(foundation, fixture)


def validate_phase5_publication_scope_descriptor(
    value: object,
) -> dict[str, object]:
    """Validate the scope-only publication candidate without opening H1."""

    descriptor = _exact(
        value,
        (
            "descriptor_id",
            "schema_version",
            "revision",
            "status",
            "phase4_exit_commit",
            "historical_plan_binding",
            "publication_scope",
            "reserve_policy",
            "sealed_content_state",
            "action_gates",
        ),
        "publication_scope_descriptor",
    )
    _exact_text(
        descriptor["schema_version"],
        PUBLICATION_SCOPE_DESCRIPTOR_SCHEMA_VERSION,
        "publication_scope_descriptor.schema_version",
    )
    _exact_text(
        descriptor["revision"],
        PUBLICATION_SCOPE_DESCRIPTOR_REVISION,
        "publication_scope_descriptor.revision",
    )
    _exact_text(
        descriptor["status"],
        "candidate_scope_only_no_action",
        "publication_scope_descriptor.status",
    )
    _exact_text(
        descriptor["phase4_exit_commit"],
        PHASE4_EXIT_COMMIT,
        "publication_scope_descriptor.phase4_exit_commit",
    )

    historical = _exact(
        descriptor["historical_plan_binding"],
        (
            "schema_version",
            "core_case_count",
            "reserve_case_count",
            "deep_label_case_count",
            "main_matrix_row_count",
            "node_generate_call_cap",
            "unchanged",
        ),
        "publication_scope_descriptor.historical_plan_binding",
    )
    _exact_text(
        historical["schema_version"],
        FORMAL_NO_ACTION_PLAN_SCHEMA_VERSION,
        "publication_scope_descriptor.historical_plan_binding.schema_version",
    )
    expected_historical = {
        "core_case_count": 12,
        "reserve_case_count": 8,
        "deep_label_case_count": 4,
        "main_matrix_row_count": 140,
        "node_generate_call_cap": 240,
    }
    for key, expected in expected_historical.items():
        if (
            _integer(
                historical[key],
                f"publication_scope_descriptor.historical_plan_binding.{key}",
            )
            != expected
        ):
            raise ValueError(f"historical formal-plan {key} drifted")
    _true(
        historical["unchanged"],
        "publication_scope_descriptor.historical_plan_binding.unchanged",
    )

    scope = _exact(
        descriptor["publication_scope"],
        (
            "case_material",
            "core_case_count",
            "condition_set_revision",
            "condition_ids",
            "condition_count",
            "model_node_ids",
            "model_node_count",
            "runtime_row_count",
            "node_generate_call_cap",
            "condition_semantics_status",
        ),
        "publication_scope_descriptor.publication_scope",
    )
    _exact_text(
        scope["case_material"],
        "new_project_authored_sealed_cases",
        "publication_scope_descriptor.publication_scope.case_material",
    )
    _exact_text(
        scope["condition_set_revision"],
        _PUBLICATION_CONDITION_SET_REVISION,
        "publication_scope_descriptor.publication_scope.condition_set_revision",
    )
    condition_ids = _array(
        scope["condition_ids"],
        "publication_scope_descriptor.publication_scope.condition_ids",
    )
    if condition_ids != list(_INTERVENTIONS):
        raise ValueError("publication condition IDs drifted")
    model_node_ids = _array(
        scope["model_node_ids"],
        "publication_scope_descriptor.publication_scope.model_node_ids",
    )
    if model_node_ids != list(_MODEL_NODES):
        raise ValueError("publication model-node IDs drifted")
    expected_scope_counts = {
        "core_case_count": 4,
        "condition_count": 3,
        "model_node_count": 4,
        "runtime_row_count": 12,
        "node_generate_call_cap": 48,
    }
    for key, expected in expected_scope_counts.items():
        if (
            _integer(
                scope[key],
                f"publication_scope_descriptor.publication_scope.{key}",
            )
            != expected
        ):
            raise ValueError(f"publication scope {key} drifted")
    if scope["runtime_row_count"] != scope["core_case_count"] * scope["condition_count"]:
        raise ValueError("publication runtime-row formula drifted")
    if scope["node_generate_call_cap"] != scope["runtime_row_count"] * len(_MODEL_NODES):
        raise ValueError("publication node-call formula drifted")
    _exact_text(
        scope["condition_semantics_status"],
        "ids_recorded_semantics_not_frozen",
        "publication_scope_descriptor.publication_scope.condition_semantics_status",
    )

    reserve = _exact(
        descriptor["reserve_policy"],
        (
            "reserve_case_cap",
            "replacement_only",
            "invalidity_declared_before_opening",
            "adds_success_sample",
            "adds_runtime_rows",
            "adds_generate_calls",
        ),
        "publication_scope_descriptor.reserve_policy",
    )
    if _integer(
        reserve["reserve_case_cap"],
        "publication_scope_descriptor.reserve_policy.reserve_case_cap",
    ) != 2:
        raise ValueError("publication reserve-case cap drifted")
    _true(
        reserve["replacement_only"],
        "publication_scope_descriptor.reserve_policy.replacement_only",
    )
    _true(
        reserve["invalidity_declared_before_opening"],
        "publication_scope_descriptor.reserve_policy.invalidity_declared_before_opening",
    )
    for key in ("adds_success_sample", "adds_runtime_rows", "adds_generate_calls"):
        _false(reserve[key], f"publication_scope_descriptor.reserve_policy.{key}")

    sealed = _exact(
        descriptor["sealed_content_state"],
        (
            "real_case_content_present",
            "gold_content_present",
            "case_identities_frozen",
            "condition_semantics_frozen",
            "metric_rules_frozen",
            "execution_package_frozen",
        ),
        "publication_scope_descriptor.sealed_content_state",
    )
    for key, item in sealed.items():
        _false(item, f"publication_scope_descriptor.sealed_content_state.{key}")

    gates = _exact(
        descriptor["action_gates"],
        (
            "h1_opened",
            "model_action_authorized",
            "gpu_action_authorized",
            "remote_action_authorized",
            "formal_evaluation_authorized",
            "formal_quality_claimed",
        ),
        "publication_scope_descriptor.action_gates",
    )
    for key, item in gates.items():
        _false(item, f"publication_scope_descriptor.action_gates.{key}")

    body = {
        key: item for key, item in descriptor.items() if key != "descriptor_id"
    }
    expected_id = _record_id("phase5-publication-scope", body)
    if descriptor["descriptor_id"] != expected_id:
        raise ValueError("publication scope descriptor ID drifted")
    return descriptor


def build_phase5_publication_scope_descriptor() -> dict[str, object]:
    """Build the bounded publication candidate as no-action scope metadata."""

    body = {
        "schema_version": PUBLICATION_SCOPE_DESCRIPTOR_SCHEMA_VERSION,
        "revision": PUBLICATION_SCOPE_DESCRIPTOR_REVISION,
        "status": "candidate_scope_only_no_action",
        "phase4_exit_commit": PHASE4_EXIT_COMMIT,
        "historical_plan_binding": {
            "schema_version": FORMAL_NO_ACTION_PLAN_SCHEMA_VERSION,
            "core_case_count": 12,
            "reserve_case_count": 8,
            "deep_label_case_count": 4,
            "main_matrix_row_count": 140,
            "node_generate_call_cap": 240,
            "unchanged": True,
        },
        "publication_scope": {
            "case_material": "new_project_authored_sealed_cases",
            "core_case_count": 4,
            "condition_set_revision": _PUBLICATION_CONDITION_SET_REVISION,
            "condition_ids": list(_INTERVENTIONS),
            "condition_count": len(_INTERVENTIONS),
            "model_node_ids": list(_MODEL_NODES),
            "model_node_count": len(_MODEL_NODES),
            "runtime_row_count": 4 * len(_INTERVENTIONS),
            "node_generate_call_cap": 4 * len(_INTERVENTIONS) * len(_MODEL_NODES),
            "condition_semantics_status": "ids_recorded_semantics_not_frozen",
        },
        "reserve_policy": {
            "reserve_case_cap": 2,
            "replacement_only": True,
            "invalidity_declared_before_opening": True,
            "adds_success_sample": False,
            "adds_runtime_rows": False,
            "adds_generate_calls": False,
        },
        "sealed_content_state": {
            "real_case_content_present": False,
            "gold_content_present": False,
            "case_identities_frozen": False,
            "condition_semantics_frozen": False,
            "metric_rules_frozen": False,
            "execution_package_frozen": False,
        },
        "action_gates": {
            "h1_opened": False,
            "model_action_authorized": False,
            "gpu_action_authorized": False,
            "remote_action_authorized": False,
            "formal_evaluation_authorized": False,
            "formal_quality_claimed": False,
        },
    }
    descriptor = {
        "descriptor_id": _record_id("phase5-publication-scope", body),
        **body,
    }
    return validate_phase5_publication_scope_descriptor(descriptor)
