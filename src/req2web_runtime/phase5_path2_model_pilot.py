"""Prepare a bounded project-authored Path 2 real-model pilot.

This pilot is an action-time pipeline/runtime check. It is not H1, does not
contain real hidden material or evaluator gold, and cannot support a formal
quality claim.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Mapping

from .phase5_action_authority import (
    Phase5FinalActionAuthority,
    create_phase5_final_action_authority,
    write_phase5_final_action_authority,
)
from .phase5_sealed_action_package import (
    FORMAL_AUTHORITY_SHA256,
    FORMAL_PLAN_SHA256,
    OWNER_CUSTODY_LAYOUT_SHA256,
    PATH2_ROUTE,
    RTX5090_PROFILE_SHA256,
    SINGLE_OWNER_PROTOCOL_SHA256,
    Phase5SealedActionPackage,
    create_phase5_sealed_action_package,
    write_phase5_sealed_action_package,
)


PREPARATION_SCHEMA_VERSION = "req2web.phase5.path2_model_pilot.preparation.v1"
OWNER_CONFIRMATION_TEXT = (
    "project owner authorized all implementation and action steps that remain "
    "within the already documented Phase 5 direction"
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _write_once(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _commit(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("Path 2 pilot source commit is invalid")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _support_artifacts(
    *,
    run_id: str,
    source_action_commit: str,
    runtime_row_count: int,
) -> dict[str, dict[str, object]]:
    common = {
        "route": PATH2_ROUTE,
        "run_id": run_id,
        "source_action_commit": source_action_commit,
        "real_h1_or_gold": False,
        "formal_quality_claimed": False,
    }
    return {
        "formal_manifest": {
            "schema_version": "req2web.phase5.path2_model_pilot.manifest.v1",
            **common,
            "experiment_kind": "project_authored_synthetic_model_pilot",
            "independent_case_count": 1,
            "runtime_row_count": runtime_row_count,
            "node_count_per_runtime_row": 4,
            "node_generate_call_cap": runtime_row_count * 4,
            "automatic_retry_allowed": False,
            "result_driven_change_allowed": False,
        },
        "duplicate_audit": {
            "schema_version": (
                "req2web.phase5.path2_model_pilot.duplicate_audit.v1"
            ),
            **common,
            "scope": "tracked_project_authored_synthetic_pilot_fixture",
            "decision": "development_fixture_reuse_allowed_pilot_not_h1",
            "formal_holdout_duplicate_claimed": False,
        },
        "gold_commitment": {
            "schema_version": (
                "req2web.phase5.path2_model_pilot.gold_commitment.v1"
            ),
            **common,
            "status": "not_applicable_pipeline_pilot",
            "gold_content_present": False,
            "owner_score_present": False,
            "single_owner_evaluation_optional": True,
        },
        "serializer": {
            "schema_version": (
                "req2web.phase5.path2_model_pilot.serializer.v1"
            ),
            **common,
            "projection_schema": (
                "req2web.phase5.path2.static_provider_projection.v1"
            ),
            "gold_visible": False,
            "local_identifiers_visible": False,
            "intervention_name_visible": False,
        },
        "provider_parity": {
            "schema_version": (
                "req2web.phase5.path2_model_pilot.provider_parity.v1"
            ),
            **common,
            "status": "canonical_local_bytes_bound_for_remote_replay",
            "payload_rewrite_allowed": False,
        },
        "metric_threshold": {
            "schema_version": (
                "req2web.phase5.path2_model_pilot.metric_threshold.v1"
            ),
            **common,
            "runtime_objective": (
                "raw_contract_and_assembly_pipeline_observation_only"
            ),
            "formal_quality_threshold_present": False,
            "all_failures_retained": True,
        },
        "g0_inventory": {
            "schema_version": (
                "req2web.phase5.path2_model_pilot.g0_inventory.v1"
            ),
            **common,
            "status": "not_materialized_path2_pilot_no_fallback",
            "fallback_allowed": False,
            "inventory": [],
        },
    }


@dataclass(frozen=True)
class Phase5Path2ModelPilotPreparation:
    output_root: Path
    authority: Phase5FinalActionAuthority
    package: Phase5SealedActionPackage
    receipt: Mapping[str, object]


def prepare_phase5_path2_model_pilot(
    *,
    fixture_path: Path,
    output_root: Path,
    source_action_commit: str,
    run_id: str,
    instance_id: str,
    gpu_uuid: str,
    ssh_fingerprint_sha256: str,
    repository_root: str,
    model_root: str,
    model_integrity_evidence: str,
    python_executable: str,
    result_root: str,
    hourly_rate_minor_units: int,
    time_cap_seconds: int = 7_200,
    cost_cap_minor_units: int = 5_000,
    storage_cap_bytes: int = 1_073_741_824,
) -> Phase5Path2ModelPilotPreparation:
    """Create all hash-only pilot authority artifacts and the sealed package."""

    source_action_commit = _commit(source_action_commit)
    run_id = _text(run_id, "Path 2 pilot run id")
    instance_id = _text(instance_id, "Path 2 pilot instance id")
    gpu_uuid = _text(gpu_uuid, "Path 2 pilot GPU UUID")
    ssh_fingerprint_sha256 = _text(
        ssh_fingerprint_sha256,
        "Path 2 pilot SSH fingerprint hash",
    )
    if (
        len(ssh_fingerprint_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in ssh_fingerprint_sha256
        )
    ):
        raise ValueError("Path 2 pilot SSH fingerprint hash is invalid")
    for value, name in (
        (hourly_rate_minor_units, "hourly rate"),
        (time_cap_seconds, "time cap"),
        (cost_cap_minor_units, "cost cap"),
        (storage_cap_bytes, "storage cap"),
    ):
        _integer(
            value,
            f"Path 2 pilot {name}",
            minimum=0 if name == "hourly rate" else 1,
        )
    if not isinstance(fixture_path, Path) or not fixture_path.is_file():
        raise ValueError("Path 2 pilot fixture path is invalid")
    if not isinstance(output_root, Path) or not output_root.is_absolute():
        raise ValueError("Path 2 pilot output root must be absolute")
    if output_root.exists():
        raise ValueError("Path 2 pilot output root must be new")
    output_root.mkdir(parents=False, exist_ok=False)

    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes.decode("utf-8"))
    if not isinstance(fixture, dict):
        raise ValueError("Path 2 pilot fixture is invalid")
    package_source = copy.deepcopy(fixture)
    package_source.pop("package_id", None)
    package_source["package_kind"] = "project_authored_path2_model_pilot"
    package_source["route"] = PATH2_ROUTE
    package_source["run_id"] = run_id
    package_source["source_action_commit"] = source_action_commit
    package_source["budget"]["time_cap_seconds"] = time_cap_seconds
    package_source["budget"]["cost_cap_minor_units"] = cost_cap_minor_units
    package_source["budget"]["storage_cap_bytes"] = storage_cap_bytes
    for row in package_source["runtime_rows"]:
        row["g0_reference"]["status"] = (
            "not_materialized_path2_pilot_no_fallback"
        )
    package_source["action_state"].update(
        {
            "owner_sealed": True,
            "contains_real_h1_projection": False,
            "final_action_authorized": True,
            "model_action_authorized": True,
            "gpu_or_remote_action_authorized": True,
        }
    )

    artifacts = _support_artifacts(
        run_id=run_id,
        source_action_commit=source_action_commit,
        runtime_row_count=len(package_source["runtime_rows"]),
    )
    artifact_hashes: dict[str, str] = {}
    artifact_inventory: list[dict[str, object]] = []
    for name, value in artifacts.items():
        raw = _canonical(value)
        filename = f"{name}.json"
        _write_once(output_root / filename, raw)
        artifact_hashes[name] = _sha(raw)
        artifact_inventory.append(
            {
                "filename": filename,
                "sha256": _sha(raw),
                "byte_length": len(raw),
            }
        )

    bindings = {
        "formal_authority_sha256": FORMAL_AUTHORITY_SHA256,
        "formal_plan_sha256": FORMAL_PLAN_SHA256,
        "rtx5090_profile_sha256": RTX5090_PROFILE_SHA256,
        "single_owner_protocol_sha256": SINGLE_OWNER_PROTOCOL_SHA256,
        "owner_custody_layout_sha256": OWNER_CUSTODY_LAYOUT_SHA256,
        "formal_manifest_sha256": artifact_hashes["formal_manifest"],
        "duplicate_audit_sha256": artifact_hashes["duplicate_audit"],
        "gold_commitment_sha256": artifact_hashes["gold_commitment"],
        "serializer_sha256": artifact_hashes["serializer"],
        "provider_parity_sha256": artifact_hashes["provider_parity"],
        "metric_threshold_sha256": artifact_hashes["metric_threshold"],
        "g0_inventory_sha256": artifact_hashes["g0_inventory"],
    }
    authority = create_phase5_final_action_authority(
        {
            "schema_version": "req2web.phase5.final_action_authority.v1",
            "status": "owner_approved_ready_for_exact_action",
            "route": PATH2_ROUTE,
            "run_id": run_id,
            "source_action_commit": source_action_commit,
            "authority_bindings": bindings,
            "instance": {
                "instance_id": instance_id,
                "gpu_uuid": gpu_uuid,
                "gpu_class": "nvidia_geforce_rtx_5090",
                "device_index": 0,
                "ssh_fingerprint_sha256": ssh_fingerprint_sha256,
            },
            "limits": {
                "hourly_rate_minor_units": hourly_rate_minor_units,
                "time_cap_seconds": time_cap_seconds,
                "cost_cap_minor_units": cost_cap_minor_units,
                "storage_cap_bytes": storage_cap_bytes,
            },
            "paths": {
                "repository_root": repository_root,
                "model_root": model_root,
                "model_integrity_evidence": model_integrity_evidence,
                "python_executable": python_executable,
                "result_root": result_root,
            },
            "owner_confirmation_sha256": _sha(
                OWNER_CONFIRMATION_TEXT.encode("utf-8")
            ),
            "authorization": {
                "owner_approved_exact_run": True,
                "real_h1_projection_open_allowed": False,
                "model_action_allowed": True,
                "gpu_remote_paid_action_allowed": True,
                "single_owner_evaluation_allowed": True,
                "training_allowed": False,
                "lora_allowed": False,
                "path3_allowed_in_h1": False,
                "result_driven_change_allowed": False,
            },
            "action_state": {
                "receipt_created": True,
                "ssh_connected": True,
                "model_loaded": False,
                "holdout_executed": False,
                "formal_quality_claimed": False,
            },
        }
    )
    authority_path = output_root / "final_action_authority.json"
    write_phase5_final_action_authority(authority_path, authority)
    artifact_inventory.append(
        {
            "filename": authority_path.name,
            "sha256": authority.sha256(),
            "byte_length": len(authority.canonical_json_bytes()),
        }
    )

    package_source["authority_bindings"] = {
        **bindings,
        "final_action_receipt_sha256": authority.sha256(),
    }
    package = create_phase5_sealed_action_package(package_source)
    package_path = output_root / "sealed_action_package.json"
    write_phase5_sealed_action_package(package_path, package)
    artifact_inventory.append(
        {
            "filename": package_path.name,
            "sha256": package.sha256(),
            "byte_length": len(package.canonical_json_bytes()),
        }
    )

    receipt_body = {
        "schema_version": PREPARATION_SCHEMA_VERSION,
        "status": "ready_for_bounded_path2_model_pilot",
        "route": PATH2_ROUTE,
        "run_id": run_id,
        "source_action_commit": source_action_commit,
        "source_fixture_sha256": _sha(fixture_bytes),
        "authority_sha256": authority.sha256(),
        "package_sha256": package.sha256(),
        "artifact_inventory": artifact_inventory,
        "real_h1_or_gold": False,
        "formal_quality_claimed": False,
        "model_action_occurred": False,
    }
    receipt = {
        "preparation_id": (
            "phase5-path2-pilot-preparation-" + _sha(_canonical(receipt_body))
        ),
        **receipt_body,
    }
    _write_once(output_root / "preparation_receipt.json", _canonical(receipt))
    return Phase5Path2ModelPilotPreparation(
        output_root=output_root,
        authority=authority,
        package=package,
        receipt=receipt,
    )


__all__ = [
    "OWNER_CONFIRMATION_TEXT",
    "PREPARATION_SCHEMA_VERSION",
    "Phase5Path2ModelPilotPreparation",
    "prepare_phase5_path2_model_pilot",
]
