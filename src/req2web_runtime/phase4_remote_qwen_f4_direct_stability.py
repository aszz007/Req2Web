"""Run the bounded P4-05 F4-only direct-acceptance stability closure.

The experiment reuses the validated F1-F3 checkpoints from the completed
baseline and F3/F4 revision roots.  Each case may start exactly one new F4
generation under the versioned direct-acceptance prompt.  The single-case
runner remains the authority for raw capture, node validation, composition,
assembly, and downstream A-07a delivery.
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


F4_DIRECT_SCHEMA_PREFIX = (
    "req2web.phase4.p4_05.remote_qwen_stability.f4_direct_acceptance"
)
F4_DIRECT_POLICY_SCHEMA_VERSION = f"{F4_DIRECT_SCHEMA_PREFIX}.policy.v1"
F4_DIRECT_PREFLIGHT_SCHEMA_VERSION = f"{F4_DIRECT_SCHEMA_PREFIX}.preflight.v1"
F4_DIRECT_SUMMARY_SCHEMA_VERSION = f"{F4_DIRECT_SCHEMA_PREFIX}.summary.v1"
F4_DIRECT_HISTORY_BINDING_SCHEMA_VERSION = (
    f"{F4_DIRECT_SCHEMA_PREFIX}.history_binding.v1"
)
F4_DIRECT_MODE = "f4_direct_acceptance"
F4_DIRECT_RUN_PREFIX = "p4-05-remote-qwen-stability-f4-direct-"
F4_DIRECT_ROOT_MARKER = (
    ".req2web-phase4-p4-05-remote-qwen-stability-f4-direct-root"
)
F4_DIRECT_PROMPT_REVISION = (
    _fresh.P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_REVISION
)
F4_DIRECT_PROMPT_NODES = _fresh.P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_NODES
F4_DIRECT_NEW_CALL_CAP_PER_CASE = 1
F4_DIRECT_TOTAL_NEW_CALL_CAP = CASE_COUNT
F4_DIRECT_HISTORICAL_PER_CASE = {
    "F1": 1,
    "F2": 1,
    "F3": 2,
    "F4": 2,
}


class Phase4RemoteQwenF4DirectStabilityError(ValueError):
    """Raised when the bounded F4-only stability closure fails closed."""


def _require_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4RemoteQwenF4DirectStabilityError(f"{name} must be an object")
    return dict(value)


def _assert_same(actual: object, expected: object, message: str) -> None:
    if _fresh._canonical_bytes(actual) != _fresh._canonical_bytes(expected):
        raise Phase4RemoteQwenF4DirectStabilityError(message)


def _read_json(path: Path, *, required: bool = False) -> dict[str, object] | None:
    try:
        return _stability._read_json(path, required=required)
    except _stability.Phase4RemoteQwenStabilityError as exc:
        raise Phase4RemoteQwenF4DirectStabilityError(str(exc)) from exc


def _load_completed_sources(
    *,
    model_root: Path,
    integrity_evidence: Path,
    baseline_root: Path,
    predecessor_root: Path,
) -> dict[str, object]:
    try:
        baseline_prepared = _stability._load_existing_prepared(
            model_root=model_root,
            integrity_evidence=integrity_evidence,
            result_root=baseline_root,
            run_id=None,
            expected_profile_identity=None,
            expected_model_inventory_identity=None,
            parent_experiment_binding=None,
            experiment_mode="baseline",
        )
        baseline_binding = _stability._build_revision_baseline_binding(
            baseline_prepared=baseline_prepared,
        )
        predecessor_prepared = _stability._load_existing_prepared(
            model_root=model_root,
            integrity_evidence=integrity_evidence,
            result_root=predecessor_root,
            run_id=None,
            expected_profile_identity=None,
            expected_model_inventory_identity=None,
            parent_experiment_binding=None,
            experiment_mode=_stability.F3_F4_REVISION_MODE,
            prompt_revision=_stability.F3_F4_PROMPT_REVISION,
            expected_baseline_binding=baseline_binding,
        )
    except _stability.Phase4RemoteQwenStabilityError as exc:
        raise Phase4RemoteQwenF4DirectStabilityError(str(exc)) from exc

    baseline_summary = _require_mapping(
        baseline_prepared.get("summary"),
        "completed baseline summary",
    )
    predecessor_summary = _require_mapping(
        predecessor_prepared.get("summary"),
        "completed F3/F4 predecessor summary",
    )
    if baseline_summary.get("baseline_complete") is not True:
        raise Phase4RemoteQwenF4DirectStabilityError(
            "the baseline source is not complete"
        )
    if predecessor_summary.get("revision_complete") is not True:
        raise Phase4RemoteQwenF4DirectStabilityError(
            "the F3/F4 predecessor source is not complete"
        )
    if (
        baseline_summary.get("case_set_identity")
        != predecessor_summary.get("case_set_identity")
    ):
        raise Phase4RemoteQwenF4DirectStabilityError(
            "baseline and predecessor case sets drifted"
        )

    expected_total_counts = {
        node_id: CASE_COUNT * count
        for node_id, count in F4_DIRECT_HISTORICAL_PER_CASE.items()
    }
    if (
        predecessor_summary.get(
            "aggregate_per_node_generate_started_count"
        )
        != expected_total_counts
    ):
        raise Phase4RemoteQwenF4DirectStabilityError(
            "predecessor aggregate call history is not the expected "
            "F1=1/F2=1/F3=2/F4=2 per case"
        )

    profile_identity = _require_mapping(
        predecessor_summary.get("profile_identity"),
        "predecessor profile identity",
    )
    model_inventory_identity = _require_mapping(
        predecessor_summary.get("model_inventory_identity"),
        "predecessor model inventory identity",
    )
    root = {
        "baseline_result_root": str(baseline_root.resolve(strict=True)),
        "predecessor_result_root": str(predecessor_root.resolve(strict=True)),
        "baseline_summary_identity": baseline_summary.get("summary_identity"),
        "predecessor_summary_identity": predecessor_summary.get(
            "summary_identity"
        ),
        "case_set_identity": predecessor_summary.get("case_set_identity"),
        "profile_identity": profile_identity,
        "model_inventory_identity": model_inventory_identity,
        "historical_per_case_generate_started_count": dict(
            F4_DIRECT_HISTORICAL_PER_CASE
        ),
        "historical_aggregate_per_node_generate_started_count": (
            expected_total_counts
        ),
        "historical_aggregate_total_model_generate_calls": sum(
            expected_total_counts.values()
        ),
    }
    history_binding = {
        **root,
        "binding_identity": _fresh._identity(
            root,
            revision=F4_DIRECT_HISTORY_BINDING_SCHEMA_VERSION,
        ),
    }
    return {
        "baseline_prepared": baseline_prepared,
        "predecessor_prepared": predecessor_prepared,
        "baseline_summary": baseline_summary,
        "predecessor_summary": predecessor_summary,
        "history_binding": history_binding,
        "profile_identity": profile_identity,
        "model_inventory_identity": model_inventory_identity,
    }


def _create_policy(
    *,
    run_id: str,
    case_set: Mapping[str, object],
    history_binding: Mapping[str, object],
    profile_identity: Mapping[str, object],
    model_inventory_identity: Mapping[str, object],
) -> dict[str, object]:
    root = {
        "schema_version": F4_DIRECT_POLICY_SCHEMA_VERSION,
        "experiment_mode": F4_DIRECT_MODE,
        "run_id": run_id,
        "case_set_schema_version": CASE_SET_SCHEMA_VERSION,
        "case_set_id": CASE_SET_ID,
        "case_set_identity": copy.deepcopy(case_set["case_set_identity"]),
        "case_order": copy.deepcopy(case_set["case_order"]),
        "case_count": CASE_COUNT,
        "node_order": list(_fresh.NODE_ORDER),
        "prompt_revision": F4_DIRECT_PROMPT_REVISION,
        "prompt_nodes": list(F4_DIRECT_PROMPT_NODES),
        "resume_prefix": _fresh.P4_05_RESUME_PREFIX_F1_F3,
        "new_call_nodes": ["F4"],
        "new_call_cap_per_case": F4_DIRECT_NEW_CALL_CAP_PER_CASE,
        "total_new_call_cap": F4_DIRECT_TOTAL_NEW_CALL_CAP,
        "history_binding": copy.deepcopy(dict(history_binding)),
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
            "one isolated worker per case; model persistence across cases is "
            "a separate performance-maintenance slice"
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
            revision=F4_DIRECT_POLICY_SCHEMA_VERSION,
        ),
    }


def _validate_policy(
    policy: Mapping[str, object],
    *,
    expected: Mapping[str, object],
) -> dict[str, object]:
    data = dict(policy)
    if data.get("schema_version") != F4_DIRECT_POLICY_SCHEMA_VERSION:
        raise Phase4RemoteQwenF4DirectStabilityError(
            "F4-direct policy schema drifted"
        )
    identity_root = {
        key: value for key, value in data.items() if key != "policy_identity"
    }
    _assert_same(
        data.get("policy_identity"),
        _fresh._identity(
            identity_root,
            revision=F4_DIRECT_POLICY_SCHEMA_VERSION,
        ),
        "F4-direct policy identity drifted",
    )
    _assert_same(data, expected, "F4-direct policy binding drifted")
    return data


def prepare_phase4_remote_qwen_f4_direct_stability(
    *,
    model_root: Path,
    integrity_evidence: Path,
    baseline_root: Path,
    predecessor_root: Path,
    result_root: Path,
    run_id: str | None = None,
    resume_existing: bool = False,
) -> dict[str, object]:
    """Prepare or reload the bounded F4-only experiment without generation."""

    _fresh._offline_process()
    model_root = _fresh._safe_path(model_root, "model root", directory=True)
    integrity_evidence = _fresh._safe_path(
        integrity_evidence,
        "integrity evidence",
        directory=False,
    )
    baseline_root = _fresh._safe_path(
        baseline_root,
        "baseline result root",
        directory=True,
    )
    predecessor_root = _fresh._safe_path(
        predecessor_root,
        "predecessor result root",
        directory=True,
    )
    result_root = _fresh._safe_path(result_root, "result root")
    if len({baseline_root, predecessor_root, result_root}) != 3:
        raise Phase4RemoteQwenF4DirectStabilityError(
            "baseline, predecessor, and new result roots must differ"
        )

    sources = _load_completed_sources(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        baseline_root=baseline_root,
        predecessor_root=predecessor_root,
    )
    case_set = get_stability_case_set()
    _assert_same(
        case_set["case_set_identity"],
        sources["history_binding"]["case_set_identity"],
        "current stability case set drifted from the predecessor",
    )
    inventory = _fresh._remote.validate_remote_model_inventory(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
    )
    inventory_identity = _require_mapping(
        inventory.get("inventory_identity"),
        "current model inventory identity",
    )
    _assert_same(
        inventory_identity,
        sources["model_inventory_identity"],
        "current model inventory drifted from the predecessor",
    )
    runtime_facts = _fresh._remote._collect_remote_runtime_facts()
    gpu_facts = _fresh._remote._probe_remote_gpu_facts()
    if gpu_facts["device_name"] != _fresh._remote.REMOTE_DEVICE_NAME:
        raise Phase4RemoteQwenF4DirectStabilityError(
            "F4-direct experiment requires RTX 5090 GPU0"
        )
    if int(gpu_facts["total_vram_bytes"]) < _fresh._remote.REMOTE_MIN_VRAM_BYTES:
        raise Phase4RemoteQwenF4DirectStabilityError(
            "F4-direct GPU VRAM is below the RTX 5090 floor"
        )
    profile = _fresh.RemoteFreshIntegratedProfile.create(
        inventory=inventory,
        runtime_facts=runtime_facts,
        gpu_facts=gpu_facts,
    )
    profile.validate()
    profile_identity = _stability._profile_identity(profile)

    existing_policy = (
        _read_json(result_root / "stability_policy.json")
        if result_root.exists()
        else None
    )
    if existing_policy is not None:
        if not resume_existing:
            raise Phase4RemoteQwenF4DirectStabilityError(
                "existing F4-direct result root requires --resume-existing"
            )
        selected_run_id = str(existing_policy.get("run_id", ""))
        if not selected_run_id or (
            run_id is not None and run_id != selected_run_id
        ):
            raise Phase4RemoteQwenF4DirectStabilityError(
                "existing F4-direct run identity drifted"
            )
    else:
        if result_root.exists() and any(result_root.iterdir()):
            raise Phase4RemoteQwenF4DirectStabilityError(
                "F4-direct result root must be new or already owned"
            )
        if resume_existing:
            raise Phase4RemoteQwenF4DirectStabilityError(
                "--resume-existing requires an existing F4-direct result root"
            )
        selected_run_id = run_id or f"{F4_DIRECT_RUN_PREFIX}{uuid.uuid4().hex[:16]}"

    policy = _create_policy(
        run_id=selected_run_id,
        case_set=case_set,
        history_binding=_require_mapping(
            sources["history_binding"],
            "F4-direct history binding",
        ),
        profile_identity=profile_identity,
        model_inventory_identity=inventory_identity,
    )
    if existing_policy is not None:
        policy = _validate_policy(existing_policy, expected=policy)
        marker = result_root / F4_DIRECT_ROOT_MARKER
        if (
            marker.is_symlink()
            or not marker.is_file()
            or marker.read_bytes() != F4_DIRECT_ROOT_MARKER.encode("ascii")
        ):
            raise Phase4RemoteQwenF4DirectStabilityError(
                "F4-direct result-root marker is invalid"
            )
        saved_case_set = _read_json(
            result_root / "stability_case_set.json",
            required=True,
        )
        _assert_same(
            saved_case_set,
            case_set,
            "saved F4-direct case set drifted",
        )
        summary = _read_json(result_root / "stability_summary.json")
        return {
            "result_root": result_root,
            "run_id": selected_run_id,
            "case_set": case_set,
            "inventory": inventory,
            "profile": profile,
            "policy": policy,
            "history_binding": sources["history_binding"],
            "summary": summary,
        }

    preflight_root = {
        "schema_version": F4_DIRECT_PREFLIGHT_SCHEMA_VERSION,
        "experiment_mode": F4_DIRECT_MODE,
        "run_id": selected_run_id,
        "case_set_identity": copy.deepcopy(case_set["case_set_identity"]),
        "policy_identity": copy.deepcopy(policy["policy_identity"]),
        "history_binding": copy.deepcopy(sources["history_binding"]),
        "model_inventory_identity": inventory_identity,
        "predecessor_profile_identity": copy.deepcopy(
            sources["profile_identity"]
        ),
        "profile_identity": profile_identity,
        "model_root": str(model_root),
        "integrity_evidence": str(integrity_evidence),
        "result_root": str(result_root),
        "total_new_call_cap": F4_DIRECT_TOTAL_NEW_CALL_CAP,
        "action_state": _fresh._action_state(
            model_action=False,
            remote_action=False,
        ),
    }
    preflight = {
        **preflight_root,
        "preflight_identity": _fresh._identity(
            preflight_root,
            revision=F4_DIRECT_PREFLIGHT_SCHEMA_VERSION,
        ),
    }
    result_root.mkdir(parents=True, exist_ok=True)
    _fresh._write_fsync(
        result_root / F4_DIRECT_ROOT_MARKER,
        F4_DIRECT_ROOT_MARKER.encode("ascii"),
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
        "history_binding": sources["history_binding"],
        "summary": None,
    }


def run_phase4_remote_qwen_f4_direct_stability(
    *,
    model_root: Path,
    integrity_evidence: Path,
    baseline_root: Path,
    predecessor_root: Path,
    result_root: Path,
    confirm_f4_direct_stability: bool,
    run_id: str | None = None,
    console: object | None = None,
    resume_existing: bool = False,
) -> dict[str, object]:
    """Run ten cases with F1-F3 restored and one new F4 call per case."""

    if confirm_f4_direct_stability is not True:
        raise Phase4RemoteQwenF4DirectStabilityError(
            "explicit confirmation is required for F4-direct generation"
        )
    prepared = prepare_phase4_remote_qwen_f4_direct_stability(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        baseline_root=baseline_root,
        predecessor_root=predecessor_root,
        result_root=result_root,
        run_id=run_id,
        resume_existing=resume_existing,
    )
    if prepared.get("summary") is not None:
        return _require_mapping(
            prepared["summary"],
            "existing F4-direct summary",
        )

    selected_run_id = str(prepared["run_id"])
    case_set = _require_mapping(prepared["case_set"], "F4-direct case set")
    policy = _require_mapping(prepared["policy"], "F4-direct policy")
    history_binding = _require_mapping(
        prepared["history_binding"],
        "F4-direct history binding",
    )
    profile_identity = _require_mapping(
        policy["profile_identity"],
        "F4-direct profile identity",
    )
    inventory_identity = _require_mapping(
        policy["model_inventory_identity"],
        "F4-direct inventory identity",
    )
    policy_identity = _require_mapping(
        policy["policy_identity"],
        "F4-direct policy identity",
    )
    cases = case_set.get("cases")
    if not isinstance(cases, list) or len(cases) != CASE_COUNT:
        raise Phase4RemoteQwenF4DirectStabilityError(
            "F4-direct case set is invalid"
        )
    baseline_result_root = Path(
        str(history_binding["baseline_result_root"])
    ).resolve(strict=True)
    predecessor_result_root = Path(
        str(history_binding["predecessor_result_root"])
    ).resolve(strict=True)

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
            prompt_revision=F4_DIRECT_PROMPT_REVISION,
            prompt_nodes=F4_DIRECT_PROMPT_NODES,
            experiment_mode=F4_DIRECT_MODE,
            expected_historical_per_node_calls=(
                F4_DIRECT_HISTORICAL_PER_CASE
            ),
        )
    except _stability.Phase4RemoteQwenStabilityError as exc:
        raise Phase4RemoteQwenF4DirectStabilityError(str(exc)) from exc

    case_results = list(previous_results)
    cases_root = result_root / "cases"
    for index, case_value in enumerate(cases, start=1):
        case = _require_mapping(case_value, "F4-direct case")
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
        baseline_child_root = _stability._expected_child_root(
            baseline_result_root,
            index,
            case,
        )
        predecessor_child_root = _stability._expected_child_root(
            predecessor_result_root,
            index,
            case,
        )
        try:
            _stability._assert_child_root(
                cases_root=cases_root,
                child_root=child_root,
                must_exist=False,
            )
            _stability._assert_child_root(
                cases_root=baseline_result_root / "cases",
                child_root=baseline_child_root,
                must_exist=True,
            )
            _stability._assert_child_root(
                cases_root=predecessor_result_root / "cases",
                child_root=predecessor_child_root,
                must_exist=True,
            )
            _stability._validate_child_b_input(
                child_root=baseline_child_root,
                case=case,
            )
            _stability._validate_child_b_input(
                child_root=predecessor_child_root,
                case=case,
            )
        except _stability.Phase4RemoteQwenStabilityError as exc:
            raise Phase4RemoteQwenF4DirectStabilityError(str(exc)) from exc

        if index <= len(previous_results):
            if console is not None:
                print(
                    f"[P4-05-STABILITY] F4-direct case "
                    f"{index:02d}/{CASE_COUNT} {case['case_id']} "
                    "resumed from progress",
                    file=console,
                    flush=True,
                )
            continue
        if console is not None:
            print(
                f"[P4-05-STABILITY] F4-direct case "
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
                history_result_roots=(
                    baseline_child_root,
                    predecessor_child_root,
                ),
                resume_from_result_root=predecessor_child_root,
                resume_prefix=_fresh.P4_05_RESUME_PREFIX_F1_F3,
                b_input=case,
                prompt_revision=F4_DIRECT_PROMPT_REVISION,
                expected_profile_identity=profile_identity,
                expected_model_inventory_identity=inventory_identity,
                parent_experiment_binding=child_parent_binding,
            )
            if (
                not isinstance(child_result, Mapping)
                or child_result.get("status")
                not in {"delivery_terminal_success", "failed_closed"}
            ):
                raise Phase4RemoteQwenF4DirectStabilityError(
                    "F4-direct single-case runner is not terminal"
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
                prompt_revision=F4_DIRECT_PROMPT_REVISION,
                prompt_nodes=F4_DIRECT_PROMPT_NODES,
                experiment_mode=F4_DIRECT_MODE,
                baseline_binding=history_binding,
                expected_historical_per_node_calls=(
                    F4_DIRECT_HISTORICAL_PER_CASE
                ),
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
                prompt_revision=F4_DIRECT_PROMPT_REVISION,
                prompt_nodes=F4_DIRECT_PROMPT_NODES,
                experiment_mode=F4_DIRECT_MODE,
                expected_historical_per_node_calls=(
                    F4_DIRECT_HISTORICAL_PER_CASE
                ),
            )
        except _stability.Phase4RemoteQwenStabilityError as exc:
            raise Phase4RemoteQwenF4DirectStabilityError(str(exc)) from exc

        if validated_case_result["per_node_generate_calls"] != {
            "F1": 0,
            "F2": 0,
            "F3": 0,
            "F4": 1,
        }:
            raise Phase4RemoteQwenF4DirectStabilityError(
                "F4-direct case generated outside its F4-only budget"
            )
        case_results.append(validated_case_result)
        aggregate_so_far = _stability._aggregate(case_results)
        if (
            int(aggregate_so_far["total_model_generate_calls"])
            > F4_DIRECT_TOTAL_NEW_CALL_CAP
        ):
            raise Phase4RemoteQwenF4DirectStabilityError(
                "F4-direct experiment exceeded its new-call cap"
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
            print(
                f"[P4-05-STABILITY] F4-direct case "
                f"{index:02d}/{CASE_COUNT} "
                f"status={validated_case_result['status']} "
                f"f4_raw={validated_case_result['f4_raw_direct_pass']} "
                f"first_pass="
                f"{validated_case_result['downstream_first_pass_success']} "
                f"fallback={validated_case_result['g0_fallback_success']}",
                file=console,
                flush=True,
            )

    if len(case_results) != CASE_COUNT:
        raise Phase4RemoteQwenF4DirectStabilityError(
            "F4-direct experiment did not complete all cases"
        )
    aggregate = _stability._aggregate(case_results)
    if aggregate["total_model_generate_calls"] != F4_DIRECT_TOTAL_NEW_CALL_CAP:
        raise Phase4RemoteQwenF4DirectStabilityError(
            "F4-direct experiment did not use exactly one new F4 call per case"
        )
    historical_counts = dict(
        history_binding[
            "historical_aggregate_per_node_generate_started_count"
        ]
    )
    aggregate_counts = {
        node_id: historical_counts[node_id]
        + aggregate["per_node_called_count"][node_id]
        for node_id in _fresh.NODE_ORDER
    }
    quality_target_met = bool(
        aggregate["downstream_first_pass_success_count"] == CASE_COUNT
        and aggregate["repair_attempted_count"] == 0
        and aggregate["g0_fallback_count"] == 0
        and aggregate["failed_closed_count"] == 0
    )
    root = {
        "schema_version": F4_DIRECT_SUMMARY_SCHEMA_VERSION,
        "experiment_mode": F4_DIRECT_MODE,
        "run_id": selected_run_id,
        "case_set_id": CASE_SET_ID,
        "case_set_identity": copy.deepcopy(case_set["case_set_identity"]),
        "policy_identity": copy.deepcopy(policy["policy_identity"]),
        "profile_identity": profile_identity,
        "model_inventory_identity": inventory_identity,
        "history_binding": history_binding,
        "prompt_revision": F4_DIRECT_PROMPT_REVISION,
        "prompt_nodes": list(F4_DIRECT_PROMPT_NODES),
        "resume_prefix": _fresh.P4_05_RESUME_PREFIX_F1_F3,
        "revision_complete": True,
        "case_results": case_results,
        "aggregate": aggregate,
        "new_model_generate_calls": aggregate["total_model_generate_calls"],
        "reused_checkpoint_nodes": ["F1", "F2", "F3"],
        "newly_generated_nodes": ["F4"],
        "a07b_executed_count": 0,
        "historical_aggregate_per_node_generate_started_count": (
            historical_counts
        ),
        "historical_aggregate_total_model_generate_calls": sum(
            historical_counts.values()
        ),
        "aggregate_per_node_generate_started_count": aggregate_counts,
        "aggregate_total_model_generate_calls": sum(aggregate_counts.values()),
        "quality_target": {
            "downstream_first_pass_success_count": CASE_COUNT,
            "repair_attempted_count": 0,
            "g0_fallback_count": 0,
            "failed_closed_count": 0,
        },
        "quality_target_met": quality_target_met,
        "training_executed": False,
        "claim_boundary": (
            "P4-05 bounded F4-only direct-acceptance stability closure over "
            "validated F1-F3 checkpoints; exactly ten new F4 model calls, "
            "A-07a direct first-pass delivery, no A-07b execution, no retry, "
            "no truncation, and no H1, browser, training, or formal-quality "
            "claim"
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
            revision=F4_DIRECT_SUMMARY_SCHEMA_VERSION,
        ),
    }
    _fresh._write_fsync(
        result_root / "stability_summary.json",
        _fresh._canonical_bytes(summary),
    )
    return summary


__all__ = [
    "F4_DIRECT_HISTORICAL_PER_CASE",
    "F4_DIRECT_MODE",
    "F4_DIRECT_PROMPT_NODES",
    "F4_DIRECT_PROMPT_REVISION",
    "F4_DIRECT_TOTAL_NEW_CALL_CAP",
    "Phase4RemoteQwenF4DirectStabilityError",
    "prepare_phase4_remote_qwen_f4_direct_stability",
    "run_phase4_remote_qwen_f4_direct_stability",
]
