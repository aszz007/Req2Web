"""Run a fresh ten-case P4-05 F1-F4 direct-acceptance experiment.

Every case starts from canonical B and performs one new raw generation for
F1, F2, F3, and F4.  No prior node checkpoint is reused.  F4 uses the
versioned actual-interaction-target policy and the existing A-07a first-pass
delivery route.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Mapping

from req2web_runtime import phase4_remote_qwen_fresh_integrated as _fresh
from req2web_runtime import phase4_remote_qwen_stability as _stability
from req2web_runtime.phase4_stability_cases import (
    CASE_COUNT,
    CASE_SET_ID,
    CASE_SET_SCHEMA_VERSION,
    canonical_case_set_record_bytes,
    get_stability_case_set,
)


FULL_DIRECT_SCHEMA_PREFIX = (
    "req2web.phase4.p4_05.remote_qwen_stability.full_direct_acceptance"
)
FLOW_AUTHORITY_ROLE = "historical_prebuilt_b_stability_runner"
ACTIVE_DEFAULT_ENTRY = False
FULL_DIRECT_POLICY_SCHEMA_VERSION = f"{FULL_DIRECT_SCHEMA_PREFIX}.policy.v1"
FULL_DIRECT_PREFLIGHT_SCHEMA_VERSION = (
    f"{FULL_DIRECT_SCHEMA_PREFIX}.preflight.v1"
)
FULL_DIRECT_SUMMARY_SCHEMA_VERSION = f"{FULL_DIRECT_SCHEMA_PREFIX}.summary.v1"
FULL_DIRECT_MODE = "full_f1_f4_direct_acceptance"
FULL_DIRECT_RUN_PREFIX = "p4-05-remote-qwen-stability-full-direct-"
FULL_DIRECT_ROOT_MARKER = (
    ".req2web-phase4-p4-05-remote-qwen-stability-full-direct-root"
)
FULL_DIRECT_PROMPT_REVISION = _fresh.P4_05_FULL_DIRECT_PROMPT_REVISION
FULL_DIRECT_PROMPT_NODES = _fresh.P4_05_FULL_DIRECT_PROMPT_NODES
FULL_DIRECT_PER_CASE_PER_NODE_CALL_CAP = 1
FULL_DIRECT_TOTAL_NEW_CALL_CAP = CASE_COUNT * len(_fresh.NODE_ORDER)
FULL_DIRECT_ZERO_HISTORY = {node_id: 0 for node_id in _fresh.NODE_ORDER}


class Phase4RemoteQwenFullDirectStabilityError(ValueError):
    """Raised when the fresh full-chain stability experiment fails closed."""


def _require_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4RemoteQwenFullDirectStabilityError(
            f"{name} must be an object"
        )
    return dict(value)


def _assert_same(actual: object, expected: object, message: str) -> None:
    if _fresh._canonical_bytes(actual) != _fresh._canonical_bytes(expected):
        raise Phase4RemoteQwenFullDirectStabilityError(message)


def _read_json(path: Path, *, required: bool = False) -> dict[str, object] | None:
    try:
        return _stability._read_json(path, required=required)
    except _stability.Phase4RemoteQwenStabilityError as exc:
        raise Phase4RemoteQwenFullDirectStabilityError(str(exc)) from exc


def _create_policy(
    *,
    run_id: str,
    case_set: Mapping[str, object],
    profile_identity: Mapping[str, object],
    model_inventory_identity: Mapping[str, object],
) -> dict[str, object]:
    root = {
        "schema_version": FULL_DIRECT_POLICY_SCHEMA_VERSION,
        "experiment_mode": FULL_DIRECT_MODE,
        "run_id": run_id,
        "case_set_schema_version": CASE_SET_SCHEMA_VERSION,
        "case_set_id": CASE_SET_ID,
        "case_set_identity": copy.deepcopy(case_set["case_set_identity"]),
        "case_order": copy.deepcopy(case_set["case_order"]),
        "case_count": CASE_COUNT,
        "node_order": list(_fresh.NODE_ORDER),
        "prompt_revision": FULL_DIRECT_PROMPT_REVISION,
        "prompt_nodes": list(FULL_DIRECT_PROMPT_NODES),
        "checkpoint_reuse": False,
        "history_result_roots": [],
        "resume_prefix": None,
        "new_call_nodes": list(_fresh.NODE_ORDER),
        "new_call_cap_per_case_per_node": (
            FULL_DIRECT_PER_CASE_PER_NODE_CALL_CAP
        ),
        "total_new_call_cap": FULL_DIRECT_TOTAL_NEW_CALL_CAP,
        "profile_identity": copy.deepcopy(dict(profile_identity)),
        "model_inventory_identity": copy.deepcopy(
            dict(model_inventory_identity)
        ),
        "downstream_policy": "a07a_direct_first_pass_v1",
        "a07b_status": "not_executed_by_policy",
        "retry_count": 0,
        "automatic_retry": False,
        "input_truncation": False,
        "output_truncation": False,
        "model_worker_policy": (
            "one isolated worker per case; the worker loads once inside the "
            "case and serves F1-F4 before verified teardown"
        ),
        "training": False,
        "weight_update": False,
        "lora": False,
        "dataset_expansion": False,
        "h1_or_gold": False,
        "formal_quality_claim": False,
        "action_state": _fresh._action_state(
            model_action=False,
            remote_action=False,
        ),
    }
    return {
        **root,
        "policy_identity": _fresh._identity(
            root,
            revision=FULL_DIRECT_POLICY_SCHEMA_VERSION,
        ),
    }


def _validate_policy(
    policy: Mapping[str, object],
    *,
    expected: Mapping[str, object],
) -> dict[str, object]:
    data = dict(policy)
    if data.get("schema_version") != FULL_DIRECT_POLICY_SCHEMA_VERSION:
        raise Phase4RemoteQwenFullDirectStabilityError(
            "full-direct policy schema drifted"
        )
    identity_root = {
        key: value for key, value in data.items() if key != "policy_identity"
    }
    _assert_same(
        data.get("policy_identity"),
        _fresh._identity(
            identity_root,
            revision=FULL_DIRECT_POLICY_SCHEMA_VERSION,
        ),
        "full-direct policy identity drifted",
    )
    _assert_same(data, expected, "full-direct policy binding drifted")
    return data


def prepare_phase4_remote_qwen_full_direct_stability(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    run_id: str | None = None,
    resume_existing: bool = False,
) -> dict[str, object]:
    """Prepare or reload the full-chain experiment without generation."""

    _fresh._offline_process()
    model_root = _fresh._safe_path(model_root, "model root", directory=True)
    integrity_evidence = _fresh._safe_path(
        integrity_evidence,
        "integrity evidence",
        directory=False,
    )
    result_root = _fresh._safe_path(result_root, "result root")
    case_set = get_stability_case_set()
    inventory = _fresh._remote.validate_remote_model_inventory(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
    )
    runtime_facts = _fresh._remote._collect_remote_runtime_facts()
    gpu_facts = _fresh._remote._probe_remote_gpu_facts()
    if gpu_facts["device_name"] != _fresh._remote.REMOTE_DEVICE_NAME:
        raise Phase4RemoteQwenFullDirectStabilityError(
            "full-direct experiment requires RTX 5090 GPU0"
        )
    if int(gpu_facts["total_vram_bytes"]) < _fresh._remote.REMOTE_MIN_VRAM_BYTES:
        raise Phase4RemoteQwenFullDirectStabilityError(
            "full-direct GPU VRAM is below the RTX 5090 floor"
        )
    profile = _fresh.RemoteFreshIntegratedProfile.create(
        inventory=inventory,
        runtime_facts=runtime_facts,
        gpu_facts=gpu_facts,
    )
    profile.validate()
    profile_identity = _stability._profile_identity(profile)
    inventory_identity = _require_mapping(
        inventory.get("inventory_identity"),
        "full-direct model inventory identity",
    )

    existing_policy = (
        _read_json(result_root / "stability_policy.json")
        if result_root.exists()
        else None
    )
    if existing_policy is not None:
        if not resume_existing:
            raise Phase4RemoteQwenFullDirectStabilityError(
                "existing full-direct result root requires --resume-existing"
            )
        selected_run_id = str(existing_policy.get("run_id", ""))
        if not selected_run_id or (
            run_id is not None and run_id != selected_run_id
        ):
            raise Phase4RemoteQwenFullDirectStabilityError(
                "existing full-direct run identity drifted"
            )
    else:
        if result_root.exists() and any(result_root.iterdir()):
            raise Phase4RemoteQwenFullDirectStabilityError(
                "full-direct result root must be new or already owned"
            )
        if resume_existing:
            raise Phase4RemoteQwenFullDirectStabilityError(
                "--resume-existing requires an existing full-direct root"
            )
        selected_run_id = run_id or (
            f"{FULL_DIRECT_RUN_PREFIX}{uuid.uuid4().hex[:16]}"
        )

    policy = _create_policy(
        run_id=selected_run_id,
        case_set=case_set,
        profile_identity=profile_identity,
        model_inventory_identity=inventory_identity,
    )
    if existing_policy is not None:
        policy = _validate_policy(existing_policy, expected=policy)
        marker = result_root / FULL_DIRECT_ROOT_MARKER
        if (
            marker.is_symlink()
            or not marker.is_file()
            or marker.read_bytes() != FULL_DIRECT_ROOT_MARKER.encode("ascii")
        ):
            raise Phase4RemoteQwenFullDirectStabilityError(
                "full-direct result-root marker is invalid"
            )
        saved_case_set = _read_json(
            result_root / "stability_case_set.json",
            required=True,
        )
        _assert_same(
            saved_case_set,
            case_set,
            "saved full-direct case set drifted",
        )
        summary = _read_json(result_root / "stability_summary.json")
        return {
            "result_root": result_root,
            "run_id": selected_run_id,
            "case_set": case_set,
            "inventory": inventory,
            "profile": profile,
            "policy": policy,
            "summary": summary,
        }

    preflight_root = {
        "schema_version": FULL_DIRECT_PREFLIGHT_SCHEMA_VERSION,
        "experiment_mode": FULL_DIRECT_MODE,
        "run_id": selected_run_id,
        "case_set_identity": copy.deepcopy(case_set["case_set_identity"]),
        "policy_identity": copy.deepcopy(policy["policy_identity"]),
        "model_inventory_identity": inventory_identity,
        "profile_identity": profile_identity,
        "model_root": str(model_root),
        "integrity_evidence": str(integrity_evidence),
        "result_root": str(result_root),
        "total_new_call_cap": FULL_DIRECT_TOTAL_NEW_CALL_CAP,
        "action_state": _fresh._action_state(
            model_action=False,
            remote_action=False,
        ),
    }
    preflight = {
        **preflight_root,
        "preflight_identity": _fresh._identity(
            preflight_root,
            revision=FULL_DIRECT_PREFLIGHT_SCHEMA_VERSION,
        ),
    }
    result_root.mkdir(parents=True, exist_ok=True)
    _fresh._write_fsync(
        result_root / FULL_DIRECT_ROOT_MARKER,
        FULL_DIRECT_ROOT_MARKER.encode("ascii"),
    )
    _fresh._write_fsync(
        result_root / "stability_case_set.json",
        canonical_case_set_record_bytes(case_set),
    )
    _fresh._write_fsync(
        result_root / "model_inventory.json",
        _fresh._canonical_bytes(inventory),
    )
    _fresh._write_fsync(
        result_root / "remote_profile.json",
        profile.canonical_bytes(),
    )
    _fresh._write_fsync(
        result_root / "stability_policy.json",
        _fresh._canonical_bytes(policy),
    )
    _fresh._write_fsync(
        result_root / "preflight_manifest.json",
        _fresh._canonical_bytes(preflight),
    )
    return {
        "result_root": result_root,
        "run_id": selected_run_id,
        "case_set": case_set,
        "inventory": inventory,
        "profile": profile,
        "policy": policy,
        "summary": None,
    }


def run_phase4_remote_qwen_full_direct_stability(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    confirm_full_direct_stability: bool,
    run_id: str | None = None,
    console: object | None = None,
    resume_existing: bool = False,
) -> dict[str, object]:
    """Run ten fresh cases with one F1-F4 call per node and no checkpoint reuse."""

    if confirm_full_direct_stability is not True:
        raise Phase4RemoteQwenFullDirectStabilityError(
            "explicit confirmation is required for full-direct generation"
        )
    prepared = prepare_phase4_remote_qwen_full_direct_stability(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        result_root=result_root,
        run_id=run_id,
        resume_existing=resume_existing,
    )
    if prepared.get("summary") is not None:
        return _require_mapping(
            prepared["summary"],
            "existing full-direct summary",
        )

    selected_run_id = str(prepared["run_id"])
    case_set = _require_mapping(prepared["case_set"], "full-direct case set")
    policy = _require_mapping(prepared["policy"], "full-direct policy")
    profile_identity = _require_mapping(
        policy["profile_identity"],
        "full-direct profile identity",
    )
    inventory_identity = _require_mapping(
        policy["model_inventory_identity"],
        "full-direct inventory identity",
    )
    policy_identity = _require_mapping(
        policy["policy_identity"],
        "full-direct policy identity",
    )
    cases = case_set.get("cases")
    if not isinstance(cases, list) or len(cases) != CASE_COUNT:
        raise Phase4RemoteQwenFullDirectStabilityError(
            "full-direct case set is invalid"
        )
    try:
        _stability._validate_cases_layout(
            result_root=result_root,
            case_set=case_set,
        )
        previous_results = _stability._load_progress(
            result_root=result_root,
            case_set=case_set,
            experiment_run_id=selected_run_id,
            experiment_policy_identity=policy_identity,
            expected_profile_identity=profile_identity,
            expected_model_inventory_identity=inventory_identity,
            prompt_revision=FULL_DIRECT_PROMPT_REVISION,
            prompt_nodes=FULL_DIRECT_PROMPT_NODES,
            experiment_mode=FULL_DIRECT_MODE,
            expected_historical_per_node_calls=FULL_DIRECT_ZERO_HISTORY,
        )
    except _stability.Phase4RemoteQwenStabilityError as exc:
        raise Phase4RemoteQwenFullDirectStabilityError(str(exc)) from exc

    case_results = list(previous_results)
    cases_root = result_root / "cases"
    expected_case_calls = {node_id: 1 for node_id in _fresh.NODE_ORDER}
    for index, case_value in enumerate(cases, start=1):
        case = _require_mapping(case_value, "full-direct case")
        child_parent_binding = _stability._child_parent_experiment_binding(
            experiment_run_id=selected_run_id,
            experiment_policy_identity=policy_identity,
            index=index,
            case=case,
        )
        child_root = _stability._expected_child_root(
            result_root,
            index,
            case,
        )
        try:
            _stability._assert_child_root(
                cases_root=cases_root,
                child_root=child_root,
                must_exist=False,
            )
        except _stability.Phase4RemoteQwenStabilityError as exc:
            raise Phase4RemoteQwenFullDirectStabilityError(str(exc)) from exc
        if index <= len(previous_results):
            if console is not None:
                print(
                    f"[P4-05-STABILITY] full-direct case "
                    f"{index:02d}/{CASE_COUNT} {case['case_id']} "
                    "resumed from progress",
                    file=console,
                    flush=True,
                )
            continue
        if console is not None:
            print(
                f"[P4-05-STABILITY] full-direct case "
                f"{index:02d}/{CASE_COUNT} {case['case_id']} started",
                file=console,
                flush=True,
            )
        child_result: Mapping[str, object] | None
        if child_root.exists():
            child_result = None
        else:
            child_result = _fresh.run_phase4_remote_qwen_fresh_integrated(
                model_root=model_root,
                integrity_evidence=integrity_evidence,
                result_root=child_root,
                confirm_one_remote_fresh_integrated_run=True,
                run_id=_stability._expected_child_run_id(
                    selected_run_id,
                    index,
                ),
                console=console,
                history_result_roots=(),
                resume_from_result_root=None,
                resume_prefix=None,
                b_input=case,
                prompt_revision=FULL_DIRECT_PROMPT_REVISION,
                expected_profile_identity=profile_identity,
                expected_model_inventory_identity=inventory_identity,
                parent_experiment_binding=child_parent_binding,
            )
            if (
                not isinstance(child_result, Mapping)
                or child_result.get("status")
                not in {"delivery_terminal_success", "failed_closed"}
            ):
                raise Phase4RemoteQwenFullDirectStabilityError(
                    "full-direct single-case runner is not terminal"
                )
        try:
            case_result = _stability._summarize_case(
                experiment_run_id=selected_run_id,
                index=index,
                case=case,
                child_root=child_root,
                child_result=child_result,
                exception=None,
                expected_profile_identity=profile_identity,
                expected_model_inventory_identity=inventory_identity,
                parent_experiment_binding=child_parent_binding,
                prompt_revision=FULL_DIRECT_PROMPT_REVISION,
                prompt_nodes=FULL_DIRECT_PROMPT_NODES,
                experiment_mode=FULL_DIRECT_MODE,
                baseline_binding=None,
                expected_historical_per_node_calls=FULL_DIRECT_ZERO_HISTORY,
            )
            validated_case_result = _stability._validate_case_result(
                case_result,
                experiment_run_id=selected_run_id,
                index=index,
                case=case,
                result_root=result_root,
                expected_profile_identity=profile_identity,
                expected_model_inventory_identity=inventory_identity,
                parent_experiment_binding=child_parent_binding,
                prompt_revision=FULL_DIRECT_PROMPT_REVISION,
                prompt_nodes=FULL_DIRECT_PROMPT_NODES,
                experiment_mode=FULL_DIRECT_MODE,
                expected_historical_per_node_calls=FULL_DIRECT_ZERO_HISTORY,
            )
        except _stability.Phase4RemoteQwenStabilityError as exc:
            raise Phase4RemoteQwenFullDirectStabilityError(str(exc)) from exc
        if validated_case_result["per_node_generate_calls"] != expected_case_calls:
            raise Phase4RemoteQwenFullDirectStabilityError(
                "full-direct case did not generate exactly once for every node"
            )
        case_results.append(validated_case_result)
        aggregate_so_far = _stability._aggregate(case_results)
        if (
            int(aggregate_so_far["total_model_generate_calls"])
            > FULL_DIRECT_TOTAL_NEW_CALL_CAP
        ):
            raise Phase4RemoteQwenFullDirectStabilityError(
                "full-direct experiment exceeded its new-call cap"
            )
        _stability._write_progress(
            result_root=result_root,
            experiment_run_id=selected_run_id,
            index=index,
            case=case,
            case_result=validated_case_result,
            aggregate_so_far=aggregate_so_far,
        )
        if console is not None:
            per_node_raw_pass = validated_case_result[
                "per_node_raw_contract_pass"
            ]
            all_nodes_raw_pass = bool(
                isinstance(per_node_raw_pass, Mapping)
                and all(
                    per_node_raw_pass.get(node_id) is True
                    for node_id in _fresh.NODE_ORDER
                )
            )
            print(
                f"[P4-05-STABILITY] full-direct case "
                f"{index:02d}/{CASE_COUNT} "
                f"status={validated_case_result['status']} "
                f"all_nodes_raw_pass={all_nodes_raw_pass} "
                f"first_pass="
                f"{validated_case_result['downstream_first_pass_success']}",
                file=console,
                flush=True,
            )

    if len(case_results) != CASE_COUNT:
        raise Phase4RemoteQwenFullDirectStabilityError(
            "full-direct experiment did not complete all cases"
        )
    aggregate = _stability._aggregate(case_results)
    expected_aggregate_calls = {
        node_id: CASE_COUNT for node_id in _fresh.NODE_ORDER
    }
    if (
        aggregate["total_model_generate_calls"] != FULL_DIRECT_TOTAL_NEW_CALL_CAP
        or aggregate["per_node_called_count"] != expected_aggregate_calls
    ):
        raise Phase4RemoteQwenFullDirectStabilityError(
            "full-direct aggregate model-call accounting is incomplete"
        )
    all_nodes_raw_pass = bool(
        aggregate["per_node_raw_contract_pass_count"]
        == expected_aggregate_calls
    )
    quality_target_met = bool(
        all_nodes_raw_pass
        and aggregate["f4_normalized_case_count"] == 0
        and aggregate["downstream_first_pass_success_count"] == CASE_COUNT
        and aggregate["repair_attempted_count"] == 0
        and aggregate["g0_fallback_count"] == 0
        and aggregate["failed_closed_count"] == 0
        and aggregate["system_adjustment_case_count"] == 0
    )
    root = {
        "schema_version": FULL_DIRECT_SUMMARY_SCHEMA_VERSION,
        "experiment_mode": FULL_DIRECT_MODE,
        "run_id": selected_run_id,
        "case_set_id": CASE_SET_ID,
        "case_set_identity": copy.deepcopy(case_set["case_set_identity"]),
        "policy_identity": copy.deepcopy(policy["policy_identity"]),
        "profile_identity": profile_identity,
        "model_inventory_identity": inventory_identity,
        "prompt_revision": FULL_DIRECT_PROMPT_REVISION,
        "prompt_nodes": list(FULL_DIRECT_PROMPT_NODES),
        "checkpoint_reuse": False,
        "revision_complete": True,
        "case_results": case_results,
        "aggregate": aggregate,
        "new_model_generate_calls": aggregate["total_model_generate_calls"],
        "all_nodes_raw_contract_pass": all_nodes_raw_pass,
        "a07b_executed_count": 0,
        "quality_target": {
            "per_node_raw_contract_pass_count": expected_aggregate_calls,
            "f4_normalized_case_count": 0,
            "downstream_first_pass_success_count": CASE_COUNT,
            "repair_attempted_count": 0,
            "g0_fallback_count": 0,
            "failed_closed_count": 0,
            "system_adjustment_case_count": 0,
        },
        "quality_target_met": quality_target_met,
        "training_executed": False,
        "claim_boundary": (
            "P4-05 fresh ten-case F1-F4 BF16 direct-acceptance experiment; "
            "one raw call per node per case, no checkpoint reuse, no retry, "
            "no truncation, A-07a direct first-pass delivery, and no H1, "
            "browser, training, or formal-quality claim"
        ),
        "action_state": _fresh._action_state(
            model_action=True,
            remote_action=True,
        ),
    }
    summary = {
        **root,
        "summary_identity": _fresh._identity(
            root,
            revision=FULL_DIRECT_SUMMARY_SCHEMA_VERSION,
        ),
    }
    _fresh._write_fsync(
        result_root / "stability_summary.json",
        _fresh._canonical_bytes(summary),
    )
    return summary


__all__ = [
    "FULL_DIRECT_MODE",
    "FULL_DIRECT_PROMPT_NODES",
    "FULL_DIRECT_PROMPT_REVISION",
    "FULL_DIRECT_TOTAL_NEW_CALL_CAP",
    "Phase4RemoteQwenFullDirectStabilityError",
    "prepare_phase4_remote_qwen_full_direct_stability",
    "run_phase4_remote_qwen_full_direct_stability",
]
