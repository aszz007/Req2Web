"""Bounded Qwen3.6-27B stronger-model diagnostic.

This module intentionally preserves the accepted two-case PageSpec-generation
contract. It changes only the pinned model checkpoint. The diagnostic remains
Path 3 development/non-H1 evidence and never changes Parser, Assembler, gates,
one-repair, or same-case G0 fallback semantics.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from types import MappingProxyType
from typing import Any, Mapping, TextIO

from req2web_runtime import qwen27b_predeploy as _runtime_authority
from req2web_runtime.autodl_repository_archive import validate_archive_file
from req2web_runtime.qwen27b_case_bundle import (
    CASE_IDS,
    MODEL_REPOSITORY as CASE_BUNDLE_ORIGIN_MODEL_REPOSITORY,
    MODEL_REVISION as CASE_BUNDLE_ORIGIN_MODEL_REVISION,
    compose_qwen27b_model_text,
    validate_qwen27b_case_bundle,
)
from req2web_runtime.model_text_stream import MODEL_TEXT_STREAM_MODES
from req2web_runtime.qwen27b_recovery_runner import (
    BackendGeneration,
    EXPECTED_DEVICE,
    EXPECTED_GPU_NAMES,
    MIN_FREE_VRAM_BEFORE_LOAD_BYTES,
    MIN_TOTAL_VRAM_BYTES,
    MAX_INPUT_TOKENS,
    MAX_NEW_TOKENS,
    MAX_RAW_BYTES_PER_CASE,
    MAX_TOTAL_RAW_BYTES,
    TransformersRecoveryBackend,
)

PLAN_SCHEMA = "req2web.runtime.qwen36_27b_diagnostic_plan.v1"
MODEL_INVENTORY_SCHEMA = "req2web.runtime.qwen36_27b_model_inventory.v1"
ACTION_BINDING_SCHEMA = "req2web.runtime.qwen36_27b_action_binding.v1"
OFFICIAL_MODEL_INVENTORY_SCHEMA = "req2web.runtime.qwen36_27b_official_inventory.v1"
RUN_SCHEMA = "req2web.runtime.qwen36_27b_diagnostic_run.v1"
RUN_MANIFEST_FILENAME = "qwen36_diagnostic_run_manifest.json"
FAILURE_FILENAME = "qwen36_diagnostic_failure.json"
MODEL_ID = "Qwen3.6-27B"
MODEL_REPOSITORY = "Qwen/Qwen3.6-27B"
MODEL_REVISION = "6a9e13bd6fc8f0983b9b99948120bc37f49c13e9"
MODEL_LICENSE = "apache-2.0"
MODEL_ARCHITECTURE = "Qwen3_5ForConditionalGeneration"
MODEL_TYPE = "qwen3_5"
MODEL_WEIGHT_TENSOR_TOTAL_BYTES = 55_562_855_904
MODEL_WEIGHT_SHARD_TOTAL_BYTES = 55_563_006_400
EXPECTED_RUNTIME = {
    "python": "3.11.15",
    "torch": "2.7.1+cu128",
    "transformers": "5.14.1",
    "cuda": "12.8",
}
EXPECTED_GPU_INDEX = 0
OFFICIAL_MODEL_INVENTORY_MANIFEST = Path(__file__).with_name(
    "qwen36_27b_6a9e13bd_official_inventory.v1.json"
)
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_GPU_UUID = re.compile(r"GPU-[A-Za-z0-9-]{4,128}")
_CASE_BUNDLE_ID = re.compile(r"qwen35-27b-case-bundle-v1-[0-9a-f]{64}")
_WEIGHT_FILES = tuple(
    f"model-{index:05d}-of-00015.safetensors" for index in range(1, 16)
)
MODEL_FILES = (
    ".gitattributes",
    "LICENSE",
    "README.md",
    "chat_template.jinja",
    "config.json",
    "configuration.json",
    "generation_config.json",
    "merges.txt",
    *_WEIGHT_FILES,
    "model.safetensors.index.json",
    "preprocessor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "video_preprocessor_config.json",
    "vocab.json",
)


class Qwen36DiagnosticError(RuntimeError):
    """The bounded stronger-model diagnostic failed closed."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _progress(event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    sys.stderr.write(_canonical(payload).decode("utf-8") + "\n")
    sys.stderr.flush()


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Qwen36DiagnosticError("duplicate_json_key")
        result[key] = value
    return result


def _load(raw: bytes) -> object:
    if type(raw) is not bytes:
        raise Qwen36DiagnosticError("canonical_bytes_required")
    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                Qwen36DiagnosticError("non_finite_json_forbidden")
            ),
        )
    except Qwen36DiagnosticError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Qwen36DiagnosticError("canonical_json_invalid") from exc


def _keys(value: object, expected: tuple[str, ...], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or len(value) != len(expected) or set(value) != set(expected):
        raise Qwen36DiagnosticError(f"{label}_keys_invalid")
    return dict(value)


def _hex(value: object, label: str, pattern: re.Pattern[str] = _HEX64) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise Qwen36DiagnosticError(f"{label}_invalid")
    return value


def _positive_int(value: object, label: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or value < minimum:
        raise Qwen36DiagnosticError(f"{label}_invalid")
    return value


def _official_model_inventory_authority() -> dict[str, Any]:
    try:
        raw = OFFICIAL_MODEL_INVENTORY_MANIFEST.read_bytes()
    except OSError as exc:
        raise Qwen36DiagnosticError(
            "official_model_inventory_manifest_unavailable"
        ) from exc
    value = _keys(
        _load(raw),
        (
            "schema_version",
            "authority_id",
            "repository",
            "revision",
            "source",
            "files",
            "file_count",
            "total_bytes",
            "tree_sha256",
        ),
        "official_model_inventory",
    )
    if raw != _canonical(value):
        raise Qwen36DiagnosticError(
            "official_model_inventory_manifest_not_canonical"
        )
    if (
        value["schema_version"] != OFFICIAL_MODEL_INVENTORY_SCHEMA
        or value["repository"] != MODEL_REPOSITORY
        or value["revision"] != MODEL_REVISION
    ):
        raise Qwen36DiagnosticError(
            "official_model_inventory_identity_invalid"
        )
    source = _keys(
        value["source"],
        ("metadata_api", "small_file_resolve_template", "method"),
        "official_model_inventory_source",
    )
    metadata_api = (
        "https://huggingface.co/api/models/Qwen/Qwen3.6-27B/revision/"
        + MODEL_REVISION
        + "?blobs=true"
    )
    resolve_template = (
        "https://huggingface.co/Qwen/Qwen3.6-27B/resolve/"
        + MODEL_REVISION
        + "/{relative_path}?download=true"
    )
    if source != {
        "metadata_api": metadata_api,
        "small_file_resolve_template": resolve_template,
        "method": (
            "LFS files use official lfs.sha256 and lfs.size; non-LFS files "
            "use exact-revision resolve bytes with local SHA-256."
        ),
    }:
        raise Qwen36DiagnosticError(
            "official_model_inventory_source_invalid"
        )
    if type(value["files"]) is not list:
        raise Qwen36DiagnosticError(
            "official_model_inventory_files_invalid"
        )
    official_files: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for item in value["files"]:
        file_value = _keys(
            item,
            ("relative_path", "bytes", "sha256", "sha256_source"),
            "official_model_file",
        )
        relative = file_value["relative_path"]
        if type(relative) is not str or not relative:
            raise Qwen36DiagnosticError("official_model_file_path_invalid")
        size = _positive_int(
            file_value["bytes"], "official_model_file_bytes", allow_zero=True
        )
        digest = _hex(
            file_value["sha256"], "official_model_file_sha256"
        )
        source_kind = file_value["sha256_source"]
        if source_kind not in {
            "huggingface_lfs_oid_sha256",
            "exact_revision_resolve_bytes_sha256",
        }:
            raise Qwen36DiagnosticError(
                "official_model_file_sha256_source_invalid"
            )
        official_files.append(
            {
                "relative_path": relative,
                "bytes": size,
                "sha256": digest,
                "sha256_source": source_kind,
            }
        )
        rows.append(
            {"relative_path": relative, "bytes": size, "sha256": digest}
        )
    paths = tuple(row["relative_path"] for row in rows)
    if paths != tuple(sorted(MODEL_FILES)) or len(paths) != len(set(paths)):
        raise Qwen36DiagnosticError("official_model_file_set_invalid")
    if (
        value["file_count"] != len(rows)
        or value["total_bytes"] != sum(row["bytes"] for row in rows)
        or value["tree_sha256"] != _sha(_canonical(official_files))
    ):
        raise Qwen36DiagnosticError(
            "official_model_inventory_summary_invalid"
        )
    _hex(value["authority_id"], "official_model_authority_id")
    body = copy.deepcopy(value)
    body["authority_id"] = "0" * 64
    if value["authority_id"] != _sha(_canonical(body)):
        raise Qwen36DiagnosticError("official_model_authority_id_invalid")
    return {
        "authority_id": value["authority_id"],
        "manifest_sha256": _sha(raw),
        "file_count": len(rows),
        "total_bytes": sum(row["bytes"] for row in rows),
        "official_tree_sha256": value["tree_sha256"],
        "files": rows,
        "runtime_tree_sha256": _sha(_canonical(rows)),
    }


def _identified(body: Mapping[str, Any], field: str) -> dict[str, Any]:
    data = copy.deepcopy(dict(body))
    data[field] = "0" * 64
    data[field] = _sha(_canonical(data))
    return data


def _read_record(value: object, type_: type["_Record"], label: str) -> "_Record":
    if type(value) is type_:
        raw = value.canonical_bytes()
    elif type(value) is bytes:
        raw = value
    elif isinstance(value, (str, os.PathLike)):
        path = Path(value)
        if path.is_symlink() or not path.is_file():
            raise Qwen36DiagnosticError(f"{label}_file_invalid")
        raw = path.read_bytes()
    else:
        raise Qwen36DiagnosticError(f"{label}_record_required")
    return type_.from_bytes(raw)


def _read_runtime_inventory(
    value: bytes | Path | str,
) -> tuple[dict[str, Any], bytes]:
    if type(value) is bytes:
        raw = value
    elif isinstance(value, (str, os.PathLike)):
        path = Path(value)
        if path.is_symlink() or not path.is_file():
            raise Qwen36DiagnosticError("runtime_inventory_file_invalid")
        raw = path.read_bytes()
    else:
        raise Qwen36DiagnosticError("runtime_inventory_required")
    try:
        data = _runtime_authority._runtime_inventory(raw)
    except _runtime_authority.Qwen27BPredeployError as exc:
        raise Qwen36DiagnosticError("runtime_inventory_invalid") from exc
    if raw != _runtime_authority._dump(data):
        raise Qwen36DiagnosticError("runtime_inventory_not_canonical")
    return data, raw


def _runtime_inventory_binding(
    value: bytes | Path | str,
) -> dict[str, Any]:
    data, raw = _read_runtime_inventory(value)
    return {
        "inventory_id": data["inventory_id"],
        "sha256": _sha(raw),
        "tree_sha256": data["tree_sha256"],
        "runtime_root_sha256": data["runtime_root_sha256"],
        "interpreter_relative_path": data["interpreter_relative_path"],
        "versions": {
            "python": data["python_version"],
            "torch": data["torch_version"],
            "transformers": data["transformers_version"],
            "cuda": data["cuda_version"],
        },
    }


def _validated_runtime_binding(value: object) -> dict[str, Any]:
    binding = _keys(
        value,
        (
            "inventory_id",
            "sha256",
            "tree_sha256",
            "runtime_root_sha256",
            "interpreter_relative_path",
            "versions",
        ),
        "runtime_binding",
    )
    for name in (
        "inventory_id",
        "sha256",
        "tree_sha256",
        "runtime_root_sha256",
    ):
        _hex(binding[name], "runtime_" + name)
    if (
        binding["interpreter_relative_path"]
        not in {"bin/python", "bin/python3.11"}
        or binding["versions"] != EXPECTED_RUNTIME
    ):
        raise Qwen36DiagnosticError("runtime_binding_invalid")
    return binding


class _Record:
    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_data", MappingProxyType(copy.deepcopy(dict(data))))
        self.validate()

    @property
    def data(self) -> Mapping[str, Any]:
        return self._data

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return copy.deepcopy(dict(self._data))

    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_dict())

    def sha256(self) -> str:
        return _sha(self.canonical_bytes())

    @classmethod
    def from_bytes(cls, raw: bytes):
        value = _load(raw)
        if type(value) is not dict:
            raise Qwen36DiagnosticError("record_object_required")
        result = cls(value)
        if raw != result.canonical_bytes():
            raise Qwen36DiagnosticError("record_not_canonical")
        return result

    def validate(self) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class Qwen36DiagnosticPlan(_Record):
    """No-model plan that freezes the sole stronger-checkpoint diagnostic."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        _Record.__init__(self, data)

    def validate(self) -> None:
        value = _keys(
            self.data,
            (
                "schema_version",
                "plan_id",
                "status",
                "source",
                "model",
                "runtime",
                "execution",
                "claims",
            ),
            "plan",
        )
        if value["schema_version"] != PLAN_SCHEMA or value["status"] != "prepared_local_no_model":
            raise Qwen36DiagnosticError("plan_header_invalid")
        source = _keys(
            value["source"],
            (
                "commit_sha",
                "tree_sha",
                "archive_manifest_id",
                "archive_manifest_sha256",
                "archive_sha256",
                "archive_byte_length",
                "case_bundle_id",
                "case_bundle_sha256",
                "case_bundle_origin_model_repository",
                "case_bundle_origin_model_revision",
                "case_bundle_model_profile_role",
            ),
            "plan_source",
        )
        _hex(source["commit_sha"], "commit_sha", _HEX40)
        _hex(source["tree_sha"], "tree_sha", _HEX40)
        for name in (
            "archive_manifest_id",
            "archive_manifest_sha256",
            "archive_sha256",
            "case_bundle_sha256",
        ):
            _hex(source[name], name)
        if (
            type(source["case_bundle_id"]) is not str
            or _CASE_BUNDLE_ID.fullmatch(source["case_bundle_id"]) is None
        ):
            raise Qwen36DiagnosticError("case_bundle_id_invalid")
        _positive_int(source["archive_byte_length"], "archive_byte_length")
        if (
            source["case_bundle_origin_model_repository"]
            != CASE_BUNDLE_ORIGIN_MODEL_REPOSITORY
            or source["case_bundle_origin_model_revision"]
            != CASE_BUNDLE_ORIGIN_MODEL_REVISION
            or source["case_bundle_model_profile_role"]
            != "prompt_contract_origin_only_not_runtime_authority"
        ):
            raise Qwen36DiagnosticError(
                "case_bundle_origin_model_profile_invalid"
            )
        official = _official_model_inventory_authority()
        if value["model"] != {
            "id": MODEL_ID,
            "repository": MODEL_REPOSITORY,
            "revision": MODEL_REVISION,
            "license": MODEL_LICENSE,
            "architecture": MODEL_ARCHITECTURE,
            "model_type": MODEL_TYPE,
            "file_count": official["file_count"],
            "total_bytes": official["total_bytes"],
            "weight_shard_count": len(_WEIGHT_FILES),
            "weight_shard_total_bytes": MODEL_WEIGHT_SHARD_TOTAL_BYTES,
            "weight_tensor_total_bytes": MODEL_WEIGHT_TENSOR_TOTAL_BYTES,
            "official_inventory_authority_id": official["authority_id"],
            "official_inventory_manifest_sha256": official["manifest_sha256"],
            "official_inventory_tree_sha256": official["official_tree_sha256"],
        }:
            raise Qwen36DiagnosticError("plan_model_invalid")
        _validated_runtime_binding(value["runtime"])
        if value["execution"] != {
            "case_ids": list(CASE_IDS),
            "device": EXPECTED_DEVICE,
            "gpu_index": EXPECTED_GPU_INDEX,
            "dtype": "bf16",
            "quantization": "none",
            "cpu_offload": False,
            "device_map": "none",
            "thinking": False,
            "decode": "greedy",
            "max_input_tokens": MAX_INPUT_TOKENS,
            "max_new_tokens": MAX_NEW_TOKENS,
            "provider_call_count_cap": len(CASE_IDS),
            "provider_calls_per_case": 1,
            "retry_count": 0,
        }:
            raise Qwen36DiagnosticError("plan_execution_invalid")
        if value["claims"] != {
            "diagnostic_only": True,
            "external_action_authorized": False,
            "model_downloaded": False,
            "model_loaded": False,
            "run_occurred": False,
            "h1": False,
            "formal_quality": False,
            "browser_quality": False,
            "evidence_use": False,
            "lora_authorized": False,
        }:
            raise Qwen36DiagnosticError("plan_claims_invalid")
        _hex(value["plan_id"], "plan_id")
        if dict(value) != _identified(value, "plan_id"):
            raise Qwen36DiagnosticError("plan_id_mismatch")


@dataclass(frozen=True)
class Qwen36ModelInventory(_Record):
    """Canonical actual-file inventory for the pinned Qwen3.6 snapshot."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        _Record.__init__(self, data)

    def validate(self) -> None:
        value = _keys(
            self.data,
            (
                "schema_version",
                "inventory_id",
                "repository",
                "revision",
                "official_inventory_authority_id",
                "official_inventory_manifest_sha256",
                "files",
                "file_count",
                "total_bytes",
                "weight_shard_count",
                "weight_shard_total_bytes",
                "weight_tensor_total_bytes",
                "tree_sha256",
            ),
            "model_inventory",
        )
        official = _official_model_inventory_authority()
        if (
            value["schema_version"] != MODEL_INVENTORY_SCHEMA
            or value["repository"] != MODEL_REPOSITORY
            or value["revision"] != MODEL_REVISION
            or value["official_inventory_authority_id"] != official["authority_id"]
            or value["official_inventory_manifest_sha256"] != official["manifest_sha256"]
        ):
            raise Qwen36DiagnosticError("model_inventory_header_invalid")
        if type(value["files"]) is not list:
            raise Qwen36DiagnosticError("model_inventory_files_invalid")
        rows: list[dict[str, Any]] = []
        for item in value["files"]:
            row = _keys(item, ("relative_path", "bytes", "sha256"), "model_file")
            if type(row["relative_path"]) is not str or not row["relative_path"]:
                raise Qwen36DiagnosticError("model_file_path_invalid")
            _positive_int(row["bytes"], "model_file_bytes", allow_zero=True)
            _hex(row["sha256"], "model_file_sha256")
            rows.append(row)
        paths = tuple(row["relative_path"] for row in rows)
        if paths != tuple(sorted(MODEL_FILES)) or len(paths) != len(set(paths)):
            raise Qwen36DiagnosticError("model_file_set_invalid")
        if rows != official["files"]:
            raise Qwen36DiagnosticError("model_files_not_official")
        total = sum(row["bytes"] for row in rows)
        weights = [row for row in rows if row["relative_path"] in _WEIGHT_FILES]
        if (
            value["file_count"] != len(rows)
            or value["total_bytes"] != total
            or value["weight_shard_count"] != len(_WEIGHT_FILES)
            or value["weight_shard_total_bytes"] != sum(row["bytes"] for row in weights)
            or value["weight_shard_total_bytes"] != MODEL_WEIGHT_SHARD_TOTAL_BYTES
            or value["weight_tensor_total_bytes"] != MODEL_WEIGHT_TENSOR_TOTAL_BYTES
        ):
            raise Qwen36DiagnosticError("model_inventory_totals_invalid")
        if value["tree_sha256"] != _sha(_canonical(rows)):
            raise Qwen36DiagnosticError("model_inventory_tree_invalid")
        _hex(value["inventory_id"], "inventory_id")
        if dict(value) != _identified(value, "inventory_id"):
            raise Qwen36DiagnosticError("model_inventory_id_mismatch")


@dataclass(frozen=True)
class Qwen36DiagnosticActionBinding(_Record):
    """Action-time GPU selection bound to the exact no-model plan."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        _Record.__init__(self, data)

    def validate(self) -> None:
        value = _keys(
            self.data,
            (
                "schema_version",
                "binding_id",
                "status",
                "plan",
                "model_inventory",
                "runtime_inventory",
                "selected_gpu",
                "execution",
                "claims",
            ),
            "action_binding",
        )
        if (
            value["schema_version"] != ACTION_BINDING_SCHEMA
            or value["status"] != "selected_action_time_identity"
        ):
            raise Qwen36DiagnosticError("action_binding_header_invalid")
        for label, item in (
            ("plan", value["plan"]),
            ("model_inventory", value["model_inventory"]),
            ("runtime_inventory", value["runtime_inventory"]),
        ):
            binding = _keys(item, ("id", "sha256"), "action_" + label)
            _hex(binding["id"], "action_" + label + "_id")
            _hex(binding["sha256"], "action_" + label + "_sha256")
        selected = _keys(
            value["selected_gpu"],
            ("index", "name", "uuid"),
            "action_selected_gpu",
        )
        if (
            selected["index"] != EXPECTED_GPU_INDEX
            or selected["name"] not in EXPECTED_GPU_NAMES
            or type(selected["uuid"]) is not str
            or _GPU_UUID.fullmatch(selected["uuid"]) is None
        ):
            raise Qwen36DiagnosticError("action_selected_gpu_invalid")
        if value["execution"] != {
            "case_ids": list(CASE_IDS),
            "device": EXPECTED_DEVICE,
            "dtype": "bf16",
            "quantization": "none",
            "cpu_offload": False,
            "device_map": "none",
            "thinking": False,
            "decode": "greedy",
            "max_input_tokens": MAX_INPUT_TOKENS,
            "max_new_tokens": MAX_NEW_TOKENS,
            "provider_calls_per_case": 1,
            "provider_call_count_cap": len(CASE_IDS),
            "retry_count": 0,
        }:
            raise Qwen36DiagnosticError("action_execution_invalid")
        if value["claims"] != {
            "diagnostic_only": True,
            "artifact_is_not_external_action_authorization": True,
            "h1": False,
            "formal_quality": False,
            "browser_quality": False,
            "evidence_use": False,
            "lora_authorized": False,
        }:
            raise Qwen36DiagnosticError("action_claims_invalid")
        _hex(value["binding_id"], "binding_id")
        if dict(value) != _identified(value, "binding_id"):
            raise Qwen36DiagnosticError("binding_id_mismatch")


def create_qwen36_diagnostic_plan(
    repository_archive: Path | str,
    repository_archive_manifest: Path | str,
    case_bundle_root: Path | str,
    runtime_inventory: bytes | Path | str,
) -> Qwen36DiagnosticPlan:
    archive = validate_archive_file(repository_archive, repository_archive_manifest)
    archive_data = archive.to_dict()
    bundle = validate_qwen27b_case_bundle(
        case_bundle_root,
        repository_archive_path=repository_archive,
        repository_archive_manifest_path=repository_archive_manifest,
    )
    authority = bundle.manifest["source"]["repository_archive_authority"]
    if authority != {
        "manifest_id": archive_data["manifest_id"],
        "manifest_sha256": archive.sha256(),
        "commit_sha": archive_data["commit_sha"],
        "tree_sha": archive_data["tree_sha"],
        "archive_byte_length": archive_data["archive"]["byte_length"],
        "archive_sha256": archive_data["archive"]["sha256"],
    }:
        raise Qwen36DiagnosticError("case_bundle_archive_authority_mismatch")
    official = _official_model_inventory_authority()
    runtime_binding = _runtime_inventory_binding(runtime_inventory)
    body = {
        "schema_version": PLAN_SCHEMA,
        "plan_id": "0" * 64,
        "status": "prepared_local_no_model",
        "source": {
            "commit_sha": archive_data["commit_sha"],
            "tree_sha": archive_data["tree_sha"],
            "archive_manifest_id": archive_data["manifest_id"],
            "archive_manifest_sha256": archive.sha256(),
            "archive_sha256": archive_data["archive"]["sha256"],
            "archive_byte_length": archive_data["archive"]["byte_length"],
            "case_bundle_id": bundle.manifest["bundle_id"],
            "case_bundle_sha256": bundle.sha256(),
            "case_bundle_origin_model_repository": (
                CASE_BUNDLE_ORIGIN_MODEL_REPOSITORY
            ),
            "case_bundle_origin_model_revision": (
                CASE_BUNDLE_ORIGIN_MODEL_REVISION
            ),
            "case_bundle_model_profile_role": (
                "prompt_contract_origin_only_not_runtime_authority"
            ),
        },
        "model": {
            "id": MODEL_ID,
            "repository": MODEL_REPOSITORY,
            "revision": MODEL_REVISION,
            "license": MODEL_LICENSE,
            "architecture": MODEL_ARCHITECTURE,
            "model_type": MODEL_TYPE,
            "file_count": official["file_count"],
            "total_bytes": official["total_bytes"],
            "weight_shard_count": len(_WEIGHT_FILES),
            "weight_shard_total_bytes": MODEL_WEIGHT_SHARD_TOTAL_BYTES,
            "weight_tensor_total_bytes": MODEL_WEIGHT_TENSOR_TOTAL_BYTES,
            "official_inventory_authority_id": official["authority_id"],
            "official_inventory_manifest_sha256": official["manifest_sha256"],
            "official_inventory_tree_sha256": official["official_tree_sha256"],
        },
        "runtime": runtime_binding,
        "execution": {
            "case_ids": list(CASE_IDS),
            "device": EXPECTED_DEVICE,
            "gpu_index": EXPECTED_GPU_INDEX,
            "dtype": "bf16",
            "quantization": "none",
            "cpu_offload": False,
            "device_map": "none",
            "thinking": False,
            "decode": "greedy",
            "max_input_tokens": MAX_INPUT_TOKENS,
            "max_new_tokens": MAX_NEW_TOKENS,
            "provider_call_count_cap": len(CASE_IDS),
            "provider_calls_per_case": 1,
            "retry_count": 0,
        },
        "claims": {
            "diagnostic_only": True,
            "external_action_authorized": False,
            "model_downloaded": False,
            "model_loaded": False,
            "run_occurred": False,
            "h1": False,
            "formal_quality": False,
            "browser_quality": False,
            "evidence_use": False,
            "lora_authorized": False,
        },
    }
    return Qwen36DiagnosticPlan(_identified(body, "plan_id"))


def create_qwen36_diagnostic_action_binding(
    plan: Qwen36DiagnosticPlan | bytes | Path | str,
    model_inventory: Qwen36ModelInventory | bytes | Path | str,
    runtime_inventory: bytes | Path | str,
    *,
    expected_gpu_name: str,
    expected_gpu_uuid: str,
) -> Qwen36DiagnosticActionBinding:
    plan_record = _read_record(plan, Qwen36DiagnosticPlan, "plan")
    model_record = _read_record(
        model_inventory, Qwen36ModelInventory, "model_inventory"
    )
    runtime_binding = _runtime_inventory_binding(runtime_inventory)
    if plan_record.to_dict()["runtime"] != runtime_binding:
        raise Qwen36DiagnosticError("action_runtime_plan_mismatch")
    if (
        expected_gpu_name not in EXPECTED_GPU_NAMES
        or type(expected_gpu_uuid) is not str
        or _GPU_UUID.fullmatch(expected_gpu_uuid) is None
    ):
        raise Qwen36DiagnosticError("action_selected_gpu_invalid")
    body = {
        "schema_version": ACTION_BINDING_SCHEMA,
        "binding_id": "0" * 64,
        "status": "selected_action_time_identity",
        "plan": {
            "id": plan_record.to_dict()["plan_id"],
            "sha256": plan_record.sha256(),
        },
        "model_inventory": {
            "id": model_record.to_dict()["inventory_id"],
            "sha256": model_record.sha256(),
        },
        "runtime_inventory": {
            "id": runtime_binding["inventory_id"],
            "sha256": runtime_binding["sha256"],
        },
        "selected_gpu": {
            "index": EXPECTED_GPU_INDEX,
            "name": expected_gpu_name,
            "uuid": expected_gpu_uuid,
        },
        "execution": {
            "case_ids": list(CASE_IDS),
            "device": EXPECTED_DEVICE,
            "dtype": "bf16",
            "quantization": "none",
            "cpu_offload": False,
            "device_map": "none",
            "thinking": False,
            "decode": "greedy",
            "max_input_tokens": MAX_INPUT_TOKENS,
            "max_new_tokens": MAX_NEW_TOKENS,
            "provider_calls_per_case": 1,
            "provider_call_count_cap": len(CASE_IDS),
            "retry_count": 0,
        },
        "claims": {
            "diagnostic_only": True,
            "artifact_is_not_external_action_authorization": True,
            "h1": False,
            "formal_quality": False,
            "browser_quality": False,
            "evidence_use": False,
            "lora_authorized": False,
        },
    }
    return Qwen36DiagnosticActionBinding(
        _identified(body, "binding_id")
    )


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    observed = 0
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
            observed += len(chunk)
    return observed, digest.hexdigest()


def collect_qwen36_model_inventory(model_root: Path | str) -> Qwen36ModelInventory:
    root = Path(model_root)
    if root.is_symlink() or not root.is_dir():
        raise Qwen36DiagnosticError("model_root_invalid")
    root = root.resolve(strict=True)
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_paths != set(MODEL_FILES):
        raise Qwen36DiagnosticError("model_root_file_set_invalid")
    rows: list[dict[str, Any]] = []
    for relative in sorted(MODEL_FILES):
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise Qwen36DiagnosticError("model_root_non_regular_file")
        observed, digest = _hash_file(path)
        if observed != path.stat().st_size:
            raise Qwen36DiagnosticError("model_file_changed_during_hash")
        rows.append(
            {"relative_path": relative, "bytes": observed, "sha256": digest}
        )
    official = _official_model_inventory_authority()
    if rows != official["files"]:
        raise Qwen36DiagnosticError("model_root_official_inventory_mismatch")
    try:
        config = json.loads((root / "config.json").read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Qwen36DiagnosticError("model_config_invalid") from exc
    text_config = config.get("text_config")
    if (
        config.get("model_type") != MODEL_TYPE
        or config.get("architectures") != [MODEL_ARCHITECTURE]
        or type(text_config) is not dict
        or text_config.get("model_type") != "qwen3_5_text"
        or text_config.get("dtype") != "bfloat16"
        or text_config.get("num_hidden_layers") != 64
    ):
        raise Qwen36DiagnosticError("model_config_invalid")
    try:
        weight_index = json.loads(
            (root / "model.safetensors.index.json").read_bytes()
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Qwen36DiagnosticError("model_weight_index_invalid") from exc
    if weight_index.get("metadata", {}).get("total_size") != MODEL_WEIGHT_TENSOR_TOTAL_BYTES:
        raise Qwen36DiagnosticError("model_weight_index_total_invalid")
    mapped = set(weight_index.get("weight_map", {}).values())
    if mapped != set(_WEIGHT_FILES):
        raise Qwen36DiagnosticError("model_weight_index_shards_invalid")
    body = {
        "schema_version": MODEL_INVENTORY_SCHEMA,
        "inventory_id": "0" * 64,
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "official_inventory_authority_id": official["authority_id"],
        "official_inventory_manifest_sha256": official["manifest_sha256"],
        "files": rows,
        "file_count": len(rows),
        "total_bytes": sum(row["bytes"] for row in rows),
        "weight_shard_count": len(_WEIGHT_FILES),
        "weight_shard_total_bytes": sum(
            row["bytes"] for row in rows if row["relative_path"] in _WEIGHT_FILES
        ),
        "weight_tensor_total_bytes": MODEL_WEIGHT_TENSOR_TOTAL_BYTES,
        "tree_sha256": _sha(_canonical(rows)),
    }
    return Qwen36ModelInventory(_identified(body, "inventory_id"))


def validate_qwen36_model_root_against_inventory(
    model_root: Path | str,
    inventory: Qwen36ModelInventory | bytes | Path | str,
) -> Qwen36ModelInventory:
    expected = _read_record(inventory, Qwen36ModelInventory, "model_inventory")
    actual = collect_qwen36_model_inventory(model_root)
    if actual.canonical_bytes() != expected.canonical_bytes():
        raise Qwen36DiagnosticError("model_inventory_live_mismatch")
    return actual


def validate_qwen36_runtime_root_against_inventory(
    runtime_root: Path | str,
    runtime_inventory: bytes | Path | str,
) -> dict[str, Any]:
    expected, raw = _read_runtime_inventory(runtime_inventory)
    root = Path(runtime_root)
    if root.is_symlink() or not root.is_dir():
        raise Qwen36DiagnosticError("runtime_root_invalid")
    root = root.resolve(strict=True)
    try:
        actual = _runtime_authority.collect_runtime_root_inventory(root)
    except _runtime_authority.Qwen27BPredeployError as exc:
        raise Qwen36DiagnosticError(
            "runtime_inventory_live_validation_failed"
        ) from exc
    if _runtime_authority._dump(actual) != raw:
        raise Qwen36DiagnosticError("runtime_inventory_live_mismatch")
    executable = Path(sys.executable).resolve(strict=True)
    try:
        relative = executable.relative_to(root).as_posix()
    except ValueError as exc:
        raise Qwen36DiagnosticError(
            "worker_python_outside_runtime_root"
        ) from exc
    if relative != expected["interpreter_relative_path"]:
        raise Qwen36DiagnosticError(
            "worker_python_runtime_inventory_mismatch"
        )
    return copy.deepcopy(expected)


def _existing_root(value: Path | str, label: str) -> Path:
    path = Path(value)
    if path.is_symlink() or not path.is_dir():
        raise Qwen36DiagnosticError(f"{label}_invalid")
    return path.resolve(strict=True)


def _new_output_root(value: Path | str) -> Path:
    path = Path(value)
    if path.exists():
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise Qwen36DiagnosticError("result_root_not_new_or_empty")
    else:
        path.mkdir(parents=True, exist_ok=False)
    return path.resolve(strict=True)


def _atomic_write(root: Path, relative: str, raw: bytes, *, cap: int) -> None:
    if type(raw) is not bytes or len(raw) > cap:
        raise Qwen36DiagnosticError("artifact_byte_cap_exceeded")
    path = root / relative
    if path.exists() or path.is_symlink():
        raise Qwen36DiagnosticError("artifact_path_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp")
    with temp.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _file_binding(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise Qwen36DiagnosticError("result_file_invalid")
    raw = path.read_bytes()
    return {"relative_path": relative, "byte_length": len(raw), "sha256": _sha(raw)}


def _validate_generation(result: object, case_id: str) -> BackendGeneration:
    if type(result) is not BackendGeneration:
        raise Qwen36DiagnosticError("backend_generation_exact_type_required")
    if type(result.text) is not str or not result.text:
        raise Qwen36DiagnosticError("empty_raw_response")
    if (
        type(result.input_tokens) is not int
        or not 0 < result.input_tokens <= MAX_INPUT_TOKENS
        or type(result.generated_tokens) is not int
        or not 0 < result.generated_tokens <= MAX_NEW_TOKENS
        or type(result.elapsed_ms) is not int
        or result.elapsed_ms < 0
        or type(result.stream_event_count) is not int
        or result.stream_event_count < 0
        or type(result.stream_text) is not str
        or case_id not in CASE_IDS
    ):
        raise Qwen36DiagnosticError("backend_generation_invalid")
    return result


def _validate_runtime(
    value: Mapping[str, Any], expected_gpu_name: str, expected_gpu_uuid: str
) -> dict[str, Any]:
    runtime = dict(value)
    if (
        type(expected_gpu_name) is not str
        or expected_gpu_name not in EXPECTED_GPU_NAMES
        or type(expected_gpu_uuid) is not str
        or _GPU_UUID.fullmatch(expected_gpu_uuid) is None
        or any(runtime.get(name) != expected for name, expected in EXPECTED_RUNTIME.items())
        or runtime.get("gpu_index") != EXPECTED_GPU_INDEX
        or runtime.get("gpu_name") != expected_gpu_name
        or runtime.get("gpu_uuid") != expected_gpu_uuid
        or runtime.get("torch_gpu_name") != expected_gpu_name
        or runtime.get("torch_gpu_uuid") not in {None, expected_gpu_uuid}
        or type(runtime.get("total_vram_bytes")) is not int
        or runtime["total_vram_bytes"] < MIN_TOTAL_VRAM_BYTES
        or type(runtime.get("torch_total_vram_bytes")) is not int
        or runtime["torch_total_vram_bytes"] < MIN_TOTAL_VRAM_BYTES
        or type(runtime.get("free_vram_bytes_before_load")) is not int
        or runtime["free_vram_bytes_before_load"] < MIN_FREE_VRAM_BEFORE_LOAD_BYTES
        or runtime.get("device") != EXPECTED_DEVICE
        or runtime.get("dtype") != "bf16"
        or runtime.get("quantization") != "none"
        or runtime.get("cpu_offload") is not False
        or runtime.get("device_map") != "none"
    ):
        raise Qwen36DiagnosticError("runtime_profile_mismatch")
    return copy.deepcopy(runtime)


def _validate_loaded(value: Mapping[str, Any]) -> dict[str, Any]:
    loaded = dict(value)
    if (
        loaded.get("device") != EXPECTED_DEVICE
        or loaded.get("dtype") != "bf16"
        or loaded.get("quantization") != "none"
        or loaded.get("cpu_offload") is not False
        or loaded.get("device_map") != "none"
    ):
        raise Qwen36DiagnosticError("loaded_model_profile_mismatch")
    _positive_int(loaded.get("allocated_vram_bytes"), "allocated_vram_bytes")
    _positive_int(loaded.get("reserved_vram_bytes"), "reserved_vram_bytes")
    return copy.deepcopy(loaded)


def _validate_run_manifest(value: object) -> dict[str, Any]:
    run = _keys(
        value,
        (
            "schema_version",
            "run_id",
            "state",
            "plan",
            "action_binding",
            "model",
            "model_inventory",
            "runtime_inventory",
            "backend",
            "actual_runtime",
            "loaded_model",
            "model_load_duration_ms",
            "execution",
            "cases",
            "inventory",
            "claims",
        ),
        "run_manifest",
    )
    if run["schema_version"] != RUN_SCHEMA or run["state"] != "completed_raw_only":
        raise Qwen36DiagnosticError("run_header_invalid")
    _keys(run["plan"], ("plan_id", "sha256"), "run_plan")
    _hex(run["plan"]["plan_id"], "run_plan_id")
    _hex(run["plan"]["sha256"], "run_plan_sha256")
    action_binding = _keys(
        run["action_binding"],
        ("binding_id", "sha256"),
        "run_action_binding",
    )
    _hex(action_binding["binding_id"], "run_action_binding_id")
    _hex(action_binding["sha256"], "run_action_binding_sha256")
    if run["model"] != {
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "local_files_only": True,
        "trust_remote_code": False,
    }:
        raise Qwen36DiagnosticError("run_model_invalid")
    actual_runtime = run["actual_runtime"]
    if not isinstance(actual_runtime, Mapping):
        raise Qwen36DiagnosticError("run_actual_runtime_invalid")
    _validate_runtime(
        actual_runtime,
        actual_runtime.get("gpu_name"),
        actual_runtime.get("gpu_uuid"),
    )
    loaded_model = run["loaded_model"]
    if not isinstance(loaded_model, Mapping):
        raise Qwen36DiagnosticError("run_loaded_model_invalid")
    _validate_loaded(loaded_model)
    _validated_runtime_binding(run["runtime_inventory"])
    if run["backend"] != {
        "kind": "fixed_transformers_backend",
        "class": "TransformersRecoveryBackend",
        "module": "req2web_runtime.qwen27b_recovery_runner",
        "caller_backend_injection_allowed": False,
    }:
        raise Qwen36DiagnosticError("run_backend_provenance_invalid")
    inventory_binding = _keys(
        run["model_inventory"],
        ("inventory_id", "sha256", "tree_sha256"),
        "run_model_inventory",
    )
    for name in inventory_binding:
        _hex(inventory_binding[name], "run_model_inventory_" + name)
    _positive_int(run["model_load_duration_ms"], "model_load_duration_ms", allow_zero=True)
    execution = _keys(
        run["execution"],
        ("case_ids", "provider_call_count", "retry_count", "elapsed_ms"),
        "run_execution",
    )
    if (
        execution["case_ids"] != list(CASE_IDS)
        or execution["provider_call_count"] != len(CASE_IDS)
        or execution["retry_count"] != 0
    ):
        raise Qwen36DiagnosticError("run_execution_invalid")
    _positive_int(execution["elapsed_ms"], "run_elapsed_ms", allow_zero=True)
    if type(run["cases"]) is not list or len(run["cases"]) != len(CASE_IDS):
        raise Qwen36DiagnosticError("run_cases_invalid")
    for expected_case, item in zip(CASE_IDS, run["cases"], strict=True):
        case = _keys(
            item,
            (
                "case_id",
                "provider_call_count",
                "retry_count",
                "input_tokens",
                "generated_tokens",
                "generation_elapsed_ms",
                "tokens_per_second",
                "stream_output",
                "stream_event_count",
                "stream_matches_final_raw",
                "raw_response",
                "downstream_state",
            ),
            "run_case",
        )
        if (
            case["case_id"] != expected_case
            or case["provider_call_count"] != 1
            or case["retry_count"] != 0
            or case["downstream_state"] != "not_started_raw_authoritative"
            or case["stream_output"] not in {"off", "console", "jsonl"}
            or type(case["stream_matches_final_raw"]) not in {bool, type(None)}
        ):
            raise Qwen36DiagnosticError("run_case_contract_invalid")
        for name in (
            "input_tokens",
            "generated_tokens",
            "generation_elapsed_ms",
            "stream_event_count",
        ):
            _positive_int(
                case[name],
                name,
                allow_zero=name in {"generation_elapsed_ms", "stream_event_count"},
            )
        if (
            case["input_tokens"] > MAX_INPUT_TOKENS
            or case["generated_tokens"] > MAX_NEW_TOKENS
            or (
                case["stream_output"] == "off"
                and (
                    case["stream_event_count"] != 0
                    or case["stream_matches_final_raw"] is not None
                )
            )
            or (
                case["stream_output"] != "off"
                and type(case["stream_matches_final_raw"]) is not bool
            )
        ):
            raise Qwen36DiagnosticError("run_case_metrics_invalid")
        if type(case["tokens_per_second"]) not in {int, float} or case["tokens_per_second"] < 0:
            raise Qwen36DiagnosticError("tokens_per_second_invalid")
        raw = _keys(case["raw_response"], ("relative_path", "byte_length", "sha256"), "raw_response")
        if raw["relative_path"] != f"cases/{expected_case}/raw_response.bin":
            raise Qwen36DiagnosticError("raw_response_path_invalid")
        _positive_int(raw["byte_length"], "raw_response_bytes")
        _hex(raw["sha256"], "raw_response_sha256")
    inventory = _keys(run["inventory"], ("files", "file_count", "total_bytes", "inventory_sha256"), "run_inventory")
    expected_rows = [case["raw_response"] for case in run["cases"]]
    if (
        inventory["files"] != expected_rows
        or inventory["file_count"] != len(expected_rows)
        or inventory["total_bytes"] != sum(row["byte_length"] for row in expected_rows)
        or inventory["inventory_sha256"] != _sha(_canonical(expected_rows))
    ):
        raise Qwen36DiagnosticError("run_inventory_invalid")
    if run["claims"] != {
        "diagnostic_only": True,
        "h1": False,
        "formal_quality": False,
        "browser_quality": False,
        "evidence_use": False,
        "semantic_success": False,
        "lora_evidence": False,
    }:
        raise Qwen36DiagnosticError("run_claims_invalid")
    _hex(run["run_id"], "run_id")
    if run != _identified(run, "run_id"):
        raise Qwen36DiagnosticError("run_id_mismatch")
    return run


def validate_qwen36_diagnostic_result(result_root: Path | str) -> dict[str, Any]:
    root = _existing_root(result_root, "result_root")
    manifest_path = root / RUN_MANIFEST_FILENAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise Qwen36DiagnosticError("run_manifest_missing")
    raw = manifest_path.read_bytes()
    run = _validate_run_manifest(_load(raw))
    if raw != _canonical(run):
        raise Qwen36DiagnosticError("run_manifest_not_canonical")
    expected_paths = {
        RUN_MANIFEST_FILENAME,
        *(f"cases/{case_id}/raw_response.bin" for case_id in CASE_IDS),
    }
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_paths != expected_paths:
        raise Qwen36DiagnosticError("run_result_file_set_invalid")
    for case in run["cases"]:
        binding = _file_binding(root, case["raw_response"]["relative_path"])
        if binding != case["raw_response"]:
            raise Qwen36DiagnosticError("run_raw_live_mismatch")
    return copy.deepcopy(run)


def validate_qwen36_diagnostic_result_against(
    result_root: Path | str,
    plan: Qwen36DiagnosticPlan | bytes | Path | str,
    action_binding: Qwen36DiagnosticActionBinding | bytes | Path | str,
    model_inventory: Qwen36ModelInventory | bytes | Path | str,
    runtime_inventory: bytes | Path | str,
    repository_archive: Path | str,
    repository_archive_manifest: Path | str,
    case_bundle_root: Path | str,
) -> dict[str, Any]:
    plan_record = _read_record(plan, Qwen36DiagnosticPlan, "plan")
    action_record = _read_record(
        action_binding, Qwen36DiagnosticActionBinding, "action_binding"
    )
    inventory_record = _read_record(
        model_inventory, Qwen36ModelInventory, "model_inventory"
    )
    runtime_binding = _runtime_inventory_binding(runtime_inventory)
    expected_plan = create_qwen36_diagnostic_plan(
        repository_archive,
        repository_archive_manifest,
        case_bundle_root,
        runtime_inventory,
    )
    if expected_plan.canonical_bytes() != plan_record.canonical_bytes():
        raise Qwen36DiagnosticError("plan_live_mismatch")
    selected = action_record.to_dict()["selected_gpu"]
    expected_action = create_qwen36_diagnostic_action_binding(
        plan_record,
        inventory_record,
        runtime_inventory,
        expected_gpu_name=selected["name"],
        expected_gpu_uuid=selected["uuid"],
    )
    if expected_action.canonical_bytes() != action_record.canonical_bytes():
        raise Qwen36DiagnosticError("action_binding_live_mismatch")
    run = validate_qwen36_diagnostic_result(result_root)
    if run["plan"] != {
        "plan_id": plan_record.to_dict()["plan_id"],
        "sha256": plan_record.sha256(),
    }:
        raise Qwen36DiagnosticError("run_plan_binding_invalid")
    if run["action_binding"] != {
        "binding_id": action_record.to_dict()["binding_id"],
        "sha256": action_record.sha256(),
    }:
        raise Qwen36DiagnosticError("run_action_binding_invalid")
    if run["model_inventory"] != {
        "inventory_id": inventory_record.to_dict()["inventory_id"],
        "sha256": inventory_record.sha256(),
        "tree_sha256": inventory_record.to_dict()["tree_sha256"],
    }:
        raise Qwen36DiagnosticError("run_model_inventory_binding_invalid")
    if run["runtime_inventory"] != runtime_binding:
        raise Qwen36DiagnosticError("run_runtime_inventory_binding_invalid")
    actual_runtime = run["actual_runtime"]
    if (
        actual_runtime["gpu_index"] != selected["index"]
        or actual_runtime["gpu_name"] != selected["name"]
        or actual_runtime["gpu_uuid"] != selected["uuid"]
    ):
        raise Qwen36DiagnosticError("run_selected_gpu_binding_invalid")
    return run


def execute_qwen36_diagnostic(
    plan: Qwen36DiagnosticPlan | bytes | Path | str,
    action_binding: Qwen36DiagnosticActionBinding | bytes | Path | str,
    model_inventory: Qwen36ModelInventory | bytes | Path | str,
    runtime_inventory: bytes | Path | str,
    model_root: Path | str,
    runtime_root: Path | str,
    repository_archive: Path | str,
    repository_archive_manifest: Path | str,
    case_bundle_root: Path | str,
    result_root: Path | str,
    *,
    stream_output: str = "off",
    stream: TextIO | None = None,
) -> dict[str, Any]:
    if stream_output not in MODEL_TEXT_STREAM_MODES:
        raise Qwen36DiagnosticError("stream_output_mode_invalid")
    plan_record = _read_record(plan, Qwen36DiagnosticPlan, "plan")
    action_record = _read_record(
        action_binding, Qwen36DiagnosticActionBinding, "action_binding"
    )
    inventory_record = _read_record(
        model_inventory, Qwen36ModelInventory, "model_inventory"
    )
    expected_plan = create_qwen36_diagnostic_plan(
        repository_archive,
        repository_archive_manifest,
        case_bundle_root,
        runtime_inventory,
    )
    if expected_plan.canonical_bytes() != plan_record.canonical_bytes():
        raise Qwen36DiagnosticError("plan_live_mismatch")
    selected = action_record.to_dict()["selected_gpu"]
    expected_action = create_qwen36_diagnostic_action_binding(
        plan_record,
        inventory_record,
        runtime_inventory,
        expected_gpu_name=selected["name"],
        expected_gpu_uuid=selected["uuid"],
    )
    if expected_action.canonical_bytes() != action_record.canonical_bytes():
        raise Qwen36DiagnosticError("action_binding_live_mismatch")
    _progress("qwen36_runtime_inventory_validation_started")
    live_runtime_inventory = validate_qwen36_runtime_root_against_inventory(
        runtime_root, runtime_inventory
    )
    _progress(
        "qwen36_runtime_inventory_validated",
        inventory_id=live_runtime_inventory["inventory_id"],
        tree_sha256=live_runtime_inventory["tree_sha256"],
    )
    runtime_binding = _runtime_inventory_binding(runtime_inventory)
    model = _existing_root(model_root, "model_root")
    _progress(
        "qwen36_model_inventory_validation_started",
        model_repository=MODEL_REPOSITORY,
        model_revision=MODEL_REVISION,
        file_count=len(MODEL_FILES),
    )
    validate_qwen36_model_root_against_inventory(model, inventory_record)
    _progress(
        "qwen36_model_inventory_validated",
        inventory_id=inventory_record.to_dict()["inventory_id"],
        tree_sha256=inventory_record.to_dict()["tree_sha256"],
    )
    bundle = validate_qwen27b_case_bundle(
        case_bundle_root,
        repository_archive_path=repository_archive,
        repository_archive_manifest_path=repository_archive_manifest,
    )
    output = _new_output_root(result_root)
    actual_backend = TransformersRecoveryBackend()
    display = stream if stream is not None else sys.stderr
    provider_calls = 0
    raw_paths: list[str] = []
    cases: list[dict[str, Any]] = []
    run_started = time.monotonic()
    _progress(
        "qwen36_diagnostic_preconditions_validated",
        case_ids=list(CASE_IDS),
        stream_output=stream_output,
    )
    try:
        runtime = _validate_runtime(
            actual_backend.runtime_facts(EXPECTED_RUNTIME),
            selected["name"],
            selected["uuid"],
        )
        _progress(
            "qwen36_runtime_validated",
            device=runtime["device"],
            gpu_name=runtime["gpu_name"],
            total_vram_bytes=runtime["total_vram_bytes"],
            dtype=runtime["dtype"],
            quantization=runtime["quantization"],
        )
        _progress("qwen36_model_load_started")
        load_started = time.monotonic()
        actual_backend.load(model)
        model_load_ms = max(0, int((time.monotonic() - load_started) * 1000))
        loaded = _validate_loaded(actual_backend.loaded_facts())
        _progress(
            "qwen36_model_loaded",
            duration_ms=model_load_ms,
            device=loaded["device"],
            allocated_vram_bytes=loaded["allocated_vram_bytes"],
            reserved_vram_bytes=loaded["reserved_vram_bytes"],
        )
        total_raw = 0
        for case_id in CASE_IDS:
            _progress(
                "qwen36_generation_started",
                case_id=case_id,
                provider_call_index=1,
                retry_count=0,
                max_new_tokens=MAX_NEW_TOKENS,
            )
            provider_calls += 1
            generation = actual_backend.generate(
                compose_qwen27b_model_text(bundle, case_id),
                case_id,
                stream_output=stream_output,
                stream=display,
            )
            generation = _validate_generation(generation, case_id)
            raw = generation.text.encode("utf-8")
            if not raw or len(raw) > MAX_RAW_BYTES_PER_CASE:
                raise Qwen36DiagnosticError("raw_response_cap_invalid")
            total_raw += len(raw)
            if total_raw > MAX_TOTAL_RAW_BYTES:
                raise Qwen36DiagnosticError("total_raw_response_cap_exceeded")
            relative = f"cases/{case_id}/raw_response.bin"
            _atomic_write(output, relative, raw, cap=MAX_RAW_BYTES_PER_CASE)
            raw_paths.append(relative)
            rate = 0.0 if generation.elapsed_ms == 0 else round(
                generation.generated_tokens / (generation.elapsed_ms / 1000.0), 6
            )
            cases.append(
                {
                    "case_id": case_id,
                    "provider_call_count": 1,
                    "retry_count": 0,
                    "input_tokens": generation.input_tokens,
                    "generated_tokens": generation.generated_tokens,
                    "generation_elapsed_ms": generation.elapsed_ms,
                    "tokens_per_second": rate,
                    "stream_output": stream_output,
                    "stream_event_count": generation.stream_event_count,
                    "stream_matches_final_raw": (
                        None if stream_output == "off" else generation.stream_text == generation.text
                    ),
                    "raw_response": {
                        "relative_path": relative,
                        "byte_length": len(raw),
                        "sha256": _sha(raw),
                    },
                    "downstream_state": "not_started_raw_authoritative",
                }
            )
            _progress(
                "qwen36_raw_persisted",
                case_id=case_id,
                byte_length=len(raw),
                sha256=_sha(raw),
                generated_tokens=generation.generated_tokens,
                tokens_per_second=rate,
                stream_matches_final_raw=(
                    None
                    if stream_output == "off"
                    else generation.stream_text == generation.text
                ),
            )
        rows = [_file_binding(output, relative) for relative in raw_paths]
        body = {
            "schema_version": RUN_SCHEMA,
            "run_id": "0" * 64,
            "state": "completed_raw_only",
            "plan": {
                "plan_id": plan_record.to_dict()["plan_id"],
                "sha256": plan_record.sha256(),
            },
            "action_binding": {
                "binding_id": action_record.to_dict()["binding_id"],
                "sha256": action_record.sha256(),
            },
            "model": {
                "repository": MODEL_REPOSITORY,
                "revision": MODEL_REVISION,
                "local_files_only": True,
                "trust_remote_code": False,
            },
            "model_inventory": {
                "inventory_id": inventory_record.to_dict()["inventory_id"],
                "sha256": inventory_record.sha256(),
                "tree_sha256": inventory_record.to_dict()["tree_sha256"],
            },
            "runtime_inventory": runtime_binding,
            "backend": {
                "kind": "fixed_transformers_backend",
                "class": "TransformersRecoveryBackend",
                "module": "req2web_runtime.qwen27b_recovery_runner",
                "caller_backend_injection_allowed": False,
            },
            "actual_runtime": runtime,
            "loaded_model": loaded,
            "model_load_duration_ms": model_load_ms,
            "execution": {
                "case_ids": list(CASE_IDS),
                "provider_call_count": provider_calls,
                "retry_count": 0,
                "elapsed_ms": max(0, int((time.monotonic() - run_started) * 1000)),
            },
            "cases": cases,
            "inventory": {
                "files": rows,
                "file_count": len(rows),
                "total_bytes": sum(row["byte_length"] for row in rows),
                "inventory_sha256": _sha(_canonical(rows)),
            },
            "claims": {
                "diagnostic_only": True,
                "h1": False,
                "formal_quality": False,
                "browser_quality": False,
                "evidence_use": False,
                "semantic_success": False,
                "lora_evidence": False,
            },
        }
        body = _identified(body, "run_id")
        _atomic_write(output, RUN_MANIFEST_FILENAME, _canonical(body), cap=1024 * 1024)
        _progress(
            "qwen36_diagnostic_completed",
            run_id=body["run_id"],
            provider_call_count=provider_calls,
            retry_count=0,
        )
        return validate_qwen36_diagnostic_result_against(
            output,
            plan_record,
            action_record,
            inventory_record,
            runtime_inventory,
            repository_archive,
            repository_archive_manifest,
            bundle.root,
        )
    except BaseException as exc:
        failure = {
            "schema_version": "req2web.runtime.qwen36_27b_diagnostic_failure.v1",
            "state": "failed_closed",
            "error_code": str(exc)[:160] or type(exc).__name__,
            "provider_call_count": provider_calls,
            "retry_count": 0,
            "completed_raw_paths": list(raw_paths),
        }
        try:
            if not (output / FAILURE_FILENAME).exists():
                _atomic_write(output, FAILURE_FILENAME, _canonical(failure), cap=64 * 1024)
        except Exception:
            pass
        if isinstance(exc, Qwen36DiagnosticError):
            raise
        raise Qwen36DiagnosticError("diagnostic_worker_failed") from exc


__all__ = (
    "ACTION_BINDING_SCHEMA",
    "CASE_IDS",
    "EXPECTED_RUNTIME",
    "MODEL_FILES",
    "MODEL_ID",
    "MODEL_INVENTORY_SCHEMA",
    "MODEL_LICENSE",
    "MODEL_REPOSITORY",
    "MODEL_REVISION",
    "MODEL_WEIGHT_SHARD_TOTAL_BYTES",
    "MODEL_WEIGHT_TENSOR_TOTAL_BYTES",
    "PLAN_SCHEMA",
    "RUN_MANIFEST_FILENAME",
    "RUN_SCHEMA",
    "Qwen36DiagnosticActionBinding",
    "Qwen36DiagnosticError",
    "Qwen36DiagnosticPlan",
    "Qwen36ModelInventory",
    "collect_qwen36_model_inventory",
    "create_qwen36_diagnostic_action_binding",
    "create_qwen36_diagnostic_plan",
    "execute_qwen36_diagnostic",
    "validate_qwen36_diagnostic_result",
    "validate_qwen36_diagnostic_result_against",
    "validate_qwen36_model_root_against_inventory",
    "validate_qwen36_runtime_root_against_inventory",
)
