"""P4-03 local Qwen foundation with a fail-closed pre-call contract.

The module deliberately separates three concerns:

* versioned, exact-key records for the pilot, D17 visibility, runtime profile,
  attempts, checkpoints, and outcomes;
* a result-root writer that commits the pre-call record before a backend can
  start generation and commits raw bytes before parsing;
* a lazy backend/orchestrator seam.  Importing this module never imports
  Transformers, torch, bitsandbytes, or a model.

P4-03 remains a Phase 4 local, non-H1 pilot.  The deterministic authorities
used for F1-F4 validation, stable IDs, mapping, composition, and assembly are
facaded by ``req2web_orchestration.phase4_graph``; this module does not copy
those rules.
"""

from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import queue
import re
import subprocess
import sys
import sysconfig
import tempfile
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable, ClassVar, Iterable, Mapping, NamedTuple, Protocol, Sequence, TypedDict


P4_03_SCHEMA_PREFIX = "req2web.phase4.p4_03"
PILOT_BINDING_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.pilot_binding.v1"
PROJECTION_POLICY_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.node_projection_policy.v1"
NODE_D17_ACTION_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.node_d17_action.v1"
LOCAL_QWEN_PROFILE_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.local_qwen_profile.v1"
LOAD_RECEIPT_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.local_qwen_load_receipt.v1"
PRE_CALL_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.attempt_pre_call.v1"
ATTEMPT_RESULT_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.attempt_result.v1"
LEDGER_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.attempt_ledger.v1"
CHECKPOINT_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.checkpoint_binding.v1"
OUTCOME_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.pilot_outcome.v1"
MANIFEST_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.pre_call_manifest.v1"
EXECUTION_LEASE_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.execution_lease.v1"
RUNTIME_START_CLAIM_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.runtime_start_claim.v1"
SUPERVISOR_RECEIPT_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.supervisor_receipt.v1"
WORKER_STDERR_SCHEMA_VERSION = f"{P4_03_SCHEMA_PREFIX}.worker_stderr.v1"

QWEN_MODEL_ID = "Qwen/Qwen3.5-9B"
QWEN_MODEL_REVISION = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
LOCAL_PROFILE_NAME = "local_smoke"
TRANSFORMERS_VERSION = "5.14.1"
TORCH_VERSION = "2.7.1+cu128"
BITSANDBYTES_VERSION = "0.50.0"
ACCELERATE_VERSION = "1.14.0"
NODE_ORDER = ("F1", "F2", "F3", "F4")
CALL_KINDS = ("node_local", "integrated")
SOURCE_KINDS = ("scripted_test_fixture", "real_local_qwen")
PROMPT_REVISIONS = ("p4-03-prompt-v1", "p4-03-prompt-v2")
CHANGE_REASONS = (
    "projection_cap_tightening",
    "prompt_contract_clarification",
    "output_schema_clarification",
)
RESULT_ROOT_MARKER_NAME = ".req2web-phase4-p4-03-result-root"
RAW_RESPONSE_NAME = "raw_response.bin"

_REF_CONTRACT = {
    "exact_keys": ["ref_type", "ref_id", "ref_revision"],
    "value_rule": "all values are non-empty strings; preserve supplied refs exactly",
}
_NODE_OUTPUT_CONTRACTS: dict[str, dict[str, object]] = {
    "F1": {
        "exact_top_level_keys": ["page_title", "layout_pattern", "sections", "components"],
        "section_exact_keys": ["local_id", "entity_type", "title", "purpose", "component_local_ids", "refs"],
        "component_exact_keys": ["local_id", "entity_type", "component_type", "section_local_id", "label", "purpose", "refs"],
        "entity_type_values": {"section": "section", "component": "component"},
        "order_rule": "sections define layout order; components must equal section component_local_ids concatenated in section order",
        "ref_contract": _REF_CONTRACT,
    },
    "F2": {
        "exact_top_level_keys": ["states"],
        "state_exact_keys": ["local_id", "entity_type", "name", "description", "visible_component_local_ids", "refs"],
        "entity_type_value": "state",
        "order_rule": "visible_component_local_ids must be a subsequence of F1 component order",
        "ref_contract": _REF_CONTRACT,
    },
    "F3": {
        "exact_top_level_keys": ["interactions"],
        "interaction_exact_keys": ["local_id", "entity_type", "trigger_component_local_id", "source_state_local_id", "action", "target_state_local_id", "user_feedback", "refs"],
        "entity_type_value": "interaction",
        "reference_rule": "component and state local IDs must come from same-run validated F1/F2 outputs",
        "ref_contract": _REF_CONTRACT,
    },
    "F4": {
        "exact_top_level_keys": ["acceptance_checks"],
        "acceptance_check_exact_keys": ["local_id", "entity_type", "description", "use_case_refs", "state_ref", "refs"],
        "entity_type_value": "candidate_acceptance_check",
        "use_case_ref_rule": "copy exact refs from authority_bindings.canonical_b_use_case_refs in canonical order and collectively cover every use case",
        "state_ref_rule": "copy one exact registry_stable ref from authority_bindings.f2_state_refs",
        "ref_contract": _REF_CONTRACT,
    },
}

_NODE_PROMPT_GUIDANCE: dict[str, tuple[str, ...]] = {
    "F1": (
        "Generate only sections and components: each section entity_type must be the literal \"section\", and each component entity_type must be the literal \"component\".",
        "Create unique F1-owned local_id values; component_local_ids and section_local_id must reference only local IDs created in this F1 output.",
        "Avoid redundant sections or components; include only the entities needed to satisfy the current input and contract without fixing an exact entity count.",
    ),
    "F2": (
        "Generate only the top-level states array; do not copy, rename, or transform upstream F1 sections or components into state objects.",
        "Every F2 object must use the literal entity_type value \"state\" and a new F2-owned local_id that does not reuse any F1 section or component local_id.",
        "Every visible_component_local_ids value must be an exact local_id from upstream F1 components only, never a section or state ID, and the list must preserve F1 component order as a subsequence.",
    ),
    "F3": (
        "Generate only interactions; every object must use the literal entity_type value \"interaction\" and a new F3-owned local_id that does not reuse F1 or F2 local IDs.",
        "trigger_component_local_id must copy an exact upstream F1 component local_id; source_state_local_id and target_state_local_id must copy exact upstream F2 state local IDs.",
    ),
    "F4": (
        "Generate only acceptance_checks; every object must use the literal entity_type value \"candidate_acceptance_check\" and a new F4-owned local_id.",
        "Copy use_case_refs only from authority_bindings.canonical_b_use_case_refs in canonical order, and copy state_ref as one exact registry_stable ref from authority_bindings.f2_state_refs.",
    ),
}

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_RELATIVE_RE = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9_./-]{0,254}$")
_HF_LOCAL_CACHE_PREFIX = ".cache/huggingface/"
_REAL_RUNTIME_CAPABILITY = object()
_FIXTURE_BACKEND_CAPABILITY = object()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class Phase4LocalQwenContractError(ValueError):
    """Raised when a P4-03 public boundary fails closed."""


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Phase4LocalQwenContractError("value is not canonical JSON") from exc


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _hex_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _reject_constant(value: str) -> None:
    raise Phase4LocalQwenContractError(f"JSON constant {value} is forbidden")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Phase4LocalQwenContractError("duplicate JSON key")
        result[key] = value
    return result


def _strict_json(raw: bytes, *, require_canonical: bool = True) -> dict[str, object]:
    if type(raw) is not bytes or not raw:
        raise Phase4LocalQwenContractError("JSON bytes must be non-empty bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Phase4LocalQwenContractError("JSON bytes are not UTF-8") from exc
    if text.startswith("\ufeff"):
        raise Phase4LocalQwenContractError("UTF-8 BOM is forbidden")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, Phase4LocalQwenContractError):
            raise
        raise Phase4LocalQwenContractError("invalid JSON object") from exc
    if type(value) is not dict:
        raise Phase4LocalQwenContractError("JSON value must be an object")
    if require_canonical and _canonical_bytes(value) != raw:
        raise Phase4LocalQwenContractError("JSON bytes are not canonical")
    return value


def _exact(value: object, keys: Sequence[str], name: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise Phase4LocalQwenContractError(f"{name} has non-exact keys")
    return dict(value)


def _text(value: object, name: str, *, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise Phase4LocalQwenContractError(f"{name} must be a non-empty trimmed string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise Phase4LocalQwenContractError(f"{name} has an invalid format")
    return value


def _bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise Phase4LocalQwenContractError(f"{name} must be a boolean")
    return value


def _integer(value: object, name: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise Phase4LocalQwenContractError(f"{name} must be an integer, not bool or float")
    if value < minimum or (maximum is not None and value > maximum):
        raise Phase4LocalQwenContractError(f"{name} is outside its allowed range")
    return value


def _string_list(value: object, name: str, *, allow_empty: bool = False) -> list[str]:
    if type(value) is not list:
        raise Phase4LocalQwenContractError(f"{name} must be a list")
    if not allow_empty and not value:
        raise Phase4LocalQwenContractError(f"{name} must not be empty")
    result = [_text(item, f"{name}[]") for item in value]
    if len(result) != len(set(result)):
        raise Phase4LocalQwenContractError(f"{name} contains duplicates")
    return result


def _sha(value: object, name: str) -> str:
    return _text(value, name, pattern=_SHA_RE)


def _identity(value: object, *, revision: str, identity_kind: str = "canonical_json") -> dict[str, object]:
    raw = _canonical_bytes(value)
    return {
        "identity_kind": identity_kind,
        "sha256": _sha256(raw),
        "byte_length": len(raw),
        "revision": _text(revision, "identity.revision"),
    }


def make_canonical_identity(
    value: object, *, revision: str
) -> dict[str, object]:
    """Return the public canonical identity shape used by P4-03 receipts."""

    return _identity(value, revision=revision)


def _validate_identity(value: object, name: str) -> dict[str, object]:
    data = _exact(value, ("identity_kind", "sha256", "byte_length", "revision"), name)
    _text(data["identity_kind"], f"{name}.identity_kind")
    _sha(data["sha256"], f"{name}.sha256")
    _integer(data["byte_length"], f"{name}.byte_length", minimum=0)
    _text(data["revision"], f"{name}.revision")
    return data


def _ref(value: object, name: str, ref_type: str | None = None) -> dict[str, object]:
    data = _exact(value, ("ref_type", "ref_id", "ref_sha256", "ref_revision"), name)
    actual_type = _text(data["ref_type"], f"{name}.ref_type")
    if ref_type is not None and actual_type != ref_type:
        raise Phase4LocalQwenContractError(f"{name}.ref_type is not {ref_type}")
    _text(data["ref_id"], f"{name}.ref_id", pattern=_ID_RE)
    _sha(data["ref_sha256"], f"{name}.ref_sha256")
    _text(data["ref_revision"], f"{name}.ref_revision")
    return data


def _ref_from_identity(ref_type: str, ref_id: str, identity: Mapping[str, object]) -> dict[str, object]:
    checked = _validate_identity(identity, "ref.identity")
    return {
        "ref_type": ref_type,
        "ref_id": _text(ref_id, "ref.ref_id", pattern=_ID_RE),
        "ref_sha256": checked["sha256"],
        "ref_revision": checked["revision"],
    }


def _b64(raw: bytes, name: str) -> str:
    if type(raw) is not bytes:
        raise Phase4LocalQwenContractError(f"{name} must be bytes")
    encoded = base64.b64encode(raw).decode("ascii")
    return encoded


def _decode_b64(value: object, name: str, *, allow_empty: bool = False) -> bytes:
    if allow_empty:
        if type(value) is not str or value != value.strip():
            raise Phase4LocalQwenContractError(f"{name} must be a trimmed string")
        text = value
    else:
        text = _text(value, name)
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise Phase4LocalQwenContractError(f"{name} is not canonical base64") from exc
    if base64.b64encode(raw).decode("ascii") != text:
        raise Phase4LocalQwenContractError(f"{name} is not canonical base64")
    return raw


def _bytes_fields(payload: Mapping[str, object], prefix: str, b64_key: str, sha_key: str, length_key: str) -> bytes:
    raw = _decode_b64(payload[b64_key], f"{prefix}.{b64_key}")
    if payload[sha_key] != _sha256(raw):
        raise Phase4LocalQwenContractError(f"{prefix}.{sha_key} does not match bytes")
    if payload[length_key] != len(raw):
        raise Phase4LocalQwenContractError(f"{prefix}.{length_key} does not match bytes")
    _integer(payload[length_key], f"{prefix}.{length_key}", minimum=0)
    return raw


def _relative_path(value: object, name: str) -> str:
    path = _text(value, name)
    if path.startswith(("/", "\\")) or ":" in path.split("/", 1)[0]:
        raise Phase4LocalQwenContractError(f"{name} must not be absolute")
    normalized = path.replace("\\", "/")
    if normalized != path or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise Phase4LocalQwenContractError(f"{name} is not a safe relative path")
    if _RELATIVE_RE.fullmatch(normalized) is None:
        raise Phase4LocalQwenContractError(f"{name} has an invalid path")
    return normalized


def _authorization_flags(value: object, name: str) -> dict[str, object]:
    expected = {
        "authorization_state_version": f"{P4_03_SCHEMA_PREFIX}.authorization_state.v1",
        "runtime_kind": "local_qwen_langgraph",
        "model_action_authorized": True,
        "graph_runtime_authorized": True,
        "dependency_installation_authorized": False,
        "training_authorized": False,
        "remote_action_authorized": False,
        "network_authorized": False,
        "telemetry_authorized": False,
        "tracing_authorized": False,
        "local_files_only_required": True,
    }
    data = _exact(value, tuple(expected), name)
    for key, expected_value in expected.items():
        if type(data[key]) is not type(expected_value) or data[key] != expected_value:
            raise Phase4LocalQwenContractError(f"{name}.{key} drifted")
    return data


def _action_flags(
    value: object,
    name: str,
    *,
    model_action: bool,
    graph_runtime_execution: bool,
) -> dict[str, object]:
    expected = {
        "action_state_version": f"{P4_03_SCHEMA_PREFIX}.action_state.v1",
        "runtime_kind": "local_qwen_langgraph",
        "model_action": model_action,
        "graph_runtime_execution": graph_runtime_execution,
        "dependency_installation": False,
        "training": False,
        "remote_action": False,
        "network": False,
        "telemetry": False,
        "tracing": False,
        "local_files_only": True,
    }
    data = _exact(value, tuple(expected), name)
    for key, expected_value in expected.items():
        if type(data[key]) is not type(expected_value) or data[key] != expected_value:
            raise Phase4LocalQwenContractError(f"{name}.{key} drifted")
    return data


def _make_action_state(
    *, model_action: bool, graph_runtime_execution: bool
) -> dict[str, object]:
    value = {
        "action_state_version": f"{P4_03_SCHEMA_PREFIX}.action_state.v1",
        "runtime_kind": "local_qwen_langgraph",
        "model_action": model_action,
        "graph_runtime_execution": graph_runtime_execution,
        "dependency_installation": False,
        "training": False,
        "remote_action": False,
        "network": False,
        "telemetry": False,
        "tracing": False,
        "local_files_only": True,
    }
    return _action_flags(
        value,
        "action_state",
        model_action=model_action,
        graph_runtime_execution=graph_runtime_execution,
    )


class _CanonicalRecord:
    """Immutable canonical record base with a disabled public constructor."""

    KEYS: ClassVar[tuple[str, ...]] = ()
    SCHEMA_VERSION: ClassVar[str] = ""
    _canonical: bytes

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("use create() or from_dict(); direct construction is disabled")

    @classmethod
    def _from_payload(cls, payload: object) -> "_CanonicalRecord":
        data = _exact(payload, cls.KEYS, cls.__name__)
        cls._validate_payload(data)
        raw = _canonical_bytes(data)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_canonical", raw)
        return instance

    @classmethod
    def from_dict(cls, payload: object) -> "_CanonicalRecord":
        return cls._from_payload(payload)

    @classmethod
    def from_bytes(cls, raw: bytes) -> "_CanonicalRecord":
        return cls._from_payload(_strict_json(raw))

    def _payload(self) -> dict[str, object]:
        raw = object.__getattribute__(self, "_canonical")
        return _strict_json(raw)

    def __getattr__(self, name: str) -> object:
        if name in self.KEYS:
            return copy.deepcopy(self._payload()[name])
        raise AttributeError(name)

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return NotImplemented
        return self.canonical_bytes() == other.canonical_bytes()  # type: ignore[attr-defined]

    def __hash__(self) -> int:
        return hash(self.canonical_bytes())

    def validate(self) -> None:
        raw = object.__getattribute__(self, "_canonical")
        data = _strict_json(raw)
        self._validate_payload(data)
        if _canonical_bytes(data) != raw:
            raise Phase4LocalQwenContractError(f"{type(self).__name__} canonical bytes drifted")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return self._payload()

    def canonical_bytes(self) -> bytes:
        self.validate()
        return bytes(object.__getattribute__(self, "_canonical"))

    def sha256(self) -> str:
        return _sha256(self.canonical_bytes())

    def hash(self) -> str:
        return self.sha256()

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        raise NotImplementedError


def _common_record(data: Mapping[str, object], *, schema: str, name: str) -> None:
    if data["schema_version"] != schema:
        raise Phase4LocalQwenContractError(f"{name}.schema_version drifted")


def _validate_id_scope(data: Mapping[str, object], name: str, *fields: str) -> None:
    for field in fields:
        _text(data[field], f"{name}.{field}", pattern=_ID_RE)


def _node_policy_caps(value: object, name: str) -> dict[str, int]:
    keys = ("input_bytes", "output_bytes", "prompt_bytes", "config_bytes", "request_bytes", "ref_count")
    data = _exact(value, keys, name)
    return {key: _integer(data[key], f"{name}.{key}", minimum=1) for key in keys}


def _count_reference_objects(value: object) -> int:
    if isinstance(value, Mapping):
        count = 1 if {"ref_type", "ref_id", "ref_revision"}.issubset(value) else 0
        return count + sum(_count_reference_objects(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_reference_objects(item) for item in value)
    return 0


class PilotExecutionLease(_CanonicalRecord):
    """Exclusive, one-shot authorization to start one local pilot process."""

    KEYS = (
        "schema_version", "lease_id", "pilot_id", "case_id", "request_id",
        "manifest_identity", "pilot_identity", "parent_pid", "acquired_at_utc",
        "state", "action_state",
    )
    SCHEMA_VERSION = EXECUTION_LEASE_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        pilot: "PilotBinding",
        manifest: "PreCallManifest",
        parent_pid: int,
    ) -> "PilotExecutionLease":
        pilot.validate()
        manifest.validate()
        case = pilot.case_binding
        root: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "lease_id": "pending",
            "pilot_id": pilot.pilot_id,
            "case_id": case["case_id"],
            "request_id": case["request_id"],
            "manifest_identity": _identity(
                manifest.to_dict(), revision=MANIFEST_SCHEMA_VERSION
            ),
            "pilot_identity": _identity(
                pilot.to_dict(), revision=PILOT_BINDING_SCHEMA_VERSION
            ),
            "parent_pid": parent_pid,
            "acquired_at_utc": _utc_now(),
            "state": "acquired_once",
            "action_state": _make_action_state(
                model_action=False, graph_runtime_execution=False
            ),
        }
        root["lease_id"] = _identity(
            {key: value for key, value in root.items() if key != "lease_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="PilotExecutionLease")
        _sha(data["lease_id"], "PilotExecutionLease.lease_id")
        _validate_id_scope(
            data, "PilotExecutionLease", "pilot_id", "case_id", "request_id"
        )
        _validate_identity(data["manifest_identity"], "PilotExecutionLease.manifest_identity")
        _validate_identity(data["pilot_identity"], "PilotExecutionLease.pilot_identity")
        _integer(data["parent_pid"], "PilotExecutionLease.parent_pid", minimum=1)
        _text(data["acquired_at_utc"], "PilotExecutionLease.acquired_at_utc")
        if data["state"] != "acquired_once":
            raise Phase4LocalQwenContractError("execution lease state drifted")
        _action_flags(
            data["action_state"],
            "PilotExecutionLease.action_state",
            model_action=False,
            graph_runtime_execution=False,
        )
        expected = _identity(
            {key: data[key] for key in data if key != "lease_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        if data["lease_id"] != expected:
            raise Phase4LocalQwenContractError("execution lease identity drifted")


class PilotRuntimeStartClaim(_CanonicalRecord):
    """One-shot persisted claim consumed before any worker process starts."""

    KEYS = (
        "schema_version", "claim_id", "lease_identity", "pilot_id", "case_id",
        "request_id", "parent_pid", "claimed_at_utc", "state", "action_state",
    )
    SCHEMA_VERSION = RUNTIME_START_CLAIM_SCHEMA_VERSION

    @classmethod
    def create(
        cls, *, lease: PilotExecutionLease, parent_pid: int
    ) -> "PilotRuntimeStartClaim":
        lease.validate()
        root: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "claim_id": "pending",
            "lease_identity": _identity(
                lease.to_dict(), revision=EXECUTION_LEASE_SCHEMA_VERSION
            ),
            "pilot_id": lease.pilot_id,
            "case_id": lease.case_id,
            "request_id": lease.request_id,
            "parent_pid": parent_pid,
            "claimed_at_utc": _utc_now(),
            "state": "runtime_start_claimed_once",
            "action_state": _make_action_state(
                model_action=False, graph_runtime_execution=False
            ),
        }
        root["claim_id"] = _identity(
            {key: value for key, value in root.items() if key != "claim_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="PilotRuntimeStartClaim")
        _sha(data["claim_id"], "PilotRuntimeStartClaim.claim_id")
        _validate_identity(data["lease_identity"], "PilotRuntimeStartClaim.lease_identity")
        _validate_id_scope(
            data, "PilotRuntimeStartClaim", "pilot_id", "case_id", "request_id"
        )
        _integer(data["parent_pid"], "PilotRuntimeStartClaim.parent_pid", minimum=1)
        _text(data["claimed_at_utc"], "PilotRuntimeStartClaim.claimed_at_utc")
        if data["state"] != "runtime_start_claimed_once":
            raise Phase4LocalQwenContractError("runtime start claim state drifted")
        _action_flags(
            data["action_state"],
            "PilotRuntimeStartClaim.action_state",
            model_action=False,
            graph_runtime_execution=False,
        )
        expected = _identity(
            {key: data[key] for key in data if key != "claim_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        if data["claim_id"] != expected:
            raise Phase4LocalQwenContractError("runtime start claim identity drifted")


class PilotSupervisorReceipt(_CanonicalRecord):
    """Terminal parent-process evidence for worker exit and pilot closure."""

    KEYS = (
        "schema_version", "receipt_id", "lease_identity", "pilot_id", "case_id",
        "request_id", "terminal_status", "worker_id", "worker_pid",
        "worker_exit_code", "worker_exit_verified", "graceful_shutdown_requested",
        "terminate_sent", "kill_sent", "generation_started", "raw_status",
        "latest_attempt_result_identity", "pilot_outcome_identity",
        "stderr_identity", "retry_performed", "completed_at_utc", "action_state",
    )
    SCHEMA_VERSION = SUPERVISOR_RECEIPT_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        lease: PilotExecutionLease,
        terminal_status: str,
        worker_id: str | None,
        worker_pid: int | None,
        worker_exit_code: int | None,
        worker_exit_verified: bool,
        graceful_shutdown_requested: bool,
        terminate_sent: bool,
        kill_sent: bool,
        generation_started: bool,
        raw_status: str,
        latest_attempt_result_identity: Mapping[str, object] | None,
        pilot_outcome_identity: Mapping[str, object] | None,
        stderr_identity: Mapping[str, object] | None,
        model_action: bool,
    ) -> "PilotSupervisorReceipt":
        lease.validate()
        root: dict[str, object] = {
            "schema_version": cls.SCHEMA_VERSION,
            "receipt_id": "pending",
            "lease_identity": _identity(
                lease.to_dict(), revision=EXECUTION_LEASE_SCHEMA_VERSION
            ),
            "pilot_id": lease.pilot_id,
            "case_id": lease.case_id,
            "request_id": lease.request_id,
            "terminal_status": terminal_status,
            "worker_id": worker_id,
            "worker_pid": worker_pid,
            "worker_exit_code": worker_exit_code,
            "worker_exit_verified": worker_exit_verified,
            "graceful_shutdown_requested": graceful_shutdown_requested,
            "terminate_sent": terminate_sent,
            "kill_sent": kill_sent,
            "generation_started": generation_started,
            "raw_status": raw_status,
            "latest_attempt_result_identity": (
                None
                if latest_attempt_result_identity is None
                else dict(latest_attempt_result_identity)
            ),
            "pilot_outcome_identity": (
                None if pilot_outcome_identity is None else dict(pilot_outcome_identity)
            ),
            "stderr_identity": None if stderr_identity is None else dict(stderr_identity),
            "retry_performed": False,
            "completed_at_utc": _utc_now(),
            "action_state": _make_action_state(
                model_action=model_action,
                graph_runtime_execution=generation_started,
            ),
        }
        root["receipt_id"] = _identity(
            {key: value for key, value in root.items() if key != "receipt_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="PilotSupervisorReceipt")
        _sha(data["receipt_id"], "PilotSupervisorReceipt.receipt_id")
        _validate_identity(data["lease_identity"], "PilotSupervisorReceipt.lease_identity")
        _validate_id_scope(
            data, "PilotSupervisorReceipt", "pilot_id", "case_id", "request_id"
        )
        if data["terminal_status"] not in {
            "normal_completed", "load_failed", "generation_timeout",
            "generation_cancelled", "worker_failed", "pilot_stopped",
            "parent_execution_failed", "evidence_persistence_failed",
            "worker_teardown_unverified",
        }:
            raise Phase4LocalQwenContractError("supervisor terminal status is invalid")
        if data["worker_id"] is not None:
            _text(data["worker_id"], "PilotSupervisorReceipt.worker_id", pattern=_ID_RE)
        if data["worker_pid"] is not None:
            _integer(data["worker_pid"], "PilotSupervisorReceipt.worker_pid", minimum=1)
        if data["worker_exit_code"] is not None:
            _integer(
                data["worker_exit_code"],
                "PilotSupervisorReceipt.worker_exit_code",
                minimum=-2147483648,
                maximum=2147483647,
            )
        for key in (
            "worker_exit_verified", "graceful_shutdown_requested", "terminate_sent",
            "kill_sent", "generation_started", "retry_performed",
        ):
            _bool(data[key], f"PilotSupervisorReceipt.{key}")
        if data["retry_performed"] is not False:
            raise Phase4LocalQwenContractError("supervisor retry is prohibited")
        if (
            data["worker_exit_verified"] is False
            and data["terminal_status"] != "worker_teardown_unverified"
        ) or (
            data["worker_exit_verified"] is True
            and data["terminal_status"] == "worker_teardown_unverified"
        ):
            raise Phase4LocalQwenContractError(
                "supervisor worker-exit verification status drifted"
            )
        if data["raw_status"] not in {"captured", "not_captured", "no_generate"}:
            raise Phase4LocalQwenContractError("supervisor raw status is invalid")
        for key in (
            "latest_attempt_result_identity", "pilot_outcome_identity", "stderr_identity"
        ):
            if data[key] is not None:
                _validate_identity(data[key], f"PilotSupervisorReceipt.{key}")
        _text(data["completed_at_utc"], "PilotSupervisorReceipt.completed_at_utc")
        action = _exact(
            data["action_state"],
            (
                "action_state_version", "runtime_kind", "model_action",
                "graph_runtime_execution", "dependency_installation", "training",
                "remote_action", "network", "telemetry", "tracing", "local_files_only",
            ),
            "PilotSupervisorReceipt.action_state",
        )
        model_action = _bool(action["model_action"], "PilotSupervisorReceipt.action_state.model_action")
        graph_runtime = _bool(
            action["graph_runtime_execution"],
            "PilotSupervisorReceipt.action_state.graph_runtime_execution",
        )
        _action_flags(
            action,
            "PilotSupervisorReceipt.action_state",
            model_action=model_action,
            graph_runtime_execution=graph_runtime,
        )
        if graph_runtime is not data["generation_started"]:
            raise Phase4LocalQwenContractError("supervisor generation action state drifted")
        expected = _identity(
            {key: data[key] for key in data if key != "receipt_id"},
            revision=cls.SCHEMA_VERSION,
        )["sha256"]
        if data["receipt_id"] != expected:
            raise Phase4LocalQwenContractError("supervisor receipt identity drifted")


class PilotBinding(_CanonicalRecord):
    """Manager-owned frozen pilot/case/budget/claim contract."""

    KEYS = (
        "schema_version", "pilot_id", "policy_id", "case_binding", "node_order",
        "node_policy_identities", "b_aux_disposition", "node_total_call_cap",
        "node_local_call_cap", "integrated_run_cap", "retry_count_cap",
        "model_id", "model_revision", "profile_id", "decode_config",
        "retention_policy", "stop_policy", "result_root_marker",
        "authorization_state", "action_state",
        "training", "remote", "h1_or_gold", "formal_quality", "data_authoring",
    )
    SCHEMA_VERSION = PILOT_BINDING_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        pilot_id: str,
        policy_id: str,
        case_binding: Mapping[str, object],
        node_policy_identities: Mapping[str, object],
        profile_id: str,
        result_root_marker: str,
        decode_config: Mapping[str, object] | None = None,
        retention_policy: str = "dedicated_result_root_only_no_cleanup_interface",
        stop_policy: str = "first_terminal_or_node_budget_exhaustion",
    ) -> "PilotBinding":
        payload = {
            "schema_version": cls.SCHEMA_VERSION,
            "pilot_id": pilot_id,
            "policy_id": policy_id,
            "case_binding": dict(case_binding),
            "node_order": list(NODE_ORDER),
            "node_policy_identities": dict(node_policy_identities),
            "b_aux_disposition": "absent/not_requested",
            "node_total_call_cap": 3,
            "node_local_call_cap": 2,
            "integrated_run_cap": 1,
            "retry_count_cap": 0,
            "model_id": QWEN_MODEL_ID,
            "model_revision": QWEN_MODEL_REVISION,
            "profile_id": profile_id,
            "decode_config": dict(decode_config or {
                "do_sample": False,
                "temperature": 0.0,
                "top_p": 1.0,
                "max_new_tokens": 512,
                "seed": 0,
            }),
            "retention_policy": retention_policy,
            "stop_policy": stop_policy,
            "result_root_marker": result_root_marker,
            "authorization_state": {
                "authorization_state_version": f"{P4_03_SCHEMA_PREFIX}.authorization_state.v1",
                "runtime_kind": "local_qwen_langgraph",
                "model_action_authorized": True,
                "graph_runtime_authorized": True,
                "dependency_installation_authorized": False,
                "training_authorized": False,
                "remote_action_authorized": False,
                "network_authorized": False,
                "telemetry_authorized": False,
                "tracing_authorized": False,
                "local_files_only_required": True,
            },
            "action_state": {
                "action_state_version": f"{P4_03_SCHEMA_PREFIX}.action_state.v1",
                "runtime_kind": "local_qwen_langgraph",
                "model_action": False,
                "graph_runtime_execution": False,
                "dependency_installation": False,
                "training": False,
                "remote_action": False,
                "network": False,
                "telemetry": False,
                "tracing": False,
                "local_files_only": True,
            },
            "training": False,
            "remote": False,
            "h1_or_gold": False,
            "formal_quality": False,
            "data_authoring": False,
        }
        root = dict(payload)
        root["policy_id"] = "pending"
        # policy_id is a caller-visible binding, but it must itself be a
        # canonical identity so it cannot be replaced by a free-form label.
        expected = _identity(root, revision="p4-03.pilot.policy.v1")["sha256"]
        payload["policy_id"] = expected
        return cls._from_payload(payload)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="PilotBinding")
        _validate_id_scope(data, "PilotBinding", "pilot_id", "policy_id", "profile_id")
        case = _exact(data["case_binding"], ("case_id", "request_id", "b_identity", "requirement_sha256", "use_case_sha256", "constraint_sha256"), "PilotBinding.case_binding")
        _validate_id_scope(case, "PilotBinding.case_binding", "case_id", "request_id")
        _validate_identity(case["b_identity"], "PilotBinding.case_binding.b_identity")
        for key in ("requirement_sha256", "use_case_sha256", "constraint_sha256"):
            _sha(case[key], f"PilotBinding.case_binding.{key}")
        if data["node_order"] != list(NODE_ORDER):
            raise Phase4LocalQwenContractError("PilotBinding.node_order drifted")
        policies = _exact(data["node_policy_identities"], NODE_ORDER, "PilotBinding.node_policy_identities")
        for node_id in NODE_ORDER:
            _sha(policies[node_id], f"PilotBinding.node_policy_identities.{node_id}")
        binding_root = dict(data)
        binding_root["policy_id"] = "pending"
        if data["policy_id"] != _identity(binding_root, revision="p4-03.pilot.policy.v1")["sha256"]:
            raise Phase4LocalQwenContractError("PilotBinding.policy_id does not bind the contract")
        if data["b_aux_disposition"] != "absent/not_requested":
            raise Phase4LocalQwenContractError("B-Aux must be absent/not_requested")
        for key, expected in (("node_total_call_cap", 3), ("node_local_call_cap", 2), ("integrated_run_cap", 1), ("retry_count_cap", 0)):
            if _integer(data[key], f"PilotBinding.{key}", minimum=0) != expected:
                raise Phase4LocalQwenContractError(f"PilotBinding.{key} is not frozen")
        if data["model_id"] != QWEN_MODEL_ID or data["model_revision"] != QWEN_MODEL_REVISION:
            raise Phase4LocalQwenContractError("PilotBinding model identity drifted")
        _text(data["retention_policy"], "PilotBinding.retention_policy")
        _text(data["stop_policy"], "PilotBinding.stop_policy")
        _text(data["result_root_marker"], "PilotBinding.result_root_marker", pattern=_ID_RE)
        _authorization_flags(data["authorization_state"], "PilotBinding.authorization_state")
        _action_flags(
            data["action_state"],
            "PilotBinding.action_state",
            model_action=False,
            graph_runtime_execution=False,
        )
        for key in ("training", "remote", "h1_or_gold", "formal_quality", "data_authoring"):
            if _bool(data[key], f"PilotBinding.{key}") is not False:
                raise Phase4LocalQwenContractError(f"PilotBinding.{key} must be false")
        decode = data["decode_config"]
        if not isinstance(decode, Mapping) or not decode:
            raise Phase4LocalQwenContractError("PilotBinding.decode_config is invalid")
        _canonical_bytes(decode)


class NodeProjectionPolicy(_CanonicalRecord):
    """Node-owned exact visible-field categories and length caps."""

    KEYS = (
        "schema_version", "policy_id", "node_id", "projection_revision",
        "allowed_categories", "prohibited_categories", "field_caps",
        "upstream_required_node_ids", "output_schema_version",
        "prompt_template_revision", "config_revision", "actual_input_source",
        "provider_visible", "b_aux_disposition", "model_output_format",
    )
    SCHEMA_VERSION = PROJECTION_POLICY_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        node_id: str,
        allowed_categories: Sequence[str],
        prohibited_categories: Sequence[str],
        field_caps: Mapping[str, object],
        upstream_required_node_ids: Sequence[str],
        prompt_template_revision: str = "p4-03-prompt-v1",
        config_revision: str = "p4-03-config-v1",
        actual_input_source: str = "same_run_live_validated_upstream",
    ) -> "NodeProjectionPolicy":
        root = {
            "schema_version": cls.SCHEMA_VERSION,
            "policy_id": "pending",
            "node_id": node_id,
            "projection_revision": "p4-03-node-projection-v1",
            "allowed_categories": list(allowed_categories),
            "prohibited_categories": list(prohibited_categories),
            "field_caps": dict(field_caps),
            "upstream_required_node_ids": list(upstream_required_node_ids),
            "output_schema_version": "req2web.phase4.f_node_output.v1",
            "prompt_template_revision": prompt_template_revision,
            "config_revision": config_revision,
            "actual_input_source": actual_input_source,
            "provider_visible": True,
            "b_aux_disposition": "absent/not_requested",
            "model_output_format": "exact_json_object",
        }
        identity_root = dict(root)
        identity_root["policy_id"] = "pending"
        root["policy_id"] = _identity(identity_root, revision="p4-03.node-policy.v1")["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="NodeProjectionPolicy")
        _sha(data["policy_id"], "NodeProjectionPolicy.policy_id")
        node_id = _text(data["node_id"], "NodeProjectionPolicy.node_id")
        if node_id not in NODE_ORDER:
            raise Phase4LocalQwenContractError("unknown projection node")
        _text(data["projection_revision"], "NodeProjectionPolicy.projection_revision")
        allowed = _string_list(data["allowed_categories"], "NodeProjectionPolicy.allowed_categories")
        prohibited = _string_list(data["prohibited_categories"], "NodeProjectionPolicy.prohibited_categories")
        expected_allowed = {
            "F1": ["canonical_b_input", "case_identity"],
            "F2": ["canonical_b_input", "validated_F1_output", "deterministic_registry"],
            "F3": ["canonical_b_input", "validated_F1_output", "validated_F2_output", "deterministic_registry"],
            "F4": ["canonical_b_input", "validated_F1_output", "validated_F2_output", "validated_F3_output", "deterministic_registry", "deterministic_mapping"],
        }[node_id]
        if allowed != expected_allowed:
            raise Phase4LocalQwenContractError("allowed projection categories drifted")
        if set(allowed) & set(prohibited):
            raise Phase4LocalQwenContractError("allowed/prohibited projection categories overlap")
        _node_policy_caps(data["field_caps"], "NodeProjectionPolicy.field_caps")
        upstream = _string_list(data["upstream_required_node_ids"], "NodeProjectionPolicy.upstream_required_node_ids", allow_empty=True)
        expected_upstream = {"F1": [], "F2": ["F1"], "F3": ["F1", "F2"], "F4": ["F1", "F2", "F3"]}[node_id]
        if upstream != expected_upstream:
            raise Phase4LocalQwenContractError("projection upstream order is not frozen")
        for value in upstream:
            if value not in NODE_ORDER:
                raise Phase4LocalQwenContractError("projection upstream node is invalid")
        for key in ("output_schema_version", "prompt_template_revision", "config_revision", "actual_input_source"):
            _text(data[key], f"NodeProjectionPolicy.{key}")
        if data["actual_input_source"] != "same_run_live_validated_upstream":
            raise Phase4LocalQwenContractError("actual input source must be same-run validated upstream")
        if _bool(data["provider_visible"], "NodeProjectionPolicy.provider_visible") is not True:
            raise Phase4LocalQwenContractError("provider_visible must be true")
        if data["b_aux_disposition"] != "absent/not_requested" or data["model_output_format"] != "exact_json_object":
            raise Phase4LocalQwenContractError("projection boundary drifted")
        policy_root = dict(data)
        policy_root["policy_id"] = "pending"
        if data["policy_id"] != _identity(policy_root, revision="p4-03.node-policy.v1")["sha256"]:
            raise Phase4LocalQwenContractError("NodeProjectionPolicy.policy_id does not bind the policy")


class LocalQwenProfile(_CanonicalRecord):
    """Runtime-owned actual local_smoke profile, not the old Tier A placeholder."""

    KEYS = (
        "schema_version", "profile_id", "profile_name", "model_id", "model_revision",
        "model_root_identity", "model_inventory_identity", "model_file_count",
        "python_version", "transformers_version", "torch_version",
        "bitsandbytes_version", "accelerate_version", "device_index", "device_name",
        "device_uuid", "total_vram_bytes", "free_vram_bytes_at_preflight",
        "driver_version", "cuda_version",
        "dtype", "quantization", "compute_dtype", "device_map", "cpu_offload",
        "local_files_only", "offline", "telemetry", "tracing", "network",
        "context_tokens", "max_input_tokens", "max_new_tokens", "timeout_seconds",
        "seed", "decode", "model_loaded", "run_occurred",
    )
    SCHEMA_VERSION = LOCAL_QWEN_PROFILE_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        model_root_identity: Mapping[str, object],
        model_inventory_identity: Mapping[str, object],
        model_file_count: int,
        python_version: str | None = None,
        device_name: str = "NVIDIA GeForce RTX 4060 Laptop GPU",
        device_uuid: str = "GPU-test-placeholder",
        total_vram_bytes: int = 8_000_000_000,
        free_vram_bytes_at_preflight: int = 6_000_000_000,
        driver_version: str = "test-driver",
        cuda_version: str = "12.8",
        context_tokens: int = 8192,
        max_input_tokens: int = 8192,
        max_new_tokens: int = 512,
        timeout_seconds: int = 600,
        seed: int = 0,
        decode: Mapping[str, object] | None = None,
    ) -> "LocalQwenProfile":
        root = {
            "schema_version": cls.SCHEMA_VERSION,
            "profile_id": "pending",
            "profile_name": LOCAL_PROFILE_NAME,
            "model_id": QWEN_MODEL_ID,
            "model_revision": QWEN_MODEL_REVISION,
            "model_root_identity": dict(model_root_identity),
            "model_inventory_identity": dict(model_inventory_identity),
            "model_file_count": model_file_count,
            "python_version": python_version or platform.python_version(),
            "transformers_version": TRANSFORMERS_VERSION,
            "torch_version": TORCH_VERSION,
            "bitsandbytes_version": BITSANDBYTES_VERSION,
            "accelerate_version": ACCELERATE_VERSION,
            "device_index": 0,
            "device_name": device_name,
            "device_uuid": device_uuid,
            "total_vram_bytes": total_vram_bytes,
            "free_vram_bytes_at_preflight": free_vram_bytes_at_preflight,
            "driver_version": driver_version,
            "cuda_version": cuda_version,
            "dtype": "bfloat16",
            "quantization": "4bit_nf4_double_quant",
            "compute_dtype": "bfloat16",
            "device_map": {"0": "cuda:0"},
            "cpu_offload": False,
            "local_files_only": True,
            "offline": True,
            "telemetry": False,
            "tracing": False,
            "network": False,
            "context_tokens": context_tokens,
            "max_input_tokens": max_input_tokens,
            "max_new_tokens": max_new_tokens,
            "timeout_seconds": timeout_seconds,
            "seed": seed,
            "decode": dict(decode or {"do_sample": False, "temperature": 0.0, "top_p": 1.0}),
            "model_loaded": False,
            "run_occurred": False,
        }
        root["profile_id"] = _sha256(_canonical_bytes({key: value for key, value in root.items() if key != "profile_id"}))
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="LocalQwenProfile")
        _sha(data["profile_id"], "LocalQwenProfile.profile_id")
        _text(data["profile_name"], "LocalQwenProfile.profile_name")
        if data["profile_name"] != LOCAL_PROFILE_NAME or data["model_id"] != QWEN_MODEL_ID or data["model_revision"] != QWEN_MODEL_REVISION:
            raise Phase4LocalQwenContractError("LocalQwenProfile model/profile identity drifted")
        _validate_identity(data["model_root_identity"], "LocalQwenProfile.model_root_identity")
        _validate_identity(data["model_inventory_identity"], "LocalQwenProfile.model_inventory_identity")
        _integer(data["model_file_count"], "LocalQwenProfile.model_file_count", minimum=1)
        for key in (
            "python_version", "transformers_version", "torch_version",
            "bitsandbytes_version", "accelerate_version", "device_name",
            "device_uuid", "driver_version", "cuda_version",
        ):
            _text(data[key], f"LocalQwenProfile.{key}")
        if data["transformers_version"] != TRANSFORMERS_VERSION or data["torch_version"] != TORCH_VERSION or data["bitsandbytes_version"] != BITSANDBYTES_VERSION or data["accelerate_version"] != ACCELERATE_VERSION:
            raise Phase4LocalQwenContractError("LocalQwenProfile runtime version drifted")
        if (
            _integer(data["device_index"], "LocalQwenProfile.device_index", minimum=0) != 0
            or data["device_name"] != "NVIDIA GeForce RTX 4060 Laptop GPU"
        ):
            raise Phase4LocalQwenContractError("LocalQwenProfile device drifted")
        total_vram = _integer(
            data["total_vram_bytes"], "LocalQwenProfile.total_vram_bytes", minimum=1
        )
        free_vram = _integer(
            data["free_vram_bytes_at_preflight"],
            "LocalQwenProfile.free_vram_bytes_at_preflight",
            minimum=0,
        )
        if free_vram > total_vram:
            raise Phase4LocalQwenContractError("LocalQwenProfile VRAM facts drifted")
        for key, expected in (("dtype", "bfloat16"), ("quantization", "4bit_nf4_double_quant"), ("compute_dtype", "bfloat16")):
            if data[key] != expected:
                raise Phase4LocalQwenContractError(f"LocalQwenProfile.{key} drifted")
        if data["device_map"] != {"0": "cuda:0"}:
            raise Phase4LocalQwenContractError("LocalQwenProfile.device_map must select only GPU0")
        for key, expected in (("cpu_offload", False), ("local_files_only", True), ("offline", True), ("telemetry", False), ("tracing", False), ("network", False), ("model_loaded", False), ("run_occurred", False)):
            if type(data[key]) is not type(expected) or data[key] != expected:
                raise Phase4LocalQwenContractError(f"LocalQwenProfile.{key} drifted")
        for key in ("context_tokens", "max_input_tokens", "max_new_tokens", "timeout_seconds"):
            _integer(data[key], f"LocalQwenProfile.{key}", minimum=1)
        _integer(data["seed"], "LocalQwenProfile.seed", minimum=0)
        if not isinstance(data["decode"], Mapping) or not data["decode"]:
            raise Phase4LocalQwenContractError("LocalQwenProfile.decode is invalid")
        _canonical_bytes(data["decode"])
        expected_id = _sha256(_canonical_bytes({key: data[key] for key in data if key != "profile_id"}))
        if data["profile_id"] != expected_id:
            raise Phase4LocalQwenContractError("LocalQwenProfile.profile_id does not bind the profile")


def _normalize_hf_device_map(
    value: object, *, name: str = "hf_device_map"
) -> dict[str, int | str]:
    """Normalize optional Transformers placement metadata without trusting it."""

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise Phase4LocalQwenContractError(f"{name} must be a mapping or None")
    normalized: dict[str, int | str] = {}
    seen_keys: set[str] = set()
    for key, device in value.items():
        if type(key) is not str:
            raise Phase4LocalQwenContractError(f"{name} keys must be strings")
        if key in seen_keys:
            raise Phase4LocalQwenContractError(f"{name} contains duplicate keys")
        seen_keys.add(key)
        is_gpu0 = (type(device) is int and device == 0) or (
            type(device) is str and device in {"0", "cuda:0"}
        )
        if not is_gpu0:
            raise Phase4LocalQwenContractError(
                f"{name} contains a non-GPU0 placement"
            )
        normalized[key] = device
    return normalized


def _validate_live_model_placement(
    model: object,
) -> tuple[dict[str, int | str], set[str]]:
    """Validate optional map metadata and authoritative live parameter placement."""

    normalized_device_map = _normalize_hf_device_map(
        getattr(model, "hf_device_map", None)
    )
    parameter_devices = {str(parameter.device) for parameter in model.parameters()}
    if parameter_devices != {"cuda:0"}:
        raise Phase4LocalQwenContractError(
            "loaded parameters are not exclusively on GPU0"
        )
    return normalized_device_map, parameter_devices


class LocalQwenLoadReceipt(_CanonicalRecord):
    """Action-time proof that the exact offline model profile was loaded."""

    KEYS = (
        "schema_version", "receipt_id", "manifest_id", "pilot_id",
        "profile_identity", "loaded_facts", "model_loaded",
        "generation_occurred", "action_state",
    )
    SCHEMA_VERSION = LOAD_RECEIPT_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        manifest: "PreCallManifest",
        pilot: PilotBinding,
        profile: LocalQwenProfile,
        loaded_facts: Mapping[str, object],
    ) -> "LocalQwenLoadReceipt":
        manifest.validate()
        pilot.validate()
        profile.validate()
        normalized_loaded_facts = dict(loaded_facts)
        normalized_loaded_facts["hf_device_map"] = _normalize_hf_device_map(
            normalized_loaded_facts.get("hf_device_map"),
            name="LocalQwenLoadReceipt.loaded_facts.hf_device_map",
        )
        root = {
            "schema_version": cls.SCHEMA_VERSION,
            "receipt_id": "pending",
            "manifest_id": manifest.manifest_id,
            "pilot_id": pilot.pilot_id,
            "profile_identity": _identity(
                profile.to_dict(), revision=LOCAL_QWEN_PROFILE_SCHEMA_VERSION
            ),
            "loaded_facts": normalized_loaded_facts,
            "model_loaded": True,
            "generation_occurred": False,
            "action_state": {
                "action_state_version": f"{P4_03_SCHEMA_PREFIX}.action_state.v1",
                "runtime_kind": "local_qwen_langgraph",
                "model_action": True,
                "graph_runtime_execution": False,
                "dependency_installation": False,
                "training": False,
                "remote_action": False,
                "network": False,
                "telemetry": False,
                "tracing": False,
                "local_files_only": True,
            },
        }
        root["receipt_id"] = _sha256(
            _canonical_bytes({key: value for key, value in root.items() if key != "receipt_id"})
        )
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="LocalQwenLoadReceipt")
        _sha(data["receipt_id"], "LocalQwenLoadReceipt.receipt_id")
        _sha(data["manifest_id"], "LocalQwenLoadReceipt.manifest_id")
        _text(data["pilot_id"], "LocalQwenLoadReceipt.pilot_id", pattern=_ID_RE)
        _validate_identity(data["profile_identity"], "LocalQwenLoadReceipt.profile_identity")
        facts = _exact(
            data["loaded_facts"],
            (
                "model_class", "processor_class", "is_loaded_in_4bit",
                "hf_device_map", "parameter_devices", "compute_dtypes",
                "cpu_offload", "device",
            ),
            "LocalQwenLoadReceipt.loaded_facts",
        )
        normalized_device_map = _normalize_hf_device_map(
            facts["hf_device_map"],
            name="LocalQwenLoadReceipt.loaded_facts.hf_device_map",
        )
        if (
            facts["model_class"] != "Qwen3_5ForConditionalGeneration"
            or facts["processor_class"] != "Qwen3VLProcessor"
            or _bool(facts["is_loaded_in_4bit"], "load.is_loaded_in_4bit") is not True
            or facts["parameter_devices"] != ["cuda:0"]
            or facts["compute_dtypes"] != ["torch.bfloat16"]
            or _bool(facts["cpu_offload"], "load.cpu_offload") is not False
            or facts["device"] != "cuda:0"
            or facts["hf_device_map"] != normalized_device_map
        ):
            raise Phase4LocalQwenContractError("loaded local Qwen facts drifted")
        if (
            _bool(data["model_loaded"], "LocalQwenLoadReceipt.model_loaded") is not True
            or _bool(data["generation_occurred"], "LocalQwenLoadReceipt.generation_occurred") is not False
        ):
            raise Phase4LocalQwenContractError("load receipt action facts drifted")
        _action_flags(
            data["action_state"],
            "LocalQwenLoadReceipt.action_state",
            model_action=True,
            graph_runtime_execution=False,
        )
        expected_id = _sha256(
            _canonical_bytes({key: data[key] for key in data if key != "receipt_id"})
        )
        if data["receipt_id"] != expected_id:
            raise Phase4LocalQwenContractError("load receipt identity drifted")

    def validate_against(
        self,
        *,
        manifest: "PreCallManifest",
        pilot: PilotBinding,
        profile: LocalQwenProfile,
    ) -> None:
        self.validate()
        if (
            self.manifest_id != manifest.manifest_id
            or self.pilot_id != pilot.pilot_id
            or self.profile_identity
            != _identity(profile.to_dict(), revision=LOCAL_QWEN_PROFILE_SCHEMA_VERSION)
        ):
            raise Phase4LocalQwenContractError("load receipt binding drifted")


class WorkerStderrArtifact(_CanonicalRecord):
    """Canonical, replayable bytes captured from the supervised worker stderr."""

    KEYS = ("schema_version", "stderr_b64")
    SCHEMA_VERSION = WORKER_STDERR_SCHEMA_VERSION

    @classmethod
    def create(cls, *, stderr_bytes: bytes) -> "WorkerStderrArtifact":
        if type(stderr_bytes) is not bytes:
            raise Phase4LocalQwenContractError("worker stderr must be bytes")
        return cls._from_payload(
            {
                "schema_version": cls.SCHEMA_VERSION,
                "stderr_b64": _b64(stderr_bytes, "WorkerStderrArtifact.stderr_bytes"),
            }
        )  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="WorkerStderrArtifact")
        _decode_b64(
            data["stderr_b64"],
            "WorkerStderrArtifact.stderr_b64",
            allow_empty=True,
        )

    @property
    def stderr_bytes(self) -> bytes:
        return _decode_b64(
            self.to_dict()["stderr_b64"],
            "WorkerStderrArtifact.stderr_b64",
            allow_empty=True,
        )

    def identity(self) -> dict[str, object]:
        return _identity(self.to_dict(), revision=self.SCHEMA_VERSION)


def _action_record_root(data: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in data.items() if key != "action_record_id"}


class NodeD17ActionRecord(_CanonicalRecord):
    """D17-owned immutable action-time record written immediately pre-call."""

    KEYS = (
        "schema_version", "action_record_id", "pilot_id", "run_id", "case_id",
        "request_id", "node_id", "attempt_index", "call_kind", "profile_identity",
        "manifest_ref", "policy_ref", "input_view_ref", "upstream_refs",
        "actual_input_b64", "actual_input_sha256", "actual_input_byte_length",
        "prompt_revision", "prompt_change", "prompt_b64", "prompt_sha256", "prompt_byte_length",
        "config_revision", "config_b64", "config_sha256", "config_byte_length",
        "request_b64", "request_sha256", "request_byte_length", "budget_identity",
        "call_count", "retry_count", "generate_started", "authorization_state",
        "action_state", "source_kind", "runtime_load_ref", "record_status",
    )
    SCHEMA_VERSION = NODE_D17_ACTION_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        pilot_id: str,
        run_id: str,
        case_id: str,
        request_id: str,
        node_id: str,
        attempt_index: int,
        call_kind: str,
        profile: LocalQwenProfile,
        manifest_ref: Mapping[str, object],
        policy_ref: Mapping[str, object],
        input_view_ref: Mapping[str, object],
        upstream_refs: Sequence[Mapping[str, object]],
        actual_input: bytes,
        prompt_revision: str,
        prompt_change: Mapping[str, object] | None,
        prompt: bytes,
        config_revision: str,
        config: bytes,
        request: bytes,
        budget_identity: Mapping[str, object],
        source_kind: str,
        runtime_load_ref: Mapping[str, object] | None,
    ) -> "NodeD17ActionRecord":
        payload = {
            "schema_version": cls.SCHEMA_VERSION,
            "action_record_id": "pending",
            "pilot_id": pilot_id,
            "run_id": run_id,
            "case_id": case_id,
            "request_id": request_id,
            "node_id": node_id,
            "attempt_index": attempt_index,
            "call_kind": call_kind,
            "profile_identity": _identity(profile.to_dict(), revision=LOCAL_QWEN_PROFILE_SCHEMA_VERSION),
            "manifest_ref": dict(manifest_ref),
            "policy_ref": dict(policy_ref),
            "input_view_ref": dict(input_view_ref),
            "upstream_refs": [dict(item) for item in upstream_refs],
            "actual_input_b64": _b64(actual_input, "actual_input"),
            "actual_input_sha256": _sha256(actual_input),
            "actual_input_byte_length": len(actual_input),
            "prompt_revision": prompt_revision,
            "prompt_change": None if prompt_change is None else dict(prompt_change),
            "prompt_b64": _b64(prompt, "prompt"),
            "prompt_sha256": _sha256(prompt),
            "prompt_byte_length": len(prompt),
            "config_revision": config_revision,
            "config_b64": _b64(config, "config"),
            "config_sha256": _sha256(config),
            "config_byte_length": len(config),
            "request_b64": _b64(request, "request"),
            "request_sha256": _sha256(request),
            "request_byte_length": len(request),
            "budget_identity": dict(budget_identity),
            "call_count": 1,
            "retry_count": 0,
            "generate_started": False,
            "authorization_state": {
                "authorization_state_version": f"{P4_03_SCHEMA_PREFIX}.authorization_state.v1",
                "runtime_kind": "local_qwen_langgraph",
                "model_action_authorized": True,
                "graph_runtime_authorized": True,
                "dependency_installation_authorized": False,
                "training_authorized": False,
                "remote_action_authorized": False,
                "network_authorized": False,
                "telemetry_authorized": False,
                "tracing_authorized": False,
                "local_files_only_required": True,
            },
            "action_state": {
                "action_state_version": f"{P4_03_SCHEMA_PREFIX}.action_state.v1",
                "runtime_kind": "local_qwen_langgraph",
                "model_action": False,
                "graph_runtime_execution": False,
                "dependency_installation": False,
                "training": False,
                "remote_action": False,
                "network": False,
                "telemetry": False,
                "tracing": False,
                "local_files_only": True,
            },
            "source_kind": source_kind,
            "runtime_load_ref": None if runtime_load_ref is None else dict(runtime_load_ref),
            "record_status": "pre_call_ready",
        }
        payload["action_record_id"] = _sha256(_canonical_bytes(_action_record_root(payload)))
        return cls._from_payload(payload)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="NodeD17ActionRecord")
        _sha(data["action_record_id"], "NodeD17ActionRecord.action_record_id")
        _validate_id_scope(data, "NodeD17ActionRecord", "pilot_id", "run_id", "case_id", "request_id")
        node_id = _text(data["node_id"], "NodeD17ActionRecord.node_id")
        if node_id not in NODE_ORDER:
            raise Phase4LocalQwenContractError("NodeD17ActionRecord.node_id is invalid")
        _integer(data["attempt_index"], "NodeD17ActionRecord.attempt_index", minimum=1, maximum=3)
        if data["call_kind"] not in CALL_KINDS:
            raise Phase4LocalQwenContractError("NodeD17ActionRecord.call_kind is invalid")
        _validate_identity(data["profile_identity"], "NodeD17ActionRecord.profile_identity")
        _ref(data["manifest_ref"], "NodeD17ActionRecord.manifest_ref", "d17_manifest")
        _ref(data["policy_ref"], "NodeD17ActionRecord.policy_ref", "d17_policy")
        _ref(data["input_view_ref"], "NodeD17ActionRecord.input_view_ref", "d17_input_view")
        if type(data["upstream_refs"]) is not list:
            raise Phase4LocalQwenContractError("NodeD17ActionRecord.upstream_refs must be a list")
        for item in data["upstream_refs"]:
            _ref(item, "NodeD17ActionRecord.upstream_ref", "node_output")
        decoded: dict[str, bytes] = {}
        for prefix, b64_key, sha_key, length_key in (("input", "actual_input_b64", "actual_input_sha256", "actual_input_byte_length"), ("prompt", "prompt_b64", "prompt_sha256", "prompt_byte_length"), ("config", "config_b64", "config_sha256", "config_byte_length"), ("request", "request_b64", "request_sha256", "request_byte_length")):
            raw = _bytes_fields(data, "NodeD17ActionRecord", b64_key, sha_key, length_key)
            decoded[prefix] = raw
            if prefix != "prompt" and not raw:
                raise Phase4LocalQwenContractError(f"NodeD17ActionRecord.{prefix} cannot be empty")
        prompt_revision = _text(data["prompt_revision"], "NodeD17ActionRecord.prompt_revision")
        prompt_payload = _strict_json(decoded["prompt"])
        if prompt_revision == "p4-03-prompt-v1":
            if (
                data["prompt_change"] is not None
                or prompt_payload.get("prompt_schema_version") != f"{P4_03_SCHEMA_PREFIX}.prompt.v1"
                or prompt_payload.get("template_revision") != prompt_revision
            ):
                raise Phase4LocalQwenContractError("initial prompt action binding drifted")
        elif prompt_revision == "p4-03-prompt-v2":
            change = _exact(
                data["prompt_change"],
                ("prior_result_id", "prior_failure_identity", "change_reason"),
                "NodeD17ActionRecord.prompt_change",
            )
            _sha(change["prior_result_id"], "prompt_change.prior_result_id")
            prior_identity = _validate_identity(
                change["prior_failure_identity"], "prompt_change.prior_failure_identity"
            )
            if (
                change["change_reason"] not in CHANGE_REASONS
                or prompt_payload.get("prompt_schema_version") != f"{P4_03_SCHEMA_PREFIX}.prompt.v2"
                or prompt_payload.get("template_revision") != prompt_revision
                or prompt_payload.get("prior_failure_identity") != prior_identity
                or prompt_payload.get("change_reason") != change["change_reason"]
            ):
                raise Phase4LocalQwenContractError("second prompt action binding drifted")
        else:
            raise Phase4LocalQwenContractError("action prompt revision is invalid")
        _text(data["config_revision"], "NodeD17ActionRecord.config_revision")
        _validate_identity(data["budget_identity"], "NodeD17ActionRecord.budget_identity")
        if _integer(data["call_count"], "NodeD17ActionRecord.call_count") != 1 or _integer(data["retry_count"], "NodeD17ActionRecord.retry_count") != 0:
            raise Phase4LocalQwenContractError("NodeD17ActionRecord call/retry envelope drifted")
        if _bool(data["generate_started"], "NodeD17ActionRecord.generate_started") is not False or data["record_status"] != "pre_call_ready":
            raise Phase4LocalQwenContractError("NodeD17ActionRecord must be pre-call")
        _authorization_flags(
            data["authorization_state"],
            "NodeD17ActionRecord.authorization_state",
        )
        _action_flags(
            data["action_state"],
            "NodeD17ActionRecord.action_state",
            model_action=False,
            graph_runtime_execution=False,
        )
        if data["source_kind"] not in SOURCE_KINDS:
            raise Phase4LocalQwenContractError("NodeD17ActionRecord source kind is invalid")
        if data["source_kind"] == "real_local_qwen":
            _ref(
                data["runtime_load_ref"],
                "NodeD17ActionRecord.runtime_load_ref",
                "local_qwen_load_receipt",
            )
        elif data["runtime_load_ref"] is not None:
            raise Phase4LocalQwenContractError("fixture action cannot bind a real load receipt")
        expected_id = _sha256(_canonical_bytes(_action_record_root(data)))
        if data["action_record_id"] != expected_id:
            raise Phase4LocalQwenContractError("NodeD17ActionRecord identity drifted")

    def validate_against(
        self,
        *,
        pilot: PilotBinding,
        policy: NodeProjectionPolicy,
        profile: LocalQwenProfile,
        load_receipt: LocalQwenLoadReceipt | None = None,
    ) -> None:
        self.validate()
        if self.pilot_id != pilot.pilot_id or self.case_id != pilot.case_binding["case_id"] or self.request_id != pilot.case_binding["request_id"]:
            raise Phase4LocalQwenContractError("NodeD17ActionRecord pilot/case binding drifted")
        if self.node_id != policy.node_id or self.policy_ref["ref_sha256"] != policy.sha256():
            raise Phase4LocalQwenContractError("NodeD17ActionRecord policy binding drifted")
        if len(self.upstream_refs) > _node_policy_caps(
            policy.field_caps, "policy.field_caps"
        )["ref_count"]:
            raise Phase4LocalQwenContractError("action upstream refs exceed cap")
        if self.profile_identity["sha256"] != _identity(profile.to_dict(), revision=LOCAL_QWEN_PROFILE_SCHEMA_VERSION)["sha256"]:
            raise Phase4LocalQwenContractError("NodeD17ActionRecord profile binding drifted")
        if self.source_kind == "real_local_qwen":
            if load_receipt is None:
                raise Phase4LocalQwenContractError("real action record lacks live load receipt")
            load_receipt.validate()
            expected_ref = {
                "ref_type": "local_qwen_load_receipt",
                "ref_id": load_receipt.receipt_id,
                "ref_sha256": load_receipt.sha256(),
                "ref_revision": LOAD_RECEIPT_SCHEMA_VERSION,
            }
            if self.runtime_load_ref != expected_ref:
                raise Phase4LocalQwenContractError("action/load receipt binding drifted")


class AttemptPreCall(_CanonicalRecord):
    """Runner-owned envelope persisted after the D17 action record."""

    KEYS = (
        "schema_version", "pre_call_id", "action_record_id", "pilot_id", "run_id",
        "case_id", "request_id", "node_id", "attempt_index", "call_kind",
        "action_record_identity", "input_identity", "prompt_identity", "config_identity",
        "request_identity", "upstream_identities", "budget_identity",
        "attempt_relative_path", "raw_response_relative_path", "call_count",
        "retry_count", "record_fsync_required", "source_kind", "status",
    )
    SCHEMA_VERSION = PRE_CALL_SCHEMA_VERSION

    @classmethod
    def create(cls, *, action: NodeD17ActionRecord, attempt_relative_path: str, raw_response_relative_path: str, upstream_identities: Sequence[Mapping[str, object]]) -> "AttemptPreCall":
        action.validate()
        root = {
            "schema_version": cls.SCHEMA_VERSION,
            "pre_call_id": "pending",
            "action_record_id": action.action_record_id,
            "pilot_id": action.pilot_id,
            "run_id": action.run_id,
            "case_id": action.case_id,
            "request_id": action.request_id,
            "node_id": action.node_id,
            "attempt_index": action.attempt_index,
            "call_kind": action.call_kind,
            "action_record_identity": _identity(action.to_dict(), revision=NODE_D17_ACTION_SCHEMA_VERSION),
            "input_identity": {"sha256": action.actual_input_sha256, "byte_length": action.actual_input_byte_length},
            "prompt_identity": {"sha256": action.prompt_sha256, "byte_length": action.prompt_byte_length},
            "config_identity": {"sha256": action.config_sha256, "byte_length": action.config_byte_length},
            "request_identity": {"sha256": action.request_sha256, "byte_length": action.request_byte_length},
            "upstream_identities": [dict(item) for item in upstream_identities],
            "budget_identity": dict(action.budget_identity),
            "attempt_relative_path": attempt_relative_path,
            "raw_response_relative_path": raw_response_relative_path,
            "call_count": 1,
            "retry_count": 0,
            "record_fsync_required": True,
            "source_kind": action.source_kind,
            "status": "pre_call_ready",
        }
        root["pre_call_id"] = _sha256(_canonical_bytes({key: value for key, value in root.items() if key != "pre_call_id"}))
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="AttemptPreCall")
        _sha(data["pre_call_id"], "AttemptPreCall.pre_call_id")
        _sha(data["action_record_id"], "AttemptPreCall.action_record_id")
        _validate_id_scope(data, "AttemptPreCall", "pilot_id", "run_id", "case_id", "request_id")
        if data["node_id"] not in NODE_ORDER or data["call_kind"] not in CALL_KINDS:
            raise Phase4LocalQwenContractError("AttemptPreCall node/call kind is invalid")
        _integer(data["attempt_index"], "AttemptPreCall.attempt_index", minimum=1, maximum=3)
        _validate_identity(data["action_record_identity"], "AttemptPreCall.action_record_identity")
        for key in ("input_identity", "prompt_identity", "config_identity", "request_identity"):
            item = _exact(data[key], ("sha256", "byte_length"), f"AttemptPreCall.{key}")
            _sha(item["sha256"], f"AttemptPreCall.{key}.sha256")
            _integer(item["byte_length"], f"AttemptPreCall.{key}.byte_length", minimum=0)
        if type(data["upstream_identities"]) is not list:
            raise Phase4LocalQwenContractError("AttemptPreCall.upstream_identities must be a list")
        for item in data["upstream_identities"]:
            _ref(item, "AttemptPreCall.upstream_identity", "node_output")
        _validate_identity(data["budget_identity"], "AttemptPreCall.budget_identity")
        _relative_path(data["attempt_relative_path"], "AttemptPreCall.attempt_relative_path")
        _relative_path(data["raw_response_relative_path"], "AttemptPreCall.raw_response_relative_path")
        if _integer(data["call_count"], "AttemptPreCall.call_count") != 1 or _integer(data["retry_count"], "AttemptPreCall.retry_count") != 0:
            raise Phase4LocalQwenContractError("AttemptPreCall call/retry envelope drifted")
        if (
            _bool(data["record_fsync_required"], "AttemptPreCall.record_fsync_required")
            is not True
            or data["source_kind"] not in SOURCE_KINDS
            or data["status"] != "pre_call_ready"
        ):
            raise Phase4LocalQwenContractError("AttemptPreCall status drifted")
        expected_id = _sha256(_canonical_bytes({key: data[key] for key in data if key != "pre_call_id"}))
        if data["pre_call_id"] != expected_id:
            raise Phase4LocalQwenContractError("AttemptPreCall identity drifted")


class AttemptResult(_CanonicalRecord):
    """Immutable result of one generate envelope, including failed calls."""

    KEYS = (
        "schema_version", "result_id", "pre_call_id", "action_record_id", "pilot_id",
        "run_id", "case_id", "request_id", "node_id", "attempt_index", "call_kind",
        "generate_started", "call_count", "retry_count", "raw_status",
        "raw_response_relative_path", "raw_sha256", "raw_byte_length", "parse_status",
        "node_contract_status", "registry_status", "composition_status",
        "assembler_status", "integrated_success", "failure_code", "failure_identity",
        "terminal", "source_kind", "action_state",
    )
    SCHEMA_VERSION = ATTEMPT_RESULT_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        pre_call: AttemptPreCall,
        generate_started: bool,
        raw_status: str,
        raw_response_relative_path: str,
        raw_sha256: str | None,
        raw_byte_length: int,
        parse_status: str,
        node_contract_status: str,
        registry_status: str,
        composition_status: str,
        assembler_status: str,
        integrated_success: bool,
        failure_code: str | None,
        terminal: bool,
    ) -> "AttemptResult":
        pre_call.validate()
        root = {
            "schema_version": cls.SCHEMA_VERSION,
            "result_id": "pending",
            "pre_call_id": pre_call.pre_call_id,
            "action_record_id": pre_call.action_record_id,
            "pilot_id": pre_call.pilot_id,
            "run_id": pre_call.run_id,
            "case_id": pre_call.case_id,
            "request_id": pre_call.request_id,
            "node_id": pre_call.node_id,
            "attempt_index": pre_call.attempt_index,
            "call_kind": pre_call.call_kind,
            "generate_started": generate_started,
            "call_count": 1,
            "retry_count": 0,
            "raw_status": raw_status,
            "raw_response_relative_path": raw_response_relative_path,
            "raw_sha256": raw_sha256,
            "raw_byte_length": raw_byte_length,
            "parse_status": parse_status,
            "node_contract_status": node_contract_status,
            "registry_status": registry_status,
            "composition_status": composition_status,
            "assembler_status": assembler_status,
            "integrated_success": integrated_success,
            "failure_code": failure_code,
            "failure_identity": None,
            "terminal": terminal,
            "source_kind": pre_call.source_kind,
            "action_state": {
                "action_state_version": f"{P4_03_SCHEMA_PREFIX}.action_state.v1",
                "runtime_kind": "local_qwen_langgraph",
                "model_action": generate_started and pre_call.source_kind == "real_local_qwen",
                "graph_runtime_execution": generate_started and pre_call.call_kind == "integrated",
                "dependency_installation": False,
                "training": False,
                "remote_action": False,
                "network": False,
                "telemetry": False,
                "tracing": False,
                "local_files_only": True,
            },
        }
        if failure_code is not None:
            failure = {
                "failure_code": failure_code,
                "failure_stage": "provider_generation" if parse_status == "failed" else "provider_runtime",
                "retry_allowed": False,
                "fallback_allowed": False,
                "source_refs": [],
                "message_code": f"p4_03_{failure_code}",
            }
            root["failure_identity"] = _identity(failure, revision=f"{P4_03_SCHEMA_PREFIX}.failure.v1")
        root["result_id"] = _sha256(_canonical_bytes({key: value for key, value in root.items() if key != "result_id"}))
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="AttemptResult")
        _sha(data["result_id"], "AttemptResult.result_id")
        _sha(data["pre_call_id"], "AttemptResult.pre_call_id")
        _sha(data["action_record_id"], "AttemptResult.action_record_id")
        _validate_id_scope(data, "AttemptResult", "pilot_id", "run_id", "case_id", "request_id")
        if data["node_id"] not in NODE_ORDER or data["call_kind"] not in CALL_KINDS:
            raise Phase4LocalQwenContractError("AttemptResult node/call kind is invalid")
        _integer(data["attempt_index"], "AttemptResult.attempt_index", minimum=1, maximum=3)
        _bool(data["generate_started"], "AttemptResult.generate_started")
        if _integer(data["call_count"], "AttemptResult.call_count") != 1 or _integer(data["retry_count"], "AttemptResult.retry_count") != 0:
            raise Phase4LocalQwenContractError("AttemptResult call/retry envelope drifted")
        raw_statuses = {"not_captured", "captured_empty", "captured_nonempty"}
        if data["raw_status"] not in raw_statuses:
            raise Phase4LocalQwenContractError("AttemptResult.raw_status is invalid")
        _relative_path(data["raw_response_relative_path"], "AttemptResult.raw_response_relative_path")
        raw_length = _integer(data["raw_byte_length"], "AttemptResult.raw_byte_length", minimum=0)
        if data["raw_sha256"] is None:
            if raw_statuses - {"not_captured"} & {data["raw_status"]} or raw_length != 0:
                raise Phase4LocalQwenContractError("captured raw requires an identity")
        else:
            _sha(data["raw_sha256"], "AttemptResult.raw_sha256")
            if data["raw_status"] == "not_captured":
                raise Phase4LocalQwenContractError("not_captured result cannot carry raw identity")
        status_values = {"not_run", "passed", "failed", "blocked"}
        for key in ("parse_status", "node_contract_status", "registry_status", "composition_status", "assembler_status"):
            if data[key] not in status_values | {"not_executed", "validated", "assembled"}:
                raise Phase4LocalQwenContractError(f"AttemptResult.{key} is invalid")
        _bool(data["integrated_success"], "AttemptResult.integrated_success")
        if data["integrated_success"] and data["call_kind"] != "integrated":
            raise Phase4LocalQwenContractError("node-local result cannot be integrated success")
        if data["failure_code"] is None:
            if data["failure_identity"] is not None or data["terminal"] is not False and data["call_kind"] == "node_local":
                raise Phase4LocalQwenContractError("successful attempt failure binding is invalid")
        else:
            _text(data["failure_code"], "AttemptResult.failure_code", pattern=_ID_RE)
            _validate_identity(data["failure_identity"], "AttemptResult.failure_identity")
        _bool(data["terminal"], "AttemptResult.terminal")
        if data["source_kind"] not in SOURCE_KINDS:
            raise Phase4LocalQwenContractError("AttemptResult source kind is invalid")
        _action_flags(
            data["action_state"],
            "AttemptResult.action_state",
            model_action=(
                data["generate_started"] and data["source_kind"] == "real_local_qwen"
            ),
            graph_runtime_execution=(
                data["generate_started"] and data["call_kind"] == "integrated"
            ),
        )
        expected_id = _sha256(_canonical_bytes({key: data[key] for key in data if key != "result_id"}))
        if data["result_id"] != expected_id:
            raise Phase4LocalQwenContractError("AttemptResult identity drifted")


class AttemptLedger(_CanonicalRecord):
    """Manager-owned cumulative call budget and stop-state record."""

    KEYS = (
        "schema_version", "ledger_id", "pilot_id", "case_id", "request_id",
        "node_total_counts", "node_local_counts", "integrated_run_count",
        "attempt_result_ids", "prior_failure_ids", "status", "stop_reason",
    )
    SCHEMA_VERSION = LEDGER_SCHEMA_VERSION

    @classmethod
    def create(cls, *, pilot: PilotBinding) -> "AttemptLedger":
        case = pilot.case_binding
        root = {
            "schema_version": cls.SCHEMA_VERSION,
            "ledger_id": "pending",
            "pilot_id": pilot.pilot_id,
            "case_id": case["case_id"],
            "request_id": case["request_id"],
            "node_total_counts": {node_id: 0 for node_id in NODE_ORDER},
            "node_local_counts": {node_id: 0 for node_id in NODE_ORDER},
            "integrated_run_count": 0,
            "attempt_result_ids": [],
            "prior_failure_ids": [],
            "status": "running",
            "stop_reason": None,
        }
        root["ledger_id"] = _sha256(_canonical_bytes({key: value for key, value in root.items() if key != "ledger_id"}))
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="AttemptLedger")
        _sha(data["ledger_id"], "AttemptLedger.ledger_id")
        _validate_id_scope(data, "AttemptLedger", "pilot_id", "case_id", "request_id")
        total = _exact(data["node_total_counts"], NODE_ORDER, "AttemptLedger.node_total_counts")
        local = _exact(data["node_local_counts"], NODE_ORDER, "AttemptLedger.node_local_counts")
        for node_id in NODE_ORDER:
            _integer(total[node_id], f"AttemptLedger.node_total_counts.{node_id}", minimum=0, maximum=3)
            _integer(local[node_id], f"AttemptLedger.node_local_counts.{node_id}", minimum=0, maximum=2)
            if local[node_id] > total[node_id]:
                raise Phase4LocalQwenContractError("local call count cannot exceed total call count")
        _integer(data["integrated_run_count"], "AttemptLedger.integrated_run_count", minimum=0, maximum=1)
        if type(data["attempt_result_ids"]) is not list or len(data["attempt_result_ids"]) != len(set(data["attempt_result_ids"])):
            raise Phase4LocalQwenContractError("AttemptLedger attempt identities are invalid")
        for value in data["attempt_result_ids"]:
            _sha(value, "AttemptLedger.attempt_result_ids[]")
        if type(data["prior_failure_ids"]) is not list or len(data["prior_failure_ids"]) != len(set(data["prior_failure_ids"])):
            raise Phase4LocalQwenContractError("AttemptLedger prior failure identities are invalid")
        for value in data["prior_failure_ids"]:
            _sha(value, "AttemptLedger.prior_failure_ids[]")
        if data["status"] not in {"running", "stopped", "terminal"}:
            raise Phase4LocalQwenContractError("AttemptLedger.status is invalid")
        if data["stop_reason"] is not None:
            _text(data["stop_reason"], "AttemptLedger.stop_reason", pattern=_ID_RE)
        expected_id = _sha256(_canonical_bytes({key: data[key] for key in data if key != "ledger_id"}))
        if data["ledger_id"] != expected_id:
            raise Phase4LocalQwenContractError("AttemptLedger identity drifted")

    def record_generate_started(self, *, node_id: str, call_kind: str, result_id: str | None = None) -> "AttemptLedger":
        self.validate()
        if node_id not in NODE_ORDER or call_kind not in CALL_KINDS:
            raise Phase4LocalQwenContractError("invalid ledger generate-start binding")
        data = self.to_dict()
        if data["status"] != "running":
            raise Phase4LocalQwenContractError("ledger is already stopped")
        total = dict(data["node_total_counts"])
        local = dict(data["node_local_counts"])
        if total[node_id] >= 3:
            raise Phase4LocalQwenContractError("node total call cap exhausted")
        if call_kind == "node_local":
            if local[node_id] >= 2:
                raise Phase4LocalQwenContractError("node-local call cap exhausted")
            local[node_id] += 1
        else:
            if data["integrated_run_count"] != 1:
                raise Phase4LocalQwenContractError("integrated run was not pre-registered")
        total[node_id] += 1
        if result_id is not None:
            _sha(result_id, "AttemptLedger.result_id")
            data["attempt_result_ids"].append(result_id)
        data["node_total_counts"] = total
        data["node_local_counts"] = local
        data["ledger_id"] = "pending"
        data["ledger_id"] = _sha256(_canonical_bytes({key: value for key, value in data.items() if key != "ledger_id"}))
        return type(self)._from_payload(data)  # type: ignore[return-value]

    def begin_integrated_run(self) -> "AttemptLedger":
        self.validate()
        data = self.to_dict()
        if data["status"] != "running" or data["integrated_run_count"] >= 1:
            raise Phase4LocalQwenContractError("integrated run cap exhausted")
        data["integrated_run_count"] = 1
        data["ledger_id"] = "pending"
        data["ledger_id"] = _sha256(_canonical_bytes({key: value for key, value in data.items() if key != "ledger_id"}))
        return type(self)._from_payload(data)  # type: ignore[return-value]

    def record_result(self, result: AttemptResult) -> "AttemptLedger":
        self.validate()
        result.validate()
        data = self.to_dict()
        if result.result_id in data["attempt_result_ids"]:
            raise Phase4LocalQwenContractError("ledger already contains attempt result")
        data["attempt_result_ids"].append(result.result_id)
        if result.failure_identity is not None and result.failure_identity["sha256"] not in data["prior_failure_ids"]:
            data["prior_failure_ids"].append(result.failure_identity["sha256"])
        data["ledger_id"] = "pending"
        data["ledger_id"] = _sha256(_canonical_bytes({key: value for key, value in data.items() if key != "ledger_id"}))
        return type(self)._from_payload(data)  # type: ignore[return-value]

    def stop(self, reason: str, *, terminal: bool = False) -> "AttemptLedger":
        self.validate()
        data = self.to_dict()
        data["status"] = "terminal" if terminal else "stopped"
        data["stop_reason"] = _text(reason, "AttemptLedger.stop_reason", pattern=_ID_RE)
        data["ledger_id"] = "pending"
        data["ledger_id"] = _sha256(_canonical_bytes({key: value for key, value in data.items() if key != "ledger_id"}))
        return type(self)._from_payload(data)  # type: ignore[return-value]


class GraphCheckpointBinding(_CanonicalRecord):
    """LangGraph-owned scheduling/checkpoint binding, never domain authority."""

    KEYS = (
        "schema_version", "checkpoint_id", "pilot_id", "run_id", "case_id",
        "request_id", "graph_revision", "contract_revision", "profile_identity",
        "last_event_seq", "completed_node_ids", "pending_node_ids", "state_identity",
        "checkpoint_identity", "resume_status",
    )
    SCHEMA_VERSION = CHECKPOINT_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        pilot: PilotBinding,
        run_id: str,
        profile: LocalQwenProfile,
        graph_revision: str,
        contract_revision: str,
        last_event_seq: int,
        completed_node_ids: Sequence[str],
        pending_node_ids: Sequence[str],
        state_identity: Mapping[str, object],
        resume_status: str = "not_requested",
    ) -> "GraphCheckpointBinding":
        if resume_status != "not_requested":
            raise Phase4LocalQwenContractError(
                "P4-03 real pilot resume is not implemented"
            )
        case = pilot.case_binding
        root = {
            "schema_version": cls.SCHEMA_VERSION,
            "checkpoint_id": "pending",
            "pilot_id": pilot.pilot_id,
            "run_id": run_id,
            "case_id": case["case_id"],
            "request_id": case["request_id"],
            "graph_revision": graph_revision,
            "contract_revision": contract_revision,
            "profile_identity": _identity(profile.to_dict(), revision=LOCAL_QWEN_PROFILE_SCHEMA_VERSION),
            "last_event_seq": last_event_seq,
            "completed_node_ids": list(completed_node_ids),
            "pending_node_ids": list(pending_node_ids),
            "state_identity": dict(state_identity),
            "checkpoint_identity": {"identity_kind": "pending", "sha256": _sha256(b"pending"), "byte_length": 7, "revision": "pending"},
            "resume_status": resume_status,
        }
        root["checkpoint_identity"] = _identity({key: value for key, value in root.items() if key not in {"checkpoint_id", "checkpoint_identity"}}, revision=CHECKPOINT_SCHEMA_VERSION)
        root["checkpoint_id"] = root["checkpoint_identity"]["sha256"]
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="GraphCheckpointBinding")
        _sha(data["checkpoint_id"], "GraphCheckpointBinding.checkpoint_id")
        _validate_id_scope(data, "GraphCheckpointBinding", "pilot_id", "run_id", "case_id", "request_id")
        _text(data["graph_revision"], "GraphCheckpointBinding.graph_revision")
        _text(data["contract_revision"], "GraphCheckpointBinding.contract_revision")
        _validate_identity(data["profile_identity"], "GraphCheckpointBinding.profile_identity")
        _integer(data["last_event_seq"], "GraphCheckpointBinding.last_event_seq", minimum=0)
        completed = _string_list(data["completed_node_ids"], "GraphCheckpointBinding.completed_node_ids", allow_empty=True)
        pending = _string_list(data["pending_node_ids"], "GraphCheckpointBinding.pending_node_ids", allow_empty=True)
        if any(value not in NODE_ORDER for value in completed + pending) or set(completed) & set(pending):
            raise Phase4LocalQwenContractError("checkpoint node binding is invalid")
        _validate_identity(data["state_identity"], "GraphCheckpointBinding.state_identity")
        _validate_identity(data["checkpoint_identity"], "GraphCheckpointBinding.checkpoint_identity")
        if data["resume_status"] != "not_requested":
            raise Phase4LocalQwenContractError("P4-03 checkpoint resume is not implemented")
        expected_checkpoint = _identity({key: data[key] for key in data if key not in {"checkpoint_id", "checkpoint_identity"}}, revision=CHECKPOINT_SCHEMA_VERSION)
        if data["checkpoint_identity"] != expected_checkpoint or data["checkpoint_id"] != expected_checkpoint["sha256"]:
            raise Phase4LocalQwenContractError("checkpoint identity drifted")


class PilotOutcome(_CanonicalRecord):
    """Manager-owned claim boundary for the first terminal pilot outcome."""

    KEYS = (
        "schema_version", "outcome_id", "pilot_id", "case_id", "request_id", "status",
        "stop_reason", "node_local_statuses", "node_total_counts", "integrated_run_id",
        "integrated_outcome", "integrated_node_raw_contract_pass_count", "composition_status",
        "assembler_status", "downstream_execution", "historical_strict_result",
        "claim_boundary", "model_calls_performed", "model_action_occurred",
        "source_kind", "training", "remote", "h1_or_gold", "formal_quality",
    )
    SCHEMA_VERSION = OUTCOME_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        pilot: PilotBinding,
        status: str,
        stop_reason: str,
        node_local_statuses: Mapping[str, object],
        node_total_counts: Mapping[str, object],
        integrated_run_id: str | None,
        integrated_outcome: str,
        integrated_node_raw_contract_pass_count: int,
        composition_status: str,
        assembler_status: str,
        model_calls_performed: int,
        model_action_occurred: bool,
        source_kind: str,
    ) -> "PilotOutcome":
        case = pilot.case_binding
        root = {
            "schema_version": cls.SCHEMA_VERSION,
            "outcome_id": "pending",
            "pilot_id": pilot.pilot_id,
            "case_id": case["case_id"],
            "request_id": case["request_id"],
            "status": status,
            "stop_reason": stop_reason,
            "node_local_statuses": dict(node_local_statuses),
            "node_total_counts": dict(node_total_counts),
            "integrated_run_id": integrated_run_id,
            "integrated_outcome": integrated_outcome,
            "integrated_node_raw_contract_pass_count": integrated_node_raw_contract_pass_count,
            "composition_status": composition_status,
            "assembler_status": assembler_status,
            "downstream_execution": {
                "consistency": "not_executed_p4_03",
                "acceptance": "not_executed_p4_03",
                "a07": "not_executed_p4_03",
                "repair": "not_executed_p4_03",
                "g0": "not_executed_p4_03",
                "package": "not_executed_p4_03",
                "production_route": "not_executed_p4_03",
            },
            "historical_strict_result": "0/2_unchanged",
            "claim_boundary": "phase4_local_qwen_node_or_integrated_pilot_only",
            "model_calls_performed": model_calls_performed,
            "model_action_occurred": model_action_occurred,
            "source_kind": source_kind,
            "training": False,
            "remote": False,
            "h1_or_gold": False,
            "formal_quality": False,
        }
        root["outcome_id"] = _sha256(_canonical_bytes({key: value for key, value in root.items() if key != "outcome_id"}))
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="PilotOutcome")
        _sha(data["outcome_id"], "PilotOutcome.outcome_id")
        _validate_id_scope(data, "PilotOutcome", "pilot_id", "case_id", "request_id")
        statuses = {"preflight_ready", "node_local_running", "node_local_pass", "stopped_after_first_failure", "stopped_node_exhausted", "integrated_success", "integrated_failed_closed"}
        if data["status"] not in statuses:
            raise Phase4LocalQwenContractError("PilotOutcome.status is invalid")
        _text(data["stop_reason"], "PilotOutcome.stop_reason", pattern=_ID_RE)
        node_statuses = _exact(data["node_local_statuses"], NODE_ORDER, "PilotOutcome.node_local_statuses")
        for value in node_statuses.values():
            if value not in {"not_started", "passed", "failed_once", "failed_twice"}:
                raise Phase4LocalQwenContractError("PilotOutcome node status is invalid")
        totals = _exact(data["node_total_counts"], NODE_ORDER, "PilotOutcome.node_total_counts")
        for node_id in NODE_ORDER:
            _integer(totals[node_id], f"PilotOutcome.node_total_counts.{node_id}", minimum=0, maximum=3)
        if data["integrated_run_id"] is not None:
            _text(data["integrated_run_id"], "PilotOutcome.integrated_run_id", pattern=_ID_RE)
        _text(data["integrated_outcome"], "PilotOutcome.integrated_outcome", pattern=_ID_RE)
        _integer(
            data["integrated_node_raw_contract_pass_count"],
            "PilotOutcome.integrated_node_raw_contract_pass_count",
            minimum=0,
            maximum=4,
        )
        for key in ("composition_status", "assembler_status"):
            _text(data[key], f"PilotOutcome.{key}", pattern=_ID_RE)
        downstream = _exact(data["downstream_execution"], ("consistency", "acceptance", "a07", "repair", "g0", "package", "production_route"), "PilotOutcome.downstream_execution")
        if any(value != "not_executed_p4_03" for value in downstream.values()):
            raise Phase4LocalQwenContractError("P4-03 downstream execution drifted")
        if data["historical_strict_result"] != "0/2_unchanged" or data["claim_boundary"] != "phase4_local_qwen_node_or_integrated_pilot_only":
            raise Phase4LocalQwenContractError("PilotOutcome claim boundary drifted")
        _integer(data["model_calls_performed"], "PilotOutcome.model_calls_performed", minimum=0, maximum=12)
        _bool(data["model_action_occurred"], "PilotOutcome.model_action_occurred")
        if data["source_kind"] not in SOURCE_KINDS:
            raise Phase4LocalQwenContractError("PilotOutcome source kind is invalid")
        if data["source_kind"] == "scripted_test_fixture" and (
            data["model_calls_performed"] != 0 or data["model_action_occurred"] is not False
        ):
            raise Phase4LocalQwenContractError("fixture outcome cannot claim model action")
        for key in ("training", "remote", "h1_or_gold", "formal_quality"):
            if _bool(data[key], f"PilotOutcome.{key}") is not False:
                raise Phase4LocalQwenContractError(f"PilotOutcome.{key} must be false")
        expected_id = _sha256(_canonical_bytes({key: data[key] for key in data if key != "outcome_id"}))
        if data["outcome_id"] != expected_id:
            raise Phase4LocalQwenContractError("PilotOutcome identity drifted")


class PreCallManifest(_CanonicalRecord):
    """Canonical manifest written by prepare before any backend is available."""

    KEYS = (
        "schema_version", "manifest_id", "pilot_binding", "projection_policies",
        "profile", "authorization_state", "action_state", "model_root_identity",
        "model_inventory_identity", "inventory_file_count", "weight_bytes_hashed",
        "offline_environment",
        "result_root_marker", "model_loaded", "run_occurred", "status",
    )
    SCHEMA_VERSION = MANIFEST_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        pilot_binding: PilotBinding,
        policies: Sequence[NodeProjectionPolicy],
        profile: LocalQwenProfile,
        model_root_identity: Mapping[str, object],
        model_inventory_identity: Mapping[str, object],
        inventory_file_count: int,
        offline_environment: Mapping[str, object],
    ) -> "PreCallManifest":
        if [policy.node_id for policy in policies] != list(NODE_ORDER):
            raise Phase4LocalQwenContractError("manifest policy order must be F1-F4")
        root = {
            "schema_version": cls.SCHEMA_VERSION,
            "manifest_id": "pending",
            "pilot_binding": pilot_binding.to_dict(),
            "projection_policies": [policy.to_dict() for policy in policies],
            "profile": profile.to_dict(),
            "authorization_state": {
                "authorization_state_version": f"{P4_03_SCHEMA_PREFIX}.authorization_state.v1",
                "runtime_kind": "local_qwen_langgraph",
                "model_action_authorized": True,
                "graph_runtime_authorized": True,
                "dependency_installation_authorized": False,
                "training_authorized": False,
                "remote_action_authorized": False,
                "network_authorized": False,
                "telemetry_authorized": False,
                "tracing_authorized": False,
                "local_files_only_required": True,
            },
            "action_state": {
                "action_state_version": f"{P4_03_SCHEMA_PREFIX}.action_state.v1",
                "runtime_kind": "local_qwen_langgraph",
                "model_action": False,
                "graph_runtime_execution": False,
                "dependency_installation": False,
                "training": False,
                "remote_action": False,
                "network": False,
                "telemetry": False,
                "tracing": False,
                "local_files_only": True,
            },
            "model_root_identity": dict(model_root_identity),
            "model_inventory_identity": dict(model_inventory_identity),
            "inventory_file_count": inventory_file_count,
            "weight_bytes_hashed": True,
            "offline_environment": dict(offline_environment),
            "result_root_marker": pilot_binding.result_root_marker,
            "model_loaded": False,
            "run_occurred": False,
            "status": "candidate_pre_call_foundation",
        }
        root["manifest_id"] = _sha256(_canonical_bytes({key: value for key, value in root.items() if key != "manifest_id"}))
        return cls._from_payload(root)  # type: ignore[return-value]

    @classmethod
    def _validate_payload(cls, data: Mapping[str, object]) -> None:
        _common_record(data, schema=cls.SCHEMA_VERSION, name="PreCallManifest")
        _sha(data["manifest_id"], "PreCallManifest.manifest_id")
        binding = PilotBinding.from_dict(data["pilot_binding"])
        policies_raw = data["projection_policies"]
        if type(policies_raw) is not list or [item.get("node_id") for item in policies_raw if isinstance(item, Mapping)] != list(NODE_ORDER):
            raise Phase4LocalQwenContractError("PreCallManifest projection policy order is invalid")
        policies = [NodeProjectionPolicy.from_dict(item) for item in policies_raw]
        if binding.node_policy_identities != {policy.node_id: policy.sha256() for policy in policies}:
            raise Phase4LocalQwenContractError("PreCallManifest policy identity binding drifted")
        profile = LocalQwenProfile.from_dict(data["profile"])
        _authorization_flags(
            data["authorization_state"],
            "PreCallManifest.authorization_state",
        )
        _action_flags(
            data["action_state"],
            "PreCallManifest.action_state",
            model_action=False,
            graph_runtime_execution=False,
        )
        model_root_identity = _validate_identity(
            data["model_root_identity"], "PreCallManifest.model_root_identity"
        )
        model_inventory_identity = _validate_identity(
            data["model_inventory_identity"],
            "PreCallManifest.model_inventory_identity",
        )
        inventory_file_count = _integer(
            data["inventory_file_count"],
            "PreCallManifest.inventory_file_count",
            minimum=1,
        )
        if _bool(data["weight_bytes_hashed"], "PreCallManifest.weight_bytes_hashed") is not True:
            raise Phase4LocalQwenContractError("preflight must live-hash all model bytes")
        if (
            binding.profile_id != profile.profile_id
            or profile.model_root_identity != model_root_identity
            or profile.model_inventory_identity != model_inventory_identity
            or profile.model_file_count != inventory_file_count
        ):
            raise Phase4LocalQwenContractError("PreCallManifest profile/model binding drifted")
        if not isinstance(data["offline_environment"], Mapping):
            raise Phase4LocalQwenContractError("offline environment record is invalid")
        _canonical_bytes(data["offline_environment"])
        if data["result_root_marker"] != binding.result_root_marker or _bool(data["model_loaded"], "PreCallManifest.model_loaded") is not False or _bool(data["run_occurred"], "PreCallManifest.run_occurred") is not False:
            raise Phase4LocalQwenContractError("PreCallManifest no-run state drifted")
        if data["status"] != "candidate_pre_call_foundation":
            raise Phase4LocalQwenContractError("PreCallManifest status drifted")
        expected_id = _sha256(_canonical_bytes({key: data[key] for key in data if key != "manifest_id"}))
        if data["manifest_id"] != expected_id:
            raise Phase4LocalQwenContractError("PreCallManifest identity drifted")


def _validate_json_bytes(raw: bytes, name: str, *, max_bytes: int | None = None) -> dict[str, object]:
    if max_bytes is not None and len(raw) > max_bytes:
        raise Phase4LocalQwenContractError(f"{name} exceeds its frozen byte cap")
    return _strict_json(raw)


def _node_prompt_instructions(
    node_id: str, *, prior_failure_code: str | None = None
) -> list[str]:
    instructions = [
        "Read the separately supplied ACTUAL_NODE_INPUT_JSON bytes.",
        "Return only one UTF-8 JSON object with the exact keys below.",
        "Do not emit markdown, fences, explanations, hidden reasoning, extra keys, null placeholders, or a second object.",
        "Serialize the answer as minified compact JSON with no unnecessary spaces, indentation, or line breaks.",
        "Keep every free-text field concise and non-redundant while preserving its required meaning.",
        "Generate the smallest complete entity collection that satisfies the current input and contract; do not omit entities required by references, ordering, or coverage.",
        "Within the fixed output budget, prioritize syntactic completeness: close every string, array, and object before ending the response.",
        "Preserve canonical input order and supplied reference identities; never invent a stable ID.",
        *_NODE_PROMPT_GUIDANCE[node_id],
    ]
    if prior_failure_code is not None:
        instructions.extend(
            (
                f"The immutable prior attempt failed with failure_code={prior_failure_code}; correct that failure against the current node-specific contract.",
                "No prior raw output is included; do not reconstruct, quote, or depend on prior raw content.",
                "Correct only the recorded contract failure; do not broaden semantics.",
            )
        )
        if prior_failure_code == "node_contract_invalid":
            instructions.append(
                "For failure_code=node_contract_invalid, rebuild one complete minified JSON object from the current ACTUAL_NODE_INPUT_JSON, close every string, array, and object, and obey every literal entity type, local-ID ownership, reference-domain, exact-key, and order rule above."
            )
    return instructions


def build_prompt_v1(*, node_id: str, input_bytes: bytes, policy: NodeProjectionPolicy, profile: LocalQwenProfile) -> bytes:
    """Build a deterministic canonical JSON prompt envelope for one node."""

    policy.validate()
    profile.validate()
    if node_id != policy.node_id:
        raise Phase4LocalQwenContractError("prompt node/policy binding drifted")
    caps = _node_policy_caps(policy.field_caps, "policy.field_caps")
    if len(input_bytes) > caps["input_bytes"]:
        raise Phase4LocalQwenContractError("prompt input exceeds node cap")
    payload = {
        "prompt_schema_version": f"{P4_03_SCHEMA_PREFIX}.prompt.v1",
        "node_id": node_id,
        "template_revision": policy.prompt_template_revision,
        "output_format": "exact_json_object",
        "input_sha256": _sha256(input_bytes),
        "input_byte_length": len(input_bytes),
        "instructions": _node_prompt_instructions(node_id),
        "output_contract": _NODE_OUTPUT_CONTRACTS[node_id],
        "model_id": profile.model_id,
        "model_revision": profile.model_revision,
    }
    result = _canonical_bytes(payload)
    if len(result) > caps["prompt_bytes"]:
        raise Phase4LocalQwenContractError("prompt exceeds node cap")
    return result


def build_prompt_v2(*, node_id: str, input_bytes: bytes, policy: NodeProjectionPolicy, profile: LocalQwenProfile, prior_failure: AttemptResult, change_reason: str) -> bytes:
    """Build a new prompt envelope bound to one immutable prior failure."""

    prior_failure.validate()
    if prior_failure.node_id != node_id or prior_failure.failure_code is None or prior_failure.call_kind != "node_local":
        raise Phase4LocalQwenContractError("prompt v2 prior failure is not a same-node immutable failure")
    if change_reason not in CHANGE_REASONS:
        raise Phase4LocalQwenContractError("prompt v2 change reason is not mechanical")
    policy.validate()
    profile.validate()
    if node_id != policy.node_id:
        raise Phase4LocalQwenContractError("prompt node/policy binding drifted")
    caps = _node_policy_caps(policy.field_caps, "policy.field_caps")
    if len(input_bytes) > caps["input_bytes"]:
        raise Phase4LocalQwenContractError("prompt input exceeds node cap")
    payload = {
        "prompt_schema_version": f"{P4_03_SCHEMA_PREFIX}.prompt.v2",
        "node_id": node_id,
        "template_revision": "p4-03-prompt-v2",
        "output_format": "exact_json_object",
        "input_sha256": _sha256(input_bytes),
        "input_byte_length": len(input_bytes),
        "instructions": _node_prompt_instructions(
            node_id, prior_failure_code=prior_failure.failure_code
        ),
        "output_contract": _NODE_OUTPUT_CONTRACTS[node_id],
        "model_id": profile.model_id,
        "model_revision": profile.model_revision,
        "prior_failure_identity": _identity(prior_failure.to_dict(), revision=ATTEMPT_RESULT_SCHEMA_VERSION),
        "change_reason": change_reason,
    }
    result = _canonical_bytes(payload)
    if len(result) > caps["prompt_bytes"]:
        raise Phase4LocalQwenContractError("prompt v2 exceeds node cap")
    return result


def build_config_bytes(*, profile: LocalQwenProfile, policy: NodeProjectionPolicy) -> bytes:
    """Build the frozen decode/config envelope without loading a model."""

    profile.validate()
    policy.validate()
    payload = {
        "config_schema_version": f"{P4_03_SCHEMA_PREFIX}.config.v1",
        "config_revision": policy.config_revision,
        "profile_id": profile.profile_id,
        "profile_name": profile.profile_name,
        "dtype": profile.dtype,
        "quantization": profile.quantization,
        "compute_dtype": profile.compute_dtype,
        "device_map": profile.device_map,
        "cpu_offload": profile.cpu_offload,
        "local_files_only": profile.local_files_only,
        "offline": profile.offline,
        "decode": profile.decode,
        "max_input_tokens": profile.max_input_tokens,
        "max_new_tokens": profile.max_new_tokens,
        "timeout_seconds": profile.timeout_seconds,
        "seed": profile.seed,
    }
    result = _canonical_bytes(payload)
    caps = _node_policy_caps(policy.field_caps, "policy.field_caps")
    if len(result) > caps["config_bytes"]:
        raise Phase4LocalQwenContractError("config exceeds node cap")
    return result


def _build_request_bytes(
    *,
    pilot: PilotBinding,
    node_id: str,
    run_id: str,
    attempt_index: int,
    call_kind: str,
    source_kind: str,
) -> bytes:
    if source_kind not in SOURCE_KINDS:
        raise Phase4LocalQwenContractError("request source kind is invalid")
    payload = {
        "request_schema_version": f"{P4_03_SCHEMA_PREFIX}.request.v1",
        "pilot_id": pilot.pilot_id,
        "case_id": pilot.case_binding["case_id"],
        "request_id": pilot.case_binding["request_id"],
        "run_id": run_id,
        "node_id": node_id,
        "attempt_index": attempt_index,
        "call_kind": call_kind,
        "b_aux_disposition": "absent/not_requested",
        "source_kind": source_kind,
    }
    return _canonical_bytes(payload)


def derive_node_input(
    *,
    node_id: str,
    b_input_bytes: bytes,
    upstream_outputs: Mapping[str, bytes],
    authority_state: Mapping[str, object],
    policy: NodeProjectionPolicy,
) -> bytes:
    """Derive actual input only from canonical B and same-run validated outputs."""

    policy.validate()
    if node_id != policy.node_id:
        raise Phase4LocalQwenContractError("input node/policy binding drifted")
    required = list(policy.upstream_required_node_ids)
    if list(upstream_outputs) != required:
        raise Phase4LocalQwenContractError("upstream input order or completeness drifted")
    if node_id == "F1" and upstream_outputs:
        raise Phase4LocalQwenContractError("F1 cannot have upstream outputs")
    rows = []
    for upstream_node in required:
        raw = upstream_outputs[upstream_node]
        if type(raw) is not bytes or not raw:
            raise Phase4LocalQwenContractError("upstream output bytes are not valid")
        _strict_json(raw, require_canonical=False)
        rows.append({"node_id": upstream_node, "raw_sha256": _sha256(raw), "raw_b64": _b64(raw, "upstream.raw")})
    if not isinstance(authority_state, Mapping):
        raise Phase4LocalQwenContractError("deterministic authority state is missing")
    from req2web_orchestration.phase4_graph import (
        phase4_project_node_input_authority,
    )

    authority_projection = phase4_project_node_input_authority(
        authority_state,
        node_id,
    )
    payload = {
        "input_schema_version": f"{P4_03_SCHEMA_PREFIX}.node_input.v1",
        "node_id": node_id,
        "canonical_b_input_sha256": _sha256(b_input_bytes),
        "canonical_b_input_b64": _b64(b_input_bytes, "canonical_b_input"),
        "upstream": rows,
        "authority_bindings": authority_projection,
        "allowed_categories": list(policy.allowed_categories),
        "prohibited_categories": list(policy.prohibited_categories),
    }
    caps = _node_policy_caps(policy.field_caps, "policy.field_caps")
    if _count_reference_objects(payload) > caps["ref_count"]:
        raise Phase4LocalQwenContractError("derived node input exceeds reference cap")
    result = _canonical_bytes(payload)
    if len(result) > caps["input_bytes"]:
        raise Phase4LocalQwenContractError("derived node input exceeds cap")
    return result


def _validate_node_output_json(raw: bytes, *, node_id: str, authority_state: Mapping[str, object], policy: NodeProjectionPolicy) -> dict[str, object]:
    caps = _node_policy_caps(policy.field_caps, "policy.field_caps")
    output = _strict_json(raw, require_canonical=False)
    if len(raw) > caps["output_bytes"]:
        raise Phase4LocalQwenContractError(f"{node_id}.raw exceeds its frozen byte cap")
    if _count_reference_objects(output) > caps["ref_count"]:
        raise Phase4LocalQwenContractError(f"{node_id}.raw exceeds its reference cap")
    from req2web_orchestration.phase4_graph import phase4_validate_node_output

    return phase4_validate_node_output(node_id, output, authority_state)


def _validate_same_scope_upstream_refs(refs: Sequence[Mapping[str, object]], *, pilot_id: str, run_id: str, case_id: str, request_id: str, expected_nodes: Sequence[str]) -> None:
    if len(refs) != len(expected_nodes):
        raise Phase4LocalQwenContractError("upstream reference count is invalid")
    seen: list[str] = []
    for ref, expected_node in zip(refs, expected_nodes, strict=True):
        data = _ref(ref, "upstream_ref", "node_output")
        ref_id = data["ref_id"]
        # The ref ID is a compact identity, while scope is carried in the
        # revision string by the runner.  This is intentionally checked again
        # before every pre-call record rather than trusted from a prior object.
        if data["ref_revision"] != f"{P4_03_SCHEMA_PREFIX}.node_output.{pilot_id}.{run_id}.{case_id}.{request_id}.{expected_node}":
            raise Phase4LocalQwenContractError("cross-node/run upstream reference rejected")
        seen.append(expected_node)
    if seen != list(expected_nodes):
        raise Phase4LocalQwenContractError("upstream reference order drifted")


def _read_json_file(path: Path, *, require_canonical: bool = True) -> dict[str, object]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Phase4LocalQwenContractError(f"cannot read evidence file {path.name}") from exc
    return _strict_json(raw, require_canonical=require_canonical)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(8 * 1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise Phase4LocalQwenContractError(
            f"cannot hash live model file {path.name}"
        ) from exc
    return digest.hexdigest()


def validate_model_inventory_metadata(
    *, model_root: Path, integrity_evidence: Path
) -> dict[str, object]:
    """Live-hash the complete existing model inventory against prior evidence."""

    if not isinstance(model_root, Path) or not isinstance(integrity_evidence, Path):
        raise Phase4LocalQwenContractError("model root/evidence must be Path objects")
    if not model_root.is_dir() or not integrity_evidence.is_file():
        raise Phase4LocalQwenContractError("model root or integrity evidence is missing")
    data = _exact(
        _read_json_file(integrity_evidence, require_canonical=False),
        (
            "repo_id", "requested_revision", "resolved_sha", "root",
            "expected_file_count", "actual_repo_file_count", "missing", "extra",
            "total_actual_bytes", "all_sizes_match", "all_identities_match",
            "elapsed_seconds", "files",
        ),
        "local_integrity_evidence",
    )
    if (
        data["repo_id"] != QWEN_MODEL_ID
        or data["requested_revision"] != QWEN_MODEL_REVISION
        or data["resolved_sha"] != QWEN_MODEL_REVISION
    ):
        raise Phase4LocalQwenContractError("model integrity identity drifted")
    evidence_root = Path(_text(data["root"], "local_integrity_evidence.root"))
    if evidence_root.resolve() != model_root.resolve():
        raise Phase4LocalQwenContractError("integrity evidence model root drifted")
    if data["missing"] != [] or data["extra"] != []:
        raise Phase4LocalQwenContractError("integrity evidence has missing/extra files")
    if (
        _bool(data["all_sizes_match"], "local_integrity_evidence.all_sizes_match")
        is not True
        or _bool(
            data["all_identities_match"],
            "local_integrity_evidence.all_identities_match",
        )
        is not True
    ):
        raise Phase4LocalQwenContractError("integrity evidence is not complete")
    files = data["files"]
    if type(files) is not list or not files:
        raise Phase4LocalQwenContractError("integrity evidence files are empty")
    expected_count = _integer(
        data["expected_file_count"],
        "local_integrity_evidence.expected_file_count",
        minimum=1,
    )
    if (
        _integer(
            data["actual_repo_file_count"],
            "local_integrity_evidence.actual_repo_file_count",
            minimum=1,
        )
        != expected_count
        or len(files) != expected_count
    ):
        raise Phase4LocalQwenContractError("integrity evidence file count drifted")
    expected_rows: list[dict[str, object]] = []
    evidence_file_keys = (
        "rfilename", "exists", "expected_size", "expected_lfs_sha256",
        "expected_blob_id", "actual_size", "actual_sha256",
        "actual_git_blob_sha1", "size_match", "identity_match",
    )
    for item in files:
        row = _exact(item, evidence_file_keys, "local_integrity_evidence.file")
        rel = _relative_path(row["rfilename"], "integrity.file.rfilename")
        actual_size = _integer(row["actual_size"], "integrity.file.actual_size")
        if (
            _bool(row["exists"], "integrity.file.exists") is not True
            or _bool(row["size_match"], "integrity.file.size_match") is not True
            or _bool(row["identity_match"], "integrity.file.identity_match")
            is not True
            or _integer(row["expected_size"], "integrity.file.expected_size")
            != actual_size
            or type(row["actual_sha256"]) is not str
            or _HEX_RE.fullmatch(row["actual_sha256"]) is None
        ):
            raise Phase4LocalQwenContractError("integrity evidence file row drifted")
        expected_rows.append(
            {
                "relative_path": rel,
                "byte_length": actual_size,
                "sha256": row["actual_sha256"],
            }
        )
    expected_rows.sort(key=lambda row: str(row["relative_path"]))
    if len({row["relative_path"] for row in expected_rows}) != expected_count:
        raise Phase4LocalQwenContractError("integrity evidence paths are not unique")

    live_paths: list[Path] = []
    local_cache_paths: list[str] = []
    for path in model_root.rglob("*"):
        if path.is_symlink():
            raise Phase4LocalQwenContractError("model inventory forbids symlinks")
        if path.is_file():
            relative_path = path.relative_to(model_root).as_posix()
            if relative_path.startswith(_HF_LOCAL_CACHE_PREFIX):
                local_cache_paths.append(relative_path)
            else:
                live_paths.append(path)
    live_paths.sort(key=lambda path: path.relative_to(model_root).as_posix())
    if [path.relative_to(model_root).as_posix() for path in live_paths] != [
        str(row["relative_path"]) for row in expected_rows
    ]:
        raise Phase4LocalQwenContractError("live model paths do not match evidence")
    live_rows: list[dict[str, object]] = []
    for path, expected in zip(live_paths, expected_rows, strict=True):
        size = path.stat().st_size
        digest = _file_sha256(path)
        if size != expected["byte_length"] or digest != expected["sha256"]:
            raise Phase4LocalQwenContractError("live model content hash drifted")
        live_rows.append(
            {
                "relative_path": expected["relative_path"],
                "byte_length": size,
                "sha256": digest,
            }
        )
    total = sum(int(row["byte_length"]) for row in live_rows)
    if _integer(data["total_actual_bytes"], "integrity.total_actual_bytes") != total:
        raise Phase4LocalQwenContractError("integrity total byte count drifted")
    return {
        "schema_version": f"{P4_03_SCHEMA_PREFIX}.live-model-inventory.v1",
        "model_id": QWEN_MODEL_ID,
        "model_revision": QWEN_MODEL_REVISION,
        "file_count": expected_count,
        "total_byte_length": total,
        "files": live_rows,
        "excluded_local_cache_file_count": len(local_cache_paths),
        "excluded_local_cache_paths_identity": _identity(
            sorted(local_cache_paths),
            revision=f"{P4_03_SCHEMA_PREFIX}.local-cache-paths.v1",
            identity_kind="canonical_row_list",
        ),
        "weight_bytes_hashed": True,
        "inventory_identity": _identity(
            live_rows,
            revision=f"{P4_03_SCHEMA_PREFIX}.model-inventory.rows.v1",
            identity_kind="canonical_row_list",
        ),
        "evidence_identity": _identity(
            data,
            revision=f"{P4_03_SCHEMA_PREFIX}.local-integrity-evidence.v1",
        ),
    }


def collect_runtime_facts(*, package_versions: Mapping[str, str] | None = None, gpu_facts: Mapping[str, object] | None = None) -> dict[str, object]:
    """Collect version/device facts without importing a model backend."""

    versions = dict(package_versions or {})
    if not versions:
        for distribution in ("transformers", "torch", "bitsandbytes", "accelerate"):
            try:
                versions[distribution] = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                versions[distribution] = "missing"
    facts = {
        "python_version": platform.python_version(),
        "transformers_version": versions.get("transformers", "missing"),
        "torch_version": versions.get("torch", "missing"),
        "bitsandbytes_version": versions.get("bitsandbytes", "missing"),
        "accelerate_version": versions.get("accelerate", "missing"),
        "gpu_probe": dict(gpu_facts or {"executed": False, "reason": "injected_or_deferred"}),
    }
    return facts


def probe_local_gpu_facts() -> dict[str, object]:
    """Probe the exact local CUDA device without loading model weights."""

    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Phase4LocalQwenContractError("local GPU probe failed closed") from exc
    rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(rows) != 1:
        raise Phase4LocalQwenContractError("exactly one visible local GPU is required")
    parts = [part.strip() for part in rows[0].split(",")]
    if len(parts) != 6:
        raise Phase4LocalQwenContractError("local GPU probe output is invalid")
    try:
        index = int(parts[0])
        total_bytes = int(parts[3]) * 1024 * 1024
        free_bytes = int(parts[4]) * 1024 * 1024
    except ValueError as exc:
        raise Phase4LocalQwenContractError("local GPU numeric facts are invalid") from exc
    try:
        torch = importlib.import_module("torch")
    except Exception as exc:
        raise Phase4LocalQwenContractError("torch runtime probe failed closed") from exc
    if (
        not torch.cuda.is_available()
        or torch.cuda.device_count() != 1
        or torch.cuda.current_device() != 0
        or torch.cuda.get_device_name(0) != parts[2]
    ):
        raise Phase4LocalQwenContractError("torch/nvidia-smi GPU binding drifted")
    cuda_version = str(torch.version.cuda)
    if index != 0 or parts[2] != "NVIDIA GeForce RTX 4060 Laptop GPU":
        raise Phase4LocalQwenContractError("local GPU profile drifted")
    return {
        "executed": True,
        "device_index": index,
        "device_uuid": parts[1],
        "device_name": parts[2],
        "total_vram_bytes": total_bytes,
        "free_vram_bytes": free_bytes,
        "driver_version": parts[5],
        "cuda_version": cuda_version,
    }


def verify_offline_environment(*, environment: Mapping[str, str] | None = None) -> dict[str, object]:
    """Verify policy flags without making a network request."""

    env = dict(environment or os.environ)
    values = {
        "HF_HUB_OFFLINE": env.get("HF_HUB_OFFLINE", "1"),
        "TRANSFORMERS_OFFLINE": env.get("TRANSFORMERS_OFFLINE", "1"),
        "LANGSMITH_TRACING": env.get("LANGSMITH_TRACING", "0"),
        "LANGCHAIN_TRACING_V2": env.get("LANGCHAIN_TRACING_V2", "0"),
    }
    offline = values["HF_HUB_OFFLINE"] == "1" and values["TRANSFORMERS_OFFLINE"] == "1"
    tracing_disabled = values["LANGSMITH_TRACING"] in {"0", "false", "False", ""} and values["LANGCHAIN_TRACING_V2"] in {"0", "false", "False", ""}
    if not offline or not tracing_disabled:
        raise Phase4LocalQwenContractError("offline/telemetry environment is not fail-closed")
    return {
        "schema_version": f"{P4_03_SCHEMA_PREFIX}.offline-environment.v1",
        "local_files_only": True,
        "network": False,
        "telemetry": False,
        "tracing": False,
        "environment_snapshot": values,
    }


def _new_result_root(root: Path, marker: str) -> None:
    if not isinstance(root, Path) or not root.is_absolute():
        raise Phase4LocalQwenContractError("result root must be an absolute caller-provided Path")
    if root.exists():
        if not root.is_dir() or any(root.iterdir()):
            raise Phase4LocalQwenContractError("result root must be new and empty")
    else:
        root.mkdir(parents=True, exist_ok=False)
    marker_path = root / RESULT_ROOT_MARKER_NAME
    with marker_path.open("xb") as handle:
        handle.write((marker + "\n").encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())


def _write_atomic(root: Path, relative_path: str, raw: bytes) -> None:
    relative = _relative_path(relative_path, "result.relative_path")
    destination = root / Path(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent, prefix=".p4-03-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        # There is deliberately no public cleanup operation.  This local
        # guard only prevents a failed atomic write from leaking a temp file.
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _write_once(root: Path, relative_path: str, raw: bytes) -> None:
    """Atomically publish immutable bytes and refuse any overwrite."""

    relative = _relative_path(relative_path, "result.relative_path")
    destination = root / Path(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise Phase4LocalQwenContractError(
            f"immutable result already exists: {relative}"
        )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=".p4-03-once-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise Phase4LocalQwenContractError(
                f"immutable result already exists: {relative}"
            ) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def acquire_pilot_execution_lease(
    *,
    result_root: Path,
    pilot: PilotBinding,
    manifest: PreCallManifest,
) -> PilotExecutionLease:
    """Acquire the one allowed execution lease and refuse every repeat run."""

    if not isinstance(result_root, Path) or not result_root.is_absolute():
        raise Phase4LocalQwenContractError("execution result root is invalid")
    for relative in (
        "execution_lease.json",
        "runtime_start_claim.json",
        "supervisor_receipt.json",
        "pilot_outcome.json",
        "local_qwen_load_receipt.json",
        "ledger.json",
    ):
        if (result_root / relative).exists():
            raise Phase4LocalQwenContractError(
                "prepared pilot already has execution evidence"
            )
    if (result_root / "runs").exists():
        raise Phase4LocalQwenContractError("prepared pilot already has run evidence")
    lease = PilotExecutionLease.create(
        pilot=pilot,
        manifest=manifest,
        parent_pid=os.getpid(),
    )
    _write_once(result_root, "execution_lease.json", lease.canonical_bytes())
    return lease


def _acquire_runtime_start_claim(
    *, result_root: Path, lease: PilotExecutionLease
) -> PilotRuntimeStartClaim:
    lease.validate()
    if (result_root / "execution_lease.json").read_bytes() != lease.canonical_bytes():
        raise Phase4LocalQwenContractError("runtime claim lease binding drifted")
    claim = PilotRuntimeStartClaim.create(lease=lease, parent_pid=os.getpid())
    _write_once(result_root, "runtime_start_claim.json", claim.canonical_bytes())
    return claim


def persist_pilot_outcome(*, result_root: Path, outcome: PilotOutcome) -> None:
    outcome.validate()
    _write_once(result_root, "pilot_outcome.json", outcome.canonical_bytes())


def persist_supervisor_receipt(
    *, result_root: Path, receipt: PilotSupervisorReceipt
) -> None:
    receipt.validate()
    _write_once(result_root, "supervisor_receipt.json", receipt.canonical_bytes())


def build_synthetic_case_binding(
    b_input: Mapping[str, object],
) -> dict[str, object]:
    """Bind the existing deterministic Phase 4 synthetic case without new data."""

    from req2web_orchestration.phase4_graph import validate_b_input

    checked = validate_b_input(dict(b_input))
    return {
        "case_id": checked["case_id"],
        "request_id": checked["request_id"],
        "b_identity": _identity(checked, revision="canonical_b.p4.v1"),
        "requirement_sha256": _identity(
            {"requirement": checked["requirement"]},
            revision=f"{P4_03_SCHEMA_PREFIX}.requirement.v1",
        )["sha256"],
        "use_case_sha256": _identity(
            checked["use_cases"],
            revision=f"{P4_03_SCHEMA_PREFIX}.use-cases.v1",
        )["sha256"],
        "constraint_sha256": _identity(
            checked["constraints"],
            revision=f"{P4_03_SCHEMA_PREFIX}.constraints.v1",
        )["sha256"],
    }


def prepare_local_qwen_pilot(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    case_binding: Mapping[str, object],
    policies: Sequence[NodeProjectionPolicy],
    pilot_id: str = "p4-03-local-qwen-9b",
    runtime_versions: Mapping[str, str] | None = None,
    gpu_facts: Mapping[str, object] | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[PilotBinding, tuple[NodeProjectionPolicy, ...], LocalQwenProfile, PreCallManifest]:
    """Prepare and persist the no-run manifest; never imports or loads a model."""

    if [policy.node_id for policy in policies] != list(NODE_ORDER):
        raise Phase4LocalQwenContractError("prepare policies must be ordered F1-F4")
    checked_policies = tuple(NodeProjectionPolicy.from_dict(policy.to_dict()) for policy in policies)
    if gpu_facts is None or gpu_facts.get("executed") is not True:
        raise Phase4LocalQwenContractError("prepare requires a live local GPU fact record")
    for key in ("device_index", "total_vram_bytes", "free_vram_bytes"):
        _integer(gpu_facts.get(key), f"gpu_facts.{key}", minimum=0)
    for key in ("device_uuid", "device_name", "driver_version", "cuda_version"):
        _text(gpu_facts.get(key), f"gpu_facts.{key}")
    inventory = validate_model_inventory_metadata(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
    )
    facts = collect_runtime_facts(package_versions=runtime_versions, gpu_facts=gpu_facts)
    expected_versions = {
        "transformers_version": TRANSFORMERS_VERSION,
        "torch_version": TORCH_VERSION,
        "bitsandbytes_version": BITSANDBYTES_VERSION,
        "accelerate_version": ACCELERATE_VERSION,
    }
    for key, expected in expected_versions.items():
        if facts[key] != expected:
            raise Phase4LocalQwenContractError(f"runtime version drifted: {key}")
    offline = verify_offline_environment(environment=environment)
    marker = f"p4-03-result-root-{uuid.uuid4().hex[:20]}"
    _new_result_root(result_root, marker)
    model_inventory_identity = dict(inventory["inventory_identity"])
    model_root_identity = _identity(
        {
            "model_id": QWEN_MODEL_ID,
            "model_revision": QWEN_MODEL_REVISION,
            "resolved_model_root": str(model_root.resolve()),
            "inventory": model_inventory_identity,
        },
        revision=f"{P4_03_SCHEMA_PREFIX}.model-root.v1",
    )
    profile = LocalQwenProfile.create(
        model_root_identity=model_root_identity,
        model_inventory_identity=model_inventory_identity,
        model_file_count=inventory["file_count"],
        python_version=facts["python_version"],
        device_name=str(
            gpu_facts.get(
                "device_name", "NVIDIA GeForce RTX 4060 Laptop GPU"
            )
        ),
        device_uuid=str(gpu_facts.get("device_uuid", "")),
        total_vram_bytes=gpu_facts["total_vram_bytes"],
        free_vram_bytes_at_preflight=gpu_facts["free_vram_bytes"],
        driver_version=str(gpu_facts.get("driver_version", "")),
        cuda_version=str(gpu_facts.get("cuda_version", "")),
    )
    if gpu_facts.get("device_index") != 0:
        raise Phase4LocalQwenContractError("GPU profile drifted from GPU0")
    binding = PilotBinding.create(
        pilot_id=pilot_id,
        policy_id="pending",
        case_binding=case_binding,
        node_policy_identities={policy.node_id: policy.sha256() for policy in checked_policies},
        profile_id=profile.profile_id,
        result_root_marker=marker,
    )
    manifest = PreCallManifest.create(
        pilot_binding=binding,
        policies=checked_policies,
        profile=profile,
        model_root_identity=model_root_identity,
        model_inventory_identity=model_inventory_identity,
        inventory_file_count=inventory["file_count"],
        offline_environment=offline,
    )
    _write_once(result_root, "pre_call_manifest.json", manifest.canonical_bytes())
    _write_once(result_root, "inventory_evidence_summary.json", _canonical_bytes(inventory))
    return binding, checked_policies, profile, manifest


def load_prepared_local_qwen_pilot(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    b_input: Mapping[str, object],
    environment: Mapping[str, str] | None = None,
) -> tuple[PilotBinding, tuple[NodeProjectionPolicy, ...], LocalQwenProfile, PreCallManifest]:
    """Revalidate the canonical prepare artifact and live model before load."""

    if not result_root.is_absolute() or not result_root.is_dir():
        raise Phase4LocalQwenContractError("prepared result root is invalid")
    manifest = PreCallManifest.from_bytes(
        (result_root / "pre_call_manifest.json").read_bytes()
    )
    binding = PilotBinding.from_dict(manifest.pilot_binding)
    policies = tuple(
        NodeProjectionPolicy.from_dict(item)
        for item in manifest.projection_policies
    )
    profile = LocalQwenProfile.from_dict(manifest.profile)
    marker = result_root / RESULT_ROOT_MARKER_NAME
    if (
        not marker.is_file()
        or marker.read_text(encoding="ascii").strip() != binding.result_root_marker
    ):
        raise Phase4LocalQwenContractError("prepared result-root marker drifted")
    if build_synthetic_case_binding(b_input) != binding.case_binding:
        raise Phase4LocalQwenContractError("prepared synthetic case binding drifted")
    inventory = validate_model_inventory_metadata(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
    )
    inventory_identity = dict(inventory["inventory_identity"])
    root_identity = _identity(
        {
            "model_id": QWEN_MODEL_ID,
            "model_revision": QWEN_MODEL_REVISION,
            "resolved_model_root": str(model_root.resolve()),
            "inventory": inventory_identity,
        },
        revision=f"{P4_03_SCHEMA_PREFIX}.model-root.v1",
    )
    if (
        profile.model_root_identity != root_identity
        or profile.model_inventory_identity != inventory_identity
        or profile.model_file_count != inventory["file_count"]
        or manifest.model_root_identity != root_identity
        or manifest.model_inventory_identity != inventory_identity
        or manifest.inventory_file_count != inventory["file_count"]
    ):
        raise Phase4LocalQwenContractError("prepared model identity drifted")
    verify_offline_environment(environment=environment)
    return binding, policies, profile, manifest


def persist_local_qwen_load_receipt(
    *, result_root: Path, receipt: LocalQwenLoadReceipt
) -> None:
    receipt.validate()
    _write_once(
        result_root,
        "local_qwen_load_receipt.json",
        receipt.canonical_bytes(),
    )


def persist_worker_stderr_artifact(
    *, result_root: Path, stderr_bytes: bytes
) -> WorkerStderrArtifact:
    """Persist the exact worker stderr once and return its file-bound identity."""

    artifact = WorkerStderrArtifact.create(stderr_bytes=stderr_bytes)
    _write_once(result_root, "worker_stderr.json", artifact.canonical_bytes())
    return artifact


class QwenBackend(Protocol):
    """Explicit backend seam; tests provide a fake, real code stays lazy."""

    def generate(self, *, node_id: str, input_bytes: bytes, prompt_bytes: bytes, config_bytes: bytes, request_bytes: bytes) -> bytes:
        ...


class ScriptedFixtureBackend:
    """Deterministic test-only backend that cannot claim a real source."""

    def __init__(
        self,
        *,
        outputs: Sequence[bytes],
        result_root: Path | None,
        capability: object,
    ) -> None:
        if capability is not _FIXTURE_BACKEND_CAPABILITY:
            raise Phase4LocalQwenContractError("fixture backend capability is invalid")
        if any(type(raw) is not bytes for raw in outputs):
            raise Phase4LocalQwenContractError("fixture outputs must be bytes")
        self.outputs = list(outputs)
        self.calls: list[str] = []
        self.inputs: list[bytes] = []
        self.prompts: list[bytes] = []
        self.result_root = result_root
        self.raw_seen_before_return = True
        object.__setattr__(self, "_fixture_capability", capability)

    def generate(
        self,
        *,
        node_id: str,
        input_bytes: bytes,
        prompt_bytes: bytes,
        config_bytes: bytes,
        request_bytes: bytes,
    ) -> bytes:
        del config_bytes, request_bytes
        self.calls.append(node_id)
        self.inputs.append(input_bytes)
        self.prompts.append(prompt_bytes)
        if self.result_root is not None and list(
            self.result_root.rglob(RAW_RESPONSE_NAME)
        ):
            self.raw_seen_before_return = False
        if not self.outputs:
            raise Phase4LocalQwenContractError("fixture output budget exhausted")
        return self.outputs.pop(0)


def create_scripted_fixture_backend(
    outputs: Sequence[bytes], *, result_root: Path | None = None
) -> ScriptedFixtureBackend:
    """Create the only backend accepted by the scripted fixture source."""

    return ScriptedFixtureBackend(
        outputs=outputs,
        result_root=result_root,
        capability=_FIXTURE_BACKEND_CAPABILITY,
    )


class _IntegratedGraphState(TypedDict):
    completed_node_ids: list[str]
    terminal: bool


class _LazyTransformersQwenBackend:
    """Lazy, explicit Transformers foundation; construction performs no load."""

    def __init__(
        self,
        *,
        model_root: Path,
        profile: LocalQwenProfile,
        runtime_capability: object | None = None,
    ) -> None:
        if not isinstance(model_root, Path):
            raise Phase4LocalQwenContractError("model_root must be a Path")
        profile.validate()
        self._model_root = model_root
        self._profile = profile
        self._processor: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._loaded_facts: dict[str, object] | None = None
        self._runtime_capability = runtime_capability

    @property
    def loaded(self) -> bool:
        return self._model is not None and self._processor is not None

    @property
    def loaded_facts(self) -> dict[str, object] | None:
        return copy.deepcopy(self._loaded_facts)

    def load(self) -> None:
        """Load only when the caller explicitly chooses a model action."""

        if self._runtime_capability is not _REAL_RUNTIME_CAPABILITY:
            raise Phase4LocalQwenContractError(
                "model load requires the internal supervised worker"
            )
        if self.loaded:
            return
        if not self._model_root.exists() or not self._model_root.is_dir():
            raise Phase4LocalQwenContractError("model root is unavailable")
        for distribution, expected in (("transformers", TRANSFORMERS_VERSION), ("torch", TORCH_VERSION), ("bitsandbytes", BITSANDBYTES_VERSION), ("accelerate", ACCELERATE_VERSION)):
            try:
                actual = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError as exc:
                raise Phase4LocalQwenContractError(f"required runtime distribution is missing: {distribution}") from exc
            if actual != expected:
                raise Phase4LocalQwenContractError(f"runtime distribution drifted: {distribution}")
        # These imports are intentionally inside explicit load().
        transformers = importlib.import_module("transformers")
        torch = importlib.import_module("torch")
        bitsandbytes = importlib.import_module("bitsandbytes")
        del bitsandbytes  # The Transformers quantization config owns the loader.
        if not torch.cuda.is_available() or torch.cuda.current_device() != 0:
            raise Phase4LocalQwenContractError("GPU0 is not the selected local device")
        if torch.cuda.get_device_name(0) != "NVIDIA GeForce RTX 4060 Laptop GPU":
            raise Phase4LocalQwenContractError("GPU device identity drifted")
        actual_gpu = probe_local_gpu_facts()
        if (
            actual_gpu["device_index"] != self._profile.device_index
            or actual_gpu["device_uuid"] != self._profile.device_uuid
            or actual_gpu["device_name"] != self._profile.device_name
            or actual_gpu["total_vram_bytes"] != self._profile.total_vram_bytes
            or actual_gpu["driver_version"] != self._profile.driver_version
            or actual_gpu["cuda_version"] != self._profile.cuda_version
        ):
            raise Phase4LocalQwenContractError("action-time GPU/profile binding drifted")
        try:
            quantization_config = transformers.BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
            )
            processor = transformers.AutoProcessor.from_pretrained(
                str(self._model_root),
                local_files_only=True,
                trust_remote_code=False,
            )
            model = transformers.AutoModelForMultimodalLM.from_pretrained(
                str(self._model_root),
                local_files_only=True,
                trust_remote_code=False,
                device_map={"": 0},
                dtype=torch.bfloat16,
                quantization_config=quantization_config,
                low_cpu_mem_usage=True,
            )
        except Exception as exc:  # pragma: no cover - explicit future model action
            raise Phase4LocalQwenContractError("explicit local Qwen load failed closed") from exc
        if getattr(model, "is_loaded_in_4bit", False) is not True:
            raise Phase4LocalQwenContractError("loaded model is not the frozen 4-bit profile")
        hf_device_map, parameter_devices = _validate_live_model_placement(model)
        compute_dtypes = {
            str(module.compute_dtype)
            for module in model.modules()
            if hasattr(module, "compute_dtype")
        }
        if compute_dtypes and compute_dtypes != {"torch.bfloat16"}:
            raise Phase4LocalQwenContractError("loaded quantized compute dtype drifted")
        model.eval()
        self._processor = processor
        self._model = model
        self._torch = torch
        self._loaded_facts = {
            "model_class": type(model).__name__,
            "processor_class": type(processor).__name__,
            "is_loaded_in_4bit": True,
            "hf_device_map": hf_device_map,
            "parameter_devices": sorted(parameter_devices),
            "compute_dtypes": sorted(compute_dtypes),
            "cpu_offload": False,
            "device": "cuda:0",
        }

    @staticmethod
    def _validated_text_inputs(inputs: object) -> tuple[str, ...]:
        if not isinstance(inputs, Mapping):
            raise Phase4LocalQwenContractError("processor output must be a mapping")
        keys = set(inputs)
        required = {"input_ids", "attention_mask"}
        allowed = required | {"mm_token_type_ids"}
        multimedia = [
            key
            for key in keys
            if key == "pixel_values"
            or str(key).startswith(("image_", "video_", "pixel_"))
            or "image" in str(key)
            or "video" in str(key)
        ]
        if multimedia or not required.issubset(keys) or not keys.issubset(allowed):
            raise Phase4LocalQwenContractError("text-only processor contract failed")
        return tuple(sorted(str(key) for key in keys))

    def generate(self, *, node_id: str, input_bytes: bytes, prompt_bytes: bytes, config_bytes: bytes, request_bytes: bytes) -> bytes:
        """Generate through an already loaded backend; never auto-loads."""

        if self._runtime_capability is not _REAL_RUNTIME_CAPABILITY:
            raise Phase4LocalQwenContractError(
                "model generation requires the internal supervised worker"
            )
        if not self.loaded:
            raise Phase4LocalQwenContractError("backend is not explicitly loaded")
        try:  # pragma: no cover - explicit future model action
            prompt_contract = _strict_json(prompt_bytes)
            actual_input = _strict_json(input_bytes)
            _strict_json(config_bytes)
            _strict_json(request_bytes)
            model_text = (
                "PROMPT_CONTRACT_JSON\n"
                + _canonical_bytes(prompt_contract).decode("utf-8")
                + "\nACTUAL_NODE_INPUT_JSON\n"
                + _canonical_bytes(actual_input).decode("utf-8")
            )
            messages = [
                {"role": "user", "content": [{"type": "text", "text": model_text}]}
            ]
            rendered = self._processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            encoded = self._processor(text=[rendered], return_tensors="pt")
            keys = self._validated_text_inputs(encoded)
            encoded = {key: encoded[key].to("cuda:0") for key in keys}
            input_length = int(encoded["input_ids"].shape[1])
            if input_length > self._profile.max_input_tokens:
                raise Phase4LocalQwenContractError("model input token cap exceeded")
            self._torch.manual_seed(self._profile.seed)
            self._torch.cuda.manual_seed_all(self._profile.seed)
            generated = self._model.generate(
                **encoded,
                max_new_tokens=self._profile.max_new_tokens,
                do_sample=False,
                num_return_sequences=1,
            )
            generated_only = generated[:, input_length:]
            text = self._processor.batch_decode(
                generated_only,
                skip_special_tokens=True,
            )[0]
            return text.encode("utf-8")
        except Exception as exc:
            raise Phase4LocalQwenContractError("explicit local Qwen generation failed closed") from exc


class SupervisedWorkerFailure(Phase4LocalQwenContractError):
    """A terminal worker failure already handled by the surviving parent."""

    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


class _WorkerStderrCapture:
    """Thread-safe stderr bytes plus explicit reader completion/error state."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._completed = False
        self._error: str | None = None
        self._lock = threading.Lock()

    def append(self, raw: bytes) -> None:
        if type(raw) is not bytes:
            raise Phase4LocalQwenContractError("worker stderr chunk must be bytes")
        with self._lock:
            if self._completed or self._error is not None:
                raise Phase4LocalQwenContractError("worker stderr capture is terminal")
            self._chunks.append(raw)

    def complete(self) -> None:
        with self._lock:
            if self._error is None:
                self._completed = True

    def fail(self, exc: BaseException) -> None:
        with self._lock:
            self._completed = False
            self._error = f"{type(exc).__name__}: {exc}"

    def snapshot(
        self,
        *,
        stderr_thread: threading.Thread | None,
        worker_exit_verified: bool,
    ) -> dict[str, object]:
        thread_joined = False
        join_error: str | None = None
        if stderr_thread is not None:
            try:
                stderr_thread.join(timeout=10)
                thread_joined = not stderr_thread.is_alive()
            except (RuntimeError, OSError) as exc:
                join_error = f"{type(exc).__name__}: {exc}"
        with self._lock:
            reader_completed = self._completed
            reader_error = self._error
            raw = b"".join(self._chunks)
        capture_error = reader_error or join_error
        if capture_error is None and not worker_exit_verified:
            capture_error = "worker_exit_unverified"
        if capture_error is None and not thread_joined:
            capture_error = "stderr_reader_not_joined"
        if capture_error is None and not reader_completed:
            capture_error = "stderr_reader_not_completed"
        completed = (
            worker_exit_verified
            and thread_joined
            and reader_completed
            and capture_error is None
        )
        return {
            "completed": completed,
            "reader_completed": reader_completed,
            "thread_joined": thread_joined,
            "error": capture_error,
            "byte_length_observed": len(raw),
            "stderr_bytes": raw if completed else None,
        }


class SupervisedWorkerStartFailure(Phase4LocalQwenContractError):
    """A load/start failure whose child-process teardown was verified."""

    def __init__(
        self,
        message: str,
        teardown_facts: Mapping[str, object],
        *,
        stderr_bytes: bytes | None,
        failure_code: str = "load_failed",
    ) -> None:
        super().__init__(message)
        self.teardown_facts = copy.deepcopy(dict(teardown_facts))
        if stderr_bytes is not None and type(stderr_bytes) is not bytes:
            raise Phase4LocalQwenContractError(
                "worker start failure stderr must be bytes or absent"
            )
        self._stderr_bytes = None if stderr_bytes is None else bytes(stderr_bytes)
        self.failure_code = failure_code

    @property
    def stderr_bytes(self) -> bytes | None:
        return None if self._stderr_bytes is None else bytes(self._stderr_bytes)


class SupervisedLocalQwenBackend:
    """Parent-side IPC client for one independently terminable model worker."""

    def __init__(
        self,
        *,
        process: subprocess.Popen[str],
        messages: "queue.Queue[dict[str, object]]",
        stderr_capture: _WorkerStderrCapture,
        profile: LocalQwenProfile,
        worker_id: str,
        loaded_facts: Mapping[str, object],
        capability: object,
        stderr_thread: threading.Thread | None = None,
    ) -> None:
        if capability is not _REAL_RUNTIME_CAPABILITY:
            raise Phase4LocalQwenContractError("real worker capability is invalid")
        profile.validate()
        if process.pid is None or process.stdin is None:
            raise Phase4LocalQwenContractError("model worker process is invalid")
        self._process = process
        self._messages = messages
        self._stderr_capture = stderr_capture
        self._stderr_thread = stderr_thread
        self._profile = profile
        self._worker_id = _text(worker_id, "worker_id", pattern=_ID_RE)
        self._loaded_facts = copy.deepcopy(dict(loaded_facts))
        self._closed = False
        self._generation_started = False
        self._last_raw_captured = False
        self._teardown = {
            "worker_id": self._worker_id,
            "worker_pid": process.pid,
            "worker_exit_code": None,
            "worker_exit_verified": False,
            "graceful_shutdown_requested": False,
            "terminate_sent": False,
            "kill_sent": False,
            "terminal_status": "worker_running",
        }
        object.__setattr__(self, "_real_runtime_capability", capability)

    @property
    def loaded_facts(self) -> dict[str, object]:
        return copy.deepcopy(self._loaded_facts)

    @property
    def generation_started(self) -> bool:
        return self._generation_started

    @property
    def last_raw_captured(self) -> bool:
        return self._last_raw_captured

    @property
    def stderr_bytes(self) -> bytes | None:
        snapshot = self._stderr_capture.snapshot(
            stderr_thread=self._stderr_thread,
            worker_exit_verified=bool(self._teardown["worker_exit_verified"]),
        )
        value = snapshot["stderr_bytes"]
        return None if value is None else bytes(value)

    @property
    def teardown_facts(self) -> dict[str, object]:
        facts = copy.deepcopy(self._teardown)
        capture = self._stderr_capture.snapshot(
            stderr_thread=self._stderr_thread,
            worker_exit_verified=bool(facts["worker_exit_verified"]),
        )
        facts["stderr_capture_completed"] = capture["completed"]
        facts["stderr_capture_error"] = capture["error"]
        facts["stderr_thread_joined"] = capture["thread_joined"]
        stderr = capture["stderr_bytes"]
        facts["stderr_identity"] = (
            None
            if stderr is None
            else WorkerStderrArtifact.create(stderr_bytes=stderr).identity()
        )
        return facts

    def _send(self, payload: Mapping[str, object]) -> None:
        if self._closed or self._process.stdin is None:
            raise SupervisedWorkerFailure(
                "worker_unavailable", "model worker is not available"
            )
        try:
            self._process.stdin.write(_canonical_bytes(dict(payload)).decode("utf-8") + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._force_teardown("worker_failed")
            raise SupervisedWorkerFailure(
                "worker_ipc_failed", "model worker IPC failed closed"
            ) from exc

    def _receive(self, *, timeout: int) -> dict[str, object]:
        try:
            message = self._messages.get(timeout=timeout)
        except queue.Empty as exc:
            self._force_teardown("generation_timeout")
            raise SupervisedWorkerFailure(
                "generation_timeout", "model generation exceeded its wall-clock deadline"
            ) from exc
        if message.get("kind") == "protocol_error":
            self._force_teardown("worker_failed")
            raise SupervisedWorkerFailure(
                "worker_protocol_failed", "model worker protocol failed closed"
            )
        return message

    def _force_teardown(self, terminal_status: str) -> None:
        if self._closed:
            return
        process = self._process
        try:
            exit_code = process.poll()
        except OSError:
            exit_code = None
        if exit_code is None:
            self._teardown["terminate_sent"] = True
            try:
                process.terminate()
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self._teardown["kill_sent"] = True
                try:
                    process.kill()
                    process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        try:
            exit_code = process.poll()
        except OSError:
            exit_code = None
        self._closed = True
        self._teardown["worker_exit_code"] = exit_code
        self._teardown["worker_exit_verified"] = exit_code is not None
        self._teardown["terminal_status"] = (
            terminal_status
            if exit_code is not None
            else "worker_teardown_unverified"
        )

    def generate(
        self,
        *,
        node_id: str,
        input_bytes: bytes,
        prompt_bytes: bytes,
        config_bytes: bytes,
        request_bytes: bytes,
    ) -> bytes:
        if self._closed or self._process.poll() is not None:
            raise SupervisedWorkerFailure(
                "worker_unavailable", "model worker exited before generation"
            )
        call_id = f"call-{uuid.uuid4().hex}"
        self._generation_started = True
        self._last_raw_captured = False
        self._send(
            {
                "protocol": f"{P4_03_SCHEMA_PREFIX}.worker-ipc.v1",
                "kind": "generate",
                "call_id": call_id,
                "node_id": node_id,
                "input_b64": _b64(input_bytes, "worker.input"),
                "prompt_b64": _b64(prompt_bytes, "worker.prompt"),
                "config_b64": _b64(config_bytes, "worker.config"),
                "request_b64": _b64(request_bytes, "worker.request"),
            }
        )
        try:
            message = self._receive(timeout=self._profile.timeout_seconds)
        except KeyboardInterrupt as exc:
            self._force_teardown("generation_cancelled")
            raise SupervisedWorkerFailure(
                "generation_cancelled", "model generation was cancelled"
            ) from exc
        if message.get("call_id") != call_id:
            self._force_teardown("worker_failed")
            raise SupervisedWorkerFailure(
                "worker_protocol_failed", "model worker call identity drifted"
            )
        if message.get("kind") == "generation_error":
            self._force_teardown("worker_failed")
            raise SupervisedWorkerFailure(
                "backend_exception", "model worker generation failed closed"
            )
        data = _exact(
            message,
            ("protocol", "kind", "call_id", "worker_id", "raw_b64"),
            "worker_generation_result",
        )
        if (
            data["protocol"] != f"{P4_03_SCHEMA_PREFIX}.worker-ipc.v1"
            or data["kind"] != "generation_result"
            or data["worker_id"] != self._worker_id
        ):
            self._force_teardown("worker_failed")
            raise SupervisedWorkerFailure(
                "worker_protocol_failed", "model worker response drifted"
            )
        raw = _decode_b64(data["raw_b64"], "worker_generation_result.raw_b64")
        self._last_raw_captured = True
        return raw

    def close(self) -> dict[str, object]:
        if not self._closed:
            self._teardown["graceful_shutdown_requested"] = True
            try:
                self._send(
                    {
                        "protocol": f"{P4_03_SCHEMA_PREFIX}.worker-ipc.v1",
                        "kind": "shutdown",
                    }
                )
                message = self._receive(timeout=20)
                if message.get("kind") != "shutdown_ack" or message.get("worker_id") != self._worker_id:
                    raise SupervisedWorkerFailure(
                        "worker_protocol_failed", "worker shutdown acknowledgement drifted"
                    )
                exit_code = self._process.wait(timeout=20)
                self._closed = True
                self._teardown["worker_exit_code"] = exit_code
                self._teardown["worker_exit_verified"] = True
                self._teardown["terminal_status"] = "normal_completed"
            except (OSError, subprocess.SubprocessError, SupervisedWorkerFailure):
                self._force_teardown("worker_failed")
        try:
            exit_code = self._process.poll()
        except OSError:
            exit_code = None
        if exit_code is None:
            if self._teardown["worker_exit_verified"] is not True:
                self._closed = False
                self._force_teardown("worker_failed")
        else:
            self._teardown["worker_exit_code"] = exit_code
            self._teardown["worker_exit_verified"] = True
        return self.teardown_facts


class SupervisedLocalQwenRuntime(NamedTuple):
    """Factory-owned live-validated records and the supervised worker handle."""

    backend: SupervisedLocalQwenBackend
    load_receipt: LocalQwenLoadReceipt
    runtime_start_claim: PilotRuntimeStartClaim
    execution_lease: PilotExecutionLease
    pilot: PilotBinding
    policies: tuple[NodeProjectionPolicy, ...]
    profile: LocalQwenProfile
    manifest: PreCallManifest
    b_input: dict[str, object]


def _worker_stdout_reader(
    stream: object, messages: "queue.Queue[dict[str, object]]"
) -> None:
    try:
        for line in stream:  # type: ignore[union-attr]
            try:
                raw = line.rstrip("\r\n").encode("utf-8")
                messages.put(_strict_json(raw))
            except Exception:
                messages.put({"kind": "protocol_error"})
                return
    except Exception:
        messages.put({"kind": "protocol_error"})


def _worker_stderr_reader(stream: object, capture: _WorkerStderrCapture) -> None:
    try:
        while True:
            raw = stream.buffer.read(4096)  # type: ignore[union-attr]
            if not raw:
                capture.complete()
                return
            capture.append(raw)
    except Exception as exc:
        capture.fail(exc)
        return


def _raise_worker_start_failure(
    *,
    process: subprocess.Popen[str],
    stderr_capture: _WorkerStderrCapture | None,
    stderr_thread: threading.Thread | None = None,
    message: str,
    worker_id: str | None = None,
) -> None:
    terminate_sent = False
    kill_sent = False
    try:
        exit_code = process.poll()
    except OSError:
        exit_code = None
    if exit_code is None:
        terminate_sent = True
        try:
            process.terminate()
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            kill_sent = True
            try:
                process.kill()
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
    try:
        exit_code = process.poll()
    except OSError:
        exit_code = None
    capture = (
        {"completed": False, "thread_joined": False,
         "error": "stderr_reader_unavailable", "stderr_bytes": None}
        if stderr_capture is None
        else stderr_capture.snapshot(
            stderr_thread=stderr_thread,
            worker_exit_verified=exit_code is not None,
        )
    )
    stderr = capture["stderr_bytes"]
    raise SupervisedWorkerStartFailure(
        message,
        {
            "worker_id": worker_id,
            "worker_pid": process.pid,
            "worker_exit_code": exit_code,
            "worker_exit_verified": exit_code is not None,
            "graceful_shutdown_requested": False,
            "terminate_sent": terminate_sent,
            "kill_sent": kill_sent,
            "terminal_status": (
                "load_failed"
                if exit_code is not None
                else "worker_teardown_unverified"
            ),
            "stderr_capture_completed": capture["completed"],
            "stderr_capture_error": capture["error"],
            "stderr_thread_joined": capture["thread_joined"],
            "stderr_identity": (
                None
                if stderr is None
                else WorkerStderrArtifact.create(stderr_bytes=stderr).identity()
            ),
        },
        stderr_bytes=None if stderr is None else bytes(stderr),
    )


def _supervised_worker_python_runtime() -> tuple[str, str]:
    """Resolve the non-overridable worker executable and isolated import path."""

    raw_executable = (
        getattr(sys, "_base_executable", None)
        if os.name == "nt"
        else sys.executable
    )
    if type(raw_executable) is not str or not raw_executable:
        raise Phase4LocalQwenContractError(
            "supervised worker Python executable is unavailable"
        )
    executable = Path(raw_executable)
    if not executable.is_absolute() or not executable.is_file():
        raise Phase4LocalQwenContractError(
            "supervised worker Python executable is invalid"
        )
    if os.name == "nt":
        executable = executable.resolve(strict=True)

    source_root = Path(__file__).resolve(strict=True).parent.parent
    if not source_root.is_absolute() or not source_root.is_dir():
        raise Phase4LocalQwenContractError(
            "Req2Web worker source root is invalid"
        )
    venv_root = Path(sys.prefix)
    if not venv_root.is_absolute() or not venv_root.is_dir():
        raise Phase4LocalQwenContractError("current Python environment is invalid")
    venv_root = venv_root.resolve(strict=True)

    configured_paths = sysconfig.get_paths()
    if not isinstance(configured_paths, Mapping):
        raise Phase4LocalQwenContractError(
            "supervised worker Python paths are unavailable"
        )
    python_paths: list[Path] = [source_root]
    seen = {os.path.normcase(str(source_root))}
    for key in ("purelib", "platlib"):
        raw_path = configured_paths.get(key)
        if (
            type(raw_path) is not str
            or not raw_path
            or os.pathsep in raw_path
        ):
            raise Phase4LocalQwenContractError(
                f"supervised worker {key} path is invalid"
            )
        path = Path(raw_path)
        if not path.is_absolute() or not path.is_dir():
            raise Phase4LocalQwenContractError(
                f"supervised worker {key} path is invalid"
            )
        path = path.resolve(strict=True)
        if not path.is_relative_to(venv_root):
            raise Phase4LocalQwenContractError(
                f"supervised worker {key} is outside the current environment"
            )
        identity = os.path.normcase(str(path))
        if identity not in seen:
            seen.add(identity)
            python_paths.append(path)
    return str(executable), os.pathsep.join(str(path) for path in python_paths)


def start_supervised_local_qwen_runtime(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    load_timeout_seconds: int = 600,
) -> SupervisedLocalQwenRuntime:
    """Live-revalidate persisted prepare evidence, then start one worker."""

    from req2web_orchestration.phase4_graph import synthetic_commerce_b_input

    b_input = synthetic_commerce_b_input()
    try:
        pilot, policies, profile, manifest = load_prepared_local_qwen_pilot(
            model_root=model_root,
            integrity_evidence=integrity_evidence,
            result_root=result_root,
            b_input=b_input,
        )
    except OSError as exc:
        raise Phase4LocalQwenContractError(
            "persisted prepare artifact is unavailable"
        ) from exc
    try:
        execution_lease = PilotExecutionLease.from_bytes(
            (result_root / "execution_lease.json").read_bytes()
        )
    except OSError as exc:
        raise Phase4LocalQwenContractError(
            "persisted execution lease is unavailable"
        ) from exc
    if (
        execution_lease.pilot_id != pilot.pilot_id
        or execution_lease.case_id != pilot.case_binding["case_id"]
        or execution_lease.request_id != pilot.case_binding["request_id"]
        or execution_lease.manifest_identity
        != _identity(manifest.to_dict(), revision=MANIFEST_SCHEMA_VERSION)
        or execution_lease.pilot_identity
        != _identity(pilot.to_dict(), revision=PILOT_BINDING_SCHEMA_VERSION)
        or (result_root / "execution_lease.json").read_bytes()
        != execution_lease.canonical_bytes()
        or execution_lease.parent_pid != os.getpid()
    ):
        raise Phase4LocalQwenContractError("execution lease binding drifted")
    _integer(load_timeout_seconds, "load_timeout_seconds", minimum=1, maximum=1800)
    runtime_claim = _acquire_runtime_start_claim(
        result_root=result_root,
        lease=execution_lease,
    )
    worker_bootstrap = (
        "import sys; from pathlib import Path; "
        "from req2web_runtime.phase4_local_qwen import "
        "_run_local_qwen_worker_protocol; "
        "raise SystemExit(_run_local_qwen_worker_protocol(model_root=Path(sys.argv[1])))"
    )
    worker_executable, worker_pythonpath = _supervised_worker_python_runtime()
    command = [
        worker_executable,
        "-c",
        worker_bootstrap,
        str(model_root),
    ]
    environment = dict(os.environ)
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "LANGSMITH_TRACING": "0",
            "LANGCHAIN_TRACING_V2": "0",
            "PYTHONPATH": worker_pythonpath,
        }
    )
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        env=environment,
        creationflags=creationflags,
    )
    if process.stdout is None or process.stderr is None or process.stdin is None:
        _raise_worker_start_failure(
            process=process,
            stderr_capture=None,
            message="model worker IPC streams are unavailable",
        )
    messages: "queue.Queue[dict[str, object]]" = queue.Queue()
    stderr_capture = _WorkerStderrCapture()
    threading.Thread(
        target=_worker_stdout_reader,
        args=(process.stdout, messages),
        daemon=True,
    ).start()
    stderr_thread = threading.Thread(
        target=_worker_stderr_reader,
        args=(process.stderr, stderr_capture),
        daemon=True,
    )
    stderr_thread.start()
    try:
        process.stdin.write(
            _canonical_bytes(
                {
                    "protocol": f"{P4_03_SCHEMA_PREFIX}.worker-ipc.v1",
                    "kind": "load",
                    "profile_b64": _b64(
                        profile.canonical_bytes(), "worker.profile"
                    ),
                }
            ).decode("utf-8")
            + "\n"
        )
        process.stdin.flush()
        loaded = messages.get(timeout=load_timeout_seconds)
    except queue.Empty:
        _raise_worker_start_failure(
            process=process,
            stderr_capture=stderr_capture,
            stderr_thread=stderr_thread,
            message="model worker load timed out",
        )
    except (BrokenPipeError, OSError):
        _raise_worker_start_failure(
            process=process,
            stderr_capture=stderr_capture,
            stderr_thread=stderr_thread,
            message="model worker load IPC failed closed",
        )
    if loaded.get("kind") != "loaded":
        _raise_worker_start_failure(
            process=process,
            stderr_capture=stderr_capture,
            stderr_thread=stderr_thread,
            message="model worker load failed closed",
        )
    data = _exact(
        loaded,
        ("protocol", "kind", "worker_id", "worker_pid", "loaded_facts"),
        "worker_loaded",
    )
    if (
        data["protocol"] != f"{P4_03_SCHEMA_PREFIX}.worker-ipc.v1"
        or data["kind"] != "loaded"
        or _integer(data["worker_pid"], "worker_loaded.worker_pid", minimum=1)
        != process.pid
        or not isinstance(data["loaded_facts"], Mapping)
    ):
        _raise_worker_start_failure(
            process=process,
            stderr_capture=stderr_capture,
            stderr_thread=stderr_thread,
            message="model worker load identity drifted",
            worker_id=(
                data["worker_id"]
                if type(data["worker_id"]) is str
                and _ID_RE.fullmatch(data["worker_id"]) is not None
                else None
            ),
        )
    backend = SupervisedLocalQwenBackend(
        process=process,
        messages=messages,
        stderr_capture=stderr_capture,
        stderr_thread=stderr_thread,
        profile=profile,
        worker_id=_text(data["worker_id"], "worker_loaded.worker_id", pattern=_ID_RE),
        loaded_facts=data["loaded_facts"],
        capability=_REAL_RUNTIME_CAPABILITY,
    )
    try:
        receipt = LocalQwenLoadReceipt.create(
            manifest=manifest,
            pilot=pilot,
            profile=profile,
            loaded_facts=backend.loaded_facts,
        )
        object.__setattr__(
            receipt, "_real_runtime_capability", _REAL_RUNTIME_CAPABILITY
        )
        persist_local_qwen_load_receipt(result_root=result_root, receipt=receipt)
    except Exception as exc:
        backend._force_teardown("load_failed")
        raise SupervisedWorkerStartFailure(
            "model load receipt persistence failed closed",
            backend.teardown_facts,
            stderr_bytes=backend.stderr_bytes,
            failure_code="evidence_persistence_failed",
        ) from exc
    return SupervisedLocalQwenRuntime(
        backend=backend,
        load_receipt=receipt,
        runtime_start_claim=runtime_claim,
        execution_lease=execution_lease,
        pilot=pilot,
        policies=policies,
        profile=profile,
        manifest=manifest,
        b_input=copy.deepcopy(b_input),
    )


def _run_local_qwen_worker_protocol(*, model_root: Path) -> int:
    """Internal child entrypoint; the parent remains the evidence authority."""

    worker_id = f"worker-{uuid.uuid4().hex}"
    try:
        first = sys.stdin.buffer.readline()
        load = _exact(
            _strict_json(first.rstrip(b"\r\n")),
            ("protocol", "kind", "profile_b64"),
            "worker_load_request",
        )
        if (
            load["protocol"] != f"{P4_03_SCHEMA_PREFIX}.worker-ipc.v1"
            or load["kind"] != "load"
        ):
            raise Phase4LocalQwenContractError("worker load request drifted")
        profile = LocalQwenProfile.from_bytes(
            _decode_b64(load["profile_b64"], "worker_load_request.profile_b64")
        )
        backend = _LazyTransformersQwenBackend(
            model_root=model_root,
            profile=profile,
            runtime_capability=_REAL_RUNTIME_CAPABILITY,
        )
        backend.load()
        loaded_facts = backend.loaded_facts
        if loaded_facts is None:
            raise Phase4LocalQwenContractError("worker loaded facts are unavailable")
        sys.stdout.buffer.write(
            _canonical_bytes(
                {
                    "protocol": f"{P4_03_SCHEMA_PREFIX}.worker-ipc.v1",
                    "kind": "loaded",
                    "worker_id": worker_id,
                    "worker_pid": os.getpid(),
                    "loaded_facts": loaded_facts,
                }
            )
            + b"\n"
        )
        sys.stdout.buffer.flush()
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return 0
            request = _strict_json(line.rstrip(b"\r\n"))
            if request.get("protocol") != f"{P4_03_SCHEMA_PREFIX}.worker-ipc.v1":
                raise Phase4LocalQwenContractError("worker protocol revision drifted")
            if request.get("kind") == "shutdown":
                response = {
                    "protocol": f"{P4_03_SCHEMA_PREFIX}.worker-ipc.v1",
                    "kind": "shutdown_ack",
                    "worker_id": worker_id,
                }
                sys.stdout.buffer.write(_canonical_bytes(response) + b"\n")
                sys.stdout.buffer.flush()
                return 0
            data = _exact(
                request,
                (
                    "protocol", "kind", "call_id", "node_id", "input_b64",
                    "prompt_b64", "config_b64", "request_b64",
                ),
                "worker_generate_request",
            )
            if data["kind"] != "generate":
                raise Phase4LocalQwenContractError("worker request kind is invalid")
            try:
                raw = backend.generate(
                    node_id=_text(data["node_id"], "worker.node_id"),
                    input_bytes=_decode_b64(data["input_b64"], "worker.input_b64"),
                    prompt_bytes=_decode_b64(data["prompt_b64"], "worker.prompt_b64"),
                    config_bytes=_decode_b64(data["config_b64"], "worker.config_b64"),
                    request_bytes=_decode_b64(data["request_b64"], "worker.request_b64"),
                )
                response = {
                    "protocol": f"{P4_03_SCHEMA_PREFIX}.worker-ipc.v1",
                    "kind": "generation_result",
                    "call_id": data["call_id"],
                    "worker_id": worker_id,
                    "raw_b64": _b64(raw, "worker.raw"),
                }
            except Exception:
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
                response = {
                    "protocol": f"{P4_03_SCHEMA_PREFIX}.worker-ipc.v1",
                    "kind": "generation_error",
                    "call_id": data["call_id"],
                }
            sys.stdout.buffer.write(_canonical_bytes(response) + b"\n")
            sys.stdout.buffer.flush()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        try:
            sys.stdout.buffer.write(
                _canonical_bytes(
                    {
                        "protocol": f"{P4_03_SCHEMA_PREFIX}.worker-ipc.v1",
                        "kind": "load_error",
                    }
                )
                + b"\n"
            )
            sys.stdout.buffer.flush()
        except Exception:
            pass
        return 2


class Phase4LocalQwenPilotRunner:
    """Bounded node-local/integrated runner around the frozen P4-03 contract."""

    def __init__(
        self,
        *,
        pilot: PilotBinding,
        policies: Sequence[NodeProjectionPolicy],
        profile: LocalQwenProfile,
        manifest: PreCallManifest,
        result_root: Path,
        b_input: Mapping[str, object],
        backend: QwenBackend | None = None,
        source_kind: str = "scripted_test_fixture",
        load_receipt: LocalQwenLoadReceipt | None = None,
        execution_lease: PilotExecutionLease | None = None,
        runtime_start_claim: PilotRuntimeStartClaim | None = None,
        assembler_context: object | None = None,
        assembler_guidance: object | None = None,
    ) -> None:
        pilot.validate()
        profile.validate()
        manifest.validate()
        if [policy.node_id for policy in policies] != list(NODE_ORDER):
            raise Phase4LocalQwenContractError("runner policies must be ordered F1-F4")
        if source_kind not in SOURCE_KINDS:
            raise Phase4LocalQwenContractError("runner source kind is invalid")
        if source_kind == "real_local_qwen":
            if (
                type(backend) is not SupervisedLocalQwenBackend
                or type(load_receipt) is not LocalQwenLoadReceipt
                or getattr(backend, "_real_runtime_capability", None)
                is not _REAL_RUNTIME_CAPABILITY
                or getattr(load_receipt, "_real_runtime_capability", None)
                is not _REAL_RUNTIME_CAPABILITY
                or type(execution_lease) is not PilotExecutionLease
                or type(runtime_start_claim) is not PilotRuntimeStartClaim
            ):
                raise Phase4LocalQwenContractError(
                    "real runner requires the controlled supervised runtime"
                )
            load_receipt.validate_against(
                manifest=manifest,
                pilot=pilot,
                profile=profile,
            )
            if backend.loaded_facts != load_receipt.loaded_facts:
                raise Phase4LocalQwenContractError(
                    "real runner loaded facts drifted from its receipt"
                )
            execution_lease.validate()
            runtime_start_claim.validate()
            if (
                execution_lease.pilot_id != pilot.pilot_id
                or execution_lease.manifest_identity
                != _identity(manifest.to_dict(), revision=MANIFEST_SCHEMA_VERSION)
                or (result_root / "execution_lease.json").read_bytes()
                != execution_lease.canonical_bytes()
                or runtime_start_claim.lease_identity
                != _identity(
                    execution_lease.to_dict(),
                    revision=EXECUTION_LEASE_SCHEMA_VERSION,
                )
                or (result_root / "runtime_start_claim.json").read_bytes()
                != runtime_start_claim.canonical_bytes()
            ):
                raise Phase4LocalQwenContractError(
                    "real runner execution lease drifted"
                )
        elif (
            load_receipt is not None
            or execution_lease is not None
            or runtime_start_claim is not None
        ):
            raise Phase4LocalQwenContractError("fixture runner cannot carry real execution evidence")
        elif (
            type(backend) is not ScriptedFixtureBackend
            or getattr(backend, "_fixture_capability", None)
            is not _FIXTURE_BACKEND_CAPABILITY
        ):
            raise Phase4LocalQwenContractError(
                "fixture runner requires the controlled deterministic backend"
            )
        self._policies = {policy.node_id: NodeProjectionPolicy.from_dict(policy.to_dict()) for policy in policies}
        if manifest.pilot_binding != pilot.to_dict():
            raise Phase4LocalQwenContractError("runner manifest/pilot binding drifted")
        if not isinstance(result_root, Path) or not result_root.is_absolute() or not result_root.is_dir():
            raise Phase4LocalQwenContractError("runner result root is invalid")
        marker_path = result_root / RESULT_ROOT_MARKER_NAME
        if not marker_path.is_file() or marker_path.read_text(encoding="ascii").strip() != pilot.result_root_marker:
            raise Phase4LocalQwenContractError("runner result-root marker is invalid")
        from req2web_orchestration.phase4_graph import validate_b_input

        self._b_input = validate_b_input(dict(b_input))
        case = pilot.case_binding
        expected_b_identity = _identity(self._b_input, revision="canonical_b.p4.v1")
        if case["case_id"] != self._b_input["case_id"] or case["request_id"] != self._b_input["request_id"] or case["b_identity"] != expected_b_identity:
            raise Phase4LocalQwenContractError("runner case/B binding drifted")
        self._b_input_bytes = _canonical_bytes(self._b_input)
        self._pilot = pilot
        self._profile = profile
        self._manifest = manifest
        self._result_root = result_root
        self._backend = backend
        self._load_receipt = load_receipt
        self._source_kind = source_kind
        self._runtime_load_ref = (
            None
            if load_receipt is None
            else {
                "ref_type": "local_qwen_load_receipt",
                "ref_id": load_receipt.receipt_id,
                "ref_sha256": load_receipt.sha256(),
                "ref_revision": LOAD_RECEIPT_SCHEMA_VERSION,
            }
        )
        self._assembler_context = assembler_context
        self._assembler_guidance = assembler_guidance
        self._ledger = AttemptLedger.create(pilot=pilot)
        self._node_local_status: dict[str, str] = {node_id: "not_started" for node_id in NODE_ORDER}
        self._node_local_failures: dict[str, AttemptResult] = {}
        self._node_local_outputs: dict[str, bytes] = {}
        self._node_local_refs: dict[str, dict[str, object]] = {}
        self._active_run_id: str | None = None
        self._active_call_kind: str | None = None
        self._active_outputs: dict[str, bytes] = {}
        self._active_refs: dict[str, dict[str, object]] = {}
        self._active_authority_state: Mapping[str, object] | None = None
        self._integrated_run_id: str | None = None
        self._integrated_outcome = "not_started"
        self._composition_status = "not_executed"
        self._assembler_status = "not_executed"
        self._stopped = False
        self._model_calls = 0
        self._integrated_graph_events: list[str] = []
        self._integrated_node_pass_count = 0
        self._latest_result: AttemptResult | None = None
        self._mapping_record: dict[str, object] | None = None
        self._candidate_record: dict[str, object] | None = None
        self._candidate_bytes: bytes | None = None

    @property
    def ledger(self) -> AttemptLedger:
        return self._ledger

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def integrated_graph_events(self) -> tuple[str, ...]:
        return tuple(self._integrated_graph_events)

    @property
    def latest_result(self) -> AttemptResult | None:
        if self._latest_result is None:
            return None
        return AttemptResult.from_dict(self._latest_result.to_dict())

    def _policy(self, node_id: str) -> NodeProjectionPolicy:
        if node_id not in self._policies:
            raise Phase4LocalQwenContractError("unknown pilot node")
        return self._policies[node_id]

    def _manifest_ref(self) -> dict[str, object]:
        return _ref_from_identity("d17_manifest", self._manifest.manifest_id, _identity(self._manifest.to_dict(), revision=MANIFEST_SCHEMA_VERSION))

    def _policy_ref(self, policy: NodeProjectionPolicy) -> dict[str, object]:
        return {"ref_type": "d17_policy", "ref_id": policy.policy_id, "ref_sha256": policy.sha256(), "ref_revision": policy.projection_revision}

    def _input_view_ref(self, run_id: str, node_id: str, actual_input: bytes) -> dict[str, object]:
        return {"ref_type": "d17_input_view", "ref_id": f"input-{_hex_sha256(actual_input)[:20]}", "ref_sha256": _sha256(actual_input), "ref_revision": f"{P4_03_SCHEMA_PREFIX}.input-view.{run_id}.{node_id}"}

    def _budget_identity(self) -> dict[str, object]:
        return _identity({"pilot_id": self._pilot.pilot_id, "node_total_call_cap": 3, "node_local_call_cap": 2, "integrated_run_cap": 1, "retry_count_cap": 0}, revision=f"{P4_03_SCHEMA_PREFIX}.budget.v1")

    def _run_paths(self, *, run_id: str, node_id: str, attempt_index: int) -> tuple[str, str]:
        attempt = f"runs/{run_id}/{node_id}/attempt-{attempt_index:02d}"
        return attempt, f"{attempt}/{RAW_RESPONSE_NAME}"

    def _write_ledger(self) -> None:
        _write_atomic(self._result_root, "ledger.json", self._ledger.canonical_bytes())

    def _authority_new(self) -> Mapping[str, object]:
        from req2web_orchestration.phase4_graph import phase4_create_authority_state

        return phase4_create_authority_state(self._b_input)

    def _upstream_for(self, node_id: str) -> tuple[list[dict[str, object]], dict[str, bytes]]:
        policy = self._policy(node_id)
        required = list(policy.upstream_required_node_ids)
        if list(self._active_outputs) != required:
            raise Phase4LocalQwenContractError("actual input depends on missing or out-of-order upstream output")
        refs = [self._active_refs[node] for node in required]
        _validate_same_scope_upstream_refs(refs, pilot_id=self._pilot.pilot_id, run_id=str(self._active_run_id), case_id=self._pilot.case_binding["case_id"], request_id=self._pilot.case_binding["request_id"], expected_nodes=required)
        return refs, {node: self._active_outputs[node] for node in required}

    def _make_attempt_records(self, *, node_id: str, attempt_index: int, call_kind: str, actual_input: bytes, prompt: bytes, config: bytes, request: bytes, upstream_refs: Sequence[Mapping[str, object]], prior_failure: AttemptResult | None, prompt_version: int, change_reason: str | None) -> tuple[NodeD17ActionRecord, AttemptPreCall, str, str]:
        policy = self._policy(node_id)
        caps = _node_policy_caps(policy.field_caps, "policy.field_caps")
        for raw, cap, name in ((actual_input, caps["input_bytes"], "input"), (prompt, caps["prompt_bytes"], "prompt"), (config, caps["config_bytes"], "config"), (request, caps["request_bytes"], "request")):
            if len(raw) > cap:
                raise Phase4LocalQwenContractError(f"{name} exceeds its frozen cap")
        if prior_failure is not None:
            prior_failure.validate()
            if prior_failure.failure_code is None or change_reason not in CHANGE_REASONS:
                raise Phase4LocalQwenContractError("attempt v2 prior result is not a failure")
        prompt_change = (
            None
            if prior_failure is None
            else {
                "prior_result_id": prior_failure.result_id,
                "prior_failure_identity": _identity(
                    prior_failure.to_dict(), revision=ATTEMPT_RESULT_SCHEMA_VERSION
                ),
                "change_reason": change_reason,
            }
        )
        run_id = str(self._active_run_id)
        case = self._pilot.case_binding
        action = NodeD17ActionRecord.create(
            pilot_id=self._pilot.pilot_id,
            run_id=run_id,
            case_id=case["case_id"],
            request_id=case["request_id"],
            node_id=node_id,
            attempt_index=attempt_index,
            call_kind=call_kind,
            profile=self._profile,
            manifest_ref=self._manifest_ref(),
            policy_ref=self._policy_ref(policy),
            input_view_ref=self._input_view_ref(run_id, node_id, actual_input),
            upstream_refs=upstream_refs,
            actual_input=actual_input,
            prompt_revision=(
                policy.prompt_template_revision
                if prompt_version == 1
                else "p4-03-prompt-v2"
            ),
            prompt_change=prompt_change,
            prompt=prompt,
            config_revision=policy.config_revision,
            config=config,
            request=request,
            budget_identity=self._budget_identity(),
            source_kind=self._source_kind,
            runtime_load_ref=self._runtime_load_ref,
        )
        action.validate_against(
            pilot=self._pilot,
            policy=policy,
            profile=self._profile,
            load_receipt=self._load_receipt,
        )
        attempt_relative, raw_relative = self._run_paths(run_id=run_id, node_id=node_id, attempt_index=attempt_index)
        pre_call = AttemptPreCall.create(action=action, attempt_relative_path=attempt_relative, raw_response_relative_path=raw_relative, upstream_identities=upstream_refs)
        pre_call.validate()
        _write_once(self._result_root, f"{attempt_relative}/node_d17_action.json", action.canonical_bytes())
        _write_once(self._result_root, f"{attempt_relative}/pre_call.json", pre_call.canonical_bytes())
        return action, pre_call, attempt_relative, raw_relative

    def _result(self, *, pre_call: AttemptPreCall, generate_started: bool, raw_status: str, raw_relative: str, raw: bytes | None, parse_status: str, node_contract_status: str, registry_status: str, composition_status: str = "not_executed", assembler_status: str = "not_executed", integrated_success: bool = False, failure_code: str | None, terminal: bool) -> AttemptResult:
        result = AttemptResult.create(
            pre_call=pre_call,
            generate_started=generate_started,
            raw_status=raw_status,
            raw_response_relative_path=raw_relative,
            raw_sha256=None if raw is None else _sha256(raw),
            raw_byte_length=0 if raw is None else len(raw),
            parse_status=parse_status,
            node_contract_status=node_contract_status,
            registry_status=registry_status,
            composition_status=composition_status,
            assembler_status=assembler_status,
            integrated_success=integrated_success,
            failure_code=failure_code,
            terminal=terminal,
        )
        result.validate()
        return result

    def _run_one(self, *, node_id: str, call_kind: str, prompt_version: int = 1, change_reason: str | None = None) -> AttemptResult:
        if self._stopped:
            raise Phase4LocalQwenContractError("pilot is stopped")
        if self._backend is None or not callable(getattr(self._backend, "generate", None)):
            raise Phase4LocalQwenContractError("no explicit backend is available")
        policy = self._policy(node_id)
        if self._active_run_id is None or self._active_call_kind != call_kind:
            raise Phase4LocalQwenContractError("active run binding is invalid")
        if call_kind == "node_local":
            attempt_index = self._ledger.node_local_counts[node_id] + 1
            prior_failure = self._node_local_failures.get(node_id)
            if attempt_index == 1 and prompt_version != 1:
                raise Phase4LocalQwenContractError("first node-local attempt must use prompt v1")
            if attempt_index == 2:
                if prior_failure is None or prompt_version != 2 or change_reason is None:
                    raise Phase4LocalQwenContractError("second node-local attempt requires explicit v2 prior failure and reason")
            if self._node_local_status[node_id] == "passed":
                raise Phase4LocalQwenContractError("first node-local pass stops that node")
        else:
            attempt_index = self._ledger.node_total_counts[node_id] + 1
            prior_failure = None
            if prompt_version != 1 or change_reason is not None:
                raise Phase4LocalQwenContractError("integrated run must use fresh prompt v1")
        upstream_refs, upstream_outputs = self._upstream_for(node_id)
        if self._active_authority_state is None:
            raise Phase4LocalQwenContractError("authority state is missing before input projection")
        actual_input = derive_node_input(
            node_id=node_id,
            b_input_bytes=self._b_input_bytes,
            upstream_outputs=upstream_outputs,
            authority_state=self._active_authority_state,
            policy=policy,
        )
        if prompt_version == 1:
            prompt = build_prompt_v1(node_id=node_id, input_bytes=actual_input, policy=policy, profile=self._profile)
        else:
            if prior_failure is None or change_reason is None:
                raise Phase4LocalQwenContractError("prompt v2 prior failure is missing")
            prompt = build_prompt_v2(node_id=node_id, input_bytes=actual_input, policy=policy, profile=self._profile, prior_failure=prior_failure, change_reason=change_reason)
        config = build_config_bytes(profile=self._profile, policy=policy)
        request = _build_request_bytes(
            pilot=self._pilot,
            node_id=node_id,
            run_id=str(self._active_run_id),
            attempt_index=attempt_index,
            call_kind=call_kind,
            source_kind=self._source_kind,
        )
        if len(request) > _node_policy_caps(policy.field_caps, "policy.field_caps")["request_bytes"]:
            raise Phase4LocalQwenContractError("request exceeds node cap")
        _, pre_call, _, raw_relative = self._make_attempt_records(
            node_id=node_id,
            attempt_index=attempt_index,
            call_kind=call_kind,
            actual_input=actual_input,
            prompt=prompt,
            config=config,
            request=request,
            upstream_refs=upstream_refs,
            prior_failure=prior_failure,
            prompt_version=prompt_version,
            change_reason=change_reason,
        )

        # This is the exact generate-start count point: the immutable pre-call
        # record is already fsynced, then the ledger is fsynced, then generate
        # is entered.  An exception after entry still consumes the call.
        self._ledger = self._ledger.record_generate_started(node_id=node_id, call_kind=call_kind)
        self._model_calls += 1
        self._write_ledger()
        generate_started = True
        raw: bytes | None = None
        try:
            candidate_raw = self._backend.generate(node_id=node_id, input_bytes=actual_input, prompt_bytes=prompt, config_bytes=config, request_bytes=request)
            if type(candidate_raw) is not bytes:
                raise Phase4LocalQwenContractError("backend raw result must be bytes")
            raw = candidate_raw
        except SupervisedWorkerFailure as exc:
            result = self._result(
                pre_call=pre_call,
                generate_started=generate_started,
                raw_status="not_captured",
                raw_relative=raw_relative,
                raw=None,
                parse_status="failed",
                node_contract_status="not_run",
                registry_status="not_run",
                failure_code=exc.failure_code,
                terminal=True,
            )
            self._ledger = self._ledger.record_result(result)
            self._latest_result = result
            self._stopped = True
            if call_kind == "node_local":
                self._node_local_status[node_id] = (
                    "failed_twice" if attempt_index >= 2 else "failed_once"
                )
                self._node_local_failures[node_id] = result
            self._integrated_outcome = (
                "failed_closed" if call_kind == "integrated" else "not_started"
            )
            self._ledger = self._ledger.stop(
                "worker_terminal_failed_closed", terminal=call_kind == "integrated"
            )
            self._write_ledger()
            _write_once(
                self._result_root,
                f"{pre_call.attempt_relative_path}/attempt_result.json",
                result.canonical_bytes(),
            )
            return result
        except Exception:
            result = self._result(pre_call=pre_call, generate_started=generate_started, raw_status="not_captured", raw_relative=raw_relative, raw=None, parse_status="failed", node_contract_status="not_run", registry_status="not_run", failure_code="backend_exception", terminal=call_kind == "integrated" or (call_kind == "node_local" and attempt_index >= 2))
            self._ledger = self._ledger.record_result(result)
            self._latest_result = result
            self._write_ledger()
            _write_once(self._result_root, f"{pre_call.attempt_relative_path}/attempt_result.json", result.canonical_bytes())
            self._handle_failure(node_id=node_id, call_kind=call_kind, result=result)
            return result

        # Raw-first: this write happens before any parser, contract, registry,
        # composition, or route call.  Empty bytes are still an artifact and a
        # failed attempt, never a ProviderRawResponse.
        _write_once(self._result_root, raw_relative, raw)
        if not raw:
            result = self._result(pre_call=pre_call, generate_started=generate_started, raw_status="captured_empty", raw_relative=raw_relative, raw=raw, parse_status="failed", node_contract_status="not_run", registry_status="not_run", failure_code="empty_raw_response", terminal=call_kind == "integrated" or (call_kind == "node_local" and attempt_index >= 2))
            self._ledger = self._ledger.record_result(result)
            self._latest_result = result
            self._write_ledger()
            _write_once(self._result_root, f"{pre_call.attempt_relative_path}/attempt_result.json", result.canonical_bytes())
            self._handle_failure(node_id=node_id, call_kind=call_kind, result=result)
            return result

        try:
            from req2web_orchestration.phase4_graph import phase4_register_node_output

            if self._active_authority_state is None:
                raise Phase4LocalQwenContractError("authority state is missing")
            output = _validate_node_output_json(
                raw,
                node_id=node_id,
                authority_state=self._active_authority_state,
                policy=policy,
            )
            self._active_authority_state = phase4_register_node_output(self._active_authority_state, node_id, output)
        except Exception:
            result = self._result(pre_call=pre_call, generate_started=generate_started, raw_status="captured_nonempty", raw_relative=raw_relative, raw=raw, parse_status="failed", node_contract_status="failed", registry_status="not_run", failure_code="node_contract_invalid", terminal=call_kind == "integrated" or (call_kind == "node_local" and attempt_index >= 2))
            self._ledger = self._ledger.record_result(result)
            self._latest_result = result
            self._write_ledger()
            _write_once(self._result_root, f"{pre_call.attempt_relative_path}/attempt_result.json", result.canonical_bytes())
            self._handle_failure(node_id=node_id, call_kind=call_kind, result=result)
            return result

        result = self._result(pre_call=pre_call, generate_started=generate_started, raw_status="captured_nonempty", raw_relative=raw_relative, raw=raw, parse_status="passed", node_contract_status="passed", registry_status="passed", failure_code=None, terminal=False)
        self._ledger = self._ledger.record_result(result)
        self._latest_result = result
        self._write_ledger()
        _write_once(self._result_root, f"{pre_call.attempt_relative_path}/attempt_result.json", result.canonical_bytes())
        self._active_outputs[node_id] = raw
        ref = {
            "ref_type": "node_output",
            "ref_id": result.result_id,
            "ref_sha256": result.result_id,
            "ref_revision": f"{P4_03_SCHEMA_PREFIX}.node_output.{self._pilot.pilot_id}.{self._active_run_id}.{self._pilot.case_binding['case_id']}.{self._pilot.case_binding['request_id']}.{node_id}",
        }
        _ref(ref, "node_output_ref", "node_output")
        self._active_refs[node_id] = ref
        if call_kind == "node_local":
            self._node_local_status[node_id] = "passed"
            self._node_local_outputs[node_id] = raw
            self._node_local_refs[node_id] = ref
        return result

    def _handle_failure(self, *, node_id: str, call_kind: str, result: AttemptResult) -> None:
        if call_kind == "node_local":
            if self._node_local_status[node_id] == "not_started":
                self._node_local_status[node_id] = "failed_once"
                self._node_local_failures[node_id] = result
            else:
                self._node_local_status[node_id] = "failed_twice"
                self._node_local_failures[node_id] = result
                self._stopped = True
                self._ledger = self._ledger.stop("node_budget_exhausted")
                self._write_ledger()
        else:
            self._stopped = True
            self._integrated_outcome = "failed_closed"
            self._ledger = self._ledger.stop("integrated_terminal_failed_closed", terminal=True)
            self._write_ledger()

    def run_node_local(self, *, node_id: str, prompt_version: int = 1, change_reason: str | None = None) -> AttemptResult:
        """Run one explicit node-local attempt; a second call is never automatic."""

        if self._stopped:
            raise Phase4LocalQwenContractError("pilot is stopped")
        if node_id not in NODE_ORDER:
            raise Phase4LocalQwenContractError("unknown node-local node")
        if self._node_local_status[node_id] == "passed":
            raise Phase4LocalQwenContractError("node-local pass already reached")
        if self._active_call_kind != "node_local":
            self._active_call_kind = "node_local"
            self._active_run_id = f"{self._pilot.pilot_id}-node-local"
            self._active_outputs = dict(self._node_local_outputs)
            self._active_refs = dict(self._node_local_refs)
            self._active_authority_state = self._authority_new()
            # Rebuild the deterministic authority from passed node-local bytes
            # only; this is a live revalidation, not a second semantic source.
            from req2web_orchestration.phase4_graph import phase4_register_node_output

            for prior_node in NODE_ORDER:
                if prior_node in self._active_outputs:
                    self._active_authority_state = phase4_register_node_output(
                        self._active_authority_state,
                        prior_node,
                        _strict_json(self._active_outputs[prior_node], require_canonical=False),
                    )
        return self._run_one(node_id=node_id, call_kind="node_local", prompt_version=prompt_version, change_reason=change_reason)

    def run_integrated(self) -> PilotOutcome:
        """Run at most one fresh integrated graph, then stop on first terminal result."""

        if self._stopped:
            raise Phase4LocalQwenContractError("pilot is stopped")
        if any(self._node_local_status[node_id] != "passed" for node_id in NODE_ORDER):
            raise Phase4LocalQwenContractError("all node-local contracts must pass before integrated run")
        if self._ledger.integrated_run_count >= 1:
            raise Phase4LocalQwenContractError("integrated run cap exhausted")
        self._active_call_kind = "integrated"
        self._integrated_run_id = f"{self._pilot.pilot_id}-integrated-01"
        self._active_run_id = self._integrated_run_id
        self._active_outputs = {}
        self._active_refs = {}
        self._active_authority_state = self._authority_new()
        self._integrated_outcome = "running"
        self._ledger = self._ledger.begin_integrated_run()
        self._write_ledger()
        try:
            from langgraph.graph import END, START, StateGraph

            builder = StateGraph(_IntegratedGraphState)
            for graph_node_id in ("F1", "F2", "F3"):
                def run_node(
                    state: _IntegratedGraphState,
                    *,
                    node_id: str = graph_node_id,
                ) -> _IntegratedGraphState:
                    if state["terminal"]:
                        return state
                    self._integrated_graph_events.append(f"{node_id}:started")
                    result = self._run_one(node_id=node_id, call_kind="integrated")
                    terminal = result.failure_code is not None
                    if not terminal:
                        self._integrated_node_pass_count += 1
                    self._integrated_graph_events.append(
                        f"{node_id}:{'failed_closed' if terminal else 'passed'}"
                    )
                    return {
                        "completed_node_ids": [*state["completed_node_ids"], node_id],
                        "terminal": terminal,
                    }

                builder.add_node(graph_node_id, run_node)

            def map_use_cases(state: _IntegratedGraphState) -> _IntegratedGraphState:
                if state["terminal"]:
                    return state
                self._integrated_graph_events.append("map_use_cases:started")
                try:
                    from req2web_orchestration.phase4_graph import phase4_create_mapping

                    if self._active_authority_state is None:
                        raise Phase4LocalQwenContractError(
                            "integrated mapping authority state is missing"
                        )
                    self._mapping_record = phase4_create_mapping(
                        self._active_authority_state
                    )
                    terminal = False
                except Exception:
                    self._composition_status = "failed_closed"
                    self._assembler_status = "not_executed"
                    self._integrated_outcome = "failed_closed"
                    self._stopped = True
                    self._ledger = self._ledger.stop(
                        "integrated_mapping_failed_closed", terminal=True
                    )
                    self._write_ledger()
                    terminal = True
                self._integrated_graph_events.append(
                    f"map_use_cases:{'failed_closed' if terminal else 'passed'}"
                )
                return {
                    "completed_node_ids": [*state["completed_node_ids"], "map_use_cases"],
                    "terminal": terminal,
                }

            def run_f4(state: _IntegratedGraphState) -> _IntegratedGraphState:
                if state["terminal"]:
                    return state
                self._integrated_graph_events.append("F4:started")
                result = self._run_one(node_id="F4", call_kind="integrated")
                terminal = result.failure_code is not None
                if not terminal:
                    self._integrated_node_pass_count += 1
                self._integrated_graph_events.append(
                    f"F4:{'failed_closed' if terminal else 'passed'}"
                )
                return {
                    "completed_node_ids": [*state["completed_node_ids"], "F4"],
                    "terminal": terminal,
                }

            def compose_candidate(state: _IntegratedGraphState) -> _IntegratedGraphState:
                if state["terminal"]:
                    return state
                self._integrated_graph_events.append("compose_candidate:started")
                try:
                    from req2web_orchestration.phase4_graph import (
                        phase4_compose_candidate,
                        phase4_create_mapping,
                    )

                    if self._active_authority_state is None:
                        raise Phase4LocalQwenContractError(
                            "integrated composition authority state is missing"
                        )
                    current = copy.deepcopy(dict(self._active_authority_state))
                    if (
                        self._mapping_record is None
                        or phase4_create_mapping(current) != self._mapping_record
                    ):
                        raise Phase4LocalQwenContractError(
                            "integrated mapping identity drifted before composition"
                        )
                    candidate_record = phase4_compose_candidate(current)
                    self._candidate_record = candidate_record
                    self._candidate_bytes = _decode_b64(
                        candidate_record["model_semantic_candidate_canonical_b64"],
                        "candidate_composition.b64",
                    )
                    self._composition_status = "validated"
                    terminal = False
                except Exception:
                    self._composition_status = "failed_closed"
                    self._assembler_status = "not_executed"
                    self._integrated_outcome = "failed_closed"
                    self._stopped = True
                    self._ledger = self._ledger.stop(
                        "integrated_composition_failed_closed", terminal=True
                    )
                    self._write_ledger()
                    terminal = True
                self._integrated_graph_events.append(
                    f"compose_candidate:{'failed_closed' if terminal else 'passed'}"
                )
                return {
                    "completed_node_ids": [*state["completed_node_ids"], "compose_candidate"],
                    "terminal": terminal,
                }

            def assemble_page_spec(state: _IntegratedGraphState) -> _IntegratedGraphState:
                if state["terminal"]:
                    return state
                self._integrated_graph_events.append("assemble_page_spec:started")
                try:
                    from req2web_orchestration.phase4_graph import (
                        phase4_assemble_candidate,
                        phase4_synthetic_assembler_bindings,
                    )

                    if self._active_authority_state is None or self._candidate_bytes is None:
                        raise Phase4LocalQwenContractError(
                            "integrated assembler input is missing"
                        )
                    if self._assembler_context is None or self._assembler_guidance is None:
                        assembly_state = copy.deepcopy(
                            dict(self._active_authority_state)
                        )
                        assembly_state["mapping_record"] = copy.deepcopy(
                            self._mapping_record
                        )
                        assembly_state["candidate_composition_record"] = copy.deepcopy(
                            self._candidate_record
                        )
                        context, guidance = phase4_synthetic_assembler_bindings(
                            assembly_state
                        )
                    else:
                        context, guidance = (
                            self._assembler_context,
                            self._assembler_guidance,
                        )
                    assembled = phase4_assemble_candidate(
                        self._candidate_bytes, context, guidance
                    )
                    assembled.validate()
                    self._assembler_status = "assembled"
                    terminal = False
                except Exception:
                    self._assembler_status = "failed_closed"
                    self._integrated_outcome = "failed_closed"
                    self._stopped = True
                    self._ledger = self._ledger.stop(
                        "integrated_assembler_failed_closed", terminal=True
                    )
                    self._write_ledger()
                    terminal = True
                self._integrated_graph_events.append(
                    f"assemble_page_spec:{'failed_closed' if terminal else 'passed'}"
                )
                return {
                    "completed_node_ids": [*state["completed_node_ids"], "assemble_page_spec"],
                    "terminal": terminal,
                }

            builder.add_node("map_use_cases", map_use_cases)
            builder.add_node("F4", run_f4)
            builder.add_node("compose_candidate", compose_candidate)
            builder.add_node("assemble_page_spec", assemble_page_spec)
            builder.add_edge(START, "F1")
            ordered_graph_nodes = (
                "F1", "F2", "F3", "map_use_cases", "F4",
                "compose_candidate", "assemble_page_spec",
            )
            for current, following in zip(
                ordered_graph_nodes[:-1], ordered_graph_nodes[1:], strict=True
            ):
                builder.add_conditional_edges(
                    current,
                    lambda state, next_node=following: (
                        END if state["terminal"] else next_node
                    ),
                )
            builder.add_edge("assemble_page_spec", END)
            graph = builder.compile()
            terminal_state = graph.invoke(
                {"completed_node_ids": [], "terminal": False}
            )
        except Phase4LocalQwenContractError:
            raise
        except Exception as exc:
            self._integrated_outcome = "failed_closed"
            self._stopped = True
            self._ledger = self._ledger.stop(
                "integrated_graph_runtime_failed_closed", terminal=True
            )
            self._write_ledger()
            raise Phase4LocalQwenContractError(
                "integrated LangGraph runtime failed closed"
            ) from exc
        if terminal_state["terminal"]:
            self._integrated_outcome = "failed_closed"
            return self.outcome(
                stop_reason=str(self._ledger.stop_reason)
            )
        self._integrated_outcome = "success"
        self._stopped = True
        self._ledger = self._ledger.stop("integrated_terminal_success", terminal=True)
        self._write_ledger()
        return self.outcome(stop_reason="integrated_terminal_success")

    def stop_for_report(self, *, reason: str) -> PilotOutcome:
        """Close a bounded pilot after an explicit no-second-attempt decision."""

        if self._stopped:
            return self.outcome(stop_reason=reason)
        self._stopped = True
        self._ledger = self._ledger.stop(reason)
        self._write_ledger()
        return self.outcome(stop_reason=reason)

    def outcome(self, *, stop_reason: str = "not_started") -> PilotOutcome:
        statuses = dict(self._node_local_status)
        if self._integrated_outcome == "success":
            status = "integrated_success"
        elif self._integrated_outcome == "failed_closed":
            status = "integrated_failed_closed"
        elif self._stopped and any(value == "failed_twice" for value in statuses.values()):
            status = "stopped_node_exhausted"
        elif self._stopped and any(value == "failed_once" for value in statuses.values()):
            status = "stopped_after_first_failure"
        elif all(value == "passed" for value in statuses.values()):
            status = "node_local_pass"
        elif self._model_calls:
            status = "node_local_running"
        else:
            status = "preflight_ready"
        return PilotOutcome.create(
            pilot=self._pilot,
            status=status,
            stop_reason=stop_reason,
            node_local_statuses=statuses,
            node_total_counts=self._ledger.node_total_counts,
            integrated_run_id=self._integrated_run_id,
            integrated_outcome=self._integrated_outcome,
            integrated_node_raw_contract_pass_count=self._integrated_node_pass_count,
            composition_status=self._composition_status,
            assembler_status=self._assembler_status,
            model_calls_performed=(
                self._model_calls if self._source_kind == "real_local_qwen" else 0
            ),
            model_action_occurred=(
                self._source_kind == "real_local_qwen" and self._model_calls > 0
            ),
            source_kind=self._source_kind,
        )


__all__ = [
    "ACCELERATE_VERSION",
    "ATTEMPT_RESULT_SCHEMA_VERSION",
    "EXECUTION_LEASE_SCHEMA_VERSION",
    "OUTCOME_SCHEMA_VERSION",
    "RUNTIME_START_CLAIM_SCHEMA_VERSION",
    "SUPERVISOR_RECEIPT_SCHEMA_VERSION",
    "WORKER_STDERR_SCHEMA_VERSION",
    "AttemptLedger",
    "AttemptPreCall",
    "AttemptResult",
    "BITSANDBYTES_VERSION",
    "CHANGE_REASONS",
    "LocalQwenLoadReceipt",
    "LocalQwenProfile",
    "NodeD17ActionRecord",
    "NodeProjectionPolicy",
    "PilotBinding",
    "PilotExecutionLease",
    "PilotOutcome",
    "PilotRuntimeStartClaim",
    "PilotSupervisorReceipt",
    "Phase4LocalQwenContractError",
    "Phase4LocalQwenPilotRunner",
    "PreCallManifest",
    "QWEN_MODEL_ID",
    "QWEN_MODEL_REVISION",
    "ScriptedFixtureBackend",
    "SupervisedLocalQwenBackend",
    "SupervisedLocalQwenRuntime",
    "SupervisedWorkerFailure",
    "SupervisedWorkerStartFailure",
    "TRANSFORMERS_VERSION",
    "TORCH_VERSION",
    "WorkerStderrArtifact",
    "acquire_pilot_execution_lease",
    "build_config_bytes",
    "build_prompt_v1",
    "build_prompt_v2",
    "build_synthetic_case_binding",
    "collect_runtime_facts",
    "create_scripted_fixture_backend",
    "derive_node_input",
    "load_prepared_local_qwen_pilot",
    "make_canonical_identity",
    "persist_pilot_outcome",
    "persist_local_qwen_load_receipt",
    "persist_supervisor_receipt",
    "persist_worker_stderr_artifact",
    "prepare_local_qwen_pilot",
    "probe_local_gpu_facts",
    "start_supervised_local_qwen_runtime",
    "validate_model_inventory_metadata",
    "verify_offline_environment",
]
