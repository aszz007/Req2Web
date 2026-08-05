"""Run and resume the bounded ten-case P4-05 stability baseline.

This module is deliberately a batch-layer wrapper around the accepted
single-case remote runner.  It owns experiment identity, progress, resume, and
aggregate accounting; the single-case runner remains the authority for the
F1-F4, composition, assembler, and downstream contracts.
"""

from __future__ import annotations

import base64
import copy
import hashlib
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
STABILITY_POLICY_SCHEMA_VERSION = f"{STABILITY_SCHEMA_PREFIX}.policy.v2"
STABILITY_PREFLIGHT_SCHEMA_VERSION = f"{STABILITY_SCHEMA_PREFIX}.preflight.v2"
STABILITY_CASE_RESULT_SCHEMA_VERSION = f"{STABILITY_SCHEMA_PREFIX}.case_result.v2"
STABILITY_PROGRESS_SCHEMA_VERSION = f"{STABILITY_SCHEMA_PREFIX}.progress.v2"
STABILITY_SUMMARY_SCHEMA_VERSION = f"{STABILITY_SCHEMA_PREFIX}.summary.v2"
STABILITY_PROFILE_BINDING_SCHEMA_VERSION = (
    _fresh.P4_05_STABILITY_PROFILE_BINDING_SCHEMA_VERSION
)
STABILITY_FAILURE_SCHEMA_VERSION = f"{STABILITY_SCHEMA_PREFIX}.failure.v1"
STABILITY_EXPERIMENT_ANCHOR_SCHEMA_VERSION = (
    f"{STABILITY_SCHEMA_PREFIX}.experiment_anchor.v1"
)
STABILITY_RUN_PREFIX = "p4-05-remote-qwen-stability-baseline-"
STABILITY_ROOT_MARKER = ".req2web-phase4-p4-05-remote-qwen-stability-root"
STABILITY_EXPERIMENT_ANCHOR_NAME = (
    ".req2web-phase4-p4-05-stability-experiment-anchor.json"
)
STABILITY_REVISION_EXPERIMENT_ANCHOR_NAME = (
    ".req2web-phase4-p4-05-stability-revision-experiment-anchor.json"
)
BASELINE_PER_CASE_PER_NODE_CALL_CAP = 1
BASELINE_TOTAL_CALL_CAP = CASE_COUNT * len(_fresh.NODE_ORDER)
PROMPT_CONTRACT_REVISION_CAP = 1
F3_F4_PROMPT_REVISION = _fresh.P4_05_F3_F4_PROMPT_REVISION
F3_F4_REVISION_MODE = "f3_f4_prompt_revision"
F3_F4_REVISION_NODES = ("F3", "F4")
F3_F4_REVISION_PER_CASE_PER_NODE_CALL_CAP = 1
F3_F4_REVISION_TOTAL_CALL_CAP = (
    CASE_COUNT * len(F3_F4_REVISION_NODES)
)
F3_F4_REVISION_RUN_PREFIX = "p4-05-remote-qwen-stability-f3-f4-revision-"
STABILITY_REVISION_BASELINE_BINDING_SCHEMA_VERSION = (
    f"{STABILITY_SCHEMA_PREFIX}.revision_baseline_binding.v1"
)
STABILITY_REVISION_PROFILE_COMPATIBILITY_SCHEMA_VERSION = (
    f"{STABILITY_SCHEMA_PREFIX}.revision_profile_compatibility.v1"
)


class Phase4RemoteQwenStabilityError(ValueError):
    """Raised when the bounded stability experiment cannot be executed safely."""


def _read_json(path: Path, *, required: bool = False) -> dict[str, object] | None:
    if path.is_symlink():
        raise Phase4RemoteQwenStabilityError(
            f"stability artifact must not be a symlink: {path}"
        )
    if not path.is_file():
        if required:
            raise Phase4RemoteQwenStabilityError(
                f"required stability artifact is missing: {path}"
            )
        return None
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase4RemoteQwenStabilityError(
            f"stability artifact is unreadable: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise Phase4RemoteQwenStabilityError(
            f"stability artifact must contain an object: {path}"
        )
    return value


def _require_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4RemoteQwenStabilityError(f"{name} must be an object")
    return dict(value)


def _copy_optional_mapping(
    value: Mapping[str, object] | None,
    name: str,
) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise Phase4RemoteQwenStabilityError(f"{name} must be an object")
    return copy.deepcopy(dict(value))


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _bare_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _same_canonical(left: object, right: object) -> bool:
    return _fresh._canonical_bytes(left) == _fresh._canonical_bytes(right)


def _assert_same_canonical(
    left: object,
    right: object,
    message: str,
) -> None:
    if not _same_canonical(left, right):
        raise Phase4RemoteQwenStabilityError(message)


def _read_required_bytes(path: Path, name: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise Phase4RemoteQwenStabilityError(f"{name} is missing")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Phase4RemoteQwenStabilityError(f"{name} is unreadable") from exc
    if not raw:
        raise Phase4RemoteQwenStabilityError(f"{name} is empty")
    return raw


def _experiment_anchor_path(
    result_root: Path,
    *,
    revision_mode: bool = False,
) -> Path:
    if revision_mode:
        return result_root / STABILITY_REVISION_EXPERIMENT_ANCHOR_NAME
    return result_root.parent / STABILITY_EXPERIMENT_ANCHOR_NAME


def _validate_experiment_anchor(
    anchor: Mapping[str, object],
    *,
    result_root: Path,
    run_id: str | None = None,
    case_set_identity: object | None = None,
    policy_identity: Mapping[str, object] | None = None,
    profile_identity: Mapping[str, object] | None = None,
    model_inventory_identity: Mapping[str, object] | None = None,
) -> dict[str, object]:
    data = dict(anchor)
    expected_keys = {
        "schema_version",
        "case_set_id",
        "case_set_identity",
        "run_id",
        "policy_identity",
        "profile_identity",
        "model_inventory_identity",
        "result_root",
        "experiment_identity",
        "anchor_identity",
    }
    if set(data) != expected_keys:
        raise Phase4RemoteQwenStabilityError(
            "stability experiment anchor shape drifted"
        )
    if data.get("schema_version") != STABILITY_EXPERIMENT_ANCHOR_SCHEMA_VERSION:
        raise Phase4RemoteQwenStabilityError(
            "stability experiment anchor schema drifted"
        )
    if data.get("case_set_id") != CASE_SET_ID:
        raise Phase4RemoteQwenStabilityError(
            "stability experiment anchor case-set ID drifted"
        )
    anchored_root = str(result_root.resolve(strict=False))
    if data.get("result_root") != anchored_root:
        raise Phase4RemoteQwenStabilityError(
            "the shared stability experiment anchor is bound to another result root"
        )
    if run_id is not None and data.get("run_id") != run_id:
        raise Phase4RemoteQwenStabilityError(
            "the shared stability experiment anchor is bound to another run"
        )
    experiment_root = {
        "case_set_id": data["case_set_id"],
        "case_set_identity": data["case_set_identity"],
        "run_id": data["run_id"],
        "policy_identity": data["policy_identity"],
        "profile_identity": data["profile_identity"],
        "model_inventory_identity": data["model_inventory_identity"],
        "result_root": data["result_root"],
    }
    _assert_same_canonical(
        data.get("experiment_identity"),
        _fresh._identity(
            experiment_root,
            revision=f"{STABILITY_EXPERIMENT_ANCHOR_SCHEMA_VERSION}.experiment",
        ),
        "stability experiment identity drifted",
    )
    anchor_root = {
        key: value for key, value in data.items() if key != "anchor_identity"
    }
    _assert_same_canonical(
        data.get("anchor_identity"),
        _fresh._identity(
            anchor_root,
            revision=STABILITY_EXPERIMENT_ANCHOR_SCHEMA_VERSION,
        ),
        "stability experiment anchor identity drifted",
    )
    for actual, expected, name in (
        (data.get("case_set_identity"), case_set_identity, "case-set identity"),
        (data.get("policy_identity"), policy_identity, "policy identity"),
        (data.get("profile_identity"), profile_identity, "profile identity"),
        (
            data.get("model_inventory_identity"),
            model_inventory_identity,
            "model inventory identity",
        ),
    ):
        if expected is not None:
            _assert_same_canonical(
                actual,
                dict(expected) if isinstance(expected, Mapping) else expected,
                f"stability experiment anchor {name} drifted",
            )
    return data


def _create_experiment_anchor(
    *,
    result_root: Path,
    run_id: str,
    case_set_identity: object,
    policy_identity: Mapping[str, object],
    profile_identity: Mapping[str, object],
    model_inventory_identity: Mapping[str, object],
    revision_mode: bool = False,
) -> dict[str, object]:
    experiment_root = {
        "case_set_id": CASE_SET_ID,
        "case_set_identity": copy.deepcopy(case_set_identity),
        "run_id": run_id,
        "policy_identity": copy.deepcopy(dict(policy_identity)),
        "profile_identity": copy.deepcopy(dict(profile_identity)),
        "model_inventory_identity": copy.deepcopy(
            dict(model_inventory_identity)
        ),
        "result_root": str(result_root.resolve(strict=False)),
    }
    root = {
        "schema_version": STABILITY_EXPERIMENT_ANCHOR_SCHEMA_VERSION,
        **experiment_root,
        "experiment_identity": _fresh._identity(
            experiment_root,
            revision=f"{STABILITY_EXPERIMENT_ANCHOR_SCHEMA_VERSION}.experiment",
        ),
    }
    anchor = {
        **root,
        "anchor_identity": _fresh._identity(
            root,
            revision=STABILITY_EXPERIMENT_ANCHOR_SCHEMA_VERSION,
        ),
    }
    _fresh._write_fsync(
        _experiment_anchor_path(result_root, revision_mode=revision_mode),
        _fresh._canonical_bytes(anchor),
    )
    return anchor


def _assert_child_root(
    *,
    cases_root: Path,
    child_root: Path,
    must_exist: bool,
) -> Path:
    if cases_root.is_symlink():
        raise Phase4RemoteQwenStabilityError("stability cases root must not be a symlink")
    if child_root.is_symlink():
        raise Phase4RemoteQwenStabilityError("stability child root must not be a symlink")
    if must_exist and not child_root.is_dir():
        raise Phase4RemoteQwenStabilityError(
            f"stability child root is missing: {child_root}"
        )
    if child_root.exists() and not child_root.is_dir():
        raise Phase4RemoteQwenStabilityError(
            f"stability child root is not a directory: {child_root}"
        )
    cases_resolved = cases_root.resolve(strict=False)
    child_resolved = child_root.resolve(strict=False)
    try:
        relative = child_resolved.relative_to(cases_resolved)
    except ValueError as exc:
        raise Phase4RemoteQwenStabilityError(
            "stability child root escapes the experiment cases directory"
        ) from exc
    if len(relative.parts) != 1:
        raise Phase4RemoteQwenStabilityError(
            "stability child root must be a direct child of the cases directory"
        )
    return child_resolved


def _expected_child_run_id(experiment_run_id: str, index: int) -> str:
    return f"{experiment_run_id}-case-{index:02d}"


def _expected_child_root(
    result_root: Path,
    index: int,
    case: Mapping[str, object],
) -> Path:
    return result_root / "cases" / f"{index:02d}-{case['case_id']}"


def _child_parent_experiment_binding(
    *,
    experiment_run_id: str,
    experiment_policy_identity: Mapping[str, object],
    index: int,
    case: Mapping[str, object],
) -> dict[str, object]:
    result = {
        "schema_version": _fresh.P4_05_PARENT_BINDING_SCHEMA_VERSION,
        "experiment_id": CASE_SET_ID,
        "experiment_run_id": experiment_run_id,
        "experiment_policy_identity": copy.deepcopy(
            dict(experiment_policy_identity)
        ),
        "case_index": index,
        "case_id": case["case_id"],
        "request_id": case["request_id"],
    }
    return result


def _validate_marker(result_root: Path) -> None:
    marker = result_root / STABILITY_ROOT_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise Phase4RemoteQwenStabilityError(
            "stability result root marker is missing"
        )
    try:
        marker_bytes = marker.read_bytes()
    except OSError as exc:
        raise Phase4RemoteQwenStabilityError(
            "stability result root marker is unreadable"
        ) from exc
    if marker_bytes != STABILITY_ROOT_MARKER.encode("ascii"):
        raise Phase4RemoteQwenStabilityError(
            "stability result root marker content drifted"
        )


def _validate_case_set_artifact(
    result_root: Path,
) -> dict[str, object]:
    case_set = _read_json(result_root / "stability_case_set.json", required=True)
    assert case_set is not None
    validated = validate_stability_case_set(copy.deepcopy(case_set))
    if (
        (result_root / "stability_case_set.json").read_bytes()
        != canonical_case_set_record_bytes(validated)
    ):
        raise Phase4RemoteQwenStabilityError(
            "stability case-set canonical bytes drifted"
        )
    return validated


def _profile_identity(profile: _fresh.RemoteFreshIntegratedProfile) -> dict[str, object]:
    stable_profile = profile.to_dict()
    stable_profile.pop("profile_id", None)
    stable_profile.pop("free_vram_bytes_at_preflight", None)
    return _fresh._identity(
        stable_profile,
        revision=STABILITY_PROFILE_BINDING_SCHEMA_VERSION,
    )


def _revision_profile_compatibility_identity(
    profile: object,
) -> dict[str, object]:
    validate = getattr(profile, "validate", None)
    to_dict = getattr(profile, "to_dict", None)
    if not callable(validate) or not callable(to_dict):
        raise Phase4RemoteQwenStabilityError(
            "revision baseline runtime profile is unavailable"
        )
    validate()
    payload = to_dict()
    if not isinstance(payload, Mapping):
        raise Phase4RemoteQwenStabilityError(
            "revision baseline runtime profile is invalid"
        )
    stable_profile = copy.deepcopy(dict(payload))
    stable_profile.pop("profile_id", None)
    stable_profile.pop("free_vram_bytes_at_preflight", None)
    stable_profile.pop("device_uuid", None)
    return _fresh._identity(
        stable_profile,
        revision=STABILITY_REVISION_PROFILE_COMPATIBILITY_SCHEMA_VERSION,
    )


def _validate_identity_match(
    actual: object,
    expected: Mapping[str, object] | None,
    name: str,
) -> dict[str, object]:
    actual_mapping = _require_mapping(actual, name)
    if expected is not None:
        _assert_same_canonical(
            actual_mapping,
            dict(expected),
            f"{name} drifted from the parent experiment",
        )
    return actual_mapping


def _validate_policy_artifact(
    policy: Mapping[str, object],
    *,
    case_set: Mapping[str, object],
    run_id: str,
    expected_profile_identity: Mapping[str, object] | None,
    expected_model_inventory_identity: Mapping[str, object] | None,
    parent_experiment_binding: Mapping[str, object] | None,
    expected_experiment_mode: str = "baseline",
    expected_prompt_revision: str | None = None,
    expected_baseline_binding: Mapping[str, object] | None = None,
) -> dict[str, object]:
    data = dict(policy)
    if data.get("schema_version") != STABILITY_POLICY_SCHEMA_VERSION:
        raise Phase4RemoteQwenStabilityError("stability policy schema drifted")
    declared_identity = data.get("policy_identity")
    identity_root = {
        key: value for key, value in data.items() if key != "policy_identity"
    }
    expected_identity = _fresh._identity(
        identity_root,
        revision=STABILITY_POLICY_SCHEMA_VERSION,
    )
    _assert_same_canonical(
        declared_identity,
        expected_identity,
        "stability policy identity drifted",
    )
    if data.get("run_id") != run_id:
        raise Phase4RemoteQwenStabilityError("stability policy run binding drifted")
    if data.get("case_set_schema_version") != CASE_SET_SCHEMA_VERSION:
        raise Phase4RemoteQwenStabilityError("stability policy case-set schema drifted")
    if data.get("case_set_id") != CASE_SET_ID:
        raise Phase4RemoteQwenStabilityError("stability policy case-set ID drifted")
    if data.get("case_set_identity") != case_set.get("case_set_identity"):
        raise Phase4RemoteQwenStabilityError(
            "stability policy case-set identity drifted"
        )
    if data.get("case_order") != case_set.get("case_order"):
        raise Phase4RemoteQwenStabilityError("stability policy case order drifted")
    if data.get("case_count") != CASE_COUNT:
        raise Phase4RemoteQwenStabilityError("stability policy case count drifted")
    if data.get("node_order") != list(_fresh.NODE_ORDER):
        raise Phase4RemoteQwenStabilityError("stability policy node order drifted")
    if data.get("baseline_per_case_per_node_call_cap") != 1:
        raise Phase4RemoteQwenStabilityError(
            "stability policy per-node call cap drifted"
        )
    if data.get("baseline_total_call_cap") != BASELINE_TOTAL_CALL_CAP:
        raise Phase4RemoteQwenStabilityError(
            "stability policy total call cap drifted"
        )
    if data.get("retry_count") != 0 or data.get("automatic_retry") is not False:
        raise Phase4RemoteQwenStabilityError("stability policy retry boundary drifted")
    if data.get("input_truncation") is not False:
        raise Phase4RemoteQwenStabilityError(
            "stability policy input truncation boundary drifted"
        )
    if data.get("output_truncation") is not False:
        raise Phase4RemoteQwenStabilityError(
            "stability policy output truncation boundary drifted"
        )
    actual_mode = data.get("experiment_mode", "baseline")
    if actual_mode != expected_experiment_mode:
        raise Phase4RemoteQwenStabilityError(
            "stability policy experiment mode drifted"
        )
    if actual_mode == F3_F4_REVISION_MODE:
        if data.get("prompt_revision") != F3_F4_PROMPT_REVISION:
            raise Phase4RemoteQwenStabilityError(
                "stability revision prompt identity drifted"
            )
        if data.get("revision_nodes") != list(F3_F4_REVISION_NODES):
            raise Phase4RemoteQwenStabilityError(
                "stability revision node selection drifted"
            )
        if (
            data.get("revision_per_case_per_node_call_cap")
            != F3_F4_REVISION_PER_CASE_PER_NODE_CALL_CAP
            or data.get("revision_total_call_cap")
            != F3_F4_REVISION_TOTAL_CALL_CAP
        ):
            raise Phase4RemoteQwenStabilityError(
                "stability revision call budget drifted"
            )
        if expected_prompt_revision != F3_F4_PROMPT_REVISION:
            raise Phase4RemoteQwenStabilityError(
                "stability revision prompt expectation is missing"
            )
        if expected_baseline_binding is not None:
            _assert_same_canonical(
                data.get("baseline_binding"),
                dict(expected_baseline_binding),
                "stability revision baseline binding drifted",
            )
    elif actual_mode != "baseline":
        raise Phase4RemoteQwenStabilityError(
            "unknown stability experiment mode"
        )
    elif expected_prompt_revision is not None:
        raise Phase4RemoteQwenStabilityError(
            "baseline policy unexpectedly contains a prompt revision"
        )
    _validate_identity_match(
        data.get("model_inventory_identity"),
        expected_model_inventory_identity,
        "stability policy model inventory identity",
    )
    _validate_identity_match(
        data.get("profile_identity"),
        expected_profile_identity,
        "stability policy profile identity",
    )
    stored_parent = data.get("parent_experiment_binding")
    if parent_experiment_binding is not None:
        _assert_same_canonical(
            stored_parent,
            dict(parent_experiment_binding),
            "stability policy parent binding drifted",
        )
    return data


def _validate_preflight_artifact(
    preflight: Mapping[str, object],
    *,
    result_root: Path,
    run_id: str,
    case_set: Mapping[str, object],
    policy: Mapping[str, object],
    model_root: Path,
    integrity_evidence: Path,
    expected_profile_identity: Mapping[str, object] | None,
    expected_model_inventory_identity: Mapping[str, object] | None,
    parent_experiment_binding: Mapping[str, object] | None,
) -> dict[str, object]:
    data = dict(preflight)
    if data.get("schema_version") != STABILITY_PREFLIGHT_SCHEMA_VERSION:
        raise Phase4RemoteQwenStabilityError("stability preflight schema drifted")
    declared_identity = data.get("preflight_identity")
    identity_root = {
        key: value for key, value in data.items() if key != "preflight_identity"
    }
    _assert_same_canonical(
        declared_identity,
        _fresh._identity(
            identity_root,
            revision=STABILITY_PREFLIGHT_SCHEMA_VERSION,
        ),
        "stability preflight identity drifted",
    )
    if data.get("run_id") != run_id:
        raise Phase4RemoteQwenStabilityError(
            "stability preflight run binding drifted"
        )
    if data.get("case_set_identity") != case_set.get("case_set_identity"):
        raise Phase4RemoteQwenStabilityError(
            "stability preflight case-set binding drifted"
        )
    if data.get("policy_identity") != policy.get("policy_identity"):
        raise Phase4RemoteQwenStabilityError(
            "stability preflight policy binding drifted"
        )
    if data.get("model_root") != str(model_root):
        raise Phase4RemoteQwenStabilityError("stability model-root binding drifted")
    if data.get("integrity_evidence") != str(integrity_evidence):
        raise Phase4RemoteQwenStabilityError(
            "stability integrity-evidence binding drifted"
        )
    if data.get("result_root") != str(result_root):
        raise Phase4RemoteQwenStabilityError(
            "stability result-root binding drifted"
        )
    _validate_identity_match(
        data.get("profile_identity"),
        expected_profile_identity,
        "stability preflight profile identity",
    )
    _validate_identity_match(
        data.get("model_inventory_identity"),
        expected_model_inventory_identity,
        "stability preflight model inventory identity",
    )
    experiment_mode = policy.get("experiment_mode", "baseline")
    if data.get("experiment_mode", "baseline") != experiment_mode:
        raise Phase4RemoteQwenStabilityError(
            "stability preflight experiment mode drifted"
        )
    if experiment_mode == F3_F4_REVISION_MODE:
        if data.get("prompt_revision") != F3_F4_PROMPT_REVISION:
            raise Phase4RemoteQwenStabilityError(
                "stability preflight prompt revision drifted"
            )
        if data.get("revision_total_call_cap") != F3_F4_REVISION_TOTAL_CALL_CAP:
            raise Phase4RemoteQwenStabilityError(
                "stability preflight revision budget drifted"
            )
        _assert_same_canonical(
            data.get("baseline_binding"),
            policy.get("baseline_binding"),
            "stability preflight baseline binding drifted",
        )
    stored_parent = data.get("parent_experiment_binding")
    if parent_experiment_binding is not None:
        _assert_same_canonical(
            stored_parent,
            dict(parent_experiment_binding),
            "stability preflight parent binding drifted",
        )
    return data


def _create_policy(
    *,
    run_id: str,
    case_set: Mapping[str, object],
    inventory: Mapping[str, object],
    profile: _fresh.RemoteFreshIntegratedProfile,
    parent_experiment_binding: Mapping[str, object] | None,
    expected_profile_identity: Mapping[str, object] | None,
    expected_model_inventory_identity: Mapping[str, object] | None,
    experiment_mode: str = "baseline",
    prompt_revision: str | None = None,
    baseline_binding: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if experiment_mode not in {"baseline", F3_F4_REVISION_MODE}:
        raise Phase4RemoteQwenStabilityError(
            "unsupported stability experiment mode"
        )
    if experiment_mode == F3_F4_REVISION_MODE:
        if prompt_revision != F3_F4_PROMPT_REVISION:
            raise Phase4RemoteQwenStabilityError(
                "F3/F4 revision requires its exact prompt revision"
            )
        if baseline_binding is None:
            raise Phase4RemoteQwenStabilityError(
                "F3/F4 revision requires a completed baseline binding"
            )
    elif prompt_revision is not None or baseline_binding is not None:
        raise Phase4RemoteQwenStabilityError(
            "baseline policy cannot carry revision-only bindings"
        )
    validated_case_set = validate_stability_case_set(copy.deepcopy(dict(case_set)))
    profile.validate()
    profile_identity = _profile_identity(profile)
    inventory_identity = _require_mapping(
        inventory.get("inventory_identity"),
        "model inventory identity",
    )
    _validate_identity_match(
        profile_identity,
        expected_profile_identity,
        "stability profile identity",
    )
    _validate_identity_match(
        inventory_identity,
        expected_model_inventory_identity,
        "stability model inventory identity",
    )
    parent_binding = _copy_optional_mapping(
        parent_experiment_binding,
        "parent experiment binding",
    )
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
        "model_inventory_identity": inventory_identity,
        "profile_identity": profile_identity,
        "parent_experiment_binding": parent_binding,
        "single_case_runner_schema_version": _fresh.P4_05_RESULT_SCHEMA_VERSION,
        "action_state": _fresh._action_state(
            model_action=False,
            remote_action=False,
        ),
    }
    if experiment_mode == F3_F4_REVISION_MODE:
        root.update(
            {
                "experiment_mode": F3_F4_REVISION_MODE,
                "prompt_revision": F3_F4_PROMPT_REVISION,
                "revision_nodes": list(F3_F4_REVISION_NODES),
                "revision_per_case_per_node_call_cap": (
                    F3_F4_REVISION_PER_CASE_PER_NODE_CALL_CAP
                ),
                "revision_total_call_cap": F3_F4_REVISION_TOTAL_CALL_CAP,
                "baseline_binding": copy.deepcopy(dict(baseline_binding)),
            }
        )
    return {
        **root,
        "policy_identity": _fresh._identity(
            root,
            revision=STABILITY_POLICY_SCHEMA_VERSION,
        ),
    }


def _load_existing_prepared(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    run_id: str | None,
    expected_profile_identity: Mapping[str, object] | None,
    expected_model_inventory_identity: Mapping[str, object] | None,
    parent_experiment_binding: Mapping[str, object] | None,
    experiment_mode: str = "baseline",
    prompt_revision: str | None = None,
    expected_baseline_binding: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if not result_root.is_dir():
        raise Phase4RemoteQwenStabilityError(
            "resume requires an existing stability result directory"
        )
    _validate_marker(result_root)
    case_set = _validate_case_set_artifact(result_root)
    inventory = _read_json(result_root / "model_inventory.json", required=True)
    profile_data = _read_json(result_root / "remote_profile.json", required=True)
    policy = _read_json(result_root / "stability_policy.json", required=True)
    preflight = _read_json(result_root / "preflight_manifest.json", required=True)
    assert inventory is not None
    assert profile_data is not None
    assert policy is not None
    assert preflight is not None
    selected_run_id = str(policy.get("run_id", ""))
    if not selected_run_id:
        raise Phase4RemoteQwenStabilityError("stability policy has no run ID")
    if run_id is not None and run_id != selected_run_id:
        raise Phase4RemoteQwenStabilityError(
            "requested run ID does not match the existing stability experiment"
        )
    profile = _fresh.RemoteFreshIntegratedProfile.from_dict(profile_data)
    validated_policy = _validate_policy_artifact(
        policy,
        case_set=case_set,
        run_id=selected_run_id,
        expected_profile_identity=expected_profile_identity,
        expected_model_inventory_identity=expected_model_inventory_identity,
        parent_experiment_binding=parent_experiment_binding,
        expected_experiment_mode=experiment_mode,
        expected_prompt_revision=prompt_revision,
        expected_baseline_binding=expected_baseline_binding,
    )
    stored_profile_identity = _require_mapping(
        validated_policy.get("profile_identity"),
        "stability policy profile identity",
    )
    stored_inventory_identity = _require_mapping(
        validated_policy.get("model_inventory_identity"),
        "stability policy model inventory identity",
    )
    _validate_preflight_artifact(
        preflight,
        result_root=result_root,
        run_id=selected_run_id,
        case_set=case_set,
        policy=validated_policy,
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        expected_profile_identity=stored_profile_identity,
        expected_model_inventory_identity=stored_inventory_identity,
        parent_experiment_binding=(
            _copy_optional_mapping(
                parent_experiment_binding,
                "parent experiment binding",
            )
            if parent_experiment_binding is not None
            else validated_policy.get("parent_experiment_binding")
        ),
    )
    anchor = _read_json(
        _experiment_anchor_path(
            result_root,
            revision_mode=experiment_mode == F3_F4_REVISION_MODE,
        ),
        required=True,
    )
    assert anchor is not None
    validated_anchor = _validate_experiment_anchor(
        anchor,
        result_root=result_root,
        run_id=selected_run_id,
        case_set_identity=case_set.get("case_set_identity"),
        policy_identity=_require_mapping(
            validated_policy.get("policy_identity"),
            "stability policy identity",
        ),
        profile_identity=stored_profile_identity,
        model_inventory_identity=stored_inventory_identity,
    )
    summary = _read_json(result_root / "stability_summary.json")
    prepared = {
        "result_root": result_root,
        "run_id": selected_run_id,
        "case_set": case_set,
        "inventory": inventory,
        "profile": profile,
        "policy": validated_policy,
        "preflight": preflight,
        "experiment_anchor": validated_anchor,
        "summary": summary,
        "experiment_mode": validated_policy.get("experiment_mode", "baseline"),
        "prompt_revision": validated_policy.get("prompt_revision"),
        "baseline_binding": validated_policy.get("baseline_binding"),
    }
    if summary is not None:
        _validate_summary(
            summary,
            result_root=result_root,
            case_set=case_set,
            policy=validated_policy,
            profile_identity=stored_profile_identity,
            model_inventory_identity=stored_inventory_identity,
            parent_experiment_binding=validated_policy.get(
                "parent_experiment_binding"
            ),
        )
    return prepared


def _build_revision_baseline_binding(
    *,
    baseline_prepared: Mapping[str, object],
) -> dict[str, object]:
    baseline_root = baseline_prepared.get("result_root")
    summary = baseline_prepared.get("summary")
    case_set = baseline_prepared.get("case_set")
    baseline_profile = baseline_prepared.get("profile")
    if not isinstance(baseline_root, Path):
        raise Phase4RemoteQwenStabilityError(
            "revision baseline result root is unavailable"
        )
    if not isinstance(summary, Mapping) or not isinstance(case_set, Mapping):
        raise Phase4RemoteQwenStabilityError(
            "revision requires a completed baseline summary"
        )
    if (
        summary.get("baseline_complete") is not True
        or summary.get("experiment_mode", "baseline") != "baseline"
    ):
        raise Phase4RemoteQwenStabilityError(
            "revision baseline root is not a completed baseline experiment"
        )
    aggregate = summary.get("aggregate")
    if not isinstance(aggregate, Mapping):
        raise Phase4RemoteQwenStabilityError(
            "revision baseline aggregate is unavailable"
        )
    expected_counts = {node_id: CASE_COUNT for node_id in _fresh.NODE_ORDER}
    if aggregate.get("per_node_called_count") != expected_counts:
        raise Phase4RemoteQwenStabilityError(
            "revision baseline must contain one call for every node in every case"
        )
    if aggregate.get("total_model_generate_calls") != BASELINE_TOTAL_CALL_CAP:
        raise Phase4RemoteQwenStabilityError(
            "revision baseline total call count is incomplete"
        )
    root = {
        "baseline_result_root": str(baseline_root.resolve(strict=False)),
        "baseline_run_id": baseline_prepared.get("run_id"),
        "baseline_case_set_identity": summary.get("case_set_identity"),
        "baseline_policy_identity": summary.get("policy_identity"),
        "baseline_summary_identity": summary.get("summary_identity"),
        "baseline_profile_identity": summary.get("profile_identity"),
        "baseline_profile_compatibility_identity": (
            _revision_profile_compatibility_identity(baseline_profile)
        ),
        "baseline_model_inventory_identity": summary.get(
            "model_inventory_identity"
        ),
        "baseline_per_node_generate_started_count": expected_counts,
        "baseline_total_model_generate_calls": BASELINE_TOTAL_CALL_CAP,
    }
    if not isinstance(root["baseline_run_id"], str) or not root["baseline_run_id"]:
        raise Phase4RemoteQwenStabilityError(
            "revision baseline run identity is invalid"
        )
    binding = {
        **root,
        "binding_identity": _fresh._identity(
            root,
            revision=STABILITY_REVISION_BASELINE_BINDING_SCHEMA_VERSION,
        ),
    }
    return binding


def _validate_revision_baseline_binding(
    binding: Mapping[str, object],
    *,
    baseline_prepared: Mapping[str, object],
) -> dict[str, object]:
    expected = _build_revision_baseline_binding(
        baseline_prepared=baseline_prepared,
    )
    _assert_same_canonical(
        binding,
        expected,
        "revision baseline binding drifted",
    )
    return dict(expected)


def prepare_phase4_remote_qwen_stability(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    run_id: str | None = None,
    resume_existing: bool = False,
    expected_profile_identity: Mapping[str, object] | None = None,
    expected_model_inventory_identity: Mapping[str, object] | None = None,
    parent_experiment_binding: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Write a new preflight or load an existing experiment without generation."""

    _fresh._offline_process()
    model_root = _fresh._safe_path(model_root, "model root", directory=True)
    integrity_evidence = _fresh._safe_path(
        integrity_evidence,
        "integrity evidence",
        directory=False,
    )
    result_root = _fresh._safe_path(result_root, "result root")

    anchor_path = _experiment_anchor_path(result_root)
    existing_anchor = _read_json(anchor_path)
    if existing_anchor is not None:
        _validate_experiment_anchor(
            existing_anchor,
            result_root=result_root,
            run_id=run_id,
        )
        if not result_root.is_dir():
            raise Phase4RemoteQwenStabilityError(
                "the anchored stability result root is missing and cannot be restarted"
            )

    if result_root.exists():
        if result_root.is_symlink() or not result_root.is_dir():
            raise Phase4RemoteQwenStabilityError(
                "stability result root must be a directory"
            )
        if existing_anchor is not None:
            return _load_existing_prepared(
                model_root=model_root,
                integrity_evidence=integrity_evidence,
                result_root=result_root,
                run_id=run_id,
                expected_profile_identity=expected_profile_identity,
                expected_model_inventory_identity=expected_model_inventory_identity,
                parent_experiment_binding=parent_experiment_binding,
            )
        summary_path = result_root / "stability_summary.json"
        if summary_path.exists():
            return _load_existing_prepared(
                model_root=model_root,
                integrity_evidence=integrity_evidence,
                result_root=result_root,
                run_id=run_id,
                expected_profile_identity=expected_profile_identity,
                expected_model_inventory_identity=expected_model_inventory_identity,
                parent_experiment_binding=parent_experiment_binding,
            )
        if resume_existing:
            return _load_existing_prepared(
                model_root=model_root,
                integrity_evidence=integrity_evidence,
                result_root=result_root,
                run_id=run_id,
                expected_profile_identity=expected_profile_identity,
                expected_model_inventory_identity=expected_model_inventory_identity,
                parent_experiment_binding=parent_experiment_binding,
            )
        if any(result_root.iterdir()):
            raise Phase4RemoteQwenStabilityError(
                "existing stability result root requires --resume-existing"
            )
    elif resume_existing:
        raise Phase4RemoteQwenStabilityError(
            "--resume-existing requires an existing stability result root"
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
        parent_experiment_binding=parent_experiment_binding,
        expected_profile_identity=expected_profile_identity,
        expected_model_inventory_identity=expected_model_inventory_identity,
    )
    profile_identity = _profile_identity(profile)
    profile_compatibility_identity = (
        _revision_profile_compatibility_identity(profile)
    )
    inventory_identity = _require_mapping(
        inventory.get("inventory_identity"),
        "model inventory identity",
    )
    parent_binding = policy["parent_experiment_binding"]
    preflight_root = {
        "schema_version": STABILITY_PREFLIGHT_SCHEMA_VERSION,
        "run_id": selected_run_id,
        "case_set_identity": case_set["case_set_identity"],
        "policy_identity": policy["policy_identity"],
        "model_inventory_identity": inventory_identity,
        "profile_identity": profile_identity,
        "parent_experiment_binding": parent_binding,
        "model_root": str(model_root),
        "integrity_evidence": str(integrity_evidence),
        "result_root": str(result_root),
        "baseline_total_call_cap": BASELINE_TOTAL_CALL_CAP,
        "action_state": _fresh._action_state(
            model_action=False,
            remote_action=False,
        ),
    }
    preflight = {
        **preflight_root,
        "preflight_identity": _fresh._identity(
            preflight_root,
            revision=STABILITY_PREFLIGHT_SCHEMA_VERSION,
        ),
    }
    anchor = _create_experiment_anchor(
        result_root=result_root,
        run_id=selected_run_id,
        case_set_identity=case_set.get("case_set_identity"),
        policy_identity=_require_mapping(
            policy.get("policy_identity"),
            "stability policy identity",
        ),
        profile_identity=profile_identity,
        model_inventory_identity=inventory_identity,
    )

    result_root.mkdir(parents=True, exist_ok=True)
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
        "experiment_anchor": anchor,
        "summary": None,
    }


def prepare_phase4_remote_qwen_f3_f4_prompt_revision(
    *,
    model_root: Path,
    integrity_evidence: Path,
    baseline_root: Path,
    result_root: Path,
    run_id: str | None = None,
    resume_existing: bool = False,
    expected_profile_identity: Mapping[str, object] | None = None,
    expected_model_inventory_identity: Mapping[str, object] | None = None,
    parent_experiment_binding: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Prepare or load the explicit F3/F4 prompt-revision experiment."""

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
    result_root = _fresh._safe_path(result_root, "result root")
    if baseline_root == result_root:
        raise Phase4RemoteQwenStabilityError(
            "revision result root must differ from the completed baseline root"
        )

    baseline_prepared = _load_existing_prepared(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        result_root=baseline_root,
        run_id=None,
        expected_profile_identity=expected_profile_identity,
        expected_model_inventory_identity=expected_model_inventory_identity,
        parent_experiment_binding=None,
        experiment_mode="baseline",
    )
    baseline_binding = _build_revision_baseline_binding(
        baseline_prepared=baseline_prepared,
    )

    revision_anchor_path = _experiment_anchor_path(
        result_root,
        revision_mode=True,
    )
    existing_anchor = _read_json(revision_anchor_path)
    if existing_anchor is not None:
        _validate_experiment_anchor(
            existing_anchor,
            result_root=result_root,
            run_id=run_id,
        )
        if not result_root.is_dir():
            raise Phase4RemoteQwenStabilityError(
                "the anchored revision result root is missing"
            )

    if result_root.exists():
        if result_root.is_symlink() or not result_root.is_dir():
            raise Phase4RemoteQwenStabilityError(
                "revision result root must be a directory"
            )
        if existing_anchor is not None or resume_existing:
            return _load_existing_prepared(
                model_root=model_root,
                integrity_evidence=integrity_evidence,
                result_root=result_root,
                run_id=run_id,
                expected_profile_identity=expected_profile_identity,
                expected_model_inventory_identity=expected_model_inventory_identity,
                parent_experiment_binding=parent_experiment_binding,
                experiment_mode=F3_F4_REVISION_MODE,
                prompt_revision=F3_F4_PROMPT_REVISION,
                expected_baseline_binding=baseline_binding,
            )
        if any(result_root.iterdir()):
            raise Phase4RemoteQwenStabilityError(
                "existing revision result root requires --resume-existing"
            )
    else:
        if resume_existing:
            raise Phase4RemoteQwenStabilityError(
                "--resume-existing requires an existing revision result root"
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
            "P4-05 F3/F4 revision requires RTX 5090 GPU0"
        )
    if int(gpu_facts["total_vram_bytes"]) < _fresh._remote.REMOTE_MIN_VRAM_BYTES:
        raise Phase4RemoteQwenStabilityError(
            "P4-05 F3/F4 revision GPU VRAM is below the RTX 5090 floor"
        )
    profile = _fresh.RemoteFreshIntegratedProfile.create(
        inventory=inventory,
        runtime_facts=runtime_facts,
        gpu_facts=gpu_facts,
    )
    profile_identity = _profile_identity(profile)
    inventory_identity = _require_mapping(
        inventory.get("inventory_identity"),
        "model inventory identity",
    )
    _assert_same_canonical(
        profile_compatibility_identity,
        baseline_binding["baseline_profile_compatibility_identity"],
        "revision profile compatibility drifted from baseline",
    )
    _assert_same_canonical(
        inventory_identity,
        baseline_binding["baseline_model_inventory_identity"],
        "revision model inventory identity drifted from baseline",
    )
    _validate_identity_match(
        profile_identity,
        expected_profile_identity,
        "stability revision profile identity",
    )
    _validate_identity_match(
        inventory_identity,
        expected_model_inventory_identity,
        "stability revision model inventory identity",
    )
    selected_run_id = run_id or f"{F3_F4_REVISION_RUN_PREFIX}{uuid.uuid4().hex[:16]}"
    policy = _create_policy(
        run_id=selected_run_id,
        case_set=case_set,
        inventory=inventory,
        profile=profile,
        parent_experiment_binding=parent_experiment_binding,
        expected_profile_identity=expected_profile_identity,
        expected_model_inventory_identity=expected_model_inventory_identity,
        experiment_mode=F3_F4_REVISION_MODE,
        prompt_revision=F3_F4_PROMPT_REVISION,
        baseline_binding=baseline_binding,
    )
    parent_binding = policy["parent_experiment_binding"]
    preflight_root = {
        "schema_version": STABILITY_PREFLIGHT_SCHEMA_VERSION,
        "experiment_mode": F3_F4_REVISION_MODE,
        "prompt_revision": F3_F4_PROMPT_REVISION,
        "baseline_result_root": baseline_binding["baseline_result_root"],
        "baseline_binding": baseline_binding,
        "run_id": selected_run_id,
        "case_set_identity": case_set["case_set_identity"],
        "policy_identity": policy["policy_identity"],
        "model_inventory_identity": inventory_identity,
        "profile_identity": profile_identity,
        "profile_compatibility_identity": profile_compatibility_identity,
        "parent_experiment_binding": parent_binding,
        "model_root": str(model_root),
        "integrity_evidence": str(integrity_evidence),
        "result_root": str(result_root),
        "revision_total_call_cap": F3_F4_REVISION_TOTAL_CALL_CAP,
        "action_state": _fresh._action_state(
            model_action=False,
            remote_action=False,
        ),
    }
    preflight = {
        **preflight_root,
        "preflight_identity": _fresh._identity(
            preflight_root,
            revision=STABILITY_PREFLIGHT_SCHEMA_VERSION,
        ),
    }
    anchor = _create_experiment_anchor(
        result_root=result_root,
        run_id=selected_run_id,
        case_set_identity=case_set.get("case_set_identity"),
        policy_identity=_require_mapping(
            policy.get("policy_identity"),
            "stability revision policy identity",
        ),
        profile_identity=profile_identity,
        model_inventory_identity=inventory_identity,
        revision_mode=True,
    )
    result_root.mkdir(parents=True, exist_ok=True)
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
        "experiment_anchor": anchor,
        "summary": None,
        "experiment_mode": F3_F4_REVISION_MODE,
        "prompt_revision": F3_F4_PROMPT_REVISION,
        "baseline_binding": baseline_binding,
    }


def _validate_child_binding(
    value: Mapping[str, object],
    *,
    child_run_id: str,
    case: Mapping[str, object],
    expected_profile_identity: Mapping[str, object] | None,
    expected_model_inventory_identity: Mapping[str, object] | None,
    parent_experiment_binding: Mapping[str, object] | None,
    name: str,
    required_keys: tuple[str, ...] = (),
) -> None:
    data = dict(value)
    missing = [key for key in required_keys if key not in data]
    if missing:
        raise Phase4RemoteQwenStabilityError(
            f"{name} is missing required bindings: {', '.join(missing)}"
        )
    for key, expected in (
        ("run_id", child_run_id),
        ("case_id", case["case_id"]),
        ("request_id", case["request_id"]),
    ):
        if key in data and data[key] != expected:
            raise Phase4RemoteQwenStabilityError(
                f"{name} {key} binding drifted"
            )
    if (
        expected_profile_identity is not None
        and "profile_identity" in data
        and expected_profile_identity.get("revision")
        != STABILITY_PROFILE_BINDING_SCHEMA_VERSION
    ):
        _assert_same_canonical(
            data["profile_identity"],
            dict(expected_profile_identity),
            f"{name} profile identity drifted",
        )
    if (
        expected_model_inventory_identity is not None
        and "model_inventory_identity" in data
    ):
        _assert_same_canonical(
            data["model_inventory_identity"],
            dict(expected_model_inventory_identity),
            f"{name} model inventory identity drifted",
        )
    if parent_experiment_binding is not None and "parent_experiment_binding" in data:
        _assert_same_canonical(
            data["parent_experiment_binding"],
            dict(parent_experiment_binding),
            f"{name} parent binding drifted",
        )


def _validate_child_b_input(
    *,
    child_root: Path,
    case: Mapping[str, object],
) -> dict[str, object]:
    path = child_root / "b_input.json"
    value = _read_json(path, required=True)
    assert value is not None
    try:
        validated = _fresh.validate_b_input(copy.deepcopy(dict(case)))
    except ValueError as exc:
        raise Phase4RemoteQwenStabilityError(
            "child frozen B input failed its owning validator"
        ) from exc
    expected_bytes = _fresh._canonical_bytes(case)
    if path.read_bytes() != expected_bytes:
        raise Phase4RemoteQwenStabilityError(
            "child frozen B input canonical bytes drifted from the stability case"
        )
    _assert_same_canonical(
        value,
        validated,
        "child frozen B input drifted from the stability case",
    )
    return dict(validated)


def _read_attempt_evidence(
    child_root: Path,
    *,
    child_run_id: str,
    case: Mapping[str, object],
    expected_profile_identity: Mapping[str, object] | None,
    expected_model_inventory_identity: Mapping[str, object] | None,
    parent_experiment_binding: Mapping[str, object] | None,
    expected_prompt_revision: str | None = None,
) -> tuple[
    dict[str, int],
    dict[str, bool],
    dict[str, str | None],
    dict[str, int],
]:
    attempts_root = child_root / "attempts"
    calls = {node_id: 0 for node_id in _fresh.NODE_ORDER}
    raw_pass = {node_id: False for node_id in _fresh.NODE_ORDER}
    failure_codes: dict[str, str | None] = {
        node_id: None for node_id in _fresh.NODE_ORDER
    }
    attempt_envelopes = {node_id: 0 for node_id in _fresh.NODE_ORDER}
    if not attempts_root.exists():
        return calls, raw_pass, failure_codes, attempt_envelopes
    if attempts_root.is_symlink() or not attempts_root.is_dir():
        raise Phase4RemoteQwenStabilityError(
            "stability attempts root is invalid"
        )
    for entry in attempts_root.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            raise Phase4RemoteQwenStabilityError(
                "stability attempts inventory is invalid"
            )
        if entry.name not in _fresh.NODE_ORDER:
            raise Phase4RemoteQwenStabilityError(
                "stability attempts contain an unknown node"
            )
        attempt = _read_json(entry / "attempt_result.json", required=True)
        pre_call = _read_json(entry / "pre_call_record.json", required=True)
        assert attempt is not None
        assert pre_call is not None
        attempt_envelopes[entry.name] = 1
        _validate_child_binding(
            attempt,
            child_run_id=child_run_id,
            case=case,
            expected_profile_identity=expected_profile_identity,
            expected_model_inventory_identity=expected_model_inventory_identity,
            parent_experiment_binding=parent_experiment_binding,
            name=f"{entry.name} attempt result",
            required_keys=(
                "run_id",
                "case_id",
                "request_id",
                "parent_experiment_binding",
            ),
        )
        if attempt.get("node_id") != entry.name:
            raise Phase4RemoteQwenStabilityError(
                "stability attempt node binding drifted"
            )
        _validate_child_binding(
            pre_call,
            child_run_id=child_run_id,
            case=case,
            expected_profile_identity=expected_profile_identity,
            expected_model_inventory_identity=expected_model_inventory_identity,
            parent_experiment_binding=parent_experiment_binding,
            name=f"{entry.name} pre-call record",
            required_keys=(
                "run_id",
                "case_id",
                "request_id",
                "profile_identity",
                "parent_experiment_binding",
            ),
        )
        if pre_call.get("node_id") != entry.name:
            raise Phase4RemoteQwenStabilityError(
                "stability pre-call node binding drifted"
            )
        _assert_same_canonical(
            attempt.get("pre_call_identity"),
            _fresh._identity(
                pre_call,
                revision=_fresh.P4_05_PRE_CALL_SCHEMA_VERSION,
            ),
            "stability attempt pre-call identity drifted",
        )
        call_count = attempt.get("call_count", 0)
        if type(call_count) is not int or call_count not in {0, 1}:
            raise Phase4RemoteQwenStabilityError(
                "stability attempt call count exceeds the baseline cap"
            )
        generate_started = attempt.get("generate_started")
        if type(generate_started) is not bool or int(generate_started) != call_count:
            raise Phase4RemoteQwenStabilityError(
                "stability attempt generate-start accounting drifted"
            )
        if attempt.get("status") not in {"validated", "failed_closed"}:
            raise Phase4RemoteQwenStabilityError(
                "stability attempt is not terminal"
            )
        if attempt.get("automatic_retry") is not False:
            raise Phase4RemoteQwenStabilityError(
                "stability attempt automatic-retry boundary drifted"
            )
        if attempt.get("retry_count") != 0:
            raise Phase4RemoteQwenStabilityError(
                "stability attempt retry count drifted"
            )
        identity_artifacts = (
            (
                "input.json",
                "input_identity",
                _fresh.P4_05_INPUT_SCHEMA_VERSION,
            ),
            (
                "prompt.json",
                "prompt_identity",
                _fresh.P4_05_PROMPT_SCHEMA_VERSION,
            ),
            (
                "config.json",
                "config_identity",
                f"{_fresh.P4_05_SCHEMA_PREFIX}.config.v1",
            ),
            (
                "request.json",
                "request_identity",
                f"{_fresh.P4_05_SCHEMA_PREFIX}.request.v1",
            ),
        )
        for filename, identity_key, revision in identity_artifacts:
            raw = _read_required_bytes(
                entry / filename,
                f"{entry.name} {filename}",
            )
            expected_identity = _fresh._identity(
                raw,
                revision=revision,
                identity_kind="raw_bytes",
            )
            _assert_same_canonical(
                attempt.get(identity_key),
                expected_identity,
                f"stability attempt {identity_key} drifted",
            )
            _assert_same_canonical(
                pre_call.get(identity_key),
                expected_identity,
                f"stability pre-call {identity_key} drifted",
            )
        prompt_value = _read_json(
            entry / "prompt.json",
            required=True,
        )
        assert prompt_value is not None
        expected_node_prompt_revision = (
            expected_prompt_revision
            if entry.name in F3_F4_REVISION_NODES
            else None
        )
        if prompt_value.get("prompt_revision") != expected_node_prompt_revision:
            raise Phase4RemoteQwenStabilityError(
                "stability attempt prompt revision binding drifted"
            )
        for record, name in ((attempt, "attempt"), (pre_call, "pre-call")):
            if record.get("prompt_revision") != expected_node_prompt_revision:
                raise Phase4RemoteQwenStabilityError(
                    f"stability {name} prompt revision binding drifted"
                )
        raw_path = entry / "raw_response.bin"
        if raw_path.exists():
            raw = _read_required_bytes(
                raw_path,
                f"{entry.name} raw response",
            )
            if call_count != 1:
                raise Phase4RemoteQwenStabilityError(
                    "stability raw response has no counted generate call"
                )
            _assert_same_canonical(
                attempt.get("raw_identity"),
                _fresh._identity(
                    raw,
                    revision=f"{_fresh.P4_05_SCHEMA_PREFIX}.raw.v1",
                    identity_kind="raw_bytes",
                ),
                "stability attempt raw identity drifted",
            )
        elif attempt.get("raw_identity") is not None:
            raise Phase4RemoteQwenStabilityError(
                "stability attempt declares raw bytes that are unavailable"
            )
        validated_output = entry / "validated_node_output.json"
        if attempt.get("status") == "validated":
            _read_required_bytes(
                validated_output,
                f"{entry.name} validated node output",
            )
        elif validated_output.exists():
            raise Phase4RemoteQwenStabilityError(
                "failed stability attempt contains a validated output"
            )
        for trace in entry.iterdir():
            if trace.is_symlink():
                raise Phase4RemoteQwenStabilityError(
                    "stability attempt trace must not be a symlink"
                )
            trace_name = trace.name.lower()
            if (
                trace.is_file()
                and ("stream" in trace_name or "generation" in trace_name)
                and call_count != 1
            ):
                raise Phase4RemoteQwenStabilityError(
                    "stability stream/generation trace has no counted generate call"
                )
        calls[entry.name] = call_count
        raw_pass[entry.name] = attempt.get("status") == "validated"
        failure = attempt.get("failure_code")
        failure_codes[entry.name] = (
            None if failure is None else str(failure)
        )
    return calls, raw_pass, failure_codes, attempt_envelopes


def _validate_ledger(
    ledger: Mapping[str, object],
    *,
    child_run_id: str,
    attempt_calls: Mapping[str, int],
    attempt_envelopes: Mapping[str, int],
) -> dict[str, int]:
    data = dict(ledger)
    if data.get("schema_version") != _fresh.P4_05_LEDGER_SCHEMA_VERSION:
        raise Phase4RemoteQwenStabilityError(
            "stability model ledger schema drifted"
        )
    if data.get("pilot_id") != _fresh.P4_05_PILOT_ID:
        raise Phase4RemoteQwenStabilityError(
            "stability model ledger pilot binding drifted"
        )
    if data.get("run_id") != child_run_id:
        raise Phase4RemoteQwenStabilityError(
            "stability model ledger run binding drifted"
        )
    if data.get("node_order") != list(_fresh.NODE_ORDER):
        raise Phase4RemoteQwenStabilityError(
            "stability model ledger node order drifted"
        )
    per_node = data.get("per_node")
    if not isinstance(per_node, Mapping):
        raise Phase4RemoteQwenStabilityError("stability model ledger is invalid")
    if set(per_node) != set(_fresh.NODE_ORDER):
        raise Phase4RemoteQwenStabilityError(
            "stability model ledger node inventory drifted"
        )
    calls: dict[str, int] = {}
    for node_id in _fresh.NODE_ORDER:
        row = per_node[node_id]
        if not isinstance(row, Mapping):
            raise Phase4RemoteQwenStabilityError(
                "stability model ledger row is invalid"
            )
        count = row.get("generate_started_count")
        if type(count) is not int or count not in {0, 1}:
            raise Phase4RemoteQwenStabilityError(
                "stability model ledger node cap was exceeded"
            )
        attempt_count = row.get("attempt_count")
        if (
            type(attempt_count) is not int
            or attempt_count not in {0, 1}
            or attempt_count != int(attempt_envelopes[node_id])
        ):
            raise Phase4RemoteQwenStabilityError(
                "stability model ledger attempt count drifted"
            )
        if (
            row.get("generate_call_cap") != 1
            or row.get("retry_count") != 0
        ):
            raise Phase4RemoteQwenStabilityError(
                "stability model ledger node policy drifted"
            )
        if count != int(attempt_calls[node_id]):
            raise Phase4RemoteQwenStabilityError(
                "stability model ledger disagrees with attempt evidence"
            )
        calls[node_id] = count
    total = data.get("total_generate_calls")
    if type(total) is not int or total != sum(calls.values()):
        raise Phase4RemoteQwenStabilityError(
            "stability model ledger total drifted"
        )
    if data.get("automatic_retry") is not False or data.get("budget_reset") is not False:
        raise Phase4RemoteQwenStabilityError(
            "stability model ledger retry/reset boundary drifted"
        )
    if sum(calls.values()) > len(_fresh.NODE_ORDER):
        raise Phase4RemoteQwenStabilityError(
            "stability child exceeded its four-call budget"
        )
    return calls


def _validate_aggregate_ledger(
    aggregate_ledger: Mapping[str, object],
    *,
    child_run_id: str,
    calls: Mapping[str, int],
    ledger: Mapping[str, object],
    historical_counts: Mapping[str, int] | None = None,
) -> None:
    data = dict(aggregate_ledger)
    expected_historical = (
        {node_id: 0 for node_id in _fresh.NODE_ORDER}
        if historical_counts is None
        else dict(historical_counts)
    )
    if set(expected_historical) != set(_fresh.NODE_ORDER) or any(
        type(expected_historical[node_id]) is not int
        or expected_historical[node_id] < 0
        for node_id in _fresh.NODE_ORDER
    ):
        raise Phase4RemoteQwenStabilityError(
            "stability aggregate historical call input is invalid"
        )
    if data.get("schema_version") != _fresh.P4_05_AGGREGATE_LEDGER_SCHEMA_VERSION:
        raise Phase4RemoteQwenStabilityError(
            "stability aggregate ledger schema drifted"
        )
    if data.get("pilot_id") != _fresh.P4_05_PILOT_ID:
        raise Phase4RemoteQwenStabilityError(
            "stability aggregate ledger pilot binding drifted"
        )
    if data.get("run_id") != child_run_id:
        raise Phase4RemoteQwenStabilityError(
            "stability aggregate ledger run binding drifted"
        )
    _assert_same_canonical(
        data.get("current_ledger_identity"),
        _fresh._identity(
            ledger,
            revision=_fresh.P4_05_LEDGER_SCHEMA_VERSION,
        ),
        "stability aggregate ledger current-ledger identity drifted",
    )
    per_node = data.get("per_node")
    if not isinstance(per_node, Mapping) or set(per_node) != set(_fresh.NODE_ORDER):
        raise Phase4RemoteQwenStabilityError(
            "stability aggregate ledger node inventory drifted"
        )
    for node_id in _fresh.NODE_ORDER:
        row = per_node[node_id]
        if not isinstance(row, Mapping):
            raise Phase4RemoteQwenStabilityError(
                "stability aggregate ledger row is invalid"
            )
        if row.get("historical_generate_started_count") != expected_historical[node_id]:
            raise Phase4RemoteQwenStabilityError(
                "stability aggregate historical call count drifted"
            )
        if row.get("current_generate_started_count") != calls[node_id]:
            raise Phase4RemoteQwenStabilityError(
                "stability aggregate ledger current count drifted"
            )
        if row.get("aggregate_generate_started_count") != (
            expected_historical[node_id] + calls[node_id]
        ):
            raise Phase4RemoteQwenStabilityError(
                "stability aggregate ledger total count drifted"
            )
        if (
            row.get("total_real_model_call_cap")
            != _fresh.P4_05_TOTAL_REAL_MODEL_CALL_CAP_PER_NODE
        ):
            raise Phase4RemoteQwenStabilityError(
                "stability aggregate ledger node cap drifted"
            )
    if data.get("aggregate_total_generate_calls") != sum(
        expected_historical[node_id] + calls[node_id]
        for node_id in _fresh.NODE_ORDER
    ):
        raise Phase4RemoteQwenStabilityError(
            "stability aggregate ledger total drifted"
        )
    if data.get("automatic_retry") is not False or data.get("budget_reset") is not False:
        raise Phase4RemoteQwenStabilityError(
            "stability aggregate ledger retry/reset boundary drifted"
        )


def _validate_supervisor_receipt(
    supervisor: Mapping[str, object],
    *,
    child_run_id: str,
    calls: Mapping[str, int],
    attempt_envelopes: Mapping[str, int],
    ledger: Mapping[str, object],
    aggregate_ledger: Mapping[str, object],
    parent_experiment_binding: Mapping[str, object],
) -> None:
    data = dict(supervisor)
    if data.get("schema_version") != _fresh.P4_05_SUPERVISOR_SCHEMA_VERSION:
        raise Phase4RemoteQwenStabilityError(
            "stability supervisor receipt schema drifted"
        )
    if data.get("pilot_id") != _fresh.P4_05_PILOT_ID:
        raise Phase4RemoteQwenStabilityError(
            "stability supervisor receipt pilot binding drifted"
        )
    if data.get("run_id") != child_run_id:
        raise Phase4RemoteQwenStabilityError(
            "stability supervisor receipt run binding drifted"
        )
    _assert_same_canonical(
        data.get("parent_experiment_binding"),
        dict(parent_experiment_binding),
        "stability supervisor receipt parent binding drifted",
    )
    generate_calls = data.get("generate_calls")
    envelopes = data.get("attempt_envelopes")
    if (
        not isinstance(generate_calls, Mapping)
        or set(generate_calls) != set(_fresh.NODE_ORDER)
        or not isinstance(envelopes, Mapping)
        or set(envelopes) != set(_fresh.NODE_ORDER)
    ):
        raise Phase4RemoteQwenStabilityError(
            "stability supervisor receipt call inventory drifted"
        )
    for node_id in _fresh.NODE_ORDER:
        if generate_calls[node_id] != calls[node_id]:
            raise Phase4RemoteQwenStabilityError(
                "stability supervisor receipt generate count drifted"
            )
        if envelopes[node_id] != attempt_envelopes[node_id]:
            raise Phase4RemoteQwenStabilityError(
                "stability supervisor receipt attempt count drifted"
            )
    _assert_same_canonical(
        data.get("model_call_ledger_identity"),
        _fresh._identity(
            ledger,
            revision=_fresh.P4_05_LEDGER_SCHEMA_VERSION,
        ),
        "stability supervisor model-ledger identity drifted",
    )
    _assert_same_canonical(
        data.get("aggregate_model_call_ledger_identity"),
        _fresh._identity(
            aggregate_ledger,
            revision=_fresh.P4_05_AGGREGATE_LEDGER_SCHEMA_VERSION,
        ),
        "stability supervisor aggregate-ledger identity drifted",
    )
    terminal_status = data.get("terminal_status")
    if terminal_status not in {
        "normal_completed",
        "generation_timeout",
        "worker_failed",
        "worker_not_started",
    }:
        raise Phase4RemoteQwenStabilityError(
            "stability supervisor receipt is not terminal"
        )
    if type(data.get("generation_started")) is not bool:
        raise Phase4RemoteQwenStabilityError(
            "stability supervisor generation-start state drifted"
        )
    worker_id = data.get("worker_id")
    if worker_id is None:
        if (
            terminal_status != "worker_not_started"
            or data.get("generation_started") is not False
            or any(calls.values())
            or any(attempt_envelopes.values())
        ):
            raise Phase4RemoteQwenStabilityError(
                "stability supervisor worker-not-started evidence drifted"
            )
        return
    if (
        not isinstance(worker_id, str)
        or not worker_id
        or type(data.get("worker_pid")) is not int
        or data.get("worker_exit_verified") is not True
        or data.get("stdout_thread_joined") is not True
        or data.get("stderr_thread_joined") is not True
        or data.get("stderr_capture_completed") is not True
    ):
        raise Phase4RemoteQwenStabilityError(
            "stability supervisor teardown evidence is incomplete"
        )


def _validate_source_result_bindings(
    value: Mapping[str, object] | None,
    *,
    name: str,
    child_run_id: str,
    case: Mapping[str, object],
    expected_profile_identity: Mapping[str, object] | None,
    expected_model_inventory_identity: Mapping[str, object] | None,
    parent_experiment_binding: Mapping[str, object] | None,
) -> None:
    if value is None:
        return
    _validate_child_binding(
        value,
        child_run_id=child_run_id,
        case=case,
        expected_profile_identity=expected_profile_identity,
        expected_model_inventory_identity=expected_model_inventory_identity,
        parent_experiment_binding=parent_experiment_binding,
        name=name,
        required_keys=(
            "run_id",
            "case_id",
            "request_id",
            "parent_experiment_binding",
        ),
    )


def _validated_candidate_bytes(
    candidate: Mapping[str, object] | None,
) -> bytes | None:
    if candidate is None:
        return None
    if (
        candidate.get("candidate_projection_status")
        != "candidate_composition_validated_only"
        or candidate.get("failure") is not None
    ):
        return None
    encoded = candidate.get("model_semantic_candidate_canonical_b64")
    if not isinstance(encoded, str):
        return None
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        return None
    if (
        candidate.get("model_semantic_candidate_byte_length") != len(raw)
        or candidate.get("model_semantic_candidate_sha256")
        != f"sha256:{_bare_sha256(raw)}"
    ):
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if _fresh._canonical_bytes(parsed) != raw:
        return None
    return raw


def _assembler_pass(
    *,
    candidate: Mapping[str, object] | None,
    page_spec: Mapping[str, object] | None,
    assembly_report: Mapping[str, object] | None,
    source_result: Mapping[str, object] | None,
    case: Mapping[str, object],
) -> bool:
    if (
        candidate is None
        or page_spec is None
        or assembly_report is None
        or source_result is None
    ):
        return False
    candidate_bytes = _validated_candidate_bytes(candidate)
    if candidate_bytes is None:
        raise Phase4RemoteQwenStabilityError(
            "assembled source candidate artifact drifted"
        )
    if source_result.get("composition_status") != "composed":
        return False
    if source_result.get("assembler_status") != "assembled":
        return False
    if source_result.get("case_id") != case["case_id"]:
        raise Phase4RemoteQwenStabilityError(
            "assembled source case binding drifted"
        )
    if source_result.get("request_id") != case["request_id"]:
        raise Phase4RemoteQwenStabilityError(
            "assembled source request binding drifted"
        )
    page_id = page_spec.get("page_id")
    report_id = assembly_report.get("report_id")
    declared_page_hash = assembly_report.get("assembled_page_spec_sha256")
    candidate_hash = _bare_sha256(candidate_bytes)
    if (
        not isinstance(page_id, str)
        or not page_id
        or not isinstance(report_id, str)
        or not report_id
    ):
        raise Phase4RemoteQwenStabilityError(
            "assembled PageSpec/report identity fields drifted"
        )
    actual_page_hash = _bare_sha256(_fresh._canonical_bytes(page_spec))
    if (
        not isinstance(declared_page_hash, str)
        or declared_page_hash != actual_page_hash
        or assembly_report.get("candidate_sha256") != candidate_hash
    ):
        raise Phase4RemoteQwenStabilityError(
            "assembled candidate/PageSpec hash binding drifted"
        )
    if (
        "model_semantic_candidate_sha256" in source_result
        and source_result["model_semantic_candidate_sha256"]
        not in {candidate_hash, f"sha256:{candidate_hash}"}
    ):
        raise Phase4RemoteQwenStabilityError(
            "source candidate hash drifted"
        )
    if (
        "assembled_page_spec_sha256" in source_result
        and source_result["assembled_page_spec_sha256"] != declared_page_hash
    ):
        raise Phase4RemoteQwenStabilityError(
            "source assembled PageSpec hash drifted"
        )
    if "candidate_composition_identity" in source_result:
        _assert_same_canonical(
            source_result["candidate_composition_identity"],
            _fresh._identity(
                candidate,
                revision="req2web.phase4.fresh_delivery.candidate_composition.v1",
            ),
            "source candidate composition identity drifted",
        )
    if "assembled_page_spec_identity" in source_result:
        _assert_same_canonical(
            source_result["assembled_page_spec_identity"],
            _fresh._identity(
                page_spec,
                revision="req2web.phase4.fresh_delivery.page_spec.v1",
            ),
            "source assembled PageSpec identity drifted",
        )
    if "assembly_report_identity" in source_result:
        _assert_same_canonical(
            source_result["assembly_report_identity"],
            _fresh._identity(
                assembly_report,
                revision="req2web.phase4.fresh_delivery.assembly_report.v1",
            ),
            "source assembly report identity drifted",
        )
    return True


def _delivery_accounting(
    delivery_receipt: Mapping[str, object] | None,
) -> dict[str, object]:
    if delivery_receipt is None:
        return {
            "downstream": {},
            "model_success": False,
            "repair_attempted": False,
            "repair_success": False,
            "fallback_attempted": False,
            "fallback_success": False,
            "delivery_success": False,
        }
    downstream_outcome = _require_mapping(
        delivery_receipt.get("downstream_outcome"),
        "delivery downstream outcome",
    )
    downstream = _require_mapping(
        delivery_receipt.get("downstream"),
        "delivery downstream display",
    )
    success_accounting = _require_mapping(
        delivery_receipt.get("success_accounting"),
        "delivery success accounting",
    )
    repair_count = downstream_outcome.get("repair_attempted")
    if type(repair_count) is not int or repair_count not in {0, 1}:
        raise Phase4RemoteQwenStabilityError(
            "authoritative repair-attempt accounting drifted"
        )
    fallback_attempted = downstream_outcome.get("fallback_attempted")
    fallback_succeeded = downstream_outcome.get("fallback_succeeded")
    if type(fallback_attempted) is not bool or type(fallback_succeeded) is not bool:
        raise Phase4RemoteQwenStabilityError(
            "authoritative fallback accounting drifted"
        )
    if fallback_succeeded and not fallback_attempted:
        raise Phase4RemoteQwenStabilityError(
            "fallback success has no authoritative attempt"
        )
    repair_attempted = repair_count == 1
    status = downstream_outcome.get("status")
    if not isinstance(status, str) or downstream.get("status") != status:
        raise Phase4RemoteQwenStabilityError(
            "delivery downstream status display drifted"
        )
    repair_success = status == "recovered_success"
    delivery_success = status in {
        "first_pass_success",
        "recovered_success",
        "fallback_delivery",
    }
    expected_success = {
        "repair_success": repair_success,
        "fallback_success": fallback_succeeded,
        "delivery_success": delivery_success,
    }
    for key, expected in expected_success.items():
        if success_accounting.get(key) is not expected:
            raise Phase4RemoteQwenStabilityError(
                f"delivery {key} disagrees with the authoritative outcome"
            )
    model_success = success_accounting.get("model_success")
    if type(model_success) is not bool:
        raise Phase4RemoteQwenStabilityError(
            "delivery model-success accounting drifted"
        )
    repair_display = downstream.get("one_repair")
    if repair_success:
        expected_repair_display = "recovered_success"
    elif repair_attempted:
        expected_repair_display = downstream_outcome.get("repair_status")
    else:
        expected_repair_display = None
    if expected_repair_display is None:
        if (
            not isinstance(repair_display, str)
            or not repair_display.startswith("not_executed")
        ):
            raise Phase4RemoteQwenStabilityError(
                "delivery repair display contradicts the authoritative outcome"
            )
    elif repair_display != expected_repair_display:
        raise Phase4RemoteQwenStabilityError(
            "delivery repair display contradicts the authoritative outcome"
        )
    fallback_display = downstream.get("same_case_g0_fallback")
    if fallback_succeeded:
        expected_fallback_display = "delivered"
    elif fallback_attempted:
        expected_fallback_display = "not_executed"
    else:
        expected_fallback_display = None
    if expected_fallback_display is None:
        if (
            not isinstance(fallback_display, str)
            or not fallback_display.startswith("not_executed")
        ):
            raise Phase4RemoteQwenStabilityError(
                "delivery fallback display contradicts the authoritative outcome"
            )
    elif fallback_display != expected_fallback_display:
        raise Phase4RemoteQwenStabilityError(
            "delivery fallback display contradicts the authoritative outcome"
        )
    return {
        "downstream": downstream,
        "model_success": model_success,
        "repair_attempted": repair_attempted,
        "repair_success": repair_success,
        "fallback_attempted": fallback_attempted,
        "fallback_success": fallback_succeeded,
        "delivery_success": delivery_success,
    }


def _inspect_child_evidence(
    *,
    child_root: Path,
    child_run_id: str,
    case: Mapping[str, object],
    expected_profile_identity: Mapping[str, object] | None,
    expected_model_inventory_identity: Mapping[str, object] | None,
    parent_experiment_binding: Mapping[str, object] | None,
    expected_prompt_revision: str | None = None,
    expected_historical_per_node_calls: Mapping[str, int] | None = None,
) -> dict[str, object]:
    cases_root = child_root.parent
    _assert_child_root(cases_root=cases_root, child_root=child_root, must_exist=True)
    _validate_child_b_input(
        child_root=child_root,
        case=case,
    )
    attempt_calls, raw_pass, failure_codes, attempt_envelopes = (
        _read_attempt_evidence(
            child_root,
            child_run_id=child_run_id,
            case=case,
            expected_profile_identity=expected_profile_identity,
            expected_model_inventory_identity=expected_model_inventory_identity,
            parent_experiment_binding=parent_experiment_binding,
            expected_prompt_revision=expected_prompt_revision,
        )
    )
    ledger = _read_json(
        child_root / "model_call_ledger.json",
        required=True,
    )
    aggregate_ledger = _read_json(
        child_root / "aggregate_model_call_ledger.json",
        required=True,
    )
    supervisor = _read_json(
        child_root / "supervisor_receipt.json",
        required=True,
    )
    assert ledger is not None
    assert aggregate_ledger is not None
    assert supervisor is not None
    calls = _validate_ledger(
        ledger,
        child_run_id=child_run_id,
        attempt_calls=attempt_calls,
        attempt_envelopes=attempt_envelopes,
    )
    _validate_aggregate_ledger(
        aggregate_ledger,
        child_run_id=child_run_id,
        calls=calls,
        ledger=ledger,
        historical_counts=expected_historical_per_node_calls,
    )
    if parent_experiment_binding is None:
        raise Phase4RemoteQwenStabilityError(
            "stability child evidence requires a parent experiment binding"
        )
    _validate_supervisor_receipt(
        supervisor,
        child_run_id=child_run_id,
        calls=calls,
        attempt_envelopes=attempt_envelopes,
        ledger=ledger,
        aggregate_ledger=aggregate_ledger,
        parent_experiment_binding=parent_experiment_binding,
    )

    manifest = _read_json(
        child_root / "preflight_manifest.json",
        required=True,
    )
    profile_artifact = _read_json(
        child_root / "remote_profile.json",
        required=True,
    )
    inventory_artifact = _read_json(
        child_root / "model_inventory.json",
        required=True,
    )
    assert manifest is not None
    assert profile_artifact is not None
    assert inventory_artifact is not None
    stable_profile = dict(profile_artifact)
    stable_profile.pop("profile_id", None)
    stable_profile.pop("free_vram_bytes_at_preflight", None)
    if expected_profile_identity is not None:
        _assert_same_canonical(
            _fresh._identity(
                stable_profile,
                revision=STABILITY_PROFILE_BINDING_SCHEMA_VERSION,
            ),
            dict(expected_profile_identity),
            "child stable profile identity drifted",
        )
    _assert_same_canonical(
        manifest.get("profile_identity"),
        _fresh._identity(
            profile_artifact,
            revision=_fresh.P4_05_PROFILE_SCHEMA_VERSION,
        ),
        "child preflight full profile identity drifted",
    )
    inventory_identity = _require_mapping(
        inventory_artifact.get("inventory_identity"),
        "child model inventory identity",
    )
    if expected_model_inventory_identity is not None:
        _assert_same_canonical(
            inventory_identity,
            dict(expected_model_inventory_identity),
            "child model inventory identity drifted",
        )
    _assert_same_canonical(
        manifest.get("model_inventory_identity"),
        inventory_identity,
        "child preflight model inventory identity drifted",
    )
    _validate_child_binding(
        manifest,
        name="child preflight",
        child_run_id=child_run_id,
        case=case,
        expected_profile_identity=expected_profile_identity,
        expected_model_inventory_identity=expected_model_inventory_identity,
        parent_experiment_binding=parent_experiment_binding,
        required_keys=(
            "run_id",
            "case_id",
            "request_id",
            "profile_identity",
            "model_inventory_identity",
            "parent_experiment_binding",
        ),
    )
    source_result = _read_json(child_root / "revalidation_result.json")
    final_result = _read_json(child_root / "p4_05_final_result.json")
    _validate_source_result_bindings(
        source_result,
        name="child source result",
        child_run_id=child_run_id,
        case=case,
        expected_profile_identity=expected_profile_identity,
        expected_model_inventory_identity=expected_model_inventory_identity,
        parent_experiment_binding=parent_experiment_binding,
    )
    _validate_source_result_bindings(
        final_result,
        name="child final result",
        child_run_id=child_run_id,
        case=case,
        expected_profile_identity=expected_profile_identity,
        expected_model_inventory_identity=expected_model_inventory_identity,
        parent_experiment_binding=parent_experiment_binding,
    )
    candidate = _read_json(child_root / "candidate_composition_record.json")
    page_spec = _read_json(child_root / "assembled_page_spec.json")
    assembly_report = _read_json(child_root / "assembly_report.json")
    normalization = _read_json(child_root / "core_f4_normalization_receipt.json")
    attempt_normalization = _read_json(
        child_root / "attempts" / "F4" / "normalization_receipt.json"
    )
    if normalization is not None and attempt_normalization is not None:
        _assert_same_canonical(
            normalization,
            attempt_normalization,
            "F4 normalization receipts disagree",
        )
    if normalization is None:
        normalization = attempt_normalization

    delivery_receipt = _read_json(
        child_root / "delivery" / "phase4_fresh_delivery_receipt.json"
    )
    if delivery_receipt is None and final_result is not None:
        embedded = final_result.get("delivery_receipt")
        if isinstance(embedded, Mapping):
            delivery_receipt = dict(embedded)
    if delivery_receipt is not None:
        _validate_child_binding(
            delivery_receipt,
            name="child delivery receipt",
            child_run_id=child_run_id,
            case=case,
            expected_profile_identity=expected_profile_identity,
            expected_model_inventory_identity=expected_model_inventory_identity,
            parent_experiment_binding=parent_experiment_binding,
            required_keys=("case_id", "request_id"),
        )

    return {
        "calls": calls,
        "historical_calls": (
            {
                node_id: int(
                    dict(aggregate_ledger["per_node"])[node_id][
                        "historical_generate_started_count"
                    ]
                )
                for node_id in _fresh.NODE_ORDER
            }
            if isinstance(aggregate_ledger.get("per_node"), Mapping)
            else {node_id: 0 for node_id in _fresh.NODE_ORDER}
        ),
        "aggregate_calls": (
            {
                node_id: int(
                    dict(aggregate_ledger["per_node"])[node_id][
                        "aggregate_generate_started_count"
                    ]
                )
                for node_id in _fresh.NODE_ORDER
            }
            if isinstance(aggregate_ledger.get("per_node"), Mapping)
            else {node_id: 0 for node_id in _fresh.NODE_ORDER}
        ),
        "raw_pass": raw_pass,
        "failure_codes": failure_codes,
        "source_result": source_result,
        "final_result": final_result,
        "candidate": candidate,
        "page_spec": page_spec,
        "assembly_report": assembly_report,
        "normalization": normalization,
        "delivery_receipt": delivery_receipt,
    }


def _summarize_case(
    *,
    experiment_run_id: str,
    index: int,
    case: Mapping[str, object],
    child_root: Path,
    child_result: Mapping[str, object] | None,
    exception: BaseException | None,
    expected_profile_identity: Mapping[str, object] | None = None,
    expected_model_inventory_identity: Mapping[str, object] | None = None,
    parent_experiment_binding: Mapping[str, object] | None = None,
    prompt_revision: str | None = None,
    baseline_binding: Mapping[str, object] | None = None,
    expected_historical_per_node_calls: Mapping[str, int] | None = None,
) -> dict[str, object]:
    child_run_id = _expected_child_run_id(experiment_run_id, index)
    evidence = _inspect_child_evidence(
        child_root=child_root,
        child_run_id=child_run_id,
        case=case,
        expected_profile_identity=expected_profile_identity,
        expected_model_inventory_identity=expected_model_inventory_identity,
        parent_experiment_binding=parent_experiment_binding,
        expected_prompt_revision=prompt_revision,
        expected_historical_per_node_calls=expected_historical_per_node_calls,
    )
    ledger_calls = evidence["calls"]
    raw_pass = evidence["raw_pass"]
    failure_codes = evidence["failure_codes"]
    historical_calls = evidence["historical_calls"]
    aggregate_calls = evidence["aggregate_calls"]
    normalization_receipt = evidence["normalization"]
    source_result = evidence["source_result"]
    final_result = evidence["final_result"]
    candidate = evidence["candidate"]
    page_spec = evidence["page_spec"]
    assembly_report = evidence["assembly_report"]
    delivery_receipt = evidence["delivery_receipt"]
    if child_result is not None:
        _validate_source_result_bindings(
            child_result,
            name="child returned result",
            child_run_id=child_run_id,
            case=case,
            expected_profile_identity=expected_profile_identity,
            expected_model_inventory_identity=expected_model_inventory_identity,
            parent_experiment_binding=parent_experiment_binding,
        )
        if child_result.get("status") not in {
            "delivery_terminal_success",
            "failed_closed",
        }:
            raise Phase4RemoteQwenStabilityError(
                "single-case runner returned a non-terminal status"
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
    if status not in {"delivery_terminal_success", "failed_closed"}:
        raise Phase4RemoteQwenStabilityError(
            "stability child terminal status is invalid"
        )
    delivery_accounting = _delivery_accounting(delivery_receipt)
    downstream = _require_mapping(
        delivery_accounting["downstream"],
        "stability downstream accounting",
    )
    failure_artifact = _read_json(child_root / "failure.json")
    delivery_failure = _read_json(child_root / "delivery_failure.json")
    failure = (
        child_result.get("failure")
        if child_result is not None
        else (
            final_result.get("failure")
            if final_result is not None
            else None
        )
    )
    if failure is None and failure_artifact is not None:
        failure = failure_artifact
    if failure is None and delivery_failure is not None:
        failure = delivery_failure.get("failure")
    failure_code = (
        str(failure.get("code"))
        if isinstance(failure, Mapping) and failure.get("code") is not None
        else (
            type(exception).__name__
            if exception is not None
            else None
        )
    )
    f4_raw_direct_pass = bool(
        normalization_receipt is not None
        and normalization_receipt.get("raw_model_contract_success") is True
    )
    f4_normalization_count = (
        int(normalization_receipt.get("normalization_count", 0))
        if normalization_receipt is not None
        and type(normalization_receipt.get("normalization_count", 0)) is int
        else 0
    )
    raw_pass = dict(raw_pass)
    raw_pass["F4"] = f4_raw_direct_pass
    if normalization_receipt is not None:
        failure_codes = dict(failure_codes)
        failure_codes["F4"] = (
            None
            if normalization_receipt.get("raw_failure_code") is None
            else str(normalization_receipt.get("raw_failure_code"))
        )
    raw_model_success = delivery_accounting["model_success"] is True
    repair_success = delivery_accounting["repair_success"] is True
    fallback_success = delivery_accounting["fallback_success"] is True
    delivery_success = delivery_accounting["delivery_success"] is True
    repair_attempted = delivery_accounting["repair_attempted"] is True
    fallback_attempted = delivery_accounting["fallback_attempted"] is True
    repair_failed = repair_attempted and not repair_success
    candidate_bytes = _validated_candidate_bytes(candidate)
    root = {
        "schema_version": STABILITY_CASE_RESULT_SCHEMA_VERSION,
        "experiment_run_id": experiment_run_id,
        "case_index": index,
        "case_id": case["case_id"],
        "request_id": case["request_id"],
        "child_run_id": child_run_id,
        "child_result_root": str(child_root.resolve(strict=False)),
        "parent_experiment_binding": _copy_optional_mapping(
            parent_experiment_binding,
            "parent experiment binding",
        ),
        "profile_identity": (
            None
            if expected_profile_identity is None
            else copy.deepcopy(dict(expected_profile_identity))
        ),
        "model_inventory_identity": (
            None
            if expected_model_inventory_identity is None
            else copy.deepcopy(dict(expected_model_inventory_identity))
        ),
        "status": status,
        "per_node_generate_calls": dict(ledger_calls),
        "per_node_raw_contract_pass": raw_pass,
        "per_node_failure_codes": failure_codes,
        "total_model_generate_calls": sum(ledger_calls.values()),
        "f4_called": ledger_calls["F4"] > 0,
        "f4_raw_direct_pass": f4_raw_direct_pass,
        "f4_normalization_count": f4_normalization_count,
        "f4_normalization_used": f4_normalization_count > 0,
        "normalized_node_contract_success": bool(
            (
                child_result is not None
                and child_result.get("normalized_node_contract_success") is True
            )
            or (
                source_result is not None
                and source_result.get("normalized_node_contract_success") is True
            )
            or (
                final_result is not None
                and final_result.get("normalized_node_contract_success") is True
            )
        ),
        "model_success": raw_model_success,
        "composition_pass": bool(
            candidate_bytes is not None
            and source_result is not None
            and source_result.get("composition_status") == "composed"
        ),
        "assembler_pass": _assembler_pass(
            candidate=candidate,
            page_spec=page_spec,
            assembly_report=assembly_report,
            source_result=source_result,
            case=case,
        ),
        "downstream_status": downstream.get("status"),
        "downstream_first_pass_success": (
            downstream.get("status") == "first_pass_success"
        ),
        "repair_attempted": repair_attempted,
        "repair_success": repair_success,
        "repair_failed": repair_failed,
        "deterministic_repair_success": repair_success,
        "fallback_attempted": fallback_attempted,
        "g0_fallback_success": fallback_success,
        "delivery_success": delivery_success,
        "system_adjustment_used": bool(
            f4_normalization_count > 0
            or repair_attempted
            or fallback_attempted
        ),
        "failure_code": failure_code,
        "exception_type": (
            None if exception is None else type(exception).__name__
        ),
        "retry_count": 0,
        "automatic_retry": False,
    }
    if prompt_revision is not None:
        root["experiment_mode"] = F3_F4_REVISION_MODE
        root["prompt_revision"] = prompt_revision
        root["baseline_binding"] = (
            None
            if baseline_binding is None
            else copy.deepcopy(dict(baseline_binding))
        )
        root["historical_per_node_generate_calls"] = dict(historical_calls)
        root["aggregate_per_node_generate_calls"] = dict(aggregate_calls)
    return {
        **root,
        "case_result_identity": _fresh._identity(
            root,
            revision=STABILITY_CASE_RESULT_SCHEMA_VERSION,
        ),
    }


def _validate_case_result(
    case_result: Mapping[str, object],
    *,
    experiment_run_id: str,
    index: int,
    case: Mapping[str, object],
    result_root: Path,
    expected_profile_identity: Mapping[str, object] | None,
    expected_model_inventory_identity: Mapping[str, object] | None,
    parent_experiment_binding: Mapping[str, object] | None,
    prompt_revision: str | None = None,
    expected_historical_per_node_calls: Mapping[str, int] | None = None,
) -> dict[str, object]:
    data = dict(case_result)
    if data.get("schema_version") != STABILITY_CASE_RESULT_SCHEMA_VERSION:
        raise Phase4RemoteQwenStabilityError(
            "stability case-result schema drifted"
        )
    declared_identity = data.get("case_result_identity")
    identity_root = {
        key: value for key, value in data.items() if key != "case_result_identity"
    }
    _assert_same_canonical(
        declared_identity,
        _fresh._identity(
            identity_root,
            revision=STABILITY_CASE_RESULT_SCHEMA_VERSION,
        ),
        "stability case-result identity drifted",
    )
    if data.get("experiment_run_id") != experiment_run_id:
        raise Phase4RemoteQwenStabilityError(
            "stability case-result experiment binding drifted"
        )
    if data.get("case_index") != index:
        raise Phase4RemoteQwenStabilityError(
            "stability case-result index binding drifted"
        )
    if data.get("case_id") != case["case_id"]:
        raise Phase4RemoteQwenStabilityError(
            "stability case-result case binding drifted"
        )
    if data.get("request_id") != case["request_id"]:
        raise Phase4RemoteQwenStabilityError(
            "stability case-result request binding drifted"
        )
    child_run_id = _expected_child_run_id(experiment_run_id, index)
    if data.get("child_run_id") != child_run_id:
        raise Phase4RemoteQwenStabilityError(
            "stability case-result child run binding drifted"
        )
    expected_child_root = _assert_child_root(
        cases_root=result_root / "cases",
        child_root=Path(str(data.get("child_result_root", ""))),
        must_exist=True,
    )
    expected_path = _expected_child_root(result_root, index, case).resolve(
        strict=False
    )
    if expected_child_root != expected_path:
        raise Phase4RemoteQwenStabilityError(
            "stability case-result child root binding drifted"
        )
    _validate_identity_match(
        data.get("profile_identity"),
        expected_profile_identity,
        "stability case-result profile identity",
    ) if expected_profile_identity is not None else None
    _validate_identity_match(
        data.get("model_inventory_identity"),
        expected_model_inventory_identity,
        "stability case-result model inventory identity",
    ) if expected_model_inventory_identity is not None else None
    if parent_experiment_binding is not None:
        _assert_same_canonical(
            data.get("parent_experiment_binding"),
            dict(parent_experiment_binding),
            "stability case-result parent binding drifted",
        )
    if data.get("status") not in {"delivery_terminal_success", "failed_closed"}:
        raise Phase4RemoteQwenStabilityError(
            "stability case-result terminal status is invalid"
        )
    calls = data.get("per_node_generate_calls")
    raw_pass = data.get("per_node_raw_contract_pass")
    if (
        not isinstance(calls, Mapping)
        or set(calls) != set(_fresh.NODE_ORDER)
        or not isinstance(raw_pass, Mapping)
        or set(raw_pass) != set(_fresh.NODE_ORDER)
    ):
        raise Phase4RemoteQwenStabilityError(
            "stability case-result node accounting is invalid"
        )
    total = 0
    for node_id in _fresh.NODE_ORDER:
        count = calls[node_id]
        if type(count) is not int or count not in {0, 1}:
            raise Phase4RemoteQwenStabilityError(
                "stability case-result node call cap was exceeded"
            )
        if type(raw_pass[node_id]) is not bool:
            raise Phase4RemoteQwenStabilityError(
                "stability case-result raw-pass accounting is invalid"
            )
        total += count
    if data.get("total_model_generate_calls") != total or total > len(
        _fresh.NODE_ORDER
    ):
        raise Phase4RemoteQwenStabilityError(
            "stability case-result total call accounting is invalid"
        )
    if prompt_revision is None:
        if data.get("prompt_revision") is not None:
            raise Phase4RemoteQwenStabilityError(
                "baseline case-result unexpectedly contains a prompt revision"
            )
    else:
        if data.get("experiment_mode") != F3_F4_REVISION_MODE:
            raise Phase4RemoteQwenStabilityError(
                "revision case-result experiment mode drifted"
            )
        if data.get("prompt_revision") != prompt_revision:
            raise Phase4RemoteQwenStabilityError(
                "revision case-result prompt revision drifted"
            )
        if expected_historical_per_node_calls is None:
            raise Phase4RemoteQwenStabilityError(
                "revision case-result historical counts are missing"
            )
        historical_calls = data.get("historical_per_node_generate_calls")
        aggregate_calls = data.get("aggregate_per_node_generate_calls")
        if (
            not isinstance(historical_calls, Mapping)
            or not isinstance(aggregate_calls, Mapping)
            or set(historical_calls) != set(_fresh.NODE_ORDER)
            or set(aggregate_calls) != set(_fresh.NODE_ORDER)
        ):
            raise Phase4RemoteQwenStabilityError(
                "revision case-result aggregate accounting is invalid"
            )
        for node_id in _fresh.NODE_ORDER:
            if (
                historical_calls[node_id]
                != expected_historical_per_node_calls[node_id]
                or aggregate_calls[node_id]
                != expected_historical_per_node_calls[node_id] + calls[node_id]
            ):
                raise Phase4RemoteQwenStabilityError(
                    "revision case-result historical call accounting drifted"
                )
    for key in (
        "f4_called",
        "f4_raw_direct_pass",
        "f4_normalization_used",
        "model_success",
        "composition_pass",
        "assembler_pass",
        "downstream_first_pass_success",
        "repair_attempted",
        "repair_success",
        "repair_failed",
        "deterministic_repair_success",
        "fallback_attempted",
        "g0_fallback_success",
        "delivery_success",
        "system_adjustment_used",
    ):
        if type(data.get(key)) is not bool:
            raise Phase4RemoteQwenStabilityError(
                f"stability case-result boolean accounting is invalid: {key}"
            )
    evidence = _inspect_child_evidence(
        child_root=expected_child_root,
        child_run_id=child_run_id,
        case=case,
        expected_profile_identity=expected_profile_identity,
        expected_model_inventory_identity=expected_model_inventory_identity,
        parent_experiment_binding=parent_experiment_binding,
        expected_prompt_revision=prompt_revision,
        expected_historical_per_node_calls=expected_historical_per_node_calls,
    )
    evidence_calls = evidence["calls"]
    if calls != evidence_calls:
        raise Phase4RemoteQwenStabilityError(
            "stability case-result calls disagree with child evidence"
        )
    return data


def _aggregate(case_results: list[dict[str, object]]) -> dict[str, object]:
    per_node_called = {node_id: 0 for node_id in _fresh.NODE_ORDER}
    per_node_raw_pass = {node_id: 0 for node_id in _fresh.NODE_ORDER}
    for item in case_results:
        calls = item["per_node_generate_calls"]
        passes = item["per_node_raw_contract_pass"]
        if not isinstance(calls, Mapping) or not isinstance(passes, Mapping):
            raise Phase4RemoteQwenStabilityError(
                "case result node accounting is invalid"
            )
        for node_id in _fresh.NODE_ORDER:
            per_node_called[node_id] += int(calls[node_id])
            per_node_raw_pass[node_id] += int(passes[node_id] is True)

    case_count = len(case_results)
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
    model_success = sum(
        int(item["model_success"] is True) for item in case_results
    )
    downstream_first_pass = sum(
        int(item["downstream_first_pass_success"] is True)
        for item in case_results
    )
    repair_attempted = sum(
        int(item["repair_attempted"] is True) for item in case_results
    )
    repair_success = sum(
        int(item["repair_success"] is True) for item in case_results
    )
    repair_failed = sum(
        int(item["repair_failed"] is True) for item in case_results
    )
    fallback_attempted = sum(
        int(item["fallback_attempted"] is True) for item in case_results
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
        if isinstance(item["per_node_failure_codes"], Mapping)
        and item["per_node_failure_codes"]["F4"] is not None
    )
    result = {
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
        "model_success_count": model_success,
        "model_success_rate": _rate(model_success, case_count),
        "downstream_first_pass_success_count": downstream_first_pass,
        "downstream_first_pass_success_rate": _rate(
            downstream_first_pass,
            case_count,
        ),
        "repair_attempted_count": repair_attempted,
        "repair_attempted_rate": _rate(repair_attempted, case_count),
        "repair_success_count": repair_success,
        "repair_success_rate": _rate(repair_success, case_count),
        "repair_failed_count": repair_failed,
        "repair_failed_rate": _rate(repair_failed, case_count),
        "deterministic_repair_count": repair_success,
        "deterministic_repair_rate": _rate(repair_success, case_count),
        "fallback_attempted_count": fallback_attempted,
        "fallback_attempted_rate": _rate(fallback_attempted, case_count),
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
    if case_results and "historical_per_node_generate_calls" in case_results[0]:
        historical = {node_id: 0 for node_id in _fresh.NODE_ORDER}
        aggregate = {node_id: 0 for node_id in _fresh.NODE_ORDER}
        for item in case_results:
            item_historical = item.get("historical_per_node_generate_calls")
            item_aggregate = item.get("aggregate_per_node_generate_calls")
            if (
                not isinstance(item_historical, Mapping)
                or not isinstance(item_aggregate, Mapping)
            ):
                raise Phase4RemoteQwenStabilityError(
                    "revision aggregate case accounting is incomplete"
                )
            for node_id in _fresh.NODE_ORDER:
                historical[node_id] += int(item_historical[node_id])
                aggregate[node_id] += int(item_aggregate[node_id])
        result["historical_per_node_generate_started_count"] = historical
        result["historical_total_model_generate_calls"] = sum(
            historical.values()
        )
        result["aggregate_per_node_generate_started_count"] = aggregate
        result["aggregate_total_model_generate_calls"] = sum(aggregate.values())
        result["new_model_generate_calls"] = result["total_model_generate_calls"]
    return result


def _write_progress(
    *,
    result_root: Path,
    experiment_run_id: str,
    index: int,
    case: Mapping[str, object],
    case_result: Mapping[str, object],
    aggregate_so_far: Mapping[str, object],
) -> None:
    root = {
        "schema_version": STABILITY_PROGRESS_SCHEMA_VERSION,
        "experiment_run_id": experiment_run_id,
        "completed_case_count": index,
        "case_index": index,
        "case_id": case["case_id"],
        "request_id": case["request_id"],
        "child_run_id": _expected_child_run_id(experiment_run_id, index),
        "child_result_root": case_result["child_result_root"],
        "case_result": dict(case_result),
        "aggregate_so_far": dict(aggregate_so_far),
    }
    progress = {
        **root,
        "progress_identity": _fresh._identity(
            root,
            revision=STABILITY_PROGRESS_SCHEMA_VERSION,
        ),
    }
    _fresh._write_fsync(
        result_root / "progress" / f"{index:02d}.json",
        _fresh._canonical_bytes(progress),
    )


def _load_progress(
    *,
    result_root: Path,
    case_set: Mapping[str, object],
    experiment_run_id: str,
    experiment_policy_identity: Mapping[str, object],
    expected_profile_identity: Mapping[str, object] | None,
    expected_model_inventory_identity: Mapping[str, object] | None,
    prompt_revision: str | None = None,
    expected_historical_per_node_calls: Mapping[str, int] | None = None,
) -> list[dict[str, object]]:
    progress_root = result_root / "progress"
    if not progress_root.exists():
        return []
    if progress_root.is_symlink() or not progress_root.is_dir():
        raise Phase4RemoteQwenStabilityError(
            "stability progress root is invalid"
        )
    files = list(progress_root.iterdir())
    indices: list[int] = []
    for path in files:
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise Phase4RemoteQwenStabilityError(
                "stability progress inventory is invalid"
            )
        try:
            index = int(path.stem)
        except ValueError as exc:
            raise Phase4RemoteQwenStabilityError(
                "stability progress filename is invalid"
            ) from exc
        if not 1 <= index <= CASE_COUNT:
            raise Phase4RemoteQwenStabilityError(
                "stability progress index is out of range"
            )
        indices.append(index)
    if not indices:
        return []
    highest = max(indices)
    if sorted(indices) != list(range(1, highest + 1)):
        raise Phase4RemoteQwenStabilityError(
            "stability progress is not contiguous"
        )
    cases = case_set["cases"]
    if not isinstance(cases, list):
        raise Phase4RemoteQwenStabilityError("stability case set is invalid")
    results: list[dict[str, object]] = []
    for index in range(1, highest + 1):
        progress = _read_json(
            progress_root / f"{index:02d}.json",
            required=True,
        )
        assert progress is not None
        if progress.get("schema_version") != STABILITY_PROGRESS_SCHEMA_VERSION:
            raise Phase4RemoteQwenStabilityError(
                "stability progress schema drifted"
            )
        progress_root_without_identity = {
            key: value
            for key, value in progress.items()
            if key != "progress_identity"
        }
        _assert_same_canonical(
            progress.get("progress_identity"),
            _fresh._identity(
                progress_root_without_identity,
                revision=STABILITY_PROGRESS_SCHEMA_VERSION,
            ),
            "stability progress identity drifted",
        )
        case = cases[index - 1]
        if not isinstance(case, Mapping):
            raise Phase4RemoteQwenStabilityError("stability case row is invalid")
        child_parent_binding = _child_parent_experiment_binding(
            experiment_run_id=experiment_run_id,
            experiment_policy_identity=experiment_policy_identity,
            index=index,
            case=case,
        )
        if (
            progress.get("experiment_run_id") != experiment_run_id
            or progress.get("completed_case_count") != index
            or progress.get("case_index") != index
            or progress.get("case_id") != case["case_id"]
            or progress.get("request_id") != case["request_id"]
            or progress.get("child_run_id")
            != _expected_child_run_id(experiment_run_id, index)
        ):
            raise Phase4RemoteQwenStabilityError(
                "stability progress case binding drifted"
            )
        case_result = _require_mapping(
            progress.get("case_result"),
            "stability progress case result",
        )
        validated_result = _validate_case_result(
            case_result,
            experiment_run_id=experiment_run_id,
            index=index,
            case=case,
            result_root=result_root,
            expected_profile_identity=expected_profile_identity,
            expected_model_inventory_identity=expected_model_inventory_identity,
            parent_experiment_binding=child_parent_binding,
            prompt_revision=prompt_revision,
            expected_historical_per_node_calls=(
                expected_historical_per_node_calls
            ),
        )
        if progress.get("child_result_root") != validated_result.get(
            "child_result_root"
        ):
            raise Phase4RemoteQwenStabilityError(
                "stability progress child-root binding drifted"
            )
        results.append(validated_result)
        aggregate = progress.get("aggregate_so_far")
        if not isinstance(aggregate, Mapping):
            raise Phase4RemoteQwenStabilityError(
                "stability progress aggregate is invalid"
            )
        _assert_same_canonical(
            aggregate,
            _aggregate(results),
            "stability progress aggregate drifted",
        )
    return results


def _validate_cases_layout(
    *,
    result_root: Path,
    case_set: Mapping[str, object],
) -> None:
    cases_root = result_root / "cases"
    if not cases_root.exists():
        return
    if cases_root.is_symlink() or not cases_root.is_dir():
        raise Phase4RemoteQwenStabilityError("stability cases root is invalid")
    cases = case_set.get("cases")
    if not isinstance(cases, list):
        raise Phase4RemoteQwenStabilityError("stability case set is invalid")
    expected_names = {
        f"{index:02d}-{case['case_id']}"
        for index, case in enumerate(cases, start=1)
        if isinstance(case, Mapping)
    }
    for entry in cases_root.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            raise Phase4RemoteQwenStabilityError(
                "stability cases inventory is invalid"
            )
        if entry.name not in expected_names:
            raise Phase4RemoteQwenStabilityError(
                "stability cases contain an unknown child root"
            )


def _validate_summary(
    summary: Mapping[str, object],
    *,
    result_root: Path,
    case_set: Mapping[str, object],
    policy: Mapping[str, object],
    profile_identity: Mapping[str, object],
    model_inventory_identity: Mapping[str, object],
    parent_experiment_binding: Mapping[str, object] | None,
) -> dict[str, object]:
    data = dict(summary)
    if data.get("schema_version") != STABILITY_SUMMARY_SCHEMA_VERSION:
        raise Phase4RemoteQwenStabilityError("stability summary schema drifted")
    run_id = str(policy["run_id"])
    if data.get("run_id") != run_id:
        raise Phase4RemoteQwenStabilityError("stability summary run binding drifted")
    if data.get("case_set_id") != CASE_SET_ID:
        raise Phase4RemoteQwenStabilityError("stability summary case-set ID drifted")
    if data.get("case_set_identity") != case_set["case_set_identity"]:
        raise Phase4RemoteQwenStabilityError(
            "stability summary case-set identity drifted"
        )
    if data.get("policy_identity") != policy.get("policy_identity"):
        raise Phase4RemoteQwenStabilityError(
            "stability summary policy identity drifted"
        )
    _assert_same_canonical(
        data.get("profile_identity"),
        profile_identity,
        "stability summary profile identity drifted",
    )
    _assert_same_canonical(
        data.get("model_inventory_identity"),
        model_inventory_identity,
        "stability summary model inventory identity drifted",
    )
    if parent_experiment_binding is not None:
        _assert_same_canonical(
            data.get("parent_experiment_binding"),
            dict(parent_experiment_binding),
            "stability summary parent binding drifted",
        )
    experiment_mode = policy.get("experiment_mode", "baseline")
    if data.get("experiment_mode", "baseline") != experiment_mode:
        raise Phase4RemoteQwenStabilityError(
            "stability summary experiment mode drifted"
        )
    prompt_revision: str | None = None
    expected_historical_per_node_calls: Mapping[str, int] | None = None
    if experiment_mode == "baseline":
        if data.get("baseline_complete") is not True:
            raise Phase4RemoteQwenStabilityError(
                "stability summary is not a complete baseline"
            )
    elif experiment_mode == F3_F4_REVISION_MODE:
        prompt_revision = F3_F4_PROMPT_REVISION
        if data.get("revision_complete") is not True:
            raise Phase4RemoteQwenStabilityError(
                "stability summary is not a complete F3/F4 revision"
            )
        _assert_same_canonical(
            data.get("baseline_binding"),
            policy.get("baseline_binding"),
            "stability revision summary baseline binding drifted",
        )
        if data.get("prompt_revision") != prompt_revision:
            raise Phase4RemoteQwenStabilityError(
                "stability revision summary prompt revision drifted"
            )
        expected_historical_per_node_calls = {
            node_id: 1 for node_id in _fresh.NODE_ORDER
        }
    else:
        raise Phase4RemoteQwenStabilityError(
            "stability summary experiment mode is unknown"
        )
    case_results_value = data.get("case_results")
    if not isinstance(case_results_value, list) or len(case_results_value) != CASE_COUNT:
        raise Phase4RemoteQwenStabilityError(
            "stability summary case inventory is incomplete"
        )
    cases = case_set.get("cases")
    if not isinstance(cases, list):
        raise Phase4RemoteQwenStabilityError("stability case set is invalid")
    validated_results: list[dict[str, object]] = []
    policy_identity = _require_mapping(
        policy.get("policy_identity"),
        "stability policy identity",
    )
    for index, item in enumerate(case_results_value, start=1):
        if not isinstance(item, Mapping) or not isinstance(cases[index - 1], Mapping):
            raise Phase4RemoteQwenStabilityError(
                "stability summary case row is invalid"
            )
        child_parent_binding = _child_parent_experiment_binding(
            experiment_run_id=run_id,
            experiment_policy_identity=policy_identity,
            index=index,
            case=cases[index - 1],
        )
        validated_results.append(
            _validate_case_result(
                item,
                experiment_run_id=run_id,
                index=index,
                case=cases[index - 1],
                result_root=result_root,
                expected_profile_identity=profile_identity,
                expected_model_inventory_identity=model_inventory_identity,
                parent_experiment_binding=child_parent_binding,
                prompt_revision=prompt_revision,
                expected_historical_per_node_calls=(
                    expected_historical_per_node_calls
                ),
            )
        )
    _assert_same_canonical(
        data.get("aggregate"),
        _aggregate(validated_results),
        "stability summary aggregate drifted",
    )
    if experiment_mode == F3_F4_REVISION_MODE:
        expected_prompt_revision_identity = _fresh._identity(
            {
                "prompt_revision": F3_F4_PROMPT_REVISION,
                "nodes": list(F3_F4_REVISION_NODES),
            },
            revision=f"{STABILITY_SCHEMA_PREFIX}.prompt_revision.v1",
        )
        _assert_same_canonical(
            data.get("prompt_revision_identity"),
            expected_prompt_revision_identity,
            "stability revision prompt identity drifted",
        )
        aggregate = _require_mapping(
            data.get("aggregate"),
            "stability revision aggregate",
        )
        if data.get("new_model_generate_calls") != aggregate.get(
            "total_model_generate_calls"
        ):
            raise Phase4RemoteQwenStabilityError(
                "stability revision new-call summary drifted"
            )
        baseline_binding = _require_mapping(
            policy.get("baseline_binding"),
            "stability revision baseline binding",
        )
        if data.get(
            "historical_aggregate_per_node_generate_started_count"
        ) != baseline_binding.get(
            "baseline_per_node_generate_started_count"
        ):
            raise Phase4RemoteQwenStabilityError(
                "stability revision historical aggregate drifted"
            )
        expected_aggregate_counts = {
            node_id: baseline_binding[
                "baseline_per_node_generate_started_count"
            ][node_id]
            + aggregate["per_node_called_count"][node_id]
            for node_id in _fresh.NODE_ORDER
        }
        if data.get("aggregate_per_node_generate_started_count") != (
            expected_aggregate_counts
        ) or data.get("aggregate_total_model_generate_calls") != sum(
            expected_aggregate_counts.values()
        ):
            raise Phase4RemoteQwenStabilityError(
                "stability revision cumulative aggregate drifted"
            )
    summary_root = {
        key: value for key, value in data.items() if key != "summary_identity"
    }
    _assert_same_canonical(
        data.get("summary_identity"),
        _fresh._identity(
            summary_root,
            revision=STABILITY_SUMMARY_SCHEMA_VERSION,
        ),
        "stability summary identity drifted",
    )
    progress = _load_progress(
        result_root=result_root,
        case_set=case_set,
        experiment_run_id=run_id,
        experiment_policy_identity=_require_mapping(
            policy.get("policy_identity"),
            "stability policy identity",
        ),
        expected_profile_identity=profile_identity,
        expected_model_inventory_identity=model_inventory_identity,
        prompt_revision=prompt_revision,
        expected_historical_per_node_calls=expected_historical_per_node_calls,
    )
    if len(progress) != CASE_COUNT:
        raise Phase4RemoteQwenStabilityError(
            "stability summary has no complete contiguous progress evidence"
        )
    _assert_same_canonical(
        progress,
        validated_results,
        "stability summary progress evidence drifted",
    )
    return data


def run_phase4_remote_qwen_stability(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    confirm_ten_case_baseline: bool,
    run_id: str | None = None,
    console: object | None = None,
    resume_existing: bool = False,
    expected_profile_identity: Mapping[str, object] | None = None,
    expected_model_inventory_identity: Mapping[str, object] | None = None,
    parent_experiment_binding: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Execute or resume all ten frozen baseline cases."""

    if confirm_ten_case_baseline is not True:
        raise Phase4RemoteQwenStabilityError(
            "explicit confirmation is required for the ten-case baseline"
        )
    prepared = prepare_phase4_remote_qwen_stability(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        result_root=result_root,
        run_id=run_id,
        resume_existing=resume_existing,
        expected_profile_identity=expected_profile_identity,
        expected_model_inventory_identity=expected_model_inventory_identity,
        parent_experiment_binding=parent_experiment_binding,
    )
    if prepared.get("summary") is not None:
        return _require_mapping(prepared["summary"], "existing stability summary")

    selected_run_id = str(prepared["run_id"])
    case_set = _require_mapping(prepared["case_set"], "prepared stability case set")
    cases = case_set.get("cases")
    if not isinstance(cases, list) or len(cases) != CASE_COUNT:
        raise Phase4RemoteQwenStabilityError(
            "prepared stability case set is invalid"
        )
    policy = _require_mapping(prepared["policy"], "prepared stability policy")
    effective_profile_identity = _require_mapping(
        policy.get("profile_identity"),
        "prepared stability profile identity",
    )
    effective_model_inventory_identity = _require_mapping(
        policy.get("model_inventory_identity"),
        "prepared stability model inventory identity",
    )
    experiment_policy_identity = _require_mapping(
        policy.get("policy_identity"),
        "prepared stability policy identity",
    )
    effective_parent_binding = _copy_optional_mapping(
        parent_experiment_binding
        if parent_experiment_binding is not None
        else policy.get("parent_experiment_binding"),
        "parent experiment binding",
    )
    _validate_cases_layout(result_root=result_root, case_set=case_set)
    previous_results = _load_progress(
        result_root=result_root,
        case_set=case_set,
        experiment_run_id=selected_run_id,
        experiment_policy_identity=_require_mapping(
            policy.get("policy_identity"),
            "stability policy identity",
        ),
        expected_profile_identity=effective_profile_identity,
        expected_model_inventory_identity=effective_model_inventory_identity,
    )
    case_results = list(previous_results)
    cases_root = result_root / "cases"
    for index, case_value in enumerate(cases, start=1):
        if not isinstance(case_value, Mapping):
            raise Phase4RemoteQwenStabilityError("stability case row is invalid")
        case = dict(case_value)
        child_parent_binding = _child_parent_experiment_binding(
            experiment_run_id=selected_run_id,
            experiment_policy_identity=experiment_policy_identity,
            index=index,
            case=case,
        )
        child_root = _expected_child_root(result_root, index, case)
        _assert_child_root(
            cases_root=cases_root,
            child_root=child_root,
            must_exist=False,
        )
        if index <= len(previous_results):
            if console is not None:
                print(
                    f"[P4-05-STABILITY] case {index:02d}/{CASE_COUNT} "
                    f"{case['case_id']} resumed from progress",
                    file=console,
                    flush=True,
                )
            continue
        if console is not None:
            print(
                f"[P4-05-STABILITY] case {index:02d}/{CASE_COUNT} "
                f"{case['case_id']} started",
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
                run_id=_expected_child_run_id(selected_run_id, index),
                console=console,
                b_input=case,
                expected_profile_identity=effective_profile_identity,
                expected_model_inventory_identity=effective_model_inventory_identity,
                parent_experiment_binding=child_parent_binding,
            )
            if not isinstance(child_result, Mapping):
                raise Phase4RemoteQwenStabilityError(
                    "single-case runner returned a non-object result"
                )
            if child_result.get("status") not in {
                "delivery_terminal_success",
                "failed_closed",
            }:
                raise Phase4RemoteQwenStabilityError(
                    "single-case runner returned a non-terminal status"
                )
            if not child_root.is_dir():
                raise Phase4RemoteQwenStabilityError(
                    "single-case runner returned without a child result root"
                )
        case_result = _summarize_case(
            experiment_run_id=selected_run_id,
            index=index,
            case=case,
            child_root=child_root,
            child_result=child_result,
            exception=None,
            expected_profile_identity=effective_profile_identity,
            expected_model_inventory_identity=effective_model_inventory_identity,
            parent_experiment_binding=child_parent_binding,
        )
        validated_case_result = _validate_case_result(
            case_result,
            experiment_run_id=selected_run_id,
            index=index,
            case=case,
            result_root=result_root,
            expected_profile_identity=effective_profile_identity,
            expected_model_inventory_identity=effective_model_inventory_identity,
            parent_experiment_binding=child_parent_binding,
        )
        case_results.append(validated_case_result)
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
        _write_progress(
            result_root=result_root,
            experiment_run_id=selected_run_id,
            index=index,
            case=case,
            case_result=validated_case_result,
            aggregate_so_far=aggregate_so_far,
        )
        if console is not None:
            print(
                f"[P4-05-STABILITY] case {index:02d}/{CASE_COUNT} "
                f"status={validated_case_result['status']} "
                f"f4_raw={validated_case_result['f4_raw_direct_pass']} "
                f"delivery={validated_case_result['delivery_success']}",
                file=console,
                flush=True,
            )

    if len(case_results) != CASE_COUNT:
        raise Phase4RemoteQwenStabilityError(
            "stability baseline did not complete all cases"
        )
    aggregate = _aggregate(case_results)
    if int(aggregate["total_model_generate_calls"]) > BASELINE_TOTAL_CALL_CAP:
        raise Phase4RemoteQwenStabilityError(
            "stability baseline exceeded the total model-call cap"
        )
    root = {
        "schema_version": STABILITY_SUMMARY_SCHEMA_VERSION,
        "run_id": selected_run_id,
        "case_set_id": CASE_SET_ID,
        "case_set_identity": case_set["case_set_identity"],
        "policy_identity": policy["policy_identity"],
        "profile_identity": effective_profile_identity,
        "model_inventory_identity": effective_model_inventory_identity,
        "parent_experiment_binding": effective_parent_binding,
        "baseline_complete": True,
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


def run_phase4_remote_qwen_f3_f4_prompt_revision(
    *,
    model_root: Path,
    integrity_evidence: Path,
    baseline_root: Path,
    result_root: Path,
    confirm_f3_f4_prompt_revision: bool,
    run_id: str | None = None,
    console: object | None = None,
    resume_existing: bool = False,
    expected_profile_identity: Mapping[str, object] | None = None,
    expected_model_inventory_identity: Mapping[str, object] | None = None,
    parent_experiment_binding: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Execute the explicit ten-case F3/F4 reachability prompt revision."""

    if confirm_f3_f4_prompt_revision is not True:
        raise Phase4RemoteQwenStabilityError(
            "explicit confirmation is required for the F3/F4 prompt revision"
        )
    prepared = prepare_phase4_remote_qwen_f3_f4_prompt_revision(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        baseline_root=baseline_root,
        result_root=result_root,
        run_id=run_id,
        resume_existing=resume_existing,
        expected_profile_identity=expected_profile_identity,
        expected_model_inventory_identity=expected_model_inventory_identity,
        parent_experiment_binding=parent_experiment_binding,
    )
    if prepared.get("summary") is not None:
        return _require_mapping(
            prepared["summary"],
            "existing F3/F4 revision summary",
        )

    selected_run_id = str(prepared["run_id"])
    case_set = _require_mapping(
        prepared["case_set"],
        "prepared F3/F4 revision case set",
    )
    cases = case_set.get("cases")
    if not isinstance(cases, list) or len(cases) != CASE_COUNT:
        raise Phase4RemoteQwenStabilityError(
            "prepared F3/F4 revision case set is invalid"
        )
    policy = _require_mapping(
        prepared["policy"],
        "prepared F3/F4 revision policy",
    )
    effective_profile_identity = _require_mapping(
        policy.get("profile_identity"),
        "prepared F3/F4 revision profile identity",
    )
    effective_model_inventory_identity = _require_mapping(
        policy.get("model_inventory_identity"),
        "prepared F3/F4 revision model inventory identity",
    )
    experiment_policy_identity = _require_mapping(
        policy.get("policy_identity"),
        "prepared F3/F4 revision policy identity",
    )
    baseline_binding = _require_mapping(
        prepared.get("baseline_binding"),
        "prepared F3/F4 revision baseline binding",
    )
    baseline_result_root = _fresh._safe_path(
        Path(str(baseline_binding["baseline_result_root"])),
        "revision baseline result root",
        directory=True,
    )
    effective_parent_binding = _copy_optional_mapping(
        parent_experiment_binding
        if parent_experiment_binding is not None
        else policy.get("parent_experiment_binding"),
        "parent experiment binding",
    )
    expected_historical_per_case = {
        node_id: 1 for node_id in _fresh.NODE_ORDER
    }
    _validate_cases_layout(result_root=result_root, case_set=case_set)
    previous_results = _load_progress(
        result_root=result_root,
        case_set=case_set,
        experiment_run_id=selected_run_id,
        experiment_policy_identity=experiment_policy_identity,
        expected_profile_identity=effective_profile_identity,
        expected_model_inventory_identity=effective_model_inventory_identity,
        prompt_revision=F3_F4_PROMPT_REVISION,
        expected_historical_per_node_calls=expected_historical_per_case,
    )
    case_results = list(previous_results)
    cases_root = result_root / "cases"
    for index, case_value in enumerate(cases, start=1):
        if not isinstance(case_value, Mapping):
            raise Phase4RemoteQwenStabilityError(
                "F3/F4 revision case row is invalid"
            )
        case = dict(case_value)
        child_parent_binding = _child_parent_experiment_binding(
            experiment_run_id=selected_run_id,
            experiment_policy_identity=experiment_policy_identity,
            index=index,
            case=case,
        )
        child_root = _expected_child_root(result_root, index, case)
        baseline_child_root = _expected_child_root(
            baseline_result_root,
            index,
            case,
        )
        _assert_child_root(
            cases_root=cases_root,
            child_root=child_root,
            must_exist=False,
        )
        _assert_child_root(
            cases_root=baseline_result_root / "cases",
            child_root=baseline_child_root,
            must_exist=True,
        )
        _validate_child_b_input(
            child_root=baseline_child_root,
            case=case,
        )
        if index <= len(previous_results):
            if console is not None:
                print(
                    f"[P4-05-STABILITY] revision case {index:02d}/{CASE_COUNT} "
                    f"{case['case_id']} resumed from progress",
                    file=console,
                    flush=True,
                )
            continue
        if console is not None:
            print(
                f"[P4-05-STABILITY] revision case {index:02d}/{CASE_COUNT} "
                f"{case['case_id']} started",
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
                run_id=_expected_child_run_id(selected_run_id, index),
                console=console,
                history_result_roots=(baseline_child_root,),
                resume_from_result_root=baseline_child_root,
                resume_prefix=_fresh.P4_05_RESUME_PREFIX_F1_F2,
                b_input=case,
                prompt_revision=F3_F4_PROMPT_REVISION,
                expected_profile_identity=effective_profile_identity,
                expected_model_inventory_identity=effective_model_inventory_identity,
                parent_experiment_binding=child_parent_binding,
            )
            if not isinstance(child_result, Mapping):
                raise Phase4RemoteQwenStabilityError(
                    "F3/F4 revision single-case runner returned a non-object"
                )
            if child_result.get("status") not in {
                "delivery_terminal_success",
                "failed_closed",
            }:
                raise Phase4RemoteQwenStabilityError(
                    "F3/F4 revision single-case runner is not terminal"
                )
            if not child_root.is_dir():
                raise Phase4RemoteQwenStabilityError(
                    "F3/F4 revision child result root is missing"
                )
        case_result = _summarize_case(
            experiment_run_id=selected_run_id,
            index=index,
            case=case,
            child_root=child_root,
            child_result=child_result,
            exception=None,
            expected_profile_identity=effective_profile_identity,
            expected_model_inventory_identity=effective_model_inventory_identity,
            parent_experiment_binding=child_parent_binding,
            prompt_revision=F3_F4_PROMPT_REVISION,
            baseline_binding=baseline_binding,
            expected_historical_per_node_calls=expected_historical_per_case,
        )
        validated_case_result = _validate_case_result(
            case_result,
            experiment_run_id=selected_run_id,
            index=index,
            case=case,
            result_root=result_root,
            expected_profile_identity=effective_profile_identity,
            expected_model_inventory_identity=effective_model_inventory_identity,
            parent_experiment_binding=child_parent_binding,
            prompt_revision=F3_F4_PROMPT_REVISION,
            expected_historical_per_node_calls=expected_historical_per_case,
        )
        case_results.append(validated_case_result)
        aggregate_so_far = _aggregate(case_results)
        if (
            int(aggregate_so_far["total_model_generate_calls"])
            > index * len(F3_F4_REVISION_NODES)
            or int(aggregate_so_far["total_model_generate_calls"])
            > F3_F4_REVISION_TOTAL_CALL_CAP
            or aggregate_so_far["per_node_called_count"].get("F1") != 0
            or aggregate_so_far["per_node_called_count"].get("F2") != 0
            or aggregate_so_far["per_node_called_count"].get("F3", 0) > index
            or aggregate_so_far["per_node_called_count"].get("F4", 0) > index
        ):
            raise Phase4RemoteQwenStabilityError(
                "F3/F4 revision exceeded its bounded new-call budget"
            )
        _write_progress(
            result_root=result_root,
            experiment_run_id=selected_run_id,
            index=index,
            case=case,
            case_result=validated_case_result,
            aggregate_so_far=aggregate_so_far,
        )
        if console is not None:
            print(
                f"[P4-05-STABILITY] revision case {index:02d}/{CASE_COUNT} "
                f"status={validated_case_result['status']} "
                f"f3_calls={validated_case_result['per_node_generate_calls']['F3']} "
                f"f4_calls={validated_case_result['per_node_generate_calls']['F4']} "
                f"f4_raw={validated_case_result['f4_raw_direct_pass']} "
                f"delivery={validated_case_result['delivery_success']}",
                file=console,
                flush=True,
            )

    if len(case_results) != CASE_COUNT:
        raise Phase4RemoteQwenStabilityError(
            "F3/F4 revision did not complete all cases"
        )
    aggregate = _aggregate(case_results)
    if int(aggregate["total_model_generate_calls"]) > F3_F4_REVISION_TOTAL_CALL_CAP:
        raise Phase4RemoteQwenStabilityError(
            "F3/F4 revision exceeded its total new-call cap"
        )
    historical_counts = dict(
        baseline_binding["baseline_per_node_generate_started_count"]
    )
    aggregate_counts = {
        node_id: historical_counts[node_id]
        + aggregate["per_node_called_count"][node_id]
        for node_id in _fresh.NODE_ORDER
    }
    prompt_revision_identity = _fresh._identity(
        {
            "prompt_revision": F3_F4_PROMPT_REVISION,
            "nodes": list(F3_F4_REVISION_NODES),
        },
        revision=f"{STABILITY_SCHEMA_PREFIX}.prompt_revision.v1",
    )
    root = {
        "schema_version": STABILITY_SUMMARY_SCHEMA_VERSION,
        "experiment_mode": F3_F4_REVISION_MODE,
        "run_id": selected_run_id,
        "case_set_id": CASE_SET_ID,
        "case_set_identity": case_set["case_set_identity"],
        "policy_identity": policy["policy_identity"],
        "profile_identity": effective_profile_identity,
        "model_inventory_identity": effective_model_inventory_identity,
        "parent_experiment_binding": effective_parent_binding,
        "baseline_complete": False,
        "revision_complete": True,
        "baseline_binding": baseline_binding,
        "prompt_revision": F3_F4_PROMPT_REVISION,
        "prompt_revision_identity": prompt_revision_identity,
        "revision_nodes": list(F3_F4_REVISION_NODES),
        "case_results": case_results,
        "aggregate": aggregate,
        "new_model_generate_calls": aggregate["total_model_generate_calls"],
        "historical_aggregate_per_node_generate_started_count": (
            historical_counts
        ),
        "historical_aggregate_total_model_generate_calls": sum(
            historical_counts.values()
        ),
        "aggregate_per_node_generate_started_count": aggregate_counts,
        "aggregate_total_model_generate_calls": sum(aggregate_counts.values()),
        "prompt_contract_revision_executed": True,
        "training_executed": False,
        "claim_boundary": (
            "P4-05 explicit F3/F4 reachability prompt revision over a completed "
            "ten-case baseline; F1/F2 are immutable baseline prefix artifacts, "
            "F3/F4 are newly generated once per case where dependencies remain "
            "executable, and raw model success remains separate from repair, "
            "fallback, delivery, H1, browser, training, and formal quality"
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
    "F3_F4_PROMPT_REVISION",
    "F3_F4_REVISION_MODE",
    "F3_F4_REVISION_NODES",
    "F3_F4_REVISION_TOTAL_CALL_CAP",
    "PROMPT_CONTRACT_REVISION_CAP",
    "Phase4RemoteQwenStabilityError",
    "STABILITY_POLICY_SCHEMA_VERSION",
    "STABILITY_PROGRESS_SCHEMA_VERSION",
    "STABILITY_RUN_PREFIX",
    "STABILITY_SUMMARY_SCHEMA_VERSION",
    "prepare_phase4_remote_qwen_f3_f4_prompt_revision",
    "prepare_phase4_remote_qwen_stability",
    "run_phase4_remote_qwen_f3_f4_prompt_revision",
    "run_phase4_remote_qwen_stability",
]
