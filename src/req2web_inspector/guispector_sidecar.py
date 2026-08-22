"""Read-only GUISpector interoperability and metric helpers.

This module consumes an already materialized Req2Web reviewer bundle. It never
changes the canonical generation flow, launches GUISpector, or calls a model.
"""

from __future__ import annotations

from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import statistics
from typing import Any, Mapping, Sequence


GUISPECTOR_EVALUATION_SCHEMA_VERSION = "req2web.guispector.evaluation.v1"
GUISPECTOR_DECISION_IMPORT_SCHEMA_VERSION = "req2web.guispector.decision_import.v1"
GUISPECTOR_COMPARISON_SCHEMA_VERSION = "req2web.guispector.comparison.v1"
GUISPECTOR_BATCH_EXECUTION_SCHEMA_VERSION = "req2web.guispector.batch_execution.v1"
GUISPECTOR_BATCH_METRICS_SCHEMA_VERSION = "req2web.guispector.batch_metrics.v3"
GUISPECTOR_PREFLIGHT_SCHEMA_VERSION = "req2web.guispector.preflight.v3"

GUISPECTOR_REPOSITORY_URL = "https://github.com/kristiankolthoff/GUISpector"
GUISPECTOR_PAPER_URL = "https://arxiv.org/abs/2510.04791"
GLM_46V_PRICING_SOURCE_URL = "https://open.bigmodel.cn/pricing"
GLM_46V_PRICING_OBSERVED_DATE = "2026-08-23"
GLM_46V_0_TO_32K_INPUT_CNY_PER_MILLION = 1.0
GLM_46V_0_TO_32K_CACHE_HIT_CNY_PER_MILLION = 0.2
GLM_46V_0_TO_32K_OUTPUT_CNY_PER_MILLION = 3.0
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


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GUISpectorSidecarError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise GUISpectorSidecarError(f"{label} must be finite and non-negative")
    return number


def _nonnegative_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GUISpectorSidecarError(f"{label} must be a non-negative integer")
    return value


def _summary_statistics(values: Sequence[float]) -> dict[str, Any]:
    rows = [float(value) for value in values]
    if not rows:
        return {
            "count": 0,
            "mean": None,
            "sample_sd": None,
            "minimum": None,
            "maximum": None,
        }
    return {
        "count": len(rows),
        "mean": statistics.fmean(rows),
        "sample_sd": statistics.stdev(rows) if len(rows) > 1 else 0.0,
        "minimum": min(rows),
        "maximum": max(rows),
    }


def _normalize_glm_46v_usage(usage: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exact per-call usage and price the official sub-32k tier."""

    tokens_in = _nonnegative_integer(usage.get("tokens_in"), "batch input tokens")
    tokens_out = _nonnegative_integer(
        usage.get("tokens_out"),
        "batch output tokens",
    )
    raw_calls = usage.get("provider_calls")
    if raw_calls is None:
        return {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "actual_glm_46v_cost": None,
        }
    calls = [
        _mapping(value, "GLM provider call usage")
        for value in _list(raw_calls, "GLM provider call usages")
    ]
    if not calls:
        raise GUISpectorSidecarError("GLM provider call usages must not be empty")
    normalized_calls: list[dict[str, Any]] = []
    for call in calls:
        input_tokens = _nonnegative_integer(
            call.get("input_tokens"),
            "GLM call input tokens",
        )
        cached_tokens = _nonnegative_integer(
            call.get("cached_tokens"),
            "GLM call cached tokens",
        )
        output_tokens = _nonnegative_integer(
            call.get("output_tokens"),
            "GLM call output tokens",
        )
        reasoning_tokens = _nonnegative_integer(
            call.get("reasoning_tokens"),
            "GLM call reasoning tokens",
        )
        if cached_tokens > input_tokens:
            raise GUISpectorSidecarError("GLM cached tokens exceed input tokens")
        if reasoning_tokens > output_tokens:
            raise GUISpectorSidecarError("GLM reasoning tokens exceed output tokens")
        if input_tokens >= 32_000:
            raise GUISpectorSidecarError(
                "GLM call does not belong to the priced [0, 32k) input tier"
            )
        uncached_tokens = input_tokens - cached_tokens
        uncached_input_cost = (
            uncached_tokens
            / 1_000_000
            * GLM_46V_0_TO_32K_INPUT_CNY_PER_MILLION
        )
        cache_hit_cost = (
            cached_tokens
            / 1_000_000
            * GLM_46V_0_TO_32K_CACHE_HIT_CNY_PER_MILLION
        )
        output_cost = (
            output_tokens
            / 1_000_000
            * GLM_46V_0_TO_32K_OUTPUT_CNY_PER_MILLION
        )
        normalized_calls.append(
            {
                "input_tokens": input_tokens,
                "cached_tokens": cached_tokens,
                "uncached_input_tokens": uncached_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": reasoning_tokens,
                "cost_cny": {
                    "uncached_input": uncached_input_cost,
                    "cache_hit_input": cache_hit_cost,
                    "output": output_cost,
                    "total": uncached_input_cost + cache_hit_cost + output_cost,
                },
            }
        )
    aggregate = {
        "tokens_in": sum(value["input_tokens"] for value in normalized_calls),
        "cached_tokens": sum(value["cached_tokens"] for value in normalized_calls),
        "uncached_input_tokens": sum(
            value["uncached_input_tokens"] for value in normalized_calls
        ),
        "tokens_out": sum(value["output_tokens"] for value in normalized_calls),
        "reasoning_tokens": sum(
            value["reasoning_tokens"] for value in normalized_calls
        ),
    }
    if aggregate["tokens_in"] != tokens_in or aggregate["tokens_out"] != tokens_out:
        raise GUISpectorSidecarError("GLM provider call usage does not sum to run usage")
    for field in ("cached_tokens", "reasoning_tokens"):
        if field in usage and _nonnegative_integer(
            usage.get(field),
            f"batch {field.replace('_', ' ')}",
        ) != aggregate[field]:
            raise GUISpectorSidecarError(f"GLM aggregate {field} drifted")
    aggregate_cost = {
        field: sum(value["cost_cny"][field] for value in normalized_calls)
        for field in ("uncached_input", "cache_hit_input", "output", "total")
    }
    return {
        **aggregate,
        "provider_call_count": len(normalized_calls),
        "provider_calls": normalized_calls,
        "actual_glm_46v_cost": aggregate_cost,
    }


def _conservative_end_to_end_metrics(
    references: Sequence[str],
    predictions: Sequence[str | None],
) -> dict[str, Any]:
    """Count verifier abstentions as operational misses, never semantic labels."""

    if len(references) != len(predictions):
        raise GUISpectorSidecarError("end-to-end reference and prediction counts differ")
    true_positive = sum(
        reference == "met" and prediction == "met"
        for reference, prediction in zip(references, predictions)
    )
    false_positive = sum(
        reference != "met" and prediction == "met"
        for reference, prediction in zip(references, predictions)
    )
    false_negative = sum(
        reference == "met" and prediction != "met"
        for reference, prediction in zip(references, predictions)
    )
    predicted_positive = true_positive + false_positive
    reference_positive = true_positive + false_negative
    precision = true_positive / predicted_positive if predicted_positive else None
    recall = true_positive / reference_positive if reference_positive else None
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    correct_complete = sum(
        prediction is not None and prediction == reference
        for reference, prediction in zip(references, predictions)
    )
    return {
        "item_count": len(references),
        "completed_prediction_count": sum(
            prediction is not None for prediction in predictions
        ),
        "abstention_count": sum(prediction is None for prediction in predictions),
        "accuracy_with_abstention_as_incorrect": (
            correct_complete / len(references) if references else None
        ),
        "met_positive_detection": {
            "support": reference_positive,
            "predicted_count": predicted_positive,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative_including_abstentions": false_negative,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "defined": precision is not None and recall is not None,
        },
    }


def build_guispector_batch_metrics(
    evaluation: Mapping[str, Any],
    batch_execution: Mapping[str, Any],
) -> dict[str, Any]:
    """Score a complete attempted batch without mapping execution errors to labels."""

    packet = _mapping(evaluation, "GUISpector evaluation")
    execution = _mapping(batch_execution, "GUISpector batch execution")
    if execution.get("schema_version") != GUISPECTOR_BATCH_EXECUTION_SCHEMA_VERSION:
        raise GUISpectorSidecarError("GUISpector batch execution schema drifted")
    if execution.get("evaluation_identity") != packet.get("evaluation_identity"):
        raise GUISpectorSidecarError("GUISpector batch execution binding drifted")
    packet_cases = [
        _mapping(row, "GUISpector evaluation case")
        for row in _list(packet.get("cases"), "GUISpector evaluation cases")
    ]
    rows = [
        _mapping(row, "GUISpector batch row")
        for row in _list(execution.get("rows"), "GUISpector batch rows")
    ]
    if len(rows) != len(packet_cases):
        raise GUISpectorSidecarError("GUISpector batch execution is incomplete")

    all_steps: list[float] = []
    all_elapsed: list[float] = []
    successful_steps: list[float] = []
    successful_elapsed: list[float] = []
    input_tokens: list[float] = []
    output_tokens: list[float] = []
    cached_tokens: list[float] = []
    uncached_input_tokens: list[float] = []
    priced_input_tokens: list[float] = []
    priced_output_tokens: list[float] = []
    reasoning_tokens: list[float] = []
    provider_call_counts: list[float] = []
    actual_uncached_input_costs: list[float] = []
    actual_cache_hit_costs: list[float] = []
    actual_output_costs: list[float] = []
    actual_total_costs: list[float] = []
    requirement_references: list[str] = []
    requirement_predictions: list[str] = []
    criterion_references: list[str] = []
    criterion_predictions: list[str] = []
    normalized_rows: list[dict[str, Any]] = []
    status_counts = {"met": 0, "unmet": 0, "partial": 0, "error": 0}

    for case, row in zip(packet_cases, rows):
        if (
            row.get("execution_index") != case.get("execution_index")
            or row.get("case_id") != case.get("case_id")
            or row.get("condition_id") != case.get("condition_id")
        ):
            raise GUISpectorSidecarError("GUISpector batch execution order drifted")
        steps = _nonnegative_integer(row.get("steps_taken"), "batch steps")
        elapsed = _finite_number(row.get("elapsed_s"), "batch elapsed time")
        all_steps.append(float(steps))
        all_elapsed.append(elapsed)
        raw_status = _nonempty(row.get("status"), "batch status")
        status = _STATUS_ALIASES.get(raw_status, raw_status)
        if status not in status_counts:
            raise GUISpectorSidecarError("GUISpector batch status is unsupported")

        if status == "error":
            if row.get("decision") is not None:
                raise GUISpectorSidecarError("error batch row carries a decision")
            _nonempty(row.get("error"), "batch error")
            status_counts["error"] += 1
            normalized_rows.append(
                {
                    "execution_index": case["execution_index"],
                    "case_id": case["case_id"],
                    "condition_id": case["condition_id"],
                    "run_id": row.get("run_id"),
                    "status": "error",
                    "steps_taken": steps,
                    "elapsed_s": elapsed,
                    "error": row["error"],
                }
            )
            continue

        requirement = _mapping(
            case.get("guispector_requirement"),
            "GUISpector requirement",
        )
        expected_names = [
            _nonempty(
                _mapping(value, "GUISpector acceptance criterion").get(
                    "criterion_name"
                ),
                "GUISpector acceptance criterion name",
            )
            for value in _list(
                requirement.get("acceptance_criteria"),
                "GUISpector acceptance criteria",
            )
        ]
        decision = normalize_guispector_decision(
            _mapping(row.get("decision"), "GUISpector batch decision"),
            expected_names,
        )
        model_reported_requirement_label = decision["requirement_label"]
        met_criterion_count = sum(
            value["label"] == "met" for value in decision["acceptance_criteria"]
        )
        if met_criterion_count == len(decision["acceptance_criteria"]):
            derived_requirement_label = "met"
        elif met_criterion_count == 0:
            derived_requirement_label = "unmet"
        else:
            derived_requirement_label = "partial"
        if derived_requirement_label != status:
            raise GUISpectorSidecarError(
                "batch status and criterion-derived GUISpector status differ"
            )
        decision["model_reported_requirement_label"] = (
            model_reported_requirement_label
        )
        decision["requirement_label"] = derived_requirement_label
        usage = _mapping(row.get("usage"), "GUISpector batch usage")
        normalized_usage = _normalize_glm_46v_usage(usage)
        tokens_in = normalized_usage["tokens_in"]
        tokens_out = normalized_usage["tokens_out"]
        reference = _mapping(case.get("internal_reference"), "internal reference")
        reference_criteria = [
            _mapping(value, "internal reference criterion")
            for value in _list(
                reference.get("acceptance_criteria"),
                "internal reference criteria",
            )
        ]
        requirement_references.append(
            _nonempty(reference.get("requirement_status"), "reference status")
        )
        requirement_predictions.append(decision["requirement_label"])
        criterion_references.extend(
            "met" if value.get("met") is True else "unmet"
            for value in reference_criteria
        )
        criterion_predictions.extend(
            value["label"] for value in decision["acceptance_criteria"]
        )
        successful_steps.append(float(steps))
        successful_elapsed.append(elapsed)
        input_tokens.append(float(tokens_in))
        output_tokens.append(float(tokens_out))
        actual_cost = normalized_usage["actual_glm_46v_cost"]
        if actual_cost is not None:
            priced_input_tokens.append(float(tokens_in))
            priced_output_tokens.append(float(tokens_out))
            cached_tokens.append(float(normalized_usage["cached_tokens"]))
            uncached_input_tokens.append(
                float(normalized_usage["uncached_input_tokens"])
            )
            reasoning_tokens.append(float(normalized_usage["reasoning_tokens"]))
            provider_call_counts.append(
                float(normalized_usage["provider_call_count"])
            )
            actual_uncached_input_costs.append(actual_cost["uncached_input"])
            actual_cache_hit_costs.append(actual_cost["cache_hit_input"])
            actual_output_costs.append(actual_cost["output"])
            actual_total_costs.append(actual_cost["total"])
        status_counts[status] += 1
        normalized_rows.append(
            {
                "execution_index": case["execution_index"],
                "case_id": case["case_id"],
                "condition_id": case["condition_id"],
                "run_id": row.get("run_id"),
                "status": status,
                "steps_taken": steps,
                "elapsed_s": elapsed,
                "usage": normalized_usage,
                "decision": decision,
            }
        )

    attempt_count = len(rows)
    decision_count = len(requirement_predictions)
    criterion_count = len(criterion_predictions)
    paper_input_costs = [value / 1_000_000 * 3.0 for value in input_tokens]
    paper_output_costs = [value / 1_000_000 * 12.0 for value in output_tokens]
    paper_total_costs = [
        input_cost + output_cost
        for input_cost, output_cost in zip(paper_input_costs, paper_output_costs)
    ]
    all_attempt_token_usage: dict[str, Any] | str
    if decision_count == attempt_count:
        all_attempt_token_usage = {
            "input_tokens": _summary_statistics(input_tokens),
            "output_tokens": _summary_statistics(output_tokens),
        }
    else:
        all_attempt_token_usage = "unavailable_for_failed_upstream_runs"
    actual_cost_coverage = len(actual_total_costs) / attempt_count if attempt_count else None
    actual_glm_cost: dict[str, Any] = {
        "status": (
            "exact_all_attempts"
            if len(actual_total_costs) == attempt_count
            else "partial_completed_decisions_only"
            if actual_total_costs
            else "unavailable_no_per_call_cache_usage"
        ),
        "priced_run_count": len(actual_total_costs),
        "attempt_count": attempt_count,
        "coverage": actual_cost_coverage,
        "currency": "CNY",
        "model": "GLM-4.6V",
        "pricing_source": GLM_46V_PRICING_SOURCE_URL,
        "pricing_observed_date": GLM_46V_PRICING_OBSERVED_DATE,
        "applied_tier": "per-call input length [0, 32k)",
        "rates_cny_per_million_tokens": {
            "uncached_input": GLM_46V_0_TO_32K_INPUT_CNY_PER_MILLION,
            "cache_hit_input": GLM_46V_0_TO_32K_CACHE_HIT_CNY_PER_MILLION,
            "output": GLM_46V_0_TO_32K_OUTPUT_CNY_PER_MILLION,
        },
        "cache_storage_price": "temporarily_free",
        "token_totals": {
            "input": int(sum(priced_input_tokens)),
            "cached_input": int(sum(cached_tokens)),
            "uncached_input": int(sum(uncached_input_tokens)),
            "output": int(sum(priced_output_tokens)),
            "reasoning_within_output": int(sum(reasoning_tokens)),
        },
        "provider_call_count": int(sum(provider_call_counts)),
        "cost_cny_totals": {
            "uncached_input": sum(actual_uncached_input_costs),
            "cache_hit_input": sum(actual_cache_hit_costs),
            "output": sum(actual_output_costs),
            "total": sum(actual_total_costs),
        },
        "per_run_total_cost_cny": _summary_statistics(actual_total_costs),
    }
    all_requirement_references: list[str] = []
    all_requirement_predictions: list[str | None] = []
    all_criterion_references: list[str] = []
    all_criterion_predictions: list[str | None] = []
    for case, normalized_row in zip(packet_cases, normalized_rows):
        reference = _mapping(case.get("internal_reference"), "internal reference")
        reference_criteria = [
            _mapping(value, "internal reference criterion")
            for value in _list(
                reference.get("acceptance_criteria"),
                "internal reference criteria",
            )
        ]
        all_requirement_references.append(
            _nonempty(reference.get("requirement_status"), "reference status")
        )
        all_criterion_references.extend(
            "met" if value.get("met") is True else "unmet"
            for value in reference_criteria
        )
        if normalized_row["status"] == "error":
            all_requirement_predictions.append(None)
            all_criterion_predictions.extend(None for _ in reference_criteria)
        else:
            decision = _mapping(
                normalized_row.get("decision"),
                "normalized batch decision",
            )
            all_requirement_predictions.append(
                _nonempty(decision.get("requirement_label"), "decision status")
            )
            all_criterion_predictions.extend(
                _nonempty(
                    _mapping(value, "normalized decision criterion").get("label"),
                    "decision criterion label",
                )
                for value in _list(
                    decision.get("acceptance_criteria"),
                    "normalized decision criteria",
                )
            )
    if len(all_criterion_predictions) != len(all_criterion_references):
        raise GUISpectorSidecarError("end-to-end criterion count drifted")
    report: dict[str, Any] = {
        "schema_version": GUISPECTOR_BATCH_METRICS_SCHEMA_VERSION,
        "status": (
            "batch_metrics_complete"
            if status_counts["error"] == 0
            else "batch_metrics_complete_with_execution_errors"
        ),
        "evaluation_identity": packet["evaluation_identity"],
        "attempt_count": attempt_count,
        "decision_count": decision_count,
        "execution_error_count": status_counts["error"],
        "decision_completion_rate": decision_count / attempt_count if attempt_count else None,
        "terminal_status_counts": status_counts,
        "criterion_judgment_count": criterion_count,
        "criterion_judgment_coverage": (
            criterion_count / packet["scope"]["acceptance_criterion_count"]
            if packet["scope"]["acceptance_criterion_count"]
            else None
        ),
        "all_attempt_efficiency": {
            "steps": _summary_statistics(all_steps),
            "elapsed_seconds": _summary_statistics(all_elapsed),
            "token_usage": all_attempt_token_usage,
        },
        "completed_decision_efficiency": {
            "steps": _summary_statistics(successful_steps),
            "elapsed_seconds": _summary_statistics(successful_elapsed),
            "input_tokens": _summary_statistics(input_tokens),
            "output_tokens": _summary_statistics(output_tokens),
            "cached_input_tokens": _summary_statistics(cached_tokens),
            "uncached_input_tokens": _summary_statistics(uncached_input_tokens),
            "reasoning_tokens_within_output": _summary_statistics(reasoning_tokens),
            "provider_calls": _summary_statistics(provider_call_counts),
            "paper_rate_normalized_input_cost_usd": _summary_statistics(
                paper_input_costs
            ),
            "paper_rate_normalized_output_cost_usd": _summary_statistics(
                paper_output_costs
            ),
            "paper_rate_normalized_total_cost_usd": _summary_statistics(
                paper_total_costs
            ),
        },
        "actual_glm_46v_cost": actual_glm_cost,
        "completed_decision_metrics": {
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
        },
        "conservative_end_to_end_metrics": {
            "policy": (
                "An execution error is counted as an operational miss for this "
                "metric, but remains an abstention and is never relabeled unmet."
            ),
            "requirement_level": _conservative_end_to_end_metrics(
                all_requirement_references,
                all_requirement_predictions,
            ),
            "acceptance_criterion_level": _conservative_end_to_end_metrics(
                all_criterion_references,
                all_criterion_predictions,
            ),
        },
        "published_guispector_reference": {
            **packet["published_reference_metrics"],
            "efficiency_average_of_five_app_means": {
                "steps_mean": 24.435,
                "steps_sd": 16.225,
                "elapsed_seconds_mean": 317.553,
                "elapsed_seconds_sd": 259.339,
                "input_tokens_mean": 221738.099,
                "input_tokens_sd": 147628.799,
                "output_tokens_mean": 2268.960,
                "output_tokens_sd": 1076.168,
                "input_cost_usd_at_paper_rate": 0.665,
                "output_cost_usd_at_paper_rate": 0.027,
            },
        },
        "paper_comparison_eligible": False,
        "formal_evaluation": False,
        "normalized_rows": normalized_rows,
        "limitations": [
            packet["reference_profile"]["limitation"],
            "Execution errors are abstentions and are never mapped to unmet labels.",
            "Conservative end-to-end metrics count abstentions as operational misses only; they do not create unmet or partial ground truth.",
            "Efficiency comparisons use a fixed five-action GLM compatibility budget, while the paper used a different model and protocol.",
            (
                "Failed upstream runs did not persist aggregate token usage, so "
                "token and normalized-cost statistics cover completed decisions only."
                if status_counts["error"]
                else "All attempted runs completed and expose aggregate token usage."
            ),
            (
                "Actual GLM-4.6V cost is cache-aware only where exact per-call "
                "usage is present; reasoning tokens remain part of output tokens "
                "and are not charged twice."
            ),
        ],
    }
    report["batch_metrics_identity"] = _identity(
        report,
        "batch_metrics_identity",
    )
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
            or (root / "docker-compose.req2web.yml").is_file()
        )
    zhipu_api_key_configured = bool(os.environ.get("ZHIPU_API_KEY", "").strip())
    openai_api_key_configured = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    dashscope_api_key_configured = bool(os.environ.get("DASHSCOPE_API_KEY", "").strip())
    dashscope_workspace_id_configured = bool(
        os.environ.get("DASHSCOPE_WORKSPACE_ID", "").strip()
    )
    gui_plus_configured = (
        dashscope_api_key_configured and dashscope_workspace_id_configured
    )
    model_api_key_configured = (
        zhipu_api_key_configured or openai_api_key_configured or gui_plus_configured
    )
    ready = docker_available and upstream_root_configured and model_api_key_configured
    blockers: list[str] = []
    if not docker_available:
        blockers.append("docker_cli_not_available")
    if not upstream_root_configured:
        blockers.append("guispector_checkout_not_configured")
    if not model_api_key_configured:
        blockers.append("model_api_key_not_configured")
    return {
        "schema_version": GUISPECTOR_PREFLIGHT_SCHEMA_VERSION,
        "status": "ready_for_operator_started_external_run" if ready else "not_ready",
        "docker_cli_available": docker_available,
        "guispector_checkout_configured": upstream_root_configured,
        "model_api_key_configured": model_api_key_configured,
        "zhipu_api_key_configured": zhipu_api_key_configured,
        "openai_api_key_configured": openai_api_key_configured,
        "dashscope_api_key_configured": dashscope_api_key_configured,
        "dashscope_workspace_id_configured": dashscope_workspace_id_configured,
        "gui_plus_configured": gui_plus_configured,
        "execution_performed": False,
        "blocking_reasons": blockers,
        "secret_values_exposed": False,
    }


__all__ = [
    "GUISPECTOR_BATCH_EXECUTION_SCHEMA_VERSION",
    "GUISPECTOR_BATCH_METRICS_SCHEMA_VERSION",
    "GUISPECTOR_COMPARISON_SCHEMA_VERSION",
    "GUISPECTOR_DECISION_IMPORT_SCHEMA_VERSION",
    "GUISPECTOR_EVALUATION_SCHEMA_VERSION",
    "GUISPECTOR_PREFLIGHT_SCHEMA_VERSION",
    "GUISpectorSidecarError",
    "build_guispector_comparison",
    "build_guispector_batch_metrics",
    "build_guispector_evaluation",
    "guispector_runtime_preflight",
    "normalize_guispector_decision",
    "validate_guispector_evaluation",
]
