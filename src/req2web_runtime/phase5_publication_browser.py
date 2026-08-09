"""Objective browser evidence for Phase 5 publication engineering packages.

This runner binds the twelve exact ResultPackages produced by the immutable
English v16 action plus the separate zero-model v17 policy revalidation to the
shared Playwright acceptance authority.  It does not invoke a semantic model,
retry browser actions, repair a package, or relabel historical first-pass
results.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

from req2web_runtime.phase4_browser_acceptance import (
    BackendFactory,
    run_real_browser_case_audit,
    validate_real_browser_case_audit,
    validate_result_package_binding,
)


EVIDENCE_SCOPE = "phase5_publication_engineering"
ROW_COUNT = 12
SOURCE_SUMMARY_SCHEMA_VERSION = (
    "req2web.phase5.publication_f4_policy_revalidation.v1.summary"
)
BROWSER_SUMMARY_SCHEMA_VERSION = (
    "req2web.phase5.publication_browser_engineering.v1.summary"
)


class Phase5PublicationBrowserError(ValueError):
    """Raised when publication browser evidence cannot be bound safely."""


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
        raise Phase5PublicationBrowserError(
            "browser evidence is not canonical JSON"
        ) from exc


def _identity(value: object, *, revision: str) -> dict[str, object]:
    raw = _canonical(value)
    return {
        "identity_kind": "canonical_json",
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": revision,
    }


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase5PublicationBrowserError(f"{name} must be an object")
    return copy.deepcopy(dict(value))


def _identity_record(value: object, name: str) -> dict[str, object]:
    data = _mapping(value, name)
    keys = ("identity_kind", "sha256", "byte_length", "revision")
    if set(data) != set(keys):
        raise Phase5PublicationBrowserError(f"{name} shape drifted")
    if (
        not isinstance(data["identity_kind"], str)
        or not isinstance(data["sha256"], str)
        or not data["sha256"].startswith("sha256:")
        or len(data["sha256"]) != 71
        or not isinstance(data["byte_length"], int)
        or isinstance(data["byte_length"], bool)
        or data["byte_length"] <= 0
        or not isinstance(data["revision"], str)
        or not data["revision"]
    ):
        raise Phase5PublicationBrowserError(f"{name} is invalid")
    return data


def _load_json(path: Path, name: str) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5PublicationBrowserError(
            f"{name} is not valid UTF-8 JSON"
        ) from exc
    return _mapping(value, name)


def _write_once(path: Path, raw: bytes) -> None:
    target = Path(path).resolve(strict=False)
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise Phase5PublicationBrowserError("browser output path drifted")
        if target.read_bytes() != raw:
            raise Phase5PublicationBrowserError("browser output bytes drifted")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _validate_amended_summary(value: object) -> dict[str, object]:
    data = _mapping(value, "amended policy summary")
    expected = {
        "schema_version",
        "run_id",
        "historical_result_return",
        "historical_prompt_authority_sha256",
        "amended_prompt_authority_identity",
        "row_results",
        "counts",
        "status",
        "historical_result_overwritten",
        "claim_boundary",
        "summary_identity",
    }
    if set(data) != expected:
        raise Phase5PublicationBrowserError(
            "amended policy summary exact keys drifted"
        )
    root = {key: item for key, item in data.items() if key != "summary_identity"}
    if data["summary_identity"] != _identity(
        root,
        revision=SOURCE_SUMMARY_SCHEMA_VERSION,
    ):
        raise Phase5PublicationBrowserError(
            "amended policy summary identity drifted"
        )
    counts = _mapping(data["counts"], "amended policy counts")
    rows = data["row_results"]
    if (
        data["schema_version"] != SOURCE_SUMMARY_SCHEMA_VERSION
        or data["status"]
        != "zero_model_policy_revalidation_complete_pending_browser"
        or data["historical_result_overwritten"] is not False
        or counts.get("row_count") != ROW_COUNT
        or counts.get("historical_downstream_first_pass_success_count") != 9
        or counts.get("f4_policy_revalidated_row_count") != 3
        or counts.get("delivery_evidence_available_count") != ROW_COUNT
        or counts.get("model_generate_calls_added") != 0
        or counts.get("automatic_retry_count_added") != 0
        or counts.get("normalization_count_added") != 0
        or counts.get("repair_count_added") != 0
        or counts.get("fallback_count_added") != 0
        or not isinstance(rows, list)
        or len(rows) != ROW_COUNT
    ):
        raise Phase5PublicationBrowserError(
            "amended policy summary scope drifted"
        )
    return data


def _source_identity(row: Mapping[str, object]) -> dict[str, object]:
    disposition = row.get("disposition")
    historical = row.get("historical_downstream_first_pass_success")
    revalidated = row.get("revalidation_receipt_identity")
    if disposition == "historical_direct_first_pass_success":
        if historical is not True or revalidated is not None:
            raise Phase5PublicationBrowserError(
                "historical browser row provenance drifted"
            )
        return _identity_record(
            row.get("historical_row_summary_identity"),
            "historical row summary identity",
        )
    if disposition == "validated_after_f4_policy_revalidation":
        if historical is not False:
            raise Phase5PublicationBrowserError(
                "revalidated browser row provenance drifted"
            )
        return _identity_record(
            revalidated,
            "policy revalidation receipt identity",
        )
    raise Phase5PublicationBrowserError(
        "browser row disposition is unsupported"
    )


def validate_phase5_publication_browser_summary(
    value: object,
) -> dict[str, object]:
    """Validate one aggregate summary without executing a browser."""

    data = _mapping(value, "publication browser summary")
    expected = {
        "schema_version",
        "evidence_scope",
        "run_id",
        "source_amended_summary_identity",
        "row_results",
        "counts",
        "all_objective_browser_checks_pass",
        "semantic_alignment_executed",
        "historical_v16_first_pass_count_preserved",
        "historical_v16_failure_count_preserved",
        "status",
        "claim_boundary",
        "summary_identity",
    }
    if set(data) != expected:
        raise Phase5PublicationBrowserError(
            "publication browser summary exact keys drifted"
        )
    root = {key: item for key, item in data.items() if key != "summary_identity"}
    if data["summary_identity"] != _identity(
        root,
        revision=BROWSER_SUMMARY_SCHEMA_VERSION,
    ):
        raise Phase5PublicationBrowserError(
            "publication browser summary identity drifted"
        )
    _identity_record(
        data["source_amended_summary_identity"],
        "source amended summary identity",
    )
    rows = data["row_results"]
    counts = _mapping(data["counts"], "publication browser counts")
    if (
        data["schema_version"] != BROWSER_SUMMARY_SCHEMA_VERSION
        or data["evidence_scope"] != EVIDENCE_SCOPE
        or not isinstance(data["run_id"], str)
        or not data["run_id"]
        or not isinstance(rows, list)
        or len(rows) != ROW_COUNT
        or counts.get("row_count") != ROW_COUNT
        or data["semantic_alignment_executed"] is not False
        or data["historical_v16_first_pass_count_preserved"] != 9
        or data["historical_v16_failure_count_preserved"] != 3
    ):
        raise Phase5PublicationBrowserError(
            "publication browser summary scope drifted"
        )
    row_keys = {
        "execution_index",
        "row_id",
        "case_id",
        "condition_id",
        "source_disposition",
        "source_identity",
        "result_package",
        "browser_audit_identity",
        "browser_status",
        "browser_execution_status",
        "page_spec_conformance_status",
        "semantic_alignment_status",
        "semantic_alignment_disposition",
        "real_browser_executed",
        "automation_reliable",
    }
    for expected_index, value_row in enumerate(rows, start=1):
        row = _mapping(value_row, f"browser row {expected_index}")
        if (
            set(row) != row_keys
            or row.get("execution_index") != expected_index
            or row.get("browser_status") not in {"pass", "fail", "unknown"}
            or row.get("browser_execution_status")
            not in {"pass", "fail", "unknown"}
            or row.get("page_spec_conformance_status")
            not in {"pass", "fail", "unknown"}
            or row.get("semantic_alignment_status") != "not_executed"
            or row.get("semantic_alignment_disposition")
            not in {"needs_semantic_review", "blocked_by_objective_failure"}
            or not isinstance(row.get("real_browser_executed"), bool)
            or not isinstance(row.get("automation_reliable"), bool)
        ):
            raise Phase5PublicationBrowserError(
                f"browser row {expected_index} scope drifted"
            )
        _identity_record(row["source_identity"], "browser row source identity")
        _identity_record(
            row["browser_audit_identity"],
            "browser row audit identity",
        )

    expected_counts = {
        "row_count": ROW_COUNT,
        "real_browser_executed_count": sum(
            row["real_browser_executed"] is True for row in rows
        ),
        "automation_reliable_count": sum(
            row["automation_reliable"] is True for row in rows
        ),
        "browser_execution_pass_count": sum(
            row["browser_execution_status"] == "pass" for row in rows
        ),
        "browser_execution_fail_count": sum(
            row["browser_execution_status"] == "fail" for row in rows
        ),
        "browser_execution_unknown_count": sum(
            row["browser_execution_status"] == "unknown" for row in rows
        ),
        "page_spec_conformance_pass_count": sum(
            row["page_spec_conformance_status"] == "pass" for row in rows
        ),
        "page_spec_conformance_fail_count": sum(
            row["page_spec_conformance_status"] == "fail" for row in rows
        ),
        "page_spec_conformance_unknown_count": sum(
            row["page_spec_conformance_status"] == "unknown" for row in rows
        ),
        "semantic_alignment_executed_count": 0,
        "model_generate_calls_added": 0,
        "automatic_retry_count_added": 0,
        "repair_count_added": 0,
    }
    if counts != expected_counts:
        raise Phase5PublicationBrowserError(
            "publication browser counts drifted"
        )
    expected_pass = bool(
        counts["real_browser_executed_count"] == ROW_COUNT
        and counts["automation_reliable_count"] == ROW_COUNT
        and counts["browser_execution_pass_count"] == ROW_COUNT
        and counts["page_spec_conformance_pass_count"] == ROW_COUNT
    )
    expected_status = (
        "objective_browser_audit_complete_pass"
        if expected_pass
        else "objective_browser_audit_complete_with_failures"
    )
    if (
        data["all_objective_browser_checks_pass"] is not expected_pass
        or data["status"] != expected_status
    ):
        raise Phase5PublicationBrowserError(
            "publication browser aggregate status drifted"
        )
    return data


def replay_phase5_publication_browser_result(
    *,
    revalidation_root: Path,
    browser_root: Path,
) -> dict[str, object]:
    """Pure-read replay of one completed publication browser result."""

    source_root = Path(revalidation_root).resolve(strict=True)
    result_root = Path(browser_root).resolve(strict=True)
    amended = _validate_amended_summary(
        _load_json(source_root / "amended_summary.json", "amended policy summary")
    )
    summary = validate_phase5_publication_browser_summary(
        _load_json(result_root / "browser_summary.json", "browser summary")
    )
    if (
        summary["run_id"] != amended["run_id"]
        or summary["source_amended_summary_identity"]
        != amended["summary_identity"]
    ):
        raise Phase5PublicationBrowserError(
            "browser summary source binding drifted"
        )
    source_rows = amended["row_results"]
    browser_rows = summary["row_results"]
    if not isinstance(source_rows, list) or not isinstance(browser_rows, list):
        raise Phase5PublicationBrowserError("browser replay rows drifted")
    for index, (source_value, browser_value) in enumerate(
        zip(source_rows, browser_rows, strict=True),
        start=1,
    ):
        source_row = _mapping(source_value, f"source row {index}")
        browser_row = _mapping(browser_value, f"browser row {index}")
        expected_source_identity = _source_identity(source_row)
        package_root = (
            source_root
            / "packages"
            / f"{index:02d}-{source_row['row_id']}"
        )
        package_binding = validate_result_package_binding(package_root)
        audit = validate_real_browser_case_audit(
            _load_json(
                result_root / "cases" / f"{index:02d}" / "case_browser_audit.json",
                f"case {index} browser audit",
            )
        )
        if (
            browser_row["row_id"] != source_row["row_id"]
            or browser_row["case_id"] != source_row["case_id"]
            or browser_row["condition_id"] != source_row["condition_id"]
            or browser_row["source_disposition"] != source_row["disposition"]
            or browser_row["source_identity"] != expected_source_identity
            or browser_row["result_package"] != package_binding
            or audit["source_case_summary_identity"] != expected_source_identity
            or audit["result_package"] != package_binding
            or browser_row["browser_audit_identity"] != audit["audit_identity"]
            or browser_row["browser_status"] != audit["browser_status"]
            or browser_row["browser_execution_status"]
            != audit["browser_execution_status"]
            or browser_row["page_spec_conformance_status"]
            != audit["page_spec_conformance_status"]
            or browser_row["semantic_alignment_status"]
            != audit["semantic_alignment"]["status"]
            or browser_row["real_browser_executed"]
            != audit["real_browser_executed"]
            or browser_row["automation_reliable"]
            != audit["automation_reliable"]
        ):
            raise Phase5PublicationBrowserError(
                f"case {index} browser replay binding drifted"
            )
    return summary


def run_phase5_publication_browser(
    *,
    revalidation_root: Path,
    output_root: Path,
    confirm_real_browser: bool,
    timeout_ms: int = 5_000,
    backend_factory: BackendFactory | None = None,
) -> dict[str, object]:
    """Execute objective browser checks for all twelve exact packages."""

    if confirm_real_browser is not True:
        raise Phase5PublicationBrowserError(
            "explicit real-browser confirmation is required"
        )
    if (
        not isinstance(timeout_ms, int)
        or isinstance(timeout_ms, bool)
        or timeout_ms <= 0
    ):
        raise Phase5PublicationBrowserError("browser timeout must be positive")
    source_root = Path(revalidation_root).resolve(strict=True)
    summary = _validate_amended_summary(
        _load_json(source_root / "amended_summary.json", "amended policy summary")
    )
    destination = Path(output_root).resolve(strict=False)
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir() or any(
            destination.iterdir()
        ):
            raise Phase5PublicationBrowserError(
                "browser output root must be new or empty"
            )
    else:
        destination.mkdir(parents=True, exist_ok=False)

    row_results: list[dict[str, object]] = []
    source_rows = summary["row_results"]
    if not isinstance(source_rows, list):
        raise Phase5PublicationBrowserError("amended browser rows drifted")
    for expected_index, value in enumerate(source_rows, start=1):
        row = _mapping(value, f"amended row {expected_index}")
        if (
            row.get("execution_index") != expected_index
            or not isinstance(row.get("row_id"), str)
            or not row["row_id"]
            or not isinstance(row.get("case_id"), str)
            or not row["case_id"]
            or not isinstance(row.get("condition_id"), str)
            or row.get("delivery_evidence_available") is not True
        ):
            raise Phase5PublicationBrowserError(
                f"amended row {expected_index} binding drifted"
            )
        package_root = (
            source_root
            / "packages"
            / f"{expected_index:02d}-{row['row_id']}"
        )
        package_binding = validate_result_package_binding(package_root)
        if package_binding != row.get("result_package"):
            raise Phase5PublicationBrowserError(
                f"row {expected_index} package binding drifted"
            )
        source_identity = _source_identity(row)
        audit = run_real_browser_case_audit(
            package_root=package_root,
            output_root=destination / "cases" / f"{expected_index:02d}",
            run_id=str(summary["run_id"]),
            case_index=expected_index,
            case_id=str(row["case_id"]),
            evidence_scope=EVIDENCE_SCOPE,
            source_case_summary_identity=source_identity,
            timeout_ms=timeout_ms,
            backend_factory=backend_factory,
        )
        if (
            audit.get("run_id") != summary["run_id"]
            or audit.get("case_index") != expected_index
            or audit.get("case_id") != row["case_id"]
            or audit.get("evidence_scope") != EVIDENCE_SCOPE
            or audit.get("source_case_summary_identity") != source_identity
            or audit.get("result_package") != package_binding
            or audit.get("semantic_alignment", {}).get("status")
            != "not_executed"
            or audit.get("semantic_alignment", {}).get("model_generate_calls")
            != 0
        ):
            raise Phase5PublicationBrowserError(
                f"row {expected_index} browser audit binding drifted"
            )
        row_results.append(
            {
                "execution_index": expected_index,
                "row_id": row["row_id"],
                "case_id": row["case_id"],
                "condition_id": row["condition_id"],
                "source_disposition": row["disposition"],
                "source_identity": source_identity,
                "result_package": package_binding,
                "browser_audit_identity": copy.deepcopy(
                    audit["audit_identity"]
                ),
                "browser_status": audit["browser_status"],
                "browser_execution_status": audit[
                    "browser_execution_status"
                ],
                "page_spec_conformance_status": audit[
                    "page_spec_conformance_status"
                ],
                "semantic_alignment_status": audit[
                    "semantic_alignment"
                ]["status"],
                "semantic_alignment_disposition": audit[
                    "semantic_alignment"
                ]["disposition"],
                "real_browser_executed": audit["real_browser_executed"],
                "automation_reliable": audit["automation_reliable"],
            }
        )

    counts = {
        "row_count": ROW_COUNT,
        "real_browser_executed_count": sum(
            row["real_browser_executed"] is True for row in row_results
        ),
        "automation_reliable_count": sum(
            row["automation_reliable"] is True for row in row_results
        ),
        "browser_execution_pass_count": sum(
            row["browser_execution_status"] == "pass" for row in row_results
        ),
        "browser_execution_fail_count": sum(
            row["browser_execution_status"] == "fail" for row in row_results
        ),
        "browser_execution_unknown_count": sum(
            row["browser_execution_status"] == "unknown" for row in row_results
        ),
        "page_spec_conformance_pass_count": sum(
            row["page_spec_conformance_status"] == "pass"
            for row in row_results
        ),
        "page_spec_conformance_fail_count": sum(
            row["page_spec_conformance_status"] == "fail"
            for row in row_results
        ),
        "page_spec_conformance_unknown_count": sum(
            row["page_spec_conformance_status"] == "unknown"
            for row in row_results
        ),
        "semantic_alignment_executed_count": sum(
            row["semantic_alignment_status"] != "not_executed"
            for row in row_results
        ),
        "model_generate_calls_added": 0,
        "automatic_retry_count_added": 0,
        "repair_count_added": 0,
    }
    all_objective_pass = bool(
        counts["real_browser_executed_count"] == ROW_COUNT
        and counts["automation_reliable_count"] == ROW_COUNT
        and counts["browser_execution_pass_count"] == ROW_COUNT
        and counts["page_spec_conformance_pass_count"] == ROW_COUNT
    )
    root = {
        "schema_version": BROWSER_SUMMARY_SCHEMA_VERSION,
        "evidence_scope": EVIDENCE_SCOPE,
        "run_id": summary["run_id"],
        "source_amended_summary_identity": copy.deepcopy(
            summary["summary_identity"]
        ),
        "row_results": row_results,
        "counts": counts,
        "all_objective_browser_checks_pass": all_objective_pass,
        "semantic_alignment_executed": False,
        "historical_v16_first_pass_count_preserved": 9,
        "historical_v16_failure_count_preserved": 3,
        "status": (
            "objective_browser_audit_complete_pass"
            if all_objective_pass
            else "objective_browser_audit_complete_with_failures"
        ),
        "claim_boundary": (
            "objective local-browser engineering evidence for twelve exact "
            "project-authored packages; semantic alignment, H1/gold, formal "
            "evaluation, and formal quality remain unexecuted"
        ),
    }
    result = {
        **root,
        "summary_identity": _identity(
            root,
            revision=BROWSER_SUMMARY_SCHEMA_VERSION,
        ),
    }
    validate_phase5_publication_browser_summary(result)
    _write_once(destination / "browser_summary.json", _canonical(result))
    return result


__all__ = [
    "BROWSER_SUMMARY_SCHEMA_VERSION",
    "EVIDENCE_SCOPE",
    "Phase5PublicationBrowserError",
    "replay_phase5_publication_browser_result",
    "run_phase5_publication_browser",
    "validate_phase5_publication_browser_summary",
]
