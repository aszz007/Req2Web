"""Savepoint-driven Phase 4 local-Qwen integrated foundation.

This module intentionally performs no model action.  It restores the already
validated F1-F4 savepoints, replays their owning validators and receipts, and
then reuses the existing registry, mapping, candidate-composition and
CanonicalPageSpecAssembler authorities.
"""

from __future__ import annotations

import base64
import copy
from pathlib import Path
from typing import Mapping

from req2web_orchestration.phase4_graph import (
    NODE_ORDER,
    phase4_assemble_candidate,
    phase4_compose_candidate,
    phase4_register_node_output,
    phase4_synthetic_assembler_bindings,
    phase4_validate_f4_normalization_receipt,
)
from req2web_runtime import phase4_local_qwen_f4 as _f4
from req2web_runtime import phase4_local_qwen_f4_revalidation as _f4_revalidation


INTEGRATED_FOUNDATION_SCHEMA_PREFIX = (
    "req2web.phase4.local_qwen.integrated_savepoint_foundation"
)
INTEGRATED_FOUNDATION_SCHEMA_VERSION = (
    f"{INTEGRATED_FOUNDATION_SCHEMA_PREFIX}.result.v1"
)
INTEGRATED_FOUNDATION_RESULT_SCHEMA_VERSION = (
    INTEGRATED_FOUNDATION_SCHEMA_VERSION
)
INTEGRATED_FOUNDATION_BINDING_SCHEMA_VERSION = (
    f"{INTEGRATED_FOUNDATION_SCHEMA_PREFIX}.source_binding.v1"
)
INTEGRATED_FOUNDATION_STATE_SCHEMA_VERSION = (
    f"{INTEGRATED_FOUNDATION_SCHEMA_PREFIX}.state.v1"
)
INTEGRATED_FOUNDATION_SEAM_SCHEMA_VERSION = (
    f"{INTEGRATED_FOUNDATION_SCHEMA_PREFIX}.fresh_generate_seam.v1"
)
INTEGRATED_FOUNDATION_ROOT_MARKER = (
    ".req2web-phase4-local-qwen-integrated-foundation-root"
)

INTEGRATED_SOURCE_BINDING_NAME = "integrated_source_savepoint_binding.json"
INTEGRATED_F4_RAW_NAME = "f4_source_raw_response.bin"
INTEGRATED_F3_STATE_NAME = "restored_f3_authority_state.json"
INTEGRATED_F4_STATE_NAME = "restored_f4_authority_state.json"
INTEGRATED_SEAM_NAME = "fresh_local_qwen_generate_seam.json"
INTEGRATED_CANDIDATE_NAME = "candidate_composition_record.json"
INTEGRATED_PAGE_SPEC_NAME = "assembled_page_spec.json"
INTEGRATED_ASSEMBLY_REPORT_NAME = "assembly_report.json"
INTEGRATED_STATE_NAME = "integrated_authority_state.json"
INTEGRATED_RESULT_NAME = "integrated_foundation_result.json"

F3_SAVEPOINT_FILES = (
    "revalidation_result.json",
    "revalidated_f3_output.json",
    "revalidated_f3_authority_state.json",
    "raw_response.bin",
    "checkpoint_packet.json",
    "checkpoint_receipt.json",
    "prior_f3_failure.json",
)
F4_SAVEPOINT_FILES = (
    "source_f4_failure_binding.json",
    "normalized_f4_output.json",
    "f4_generic_ref_normalization_receipt.json",
    "normalized_f4_authority_state.json",
    "candidate_composition_record.json",
    "assembled_page_spec.json",
    "assembly_report.json",
    "f4_revalidation_result.json",
)

_canonical_bytes = _f4._canonical_bytes
_identity = _f4._identity
_strict_json = _f4._strict_json
_write_once = _f4._write_once


class Phase4LocalQwenIntegratedFoundationError(ValueError):
    """Raised when a savepoint-integrated foundation cannot be proven."""


def _safe_existing_root(path: Path, name: str) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or not path.is_dir()
        or path.is_symlink()
    ):
        raise Phase4LocalQwenIntegratedFoundationError(
            f"{name} must be an existing absolute non-symlink directory"
        )
    return path


def _prepare_result_root(path: Path) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path.exists()
        or not path.parent.is_dir()
        or path.parent.is_symlink()
    ):
        raise Phase4LocalQwenIntegratedFoundationError(
            "integrated foundation result root must be new and safe"
        )
    path.mkdir()
    _write_once(
        path,
        INTEGRATED_FOUNDATION_ROOT_MARKER,
        _canonical_bytes(
            {
                "schema_version": (
                    f"{INTEGRATED_FOUNDATION_SCHEMA_PREFIX}.root_marker.v1"
                ),
                "model_action": False,
                "model_generate_calls": 0,
                "downstream": "not_executed",
            }
        ),
    )
    return path


def _read_regular(root: Path, name: str) -> bytes:
    path = root / name
    if not path.is_file() or path.is_symlink():
        raise Phase4LocalQwenIntegratedFoundationError(
            f"required savepoint artifact is unavailable: {name}"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise Phase4LocalQwenIntegratedFoundationError(
            f"required savepoint artifact is unreadable: {name}"
        ) from exc


def _read_canonical_json(root: Path, name: str) -> tuple[bytes, dict[str, object]]:
    raw = _read_regular(root, name)
    try:
        value = _strict_json(raw, require_canonical=True)
    except Exception as exc:
        raise Phase4LocalQwenIntegratedFoundationError(
            f"savepoint JSON is not canonical: {name}"
        ) from exc
    if not isinstance(value, dict):
        raise Phase4LocalQwenIntegratedFoundationError(
            f"savepoint JSON root is not an object: {name}"
        )
    return raw, value


def _artifact_identities(
    root: Path,
    names: tuple[str, ...],
    *,
    revision: str,
) -> dict[str, object]:
    return {
        name: _identity(
            _read_regular(root, name),
            revision=revision,
            identity_kind="raw_bytes",
        )
        for name in names
    }


def _require_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise Phase4LocalQwenIntegratedFoundationError(message)


def _require_canonical_equal(
    actual: object,
    expected: object,
    message: str,
) -> None:
    if _canonical_bytes(actual) != _canonical_bytes(expected):
        raise Phase4LocalQwenIntegratedFoundationError(message)


def _ordered_object(
    value: object,
    keys: tuple[str, ...],
    name: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise Phase4LocalQwenIntegratedFoundationError(
            f"{name} has non-exact keys"
        )
    return {key: copy.deepcopy(value[key]) for key in keys}


def _ordered_ref(value: object, name: str) -> dict[str, object]:
    return _ordered_object(
        value,
        ("ref_type", "ref_id", "ref_revision"),
        name,
    )


def _ordered_f4_output(value: object) -> dict[str, object]:
    root = _ordered_object(value, ("acceptance_checks",), "F4.output")
    checks = root["acceptance_checks"]
    if not isinstance(checks, list):
        raise Phase4LocalQwenIntegratedFoundationError(
            "F4.output.acceptance_checks must be a list"
        )
    ordered_checks: list[dict[str, object]] = []
    for index, raw_check in enumerate(checks):
        check = _ordered_object(
            raw_check,
            (
                "local_id",
                "entity_type",
                "description",
                "use_case_refs",
                "state_ref",
                "refs",
            ),
            f"F4.acceptance_checks[{index}]",
        )
        use_case_refs = check["use_case_refs"]
        refs = check["refs"]
        if not isinstance(use_case_refs, list) or not isinstance(refs, list):
            raise Phase4LocalQwenIntegratedFoundationError(
                "F4 acceptance references must be lists"
            )
        check["use_case_refs"] = [
            _ordered_ref(item, f"F4.use_case_refs[{index}]")
            for item in use_case_refs
        ]
        check["state_ref"] = _ordered_ref(
            check["state_ref"],
            f"F4.state_ref[{index}]",
        )
        check["refs"] = [
            _ordered_ref(item, f"F4.refs[{index}]")
            for item in refs
        ]
        ordered_checks.append(check)
    root["acceptance_checks"] = ordered_checks
    return root


def _ordered_f4_receipt(value: object) -> dict[str, object]:
    receipt = _ordered_object(
        value,
        (
            "schema_version",
            "receipt_id",
            "normalizer_revision",
            "node_id",
            "case_id",
            "request_id",
            "contract_identity",
            "status",
            "raw_model_contract_success",
            "raw_failure_code",
            "raw_output_identity",
            "normalized_output_identity",
            "operations",
            "normalization_count",
            "normalized_node_contract_success",
            "semantic_arrays_unchanged",
            "model_generate_calls",
            "normalization_counts_as_repair",
        ),
        "F4.normalization_receipt",
    )
    for key in ("raw_output_identity", "normalized_output_identity"):
        receipt[key] = _ordered_object(
            receipt[key],
            ("identity_kind", "sha256", "byte_length", "revision"),
            f"F4.normalization_receipt.{key}",
        )
    return receipt


def _validate_f3_savepoint(
    f3_savepoint_root: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    """Use the accepted F3 replay and return its exact authority state."""

    try:
        (
            result_raw,
            output_raw,
            state_raw,
            source_raw,
            revalidation,
            _profile,
            authority_state,
            _checkpoint,
        ) = _f4._replay_f3_savepoint_local(
            f3_result_root=f3_savepoint_root
        )
    except Exception as exc:
        raise Phase4LocalQwenIntegratedFoundationError(
            "F3 savepoint live replay failed closed"
        ) from exc

    if revalidation.node_model_pass is not True:
        raise Phase4LocalQwenIntegratedFoundationError(
            "F3 savepoint is not contract-validated"
        )
    if revalidation.revalidation_generate_calls != 0:
        raise Phase4LocalQwenIntegratedFoundationError(
            "F3 savepoint revalidation performed a model call"
        )
    if not isinstance(authority_state, Mapping):
        raise Phase4LocalQwenIntegratedFoundationError(
            "F3 savepoint authority state is not an object"
        )

    _, saved_state = _read_canonical_json(
        f3_savepoint_root,
        "revalidated_f3_authority_state.json",
    )
    _require_equal(
        _canonical_bytes(saved_state),
        state_raw,
        "F3 authority state bytes drifted during replay",
    )

    # These comparisons make the no-model provenance explicit in the new
    # integrated envelope.  The source bytes remain historical evidence only.
    if not result_raw or not output_raw or not source_raw:
        raise Phase4LocalQwenIntegratedFoundationError(
            "F3 savepoint replay did not retain required source bytes"
        )

    return copy.deepcopy(dict(authority_state)), {
        "revalidation_id": revalidation.revalidation_id,
        "revalidation_generate_calls": revalidation.revalidation_generate_calls,
        "source_model_success_claim": "not_reclaimed_from_checkpoint",
    }


def _validate_f4_source_binding(
    f4_savepoint_root: Path,
    *,
    f4_source_result_root: Path | None,
    f4_raw_artifact_root: Path | None,
) -> tuple[dict[str, object], bytes, dict[str, object]]:
    if f4_source_result_root is not None and f4_raw_artifact_root is not None:
        raise Phase4LocalQwenIntegratedFoundationError(
            "F4 source result root and raw artifact root are mutually exclusive"
        )
    binding_raw, binding = _read_canonical_json(
        f4_savepoint_root,
        _f4_revalidation.F4_REVALIDATION_SOURCE_BINDING_NAME,
    )
    expected_binding_id = _identity(
        {
            key: value
            for key, value in binding.items()
            if key != "binding_id"
        },
        revision=_f4_revalidation.F4_REVALIDATION_SOURCE_BINDING_SCHEMA_VERSION,
    )["sha256"]
    _require_equal(
        binding.get("binding_id"),
        expected_binding_id,
        "F4 source binding identity drifted",
    )

    stored_root = binding.get("resolved_source_root")
    source_failure_facts = binding.get("source_failure_facts")
    if not isinstance(source_failure_facts, Mapping):
        raise Phase4LocalQwenIntegratedFoundationError(
            "F4 source failure facts are missing"
        )

    if f4_raw_artifact_root is not None:
        _safe_existing_root(f4_raw_artifact_root, "F4 raw artifact root")
        raw_response = _read_regular(
            f4_raw_artifact_root,
            INTEGRATED_F4_RAW_NAME,
        )
        expected_raw_identity = _identity(
            raw_response,
            revision=(
                f"{_f4_revalidation.F4_REVALIDATION_SCHEMA_PREFIX}.source_raw.v1"
            ),
            identity_kind="raw_bytes",
        )
        _require_equal(
            binding.get("source_raw_identity"),
            expected_raw_identity,
            "self-contained F4 raw artifact identity drifted",
        )
        return dict(binding), raw_response, {
            "binding_raw": binding_raw,
            "source_final_result_identity": binding[
                "source_final_result_identity"
            ],
            "source_raw_identity": expected_raw_identity,
            "source_root": None,
            "source_raw_from_integrated_artifact": True,
            "source_final_result_live_verified": False,
            "source_failure_facts": dict(source_failure_facts),
        }

    if f4_source_result_root is None:
        if not isinstance(stored_root, str):
            raise Phase4LocalQwenIntegratedFoundationError(
                "F4 source result root is unavailable"
            )
        f4_source_result_root = Path(stored_root)
    _safe_existing_root(f4_source_result_root, "F4 source result root")

    final_raw = _read_regular(
        f4_source_result_root,
        _f4.F4_LOCAL_FINAL_RESULT_NAME,
    )
    raw_response = _read_regular(
        f4_source_result_root,
        _f4.F4_LOCAL_RAW_NAME,
    )
    try:
        final = _strict_json(final_raw, require_canonical=True)
    except Exception as exc:
        raise Phase4LocalQwenIntegratedFoundationError(
            "F4 source final result is not canonical"
        ) from exc
    if not isinstance(final, Mapping):
        raise Phase4LocalQwenIntegratedFoundationError(
            "F4 source final result is not an object"
        )

    expected_final_identity = _identity(
        final_raw,
        revision=(
            f"{_f4_revalidation.F4_REVALIDATION_SCHEMA_PREFIX}.source_result.v1"
        ),
        identity_kind="raw_bytes",
    )
    expected_raw_identity = _identity(
        raw_response,
        revision=(
            f"{_f4_revalidation.F4_REVALIDATION_SCHEMA_PREFIX}.source_raw.v1"
        ),
        identity_kind="raw_bytes",
    )
    _require_equal(
        binding.get("source_final_result_identity"),
        expected_final_identity,
        "F4 source final result bytes drifted",
    )
    _require_equal(
        binding.get("source_raw_identity"),
        expected_raw_identity,
        "F4 source raw bytes drifted",
    )

    actual_facts = {
        "pilot_id": final.get("pilot_id"),
        "status": final.get("status"),
        "node_model_pass": final.get("node_model_pass"),
        "generate_calls": final.get("generate_calls"),
        "previous_call_consumed": final.get("previous_call_consumed"),
        "current_call_consumed": final.get("current_call_consumed"),
        "aggregate_after_call": final.get("aggregate_after_call"),
        "parse": final.get("parse"),
        "registry": final.get("registry"),
        "failure_code": final.get("failure_code"),
        "raw_status": (
            final.get("raw", {}).get("status")
            if isinstance(final.get("raw"), Mapping)
            else None
        ),
        "worker_exit_verified": (
            final.get("worker_teardown", {}).get("worker_exit_verified")
            if isinstance(final.get("worker_teardown"), Mapping)
            else None
        ),
    }
    _require_equal(
        dict(source_failure_facts),
        actual_facts,
        "F4 source failure facts drifted",
    )
    return dict(binding), raw_response, {
        "binding_raw": binding_raw,
        "source_final_result_identity": expected_final_identity,
        "source_raw_identity": expected_raw_identity,
        "source_root": str(f4_source_result_root.resolve(strict=True)),
        "source_raw_from_integrated_artifact": False,
        "source_final_result_live_verified": True,
        "source_failure_facts": dict(source_failure_facts),
    }


def _validate_shared_binding(
    f3_state: Mapping[str, object],
    f4_state: Mapping[str, object],
) -> None:
    shared_keys = (
        "schema_version",
        "graph_revision",
        "case_id",
        "request_id",
        "contract_identity",
        "dependency_receipt_identity",
        "b_input",
        "b_identity",
        "constraint_identity",
        "advisory_dispositions",
    )
    for key in shared_keys:
        if f3_state.get(key) != f4_state.get(key):
            raise Phase4LocalQwenIntegratedFoundationError(
                f"F3/F4 savepoint binding drifted at {key}"
            )
    f3_registry = [
        row
        for row in f3_state.get("registry_inventory", [])
        if row.get("node_id") in {"F1", "F2", "F3"}
    ]
    f4_registry = [
        row
        for row in f4_state.get("registry_inventory", [])
        if row.get("node_id") in {"F1", "F2", "F3"}
    ]
    if f3_registry != f4_registry:
        raise Phase4LocalQwenIntegratedFoundationError(
            "F3/F4 savepoint binding drifted at prior registry inventory"
        )
    for node_id in ("F1", "F2", "F3"):
        if f3_state.get("registry_identities", {}).get(node_id) != f4_state.get(
            "registry_identities", {}
        ).get(node_id):
            raise Phase4LocalQwenIntegratedFoundationError(
                f"F3/F4 savepoint registry identity drifted at {node_id}"
            )
    for node_id in ("F1", "F2", "F3"):
        if f3_state.get("node_results", {}).get(node_id) != f4_state.get(
            "node_results", {}
        ).get(node_id):
            raise Phase4LocalQwenIntegratedFoundationError(
                f"F3/F4 savepoint node binding drifted at {node_id}"
            )


def _build_fresh_generate_seam(
    *,
    state: Mapping[str, object],
    source_binding_identity: Mapping[str, object],
) -> dict[str, object]:
    seam: dict[str, object] = {
        "schema_version": INTEGRATED_FOUNDATION_SEAM_SCHEMA_VERSION,
        "status": "reserved_not_executed",
        "case_id": state["case_id"],
        "request_id": state["request_id"],
        "node_order": list(NODE_ORDER),
        "upstream_input_rule": (
            "fresh integrated inputs must be derived from same-run live-"
            "validated upstream outputs"
        ),
        "checkpoint_reuse_is_not_raw_model_success": True,
        "fresh_model_action_required": True,
        "fresh_pilot_identity_required": True,
        "fresh_pre_call_record_required": True,
        "parent_supervisor_worker_required": True,
        "automatic_retry": False,
        "output_truncation_is_failure": True,
        "current_model_generate_calls": 0,
        "current_model_action": False,
        "current_source_binding_identity": copy.deepcopy(
            dict(source_binding_identity)
        ),
    }
    seam["seam_id"] = _identity(
        {
            key: value
            for key, value in seam.items()
            if key != "seam_id"
        },
        revision=INTEGRATED_FOUNDATION_SEAM_SCHEMA_VERSION,
    )["sha256"]
    return seam


def run_phase4_local_qwen_integrated_savepoint_foundation(
    *,
    f3_savepoint_root: Path,
    f4_savepoint_root: Path,
    result_root: Path,
    f4_source_result_root: Path | None = None,
    f4_raw_artifact_root: Path | None = None,
    confirm_no_model_integrated_foundation: bool,
) -> dict[str, object]:
    """Restore F1-F4 savepoints and assemble once without model execution."""

    if confirm_no_model_integrated_foundation is not True:
        raise Phase4LocalQwenIntegratedFoundationError(
            "explicit no-model integrated-foundation confirmation is required"
        )
    _safe_existing_root(f3_savepoint_root, "F3 savepoint root")
    _safe_existing_root(f4_savepoint_root, "F4 savepoint root")
    result_root = _prepare_result_root(result_root)

    f3_state, f3_facts = _validate_f3_savepoint(f3_savepoint_root)
    f4_binding, f4_raw, f4_source_facts = _validate_f4_source_binding(
        f4_savepoint_root,
        f4_source_result_root=f4_source_result_root,
        f4_raw_artifact_root=f4_raw_artifact_root,
    )
    _, normalized_output_raw = _read_canonical_json(
        f4_savepoint_root,
        _f4_revalidation.F4_REVALIDATION_NORMALIZED_OUTPUT_NAME,
    )
    _, receipt_raw = _read_canonical_json(
        f4_savepoint_root,
        _f4_revalidation.F4_REVALIDATION_NORMALIZATION_RECEIPT_NAME,
    )
    normalized_output = _ordered_f4_output(normalized_output_raw)
    receipt = _ordered_f4_receipt(receipt_raw)
    _, saved_f4_state = _read_canonical_json(
        f4_savepoint_root,
        _f4_revalidation.F4_REVALIDATION_REGISTERED_STATE_NAME,
    )
    _, saved_candidate = _read_canonical_json(
        f4_savepoint_root,
        _f4_revalidation.F4_REVALIDATION_CANDIDATE_NAME,
    )
    _, saved_page_spec = _read_canonical_json(
        f4_savepoint_root,
        _f4_revalidation.F4_REVALIDATION_PAGE_SPEC_NAME,
    )
    _, saved_assembly_report = _read_canonical_json(
        f4_savepoint_root,
        _f4_revalidation.F4_REVALIDATION_ASSEMBLY_REPORT_NAME,
    )
    _, saved_f4_result = _read_canonical_json(
        f4_savepoint_root,
        _f4_revalidation.F4_REVALIDATION_RESULT_NAME,
    )

    try:
        phase4_validate_f4_normalization_receipt(
            receipt,
            raw_bytes=f4_raw,
            normalized_output=normalized_output,
            state=f3_state,
        )
        restored_f4_state = phase4_register_node_output(
            f3_state,
            "F4",
            normalized_output,
        )
    except Exception as exc:
        raise Phase4LocalQwenIntegratedFoundationError(
            "F4 savepoint receipt/state replay failed closed"
        ) from exc

    _require_canonical_equal(
        restored_f4_state,
        saved_f4_state,
        "F4 authority state bytes drifted during live replay",
    )
    _validate_shared_binding(f3_state, restored_f4_state)

    try:
        candidate_record = phase4_compose_candidate(restored_f4_state)
        candidate_bytes = base64.b64decode(
            candidate_record["model_semantic_candidate_canonical_b64"],
            validate=True,
        )
        context, guidance = phase4_synthetic_assembler_bindings(
            restored_f4_state
        )
        assembled = phase4_assemble_candidate(
            candidate_bytes,
            context,
            guidance,
        )
        page_spec = assembled.page_spec.to_dict()
        assembly_report = assembled.report.to_dict()
    except Exception as exc:
        raise Phase4LocalQwenIntegratedFoundationError(
            "integrated composition or assembler replay failed closed"
        ) from exc

    _require_canonical_equal(
        candidate_record,
        saved_candidate,
        "candidate composition bytes drifted during replay",
    )
    _require_canonical_equal(
        page_spec,
        saved_page_spec,
        "assembled PageSpec bytes drifted during replay",
    )
    _require_canonical_equal(
        assembly_report,
        saved_assembly_report,
        "assembly report bytes drifted during replay",
    )

    f3_file_identities = _artifact_identities(
        f3_savepoint_root,
        F3_SAVEPOINT_FILES,
        revision=f"{INTEGRATED_FOUNDATION_SCHEMA_PREFIX}.source_file.v1",
    )
    f4_file_identities = _artifact_identities(
        f4_savepoint_root,
        F4_SAVEPOINT_FILES,
        revision=f"{INTEGRATED_FOUNDATION_SCHEMA_PREFIX}.source_file.v1",
    )
    source_binding: dict[str, object] = {
        "schema_version": INTEGRATED_FOUNDATION_BINDING_SCHEMA_VERSION,
        "binding_id": "pending",
        "f3_savepoint_root": str(f3_savepoint_root.resolve(strict=True)),
        "f4_savepoint_root": str(f4_savepoint_root.resolve(strict=True)),
        "f4_source_result_root": f4_source_facts["source_root"],
        "f3_files": f3_file_identities,
        "f4_files": f4_file_identities,
        "f3_authority_state_identity": _identity(
            f3_state,
            revision=f"{INTEGRATED_FOUNDATION_SCHEMA_PREFIX}.f3_state.v1",
        ),
        "f4_authority_state_identity": _identity(
            restored_f4_state,
            revision=f"{INTEGRATED_FOUNDATION_SCHEMA_PREFIX}.f4_state.v1",
        ),
        "f4_source_binding_identity": _identity(
            f4_binding,
            revision=_f4_revalidation.F4_REVALIDATION_SOURCE_BINDING_SCHEMA_VERSION,
        ),
        "f4_source_raw_identity": f4_source_facts["source_raw_identity"],
        "f4_source_raw_artifact": {
            "relative_path": INTEGRATED_F4_RAW_NAME,
            "identity": copy.deepcopy(
                f4_source_facts["source_raw_identity"]
            ),
            "source_kind": "live_validated_immutable_copy",
        },
        "f4_source_raw_from_integrated_artifact": f4_source_facts[
            "source_raw_from_integrated_artifact"
        ],
        "f4_source_final_result_live_verified": f4_source_facts[
            "source_final_result_live_verified"
        ],
        "f3_revalidation": f3_facts,
        "model_generate_calls": 0,
        "checkpoint_reuse_is_not_raw_model_success": True,
    }
    source_binding["binding_id"] = _identity(
        {
            key: value
            for key, value in source_binding.items()
            if key != "binding_id"
        },
        revision=INTEGRATED_FOUNDATION_BINDING_SCHEMA_VERSION,
    )["sha256"]

    seam = _build_fresh_generate_seam(
        state=restored_f4_state,
        source_binding_identity=source_binding,
    )
    source_binding_identity = _identity(
        source_binding,
        revision=INTEGRATED_FOUNDATION_BINDING_SCHEMA_VERSION,
    )
    integrated_state: dict[str, object] = {
        "schema_version": INTEGRATED_FOUNDATION_STATE_SCHEMA_VERSION,
        "state_id": "pending",
        "runtime_kind": "local_savepoint_replay_composition",
        "savepoint_replay_execution": True,
        "graph_runtime_execution": False,
        "model_action": False,
        "model_generate_calls": 0,
        "source_binding_identity": source_binding_identity,
        "completed_node_ids": list(NODE_ORDER),
        "skipped_model_node_ids": list(NODE_ORDER),
        "checkpoint_reuse_is_not_raw_model_success": True,
        "authority_state": restored_f4_state,
        "candidate_composition_record": candidate_record,
        "assembly_status": "assembled",
        "fresh_local_qwen_generate_seam": seam,
    }
    integrated_state["state_id"] = _identity(
        {
            key: value
            for key, value in integrated_state.items()
            if key != "state_id"
        },
        revision=INTEGRATED_FOUNDATION_STATE_SCHEMA_VERSION,
    )["sha256"]

    node_outcomes = [
        {
            "node_id": node_id,
            "checkpoint_reused": True,
            "model_generate_calls": 0,
            "restored_contract_status": (
                "validated_after_generic_ref_normalization"
                if node_id == "F4"
                else "validated"
            ),
            "raw_model_success_claim": (
                "not_claimed_from_checkpoint"
                if node_id != "F4"
                else "false"
            ),
            "normalized_node_contract_success": (
                True if node_id == "F4" else None
            ),
        }
        for node_id in NODE_ORDER
    ]
    result: dict[str, object] = {
        "schema_version": INTEGRATED_FOUNDATION_SCHEMA_VERSION,
        "result_id": "pending",
        "status": "integrated_savepoint_foundation_ready",
        "case_id": restored_f4_state["case_id"],
        "request_id": restored_f4_state["request_id"],
        "runtime_kind": "local_savepoint_replay_composition",
        "savepoint_replay_execution": True,
        "graph_runtime_execution": False,
        "model_action": False,
        "model_generate_calls": 0,
        "source_binding_identity": source_binding_identity,
        "integrated_state_identity": _identity(
            integrated_state,
            revision=INTEGRATED_FOUNDATION_STATE_SCHEMA_VERSION,
        ),
        "node_outcomes": node_outcomes,
        "completed_node_ids": list(NODE_ORDER),
        "skipped_model_node_ids": list(NODE_ORDER),
        "checkpoint_reuse_is_not_raw_model_success": True,
        "raw_model_contract_success": False,
        "restored_node_contracts_validated": True,
        "normalized_node_contract_success": True,
        "agent_chain_system_output_usable": True,
        "registry_status": "validated_and_registered",
        "composition_status": candidate_record["candidate_projection_status"],
        "assembler_status": "assembled",
        "fresh_local_qwen_generate_seam": "reserved_not_executed",
        "downstream": {
            "consistency": "not_executed",
            "acceptance": "not_executed",
            "repair": "not_executed",
            "g0": "not_executed",
            "package": "not_executed",
            "production_route": "not_executed",
        },
        "claim_boundary": (
            "savepoint_replay_foundation_not_integrated_model_success"
        ),
    }
    result["result_id"] = _identity(
        {
            key: value
            for key, value in result.items()
            if key != "result_id"
        },
        revision=INTEGRATED_FOUNDATION_SCHEMA_VERSION,
    )["sha256"]

    artifacts = (
        (
            INTEGRATED_SOURCE_BINDING_NAME,
            source_binding,
        ),
        (
            INTEGRATED_F3_STATE_NAME,
            f3_state,
        ),
        (
            INTEGRATED_F4_STATE_NAME,
            restored_f4_state,
        ),
        (
            INTEGRATED_SEAM_NAME,
            seam,
        ),
        (
            INTEGRATED_CANDIDATE_NAME,
            candidate_record,
        ),
        (
            INTEGRATED_PAGE_SPEC_NAME,
            page_spec,
        ),
        (
            INTEGRATED_ASSEMBLY_REPORT_NAME,
            assembly_report,
        ),
        (
            INTEGRATED_STATE_NAME,
            integrated_state,
        ),
        (
            INTEGRATED_RESULT_NAME,
            result,
        ),
    )
    _write_once(result_root, INTEGRATED_F4_RAW_NAME, f4_raw)
    for name, value in artifacts:
        _write_once(result_root, name, _canonical_bytes(value))
    return copy.deepcopy(result)


__all__ = [
    "INTEGRATED_FOUNDATION_BINDING_SCHEMA_VERSION",
    "INTEGRATED_FOUNDATION_RESULT_SCHEMA_VERSION",
    "INTEGRATED_FOUNDATION_SCHEMA_PREFIX",
    "INTEGRATED_FOUNDATION_SCHEMA_VERSION",
    "INTEGRATED_FOUNDATION_SEAM_SCHEMA_VERSION",
    "INTEGRATED_FOUNDATION_STATE_SCHEMA_VERSION",
    "Phase4LocalQwenIntegratedFoundationError",
    "run_phase4_local_qwen_integrated_savepoint_foundation",
]
