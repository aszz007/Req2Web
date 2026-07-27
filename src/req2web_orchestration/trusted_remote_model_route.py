"""Trusted-remote source-exclusive model-route foundation; local/no-action Slice 2.

The module defines the v2/wrapper contract only. It has no provider backend,
model loader, network, filesystem, result-package, repair, fallback, or delivery
execution surface.
"""
from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
from types import CodeType, FunctionType, MappingProxyType, MethodType
import hashlib
import json
import re
from collections.abc import Mapping

from req2web_evaluation import frozen_g0_reference as _g0_module
from req2web_orchestration import model_route as _v1_route_module
from req2web_provider import d17_audit as _audit_module
from req2web_provider import d17_input_view as _selected_module
from req2web_provider import d17_manifest as _manifest_module
from req2web_provider import d17_serializer as _request_module
from req2web_provider import local_qwen_provider as _preparation_module
from req2web_provider import semantic_candidate as _semantic_module
from req2web_runtime import autodl_trusted_remote_qwen_raw as _raw_module
from req2web_runtime import autodl_trusted_remote_records as _records_module


REAL_RUN_ENVELOPE_SCHEMA = "req2web.orchestration.trusted_remote_real_run_envelope.v2"
MODEL_ROUTE_OUTCOME_SCHEMA = "req2web.orchestration.trusted_remote_model_route_outcome.v2"
TEST_ONLY_REPLAY_VECTOR_SCHEMA = "req2web.orchestration.test_only_non_attested_replay_vector.v1"
TEST_ONLY_REPLAY_RESULT_SCHEMA = "req2web.orchestration.test_only_non_attested_replay_result.v1"

_UNTRUSTED_SOURCE_STATE = "untrusted_slice_1_local_candidate"
_UNTRUSTED_ATTESTATION_STATE = "not_verified_real_provider"
_REAL_AUTHORITY_UNAVAILABLE = "real_provider_authority_unavailable"
_SCRIPTED_BRANCH = "scripted_local_fixture"
_REAL_BRANCH = "trusted_remote_local_qwen"
_FAILURE_BRANCH = "failure"
_SCRIPTED_DISPOSITION = "scripted_fixture_assembled"
_REAL_DISPOSITION = "real_provider_assembled"
_REAL_ATTESTATION_STATE = "real_provider_attested"
_REAL_SOURCE_KIND = "verified_real_provider"
_FAILURE_DISPOSITION = "fail_closed"
_TEST_ONLY_SUCCESS = "semantic_replay_structurally_valid_test_only"
_TEST_ONLY_PARSE_FAILURE = "semantic_replay_parse_failed_test_only"
_TEST_ONLY_ASSEMBLY_FAILURE = "semantic_replay_assembly_failed_test_only"
_TEST_ONLY_BINDING_FAILURE = "test_only_binding_failed"
_REQUIRED_CASE_IDS = ("path3-commerce-checkout", "path3-media-analysis")
_ENVELOPE_PREFIX = "trusted-remote-real-run-envelope-"
_OUTCOME_PREFIX = "trusted-remote-model-route-outcome-"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_ENVELOPE_KEYS = (
    "schema_version", "envelope_id", "source_state", "attestation_state",
    "future_issuer", "action_time_plan", "plan_binding", "case_binding",
    "runtime_profile", "loader_contract", "raw_record", "raw_record_binding",
    "raw_response", "frozen_g0_binding", "execution_state",
)
_OUTCOME_KEYS = (
    "schema_version", "outcome_id", "source_branch", "disposition",
    "attestation_state", "case_binding", "frozen_g0_binding", "source_binding",
    "failure",
)
_FUTURE_ISSUER = {
    "issuer_schema": "req2web.orchestration.trusted_remote_real_provider_writer.v1",
    "issuer_identity": "unavailable_slice_2_future_writer_only",
    "issuer_state": "production_authority_unavailable",
}
_ENVELOPE_EXECUTION_STATE = {
    "provider_execution": "not_executed_slice_2",
    "model_loaded": False,
    "network_invoked": False,
    "run_occurred": False,
    "provider_calls_after_first_complete_raw": 0,
    "model_repair_call_count": 0,
}

# Definition-time captures. Public routes close over these concrete authorities.
_ACTION_PLAN_TYPE = _records_module.TrustedRemoteActionTimePlan
_ACTION_PLAN_FROM_BYTES = _ACTION_PLAN_TYPE.from_bytes
_RAW_RECORD_TYPE = _raw_module.TrustedRemoteQwenRawResponseRecord
_RAW_RECORD_FROM_BYTES = _RAW_RECORD_TYPE.from_bytes
_RAW_RECORD_BIND = _raw_module.validate_trusted_remote_qwen_raw_response_binding
_RAW_RECORD_REPLAY = _raw_module.replay_trusted_remote_qwen_raw_response

_FROZEN_G0_TYPE = _g0_module.FrozenG0PackageReference
_FROZEN_G0_VALIDATE = _FROZEN_G0_TYPE.validate
_FROZEN_G0_FROM_VERIFIED = _FROZEN_G0_TYPE.from_verified_package
_FROZEN_G0_ENTRY_TYPE = _g0_module.FrozenG0InventoryEntry
_FROZEN_G0_DECLARATIONS = _g0_module._declarations_dict()

_V1_OUTCOME_TYPE = _v1_route_module.ModelRouteOutcome
_V1_VALIDATE_SERIALIZED = _v1_route_module.validate_serialized_model_route_outcome
_V1_CANONICAL_BYTES = _V1_OUTCOME_TYPE.canonical_bytes
_V1_VALIDATE = _V1_OUTCOME_TYPE.validate

_MANIFEST_VALIDATE = _manifest_module.D17Path3TierAManifest.validate
_SELECTED_VALIDATE_AGAINST = _selected_module.D17Path3SelectedInput.validate_against
_REQUEST_VALIDATE_AGAINST = _request_module.D17Path3LocalRequestArtifact.validate_against
_AUDIT_VALIDATE_AGAINST = _audit_module.D17Path3TierAPreInvocationAuditRecord.validate_against
_PREPARATION_VALIDATE_AGAINST = _preparation_module.LocalQwenProviderPreparationRecord.validate_against

# Capture the canonical assembler's functional authority, not its mutable module
# globals or a runtime class-method lookup.
_CANONICAL_ASSEMBLER_TYPE = _semantic_module.CanonicalPageSpecAssembler
_PARSE_RAW_RESPONSE = _semantic_module.parse_provider_raw_response
_VALIDATE_LOCAL_BINDINGS = _semantic_module._validate_local_bindings
_VALIDATE_CANDIDATE_CONTEXT = _semantic_module._validate_candidate_against_context
_ASSEMBLE_PAGE_SPEC = _semantic_module._assemble_page_spec
_BUILD_ASSEMBLY_REPORT = _semantic_module._build_report
_PAGE_SPEC_VALIDATE = _semantic_module.PageSpec.validate
_ASSEMBLED_TYPE = _semantic_module.AssembledPageSpec
_ASSEMBLED_VALIDATE = _ASSEMBLED_TYPE.validate


class TrustedRemoteModelRouteError(ValueError):
    """A Slice 2 source-exclusive route boundary failed closed."""


def _pairs(pairs, _error=TrustedRemoteModelRouteError):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _error("duplicate_json_key")
        result[key] = value
    return result


def _dumps(value, _json_dumps=json.dumps, _error=TrustedRemoteModelRouteError):
    try:
        return _json_dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _error("canonical_json_invalid") from exc


def _loads(raw, _json_loads=json.loads, _pairs_fn=_pairs, _error=TrustedRemoteModelRouteError):
    if type(raw) is not bytes:
        raise _error("canonical_bytes_required")

    def reject_constant(_value):
        raise _error("non_finite_json_forbidden")

    try:
        return _json_loads(raw.decode("utf-8"), object_pairs_hook=_pairs_fn, parse_constant=reject_constant)
    except UnicodeDecodeError as exc:
        raise _error("canonical_bytes_not_utf8") from exc
    except _error:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise _error("canonical_bytes_not_json") from exc


def _sha256(raw, _sha=hashlib.sha256):
    if type(raw) is not bytes:
        raise TrustedRemoteModelRouteError("sha256_bytes_required")
    return _sha(raw).hexdigest()


def _keys(value, expected, label, _mapping=Mapping, _error=TrustedRemoteModelRouteError):
    if not isinstance(value, _mapping) or len(value) != len(expected) or set(value) != set(expected):
        raise _error(f"{label}_exact_keys_invalid")
    return value


def _text(value, label, _error=TrustedRemoteModelRouteError):
    if type(value) is not str or not value or value != value.strip():
        raise _error(f"{label}_not_nonempty_text")
    return value


def _hex64(value, label, _text_fn=_text, _pattern=_HEX64, _error=TrustedRemoteModelRouteError):
    value = _text_fn(value, label)
    if not _pattern.fullmatch(value):
        raise _error(f"{label}_invalid_sha256")
    return value


def _bool_false(value, label, _error=TrustedRemoteModelRouteError):
    if type(value) is not bool or value is not False:
        raise _error(f"{label}_must_be_false")
    return False


def _nonnegative(value, label, _error=TrustedRemoteModelRouteError):
    if type(value) is not int or value < 0:
        raise _error(f"{label}_not_nonnegative_integer")
    return value


def _strict_b64_decode(value, label, _b64decode=base64.b64decode, _b64encode=base64.b64encode, _error=TrustedRemoteModelRouteError):
    value = _text(value, label)
    try:
        raw = _b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise _error(f"{label}_invalid_base64") from exc
    if _b64encode(raw).decode("ascii") != value:
        raise _error(f"{label}_not_canonical_base64")
    return raw


def _copy_tree(value, _mapping=Mapping):
    """Return a recursively detached plain-data copy without mutable aliases."""
    if isinstance(value, _mapping):
        return {_copy_tree(key): _copy_tree(item) for key, item in value.items()}
    if type(value) is list:
        return [_copy_tree(item) for item in value]
    if type(value) is tuple:
        return tuple(_copy_tree(item) for item in value)
    if type(value) is set:
        return {_copy_tree(item) for item in value}
    if type(value) is frozenset:
        return frozenset(_copy_tree(item) for item in value)
    if type(value) is bytearray:
        return bytes(value)
    return value


def _identified(data, key, prefix, _copy=_copy_tree, _dumps_fn=_dumps, _sha_fn=_sha256):
    body = _copy(dict(data))
    body[key] = prefix + "0" * 64
    body[key] = prefix + _sha_fn(_dumps_fn(body))
    return body


def _validate_identified(data, key, prefix, _copy=_copy_tree, _dumps_fn=_dumps, _sha_fn=_sha256, _error=TrustedRemoteModelRouteError):
    actual = _text(data[key], key)
    if not actual.startswith(prefix) or not _HEX64.fullmatch(actual[len(prefix):]):
        raise _error(f"{key}_invalid")
    body = _copy(dict(data))
    body[key] = prefix + "0" * 64
    if prefix + _sha_fn(_dumps_fn(body)) != actual:
        raise _error(f"{key}_invalid")


def _extract_canonical(instance, expected_type, label, _error=TrustedRemoteModelRouteError):
    if type(instance) is not expected_type:
        raise _error(f"{label}_exact_type_required")
    raw = object.__getattribute__(instance, "_canonical")
    if type(raw) is not bytes:
        raise _error(f"{label}_private_storage_invalid")
    return bytes(raw)

def _replay_action_plan(value, _plan_type=_ACTION_PLAN_TYPE, _plan_from_bytes=_ACTION_PLAN_FROM_BYTES, _dumps_fn=_dumps, _loads_fn=_loads, _error=TrustedRemoteModelRouteError):
    try:
        if type(value) is _plan_type:
            raw = _extract_canonical(value, _plan_type, "action_time_plan")
        elif type(value) is bytes:
            raw = value
        elif isinstance(value, Mapping):
            raw = _dumps_fn(dict(value))
        else:
            raise _error("action_time_plan_exact_type_bytes_or_mapping_required")
        replay = _plan_from_bytes(raw)
        canonical = _extract_canonical(replay, _plan_type, "action_time_plan")
        if type(value) is bytes and raw != canonical:
            raise _error("action_time_plan_not_canonical")
        data = _loads_fn(canonical)
        if type(data) is not dict:
            raise _error("action_time_plan_replay_invalid")
        return data, canonical
    except _error:
        raise
    except Exception as exc:
        raise _error("action_time_plan_replay_invalid") from exc


def _plan_binding(plan, plan_bytes, _sha_fn=_sha256):
    return {"record_id": _text(plan["record_id"], "plan_record_id"), "sha256": _sha_fn(plan_bytes)}


def _case_from_plan(plan, case_id, _text_fn=_text, _error=TrustedRemoteModelRouteError):
    case_id = _text_fn(case_id, "case_id")
    if case_id not in _REQUIRED_CASE_IDS:
        raise _error("case_id_not_approved")
    rows = plan.get("cases")
    if not isinstance(rows, list) or tuple(item.get("case_id") if isinstance(item, Mapping) else None for item in rows) != _REQUIRED_CASE_IDS:
        raise _error("action_time_plan_case_inventory_invalid")
    selected = next(item for item in rows if item["case_id"] == case_id)
    if selected.get("d17_path") != "path_3":
        raise _error("d17_path_invalid")
    provider = _keys(selected.get("provider_input"), ("input_id", "sha256", "byte_length"), "provider_input")
    frozen = _keys(selected.get("frozen_g0"), ("package_id", "sha256"), "frozen_g0")
    if type(provider["byte_length"]) is not int or provider["byte_length"] < 1:
        raise _error("provider_input_byte_length_invalid")
    return {
        "case_id": case_id,
        "d17_path": "path_3",
        "provider_input": {
            "input_id": _text(provider["input_id"], "provider_input_id"),
            "sha256": _hex64(provider["sha256"], "provider_input_sha256"),
            "byte_length": provider["byte_length"],
        },
        "frozen_g0": {
            "package_id": _text(frozen["package_id"], "frozen_g0_package_id"),
            "sha256": _hex64(frozen["sha256"], "frozen_g0_sha256"),
        },
    }


def _replay_raw_record(value, plan_data, expected_case_id, _raw_type=_RAW_RECORD_TYPE, _raw_from=_RAW_RECORD_FROM_BYTES, _raw_bind=_RAW_RECORD_BIND, _loads_fn=_loads, _dumps_fn=_dumps, _sha_fn=_sha256, _error=TrustedRemoteModelRouteError):
    try:
        if type(value) is _raw_type:
            raw = _extract_canonical(value, _raw_type, "raw_record")
        elif type(value) is bytes:
            raw = value
        elif isinstance(value, Mapping):
            raw = _dumps_fn(dict(value))
        else:
            raise _error("raw_record_exact_type_bytes_or_mapping_required")
        record = _raw_from(raw)
        canonical = _extract_canonical(record, _raw_type, "raw_record")
        if type(value) is bytes and raw != canonical:
            raise _error("raw_record_not_canonical")
        _raw_bind(record, _dumps_fn(plan_data), expected_case_id)
        data = _loads_fn(canonical)
        if type(data) is not dict:
            raise _error("raw_record_replay_invalid")
        identity = {"record_id": _text(data["record_id"], "raw_record_id"), "sha256": _sha_fn(canonical)}
        return data, canonical, identity
    except _error:
        raise
    except Exception as exc:
        raise _error("raw_record_replay_invalid") from exc


def _reference_mapping_from_instance(value, _type=_FROZEN_G0_TYPE, _validate=_FROZEN_G0_VALIDATE, _entry_type=_FROZEN_G0_ENTRY_TYPE, _declarations=copy.deepcopy(_FROZEN_G0_DECLARATIONS), _copy=_copy_tree, _error=TrustedRemoteModelRouteError):
    if type(value) is not _type:
        raise _error("frozen_g0_reference_exact_type_required")
    try:
        _validate(value)
    except Exception as exc:
        raise _error("frozen_g0_reference_validation_failed") from exc
    inventory = object.__getattribute__(value, "inventory")
    if type(inventory) is not tuple:
        raise _error("frozen_g0_reference_inventory_invalid")
    rows = []
    for item in inventory:
        if type(item) is not _entry_type:
            raise _error("frozen_g0_reference_inventory_invalid")
        rows.append({
            "relative_path": object.__getattribute__(item, "relative_path"),
            "role": object.__getattribute__(item, "role"),
            "size": object.__getattribute__(item, "size"),
            "sha256": object.__getattribute__(item, "sha256"),
        })
    return {
        "schema_version": object.__getattribute__(value, "schema_version"),
        "reference_id": object.__getattribute__(value, "reference_id"),
        "package_schema_version": object.__getattribute__(value, "package_schema_version"),
        "package_id": object.__getattribute__(value, "package_id"),
        "page_id": object.__getattribute__(value, "page_id"),
        "context_schema_version": object.__getattribute__(value, "context_schema_version"),
        "context_id": object.__getattribute__(value, "context_id"),
        "context_sha256": object.__getattribute__(value, "context_sha256"),
        "guidance_schema_version": object.__getattribute__(value, "guidance_schema_version"),
        "guidance_bundle_id": object.__getattribute__(value, "guidance_bundle_id"),
        "guidance_sha256": object.__getattribute__(value, "guidance_sha256"),
        "package_manifest_sha256": object.__getattribute__(value, "package_manifest_sha256"),
        "inventory_tree_sha256": object.__getattribute__(value, "inventory_tree_sha256"),
        "declarations": _copy(_declarations),
        "inventory": rows,
    }


def _validate_reference_mapping(value, _copy=_copy_tree, _error=TrustedRemoteModelRouteError):
    expected = (
        "schema_version", "reference_id", "package_schema_version", "package_id", "page_id",
        "context_schema_version", "context_id", "context_sha256", "guidance_schema_version",
        "guidance_bundle_id", "guidance_sha256", "package_manifest_sha256",
        "inventory_tree_sha256", "declarations", "inventory",
    )
    data = _keys(value, expected, "frozen_g0_reference")
    for name in (
        "schema_version", "reference_id", "package_schema_version", "package_id", "page_id",
        "context_schema_version", "context_id", "guidance_schema_version", "guidance_bundle_id",
    ):
        _text(data[name], f"frozen_g0_reference_{name}")
    for name in ("context_sha256", "guidance_sha256", "package_manifest_sha256", "inventory_tree_sha256"):
        _hex64(data[name], f"frozen_g0_reference_{name}")
    if not isinstance(data["declarations"], Mapping) or not data["declarations"]:
        raise _error("frozen_g0_reference_declarations_invalid")
    if type(data["inventory"]) is not list or not data["inventory"]:
        raise _error("frozen_g0_reference_inventory_invalid")
    seen = set()
    rows = []
    for row in data["inventory"]:
        item = _keys(row, ("relative_path", "role", "size", "sha256"), "frozen_g0_inventory")
        path = _text(item["relative_path"], "frozen_g0_inventory_path")
        if path in seen or "\\" in path or path.startswith("/") or ".." in path.split("/"):
            raise _error("frozen_g0_reference_inventory_invalid")
        seen.add(path)
        if type(item["size"]) is not int or item["size"] < 0:
            raise _error("frozen_g0_reference_inventory_invalid")
        rows.append({"relative_path": path, "role": _text(item["role"], "frozen_g0_inventory_role"), "size": item["size"], "sha256": _hex64(item["sha256"], "frozen_g0_inventory_sha256")})
    return {key: _copy(data[key]) for key in expected[:-1]} | {"inventory": rows}


def _reference_binding(reference_data, case, _dumps_fn=_dumps, _sha_fn=_sha256, _copy=_copy_tree, _error=TrustedRemoteModelRouteError):
    reference = _validate_reference_mapping(reference_data)
    identity = _sha_fn(_dumps_fn(reference))
    frozen = case["frozen_g0"]
    if reference["package_id"] != frozen["package_id"] or identity != frozen["sha256"]:
        raise _error("same_case_frozen_g0_binding_invalid")
    return {"plan_frozen_g0": _copy(frozen), "reference": reference, "reference_sha256": identity}


def _validate_raw_local_candidate(data, case, _error=TrustedRemoteModelRouteError):
    expected_identity = {
        "provider_branch_state": "not_routable_slice_1_local_capture",
        "candidate_model": data["runtime_profile"]["candidate_model"],
        "model_repository": data["runtime_profile"]["model_repository"],
        "provenance_state": "not_attested_local_contract_candidate",
    }
    expected_status = {
        "capture_state": "local_contract_candidate_capture",
        "real_provider_provenance": "not_attested_local_contract_candidate",
        "provider_invocation_state": "not_invoked",
        "provider_calls_after_first_complete_raw": 0,
        "model_repair_call_count": 0,
        "model_loaded": False,
        "network_invoked": False,
        "run_occurred": False,
    }
    expected_case = {"case_id": case["case_id"], "d17_path": case["d17_path"], "provider_input": case["provider_input"]}
    if data.get("provider_identity") != expected_identity or data.get("status") != expected_status or data.get("case_binding") != expected_case:
        raise _error("slice_1_raw_source_or_case_invalid")


def _validate_execution_state(value, _keys_fn=_keys, _false_fn=_bool_false, _nonnegative_fn=_nonnegative, _state=copy.deepcopy(_ENVELOPE_EXECUTION_STATE), _copy=_copy_tree, _error=TrustedRemoteModelRouteError):
    data = _keys_fn(value, tuple(_state), "execution_state")
    if data["provider_execution"] != "not_executed_slice_2":
        raise _error("execution_state_invalid")
    _false_fn(data["model_loaded"], "execution_state_model_loaded")
    _false_fn(data["network_invoked"], "execution_state_network_invoked")
    _false_fn(data["run_occurred"], "execution_state_run_occurred")
    if _nonnegative_fn(data["provider_calls_after_first_complete_raw"], "execution_state_provider_calls") != 0 or _nonnegative_fn(data["model_repair_call_count"], "execution_state_repair_calls") != 0:
        raise _error("execution_state_invalid")
    return _copy(_state)

def _validate_envelope(data, _keys_fn=_keys, _replay_plan=_replay_action_plan, _plan_binding_fn=_plan_binding, _case_fn=_case_from_plan, _replay_raw=_replay_raw_record, _reference_binding_fn=_reference_binding, _validate_identity=_validate_identified, _schema=REAL_RUN_ENVELOPE_SCHEMA, _source=_UNTRUSTED_SOURCE_STATE, _attestation=_UNTRUSTED_ATTESTATION_STATE, _issuer=copy.deepcopy(_FUTURE_ISSUER), _prefix=_ENVELOPE_PREFIX, _execution=_validate_execution_state, _copy=_copy_tree, _error=TrustedRemoteModelRouteError):
    data = _keys_fn(data, _ENVELOPE_KEYS, "real_run_envelope")
    if data["schema_version"] != _schema or data["source_state"] != _source or data["attestation_state"] != _attestation or data["future_issuer"] != _copy(_issuer):
        raise _error("real_run_envelope_state_or_issuer_invalid")
    plan, plan_bytes = _replay_plan(data["action_time_plan"])
    binding = _plan_binding_fn(plan, plan_bytes)
    if data["plan_binding"] != binding:
        raise _error("real_run_envelope_plan_binding_invalid")
    if not isinstance(data["case_binding"], Mapping):
        raise _error("real_run_envelope_case_binding_invalid")
    case = _case_fn(plan, data["case_binding"].get("case_id"))
    expected_case = {"case_id": case["case_id"], "d17_path": case["d17_path"], "provider_input": case["provider_input"]}
    if data["case_binding"] != expected_case:
        raise _error("real_run_envelope_case_binding_invalid")
    raw, _raw_bytes, raw_binding = _replay_raw(data["raw_record"], plan, case["case_id"])
    if data["raw_record_binding"] != raw_binding:
        raise _error("real_run_envelope_raw_binding_invalid")
    _validate_raw_local_candidate(raw, case)
    for key in ("runtime_profile", "loader_contract", "raw_response"):
        if data[key] != raw[key]:
            raise _error(f"real_run_envelope_{key}_cross_binding_invalid")
    if not isinstance(data["frozen_g0_binding"], Mapping) or "reference" not in data["frozen_g0_binding"]:
        raise _error("real_run_envelope_frozen_g0_binding_invalid")
    frozen = _reference_binding_fn(data["frozen_g0_binding"]["reference"], case)
    if data["frozen_g0_binding"] != frozen:
        raise _error("real_run_envelope_frozen_g0_binding_invalid")
    result = {
        "schema_version": _schema,
        "envelope_id": _text(data["envelope_id"], "envelope_id"),
        "source_state": _source,
        "attestation_state": _attestation,
        "future_issuer": _copy(_issuer),
        "action_time_plan": plan,
        "plan_binding": binding,
        "case_binding": expected_case,
        "runtime_profile": _copy(raw["runtime_profile"]),
        "loader_contract": _copy(raw["loader_contract"]),
        "raw_record": raw,
        "raw_record_binding": raw_binding,
        "raw_response": _copy(raw["raw_response"]),
        "frozen_g0_binding": frozen,
        "execution_state": _execution(data["execution_state"]),
    }
    _validate_identity(result, "envelope_id", _prefix)
    return result


def _make_record_type(name, validator, _mapping=Mapping, _copy=_copy_tree, _dumps_fn=_dumps, _loads_fn=_loads, _sha_fn=_sha256, _error=TrustedRemoteModelRouteError):
    record_type = None

    def extract(instance):
        if type(instance) is not record_type:
            raise _error("record_exact_type_required")
        raw = object.__getattribute__(instance, "_canonical")
        if type(raw) is not bytes:
            raise _error("record_private_storage_invalid")
        canonical = _dumps_fn(validator(_loads_fn(raw)))
        if canonical != raw:
            raise _error("record_private_storage_not_canonical")
        return canonical

    def from_canonical(canonical):
        result = object.__new__(record_type)
        object.__setattr__(result, "_canonical", canonical)
        return result

    def from_data(data):
        if not isinstance(data, _mapping):
            raise _error("record_not_mapping")
        return from_canonical(_dumps_fn(validator(data)))

    def from_bytes(raw):
        parsed = _loads_fn(raw)
        canonical = _dumps_fn(validator(parsed))
        if canonical != raw:
            raise _error("record_not_canonical")
        return from_canonical(canonical)

    class Record:
        __slots__ = ("_canonical",)

        def __init__(self, data):
            if not isinstance(data, _mapping):
                raise _error("record_not_mapping")
            object.__setattr__(self, "_canonical", _dumps_fn(validator(data)))

        def __setattr__(self, _name, _value):
            raise AttributeError("trusted_remote_model_route_record_is_immutable")

        @classmethod
        def from_dict(cls, data):
            if cls is not record_type:
                raise _error("record_subclass_forbidden")
            return from_data(data)

        @classmethod
        def from_bytes(cls, raw):
            if cls is not record_type:
                raise _error("record_subclass_forbidden")
            return from_bytes(raw)

        def _bytes(self):
            return extract(self)

        def to_dict(self):
            return _copy(_loads_fn(extract(self)))

        def canonical_bytes(self):
            return bytes(extract(self))

        def sha256(self):
            return _sha_fn(extract(self))

        def identity(self):
            data = _loads_fn(extract(self))
            return data["envelope_id"] if "envelope_id" in data else data["outcome_id"]

        def validate(self):
            extract(self)
            return self

        def __eq__(self, other):
            return type(other) is record_type and extract(self) == extract(other)

        def __hash__(self):
            return hash(extract(self))

    Record.__name__ = name
    Record.__qualname__ = name
    record_type = Record
    return Record, extract, from_data, from_bytes


TrustedRemoteRealRunEnvelope, _ENVELOPE_EXTRACT, _ENVELOPE_FROM_DATA, _ENVELOPE_FROM_BYTES = _make_record_type(
    "TrustedRemoteRealRunEnvelope", _validate_envelope
)


# Slice 2 exports no verified-provider capability type. Slice 3 owns any future private writer capability.


def _replay_v1_scripted(serialized, _validate_serialized=_V1_VALIDATE_SERIALIZED, _canonical=_V1_CANONICAL_BYTES, _validate=_V1_VALIDATE, _error=TrustedRemoteModelRouteError):
    if type(serialized) is not bytes:
        raise _error("scripted_v1_outcome_bytes_required")
    try:
        outcome = _validate_serialized(serialized)
        _validate(outcome)
        canonical = _canonical(outcome)
    except Exception as exc:
        raise _error("scripted_v1_outcome_validation_failed") from exc
    if canonical != serialized:
        raise _error("scripted_v1_outcome_not_canonical")
    try:
        branch = object.__getattribute__(outcome, "execution_branch")
        disposition = object.__getattribute__(outcome, "disposition")
        failure = object.__getattribute__(outcome, "failure")
        fixture = object.__getattribute__(outcome, "scripted_local_fixture")
        assembled = object.__getattribute__(outcome, "assembled_page_spec")
        reference = object.__getattribute__(outcome, "frozen_g0_reference")
        artifacts = object.__getattribute__(outcome, "artifacts")
    except Exception as exc:
        raise _error("scripted_v1_outcome_storage_invalid") from exc
    if branch != _SCRIPTED_BRANCH or disposition != _SCRIPTED_DISPOSITION or failure is not None or fixture is None:
        raise _error("scripted_v1_outcome_not_source_exclusive_success")
    reference_data = _reference_mapping_from_instance(reference)
    if not isinstance(artifacts, Mapping) or artifacts.get("assembled_page_id") != reference_data["page_id"] or not artifacts.get("assembled_page_spec_sha256"):
        raise _error("scripted_v1_outcome_g0_binding_invalid")
    return canonical, reference_data, {
        "fixture_id": _text(object.__getattribute__(fixture, "fixture_id"), "scripted_fixture_id"),
        "registry_id": _text(object.__getattribute__(fixture, "registry_id"), "scripted_registry_id"),
        "registry_sha256": _hex64(object.__getattribute__(fixture, "registry_sha256"), "scripted_registry_sha256"),
    }


def _validate_source_binding(value, frozen_binding, _sha_fn=_sha256, _decode=_strict_b64_decode, _replay=_replay_v1_scripted, _error=TrustedRemoteModelRouteError):
    if not isinstance(value, Mapping):
        raise _error("route_source_binding_invalid")
    kind = value.get("kind")
    if kind == "scripted_v1":
        data = _keys(value, ("kind", "v1_outcome_base64", "v1_outcome_sha256", "fixture"), "scripted_source_binding")
        raw = _decode(data["v1_outcome_base64"], "scripted_v1_outcome_base64")
        if _sha_fn(raw) != _hex64(data["v1_outcome_sha256"], "scripted_v1_outcome_sha256"):
            raise _error("scripted_v1_outcome_sha256_invalid")
        canonical, reference, fixture = _replay(raw)
        if reference != frozen_binding["reference"] or data["fixture"] != fixture or raw != canonical:
            raise _error("scripted_source_binding_invalid")
        return {"kind": "scripted_v1", "v1_outcome_base64": data["v1_outcome_base64"], "v1_outcome_sha256": _sha_fn(raw), "fixture": fixture}
    if kind == "untrusted_envelope":
        data = _keys(value, ("kind", "envelope_id", "envelope_sha256"), "failure_source_binding")
        return {"kind": "untrusted_envelope", "envelope_id": _text(data["envelope_id"], "failure_envelope_id"), "envelope_sha256": _hex64(data["envelope_sha256"], "failure_envelope_sha256")}
    if kind == "verified_real_provider":
        raise _error("real_provider_authority_unavailable")
    raise _error("route_source_binding_invalid")


def _failure_mapping(value, _code=_REAL_AUTHORITY_UNAVAILABLE, _copy=_copy_tree, _error=TrustedRemoteModelRouteError):
    expected = {"code": _code, "stage": "source_exclusive_route", "phase": "verified_real_provider_authority", "retryable": False, "payload_disclosure": "none"}
    if value != expected:
        raise _error("route_failure_invalid")
    return _copy(expected)

def _validate_outcome(data, _keys_fn=_keys, _reference_binding_fn=_reference_binding, _validate_identity=_validate_identified, _schema=MODEL_ROUTE_OUTCOME_SCHEMA, _scripted_branch=_SCRIPTED_BRANCH, _real_branch=_REAL_BRANCH, _failure_branch=_FAILURE_BRANCH, _scripted_disposition=_SCRIPTED_DISPOSITION, _failure_disposition=_FAILURE_DISPOSITION, _untrusted=_UNTRUSTED_ATTESTATION_STATE, _prefix=_OUTCOME_PREFIX, _source_binding=_validate_source_binding, _failure_fn=_failure_mapping, _error=TrustedRemoteModelRouteError):
    data = _keys_fn(data, _OUTCOME_KEYS, "trusted_remote_model_route_outcome")
    if data["schema_version"] != _schema:
        raise _error("route_outcome_schema_invalid")
    case_data = _keys(data["case_binding"], ("case_id", "d17_path", "provider_input", "frozen_g0"), "route_outcome_case_binding")
    if case_data["case_id"] not in _REQUIRED_CASE_IDS or case_data["d17_path"] != "path_3":
        raise _error("route_outcome_case_binding_invalid")
    provider = _keys(case_data["provider_input"], ("input_id", "sha256", "byte_length"), "route_outcome_provider_input")
    frozen_case = _keys(case_data["frozen_g0"], ("package_id", "sha256"), "route_outcome_frozen_g0_case")
    if type(provider["byte_length"]) is not int or provider["byte_length"] < 1:
        raise _error("route_outcome_case_binding_invalid")
    case = {
        "case_id": case_data["case_id"], "d17_path": "path_3",
        "provider_input": {"input_id": _text(provider["input_id"], "route_outcome_input_id"), "sha256": _hex64(provider["sha256"], "route_outcome_input_sha256"), "byte_length": provider["byte_length"]},
        "frozen_g0": {"package_id": _text(frozen_case["package_id"], "route_outcome_frozen_g0_package_id"), "sha256": _hex64(frozen_case["sha256"], "route_outcome_frozen_g0_sha256")},
    }
    if not isinstance(data["frozen_g0_binding"], Mapping) or "reference" not in data["frozen_g0_binding"]:
        raise _error("route_outcome_frozen_g0_binding_invalid")
    frozen = _reference_binding_fn(data["frozen_g0_binding"]["reference"], case)
    if data["frozen_g0_binding"] != frozen:
        raise _error("route_outcome_frozen_g0_binding_invalid")
    branch = data["source_branch"]
    disposition = data["disposition"]
    if branch == _scripted_branch:
        if disposition != _scripted_disposition or data["attestation_state"] != "scripted_fixture_authoritative_v1" or data["failure"] is not None:
            raise _error("route_outcome_branch_disposition_invalid")
        source = _source_binding(data["source_binding"], frozen)
        if source["kind"] != "scripted_v1":
            raise _error("route_outcome_source_exclusivity_invalid")
    elif branch == _real_branch:
        # Slice 2 cannot create or parse an authority-bearing real result.
        raise _error("real_provider_authority_unavailable")
    elif branch == _failure_branch:
        if disposition != _failure_disposition or data["attestation_state"] != _untrusted:
            raise _error("route_outcome_branch_disposition_invalid")
        source = _source_binding(data["source_binding"], frozen)
        if source["kind"] != "untrusted_envelope":
            raise _error("route_outcome_source_exclusivity_invalid")
        _failure_fn(data["failure"])
    else:
        raise _error("route_outcome_branch_invalid")
    result = {
        "schema_version": _schema,
        "outcome_id": _text(data["outcome_id"], "outcome_id"),
        "source_branch": branch,
        "disposition": disposition,
        "attestation_state": data["attestation_state"],
        "case_binding": case,
        "frozen_g0_binding": frozen,
        "source_binding": source,
        "failure": None if branch == _scripted_branch else _failure_fn(data["failure"]),
    }
    _validate_identity(result, "outcome_id", _prefix)
    return result


TrustedRemoteModelRouteOutcome, _OUTCOME_EXTRACT, _OUTCOME_FROM_DATA, _OUTCOME_FROM_BYTES = _make_record_type(
    "TrustedRemoteModelRouteOutcome", _validate_outcome
)


def _resolve_envelope(value, action_time_plan, expected_case_id, frozen_g0_reference, _envelope_type=TrustedRemoteRealRunEnvelope, _envelope_from=_ENVELOPE_FROM_BYTES, _extract=_ENVELOPE_EXTRACT, _loads_fn=_loads, _replay_plan=_replay_action_plan, _plan_binding_fn=_plan_binding, _case_fn=_case_from_plan, _reference_map=_reference_mapping_from_instance, _reference_binding_fn=_reference_binding, _error=TrustedRemoteModelRouteError):
    try:
        if type(value) is _envelope_type:
            raw = _extract(value)
        elif type(value) is bytes:
            raw = value
        else:
            raise _error("real_run_envelope_exact_type_or_bytes_required")
        replay = _envelope_from(raw)
        canonical = _extract(replay)
        if raw != canonical:
            raise _error("real_run_envelope_not_canonical")
        data = _loads_fn(canonical)
        plan, plan_bytes = _replay_plan(action_time_plan)
        if data["action_time_plan"] != plan or data["plan_binding"] != _plan_binding_fn(plan, plan_bytes):
            raise _error("real_run_envelope_action_time_plan_cross_binding_invalid")
        case = _case_fn(plan, expected_case_id)
        expected_case = {"case_id": case["case_id"], "d17_path": case["d17_path"], "provider_input": case["provider_input"]}
        if data["case_binding"] != expected_case:
            raise _error("real_run_envelope_case_request_or_d17_projection_cross_binding_invalid")
        reference = _reference_map(frozen_g0_reference)
        expected_frozen = _reference_binding_fn(reference, case)
        if data["frozen_g0_binding"] != expected_frozen:
            raise _error("real_run_envelope_frozen_g0_cross_binding_invalid")
        return data, canonical, plan, case, expected_frozen
    except _error:
        raise
    except Exception as exc:
        raise _error("real_run_envelope_live_validation_failed") from exc


def _create_envelope_impl(
    action_time_plan, raw_record, frozen_g0_reference,
    _from_data=_ENVELOPE_FROM_DATA, _replay_plan=_replay_action_plan,
    _loads_fn=_loads, _extract=_extract_canonical, _raw_type=_RAW_RECORD_TYPE,
    _case_fn=_case_from_plan, _replay_raw=_replay_raw_record,
    _validate_raw=_validate_raw_local_candidate,
    _reference_map=_reference_mapping_from_instance,
    _reference_binding_fn=_reference_binding, _identified_fn=_identified,
    _schema=REAL_RUN_ENVELOPE_SCHEMA, _prefix=_ENVELOPE_PREFIX,
    _source=_UNTRUSTED_SOURCE_STATE, _attestation=_UNTRUSTED_ATTESTATION_STATE,
    _issuer=copy.deepcopy(_FUTURE_ISSUER), _execution=copy.deepcopy(_ENVELOPE_EXECUTION_STATE), _copy=_copy_tree,
):
    plan, plan_bytes = _replay_plan(action_time_plan)
    if type(raw_record) is _raw_type:
        raw_seed = _loads_fn(_extract(raw_record, _raw_type, "raw_record"))
    elif type(raw_record) is bytes:
        raw_seed = _loads_fn(raw_record)
    else:
        raise TrustedRemoteModelRouteError("raw_record_exact_type_or_bytes_required")
    raw_case = raw_seed.get("case_binding")
    if not isinstance(raw_case, Mapping):
        raise TrustedRemoteModelRouteError("raw_record_case_binding_invalid")
    case = _case_fn(plan, raw_case.get("case_id"))
    raw, _raw_bytes, raw_binding = _replay_raw(raw_record, plan, case["case_id"])
    _validate_raw(raw, case)
    frozen = _reference_binding_fn(_reference_map(frozen_g0_reference), case)
    data = {
        "schema_version": _schema,
        "envelope_id": _prefix + "0" * 64,
        "source_state": _source,
        "attestation_state": _attestation,
        "future_issuer": _copy(_issuer),
        "action_time_plan": plan,
        "plan_binding": _plan_binding(plan, plan_bytes),
        "case_binding": {"case_id": case["case_id"], "d17_path": case["d17_path"], "provider_input": case["provider_input"]},
        "runtime_profile": _copy(raw["runtime_profile"]),
        "loader_contract": _copy(raw["loader_contract"]),
        "raw_record": raw,
        "raw_record_binding": raw_binding,
        "raw_response": _copy(raw["raw_response"]),
        "frozen_g0_binding": frozen,
        "execution_state": _copy(_execution),
    }
    return _from_data(_identified_fn(data, "envelope_id", _prefix))


def _wrap_scripted_impl(
    serialized_v1_outcome, action_time_plan, expected_case_id, frozen_g0_reference,
    _from_data=_OUTCOME_FROM_DATA, _replay_plan=_replay_action_plan,
    _case_fn=_case_from_plan, _reference_map=_reference_mapping_from_instance,
    _reference_binding_fn=_reference_binding, _replay_v1=_replay_v1_scripted,
    _b64encode=base64.b64encode, _sha_fn=_sha256, _identified_fn=_identified,
    _schema=MODEL_ROUTE_OUTCOME_SCHEMA, _prefix=_OUTCOME_PREFIX,
    _branch=_SCRIPTED_BRANCH, _disposition=_SCRIPTED_DISPOSITION,
):
    plan, _plan_bytes = _replay_plan(action_time_plan)
    case = _case_fn(plan, expected_case_id)
    frozen = _reference_binding_fn(_reference_map(frozen_g0_reference), case)
    canonical, v1_reference, fixture = _replay_v1(serialized_v1_outcome)
    if v1_reference != frozen["reference"]:
        raise TrustedRemoteModelRouteError("scripted_v1_outcome_frozen_g0_cross_binding_invalid")
    data = {
        "schema_version": _schema,
        "outcome_id": _prefix + "0" * 64,
        "source_branch": _branch,
        "disposition": _disposition,
        "attestation_state": "scripted_fixture_authoritative_v1",
        "case_binding": case,
        "frozen_g0_binding": frozen,
        "source_binding": {"kind": "scripted_v1", "v1_outcome_base64": _b64encode(canonical).decode("ascii"), "v1_outcome_sha256": _sha_fn(canonical), "fixture": fixture},
        "failure": None,
    }
    return _from_data(_identified_fn(data, "outcome_id", _prefix))


def _route_untrusted_impl(
    envelope, action_time_plan, expected_case_id, frozen_g0_reference,
    _from_data=_OUTCOME_FROM_DATA, _resolve=_resolve_envelope, _sha_fn=_sha256,
    _identified_fn=_identified, _schema=MODEL_ROUTE_OUTCOME_SCHEMA,
    _prefix=_OUTCOME_PREFIX, _failure_branch=_FAILURE_BRANCH,
    _failure_disposition=_FAILURE_DISPOSITION, _attestation=_UNTRUSTED_ATTESTATION_STATE,
    _code=_REAL_AUTHORITY_UNAVAILABLE,
):
    data, canonical, _plan, case, frozen = _resolve(envelope, action_time_plan, expected_case_id, frozen_g0_reference)
    failure = {"code": _code, "stage": "source_exclusive_route", "phase": "verified_real_provider_authority", "retryable": False, "payload_disclosure": "none"}
    result = {
        "schema_version": _schema,
        "outcome_id": _prefix + "0" * 64,
        "source_branch": _failure_branch,
        "disposition": _failure_disposition,
        "attestation_state": _attestation,
        "case_binding": case,
        "frozen_g0_binding": frozen,
        "source_binding": {"kind": "untrusted_envelope", "envelope_id": data["envelope_id"], "envelope_sha256": _sha_fn(canonical)},
        "failure": failure,
    }
    return _from_data(_identified_fn(result, "outcome_id", _prefix))


def _validate_outcome_binding_impl(outcome, action_time_plan, expected_case_id, frozen_g0_reference, _outcome_type=TrustedRemoteModelRouteOutcome, _outcome_from=_OUTCOME_FROM_BYTES, _extract=_OUTCOME_EXTRACT, _loads_fn=_loads, _replay_plan=_replay_action_plan, _case_fn=_case_from_plan, _reference_map=_reference_mapping_from_instance, _reference_binding_fn=_reference_binding, _mapping=Mapping, _real_branch=_REAL_BRANCH, _real_disposition=_REAL_DISPOSITION, _real_attestation=_REAL_ATTESTATION_STATE, _real_source_kind=_REAL_SOURCE_KIND, _error=TrustedRemoteModelRouteError):
    """Apply the sole manager-consumable Slice 2 outcome gate."""
    try:
        if type(outcome) is _outcome_type:
            raw = _extract(outcome)
        elif type(outcome) is bytes:
            raw = outcome
        else:
            raise _error("route_outcome_exact_type_or_bytes_required")
        replay = _outcome_from(raw)
        canonical = _extract(replay)
        if raw != canonical:
            raise _error("route_outcome_not_canonical")
        data = _loads_fn(canonical)
        source_binding = data.get("source_binding") if isinstance(data, _mapping) else None
        source_kind = source_binding.get("kind") if isinstance(source_binding, _mapping) else None
        if (
            data.get("source_branch") == _real_branch
            or data.get("disposition") == _real_disposition
            or data.get("attestation_state") == _real_attestation
            or source_kind == _real_source_kind
        ):
            raise _error("real_provider_authority_unavailable")
        plan, _plan_bytes = _replay_plan(action_time_plan)
        case = _case_fn(plan, expected_case_id)
        if data["case_binding"] != case:
            raise _error("route_outcome_case_request_or_d17_projection_cross_binding_invalid")
        frozen = _reference_binding_fn(_reference_map(frozen_g0_reference), case)
        if data["frozen_g0_binding"] != frozen:
            raise _error("route_outcome_frozen_g0_cross_binding_invalid")
        return replay
    except _error:
        raise
    except Exception as exc:
        raise _error("route_outcome_live_validation_failed") from exc

@dataclass(frozen=True)
class TestOnlyNonAttestedReplayVector:
    """Explicit in-memory test vector; not serializable run evidence or a route input."""

    envelope: object
    action_time_plan: object
    expected_case_id: str
    frozen_g0_reference: object
    package: object
    context: object
    guidance: object
    manifest: object
    selected: object
    local_request: object
    pre_invocation_audit: object
    local_qwen_preparation: object
    schema_version: str = TEST_ONLY_REPLAY_VECTOR_SCHEMA


@dataclass(frozen=True)
class TestOnlyStructuralReplayResult:
    """In-memory structural replay result; never a real-provider route outcome."""

    state: str
    failure_code: str | None
    assembled_page_spec: object | None
    provider_calls_after_first_complete_raw: int = 0
    model_repair_call_count: int = 0
    real_provider_assembled: bool = False
    result_package_created: bool = False
    a07_input_created: bool = False
    schema_version: str = TEST_ONLY_REPLAY_RESULT_SCHEMA

    def validate(self):
        if self.schema_version != TEST_ONLY_REPLAY_RESULT_SCHEMA:
            raise TrustedRemoteModelRouteError("test_only_result_schema_invalid")
        if self.provider_calls_after_first_complete_raw != 0 or self.model_repair_call_count != 0 or self.real_provider_assembled is not False or self.result_package_created is not False or self.a07_input_created is not False:
            raise TrustedRemoteModelRouteError("test_only_result_execution_state_invalid")
        if self.state == _TEST_ONLY_SUCCESS:
            if self.failure_code is not None or self.assembled_page_spec is None:
                raise TrustedRemoteModelRouteError("test_only_success_invalid")
        elif self.state in (_TEST_ONLY_PARSE_FAILURE, _TEST_ONLY_ASSEMBLY_FAILURE, _TEST_ONLY_BINDING_FAILURE):
            if self.failure_code is None or self.assembled_page_spec is not None:
                raise TrustedRemoteModelRouteError("test_only_failure_invalid")
        else:
            raise TrustedRemoteModelRouteError("test_only_result_state_invalid")
        return self


def _selection_provider_identity(selected, _error=TrustedRemoteModelRouteError):
    try:
        record = object.__getattribute__(selected, "selection_record")
        return {
            "input_id": object.__getattribute__(record, "input_view_id"),
            "sha256": object.__getattribute__(record, "provider_visible_input_sha256"),
            "byte_length": object.__getattribute__(record, "provider_visible_input_byte_length"),
        }
    except Exception as exc:
        raise _error("test_only_selected_input_identity_unavailable") from exc


def _live_validate_test_vector(vector, _vector_type=TestOnlyNonAttestedReplayVector, _g0_from_verified=_FROZEN_G0_FROM_VERIFIED, _g0_type=_FROZEN_G0_TYPE, _resolve_fn=_resolve_envelope, _error=TrustedRemoteModelRouteError):
    if type(vector) is not _vector_type or vector.schema_version != TEST_ONLY_REPLAY_VECTOR_SCHEMA:
        raise _error("test_only_replay_vector_invalid")
    data, _canonical, _plan, case, frozen = _resolve_fn(vector.envelope, vector.action_time_plan, vector.expected_case_id, vector.frozen_g0_reference)
    try:
        live_reference = _g0_from_verified(vector.package, vector.context, vector.guidance)
    except Exception as exc:
        raise _error("test_only_frozen_g0_live_validation_failed") from exc
    if _reference_mapping_from_instance(live_reference) != frozen["reference"]:
        raise _error("test_only_frozen_g0_live_binding_invalid")
    try:
        _MANIFEST_VALIDATE(vector.manifest)
        _SELECTED_VALIDATE_AGAINST(vector.selected, vector.context, vector.manifest)
        _REQUEST_VALIDATE_AGAINST(vector.local_request, vector.context, vector.manifest)
        _AUDIT_VALIDATE_AGAINST(vector.pre_invocation_audit, vector.context, vector.manifest, vector.selected, vector.local_request)
        _PREPARATION_VALIDATE_AGAINST(vector.local_qwen_preparation, vector.context, vector.manifest, vector.selected, vector.local_request, vector.pre_invocation_audit)
    except Exception as exc:
        raise _error("test_only_d17_live_validation_failed") from exc
    if _selection_provider_identity(vector.selected) != case["provider_input"]:
        raise _error("test_only_d17_provider_projection_cross_binding_invalid")
    raw_record = _RAW_RECORD_FROM_BYTES(_dumps(data["raw_record"]))
    return _RAW_RECORD_REPLAY(raw_record)


def _assemble_with_captured_authority(raw_response, context, guidance, _parse=_PARSE_RAW_RESPONSE, _validate_local=_VALIDATE_LOCAL_BINDINGS, _validate_candidate=_VALIDATE_CANDIDATE_CONTEXT, _assemble=_ASSEMBLE_PAGE_SPEC, _page_validate=_PAGE_SPEC_VALIDATE, _report=_BUILD_ASSEMBLY_REPORT, _assembled_type=_ASSEMBLED_TYPE, _assembled_validate=_ASSEMBLED_VALIDATE):
    candidate = _parse(raw_response)
    context_hash, guidance_hash, evidence, evidence_by_role = _validate_local(context, guidance)
    _validate_candidate(candidate, context)
    page_spec = _assemble(candidate, context, evidence, evidence_by_role)
    _page_validate(page_spec)
    report = _report(raw_response, candidate, page_spec, context_hash, guidance_hash)
    result = _assembled_type(raw_response=raw_response, candidate=candidate, page_spec=page_spec, report=report)
    _assembled_validate(result)
    return result


def _test_only_replay_impl(vector, _live=_live_validate_test_vector):
    try:
        raw_response = _live(vector)
    except Exception:
        return TestOnlyStructuralReplayResult(_TEST_ONLY_BINDING_FAILURE, "test_only_binding_failed", None).validate()
    try:
        assembled = _assemble_with_captured_authority(raw_response, vector.context, vector.guidance)
        return TestOnlyStructuralReplayResult(_TEST_ONLY_SUCCESS, None, assembled).validate()
    except Exception as exc:
        phase = getattr(exc, "phase", None)
        state = _TEST_ONLY_ASSEMBLY_FAILURE if phase == "assembly" else _TEST_ONLY_PARSE_FAILURE
        code = "semantic_assembly_failed" if state == _TEST_ONLY_ASSEMBLY_FAILURE else "semantic_parse_failed"
        return TestOnlyStructuralReplayResult(state, code, None).validate()


def _public_envelope(impl):
    def public(action_time_plan, raw_record, frozen_g0_reference):
        return impl(action_time_plan, raw_record, frozen_g0_reference)
    public.__name__ = "create_untrusted_trusted_remote_real_run_envelope"
    public.__qualname__ = public.__name__
    return public


def _public_four(impl, name):
    def public(value, action_time_plan, expected_case_id, frozen_g0_reference):
        return impl(value, action_time_plan, expected_case_id, frozen_g0_reference)
    public.__name__ = name
    public.__qualname__ = name
    return public


def _public_test_only(impl):
    def public(test_only_non_attested_replay_vector):
        return impl(test_only_non_attested_replay_vector)
    public.__name__ = "test_only_replay_trusted_remote_envelope"
    public.__qualname__ = public.__name__
    return public


create_untrusted_trusted_remote_real_run_envelope = _public_envelope(_create_envelope_impl)
wrap_serialized_scripted_local_fixture_outcome = _public_four(_wrap_scripted_impl, "wrap_serialized_scripted_local_fixture_outcome")
route_untrusted_trusted_remote_real_run_envelope = _public_four(_route_untrusted_impl, "route_untrusted_trusted_remote_real_run_envelope")
validate_trusted_remote_model_route_outcome_binding = _public_four(_validate_outcome_binding_impl, "validate_trusted_remote_model_route_outcome_binding")
test_only_replay_trusted_remote_envelope = _public_test_only(_test_only_replay_impl)


# Install the final public surface once.  The factory owns every memo and sealed
# authority value locally; after installation no mutable authority-holder graph is
# retained as a route-module global.
def _install_sealed_public_api():
    memo = {}

    def make_cell(value):
        return (lambda: value).__closure__[0]

    def reachable_names(code):
        names = set(code.co_names)
        for constant in code.co_consts:
            if isinstance(constant, CodeType):
                names.update(reachable_names(constant))
        return names

    def freeze(value):
        key = id(value)
        if key in memo:
            return memo[key]
        if isinstance(value, MethodType):
            frozen = MethodType(freeze(value.__func__), value.__self__)
            memo[key] = frozen
            return frozen
        if isinstance(value, FunctionType):
            closure = None if value.__closure__ is None else tuple(
                make_cell(freeze(cell.cell_contents)) for cell in value.__closure__
            )
            frozen_globals = {"__builtins__": value.__globals__.get("__builtins__", __builtins__)}
            frozen = FunctionType(value.__code__, frozen_globals, value.__name__, None, closure)
            memo[key] = frozen
            for name in reachable_names(value.__code__):
                if name in value.__globals__:
                    frozen_globals[name] = freeze(value.__globals__[name])
            defaults = value.__defaults__
            frozen.__defaults__ = None if defaults is None else tuple(freeze(item) for item in defaults)
            if value.__kwdefaults__ is not None:
                frozen.__kwdefaults__ = {name: freeze(item) for name, item in value.__kwdefaults__.items()}
            frozen.__annotations__ = dict(getattr(value, "__annotations__", {}))
            frozen.__qualname__ = value.__qualname__
            return frozen
        if isinstance(value, Mapping):
            backing = {}
            frozen = MappingProxyType(backing)
            memo[key] = frozen
            for item_key, item_value in value.items():
                backing[freeze(item_key)] = freeze(item_value)
            return frozen
        if type(value) is list:
            memo[key] = None
            frozen = tuple(freeze(item) for item in value)
            memo[key] = frozen
            return frozen
        if type(value) is tuple:
            memo[key] = None
            frozen = tuple(freeze(item) for item in value)
            memo[key] = frozen
            return frozen
        if type(value) is set:
            memo[key] = None
            frozen = frozenset(freeze(item) for item in value)
            memo[key] = frozen
            return frozen
        if type(value) is frozenset:
            memo[key] = None
            frozen = frozenset(freeze(item) for item in value)
            memo[key] = frozen
            return frozen
        if type(value) is bytearray:
            frozen = bytes(value)
            memo[key] = frozen
            return frozen
        return value

    sealed_dumps = freeze(_dumps)
    sealed_loads = freeze(_loads)
    sealed_sha256 = freeze(_sha256)
    sealed_validate_envelope = freeze(_validate_envelope)
    sealed_validate_outcome = freeze(_validate_outcome)
    sealed_record_factory = freeze(_make_record_type)
    envelope_type, envelope_extract, envelope_from_data, envelope_from_bytes = sealed_record_factory(
        "TrustedRemoteRealRunEnvelope",
        sealed_validate_envelope,
        _dumps_fn=sealed_dumps,
        _loads_fn=sealed_loads,
        _sha_fn=sealed_sha256,
    )
    outcome_type, outcome_extract, outcome_from_data, outcome_from_bytes = sealed_record_factory(
        "TrustedRemoteModelRouteOutcome",
        sealed_validate_outcome,
        _dumps_fn=sealed_dumps,
        _loads_fn=sealed_loads,
        _sha_fn=sealed_sha256,
    )
    resolve_base = freeze(_resolve_envelope)
    create_base = freeze(_create_envelope_impl)
    wrap_base = freeze(_wrap_scripted_impl)
    route_base = freeze(_route_untrusted_impl)
    validate_base = freeze(_validate_outcome_binding_impl)
    test_only_base = freeze(_test_only_replay_impl)
    live_base = freeze(_live_validate_test_vector)

    def build_public_api():
        def sealed_resolve(value, action_time_plan, expected_case_id, frozen_g0_reference):
            return resolve_base(
                value,
                action_time_plan,
                expected_case_id,
                frozen_g0_reference,
                _envelope_type=envelope_type,
                _envelope_from=envelope_from_bytes,
                _extract=envelope_extract,
            )

        def create(action_time_plan, raw_record, frozen_g0_reference):
            return create_base(action_time_plan, raw_record, frozen_g0_reference, _from_data=envelope_from_data)

        def wrap(value, action_time_plan, expected_case_id, frozen_g0_reference):
            return wrap_base(value, action_time_plan, expected_case_id, frozen_g0_reference, _from_data=outcome_from_data)

        def route_untrusted(value, action_time_plan, expected_case_id, frozen_g0_reference):
            return route_base(
                value,
                action_time_plan,
                expected_case_id,
                frozen_g0_reference,
                _from_data=outcome_from_data,
                _resolve=sealed_resolve,
            )

        def validate_outcome(value, action_time_plan, expected_case_id, frozen_g0_reference):
            return validate_base(
                value,
                action_time_plan,
                expected_case_id,
                frozen_g0_reference,
                _outcome_type=outcome_type,
                _outcome_from=outcome_from_bytes,
                _extract=outcome_extract,
            )

        def sealed_live(vector):
            return live_base(vector, _resolve_fn=sealed_resolve)

        def test_only(test_only_non_attested_replay_vector):
            return test_only_base(test_only_non_attested_replay_vector, _live=sealed_live)

        names = (
            (create, "create_untrusted_trusted_remote_real_run_envelope"),
            (wrap, "wrap_serialized_scripted_local_fixture_outcome"),
            (route_untrusted, "route_untrusted_trusted_remote_real_run_envelope"),
            (validate_outcome, "validate_trusted_remote_model_route_outcome_binding"),
            (test_only, "test_only_replay_trusted_remote_envelope"),
        )
        for function, name in names:
            function.__name__ = name
            function.__qualname__ = name
        return create, wrap, route_untrusted, validate_outcome, test_only

    create, wrap, route_untrusted, validate_outcome, test_only = build_public_api()
    return (
        envelope_type,
        envelope_extract,
        envelope_from_data,
        envelope_from_bytes,
        outcome_type,
        outcome_extract,
        outcome_from_data,
        outcome_from_bytes,
        create,
        wrap,
        route_untrusted,
        validate_outcome,
        test_only,
    )


(
    TrustedRemoteRealRunEnvelope,
    _ENVELOPE_EXTRACT,
    _ENVELOPE_FROM_DATA,
    _ENVELOPE_FROM_BYTES,
    TrustedRemoteModelRouteOutcome,
    _OUTCOME_EXTRACT,
    _OUTCOME_FROM_DATA,
    _OUTCOME_FROM_BYTES,
    create_untrusted_trusted_remote_real_run_envelope,
    wrap_serialized_scripted_local_fixture_outcome,
    route_untrusted_trusted_remote_real_run_envelope,
    validate_trusted_remote_model_route_outcome_binding,
    test_only_replay_trusted_remote_envelope,
) = _install_sealed_public_api()
del _install_sealed_public_api

# Slice 2 deliberately exposes no authority-bearing capability symbol. A future
# Slice 3 writer may define its own private capability in its separate module.


__all__ = (
    "MODEL_ROUTE_OUTCOME_SCHEMA",
    "REAL_RUN_ENVELOPE_SCHEMA",
    "TEST_ONLY_REPLAY_RESULT_SCHEMA",
    "TEST_ONLY_REPLAY_VECTOR_SCHEMA",
    "TrustedRemoteModelRouteError",
    "TrustedRemoteModelRouteOutcome",
    "TrustedRemoteRealRunEnvelope",
    "TestOnlyNonAttestedReplayVector",
    "TestOnlyStructuralReplayResult",
    "create_untrusted_trusted_remote_real_run_envelope",
    "route_untrusted_trusted_remote_real_run_envelope",
    "test_only_replay_trusted_remote_envelope",
    "validate_trusted_remote_model_route_outcome_binding",
    "wrap_serialized_scripted_local_fixture_outcome",
)