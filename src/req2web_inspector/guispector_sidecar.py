"""Read-only GUISpector interoperability and metric helpers.

This module consumes an already materialized Req2Web reviewer bundle. It never
changes the canonical generation flow, launches GUISpector, or calls a model.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Any, Mapping, Sequence


GUISPECTOR_EVALUATION_SCHEMA_VERSION = "req2web.guispector.evaluation.v1"
GUISPECTOR_DECISION_IMPORT_SCHEMA_VERSION = "req2web.guispector.decision_import.v1"
GUISPECTOR_COMPARISON_SCHEMA_VERSION = "req2web.guispector.comparison.v1"
GUISPECTOR_PREFLIGHT_SCHEMA_VERSION = "req2web.guispector.preflight.v1"

GUISPECTOR_REPOSITORY_URL = "https://github.com/kristiankolthoff/GUISpector"
GUISPECTOR_PAPER_URL = "https://arxiv.org/abs/2510.04791"
START_URL_TOKEN = "{REQ2WEB_INSPECTOR_BASE_URL}"

_REQUIREMENT_LABELS = ("met", "unmet", "partial")
_CRITERION_LABELS = ("met", "unmet")
_STATUS_ALIASES = {
    "met": "met",
    "unmet": "unmet",
    "not_met": "unmet",
    "partial": "partial",
    "partially_met": "partial",
}


class GUISpectorSidecarError(ValueError):
    """Raised when GUISpector interoperability evidence fails closed."""


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _identity(value: Mapping[str, Any], identity_key: str) -> str:
    body = dict(value)
    body.pop(identity_key, None)
    return sha256(_canonical_bytes(body)).hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GUISpectorSidecarError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise GUISpectorSidecarError(f"{label} must be a list")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GUISpectorSidecarError(f"{label} must be a non-empty string")
    return value.strip()


def _safe_relative(value: Any, label: str) -> str:
    text = _nonempty(value, label).replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != text:
        raise GUISpectorSidecarError(f"{label} is not a safe relative path")
    return text


def _bound_path(root: Path, relative: Any, label: str) -> Path:
    normalized = _safe_relative(relative, label)
    path = (root / Path(normalized)).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GUISpectorSidecarError(f"{label} escapes the bundle root") from exc
    if path.is_symlink() or not path.is_file():
        raise GUISpectorSidecarError(f"{label} is not a regular file")
    return path


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GUISpectorSidecarError(f"could not read {label}") from exc
    return _mapping(value, label)


def _file_binding(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"sha256": sha256(raw).hexdigest(), "byte_length": len(raw)}


def _description(requirement: Mapping[str, Any]) -> str:
    raw = _nonempty(requirement.get("requirement"), "requirement text")
    constraints = [
        _nonempty(item, "requirement constraint")
        for item in _list(requirement.get("constraints"), "requirement constraints")
    ]
    if not constraints:
        return raw
    return raw + "\n\nConstraints:\n" + "\n".join(f"- {item}" for item in constraints)


def _acceptance_criteria(requirement: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(
        _list(requirement.get("use_cases"), "requirement use cases"),
        start=1,
    ):
        use_case = _mapping(raw, f"use case {index}")
        title = _nonempty(use_case.get("title"), f"use case {index} title")
        expected = _nonempty(
            use_case.get("expected_outcome"),
            f"use case {index} expected outcome",
        )
        rows.append(
            {
                "criterion_name": f"AC-{index}",
                "description": f"{title}. Expected outcome: {expected}.",
                "source_use_case_id": _nonempty(
                    use_case.get("use_case_id"),
                    f"use case {index} ID",
                ),
            }
        )
    if not rows:
        raise GUISpectorSidecarError("at least one acceptance criterion is required")
    return rows


def build_guispector_evaluation(
    bundle_root: Path,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the deterministic GUISpector input packet for one reviewer bundle."""

    root = Path(bundle_root).resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise GUISpectorSidecarError("reviewer bundle root is invalid")
    catalog_value = (
        _read_json(root / "catalog.json", "reviewer catalog")
        if catalog is None
        else _mapping(catalog, "reviewer catalog")
    )
    cases: list[dict[str, Any]] = []
    criterion_count = 0
    for expected_index, raw_item in enumerate(
        _list(catalog_value.get("cases"), "reviewer catalog cases"),
        start=1,
    ):
        item = _mapping(raw_item, f"reviewer catalog case {expected_index}")
        if item.get("execution_index") != expected_index:
            raise GUISpectorSidecarError("reviewer catalog execution order drifted")
        case_path = _bound_path(root, item.get("case_record"), "case record")
        case = _read_json(case_path, f"case {expected_index}")
        artifacts = _mapping(case.get("artifacts"), f"case {expected_index} artifacts")
        requirement_path = _bound_path(
            root,
            artifacts.get("requirement"),
            f"case {expected_index} requirement",
        )
        page_spec_path = _bound_path(
            root,
            artifacts.get("page_spec"),
            f"case {expected_index} PageSpec",
        )
        semantic_path = _bound_path(
            root,
            artifacts.get("semantic_result"),
            f"case {expected_index} semantic result",
        )
        final_page = _safe_relative(
            artifacts.get("final_page"),
            f"case {expected_index} final page",
        )
        _bound_path(root, final_page, f"case {expected_index} final page")
        requirement = _read_json(requirement_path, f"case {expected_index} requirement")
        semantic = _read_json(semantic_path, f"case {expected_index} semantic result")
        verdicts = [
            _mapping(row, f"case {expected_index} semantic verdict")
            for row in _list(
                semantic.get("verdicts"),
                f"case {expected_index} semantic verdicts",
            )
        ]
        criteria = _acceptance_criteria(requirement)
        if len(verdicts) != len(criteria) or any(
            row.get("verdict") != "supported" for row in verdicts
        ):
            raise GUISpectorSidecarError(
                f"case {expected_index} internal reference is not fully supported"
            )
        criterion_count += len(criteria)
        target = _nonempty(requirement.get("target_device"), "target device")
        task = _nonempty(requirement.get("task_type"), "task type")
        case_id = _nonempty(case.get("case_id"), f"case {expected_index} ID")
        condition_id = _nonempty(
            case.get("condition_id"),
            f"case {expected_index} condition ID",
        )
        cases.append(
            {
                "execution_index": expected_index,
                "case_id": case_id,
                "condition_id": condition_id,
                "bundle_relative_start_url": final_page,
                "start_url_template": f"{START_URL_TOKEN}/{final_page}",
                "guispector_requirement": {
                    "title": f"{case_id} / {condition_id}",
                    "description": _description(requirement),
                    "acceptance_criteria": criteria,
                    "source": "Req2Web frozen Phase 5 result package",
                    "tags": ["req2web", target, task, condition_id],
                    "priority": "medium",
                    "start_url": f"{START_URL_TOKEN}/{final_page}",
                },
                "internal_reference": {
                    "requirement_status": "met",
                    "acceptance_criteria": [
                        {
                            "criterion_name": row["criterion_name"],
                            "met": True,
                        }
                        for row in criteria
                    ],
                    "source": "frozen Req2Web browser and semantic sidecars",
                    "independent_human_gold": False,
                },
                "artifact_bindings": {
                    "case_record": {
                        "path": case_path.relative_to(root).as_posix(),
                        **_file_binding(case_path),
                    },
                    "requirement": {
                        "path": requirement_path.relative_to(root).as_posix(),
                        **_file_binding(requirement_path),
                    },
                    "page_spec": {
                        "path": page_spec_path.relative_to(root).as_posix(),
                        **_file_binding(page_spec_path),
                    },
                    "semantic_result": {
                        "path": semantic_path.relative_to(root).as_posix(),
                        **_file_binding(semantic_path),
                    },
                },
            }
        )

    payload: dict[str, Any] = {
        "schema_version": GUISPECTOR_EVALUATION_SCHEMA_VERSION,
        "status": "prepared_not_executed",
        "execution_performed": False,
        "upstream": {
            "name": "GUISpector",
            "repository_url": GUISPECTOR_REPOSITORY_URL,
            "paper_url": GUISPECTOR_PAPER_URL,
            "paper_version": "arXiv:2510.04791v1",
            "repository_revision": "main_unpinned_execution_not_started",
            "license_status": "not_detected_in_inspected_repository_root",
            "execution_interface": "official Docker deployment and requirement verification workflow",
        },
        "scope": {
            "result_package_count": len(cases),
            "acceptance_criterion_count": criterion_count,
            "start_url_token": START_URL_TOKEN,
            "frozen_req2web_artifacts_modified": False,
            "canonical_flow_modified": False,
        },
        "input_policy": {
            "requirement_source": "frozen canonical requirement",
            "acceptance_criterion_source": "frozen canonical use cases and expected outcomes",
            "page_source": "exact packaged runnable page",
            "criteria_derived_from_generated_page_spec": False,
        },
        "reference_profile": {
            "status": "internal_reference_only",
            "independent_human_gold": False,
            "paper_comparison_eligible": False,
            "requirement_label_distribution": {
                "met": len(cases),
                "unmet": 0,
                "partial": 0,
            },
            "criterion_label_distribution": {
                "met": criterion_count,
                "unmet": 0,
            },
            "limitation": (
                "The frozen set contains no independently labeled unmet or partial cases. "
                "Agreement metrics are descriptive integration evidence, not a formal "
                "head-to-head reproduction of the GUISpector paper."
            ),
        },
        "published_reference_metrics": {
            "requirement_level_f1": {
                "met": 0.905,
                "unmet": 0.878,
                "partial": 0.631,
            },
            "acceptance_criterion_level_f1": {
                "met": 0.940,
                "unmet": 0.870,
            },
            "source": "GUISpector paper, arXiv:2510.04791v1",
        },
        "current_result": {
            "status": "not_executed",
            "decision_count": 0,
            "metric_status": "not_computed_no_guispector_decisions",
        },
        "cases": cases,
    }
    payload["evaluation_identity"] = _identity(payload, "evaluation_identity")
    return payload


def validate_guispector_evaluation(
    bundle_root: Path,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Recompute and validate the complete deterministic evaluation packet."""

    root = Path(bundle_root).resolve(strict=True)
    observed = (
        _read_json(root / "guispector_evaluation.json", "GUISpector evaluation")
        if payload is None
        else _mapping(payload, "GUISpector evaluation")
    )
    if observed.get("schema_version") != GUISPECTOR_EVALUATION_SCHEMA_VERSION:
        raise GUISpectorSidecarError("GUISpector evaluation schema drifted")
    if observed.get("evaluation_identity") != _identity(
        observed,
        "evaluation_identity",
    ):
        raise GUISpectorSidecarError("GUISpector evaluation identity drifted")
    expected = build_guispector_evaluation(root)
    if dict(observed) != expected:
        raise GUISpectorSidecarError("GUISpector evaluation packet drifted")
    return dict(observed)


def normalize_guispector_decision(
    raw_decision: Mapping[str, Any],
    expected_criteria: Sequence[str],
) -> dict[str, Any]:
    """Normalize documented GUISpector status aliases without changing raw evidence."""

    decision = _mapping(raw_decision, "GUISpector decision")
    raw_status = _nonempty(decision.get("status"), "GUISpector status")
    status = _STATUS_ALIASES.get(raw_status)
    if status is None:
        raise GUISpectorSidecarError("GUISpector status is unsupported")
    observed: dict[str, bool] = {}
    normalized_rows: list[dict[str, Any]] = []
    for index, raw in enumerate(
        _list(
            decision.get("acceptance_criteria_results"),
            "GUISpector acceptance criteria results",
        ),
        start=1,
    ):
        row = _mapping(raw, f"GUISpector criterion result {index}")
        name = _nonempty(
            row.get("criterion_name"),
            f"GUISpector criterion result {index} name",
        )
        if name in observed:
            raise GUISpectorSidecarError("GUISpector criterion names are not unique")
        met = row.get("met")
        if type(met) is not bool:
            raise GUISpectorSidecarError("GUISpector criterion met value must be boolean")
        observed[name] = met
        normalized_rows.append(
            {
                "criterion_name": name,
                "label": "met" if met else "unmet",
                "evidence": str(row.get("evidence", "")),
            }
        )
    if list(observed) != list(expected_criteria):
        raise GUISpectorSidecarError("GUISpector criterion set or order drifted")
    return {
        "requirement_label": status,
        "acceptance_criteria": normalized_rows,
        "raw_decision": dict(decision),
    }


def _one_vs_rest(
    references: Sequence[str],
    predictions: Sequence[str],
    labels: Sequence[str],
) -> dict[str, Any]:
    if len(references) != len(predictions):
        raise GUISpectorSidecarError("reference and prediction counts differ")
    rows: dict[str, Any] = {}
    for label in labels:
        true_positive = sum(
            reference == label and prediction == label
            for reference, prediction in zip(references, predictions)
        )
        false_positive = sum(
            reference != label and prediction == label
            for reference, prediction in zip(references, predictions)
        )
        false_negative = sum(
            reference == label and prediction != label
            for reference, prediction in zip(references, predictions)
        )
        predicted_positive = true_positive + false_positive
        reference_positive = true_positive + false_negative
        precision = (
            true_positive / predicted_positive if predicted_positive else None
        )
        recall = true_positive / reference_positive if reference_positive else None
        if precision is None or recall is None:
            f1 = None
        elif precision + recall == 0:
            f1 = 0.0
        else:
            f1 = 2 * precision * recall / (precision + recall)
        rows[label] = {
            "support": reference_positive,
            "predicted_count": predicted_positive,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "defined": precision is not None and recall is not None,
        }
    accuracy = (
        sum(reference == prediction for reference, prediction in zip(references, predictions))
        / len(references)
        if references
        else None
    )
    return {"item_count": len(references), "accuracy": accuracy, "classes": rows}


def build_guispector_comparison(
    evaluation: Mapping[str, Any],
    decision_import: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate imported decisions and compute GUISpector-style P/R/F1 metrics."""

    packet = _mapping(evaluation, "GUISpector evaluation")
    imported = _mapping(decision_import, "GUISpector decision import")
    if imported.get("schema_version") != GUISPECTOR_DECISION_IMPORT_SCHEMA_VERSION:
        raise GUISpectorSidecarError("GUISpector decision import schema drifted")
    if imported.get("evaluation_identity") != packet.get("evaluation_identity"):
        raise GUISpectorSidecarError("GUISpector decision import binding drifted")
    packet_cases = [
        _mapping(row, "GUISpector evaluation case")
        for row in _list(packet.get("cases"), "GUISpector evaluation cases")
    ]
    decisions = [
        _mapping(row, "GUISpector imported decision")
        for row in _list(imported.get("decisions"), "GUISpector imported decisions")
    ]
    if len(decisions) != len(packet_cases):
        raise GUISpectorSidecarError("GUISpector decision import is incomplete")

    normalized: list[dict[str, Any]] = []
    requirement_references: list[str] = []
    requirement_predictions: list[str] = []
    criterion_references: list[str] = []
    criterion_predictions: list[str] = []
    for case, imported_row in zip(packet_cases, decisions):
        if (
            imported_row.get("execution_index") != case.get("execution_index")
            or imported_row.get("case_id") != case.get("case_id")
        ):
            raise GUISpectorSidecarError("GUISpector decision execution order drifted")
        requirement = _mapping(
            case.get("guispector_requirement"),
            "GUISpector requirement",
        )
        expected_names = [
            _nonempty(
                _mapping(row, "GUISpector acceptance criterion").get("criterion_name"),
                "GUISpector acceptance criterion name",
            )
            for row in _list(
                requirement.get("acceptance_criteria"),
                "GUISpector acceptance criteria",
            )
        ]
        decision = normalize_guispector_decision(
            _mapping(imported_row.get("decision"), "GUISpector raw decision"),
            expected_names,
        )
        reference = _mapping(case.get("internal_reference"), "internal reference")
        reference_criteria = [
            _mapping(row, "internal reference criterion")
            for row in _list(
                reference.get("acceptance_criteria"),
                "internal reference criteria",
            )
        ]
        requirement_references.append(
            _nonempty(reference.get("requirement_status"), "reference status")
        )
        requirement_predictions.append(decision["requirement_label"])
        criterion_references.extend(
            "met" if row.get("met") is True else "unmet" for row in reference_criteria
        )
        criterion_predictions.extend(
            row["label"] for row in decision["acceptance_criteria"]
        )
        normalized.append(
            {
                "execution_index": case["execution_index"],
                "case_id": case["case_id"],
                **decision,
            }
        )

    report: dict[str, Any] = {
        "schema_version": GUISPECTOR_COMPARISON_SCHEMA_VERSION,
        "status": "descriptive_internal_reference_comparison_complete",
        "evaluation_identity": packet["evaluation_identity"],
        "decision_count": len(normalized),
        "paper_comparison_eligible": False,
        "formal_evaluation": False,
        "requirement_level": _one_vs_rest(
            requirement_references,
            requirement_predictions,
            _REQUIREMENT_LABELS,
        ),
        "acceptance_criterion_level": _one_vs_rest(
            criterion_references,
            criterion_predictions,
            _CRITERION_LABELS,
        ),
        "normalized_decisions": normalized,
        "limitation": packet["reference_profile"]["limitation"],
    }
    report["comparison_identity"] = _identity(report, "comparison_identity")
    return report


def guispector_runtime_preflight() -> dict[str, Any]:
    """Report only prerequisite presence; never expose secret values or run Docker."""

    docker_available = shutil.which("docker") is not None
    upstream_root_text = os.environ.get("REQ2WEB_GUISPECTOR_ROOT", "").strip()
    upstream_root_configured = False
    if upstream_root_text:
        root = Path(upstream_root_text)
        upstream_root_configured = root.is_dir() and (
            (root / "docker-compose.yml").is_file()
            or (root / "docker-compose.yaml").is_file()
            or (root / "compose.yml").is_file()
        )
    api_key_configured = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    ready = docker_available and upstream_root_configured and api_key_configured
    blockers: list[str] = []
    if not docker_available:
        blockers.append("docker_cli_not_available")
    if not upstream_root_configured:
        blockers.append("guispector_checkout_not_configured")
    if not api_key_configured:
        blockers.append("openai_api_key_not_configured")
    return {
        "schema_version": GUISPECTOR_PREFLIGHT_SCHEMA_VERSION,
        "status": "ready_for_operator_started_external_run" if ready else "not_ready",
        "docker_cli_available": docker_available,
        "guispector_checkout_configured": upstream_root_configured,
        "openai_api_key_configured": api_key_configured,
        "execution_performed": False,
        "blocking_reasons": blockers,
        "secret_values_exposed": False,
    }


__all__ = [
    "GUISPECTOR_COMPARISON_SCHEMA_VERSION",
    "GUISPECTOR_DECISION_IMPORT_SCHEMA_VERSION",
    "GUISPECTOR_EVALUATION_SCHEMA_VERSION",
    "GUISPECTOR_PREFLIGHT_SCHEMA_VERSION",
    "GUISpectorSidecarError",
    "build_guispector_comparison",
    "build_guispector_evaluation",
    "guispector_runtime_preflight",
    "normalize_guispector_decision",
    "validate_guispector_evaluation",
]
