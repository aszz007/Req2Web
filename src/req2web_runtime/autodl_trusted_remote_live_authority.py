"""Local preparation for externally signed trusted-remote live-action authority.

This module verifies an OpenSSH detached signature over a canonical, single-use
receipt.  It deliberately does not contain a signing key and performs no remote,
SSH, model, credential, or provider action.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import autodl_trusted_remote_records as _records
from . import autodl_trusted_remote_slice3 as _slice3

LIVE_AUTHORITY_SCHEMA = "req2web.runtime.trusted_remote_live_action_authority.v1"
LIVE_AUTHORITY_NAMESPACE = "req2web.stage3.trusted_remote.live_action.v1"
LIVE_AUTHORITY_PRINCIPAL = "req2web-stage3-owner"
LIVE_AUTHORITY_PREFIX = "trusted-remote-live-authority-"
REQUIRED_CASE_IDS = _records.REQUIRED_CASE_IDS
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_PROCESS_CONSUMED_NONCES = set()
_PROCESS_NONCE_LOCK = threading.Lock()


class TrustedRemoteLiveAuthorityError(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise TrustedRemoteLiveAuthorityError("duplicate_json_key")
        result[key] = value
    return result


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _loads(raw):
    if not isinstance(raw, bytes):
        raise TrustedRemoteLiveAuthorityError("canonical_bytes_required")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=lambda _x: (_ for _ in ()).throw(TrustedRemoteLiveAuthorityError("non_finite_json_forbidden")))
    except TrustedRemoteLiveAuthorityError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise TrustedRemoteLiveAuthorityError("canonical_json_invalid") from exc


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _keys(value, names, label):
    if not isinstance(value, Mapping) or set(value) != set(names) or len(value) != len(names):
        raise TrustedRemoteLiveAuthorityError(f"{label}_exact_keys_invalid")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value:
        raise TrustedRemoteLiveAuthorityError(f"{label}_invalid")
    return value


def _hex(value, label, length=64):
    value = _text(value, label)
    pattern = _HEX64 if length == 64 else _HEX40
    if not pattern.fullmatch(value):
        raise TrustedRemoteLiveAuthorityError(f"{label}_invalid")
    return value


def _integer(value, label, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise TrustedRemoteLiveAuthorityError(f"{label}_invalid")
    return value


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _unb64(value, label):
    value = _text(value, label)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise TrustedRemoteLiveAuthorityError(f"{label}_invalid") from exc
    if _b64(raw) != value:
        raise TrustedRemoteLiveAuthorityError(f"{label}_not_canonical")
    return raw


def _utc(value, label):
    value = _text(value, label)
    if not value.endswith("Z"):
        raise TrustedRemoteLiveAuthorityError(f"{label}_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise TrustedRemoteLiveAuthorityError(f"{label}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed) or parsed.microsecond:
        raise TrustedRemoteLiveAuthorityError(f"{label}_invalid")
    return parsed.astimezone(timezone.utc), value


def _identified(data):
    body = copy.deepcopy(dict(data))
    body["receipt_id"] = LIVE_AUTHORITY_PREFIX + "0" * 64
    expected = LIVE_AUTHORITY_PREFIX + _sha(_dumps(body))
    if data.get("receipt_id") not in ("", LIVE_AUTHORITY_PREFIX + "0" * 64, expected):
        raise TrustedRemoteLiveAuthorityError("receipt_id_invalid")
    body["receipt_id"] = expected
    return body


def _plan(plan):
    if type(plan) is _records.TrustedRemoteActionTimePlan:
        raw = plan.canonical_bytes()
    elif type(plan) is bytes:
        raw = plan
    else:
        raise TrustedRemoteLiveAuthorityError("action_time_plan_exact_type_or_bytes_required")
    try:
        replay = _records.TrustedRemoteActionTimePlan.from_bytes(raw)
    except Exception as exc:
        raise TrustedRemoteLiveAuthorityError("action_time_plan_replay_invalid") from exc
    if replay.canonical_bytes() != raw:
        raise TrustedRemoteLiveAuthorityError("action_time_plan_not_canonical")
    return replay.to_dict(), raw


def _instance(value, plan):
    names = ("provider", "instance_id", "gpu_model", "gpu_index", "gpu_uuid", "ssh_host_key_sha256", "ssh_public_key_sha256")
    value = _keys(value, names, "instance_facts")
    result = {
        "provider": _text(value["provider"], "instance_provider"),
        "instance_id": _text(value["instance_id"], "instance_id"),
        "gpu_model": _text(value["gpu_model"], "gpu_model"),
        "gpu_index": _integer(value["gpu_index"], "gpu_index"),
        "gpu_uuid": _text(value["gpu_uuid"], "gpu_uuid"),
        "ssh_host_key_sha256": _hex(value["ssh_host_key_sha256"], "ssh_host_key_sha256"),
        "ssh_public_key_sha256": _hex(value["ssh_public_key_sha256"], "ssh_public_key_sha256"),
    }
    operational = plan["operational"]
    if result["provider"] != operational["target_instance"]["provider"] or result["instance_id"] != operational["target_instance"]["instance_id"]:
        raise TrustedRemoteLiveAuthorityError("instance_plan_binding_invalid")
    if result["gpu_model"] != operational["gpu"]["model"] or result["gpu_index"] != operational["gpu"]["selected_index"]:
        raise TrustedRemoteLiveAuthorityError("gpu_plan_binding_invalid")
    if result["ssh_host_key_sha256"] != operational["ssh"]["host_key_sha256"] or result["ssh_public_key_sha256"] != operational["ssh"]["temporary_public_key_sha256"]:
        raise TrustedRemoteLiveAuthorityError("ssh_plan_binding_invalid")
    return result


def _candidate_rows(plan, candidate_records):
    if not isinstance(candidate_records, Mapping) or tuple(candidate_records) != REQUIRED_CASE_IDS:
        raise TrustedRemoteLiveAuthorityError("candidate_records_coverage_invalid")
    rows = []
    for case in plan["cases"]:
        case_id = case["case_id"]
        try:
            record = _slice3.validate_trusted_remote_production_run_record_binding(candidate_records[case_id], _records.TrustedRemoteActionTimePlan.from_dict(plan), case_id)
        except Exception as exc:
            raise TrustedRemoteLiveAuthorityError("candidate_record_binding_invalid") from exc
        data = record.to_dict()
        raw = data["raw_response"]
        rows.append({
            "case_id": case_id,
            "provider_input_sha256": case["provider_input"]["sha256"],
            "frozen_g0_sha256": case["frozen_g0"]["sha256"],
            "candidate_record_id": data["run_record_id"],
            "candidate_record_sha256": _sha(record.canonical_bytes()),
            "raw_response_sha256": raw["sha256"],
            "raw_response_byte_length": raw["byte_length"],
        })
    return rows


def _payload(plan, plan_raw, candidate_records, instance_facts, nonce, issued_at_utc, expires_at_utc):
    nonce = _hex(nonce, "nonce")
    issued, issued_text = _utc(issued_at_utc, "issued_at_utc")
    expires, expires_text = _utc(expires_at_utc, "expires_at_utc")
    if expires <= issued or expires - issued > timedelta(days=7):
        raise TrustedRemoteLiveAuthorityError("authority_window_invalid")
    caps = plan["operational"]["caps"]
    return {
        "schema_version": LIVE_AUTHORITY_SCHEMA,
        "namespace": LIVE_AUTHORITY_NAMESPACE,
        "action_time_plan": {"record_id": plan["record_id"], "sha256": _sha(plan_raw), "commit_sha": plan["policy"]["implementation_commit_sha"], "tree_sha": plan["policy"]["implementation_tree_sha"]},
        "archive": copy.deepcopy(plan["archive_binding"]),
        "model": {"model_id": plan["model_inventory"]["model_id"], "repository": plan["model_inventory"]["repository"], "exact_revision": plan["model_inventory"]["exact_revision"], "inventory_sha256": plan["model_inventory"]["inventory_sha256"], "tree_sha256": plan["model_inventory"]["tree_sha256"]},
        "runtime": {"runtime_id": plan["runtime_inventory"]["runtime_id"], "image_id": plan["runtime_inventory"]["image_id"], "inventory_sha256": plan["runtime_inventory"]["inventory_sha256"], "profile": plan["profile_selection"]["selected_profile"], "dtype": plan["precision"]["selected_dtype"], "quantization": plan["precision"]["selected_quantization"]},
        "instance": _instance(instance_facts, plan),
        "cases": _candidate_rows(plan, candidate_records),
        "nonce": nonce,
        "issued_at_utc": issued_text,
        "expires_at_utc": expires_text,
        "caps": copy.deepcopy(caps),
    }


@dataclass(frozen=True)
class ExpectedLiveAuthoritySigner:
    """Manager-supplied expected signer identity; never taken from a receipt."""

    principal: str
    public_key: str
    namespace: str = LIVE_AUTHORITY_NAMESPACE

    def __post_init__(self):
        if self.principal != LIVE_AUTHORITY_PRINCIPAL:
            raise TrustedRemoteLiveAuthorityError("signer_principal_invalid")
        if self.namespace != LIVE_AUTHORITY_NAMESPACE:
            raise TrustedRemoteLiveAuthorityError("signer_namespace_invalid")
        parts = self.public_key.strip().split()
        if len(parts) < 2 or not parts[0].startswith("ssh-"):
            raise TrustedRemoteLiveAuthorityError("signer_public_key_invalid")
        try:
            base64.b64decode(parts[1].encode("ascii"), validate=True)
        except Exception as exc:
            raise TrustedRemoteLiveAuthorityError("signer_public_key_invalid") from exc

    @property
    def key_sha256(self):
        return _sha((" ".join(self.public_key.strip().split()[:2])).encode("ascii"))

    def allowed_signers_text(self):
        return f"{self.principal} {' '.join(self.public_key.strip().split()[:2])}\n"


class TrustedRemoteLiveAuthorityReceipt:
    __slots__ = ("_data",)

    def __init__(self, data):
        self._data = data

    @classmethod
    def from_dict(cls, value):
        names = ("schema_version", "receipt_id", "signer_key_sha256", "signer_principal", "namespace", "signed_payload_base64", "signature_base64")
        value = _keys(value, names, "live_authority_receipt")
        if value["schema_version"] != LIVE_AUTHORITY_SCHEMA or value["namespace"] != LIVE_AUTHORITY_NAMESPACE:
            raise TrustedRemoteLiveAuthorityError("live_authority_receipt_schema_invalid")
        result = {
            "schema_version": value["schema_version"],
            "receipt_id": _text(value["receipt_id"], "receipt_id"),
            "signer_key_sha256": _hex(value["signer_key_sha256"], "signer_key_sha256"),
            "signer_principal": _text(value["signer_principal"], "signer_principal"),
            "namespace": value["namespace"],
            "signed_payload_base64": _b64(_unb64(value["signed_payload_base64"], "signed_payload_base64")),
            "signature_base64": _b64(_unb64(value["signature_base64"], "signature_base64")),
        }
        body = _identified(result)
        _loads(_unb64(body["signed_payload_base64"], "signed_payload_base64"))
        return cls(body)

    @classmethod
    def from_bytes(cls, raw):
        value = _loads(raw)
        instance = cls.from_dict(value)
        if instance.canonical_bytes() != raw:
            raise TrustedRemoteLiveAuthorityError("live_authority_receipt_not_canonical")
        return instance

    def to_dict(self):
        return copy.deepcopy(self._data)

    def canonical_bytes(self):
        return _dumps(self._data)

    def sha256(self):
        return _sha(self.canonical_bytes())


def _validate_payload_shape(value):
    names = ("schema_version", "namespace", "action_time_plan", "archive", "model", "runtime", "instance", "cases", "nonce", "issued_at_utc", "expires_at_utc", "caps")
    value = _keys(value, names, "signed_payload")
    if value["schema_version"] != LIVE_AUTHORITY_SCHEMA or value["namespace"] != LIVE_AUTHORITY_NAMESPACE:
        raise TrustedRemoteLiveAuthorityError("signed_payload_schema_invalid")
    _keys(value["action_time_plan"], ("record_id", "sha256", "commit_sha", "tree_sha"), "signed_payload_plan")
    _hex(value["action_time_plan"]["sha256"], "signed_payload_plan_sha256")
    _hex(value["action_time_plan"]["commit_sha"], "signed_payload_commit_sha", length=40)
    _hex(value["action_time_plan"]["tree_sha"], "signed_payload_tree_sha", length=40)
    _hex(value["nonce"], "nonce")
    issued, _ = _utc(value["issued_at_utc"], "issued_at_utc")
    expires, _ = _utc(value["expires_at_utc"], "expires_at_utc")
    if expires <= issued or not isinstance(value["cases"], list) or [row.get("case_id") for row in value["cases"] if isinstance(row, Mapping)] != list(REQUIRED_CASE_IDS):
        raise TrustedRemoteLiveAuthorityError("signed_payload_binding_shape_invalid")
    return value


def build_trusted_remote_live_authority_payload(action_time_plan, candidate_records, instance_facts, nonce, issued_at_utc, expires_at_utc):
    plan, raw = _plan(action_time_plan)
    return _payload(plan, raw, candidate_records, instance_facts, nonce, issued_at_utc, expires_at_utc)


def create_trusted_remote_live_authority_receipt(payload, signature_bytes, expected_signer):
    if type(expected_signer) is not ExpectedLiveAuthoritySigner:
        raise TrustedRemoteLiveAuthorityError("expected_signer_exact_type_required")
    if not isinstance(signature_bytes, bytes) or not signature_bytes:
        raise TrustedRemoteLiveAuthorityError("signature_bytes_required")
    if not isinstance(payload, Mapping):
        raise TrustedRemoteLiveAuthorityError("signed_payload_mapping_required")
    _validate_payload_shape(payload)
    raw = _dumps(payload)
    if _loads(raw) != dict(payload):
        raise TrustedRemoteLiveAuthorityError("signed_payload_not_canonical")
    return TrustedRemoteLiveAuthorityReceipt.from_dict({
        "schema_version": LIVE_AUTHORITY_SCHEMA,
        "receipt_id": LIVE_AUTHORITY_PREFIX + "0" * 64,
        "signer_key_sha256": expected_signer.key_sha256,
        "signer_principal": expected_signer.principal,
        "namespace": expected_signer.namespace,
        "signed_payload_base64": _b64(raw),
        "signature_base64": _b64(signature_bytes),
    })


def _ssh_verify(payload, signature, signer):
    # Use a private short-lived scratch directory without tempfile's Windows
    # 0700 creation mode: some managed Windows hosts deny child creation there.
    root = Path(tempfile.gettempdir()) / ("req2web-live-authority-" + uuid.uuid4().hex)
    try:
        os.mkdir(root, 0o777)
        payload_path = root / "payload.json"
        signature_path = root / "payload.json.sig"
        allowed_path = root / "allowed_signers"
        payload_path.write_bytes(payload)
        signature_path.write_bytes(signature)
        allowed_path.write_text(signer.allowed_signers_text(), encoding="utf-8", newline="\n")
        result = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed_path), "-I", signer.principal, "-n", signer.namespace, "-s", str(signature_path)], input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=20)
    except FileNotFoundError as exc:
        raise TrustedRemoteLiveAuthorityError("openssh_sshsig_unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise TrustedRemoteLiveAuthorityError("openssh_sshsig_timeout") from exc
    except OSError as exc:
        raise TrustedRemoteLiveAuthorityError("openssh_sshsig_scratch_unavailable") from exc
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if result.returncode != 0:
        raise TrustedRemoteLiveAuthorityError("live_authority_signature_invalid")


def verify_trusted_remote_live_authority_receipt(receipt, action_time_plan, candidate_records, expected_instance_facts, expected_signer, now_utc):
    if type(expected_signer) is not ExpectedLiveAuthoritySigner:
        raise TrustedRemoteLiveAuthorityError("expected_signer_exact_type_required")
    if type(receipt) is TrustedRemoteLiveAuthorityReceipt:
        raw = receipt.canonical_bytes()
    elif type(receipt) is bytes:
        raw = receipt
    else:
        raise TrustedRemoteLiveAuthorityError("live_authority_receipt_exact_type_or_bytes_required")
    replay = TrustedRemoteLiveAuthorityReceipt.from_bytes(raw)
    data = replay.to_dict()
    if data["signer_key_sha256"] != expected_signer.key_sha256 or data["signer_principal"] != expected_signer.principal or data["namespace"] != expected_signer.namespace:
        raise TrustedRemoteLiveAuthorityError("expected_signer_identity_mismatch")
    payload_raw = _unb64(data["signed_payload_base64"], "signed_payload_base64")
    signature = _unb64(data["signature_base64"], "signature_base64")
    _ssh_verify(payload_raw, signature, expected_signer)
    plan, plan_raw = _plan(action_time_plan)
    if expected_signer.key_sha256 != plan["operational"]["ssh"]["temporary_public_key_sha256"]:
        raise TrustedRemoteLiveAuthorityError("expected_signer_plan_binding_invalid")
    expected = _payload(plan, plan_raw, candidate_records, expected_instance_facts, _loads(payload_raw)["nonce"], _loads(payload_raw)["issued_at_utc"], _loads(payload_raw)["expires_at_utc"])
    if _dumps(expected) != payload_raw:
        raise TrustedRemoteLiveAuthorityError("live_authority_binding_mismatch")
    now, _ = _utc(now_utc, "now_utc")
    issued, _ = _utc(expected["issued_at_utc"], "issued_at_utc")
    expires, _ = _utc(expected["expires_at_utc"], "expires_at_utc")
    if now < issued or now >= expires:
        raise TrustedRemoteLiveAuthorityError("live_authority_window_invalid")
    return replay


def consume_trusted_remote_live_authority_receipt(receipt, action_time_plan, candidate_records, expected_instance_facts, expected_signer, now_utc):
    verified = verify_trusted_remote_live_authority_receipt(
        receipt, action_time_plan, candidate_records, expected_instance_facts, expected_signer, now_utc
    )
    data = verified.to_dict()
    payload = _loads(_unb64(data["signed_payload_base64"], "signed_payload_base64"))
    consumption_key = (data["signer_key_sha256"], payload["action_time_plan"]["record_id"], _hex(payload.get("nonce"), "nonce"))
    with _PROCESS_NONCE_LOCK:
        if consumption_key in _PROCESS_CONSUMED_NONCES:
            raise TrustedRemoteLiveAuthorityError("live_authority_nonce_replay")
        _PROCESS_CONSUMED_NONCES.add(consumption_key)
    return verified


__all__ = [
    "ExpectedLiveAuthoritySigner", "LIVE_AUTHORITY_NAMESPACE", "LIVE_AUTHORITY_PRINCIPAL", "LIVE_AUTHORITY_PREFIX", "LIVE_AUTHORITY_SCHEMA", "TrustedRemoteLiveAuthorityError", "TrustedRemoteLiveAuthorityReceipt", "build_trusted_remote_live_authority_payload", "consume_trusted_remote_live_authority_receipt", "create_trusted_remote_live_authority_receipt", "verify_trusted_remote_live_authority_receipt",
]
