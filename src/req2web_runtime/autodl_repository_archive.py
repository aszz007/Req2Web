"""Local tracked-tree coverage and policy-filtered archive foundation for trusted remote compute.

This module implements only a local, no-action repository snapshot contract. It
never performs SSH, upload, AutoDL, model, seccomp, Provider, or network work.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile
from types import MappingProxyType
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "req2web.runtime.autodl_policy_filtered_repository_archive_manifest.v1"
BUILDER_ID = "req2web.autodl_policy_filtered_repository_archive_builder.v1"
ARCHIVE_FORMAT = "zip"
ARCHIVE_FILENAME = "req2web_policy_filtered_repository_snapshot.zip"
MANIFEST_FILENAME = "req2web_policy_filtered_repository_snapshot.manifest.json"
EXCLUSION_POLICY_ID = "req2web.d17.trusted_remote_repository_exclusion_policy.v1"
FOUNDATION_STATUS = "local_foundation_only_not_uploaded_not_authorized"

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_FILE_MODES = {"100644", "100755"}
_MANIFEST_KEYS = (
    "schema_version",
    "manifest_id",
    "foundation_status",
    "repository",
    "commit_sha",
    "tree_sha",
    "source_tree",
    "builder",
    "archive",
    "exclusion_policy",
    "files",
    "excluded_files",
)
_REPOSITORY_KEYS = ("identity", "head_equals_origin_main", "tracked_state_clean")
_SOURCE_TREE_KEYS = (
    "tracked_file_count",
    "included_file_count",
    "excluded_file_count",
    "tracked_inventory_sha256",
    "coverage_sha256",
)
_BUILDER_KEYS = ("builder_id", "builder_version", "archive_format")
_ARCHIVE_KEYS = ("format", "filename", "byte_length", "sha256")
_EXCLUSION_KEYS = ("policy_id", "policy_sha256", "reason_codes")
_FILE_KEYS = ("path", "git_mode", "blob_sha", "byte_length", "sha256")
_EXCLUDED_FILE_KEYS = ("path", "git_mode", "blob_sha", "reason")

_DECISION_INCLUDE = "include"
_DECISION_EXCLUDE = "exclude"
_DECISION_AMBIGUOUS = "ambiguous_sensitive"

_REASON_RESTRICTED_RICO = "restricted_rico_material"
_REASON_H1_GOLD_EVALUATOR = "h1_gold_evaluator_data"
_REASON_REFERENCE_ONLY = "reference_only_material"
_REASON_CREDENTIAL_SECRET = "credential_secret"
_REASON_MODEL_WEIGHT = "model_weight"
_REASON_GENERATED_CACHE = "generated_output_cache"

_AMBIGUOUS_RICO = "ambiguous_rico"
_AMBIGUOUS_H1_GOLD_EVALUATOR = "ambiguous_h1_gold_evaluator"
_AMBIGUOUS_REFERENCE_ONLY = "ambiguous_reference_only"
_AMBIGUOUS_CREDENTIAL_SECRET = "ambiguous_credential_secret"
_AMBIGUOUS_MODEL_WEIGHT = "ambiguous_model_weight"
_AMBIGUOUS_GENERATED_CACHE = "ambiguous_generated_cache"

_EXPLICIT_SAFE_PATHS = (
    "docs/rico_reference_asset_permission_review.md",
    "scripts/audit_rico_combined.ps1",
    "scripts/audit_rico_combined.py",
    "scripts/audit_rico_traces.ps1",
    "scripts/audit_rico_traces.py",
    "scripts/build_rico_combined_final_review.py",
    "scripts/build_rico_replacement_final_review.py",
    "scripts/build_rico_replacement_review.py",
    "scripts/build_rico_review_sheets.py",
    "scripts/build_rico_second_replacement_review.py",
    "scripts/build_rico_selected_review.py",
    "scripts/build_rico_trace_replacement_review.py",
    "scripts/build_rico_trace_review.py",
    "scripts/finalize_rico_combined.py",
    "scripts/finalize_rico_traces.py",
    "src/req2web_faults/evaluator_gold.py",
    "tests/test_fault_gold_manifest.py",
)
_EXPLICIT_EXCLUDED_PREFIX_RULES = (
    ("data/h1/", _REASON_H1_GOLD_EVALUATOR),
    ("data/gold/", _REASON_H1_GOLD_EVALUATOR),
    ("data/evaluator_only/", _REASON_H1_GOLD_EVALUATOR),
    ("data/evaluator-only/", _REASON_H1_GOLD_EVALUATOR),
    ("data/reference_only/", _REASON_REFERENCE_ONLY),
    ("data/reference-only/", _REASON_REFERENCE_ONLY),
    ("assets/reference_only/", _REASON_REFERENCE_ONLY),
    ("assets/reference-only/", _REASON_REFERENCE_ONLY),
)
_RICO_MATERIAL_ROOT = "data/processed/"
_RICO_MATERIAL_COMPONENT_NAMES = ("rico",)
_RICO_MATERIAL_COMPONENT_PREFIXES = ("rico_", "rico-")

_EXACT_COMPONENT_RULES = (
    (".env", _REASON_CREDENTIAL_SECRET),
    (".ssh", _REASON_CREDENTIAL_SECRET),
    ("credential", _REASON_CREDENTIAL_SECRET),
    ("credentials", _REASON_CREDENTIAL_SECRET),
    ("secret", _REASON_CREDENTIAL_SECRET),
    ("secrets", _REASON_CREDENTIAL_SECRET),
    ("token", _REASON_CREDENTIAL_SECRET),
    ("tokens", _REASON_CREDENTIAL_SECRET),
    ("cookie", _REASON_CREDENTIAL_SECRET),
    ("cookies", _REASON_CREDENTIAL_SECRET),
    ("weights", _REASON_MODEL_WEIGHT),
    ("model_weights", _REASON_MODEL_WEIGHT),
    ("models", _REASON_MODEL_WEIGHT),
    ("__pycache__", _REASON_GENERATED_CACHE),
    (".cache", _REASON_GENERATED_CACHE),
    ("cache", _REASON_GENERATED_CACHE),
    ("caches", _REASON_GENERATED_CACHE),
    ("build", _REASON_GENERATED_CACHE),
    ("dist", _REASON_GENERATED_CACHE),
    ("node_modules", _REASON_GENERATED_CACHE),
    ("output", _REASON_GENERATED_CACHE),
    ("outputs", _REASON_GENERATED_CACHE),
)
_SUFFIX_RULES = (
    (".env", _REASON_CREDENTIAL_SECRET),
    (".pem", _REASON_CREDENTIAL_SECRET),
    (".key", _REASON_CREDENTIAL_SECRET),
    (".p12", _REASON_CREDENTIAL_SECRET),
    (".pfx", _REASON_CREDENTIAL_SECRET),
    (".safetensors", _REASON_MODEL_WEIGHT),
    (".ckpt", _REASON_MODEL_WEIGHT),
    (".pt", _REASON_MODEL_WEIGHT),
    (".pth", _REASON_MODEL_WEIGHT),
    (".onnx", _REASON_MODEL_WEIGHT),
)
_BASENAME_RULES = (
    ("id_rsa", _REASON_CREDENTIAL_SECRET),
    ("id_dsa", _REASON_CREDENTIAL_SECRET),
    ("id_ecdsa", _REASON_CREDENTIAL_SECRET),
    ("id_ed25519", _REASON_CREDENTIAL_SECRET),
    ("known_hosts", _REASON_CREDENTIAL_SECRET),
    ("authorized_keys", _REASON_CREDENTIAL_SECRET),
)
_AMBIGUOUS_TOKEN_SEQUENCE_RULES = (
    (("rico",), _AMBIGUOUS_RICO),
    (("h1",), _AMBIGUOUS_H1_GOLD_EVALUATOR),
    (("gold",), _AMBIGUOUS_H1_GOLD_EVALUATOR),
    (("evaluator",), _AMBIGUOUS_H1_GOLD_EVALUATOR),
    (("reference", "only"), _AMBIGUOUS_REFERENCE_ONLY),
    (("credential",), _AMBIGUOUS_CREDENTIAL_SECRET),
    (("credentials",), _AMBIGUOUS_CREDENTIAL_SECRET),
    (("secret",), _AMBIGUOUS_CREDENTIAL_SECRET),
    (("secrets",), _AMBIGUOUS_CREDENTIAL_SECRET),
    (("token",), _AMBIGUOUS_CREDENTIAL_SECRET),
    (("tokens",), _AMBIGUOUS_CREDENTIAL_SECRET),
    (("cookie",), _AMBIGUOUS_CREDENTIAL_SECRET),
    (("cookies",), _AMBIGUOUS_CREDENTIAL_SECRET),
    (("weight",), _AMBIGUOUS_MODEL_WEIGHT),
    (("weights",), _AMBIGUOUS_MODEL_WEIGHT),
    (("cache",), _AMBIGUOUS_GENERATED_CACHE),
    (("caches",), _AMBIGUOUS_GENERATED_CACHE),
)
_POLICY_REASON_CODES = tuple(sorted({
    reason
    for _, reason in (
        *_EXPLICIT_EXCLUDED_PREFIX_RULES,
        *_EXACT_COMPONENT_RULES,
        *_SUFFIX_RULES,
        *_BASENAME_RULES,
    )
} | {_REASON_RESTRICTED_RICO}))
_AMBIGUOUS_REASON_CODES = tuple(sorted({
    reason for _, reason in _AMBIGUOUS_TOKEN_SEQUENCE_RULES
}))
_POLICY_DESCRIPTOR = {
    "policy_id": EXCLUSION_POLICY_ID,
    "working_tree_excluded_by_construction": ["untracked", "ignored"],
    "working_tree_rejected": ["unstaged", "staged"],
    "git_objects_rejected": ["symlink", "submodule", "special_mode"],
    "classification_states": [
        _DECISION_INCLUDE,
        _DECISION_EXCLUDE,
        _DECISION_AMBIGUOUS,
    ],
    "classification_order": [
        "explicit_safe_exact_path",
        "explicit_excluded_material_path",
        "automatic_component_suffix_basename_exclusion",
        "ambiguous_sensitive_token_fail_closed",
        "ordinary_include",
    ],
    "explicit_safe_paths": list(_EXPLICIT_SAFE_PATHS),
    "explicit_excluded_prefix_rules": [
        {"prefix": prefix, "reason": reason}
        for prefix, reason in _EXPLICIT_EXCLUDED_PREFIX_RULES
    ],
    "explicit_excluded_patterns": [{
        "pattern_id": "data_processed_rico_material_v1",
        "root": _RICO_MATERIAL_ROOT,
        "first_relative_component_names": list(_RICO_MATERIAL_COMPONENT_NAMES),
        "first_relative_component_prefixes": list(_RICO_MATERIAL_COMPONENT_PREFIXES),
        "reason": _REASON_RESTRICTED_RICO,
    }],
    "automatic_exact_component_rules": [
        {"component": component, "reason": reason}
        for component, reason in _EXACT_COMPONENT_RULES
    ],
    "automatic_suffix_rules": [
        {"suffix": suffix, "reason": reason}
        for suffix, reason in _SUFFIX_RULES
    ],
    "automatic_basename_rules": [
        {"basename": basename, "reason": reason}
        for basename, reason in _BASENAME_RULES
    ],
    "tokenizer": "lowercase_ascii_split_on_non_alphanumeric_v1",
    "ambiguous_token_sequence_rules": [
        {"tokens": list(tokens), "reason": reason}
        for tokens, reason in _AMBIGUOUS_TOKEN_SEQUENCE_RULES
    ],
    "ambiguous_behavior": "fail_closed_before_manifest",
    "excluded_reason_codes": list(_POLICY_REASON_CODES),
    "ambiguous_reason_codes": list(_AMBIGUOUS_REASON_CODES),
}


class RepositoryArchiveError(ValueError):
    """Repository archive contract failed closed."""


def _reject_duplicate_pairs(
    pairs: list[tuple[str, object]],
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _error_type("duplicate_json_key")
        result[key] = value
    return result


@dataclass(frozen=True)
class _Authority:
    json_dumps: Any
    json_loads: Any
    sha256_bytes: Any


def _make_authority(
    json_dumps_fn: Any = json.dumps,
    json_loads_fn: Any = json.loads,
    sha256_factory: Any = hashlib.sha256,
    reject_pairs: Any = _reject_duplicate_pairs,
    json_error_type: type[Exception] = json.JSONDecodeError,
    error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> _Authority:
    def _dumps(value: object) -> bytes:
        return json_dumps_fn(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")

    def _loads(raw: bytes | str) -> object:
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        try:
            return json_loads_fn(raw.decode("utf-8"), object_pairs_hook=reject_pairs)
        except UnicodeDecodeError as exc:
            raise error_type("manifest_not_utf8") from exc
        except json_error_type as exc:
            raise error_type("manifest_not_json") from exc

    def _sha(data: bytes) -> str:
        return sha256_factory(data).hexdigest()

    return _Authority(_dumps, _loads, _sha)


_AUTHORITY = _make_authority()
_POLICY_SHA256 = _AUTHORITY.sha256_bytes(_AUTHORITY.json_dumps(_POLICY_DESCRIPTOR))


def _deepcopy_json(value: object, _deepcopy: Any = copy.deepcopy) -> object:
    return _deepcopy(value)


def _expect_keys(
    value: Mapping[str, object],
    keys: Sequence[str],
    label: str,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> None:
    # Canonical JSON uses sorted object keys, whereas in-memory builders retain
    # declaration order. Validate the exact key set here; byte parsers enforce
    # canonical ordering by comparing the original bytes after validation.
    if len(value) != len(keys) or set(value) != set(keys):
        raise _error_type(f"{label}_keys_invalid")


def _expect_str(
    value: object,
    label: str,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> str:
    if not isinstance(value, str) or not value:
        raise _error_type(f"{label}_not_string")
    return value


def _expect_int(
    value: object,
    label: str,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _error_type(f"{label}_not_nonnegative_int")
    return value


def _validate_hex(
    value: object,
    label: str,
    pattern: re.Pattern[str],
    _expect_str_fn: Any = _expect_str,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> str:
    text = _expect_str_fn(value, label)
    if not pattern.fullmatch(text):
        raise _error_type(f"{label}_not_expected_hex")
    return text


def _validate_posix_relative(
    path: object,
    _expect_str_fn: Any = _expect_str,
    _pure_posix_path: Any = PurePosixPath,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> str:
    text = _expect_str_fn(path, "path")
    if text.startswith("/") or "\\" in text or text in {".", ".."}:
        raise _error_type("path_not_posix_relative")
    posix = _pure_posix_path(text)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise _error_type("path_escapes_or_is_not_normal")
    if str(posix) != text:
        raise _error_type("path_not_canonical")
    return text


def _contains_token_sequence(tokens: tuple[str, ...], sequence: tuple[str, ...]) -> bool:
    width = len(sequence)
    return any(tokens[index:index + width] == sequence for index in range(len(tokens) - width + 1))


def _classify_path(
    path: str,
    _pure_posix_path: Any = PurePosixPath,
    _token_splitter: re.Pattern[str] = re.compile(r"[^a-z0-9]+"),
    _explicit_safe_paths: frozenset[str] = frozenset(_EXPLICIT_SAFE_PATHS),
    _explicit_excluded_prefix_rules: tuple[tuple[str, str], ...] = _EXPLICIT_EXCLUDED_PREFIX_RULES,
    _rico_material_root: str = _RICO_MATERIAL_ROOT,
    _rico_component_names: tuple[str, ...] = _RICO_MATERIAL_COMPONENT_NAMES,
    _rico_component_prefixes: tuple[str, ...] = _RICO_MATERIAL_COMPONENT_PREFIXES,
    _rico_reason: str = _REASON_RESTRICTED_RICO,
    _exact_component_rules: tuple[tuple[str, str], ...] = _EXACT_COMPONENT_RULES,
    _suffix_rules: tuple[tuple[str, str], ...] = _SUFFIX_RULES,
    _basename_rules: tuple[tuple[str, str], ...] = _BASENAME_RULES,
    _ambiguous_token_rules: tuple[tuple[tuple[str, ...], str], ...] = _AMBIGUOUS_TOKEN_SEQUENCE_RULES,
    _contains_sequence_fn: Any = _contains_token_sequence,
    _include_decision: str = _DECISION_INCLUDE,
    _exclude_decision: str = _DECISION_EXCLUDE,
    _ambiguous_decision: str = _DECISION_AMBIGUOUS,
) -> tuple[str, str]:
    lowered = path.lower()
    components = tuple(part.lower() for part in _pure_posix_path(path).parts)
    if lowered in _explicit_safe_paths:
        return _include_decision, "explicit_safe_path"

    if lowered.startswith(_rico_material_root):
        relative = lowered[len(_rico_material_root):]
        first_component = relative.split("/", 1)[0]
        if first_component in _rico_component_names or first_component.startswith(_rico_component_prefixes):
            return _exclude_decision, _rico_reason
    for prefix, reason in _explicit_excluded_prefix_rules:
        if lowered.startswith(prefix):
            return _exclude_decision, reason

    basename = components[-1]
    basename_rule_map = dict(_basename_rules)
    if basename in basename_rule_map:
        return _exclude_decision, basename_rule_map[basename]
    for suffix, reason in _suffix_rules:
        if lowered.endswith(suffix):
            return _exclude_decision, reason
    exact_component_map = dict(_exact_component_rules)
    for component in components:
        if component in exact_component_map:
            return _exclude_decision, exact_component_map[component]

    for component in components:
        tokens = tuple(token for token in _token_splitter.split(component) if token)
        for sequence, reason in _ambiguous_token_rules:
            if _contains_sequence_fn(tokens, sequence):
                return _ambiguous_decision, reason
    return _include_decision, "ordinary_project_path"

def _validate_file_row(
    row: object,
    _expect_keys_fn: Any = _expect_keys,
    _validate_posix_fn: Any = _validate_posix_relative,
    _expect_str_fn: Any = _expect_str,
    _validate_hex_fn: Any = _validate_hex,
    _expect_int_fn: Any = _expect_int,
    _classify_path_fn: Any = _classify_path,
    _mapping_type: Any = Mapping,
    _file_keys: tuple[str, ...] = _FILE_KEYS,
    _allowed_modes: frozenset[str] = frozenset(_ALLOWED_FILE_MODES),
    _hex40: re.Pattern[str] = _HEX40,
    _hex64: re.Pattern[str] = _HEX64,
    _include_decision: str = _DECISION_INCLUDE,
    _exclude_decision: str = _DECISION_EXCLUDE,
    _ambiguous_decision: str = _DECISION_AMBIGUOUS,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> dict[str, object]:
    if not isinstance(row, _mapping_type):
        raise _error_type("file_row_not_mapping")
    _expect_keys_fn(row, _file_keys, "file_row")
    path = _validate_posix_fn(row["path"])
    git_mode = _expect_str_fn(row["git_mode"], "git_mode")
    if git_mode not in _allowed_modes:
        raise _error_type("git_mode_not_regular_file")
    blob_sha = _validate_hex_fn(row["blob_sha"], "blob_sha", _hex40)
    byte_length = _expect_int_fn(row["byte_length"], "file_byte_length")
    sha256 = _validate_hex_fn(row["sha256"], "file_sha256", _hex64)
    decision, detail = _classify_path_fn(path)
    if decision == _exclude_decision:
        raise _error_type("included_path_matches_exclusion_policy")
    if decision == _ambiguous_decision:
        raise _error_type(f"included_path_ambiguous_sensitive:{detail}")
    if decision != _include_decision:
        raise _error_type("included_path_decision_invalid")
    return {
        "path": path,
        "git_mode": git_mode,
        "blob_sha": blob_sha,
        "byte_length": byte_length,
        "sha256": sha256,
    }


def _validate_excluded_file_row(
    row: object,
    _expect_keys_fn: Any = _expect_keys,
    _validate_posix_fn: Any = _validate_posix_relative,
    _expect_str_fn: Any = _expect_str,
    _validate_hex_fn: Any = _validate_hex,
    _classify_path_fn: Any = _classify_path,
    _mapping_type: Any = Mapping,
    _excluded_file_keys: tuple[str, ...] = _EXCLUDED_FILE_KEYS,
    _allowed_modes: frozenset[str] = frozenset(_ALLOWED_FILE_MODES),
    _allowed_reasons: tuple[str, ...] = _POLICY_REASON_CODES,
    _hex40: re.Pattern[str] = _HEX40,
    _exclude_decision: str = _DECISION_EXCLUDE,
    _ambiguous_decision: str = _DECISION_AMBIGUOUS,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> dict[str, object]:
    if not isinstance(row, _mapping_type):
        raise _error_type("excluded_file_row_not_mapping")
    _expect_keys_fn(row, _excluded_file_keys, "excluded_file_row")
    path = _validate_posix_fn(row["path"])
    git_mode = _expect_str_fn(row["git_mode"], "excluded_git_mode")
    if git_mode not in _allowed_modes:
        raise _error_type("excluded_git_mode_not_regular_file")
    blob_sha = _validate_hex_fn(row["blob_sha"], "excluded_blob_sha", _hex40)
    reason = _expect_str_fn(row["reason"], "excluded_reason")
    if reason not in _allowed_reasons:
        raise _error_type("excluded_reason_not_allowed")
    decision, canonical_reason = _classify_path_fn(path)
    if decision == _ambiguous_decision:
        raise _error_type(f"excluded_path_ambiguous_sensitive:{canonical_reason}")
    if decision != _exclude_decision:
        raise _error_type("excluded_path_not_policy_excluded")
    if canonical_reason != reason:
        raise _error_type("excluded_reason_not_canonical_for_path")
    return {
        "path": path,
        "git_mode": git_mode,
        "blob_sha": blob_sha,
        "reason": reason,
    }


def _tracked_identity(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "path": row["path"],
        "git_mode": row["git_mode"],
        "blob_sha": row["blob_sha"],
    }


def _inventory_identities(
    files: Sequence[Mapping[str, object]],
    excluded_files: Sequence[Mapping[str, object]],
    *,
    authority: _Authority,
    _tracked_identity_fn: Any = _tracked_identity,
) -> tuple[str, str]:
    included_identity = [_tracked_identity_fn(row) for row in files]
    excluded_identity = [_tracked_identity_fn(row) for row in excluded_files]
    tracked_rows = sorted(
        [*included_identity, *excluded_identity],
        key=lambda row: str(row["path"]),
    )
    tracked_inventory_sha256 = authority.sha256_bytes(
        authority.json_dumps({"tracked_rows": tracked_rows})
    )
    coverage_sha256 = authority.sha256_bytes(authority.json_dumps({
        "included": included_identity,
        "excluded": [dict(row) for row in excluded_files],
    }))
    return tracked_inventory_sha256, coverage_sha256

def _validate_manifest_dict(
    data: object,
    *,
    authority: _Authority,
    _mapping_type: Any = Mapping,
    _expect_keys_fn: Any = _expect_keys,
    _validate_hex_fn: Any = _validate_hex,
    _expect_int_fn: Any = _expect_int,
    _validate_file_row_fn: Any = _validate_file_row,
    _validate_excluded_file_row_fn: Any = _validate_excluded_file_row,
    _inventory_identities_fn: Any = _inventory_identities,
    _manifest_keys: tuple[str, ...] = _MANIFEST_KEYS,
    _repository_keys: tuple[str, ...] = _REPOSITORY_KEYS,
    _source_tree_keys: tuple[str, ...] = _SOURCE_TREE_KEYS,
    _builder_keys: tuple[str, ...] = _BUILDER_KEYS,
    _archive_keys: tuple[str, ...] = _ARCHIVE_KEYS,
    _exclusion_keys: tuple[str, ...] = _EXCLUSION_KEYS,
    _schema_version: str = SCHEMA_VERSION,
    _foundation_status: str = FOUNDATION_STATUS,
    _builder_id: str = BUILDER_ID,
    _archive_format: str = ARCHIVE_FORMAT,
    _archive_filename: str = ARCHIVE_FILENAME,
    _exclusion_policy_id: str = EXCLUSION_POLICY_ID,
    _policy_sha256: str = _POLICY_SHA256,
    _expected_reason_codes: tuple[str, ...] = _POLICY_REASON_CODES,
    _hex40: re.Pattern[str] = _HEX40,
    _hex64: re.Pattern[str] = _HEX64,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> dict[str, object]:
    if not isinstance(data, _mapping_type):
        raise _error_type("manifest_not_mapping")
    _expect_keys_fn(data, _manifest_keys, "manifest")
    if data["schema_version"] != _schema_version:
        raise _error_type("schema_version_mismatch")
    if data["foundation_status"] != _foundation_status:
        raise _error_type("foundation_status_mismatch")
    manifest_id = _validate_hex_fn(data["manifest_id"], "manifest_id", _hex64)
    commit_sha = _validate_hex_fn(data["commit_sha"], "commit_sha", _hex40)
    tree_sha = _validate_hex_fn(data["tree_sha"], "tree_sha", _hex40)

    repository = data["repository"]
    if not isinstance(repository, _mapping_type):
        raise _error_type("repository_not_mapping")
    _expect_keys_fn(repository, _repository_keys, "repository")
    if repository["identity"] != "pushed_git_tracked_tree_policy_filtered_archive":
        raise _error_type("repository_identity_mismatch")
    if repository["head_equals_origin_main"] is not True:
        raise _error_type("head_origin_main_not_bound")
    if repository["tracked_state_clean"] is not True:
        raise _error_type("tracked_state_not_clean")

    source_tree = data["source_tree"]
    if not isinstance(source_tree, _mapping_type):
        raise _error_type("source_tree_not_mapping")
    _expect_keys_fn(source_tree, _source_tree_keys, "source_tree")
    tracked_file_count = _expect_int_fn(
        source_tree["tracked_file_count"], "tracked_file_count"
    )
    included_file_count = _expect_int_fn(
        source_tree["included_file_count"], "included_file_count"
    )
    excluded_file_count = _expect_int_fn(
        source_tree["excluded_file_count"], "excluded_file_count"
    )
    tracked_inventory_sha256 = _validate_hex_fn(
        source_tree["tracked_inventory_sha256"], "tracked_inventory_sha256", _hex64
    )
    coverage_sha256 = _validate_hex_fn(
        source_tree["coverage_sha256"], "coverage_sha256", _hex64
    )

    builder = data["builder"]
    if not isinstance(builder, _mapping_type):
        raise _error_type("builder_not_mapping")
    _expect_keys_fn(builder, _builder_keys, "builder")
    if builder != {
        "builder_id": _builder_id,
        "builder_version": "1",
        "archive_format": _archive_format,
    }:
        raise _error_type("builder_identity_mismatch")

    archive = data["archive"]
    if not isinstance(archive, _mapping_type):
        raise _error_type("archive_not_mapping")
    _expect_keys_fn(archive, _archive_keys, "archive")
    if archive["format"] != _archive_format or archive["filename"] != _archive_filename:
        raise _error_type("archive_identity_mismatch")
    archive_length = _expect_int_fn(archive["byte_length"], "archive_byte_length")
    archive_sha = _validate_hex_fn(archive["sha256"], "archive_sha256", _hex64)

    policy = data["exclusion_policy"]
    if not isinstance(policy, _mapping_type):
        raise _error_type("exclusion_policy_not_mapping")
    _expect_keys_fn(policy, _exclusion_keys, "exclusion_policy")
    if policy["policy_id"] != _exclusion_policy_id or policy["policy_sha256"] != _policy_sha256:
        raise _error_type("exclusion_policy_identity_mismatch")
    reason_codes = policy["reason_codes"]
    expected_reason_codes = list(_expected_reason_codes)
    if not isinstance(reason_codes, list) or reason_codes != expected_reason_codes:
        raise _error_type("exclusion_policy_reason_codes_mismatch")

    files = data["files"]
    if not isinstance(files, list):
        raise _error_type("files_not_list")
    validated_files = [_validate_file_row_fn(row) for row in files]
    included_paths = [row["path"] for row in validated_files]
    if included_paths != sorted(included_paths):
        raise _error_type("files_not_ordered")
    if len(included_paths) != len(set(included_paths)):
        raise _error_type("duplicate_manifest_path")

    excluded_files = data["excluded_files"]
    if not isinstance(excluded_files, list):
        raise _error_type("excluded_files_not_list")
    validated_excluded_files = [
        _validate_excluded_file_row_fn(row) for row in excluded_files
    ]
    excluded_paths = [row["path"] for row in validated_excluded_files]
    if excluded_paths != sorted(excluded_paths):
        raise _error_type("excluded_files_not_ordered")
    if len(excluded_paths) != len(set(excluded_paths)):
        raise _error_type("duplicate_excluded_path")
    if set(included_paths) & set(excluded_paths):
        raise _error_type("included_excluded_path_overlap")

    if included_file_count != len(validated_files):
        raise _error_type("included_file_count_mismatch")
    if excluded_file_count != len(validated_excluded_files):
        raise _error_type("excluded_file_count_mismatch")
    if tracked_file_count != included_file_count + excluded_file_count:
        raise _error_type("tracked_file_count_mismatch")
    expected_tracked_hash, expected_coverage_hash = _inventory_identities_fn(
        validated_files,
        validated_excluded_files,
        authority=authority,
    )
    if tracked_inventory_sha256 != expected_tracked_hash:
        raise _error_type("tracked_inventory_sha256_mismatch")
    if coverage_sha256 != expected_coverage_hash:
        raise _error_type("coverage_sha256_mismatch")

    body = {
        "schema_version": _schema_version,
        "manifest_id": "0" * 64,
        "foundation_status": _foundation_status,
        "repository": {
            "identity": "pushed_git_tracked_tree_policy_filtered_archive",
            "head_equals_origin_main": True,
            "tracked_state_clean": True,
        },
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
        "source_tree": {
            "tracked_file_count": tracked_file_count,
            "included_file_count": included_file_count,
            "excluded_file_count": excluded_file_count,
            "tracked_inventory_sha256": tracked_inventory_sha256,
            "coverage_sha256": coverage_sha256,
        },
        "builder": {
            "builder_id": _builder_id,
            "builder_version": "1",
            "archive_format": _archive_format,
        },
        "archive": {
            "format": _archive_format,
            "filename": _archive_filename,
            "byte_length": archive_length,
            "sha256": archive_sha,
        },
        "exclusion_policy": {
            "policy_id": _exclusion_policy_id,
            "policy_sha256": _policy_sha256,
            "reason_codes": expected_reason_codes,
        },
        "files": validated_files,
        "excluded_files": validated_excluded_files,
    }
    expected_id = authority.sha256_bytes(authority.json_dumps(body))
    if manifest_id != expected_id:
        raise _error_type("manifest_id_mismatch")
    body["manifest_id"] = manifest_id
    return body

def _make_manifest_type(
    authority: _Authority,
    *,
    validate_manifest: Any,
    copy_json: Any,
    validate_zip: Any,
    mapping_type: Any = Mapping,
    mapping_proxy_type: Any = MappingProxyType,
    error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
):
    """Bind every public manifest route to definition-time trusted helpers."""
    manifest_type: type | None = None

    def _construct(data: Mapping[str, object]):
        if manifest_type is None:  # pragma: no cover - factory construction invariant
            raise error_type("manifest_type_not_bound")
        return manifest_type(data)

    class RepositoryArchiveManifest:
        """Canonical manifest for a D17 tracked-tree coverage and filtered archive artifact.

        Parsed bytes are replay data only; callers cannot inject a different
        JSON, hashing, validation, or archive authority through a public route.
        """

        __slots__ = ("_data",)

        def __init__(self, data: Mapping[str, object]) -> None:
            validated = validate_manifest(data, authority=authority)
            object.__setattr__(self, "_data", mapping_proxy_type(validated))

        @staticmethod
        def from_dict(data: Mapping[str, object]):
            return _construct(data)

        @staticmethod
        def from_bytes(raw: bytes):
            if not isinstance(raw, bytes):
                raise error_type("manifest_bytes_required")
            parsed = authority.json_loads(raw)
            manifest = _construct(parsed)
            canonical = authority.json_dumps(validate_manifest(dict(manifest._data), authority=authority))
            if canonical != raw:
                raise error_type("manifest_not_canonical")
            return manifest

        def _validated_data(self) -> dict[str, object]:
            return validate_manifest(dict(self._data), authority=authority)

        def to_dict(self) -> dict[str, object]:
            return copy_json(self._validated_data())  # type: ignore[return-value]

        def canonical_bytes(self) -> bytes:
            return authority.json_dumps(self._validated_data())

        def sha256(self) -> str:
            canonical = authority.json_dumps(self._validated_data())
            return authority.sha256_bytes(canonical)

        def validate_archive_bytes(self, archive_bytes: bytes) -> None:
            if not isinstance(archive_bytes, bytes):
                raise error_type("archive_bytes_required")
            manifest = self._validated_data()
            archive_info = manifest["archive"]
            if not isinstance(archive_info, mapping_type):
                raise error_type("archive_not_mapping")
            if len(archive_bytes) != archive_info["byte_length"]:
                raise error_type("archive_byte_length_drift")
            if authority.sha256_bytes(archive_bytes) != archive_info["sha256"]:
                raise error_type("archive_sha256_drift")
            validate_zip(archive_bytes, manifest, authority=authority)

    manifest_type = RepositoryArchiveManifest
    return RepositoryArchiveManifest

def _run_git(
    repo: Path,
    args: Sequence[str],
    _run: Any = subprocess.run,
    _pipe: Any = subprocess.PIPE,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> str:
    completed = _run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        stdout=_pipe,
        stderr=_pipe,
        check=False,
    )
    if completed.returncode != 0:
        raise _error_type(f"git_failed:{args[0]}:{completed.stderr.strip()}")
    return completed.stdout.strip()


def _git_root(
    source: Path,
    _run_git_fn: Any = _run_git,
    _path_type: Any = Path,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> Path:
    root = _run_git_fn(source, ["rev-parse", "--show-toplevel"])
    if not root:
        raise _error_type("not_git_repository")
    return _path_type(root).resolve()


def _validate_git_state(
    repo: Path,
    expected_action_time_sha: str,
    _hex40: re.Pattern[str] = _HEX40,
    _run_git_fn: Any = _run_git,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> tuple[str, str]:
    if not _hex40.fullmatch(expected_action_time_sha):
        raise _error_type("expected_action_time_sha_not_40_hex")
    inside = _run_git_fn(repo, ["rev-parse", "--is-inside-work-tree"])
    if inside != "true":
        raise _error_type("source_not_work_tree")
    head = _run_git_fn(repo, ["rev-parse", "HEAD"])
    if head != expected_action_time_sha:
        raise _error_type("head_not_expected_action_time_sha")
    origin_main = _run_git_fn(repo, ["rev-parse", "--verify", "refs/remotes/origin/main"])
    if origin_main != head:
        raise _error_type("head_not_equal_local_origin_main")
    status = _run_git_fn(repo, ["status", "--porcelain=v1", "--untracked-files=no"])
    if status:
        raise _error_type("tracked_or_index_state_not_clean")
    tree = _run_git_fn(repo, ["rev-parse", f"{head}^{{tree}}"])
    return head, tree


def _ls_tree_rows(
    repo: Path,
    commit_sha: str,
    *,
    authority: _Authority,
    _run: Any = subprocess.run,
    _pipe: Any = subprocess.PIPE,
    _validate_posix_fn: Any = _validate_posix_relative,
    _classify_path_fn: Any = _classify_path,
    _allowed_modes: frozenset[str] = frozenset(_ALLOWED_FILE_MODES),
    _hex40: re.Pattern[str] = _HEX40,
    _include_decision: str = _DECISION_INCLUDE,
    _exclude_decision: str = _DECISION_EXCLUDE,
    _ambiguous_decision: str = _DECISION_AMBIGUOUS,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    raw = _run(
        ["git", "ls-tree", "-r", "-z", "--full-tree", commit_sha],
        cwd=str(repo),
        stdout=_pipe,
        stderr=_pipe,
        check=False,
    )
    if raw.returncode != 0:
        raise _error_type(f"git_failed:ls-tree:{raw.stderr.decode('utf-8', 'replace').strip()}")
    entries = raw.stdout.split(b"\0")
    included_rows: list[dict[str, object]] = []
    excluded_rows: list[dict[str, object]] = []
    for entry in entries:
        if not entry:
            continue
        try:
            header, path_bytes = entry.split(b"\t", 1)
            mode_b, kind_b, blob_b = header.split(b" ", 2)
        except ValueError as exc:
            raise _error_type("ls_tree_entry_not_parseable") from exc
        mode = mode_b.decode("ascii")
        kind = kind_b.decode("ascii")
        path = _validate_posix_fn(path_bytes.decode("utf-8"))
        if kind != "blob" or mode not in _allowed_modes:
            raise _error_type("git_tree_contains_non_regular_file")
        blob_sha = blob_b.decode("ascii")
        if not _hex40.fullmatch(blob_sha):
            raise _error_type("ls_tree_blob_sha_invalid")
        decision, detail = _classify_path_fn(path)
        if decision == _ambiguous_decision:
            raise _error_type(f"ambiguous_sensitive_path:{detail}:{path}")
        if decision == _exclude_decision:
            excluded_rows.append({
                "path": path,
                "git_mode": mode,
                "blob_sha": blob_sha,
                "reason": detail,
            })
            # Excluded content is deliberately never passed to git cat-file.
            continue
        if decision != _include_decision:
            raise _error_type("path_policy_decision_invalid")
        data = _run(
            ["git", "cat-file", "blob", blob_sha],
            cwd=str(repo),
            stdout=_pipe,
            stderr=_pipe,
            check=False,
        )
        if data.returncode != 0:
            raise _error_type(
                f"git_failed:cat-file:{data.stderr.decode('utf-8', 'replace').strip()}"
            )
        included_rows.append({
            "path": path,
            "git_mode": mode,
            "blob_sha": blob_sha,
            "byte_length": len(data.stdout),
            "sha256": authority.sha256_bytes(data.stdout),
        })
    included_rows.sort(key=lambda row: row["path"])
    excluded_rows.sort(key=lambda row: row["path"])
    all_paths = [row["path"] for row in included_rows] + [
        row["path"] for row in excluded_rows
    ]
    if len(set(all_paths)) != len(all_paths):
        raise _error_type("duplicate_git_tree_path")
    return included_rows, excluded_rows

def _make_archive(
    files: Sequence[Mapping[str, object]],
    repo: Path,
    _bytes_io: Any = io.BytesIO,
    _zip_file: Any = zipfile.ZipFile,
    _zip_info: Any = zipfile.ZipInfo,
    _zip_deflated: int = zipfile.ZIP_DEFLATED,
    _run: Any = subprocess.run,
    _pipe: Any = subprocess.PIPE,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> bytes:
    output = _bytes_io()
    with _zip_file(output, "w", compression=_zip_deflated, compresslevel=9) as zf:
        for row in files:
            path = str(row["path"])
            blob_sha = str(row["blob_sha"])
            mode = str(row["git_mode"])
            data = _run(
                ["git", "cat-file", "blob", blob_sha],
                cwd=str(repo),
                stdout=_pipe,
                stderr=_pipe,
                check=False,
            )
            if data.returncode != 0:
                raise _error_type("git_failed:cat-file-archive")
            info = _zip_info(filename=path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = _zip_deflated
            info.external_attr = (int(mode, 8) & 0xFFFF) << 16
            zf.writestr(info, data.stdout)
    return output.getvalue()


def _manifest_body(
    commit_sha: str,
    tree_sha: str,
    files: Sequence[Mapping[str, object]],
    excluded_files: Sequence[Mapping[str, object]],
    archive_bytes: bytes,
    *,
    authority: _Authority,
    _inventory_identities_fn: Any = _inventory_identities,
    _schema_version: str = SCHEMA_VERSION,
    _foundation_status: str = FOUNDATION_STATUS,
    _builder_id: str = BUILDER_ID,
    _archive_format: str = ARCHIVE_FORMAT,
    _archive_filename: str = ARCHIVE_FILENAME,
    _exclusion_policy_id: str = EXCLUSION_POLICY_ID,
    _policy_sha256: str = _POLICY_SHA256,
    _reason_codes: tuple[str, ...] = _POLICY_REASON_CODES,
) -> dict[str, object]:
    tracked_inventory_sha256, coverage_sha256 = _inventory_identities_fn(
        files,
        excluded_files,
        authority=authority,
    )
    body = {
        "schema_version": _schema_version,
        "manifest_id": "0" * 64,
        "foundation_status": _foundation_status,
        "repository": {
            "identity": "pushed_git_tracked_tree_policy_filtered_archive",
            "head_equals_origin_main": True,
            "tracked_state_clean": True,
        },
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
        "source_tree": {
            "tracked_file_count": len(files) + len(excluded_files),
            "included_file_count": len(files),
            "excluded_file_count": len(excluded_files),
            "tracked_inventory_sha256": tracked_inventory_sha256,
            "coverage_sha256": coverage_sha256,
        },
        "builder": {
            "builder_id": _builder_id,
            "builder_version": "1",
            "archive_format": _archive_format,
        },
        "archive": {
            "format": _archive_format,
            "filename": _archive_filename,
            "byte_length": len(archive_bytes),
            "sha256": authority.sha256_bytes(archive_bytes),
        },
        "exclusion_policy": {
            "policy_id": _exclusion_policy_id,
            "policy_sha256": _policy_sha256,
            "reason_codes": list(_reason_codes),
        },
        "files": [dict(row) for row in files],
        "excluded_files": [dict(row) for row in excluded_files],
    }
    body["manifest_id"] = authority.sha256_bytes(authority.json_dumps(body))
    return body

def _validate_output_directory(
    output_dir: Path,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> None:
    if output_dir.is_symlink():
        raise _error_type("output_directory_symlink_rejected")
    if not output_dir.exists() or not output_dir.is_dir():
        raise _error_type("output_directory_must_preexist")
    if any(output_dir.iterdir()):
        raise _error_type("output_directory_not_empty")


def _make_public_api(
    *,
    authority: _Authority,
    manifest_type: Any,
    git_root_fn: Any,
    validate_git_state_fn: Any,
    ls_tree_rows_fn: Any,
    make_archive_fn: Any,
    manifest_body_fn: Any,
    validate_output_directory_fn: Any,
    path_type: Any,
    os_replace: Any,
    archive_filename: str,
    manifest_filename: str,
    error_type: type[RepositoryArchiveError],
):
    """Bind public actions to one definition-time trust bundle."""

    def build_repository_archive_manifest(
        source: str | os.PathLike[str],
        expected_action_time_sha: str,
    ):
        """Build a deterministic policy-filtered archive with exact tracked-tree coverage.

        This is local foundation work only. The function performs no upload and
        no network fetch; the local origin/main ref must already equal HEAD.
        """
        source_path = path_type(source)
        if not source_path.exists():
            raise error_type("source_missing")
        repo = git_root_fn(source_path)
        head, tree = validate_git_state_fn(repo, expected_action_time_sha)
        rows, excluded_rows = ls_tree_rows_fn(repo, head, authority=authority)
        archive_bytes = make_archive_fn(rows, repo)
        manifest = manifest_type.from_dict(
            manifest_body_fn(
                head,
                tree,
                rows,
                excluded_rows,
                archive_bytes,
                authority=authority,
            )
        )
        # Reparse both canonical manifest data and archive bytes before return.
        manifest = manifest_type.from_bytes(manifest.canonical_bytes())
        manifest.validate_archive_bytes(archive_bytes)
        return manifest, archive_bytes

    def write_repository_archive(
        source: str | os.PathLike[str],
        expected_action_time_sha: str,
        output_dir: str | os.PathLike[str],
    ):
        """Write the fixed archive and manifest into a dedicated empty directory."""
        out = path_type(output_dir)
        validate_output_directory_fn(out)
        archive_path = out / archive_filename
        manifest_path = out / manifest_filename
        tmp_archive = out / f".{archive_filename}.tmp"
        tmp_manifest = out / f".{manifest_filename}.tmp"
        try:
            manifest, archive_bytes = build_repository_archive_manifest(
                source, expected_action_time_sha
            )
            canonical_manifest = manifest.canonical_bytes()
            # Canonical bytes must round-trip before any filesystem action.
            manifest_type.from_bytes(canonical_manifest).validate_archive_bytes(archive_bytes)
            tmp_archive.write_bytes(archive_bytes)
            tmp_manifest.write_bytes(canonical_manifest)
            os_replace(tmp_archive, archive_path)
            os_replace(tmp_manifest, manifest_path)
            written_manifest_bytes = manifest_path.read_bytes()
            written_archive = archive_path.read_bytes()
            written_manifest = manifest_type.from_bytes(written_manifest_bytes)
            written_manifest.validate_archive_bytes(written_archive)
            if written_manifest.sha256() != manifest.sha256():
                raise error_type("written_manifest_identity_drift")
            return written_manifest
        except Exception:
            for item in (tmp_archive, tmp_manifest, archive_path, manifest_path):
                try:
                    if item.exists() or item.is_symlink():
                        item.unlink()
                except OSError:
                    pass
            raise

    def validate_archive_file(
        archive_path: str | os.PathLike[str],
        manifest_path: str | os.PathLike[str],
    ):
        manifest_bytes = path_type(manifest_path).read_bytes()
        archive_bytes = path_type(archive_path).read_bytes()
        manifest = manifest_type.from_bytes(manifest_bytes)
        manifest.validate_archive_bytes(archive_bytes)
        # Return a fresh instance reconstructed from the exact validated bytes.
        return manifest_type.from_bytes(manifest.canonical_bytes())

    return (
        build_repository_archive_manifest,
        write_repository_archive,
        validate_archive_file,
    )

def _zip_mode(
    info: zipfile.ZipInfo,
    _is_regular: Any = stat.S_ISREG,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> str:
    mode = (info.external_attr >> 16) & 0o777777
    if mode == 0:
        return "100644"
    if _is_regular(mode):
        file_bits = mode & 0o777
        if file_bits & 0o111:
            return "100755"
        return "100644"
    # Some synthetic tests set only the regular file mode bits as Git-style octal.
    if mode in (0o100644, 0o100755):
        return format(mode, "o")
    raise _error_type("archive_entry_mode_not_regular")


def _validate_zip_archive(
    archive_bytes: bytes,
    manifest: Mapping[str, object],
    *,
    authority: _Authority,
    _zip_file: Any = zipfile.ZipFile,
    _bad_zip_file: Any = zipfile.BadZipFile,
    _bytes_io: Any = io.BytesIO,
    _validate_posix_fn: Any = _validate_posix_relative,
    _zip_mode_fn: Any = _zip_mode,
    _mapping_type: Any = Mapping,
    _error_type: type[RepositoryArchiveError] = RepositoryArchiveError,
) -> None:
    try:
        zf = _zip_file(_bytes_io(archive_bytes), "r")
    except _bad_zip_file as exc:
        raise _error_type("archive_not_zip") from exc
    with zf:
        infos = zf.infolist()
        names = [info.filename for info in infos]
        for name in names:
            _validate_posix_fn(name)
        if len(names) != len(set(names)):
            raise _error_type("archive_duplicate_entry")
        expected_rows = manifest["files"]
        if not isinstance(expected_rows, list):
            raise _error_type("manifest_files_not_list")
        expected_by_path = {
            str(row["path"]): row
            for row in expected_rows
            if isinstance(row, _mapping_type)
        }
        if len(expected_by_path) != len(expected_rows):
            raise _error_type("manifest_file_row_not_mapping")
        if set(names) != set(expected_by_path):
            raise _error_type("archive_entry_set_mismatch")
        if names != sorted(names):
            raise _error_type("archive_entries_not_ordered")
        for info in infos:
            row = expected_by_path[info.filename]
            if _zip_mode_fn(info) != row["git_mode"]:
                raise _error_type("archive_entry_mode_drift")
            data = zf.read(info)
            if len(data) != row["byte_length"]:
                raise _error_type("archive_entry_byte_length_drift")
            if authority.sha256_bytes(data) != row["sha256"]:
                raise _error_type("archive_entry_sha256_drift")


# Freeze the public artifact and actions only after every trust-critical helper exists.
RepositoryArchiveManifest = _make_manifest_type(
    _AUTHORITY,
    validate_manifest=_validate_manifest_dict,
    copy_json=_deepcopy_json,
    validate_zip=_validate_zip_archive,
)
(
    build_repository_archive_manifest,
    write_repository_archive,
    validate_archive_file,
) = _make_public_api(
    authority=_AUTHORITY,
    manifest_type=RepositoryArchiveManifest,
    git_root_fn=_git_root,
    validate_git_state_fn=_validate_git_state,
    ls_tree_rows_fn=_ls_tree_rows,
    make_archive_fn=_make_archive,
    manifest_body_fn=_manifest_body,
    validate_output_directory_fn=_validate_output_directory,
    path_type=Path,
    os_replace=os.replace,
    archive_filename=ARCHIVE_FILENAME,
    manifest_filename=MANIFEST_FILENAME,
    error_type=RepositoryArchiveError,
)


__all__ = [
    "ARCHIVE_FILENAME",
    "FOUNDATION_STATUS",
    "MANIFEST_FILENAME",
    "RepositoryArchiveError",
    "RepositoryArchiveManifest",
    "build_repository_archive_manifest",
    "validate_archive_file",
    "write_repository_archive",
]
