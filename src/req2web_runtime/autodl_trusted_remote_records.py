"""Canonical local-R0 trusted-remote records; no action or authorization surface."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from . import autodl_repository_archive as _archive_module

ACTION_TIME_PLAN_SCHEMA = "req2web.runtime.autodl_trusted_remote_action_time_plan.v3"
RUN_EVIDENCE_SCHEMA = "req2web.runtime.autodl_trusted_remote_run_evidence.v3"
CLOSEOUT_EXPECTATION_SCHEMA = "req2web.runtime.autodl_trusted_remote_closeout_expectation.v3"
RECORD_STATE = "local_r0_no_action"
PATH_3 = "path_3"
MODEL_ID = "Qwen3.5-9B"
MODEL_REPOSITORY = "Qwen/Qwen3.5-9B"
PROFILE = "quality_experiment"
DTYPE = "bf16"
QUANTIZATION = "none"
REQUIRED_CASE_IDS = ("path3-commerce-checkout", "path3-media-analysis")
REQUIRED_DEVICE = "autodl_rtx_5090_or_stronger"
RESULT_RETURN_PATHS = (
    "run/run_manifest.json",
    "run/runtime_observation.json",
    "run/model_inventory.json",
    "cases/path3-commerce-checkout/invocation.json",
    "cases/path3-commerce-checkout/raw_response.bin",
    "cases/path3-commerce-checkout/error.json",
    "cases/path3-commerce-checkout/model_route_outcome.json",
    "cases/path3-commerce-checkout/gate_delivery_outcome.json",
    "cases/path3-commerce-checkout/result_package_manifest.json",
    "cases/path3-media-analysis/invocation.json",
    "cases/path3-media-analysis/raw_response.bin",
    "cases/path3-media-analysis/error.json",
    "cases/path3-media-analysis/model_route_outcome.json",
    "cases/path3-media-analysis/gate_delivery_outcome.json",
    "cases/path3-media-analysis/result_package_manifest.json",
    "final/final_result_index.json",
    "closeout/process_completion.json",
    "closeout/closeout.json",
)
CLOSEOUT_STEP_ORDER = (
    "process_completion", "result_return", "project_side_temporary_deletion", "instance_release", "temporary_access_revocation",
)
NO_ACTION_FLAGS = (
    "remote_action_authorized", "ssh_authorized", "upload_authorized", "model_acquisition_authorized",
    "model_load_authorized", "model_inference_authorized", "remote_command_authorized", "delete_authorized",
    "instance_release_authorized", "credential_action_authorized", "execution_occurred", "cleanup_complete",
    "h1_eligible", "formal_quality_claim",
)
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_POSIX_PATH = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9._/-]*$")
_ARCHIVE_MANIFEST_TYPE = _archive_module.RepositoryArchiveManifest
_ARCHIVE_FROM_BYTES = _ARCHIVE_MANIFEST_TYPE.from_bytes
_ARCHIVE_CANONICAL_BYTES = _ARCHIVE_MANIFEST_TYPE.canonical_bytes
_ARCHIVE_TO_DICT = _ARCHIVE_MANIFEST_TYPE.to_dict
_ARCHIVE_SHA256 = _ARCHIVE_MANIFEST_TYPE.sha256
_ARCHIVE_VALIDATED_DATA = _ARCHIVE_MANIFEST_TYPE._validated_data
_ARCHIVE_JSON_DUMPS = _archive_module._AUTHORITY.json_dumps
_ARCHIVE_JSON_SHA256 = _archive_module._AUTHORITY.sha256_bytes


class TrustedRemoteRecordsError(ValueError):
    """A record violates a strict no-action trusted-remote contract."""


def _pairs(pairs, _error=TrustedRemoteRecordsError):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _error("duplicate_json_key")
        result[key] = value
    return result


class _Authority:
    __slots__ = ("dumps", "loads", "sha")
    def __init__(self, dumps, loads, sha):
        self.dumps, self.loads, self.sha = dumps, loads, sha


def _make_authority(_dumps=json.dumps, _loads=json.loads, _sha=hashlib.sha256, _pairs_fn=_pairs, _error=TrustedRemoteRecordsError):
    def dumps(value):
        return _dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    def reject_constant(_value):
        raise _error("non_finite_json_forbidden")
    def loads(raw):
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not isinstance(raw, bytes):
            raise _error("record_bytes_required")
        try:
            return _loads(raw.decode("utf-8"), object_pairs_hook=_pairs_fn, parse_constant=reject_constant)
        except UnicodeDecodeError as exc:
            raise _error("record_not_utf8") from exc
        except _error:
            raise
        except (json.JSONDecodeError, ValueError) as exc:
            raise _error("record_not_json") from exc
    return _Authority(dumps, loads, lambda raw: _sha(raw).hexdigest())


_AUTHORITY = _make_authority()


def _keys(value, expected, label, _mapping=Mapping, _error=TrustedRemoteRecordsError):
    if not isinstance(value, _mapping) or len(value) != len(expected) or set(value) != set(expected):
        raise _error(f"{label}_exact_keys_invalid")
    return value


def _text(value, label, _error=TrustedRemoteRecordsError):
    if not isinstance(value, str) or not value:
        raise _error(f"{label}_not_nonempty_string")
    return value


def _integer(value, label, positive=False, _error=TrustedRemoteRecordsError):
    if not isinstance(value, int) or isinstance(value, bool) or value < (1 if positive else 0):
        raise _error(f"{label}_not_{'positive' if positive else 'nonnegative'}_integer")
    return value


def _hex(value, label, pattern, _text_fn=_text, _error=TrustedRemoteRecordsError):
    value = _text_fn(value, label)
    if not pattern.fullmatch(value):
        raise _error(f"{label}_invalid_hex")
    return value


def _path(value, label, _text_fn=_text, _pattern=_POSIX_PATH, _error=TrustedRemoteRecordsError):
    value = _text_fn(value, label)
    if not _pattern.fullmatch(value) or value.startswith("/") or "\\" in value or "//" in value or any(part in {"", ".", ".."} for part in value.split("/")):
        raise _error(f"{label}_not_canonical_posix_path")
    return value


def _false_flags(value, _flags=NO_ACTION_FLAGS, _keys_fn=_keys, _error=TrustedRemoteRecordsError):
    value = _keys_fn(value, _flags, "no_action_flags")
    if any(type(value[name]) is not bool or value[name] is not False for name in _flags):
        raise _error("no_action_flag_state_promotion")
    return {name: False for name in _flags}


def _identified(body, authority):
    body["record_id"] = "0" * 64
    body["record_id"] = authority.sha(authority.dumps(body))
    return body


def _valid_id(data, authority, _hex_fn=_hex, _hex64=_HEX64, _copy=copy.deepcopy, _error=TrustedRemoteRecordsError):
    actual = _hex_fn(data["record_id"], "record_id", _hex64)
    body = _copy(dict(data)); body["record_id"] = "0" * 64
    if authority.sha(authority.dumps(body)) != actual:
        raise _error("record_identity_invalid")


def _utc(value, label, _text_fn=_text, _datetime=datetime, _timezone=timezone, _error=TrustedRemoteRecordsError):
    value = _text_fn(value, label)
    if not value.endswith("Z"):
        raise _error(f"{label}_not_utc_z")
    try:
        parsed = _datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise _error(f"{label}_not_iso8601") from exc
    if parsed.tzinfo != _timezone.utc or parsed.microsecond:
        raise _error(f"{label}_not_canonical_utc")
    return parsed, value


def _resolve_archive(value, manifest_type=_ARCHIVE_MANIFEST_TYPE, _from_bytes=_ARCHIVE_FROM_BYTES, _canonical_bytes=_ARCHIVE_CANONICAL_BYTES, _validated_data=_ARCHIVE_VALIDATED_DATA, _json_dumps=_ARCHIVE_JSON_DUMPS, _bytes=bytes, _error=TrustedRemoteRecordsError):
    try:
        if type(value) is manifest_type:
            canonical = _json_dumps(_validated_data(value))
            manifest = _from_bytes(canonical)
        elif type(value) is _bytes:
            manifest = _from_bytes(value)
        else:
            raise _error("archive_manifest_exact_type_or_bytes_required")
        _validated_data(manifest)
        return manifest
    except _error:
        raise
    except Exception as exc:
        raise _error("archive_manifest_live_validation_failed") from exc


def _derive_archive_binding(data, _json_dumps=_ARCHIVE_JSON_DUMPS, _json_sha256=_ARCHIVE_JSON_SHA256, _error=TrustedRemoteRecordsError):
    try:
        return {
            "schema_version": data["schema_version"], "manifest_id": data["manifest_id"],
            "manifest_sha256": _json_sha256(_json_dumps(data)), "commit_sha": data["commit_sha"], "tree_sha": data["tree_sha"],
            "archive_sha256": data["archive"]["sha256"], "tracked_inventory_sha256": data["source_tree"]["tracked_inventory_sha256"],
            "coverage_sha256": data["source_tree"]["coverage_sha256"],
        }
    except (KeyError, TypeError) as exc:
        raise _error("archive_manifest_binding_unavailable") from exc


def _archive_binding(value, _resolve_fn=_resolve_archive, _validated_data=_ARCHIVE_VALIDATED_DATA, _derive_fn=_derive_archive_binding):
    manifest = _resolve_fn(value)
    return _derive_fn(_validated_data(manifest))


def _archive_full_binding(value, _resolve_fn=_resolve_archive, _validated_data=_ARCHIVE_VALIDATED_DATA, _derive_fn=_derive_archive_binding):
    manifest = _resolve_fn(value)
    data = _validated_data(manifest)
    return data, _derive_fn(data)


def _replay_archive_manifest(value, _mapping=Mapping, _json_dumps=_ARCHIVE_JSON_DUMPS, _from_bytes=_ARCHIVE_FROM_BYTES, _validated_data=_ARCHIVE_VALIDATED_DATA, _derive_fn=_derive_archive_binding, _error=TrustedRemoteRecordsError):
    if not isinstance(value, _mapping):
        raise _error("archive_manifest_full_mapping_required")
    try:
        manifest = _from_bytes(_json_dumps(dict(value)))
        data = _validated_data(manifest)
        return data, _derive_fn(data)
    except _error:
        raise
    except Exception as exc:
        raise _error("archive_manifest_replay_invalid") from exc


def _validate_archive_binding(value, _keys_fn=_keys, _hex_fn=_hex, _hex40=_HEX40, _hex64=_HEX64, _error=TrustedRemoteRecordsError):
    names = ("schema_version", "manifest_id", "manifest_sha256", "commit_sha", "tree_sha", "archive_sha256", "tracked_inventory_sha256", "coverage_sha256")
    value = _keys_fn(value, names, "archive_manifest")
    if value["schema_version"] != "req2web.runtime.autodl_policy_filtered_repository_archive_manifest.v1":
        raise _error("archive_manifest_schema_invalid")
    result = {"schema_version": value["schema_version"]}
    for name in ("manifest_id", "manifest_sha256", "archive_sha256", "tracked_inventory_sha256", "coverage_sha256"):
        result[name] = _hex_fn(value[name], name, _hex64)
    for name in ("commit_sha", "tree_sha"):
        result[name] = _hex_fn(value[name], name, _hex40)
    return result


def _rows(value, label, _keys_fn=_keys, _path_fn=_path, _int_fn=_integer, _hex_fn=_hex, _hex64=_HEX64, _error=TrustedRemoteRecordsError):
    if not isinstance(value, list) or not value:
        raise _error(f"{label}_rows_invalid")
    result = []
    for row in value:
        row = _keys_fn(row, ("relative_path", "byte_length", "sha256"), label + "_row")
        result.append({"relative_path": _path_fn(row["relative_path"], label + "_path"), "byte_length": _int_fn(row["byte_length"], label + "_byte_length", True), "sha256": _hex_fn(row["sha256"], label + "_sha256", _hex64)})
    paths = [row["relative_path"] for row in result]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise _error(f"{label}_rows_order_or_duplicate_invalid")
    return result


def _inventory_identity(kind, fields, rows, authority):
    return authority.sha(authority.dumps({"kind": kind, "fields": fields, "rows": rows}))


def _model_inventory(value, authority, _keys_fn=_keys, _rows_fn=_rows, _identity_fn=_inventory_identity, _hex_fn=_hex, _int_fn=_integer, _text_fn=_text, _hex40=_HEX40, _hex64=_HEX64, _repository=MODEL_REPOSITORY, _model_id=MODEL_ID, _error=TrustedRemoteRecordsError):
    names = ("model_id", "repository", "exact_revision", "source_state", "files", "file_count", "total_bytes", "inventory_sha256", "tree_sha256")
    value = _keys_fn(value, names, "model_inventory")
    if value["model_id"] != _model_id or value["repository"] != _repository or value["source_state"] != "recorded_not_verified_no_action":
        raise _error("model_inventory_identity_or_state_invalid")
    rows = _rows_fn(value["files"], "model_file")
    count = _int_fn(value["file_count"], "model_file_count", True)
    total = _int_fn(value["total_bytes"], "model_total_bytes", True)
    if count != len(rows) or total != sum(row["byte_length"] for row in rows):
        raise _error("model_inventory_count_or_total_drift")
    fields = {"model_id": _model_id, "repository": _repository, "exact_revision": _hex_fn(value["exact_revision"], "model_exact_revision", _hex40), "source_state": "recorded_not_verified_no_action"}
    inventory_sha = _hex_fn(value["inventory_sha256"], "model_inventory_sha256", _hex64)
    tree_sha = _hex_fn(value["tree_sha256"], "model_tree_sha256", _hex64)
    if inventory_sha != _identity_fn("model_inventory", fields, rows, authority) or tree_sha != _identity_fn("model_tree", {}, rows, authority):
        raise _error("model_inventory_identity_drift")
    return {**fields, "files": rows, "file_count": count, "total_bytes": total, "inventory_sha256": inventory_sha, "tree_sha256": tree_sha}


def _runtime_inventory(value, authority, _keys_fn=_keys, _rows_fn=_rows, _identity_fn=_inventory_identity, _hex_fn=_hex, _int_fn=_integer, _text_fn=_text, _hex64=_HEX64, _error=TrustedRemoteRecordsError):
    names = ("runtime_id", "image_id", "source_state", "artifacts", "artifact_count", "total_bytes", "inventory_sha256")
    value = _keys_fn(value, names, "runtime_inventory")
    if value["source_state"] != "recorded_not_verified_no_action":
        raise _error("runtime_inventory_state_invalid")
    rows = _rows_fn(value["artifacts"], "runtime_artifact")
    count = _int_fn(value["artifact_count"], "runtime_artifact_count", True)
    total = _int_fn(value["total_bytes"], "runtime_total_bytes", True)
    if count != len(rows) or total != sum(row["byte_length"] for row in rows):
        raise _error("runtime_inventory_count_or_total_drift")
    fields = {"runtime_id": _text_fn(value["runtime_id"], "runtime_id"), "image_id": _text_fn(value["image_id"], "image_id"), "source_state": "recorded_not_verified_no_action"}
    inventory_sha = _hex_fn(value["inventory_sha256"], "runtime_inventory_sha256", _hex64)
    if inventory_sha != _identity_fn("runtime_inventory", fields, rows, authority):
        raise _error("runtime_inventory_identity_drift")
    return {**fields, "artifacts": rows, "artifact_count": count, "total_bytes": total, "inventory_sha256": inventory_sha}


def _cases(value, _keys_fn=_keys, _text_fn=_text, _hex_fn=_hex, _int_fn=_integer, _hex64=_HEX64, _cases=REQUIRED_CASE_IDS, _path_3=PATH_3, _error=TrustedRemoteRecordsError):
    if not isinstance(value, list) or len(value) != len(_cases):
        raise _error("case_inventory_count_invalid")
    result = []
    for row in value:
        row = _keys_fn(row, ("case_id", "d17_path", "metadata_state", "provider_input", "frozen_g0"), "case")
        provider = _keys_fn(row["provider_input"], ("input_id", "sha256", "byte_length"), "provider_input")
        g0 = _keys_fn(row["frozen_g0"], ("package_id", "sha256"), "frozen_g0")
        result.append({"case_id": _text_fn(row["case_id"], "case_id"), "d17_path": row["d17_path"], "metadata_state": row["metadata_state"], "provider_input": {"input_id": _text_fn(provider["input_id"], "provider_input_id"), "sha256": _hex_fn(provider["sha256"], "provider_input_sha256", _hex64), "byte_length": _int_fn(provider["byte_length"], "provider_input_byte_length", True)}, "frozen_g0": {"package_id": _text_fn(g0["package_id"], "frozen_g0_package_id"), "sha256": _hex_fn(g0["sha256"], "frozen_g0_sha256", _hex64)}})
    if tuple(row["case_id"] for row in result) != _cases or any(row["d17_path"] != _path_3 or row["metadata_state"] != "recorded_not_verified_no_action" for row in result):
        raise _error("case_inventory_order_path_or_state_invalid")
    return result


def _profile(value, _keys_fn=_keys, _profile=PROFILE, _device=REQUIRED_DEVICE, _error=TrustedRemoteRecordsError):
    names = ("requested_profile", "recommended_profile", "selected_profile", "requested_device", "recommended_device", "selected_device", "selection_basis", "human_override_applied")
    value = _keys_fn(value, names, "profile_selection")
    if any(value[name] != _profile for name in names[:3]) or any(value[name] != _device for name in names[3:6]) or value["selection_basis"] != "owner_approved_quality_experiment_no_action_record" or value["human_override_applied"] is not False:
        raise _error("profile_or_device_selection_invalid")
    return {name: value[name] for name in names}


def _precision(value, _keys_fn=_keys, _dtype=DTYPE, _quantization=QUANTIZATION, _error=TrustedRemoteRecordsError):
    names = ("requested_dtype", "recommended_dtype", "selected_dtype", "requested_quantization", "recommended_quantization", "selected_quantization")
    value = _keys_fn(value, names, "precision")
    if any(value[name] != _dtype for name in names[:3]) or any(value[name] != _quantization for name in names[3:]):
        raise _error("dtype_or_quantization_invalid")
    return {name: value[name] for name in names}


def _port(value, _error=TrustedRemoteRecordsError):
    if value > 65535:
        raise _error("ssh_port_out_of_range")
    return value


def _operational(value, _keys_fn=_keys, _text_fn=_text, _int_fn=_integer, _hex_fn=_hex, _path_fn=_path, _port_fn=_port, _hex64=_HEX64, _utc_fn=_utc, _timedelta=timedelta, _paths=RESULT_RETURN_PATHS, _error=TrustedRemoteRecordsError):
    names = ("action_time", "target_instance", "gpu", "quoted_price", "ssh", "old_instance", "caps", "result_return", "cleanup_plan")
    value = _keys_fn(value, names, "operational")
    action = _keys_fn(value["action_time"], ("issued_at_utc", "expires_at_utc", "single_use_state"), "action_time")
    issued, issued_text = _utc_fn(action["issued_at_utc"], "issued_at_utc"); expires, expires_text = _utc_fn(action["expires_at_utc"], "expires_at_utc")
    if not issued < expires <= issued + _timedelta(days=7) or action["single_use_state"] != "recorded_single_use_not_verified_no_action":
        raise _error("action_time_window_or_single_use_invalid")
    instance = _keys_fn(value["target_instance"], ("provider", "instance_id", "instance_class", "state"), "target_instance")
    if instance["provider"] != "AutoDL" or instance["state"] != "recorded_not_verified_no_action":
        raise _error("target_instance_state_invalid")
    gpu = _keys_fn(value["gpu"], ("model", "selected_index", "uuid_state", "uuid", "vram_bytes", "state"), "gpu")
    if gpu["state"] != "recorded_not_verified_no_action" or gpu["uuid_state"] not in {"recorded_uuid", "platform_uuid_absent"}:
        raise _error("gpu_state_invalid")
    if gpu["uuid_state"] == "platform_uuid_absent" and gpu["uuid"] != "absent_by_platform_no_action":
        raise _error("gpu_uuid_absent_state_invalid")
    if gpu["uuid_state"] == "recorded_uuid":
        _text_fn(gpu["uuid"], "gpu_uuid")
    price = _keys_fn(value["quoted_price"], ("currency", "quoted_price_milli", "state"), "quoted_price")
    if price["currency"] != "CNY" or price["state"] != "recorded_not_verified_no_action":
        raise _error("quoted_price_state_invalid")
    ssh = _keys_fn(value["ssh"], ("host", "port", "host_key_sha256", "temporary_public_key_sha256", "state"), "ssh")
    if ssh["state"] != "recorded_not_verified_no_action":
        raise _error("ssh_state_invalid")
    old = _keys_fn(value["old_instance"], ("instance_id", "release_evidence_id", "release_evidence_sha256", "access_revocation_evidence_id", "access_revocation_evidence_sha256", "state"), "old_instance")
    if old["state"] != "recorded_released_and_access_revoked_not_verified_no_action":
        raise _error("old_instance_state_invalid")
    caps = _keys_fn(value["caps"], ("gpu_count", "price_cap_milli", "time_cap_seconds", "storage_cap_bytes", "transfer_cap_bytes"), "caps")
    caps = {name: _int_fn(caps[name], name, True) for name in caps}
    if caps["gpu_count"] != 1:
        raise _error("gpu_count_must_be_one")
    if _int_fn(price["quoted_price_milli"], "quoted_price_milli", True) > caps["price_cap_milli"]:
        raise _error("quoted_price_exceeds_cap")
    result = _keys_fn(value["result_return"], ("location_id", "location_sha256", "max_file_count", "max_total_bytes", "allowed_paths", "state"), "result_return")
    if result["state"] != "recorded_not_verified_no_action" or not isinstance(result["allowed_paths"], list):
        raise _error("result_return_state_or_paths_invalid")
    paths = tuple(_path_fn(item, "result_return_path") for item in result["allowed_paths"])
    if paths != _paths or len(paths) != len(set(paths)):
        raise _error("result_return_paths_invalid")
    max_files = _int_fn(result["max_file_count"], "max_result_file_count", True)
    if max_files < len(paths):
        raise _error("result_return_file_cap_too_small")
    cleanup = _keys_fn(value["cleanup_plan"], ("plan_id", "plan_sha256", "state"), "cleanup_plan")
    if cleanup["state"] != "recorded_not_verified_no_action":
        raise _error("cleanup_plan_state_invalid")
    return {
        "action_time": {"issued_at_utc": issued_text, "expires_at_utc": expires_text, "single_use_state": "recorded_single_use_not_verified_no_action"},
        "target_instance": {"provider": "AutoDL", "instance_id": _text_fn(instance["instance_id"], "instance_id"), "instance_class": _text_fn(instance["instance_class"], "instance_class"), "state": "recorded_not_verified_no_action"},
        "gpu": {"model": _text_fn(gpu["model"], "gpu_model"), "selected_index": _int_fn(gpu["selected_index"], "gpu_selected_index"), "uuid_state": gpu["uuid_state"], "uuid": gpu["uuid"], "vram_bytes": _int_fn(gpu["vram_bytes"], "gpu_vram_bytes", True), "state": "recorded_not_verified_no_action"},
        "quoted_price": {"currency": "CNY", "quoted_price_milli": _int_fn(price["quoted_price_milli"], "quoted_price_milli", True), "state": "recorded_not_verified_no_action"},
        "ssh": {"host": _text_fn(ssh["host"], "ssh_host"), "port": _port_fn(_int_fn(ssh["port"], "ssh_port", True)), "host_key_sha256": _hex_fn(ssh["host_key_sha256"], "host_key_sha256", _hex64), "temporary_public_key_sha256": _hex_fn(ssh["temporary_public_key_sha256"], "temporary_public_key_sha256", _hex64), "state": "recorded_not_verified_no_action"},
        "old_instance": {"instance_id": _text_fn(old["instance_id"], "old_instance_id"), "release_evidence_id": _text_fn(old["release_evidence_id"], "release_evidence_id"), "release_evidence_sha256": _hex_fn(old["release_evidence_sha256"], "release_evidence_sha256", _hex64), "access_revocation_evidence_id": _text_fn(old["access_revocation_evidence_id"], "access_revocation_evidence_id"), "access_revocation_evidence_sha256": _hex_fn(old["access_revocation_evidence_sha256"], "access_revocation_evidence_sha256", _hex64), "state": "recorded_released_and_access_revoked_not_verified_no_action"},
        "caps": caps,
        "result_return": {"location_id": _text_fn(result["location_id"], "result_location_id"), "location_sha256": _hex_fn(result["location_sha256"], "result_location_sha256", _hex64), "max_file_count": max_files, "max_total_bytes": _int_fn(result["max_total_bytes"], "max_result_total_bytes", True), "allowed_paths": list(paths), "state": "recorded_not_verified_no_action"},
        "cleanup_plan": {"plan_id": _text_fn(cleanup["plan_id"], "cleanup_plan_id"), "plan_sha256": _hex_fn(cleanup["plan_sha256"], "cleanup_plan_sha256", _hex64), "state": "recorded_not_verified_no_action"},
    }


def _historical(value, _keys_fn=_keys, _error=TrustedRemoteRecordsError):
    names = ("controlling_policy", "historical_a1_a3_authority_used", "seccomp_live_claim", "netns_live_claim", "independent_isolation_live_claim")
    value = _keys_fn(value, names, "historical_boundary")
    if value["controlling_policy"] != "d17_trusted_remote_compute_amendment" or any(value[name] is not False for name in names[1:]):
        raise _error("historical_authority_or_claim_promoted")
    return {"controlling_policy": "d17_trusted_remote_compute_amendment", **{name: False for name in names[1:]}}


def _validate_plan(data, authority, _keys_fn=_keys, _hex_fn=_hex, _valid_id_fn=_valid_id, _archive_replay_fn=_replay_archive_manifest, _archive_binding_fn=_validate_archive_binding, _cases_fn=_cases, _model_fn=_model_inventory, _runtime_fn=_runtime_inventory, _profile_fn=_profile, _precision_fn=_precision, _operational_fn=_operational, _historical_fn=_historical, _flags_fn=_false_flags, _hex40=_HEX40, _hex64=_HEX64, _schema=ACTION_TIME_PLAN_SCHEMA, _state=RECORD_STATE, _error=TrustedRemoteRecordsError):
    names = ("schema_version", "record_id", "record_state", "policy", "archive_manifest", "archive_binding", "cases", "model_inventory", "runtime_inventory", "profile_selection", "precision", "operational", "historical_boundary", "no_action_flags")
    data = _keys_fn(data, names, "action_time_plan")
    if data["schema_version"] != _schema or data["record_state"] != _state:
        raise _error("action_time_plan_schema_or_state_invalid")
    policy = _keys_fn(data["policy"], ("amendment_sha256", "implementation_commit_sha", "implementation_tree_sha"), "policy")
    full_archive, archive = _archive_replay_fn(data["archive_manifest"])
    supplied_binding = _archive_binding_fn(data["archive_binding"])
    if supplied_binding != archive:
        raise _error("archive_binding_replay_mismatch")
    commit = _hex_fn(policy["implementation_commit_sha"], "implementation_commit_sha", _hex40)
    tree = _hex_fn(policy["implementation_tree_sha"], "implementation_tree_sha", _hex40)
    if commit != archive["commit_sha"] or tree != archive["tree_sha"]:
        raise _error("implementation_archive_binding_drift")
    result = {"schema_version": _schema, "record_id": _hex_fn(data["record_id"], "record_id", _hex64), "record_state": _state, "policy": {"amendment_sha256": _hex_fn(policy["amendment_sha256"], "amendment_sha256", _hex64), "implementation_commit_sha": commit, "implementation_tree_sha": tree}, "archive_manifest": full_archive, "archive_binding": archive, "cases": _cases_fn(data["cases"]), "model_inventory": _model_fn(data["model_inventory"], authority), "runtime_inventory": _runtime_fn(data["runtime_inventory"], authority), "profile_selection": _profile_fn(data["profile_selection"]), "precision": _precision_fn(data["precision"]), "operational": _operational_fn(data["operational"]), "historical_boundary": _historical_fn(data["historical_boundary"]), "no_action_flags": _flags_fn(data["no_action_flags"])}
    _valid_id_fn(result, authority)
    return result


def _make_record_type(name, validator, authority, _mapping=Mapping, _copy=copy.deepcopy, _error=TrustedRemoteRecordsError):
    captured_type = None
    class Record:
        __slots__ = ("_canonical",)
        def __init__(self, data):
            if not isinstance(data, _mapping):
                raise _error("record_not_mapping")
            canonical = authority.dumps(validator(data)); validator(authority.loads(canonical))
            object.__setattr__(self, "_canonical", canonical)
        def __setattr__(self, _name, _value):
            raise AttributeError("trusted_remote_record_is_immutable")
        @classmethod
        def from_dict(cls, data):
            if cls is not captured_type: raise _error("record_subclass_forbidden")
            return captured_type(data)
        @classmethod
        def from_bytes(cls, raw):
            if cls is not captured_type: raise _error("record_subclass_forbidden")
            parsed = authority.loads(raw); canonical = authority.dumps(validator(parsed))
            raw = raw.encode("utf-8") if isinstance(raw, str) else raw
            if raw != canonical: raise _error("record_not_canonical")
            return captured_type(authority.loads(canonical))
        def _bytes(self):
            if type(self) is not captured_type: raise _error("record_exact_type_required")
            raw = object.__getattribute__(self, "_canonical")
            if not isinstance(raw, bytes): raise _error("record_private_storage_invalid")
            canonical = authority.dumps(validator(authority.loads(raw)))
            if raw != canonical: raise _error("record_private_storage_not_canonical")
            return canonical
        def to_dict(self):
            value = _copy(authority.loads(self._bytes()))
            if not isinstance(value, dict): raise _error("record_copy_invalid")
            return value
        def canonical_bytes(self): return bytes(self._bytes())
        def sha256(self): return authority.sha(self._bytes())
        def record_id(self): return self.to_dict()["record_id"]
        def validate(self): return captured_type.from_bytes(self._bytes())
        def __eq__(self, other): return type(other) is captured_type and self._bytes() == other._bytes()
        def __hash__(self): return hash(self._bytes())
    Record.__name__ = name; Record.__qualname__ = name; captured_type = Record
    return Record


TrustedRemoteActionTimePlan = _make_record_type("TrustedRemoteActionTimePlan", lambda data, _v=_validate_plan, _a=_AUTHORITY: _v(data, _a), _AUTHORITY)


def _nested_plan(value, plan_type, _error=TrustedRemoteRecordsError):
    if not isinstance(value, Mapping): raise _error("nested_action_time_plan_not_mapping")
    plan = plan_type.from_bytes(plan_type.from_dict(value).canonical_bytes())
    return plan.to_dict(), plan.record_id(), plan.sha256()


def _validate_run(data, authority, plan_type, _keys_fn=_keys, _hex_fn=_hex, _int_fn=_integer, _path_fn=_path, _nested_plan_fn=_nested_plan, _valid_id_fn=_valid_id, _flags_fn=_false_flags, _paths=RESULT_RETURN_PATHS, _hex64=_HEX64, _schema=RUN_EVIDENCE_SCHEMA, _state=RECORD_STATE, _error=TrustedRemoteRecordsError):
    names = ("schema_version", "record_id", "record_state", "action_time_plan", "plan_binding", "observed_runtime", "timeout_cancel", "raw_result", "result_inventory", "completion", "no_action_flags")
    data = _keys_fn(data, names, "run_evidence")
    if data["schema_version"] != _schema or data["record_state"] != _state: raise _error("run_evidence_schema_or_state_invalid")
    plan_data, plan_id, plan_sha = _nested_plan_fn(data["action_time_plan"], plan_type)
    if _keys_fn(data["plan_binding"], ("record_id", "sha256"), "plan_binding") != {"record_id": plan_id, "sha256": plan_sha}: raise _error("run_plan_cross_binding_invalid")
    observed = _keys_fn(data["observed_runtime"], ("actual_profile", "actual_device", "actual_dtype", "actual_quantization"), "observed_runtime")
    expected_observed = {"actual_profile": "not_observed_no_action", "actual_device": "not_observed_no_action", "actual_dtype": "not_observed_no_action", "actual_quantization": "not_observed_no_action"}
    if observed != expected_observed: raise _error("actual_runtime_state_promotion")
    timeout = _keys_fn(data["timeout_cancel"], ("timeout_state", "cancel_state"), "timeout_cancel")
    if timeout != {"timeout_state": "not_started_no_action", "cancel_state": "not_requested_no_action"}: raise _error("timeout_or_cancel_state_promotion")
    raw = _keys_fn(data["raw_result"], ("state", "byte_length", "sha256"), "raw_result")
    if raw != {"state": "not_created_no_action", "byte_length": 0, "sha256": "not_created_no_action"}: raise _error("raw_result_state_promotion")
    inventory = _keys_fn(data["result_inventory"], ("state", "max_file_count", "max_total_bytes", "allowed_paths", "files"), "result_inventory")
    if inventory["state"] != "not_created_no_action" or inventory["files"] != [] or not isinstance(inventory["allowed_paths"], list): raise _error("result_inventory_state_promotion")
    paths = tuple(_path_fn(item, "result_inventory_path") for item in inventory["allowed_paths"])
    if paths != _paths or len(paths) != len(set(paths)): raise _error("result_inventory_path_traversal_or_duplicate")
    files = _int_fn(inventory["max_file_count"], "result_inventory_max_file_count", True); total = _int_fn(inventory["max_total_bytes"], "result_inventory_max_total_bytes", True)
    if files != plan_data["operational"]["result_return"]["max_file_count"] or total != plan_data["operational"]["result_return"]["max_total_bytes"]: raise _error("result_inventory_cap_drift")
    if _keys_fn(data["completion"], ("process_completed", "result_return_completed"), "completion") != {"process_completed": False, "result_return_completed": False}: raise _error("run_completion_state_promotion")
    result = {"schema_version": _schema, "record_id": _hex_fn(data["record_id"], "record_id", _hex64), "record_state": _state, "action_time_plan": plan_data, "plan_binding": {"record_id": plan_id, "sha256": plan_sha}, "observed_runtime": expected_observed, "timeout_cancel": dict(timeout), "raw_result": dict(raw), "result_inventory": {"state": "not_created_no_action", "max_file_count": files, "max_total_bytes": total, "allowed_paths": list(paths), "files": []}, "completion": {"process_completed": False, "result_return_completed": False}, "no_action_flags": _flags_fn(data["no_action_flags"])}
    _valid_id_fn(result, authority); return result


TrustedRemoteRunEvidence = _make_record_type("TrustedRemoteRunEvidence", lambda data, _v=_validate_run, _a=_AUTHORITY, _p=TrustedRemoteActionTimePlan: _v(data, _a, _p), _AUTHORITY)


def _nested_run(value, run_type, _error=TrustedRemoteRecordsError):
    if not isinstance(value, Mapping): raise _error("nested_run_evidence_not_mapping")
    run = run_type.from_bytes(run_type.from_dict(value).canonical_bytes())
    return run.to_dict(), run.record_id(), run.sha256()


def _validate_closeout(data, authority, plan_type, run_type, _keys_fn=_keys, _hex_fn=_hex, _nested_plan_fn=_nested_plan, _nested_run_fn=_nested_run, _valid_id_fn=_valid_id, _flags_fn=_false_flags, _steps=CLOSEOUT_STEP_ORDER, _hex64=_HEX64, _schema=CLOSEOUT_EXPECTATION_SCHEMA, _state=RECORD_STATE, _error=TrustedRemoteRecordsError):
    names = ("schema_version", "record_id", "record_state", "action_time_plan", "run_evidence", "bindings", "expectations", "self_certification_accepted", "no_action_flags")
    data = _keys_fn(data, names, "closeout_expectation")
    if data["schema_version"] != _schema or data["record_state"] != _state: raise _error("closeout_schema_or_state_invalid")
    plan_data, plan_id, plan_sha = _nested_plan_fn(data["action_time_plan"], plan_type); run_data, run_id, run_sha = _nested_run_fn(data["run_evidence"], run_type)
    if run_data["plan_binding"] != {"record_id": plan_id, "sha256": plan_sha}: raise _error("closeout_plan_run_cross_binding_invalid")
    bindings = _keys_fn(data["bindings"], ("plan_record_id", "plan_sha256", "run_record_id", "run_sha256"), "closeout_bindings")
    expected_binding = {"plan_record_id": plan_id, "plan_sha256": plan_sha, "run_record_id": run_id, "run_sha256": run_sha}
    if bindings != expected_binding: raise _error("closeout_binding_drift")
    if not isinstance(data["expectations"], list) or len(data["expectations"]) != len(_steps): raise _error("closeout_expectation_count_invalid")
    expectations = []
    for step, row in zip(_steps, data["expectations"], strict=True):
        expected = {"step": step, "required_before_closeout": True, "observed": False}
        if _keys_fn(row, ("step", "required_before_closeout", "observed"), "closeout_expectation_row") != expected: raise _error("closeout_expectation_state_promotion")
        expectations.append(expected)
    if data["self_certification_accepted"] is not False: raise _error("closeout_self_certification_forbidden")
    result = {"schema_version": _schema, "record_id": _hex_fn(data["record_id"], "record_id", _hex64), "record_state": _state, "action_time_plan": plan_data, "run_evidence": run_data, "bindings": expected_binding, "expectations": expectations, "self_certification_accepted": False, "no_action_flags": _flags_fn(data["no_action_flags"])}
    _valid_id_fn(result, authority); return result


TrustedRemoteCloseoutExpectation = _make_record_type("TrustedRemoteCloseoutExpectation", lambda data, _v=_validate_closeout, _a=_AUTHORITY, _p=TrustedRemoteActionTimePlan, _r=TrustedRemoteRunEvidence: _v(data, _a, _p, _r), _AUTHORITY)


def _build_model_input(value, authority, _keys_fn=_keys, _rows_fn=_rows, _identity_fn=_inventory_identity, _hex_fn=_hex, _hex40=_HEX40, _repository=MODEL_REPOSITORY, _model_id=MODEL_ID):
    value = _keys_fn(value, ("exact_revision", "files"), "model_input")
    rows = _rows_fn(value["files"], "model_file")
    fields = {"model_id": _model_id, "repository": _repository, "exact_revision": _hex_fn(value["exact_revision"], "model_exact_revision", _hex40), "source_state": "recorded_not_verified_no_action"}
    return {**fields, "files": rows, "file_count": len(rows), "total_bytes": sum(row["byte_length"] for row in rows), "inventory_sha256": _identity_fn("model_inventory", fields, rows, authority), "tree_sha256": _identity_fn("model_tree", {}, rows, authority)}


def _build_runtime_input(value, authority, _keys_fn=_keys, _rows_fn=_rows, _identity_fn=_inventory_identity, _text_fn=_text):
    value = _keys_fn(value, ("runtime_id", "image_id", "artifacts"), "runtime_input")
    rows = _rows_fn(value["artifacts"], "runtime_artifact")
    fields = {"runtime_id": _text_fn(value["runtime_id"], "runtime_id"), "image_id": _text_fn(value["image_id"], "image_id"), "source_state": "recorded_not_verified_no_action"}
    return {**fields, "artifacts": rows, "artifact_count": len(rows), "total_bytes": sum(row["byte_length"] for row in rows), "inventory_sha256": _identity_fn("runtime_inventory", fields, rows, authority)}


def _make_factory(authority, plan_type, run_type, closeout_type, archive_binding_fn, archive_full_binding_fn, _identified_fn=_identified, _copy=copy.deepcopy, _build_model_fn=_build_model_input, _build_runtime_fn=_build_runtime_input, _plan_schema=ACTION_TIME_PLAN_SCHEMA, _run_schema=RUN_EVIDENCE_SCHEMA, _closeout_schema=CLOSEOUT_EXPECTATION_SCHEMA, _state=RECORD_STATE, _profile=PROFILE, _device=REQUIRED_DEVICE, _dtype=DTYPE, _quantization=QUANTIZATION, _paths=RESULT_RETURN_PATHS, _steps=CLOSEOUT_STEP_ORDER, _flags=NO_ACTION_FLAGS):
    def create_local_r0_record_bundle(*, amendment_sha256, archive_manifest, cases, model, runtime, action_time, target_instance, gpu, quoted_price, ssh, old_instance, caps, result_return, cleanup_plan):
        """Create only canonical local-R0 no-action records from business metadata.

        archive_manifest must be the accepted archive type or its canonical bytes;
        it is reparsed through the owning archive validator. No caller authority,
        callback, parser, hash, validator, executor, transport, or override is accepted.
        """
        full_archive, archive = archive_full_binding_fn(archive_manifest)
        model_inventory = _build_model_fn(model, authority); runtime_inventory = _build_runtime_fn(runtime, authority)
        operational = {"action_time": _copy(dict(action_time)), "target_instance": _copy(dict(target_instance)), "gpu": _copy(dict(gpu)), "quoted_price": _copy(dict(quoted_price)), "ssh": _copy(dict(ssh)), "old_instance": _copy(dict(old_instance)), "caps": _copy(dict(caps)), "result_return": _copy(dict(result_return)), "cleanup_plan": _copy(dict(cleanup_plan))}
        plan_body = _identified_fn({"schema_version": _plan_schema, "record_id": "", "record_state": _state, "policy": {"amendment_sha256": amendment_sha256, "implementation_commit_sha": archive["commit_sha"], "implementation_tree_sha": archive["tree_sha"]}, "archive_manifest": full_archive, "archive_binding": archive, "cases": [_copy(dict(row)) for row in cases], "model_inventory": model_inventory, "runtime_inventory": runtime_inventory, "profile_selection": {"requested_profile": _profile, "recommended_profile": _profile, "selected_profile": _profile, "requested_device": _device, "recommended_device": _device, "selected_device": _device, "selection_basis": "owner_approved_quality_experiment_no_action_record", "human_override_applied": False}, "precision": {"requested_dtype": _dtype, "recommended_dtype": _dtype, "selected_dtype": _dtype, "requested_quantization": _quantization, "recommended_quantization": _quantization, "selected_quantization": _quantization}, "operational": operational, "historical_boundary": {"controlling_policy": "d17_trusted_remote_compute_amendment", "historical_a1_a3_authority_used": False, "seccomp_live_claim": False, "netns_live_claim": False, "independent_isolation_live_claim": False}, "no_action_flags": {name: False for name in _flags}}, authority)
        plan = plan_type.from_bytes(plan_type.from_dict(plan_body).canonical_bytes()); plan_data = plan.to_dict()
        result_return = plan_data["operational"]["result_return"]
        run_body = _identified_fn({"schema_version": _run_schema, "record_id": "", "record_state": _state, "action_time_plan": plan_data, "plan_binding": {"record_id": plan.record_id(), "sha256": plan.sha256()}, "observed_runtime": {"actual_profile": "not_observed_no_action", "actual_device": "not_observed_no_action", "actual_dtype": "not_observed_no_action", "actual_quantization": "not_observed_no_action"}, "timeout_cancel": {"timeout_state": "not_started_no_action", "cancel_state": "not_requested_no_action"}, "raw_result": {"state": "not_created_no_action", "byte_length": 0, "sha256": "not_created_no_action"}, "result_inventory": {"state": "not_created_no_action", "max_file_count": result_return["max_file_count"], "max_total_bytes": result_return["max_total_bytes"], "allowed_paths": list(_paths), "files": []}, "completion": {"process_completed": False, "result_return_completed": False}, "no_action_flags": {name: False for name in _flags}}, authority)
        run = run_type.from_bytes(run_type.from_dict(run_body).canonical_bytes()); run_data = run.to_dict()
        closeout_body = _identified_fn({"schema_version": _closeout_schema, "record_id": "", "record_state": _state, "action_time_plan": plan_data, "run_evidence": run_data, "bindings": {"plan_record_id": plan.record_id(), "plan_sha256": plan.sha256(), "run_record_id": run.record_id(), "run_sha256": run.sha256()}, "expectations": [{"step": step, "required_before_closeout": True, "observed": False} for step in _steps], "self_certification_accepted": False, "no_action_flags": {name: False for name in _flags}}, authority)
        closeout = closeout_type.from_bytes(closeout_type.from_dict(closeout_body).canonical_bytes())
        return plan.validate(), run.validate(), closeout.validate()
    return create_local_r0_record_bundle


create_local_r0_record_bundle = _make_factory(_AUTHORITY, TrustedRemoteActionTimePlan, TrustedRemoteRunEvidence, TrustedRemoteCloseoutExpectation, _archive_binding, _archive_full_binding)


__all__ = [
    "ACTION_TIME_PLAN_SCHEMA", "CLOSEOUT_EXPECTATION_SCHEMA", "DTYPE", "MODEL_ID", "MODEL_REPOSITORY", "NO_ACTION_FLAGS", "PATH_3", "PROFILE", "QUANTIZATION", "RECORD_STATE", "REQUIRED_CASE_IDS", "RESULT_RETURN_PATHS", "RUN_EVIDENCE_SCHEMA", "TrustedRemoteActionTimePlan", "TrustedRemoteCloseoutExpectation", "TrustedRemoteRecordsError", "TrustedRemoteRunEvidence", "create_local_r0_record_bundle",
]
