"""Build the fixed two-case input bundle for the Qwen3.5-27B recovery pilot.

The bundle is local deterministic preparation.  It does not load a model,
contact a Provider, create a remote resource, or make a quality claim.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import shutil
from pathlib import Path
from typing import Mapping
from uuid import uuid4
import zipfile

from req2web_agent import (
    AGENT_BUNDLE_SCHEMA_VERSION,
    DeterministicRequirementProvider,
    MinimalAgentChain,
)
from req2web_generation import (
    RETRIEVAL_GUIDANCE_SCHEMA_VERSION,
    RetrievalGuidanceBuilder,
)
from req2web_provider.d17_audit import create_d17_path3_pre_invocation_audit_record
from req2web_provider.d17_input_view import select_d17_path3_provider_input
from req2web_provider.d17_manifest import D17Path3TierAManifest
from req2web_provider.d17_serializer import serialize_d17_path3_local_request
from req2web_provider.local_qwen_provider import prepare_local_qwen_provider_interface
from req2web_rag import RetrieverConfig, create_retriever

from .autodl_trusted_remote_executor import PROMPT_ARTIFACT_SCHEMA
from . import autodl_repository_archive as repository_archive


CASE_BUNDLE_SCHEMA = "req2web.runtime.qwen35_27b_case_bundle.v1"
CASE_BUNDLE_STATE = "prepared_local_no_model"
CASE_BUNDLE_MANIFEST = "case_bundle_manifest.json"
CASE_IDS = ("path3-commerce-checkout", "path3-media-analysis")
MODEL_REPOSITORY = "Qwen/Qwen3.5-27B"
MODEL_REVISION = "fc05daec18b0a78c049392ed2e771dde82bdf654"

_CASE_SET_PATH = "fixtures/stage3_path3_compatibility_cases_v1.json"
_TRACKED_RECORD_PATH = "fixtures/stage3_path3_external_action_gate_preparation_v1.json"
_RAG_INDEX_PATH = "data/processed/rag"
_HEX64 = frozenset("0123456789abcdef")


class Qwen27BCaseBundleError(ValueError):
    """Raised when fixed recovery-pilot case bytes or bindings drift."""


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


def _load_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Qwen27BCaseBundleError(f"{label}_invalid") from exc
    if type(value) is not dict:
        raise Qwen27BCaseBundleError(f"{label}_invalid")
    return value


def _hex64(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64 or any(ch not in _HEX64 for ch in value):
        raise Qwen27BCaseBundleError(f"{label}_invalid")
    return value


def _hex40(value: object, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 40
        or any(ch not in _HEX64 for ch in value)
    ):
        raise Qwen27BCaseBundleError(f"{label}_invalid")
    return value


def _binding(raw: bytes, *, identifier: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {"sha256": _sha(raw), "byte_length": len(raw)}
    if identifier is not None:
        result["id"] = identifier
    return result


def _context_guidance_bindings(context, guidance) -> dict[str, object]:
    context_raw = _canonical(context.to_dict())
    guidance_raw = _canonical(guidance.to_dict())
    context_binding = {
        "schema_version": context.schema_version,
        "sha256": _sha(context_raw),
        "byte_length": len(context_raw),
    }
    if guidance.source_context_schema_version != context.schema_version:
        raise Qwen27BCaseBundleError(
            "retrieval_guidance_source_context_schema_invalid"
        )
    return {
        "agent_context": context_binding,
        "retrieval_guidance": {
            "schema_version": guidance.schema_version,
            "sha256": _sha(guidance_raw),
            "byte_length": len(guidance_raw),
            "source_context": dict(context_binding),
        },
    }


def _validate_context_guidance_binding_shape(
    row: Mapping[str, object],
) -> bool:
    context = row.get("agent_context")
    guidance = row.get("retrieval_guidance")
    if context is None and guidance is None:
        return False
    if (
        type(context) is not dict
        or set(context) != {"schema_version", "sha256", "byte_length"}
        or context["schema_version"] != AGENT_BUNDLE_SCHEMA_VERSION
        or type(context["byte_length"]) is not int
        or context["byte_length"] <= 0
    ):
        raise Qwen27BCaseBundleError("case_bundle_agent_context_binding_invalid")
    _hex64(context["sha256"], "case_bundle_agent_context_sha256")
    if (
        type(guidance) is not dict
        or set(guidance)
        != {
            "schema_version",
            "sha256",
            "byte_length",
            "source_context",
        }
        or guidance["schema_version"] != RETRIEVAL_GUIDANCE_SCHEMA_VERSION
        or type(guidance["byte_length"]) is not int
        or guidance["byte_length"] <= 0
        or guidance["source_context"] != context
    ):
        raise Qwen27BCaseBundleError(
            "case_bundle_retrieval_guidance_binding_invalid"
        )
    _hex64(guidance["sha256"], "case_bundle_retrieval_guidance_sha256")
    return True


def _validate_binding(
    value: object,
    raw: bytes,
    label: str,
    *,
    identifier: str | None = None,
    relative_path: str | None = None,
) -> None:
    if type(value) is not dict:
        raise Qwen27BCaseBundleError(f"{label}_binding_invalid")
    expected = _binding(raw, identifier=identifier)
    if relative_path is not None:
        expected = {"relative_path": relative_path, **expected}
    if value != expected:
        raise Qwen27BCaseBundleError(f"{label}_binding_invalid")


def _tracked_binding(record: Mapping[str, object], key: str, raw: bytes) -> None:
    value = record.get(key)
    if not isinstance(value, Mapping):
        raise Qwen27BCaseBundleError(f"tracked_{key}_binding_invalid")
    if value.get("sha256") != _sha(raw) or value.get("byte_length") != len(raw):
        raise Qwen27BCaseBundleError(f"tracked_{key}_binding_invalid")


def _case_relative_paths(case_id: str) -> tuple[str, str]:
    return (
        f"cases/{case_id}/prompt.json",
        f"cases/{case_id}/provider_input.json",
    )


def _expected_payload_paths() -> tuple[str, ...]:
    return tuple(
        path
        for case_id in CASE_IDS
        for path in _case_relative_paths(case_id)
    )


def _safe_file(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise Qwen27BCaseBundleError("bundle_relative_path_invalid")
    path = root.joinpath(relative)
    if path.is_symlink():
        raise Qwen27BCaseBundleError("bundle_symlink_forbidden")
    return path


def _archive_authority(
    manifest: repository_archive.RepositoryArchiveManifest,
) -> dict[str, object]:
    data = manifest.to_dict()
    return {
        "manifest_id": data["manifest_id"],
        "manifest_sha256": manifest.sha256(),
        "commit_sha": data["commit_sha"],
        "tree_sha": data["tree_sha"],
        "archive_byte_length": data["archive"]["byte_length"],
        "archive_sha256": data["archive"]["sha256"],
    }


def _validate_archive_authority(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != {
        "manifest_id",
        "manifest_sha256",
        "commit_sha",
        "tree_sha",
        "archive_byte_length",
        "archive_sha256",
    }:
        raise Qwen27BCaseBundleError("case_bundle_archive_authority_invalid")
    if (
        type(value["archive_byte_length"]) is not int
        or value["archive_byte_length"] <= 0
    ):
        raise Qwen27BCaseBundleError("case_bundle_archive_authority_invalid")
    return {
        "manifest_id": _hex64(value["manifest_id"], "archive_manifest_id"),
        "manifest_sha256": _hex64(
            value["manifest_sha256"], "archive_manifest_sha256"
        ),
        "commit_sha": _hex40(value["commit_sha"], "archive_commit_sha"),
        "tree_sha": _hex40(value["tree_sha"], "archive_tree_sha"),
        "archive_byte_length": value["archive_byte_length"],
        "archive_sha256": _hex64(value["archive_sha256"], "archive_sha256"),
    }


def _source_row(
    archive_rows: Mapping[str, Mapping[str, object]],
    relative_path: str,
    raw: bytes,
) -> dict[str, object]:
    row = archive_rows.get(relative_path)
    if (
        not isinstance(row, Mapping)
        or row.get("byte_length") != len(raw)
        or row.get("sha256") != _sha(raw)
        or type(row.get("git_mode")) is not str
        or type(row.get("blob_sha")) is not str
    ):
        raise Qwen27BCaseBundleError("archive_source_file_binding_invalid")
    return {
        "relative_path": relative_path,
        "git_mode": row["git_mode"],
        "blob_sha": row["blob_sha"],
        **_binding(raw),
    }


def _validate_source_documents(
    repository_root: Path,
    archive_authority: Mapping[str, object],
    archive_rows: Mapping[str, Mapping[str, object]],
) -> tuple[tuple[dict[str, object], ...], dict[str, Mapping[str, object]], dict[str, object]]:
    case_path = repository_root / _CASE_SET_PATH
    tracked_path = repository_root / _TRACKED_RECORD_PATH
    cases_document = _load_object(case_path, "case_set")
    tracked_document = _load_object(tracked_path, "tracked_record")
    cases = cases_document.get("cases")
    tracked_cases = tracked_document.get("cases")
    if (
        type(cases) is not list
        or tuple(row.get("case_id") for row in cases if type(row) is dict) != CASE_IDS
        or type(tracked_cases) is not list
        or tuple(row.get("case_id") for row in tracked_cases if type(row) is dict) != CASE_IDS
    ):
        raise Qwen27BCaseBundleError("fixed_case_coverage_invalid")
    boundary = tracked_document.get("inherited_boundary")
    expected_boundary = {
        "execution_path": "path_3",
        "non_h1": True,
        "frozen_regression_cases_used": False,
        "h1_or_gold_accessed": False,
        "reference_only_assets_used": False,
        "third_party_payload_used": False,
    }
    if boundary != expected_boundary:
        raise Qwen27BCaseBundleError("tracked_boundary_invalid")
    canonical_case_set = _canonical(cases_document)
    tracked_case_set = tracked_document.get("case_set")
    if (
        not isinstance(tracked_case_set, Mapping)
        or tracked_case_set.get("sha256") != _sha(canonical_case_set)
        or tracked_case_set.get("byte_length") != len(canonical_case_set)
    ):
        raise Qwen27BCaseBundleError("tracked_case_set_binding_invalid")
    tracked_by_case = {row["case_id"]: row for row in tracked_cases}
    rag_rows = [
        dict(row)
        for path, row in archive_rows.items()
        if path.startswith(_RAG_INDEX_PATH + "/")
    ]
    if not rag_rows:
        raise Qwen27BCaseBundleError("archive_rag_index_missing")
    rag_rows.sort(key=lambda row: row["path"])
    source = {
        "repository_archive_authority": dict(archive_authority),
        "case_set": _source_row(
            archive_rows, _CASE_SET_PATH, case_path.read_bytes()
        ),
        "tracked_preparation": _source_row(
            archive_rows, _TRACKED_RECORD_PATH, tracked_path.read_bytes()
        ),
        "rag_index": {
            "relative_path": _RAG_INDEX_PATH,
            "file_count": len(rag_rows),
            "inventory_sha256": _sha(_canonical(rag_rows)),
        },
    }
    return tuple(cases), tracked_by_case, source


def _extract_bundle_sources(
    archive_path: Path,
    manifest: repository_archive.RepositoryArchiveManifest,
    destination: Path,
) -> tuple[dict[str, object], dict[str, Mapping[str, object]]]:
    manifest_data = manifest.to_dict()
    rows = {
        row["path"]: row
        for row in manifest_data["files"]
        if isinstance(row, Mapping)
    }
    selected_paths = sorted(
        path
        for path in rows
        if path in {_CASE_SET_PATH, _TRACKED_RECORD_PATH}
        or path.startswith(_RAG_INDEX_PATH + "/")
    )
    if (
        _CASE_SET_PATH not in selected_paths
        or _TRACKED_RECORD_PATH not in selected_paths
        or not any(path.startswith(_RAG_INDEX_PATH + "/") for path in selected_paths)
    ):
        raise Qwen27BCaseBundleError("archive_bundle_sources_missing")
    try:
        with zipfile.ZipFile(archive_path, "r") as source:
            for relative_path in selected_paths:
                raw = source.read(relative_path)
                target = _safe_file(destination, relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise Qwen27BCaseBundleError("archive_bundle_source_extract_failed") from exc
    return _archive_authority(manifest), rows


def _build_case_bytes(
    repository_root: Path,
    case: Mapping[str, object],
    tracked: Mapping[str, object],
    chain: MinimalAgentChain,
) -> tuple[bytes, bytes, dict[str, object]]:
    case_id = case["case_id"]
    context = chain.run(
        case["requirement"],
        target_device=case["target_device"],
        task_type=case["task_type"],
        constraints=case["constraints"],
    )
    guidance = RetrievalGuidanceBuilder().build(context)
    manifest = D17Path3TierAManifest.create(
        structural_signal_names=tuple(case["structural_signal_names"])
    )
    selected = select_d17_path3_provider_input(
        context,
        manifest,
        original_requirement_source_class=case["source_class"],
    )
    local_request = serialize_d17_path3_local_request(context, selected, manifest)
    audit = create_d17_path3_pre_invocation_audit_record(
        context, manifest, selected, local_request
    )
    preparation = prepare_local_qwen_provider_interface(
        context, manifest, selected, local_request, audit
    )
    selected.validate_against(context, manifest)
    local_request.validate_against(context, manifest)
    audit.validate_against(context, manifest, selected, local_request)
    preparation.validate_against(context, manifest, selected, local_request, audit)

    provider_input = selected.provider_visible_input.canonical_bytes()
    prompt_artifact = local_request.prompt_artifact.canonical_bytes()
    local_request_raw = local_request.canonical_bytes()
    _tracked_binding(tracked, "provider_visible_payload", provider_input)
    _tracked_binding(tracked, "prompt_artifact", prompt_artifact)
    _tracked_binding(tracked, "local_request", local_request_raw)

    prompt = _canonical(
        {
            "schema_version": PROMPT_ARTIFACT_SCHEMA,
            "prompt_text": local_request.prompt_artifact.prompt_text,
            "provider_input_mode": "append_exact_provider_visible_input_utf8",
        }
    )
    metadata = {
        "case_id": case_id,
        "run_order": case["run_order"],
        "source_class": case["source_class"],
        "provider_input_id": selected.selection_record.input_view_id,
        "provider_input": _binding(
            provider_input, identifier=selected.selection_record.input_view_id
        ),
        "prompt": _binding(prompt),
        "canonical_prompt_artifact": _binding(prompt_artifact),
        "local_request": _binding(local_request_raw),
        "context_sha256": _sha(_canonical(context.to_dict())),
        "guidance_sha256": _sha(_canonical(guidance.to_dict())),
        **_context_guidance_bindings(context, guidance),
    }
    return prompt, provider_input, metadata


def _rebuild_case_authority(
    *,
    bundle_root: Path,
    archive_path: Path,
    archive_manifest: repository_archive.RepositoryArchiveManifest,
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    extraction_root = bundle_root.parent / (
        "." + bundle_root.name + ".authority-source-" + uuid4().hex
    )
    extraction_root.mkdir(parents=False, exist_ok=False)
    try:
        archive_authority, archive_rows = _extract_bundle_sources(
            archive_path, archive_manifest, extraction_root
        )
        cases, tracked_by_case, source = _validate_source_documents(
            extraction_root, archive_authority, archive_rows
        )
        retriever = create_retriever(
            RetrieverConfig(
                index_dir=extraction_root / _RAG_INDEX_PATH,
                backend="tfidf",
            )
        )
        chain = MinimalAgentChain(
            DeterministicRequirementProvider(), retriever, top_k_per_role=2
        )
        rebuilt: dict[str, dict[str, object]] = {}
        for case in cases:
            case_id = case["case_id"]
            prompt, provider_input, row = _build_case_bytes(
                extraction_root, case, tracked_by_case[case_id], chain
            )
            rebuilt[case_id] = {
                "prompt_bytes": prompt,
                "provider_input_bytes": provider_input,
                "metadata": row,
                "semantic_bindings": {
                    "agent_context": row["agent_context"],
                    "retrieval_guidance": row["retrieval_guidance"],
                },
            }
        return source, rebuilt
    finally:
        shutil.rmtree(extraction_root, ignore_errors=True)


def _inventory(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for relative_path in _expected_payload_paths():
        path = _safe_file(root, relative_path)
        if not path.is_file():
            raise Qwen27BCaseBundleError("bundle_payload_missing")
        raw = path.read_bytes()
        rows.append(
            {
                "relative_path": relative_path,
                "byte_length": len(raw),
                "sha256": _sha(raw),
            }
        )
    return rows


def _manifest_body(
    source: Mapping[str, object],
    cases: list[dict[str, object]],
    inventory: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": CASE_BUNDLE_SCHEMA,
        "state": CASE_BUNDLE_STATE,
        "model_profile": {
            "model_id": "Qwen3.5-27B",
            "repository": MODEL_REPOSITORY,
            "exact_revision": MODEL_REVISION,
        },
        "case_ids": list(CASE_IDS),
        "source": dict(source),
        "cases": cases,
        "inventory": {
            "files": inventory,
            "file_count": len(inventory),
            "total_bytes": sum(row["byte_length"] for row in inventory),
            "inventory_sha256": _sha(_canonical(inventory)),
        },
        "claims": {
            "model_loaded": False,
            "run_occurred": False,
            "provider_call_count": 0,
            "h1": False,
            "formal_quality": False,
            "browser_quality": False,
            "evidence_use": False,
        },
    }


def _identified(body: dict[str, object]) -> dict[str, object]:
    data = dict(body)
    data["bundle_id"] = "qwen35-27b-case-bundle-v1-" + _sha(_canonical(body))
    return data


def _validate_manifest_shape(data: object) -> dict[str, object]:
    if type(data) is not dict or set(data) != {
        "schema_version",
        "state",
        "bundle_id",
        "model_profile",
        "case_ids",
        "source",
        "cases",
        "inventory",
        "claims",
    }:
        raise Qwen27BCaseBundleError("case_bundle_manifest_shape_invalid")
    if data["schema_version"] != CASE_BUNDLE_SCHEMA or data["state"] != CASE_BUNDLE_STATE:
        raise Qwen27BCaseBundleError("case_bundle_schema_or_state_invalid")
    if data["case_ids"] != list(CASE_IDS):
        raise Qwen27BCaseBundleError("case_bundle_case_order_invalid")
    expected_profile = {
        "model_id": "Qwen3.5-27B",
        "repository": MODEL_REPOSITORY,
        "exact_revision": MODEL_REVISION,
    }
    if data["model_profile"] != expected_profile:
        raise Qwen27BCaseBundleError("case_bundle_model_profile_invalid")
    source = data["source"]
    if type(source) is not dict or set(source) != {
        "repository_archive_authority",
        "case_set",
        "tracked_preparation",
        "rag_index",
    }:
        raise Qwen27BCaseBundleError("case_bundle_source_invalid")
    _validate_archive_authority(source["repository_archive_authority"])
    for key, relative_path in (
        ("case_set", _CASE_SET_PATH),
        ("tracked_preparation", _TRACKED_RECORD_PATH),
    ):
        row = source[key]
        if type(row) is not dict or set(row) != {
            "relative_path",
            "git_mode",
            "blob_sha",
            "sha256",
            "byte_length",
        }:
            raise Qwen27BCaseBundleError("case_bundle_source_file_invalid")
        if (
            row["relative_path"] != relative_path
            or row["git_mode"] not in {"100644", "100755"}
            or type(row["blob_sha"]) is not str
            or len(row["blob_sha"]) != 40
            or any(ch not in _HEX64 for ch in row["blob_sha"])
            or type(row["byte_length"]) is not int
            or row["byte_length"] <= 0
        ):
            raise Qwen27BCaseBundleError("case_bundle_source_file_invalid")
        _hex64(row["sha256"], "case_bundle_source_file_sha256")
    rag_index = source["rag_index"]
    if (
        type(rag_index) is not dict
        or rag_index.get("relative_path") != _RAG_INDEX_PATH
        or type(rag_index.get("file_count")) is not int
        or rag_index["file_count"] <= 0
    ):
        raise Qwen27BCaseBundleError("case_bundle_rag_source_invalid")
    _hex64(rag_index.get("inventory_sha256"), "case_bundle_rag_inventory_sha256")
    claims = data["claims"]
    if claims != {
        "model_loaded": False,
        "run_occurred": False,
        "provider_call_count": 0,
        "h1": False,
        "formal_quality": False,
        "browser_quality": False,
        "evidence_use": False,
    }:
        raise Qwen27BCaseBundleError("case_bundle_claims_invalid")
    body = {key: value for key, value in data.items() if key != "bundle_id"}
    expected_id = "qwen35-27b-case-bundle-v1-" + _sha(_canonical(body))
    if data["bundle_id"] != expected_id:
        raise Qwen27BCaseBundleError("case_bundle_id_invalid")
    return data


@dataclass(frozen=True)
class Qwen27BCaseBundle:
    root: Path
    manifest: dict[str, object]
    verified_case_bindings: dict[str, dict[str, object]] | None = None

    def validate(self) -> None:
        validate_qwen27b_case_bundle(self.root, expected_manifest=self.manifest)

    def canonical_bytes(self) -> bytes:
        self.validate()
        return _canonical(self.manifest)

    def sha256(self) -> str:
        return _sha(self.canonical_bytes())


def build_qwen27b_case_bundle(
    repository_archive_path: Path | str,
    repository_archive_manifest_path: Path | str,
    output_root: Path | str,
) -> Qwen27BCaseBundle:
    archive_path = Path(repository_archive_path).resolve(strict=True)
    archive_manifest_path = Path(repository_archive_manifest_path).resolve(strict=True)
    try:
        archive_manifest = repository_archive.validate_archive_file(
            archive_path, archive_manifest_path
        )
    except repository_archive.RepositoryArchiveError as exc:
        raise Qwen27BCaseBundleError("repository_archive_authority_invalid") from exc
    output_root = Path(output_root)
    if output_root.exists():
        if output_root.is_symlink() or not output_root.is_dir() or any(output_root.iterdir()):
            raise Qwen27BCaseBundleError("case_bundle_output_root_not_empty")
        root = output_root.resolve(strict=True)
    else:
        output_root.mkdir(parents=True, exist_ok=False)
        root = output_root.resolve(strict=True)
    extraction_root = root.parent / (
        "." + root.name + ".archive-source-" + uuid4().hex
    )
    extraction_root.mkdir(parents=False, exist_ok=False)
    try:
        archive_authority, archive_rows = _extract_bundle_sources(
            archive_path, archive_manifest, extraction_root
        )
        cases, tracked_by_case, source = _validate_source_documents(
            extraction_root, archive_authority, archive_rows
        )
        retriever = create_retriever(
            RetrieverConfig(
                index_dir=extraction_root / _RAG_INDEX_PATH,
                backend="tfidf",
            )
        )
        chain = MinimalAgentChain(
            DeterministicRequirementProvider(), retriever, top_k_per_role=2
        )
        case_rows: list[dict[str, object]] = []
        for case in cases:
            case_id = case["case_id"]
            prompt, provider_input, row = _build_case_bytes(
                extraction_root, case, tracked_by_case[case_id], chain
            )
            prompt_relative, provider_relative = _case_relative_paths(case_id)
            prompt_path = _safe_file(root, prompt_relative)
            provider_path = _safe_file(root, provider_relative)
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_bytes(prompt)
            provider_path.write_bytes(provider_input)
            row["prompt_file"] = {
                "relative_path": prompt_relative,
                **_binding(prompt),
            }
            row["provider_input_file"] = {
                "relative_path": provider_relative,
                **_binding(provider_input),
            }
            case_rows.append(row)
    finally:
        shutil.rmtree(extraction_root, ignore_errors=True)
    rows = _inventory(root)
    manifest = _identified(_manifest_body(source, case_rows, rows))
    (root / CASE_BUNDLE_MANIFEST).write_bytes(_canonical(manifest))
    return validate_qwen27b_case_bundle(
        root,
        repository_archive_path=archive_path,
        repository_archive_manifest_path=archive_manifest_path,
    )


def validate_qwen27b_case_bundle(
    bundle_root: Path | str,
    *,
    expected_manifest: Mapping[str, object] | None = None,
    repository_archive_path: Path | str | None = None,
    repository_archive_manifest_path: Path | str | None = None,
) -> Qwen27BCaseBundle:
    root = Path(bundle_root).resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise Qwen27BCaseBundleError("case_bundle_root_invalid")
    actual_paths = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    )
    expected_paths = sorted((*_expected_payload_paths(), CASE_BUNDLE_MANIFEST))
    if actual_paths != expected_paths:
        raise Qwen27BCaseBundleError("case_bundle_path_inventory_invalid")
    data = _validate_manifest_shape(
        _load_object(root / CASE_BUNDLE_MANIFEST, "case_bundle_manifest")
    )
    if expected_manifest is not None and data != dict(expected_manifest):
        raise Qwen27BCaseBundleError("case_bundle_expected_manifest_mismatch")
    if (repository_archive_path is None) != (
        repository_archive_manifest_path is None
    ):
        raise Qwen27BCaseBundleError("case_bundle_archive_pair_required")
    rebuilt_source: dict[str, object] | None = None
    rebuilt_cases: dict[str, dict[str, object]] | None = None
    if repository_archive_path is not None:
        archive_path = Path(repository_archive_path).resolve(strict=True)
        try:
            owner = repository_archive.validate_archive_file(
                archive_path,
                repository_archive_manifest_path,
            )
        except repository_archive.RepositoryArchiveError as exc:
            raise Qwen27BCaseBundleError(
                "repository_archive_authority_invalid"
            ) from exc
        if (
            _archive_authority(owner)
            != data["source"]["repository_archive_authority"]
        ):
            raise Qwen27BCaseBundleError(
                "case_bundle_archive_authority_mismatch"
            )
        rebuilt_source, rebuilt_cases = _rebuild_case_authority(
            bundle_root=root,
            archive_path=archive_path,
            archive_manifest=owner,
        )
        if rebuilt_source != data["source"]:
            raise Qwen27BCaseBundleError(
                "case_bundle_source_authority_mismatch"
            )
    rows = _inventory(root)
    inventory = data.get("inventory")
    if type(inventory) is not dict or inventory != {
        "files": rows,
        "file_count": len(rows),
        "total_bytes": sum(row["byte_length"] for row in rows),
        "inventory_sha256": _sha(_canonical(rows)),
    }:
        raise Qwen27BCaseBundleError("case_bundle_inventory_invalid")
    case_rows = data.get("cases")
    if type(case_rows) is not list or tuple(
        row.get("case_id") for row in case_rows if type(row) is dict
    ) != CASE_IDS:
        raise Qwen27BCaseBundleError("case_bundle_case_rows_invalid")
    for row in case_rows:
        has_semantic_bindings = _validate_context_guidance_binding_shape(row)
        prompt_relative, provider_relative = _case_relative_paths(row["case_id"])
        prompt = _safe_file(root, prompt_relative).read_bytes()
        provider_input = _safe_file(root, provider_relative).read_bytes()
        _validate_binding(
            row.get("prompt_file"),
            prompt,
            "prompt_file",
            relative_path=prompt_relative,
        )
        _validate_binding(
            row.get("provider_input_file"),
            provider_input,
            "provider_input_file",
            relative_path=provider_relative,
        )
        _validate_binding(
            row.get("prompt"),
            prompt,
            "prompt",
        )
        provider_id = row.get("provider_input_id")
        _validate_binding(
            row.get("provider_input"),
            provider_input,
            "provider_input",
            identifier=provider_id,
        )
        try:
            prompt_document = json.loads(prompt.decode("utf-8"))
            provider_document = json.loads(provider_input.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Qwen27BCaseBundleError("case_bundle_json_invalid") from exc
        if (
            type(prompt_document) is not dict
            or set(prompt_document) != {
                "schema_version",
                "prompt_text",
                "provider_input_mode",
            }
            or prompt_document["schema_version"] != PROMPT_ARTIFACT_SCHEMA
            or prompt_document["provider_input_mode"]
            != "append_exact_provider_visible_input_utf8"
            or type(prompt_document["prompt_text"]) is not str
            or not prompt_document["prompt_text"]
            or type(provider_document) is not dict
        ):
            raise Qwen27BCaseBundleError("case_bundle_document_contract_invalid")
        if rebuilt_cases is not None:
            rebuilt = rebuilt_cases[row["case_id"]]
            if (
                prompt != rebuilt["prompt_bytes"]
                or provider_input != rebuilt["provider_input_bytes"]
            ):
                raise Qwen27BCaseBundleError(
                    "case_bundle_upstream_payload_mismatch"
                )
            rebuilt_row = rebuilt["metadata"]
            for key in (
                "provider_input_id",
                "provider_input",
                "prompt",
                "canonical_prompt_artifact",
                "local_request",
                "context_sha256",
                "guidance_sha256",
            ):
                if row.get(key) != rebuilt_row[key]:
                    raise Qwen27BCaseBundleError(
                        f"case_bundle_upstream_{key}_mismatch"
                    )
            if has_semantic_bindings and (
                row["agent_context"] != rebuilt_row["agent_context"]
                or row["retrieval_guidance"]
                != rebuilt_row["retrieval_guidance"]
            ):
                raise Qwen27BCaseBundleError(
                    "case_bundle_upstream_context_guidance_mismatch"
                )
    verified = (
        None
        if rebuilt_cases is None
        else {
            case_id: dict(rebuilt_cases[case_id]["semantic_bindings"])
            for case_id in CASE_IDS
        }
    )
    return Qwen27BCaseBundle(root, data, verified)


def compose_qwen27b_model_text(
    bundle: Qwen27BCaseBundle, case_id: str
) -> str:
    if type(bundle) is not Qwen27BCaseBundle:
        raise Qwen27BCaseBundleError("case_bundle_exact_type_required")
    live = validate_qwen27b_case_bundle(
        bundle.root, expected_manifest=bundle.manifest
    )
    if case_id not in CASE_IDS:
        raise Qwen27BCaseBundleError("case_id_invalid")
    prompt_relative, provider_relative = _case_relative_paths(case_id)
    prompt = json.loads((live.root / prompt_relative).read_text(encoding="utf-8"))
    provider_text = (live.root / provider_relative).read_text(encoding="utf-8")
    return (
        prompt["prompt_text"]
        + "\n\n<provider_visible_input>\n"
        + provider_text
        + "\n</provider_visible_input>"
    )


__all__ = (
    "CASE_BUNDLE_MANIFEST",
    "CASE_BUNDLE_SCHEMA",
    "CASE_IDS",
    "MODEL_REPOSITORY",
    "MODEL_REVISION",
    "Qwen27BCaseBundle",
    "Qwen27BCaseBundleError",
    "build_qwen27b_case_bundle",
    "compose_qwen27b_model_text",
    "validate_qwen27b_case_bundle",
)
