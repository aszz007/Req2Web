"""Canonical Slice 3 trusted-remote run and closeout records (local/no-action)."""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import re
from collections.abc import Mapping

from . import autodl_trusted_remote_qwen_raw as _raw
from . import autodl_trusted_remote_records as _records

PRODUCTION_RUN_RECORD_SCHEMA = "req2web.runtime.trusted_remote_production_run_record.v1"
PRODUCTION_RUN_BUNDLE_SCHEMA = "req2web.runtime.trusted_remote_production_run_bundle.v1"
WRITER_SCHEMA = "req2web.runtime.trusted_remote_unique_production_writer.v1"
WRITER_ID = "req2web.trusted_remote_unique_production_writer.v1"
PRODUCTION_SOURCE_KIND = "production_record_candidate"
RECORD_PREFIX = "trusted-remote-production-run-record-"
BUNDLE_PREFIX = "trusted-remote-production-run-bundle-"
REQUIRED_CASE_IDS = _records.REQUIRED_CASE_IDS
RESULT_RETURN_PATHS = _records.RESULT_RETURN_PATHS
CLOSEOUT_STEP_ORDER = _records.CLOSEOUT_STEP_ORDER
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_POSIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
# Structural local-contract seal only; it is neither a remote credential nor
# proof that any action happened.  The action-time gate remains separate.
_WRITER_KEY = bytes.fromhex("8d3f33e10c9c2b9785a7f157cd9817d1b39ef5be8b5d72019ce2cc1f8a65e303")


class TrustedRemoteSlice3Error(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise TrustedRemoteSlice3Error("duplicate_json_key")
        result[key] = value
    return result


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _loads(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes):
        raise TrustedRemoteSlice3Error("canonical_bytes_required")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=lambda _x: (_ for _ in ()).throw(TrustedRemoteSlice3Error("non_finite_json_forbidden")))
    except TrustedRemoteSlice3Error:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise TrustedRemoteSlice3Error("canonical_json_invalid") from exc


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _keys(value, names, label):
    if not isinstance(value, Mapping) or set(value) != set(names) or len(value) != len(names):
        raise TrustedRemoteSlice3Error(f"{label}_exact_keys_invalid")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value:
        raise TrustedRemoteSlice3Error(f"{label}_invalid")
    return value


def _integer(value, label, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise TrustedRemoteSlice3Error(f"{label}_invalid")
    return value


def _hex(value, label):
    value = _text(value, label)
    if not _HEX64.fullmatch(value):
        raise TrustedRemoteSlice3Error(f"{label}_invalid")
    return value


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _unb64(value, label):
    value = _text(value, label)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise TrustedRemoteSlice3Error(f"{label}_invalid") from exc
    if _b64(raw) != value:
        raise TrustedRemoteSlice3Error(f"{label}_not_canonical")
    return raw


def _identified(data, field, prefix):
    body = copy.deepcopy(dict(data))
    body[field] = prefix + "0" * 64
    expected = prefix + _sha(_dumps(body))
    if data.get(field) not in ("", prefix + "0" * 64, expected):
        raise TrustedRemoteSlice3Error(f"{field}_invalid")
    body[field] = expected
    return body


def _seal(data):
    body = copy.deepcopy(dict(data))
    body["writer_seal"] = ""
    return hmac.new(_WRITER_KEY, _dumps(body), hashlib.sha256).hexdigest()


def _writer(value):
    names = ("schema_version", "writer_id", "writer_state", "writer_seal")
    value = _keys(value, names, "writer")
    if value["schema_version"] != WRITER_SCHEMA or value["writer_id"] != WRITER_ID or value["writer_state"] != "unique_writer_contract_record":
        raise TrustedRemoteSlice3Error("writer_identity_invalid")
    seal = _hex(value["writer_seal"], "writer_seal")
    candidate = {"schema_version": WRITER_SCHEMA, "writer_id": WRITER_ID, "writer_state": "unique_writer_contract_record", "writer_seal": seal}
    if not hmac.compare_digest(seal, _seal(candidate)):
        raise TrustedRemoteSlice3Error("writer_seal_invalid")
    return candidate


def _new_writer():
    data = {"schema_version": WRITER_SCHEMA, "writer_id": WRITER_ID, "writer_state": "unique_writer_contract_record", "writer_seal": ""}
    data["writer_seal"] = _seal(data)
    return data


def _plan(value):
    if type(value) is _records.TrustedRemoteActionTimePlan:
        raw = _records.TrustedRemoteActionTimePlan.canonical_bytes(value)
    elif type(value) is bytes:
        raw = value
    else:
        raise TrustedRemoteSlice3Error("action_time_plan_exact_type_or_bytes_required")
    try:
        replay = _records.TrustedRemoteActionTimePlan.from_bytes(raw)
    except Exception as exc:
        raise TrustedRemoteSlice3Error("action_time_plan_replay_invalid") from exc
    if replay.canonical_bytes() != raw:
        raise TrustedRemoteSlice3Error("action_time_plan_not_canonical")
    return replay.to_dict(), raw


def _case(plan, case_id):
    case_id = _text(case_id, "case_id")
    for row in plan["cases"]:
        if row["case_id"] == case_id:
            return copy.deepcopy(row)
    raise TrustedRemoteSlice3Error("case_not_approved")


def _profile(value, plan):
    if type(value) is _raw.TrustedRemoteQwenRuntimeProfile:
        raw = _raw.TrustedRemoteQwenRuntimeProfile.canonical_bytes(value)
    elif type(value) is bytes:
        raw = value
    else:
        raise TrustedRemoteSlice3Error("runtime_profile_exact_type_or_bytes_required")
    try:
        replay = _raw.TrustedRemoteQwenRuntimeProfile.from_bytes(raw)
    except Exception as exc:
        raise TrustedRemoteSlice3Error("runtime_profile_replay_invalid") from exc
    if replay.canonical_bytes() != raw or replay.to_dict().get("action_time_plan") != plan:
        raise TrustedRemoteSlice3Error("runtime_profile_cross_binding_invalid")
    return replay.to_dict()


def _loader(value, profile):
    if type(value) is _raw.TrustedRemoteQwenLoaderContract:
        raw = _raw.TrustedRemoteQwenLoaderContract.canonical_bytes(value)
    elif type(value) is bytes:
        raw = value
    else:
        raise TrustedRemoteSlice3Error("loader_contract_exact_type_or_bytes_required")
    try:
        replay = _raw.TrustedRemoteQwenLoaderContract.from_bytes(raw)
    except Exception as exc:
        raise TrustedRemoteSlice3Error("loader_contract_replay_invalid") from exc
    if replay.canonical_bytes() != raw or replay.to_dict().get("runtime_profile") != profile:
        raise TrustedRemoteSlice3Error("loader_contract_cross_binding_invalid")
    return replay.to_dict()


def _raw_response(value):
    value = _keys(value, ("encoding", "base64", "byte_length", "sha256"), "raw_response")
    if value["encoding"] != "base64":
        raise TrustedRemoteSlice3Error("raw_response_encoding_invalid")
    raw = _unb64(value["base64"], "raw_response_base64")
    if _integer(value["byte_length"], "raw_response_byte_length", True) != len(raw) or _hex(value["sha256"], "raw_response_sha256") != _sha(raw):
        raise TrustedRemoteSlice3Error("raw_response_identity_invalid")
    return {"encoding": "base64", "base64": _b64(raw), "byte_length": len(raw), "sha256": _sha(raw)}


def _observation(value, plan):
    names = ("actual_profile", "actual_device", "actual_dtype", "actual_quantization", "elapsed_seconds", "cost_milli", "storage_bytes", "timeout_state", "cancel_state")
    value = _keys(value, names, "runtime_observation")
    selected = plan["profile_selection"]
    precision = plan["precision"]
    result = {key: value[key] for key in names}
    for key in ("actual_profile", "actual_device", "actual_dtype", "actual_quantization", "timeout_state", "cancel_state"):
        _text(result[key], key)
    for key in ("elapsed_seconds", "cost_milli", "storage_bytes"):
        _integer(result[key], key)
    if (result["actual_profile"], result["actual_device"], result["actual_dtype"], result["actual_quantization"]) != (selected["selected_profile"], selected["selected_device"], precision["selected_dtype"], precision["selected_quantization"]):
        raise TrustedRemoteSlice3Error("runtime_profile_or_precision_drift")
    caps = plan["operational"]["caps"]
    if result["elapsed_seconds"] > caps["time_cap_seconds"] or result["cost_milli"] > caps["price_cap_milli"] or result["storage_bytes"] > caps["storage_cap_bytes"]:
        raise TrustedRemoteSlice3Error("runtime_cap_drift")
    if result["timeout_state"] != "completed" or result["cancel_state"] != "not_requested":
        raise TrustedRemoteSlice3Error("runtime_completion_state_invalid")
    return result


def _execution(value):
    value = _keys(value, ("provider_call_count", "provider_calls_after_first_complete_raw", "model_repair_call_count", "retry_count", "model_loaded", "run_occurred", "evidence_state"), "execution")
    if (value["provider_call_count"], value["provider_calls_after_first_complete_raw"], value["model_repair_call_count"], value["retry_count"], value["model_loaded"], value["run_occurred"], value["evidence_state"]) != (1, 0, 0, 0, True, True, "production_writer_contract_candidate"):
        raise TrustedRemoteSlice3Error("execution_state_invalid")
    return copy.deepcopy(dict(value))


def _validate_record(data):
    names = ("schema_version", "run_record_id", "writer", "action_time_plan", "plan_binding", "case_binding", "runtime_profile", "loader_contract", "raw_response", "runtime_observation", "execution")
    data = _keys(data, names, "production_run_record")
    if data["schema_version"] != PRODUCTION_RUN_RECORD_SCHEMA:
        raise TrustedRemoteSlice3Error("production_run_record_schema_invalid")
    plan, plan_bytes = _plan(_dumps(data["action_time_plan"]))
    binding = {"record_id": plan["record_id"], "sha256": _sha(plan_bytes)}
    case_id = data["case_binding"].get("case_id") if isinstance(data["case_binding"], Mapping) else ""
    result = {
        "schema_version": PRODUCTION_RUN_RECORD_SCHEMA,
        "run_record_id": data["run_record_id"],
        "writer": _writer(data["writer"]),
        "action_time_plan": plan,
        "plan_binding": binding,
        "case_binding": _case(plan, case_id),
        "runtime_profile": _profile(_dumps(data["runtime_profile"]), plan),
        "loader_contract": None,
        "raw_response": _raw_response(data["raw_response"]),
        "runtime_observation": _observation(data["runtime_observation"], plan),
        "execution": _execution(data["execution"]),
    }
    if data["plan_binding"] != binding or data["case_binding"] != result["case_binding"]:
        raise TrustedRemoteSlice3Error("production_run_record_cross_binding_invalid")
    result["loader_contract"] = _loader(_dumps(data["loader_contract"]), result["runtime_profile"])
    result = _identified(result, "run_record_id", RECORD_PREFIX)
    if data["run_record_id"] != result["run_record_id"]:
        raise TrustedRemoteSlice3Error("run_record_id_invalid")
    return result


def _canonical_json(raw, label):
    if not isinstance(raw, bytes) or not raw:
        raise TrustedRemoteSlice3Error(f"{label}_bytes_required")
    data = _loads(raw)
    if _dumps(data) != raw:
        raise TrustedRemoteSlice3Error(f"{label}_not_canonical")
    return data


def _delivery(value, case_id):
    if isinstance(value, Mapping) and set(value) == {"case_id", "route_outcome", "gate_delivery_outcome", "result_package_manifest", "failure"}:
        if value["case_id"] != case_id:
            raise TrustedRemoteSlice3Error("delivery_case_cross_binding_invalid")
        normalized = {"case_id": case_id}
        for name in ("route_outcome", "gate_delivery_outcome"):
            row = _keys(value[name], ("base64", "byte_length", "sha256"), name)
            raw = _unb64(row["base64"], name + "_base64")
            if _integer(row["byte_length"], name + "_byte_length", True) != len(raw) or _hex(row["sha256"], name + "_sha256") != _sha(raw):
                raise TrustedRemoteSlice3Error(name + "_identity_invalid")
            _canonical_json(raw, name)
            normalized[name] = {"base64": _b64(raw), "byte_length": len(raw), "sha256": _sha(raw)}
        manifest = value["result_package_manifest"]
        failure = value["failure"]
        if (manifest is None) == (failure is None):
            raise TrustedRemoteSlice3Error("delivery_manifest_or_failure_required")
        if manifest is not None:
            row = _keys(manifest, ("base64", "byte_length", "sha256"), "result_package_manifest")
            raw = _unb64(row["base64"], "result_package_manifest_base64")
            if _integer(row["byte_length"], "result_package_manifest_byte_length", True) != len(raw) or _hex(row["sha256"], "result_package_manifest_sha256") != _sha(raw):
                raise TrustedRemoteSlice3Error("result_package_manifest_identity_invalid")
            _canonical_json(raw, "result_package_manifest")
            normalized["result_package_manifest"] = {"base64": _b64(raw), "byte_length": len(raw), "sha256": _sha(raw)}
            normalized["failure"] = None
        else:
            failure = _keys(failure, ("code", "stage", "retryable"), "delivery_failure")
            if failure["retryable"] is not False:
                raise TrustedRemoteSlice3Error("delivery_failure_invalid")
            normalized["result_package_manifest"] = None
            normalized["failure"] = {"code": _text(failure["code"], "delivery_failure_code"), "stage": _text(failure["stage"], "delivery_failure_stage"), "retryable": False}
        return normalized
    value = _keys(value, ("route_outcome", "gate_delivery_outcome", "result_package_manifest", "failure"), "delivery_record")
    route = _canonical_json(value["route_outcome"], "route_outcome")
    gate = _canonical_json(value["gate_delivery_outcome"], "gate_delivery_outcome")
    if route.get("case_binding", {}).get("case_id") != case_id:
        raise TrustedRemoteSlice3Error("delivery_route_case_cross_binding_invalid")
    manifest, failure = value["result_package_manifest"], value["failure"]
    if (manifest is None) == (failure is None):
        raise TrustedRemoteSlice3Error("delivery_manifest_or_failure_required")
    if manifest is not None:
        _canonical_json(manifest, "result_package_manifest")
        manifest_data = {"base64": _b64(manifest), "byte_length": len(manifest), "sha256": _sha(manifest)}
        failure_data = None
    else:
        failure = _keys(failure, ("code", "stage", "retryable"), "delivery_failure")
        if failure["retryable"] is not False:
            raise TrustedRemoteSlice3Error("delivery_failure_invalid")
        failure_data = {"code": _text(failure["code"], "delivery_failure_code"), "stage": _text(failure["stage"], "delivery_failure_stage"), "retryable": False}
        manifest_data = None
    return {"case_id": case_id, "route_outcome": {"base64": _b64(value["route_outcome"]), "byte_length": len(value["route_outcome"]), "sha256": _sha(value["route_outcome"])}, "gate_delivery_outcome": {"base64": _b64(value["gate_delivery_outcome"]), "byte_length": len(value["gate_delivery_outcome"]), "sha256": _sha(value["gate_delivery_outcome"])}, "result_package_manifest": manifest_data, "failure": failure_data}


def _inventory(value, plan):
    value = _keys(value, ("files", "file_count", "total_bytes", "inventory_sha256"), "result_inventory")
    if not isinstance(value["files"], list) or len(value["files"]) != len(RESULT_RETURN_PATHS):
        raise TrustedRemoteSlice3Error("result_inventory_file_count_invalid")
    rows = []
    for row in value["files"]:
        row = _keys(row, ("relative_path", "byte_length", "sha256"), "result_inventory_row")
        path = _text(row["relative_path"], "result_inventory_path")
        if not _POSIX.fullmatch(path):
            raise TrustedRemoteSlice3Error("result_inventory_path_invalid")
        rows.append({"relative_path": path, "byte_length": _integer(row["byte_length"], "result_inventory_byte_length"), "sha256": _hex(row["sha256"], "result_inventory_sha256")})
    if tuple(row["relative_path"] for row in rows) != RESULT_RETURN_PATHS:
        raise TrustedRemoteSlice3Error("result_inventory_paths_invalid")
    total = sum(row["byte_length"] for row in rows)
    if value["file_count"] != len(rows) or value["total_bytes"] != total or total > plan["operational"]["result_return"]["max_total_bytes"] or value["inventory_sha256"] != _sha(_dumps(rows)):
        raise TrustedRemoteSlice3Error("result_inventory_cap_or_identity_invalid")
    return {"files": rows, "file_count": len(rows), "total_bytes": total, "inventory_sha256": _sha(_dumps(rows))}


def _closeout(value):
    if isinstance(value, list):
        if len(value) != len(CLOSEOUT_STEP_ORDER):
            raise TrustedRemoteSlice3Error("closeout_count_invalid")
        rows = []
        for step, row in zip(CLOSEOUT_STEP_ORDER, value):
            row = _keys(row, ("step", "state", "evidence_id", "evidence_sha256"), f"closeout_{step}")
            if row["step"] != step or row["state"] != "completed":
                raise TrustedRemoteSlice3Error("closeout_state_invalid")
            rows.append({"step": step, "state": "completed", "evidence_id": _text(row["evidence_id"], f"{step}_evidence_id"), "evidence_sha256": _hex(row["evidence_sha256"], f"{step}_evidence_sha256")})
        return rows
    value = _keys(value, CLOSEOUT_STEP_ORDER, "closeout")
    rows = []
    for step in CLOSEOUT_STEP_ORDER:
        row = _keys(value[step], ("state", "evidence_id", "evidence_sha256"), f"closeout_{step}")
        if row["state"] != "completed":
            raise TrustedRemoteSlice3Error("closeout_state_invalid")
        rows.append({"step": step, "state": "completed", "evidence_id": _text(row["evidence_id"], f"{step}_evidence_id"), "evidence_sha256": _hex(row["evidence_sha256"], f"{step}_evidence_sha256")})
    return rows


def _validate_bundle(data):
    names = ("schema_version", "bundle_id", "writer", "action_time_plan", "plan_binding", "case_run_records", "delivery_records", "result_inventory", "final_result_index", "return_receipt", "closeout")
    data = _keys(data, names, "production_run_bundle")
    if data["schema_version"] != PRODUCTION_RUN_BUNDLE_SCHEMA:
        raise TrustedRemoteSlice3Error("production_run_bundle_schema_invalid")
    plan, plan_bytes = _plan(_dumps(data["action_time_plan"]))
    binding = {"record_id": plan["record_id"], "sha256": _sha(plan_bytes)}
    if data["plan_binding"] != binding or not isinstance(data["case_run_records"], list) or len(data["case_run_records"]) != 2:
        raise TrustedRemoteSlice3Error("production_run_bundle_plan_or_case_count_invalid")
    records = []
    for case_id, row in zip(REQUIRED_CASE_IDS, data["case_run_records"]):
        row = _keys(row, ("case_id", "run_record_base64", "run_record_sha256", "run_record_id"), "case_run_record")
        source = validate_trusted_remote_production_source_binding({"kind": PRODUCTION_SOURCE_KIND, "run_record_base64": row["run_record_base64"], "run_record_sha256": row["run_record_sha256"], "run_record_id": row["run_record_id"]}, case_id, _dumps(plan))
        records.append({"case_id": case_id, "run_record_base64": source["run_record_base64"], "run_record_sha256": source["run_record_sha256"], "run_record_id": source["run_record_id"]})
    if not isinstance(data["delivery_records"], list) or len(data["delivery_records"]) != 2:
        raise TrustedRemoteSlice3Error("delivery_record_count_invalid")
    delivery = [_delivery(value, case_id) for case_id, value in zip(REQUIRED_CASE_IDS, data["delivery_records"])]
    inventory = _inventory(data["result_inventory"], plan)
    index = _keys(data["final_result_index"], ("case_ids", "run_record_sha256", "delivery_sha256", "inventory_sha256"), "final_result_index")
    if index != {"case_ids": list(REQUIRED_CASE_IDS), "run_record_sha256": [row["run_record_sha256"] for row in records], "delivery_sha256": [_sha(_dumps(row)) for row in delivery], "inventory_sha256": inventory["inventory_sha256"]}:
        raise TrustedRemoteSlice3Error("final_result_index_cross_binding_invalid")
    expected_return = plan["operational"]["result_return"]
    if data["return_receipt"] != {"state": "completed", "location_id": expected_return["location_id"], "location_sha256": expected_return["location_sha256"], "inventory_sha256": inventory["inventory_sha256"]}:
        raise TrustedRemoteSlice3Error("return_receipt_cross_binding_invalid")
    result = {"schema_version": PRODUCTION_RUN_BUNDLE_SCHEMA, "bundle_id": data["bundle_id"], "writer": _writer(data["writer"]), "action_time_plan": plan, "plan_binding": binding, "case_run_records": records, "delivery_records": delivery, "result_inventory": inventory, "final_result_index": copy.deepcopy(dict(index)), "return_receipt": copy.deepcopy(dict(data["return_receipt"])), "closeout": _closeout(data["closeout"])}
    result = _identified(result, "bundle_id", BUNDLE_PREFIX)
    if data["bundle_id"] != result["bundle_id"]:
        raise TrustedRemoteSlice3Error("bundle_id_invalid")
    return result


def _record_type(name, validator):
    class Record:
        __slots__ = ("_data", "_canonical")
        def __init__(self, data):
            clean = validator(copy.deepcopy(dict(data)))
            object.__setattr__(self, "_data", clean)
            object.__setattr__(self, "_canonical", _dumps(clean))
        @classmethod
        def from_dict(cls, data):
            return cls(data)
        @classmethod
        def from_bytes(cls, raw):
            item = cls(_loads(raw))
            if item.canonical_bytes() != raw:
                raise TrustedRemoteSlice3Error("record_not_canonical")
            return item
        def _live(self):
            clean = validator(copy.deepcopy(object.__getattribute__(self, "_data")))
            if _dumps(clean) != object.__getattribute__(self, "_canonical"):
                raise TrustedRemoteSlice3Error("private_storage_or_canonical_drift")
            return clean
        def validate(self):
            self._live()
            return self
        def to_dict(self):
            return copy.deepcopy(self._live())
        def canonical_bytes(self):
            return bytes(_dumps(self._live()))
        def sha256(self):
            return _sha(self.canonical_bytes())
    Record.__name__ = name
    Record.__qualname__ = name
    return Record


TrustedRemoteProductionRunRecord = _record_type("TrustedRemoteProductionRunRecord", _validate_record)
TrustedRemoteProductionRunBundle = _record_type("TrustedRemoteProductionRunBundle", _validate_bundle)


def _source(record, expected_case_id, action_time_plan=None):
    if type(record) is TrustedRemoteProductionRunRecord:
        raw = record.canonical_bytes()
    elif type(record) is bytes:
        raw = record
    else:
        raise TrustedRemoteSlice3Error("production_run_record_exact_type_or_bytes_required")
    replay = TrustedRemoteProductionRunRecord.from_bytes(raw)
    if replay.canonical_bytes() != raw:
        raise TrustedRemoteSlice3Error("production_run_record_not_canonical")
    data = replay.to_dict()
    if data["case_binding"]["case_id"] != expected_case_id:
        raise TrustedRemoteSlice3Error("production_run_record_case_cross_binding_invalid")
    if action_time_plan is not None:
        plan, plan_raw = _plan(action_time_plan)
        if data["action_time_plan"] != plan or data["plan_binding"] != {"record_id": plan["record_id"], "sha256": _sha(plan_raw)}:
            raise TrustedRemoteSlice3Error("production_run_record_plan_cross_binding_invalid")
    return {"kind": PRODUCTION_SOURCE_KIND, "run_record_base64": _b64(raw), "run_record_sha256": _sha(raw), "run_record_id": data["run_record_id"]}


def _write_record(action_time_plan, runtime_profile, loader_contract, case_id, raw_bytes, runtime_observation):
    if not isinstance(raw_bytes, bytes) or not raw_bytes:
        raise TrustedRemoteSlice3Error("raw_bytes_required")
    plan, plan_raw = _plan(action_time_plan)
    profile = _profile(runtime_profile, plan)
    loader = _loader(loader_contract, profile)
    data = {"schema_version": PRODUCTION_RUN_RECORD_SCHEMA, "run_record_id": RECORD_PREFIX + "0" * 64, "writer": _new_writer(), "action_time_plan": plan, "plan_binding": {"record_id": plan["record_id"], "sha256": _sha(plan_raw)}, "case_binding": _case(plan, case_id), "runtime_profile": profile, "loader_contract": loader, "raw_response": {"encoding": "base64", "base64": _b64(raw_bytes), "byte_length": len(raw_bytes), "sha256": _sha(raw_bytes)}, "runtime_observation": copy.deepcopy(dict(runtime_observation)), "execution": {"provider_call_count": 1, "provider_calls_after_first_complete_raw": 0, "model_repair_call_count": 0, "retry_count": 0, "model_loaded": True, "run_occurred": True, "evidence_state": "production_writer_contract_candidate"}}
    return TrustedRemoteProductionRunRecord.from_dict(_identified(data, "run_record_id", RECORD_PREFIX))


def _write_bundle(action_time_plan, case_run_records, delivery_records, result_artifacts, closeout):
    if not all(isinstance(item, Mapping) for item in (case_run_records, delivery_records, result_artifacts, closeout)) or tuple(case_run_records) != REQUIRED_CASE_IDS or tuple(delivery_records) != REQUIRED_CASE_IDS or tuple(result_artifacts) != RESULT_RETURN_PATHS:
        raise TrustedRemoteSlice3Error("bundle_writer_coverage_invalid")
    plan, plan_raw = _plan(action_time_plan)
    records = []
    for case_id in REQUIRED_CASE_IDS:
        source = _source(case_run_records[case_id], case_id, _dumps(plan))
        records.append({"case_id": case_id, "run_record_base64": source["run_record_base64"], "run_record_sha256": source["run_record_sha256"], "run_record_id": source["run_record_id"]})
    delivery = [copy.deepcopy(dict(delivery_records[case_id])) for case_id in REQUIRED_CASE_IDS]
    files = []
    for path in RESULT_RETURN_PATHS:
        raw = result_artifacts[path]
        if not isinstance(raw, bytes):
            raise TrustedRemoteSlice3Error("result_artifact_bytes_required")
        files.append({"relative_path": path, "byte_length": len(raw), "sha256": _sha(raw)})
    inventory = {"files": files, "file_count": len(files), "total_bytes": sum(row["byte_length"] for row in files), "inventory_sha256": _sha(_dumps(files))}
    normalized_delivery = [_delivery(value, case_id) for case_id, value in zip(REQUIRED_CASE_IDS, delivery)]
    index = {"case_ids": list(REQUIRED_CASE_IDS), "run_record_sha256": [row["run_record_sha256"] for row in records], "delivery_sha256": [_sha(_dumps(row)) for row in normalized_delivery], "inventory_sha256": inventory["inventory_sha256"]}
    receipt = {"state": "completed", "location_id": plan["operational"]["result_return"]["location_id"], "location_sha256": plan["operational"]["result_return"]["location_sha256"], "inventory_sha256": inventory["inventory_sha256"]}
    normalized_closeout = _closeout(closeout)
    data = {"schema_version": PRODUCTION_RUN_BUNDLE_SCHEMA, "bundle_id": BUNDLE_PREFIX + "0" * 64, "writer": _new_writer(), "action_time_plan": plan, "plan_binding": {"record_id": plan["record_id"], "sha256": _sha(plan_raw)}, "case_run_records": records, "delivery_records": normalized_delivery, "result_inventory": inventory, "final_result_index": index, "return_receipt": receipt, "closeout": normalized_closeout}
    return TrustedRemoteProductionRunBundle.from_dict(_identified(data, "bundle_id", BUNDLE_PREFIX))


def _validate_bundle(bundle, action_time_plan):
    if type(bundle) is TrustedRemoteProductionRunBundle:
        raw = bundle.canonical_bytes()
    elif type(bundle) is bytes:
        raw = bundle
    else:
        raise TrustedRemoteSlice3Error("production_run_bundle_exact_type_or_bytes_required")
    replay = TrustedRemoteProductionRunBundle.from_bytes(raw)
    plan, plan_raw = _plan(action_time_plan)
    data = replay.to_dict()
    if data["action_time_plan"] != plan or data["plan_binding"] != {"record_id": plan["record_id"], "sha256": _sha(plan_raw)}:
        raise TrustedRemoteSlice3Error("production_run_bundle_plan_cross_binding_invalid")
    return replay


def write_trusted_remote_production_run_record(action_time_plan, runtime_profile, loader_contract, case_id, raw_bytes, runtime_observation):
    return _write_record(action_time_plan, runtime_profile, loader_contract, case_id, raw_bytes, runtime_observation)


def write_trusted_remote_production_run_bundle(action_time_plan, case_run_records, delivery_records, result_artifacts, closeout):
    return _write_bundle(action_time_plan, case_run_records, delivery_records, result_artifacts, closeout)


def make_trusted_remote_production_source_binding(record, expected_case_id):
    return _source(record, expected_case_id)


def validate_trusted_remote_production_source_binding(value, expected_case_id, action_time_plan=None):
    value = _keys(value, ("kind", "run_record_base64", "run_record_sha256", "run_record_id"), "production_source_binding")
    if value["kind"] != PRODUCTION_SOURCE_KIND:
        raise TrustedRemoteSlice3Error("production_source_binding_kind_invalid")
    raw = _unb64(value["run_record_base64"], "production_source_binding_base64")
    if _hex(value["run_record_sha256"], "production_source_binding_sha256") != _sha(raw):
        raise TrustedRemoteSlice3Error("production_source_binding_sha256_invalid")
    source = _source(raw, expected_case_id, action_time_plan)
    if value != source:
        raise TrustedRemoteSlice3Error("production_source_binding_cross_binding_invalid")
    return source


def validate_trusted_remote_production_run_record_binding(record, action_time_plan, expected_case_id):
    source = _source(record, expected_case_id, action_time_plan)
    return TrustedRemoteProductionRunRecord.from_bytes(_unb64(source["run_record_base64"], "production_source_binding_base64"))


def validate_trusted_remote_production_run_bundle_binding(bundle, action_time_plan):
    return _validate_bundle(bundle, action_time_plan)


__all__ = [
    "BUNDLE_PREFIX", "CLOSEOUT_STEP_ORDER", "PRODUCTION_RUN_BUNDLE_SCHEMA", "PRODUCTION_RUN_RECORD_SCHEMA", "PRODUCTION_SOURCE_KIND", "RECORD_PREFIX", "REQUIRED_CASE_IDS", "RESULT_RETURN_PATHS", "TrustedRemoteProductionRunBundle", "TrustedRemoteProductionRunRecord", "TrustedRemoteSlice3Error", "WRITER_ID", "WRITER_SCHEMA", "make_trusted_remote_production_source_binding", "validate_trusted_remote_production_run_bundle_binding", "validate_trusted_remote_production_run_record_binding", "validate_trusted_remote_production_source_binding", "write_trusted_remote_production_run_bundle", "write_trusted_remote_production_run_record",
]
