"""Run the bounded ten-case P4-05 Agent-chain stability baseline.

The stability experiment reuses the accepted single-case P4-05 remote runner
without changing its node contracts, model profile, downstream authorities, or
success accounting.  Each project-authored synthetic commerce case is isolated
in its own result root and receives at most one real generate call per F node.
One case failing closed does not erase its evidence or prevent the remaining
baseline cases from running.
"""

from __future__ import annotations

import copy
import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Mapping

from req2web_runtime import phase4_remote_qwen_fresh_integrated as _fresh
from req2web_runtime.phase4_stability_cases import (
    CASE_COUNT,
    CASE_SET_ID,
    CASE_SET_SCHEMA_VERSION,
    canonical_case_set_record_bytes,
    get_stability_case_set,
    validate_stability_case_set,
)


STABILITY_SCHEMA_PREFIX = "req2web.phase4.p4_05.remote_qwen_stability"
STABILITY_POLICY_SCHEMA_VERSION = f"{STABILITY_SCHEMA_PREFIX}.policy.v1"
STABILITY_PREFLIGHT_SCHEMA_VERSION = f"{STABILITY_SCHEMA_PREFIX}.preflight.v1"
STABILITY_CASE_RESULT_SCHEMA_VERSION = f"{STABILITY_SCHEMA_PREFIX}.case_result.v1"
STABILITY_SUMMARY_SCHEMA_VERSION = f"{STABILITY_SCHEMA_PREFIX}.summary.v1"
STABILITY_RUN_PREFIX = "p4-05-remote-qwen-stability-baseline-"
STABILITY_ROOT_MARKER = ".req2web-phase4-p4-05-remote-qwen-stability-root"
BASELINE_PER_CASE_PER_NODE_CALL_CAP = 1
BASELINE_TOTAL_CALL_CAP = CASE_COUNT * len(_fresh.NODE_ORDER)
PROMPT_CONTRACT_REVISION_CAP = 1


class Phase4RemoteQwenStabilityError(ValueError):
    """Raised when the bounded stability experiment cannot be executed safely."""


def _read_json(path: Path) -> dict[str, object] | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _create_policy(
    *,
    run_id: str,
    case_set: Mapping[str, object],
    inventory: Mapping[str, object],
    profile: _fresh.RemoteFreshIntegratedProfile,
) -> dict[str, object]:
    validated_case_set = validate_stability_case_set(copy.deepcopy(dict(case_set)))
    profile.validate()
    root = {
        "schema_version": STABILITY_POLICY_SCHEMA_VERSION,
        "run_id": run_id,
        "case_set_schema_version": CASE_SET_SCHEMA_VERSION,
        "case_set_id": CASE_SET_ID,
        "case_set_identity": validated_case_set["case_set_identity"],
        "case_order": copy.deepcopy(validated_case_set["case_order"]),
        "case_count": CASE_COUNT,
        "node_order": list(_fresh.NODE_ORDER),
        "baseline_per_case_per_node_call_cap": (
            BASELINE_PER_CASE_PER_NODE_CALL_CAP
        ),
        "baseline_total_call_cap": BASELINE_TOTAL_CALL_CAP,
        "retry_count": 0,
        "automatic_retry": False,
        "input_truncation": False,
        "output_truncation": False,
        "checkpoint_policy": (
            "preserve every completed child result root; never regenerate a "
            "validated child node inside the same child run"
        ),
        "case_failure_policy": (
            "record the terminal child outcome and continue the remaining "
            "predeclared baseline cases without retry"
        ),
        "baseline_selection_policy": (
            "execute all ten cases in frozen order before interpreting quality"
        ),
        "prompt_contract_revision_cap": PROMPT_CONTRACT_REVISION_CAP,
        "prompt_contract_revision_policy": (
            "after the complete baseline, at most one separately versioned "
            "prompt-contract revision may rerun only necessary failed nodes or "
            "cases; it is not part of this baseline and cannot rewrite evidence"
        ),
        "training": False,
        "weight_update": False,
        "lora": False,
        "dataset_expansion": False,
        "h1_or_gold": False,
        "formal_quality_claim": False,
        "model_inventory_identity": inventory["inventory_identity"],
        "profile_identity": _fresh._identity(
            profile.to_dict(),
            revision=_fresh.P4_05_PROFILE_SCHEMA_VERSION,
        ),
        "single_case_runner_schema_version": _fresh.P4_05_RESULT_SCHEMA_VERSION,
        "action_state": _fresh._action_state(
            model_action=False,
            remote_action=False,
        ),
    }
    return {
        **root,
        "policy_identity": _fresh._identity(
            root,
            revision=STABILITY_POLICY_SCHEMA_VERSION,
        ),
    }


def prepare_phase4_remote_qwen_stability(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    run_id: str | None = None,
) -> dict[str, object]:
    """Write the complete no-generation ten-case stability preflight."""

    _fresh._offline_process()
    model_root = _fresh._safe_path(model_root, "model root", directory=True)
    integrity_evidence = _fresh._safe_path(
        integrity_evidence,
        "integrity evidence",
        directory=False,
    )
    result_root = _fresh._safe_path(result_root, "result root")
    if result_root.exists():
        if result_root.is_symlink() or not result_root.is_dir():
            raise Phase4RemoteQwenStabilityError(
                "stability result root must be a new directory"
            )
        if any(result_root.iterdir()):
            raise Phase4RemoteQwenStabilityError(
                "stability result root must be new and empty"
            )

    case_set = get_stability_case_set()
    inventory = _fresh._remote.validate_remote_model_inventory(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
    )
    runtime_facts = _fresh._remote._collect_remote_runtime_facts()
    gpu_facts = _fresh._remote._probe_remote_gpu_facts()
    if gpu_facts["device_name"] != _fresh._remote.REMOTE_DEVICE_NAME:
        raise Phase4RemoteQwenStabilityError(
            "P4-05 stability baseline requires RTX 5090 GPU0"
        )
    if int(gpu_facts["total_vram_bytes"]) < _fresh._remote.REMOTE_MIN_VRAM_BYTES:
        raise Phase4RemoteQwenStabilityError(
            "P4-05 stability GPU VRAM is below the RTX 5090 floor"
        )
    profile = _fresh.RemoteFreshIntegratedProfile.create(
        inventory=inventory,
        runtime_facts=runtime_facts,
        gpu_facts=gpu_facts,
    )
    selected_run_id = run_id or f"{STABILITY_RUN_PREFIX}{uuid.uuid4().hex[:16]}"
    policy = _create_policy(
        run_id=selected_run_id,
        case_set=case_set,
        inventory=inventory,
        profile=profile,
    )
    preflight = {
        "schema_version": STABILITY_PREFLIGHT_SCHEMA_VERSION,
        "run_id": selected_run_id,
        "case_set_identity": case_set["case_set_identity"],
        "policy_identity": _fresh._identity(
            policy,
            revision=STABILITY_POLICY_SCHEMA_VERSION,
        ),
        "model_inventory_identity": inventory["inventory_identity"],
        "profile_identity": _fresh._identity(
            profile.to_dict(),
            revision=_fresh.P4_05_PROFILE_SCHEMA_VERSION,
        ),
        "model_root": str(model_root),
        "integrity_evidence": str(integrity_evidence),
        "result_root": str(result_root),
        "baseline_total_call_cap": BASELINE_TOTAL_CALL_CAP,
        "action_state": _fresh._action_state(
            model_action=False,
            remote_action=False,
        ),
    }

    result_root.mkdir(parents=True, exist_ok=False)
    _fresh._write_fsync(
        result_root / STABILITY_ROOT_MARKER,
        STABILITY_ROOT_MARKER.encode("ascii"),
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
        "preflight": preflight,
    }


def _summarize_case(
    *,
    index: int,
    case: Mapping[str, object],
    child_root: Path,
    child_result: Mapping[str, object] | None,
    exception: BaseException | None,
) -> dict[str, object]:
    ledger = _read_json(child_root / "model_call_ledger.json")
    per_node_calls: dict[str, int] = {}
    per_node_raw_pass: dict[str, bool] = {}
    per_node_failure_codes: dict[str, str | None] = {}
    for node_id in _fresh.NODE_ORDER:
        ledger_row = (
            ledger.get("per_node", {}).get(node_id, {})
            if isinstance(ledger, dict)
            and isinstance(ledger.get("per_node"), dict)
            else {}
        )
        per_node_calls[node_id] = (
            int(ledger_row.get("generate_started_count", 0))
            if isinstance(ledger_row, dict)
            else 0
        )
        attempt = _read_json(
            child_root / "attempts" / node_id / "attempt_result.json"
        )
        per_node_raw_pass[node_id] = bool(
            attempt is not None and attempt.get("status") == "validated"
        )
        per_node_failure_codes[node_id] = (
            None if attempt is None else attempt.get("failure_code")
        )

    normalization_receipt = _read_json(
        child_root / "attempts" / "F4" / "normalization_receipt.json"
    )
    f4_raw_direct_pass = bool(
        normalization_receipt is not None
        and normalization_receipt.get("raw_model_contract_success") is True
    )
    f4_normalization_count = (
        int(normalization_receipt.get("normalization_count", 0))
        if normalization_receipt is not None
        else 0
    )
    per_node_raw_pass["F4"] = f4_raw_direct_pass
    if normalization_receipt is not None:
        per_node_failure_codes["F4"] = normalization_receipt.get(
            "raw_failure_code"
        )

    final_result = _read_json(child_root / "p4_05_final_result.json")
    source_result = _read_json(child_root / "revalidation_result.json")
    delivery_receipt = _read_json(
        child_root / "delivery" / "phase4_fresh_delivery_receipt.json"
    )
    success_accounting = (
        delivery_receipt.get("success_accounting", {})
        if isinstance(delivery_receipt, dict)
        and isinstance(delivery_receipt.get("success_accounting"), dict)
        else {}
    )
    downstream = (
        delivery_receipt.get("downstream", {})
        if isinstance(delivery_receipt, dict)
        and isinstance(delivery_receipt.get("downstream"), dict)
        else {}
    )
    status = (
        str(child_result.get("status"))
        if child_result is not None and child_result.get("status") is not None
        else (
            str(final_result.get("status"))
            if final_result is not None and final_result.get("status") is not None
            else "failed_closed"
        )
    )
    failure = (
        child_result.get("failure")
        if child_result is not None
        else None
    )
    failure_code = (
        str(failure.get("code"))
        if isinstance(failure, dict) and failure.get("code") is not None
        else (
            type(exception).__name__
            if exception is not None
            else None
        )
    )
    model_success = success_accounting.get("model_success") is True
    repair_success = success_accounting.get("repair_success") is True
    fallback_success = success_accounting.get("fallback_success") is True
    delivery_success = success_accounting.get("delivery_success") is True
    normalized_node_contract_success = bool(
        (
            child_result is not None
            and child_result.get("normalized_node_contract_success") is True
        )
        or (
            source_result is not None
            and source_result.get("normalized_node_contract_success") is True
        )
    )
    system_adjustment_used = (
        f4_normalization_count > 0 or repair_success or fallback_success
    )
    root = {
        "schema_version": STABILITY_CASE_RESULT_SCHEMA_VERSION,
        "case_index": index,
        "case_id": case["case_id"],
        "request_id": case["request_id"],
        "child_result_root": str(child_root),
        "status": status,
        "per_node_generate_calls": per_node_calls,
        "per_node_raw_contract_pass": per_node_raw_pass,
        "per_node_failure_codes": per_node_failure_codes,
        "total_model_generate_calls": sum(per_node_calls.values()),
        "f4_called": per_node_calls["F4"] > 0,
        "f4_raw_direct_pass": f4_raw_direct_pass,
        "f4_normalization_count": f4_normalization_count,
        "f4_normalization_used": f4_normalization_count > 0,
        "normalized_node_contract_success": normalized_node_contract_success,
        "composition_pass": bool(
            source_result is not None
            and source_result.get("composition_status") == "composed"
        ),
        "assembler_pass": bool(
            source_result is not None
            and source_result.get("assembler_status") == "assembled"
        ),
        "downstream_status": downstream.get("status"),
        "downstream_first_pass_success": model_success,
        "deterministic_repair_success": repair_success,
        "g0_fallback_success": fallback_success,
        "delivery_success": delivery_success,
        "system_adjustment_used": system_adjustment_used,
        "failure_code": failure_code,
        "exception_type": (
            None if exception is None else type(exception).__name__
        ),
        "retry_count": 0,
        "automatic_retry": False,
    }
    return {
        **root,
        "case_result_identity": _fresh._identity(
            root,
            revision=STABILITY_CASE_RESULT_SCHEMA_VERSION,
        ),
    }


def _aggregate(case_results: list[dict[str, object]]) -> dict[str, object]:
    per_node_called = {node_id: 0 for node_id in _fresh.NODE_ORDER}
    per_node_raw_pass = {node_id: 0 for node_id in _fresh.NODE_ORDER}
    for item in case_results:
        calls = item["per_node_generate_calls"]
        passes = item["per_node_raw_contract_pass"]
        if not isinstance(calls, dict) or not isinstance(passes, dict):
            raise Phase4RemoteQwenStabilityError(
                "case result node accounting is invalid"
            )
        for node_id in _fresh.NODE_ORDER:
            per_node_called[node_id] += int(calls[node_id])
            per_node_raw_pass[node_id] += int(passes[node_id] is True)

    f4_called = sum(int(item["f4_called"] is True) for item in case_results)
    f4_raw_pass = sum(
        int(item["f4_raw_direct_pass"] is True) for item in case_results
    )
    f4_normalized = sum(
        int(item["f4_normalization_used"] is True) for item in case_results
    )
    composition_pass = sum(
        int(item["composition_pass"] is True) for item in case_results
    )
    assembler_pass = sum(
        int(item["assembler_pass"] is True) for item in case_results
    )
    downstream_first_pass = sum(
        int(item["downstream_first_pass_success"] is True)
        for item in case_results
    )
    repair_count = sum(
        int(item["deterministic_repair_success"] is True)
        for item in case_results
    )
    fallback_count = sum(
        int(item["g0_fallback_success"] is True) for item in case_results
    )
    delivery_count = sum(
        int(item["delivery_success"] is True) for item in case_results
    )
    failed_closed_count = sum(
        int(item["status"] != "delivery_terminal_success")
        for item in case_results
    )
    adjustment_count = sum(
        int(item["system_adjustment_used"] is True) for item in case_results
    )
    failure_counts = Counter(
        str(item["failure_code"])
        for item in case_results
        if item["failure_code"] is not None
    )
    f4_failure_counts = Counter(
        str(item["per_node_failure_codes"]["F4"])
        for item in case_results
        if isinstance(item["per_node_failure_codes"], dict)
        and item["per_node_failure_codes"]["F4"] is not None
    )
    case_count = len(case_results)
    return {
        "case_count": case_count,
        "per_node_called_count": per_node_called,
        "per_node_raw_contract_pass_count": per_node_raw_pass,
        "per_node_raw_contract_pass_rate": {
            node_id: _rate(per_node_raw_pass[node_id], per_node_called[node_id])
            for node_id in _fresh.NODE_ORDER
        },
        "f4_called_count": f4_called,
        "f4_reached_rate": _rate(f4_called, case_count),
        "f4_raw_direct_pass_count": f4_raw_pass,
        "f4_raw_direct_pass_rate": _rate(f4_raw_pass, f4_called),
        "f4_raw_direct_pass_rate_all_cases": _rate(f4_raw_pass, case_count),
        "f4_normalized_case_count": f4_normalized,
        "f4_normalization_rate": _rate(f4_normalized, f4_called),
        "f4_normalization_rate_all_cases": _rate(f4_normalized, case_count),
        "composition_pass_count": composition_pass,
        "composition_pass_rate": _rate(composition_pass, case_count),
        "assembler_pass_count": assembler_pass,
        "assembler_pass_rate": _rate(assembler_pass, case_count),
        "downstream_first_pass_success_count": downstream_first_pass,
        "downstream_first_pass_success_rate": _rate(
            downstream_first_pass,
            case_count,
        ),
        "deterministic_repair_count": repair_count,
        "deterministic_repair_rate": _rate(repair_count, case_count),
        "g0_fallback_count": fallback_count,
        "g0_fallback_rate": _rate(fallback_count, case_count),
        "delivery_success_count": delivery_count,
        "delivery_success_rate": _rate(delivery_count, case_count),
        "failed_closed_count": failed_closed_count,
        "failed_closed_rate": _rate(failed_closed_count, case_count),
        "system_adjustment_case_count": adjustment_count,
        "system_adjustment_rate": _rate(adjustment_count, case_count),
        "total_model_generate_calls": sum(
            int(item["total_model_generate_calls"]) for item in case_results
        ),
        "failure_code_counts": dict(sorted(failure_counts.items())),
        "f4_raw_failure_code_counts": dict(sorted(f4_failure_counts.items())),
    }


def run_phase4_remote_qwen_stability(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    confirm_ten_case_baseline: bool,
    run_id: str | None = None,
    console: object | None = None,
) -> dict[str, object]:
    """Execute all ten frozen baseline cases and write an immutable summary."""

    if confirm_ten_case_baseline is not True:
        raise Phase4RemoteQwenStabilityError(
            "explicit confirmation is required for the ten-case baseline"
        )
    prepared = prepare_phase4_remote_qwen_stability(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        result_root=result_root,
        run_id=run_id,
    )
    selected_run_id = str(prepared["run_id"])
    case_set = prepared["case_set"]
    if not isinstance(case_set, dict) or not isinstance(case_set.get("cases"), list):
        raise Phase4RemoteQwenStabilityError("prepared stability case set is invalid")

    case_results: list[dict[str, object]] = []
    cases_root = result_root / "cases"
    for index, case in enumerate(case_set["cases"], start=1):
        if not isinstance(case, dict):
            raise Phase4RemoteQwenStabilityError("stability case row is invalid")
        child_root = cases_root / f"{index:02d}-{case['case_id']}"
        if console is not None:
            print(
                f"[P4-05-STABILITY] case {index:02d}/{CASE_COUNT} "
                f"{case['case_id']} started",
                file=console,
                flush=True,
            )
        child_result: Mapping[str, object] | None = None
        exception: BaseException | None = None
        try:
            child_result = _fresh.run_phase4_remote_qwen_fresh_integrated(
                model_root=model_root,
                integrity_evidence=integrity_evidence,
                result_root=child_root,
                confirm_one_remote_fresh_integrated_run=True,
                run_id=f"{selected_run_id}-case-{index:02d}",
                console=console,
                b_input=case,
            )
        except Exception as exc:
            exception = exc
        case_result = _summarize_case(
            index=index,
            case=case,
            child_root=child_root,
            child_result=child_result,
            exception=exception,
        )
        calls = case_result["per_node_generate_calls"]
        if (
            not isinstance(calls, dict)
            or any(int(calls[node_id]) > 1 for node_id in _fresh.NODE_ORDER)
            or int(case_result["total_model_generate_calls"])
            > len(_fresh.NODE_ORDER)
        ):
            raise Phase4RemoteQwenStabilityError(
                "a stability case exceeded its one-call-per-node budget"
            )
        case_results.append(case_result)
        aggregate_so_far = _aggregate(case_results)
        if (
            int(aggregate_so_far["total_model_generate_calls"])
            > index * len(_fresh.NODE_ORDER)
            or int(aggregate_so_far["total_model_generate_calls"])
            > BASELINE_TOTAL_CALL_CAP
        ):
            raise Phase4RemoteQwenStabilityError(
                "stability baseline exceeded its cumulative model-call budget"
            )
        _fresh._write_fsync(
            result_root / "progress" / f"{index:02d}.json",
            _fresh._canonical_bytes(
                {
                    "schema_version": (
                        f"{STABILITY_SCHEMA_PREFIX}.progress.v1"
                    ),
                    "run_id": selected_run_id,
                    "completed_case_count": index,
                    "case_result": case_result,
                    "aggregate_so_far": aggregate_so_far,
                }
            ),
        )
        if console is not None:
            print(
                f"[P4-05-STABILITY] case {index:02d}/{CASE_COUNT} "
                f"status={case_result['status']} "
                f"f4_raw={case_result['f4_raw_direct_pass']} "
                f"delivery={case_result['delivery_success']}",
                file=console,
                flush=True,
            )

    aggregate = _aggregate(case_results)
    if aggregate["total_model_generate_calls"] > BASELINE_TOTAL_CALL_CAP:
        raise Phase4RemoteQwenStabilityError(
            "stability baseline exceeded the total model-call cap"
        )
    root = {
        "schema_version": STABILITY_SUMMARY_SCHEMA_VERSION,
        "run_id": selected_run_id,
        "case_set_id": CASE_SET_ID,
        "case_set_identity": case_set["case_set_identity"],
        "baseline_complete": len(case_results) == CASE_COUNT,
        "case_results": case_results,
        "aggregate": aggregate,
        "prompt_contract_revision_executed": False,
        "training_executed": False,
        "claim_boundary": (
            "P4-05 ten-case project-authored synthetic commerce stability "
            "baseline; normalization, deterministic repair, and G0 fallback "
            "remain separately accounted and never become raw-model success; "
            "not H1, browser, training, or formal-quality evidence"
        ),
        "action_state": _fresh._action_state(
            model_action=aggregate["total_model_generate_calls"] > 0,
            remote_action=aggregate["total_model_generate_calls"] > 0,
        ),
    }
    summary = {
        **root,
        "summary_identity": _fresh._identity(
            root,
            revision=STABILITY_SUMMARY_SCHEMA_VERSION,
        ),
    }
    _fresh._write_fsync(
        result_root / "stability_summary.json",
        _fresh._canonical_bytes(summary),
    )
    return summary


__all__ = [
    "BASELINE_PER_CASE_PER_NODE_CALL_CAP",
    "BASELINE_TOTAL_CALL_CAP",
    "PROMPT_CONTRACT_REVISION_CAP",
    "Phase4RemoteQwenStabilityError",
    "STABILITY_POLICY_SCHEMA_VERSION",
    "STABILITY_RUN_PREFIX",
    "STABILITY_SUMMARY_SCHEMA_VERSION",
    "prepare_phase4_remote_qwen_stability",
    "run_phase4_remote_qwen_stability",
]
