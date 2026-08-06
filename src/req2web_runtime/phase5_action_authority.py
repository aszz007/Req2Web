"""Hash-only final action authority for one sealed Phase 5 run.

The receipt binds the already approved route, frozen non-content identities,
instance/GPU/SSH identity, price and resource caps, and exact server paths.
It contains no H1 requirement text, gold, owner score, evidence text, secret,
credential, or SSH private material.  Creating a receipt does not connect to
the server or execute the holdout.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence

from .phase5_sealed_action_package import Phase5SealedActionPackage


SCHEMA_VERSION = "req2web.phase5.final_action_authority.v1"
PATH2_SCHEMA_VERSION = "req2web.phase5.final_action_authority.v2"
_POSIX_ABSOLUTE = re.compile(r"^/(?:[^/\x00]+/)*[^/\x00]*$")
_PROHIBITED_KEYS = {
    "credential",
    "gold",
    "gold_payload",
    "owner_score",
    "password",
    "private_key",
    "requirement",
    "requirement_projection",
    "secret",
    "token",
}


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
    return value


def _digest(value: object, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _commit(value: object, name: str) -> str:
    value = _text(value, name)
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase 40-character commit")
    return value


def _integer(value: object, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _posix_path(value: object, name: str) -> str:
    value = _text(value, name)
    if (
        not _POSIX_ABSOLUTE.fullmatch(value)
        or "\\" in value
        or "/../" in f"{value}/"
        or value.endswith("/..")
    ):
        raise ValueError(f"{name} must be an absolute normalized POSIX path")
    return value


def _scan_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_scan_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_scan_keys(item))
    return keys


def _validate_payload(value: object) -> dict[str, object]:
    receipt = _exact(
        value,
        (
            "receipt_id",
            "schema_version",
            "status",
            "route",
            "run_id",
            "source_action_commit",
            "authority_bindings",
            "instance",
            "limits",
            "paths",
            "owner_confirmation_sha256",
            "authorization",
            "action_state",
        ),
        "final action authority",
    )
    route = receipt["route"]
    if route not in {
        "path_1_licensed_minimal_real_material",
        "path_2_public_or_project_authored_synthetic_fixture",
    }:
        raise ValueError("final action authority route drifted")
    expected_schema = (
        PATH2_SCHEMA_VERSION
        if route == "path_2_public_or_project_authored_synthetic_fixture"
        else SCHEMA_VERSION
    )
    if receipt["schema_version"] != expected_schema:
        raise ValueError("final action authority schema drifted")
    if receipt["status"] != "owner_approved_ready_for_exact_action":
        raise ValueError("final action authority status drifted")
    _text(receipt["run_id"], "final action run id")
    _commit(receipt["source_action_commit"], "final action source commit")
    bindings = _exact(
        receipt["authority_bindings"],
        (
            "formal_authority_sha256",
            "formal_plan_sha256",
            "rtx5090_profile_sha256",
            "single_owner_protocol_sha256",
            "owner_custody_layout_sha256",
            "formal_manifest_sha256",
            "duplicate_audit_sha256",
            "gold_commitment_sha256",
            "serializer_sha256",
            "provider_parity_sha256",
            "metric_threshold_sha256",
            "g0_inventory_sha256",
        ),
        "final action authority bindings",
    )
    for key, item in bindings.items():
        _digest(item, f"final action binding {key}")
    instance = _exact(
        receipt["instance"],
        (
            "instance_id",
            "gpu_uuid",
            "gpu_class",
            "device_index",
            "ssh_fingerprint_sha256",
        ),
        "final action instance",
    )
    _text(instance["instance_id"], "final action instance id")
    _text(instance["gpu_uuid"], "final action GPU UUID")
    if instance["gpu_class"] != "nvidia_geforce_rtx_5090":
        raise ValueError("final action GPU class drifted")
    if instance["device_index"] != 0:
        raise ValueError("final action device index drifted")
    _digest(instance["ssh_fingerprint_sha256"], "final action SSH fingerprint")
    limits = _exact(
        receipt["limits"],
        (
            "hourly_rate_minor_units",
            "time_cap_seconds",
            "cost_cap_minor_units",
            "storage_cap_bytes",
        ),
        "final action limits",
    )
    for key, minimum in (
        ("hourly_rate_minor_units", 0),
        ("time_cap_seconds", 1),
        ("cost_cap_minor_units", 1),
        ("storage_cap_bytes", 1),
    ):
        _integer(limits[key], f"final action {key}", minimum=minimum)
    paths = _exact(
        receipt["paths"],
        (
            "repository_root",
            "model_root",
            "model_integrity_evidence",
            "python_executable",
            "result_root",
        ),
        "final action paths",
    )
    for key, item in paths.items():
        _posix_path(item, f"final action path {key}")
    _digest(receipt["owner_confirmation_sha256"], "owner confirmation")
    authorization = _exact(
        receipt["authorization"],
        (
            "owner_approved_exact_run",
            "real_h1_projection_open_allowed",
            "model_action_allowed",
            "gpu_remote_paid_action_allowed",
            "single_owner_evaluation_allowed",
            "training_allowed",
            "lora_allowed",
            "path3_allowed_in_h1",
            "result_driven_change_allowed",
        ),
        "final action authorization",
    )
    for key in (
        "owner_approved_exact_run",
        "model_action_allowed",
        "gpu_remote_paid_action_allowed",
        "single_owner_evaluation_allowed",
    ):
        if authorization[key] is not True:
            raise ValueError(f"final action authorization {key} must be true")
    if authorization["real_h1_projection_open_allowed"] is not (
        route == "path_1_licensed_minimal_real_material"
    ):
        raise ValueError(
            "final action real H1 authorization drifted from selected route"
        )
    for key in (
        "training_allowed",
        "lora_allowed",
        "path3_allowed_in_h1",
        "result_driven_change_allowed",
    ):
        if authorization[key] is not False:
            raise ValueError(f"final action authorization {key} must be false")
    state = _exact(
        receipt["action_state"],
        (
            "receipt_created",
            "ssh_connected",
            "model_loaded",
            "holdout_executed",
            "formal_quality_claimed",
        ),
        "final action state",
    )
    if state["receipt_created"] is not True:
        raise ValueError("final action receipt must record its creation")
    expected_ssh_connected = (
        route == "path_2_public_or_project_authored_synthetic_fixture"
    )
    if state["ssh_connected"] is not expected_ssh_connected:
        raise ValueError(
            "final action SSH state drifted from selected action route"
        )
    for key in (
        "model_loaded",
        "holdout_executed",
        "formal_quality_claimed",
    ):
        if state[key] is not False:
            raise ValueError(f"final action state {key} must remain false")
    prohibited = _scan_keys(receipt) & _PROHIBITED_KEYS
    if prohibited:
        raise ValueError(
            f"final action authority contains prohibited keys: {sorted(prohibited)}"
        )
    body = {key: receipt[key] for key in receipt if key != "receipt_id"}
    if receipt["receipt_id"] != _record_id("phase5-final-action", body):
        raise ValueError("final action authority ID drifted")
    return receipt


@dataclass(frozen=True)
class Phase5FinalActionAuthority:
    canonical_json: str
    digest_sha256: str

    @classmethod
    def from_dict(cls, value: object) -> "Phase5FinalActionAuthority":
        payload = _validate_payload(value)
        canonical = _canonical(payload)
        result = cls(canonical.decode("utf-8"), sha256(canonical).hexdigest())
        result.validate()
        return result

    @classmethod
    def from_json_bytes(cls, value: bytes) -> "Phase5FinalActionAuthority":
        if not isinstance(value, bytes) or not value:
            raise ValueError("final action authority JSON must be non-empty bytes")
        try:
            parsed = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("final action authority JSON is invalid") from exc
        if _canonical(parsed) != value:
            raise ValueError("final action authority JSON is not canonical")
        return cls.from_dict(parsed)

    def validate(self) -> None:
        try:
            parsed = json.loads(self.canonical_json)
        except json.JSONDecodeError as exc:
            raise ValueError("stored final action authority is invalid") from exc
        canonical = _canonical(parsed)
        if canonical.decode("utf-8") != self.canonical_json:
            raise ValueError("stored final action authority is not canonical")
        if sha256(canonical).hexdigest() != self.digest_sha256:
            raise ValueError("stored final action authority digest drifted")
        _validate_payload(parsed)

    def to_dict(self) -> dict[str, object]:
        self.validate()
        value = json.loads(self.canonical_json)
        if not isinstance(value, dict):
            raise ValueError("stored final action authority root is invalid")
        return value

    def canonical_json_bytes(self) -> bytes:
        self.validate()
        return self.canonical_json.encode("utf-8")

    def sha256(self) -> str:
        self.validate()
        return self.digest_sha256


def create_phase5_final_action_authority(
    value: object,
) -> Phase5FinalActionAuthority:
    if not isinstance(value, Mapping):
        raise ValueError("final action authority source must be an object")
    payload = dict(value)
    if "receipt_id" in payload:
        return Phase5FinalActionAuthority.from_dict(payload)
    return Phase5FinalActionAuthority.from_dict(
        {
            "receipt_id": _record_id("phase5-final-action", payload),
            **payload,
        }
    )


def validate_phase5_action_authority_against_package(
    authority: Phase5FinalActionAuthority,
    package: Phase5SealedActionPackage,
) -> None:
    authority.validate()
    package.validate()
    receipt = authority.to_dict()
    sealed = package.to_dict()
    expected_kind = {
        "path_1_licensed_minimal_real_material": "owner_sealed_formal_h1",
        "path_2_public_or_project_authored_synthetic_fixture": (
            "project_authored_path2_model_pilot"
        ),
    }[receipt["route"]]
    if sealed["package_kind"] != expected_kind:
        raise ValueError("final action authority requires a formal sealed package")
    if (
        sealed["route"] != receipt["route"]
        or sealed["run_id"] != receipt["run_id"]
        or sealed["source_action_commit"] != receipt["source_action_commit"]
        or sealed["authority_bindings"]["final_action_receipt_sha256"]
        != authority.sha256()
    ):
        raise ValueError("final action authority package binding drifted")
    for key, value in receipt["authority_bindings"].items():
        if sealed["authority_bindings"][key] != value:
            raise ValueError(f"final action binding drifted: {key}")
    for key in ("time_cap_seconds", "cost_cap_minor_units", "storage_cap_bytes"):
        if sealed["budget"][key] != receipt["limits"][key]:
            raise ValueError(f"final action/package limit drifted: {key}")


def write_phase5_final_action_authority(
    path: Path,
    authority: Phase5FinalActionAuthority,
) -> None:
    authority.validate()
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("final action authority output must be absolute")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError("final action authority output parent is invalid")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(authority.canonical_json_bytes())
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


__all__ = [
    "Phase5FinalActionAuthority",
    "SCHEMA_VERSION",
    "create_phase5_final_action_authority",
    "validate_phase5_action_authority_against_package",
    "write_phase5_final_action_authority",
]
