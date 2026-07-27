"""Versioned trusted-remote Qwen raw-capture contract; local no-action only.

This module has no Provider, model, network, loader, filesystem, or route execution
surface. It represents a locally supplied, parser-shaped raw response candidate and
explicitly does not attest real-provider provenance.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from collections.abc import Mapping

from . import autodl_trusted_remote_records as _records_module
from req2web_provider import semantic_candidate as _semantic_module


RUNTIME_PROFILE_SCHEMA = "req2web.runtime.trusted_remote_qwen_runtime_profile.v1"
LOADER_CONTRACT_SCHEMA = "req2web.runtime.trusted_remote_qwen_loader_contract.v1"
RAW_RESPONSE_RECORD_SCHEMA = "req2web.runtime.trusted_remote_qwen_raw_response_record.v1"
LOCAL_CONTRACT_STATE = "local_contract_not_executed"
LOCAL_CAPTURE_STATE = "local_contract_candidate_capture"
PROVENANCE_NOT_ATTESTED = "not_attested_local_contract_candidate"
_REQUIRED_CASE_IDS = ("path3-commerce-checkout", "path3-media-analysis")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_PROFILE_KEYS = (
    "schema_version", "profile_id", "contract_state", "action_time_plan",
    "plan_binding", "candidate_model", "model_repository", "model_inventory",
    "runtime_inventory", "profile_selection", "precision", "execution_flags",
)
_LOADER_KEYS = (
    "schema_version", "loader_id", "contract_state", "runtime_profile",
    "profile_binding", "loader_boundary",
)
_RAW_KEYS = (
    "schema_version", "record_id", "contract_state", "action_time_plan",
    "plan_binding", "runtime_profile", "profile_binding", "loader_contract",
    "loader_binding", "case_binding", "provider_identity", "raw_response", "status",
)

_ACTION_PLAN_TYPE = _records_module.TrustedRemoteActionTimePlan
_ACTION_PLAN_FROM_BYTES = _ACTION_PLAN_TYPE.from_bytes
_ACTION_PLAN_CANONICAL_BYTES = _ACTION_PLAN_TYPE.canonical_bytes
_ACTION_PLAN_TO_DICT = _ACTION_PLAN_TYPE.to_dict
_ACTION_PLAN_SHA256 = _ACTION_PLAN_TYPE.sha256
_RAW_TYPE = _semantic_module.ProviderRawResponse
_RAW_FROM_BYTES = _RAW_TYPE.from_bytes
_RAW_TO_DICT = _RAW_TYPE.to_dict


class TrustedRemoteQwenRawContractError(ValueError):
    """A local no-action Qwen raw contract violates its canonical boundary."""


def _pairs(pairs, _error=TrustedRemoteQwenRawContractError):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _error("duplicate_json_key")
        result[key] = value
    return result


class _Authority:
    __slots__ = ("dumps", "loads", "sha")

    def __init__(self, dumps, loads, sha):
        self.dumps = dumps
        self.loads = loads
        self.sha = sha


def _make_authority(
    _dumps=json.dumps,
    _loads=json.loads,
    _sha=hashlib.sha256,
    _pairs_fn=_pairs,
    _error=TrustedRemoteQwenRawContractError,
):
    def dumps(value):
        try:
            return _dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise _error("canonical_json_invalid") from exc

    def reject_constant(_value):
        raise _error("non_finite_json_forbidden")

    def loads(raw):
        if type(raw) is not bytes:
            raise _error("canonical_bytes_required")
        try:
            return _loads(raw.decode("utf-8"), object_pairs_hook=_pairs_fn, parse_constant=reject_constant)
        except UnicodeDecodeError as exc:
            raise _error("canonical_bytes_not_utf8") from exc
        except _error:
            raise
        except (json.JSONDecodeError, ValueError) as exc:
            raise _error("canonical_bytes_not_json") from exc

    return _Authority(dumps, loads, lambda raw: _sha(raw).hexdigest())


_AUTHORITY = _make_authority()
_CANONICAL_DUMPS = _AUTHORITY.dumps
_CANONICAL_LOADS = _AUTHORITY.loads
_CANONICAL_SHA256 = _AUTHORITY.sha


def _keys(value, expected, label, _mapping=Mapping, _error=TrustedRemoteQwenRawContractError):
    if not isinstance(value, _mapping) or len(value) != len(expected) or set(value) != set(expected):
        raise _error(f"{label}_exact_keys_invalid")
    return value


def _text(value, label, _error=TrustedRemoteQwenRawContractError):
    if not isinstance(value, str) or not value or value != value.strip():
        raise _error(f"{label}_not_nonempty_text")
    return value


def _hex64(value, label, _text_fn=_text, _pattern=_HEX64, _error=TrustedRemoteQwenRawContractError):
    value = _text_fn(value, label)
    if not _pattern.fullmatch(value):
        raise _error(f"{label}_invalid_sha256")
    return value


def _positive_int(value, label, _error=TrustedRemoteQwenRawContractError):
    if type(value) is not int or value < 1:
        raise _error(f"{label}_not_positive_integer")
    return value


def _false(value, label, _error=TrustedRemoteQwenRawContractError):
    if type(value) is not bool or value is not False:
        raise _error(f"{label}_must_be_false")
    return False


def _identified(data, _authority_ignored=None, _copy=copy.deepcopy, _dumps=_CANONICAL_DUMPS, _sha=_CANONICAL_SHA256):
    body = _copy(dict(data))
    key = "profile_id" if "profile_id" in body else "loader_id" if "loader_id" in body else "record_id"
    body[key] = "0" * 64
    body[key] = _sha(_dumps(body))
    return body


def _valid_identity(data, key, _authority_ignored=None, _copy=copy.deepcopy, _hex_fn=_hex64, _dumps=_CANONICAL_DUMPS, _sha=_CANONICAL_SHA256, _error=TrustedRemoteQwenRawContractError):
    actual = _hex_fn(data[key], key)
    body = _copy(dict(data))
    body[key] = "0" * 64
    if _sha(_dumps(body)) != actual:
        raise _error(f"{key}_invalid")


def _resolve_plan(
    value,
    _plan_type=_ACTION_PLAN_TYPE,
    _plan_from_bytes=_ACTION_PLAN_FROM_BYTES,
    _plan_canonical_bytes=_ACTION_PLAN_CANONICAL_BYTES,
    _plan_to_dict=_ACTION_PLAN_TO_DICT,
    _plan_sha256=_ACTION_PLAN_SHA256,
    _dumps=_CANONICAL_DUMPS,
    _error=TrustedRemoteQwenRawContractError,
):
    try:
        if type(value) is _plan_type:
            raw = _plan_canonical_bytes(value)
        elif type(value) is bytes:
            raw = value
        else:
            raise _error("action_time_plan_exact_type_or_bytes_required")
        plan = _plan_from_bytes(raw)
        canonical = _plan_canonical_bytes(plan)
        if raw != canonical:
            raise _error("action_time_plan_not_canonical")
        replay = _plan_from_bytes(canonical)
        data = _plan_to_dict(replay)
        if _dumps(data) != canonical:
            raise _error("action_time_plan_canonical_replay_invalid")
        return data, {"record_id": data["record_id"], "sha256": _plan_sha256(replay)}, canonical
    except _error:
        raise
    except Exception as exc:
        raise _error("action_time_plan_live_validation_failed") from exc


def _replay_plan_mapping(value, _dumps=_CANONICAL_DUMPS, _resolve=_resolve_plan, _error=TrustedRemoteQwenRawContractError):
    if not isinstance(value, Mapping):
        raise _error("action_time_plan_full_mapping_required")
    data, binding, _raw = _resolve(_dumps(dict(value)))
    return data, binding


def _plan_binding(value, expected, _keys_fn=_keys, _hex_fn=_hex64, _error=TrustedRemoteQwenRawContractError):
    data = _keys_fn(value, ("record_id", "sha256"), "plan_binding")
    result = {"record_id": _hex_fn(data["record_id"], "plan_binding_record_id"), "sha256": _hex_fn(data["sha256"], "plan_binding_sha256")}
    if result != expected:
        raise _error("action_time_plan_binding_invalid")
    return result


def _plan_case(data, case_id, _text_fn=_text, _cases=_REQUIRED_CASE_IDS, _copy=copy.deepcopy, _error=TrustedRemoteQwenRawContractError):
    case_id = _text_fn(case_id, "case_id")
    if case_id not in _cases:
        raise _error("case_id_not_approved")
    rows = data.get("cases")
    if not isinstance(rows, list) or tuple(row.get("case_id") if isinstance(row, Mapping) else None for row in rows) != _cases:
        raise _error("action_time_plan_case_inventory_invalid")
    selected = next(row for row in rows if row["case_id"] == case_id)
    return {"case_id": selected["case_id"], "d17_path": selected["d17_path"], "provider_input": _copy(selected["provider_input"])}


def _profile_flags(value, _keys_fn=_keys, _false_fn=_false):
    keys = ("model_loaded", "provider_invoked", "network_invoked", "run_occurred")
    value = _keys_fn(value, keys, "execution_flags")
    return {key: _false_fn(value[key], f"execution_flags_{key}") for key in keys}


def _validate_profile(
    data, authority,
    _keys_fn=_keys, _replay_plan=_replay_plan_mapping, _plan_binding_fn=_plan_binding,
    _profile_flags_fn=_profile_flags, _valid_identity_fn=_valid_identity, _hex_fn=_hex64,
    _copy=copy.deepcopy, _schema=RUNTIME_PROFILE_SCHEMA, _state=LOCAL_CONTRACT_STATE,
    _error=TrustedRemoteQwenRawContractError,
):
    data = _keys_fn(data, _PROFILE_KEYS, "runtime_profile")
    if data["schema_version"] != _schema or data["contract_state"] != _state:
        raise _error("runtime_profile_schema_or_state_invalid")
    plan, binding = _replay_plan(data["action_time_plan"])
    _plan_binding_fn(data["plan_binding"], binding)
    model = plan["model_inventory"]
    if data["candidate_model"] != model["model_id"] or data["model_repository"] != model["repository"]:
        raise _error("runtime_profile_model_identity_invalid")
    if (data["model_inventory"] != model or data["runtime_inventory"] != plan["runtime_inventory"] or data["profile_selection"] != plan["profile_selection"] or data["precision"] != plan["precision"]):
        raise _error("runtime_profile_plan_binding_invalid")
    result = {
        "schema_version": _schema, "profile_id": _hex_fn(data["profile_id"], "profile_id"),
        "contract_state": _state, "action_time_plan": plan, "plan_binding": binding,
        "candidate_model": model["model_id"], "model_repository": model["repository"],
        "model_inventory": _copy(model), "runtime_inventory": _copy(plan["runtime_inventory"]),
        "profile_selection": _copy(plan["profile_selection"]), "precision": _copy(plan["precision"]),
        "execution_flags": _profile_flags_fn(data["execution_flags"]),
    }
    _valid_identity_fn(result, "profile_id", authority)
    return result


def _profile_binding(value, expected, _keys_fn=_keys, _hex_fn=_hex64, _error=TrustedRemoteQwenRawContractError):
    data = _keys_fn(value, ("profile_id", "sha256"), "profile_binding")
    actual = {"profile_id": _hex_fn(data["profile_id"], "profile_binding_profile_id"), "sha256": _hex_fn(data["sha256"], "profile_binding_sha256")}
    if actual != expected:
        raise _error("runtime_profile_binding_invalid")
    return actual


def _loader_boundary(value, _keys_fn=_keys, _error=TrustedRemoteQwenRawContractError):
    keys = ("backend", "callback", "command", "path", "network", "client", "model_load")
    data = _keys_fn(value, keys, "loader_boundary")
    expected = {"backend": "not_accepted", "callback": "not_accepted", "command": "not_accepted", "path": "not_accepted", "network": "not_accepted", "client": "not_accepted", "model_load": "not_executed"}
    if data != expected:
        raise _error("loader_boundary_invalid")
    return expected


def _validate_loader(
    data, authority, profile_type, profile_from_bytes, profile_to_dict, profile_sha,
    _keys_fn=_keys, _profile_binding_fn=_profile_binding, _loader_boundary_fn=_loader_boundary,
    _valid_identity_fn=_valid_identity, _mapping=Mapping, _hex_fn=_hex64, _dumps=_CANONICAL_DUMPS, _schema=LOADER_CONTRACT_SCHEMA,
    _state=LOCAL_CONTRACT_STATE, _error=TrustedRemoteQwenRawContractError,
):
    data = _keys_fn(data, _LOADER_KEYS, "loader_contract")
    if data["schema_version"] != _schema or data["contract_state"] != _state:
        raise _error("loader_contract_schema_or_state_invalid")
    if not isinstance(data["runtime_profile"], _mapping):
        raise _error("loader_runtime_profile_mapping_required")
    profile = profile_from_bytes(_dumps(dict(data["runtime_profile"])))
    profile_data = profile_to_dict(profile)
    binding = {"profile_id": profile_data["profile_id"], "sha256": profile_sha(profile)}
    _profile_binding_fn(data["profile_binding"], binding)
    result = {"schema_version": _schema, "loader_id": _hex_fn(data["loader_id"], "loader_id"), "contract_state": _state, "runtime_profile": profile_data, "profile_binding": binding, "loader_boundary": _loader_boundary_fn(data["loader_boundary"])}
    _valid_identity_fn(result, "loader_id", authority)
    return result


def _loader_binding(value, expected, _keys_fn=_keys, _hex_fn=_hex64, _error=TrustedRemoteQwenRawContractError):
    data = _keys_fn(value, ("loader_id", "sha256"), "loader_binding")
    actual = {"loader_id": _hex_fn(data["loader_id"], "loader_binding_loader_id"), "sha256": _hex_fn(data["sha256"], "loader_binding_sha256")}
    if actual != expected:
        raise _error("loader_contract_binding_invalid")
    return actual


def _case_binding(value, expected, _keys_fn=_keys, _text_fn=_text, _hex_fn=_hex64, _positive_fn=_positive_int, _error=TrustedRemoteQwenRawContractError):
    data = _keys_fn(value, ("case_id", "d17_path", "provider_input"), "case_binding")
    provider = _keys_fn(data["provider_input"], ("input_id", "sha256", "byte_length"), "case_provider_input")
    normalized = {"case_id": _text_fn(data["case_id"], "case_binding_case_id"), "d17_path": _text_fn(data["d17_path"], "case_binding_d17_path"), "provider_input": {"input_id": _text_fn(provider["input_id"], "case_binding_input_id"), "sha256": _hex_fn(provider["sha256"], "case_binding_input_sha256"), "byte_length": _positive_fn(provider["byte_length"], "case_binding_input_byte_length")}}
    if normalized != expected:
        raise _error("case_request_or_d17_projection_binding_invalid")
    return normalized


def _provider_identity(value, profile, _keys_fn=_keys, _branch_state="not_routable_slice_1_local_capture", _provenance=PROVENANCE_NOT_ATTESTED, _error=TrustedRemoteQwenRawContractError):
    keys = ("provider_branch_state", "candidate_model", "model_repository", "provenance_state")
    data = _keys_fn(value, keys, "provider_identity")
    expected = {"provider_branch_state": _branch_state, "candidate_model": profile["candidate_model"], "model_repository": profile["model_repository"], "provenance_state": _provenance}
    if data != expected:
        raise _error("provider_identity_or_provenance_invalid")
    return expected


def _raw_payload(
    value,
    _keys_fn=_keys, _text_fn=_text, _positive_fn=_positive_int, _hex_fn=_hex64,
    _b64decode=base64.b64decode, _b64encode=base64.b64encode, _raw_from_bytes=_RAW_FROM_BYTES,
    _raw_to_dict=_RAW_TO_DICT, _sha=hashlib.sha256, _copy=copy.deepcopy,
    _error=TrustedRemoteQwenRawContractError,
):
    data = _keys_fn(value, ("encoding", "base64", "byte_length", "sha256", "provider_raw_response"), "raw_response")
    if data["encoding"] != "base64":
        raise _error("raw_response_encoding_invalid")
    encoded = _text_fn(data["base64"], "raw_response_base64")
    try:
        raw = _b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise _error("raw_response_base64_invalid") from exc
    if encoded != _b64encode(raw).decode("ascii"):
        raise _error("raw_response_base64_not_canonical")
    length = _positive_fn(data["byte_length"], "raw_response_byte_length")
    digest = _hex_fn(data["sha256"], "raw_response_sha256")
    if len(raw) != length or _sha(raw).hexdigest() != digest:
        raise _error("raw_response_identity_invalid")
    try:
        provider_raw = _raw_from_bytes(raw)
        if _raw_to_dict(provider_raw) != data["provider_raw_response"]:
            raise _error("provider_raw_response_binding_invalid")
    except _error:
        raise
    except Exception as exc:
        raise _error("provider_raw_response_parser_shape_invalid") from exc
    return {"encoding": "base64", "base64": encoded, "byte_length": length, "sha256": digest, "provider_raw_response": _copy(data["provider_raw_response"])}, raw


def _status(value, _keys_fn=_keys, _false_fn=_false, _capture_state=LOCAL_CAPTURE_STATE, _provenance=PROVENANCE_NOT_ATTESTED, _provider_state="not_invoked", _zero=0, _error=TrustedRemoteQwenRawContractError):
    keys = ("capture_state", "real_provider_provenance", "provider_invocation_state", "provider_calls_after_first_complete_raw", "model_repair_call_count", "model_loaded", "network_invoked", "run_occurred")
    data = _keys_fn(value, keys, "raw_status")
    expected = {"capture_state": _capture_state, "real_provider_provenance": _provenance, "provider_invocation_state": _provider_state, "provider_calls_after_first_complete_raw": _zero, "model_repair_call_count": _zero, "model_loaded": False, "network_invoked": False, "run_occurred": False}
    if (data["capture_state"] != expected["capture_state"] or data["real_provider_provenance"] != expected["real_provider_provenance"] or data["provider_invocation_state"] != expected["provider_invocation_state"] or type(data["provider_calls_after_first_complete_raw"]) is not int or data["provider_calls_after_first_complete_raw"] != _zero or type(data["model_repair_call_count"]) is not int or data["model_repair_call_count"] != _zero):
        raise _error("raw_status_invalid")
    for key in ("model_loaded", "network_invoked", "run_occurred"):
        _false_fn(data[key], f"raw_status_{key}")
    return expected


def _validate_raw_record(
    data, authority, profile_type, loader_type, profile_from_bytes, profile_to_dict, profile_sha, loader_from_bytes, loader_to_dict, loader_sha,
    _keys_fn=_keys, _replay_plan=_replay_plan_mapping, _plan_binding_fn=_plan_binding,
    _profile_binding_fn=_profile_binding, _loader_binding_fn=_loader_binding,
    _plan_case_fn=_plan_case, _case_binding_fn=_case_binding,
    _provider_identity_fn=_provider_identity, _raw_payload_fn=_raw_payload,
    _status_fn=_status, _valid_identity_fn=_valid_identity, _mapping=Mapping, _hex_fn=_hex64, _dumps=_CANONICAL_DUMPS,
    _schema=RAW_RESPONSE_RECORD_SCHEMA, _state=LOCAL_CONTRACT_STATE,
    _error=TrustedRemoteQwenRawContractError,
):
    data = _keys_fn(data, _RAW_KEYS, "raw_response_record")
    if data["schema_version"] != _schema or data["contract_state"] != _state:
        raise _error("raw_response_record_schema_or_state_invalid")
    plan, plan_binding = _replay_plan(data["action_time_plan"])
    _plan_binding_fn(data["plan_binding"], plan_binding)
    if not isinstance(data["runtime_profile"], Mapping):
        raise _error("raw_runtime_profile_mapping_required")
    profile = profile_from_bytes(_dumps(dict(data["runtime_profile"])))
    profile_data = profile_to_dict(profile)
    profile_binding = {"profile_id": profile_data["profile_id"], "sha256": profile_sha(profile)}
    _profile_binding_fn(data["profile_binding"], profile_binding)
    if profile_data["action_time_plan"] != plan or profile_data["plan_binding"] != plan_binding:
        raise _error("raw_runtime_profile_plan_cross_binding_invalid")
    if not isinstance(data["loader_contract"], _mapping):
        raise _error("raw_loader_contract_mapping_required")
    loader = loader_from_bytes(_dumps(dict(data["loader_contract"])))
    loader_data = loader_to_dict(loader)
    loader_binding = {"loader_id": loader_data["loader_id"], "sha256": loader_sha(loader)}
    _loader_binding_fn(data["loader_binding"], loader_binding)
    if loader_data["runtime_profile"] != profile_data or loader_data["profile_binding"] != profile_binding:
        raise _error("raw_loader_profile_cross_binding_invalid")
    if not isinstance(data["case_binding"], _mapping):
        raise _error("case_binding_mapping_required")
    case_binding = _case_binding_fn(data["case_binding"], _plan_case_fn(plan, data["case_binding"].get("case_id")))
    provider_identity = _provider_identity_fn(data["provider_identity"], profile_data)
    raw_response, _raw = _raw_payload_fn(data["raw_response"])
    status = _status_fn(data["status"])
    result = {"schema_version": _schema, "record_id": _hex_fn(data["record_id"], "record_id"), "contract_state": _state, "action_time_plan": plan, "plan_binding": plan_binding, "runtime_profile": profile_data, "profile_binding": profile_binding, "loader_contract": loader_data, "loader_binding": loader_binding, "case_binding": case_binding, "provider_identity": provider_identity, "raw_response": raw_response, "status": status}
    _valid_identity_fn(result, "record_id", authority)
    return result


def _make_record_type(name, validator, dumps, loads, sha, _mapping=Mapping, _copy=copy.deepcopy, _error=TrustedRemoteQwenRawContractError):
    captured_type = None

    def _extract(instance):
        if type(instance) is not captured_type:
            raise _error("record_exact_type_required")
        raw = object.__getattribute__(instance, "_canonical")
        if type(raw) is not bytes:
            raise _error("record_private_storage_invalid")
        canonical = dumps(validator(loads(raw)))
        if raw != canonical:
            raise _error("record_private_storage_not_canonical")
        return canonical

    def _from_data(data):
        if not isinstance(data, _mapping):
            raise _error("record_not_mapping")
        canonical = dumps(validator(data))
        validator(loads(canonical))
        return captured_type(loads(canonical))

    def _from_canonical_bytes(raw):
        parsed = loads(raw)
        canonical = dumps(validator(parsed))
        if raw != canonical:
            raise _error("record_not_canonical")
        return _from_data(loads(canonical))

    class Record:
        __slots__ = ("_canonical",)

        def __init__(self, data):
            if not isinstance(data, _mapping):
                raise _error("record_not_mapping")
            canonical = dumps(validator(data))
            validator(loads(canonical))
            object.__setattr__(self, "_canonical", canonical)

        def __setattr__(self, _name, _value):
            raise AttributeError("trusted_remote_qwen_record_is_immutable")

        @classmethod
        def from_dict(cls, data):
            if cls is not captured_type:
                raise _error("record_subclass_forbidden")
            return _from_data(data)

        @classmethod
        def from_bytes(cls, raw):
            if cls is not captured_type:
                raise _error("record_subclass_forbidden")
            return _from_canonical_bytes(raw)

        def _bytes(self):
            return _extract(self)

        def to_dict(self):
            data = _copy(loads(_extract(self)))
            if type(data) is not dict:
                raise _error("record_copy_invalid")
            return data

        def canonical_bytes(self):
            return bytes(_extract(self))

        def sha256(self):
            return sha(_extract(self))

        def identity(self):
            data = _copy(loads(_extract(self)))
            return data["profile_id"] if "profile_id" in data else data["loader_id"] if "loader_id" in data else data["record_id"]

        def validate(self):
            return _from_canonical_bytes(_extract(self))

        def __eq__(self, other):
            return type(other) is captured_type and _extract(self) == _extract(other)

        def __hash__(self):
            return hash(_extract(self))

    Record.__name__ = name
    Record.__qualname__ = name
    captured_type = Record
    return Record


TrustedRemoteQwenRuntimeProfile = _make_record_type(
    "TrustedRemoteQwenRuntimeProfile",
    lambda data, _validator=_validate_profile, _captured=_CANONICAL_DUMPS: _validator(data, _captured),
    _CANONICAL_DUMPS, _CANONICAL_LOADS, _CANONICAL_SHA256,
)
TrustedRemoteQwenLoaderContract = _make_record_type(
    "TrustedRemoteQwenLoaderContract",
    lambda data, _validator=_validate_loader, _captured=_CANONICAL_DUMPS, _profile=TrustedRemoteQwenRuntimeProfile, _profile_from=TrustedRemoteQwenRuntimeProfile.from_bytes, _profile_dict=TrustedRemoteQwenRuntimeProfile.to_dict, _profile_sha=TrustedRemoteQwenRuntimeProfile.sha256: _validator(data, _captured, _profile, _profile_from, _profile_dict, _profile_sha),
    _CANONICAL_DUMPS, _CANONICAL_LOADS, _CANONICAL_SHA256,
)
TrustedRemoteQwenRawResponseRecord = _make_record_type(
    "TrustedRemoteQwenRawResponseRecord",
    lambda data, _validator=_validate_raw_record, _captured=_CANONICAL_DUMPS, _profile=TrustedRemoteQwenRuntimeProfile, _loader=TrustedRemoteQwenLoaderContract, _profile_from=TrustedRemoteQwenRuntimeProfile.from_bytes, _profile_dict=TrustedRemoteQwenRuntimeProfile.to_dict, _profile_sha=TrustedRemoteQwenRuntimeProfile.sha256, _loader_from=TrustedRemoteQwenLoaderContract.from_bytes, _loader_dict=TrustedRemoteQwenLoaderContract.to_dict, _loader_sha=TrustedRemoteQwenLoaderContract.sha256: _validator(data, _captured, _profile, _loader, _profile_from, _profile_dict, _profile_sha, _loader_from, _loader_dict, _loader_sha),
    _CANONICAL_DUMPS, _CANONICAL_LOADS, _CANONICAL_SHA256,
)

def _create_trusted_remote_qwen_runtime_profile_impl(action_time_plan, _resolve_plan_fn=_resolve_plan, _identified_fn=_identified, _record_from_dict=TrustedRemoteQwenRuntimeProfile.from_dict, _dumps=_CANONICAL_DUMPS, _sha=_CANONICAL_SHA256, _copy=copy.deepcopy, _schema=RUNTIME_PROFILE_SCHEMA, _state=LOCAL_CONTRACT_STATE):
    plan, binding, _raw = _resolve_plan_fn(action_time_plan)
    data = {"schema_version": _schema, "profile_id": "0" * 64, "contract_state": _state, "action_time_plan": plan, "plan_binding": binding, "candidate_model": plan["model_inventory"]["model_id"], "model_repository": plan["model_inventory"]["repository"], "model_inventory": _copy(plan["model_inventory"]), "runtime_inventory": _copy(plan["runtime_inventory"]), "profile_selection": _copy(plan["profile_selection"]), "precision": _copy(plan["precision"]), "execution_flags": {"model_loaded": False, "provider_invoked": False, "network_invoked": False, "run_occurred": False}}
    return _record_from_dict(_identified_fn(data, _dumps))


def _create_trusted_remote_qwen_loader_contract_impl(runtime_profile, _profile_type=TrustedRemoteQwenRuntimeProfile, _profile_from=TrustedRemoteQwenRuntimeProfile.from_bytes, _profile_canonical=TrustedRemoteQwenRuntimeProfile.canonical_bytes, _profile_dict=TrustedRemoteQwenRuntimeProfile.to_dict, _profile_sha=TrustedRemoteQwenRuntimeProfile.sha256, _identified_fn=_identified, _record_from_dict=TrustedRemoteQwenLoaderContract.from_dict, _dumps=_CANONICAL_DUMPS, _schema=LOADER_CONTRACT_SCHEMA, _state=LOCAL_CONTRACT_STATE, _error=TrustedRemoteQwenRawContractError):
    try:
        if type(runtime_profile) is _profile_type:
            profile = _profile_from(_profile_canonical(runtime_profile))
        elif type(runtime_profile) is bytes:
            profile = _profile_from(runtime_profile)
        else:
            raise _error("runtime_profile_exact_type_or_bytes_required")
        profile_data = _profile_dict(profile)
        data = {"schema_version": _schema, "loader_id": "0" * 64, "contract_state": _state, "runtime_profile": profile_data, "profile_binding": {"profile_id": profile_data["profile_id"], "sha256": _profile_sha(profile)}, "loader_boundary": {"backend": "not_accepted", "callback": "not_accepted", "command": "not_accepted", "path": "not_accepted", "network": "not_accepted", "client": "not_accepted", "model_load": "not_executed"}}
        return _record_from_dict(_identified_fn(data, _dumps))
    except _error:
        raise
    except Exception as exc:
        raise _error("runtime_profile_live_validation_failed") from exc


def _capture_local_contract_candidate_raw_response_impl(action_time_plan, runtime_profile, loader_contract, case_id, raw_bytes, _resolve_plan_fn=_resolve_plan, _profile_type=TrustedRemoteQwenRuntimeProfile, _profile_from=TrustedRemoteQwenRuntimeProfile.from_bytes, _profile_canonical=TrustedRemoteQwenRuntimeProfile.canonical_bytes, _profile_dict=TrustedRemoteQwenRuntimeProfile.to_dict, _profile_sha=TrustedRemoteQwenRuntimeProfile.sha256, _loader_type=TrustedRemoteQwenLoaderContract, _loader_from=TrustedRemoteQwenLoaderContract.from_bytes, _loader_canonical=TrustedRemoteQwenLoaderContract.canonical_bytes, _loader_dict=TrustedRemoteQwenLoaderContract.to_dict, _loader_sha=TrustedRemoteQwenLoaderContract.sha256, _raw_from_bytes=_RAW_FROM_BYTES, _raw_to_dict=_RAW_TO_DICT, _plan_case_fn=_plan_case, _identified_fn=_identified, _record_from_dict=TrustedRemoteQwenRawResponseRecord.from_dict, _dumps=_CANONICAL_DUMPS, _b64encode=base64.b64encode, _sha=hashlib.sha256, _schema=RAW_RESPONSE_RECORD_SCHEMA, _state=LOCAL_CONTRACT_STATE, _branch_state="not_routable_slice_1_local_capture", _provenance=PROVENANCE_NOT_ATTESTED, _capture_state=LOCAL_CAPTURE_STATE, _provider_state="not_invoked", _zero=0, _error=TrustedRemoteQwenRawContractError):
    """Capture supplied parser-shaped bytes with explicit non-attested provenance only."""
    try:
        if type(raw_bytes) is not bytes:
            raise _error("raw_response_bytes_required")
        plan, binding, _raw = _resolve_plan_fn(action_time_plan)
        if type(runtime_profile) is _profile_type:
            profile = _profile_from(_profile_canonical(runtime_profile))
        elif type(runtime_profile) is bytes:
            profile = _profile_from(runtime_profile)
        else:
            raise _error("runtime_profile_exact_type_or_bytes_required")
        profile_data = _profile_dict(profile)
        if profile_data["action_time_plan"] != plan or profile_data["plan_binding"] != binding:
            raise _error("runtime_profile_plan_cross_binding_invalid")
        if type(loader_contract) is _loader_type:
            loader = _loader_from(_loader_canonical(loader_contract))
        elif type(loader_contract) is bytes:
            loader = _loader_from(loader_contract)
        else:
            raise _error("loader_contract_exact_type_or_bytes_required")
        loader_data = _loader_dict(loader)
        if loader_data["runtime_profile"] != profile_data:
            raise _error("loader_contract_profile_cross_binding_invalid")
        case = _plan_case_fn(plan, case_id)
        provider_raw = _raw_from_bytes(raw_bytes)
        raw_data = _raw_to_dict(provider_raw)
        data = {"schema_version": _schema, "record_id": "0" * 64, "contract_state": _state, "action_time_plan": plan, "plan_binding": binding, "runtime_profile": profile_data, "profile_binding": {"profile_id": profile_data["profile_id"], "sha256": _profile_sha(profile)}, "loader_contract": loader_data, "loader_binding": {"loader_id": loader_data["loader_id"], "sha256": _loader_sha(loader)}, "case_binding": case, "provider_identity": {"provider_branch_state": _branch_state, "candidate_model": profile_data["candidate_model"], "model_repository": profile_data["model_repository"], "provenance_state": _provenance}, "raw_response": {"encoding": "base64", "base64": _b64encode(raw_bytes).decode("ascii"), "byte_length": len(raw_bytes), "sha256": _sha(raw_bytes).hexdigest(), "provider_raw_response": raw_data}, "status": {"capture_state": _capture_state, "real_provider_provenance": _provenance, "provider_invocation_state": _provider_state, "provider_calls_after_first_complete_raw": _zero, "model_repair_call_count": _zero, "model_loaded": False, "network_invoked": False, "run_occurred": False}}
        return _record_from_dict(_identified_fn(data, _dumps))
    except _error:
        raise
    except Exception as exc:
        raise _error("local_contract_raw_capture_failed") from exc


def _validate_trusted_remote_qwen_raw_response_binding_impl(
    record, action_time_plan, expected_case_id,
    _record_type=TrustedRemoteQwenRawResponseRecord,
    _record_from=TrustedRemoteQwenRawResponseRecord.from_bytes,
    _record_canonical=TrustedRemoteQwenRawResponseRecord.canonical_bytes,
    _record_dict=TrustedRemoteQwenRawResponseRecord.to_dict,
    _resolve_plan_fn=_resolve_plan, _plan_case_fn=_plan_case,
    _error=TrustedRemoteQwenRawContractError,
):
    """Live-validate an exact record against the caller's fixed case/run context."""
    try:
        if type(record) is _record_type:
            replay = _record_from(_record_canonical(record))
        elif type(record) is bytes:
            replay = _record_from(record)
        else:
            raise _error("raw_response_record_exact_type_or_bytes_required")
        plan, binding, _raw = _resolve_plan_fn(action_time_plan)
        data = _record_dict(replay)
        if data["action_time_plan"] != plan or data["plan_binding"] != binding:
            raise _error("raw_record_action_time_plan_cross_binding_invalid")
        expected = _plan_case_fn(plan, expected_case_id)
        if data["case_binding"] != expected:
            raise _error("raw_record_case_request_or_d17_projection_cross_binding_invalid")
        return replay
    except _error:
        raise
    except Exception as exc:
        raise _error("raw_record_binding_live_validation_failed") from exc


def _replay_trusted_remote_qwen_raw_response_impl(record, _record_type=TrustedRemoteQwenRawResponseRecord, _record_from=TrustedRemoteQwenRawResponseRecord.from_bytes, _record_canonical=TrustedRemoteQwenRawResponseRecord.canonical_bytes, _record_dict=TrustedRemoteQwenRawResponseRecord.to_dict, _raw_from_bytes=_RAW_FROM_BYTES, _b64decode=base64.b64decode, _error=TrustedRemoteQwenRawContractError):
    """Reconstruct and parser-validate exact bytes without provider/model execution."""
    try:
        if type(record) is _record_type:
            replay = _record_from(_record_canonical(record))
        elif type(record) is bytes:
            replay = _record_from(record)
        else:
            raise _error("raw_response_record_exact_type_or_bytes_required")
        data = _record_dict(replay)
        raw = _b64decode(data["raw_response"]["base64"].encode("ascii"), validate=True)
        return _raw_from_bytes(raw)
    except _error:
        raise
    except Exception as exc:
        raise _error("raw_response_replay_failed") from exc



def _runtime_profile_public_factory(impl):
    def public(action_time_plan):
        return impl(action_time_plan)
    public.__name__ = "create_trusted_remote_qwen_runtime_profile"
    public.__qualname__ = public.__name__
    return public


def _loader_public_factory(impl):
    def public(runtime_profile):
        return impl(runtime_profile)
    public.__name__ = "create_trusted_remote_qwen_loader_contract"
    public.__qualname__ = public.__name__
    return public


def _capture_public_factory(impl):
    def public(action_time_plan, runtime_profile, loader_contract, case_id, raw_bytes):
        return impl(action_time_plan, runtime_profile, loader_contract, case_id, raw_bytes)
    public.__name__ = "capture_local_contract_candidate_raw_response"
    public.__qualname__ = public.__name__
    return public


def _binding_public_factory(impl):
    def public(record, action_time_plan, expected_case_id):
        return impl(record, action_time_plan, expected_case_id)
    public.__name__ = "validate_trusted_remote_qwen_raw_response_binding"
    public.__qualname__ = public.__name__
    return public


def _replay_public_factory(impl):
    def public(record):
        return impl(record)
    public.__name__ = "replay_trusted_remote_qwen_raw_response"
    public.__qualname__ = public.__name__
    return public


create_trusted_remote_qwen_runtime_profile = _runtime_profile_public_factory(
    _create_trusted_remote_qwen_runtime_profile_impl
)
create_trusted_remote_qwen_loader_contract = _loader_public_factory(
    _create_trusted_remote_qwen_loader_contract_impl
)
capture_local_contract_candidate_raw_response = _capture_public_factory(
    _capture_local_contract_candidate_raw_response_impl
)
validate_trusted_remote_qwen_raw_response_binding = _binding_public_factory(
    _validate_trusted_remote_qwen_raw_response_binding_impl
)
replay_trusted_remote_qwen_raw_response = _replay_public_factory(
    _replay_trusted_remote_qwen_raw_response_impl
)
