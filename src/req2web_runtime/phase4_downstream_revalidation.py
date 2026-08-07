"""Zero-model revalidation for the Phase 4 cases affected by status drift.

The original case summaries remain immutable.  This module binds those
historical summaries to already-existing ResultPackage and real-browser
evidence, then emits separate receipts and an amended aggregate.  It never
changes raw-model, first-pass, retry, repair, normalization, or fallback
accounting.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

from req2web_runtime.phase4_browser_acceptance import (
    validate_real_browser_case_audit,
    validate_result_package_binding,
)


CASE_RECEIPT_SCHEMA_VERSION = (
    "req2web.phase4.downstream_runtime_status_revalidation.v1.case_receipt"
)
AMENDED_SUMMARY_SCHEMA_VERSION = (
    "req2web.phase4.downstream_runtime_status_revalidation.v1.summary"
)
FLOW_SUMMARY_SCHEMA_VERSION = (
    "req2web.phase4.canonical_full_flow.v1.summary"
)
CASE_SUMMARY_SCHEMA_VERSION = (
    "req2web.phase4.canonical_full_flow.v1.case_summary"
)
FINAL_BROWSER_SUMMARY_SCHEMA_VERSION = (
    "req2web.phase4.real_browser_final_summary.v1"
)
REVALIDATED_CASE_INDICES = (1, 2, 3)
NODE_ORDER = ("F1", "F2", "F3", "F4")


class Phase4DownstreamRevalidationError(ValueError):
    """Raised when immutable Phase 4 evidence cannot be rebound safely."""


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
        raise Phase4DownstreamRevalidationError(
            "revalidation evidence is not canonical JSON"
        ) from exc


def _identity(
    value: object,
    *,
    revision: str,
    identity_kind: str = "canonical_json",
) -> dict[str, object]:
    raw = value if type(value) is bytes else _canonical(value)
    return {
        "identity_kind": identity_kind,
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": revision,
    }


def _mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4DownstreamRevalidationError(f"{name} must be an object")
    return copy.deepcopy(dict(value))


def _load_json(path: Path, name: str) -> dict[str, object]:
    candidate = Path(path).resolve(strict=True)
    if candidate.is_symlink() or not candidate.is_file():
        raise Phase4DownstreamRevalidationError(f"{name} must be a real file")
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase4DownstreamRevalidationError(
            f"{name} is not valid JSON"
        ) from exc
    return _mapping(value, name)


def _write_once(path: Path, value: object) -> None:
    raw = _canonical(value)
    target = Path(path).resolve(strict=False)
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise Phase4DownstreamRevalidationError(
                f"revalidation artifact path drifted: {target.name}"
            )
        if target.read_bytes() != raw:
            raise Phase4DownstreamRevalidationError(
                f"revalidation artifact content drifted: {target.name}"
            )
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _validate_embedded_identity(
    value: Mapping[str, object],
    *,
    identity_key: str,
    revision: str,
    name: str,
) -> dict[str, object]:
    data = copy.deepcopy(dict(value))
    actual = _mapping(data.get(identity_key), f"{name} identity")
    root = {key: item for key, item in data.items() if key != identity_key}
    expected = _identity(root, revision=revision)
    if actual != expected:
        raise Phase4DownstreamRevalidationError(f"{name} identity drifted")
    return data


def _validate_flow_summary(value: Mapping[str, object]) -> dict[str, object]:
    data = _validate_embedded_identity(
        value,
        identity_key="summary_identity",
        revision=FLOW_SUMMARY_SCHEMA_VERSION,
        name="canonical flow summary",
    )
    rows = data.get("case_summaries")
    aggregate = data.get("aggregate")
    if (
        data.get("schema_version") != FLOW_SUMMARY_SCHEMA_VERSION
        or data.get("completed_case_count") != 10
        or not isinstance(rows, list)
        or len(rows) != 10
        or not isinstance(aggregate, Mapping)
    ):
        raise Phase4DownstreamRevalidationError(
            "canonical flow summary root drifted"
        )
    for index, row in enumerate(rows, start=1):
        case = _mapping(row, f"case {index} summary")
        _validate_embedded_identity(
            case,
            identity_key="case_summary_identity",
            revision=CASE_SUMMARY_SCHEMA_VERSION,
            name=f"case {index} summary",
        )
        if case.get("case_index") != index:
            raise Phase4DownstreamRevalidationError(
                "canonical flow case order drifted"
            )
    return data


def _validate_browser_summary(
    value: Mapping[str, object],
    *,
    run_id: str,
) -> dict[str, object]:
    data = _validate_embedded_identity(
        value,
        identity_key="summary_identity",
        revision=FINAL_BROWSER_SUMMARY_SCHEMA_VERSION,
        name="final browser summary",
    )
    rows = data.get("case_rows")
    if (
        data.get("schema_version") != FINAL_BROWSER_SUMMARY_SCHEMA_VERSION
        or data.get("run_id") != run_id
        or not isinstance(rows, list)
        or len(rows) != 10
    ):
        raise Phase4DownstreamRevalidationError(
            "final browser summary root drifted"
        )
    return data


def _validate_result_summary(package_root: Path) -> dict[str, object]:
    result = _load_json(
        package_root / "result_summary.json",
        "result package summary",
    )
    gate = _mapping(result.get("quality_gate"), "result package quality gate")
    if (
        result.get("schema_version") != "req2web.result.summary.v1"
        or gate.get("passed") is not True
        or gate.get("fail") != 0
    ):
        raise Phase4DownstreamRevalidationError(
            "result package quality gate did not pass"
        )
    return result


def _case_receipt(
    *,
    flow_case: Mapping[str, object],
    copied_case: Mapping[str, object],
    package_root: Path,
    audit_path: Path,
    browser_row: Mapping[str, object],
    run_id: str,
) -> dict[str, object]:
    case = _validate_embedded_identity(
        copied_case,
        identity_key="case_summary_identity",
        revision=CASE_SUMMARY_SCHEMA_VERSION,
        name="copied historical case summary",
    )
    if _canonical(case) != _canonical(flow_case):
        raise Phase4DownstreamRevalidationError(
            "copied historical case summary drifted from flow summary"
        )
    index = case["case_index"]
    case_id = case["case_id"]
    calls = _mapping(
        case.get("per_node_generate_started"),
        f"case {index} generate accounting",
    )
    passes = _mapping(
        case.get("per_node_raw_contract_pass"),
        f"case {index} raw-contract accounting",
    )
    if (
        tuple(calls) != NODE_ORDER
        or tuple(passes) != NODE_ORDER
        or any(calls[node] != 1 for node in NODE_ORDER)
        or any(passes[node] is not True for node in NODE_ORDER)
        or case.get("all_four_nodes_executed") is not True
        or case.get("all_four_nodes_raw_contract_pass") is not True
        or case.get("status") != "failed_closed"
        or case.get("failure") is not None
        or case.get("downstream_status") is not None
        or case.get("downstream_delivery_success") is not False
        or case.get("downstream_first_pass_success") is not False
        or case.get("scripted_acceptance_executed") is not False
    ):
        raise Phase4DownstreamRevalidationError(
            f"case {index} is not the recognized runtime-status drift shape"
        )

    package = validate_result_package_binding(package_root)
    result_summary = _validate_result_summary(package_root)
    audit = validate_real_browser_case_audit(
        _load_json(audit_path, f"case {index} browser audit")
    )
    browser = _mapping(browser_row, f"case {index} final browser row")
    if (
        audit.get("run_id") != run_id
        or audit.get("case_index") != index
        or audit.get("case_id") != case_id
        or audit.get("source_case_summary_identity")
        != case.get("case_summary_identity")
        or audit.get("result_package") != package
        or audit.get("real_browser_executed") is not True
        or audit.get("browser_execution_status") != "pass"
        or audit.get("page_spec_conformance_status") != "pass"
        or audit.get("automation_reliable") is not True
        or browser.get("case_index") != index
        or browser.get("case_id") != case_id
        or browser.get("source_case_summary_identity")
        != case.get("case_summary_identity")
        or browser.get("case_browser_audit_identity")
        != audit.get("audit_identity")
        or browser.get("browser_execution_status") != "pass"
        or browser.get("page_spec_conformance_status") != "pass"
        or browser.get("automation_reliable") is not True
        or result_summary.get("package_id") != package.get("package_id")
        or result_summary.get("page_id") != package.get("page_id")
    ):
        raise Phase4DownstreamRevalidationError(
            f"case {index} package/browser binding drifted"
        )

    root = {
        "schema_version": CASE_RECEIPT_SCHEMA_VERSION,
        "run_id": run_id,
        "case_index": index,
        "case_id": case_id,
        "historical_case_summary_identity": copy.deepcopy(
            case["case_summary_identity"]
        ),
        "historical_parent_status": "failed_closed",
        "historical_parent_first_pass_success": False,
        "all_four_nodes_raw_contract_pass": True,
        "result_package": copy.deepcopy(package),
        "result_package_quality_gate_pass": True,
        "browser_audit_identity": copy.deepcopy(audit["audit_identity"]),
        "browser_execution_status": "pass",
        "page_spec_conformance_status": "pass",
        "semantic_alignment_status": audit["semantic_alignment"]["status"],
        "revalidation_status": (
            "validated_after_runtime_status_revalidation"
        ),
        "model_generate_calls_added": 0,
        "automatic_retry_count_added": 0,
        "normalization_count_added": 0,
        "repair_count_added": 0,
        "fallback_count_added": 0,
        "historical_case_summary_overwritten": False,
        "claim_boundary": (
            "zero-model deterministic evidence revalidation only; the "
            "historical parent summary remains failed_closed and is not "
            "recast as raw downstream first-pass success"
        ),
    }
    return {
        **root,
        "receipt_identity": _identity(
            root,
            revision=CASE_RECEIPT_SCHEMA_VERSION,
        ),
    }


def run_phase4_downstream_status_revalidation(
    *,
    flow_summary_path: Path,
    final_browser_summary_path: Path,
    legacy_canary_root: Path,
    browser_audit_root: Path,
    output_root: Path,
    confirm_zero_model_revalidation: bool,
) -> dict[str, object]:
    """Create immutable case receipts and one amended machine summary."""

    if confirm_zero_model_revalidation is not True:
        raise Phase4DownstreamRevalidationError(
            "explicit zero-model revalidation confirmation is required"
        )
    flow = _validate_flow_summary(
        _load_json(flow_summary_path, "canonical flow summary")
    )
    run_id = flow.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise Phase4DownstreamRevalidationError("flow run_id is invalid")
    browser = _validate_browser_summary(
        _load_json(final_browser_summary_path, "final browser summary"),
        run_id=run_id,
    )
    legacy_root = Path(legacy_canary_root).resolve(strict=True)
    audit_root = Path(browser_audit_root).resolve(strict=True)
    destination = Path(output_root).resolve(strict=False)
    flow_rows = flow["case_summaries"]
    browser_rows = browser["case_rows"]

    receipts: list[dict[str, object]] = []
    for index in REVALIDATED_CASE_INDICES:
        copied_case = _load_json(
            legacy_root
            / "flow_binding"
            / "case-summaries"
            / f"{index:02d}.json",
            f"case {index} copied historical summary",
        )
        receipt = _case_receipt(
            flow_case=_mapping(
                flow_rows[index - 1],
                f"case {index} flow summary",
            ),
            copied_case=copied_case,
            package_root=(
                legacy_root
                / "packages"
                / f"case{index:02d}"
                / "result_package_v1"
            ),
            audit_path=(
                audit_root
                / "audits"
                / f"case{index:02d}"
                / "case_browser_audit.json"
            ),
            browser_row=_mapping(
                browser_rows[index - 1],
                f"case {index} browser summary row",
            ),
            run_id=run_id,
        )
        _write_once(
            destination / "case_receipts" / f"{index:02d}.json",
            receipt,
        )
        receipts.append(receipt)

    rows: list[dict[str, object]] = []
    receipt_by_index = {
        int(receipt["case_index"]): receipt for receipt in receipts
    }
    for index, source_value in enumerate(flow_rows, start=1):
        source = _mapping(source_value, f"case {index} source row")
        browser_row = _mapping(
            browser_rows[index - 1],
            f"case {index} browser row",
        )
        if index in receipt_by_index:
            disposition = "validated_after_runtime_status_revalidation"
            delivery_evidence = True
            receipt_identity = copy.deepcopy(
                receipt_by_index[index]["receipt_identity"]
            )
        elif source.get("downstream_delivery_success") is True:
            disposition = "historical_direct_delivery_success"
            delivery_evidence = True
            receipt_identity = None
        elif index == 4:
            disposition = "unresolved_model_contract_failure"
            delivery_evidence = False
            receipt_identity = None
        elif index == 6:
            disposition = "unresolved_infrastructure_interruption"
            delivery_evidence = False
            receipt_identity = None
        else:
            raise Phase4DownstreamRevalidationError(
                f"case {index} has an unrecognized terminal disposition"
            )
        rows.append(
            {
                "case_index": index,
                "case_id": source["case_id"],
                "historical_case_summary_identity": copy.deepcopy(
                    source["case_summary_identity"]
                ),
                "historical_parent_status": source["status"],
                "all_four_nodes_raw_contract_pass": (
                    source["all_four_nodes_raw_contract_pass"] is True
                ),
                "historical_downstream_first_pass_success": (
                    source["downstream_first_pass_success"] is True
                ),
                "delivery_evidence_available": delivery_evidence,
                "real_browser_executed": (
                    browser_row["real_browser_executed"] is True
                ),
                "browser_execution_status": browser_row[
                    "browser_execution_status"
                ],
                "page_spec_conformance_status": browser_row[
                    "page_spec_conformance_status"
                ],
                "disposition": disposition,
                "revalidation_receipt_identity": receipt_identity,
            }
        )

    aggregate = _mapping(flow["aggregate"], "historical flow aggregate")
    root = {
        "schema_version": AMENDED_SUMMARY_SCHEMA_VERSION,
        "run_id": run_id,
        "historical_flow_summary_identity": copy.deepcopy(
            flow["summary_identity"]
        ),
        "final_browser_summary_identity": copy.deepcopy(
            browser["summary_identity"]
        ),
        "case_rows": rows,
        "counts": {
            "case_count": 10,
            "all_node_raw_contract_pass_case_count": sum(
                row["all_four_nodes_raw_contract_pass"] is True
                for row in rows
            ),
            "historical_downstream_first_pass_success_count": aggregate[
                "downstream_first_pass_success_count"
            ],
            "runtime_status_revalidated_case_count": len(receipts),
            "delivery_evidence_available_count": sum(
                row["delivery_evidence_available"] is True for row in rows
            ),
            "real_browser_execution_pass_count": browser["counts"][
                "browser_execution_pass_count"
            ],
            "page_spec_conformance_pass_count": browser["counts"][
                "page_spec_conformance_pass_count"
            ],
            "unresolved_model_contract_failure_count": 1,
            "unresolved_infrastructure_interruption_count": 1,
            "model_generate_calls_added": 0,
            "automatic_retry_count_added": 0,
            "normalization_count_added": 0,
            "repair_count_added": 0,
            "fallback_count_added": 0,
        },
        "status": "phase4_terminal_evidence_ready_for_handoff",
        "historical_summaries_overwritten": False,
        "claim_boundary": (
            "amended deterministic delivery-evidence accounting only; "
            "historical parent first-pass counts remain unchanged, cases 4 "
            "and 6 remain unresolved terminal failures, and semantic "
            "alignment remains unexecuted"
        ),
    }
    amended = {
        **root,
        "summary_identity": _identity(
            root,
            revision=AMENDED_SUMMARY_SCHEMA_VERSION,
        ),
    }
    _write_once(destination / "amended_flow_summary.json", amended)
    return amended


__all__ = [
    "AMENDED_SUMMARY_SCHEMA_VERSION",
    "CASE_RECEIPT_SCHEMA_VERSION",
    "Phase4DownstreamRevalidationError",
    "run_phase4_downstream_status_revalidation",
]
