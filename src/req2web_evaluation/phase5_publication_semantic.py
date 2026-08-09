"""Bind and aggregate the twelve-row Phase 5 semantic evaluation.

This module is an additive sidecar over already completed objective browser
evidence.  It reuses the owning semantic request, evaluator, and supervised
Qwen runtime.  It never changes a ResultPackage, browser audit, F1-F4 output,
historical first-pass count, or objective browser status.
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
    SEMANTIC_ALIGNMENT_CONTRACT_REVISION,
    SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION,
    SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
    SEMANTIC_EVALUATOR_PROMPT_REVISION,
    Phase5SemanticEvaluatorError,
    parse_phase5_semantic_evaluator_raw_response,
    validate_phase5_semantic_evaluator_no_action_bundle,
)
from req2web_evaluation.phase5_semantic_qwen_runtime import (
    HIGH_GPU_PROFILE,
    MODEL_ID,
    MODEL_INPUT_SCHEMA_VERSION,
    MODEL_REVISION,
    RUN_SUMMARY_SCHEMA_VERSION,
    RUNTIME_SCHEMA_VERSION,
    Phase5SemanticQwenRuntimeError,
    prepare_phase5_semantic_case,
    semantic_qwen_runtime_profile,
    validate_existing_phase5_semantic_qwen_result,
)
from req2web_runtime.phase4_browser_acceptance import (
    Phase4BrowserAcceptanceError,
    validate_real_browser_case_audit,
)
from req2web_runtime.phase5_publication_browser import (
    Phase5PublicationBrowserError,
    replay_phase5_publication_browser_result,
)


PUBLICATION_SEMANTIC_MANIFEST_SCHEMA_VERSION = (
    "req2web.phase5.publication_semantic_manifest.v1"
)
PUBLICATION_SEMANTIC_SUMMARY_SCHEMA_VERSION = (
    "req2web.phase5.publication_semantic_summary.v1"
)
PUBLICATION_SEMANTIC_STATUS = "prepared_no_action"
PUBLICATION_SEMANTIC_ROW_COUNT = 12
PUBLICATION_SEMANTIC_GENERATE_CALL_CAP = 12
PUBLICATION_SEMANTIC_CRITERION_COUNT = 24
GENERATOR_MODEL_IDENTITY = f"{MODEL_ID}@{MODEL_REVISION}"

_VERDICTS = ("supported", "violated", "partial", "unknown")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")
_RUN_SUMMARY_KEYS = {
    "schema_version",
    "runtime_schema_version",
    "status",
    "case_id",
    "profile",
    "worker_exit_code",
    "worker_timed_out",
    "worker_error",
    "worker_receipt",
    "generate_started_count",
    "automatic_retry_count",
    "raw_response_formed",
    "raw_response_identity",
    "parse_error",
    "semantic_alignment_executed",
    "owner_review_required",
    "formal_quality_claimed",
    "finalized_without_model_call",
    "claim_boundary",
}


class Phase5PublicationSemanticError(ValueError):
    """Raised when publication semantic evidence must fail closed."""


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
        raise Phase5PublicationSemanticError(
            "publication semantic artifact is not canonical JSON"
        ) from exc


def _identity(
    raw: bytes,
    *,
    revision: str,
    identity_kind: str = "canonical_json",
) -> dict[str, object]:
    if type(raw) is not bytes:
        raise Phase5PublicationSemanticError("identity input must be bytes")
    return {
        "identity_kind": identity_kind,
        "sha256": "sha256:" + sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": revision,
    }


def _runtime_identity(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes:
        raise Phase5PublicationSemanticError(
            "runtime identity input must be bytes"
        )
    return {
        "sha256": "sha256:" + sha256(raw).hexdigest(),
        "byte_length": len(raw),
    }


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase5PublicationSemanticError(f"{name} must be an object")
    return copy.deepcopy(dict(value))


def _read_canonical_object(path: Path, name: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise Phase5PublicationSemanticError(f"{name} is unavailable")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5PublicationSemanticError(f"{name} is not JSON") from exc
    if not isinstance(value, Mapping) or _canonical(value) != raw:
        raise Phase5PublicationSemanticError(
            f"{name} is not a canonical JSON object"
        )
    return copy.deepcopy(dict(value))


def _write_once(path: Path, raw: bytes) -> None:
    if (
        not isinstance(path, Path)
        or path.exists()
        or path.is_symlink()
        or not path.parent.is_dir()
        or path.parent.is_symlink()
    ):
        raise Phase5PublicationSemanticError(
            "publication semantic output must be a new file in an existing directory"
        )
    try:
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise Phase5PublicationSemanticError(
            "publication semantic output could not be created"
        ) from exc


def _result_leaf(execution_index: int, row_id: str) -> str:
    return f"{execution_index:02d}-{row_id}"


def _source_roots(
    *,
    revalidation_root: Path,
    browser_root: Path,
) -> tuple[Path, Path, dict[str, object]]:
    try:
        source = Path(revalidation_root).resolve(strict=True)
        browser = Path(browser_root).resolve(strict=True)
    except OSError as exc:
        raise Phase5PublicationSemanticError(
            "publication semantic source root is unavailable"
        ) from exc
    if (
        not source.is_dir()
        or source.is_symlink()
        or not browser.is_dir()
        or browser.is_symlink()
    ):
        raise Phase5PublicationSemanticError(
            "publication semantic source root is invalid"
        )
    try:
        summary = replay_phase5_publication_browser_result(
            revalidation_root=source,
            browser_root=browser,
        )
    except (Phase5PublicationBrowserError, ValueError, OSError) as exc:
        raise Phase5PublicationSemanticError(
            "publication browser evidence replay failed"
        ) from exc
    return source, browser, summary


def _validate_browser_rows(
    summary: Mapping[str, object],
) -> list[dict[str, object]]:
    rows = summary.get("row_results")
    if not isinstance(rows, list) or len(rows) != PUBLICATION_SEMANTIC_ROW_COUNT:
        raise Phase5PublicationSemanticError(
            "publication semantic input requires exactly twelve browser rows"
        )
    if (
        summary.get("all_objective_browser_checks_pass") is not True
        or summary.get("semantic_alignment_executed") is not False
        or summary.get("historical_v16_first_pass_count_preserved") != 9
        or summary.get("historical_v16_failure_count_preserved") != 3
    ):
        raise Phase5PublicationSemanticError(
            "publication objective browser boundary is not eligible"
        )
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for expected_index, value in enumerate(rows, start=1):
        row = _mapping(value, f"browser row {expected_index}")
        row_id = row.get("row_id")
        case_id = row.get("case_id")
        condition_id = row.get("condition_id")
        if (
            row.get("execution_index") != expected_index
            or not isinstance(row_id, str)
            or not _SAFE_ID.fullmatch(row_id)
            or row_id in seen
            or not isinstance(case_id, str)
            or not _SAFE_ID.fullmatch(case_id)
            or condition_id
            not in {"none", "irrelevant_evidence", "remove_critical_role"}
            or row.get("browser_status") != "pass"
            or row.get("browser_execution_status") != "pass"
            or row.get("page_spec_conformance_status") != "pass"
            or row.get("semantic_alignment_status") != "not_executed"
            or row.get("semantic_alignment_disposition")
            != "needs_semantic_review"
            or row.get("real_browser_executed") is not True
            or row.get("automation_reliable") is not True
        ):
            raise Phase5PublicationSemanticError(
                f"browser row {expected_index} is not eligible"
            )
        seen.add(row_id)
        normalized.append(row)
    case_conditions: dict[str, set[str]] = {}
    for row in normalized:
        case_conditions.setdefault(str(row["case_id"]), set()).add(
            str(row["condition_id"])
        )
    if (
        len(case_conditions) != 4
        or any(
            values
            != {"none", "irrelevant_evidence", "remove_critical_role"}
            for values in case_conditions.values()
        )
    ):
        raise Phase5PublicationSemanticError(
            "publication semantic case-condition coverage drifted"
        )
    return normalized


def _prepare_manifest_row(
    *,
    source_root: Path,
    browser_root: Path,
    row: Mapping[str, object],
) -> dict[str, object]:
    execution_index = int(row["execution_index"])
    row_id = str(row["row_id"])
    case_id = str(row["case_id"])
    audit_root = browser_root / "cases" / f"{execution_index:02d}"
    package_root = (
        source_root
        / "packages"
        / _result_leaf(execution_index, row_id)
    )
    try:
        audit = validate_real_browser_case_audit(
            _read_canonical_object(
                audit_root / "case_browser_audit.json",
                f"browser audit {execution_index}",
            )
        )
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
    except (
        Phase4BrowserAcceptanceError,
        Phase5SemanticEvaluatorError,
        Phase5SemanticQwenRuntimeError,
        ValueError,
        OSError,
    ) as exc:
        raise Phase5PublicationSemanticError(
            f"semantic preparation failed for row {execution_index}"
        ) from exc
    request_raw = request.canonical_json_bytes()
    expected_request_identity = _identity(
        request_raw,
        revision=request.schema_version,
    )
    if (
        audit.get("case_index") != execution_index
        or audit.get("case_id") != case_id
        or audit.get("audit_identity") != row.get("browser_audit_identity")
        or audit.get("result_package") != row.get("result_package")
        or audit.get("semantic_alignment", {}).get("request_identity")
        != expected_request_identity
        or request.case_id != case_id
        or request.model_generate_call_limit != 1
        or request.automatic_retry_limit != 0
        or len(request.review_items) != 2
    ):
        raise Phase5PublicationSemanticError(
            f"semantic row {execution_index} binding drifted"
        )
    return {
        "execution_index": execution_index,
        "row_id": row_id,
        "case_id": case_id,
        "condition_id": row["condition_id"],
        "source_disposition": row["source_disposition"],
        "source_identity": copy.deepcopy(row["source_identity"]),
        "result_package": copy.deepcopy(row["result_package"]),
        "browser_audit_identity": copy.deepcopy(
            row["browser_audit_identity"]
        ),
        "semantic_request_identity": expected_request_identity,
        "semantic_request_sha256": request.sha256(),
        "prompt_identity": _identity(
            prepared["prompt_raw"],
            revision=SEMANTIC_EVALUATOR_PROMPT_REVISION,
        ),
        "model_input_identity": _identity(
            prepared["model_input_raw"],
            revision=MODEL_INPUT_SCHEMA_VERSION,
        ),
        "no_action_bundle_identity": copy.deepcopy(
            no_action_bundle["bundle_identity"]
        ),
        "evidence_identities": [
            copy.deepcopy(item.to_dict()) for item in request.evidence
        ],
        "review_item_count": len(request.review_items),
        "result_leaf": _result_leaf(execution_index, row_id),
    }


def _build_manifest(
    *,
    revalidation_root: Path,
    browser_root: Path,
) -> dict[str, object]:
    source, browser, browser_summary = _source_roots(
        revalidation_root=revalidation_root,
        browser_root=browser_root,
    )
    browser_rows = _validate_browser_rows(browser_summary)
    rows = [
        _prepare_manifest_row(
            source_root=source,
            browser_root=browser,
            row=row,
        )
        for row in browser_rows
    ]
    criterion_count = sum(int(row["review_item_count"]) for row in rows)
    if criterion_count != PUBLICATION_SEMANTIC_CRITERION_COUNT:
        raise Phase5PublicationSemanticError(
            "publication semantic criterion count drifted"
        )
    profile = semantic_qwen_runtime_profile(HIGH_GPU_PROFILE).to_dict()
    root = {
        "schema_version": PUBLICATION_SEMANTIC_MANIFEST_SCHEMA_VERSION,
        "status": PUBLICATION_SEMANTIC_STATUS,
        "run_id": browser_summary["run_id"],
        "source_amended_summary_identity": copy.deepcopy(
            browser_summary["source_amended_summary_identity"]
        ),
        "source_browser_summary_identity": copy.deepcopy(
            browser_summary["summary_identity"]
        ),
        "model_identity": GENERATOR_MODEL_IDENTITY,
        "profile_name": HIGH_GPU_PROFILE,
        "runtime_profile_identity": _identity(
            _canonical(profile),
            revision=str(profile["schema_version"]),
        ),
        "evaluator_prompt_revision": SEMANTIC_EVALUATOR_PROMPT_REVISION,
        "semantic_contract_revision": SEMANTIC_ALIGNMENT_CONTRACT_REVISION,
        "semantic_request_schema_version": (
            SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION
        ),
        "semantic_result_schema_version": (
            SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION
        ),
        "row_count": PUBLICATION_SEMANTIC_ROW_COUNT,
        "criterion_count": criterion_count,
        "aggregate_generate_call_cap": (
            PUBLICATION_SEMANTIC_GENERATE_CALL_CAP
        ),
        "per_row_generate_call_cap": 1,
        "automatic_retry_limit": 0,
        "aggregate_time_cap_seconds": (
            PUBLICATION_SEMANTIC_ROW_COUNT
            * int(profile["timeout_seconds"])
        ),
        "storage_cap_bytes": 1_000_000_000,
        "raw_response_capture_required": True,
        "fresh_evidence_only_context": True,
        "partial_or_unknown_requires_owner_review": True,
        "objective_browser_results_are_immutable": True,
        "historical_v16_first_pass_count_preserved": 9,
        "historical_v16_failure_count_preserved": 3,
        "browser_control_allowed": False,
        "model_loaded": False,
        "gpu_or_remote_action": False,
        "semantic_alignment_executed": False,
        "h1_or_gold_access": False,
        "formal_evaluation_executed": False,
        "formal_quality_claimed": False,
        "rows": rows,
    }
    return {
        **root,
        "manifest_identity": _identity(
            _canonical(root),
            revision=PUBLICATION_SEMANTIC_MANIFEST_SCHEMA_VERSION,
        ),
    }


def prepare_phase5_publication_semantic_manifest(
    *,
    revalidation_root: Path,
    browser_root: Path,
) -> dict[str, object]:
    """Build the path-free twelve-row no-action semantic manifest."""

    return _build_manifest(
        revalidation_root=revalidation_root,
        browser_root=browser_root,
    )


def validate_phase5_publication_semantic_manifest(
    *,
    value: object,
    revalidation_root: Path,
    browser_root: Path,
) -> dict[str, object]:
    """Replay one manifest against the exact package and browser evidence."""

    data = _mapping(value, "publication semantic manifest")
    expected = _build_manifest(
        revalidation_root=revalidation_root,
        browser_root=browser_root,
    )
    if data != expected:
        raise Phase5PublicationSemanticError(
            "publication semantic manifest does not match replayed evidence"
        )
    return data


def write_phase5_publication_semantic_manifest(
    *,
    output_path: Path,
    revalidation_root: Path,
    browser_root: Path,
) -> dict[str, object]:
    """Replay-validate and write one new path-free manifest."""

    manifest = prepare_phase5_publication_semantic_manifest(
        revalidation_root=revalidation_root,
        browser_root=browser_root,
    )
    validate_phase5_publication_semantic_manifest(
        value=manifest,
        revalidation_root=revalidation_root,
        browser_root=browser_root,
    )
    _write_once(output_path, _canonical(manifest))
    return manifest


def load_phase5_publication_semantic_manifest(
    *,
    path: Path,
    revalidation_root: Path,
    browser_root: Path,
) -> dict[str, object]:
    """Load and replay one existing canonical manifest."""

    return validate_phase5_publication_semantic_manifest(
        value=_read_canonical_object(path, "publication semantic manifest"),
        revalidation_root=revalidation_root,
        browser_root=browser_root,
    )


def _expected_claim_boundary(profile_name: str) -> str:
    if profile_name == HIGH_GPU_PROFILE:
        return "bounded high-GPU semantic evaluation pending owner review"
    return "local low-GPU quantized smoke only"


def _validate_terminal_preparation(
    *,
    audit_root: Path,
    package_root: Path,
    result_root: Path,
    expected_prompt_identity: object,
    expected_model_input_identity: object,
) -> tuple[object, dict[str, object]]:
    profile = semantic_qwen_runtime_profile(HIGH_GPU_PROFILE)
    prepared = prepare_phase5_semantic_case(
        audit_root=audit_root,
        result_package_root=package_root,
        generator_model_identity=GENERATOR_MODEL_IDENTITY,
        profile_name=HIGH_GPU_PROFILE,
    )
    request = prepared["request"]
    if (
        _identity(
            prepared["prompt_raw"],
            revision=SEMANTIC_EVALUATOR_PROMPT_REVISION,
        )
        != expected_prompt_identity
        or _identity(
            prepared["model_input_raw"],
            revision=MODEL_INPUT_SCHEMA_VERSION,
        )
        != expected_model_input_identity
    ):
        raise Phase5PublicationSemanticError(
            "semantic terminal prompt or model-input identity drifted"
        )
    evidence_inventory = [item.to_dict() for item in request.evidence]
    expected_files = {
        "semantic_alignment_request.json": prepared["request_raw"],
        "evaluator_prompt.json": prepared["prompt_raw"],
        "model_input.json": prepared["model_input_raw"],
        "runtime_profile.json": _canonical(profile.to_dict()),
        "evidence_inventory.json": _canonical(evidence_inventory),
        "no_action_preparation.json": _canonical(
            prepared["no_action_bundle"]
        ),
    }
    for name, expected_raw in expected_files.items():
        path = result_root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.read_bytes() != expected_raw
        ):
            raise Phase5PublicationSemanticError(
                f"semantic terminal {name} drifted"
            )
    pre_call = _read_canonical_object(
        result_root / "pre_call_record.json",
        "semantic pre-call record",
    )
    expected_pre_call = {
        "schema_version": "req2web.phase5.semantic_pre_call.v1",
        "case_id": request.case_id,
        "request_identity": _runtime_identity(prepared["request_raw"]),
        "prompt_identity": _runtime_identity(prepared["prompt_raw"]),
        "model_input_identity": _runtime_identity(prepared["model_input_raw"]),
        "screenshot_identity": _runtime_identity(
            prepared["evidence_payloads"]["browser_screenshot"]
        ),
        "profile": profile.to_dict(),
        "model_generate_call_limit": 1,
        "automatic_retry_limit": 0,
        "raw_first": True,
    }
    if pre_call != expected_pre_call:
        raise Phase5PublicationSemanticError(
            "semantic pre-call record drifted"
        )
    return profile, prepared


def _validate_generation_started(
    *,
    result_root: Path,
    expected_count: int,
) -> None:
    generation_path = result_root / "generation_started.json"
    if (
        int(generation_path.is_file() and not generation_path.is_symlink())
        != expected_count
    ):
        raise Phase5PublicationSemanticError(
            "semantic generate-start accounting drifted"
        )
    if generation_path.is_file():
        generation = _read_canonical_object(
            generation_path,
            "semantic generation record",
        )
        if (
            generation.get("schema_version")
            != "req2web.phase5.semantic_generation_started.v1"
            or generation.get("model_generate_call_consumed") is not True
            or generation.get("automatic_retry") is not False
            or generation.get("retry_count") != 0
            or not isinstance(
                generation.get("started_epoch_seconds"),
                (int, float),
            )
            or isinstance(generation.get("started_epoch_seconds"), bool)
        ):
            raise Phase5PublicationSemanticError(
                "semantic generation record drifted"
            )


def _validate_failed_terminal_result(
    *,
    audit_root: Path,
    package_root: Path,
    result_root: Path,
    expected_prompt_identity: object,
    expected_model_input_identity: object,
) -> tuple[dict[str, object], dict[str, object] | None]:
    profile, prepared = _validate_terminal_preparation(
        audit_root=audit_root,
        package_root=package_root,
        result_root=result_root,
        expected_prompt_identity=expected_prompt_identity,
        expected_model_input_identity=expected_model_input_identity,
    )
    request = prepared["request"]
    summary = _read_canonical_object(
        result_root / "run_summary.json",
        "failed semantic run summary",
    )
    if (
        set(summary) != _RUN_SUMMARY_KEYS
        or summary.get("schema_version") != RUN_SUMMARY_SCHEMA_VERSION
        or summary.get("runtime_schema_version") != RUNTIME_SCHEMA_VERSION
        or summary.get("status") != "failed_closed"
        or summary.get("case_id") != request.case_id
        or summary.get("profile") != profile.to_dict()
        or type(summary.get("worker_exit_code")) is not int
        or type(summary.get("worker_timed_out")) is not bool
        or (
            summary.get("worker_error") is not None
            and not isinstance(summary.get("worker_error"), str)
        )
        or (
            summary.get("worker_receipt") is not None
            and not isinstance(summary.get("worker_receipt"), Mapping)
        )
        or type(summary.get("automatic_retry_count")) is not int
        or summary.get("automatic_retry_count") != 0
        or type(summary.get("raw_response_formed")) is not bool
        or type(summary.get("semantic_alignment_executed")) is not bool
        or type(summary.get("owner_review_required")) is not bool
        or summary.get("formal_quality_claimed") is not False
        or summary.get("finalized_without_model_call") is not False
        or summary.get("claim_boundary")
        != _expected_claim_boundary(HIGH_GPU_PROFILE)
        or type(summary.get("generate_started_count")) is not int
        or summary.get("generate_started_count") not in {0, 1}
        or (
            summary.get("raw_response_formed") is True
            and summary.get("generate_started_count") != 1
        )
    ):
        raise Phase5PublicationSemanticError(
            "failed semantic terminal summary drifted"
        )
    _validate_generation_started(
        result_root=result_root,
        expected_count=int(summary["generate_started_count"]),
    )
    if (
        summary["worker_timed_out"] is True
        and summary["worker_error"] != "worker_timeout"
    ):
        raise Phase5PublicationSemanticError(
            "failed semantic timeout disposition drifted"
        )
    raw_path = result_root / "raw_response.bin"
    raw_exists = raw_path.is_file() and not raw_path.is_symlink()
    if raw_exists is not summary["raw_response_formed"]:
        raise Phase5PublicationSemanticError(
            "failed semantic raw-response accounting drifted"
        )
    parsed: dict[str, object] | None = None
    parse_error: str | None = None
    if raw_exists:
        raw = raw_path.read_bytes()
        if summary.get("raw_response_identity") != _runtime_identity(raw):
            raise Phase5PublicationSemanticError(
                "failed semantic raw-response identity drifted"
            )
        try:
            semantic_result = parse_phase5_semantic_evaluator_raw_response(
                request=request,
                raw_response=raw,
                evaluator_model_identity=GENERATOR_MODEL_IDENTITY,
                generator_model_identity=GENERATOR_MODEL_IDENTITY,
            )
            parsed = semantic_result.to_dict(request)
        except (Phase5SemanticEvaluatorError, ValueError) as exc:
            parse_error = type(exc).__name__
    elif summary.get("raw_response_identity") is not None:
        raise Phase5PublicationSemanticError(
            "failed semantic raw-response absence drifted"
        )
    result_path = result_root / "semantic_alignment_result.json"
    if parsed is None:
        if result_path.exists() or summary.get("semantic_alignment_executed") is not False:
            raise Phase5PublicationSemanticError(
                "failed semantic parsed-result absence drifted"
            )
    else:
        if (
            result_path.is_symlink()
            or not result_path.is_file()
            or result_path.read_bytes() != _canonical(parsed)
            or summary.get("semantic_alignment_executed") is not True
        ):
            raise Phase5PublicationSemanticError(
                "failed semantic parsed-result binding drifted"
            )
    expected_review = bool(
        parsed is not None
        and any(
            row["verdict"] in {"partial", "unknown"}
            for row in parsed["verdicts"]
        )
    )
    if (
        summary.get("parse_error") != parse_error
        or summary.get("owner_review_required") is not expected_review
        or (parsed is not None and summary.get("worker_error") is None)
    ):
        raise Phase5PublicationSemanticError(
            "failed semantic terminal disposition drifted"
        )
    return summary, parsed


def _validate_terminal_result(
    *,
    audit_root: Path,
    package_root: Path,
    result_root: Path,
    expected_prompt_identity: object,
    expected_model_input_identity: object,
) -> tuple[dict[str, object], dict[str, object] | None]:
    if result_root.is_symlink() or not result_root.is_dir():
        raise Phase5PublicationSemanticError(
            "publication semantic terminal result is unavailable"
        )
    summary = _read_canonical_object(
        result_root / "run_summary.json",
        "publication semantic run summary",
    )
    if summary.get("status") == "semantic_alignment_complete_pending_owner_review":
        try:
            profile, prepared = _validate_terminal_preparation(
                audit_root=audit_root,
                package_root=package_root,
                result_root=result_root,
                expected_prompt_identity=expected_prompt_identity,
                expected_model_input_identity=expected_model_input_identity,
            )
            validated = validate_existing_phase5_semantic_qwen_result(
                audit_root=audit_root,
                result_package_root=package_root,
                result_root=result_root,
                profile_name=HIGH_GPU_PROFILE,
                generator_model_identity=GENERATOR_MODEL_IDENTITY,
            )
        except (Phase5SemanticQwenRuntimeError, ValueError, OSError) as exc:
            raise Phase5PublicationSemanticError(
                "publication semantic success replay failed"
            ) from exc
        parsed = _read_canonical_object(
            result_root / "semantic_alignment_result.json",
            "publication semantic parsed result",
        )
        request = prepared["request"]
        expected_review = any(
            row.get("verdict") in {"partial", "unknown"}
            for row in parsed.get("verdicts", [])
            if isinstance(row, Mapping)
        )
        if (
            set(validated) != _RUN_SUMMARY_KEYS
            or validated.get("schema_version") != RUN_SUMMARY_SCHEMA_VERSION
            or validated.get("runtime_schema_version") != RUNTIME_SCHEMA_VERSION
            or validated.get("case_id") != request.case_id
            or validated.get("profile") != profile.to_dict()
            or validated.get("worker_exit_code") != 0
            or validated.get("worker_timed_out") is not False
            or validated.get("worker_error") is not None
            or not isinstance(validated.get("worker_receipt"), Mapping)
            or validated.get("generate_started_count") != 1
            or validated.get("automatic_retry_count") != 0
            or validated.get("raw_response_formed") is not True
            or validated.get("parse_error") is not None
            or validated.get("semantic_alignment_executed") is not True
            or validated.get("owner_review_required") is not expected_review
            or validated.get("formal_quality_claimed") is not False
            or type(validated.get("finalized_without_model_call")) is not bool
            or validated.get("claim_boundary")
            != _expected_claim_boundary(HIGH_GPU_PROFILE)
        ):
            raise Phase5PublicationSemanticError(
                "publication semantic success terminal drifted"
            )
        _validate_generation_started(result_root=result_root, expected_count=1)
        return validated, parsed
    if summary.get("status") == "failed_closed":
        try:
            return _validate_failed_terminal_result(
                audit_root=audit_root,
                package_root=package_root,
                result_root=result_root,
                expected_prompt_identity=expected_prompt_identity,
                expected_model_input_identity=expected_model_input_identity,
            )
        except (
            Phase5SemanticEvaluatorError,
            Phase5SemanticQwenRuntimeError,
            ValueError,
            OSError,
        ) as exc:
            if isinstance(exc, Phase5PublicationSemanticError):
                raise
            raise Phase5PublicationSemanticError(
                "publication semantic failed-closed replay failed"
            ) from exc
    raise Phase5PublicationSemanticError(
        "publication semantic terminal status is unsupported"
    )


def _build_summary(
    *,
    manifest: Mapping[str, object],
    revalidation_root: Path,
    browser_root: Path,
    results_root: Path,
) -> dict[str, object]:
    validated_manifest = validate_phase5_publication_semantic_manifest(
        value=manifest,
        revalidation_root=revalidation_root,
        browser_root=browser_root,
    )
    try:
        source = Path(revalidation_root).resolve(strict=True)
        browser = Path(browser_root).resolve(strict=True)
        results = Path(results_root).resolve(strict=True)
    except OSError as exc:
        raise Phase5PublicationSemanticError(
            "publication semantic aggregation root is unavailable"
        ) from exc
    if results.is_symlink() or not results.is_dir():
        raise Phase5PublicationSemanticError(
            "publication semantic results root is invalid"
        )
    rows = validated_manifest["rows"]
    if not isinstance(rows, list):
        raise Phase5PublicationSemanticError(
            "publication semantic manifest rows drifted"
        )
    expected_leaves = {str(row["result_leaf"]) for row in rows}
    actual_entries = {entry.name for entry in results.iterdir()}
    if actual_entries != expected_leaves or any(
        not (results / leaf).is_dir() or (results / leaf).is_symlink()
        for leaf in expected_leaves
    ):
        raise Phase5PublicationSemanticError(
            "publication semantic result-root inventory drifted"
        )
    row_results: list[dict[str, object]] = []
    for expected_index, value in enumerate(rows, start=1):
        row = _mapping(value, f"semantic manifest row {expected_index}")
        if row.get("execution_index") != expected_index:
            raise Phase5PublicationSemanticError(
                "publication semantic execution order drifted"
            )
        row_id = str(row["row_id"])
        audit_root = browser / "cases" / f"{expected_index:02d}"
        package_root = source / "packages" / _result_leaf(expected_index, row_id)
        terminal_root = results / str(row["result_leaf"])
        summary, parsed = _validate_terminal_result(
            audit_root=audit_root,
            package_root=package_root,
            result_root=terminal_root,
            expected_prompt_identity=row.get("prompt_identity"),
            expected_model_input_identity=row.get("model_input_identity"),
        )
        if summary.get("case_id") != row.get("case_id"):
            raise Phase5PublicationSemanticError(
                f"semantic terminal case binding drifted for row {expected_index}"
            )
        accepted = summary["status"] == "semantic_alignment_complete_pending_owner_review"
        verdict_counts = {name: 0 for name in _VERDICTS}
        criterion_count = 0
        semantic_result_identity = (
            _identity(
                _canonical(parsed),
                revision=SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
            )
            if parsed is not None
            else None
        )
        if accepted:
            if parsed is None or parsed.get("request_sha256") != row.get(
                "semantic_request_sha256"
            ):
                raise Phase5PublicationSemanticError(
                    f"semantic result request binding drifted for row {expected_index}"
                )
            verdicts = parsed.get("verdicts")
            if not isinstance(verdicts, list) or len(verdicts) != row.get(
                "review_item_count"
            ):
                raise Phase5PublicationSemanticError(
                    f"semantic verdict coverage drifted for row {expected_index}"
                )
            for verdict in verdicts:
                item = _mapping(verdict, "semantic verdict")
                name = item.get("verdict")
                if name not in verdict_counts:
                    raise Phase5PublicationSemanticError(
                        "semantic verdict value drifted"
                    )
                verdict_counts[str(name)] += 1
            criterion_count = len(verdicts)
        run_summary_raw = (terminal_root / "run_summary.json").read_bytes()
        row_results.append(
            {
                "execution_index": expected_index,
                "row_id": row_id,
                "case_id": row["case_id"],
                "condition_id": row["condition_id"],
                "result_leaf": row["result_leaf"],
                "terminal_status": summary["status"],
                "semantic_result_accepted": accepted,
                "semantic_result_identity": semantic_result_identity,
                "raw_response_identity": copy.deepcopy(
                    summary["raw_response_identity"]
                ),
                "run_summary_identity": _identity(
                    run_summary_raw,
                    revision=str(summary["schema_version"]),
                ),
                "generate_started_count": summary["generate_started_count"],
                "automatic_retry_count": summary["automatic_retry_count"],
                "raw_response_formed": summary["raw_response_formed"],
                "worker_error": summary["worker_error"],
                "parse_error": summary["parse_error"],
                "criterion_count": criterion_count,
                "verdict_counts": verdict_counts,
                "owner_review_required": summary["owner_review_required"],
            }
        )
    counts = {
        "row_count": PUBLICATION_SEMANTIC_ROW_COUNT,
        "terminal_row_count": len(row_results),
        "accepted_semantic_result_count": sum(
            row["semantic_result_accepted"] is True for row in row_results
        ),
        "failed_closed_count": sum(
            row["terminal_status"] == "failed_closed" for row in row_results
        ),
        "generate_started_count": sum(
            int(row["generate_started_count"]) for row in row_results
        ),
        "automatic_retry_count": sum(
            int(row["automatic_retry_count"]) for row in row_results
        ),
        "raw_response_formed_count": sum(
            row["raw_response_formed"] is True for row in row_results
        ),
        "accepted_criterion_count": sum(
            int(row["criterion_count"]) for row in row_results
        ),
        "supported_count": sum(
            int(row["verdict_counts"]["supported"]) for row in row_results
        ),
        "violated_count": sum(
            int(row["verdict_counts"]["violated"]) for row in row_results
        ),
        "partial_count": sum(
            int(row["verdict_counts"]["partial"]) for row in row_results
        ),
        "unknown_count": sum(
            int(row["verdict_counts"]["unknown"]) for row in row_results
        ),
        "owner_review_required_row_count": sum(
            row["owner_review_required"] is True for row in row_results
        ),
    }
    if (
        counts["generate_started_count"]
        > PUBLICATION_SEMANTIC_GENERATE_CALL_CAP
        or counts["automatic_retry_count"] != 0
    ):
        raise Phase5PublicationSemanticError(
            "publication semantic call accounting exceeded the frozen boundary"
        )
    root = {
        "schema_version": PUBLICATION_SEMANTIC_SUMMARY_SCHEMA_VERSION,
        "run_id": validated_manifest["run_id"],
        "source_manifest_identity": copy.deepcopy(
            validated_manifest["manifest_identity"]
        ),
        "source_browser_summary_identity": copy.deepcopy(
            validated_manifest["source_browser_summary_identity"]
        ),
        "row_results": row_results,
        "counts": counts,
        "objective_browser_pass_count_preserved": 12,
        "historical_v16_first_pass_count_preserved": 9,
        "historical_v16_failure_count_preserved": 3,
        "semantic_alignment_executed": (
            counts["accepted_semantic_result_count"] > 0
        ),
        "semantic_attempted": counts["generate_started_count"] > 0,
        "h1_or_gold_access": False,
        "formal_evaluation_executed": False,
        "formal_quality_claimed": False,
        "status": (
            "semantic_evaluation_complete_with_failed_closed_rows"
            if counts["failed_closed_count"] > 0
            else (
                "semantic_evaluation_complete_pending_owner_review"
                if counts["owner_review_required_row_count"] > 0
                else "semantic_evaluation_complete_no_owner_review_required"
            )
        ),
        "claim_boundary": (
            "descriptive semantic-alignment engineering evidence for twelve "
            "project-authored publication rows; objective browser facts and "
            "historical first-pass accounting remain unchanged; H1/gold, "
            "formal evaluation, and formal quality remain closed"
        ),
    }
    return {
        **root,
        "summary_identity": _identity(
            _canonical(root),
            revision=PUBLICATION_SEMANTIC_SUMMARY_SCHEMA_VERSION,
        ),
    }


def aggregate_phase5_publication_semantic_results(
    *,
    manifest: Mapping[str, object],
    revalidation_root: Path,
    browser_root: Path,
    results_root: Path,
) -> dict[str, object]:
    """Validate all twelve terminal rows and build a separate summary."""

    return _build_summary(
        manifest=manifest,
        revalidation_root=revalidation_root,
        browser_root=browser_root,
        results_root=results_root,
    )


def validate_phase5_publication_semantic_summary(
    *,
    value: object,
    manifest: Mapping[str, object],
    revalidation_root: Path,
    browser_root: Path,
    results_root: Path,
) -> dict[str, object]:
    """Replay one aggregate summary from the exact terminal results."""

    data = _mapping(value, "publication semantic summary")
    expected = _build_summary(
        manifest=manifest,
        revalidation_root=revalidation_root,
        browser_root=browser_root,
        results_root=results_root,
    )
    if data != expected:
        raise Phase5PublicationSemanticError(
            "publication semantic summary does not match replayed results"
        )
    return data


def write_phase5_publication_semantic_summary(
    *,
    output_path: Path,
    manifest: Mapping[str, object],
    revalidation_root: Path,
    browser_root: Path,
    results_root: Path,
) -> dict[str, object]:
    """Replay and write one new aggregate semantic summary."""

    summary = aggregate_phase5_publication_semantic_results(
        manifest=manifest,
        revalidation_root=revalidation_root,
        browser_root=browser_root,
        results_root=results_root,
    )
    validate_phase5_publication_semantic_summary(
        value=summary,
        manifest=manifest,
        revalidation_root=revalidation_root,
        browser_root=browser_root,
        results_root=results_root,
    )
    _write_once(output_path, _canonical(summary))
    return summary


__all__ = [
    "GENERATOR_MODEL_IDENTITY",
    "PUBLICATION_SEMANTIC_CRITERION_COUNT",
    "PUBLICATION_SEMANTIC_GENERATE_CALL_CAP",
    "PUBLICATION_SEMANTIC_MANIFEST_SCHEMA_VERSION",
    "PUBLICATION_SEMANTIC_ROW_COUNT",
    "PUBLICATION_SEMANTIC_STATUS",
    "PUBLICATION_SEMANTIC_SUMMARY_SCHEMA_VERSION",
    "Phase5PublicationSemanticError",
    "aggregate_phase5_publication_semantic_results",
    "load_phase5_publication_semantic_manifest",
    "prepare_phase5_publication_semantic_manifest",
    "validate_phase5_publication_semantic_manifest",
    "validate_phase5_publication_semantic_summary",
    "write_phase5_publication_semantic_manifest",
    "write_phase5_publication_semantic_summary",
]
