"""No-model F4 generic-audit-ref normalization and chain revalidation."""

from __future__ import annotations

import base64
import copy
from pathlib import Path
from typing import Mapping

from req2web_orchestration.phase4_graph import (
    F4_AUDIT_REF_NORMALIZATION_REVISION,
    phase4_assemble_candidate,
    phase4_compose_candidate,
    phase4_normalize_and_validate_f4_output,
    phase4_register_node_output,
    phase4_synthetic_assembler_bindings,
    phase4_validate_f4_normalization_receipt,
)
from req2web_runtime import phase4_local_qwen_f4 as _f4


F4_REVALIDATION_SCHEMA_PREFIX = (
    "req2web.phase4.p4_03d4.local_f4_revalidation"
)
F4_REVALIDATION_RESULT_SCHEMA_VERSION = (
    f"{F4_REVALIDATION_SCHEMA_PREFIX}.result.v1"
)
F4_REVALIDATION_SOURCE_BINDING_SCHEMA_VERSION = (
    f"{F4_REVALIDATION_SCHEMA_PREFIX}.source_binding.v1"
)
F4_REVALIDATION_ROOT_MARKER = (
    ".req2web-phase4-p4-03d4-local-f4-revalidation-root"
)
F4_REVALIDATION_SOURCE_BINDING_NAME = "source_f4_failure_binding.json"
F4_REVALIDATION_NORMALIZED_OUTPUT_NAME = "normalized_f4_output.json"
F4_REVALIDATION_NORMALIZATION_RECEIPT_NAME = (
    "f4_generic_ref_normalization_receipt.json"
)
F4_REVALIDATION_REGISTERED_STATE_NAME = "normalized_f4_authority_state.json"
F4_REVALIDATION_CANDIDATE_NAME = "candidate_composition_record.json"
F4_REVALIDATION_PAGE_SPEC_NAME = "assembled_page_spec.json"
F4_REVALIDATION_ASSEMBLY_REPORT_NAME = "assembly_report.json"
F4_REVALIDATION_RESULT_NAME = "f4_revalidation_result.json"

Phase4LocalQwenF4RevalidationError = _f4.Phase4LocalQwenF4ContractError
_canonical_bytes = _f4._canonical_bytes
_strict_json = _f4._strict_json
_identity = _f4._identity
_write_once = _f4._write_once


def _safe_existing_root(path: Path, name: str) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or not path.is_dir()
        or path.is_symlink()
    ):
        raise Phase4LocalQwenF4RevalidationError(
            f"{name} must be an existing absolute non-symlink directory"
        )
    return path


def _prepare_new_root(path: Path) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path.exists()
        or not path.parent.is_dir()
        or path.parent.is_symlink()
    ):
        raise Phase4LocalQwenF4RevalidationError(
            "F4 revalidation result root must be new and safe"
        )
    path.mkdir()
    _write_once(
        path,
        F4_REVALIDATION_ROOT_MARKER,
        _canonical_bytes(
            {
                "schema_version": (
                    f"{F4_REVALIDATION_SCHEMA_PREFIX}.root_marker.v1"
                ),
                "result_root_leaf": path.name,
                "model_action": False,
                "model_generate_calls": 0,
            }
        ),
    )
    return path


def _source_failure_binding(source_result_root: Path) -> tuple[dict[str, object], bytes]:
    final_path = source_result_root / _f4.F4_LOCAL_FINAL_RESULT_NAME
    raw_path = source_result_root / _f4.F4_LOCAL_RAW_NAME
    if (
        not final_path.is_file()
        or final_path.is_symlink()
        or not raw_path.is_file()
        or raw_path.is_symlink()
    ):
        raise Phase4LocalQwenF4RevalidationError(
            "source F4 failure files are unavailable"
        )
    final_raw = final_path.read_bytes()
    raw_bytes = raw_path.read_bytes()
    final = _strict_json(final_raw, require_canonical=True)
    expected_facts = {
        "pilot_id": _f4.F4_LOCAL_PILOT_ID,
        "status": "failed_closed",
        "node_model_pass": False,
        "generate_calls": 1,
        "previous_call_consumed": 2,
        "current_call_consumed": 1,
        "aggregate_after_call": 3,
        "parse": "parsed",
        "registry": "not_executed",
        "failure_code": "contract_failed",
        "raw_status": "captured",
        "worker_exit_verified": True,
    }
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
    if actual_facts != expected_facts:
        raise Phase4LocalQwenF4RevalidationError(
            "source F4 failure facts drifted"
        )
    expected_raw_identity = _identity(
        raw_bytes,
        revision=f"{F4_REVALIDATION_SCHEMA_PREFIX}.source_raw.v1",
        identity_kind="raw_bytes",
    )
    source_raw_identity = final.get("raw", {}).get("identity")
    if not isinstance(source_raw_identity, Mapping):
        raise Phase4LocalQwenF4RevalidationError(
            "source F4 raw identity is missing"
        )
    if (
        source_raw_identity.get("sha256")
        != expected_raw_identity["sha256"]
        or source_raw_identity.get("byte_length")
        != expected_raw_identity["byte_length"]
        or source_raw_identity.get("identity_kind") != "raw_bytes"
    ):
        raise Phase4LocalQwenF4RevalidationError(
            "source F4 raw identity drifted"
        )
    binding: dict[str, object] = {
        "schema_version": F4_REVALIDATION_SOURCE_BINDING_SCHEMA_VERSION,
        "binding_id": "pending",
        "resolved_source_root": str(source_result_root.resolve(strict=True)),
        "source_final_result_identity": _identity(
            final_raw,
            revision=f"{F4_REVALIDATION_SCHEMA_PREFIX}.source_result.v1",
            identity_kind="raw_bytes",
        ),
        "source_raw_identity": expected_raw_identity,
        "source_failure_facts": actual_facts,
        "normalizer_revision": F4_AUDIT_REF_NORMALIZATION_REVISION,
        "model_generate_calls": 0,
    }
    binding["binding_id"] = _identity(
        {
            key: value
            for key, value in binding.items()
            if key != "binding_id"
        },
        revision=F4_REVALIDATION_SOURCE_BINDING_SCHEMA_VERSION,
    )["sha256"]
    return binding, raw_bytes


def revalidate_phase4_local_qwen_f4_result(
    *,
    source_result_root: Path,
    f3_result_root: Path,
    result_root: Path,
    confirm_no_model_revalidation: bool,
) -> dict[str, object]:
    """Normalize the captured F4 raw and prove registry/composition/assembly."""

    if confirm_no_model_revalidation is not True:
        raise Phase4LocalQwenF4RevalidationError(
            "explicit no-model F4 revalidation confirmation is required"
        )
    _safe_existing_root(source_result_root, "source F4 result root")
    _safe_existing_root(f3_result_root, "F3 savepoint root")
    result_root = _prepare_new_root(result_root)

    source_binding, raw_bytes = _source_failure_binding(source_result_root)
    (
        _,
        _,
        _,
        _,
        _,
        _,
        authority_state,
        _,
    ) = _f4._replay_f3_savepoint_local(f3_result_root=f3_result_root)
    parsed = _strict_json(raw_bytes, require_canonical=False)
    normalized, receipt = phase4_normalize_and_validate_f4_output(
        raw_bytes=raw_bytes,
        output=parsed,
        state=authority_state,
    )
    if (
        receipt["status"] != "normalized"
        or receipt["raw_model_contract_success"] is not False
        or receipt["normalized_node_contract_success"] is not True
        or receipt["normalization_count"] < 1
    ):
        raise Phase4LocalQwenF4RevalidationError(
            "F4 normalization did not close the approved order-only failure"
        )
    phase4_validate_f4_normalization_receipt(
        receipt,
        raw_bytes=raw_bytes,
        normalized_output=normalized,
        state=authority_state,
    )
    registered_state = phase4_register_node_output(
        authority_state,
        "F4",
        normalized,
    )
    candidate_record = phase4_compose_candidate(registered_state)
    candidate_bytes = base64.b64decode(
        candidate_record["model_semantic_candidate_canonical_b64"],
        validate=True,
    )
    context, guidance = phase4_synthetic_assembler_bindings(
        registered_state
    )
    assembled = phase4_assemble_candidate(
        candidate_bytes,
        context,
        guidance,
    )
    page_spec = assembled.page_spec.to_dict()
    assembly_report = assembled.report.to_dict()

    artifacts = (
        (F4_REVALIDATION_SOURCE_BINDING_NAME, source_binding),
        (F4_REVALIDATION_NORMALIZED_OUTPUT_NAME, normalized),
        (F4_REVALIDATION_NORMALIZATION_RECEIPT_NAME, receipt),
        (F4_REVALIDATION_REGISTERED_STATE_NAME, registered_state),
        (F4_REVALIDATION_CANDIDATE_NAME, candidate_record),
        (F4_REVALIDATION_PAGE_SPEC_NAME, page_spec),
        (F4_REVALIDATION_ASSEMBLY_REPORT_NAME, assembly_report),
    )
    artifact_identities: dict[str, object] = {}
    for name, value in artifacts:
        raw = _canonical_bytes(value)
        _write_once(result_root, name, raw)
        artifact_identities[name] = _identity(
            raw,
            revision=f"{F4_REVALIDATION_SCHEMA_PREFIX}.artifact.v1",
            identity_kind="raw_bytes",
        )

    result: dict[str, object] = {
        "schema_version": F4_REVALIDATION_RESULT_SCHEMA_VERSION,
        "result_id": "pending",
        "status": "normalized_chain_ready",
        "source_binding_identity": _identity(
            source_binding,
            revision=F4_REVALIDATION_SOURCE_BINDING_SCHEMA_VERSION,
        ),
        "raw_model_contract_success": False,
        "raw_failure_code": receipt["raw_failure_code"],
        "normalized_node_contract_success": True,
        "agent_chain_system_output_usable": True,
        "normalization_count": receipt["normalization_count"],
        "normalization_counts_as_repair": False,
        "model_generate_calls": 0,
        "registry_status": "validated_and_registered",
        "composition_status": candidate_record["candidate_projection_status"],
        "assembler_status": "assembled",
        "page_spec_schema_version": page_spec["schema_version"],
        "artifact_identities": artifact_identities,
        "downstream": {
            "consistency": "not_executed",
            "acceptance": "not_executed",
            "repair": "not_executed",
            "g0": "not_executed",
            "package": "not_executed",
            "production_route": "not_executed",
        },
        "claim_boundary": (
            "amended_agent_chain_success_with_deterministic_"
            "audit_ref_normalization_not_raw_model_first_pass"
        ),
    }
    result["result_id"] = _identity(
        {
            key: value
            for key, value in result.items()
            if key != "result_id"
        },
        revision=F4_REVALIDATION_RESULT_SCHEMA_VERSION,
    )["sha256"]
    _write_once(
        result_root,
        F4_REVALIDATION_RESULT_NAME,
        _canonical_bytes(result),
    )
    return copy.deepcopy(result)
