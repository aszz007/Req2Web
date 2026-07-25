"""No-action AutoDL A1 fail-closed foundation.

This module intentionally contains no worker, model-loading, generation,
network-namespace, Provider, SSH, AutoDL, or external-action implementation.
It can only validate recorded local control artifacts and emit a typed failed
receipt for independent review.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

from .tier_b_readiness import create_tier_b_manager_run_plan as _manager_plan_factory

AUTODL_A1_APPROVED_DISPOSITION_SCHEMA_VERSION = "req2web.runtime.autodl_a1_approved_disposition.v3"
AUTODL_A1_PACKAGE_MANIFEST_SCHEMA_VERSION = "req2web.runtime.autodl_a1_package_manifest.v3"
AUTODL_A1_PREFLIGHT_PLAN_SCHEMA_VERSION = "req2web.runtime.autodl_a1_preflight_plan.v3"
AUTODL_A1_PREFLIGHT_CONTROL_EVIDENCE_SCHEMA_VERSION = "req2web.runtime.autodl_a1_control_evidence.v3"
AUTODL_A1_OPERATIONAL_RECEIPT_SCHEMA_VERSION = "req2web.runtime.autodl_a1_operational_receipt.v3"
D17_GATE_REQUEST_SHA256 = "17c18c91e0bd9424a9c8c73f59e9d21e255bc9288459233b69658bdfc09aedf9"
D17_DECISION_SET_SHA256 = "6a9eb554d77d3e872e194545459c48a951d257718f71c037dda434312bba7669"
D17_FIELD_POLICY_SHA256 = "fb12efc6a0bd0d61b7ab4fea020c5e8053283b58406da37326d7cc02a7e47438"
LOCAL_SMOKE_REPORT_SHA256 = "84d3dba4fc374a750b44a2047db9928149765e6f0f6277b872067f0a0446cd15"
FAILURE_WORKER_NOT_EXECUTED = "operational_worker_not_executed_in_this_boundary"
_FAILURE_REASONS = (
    FAILURE_WORKER_NOT_EXECUTED,
    "deployment_package_precheck_failed",
    "approval_window_invalid",
)
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TIME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_DISP = (
    "schema_version", "disposition_id", "disposition", "approval_record_sha256",
    "approval_timestamp_utc", "authorization_expiry_utc", "action_time_git_sha",
    "d17_gate_request_sha256", "d17_decision_set_sha256", "d17_field_policy_sha256",
    "manager_run_plan_sha256", "local_smoke_identity_sha256",
    "deployment_package_manifest_sha256",
)
_MANIFEST = ("schema_version", "manifest_id", "source_commit_sha", "files")
_PLAN = (
    "schema_version", "plan_id", "approved_disposition", "deployment_package_manifest",
    "attempt_index", "provider_invoked", "project_data_transferred",
    "compatibility_run_occurred", "h1_allowed", "formal_quality_allowed",
    "a2_auto_unlock_allowed",
)
_CONTROL = ("schema_version", "control_evidence_id", "review_state", "evidence_inventory")
_OUTCOME = ("schema_version", "outcome_id", "plan_id", "reason", "measurement_complete")
_RECEIPT = (
    "schema_version", "receipt_id", "plan_id", "plan_sha256", "control_evidence_id",
    "control_evidence_sha256", "outcome", "status", "next_state", "failure_codes",
    "provider_invoked", "project_data_transferred", "compatibility_run_occurred",
    "h1_allowed", "formal_quality_allowed", "a2_unlocked",
)


class AutoDLA1PreflightError(ValueError):
    pass


# Capture the trust-critical decision values and canonical/hash/parser helpers at
# module definition time. This prevents later module-global rebinding from
# changing the validator's approved threat-model authority.
def _captured_canon(value, _dumps=json.dumps):
    return _dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _captured_sha(value, _sha256=hashlib.sha256):
    return _sha256(value).hexdigest()


def _captured_id(prefix, value, _canon_fn=_captured_canon, _sha_fn=_captured_sha):
    return prefix + _sha_fn(_canon_fn(dict(value)))[:20]


def _captured_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise AutoDLA1PreflightError("duplicate_json_key")
        result[key] = value
    return result


def _captured_constant(_):
    raise AutoDLA1PreflightError("nonfinite_json_rejected")


def _captured_json(
    raw,
    _loads=json.loads,
    _decode_error=json.JSONDecodeError,
    _pairs_fn=_captured_pairs,
    _constant_fn=_captured_constant,
    _canon_fn=_captured_canon,
):
    if type(raw) is not bytes:
        raise AutoDLA1PreflightError("bytes_required")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise AutoDLA1PreflightError("utf8_bom_rejected")
    try:
        value = _loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs_fn, parse_constant=_constant_fn
        )
    except (UnicodeDecodeError, _decode_error) as exc:
        raise AutoDLA1PreflightError("json_invalid") from exc
    if _canon_fn(value) != raw:
        raise AutoDLA1PreflightError("noncanonical_json")
    return value


def _captured_map(value, keys, code, _mapping=Mapping):
    if not isinstance(value, _mapping) or set(value) != set(keys):
        raise AutoDLA1PreflightError(code)
    return value


def _captured_hex(value, code, short=False, _hex40=_HEX40, _hex64=_HEX64):
    if type(value) is not str or (_hex40 if short else _hex64).fullmatch(value) is None:
        raise AutoDLA1PreflightError(code)
    return value


def _captured_time(value, code, _time=_TIME, _parse=datetime.strptime):
    if type(value) is not str or _time.fullmatch(value) is None:
        raise AutoDLA1PreflightError(code)
    try:
        _parse(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise AutoDLA1PreflightError(code) from exc
    return value


def _captured_datetime(value, _parse=datetime.strptime, _utc=timezone.utc):
    return _parse(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_utc)


def _captured_now(_datetime=datetime, _utc=timezone.utc):
    return _datetime.now(_utc)


_TRUST = MappingProxyType(
    {
        "disposition_schema": AUTODL_A1_APPROVED_DISPOSITION_SCHEMA_VERSION,
        "disposition_keys": _DISP,
        "manifest_keys": _MANIFEST,
        "plan_keys": _PLAN,
        "control_keys": _CONTROL,
        "outcome_keys": _OUTCOME,
        "receipt_keys": _RECEIPT,
        "manifest_schema": AUTODL_A1_PACKAGE_MANIFEST_SCHEMA_VERSION,
        "plan_schema": AUTODL_A1_PREFLIGHT_PLAN_SCHEMA_VERSION,
        "control_schema": AUTODL_A1_PREFLIGHT_CONTROL_EVIDENCE_SCHEMA_VERSION,
        "receipt_schema": AUTODL_A1_OPERATIONAL_RECEIPT_SCHEMA_VERSION,
        "d17_gate": D17_GATE_REQUEST_SHA256,
        "d17_decision_set": D17_DECISION_SET_SHA256,
        "d17_field_policy": D17_FIELD_POLICY_SHA256,
        "local_smoke": LOCAL_SMOKE_REPORT_SHA256,
        "manager_plan": _manager_plan_factory().sha256(),
        "failure_reasons": _FAILURE_REASONS,
        "worker_not_executed": FAILURE_WORKER_NOT_EXECUTED,
        "control_review_state": "awaiting_independent_control_plane_validation",
        "receipt_status": "a1_observation_failed_cleanup_required",
        "receipt_next_state": "a3_cleanup_release_revoke_required",
        "approval_lifetime": timedelta(days=7),
    }
)


@dataclass(frozen=True)
class AutoDLA1ApprovedDisposition:
    data: Mapping[str, object]

    @classmethod
    def from_bytes(cls, raw, _json_fn=_captured_json):
        return cls.from_dict(_json_fn(raw))

    @classmethod
    def from_dict(cls, value, _map_fn=_captured_map, _trust=_TRUST):
        data = dict(_map_fn(value, _trust["disposition_keys"], "disposition_exact_keys_invalid"))
        result = cls(data)
        result.validate()
        return result

    def validate(
        self,
        _map_fn=_captured_map,
        _hex_fn=_captured_hex,
        _time_fn=_captured_time,
        _datetime_fn=_captured_datetime,
        _id_fn=_captured_id,
        _trust=_TRUST,
    ):
        data = self.data
        _map_fn(data, _trust["disposition_keys"], "disposition_exact_keys_invalid")
        fixed = {
            "schema_version": _trust["disposition_schema"],
            "disposition": "approved",
            "d17_gate_request_sha256": _trust["d17_gate"],
            "d17_decision_set_sha256": _trust["d17_decision_set"],
            "d17_field_policy_sha256": _trust["d17_field_policy"],
            "manager_run_plan_sha256": _trust["manager_plan"],
            "local_smoke_identity_sha256": _trust["local_smoke"],
        }
        if any(data[key] != expected or type(data[key]) is not type(expected) for key, expected in fixed.items()):
            raise AutoDLA1PreflightError("disposition_frozen_binding_invalid")
        for key in ("approval_record_sha256", "deployment_package_manifest_sha256"):
            _hex_fn(data[key], "disposition_hash_invalid")
        _hex_fn(data["action_time_git_sha"], "disposition_action_sha_invalid", True)
        _time_fn(data["approval_timestamp_utc"], "disposition_time_invalid")
        _time_fn(data["authorization_expiry_utc"], "disposition_expiry_invalid")
        if _datetime_fn(data["authorization_expiry_utc"]) != _datetime_fn(data["approval_timestamp_utc"]) + _trust["approval_lifetime"]:
            raise AutoDLA1PreflightError("disposition_expiry_invalid")
        root = {key: data[key] for key in _trust["disposition_keys"] if key != "disposition_id"}
        if data["disposition_id"] != _id_fn("autodl-a1-disposition-", root):
            raise AutoDLA1PreflightError("disposition_identity_invalid")

    def to_dict(self):
        self.validate()
        return dict(self.data)

    def canonical_bytes(self, _canon_fn=_captured_canon):
        return _canon_fn(self.to_dict())

    def sha256(self, _sha_fn=_captured_sha):
        return _sha_fn(self.canonical_bytes())


@dataclass(frozen=True)
class AutoDLA1DeploymentPackageManifest:
    data: Mapping[str, object]

    @classmethod
    def from_bytes(cls, raw, _json_fn=_captured_json):
        return cls.from_dict(_json_fn(raw))

    @classmethod
    def from_dict(cls, value, _map_fn=_captured_map, _path=_PATH, _hex_fn=_captured_hex, _trust=_TRUST):
        data = dict(_map_fn(value, _trust["manifest_keys"], "package_manifest_exact_keys_invalid"))
        rows = data["files"]
        if type(rows) is not list or not rows:
            raise AutoDLA1PreflightError("package_manifest_files_invalid")
        paths = []
        for row in rows:
            _map_fn(row, ("path", "bytes", "sha256"), "package_manifest_row_invalid")
            if type(row["path"]) is not str or _path.fullmatch(row["path"]) is None or row["path"].startswith("../"):
                raise AutoDLA1PreflightError("package_manifest_path_invalid")
            if type(row["bytes"]) is not int or row["bytes"] < 0:
                raise AutoDLA1PreflightError("package_manifest_bytes_invalid")
            _hex_fn(row["sha256"], "package_manifest_hash_invalid")
            paths.append(row["path"])
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise AutoDLA1PreflightError("package_manifest_files_invalid")
        result = cls(data)
        result.validate()
        return result

    def validate(self, _map_fn=_captured_map, _hex_fn=_captured_hex, _id_fn=_captured_id, _trust=_TRUST):
        data = self.data
        _map_fn(data, _trust["manifest_keys"], "package_manifest_exact_keys_invalid")
        if data["schema_version"] != _trust["manifest_schema"]:
            raise AutoDLA1PreflightError("package_manifest_schema_invalid")
        _hex_fn(data["source_commit_sha"], "package_manifest_commit_invalid", True)
        root = {key: data[key] for key in _trust["manifest_keys"] if key != "manifest_id"}
        if data["manifest_id"] != _id_fn("autodl-a1-package-", root):
            raise AutoDLA1PreflightError("package_manifest_identity_invalid")

    def validate_against_root(self, root, _path_type=Path, _sha_fn=_captured_sha):
        self.validate()
        root = _path_type(root).resolve(strict=True)
        expected = {row["path"]: row for row in self.data["files"]}
        actual = {}
        for item in root.rglob("*"):
            relative = item.relative_to(root).as_posix()
            if item.is_symlink():
                raise AutoDLA1PreflightError("deployment_package_symlink_or_reparse")
            if item.is_file():
                actual[relative] = item
        if set(actual) != set(expected):
            raise AutoDLA1PreflightError("deployment_package_regular_file_set_invalid")
        for path, row in expected.items():
            item = actual[path]
            if item.stat().st_size != row["bytes"] or _sha_fn(item.read_bytes()) != row["sha256"]:
                raise AutoDLA1PreflightError("deployment_package_identity_drift")

    def to_dict(self):
        self.validate()
        return dict(self.data)

    def canonical_bytes(self, _canon_fn=_captured_canon):
        return _canon_fn(self.to_dict())

    def sha256(self, _sha_fn=_captured_sha):
        return _sha_fn(self.canonical_bytes())


@dataclass(frozen=True)
class AutoDLA1PreflightPlan:
    data: Mapping[str, object]

    @classmethod
    def create(
        cls,
        disposition,
        manifest,
        _disposition_type=AutoDLA1ApprovedDisposition,
        _manifest_type=AutoDLA1DeploymentPackageManifest,
        _id_fn=_captured_id,
        _trust=_TRUST,
    ):
        if type(disposition) is not _disposition_type or type(manifest) is not _manifest_type:
            raise AutoDLA1PreflightError("plan_authority_type_invalid")
        disposition = _disposition_type.from_dict(disposition.data)
        manifest = _manifest_type.from_dict(manifest.data)
        if (
            disposition.data["deployment_package_manifest_sha256"] != manifest.sha256()
            or disposition.data["action_time_git_sha"] != manifest.data["source_commit_sha"]
        ):
            raise AutoDLA1PreflightError("plan_source_or_manifest_binding_invalid")
        root = {
            "schema_version": _trust["plan_schema"],
            "approved_disposition": disposition.to_dict(),
            "deployment_package_manifest": manifest.to_dict(),
            "attempt_index": 1,
            "provider_invoked": False,
            "project_data_transferred": False,
            "compatibility_run_occurred": False,
            "h1_allowed": False,
            "formal_quality_allowed": False,
            "a2_auto_unlock_allowed": False,
        }
        root["plan_id"] = _id_fn("autodl-a1-plan-", root)
        return cls.from_dict(root)

    @classmethod
    def from_bytes(cls, raw, _json_fn=_captured_json):
        return cls.from_dict(_json_fn(raw))

    @classmethod
    def from_dict(
        cls,
        value,
        _map_fn=_captured_map,
        _disposition_type=AutoDLA1ApprovedDisposition,
        _manifest_type=AutoDLA1DeploymentPackageManifest,
        _id_fn=_captured_id,
        _trust=_TRUST,
    ):
        data = dict(_map_fn(value, _trust["plan_keys"], "plan_exact_keys_invalid"))
        disposition = _disposition_type.from_dict(data["approved_disposition"])
        manifest = _manifest_type.from_dict(data["deployment_package_manifest"])
        if (
            disposition.data["deployment_package_manifest_sha256"] != manifest.sha256()
            or disposition.data["action_time_git_sha"] != manifest.data["source_commit_sha"]
        ):
            raise AutoDLA1PreflightError("plan_source_or_manifest_binding_invalid")
        fixed = {
            "schema_version": _trust["plan_schema"],
            "attempt_index": 1,
            "provider_invoked": False,
            "project_data_transferred": False,
            "compatibility_run_occurred": False,
            "h1_allowed": False,
            "formal_quality_allowed": False,
            "a2_auto_unlock_allowed": False,
        }
        if any(data[key] != expected or type(data[key]) is not type(expected) for key, expected in fixed.items()):
            raise AutoDLA1PreflightError("plan_values_invalid")
        root = {key: data[key] for key in _trust["plan_keys"] if key != "plan_id"}
        if data["plan_id"] != _id_fn("autodl-a1-plan-", root):
            raise AutoDLA1PreflightError("plan_identity_invalid")
        return cls(data)

    def validate(self):
        type(self).from_dict(self.data)

    @property
    def plan_id(self):
        self.validate()
        return self.data["plan_id"]

    def to_dict(self):
        self.validate()
        return dict(self.data)

    def canonical_bytes(self, _canon_fn=_captured_canon):
        return _canon_fn(self.to_dict())

    def sha256(self, _sha_fn=_captured_sha):
        return _sha_fn(self.canonical_bytes())


@dataclass(frozen=True)
class AutoDLA1ControlPlaneEvidence:
    data: Mapping[str, object]

    @classmethod
    def from_bytes(cls, raw, _json_fn=_captured_json):
        return cls.from_dict(_json_fn(raw))

    @classmethod
    def from_dict(cls, value, _map_fn=_captured_map, _time_fn=_captured_time, _hex_fn=_captured_hex, _path=_PATH, _id_fn=_captured_id, _trust=_TRUST):
        data = dict(_map_fn(value, _trust["control_keys"], "control_exact_keys_invalid"))
        if data["schema_version"] != _trust["control_schema"] or data["review_state"] != _trust["control_review_state"]:
            raise AutoDLA1PreflightError("control_review_state_invalid")
        rows = data["evidence_inventory"]
        if type(rows) is not list or tuple(row.get("kind") for row in rows) != ("instance", "price", "ssh", "cleanup_readiness"):
            raise AutoDLA1PreflightError("control_inventory_invalid")
        for row in rows:
            _map_fn(row, ("kind", "source", "observed_at_utc", "evidence_sha256"), "control_row_invalid")
            if type(row["source"]) is not str or _path.fullmatch(row["source"]) is None:
                raise AutoDLA1PreflightError("control_source_invalid")
            _time_fn(row["observed_at_utc"], "control_time_invalid")
            _hex_fn(row["evidence_sha256"], "control_hash_invalid")
        root = {key: data[key] for key in _trust["control_keys"] if key != "control_evidence_id"}
        if data["control_evidence_id"] != _id_fn("autodl-a1-controls-", root):
            raise AutoDLA1PreflightError("control_identity_invalid")
        return cls(data)

    def validate(self):
        type(self).from_dict(self.data)

    def to_dict(self):
        self.validate()
        return dict(self.data)

    def canonical_bytes(self, _canon_fn=_captured_canon):
        return _canon_fn(self.to_dict())

    def sha256(self, _sha_fn=_captured_sha):
        return _sha_fn(self.canonical_bytes())


@dataclass(frozen=True)
class AutoDLA1NoActionOutcome:
    data: Mapping[str, object]

    @classmethod
    def create(cls, plan, reason, _plan_type=AutoDLA1PreflightPlan, _id_fn=_captured_id, _trust=_TRUST):
        if type(plan) is not _plan_type:
            raise AutoDLA1PreflightError("outcome_plan_type_invalid")
        plan = _plan_type.from_dict(plan.data)
        if reason not in _trust["failure_reasons"]:
            raise AutoDLA1PreflightError("outcome_reason_invalid")
        root = {
            "schema_version": "req2web.runtime.autodl_a1_no_action_outcome.v1",
            "plan_id": plan.plan_id,
            "reason": reason,
            "measurement_complete": False,
        }
        root["outcome_id"] = _id_fn("autodl-a1-outcome-", root)
        return cls.from_dict(root)

    @classmethod
    def from_dict(cls, value, _map_fn=_captured_map, _id_fn=_captured_id, _trust=_TRUST):
        data = dict(_map_fn(value, _trust["outcome_keys"], "outcome_exact_keys_invalid"))
        if data["reason"] not in _trust["failure_reasons"] or data["measurement_complete"] is not False:
            raise AutoDLA1PreflightError("outcome_not_fail_closed")
        root = {key: data[key] for key in _trust["outcome_keys"] if key != "outcome_id"}
        if data["outcome_id"] != _id_fn("autodl-a1-outcome-", root):
            raise AutoDLA1PreflightError("outcome_identity_invalid")
        return cls(data)

    @classmethod
    def from_bytes(cls, raw, _json_fn=_captured_json):
        return cls.from_dict(_json_fn(raw))

    def validate(self):
        type(self).from_dict(self.data)

    def to_dict(self):
        self.validate()
        return dict(self.data)

    def canonical_bytes(self, _canon_fn=_captured_canon):
        return _canon_fn(self.to_dict())


@dataclass(frozen=True)
class AutoDLA1OperationalReceipt:
    data: Mapping[str, object]

    @classmethod
    def create(
        cls,
        plan,
        controls,
        outcome,
        _plan_type=AutoDLA1PreflightPlan,
        _control_type=AutoDLA1ControlPlaneEvidence,
        _outcome_type=AutoDLA1NoActionOutcome,
        _id_fn=_captured_id,
        _trust=_TRUST,
    ):
        if type(plan) is not _plan_type or type(controls) is not _control_type or type(outcome) is not _outcome_type:
            raise AutoDLA1PreflightError("receipt_input_type_invalid")
        plan = _plan_type.from_dict(plan.data)
        controls = _control_type.from_dict(controls.data)
        outcome = _outcome_type.from_dict(outcome.data)
        if outcome.data["plan_id"] != plan.plan_id:
            raise AutoDLA1PreflightError("receipt_outcome_plan_binding_invalid")
        root = {
            "schema_version": _trust["receipt_schema"],
            "plan_id": plan.plan_id,
            "plan_sha256": plan.sha256(),
            "control_evidence_id": controls.data["control_evidence_id"],
            "control_evidence_sha256": controls.sha256(),
            "outcome": outcome.to_dict(),
            "status": _trust["receipt_status"],
            "next_state": _trust["receipt_next_state"],
            "failure_codes": [outcome.data["reason"]],
            "provider_invoked": False,
            "project_data_transferred": False,
            "compatibility_run_occurred": False,
            "h1_allowed": False,
            "formal_quality_allowed": False,
            "a2_unlocked": False,
        }
        root["receipt_id"] = _id_fn("autodl-a1-receipt-", root)
        return cls.from_dict(root)

    @classmethod
    def from_dict(cls, value, _map_fn=_captured_map, _outcome_type=AutoDLA1NoActionOutcome, _id_fn=_captured_id, _trust=_TRUST):
        data = dict(_map_fn(value, _trust["receipt_keys"], "receipt_exact_keys_invalid"))
        outcome = _outcome_type.from_dict(data["outcome"])
        if outcome.data["plan_id"] != data["plan_id"]:
            raise AutoDLA1PreflightError("receipt_outcome_plan_binding_invalid")
        if type(data["failure_codes"]) is not list or any(type(code) is not str for code in data["failure_codes"]) or data["failure_codes"] != [outcome.data["reason"]]:
            raise AutoDLA1PreflightError("receipt_failure_codes_invalid")
        if data["schema_version"] != _trust["receipt_schema"] or data["status"] != _trust["receipt_status"] or data["next_state"] != _trust["receipt_next_state"]:
            raise AutoDLA1PreflightError("receipt_success_schema_forbidden")
        for key in ("provider_invoked", "project_data_transferred", "compatibility_run_occurred", "h1_allowed", "formal_quality_allowed", "a2_unlocked"):
            if data[key] is not False:
                raise AutoDLA1PreflightError("receipt_boundary_invalid")
        root = {key: data[key] for key in _trust["receipt_keys"] if key != "receipt_id"}
        if data["receipt_id"] != _id_fn("autodl-a1-receipt-", root):
            raise AutoDLA1PreflightError("receipt_identity_invalid")
        return cls(data)

    @classmethod
    def from_bytes(cls, raw, _json_fn=_captured_json):
        return cls.from_dict(_json_fn(raw))

    def validate(self):
        type(self).from_dict(self.data)

    def validate_against(
        self,
        plan,
        controls,
        outcome,
        _receipt_type=None,
        _plan_type=AutoDLA1PreflightPlan,
        _control_type=AutoDLA1ControlPlaneEvidence,
        _outcome_type=AutoDLA1NoActionOutcome,
    ):
        receipt_type = _receipt_type or type(self)
        if type(plan) is not _plan_type or type(controls) is not _control_type or type(outcome) is not _outcome_type:
            raise AutoDLA1PreflightError("receipt_input_type_invalid")
        receipt = receipt_type.from_dict(self.data)
        plan = _plan_type.from_dict(plan.data)
        controls = _control_type.from_dict(controls.data)
        outcome = _outcome_type.from_dict(outcome.data)
        if outcome.data["plan_id"] != plan.plan_id or receipt.data["plan_id"] != plan.plan_id:
            raise AutoDLA1PreflightError("receipt_outcome_plan_binding_invalid")
        if receipt.to_dict() != receipt_type.create(plan, controls, outcome).to_dict():
            raise AutoDLA1PreflightError("receipt_replay_invalid")

    def to_dict(self):
        self.validate()
        return dict(self.data)

    def canonical_bytes(self, _canon_fn=_captured_canon):
        return _canon_fn(self.to_dict())


def run_autodl_a1_enforcement(
    plan,
    controls,
    package_root,
    _plan_type=AutoDLA1PreflightPlan,
    _control_type=AutoDLA1ControlPlaneEvidence,
    _disposition_type=AutoDLA1ApprovedDisposition,
    _manifest_type=AutoDLA1DeploymentPackageManifest,
    _outcome_type=AutoDLA1NoActionOutcome,
    _receipt_type=AutoDLA1OperationalReceipt,
    _now_fn=_captured_now,
    _datetime_fn=_captured_datetime,
    _trust=_TRUST,
):
    if type(plan) is not _plan_type or type(controls) is not _control_type:
        raise AutoDLA1PreflightError("runner_input_type_invalid")
    plan = _plan_type.from_dict(plan.data)
    controls = _control_type.from_dict(controls.data)
    disposition = _disposition_type.from_dict(plan.data["approved_disposition"])
    manifest = _manifest_type.from_dict(plan.data["deployment_package_manifest"])
    reason = _trust["worker_not_executed"]
    try:
        manifest.validate_against_root(package_root)
        if not _datetime_fn(disposition.data["approval_timestamp_utc"]) <= _now_fn() <= _datetime_fn(disposition.data["authorization_expiry_utc"]):
            reason = "approval_window_invalid"
    except AutoDLA1PreflightError:
        reason = "deployment_package_precheck_failed"
    return _receipt_type.create(plan, controls, _outcome_type.create(plan, reason))


def validate_autodl_a1_preflight_plan_bytes(raw, _plan_type=AutoDLA1PreflightPlan):
    return _plan_type.from_bytes(raw)


def validate_autodl_a1_control_evidence_bytes(raw, _control_type=AutoDLA1ControlPlaneEvidence):
    return _control_type.from_bytes(raw)


def validate_autodl_a1_operational_receipt_bytes(
    raw,
    plan,
    controls,
    outcome,
    _receipt_type=AutoDLA1OperationalReceipt,
    _plan_type=AutoDLA1PreflightPlan,
    _control_type=AutoDLA1ControlPlaneEvidence,
    _outcome_type=AutoDLA1NoActionOutcome,
):
    if type(plan) is not _plan_type or type(controls) is not _control_type or type(outcome) is not _outcome_type:
        raise AutoDLA1PreflightError("receipt_input_type_invalid")
    plan = _plan_type.from_dict(plan.data)
    controls = _control_type.from_dict(controls.data)
    outcome = _outcome_type.from_dict(outcome.data)
    receipt = _receipt_type.from_bytes(raw)
    receipt.validate_against(plan, controls, outcome)
    return receipt


def _main(argv):
    # Explicitly reject every historical executable mode, including --worker.
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(__import__("sys").argv))