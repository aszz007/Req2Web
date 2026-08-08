"""Prepare one path-free, three-case Phase 5 semantic canary manifest.

The canary reads completed Phase 4 browser evidence, reuses the owning
semantic-evidence validators, and writes no model result. It never loads a
model, controls a browser, reads gold, or serializes local paths.
"""

from __future__ import annotations

import copy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence

from req2web_evaluation.phase5_semantic_evaluator import (
    Phase5SemanticEvaluatorError,
    SEMANTIC_ALIGNMENT_CONTRACT_REVISION,
    SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION,
    SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
    SEMANTIC_EVALUATOR_PROMPT_REVISION,
    validate_phase5_semantic_evaluator_no_action_bundle,
)
from req2web_evaluation.phase5_semantic_qwen_runtime import (
    HIGH_GPU_PROFILE,
    MODEL_ID,
    MODEL_REVISION,
    prepare_phase5_semantic_case,
    semantic_qwen_runtime_profile,
)
from req2web_runtime.phase4_browser_acceptance import (
    Phase4BrowserAcceptanceError,
    validate_real_browser_case_audit,
)


SEMANTIC_CANARY_SCHEMA_VERSION = (
    "req2web.phase5.semantic_evaluation_canary.no_action.v1"
)
SEMANTIC_CANARY_STATUS = "prepared_no_action"
SEMANTIC_CANARY_CASE_COUNT = 3
GENERATOR_MODEL_IDENTITY = f"{MODEL_ID}@{MODEL_REVISION}"

_CASE_INPUT_KEYS = {
    "case_order",
    "case_id",
    "audit_root",
    "result_package_root",
}
_EVIDENCE_IDS = (
    "acceptance_binding",
    "acceptance_plan",
    "browser_execution_report",
    "browser_screenshot",
    "page_spec",
    "result_package_manifest",
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")


class Phase5SemanticCanaryError(ValueError):
    """Raised when the no-action semantic canary must fail closed."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Phase5SemanticCanaryError(
            "semantic canary artifact is not canonical JSON"
        ) from exc


def _identity(
    raw: bytes,
    *,
    revision: str,
    identity_kind: str = "canonical_json",
) -> dict[str, object]:
    return {
        "identity_kind": identity_kind,
        "sha256": "sha256:" + sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": revision,
    }


def _read_canonical_object(path: Path, name: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise Phase5SemanticCanaryError(f"{name} is unavailable")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5SemanticCanaryError(f"{name} is not JSON") from exc
    if not isinstance(value, Mapping) or _canonical(value) != raw:
        raise Phase5SemanticCanaryError(
            f"{name} is not a canonical JSON object"
        )
    return copy.deepcopy(dict(value))


def _normalize_cases(
    cases: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    if (
        isinstance(cases, (str, bytes))
        or not isinstance(cases, Sequence)
        or len(cases) != SEMANTIC_CANARY_CASE_COUNT
    ):
        raise Phase5SemanticCanaryError(
            "semantic canary requires exactly three cases"
        )
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for expected_order, value in enumerate(cases, start=1):
        if not isinstance(value, Mapping) or set(value) != _CASE_INPUT_KEYS:
            raise Phase5SemanticCanaryError(
                f"case {expected_order} input keys drifted"
            )
        if (
            type(value["case_order"]) is not int
            or value["case_order"] != expected_order
        ):
            raise Phase5SemanticCanaryError(
                "case_order must be exactly 1, 2, 3 in order"
            )
        case_id = value["case_id"]
        if not isinstance(case_id, str) or not _SAFE_ID.fullmatch(case_id):
            raise Phase5SemanticCanaryError("case_id is invalid")
        if case_id in seen:
            raise Phase5SemanticCanaryError("case_id values must be unique")
        seen.add(case_id)
        audit_root = value["audit_root"]
        package_root = value["result_package_root"]
        if (
            not isinstance(audit_root, Path)
            or not isinstance(package_root, Path)
            or audit_root.is_symlink()
            or package_root.is_symlink()
            or not audit_root.is_dir()
            or not package_root.is_dir()
        ):
            raise Phase5SemanticCanaryError(
                f"case {expected_order} evidence root is unavailable"
            )
        normalized.append(
            {
                "case_order": expected_order,
                "case_id": case_id,
                "audit_root": audit_root,
                "result_package_root": package_root,
            }
        )
    return normalized


def _prepare_row(case: Mapping[str, object]) -> dict[str, object]:
    order = int(case["case_order"])
    case_id = str(case["case_id"])
    audit_root = case["audit_root"]
    package_root = case["result_package_root"]
    if not isinstance(audit_root, Path) or not isinstance(package_root, Path):
        raise Phase5SemanticCanaryError("normalized evidence roots drifted")

    try:
        audit = validate_real_browser_case_audit(
            _read_canonical_object(
                audit_root / "case_browser_audit.json",
                f"case {order} browser audit",
            )
        )
    except (Phase4BrowserAcceptanceError, ValueError) as exc:
        raise Phase5SemanticCanaryError(
            f"case {order} browser audit is invalid"
        ) from exc
    if (
        audit["case_index"] != order
        or audit["case_id"] != case_id
        or audit["browser_status"] != "pass"
        or audit["browser_execution_status"] != "pass"
        or audit["page_spec_conformance_status"] != "pass"
        or audit["real_browser_executed"] is not True
        or audit["automation_reliable"] is not True
        or audit["semantic_alignment"]["status"] != "not_executed"
    ):
        raise Phase5SemanticCanaryError(
            f"case {order} browser audit is not an eligible no-action canary"
        )

    try:
        prepared = prepare_phase5_semantic_case(
            audit_root=audit_root,
            result_package_root=package_root,
            generator_model_identity=GENERATOR_MODEL_IDENTITY,
            profile_name=HIGH_GPU_PROFILE,
        )
        request = prepared["request"]
        evidence_payloads = prepared["evidence_payloads"]
        no_action_bundle = prepared["no_action_bundle"]
        validate_phase5_semantic_evaluator_no_action_bundle(
            request=request,
            evidence_payloads=evidence_payloads,
            value=no_action_bundle,
        )
    except (Phase5SemanticEvaluatorError, ValueError, OSError) as exc:
        raise Phase5SemanticCanaryError(
            f"case {order} semantic evidence preparation failed"
        ) from exc

    if (
        request.case_id != case_id
        or tuple(item.evidence_id for item in request.evidence) != _EVIDENCE_IDS
    ):
        raise Phase5SemanticCanaryError(
            f"case {order} semantic request binding drifted"
        )
    request_raw = request.canonical_json_bytes()
    if audit["semantic_alignment"]["request_identity"] != _identity(
        request_raw,
        revision=request.schema_version,
    ):
        raise Phase5SemanticCanaryError(
            f"case {order} semantic request identity drifted"
        )
    return {
        "case_order": order,
        "case_id": case_id,
        "case_browser_audit_identity": copy.deepcopy(audit["audit_identity"]),
        "request_sha256": request.sha256(),
        "no_action_bundle_identity": copy.deepcopy(
            no_action_bundle["bundle_identity"]
        ),
        "evidence_identities": [
            copy.deepcopy(item.to_dict()) for item in request.evidence
        ],
    }


def _build_manifest(
    cases: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    rows = [_prepare_row(case) for case in _normalize_cases(cases)]
    runtime_profile = semantic_qwen_runtime_profile(HIGH_GPU_PROFILE).to_dict()
    root = {
        "schema_version": SEMANTIC_CANARY_SCHEMA_VERSION,
        "status": SEMANTIC_CANARY_STATUS,
        "model_identity": GENERATOR_MODEL_IDENTITY,
        "profile_name": HIGH_GPU_PROFILE,
        "evaluator_prompt_revision": SEMANTIC_EVALUATOR_PROMPT_REVISION,
        "semantic_contract_revision": SEMANTIC_ALIGNMENT_CONTRACT_REVISION,
        "semantic_request_schema_version": (
            SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION
        ),
        "semantic_result_schema_version": (
            SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION
        ),
        "runtime_profile_identity": _identity(
            _canonical(runtime_profile),
            revision=str(runtime_profile["schema_version"]),
        ),
        "aggregate_generate_call_cap": 3,
        "per_case_generate_call_cap": 1,
        "aggregate_time_cap_seconds": 3_600,
        "storage_cap_bytes": 250_000_000,
        "automatic_retry_limit": 0,
        "fresh_evidence_only_context": True,
        "partial_or_unknown_requires_human_review": True,
        "owner_review_cannot_modify_model_artifacts": True,
        "raw_response_capture_required": True,
        "result_return_required": True,
        "browser_control_allowed": False,
        "formal_quality_claimed": False,
        "h1_or_gold_access": False,
        "model_loaded": False,
        "gpu_or_remote_action": False,
        "semantic_alignment_executed": False,
        "cases": rows,
    }
    return {
        **root,
        "manifest_identity": _identity(
            _canonical(root),
            revision=SEMANTIC_CANARY_SCHEMA_VERSION,
        ),
    }


def prepare_phase5_semantic_canary(
    *,
    cases: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build the deterministic no-action canary manifest."""

    return _build_manifest(cases)


def validate_phase5_semantic_canary(
    *,
    manifest: object,
    cases: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Replay the manifest against the same read-only evidence roots."""

    if not isinstance(manifest, Mapping):
        raise Phase5SemanticCanaryError(
            "semantic canary manifest must be an object"
        )
    data = copy.deepcopy(dict(manifest))
    if data != _build_manifest(cases):
        raise Phase5SemanticCanaryError(
            "semantic canary manifest does not match replayed evidence"
        )
    return data


def write_phase5_semantic_canary_manifest(
    *,
    output_path: Path,
    cases: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Replay-validate and exclusively write one new manifest."""

    manifest = prepare_phase5_semantic_canary(cases=cases)
    validate_phase5_semantic_canary(manifest=manifest, cases=cases)
    if (
        not isinstance(output_path, Path)
        or output_path.exists()
        or output_path.is_symlink()
        or not output_path.parent.is_dir()
        or output_path.parent.is_symlink()
    ):
        raise Phase5SemanticCanaryError(
            "manifest output must be a new file in an existing directory"
        )
    try:
        with output_path.open("xb") as handle:
            handle.write(_canonical(manifest))
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise Phase5SemanticCanaryError(
            "manifest output could not be created"
        ) from exc
    return manifest


__all__ = [
    "GENERATOR_MODEL_IDENTITY",
    "SEMANTIC_CANARY_CASE_COUNT",
    "SEMANTIC_CANARY_SCHEMA_VERSION",
    "SEMANTIC_CANARY_STATUS",
    "Phase5SemanticCanaryError",
    "prepare_phase5_semantic_canary",
    "validate_phase5_semantic_canary",
    "write_phase5_semantic_canary_manifest",
]
