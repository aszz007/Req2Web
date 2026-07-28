"""Versioned trusted-remote two-case executor and detached attestation foundation.

The module keeps pre-run authorization separate from post-run result attestation.
It performs no action at import time.  Model/runtime imports are lazy and only
occur inside the fixed Linux worker entrypoint.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from . import autodl_repository_archive as _archive
from . import autodl_trusted_remote_records as _records

EXECUTION_PACKAGE_SCHEMA = "req2web.runtime.trusted_remote_execution_package.v2"
PRE_RUN_PAYLOAD_SCHEMA = "req2web.runtime.trusted_remote_pre_run_authorization_payload.v2"
PRE_RUN_RECEIPT_SCHEMA = "req2web.runtime.trusted_remote_pre_run_authorization_receipt.v2"
EXECUTION_RESULT_SCHEMA = "req2web.runtime.trusted_remote_execution_result.v2"
PROMPT_ARTIFACT_SCHEMA = "req2web.runtime.trusted_remote_prompt_artifact.v2"
RETURN_BUNDLE_SCHEMA = "req2web.runtime.trusted_remote_return_bundle.v2"
CLOSEOUT_RECORD_SCHEMA = "req2web.runtime.trusted_remote_closeout_record.v2"
PRE_RUN_NAMESPACE = "req2web.stage3.trusted_remote.pre_run.v2"
SIGNER_PRINCIPAL = "req2web-stage3-owner"
REQUIRED_CASE_IDS = _records.REQUIRED_CASE_IDS
QUALITY_PROFILE = {"profile": "quality_experiment", "dtype": "bf16", "quantization": "none"}
MODEL_REPOSITORY = _records.MODEL_REPOSITORY
MODEL_ID = _records.MODEL_ID
WORKER_RELATIVE_PATH = "scripts/stage3_trusted_remote_qwen_worker.py"
WORKER_ENTRYPOINT = "req2web_runtime.autodl_trusted_remote_executor._worker_main_v2"
WORKER_COMMAND_PREFIX = ("python3", WORKER_RELATIVE_PATH)
MAX_AUTHORIZATION_WINDOW = timedelta(days=7)
_RESULT_RELATIVE_PATH = "execution_result.json"
_ATTEMPT_MARKER_RELATIVE_PATH = ".req2web_attempt_consumed.json"
_VERIFIED_EXECUTION_TOKEN = object()
_PROCESS_CONSUMED_PRE_RUN_NONCES = set()
_PROCESS_NONCE_LOCK = threading.Lock()
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WORKER_SCRIPT = _PROJECT_ROOT / WORKER_RELATIVE_PATH


class TrustedRemoteExecutorError(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise TrustedRemoteExecutorError("duplicate_json_key")
        result[key] = value
    return result


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _loads(raw):
    if not isinstance(raw, bytes):
        raise TrustedRemoteExecutorError("canonical_bytes_required")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=lambda _x: (_ for _ in ()).throw(TrustedRemoteExecutorError("non_finite_json_forbidden")))
    except TrustedRemoteExecutorError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise TrustedRemoteExecutorError("canonical_json_invalid") from exc


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _keys(value, names, label):
    if not isinstance(value, Mapping) or set(value) != set(names) or len(value) != len(names):
        raise TrustedRemoteExecutorError(f"{label}_exact_keys_invalid")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value:
        raise TrustedRemoteExecutorError(f"{label}_invalid")
    return value


def _hex(value, label, length=64):
    value = _text(value, label)
    pattern = _HEX64 if length == 64 else _HEX40
    if not pattern.fullmatch(value):
        raise TrustedRemoteExecutorError(f"{label}_invalid")
    return value


def _integer(value, label, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise TrustedRemoteExecutorError(f"{label}_invalid")
    return value


def _boolean(value, label):
    if type(value) is not bool:
        raise TrustedRemoteExecutorError(f"{label}_invalid")
    return value


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _unb64(value, label):
    value = _text(value, label)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise TrustedRemoteExecutorError(f"{label}_invalid") from exc
    if _b64(raw) != value:
        raise TrustedRemoteExecutorError(f"{label}_not_canonical")
    return raw


def _utc(value, label):
    value = _text(value, label)
    if not value.endswith("Z"):
        raise TrustedRemoteExecutorError(f"{label}_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise TrustedRemoteExecutorError(f"{label}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed) or parsed.microsecond:
        raise TrustedRemoteExecutorError(f"{label}_invalid")
    return parsed.astimezone(timezone.utc), value


def _utc_now_text():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _relative_path(value, label, expected_prefix=None):
    value = _text(value, label)
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in ("", ".", "..") for part in path.parts):
        raise TrustedRemoteExecutorError(f"{label}_invalid")
    if expected_prefix is not None and (not path.parts or path.parts[0] != expected_prefix):
        raise TrustedRemoteExecutorError(f"{label}_scope_invalid")
    return value


def _identified(data, field, prefix):
    body = copy.deepcopy(dict(data))
    body[field] = prefix + "0" * 64
    expected = prefix + _sha(_dumps(body))
    if data.get(field) not in ("", prefix + "0" * 64, expected):
        raise TrustedRemoteExecutorError(f"{field}_invalid")
    body[field] = expected
    return body


def _plan(value):
    if type(value) is _records.TrustedRemoteActionTimePlan:
        raw = value.canonical_bytes()
    elif type(value) is bytes:
        raw = value
    else:
        raise TrustedRemoteExecutorError("action_time_plan_exact_type_or_bytes_required")
    try:
        replay = _records.TrustedRemoteActionTimePlan.from_bytes(raw)
    except Exception as exc:
        raise TrustedRemoteExecutorError("action_time_plan_replay_invalid") from exc
    if replay.canonical_bytes() != raw:
        raise TrustedRemoteExecutorError("action_time_plan_not_canonical")
    return replay.to_dict(), raw


def _file_binding(value, label, expected_prefix):
    value = _keys(value, ("relative_path", "byte_length", "sha256"), label)
    return {
        "relative_path": _relative_path(value["relative_path"], f"{label}_relative_path", expected_prefix),
        "byte_length": _integer(value["byte_length"], f"{label}_byte_length"),
        "sha256": _hex(value["sha256"], f"{label}_sha256"),
    }


def _canonical_json_document(raw, label):
    if not isinstance(raw, bytes) or not raw:
        raise TrustedRemoteExecutorError(f"{label}_bytes_invalid")
    value = _loads(raw)
    if _dumps(value) != raw:
        raise TrustedRemoteExecutorError(f"{label}_not_canonical")
    return value


def _prompt_artifact(raw, label):
    value = _canonical_json_document(raw, label)
    value = _keys(value, ("schema_version", "prompt_text", "provider_input_mode"), label)
    if value["schema_version"] != PROMPT_ARTIFACT_SCHEMA or value["provider_input_mode"] != "append_exact_provider_visible_input_utf8":
        raise TrustedRemoteExecutorError(f"{label}_contract_invalid")
    return {"schema_version": PROMPT_ARTIFACT_SCHEMA, "prompt_text": _text(value["prompt_text"], f"{label}_prompt_text"), "provider_input_mode": "append_exact_provider_visible_input_utf8"}


def _case_payload_bytes(value, label):
    value = _keys(value, ("provider_input", "prompt"), label)
    provider_input = value["provider_input"]
    prompt = value["prompt"]
    _canonical_json_document(provider_input, f"{label}_provider_input")
    _prompt_artifact(prompt, f"{label}_prompt")
    return {"provider_input": provider_input, "prompt": prompt}


def _normalize_public_key(value, label):
    value = _text(value, label)
    parts = value.strip().split()
    if len(parts) < 2 or not parts[0].startswith("ssh-"):
        raise TrustedRemoteExecutorError(f"{label}_invalid")
    try:
        base64.b64decode(parts[1].encode("ascii"), validate=True)
    except Exception as exc:
        raise TrustedRemoteExecutorError(f"{label}_invalid") from exc
    return " ".join(parts[:2])


def _runtime_execution(value, runtime_inventory):
    value = _keys(value, ("python_relative_path", "isolated_flags", "torch_module_relative_path", "transformers_module_relative_path", "expected_versions"), "runtime_execution")
    python_path = _relative_path(value["python_relative_path"], "runtime_python_relative_path", "bin")
    if python_path != "bin/python" or value["isolated_flags"] != ["-I", "-s", "-E"]:
        raise TrustedRemoteExecutorError("runtime_interpreter_contract_invalid")
    torch_path = _relative_path(value["torch_module_relative_path"], "torch_module_relative_path")
    transformers_path = _relative_path(value["transformers_module_relative_path"], "transformers_module_relative_path")
    versions = _keys(value["expected_versions"], ("python", "torch", "transformers", "cuda"), "runtime_expected_versions")
    versions = {key: _text(versions[key], f"runtime_expected_{key}") for key in versions}
    inventory_paths = {row["relative_path"] for row in runtime_inventory["artifacts"]}
    if not {python_path, torch_path, transformers_path}.issubset(inventory_paths):
        raise TrustedRemoteExecutorError("runtime_execution_files_not_in_inventory")
    return {"python_relative_path": python_path, "isolated_flags": ["-I", "-s", "-E"], "torch_module_relative_path": torch_path, "transformers_module_relative_path": transformers_path, "expected_versions": versions}


def _plan_binding(plan, plan_raw):
    return {
        "record_id": plan["record_id"],
        "sha256": _sha(plan_raw),
        "commit_sha": plan["policy"]["implementation_commit_sha"],
        "tree_sha": plan["policy"]["implementation_tree_sha"],
    }


def _inventory_binding(value, kind):
    if kind == "model":
        names = ("model_id", "repository", "exact_revision", "source_state", "files", "file_count", "total_bytes", "inventory_sha256", "tree_sha256")
        value = _keys(value, names, "model_inventory")
        if value["repository"] != MODEL_REPOSITORY or value["model_id"] != MODEL_ID or value["source_state"] != "recorded_not_verified_no_action":
            raise TrustedRemoteExecutorError("model_identity_invalid")
        _hex(value["exact_revision"], "model_exact_revision", 40)
        rows_key, count_key = "files", "file_count"
    else:
        names = ("runtime_id", "image_id", "source_state", "artifacts", "artifact_count", "total_bytes", "inventory_sha256")
        value = _keys(value, names, "runtime_inventory")
        if value["source_state"] != "recorded_not_verified_no_action":
            raise TrustedRemoteExecutorError("runtime_inventory_state_invalid")
        _text(value["runtime_id"], "runtime_id")
        _text(value["image_id"], "runtime_image_id")
        rows_key, count_key = "artifacts", "artifact_count"
    if not isinstance(value[rows_key], list) or not value[rows_key]:
        raise TrustedRemoteExecutorError(f"{kind}_inventory_files_invalid")
    rows = []
    for row in value[rows_key]:
        row = _keys(row, ("relative_path", "byte_length", "sha256"), f"{kind}_inventory_row")
        rows.append({"relative_path": _relative_path(row["relative_path"], f"{kind}_inventory_relative_path"), "byte_length": _integer(row["byte_length"], f"{kind}_inventory_byte_length", minimum=1), "sha256": _hex(row["sha256"], f"{kind}_inventory_sha256")})
    if rows != sorted(rows, key=lambda row: row["relative_path"]) or len({row["relative_path"] for row in rows}) != len(rows):
        raise TrustedRemoteExecutorError(f"{kind}_inventory_order_invalid")
    if value[count_key] != len(rows) or value["total_bytes"] != sum(row["byte_length"] for row in rows):
        raise TrustedRemoteExecutorError(f"{kind}_inventory_totals_invalid")
    return copy.deepcopy(dict(value))


def _worker_identity():
    if not _WORKER_SCRIPT.is_file() or _WORKER_SCRIPT.is_symlink():
        raise TrustedRemoteExecutorError("fixed_worker_script_unavailable")
    raw = _WORKER_SCRIPT.read_bytes()
    return {
        "relative_path": WORKER_RELATIVE_PATH,
        "sha256": _sha(raw),
        "byte_length": len(raw),
        "entrypoint": WORKER_ENTRYPOINT,
        "command_prefix": list(WORKER_COMMAND_PREFIX),
    }


def _validate_worker_identity(value):
    value = _keys(value, ("relative_path", "sha256", "byte_length", "entrypoint", "command_prefix"), "worker_identity")
    normalized = {
        "relative_path": _relative_path(value["relative_path"], "worker_relative_path", "scripts"),
        "sha256": _hex(value["sha256"], "worker_sha256"),
        "byte_length": _integer(value["byte_length"], "worker_byte_length", minimum=1),
        "entrypoint": _text(value["entrypoint"], "worker_entrypoint"),
        "command_prefix": list(value["command_prefix"]) if isinstance(value["command_prefix"], list) else None,
    }
    if normalized["relative_path"] != WORKER_RELATIVE_PATH or normalized["entrypoint"] != WORKER_ENTRYPOINT or normalized["command_prefix"] != list(WORKER_COMMAND_PREFIX):
        raise TrustedRemoteExecutorError("worker_identity_invalid")
    return normalized


class _CanonicalRecord:
    __slots__ = ("_data",)
    _validator = None

    def __init__(self, data):
        self._data = data

    @classmethod
    def from_dict(cls, value):
        return cls(cls._validator(copy.deepcopy(value)))

    @classmethod
    def from_bytes(cls, raw):
        value = _loads(raw)
        instance = cls.from_dict(value)
        if instance.canonical_bytes() != raw:
            raise TrustedRemoteExecutorError("record_not_canonical")
        return instance

    def validate(self):
        return type(self).from_dict(self._data)

    def to_dict(self):
        return copy.deepcopy(self.validate()._data)

    def canonical_bytes(self):
        return _dumps(self.validate()._data)

    def sha256(self):
        return _sha(self.canonical_bytes())


def _validate_package(value):
    names = ("schema_version", "package_id", "action_time_plan", "quality_profile", "model_inventory", "runtime_inventory", "runtime_execution", "expected_instance", "cases", "generation", "worker", "result_policy", "claims")
    value = _keys(value, names, "execution_package")
    if value["schema_version"] != EXECUTION_PACKAGE_SCHEMA:
        raise TrustedRemoteExecutorError("execution_package_schema_invalid")
    plan_binding = _keys(value["action_time_plan"], ("record_id", "sha256", "commit_sha", "tree_sha"), "package_plan_binding")
    _text(plan_binding["record_id"], "package_plan_record_id")
    _hex(plan_binding["sha256"], "package_plan_sha256")
    _hex(plan_binding["commit_sha"], "package_commit_sha", 40)
    _hex(plan_binding["tree_sha"], "package_tree_sha", 40)
    if value["quality_profile"] != QUALITY_PROFILE:
        raise TrustedRemoteExecutorError("quality_profile_invalid")
    model = _inventory_binding(value["model_inventory"], "model")
    runtime = _inventory_binding(value["runtime_inventory"], "runtime")
    runtime_execution = _runtime_execution(value["runtime_execution"], runtime)
    instance = _keys(value["expected_instance"], ("provider", "instance_id", "gpu_model", "gpu_index", "gpu_uuid", "ssh_host_key_sha256", "ssh_public_key_sha256"), "expected_instance")
    instance = {
        "provider": _text(instance["provider"], "instance_provider"),
        "instance_id": _text(instance["instance_id"], "instance_id"),
        "gpu_model": _text(instance["gpu_model"], "gpu_model"),
        "gpu_index": _integer(instance["gpu_index"], "gpu_index"),
        "gpu_uuid": _text(instance["gpu_uuid"], "gpu_uuid"),
        "ssh_host_key_sha256": _hex(instance["ssh_host_key_sha256"], "ssh_host_key_sha256"),
        "ssh_public_key_sha256": _hex(instance["ssh_public_key_sha256"], "ssh_public_key_sha256"),
    }
    if not isinstance(value["cases"], list) or [row.get("case_id") for row in value["cases"] if isinstance(row, Mapping)] != list(REQUIRED_CASE_IDS):
        raise TrustedRemoteExecutorError("execution_package_cases_invalid")
    cases = []
    for row in value["cases"]:
        row = _keys(row, ("case_id", "provider_input", "frozen_g0", "provider_input_file", "prompt_file"), "execution_package_case")
        cases.append({
            "case_id": _text(row["case_id"], "case_id"),
            "provider_input": copy.deepcopy(_keys(row["provider_input"], ("input_id", "sha256", "byte_length"), "provider_input_binding")),
            "frozen_g0": copy.deepcopy(_keys(row["frozen_g0"], ("package_id", "sha256"), "frozen_g0_binding")),
            "provider_input_file": _file_binding(row["provider_input_file"], "provider_input_file", "inputs"),
            "prompt_file": _file_binding(row["prompt_file"], "prompt_file", "inputs"),
        })
        _text(cases[-1]["provider_input"]["input_id"], "provider_input_id")
        _hex(cases[-1]["provider_input"]["sha256"], "provider_input_sha256")
        _integer(cases[-1]["provider_input"]["byte_length"], "provider_input_byte_length", minimum=1)
        _text(cases[-1]["frozen_g0"]["package_id"], "frozen_g0_package_id")
        _hex(cases[-1]["frozen_g0"]["sha256"], "frozen_g0_sha256")
        if cases[-1]["provider_input_file"]["sha256"] != cases[-1]["provider_input"]["sha256"] or cases[-1]["provider_input_file"]["byte_length"] != cases[-1]["provider_input"]["byte_length"]:
            raise TrustedRemoteExecutorError("provider_input_file_binding_invalid")
        if not cases[-1]["prompt_file"]["relative_path"].endswith("/prompt.json"):
            raise TrustedRemoteExecutorError("prompt_artifact_path_invalid")
    generation = _keys(value["generation"], ("enable_thinking", "do_sample", "max_new_tokens", "provider_call_limit_per_case", "retry_allowed", "text_only"), "generation")
    if generation != {"enable_thinking": False, "do_sample": False, "max_new_tokens": 4096, "provider_call_limit_per_case": 1, "retry_allowed": False, "text_only": True}:
        raise TrustedRemoteExecutorError("generation_contract_invalid")
    worker = _validate_worker_identity(value["worker"])
    result_policy = _keys(value["result_policy"], ("dedicated_root_required", "allowed_relative_paths", "max_file_count", "max_total_bytes", "raw_saved_before_parse"), "result_policy")
    expected_paths = [f"cases/{case_id}/raw_response.bin" for case_id in REQUIRED_CASE_IDS] + [_RESULT_RELATIVE_PATH]
    if result_policy["dedicated_root_required"] is not True or result_policy["allowed_relative_paths"] != expected_paths or result_policy["raw_saved_before_parse"] is not True:
        raise TrustedRemoteExecutorError("result_policy_invalid")
    _integer(result_policy["max_file_count"], "result_max_file_count", minimum=len(expected_paths))
    _integer(result_policy["max_total_bytes"], "result_max_total_bytes", minimum=1)
    if value["claims"] != {"maximum_state": "remote_execution_result_unattested", "verified_real_provider": False, "real_provider_assembled": False, "g1_g2_delivery_quality": False}:
        raise TrustedRemoteExecutorError("package_claims_invalid")
    normalized = {
        "schema_version": value["schema_version"], "package_id": value["package_id"], "action_time_plan": copy.deepcopy(dict(plan_binding)),
        "quality_profile": copy.deepcopy(QUALITY_PROFILE), "model_inventory": model, "runtime_inventory": runtime, "runtime_execution": runtime_execution,
        "expected_instance": instance, "cases": cases, "generation": copy.deepcopy(dict(generation)),
        "worker": worker, "result_policy": copy.deepcopy(dict(result_policy)), "claims": copy.deepcopy(dict(value["claims"])),
    }
    body = _identified(normalized, "package_id", "trusted-remote-execution-package-v2-")
    if value["package_id"] != body["package_id"]:
        raise TrustedRemoteExecutorError("package_id_invalid")
    return body


class TrustedRemoteExecutionPackageV2(_CanonicalRecord):
    _validator = staticmethod(_validate_package)


def prepare_trusted_remote_execution_package_v2(action_time_plan, case_payloads, expected_instance_facts, runtime_execution):
    plan, plan_raw = _plan(action_time_plan)
    if not isinstance(case_payloads, Mapping) or tuple(case_payloads) != REQUIRED_CASE_IDS:
        raise TrustedRemoteExecutorError("case_payload_coverage_invalid")
    cases = []
    for case in plan["cases"]:
        case_id = case["case_id"]
        payload = _case_payload_bytes(case_payloads[case_id], f"case_payload_{case_id}")
        input_path = f"inputs/{case_id}/provider_input.json"
        prompt_path = f"inputs/{case_id}/prompt.json"
        input_binding = {"relative_path": input_path, "byte_length": len(payload["provider_input"]), "sha256": _sha(payload["provider_input"])}
        prompt_binding = {"relative_path": prompt_path, "byte_length": len(payload["prompt"]), "sha256": _sha(payload["prompt"])}
        if input_binding["sha256"] != case["provider_input"]["sha256"] or input_binding["byte_length"] != case["provider_input"]["byte_length"]:
            raise TrustedRemoteExecutorError("provider_input_plan_binding_invalid")
        cases.append({"case_id": case_id, "provider_input": copy.deepcopy(case["provider_input"]), "frozen_g0": copy.deepcopy(case["frozen_g0"]), "provider_input_file": input_binding, "prompt_file": prompt_binding})
    expected_instance = copy.deepcopy(dict(expected_instance_facts))
    caps = plan["operational"]["result_return"]
    data = {
        "schema_version": EXECUTION_PACKAGE_SCHEMA,
        "package_id": "trusted-remote-execution-package-v2-" + "0" * 64,
        "action_time_plan": _plan_binding(plan, plan_raw),
        "quality_profile": copy.deepcopy(QUALITY_PROFILE),
        "model_inventory": copy.deepcopy(plan["model_inventory"]),
        "runtime_inventory": copy.deepcopy(plan["runtime_inventory"]),
        "runtime_execution": copy.deepcopy(dict(runtime_execution)),
        "expected_instance": expected_instance,
        "cases": cases,
        "generation": {"enable_thinking": False, "do_sample": False, "max_new_tokens": 4096, "provider_call_limit_per_case": 1, "retry_allowed": False, "text_only": True},
        "worker": _worker_identity(),
        "result_policy": {"dedicated_root_required": True, "allowed_relative_paths": [f"cases/{case_id}/raw_response.bin" for case_id in REQUIRED_CASE_IDS] + [_RESULT_RELATIVE_PATH], "max_file_count": caps["max_file_count"], "max_total_bytes": caps["max_total_bytes"], "raw_saved_before_parse": True},
        "claims": {"maximum_state": "remote_execution_result_unattested", "verified_real_provider": False, "real_provider_assembled": False, "g1_g2_delivery_quality": False},
    }
    return TrustedRemoteExecutionPackageV2.from_dict(_identified(data, "package_id", "trusted-remote-execution-package-v2-"))


def materialize_trusted_remote_execution_inputs_v2(package, package_root, case_payloads):
    package = _replay_package(package)
    root = _new_or_empty_root(package_root, "package_root")
    if not isinstance(case_payloads, Mapping) or tuple(case_payloads) != REQUIRED_CASE_IDS:
        raise TrustedRemoteExecutorError("case_payload_coverage_invalid")
    for row in package.to_dict()["cases"]:
        payload = _case_payload_bytes(case_payloads[row["case_id"]], f"case_payload_{row['case_id']}")
        for key, binding_key in (("provider_input", "provider_input_file"), ("prompt", "prompt_file")):
            binding = row[binding_key]
            raw = payload[key]
            if len(raw) != binding["byte_length"] or _sha(raw) != binding["sha256"]:
                raise TrustedRemoteExecutorError("case_payload_package_binding_invalid")
            target = _contained_target(root, binding["relative_path"], create_parents=True)
            target.write_bytes(raw)
    package_path = _contained_target(root, "execution_package.json", create_parents=False)
    package_path.write_bytes(package.canonical_bytes())
    return root


def _replay_package(value):
    if type(value) is TrustedRemoteExecutionPackageV2:
        raw = value.canonical_bytes()
    elif type(value) is bytes:
        raw = value
    else:
        raise TrustedRemoteExecutorError("execution_package_exact_type_or_bytes_required")
    replay = TrustedRemoteExecutionPackageV2.from_bytes(raw)
    return replay


def _validate_package_against_plan(package, action_time_plan):
    package = _replay_package(package)
    plan, plan_raw = _plan(action_time_plan)
    data = package.to_dict()
    if data["action_time_plan"] != _plan_binding(plan, plan_raw) or data["model_inventory"] != plan["model_inventory"] or data["runtime_inventory"] != plan["runtime_inventory"]:
        raise TrustedRemoteExecutorError("package_plan_cross_binding_invalid")
    if data["quality_profile"] != {"profile": plan["profile_selection"]["selected_profile"], "dtype": plan["precision"]["selected_dtype"], "quantization": plan["precision"]["selected_quantization"]}:
        raise TrustedRemoteExecutorError("package_profile_plan_binding_invalid")
    if data["expected_instance"]["provider"] != plan["operational"]["target_instance"]["provider"] or data["expected_instance"]["instance_id"] != plan["operational"]["target_instance"]["instance_id"]:
        raise TrustedRemoteExecutorError("package_instance_plan_binding_invalid")
    if data["expected_instance"]["gpu_model"] != plan["operational"]["gpu"]["model"] or data["expected_instance"]["gpu_index"] != plan["operational"]["gpu"]["selected_index"]:
        raise TrustedRemoteExecutorError("package_gpu_plan_binding_invalid")
    if data["expected_instance"]["ssh_host_key_sha256"] != plan["operational"]["ssh"]["host_key_sha256"] or data["expected_instance"]["ssh_public_key_sha256"] != plan["operational"]["ssh"]["temporary_public_key_sha256"]:
        raise TrustedRemoteExecutorError("package_ssh_plan_binding_invalid")
    for row, case in zip(data["cases"], plan["cases"]):
        if row["case_id"] != case["case_id"] or row["provider_input"] != case["provider_input"] or row["frozen_g0"] != case["frozen_g0"]:
            raise TrustedRemoteExecutorError("package_case_plan_binding_invalid")
    return package, plan, plan_raw


def _instance(value, plan):
    value = _keys(value, ("provider", "instance_id", "gpu_model", "gpu_index", "gpu_uuid", "ssh_host_key_sha256", "ssh_public_key_sha256"), "instance_facts")
    result = {
        "provider": _text(value["provider"], "instance_provider"), "instance_id": _text(value["instance_id"], "instance_id"),
        "gpu_model": _text(value["gpu_model"], "gpu_model"), "gpu_index": _integer(value["gpu_index"], "gpu_index"),
        "gpu_uuid": _text(value["gpu_uuid"], "gpu_uuid"), "ssh_host_key_sha256": _hex(value["ssh_host_key_sha256"], "ssh_host_key_sha256"),
        "ssh_public_key_sha256": _hex(value["ssh_public_key_sha256"], "ssh_public_key_sha256"),
    }
    package_like = {"provider": plan["operational"]["target_instance"]["provider"], "instance_id": plan["operational"]["target_instance"]["instance_id"], "gpu_model": plan["operational"]["gpu"]["model"], "gpu_index": plan["operational"]["gpu"]["selected_index"], "ssh_host_key_sha256": plan["operational"]["ssh"]["host_key_sha256"], "ssh_public_key_sha256": plan["operational"]["ssh"]["temporary_public_key_sha256"]}
    if any(result[key] != package_like[key] for key in package_like):
        raise TrustedRemoteExecutorError("instance_plan_binding_invalid")
    return result


@dataclass(frozen=True)
class _SignerBase:
    public_key: str
    namespace: str

    def __post_init__(self):
        if self.namespace != PRE_RUN_NAMESPACE:
            raise TrustedRemoteExecutorError("signer_namespace_invalid")
        parts = self.public_key.strip().split()
        if len(parts) < 2 or not parts[0].startswith("ssh-"):
            raise TrustedRemoteExecutorError("signer_public_key_invalid")
        try:
            base64.b64decode(parts[1].encode("ascii"), validate=True)
        except Exception as exc:
            raise TrustedRemoteExecutorError("signer_public_key_invalid") from exc

    @property
    def principal(self):
        return SIGNER_PRINCIPAL

    @property
    def key_sha256(self):
        return _sha(" ".join(self.public_key.strip().split()[:2]).encode("ascii"))

    def allowed_signers_text(self):
        return f"{self.principal} {' '.join(self.public_key.strip().split()[:2])}\n"


class ExpectedPreRunSigner(_SignerBase):
    def __init__(self, public_key):
        super().__init__(public_key, PRE_RUN_NAMESPACE)


def build_trusted_remote_pre_run_authorization_payload_v2(action_time_plan, execution_package, expected_instance_facts, nonce, issued_at_utc, expires_at_utc):
    package, plan, plan_raw = _validate_package_against_plan(execution_package, action_time_plan)
    issued, issued_text = _utc(issued_at_utc, "issued_at_utc")
    expires, expires_text = _utc(expires_at_utc, "expires_at_utc")
    if expires <= issued or expires - issued > MAX_AUTHORIZATION_WINDOW:
        raise TrustedRemoteExecutorError("authorization_window_invalid")
    nonce = _hex(nonce, "nonce")
    instance = _instance(expected_instance_facts, plan)
    if instance != package.to_dict()["expected_instance"]:
        raise TrustedRemoteExecutorError("authorization_instance_package_binding_invalid")
    return {
        "schema_version": PRE_RUN_PAYLOAD_SCHEMA, "namespace": PRE_RUN_NAMESPACE,
        "action_time_plan": _plan_binding(plan, plan_raw),
        "execution_package": {"package_id": package.to_dict()["package_id"], "sha256": package.sha256()},
        "instance": instance, "cases": [{"case_id": row["case_id"], "provider_input": row["provider_input"], "frozen_g0": row["frozen_g0"], "provider_input_file": row["provider_input_file"], "prompt_file": row["prompt_file"]} for row in package.to_dict()["cases"]],
        "model": {"repository": plan["model_inventory"]["repository"], "model_id": plan["model_inventory"]["model_id"], "exact_revision": plan["model_inventory"]["exact_revision"], "inventory_sha256": plan["model_inventory"]["inventory_sha256"], "tree_sha256": plan["model_inventory"]["tree_sha256"]},
        "runtime": {"runtime_id": plan["runtime_inventory"]["runtime_id"], "image_id": plan["runtime_inventory"]["image_id"], "inventory_sha256": plan["runtime_inventory"]["inventory_sha256"]},
        "runtime_execution": copy.deepcopy(package.to_dict()["runtime_execution"]),
        "profile": copy.deepcopy(QUALITY_PROFILE), "worker": copy.deepcopy(package.to_dict()["worker"]), "caps": copy.deepcopy(plan["operational"]["caps"]),
        "result_policy": copy.deepcopy(package.to_dict()["result_policy"]), "nonce": nonce, "issued_at_utc": issued_text, "expires_at_utc": expires_text,
        "unknown_post_run_values": {"raw_response_bytes": "not_prebound", "runtime_observation": "not_prebound", "completion_state": "not_prebound"},
    }


def _validate_pre_payload(value):
    names = ("schema_version", "namespace", "action_time_plan", "execution_package", "instance", "cases", "model", "runtime", "runtime_execution", "profile", "worker", "caps", "result_policy", "nonce", "issued_at_utc", "expires_at_utc", "unknown_post_run_values")
    value = _keys(value, names, "pre_run_payload")
    if value["schema_version"] != PRE_RUN_PAYLOAD_SCHEMA or value["namespace"] != PRE_RUN_NAMESPACE:
        raise TrustedRemoteExecutorError("pre_run_payload_schema_invalid")
    if value["unknown_post_run_values"] != {"raw_response_bytes": "not_prebound", "runtime_observation": "not_prebound", "completion_state": "not_prebound"}:
        raise TrustedRemoteExecutorError("pre_run_payload_unknown_values_invalid")
    _hex(value["nonce"], "nonce")
    issued, _ = _utc(value["issued_at_utc"], "issued_at_utc")
    expires, _ = _utc(value["expires_at_utc"], "expires_at_utc")
    if expires <= issued or expires - issued > MAX_AUTHORIZATION_WINDOW:
        raise TrustedRemoteExecutorError("authorization_window_invalid")
    return copy.deepcopy(dict(value))


def _validate_signature_receipt(value, schema, namespace, prefix):
    value = _keys(value, ("schema_version", "receipt_id", "signer_key_sha256", "signer_principal", "namespace", "signed_payload_base64", "signature_base64"), "signature_receipt")
    if value["schema_version"] != schema or value["namespace"] != namespace or value["signer_principal"] != SIGNER_PRINCIPAL:
        raise TrustedRemoteExecutorError("signature_receipt_identity_invalid")
    result = {"schema_version": schema, "receipt_id": _text(value["receipt_id"], "receipt_id"), "signer_key_sha256": _hex(value["signer_key_sha256"], "signer_key_sha256"), "signer_principal": SIGNER_PRINCIPAL, "namespace": namespace, "signed_payload_base64": _b64(_unb64(value["signed_payload_base64"], "signed_payload_base64")), "signature_base64": _b64(_unb64(value["signature_base64"], "signature_base64"))}
    body = _identified(result, "receipt_id", prefix)
    if value["receipt_id"] != body["receipt_id"]:
        raise TrustedRemoteExecutorError("receipt_id_invalid")
    return body


class TrustedRemotePreRunAuthorizationReceiptV2(_CanonicalRecord):
    _validator = staticmethod(lambda value: _validate_signature_receipt(value, PRE_RUN_RECEIPT_SCHEMA, PRE_RUN_NAMESPACE, "trusted-remote-pre-run-receipt-v2-"))


def _create_receipt(payload, signature_bytes, signer, schema, namespace, prefix, record_type, payload_validator):
    if not isinstance(payload, Mapping) or not isinstance(signature_bytes, bytes) or not signature_bytes:
        raise TrustedRemoteExecutorError("signed_payload_or_signature_invalid")
    payload = payload_validator(payload)
    if signer.namespace != namespace:
        raise TrustedRemoteExecutorError("signer_namespace_invalid")
    raw = _dumps(payload)
    data = {"schema_version": schema, "receipt_id": prefix + "0" * 64, "signer_key_sha256": signer.key_sha256, "signer_principal": SIGNER_PRINCIPAL, "namespace": namespace, "signed_payload_base64": _b64(raw), "signature_base64": _b64(signature_bytes)}
    return record_type.from_dict(_identified(data, "receipt_id", prefix))


def create_trusted_remote_pre_run_authorization_receipt_v2(payload, signature_bytes, expected_signer):
    if type(expected_signer) is not ExpectedPreRunSigner:
        raise TrustedRemoteExecutorError("expected_pre_run_signer_exact_type_required")
    return _create_receipt(payload, signature_bytes, expected_signer, PRE_RUN_RECEIPT_SCHEMA, PRE_RUN_NAMESPACE, "trusted-remote-pre-run-receipt-v2-", TrustedRemotePreRunAuthorizationReceiptV2, _validate_pre_payload)


def _verify_openssh(payload_raw, signature_raw, signer):
    executable = shutil.which("ssh-keygen")
    if executable is None:
        raise TrustedRemoteExecutorError("openssh_verifier_unavailable")
    root = Path(tempfile.gettempdir()) / ("req2web-executor-signature-v2-" + uuid.uuid4().hex)
    try:
        os.mkdir(root, 0o777)
        allowed = root / "allowed_signers"
        signature = root / "payload.sig"
        allowed.write_text(signer.allowed_signers_text(), encoding="utf-8", newline="\n")
        signature.write_bytes(signature_raw)
        result = subprocess.run([executable, "-Y", "verify", "-f", str(allowed), "-I", signer.principal, "-n", signer.namespace, "-s", str(signature)], input=payload_raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrustedRemoteExecutorError("openssh_signature_verification_failed") from exc
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if result.returncode != 0:
        raise TrustedRemoteExecutorError("openssh_signature_invalid")


def _verify_receipt(receipt, record_type, signer_type, signer, namespace, payload_validator):
    if type(signer) is not signer_type:
        raise TrustedRemoteExecutorError("expected_signer_exact_type_required")
    if type(receipt) is record_type:
        raw = receipt.canonical_bytes()
    elif type(receipt) is bytes:
        raw = receipt
    else:
        raise TrustedRemoteExecutorError("signature_receipt_exact_type_or_bytes_required")
    replay = record_type.from_bytes(raw)
    data = replay.to_dict()
    if data["signer_key_sha256"] != signer.key_sha256 or data["signer_principal"] != signer.principal or data["namespace"] != namespace:
        raise TrustedRemoteExecutorError("signature_receipt_expected_signer_mismatch")
    payload_raw = _unb64(data["signed_payload_base64"], "signed_payload_base64")
    payload = payload_validator(_loads(payload_raw))
    if _dumps(payload) != payload_raw:
        raise TrustedRemoteExecutorError("signed_payload_not_canonical")
    _verify_openssh(payload_raw, _unb64(data["signature_base64"], "signature_base64"), signer)
    return replay, payload


def _verify_pre_run_authorization_core(receipt, action_time_plan, execution_package, expected_instance_facts, expected_signer, *, enforce_current_window):
    package, plan, _ = _validate_package_against_plan(execution_package, action_time_plan)
    replay, payload = _verify_receipt(receipt, TrustedRemotePreRunAuthorizationReceiptV2, ExpectedPreRunSigner, expected_signer, PRE_RUN_NAMESPACE, _validate_pre_payload)
    expected = build_trusted_remote_pre_run_authorization_payload_v2(action_time_plan, package, expected_instance_facts, payload["nonce"], payload["issued_at_utc"], payload["expires_at_utc"])
    if payload != expected:
        raise TrustedRemoteExecutorError("pre_run_authorization_cross_binding_invalid")
    issued, _ = _utc(payload["issued_at_utc"], "issued_at_utc")
    expires, _ = _utc(payload["expires_at_utc"], "expires_at_utc")
    if enforce_current_window:
        now, _ = _utc(_utc_now_text(), "internal_now_utc")
        if now < issued or now > expires:
            raise TrustedRemoteExecutorError("pre_run_authorization_expired_or_not_yet_valid")
    if expected_signer.key_sha256 != plan["operational"]["ssh"]["temporary_public_key_sha256"]:
        raise TrustedRemoteExecutorError("pre_run_signer_plan_binding_invalid")
    return replay, payload


def verify_trusted_remote_pre_run_authorization_receipt_v2(receipt, action_time_plan, execution_package, expected_instance_facts, expected_signer):
    return _verify_pre_run_authorization_core(receipt, action_time_plan, execution_package, expected_instance_facts, expected_signer, enforce_current_window=True)[0]


def authorize_trusted_remote_execution_once_v2(receipt, action_time_plan, execution_package, expected_instance_facts, expected_signer):
    replay, payload = _verify_pre_run_authorization_core(receipt, action_time_plan, execution_package, expected_instance_facts, expected_signer, enforce_current_window=True)
    key = (expected_signer.key_sha256, payload["action_time_plan"]["record_id"], payload["execution_package"]["package_id"], payload["nonce"])
    with _PROCESS_NONCE_LOCK:
        if key in _PROCESS_CONSUMED_PRE_RUN_NONCES:
            raise TrustedRemoteExecutorError("pre_run_authorization_nonce_replay")
        _PROCESS_CONSUMED_PRE_RUN_NONCES.add(key)
    return replay


def _validate_actual_runtime(value, expected_instance):
    names = ("python", "torch", "transformers", "cuda", "executable_path", "torch_module_path", "transformers_module_path", "device", "gpu_index", "gpu_uuid", "gpu_model", "dtype", "quantization", "model_repository", "model_revision", "model_inventory_sha256", "runtime_inventory_sha256", "model_loaded")
    value = _keys(value, names, "actual_runtime")
    text_keys = ("python", "torch", "transformers", "cuda", "executable_path", "torch_module_path", "transformers_module_path", "device", "gpu_uuid", "gpu_model", "dtype", "quantization", "model_repository", "model_revision", "model_inventory_sha256", "runtime_inventory_sha256")
    result = {key: _text(value[key], f"actual_runtime_{key}") for key in text_keys}
    result["gpu_index"] = _integer(value["gpu_index"], "actual_runtime_gpu_index")
    result["model_loaded"] = _boolean(value["model_loaded"], "actual_runtime_model_loaded")
    if result["device"] != f"cuda:{expected_instance['gpu_index']}" or result["gpu_index"] != expected_instance["gpu_index"] or result["gpu_uuid"] != expected_instance["gpu_uuid"] or result["gpu_model"] != expected_instance["gpu_model"]:
        raise TrustedRemoteExecutorError("actual_device_identity_invalid")
    if result["dtype"] != "bf16" or result["quantization"] != "none":
        raise TrustedRemoteExecutorError("actual_precision_invalid")
    return result


def _validate_result(value):
    names = ("schema_version", "result_id", "state", "authorization_binding", "package_binding", "actual_runtime", "execution", "cases", "result_inventory", "test_only", "claims")
    value = _keys(value, names, "execution_result")
    if value["schema_version"] != EXECUTION_RESULT_SCHEMA or value["state"] != "remote_execution_result_unattested":
        raise TrustedRemoteExecutorError("execution_result_state_invalid")
    auth = copy.deepcopy(dict(_keys(value["authorization_binding"], ("receipt_id", "sha256"), "authorization_binding")))
    _text(auth["receipt_id"], "authorization_receipt_id"); _hex(auth["sha256"], "authorization_receipt_sha256")
    package = copy.deepcopy(dict(_keys(value["package_binding"], ("package_id", "sha256"), "package_binding")))
    _text(package["package_id"], "package_id"); _hex(package["sha256"], "package_sha256")
    expected_instance = value.get("actual_runtime") if isinstance(value.get("actual_runtime"), Mapping) else {}
    actual = _validate_actual_runtime(value["actual_runtime"], {"gpu_index": expected_instance.get("gpu_index"), "gpu_uuid": expected_instance.get("gpu_uuid"), "gpu_model": expected_instance.get("gpu_model")})
    execution = _keys(value["execution"], ("completion_state", "timeout_state", "cancel_state", "provider_call_count", "retry_count", "started_at_utc", "completed_at_utc", "elapsed_millis"), "execution")
    if execution["completion_state"] not in ("completed", "failed", "timed_out", "cancelled") or execution["timeout_state"] not in ("not_timed_out", "timed_out") or execution["cancel_state"] not in ("not_requested", "requested"):
        raise TrustedRemoteExecutorError("execution_completion_state_invalid")
    _integer(execution["provider_call_count"], "provider_call_count"); _integer(execution["retry_count"], "retry_count")
    _utc(execution["started_at_utc"], "started_at_utc"); _utc(execution["completed_at_utc"], "completed_at_utc"); _integer(execution["elapsed_millis"], "elapsed_millis")
    if execution["retry_count"] != 0 or execution["provider_call_count"] > len(REQUIRED_CASE_IDS):
        raise TrustedRemoteExecutorError("execution_call_count_invalid")
    if not isinstance(value["cases"], list) or [row.get("case_id") for row in value["cases"] if isinstance(row, Mapping)] != list(REQUIRED_CASE_IDS):
        raise TrustedRemoteExecutorError("execution_result_cases_invalid")
    cases = []
    files = []
    for row in value["cases"]:
        row = _keys(row, ("case_id", "call_state", "provider_call_count", "raw_response_base64", "raw_response_byte_length", "raw_response_sha256", "raw_saved_relative_path", "semantic_parse_state"), "execution_result_case")
        raw = _unb64(row["raw_response_base64"], "raw_response_base64")
        normalized = {"case_id": _text(row["case_id"], "case_id"), "call_state": _text(row["call_state"], "call_state"), "provider_call_count": _integer(row["provider_call_count"], "case_provider_call_count"), "raw_response_base64": _b64(raw), "raw_response_byte_length": _integer(row["raw_response_byte_length"], "raw_response_byte_length"), "raw_response_sha256": _hex(row["raw_response_sha256"], "raw_response_sha256"), "raw_saved_relative_path": _relative_path(row["raw_saved_relative_path"], "raw_saved_relative_path", "cases"), "semantic_parse_state": _text(row["semantic_parse_state"], "semantic_parse_state")}
        expected_path = f"cases/{normalized['case_id']}/raw_response.bin"
        if normalized["raw_saved_relative_path"] != expected_path or normalized["raw_response_byte_length"] != len(raw) or normalized["raw_response_sha256"] != _sha(raw) or normalized["semantic_parse_state"] != "not_started_raw_saved_first":
            raise TrustedRemoteExecutorError("raw_response_binding_invalid")
        if normalized["call_state"] == "completed" and (not raw or normalized["provider_call_count"] != 1):
            raise TrustedRemoteExecutorError("completed_case_call_invalid")
        cases.append(normalized)
        files.append({"relative_path": expected_path, "byte_length": len(raw), "sha256": _sha(raw)})
    inventory = _keys(value["result_inventory"], ("files", "file_count", "total_bytes", "inventory_sha256"), "result_inventory")
    if inventory["files"] != files or inventory["file_count"] != len(files) or inventory["total_bytes"] != sum(row["byte_length"] for row in files) or inventory["inventory_sha256"] != _sha(_dumps(files)):
        raise TrustedRemoteExecutorError("result_inventory_invalid")
    test_only = _boolean(value["test_only"], "test_only")
    claims = value["claims"]
    if claims != {"verified_real_provider": False, "real_provider_assembled": False, "g1_g2_delivery_quality": False}:
        raise TrustedRemoteExecutorError("execution_result_claims_invalid")
    normalized = {"schema_version": value["schema_version"], "result_id": value["result_id"], "state": value["state"], "authorization_binding": auth, "package_binding": package, "actual_runtime": actual, "execution": copy.deepcopy(dict(execution)), "cases": cases, "result_inventory": copy.deepcopy(dict(inventory)), "test_only": test_only, "claims": copy.deepcopy(dict(claims))}
    body = _identified(normalized, "result_id", "trusted-remote-execution-result-v2-")
    if value["result_id"] != body["result_id"]:
        raise TrustedRemoteExecutorError("execution_result_id_invalid")
    return body


class TrustedRemoteExecutionResultV2(_CanonicalRecord):
    _validator = staticmethod(_validate_result)


def _create_execution_result(pre_run_receipt, package, actual_runtime, case_raw_bytes, started_at_utc, completed_at_utc, elapsed_millis, *, test_only):
    receipt = _replay_pre_receipt(pre_run_receipt)
    package = _replay_package(package)
    if not isinstance(case_raw_bytes, Mapping) or tuple(case_raw_bytes) != REQUIRED_CASE_IDS:
        raise TrustedRemoteExecutorError("case_raw_coverage_invalid")
    cases = []
    files = []
    for case_id in REQUIRED_CASE_IDS:
        raw = case_raw_bytes[case_id]
        if not isinstance(raw, bytes) or not raw:
            raise TrustedRemoteExecutorError("case_raw_bytes_invalid")
        path = f"cases/{case_id}/raw_response.bin"
        cases.append({"case_id": case_id, "call_state": "completed", "provider_call_count": 1, "raw_response_base64": _b64(raw), "raw_response_byte_length": len(raw), "raw_response_sha256": _sha(raw), "raw_saved_relative_path": path, "semantic_parse_state": "not_started_raw_saved_first"})
        files.append({"relative_path": path, "byte_length": len(raw), "sha256": _sha(raw)})
    data = {"schema_version": EXECUTION_RESULT_SCHEMA, "result_id": "trusted-remote-execution-result-v2-" + "0" * 64, "state": "remote_execution_result_unattested", "authorization_binding": {"receipt_id": receipt.to_dict()["receipt_id"], "sha256": receipt.sha256()}, "package_binding": {"package_id": package.to_dict()["package_id"], "sha256": package.sha256()}, "actual_runtime": copy.deepcopy(dict(actual_runtime)), "execution": {"completion_state": "completed", "timeout_state": "not_timed_out", "cancel_state": "not_requested", "provider_call_count": len(REQUIRED_CASE_IDS), "retry_count": 0, "started_at_utc": started_at_utc, "completed_at_utc": completed_at_utc, "elapsed_millis": elapsed_millis}, "cases": cases, "result_inventory": {"files": files, "file_count": len(files), "total_bytes": sum(row["byte_length"] for row in files), "inventory_sha256": _sha(_dumps(files))}, "test_only": bool(test_only), "claims": {"verified_real_provider": False, "real_provider_assembled": False, "g1_g2_delivery_quality": False}}
    return TrustedRemoteExecutionResultV2.from_dict(_identified(data, "result_id", "trusted-remote-execution-result-v2-"))


def _create_test_only_execution_result_v2(pre_run_receipt, package, actual_runtime, case_raw_bytes, started_at_utc, completed_at_utc, elapsed_millis):
    return _create_execution_result(pre_run_receipt, package, actual_runtime, case_raw_bytes, started_at_utc, completed_at_utc, elapsed_millis, test_only=True)


def _replay_pre_receipt(value):
    if type(value) is TrustedRemotePreRunAuthorizationReceiptV2:
        raw = value.canonical_bytes()
    elif type(value) is bytes:
        raw = value
    else:
        raise TrustedRemoteExecutorError("pre_run_receipt_exact_type_or_bytes_required")
    return TrustedRemotePreRunAuthorizationReceiptV2.from_bytes(raw)


def _replay_result(value):
    if type(value) is TrustedRemoteExecutionResultV2:
        raw = value.canonical_bytes()
    elif type(value) is bytes:
        raw = value
    else:
        raise TrustedRemoteExecutorError("execution_result_exact_type_or_bytes_required")
    return TrustedRemoteExecutionResultV2.from_bytes(raw)


def validate_execution_result_against_package_v2(execution_result, execution_package):
    result = _replay_result(execution_result)
    package = _replay_package(execution_package)
    result_data = result.to_dict()
    package_data = package.to_dict()
    if result_data["package_binding"] != {"package_id": package_data["package_id"], "sha256": package.sha256()}:
        raise TrustedRemoteExecutorError("execution_result_package_binding_invalid")
    actual = result_data["actual_runtime"]
    expected = package_data["expected_instance"]
    if actual["device"] != f"cuda:{expected['gpu_index']}" or actual["gpu_index"] != expected["gpu_index"] or actual["gpu_uuid"] != expected["gpu_uuid"] or actual["gpu_model"] != expected["gpu_model"]:
        raise TrustedRemoteExecutorError("execution_result_device_binding_invalid")
    if actual["dtype"] != package_data["quality_profile"]["dtype"] or actual["quantization"] != package_data["quality_profile"]["quantization"]:
        raise TrustedRemoteExecutorError("execution_result_precision_binding_invalid")
    if actual["model_repository"] != package_data["model_inventory"]["repository"] or actual["model_revision"] != package_data["model_inventory"]["exact_revision"] or actual["model_inventory_sha256"] != package_data["model_inventory"]["inventory_sha256"] or actual["runtime_inventory_sha256"] != package_data["runtime_inventory"]["inventory_sha256"]:
        raise TrustedRemoteExecutorError("execution_result_inventory_binding_invalid")
    runtime = package_data["runtime_execution"]
    versions = runtime["expected_versions"]
    if any(actual[key] != versions[key] for key in ("python", "torch", "transformers", "cuda")):
        raise TrustedRemoteExecutorError("execution_result_runtime_version_binding_invalid")
    if actual["executable_path"] != runtime["python_relative_path"] or actual["torch_module_path"] != runtime["torch_module_relative_path"] or actual["transformers_module_path"] != runtime["transformers_module_relative_path"]:
        raise TrustedRemoteExecutorError("execution_result_runtime_path_binding_invalid")
    policy = package_data["result_policy"]
    inventory = result_data["result_inventory"]
    on_disk_total = inventory["total_bytes"] + len(result.canonical_bytes())
    if inventory["file_count"] + 1 > policy["max_file_count"] or on_disk_total > policy["max_total_bytes"]:
        raise TrustedRemoteExecutorError("execution_result_cap_exceeded")
    return result


def _root(path_value, label, *, must_exist=True):
    path = Path(path_value)
    if not path.is_absolute():
        raise TrustedRemoteExecutorError(f"{label}_absolute_path_required")
    if must_exist and (not path.exists() or not path.is_dir() or path.is_symlink()):
        raise TrustedRemoteExecutorError(f"{label}_invalid")
    return path.resolve(strict=must_exist)


def _new_or_empty_root(path_value, label):
    path = Path(path_value)
    if not path.is_absolute() or path.is_symlink():
        raise TrustedRemoteExecutorError(f"{label}_invalid")
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise TrustedRemoteExecutorError(f"{label}_not_empty")
    else:
        path.mkdir(parents=True)
    return path.resolve(strict=True)


def _contained_target(root, relative_path, *, create_parents):
    relative_path = _relative_path(relative_path, "relative_path")
    target = root.joinpath(*PurePosixPath(relative_path).parts)
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = target.parent.resolve(strict=True)
    if resolved_parent != root and root not in resolved_parent.parents:
        raise TrustedRemoteExecutorError("path_containment_invalid")
    if target.exists() and (target.is_symlink() or not target.is_file()):
        raise TrustedRemoteExecutorError("path_type_invalid")
    return target


def validate_execution_package_files_v2(package, package_root):
    package = _replay_package(package)
    root = _root(package_root, "package_root")
    for row in package.to_dict()["cases"]:
        for key in ("provider_input_file", "prompt_file"):
            binding = row[key]
            target = _contained_target(root, binding["relative_path"], create_parents=False)
            if not target.is_file() or target.is_symlink():
                raise TrustedRemoteExecutorError("package_input_file_invalid")
            raw = target.read_bytes()
            if len(raw) != binding["byte_length"] or _sha(raw) != binding["sha256"]:
                raise TrustedRemoteExecutorError("package_input_file_binding_invalid")
            if key == "provider_input_file":
                _canonical_json_document(raw, "provider_visible_input")
            else:
                _prompt_artifact(raw, "prompt_artifact")
    return package


def _validate_inventory_root(root_value, inventory, label):
    root = _root(root_value, f"{label}_root")
    rows = inventory["files"] if "files" in inventory else inventory["artifacts"]
    expected = {row["relative_path"]: row for row in rows}
    actual = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TrustedRemoteExecutorError(f"{label}_inventory_symlink_forbidden")
        if path.is_dir():
            continue
        if not path.is_file():
            raise TrustedRemoteExecutorError(f"{label}_inventory_non_regular_forbidden")
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        actual[relative] = {"relative_path": relative, "byte_length": len(raw), "sha256": _sha(raw)}
    if actual != expected:
        raise TrustedRemoteExecutorError(f"{label}_inventory_root_mismatch")
    return root


def validate_trusted_remote_result_root_v2(result, result_root, package):
    package = _replay_package(package)
    result = validate_execution_result_against_package_v2(result, package)
    root = _root(result_root, "result_root")
    allowed = set(package.to_dict()["result_policy"]["allowed_relative_paths"])
    actual_paths = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TrustedRemoteExecutorError("result_root_symlink_forbidden")
        if path.is_dir():
            continue
        if not path.is_file():
            raise TrustedRemoteExecutorError("result_root_non_regular_forbidden")
        relative = path.relative_to(root).as_posix()
        if relative not in allowed:
            raise TrustedRemoteExecutorError("result_root_path_not_allowed")
        raw = path.read_bytes(); total += len(raw); actual_paths.append(relative)
    policy = package.to_dict()["result_policy"]
    if len(actual_paths) > policy["max_file_count"] or total > policy["max_total_bytes"]:
        raise TrustedRemoteExecutorError("result_root_cap_exceeded")
    result_data = result.to_dict()
    for row in result_data["cases"]:
        target = _contained_target(root, row["raw_saved_relative_path"], create_parents=False)
        raw = target.read_bytes()
        if _b64(raw) != row["raw_response_base64"] or len(raw) != row["raw_response_byte_length"] or _sha(raw) != row["raw_response_sha256"]:
            raise TrustedRemoteExecutorError("result_root_raw_binding_invalid")
    record_path = _contained_target(root, _RESULT_RELATIVE_PATH, create_parents=False)
    if record_path.read_bytes() != result.canonical_bytes():
        raise TrustedRemoteExecutorError("result_root_record_binding_invalid")
    return result


def _validate_repository_root(repository_root, plan):
    root = _root(repository_root, "repository_root")
    try:
        manifest = _archive.RepositoryArchiveManifest.from_dict(plan["archive_manifest"]).to_dict()
    except Exception as exc:
        raise TrustedRemoteExecutorError("repository_archive_manifest_invalid") from exc
    expected = {row["path"]: row for row in manifest["files"]}
    actual = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TrustedRemoteExecutorError("repository_root_symlink_forbidden")
        if path.is_dir():
            continue
        if not path.is_file():
            raise TrustedRemoteExecutorError("repository_root_non_regular_forbidden")
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        actual[relative] = {"byte_length": len(raw), "sha256": _sha(raw)}
    if set(actual) != set(expected):
        raise TrustedRemoteExecutorError("repository_root_path_inventory_mismatch")
    for relative, row in expected.items():
        if actual[relative] != {"byte_length": row["byte_length"], "sha256": row["sha256"]}:
            raise TrustedRemoteExecutorError("repository_root_file_binding_mismatch")
    if any((root / Path(row["path"])).exists() for row in manifest["excluded_files"]):
        raise TrustedRemoteExecutorError("repository_root_excluded_material_present")
    return root


def _worker_script_at_repository_root(repository_root, package):
    root = _root(repository_root, "repository_root")
    target = _contained_target(root, WORKER_RELATIVE_PATH, create_parents=False)
    if not target.is_file() or target.is_symlink():
        raise TrustedRemoteExecutorError("fixed_worker_script_unavailable")
    raw = target.read_bytes()
    worker = package.to_dict()["worker"]
    if len(raw) != worker["byte_length"] or _sha(raw) != worker["sha256"]:
        raise TrustedRemoteExecutorError("fixed_worker_identity_drift")
    return target.resolve(strict=True)


def _runtime_file(runtime_root, relative_path, label):
    root = _root(runtime_root, "runtime_root")
    target = _contained_target(root, relative_path, create_parents=False)
    if not target.is_file() or target.is_symlink():
        raise TrustedRemoteExecutorError(f"{label}_invalid")
    return target.resolve(strict=True)


def _runtime_interpreter(package, runtime_root):
    package = _replay_package(package)
    relative = package.to_dict()["runtime_execution"]["python_relative_path"]
    interpreter = _runtime_file(runtime_root, relative, "runtime_interpreter")
    if sys.platform == "linux" and not os.access(interpreter, os.X_OK):
        raise TrustedRemoteExecutorError("runtime_interpreter_not_executable")
    return interpreter


def _relative_to_verified_root(path_value, root_value, label):
    root = _root(root_value, f"{label}_root")
    path = Path(path_value).resolve(strict=True)
    if path != root and root not in path.parents:
        raise TrustedRemoteExecutorError(f"{label}_outside_runtime_root")
    return path.relative_to(root).as_posix()


def _compose_exact_model_text(package_root, case_row):
    root = _root(package_root, "package_root")
    prompt_binding = case_row["prompt_file"]
    input_binding = case_row["provider_input_file"]
    prompt_path = _contained_target(root, prompt_binding["relative_path"], create_parents=False)
    input_path = _contained_target(root, input_binding["relative_path"], create_parents=False)
    prompt_raw = prompt_path.read_bytes()
    provider_raw = input_path.read_bytes()
    if len(prompt_raw) != prompt_binding["byte_length"] or _sha(prompt_raw) != prompt_binding["sha256"]:
        raise TrustedRemoteExecutorError("prompt_artifact_file_binding_invalid")
    if len(provider_raw) != input_binding["byte_length"] or _sha(provider_raw) != input_binding["sha256"]:
        raise TrustedRemoteExecutorError("provider_input_file_binding_invalid")
    prompt = _prompt_artifact(prompt_raw, "prompt_artifact")
    _canonical_json_document(provider_raw, "provider_visible_input")
    provider_text = provider_raw.decode("utf-8")
    if not provider_text:
        raise TrustedRemoteExecutorError("provider_visible_input_empty")
    return prompt["prompt_text"] + "\n\n<provider_visible_input>\n" + provider_text + "\n</provider_visible_input>"


def _nvidia_smi_device(index):
    command = ["nvidia-smi", f"--id={index}", "--query-gpu=index,uuid,name,memory.total", "--format=csv,noheader,nounits"]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30, text=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrustedRemoteExecutorError("nvidia_smi_unavailable") from exc
    if result.returncode != 0 or len(result.stdout.strip().splitlines()) != 1:
        raise TrustedRemoteExecutorError("nvidia_smi_invalid")
    parts = [part.strip() for part in result.stdout.strip().split(",")]
    if len(parts) != 4 or int(parts[0]) != index:
        raise TrustedRemoteExecutorError("nvidia_smi_invalid")
    return {"gpu_index": index, "gpu_uuid": parts[1], "gpu_model": parts[2], "vram_mib": int(parts[3])}


def _actual_runtime_facts(torch, transformers, expected, package, runtime_root):
    package_data = package.to_dict() if type(package) is TrustedRemoteExecutionPackageV2 else package
    runtime = package_data["runtime_execution"]
    if sys.flags.isolated != 1 or sys.flags.no_user_site != 1 or sys.flags.ignore_environment != 1:
        raise TrustedRemoteExecutorError("runtime_isolation_flags_invalid")
    if any(os.environ.get(key) for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE")):
        raise TrustedRemoteExecutorError("runtime_environment_injection_detected")
    executable_relative = _relative_to_verified_root(sys.executable, runtime_root, "runtime_executable")
    torch_relative = _relative_to_verified_root(torch.__file__, runtime_root, "torch_module")
    transformers_relative = _relative_to_verified_root(transformers.__file__, runtime_root, "transformers_module")
    if executable_relative != runtime["python_relative_path"] or torch_relative != runtime["torch_module_relative_path"] or transformers_relative != runtime["transformers_module_relative_path"]:
        raise TrustedRemoteExecutorError("runtime_module_path_binding_invalid")
    versions = {"python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", "torch": torch.__version__, "transformers": transformers.__version__, "cuda": str(torch.version.cuda)}
    if versions != runtime["expected_versions"]:
        raise TrustedRemoteExecutorError("runtime_version_binding_invalid")
    index = expected["gpu_index"]
    if torch.cuda.device_count() <= index or not torch.cuda.is_bf16_supported():
        raise TrustedRemoteExecutorError("cuda_device_or_bf16_invalid")
    torch.cuda.set_device(index)
    properties = torch.cuda.get_device_properties(index)
    observed = _nvidia_smi_device(index)
    property_uuid = str(getattr(properties, "uuid", ""))
    if properties.name != expected["gpu_model"] or observed["gpu_model"] != expected["gpu_model"] or observed["gpu_uuid"] != expected["gpu_uuid"] or (property_uuid and property_uuid != expected["gpu_uuid"]):
        raise TrustedRemoteExecutorError("actual_device_identity_invalid")
    return {**versions, "executable_path": executable_relative, "torch_module_path": torch_relative, "transformers_module_path": transformers_relative, "device": f"cuda:{index}", "gpu_index": index, "gpu_uuid": expected["gpu_uuid"], "gpu_model": expected["gpu_model"], "dtype": "bf16", "quantization": "none", "model_repository": package_data["model_inventory"]["repository"], "model_revision": package_data["model_inventory"]["exact_revision"], "model_inventory_sha256": package_data["model_inventory"]["inventory_sha256"], "runtime_inventory_sha256": package_data["runtime_inventory"]["inventory_sha256"], "model_loaded": True}


def _validated_text_only_processor_inputs(inputs):
    if not isinstance(inputs, Mapping):
        raise TrustedRemoteExecutorError("processor_output_mapping_required")
    keys = set(inputs)
    required = {"input_ids", "attention_mask"}
    allowed = required | {"mm_token_type_ids"}
    multimedia = sorted(key for key in keys if key == "pixel_values" or key.startswith(("image_", "video_", "pixel_")) or "image" in key or "video" in key)
    if multimedia:
        raise TrustedRemoteExecutorError("multimedia_processor_tensor_forbidden")
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise TrustedRemoteExecutorError("text_only_processor_contract_invalid")
    if any(not hasattr(inputs[key], "to") for key in keys):
        raise TrustedRemoteExecutorError("processor_tensor_invalid")
    return tuple(sorted(keys))


def _write_raw_first(result_root, case_id, raw, cap):
    target = _contained_target(result_root, f"cases/{case_id}/raw_response.bin", create_parents=True)
    if len(raw) > cap:
        raise TrustedRemoteExecutorError("raw_response_cap_exceeded")
    with target.open("xb") as handle:
        handle.write(raw); handle.flush(); os.fsync(handle.fileno())


def _pre_payload_from_receipt(receipt):
    receipt = _replay_pre_receipt(receipt)
    return _validate_pre_payload(_loads(_unb64(receipt.to_dict()["signed_payload_base64"], "signed_payload_base64")))


def _consume_attempt_marker(package_root, receipt, package):
    root = _root(package_root, "package_root")
    payload = _pre_payload_from_receipt(receipt)
    marker = root / _ATTEMPT_MARKER_RELATIVE_PATH
    data = {"schema_version": "req2web.runtime.trusted_remote_attempt_consumption.v2", "action_time_plan": copy.deepcopy(payload["action_time_plan"]), "execution_package": {"package_id": package.to_dict()["package_id"], "sha256": package.sha256()}, "pre_run_receipt": {"receipt_id": receipt.to_dict()["receipt_id"], "sha256": receipt.sha256()}, "nonce": payload["nonce"], "consumed_at_utc": _utc_now_text()}
    raw = _dumps(data)
    try:
        with marker.open("xb") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise TrustedRemoteExecutorError("pre_run_attempt_already_consumed") from exc
    return marker


class _VerifiedExecutionHandle:
    __slots__ = ("_result", "_package", "_plan", "_pre_receipt", "_result_root", "_token")

    def __init__(self, result, package, plan, pre_receipt, result_root, token=None):
        if token is not _VERIFIED_EXECUTION_TOKEN:
            raise TrustedRemoteExecutorError("verified_execution_requires_fixed_parent_worker")
        self._result = validate_execution_result_against_package_v2(result, package)
        self._package = _replay_package(package)
        self._plan = _records.TrustedRemoteActionTimePlan.from_bytes(plan.canonical_bytes())
        self._pre_receipt = _replay_pre_receipt(pre_receipt)
        self._result_root = _root(result_root, "result_root")
        self._token = token

    def _validated(self):
        if self._token is not _VERIFIED_EXECUTION_TOKEN:
            raise TrustedRemoteExecutorError("verified_execution_handle_invalid")
        validate_trusted_remote_result_root_v2(self._result, self._result_root, self._package)
        return self


def _verify_execution_start_in_authority_window(result, pre_receipt):
    payload = _pre_payload_from_receipt(pre_receipt)
    start, _ = _utc(result.to_dict()["execution"]["started_at_utc"], "execution_started_at_utc")
    issued, _ = _utc(payload["issued_at_utc"], "issued_at_utc")
    expires, _ = _utc(payload["expires_at_utc"], "expires_at_utc")
    if start < issued or start > expires:
        raise TrustedRemoteExecutorError("execution_start_outside_authority_window")


def execute_worker_v2(package, action_time_plan, pre_run_receipt, expected_signer, repository_root, package_root, model_root, runtime_root, result_root):
    package, plan, _ = _validate_package_against_plan(package, action_time_plan)
    package_data = package.to_dict()
    _validate_repository_root(repository_root, plan)
    _worker_script_at_repository_root(repository_root, package)
    validate_execution_package_files_v2(package, package_root)
    _validate_inventory_root(model_root, package_data["model_inventory"], "model")
    _validate_inventory_root(runtime_root, package_data["runtime_inventory"], "runtime")
    _runtime_interpreter(package, runtime_root)
    if sys.platform != "linux":
        raise TrustedRemoteExecutorError("linux_worker_required")
    if "CUDA_VISIBLE_DEVICES" in os.environ:
        raise TrustedRemoteExecutorError("cuda_visible_devices_remapping_forbidden")
    authorized = authorize_trusted_remote_execution_once_v2(pre_run_receipt, action_time_plan, package, package_data["expected_instance"], expected_signer)
    _consume_attempt_marker(package_root, authorized, package)
    result_root = _new_or_empty_root(result_root, "result_root")
    expected = package_data["expected_instance"]
    started_text = _utc_now_text()
    monotonic_started = time.monotonic()
    # Lazy imports occur only after exact authority, attempt, runtime, inventory,
    # input, and output-root checks have passed.
    import torch
    import transformers
    from transformers import AutoModelForMultimodalLM, AutoProcessor
    actual = _actual_runtime_facts(torch, transformers, expected, package, runtime_root)
    device = f"cuda:{expected['gpu_index']}"
    processor = AutoProcessor.from_pretrained(str(model_root), local_files_only=True, trust_remote_code=False)
    model = AutoModelForMultimodalLM.from_pretrained(str(model_root), local_files_only=True, trust_remote_code=False, dtype=torch.bfloat16, device_map=None)
    model = model.to(device)
    model.eval()
    first_parameter = next(model.parameters())
    if str(first_parameter.device) != device or first_parameter.dtype != torch.bfloat16:
        raise TrustedRemoteExecutorError("loaded_model_device_or_dtype_invalid")
    raw_outputs = {}
    remaining_cap = package_data["result_policy"]["max_total_bytes"]
    for row in package_data["cases"]:
        model_text = _compose_exact_model_text(package_root, row)
        messages = [{"role": "user", "content": [{"type": "text", "text": model_text}]}]
        rendered = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        inputs = processor(text=[rendered], return_tensors="pt")
        processor_keys = _validated_text_only_processor_inputs(inputs)
        inputs = {key: inputs[key].to(device) for key in processor_keys}
        input_length = inputs["input_ids"].shape[1]
        with torch.inference_mode():
            output = model.generate(**inputs, do_sample=False, max_new_tokens=package_data["generation"]["max_new_tokens"], num_return_sequences=1)
        generated = output[:, input_length:]
        text = processor.batch_decode(generated, skip_special_tokens=True)[0]
        raw = text.encode("utf-8")
        if not raw:
            raise TrustedRemoteExecutorError("empty_raw_response")
        _write_raw_first(result_root, row["case_id"], raw, remaining_cap)
        remaining_cap -= len(raw)
        raw_outputs[row["case_id"]] = raw
    completed_text = _utc_now_text()
    elapsed = int((time.monotonic() - monotonic_started) * 1000)
    result = _create_execution_result(authorized, package, actual, raw_outputs, started_text, completed_text, elapsed, test_only=False)
    record_path = _contained_target(result_root, _RESULT_RELATIVE_PATH, create_parents=False)
    record_path.write_bytes(result.canonical_bytes())
    validate_trusted_remote_result_root_v2(result, result_root, package)
    _verify_execution_start_in_authority_window(result, authorized)
    return result


def _terminate_complete_process_tree(process, grace_seconds=5.0):
    if process.poll() is not None:
        return
    if sys.platform != "linux":
        process.kill(); process.wait(timeout=grace_seconds); return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=grace_seconds)


def _cancel_request_path(value):
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
        raise TrustedRemoteExecutorError("cancel_request_path_invalid")
    return path


def _wait_for_fixed_worker(process, timeout_seconds, cancel_request_path):
    deadline = time.monotonic() + timeout_seconds
    while True:
        if cancel_request_path is not None and cancel_request_path.exists():
            if cancel_request_path.is_symlink() or not cancel_request_path.is_file():
                _terminate_complete_process_tree(process)
                raise TrustedRemoteExecutorError("cancel_request_invalid")
            _terminate_complete_process_tree(process)
            raise TrustedRemoteExecutorError("executor_cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_complete_process_tree(process)
            raise TrustedRemoteExecutorError("executor_total_timeout")
        try:
            return process.communicate(timeout=min(1.0, remaining))
        except subprocess.TimeoutExpired:
            continue


def run_trusted_remote_executor_v2(execution_package, action_time_plan, pre_run_receipt, expected_signer, expected_instance_facts, repository_root, package_root, model_root, runtime_root, result_root, cancel_request_path=None):
    if sys.platform != "linux":
        raise TrustedRemoteExecutorError("linux_parent_runner_required")
    package, plan, _ = _validate_package_against_plan(execution_package, action_time_plan)
    cancel_request_path = _cancel_request_path(cancel_request_path)
    package_data = package.to_dict()
    repository_root = _validate_repository_root(repository_root, plan)
    worker_script = _worker_script_at_repository_root(repository_root, package)
    if package_data["worker"] != _worker_identity():
        raise TrustedRemoteExecutorError("fixed_worker_identity_drift")
    validate_execution_package_files_v2(package, package_root)
    _validate_inventory_root(model_root, package_data["model_inventory"], "model")
    _validate_inventory_root(runtime_root, package_data["runtime_inventory"], "runtime")
    interpreter = _runtime_interpreter(package, runtime_root)
    marker = _root(package_root, "package_root") / _ATTEMPT_MARKER_RELATIVE_PATH
    if marker.exists():
        raise TrustedRemoteExecutorError("pre_run_attempt_already_consumed")
    result_root_path = Path(result_root)
    if result_root_path.exists() and any(result_root_path.iterdir()):
        raise TrustedRemoteExecutorError("result_root_not_empty")
    authorized = verify_trusted_remote_pre_run_authorization_receipt_v2(pre_run_receipt, action_time_plan, package, expected_instance_facts, expected_signer)
    control_root = Path(tempfile.gettempdir()) / ("req2web-executor-v2-" + uuid.uuid4().hex)
    try:
        os.mkdir(control_root, 0o777)
        plan_path = control_root / "action_time_plan.json"; plan_path.write_bytes(_records.TrustedRemoteActionTimePlan.from_dict(plan).canonical_bytes())
        package_path = control_root / "execution_package.json"; package_path.write_bytes(package.canonical_bytes())
        receipt_path = control_root / "pre_run_receipt.json"; receipt_path.write_bytes(authorized.canonical_bytes())
        signer_path = control_root / "expected_pre_run_signer.pub"; signer_path.write_text(_normalize_public_key(expected_signer.public_key, "expected_pre_run_signer_public_key") + "\n", encoding="utf-8", newline="\n")
        command = [str(interpreter), *package_data["runtime_execution"]["isolated_flags"], str(worker_script), "--package", str(package_path), "--action-time-plan", str(plan_path), "--pre-run-receipt", str(receipt_path), "--pre-run-signer-public-key", str(signer_path), "--repository-root", str(repository_root), "--package-root", str(package_root), "--model-root", str(model_root), "--runtime-root", str(runtime_root), "--result-root", str(result_root)]
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "PYTHONNOUSERSITE": "1"}
        for key in ("CUDA_VISIBLE_DEVICES", "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE"):
            env.pop(key, None)
        process = subprocess.Popen(command, cwd=str(repository_root), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        stdout, stderr = _wait_for_fixed_worker(process, plan["operational"]["caps"]["time_cap_seconds"], cancel_request_path)
        if process.returncode != 0:
            raise TrustedRemoteExecutorError("fixed_worker_failed")
        result_path = Path(result_root) / _RESULT_RELATIVE_PATH
        if not result_path.is_file():
            raise TrustedRemoteExecutorError("worker_result_missing")
        result = TrustedRemoteExecutionResultV2.from_bytes(result_path.read_bytes())
        result = validate_trusted_remote_result_root_v2(result, result_root, package)
        _verify_execution_start_in_authority_window(result, authorized)
        _verify_pre_run_authorization_core(authorized, action_time_plan, package, expected_instance_facts, expected_signer, enforce_current_window=False)
        return _VerifiedExecutionHandle(result, package, _records.TrustedRemoteActionTimePlan.from_dict(plan), authorized, result_root, _VERIFIED_EXECUTION_TOKEN)
    finally:
        shutil.rmtree(control_root, ignore_errors=True)


def _worker_main_v2(argv=None):
    parser = argparse.ArgumentParser(description="Run the fixed Req2Web trusted-remote Qwen two-case worker.")
    parser.add_argument("--package", required=True); parser.add_argument("--action-time-plan", required=True); parser.add_argument("--pre-run-receipt", required=True); parser.add_argument("--pre-run-signer-public-key", required=True)
    parser.add_argument("--repository-root", required=True); parser.add_argument("--package-root", required=True); parser.add_argument("--model-root", required=True); parser.add_argument("--runtime-root", required=True); parser.add_argument("--result-root", required=True)
    args = parser.parse_args(argv)
    signer = ExpectedPreRunSigner(Path(args.pre_run_signer_public_key).read_text(encoding="utf-8").strip())
    result = execute_worker_v2(TrustedRemoteExecutionPackageV2.from_bytes(Path(args.package).read_bytes()), _records.TrustedRemoteActionTimePlan.from_bytes(Path(args.action_time_plan).read_bytes()), TrustedRemotePreRunAuthorizationReceiptV2.from_bytes(Path(args.pre_run_receipt).read_bytes()), signer, Path(args.repository_root), Path(args.package_root), Path(args.model_root), Path(args.runtime_root), Path(args.result_root))
    sys.stdout.buffer.write(result.canonical_bytes() + b"\n")
    return 0


def create_trusted_remote_project_temp_root_v2(path_value, execution_package):
    package = _replay_package(execution_package)
    root = _new_or_empty_root(path_value, "project_temp_root")
    marker = {"schema_version": "req2web.runtime.trusted_remote_project_temp_root.v2", "package_id": package.to_dict()["package_id"], "package_sha256": package.sha256()}
    (root / ".req2web_project_temp_root.json").write_bytes(_dumps(marker))
    return root


def _delete_verified_project_temp_root(root_value, package, return_root):
    root = _root(root_value, "project_temp_root")
    return_root = _root(return_root, "return_root")
    if root == return_root or root in return_root.parents or return_root in root.parents:
        raise TrustedRemoteExecutorError("project_temp_return_root_overlap")
    marker_path = root / ".req2web_project_temp_root.json"
    if not marker_path.is_file() or marker_path.is_symlink():
        raise TrustedRemoteExecutorError("project_temp_marker_missing")
    expected = {"schema_version": "req2web.runtime.trusted_remote_project_temp_root.v2", "package_id": package.to_dict()["package_id"], "package_sha256": package.sha256()}
    if _canonical_json_document(marker_path.read_bytes(), "project_temp_marker") != expected:
        raise TrustedRemoteExecutorError("project_temp_marker_binding_invalid")
    shutil.rmtree(root)
    if root.exists():
        raise TrustedRemoteExecutorError("project_temp_deletion_failed")


def _case_return_artifacts(value, case_id):
    value = _keys(value, ("model_route_outcome", "gate_delivery_outcome", "result_package_manifest", "error"), f"case_return_artifacts_{case_id}")
    result = {}
    for key in ("model_route_outcome", "gate_delivery_outcome", "result_package_manifest"):
        raw = value[key]
        if not isinstance(raw, bytes) or not raw:
            raise TrustedRemoteExecutorError(f"case_return_{key}_invalid")
        result[key] = _dumps(_loads(raw))
    error = value["error"]
    if error is None:
        error = b"null"
    if not isinstance(error, bytes) or not error:
        raise TrustedRemoteExecutorError("case_return_error_invalid")
    result["error"] = _dumps(_loads(error))
    return result


def _write_exact_file(root, relative_path, raw):
    target = _contained_target(root, relative_path, create_parents=True)
    if target.exists():
        raise TrustedRemoteExecutorError("return_path_already_exists")
    target.write_bytes(raw)


def _return_inventory(root, excluded=()):
    excluded = set(excluded)
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TrustedRemoteExecutorError("return_root_symlink_forbidden")
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        raw = path.read_bytes()
        rows.append({"relative_path": relative, "byte_length": len(raw), "sha256": _sha(raw)})
    return rows


def _closeout_payload_from_records(result, package, *, project_temp_deleted, release_evidence=None, revocation_evidence=None):
    result = validate_execution_result_against_package_v2(result, package)
    release = {"state": "pending_external_action", "evidence_sha256": None}
    revoke = {"state": "pending_external_action", "evidence_sha256": None}
    if release_evidence is not None:
        if not isinstance(release_evidence, bytes) or not release_evidence:
            raise TrustedRemoteExecutorError("instance_release_evidence_invalid")
        release = {"state": "completed", "evidence_sha256": _sha(release_evidence)}
    if revocation_evidence is not None:
        if not isinstance(revocation_evidence, bytes) or not revocation_evidence:
            raise TrustedRemoteExecutorError("access_revocation_evidence_invalid")
        revoke = {"state": "completed", "evidence_sha256": _sha(revocation_evidence)}
    state = "completed" if release["state"] == revoke["state"] == "completed" else "pending_instance_release_and_access_revocation"
    return {
        "schema_version": CLOSEOUT_RECORD_SCHEMA,
        "state": state,
        "execution_result": {"result_id": result.to_dict()["result_id"], "sha256": result.sha256()},
        "steps": {
            "process_completion": {"state": "completed", "evidence_sha256": _sha(result.canonical_bytes())},
            "result_return": {"state": "completed", "evidence_sha256": "pending_manifest_rebind"},
            "project_side_temporary_deletion": {"state": "completed" if project_temp_deleted else "failed", "evidence_sha256": _sha(b"deleted") if project_temp_deleted else None},
            "instance_release": release,
            "temporary_access_revocation": revoke,
        },
    }


def _closeout_payload(handle, *, project_temp_deleted, release_evidence=None, revocation_evidence=None):
    handle = handle._validated()
    return _closeout_payload_from_records(
        handle._result,
        handle._package,
        project_temp_deleted=project_temp_deleted,
        release_evidence=release_evidence,
        revocation_evidence=revocation_evidence,
    )


def _write_return_index_and_manifest_from_records(root, result, package, closeout):
    result = validate_execution_result_against_package_v2(result, package)
    package = _replay_package(package)
    index_path = root / "final" / "final_result_index.json"
    manifest_path = root / "run" / "run_manifest.json"
    for path in (index_path, manifest_path):
        if path.exists():
            path.unlink()
    rows = _return_inventory(root, excluded=("final/final_result_index.json", "run/run_manifest.json"))
    index = {"schema_version": "req2web.runtime.trusted_remote_final_result_index.v2", "case_ids": list(REQUIRED_CASE_IDS), "files": rows, "file_count": len(rows), "total_bytes": sum(row["byte_length"] for row in rows), "inventory_sha256": _sha(_dumps(rows))}
    index_raw = _dumps(index)
    _write_exact_file(root, "final/final_result_index.json", index_raw)
    manifest = {
        "schema_version": RETURN_BUNDLE_SCHEMA,
        "state": closeout["state"],
        "execution_result": {"record": result.to_dict(), "sha256": result.sha256()},
        "execution_package": {"package_id": package.to_dict()["package_id"], "sha256": package.sha256()},
        "final_result_index_sha256": _sha(index_raw),
        "closeout_sha256": _sha(_dumps(closeout)),
    }
    _write_exact_file(root, "run/run_manifest.json", _dumps(manifest))
    return manifest


def _write_return_index_and_manifest(root, handle, closeout):
    handle = handle._validated()
    return _write_return_index_and_manifest_from_records(root, handle._result, handle._package, closeout)


def write_trusted_remote_return_bundle_v2(verified_execution, case_artifacts, return_root, project_temp_root):
    if type(verified_execution) is not _VerifiedExecutionHandle:
        raise TrustedRemoteExecutorError("verified_execution_handle_required")
    handle = verified_execution._validated()
    package = handle._package
    if not isinstance(case_artifacts, Mapping) or tuple(case_artifacts) != REQUIRED_CASE_IDS:
        raise TrustedRemoteExecutorError("case_return_coverage_invalid")
    normalized = {case_id: _case_return_artifacts(case_artifacts[case_id], case_id) for case_id in REQUIRED_CASE_IDS}
    root = _new_or_empty_root(return_root, "return_root")
    result = handle._result.to_dict()
    _write_exact_file(root, "run/runtime_observation.json", _dumps(result["actual_runtime"]))
    _write_exact_file(root, "run/model_inventory.json", _dumps(package.to_dict()["model_inventory"]))
    for case_row, result_row in zip(package.to_dict()["cases"], result["cases"]):
        case_id = case_row["case_id"]
        prefix = f"cases/{case_id}"
        invocation = {"case_id": case_id, "provider_input": case_row["provider_input"], "provider_input_file": case_row["provider_input_file"], "prompt_file": case_row["prompt_file"], "provider_call_count": result_row["provider_call_count"], "retry_count": 0}
        _write_exact_file(root, f"{prefix}/invocation.json", _dumps(invocation))
        _write_exact_file(root, f"{prefix}/raw_response.bin", _unb64(result_row["raw_response_base64"], "raw_response_base64"))
        _write_exact_file(root, f"{prefix}/error.json", normalized[case_id]["error"])
        _write_exact_file(root, f"{prefix}/model_route_outcome.json", normalized[case_id]["model_route_outcome"])
        _write_exact_file(root, f"{prefix}/gate_delivery_outcome.json", normalized[case_id]["gate_delivery_outcome"])
        _write_exact_file(root, f"{prefix}/result_package_manifest.json", normalized[case_id]["result_package_manifest"])
    process = {"state": "completed", "worker_result_sha256": handle._result.sha256(), "provider_call_count": result["execution"]["provider_call_count"], "retry_count": result["execution"]["retry_count"], "timeout_state": result["execution"]["timeout_state"], "cancel_state": result["execution"]["cancel_state"]}
    _write_exact_file(root, "closeout/process_completion.json", _dumps(process))
    _delete_verified_project_temp_root(project_temp_root, package, root)
    closeout = _closeout_payload(handle, project_temp_deleted=True)
    closeout["steps"]["result_return"]["evidence_sha256"] = _sha(_dumps({"return_root": "bounded", "case_ids": list(REQUIRED_CASE_IDS)}))
    _write_exact_file(root, "closeout/closeout.json", _dumps(closeout))
    manifest = _write_return_index_and_manifest(root, handle, closeout)
    allowed = set(_records.RESULT_RETURN_PATHS)
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != allowed:
        raise TrustedRemoteExecutorError("return_path_inventory_invalid")
    rows = _return_inventory(root)
    caps = handle._plan.to_dict()["operational"]["result_return"]
    if len(rows) > caps["max_file_count"] or sum(row["byte_length"] for row in rows) > caps["max_total_bytes"]:
        raise TrustedRemoteExecutorError("return_bundle_cap_exceeded")
    return copy.deepcopy(manifest)


def _read_return_json(root, relative_path, label):
    path = _contained_target(root, relative_path, create_parents=False)
    if not path.is_file() or path.is_symlink():
        raise TrustedRemoteExecutorError(f"{label}_missing")
    raw = path.read_bytes()
    value = _canonical_json_document(raw, label)
    return value, raw


def validate_downloaded_trusted_remote_return_bundle_v2(action_time_plan, execution_package, return_root, *, expected_state=None):
    package, plan, _ = _validate_package_against_plan(execution_package, action_time_plan)
    root = _root(return_root, "return_root")
    rows = _return_inventory(root)
    actual_paths = {row["relative_path"] for row in rows}
    if actual_paths != set(_records.RESULT_RETURN_PATHS):
        raise TrustedRemoteExecutorError("return_path_inventory_invalid")
    caps = plan["operational"]["result_return"]
    if len(rows) > caps["max_file_count"] or sum(row["byte_length"] for row in rows) > caps["max_total_bytes"]:
        raise TrustedRemoteExecutorError("return_bundle_cap_exceeded")

    manifest, manifest_raw = _read_return_json(root, "run/run_manifest.json", "return_manifest")
    manifest = _keys(manifest, ("schema_version", "state", "execution_result", "execution_package", "final_result_index_sha256", "closeout_sha256"), "return_manifest")
    if manifest["schema_version"] != RETURN_BUNDLE_SCHEMA or (expected_state is not None and manifest["state"] != expected_state):
        raise TrustedRemoteExecutorError("return_manifest_state_invalid")
    result_binding = _keys(manifest["execution_result"], ("record", "sha256"), "return_execution_result")
    result = TrustedRemoteExecutionResultV2.from_dict(result_binding["record"])
    result = validate_execution_result_against_package_v2(result, package)
    if result_binding["sha256"] != result.sha256():
        raise TrustedRemoteExecutorError("return_execution_result_binding_invalid")
    if manifest["execution_package"] != {"package_id": package.to_dict()["package_id"], "sha256": package.sha256()}:
        raise TrustedRemoteExecutorError("return_execution_package_binding_invalid")

    runtime, _ = _read_return_json(root, "run/runtime_observation.json", "return_runtime_observation")
    model_inventory, _ = _read_return_json(root, "run/model_inventory.json", "return_model_inventory")
    if runtime != result.to_dict()["actual_runtime"] or model_inventory != package.to_dict()["model_inventory"]:
        raise TrustedRemoteExecutorError("return_runtime_or_model_binding_invalid")

    process, _ = _read_return_json(root, "closeout/process_completion.json", "return_process_completion")
    expected_process = {"state": "completed", "worker_result_sha256": result.sha256(), "provider_call_count": result.to_dict()["execution"]["provider_call_count"], "retry_count": result.to_dict()["execution"]["retry_count"], "timeout_state": result.to_dict()["execution"]["timeout_state"], "cancel_state": result.to_dict()["execution"]["cancel_state"]}
    if process != expected_process:
        raise TrustedRemoteExecutorError("return_process_completion_binding_invalid")

    package_cases = {row["case_id"]: row for row in package.to_dict()["cases"]}
    result_cases = {row["case_id"]: row for row in result.to_dict()["cases"]}
    for case_id in REQUIRED_CASE_IDS:
        package_row = package_cases[case_id]
        result_row = result_cases[case_id]
        invocation, _ = _read_return_json(root, f"cases/{case_id}/invocation.json", f"return_invocation_{case_id}")
        expected_invocation = {"case_id": case_id, "provider_input": package_row["provider_input"], "provider_input_file": package_row["provider_input_file"], "prompt_file": package_row["prompt_file"], "provider_call_count": result_row["provider_call_count"], "retry_count": 0}
        if invocation != expected_invocation:
            raise TrustedRemoteExecutorError("return_invocation_binding_invalid")
        raw_path = _contained_target(root, f"cases/{case_id}/raw_response.bin", create_parents=False)
        raw = raw_path.read_bytes()
        if _b64(raw) != result_row["raw_response_base64"] or len(raw) != result_row["raw_response_byte_length"] or _sha(raw) != result_row["raw_response_sha256"]:
            raise TrustedRemoteExecutorError("return_raw_response_binding_invalid")
        for name in ("error", "model_route_outcome", "gate_delivery_outcome", "result_package_manifest"):
            _read_return_json(root, f"cases/{case_id}/{name}.json", f"return_{name}_{case_id}")

    closeout, closeout_raw = _read_return_json(root, "closeout/closeout.json", "return_closeout")
    closeout = _keys(closeout, ("schema_version", "state", "execution_result", "steps"), "return_closeout")
    if closeout["schema_version"] != CLOSEOUT_RECORD_SCHEMA or closeout["state"] != manifest["state"] or closeout["execution_result"] != {"result_id": result.to_dict()["result_id"], "sha256": result.sha256()}:
        raise TrustedRemoteExecutorError("return_closeout_binding_invalid")
    steps = _keys(closeout["steps"], tuple(_records.CLOSEOUT_STEP_ORDER), "return_closeout_steps")
    if steps["process_completion"]["state"] != "completed" or steps["result_return"]["state"] != "completed" or steps["project_side_temporary_deletion"]["state"] != "completed":
        raise TrustedRemoteExecutorError("return_closeout_completed_steps_invalid")
    if manifest["state"] == "completed":
        if steps["instance_release"]["state"] != "completed" or steps["temporary_access_revocation"]["state"] != "completed":
            raise TrustedRemoteExecutorError("return_closeout_external_steps_invalid")
    elif manifest["state"] == "pending_instance_release_and_access_revocation":
        if steps["instance_release"]["state"] != "pending_external_action" or steps["temporary_access_revocation"]["state"] != "pending_external_action":
            raise TrustedRemoteExecutorError("return_closeout_external_steps_invalid")
    else:
        raise TrustedRemoteExecutorError("return_closeout_state_invalid")

    index, index_raw = _read_return_json(root, "final/final_result_index.json", "return_final_index")
    expected_rows = _return_inventory(root, excluded=("final/final_result_index.json", "run/run_manifest.json"))
    expected_index = {"schema_version": "req2web.runtime.trusted_remote_final_result_index.v2", "case_ids": list(REQUIRED_CASE_IDS), "files": expected_rows, "file_count": len(expected_rows), "total_bytes": sum(row["byte_length"] for row in expected_rows), "inventory_sha256": _sha(_dumps(expected_rows))}
    if index != expected_index or manifest["final_result_index_sha256"] != _sha(index_raw) or manifest["closeout_sha256"] != _sha(closeout_raw):
        raise TrustedRemoteExecutorError("return_index_or_manifest_binding_invalid")
    return {"manifest": copy.deepcopy(dict(manifest)), "execution_result": result, "package": package, "plan": _records.TrustedRemoteActionTimePlan.from_dict(plan), "root": root}


def finalize_downloaded_trusted_remote_closeout_v2(action_time_plan, execution_package, return_root, instance_release_evidence, temporary_access_revocation_evidence):
    validated = validate_downloaded_trusted_remote_return_bundle_v2(action_time_plan, execution_package, return_root, expected_state="pending_instance_release_and_access_revocation")
    root = validated["root"]
    result = validated["execution_result"]
    package = validated["package"]
    closeout = _closeout_payload_from_records(result, package, project_temp_deleted=True, release_evidence=instance_release_evidence, revocation_evidence=temporary_access_revocation_evidence)
    closeout["steps"]["result_return"]["evidence_sha256"] = _sha(_dumps({"return_root": "bounded", "case_ids": list(REQUIRED_CASE_IDS)}))
    (root / "closeout" / "closeout.json").write_bytes(_dumps(closeout))
    manifest = _write_return_index_and_manifest_from_records(root, result, package, closeout)
    validate_downloaded_trusted_remote_return_bundle_v2(action_time_plan, package, root, expected_state="completed")
    return copy.deepcopy(manifest)


def finalize_trusted_remote_closeout_v2(verified_execution, return_root, instance_release_evidence, temporary_access_revocation_evidence):
    if type(verified_execution) is not _VerifiedExecutionHandle:
        raise TrustedRemoteExecutorError("verified_execution_handle_required")
    handle = verified_execution._validated()
    return finalize_downloaded_trusted_remote_closeout_v2(handle._plan, handle._package, return_root, instance_release_evidence, temporary_access_revocation_evidence)


__all__ = [
    "CLOSEOUT_RECORD_SCHEMA", "EXECUTION_PACKAGE_SCHEMA", "EXECUTION_RESULT_SCHEMA", "ExpectedPreRunSigner",
    "PRE_RUN_NAMESPACE", "PRE_RUN_PAYLOAD_SCHEMA", "PRE_RUN_RECEIPT_SCHEMA", "PROMPT_ARTIFACT_SCHEMA",
    "RETURN_BUNDLE_SCHEMA", "TrustedRemoteExecutionPackageV2", "TrustedRemoteExecutionResultV2",
    "TrustedRemoteExecutorError", "TrustedRemotePreRunAuthorizationReceiptV2",
    "authorize_trusted_remote_execution_once_v2", "build_trusted_remote_pre_run_authorization_payload_v2",
    "create_trusted_remote_pre_run_authorization_receipt_v2", "create_trusted_remote_project_temp_root_v2",
    "finalize_downloaded_trusted_remote_closeout_v2", "finalize_trusted_remote_closeout_v2", "materialize_trusted_remote_execution_inputs_v2",
    "prepare_trusted_remote_execution_package_v2", "run_trusted_remote_executor_v2",
    "validate_downloaded_trusted_remote_return_bundle_v2", "validate_execution_package_files_v2", "validate_execution_result_against_package_v2",
    "validate_trusted_remote_result_root_v2", "verify_trusted_remote_pre_run_authorization_receipt_v2",
    "write_trusted_remote_return_bundle_v2",
]
