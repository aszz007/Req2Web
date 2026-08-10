"""Deterministic retrieval-backend comparison for the frozen Demo v2 cases.

The comparison has two deliberately separate layers:

* task-utility diagnostics reuse the existing deterministic guidance,
  PageSpec, consistency, and influence pipeline;
* ranking-quality metrics are computed only when an independent, complete
  qrels file is supplied.

Without qrels the report may describe differences, but it may not declare a
winning retrieval method.
"""

from __future__ import annotations

from hashlib import sha256
from itertools import combinations, product
import json
import math
from pathlib import Path, PurePosixPath
import random
import shutil
from statistics import fmean
from typing import Any, Mapping, Sequence

from req2web_agent import DeterministicRequirementProvider, build_retrieval_queries
from req2web_generation.demo_regression import (
    DemoV2RegressionRunner,
    RegressionCaseSet,
    RegressionSuiteValidator,
)
from req2web_rag.corpus import ROLE_ORDER
from req2web_rag.retriever import RetrieverConfig, create_retriever


RETRIEVAL_COMPARISON_SCHEMA_VERSION = "req2web.retrieval.comparison.v2"
RETRIEVAL_COMPARISON_MANIFEST_SCHEMA_VERSION = (
    "req2web.retrieval.comparison.manifest.v2"
)
RETRIEVAL_QRELS_SCHEMA_VERSION = "req2web.retrieval.qrels.v2"
RETRIEVAL_ASSISTED_QRELS_SCHEMA_VERSION = "req2web.retrieval.qrels.v3"
RETRIEVAL_METRIC_PROTOCOL_REVISION = "req2web.retrieval.metrics.phase6.v2"
SUPPORTED_METHODS = ("bm25", "rrf", "tfidf")
DEFAULT_RANKING_TOP_K = 5
DEFAULT_PIPELINE_TOP_K = 2
RELEVANT_GRADE_THRESHOLD = 2
BOOTSTRAP_SEED = 20260809
BOOTSTRAP_REPLICATES = 5000


class RetrievalComparisonError(ValueError):
    """Raised when comparison evidence is incomplete or inconsistent."""


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RetrievalComparisonError("value is not canonical JSON") from exc


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RetrievalComparisonError(f"{name} must be an object")
    return dict(value)


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise RetrievalComparisonError(f"{name} must be a list")
    return value


def _read_json(path: Path, name: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RetrievalComparisonError(f"{name} is unavailable")
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalComparisonError(f"{name} is not UTF-8 JSON") from exc
    return _mapping(value, name)


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RetrievalComparisonError(f"source file is unavailable: {path}")
    return sha256(path.read_bytes()).hexdigest()


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or ".." in path.parts
        or str(path) != value
    ):
        raise RetrievalComparisonError("comparison path is not safely relative")
    return value


def _source_identities(fixture_path: Path, index_dir: Path) -> dict[str, Any]:
    return {
        "case_fixture_sha256": _file_sha256(fixture_path),
        "documents_sha256": _file_sha256(index_dir / "documents.jsonl"),
        "tfidf_index_sha256": _file_sha256(index_dir / "tfidf_index.json.gz"),
        "index_manifest_sha256": _file_sha256(index_dir / "index_manifest.json"),
        "bm25_parameters": {"k1": 1.5, "b": 0.75},
        "rrf_parameters": {
            "rrf_k": 60,
            "source_depth": 20,
            "sources": ["bm25", "tfidf"],
        },
    }


def _candidate_view(result: Mapping[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "doc_id": result["doc_id"],
        "score": result["score"],
        "dataset": result["dataset"],
        "subset": result["subset"],
        "title": result["title"],
        "summary": result["summary"],
    }


def build_candidate_units(
    *,
    case_set: RegressionCaseSet,
    index_dir: Path,
    methods: Sequence[str] = SUPPORTED_METHODS,
    top_k: int = DEFAULT_RANKING_TOP_K,
) -> list[dict[str, Any]]:
    """Retrieve one shared candidate pool without running downstream stages."""

    if tuple(methods) != tuple(sorted(set(methods))):
        raise RetrievalComparisonError("methods must be sorted and unique")
    if len(methods) < 2:
        raise RetrievalComparisonError("comparison requires at least two methods")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise RetrievalComparisonError("ranking top_k must be a positive integer")
    retrievers = {
        method: create_retriever(
            RetrieverConfig(index_dir=index_dir, backend=method)
        )
        for method in methods
    }
    provider = DeterministicRequirementProvider()
    units: list[dict[str, Any]] = []
    for case in case_set.cases:
        understanding = provider.understand(
            case.requirement,
            target_device=case.target_device,
            task_type=case.task_type,
            constraints=case.constraints,
        )
        queries = build_retrieval_queries(understanding)
        for role in ROLE_ORDER:
            candidates = {
                method: [
                    _candidate_view(result, rank)
                    for rank, result in enumerate(
                        retrievers[method].search(
                            queries[role],
                            top_k=top_k,
                            roles=(role,),
                        ),
                        start=1,
                    )
                ]
                for method in methods
            }
            diagnostics = []
            for first_method, second_method in combinations(methods, 2):
                first_ids = [
                    item["doc_id"] for item in candidates[first_method]
                ]
                second_ids = [
                    item["doc_id"] for item in candidates[second_method]
                ]
                intersection = set(first_ids) & set(second_ids)
                union = set(first_ids) | set(second_ids)
                diagnostics.append(
                    {
                        "methods": [first_method, second_method],
                        "top1_same": bool(
                            first_ids
                            and second_ids
                            and first_ids[0] == second_ids[0]
                        ),
                        "overlap_count_at_k": len(intersection),
                        "jaccard_at_k": round(
                            len(intersection) / len(union) if union else 1.0,
                            6,
                        ),
                    }
                )
            units.append(
                {
                    "case_id": case.case_id,
                    "role": role,
                    "query_sha256": sha256(
                        queries[role].encode("utf-8")
                    ).hexdigest(),
                    "candidates": candidates,
                    "pairwise_diagnostics": diagnostics,
                }
            )
    return units


def _pairwise_summary(units: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for unit in units:
        for raw in _list(
            unit["pairwise_diagnostics"], "pairwise diagnostics"
        ):
            diagnostic = _mapping(raw, "pairwise diagnostic")
            pair = tuple(diagnostic["methods"])
            if len(pair) != 2:
                raise RetrievalComparisonError("pairwise method binding drifted")
            by_pair.setdefault((str(pair[0]), str(pair[1])), []).append(
                diagnostic
            )
    comparisons = []
    for pair in sorted(by_pair):
        diagnostics = by_pair[pair]
        comparisons.append(
            {
                "methods": list(pair),
                "query_role_unit_count": len(diagnostics),
                "top1_same_count": sum(
                    item["top1_same"] is True for item in diagnostics
                ),
                "top1_changed_count": sum(
                    item["top1_same"] is False for item in diagnostics
                ),
                "mean_overlap_count_at_k": round(
                    fmean(
                        float(item["overlap_count_at_k"])
                        for item in diagnostics
                    ),
                    6,
                ),
                "mean_jaccard_at_k": round(
                    fmean(float(item["jaccard_at_k"]) for item in diagnostics),
                    6,
                ),
            }
        )
    return {
        "query_role_unit_count": len(units),
        "comparison_count": len(comparisons),
        "comparisons": comparisons,
        "quality_winner_declared": False,
    }


def _method_utility(aggregate_report: Mapping[str, Any]) -> dict[str, Any]:
    aggregate = _mapping(aggregate_report.get("aggregate"), "method aggregate")
    role_totals = _list(aggregate.get("role_totals"), "method role totals")
    if len(role_totals) != len(ROLE_ORDER):
        raise RetrievalComparisonError("method utility has incomplete roles")
    by_role: list[dict[str, Any]] = []
    for expected_role, raw in zip(ROLE_ORDER, role_totals, strict=True):
        row = _mapping(raw, "method role total")
        if row.get("role") != expected_role:
            raise RetrievalComparisonError("method utility role order drifted")
        by_role.append(
            {
                key: row[key]
                for key in (
                    "role",
                    "retrieval_count",
                    "guidance_count",
                    "adopted",
                    "ignored",
                    "fallback",
                    "has_influence",
                    "not_applicable_or_ignored",
                    "verification_failed",
                )
            }
        )
    return {
        "all_consistency_passed": aggregate.get("all_consistency_passed"),
        "all_retrieval_influence_passed": aggregate.get(
            "all_retrieval_influence_passed"
        ),
        "retrieval_count": sum(row["retrieval_count"] for row in by_role),
        "guidance_count": sum(row["guidance_count"] for row in by_role),
        "adopted": sum(row["adopted"] for row in by_role),
        "ignored": sum(row["ignored"] for row in by_role),
        "fallback": sum(row["fallback"] for row in by_role),
        "role_has_influence_count": sum(row["has_influence"] for row in by_role),
        "role_not_applicable_or_ignored_count": sum(
            row["not_applicable_or_ignored"] for row in by_role
        ),
        "verification_failed_count": sum(
            row["verification_failed"] for row in by_role
        ),
        "by_role": by_role,
    }


def _expected_pool(
    units: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], tuple[str, set[str]]]:
    expected: dict[tuple[str, str], tuple[str, set[str]]] = {}
    for unit in units:
        key = (str(unit["case_id"]), str(unit["role"]))
        if key in expected:
            raise RetrievalComparisonError("comparison query unit is duplicated")
        pooled = {
            str(item["doc_id"])
            for candidates in _mapping(
                unit["candidates"], "unit candidates"
            ).values()
            for item in candidates
        }
        expected[key] = (str(unit["query_sha256"]), pooled)
    return expected


def _parse_judgments(
    *,
    rows: object,
    expected: Mapping[tuple[str, str], tuple[str, set[str]]],
    name: str,
) -> dict[tuple[str, str], dict[str, int]]:
    parsed: dict[tuple[str, str], dict[str, int]] = {}
    for raw in _list(rows, name):
        row = _mapping(raw, f"{name} row")
        if set(row) != {
            "case_id",
            "role",
            "query_sha256",
            "doc_id",
            "relevance",
        }:
            raise RetrievalComparisonError(f"{name} row keys drifted")
        case_id = row["case_id"]
        role = row["role"]
        query_sha256 = row["query_sha256"]
        if (
            not isinstance(case_id, str)
            or not isinstance(role, str)
            or not isinstance(query_sha256, str)
        ):
            raise RetrievalComparisonError(f"{name} query binding is invalid")
        key = (case_id, role)
        if key not in expected or query_sha256 != expected[key][0]:
            raise RetrievalComparisonError(f"{name} query binding drifted")
        doc_id = row["doc_id"]
        relevance = row["relevance"]
        if not isinstance(doc_id, str) or doc_id not in expected[key][1]:
            raise RetrievalComparisonError(f"{name} document is outside the pool")
        if (
            isinstance(relevance, bool)
            or not isinstance(relevance, int)
            or relevance not in {0, 1, 2, 3}
        ):
            raise RetrievalComparisonError(
                f"{name} relevance must be 0, 1, 2, or 3"
            )
        bucket = parsed.setdefault(key, {})
        if doc_id in bucket:
            raise RetrievalComparisonError(f"{name} judgment is duplicated")
        bucket[doc_id] = relevance
    for key, (_, pooled) in expected.items():
        bucket = parsed.get(key)
        if bucket is None or set(bucket) != pooled:
            raise RetrievalComparisonError(f"{name} does not completely judge {key}")
    return parsed


def _linear_weighted_kappa(
    first: Mapping[tuple[str, str], Mapping[str, int]],
    second: Mapping[tuple[str, str], Mapping[str, int]],
) -> float:
    first_values: list[int] = []
    second_values: list[int] = []
    for key in sorted(first):
        for doc_id in sorted(first[key]):
            first_values.append(first[key][doc_id])
            second_values.append(second[key][doc_id])
    if not first_values or len(first_values) != len(second_values):
        raise RetrievalComparisonError("annotator judgments cannot be aligned")
    count = len(first_values)
    observed = sum(
        abs(left - right) / 3.0
        for left, right in zip(first_values, second_values, strict=True)
    ) / count
    first_marginal = [first_values.count(grade) / count for grade in range(4)]
    second_marginal = [second_values.count(grade) / count for grade in range(4)]
    expected = sum(
        (abs(left - right) / 3.0)
        * first_marginal[left]
        * second_marginal[right]
        for left in range(4)
        for right in range(4)
    )
    if expected == 0.0:
        return 1.0 if observed == 0.0 else 0.0
    return round(1.0 - observed / expected, 6)


def _load_legacy_qrels(
    *,
    path: Path,
    case_set: RegressionCaseSet,
    units: Sequence[Mapping[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, int]], dict[str, Any]]:
    value = _read_json(path, "retrieval qrels")
    if set(value) != {
        "schema_version",
        "protocol_revision",
        "case_set_id",
        "annotators",
        "adjudication",
        "agreement",
    }:
        raise RetrievalComparisonError("qrels keys drifted")
    if (
        value["schema_version"] != RETRIEVAL_QRELS_SCHEMA_VERSION
        or value["protocol_revision"] != RETRIEVAL_METRIC_PROTOCOL_REVISION
        or value["case_set_id"] != case_set.case_set_id
    ):
        raise RetrievalComparisonError("qrels authority binding drifted")
    expected = _expected_pool(units)
    annotators = _list(value["annotators"], "qrels annotators")
    if len(annotators) != 2:
        raise RetrievalComparisonError("qrels require two independent annotators")
    annotator_ids: list[str] = []
    raw_judgments: list[dict[tuple[str, str], dict[str, int]]] = []
    for index, raw in enumerate(annotators, start=1):
        annotator = _mapping(raw, f"qrels annotator {index}")
        if set(annotator) != {"annotator_id", "judgments"}:
            raise RetrievalComparisonError("qrels annotator keys drifted")
        annotator_id = annotator["annotator_id"]
        if not isinstance(annotator_id, str) or not annotator_id:
            raise RetrievalComparisonError("qrels annotator id is invalid")
        annotator_ids.append(annotator_id)
        raw_judgments.append(
            _parse_judgments(
                rows=annotator["judgments"],
                expected=expected,
                name=f"annotator {index} judgments",
            )
        )
    if len(set(annotator_ids)) != 2:
        raise RetrievalComparisonError("qrels annotator ids must be distinct")
    computed_kappa = _linear_weighted_kappa(
        raw_judgments[0], raw_judgments[1]
    )
    agreement = _mapping(value["agreement"], "qrels agreement")
    if (
        set(agreement) != {"metric", "value"}
        or agreement["metric"] != "linear_weighted_cohen_kappa"
        or agreement["value"] != computed_kappa
    ):
        raise RetrievalComparisonError("qrels agreement does not replay")
    adjudication = _mapping(value["adjudication"], "qrels adjudication")
    if set(adjudication) != {
        "adjudicator_id",
        "policy_revision",
        "judgments",
    }:
        raise RetrievalComparisonError("qrels adjudication keys drifted")
    if (
        not isinstance(adjudication["adjudicator_id"], str)
        or not adjudication["adjudicator_id"]
        or adjudication["policy_revision"]
        != "independent_raw_then_adjudicate_v1"
    ):
        raise RetrievalComparisonError("qrels adjudication policy drifted")
    judgments = _parse_judgments(
        rows=adjudication["judgments"],
        expected=expected,
        name="adjudicated judgments",
    )
    insufficient_pool_units = [
        {"case_id": key[0], "role": key[1]}
        for key, bucket in sorted(judgments.items())
        if not any(
            grade >= RELEVANT_GRADE_THRESHOLD for grade in bucket.values()
        )
    ]
    return (
        judgments,
        {
            "schema_version": RETRIEVAL_QRELS_SCHEMA_VERSION,
            "label_source": "two_independent_human_annotators",
            "annotator_count": 2,
            "annotator_ids": annotator_ids,
            "agreement_metric": "linear_weighted_cohen_kappa",
            "agreement_value": computed_kappa,
            "adjudicator_id": adjudication["adjudicator_id"],
            "procedural_independence_machine_verifiable": False,
            "insufficient_pool_unit_count": len(insufficient_pool_units),
            "insufficient_pool_units": insufficient_pool_units,
        },
    )


def _parse_partial_judgments(
    *,
    rows: object,
    expected: Mapping[tuple[str, str], tuple[str, set[str]]],
    name: str,
) -> dict[tuple[str, str], dict[str, int]]:
    parsed: dict[tuple[str, str], dict[str, int]] = {}
    for raw in _list(rows, name):
        row = _mapping(raw, name)
        if set(row) != {
            "case_id",
            "role",
            "query_sha256",
            "doc_id",
            "relevance",
        }:
            raise RetrievalComparisonError(f"{name} judgment keys drifted")
        key = (row["case_id"], row["role"])
        if key not in expected or row["query_sha256"] != expected[key][0]:
            raise RetrievalComparisonError(f"{name} query binding drifted")
        doc_id = row["doc_id"]
        relevance = row["relevance"]
        if not isinstance(doc_id, str) or doc_id not in expected[key][1]:
            raise RetrievalComparisonError(f"{name} document is outside the pool")
        if (
            isinstance(relevance, bool)
            or not isinstance(relevance, int)
            or relevance not in {0, 1, 2, 3}
        ):
            raise RetrievalComparisonError(
                f"{name} relevance must be 0, 1, 2, or 3"
            )
        bucket = parsed.setdefault(key, {})
        if doc_id in bucket:
            raise RetrievalComparisonError(f"{name} judgment is duplicated")
        bucket[doc_id] = relevance
    return parsed


def _flat_judgment_keys(
    value: Mapping[tuple[str, str], Mapping[str, int]],
) -> set[tuple[str, str, str]]:
    return {
        (case_id, role, doc_id)
        for (case_id, role), judgments in value.items()
        for doc_id in judgments
    }


def _load_assisted_qrels(
    *,
    value: Mapping[str, Any],
    case_set: RegressionCaseSet,
    units: Sequence[Mapping[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, int]], dict[str, Any]]:
    if set(value) != {
        "schema_version",
        "protocol_revision",
        "case_set_id",
        "model_prelabels",
        "human_review",
        "claim_boundary",
    }:
        raise RetrievalComparisonError("assisted qrels keys drifted")
    if (
        value["schema_version"] != RETRIEVAL_ASSISTED_QRELS_SCHEMA_VERSION
        or value["protocol_revision"] != RETRIEVAL_METRIC_PROTOCOL_REVISION
        or value["case_set_id"] != case_set.case_set_id
    ):
        raise RetrievalComparisonError("assisted qrels authority binding drifted")
    expected = _expected_pool(units)
    model = _mapping(value["model_prelabels"], "assisted qrels model prelabels")
    if set(model) != {
        "provider",
        "model_id",
        "prompt_revision",
        "request_sha256",
        "raw_response_sha256",
        "paid_api_used",
        "judgments",
    }:
        raise RetrievalComparisonError("assisted qrels model keys drifted")
    if (
        any(
            not isinstance(model[key], str) or not model[key]
            for key in (
                "provider",
                "model_id",
                "prompt_revision",
                "request_sha256",
                "raw_response_sha256",
            )
        )
        or not isinstance(model["paid_api_used"], bool)
    ):
        raise RetrievalComparisonError("assisted qrels model identity is invalid")
    model_rows = _list(model["judgments"], "assisted qrels model judgments")
    model_relevance_rows = []
    for raw in model_rows:
        row = _mapping(raw, "assisted qrels model judgment")
        if set(row) != {
            "case_id",
            "role",
            "query_sha256",
            "doc_id",
            "relevance",
            "rationale",
            "confidence",
        }:
            raise RetrievalComparisonError("assisted model judgment keys drifted")
        if not isinstance(row["rationale"], str) or not row["rationale"].strip():
            raise RetrievalComparisonError("assisted model rationale is invalid")
        if row["confidence"] not in {"low", "medium", "high"}:
            raise RetrievalComparisonError("assisted model confidence is invalid")
        model_relevance_rows.append(
            {key: row[key] for key in (
                "case_id", "role", "query_sha256", "doc_id", "relevance"
            )}
        )
    model_judgments = _parse_judgments(
        rows=model_relevance_rows,
        expected=expected,
        name="assisted model judgments",
    )
    human = _mapping(value["human_review"], "assisted qrels human review")
    if set(human) != {
        "protocol_revision",
        "reviewer_ids",
        "full_human_coverage",
        "primary_judgments",
        "blind_overlap_audit",
        "joint_resolution",
        "final_judgments",
    }:
        raise RetrievalComparisonError("assisted human review keys drifted")
    if human["protocol_revision"] != "llm_prelabel_split_review_overlap84_v1":
        raise RetrievalComparisonError("assisted human review protocol drifted")
    reviewer_ids = _list(human["reviewer_ids"], "assisted reviewer ids")
    if (
        len(reviewer_ids) != 2
        or len(set(reviewer_ids)) != 2
        or any(not isinstance(value, str) or not value for value in reviewer_ids)
        or human["full_human_coverage"] is not True
    ):
        raise RetrievalComparisonError("assisted reviewer identities are invalid")
    primary_rows = _list(human["primary_judgments"], "assisted primary judgments")
    if len(primary_rows) != 2:
        raise RetrievalComparisonError("assisted primary review count drifted")
    primary_maps = []
    primary_ids = []
    for index, raw in enumerate(primary_rows, start=1):
        record = _mapping(raw, f"assisted primary reviewer {index}")
        if set(record) != {"reviewer_id", "judgments"}:
            raise RetrievalComparisonError("assisted primary reviewer keys drifted")
        primary_ids.append(record["reviewer_id"])
        primary_maps.append(
            _parse_partial_judgments(
                rows=record["judgments"],
                expected=expected,
                name=f"assisted primary reviewer {index}",
            )
        )
    if primary_ids != reviewer_ids:
        raise RetrievalComparisonError("assisted primary reviewer order drifted")
    audit = _mapping(human["blind_overlap_audit"], "blind overlap audit")
    if set(audit) != {
        "candidate_count",
        "model_suggestion_hidden",
        "reviewer_judgments",
        "agreement",
    }:
        raise RetrievalComparisonError("blind overlap audit keys drifted")
    if audit["candidate_count"] != 84 or audit["model_suggestion_hidden"] is not True:
        raise RetrievalComparisonError("blind overlap audit policy drifted")
    audit_rows = _list(audit["reviewer_judgments"], "blind audit reviewers")
    if len(audit_rows) != 2:
        raise RetrievalComparisonError("blind audit reviewer count drifted")
    audit_maps = []
    audit_ids = []
    for index, raw in enumerate(audit_rows, start=1):
        record = _mapping(raw, f"blind audit reviewer {index}")
        if set(record) != {"reviewer_id", "judgments"}:
            raise RetrievalComparisonError("blind audit reviewer keys drifted")
        audit_ids.append(record["reviewer_id"])
        audit_maps.append(
            _parse_partial_judgments(
                rows=record["judgments"],
                expected=expected,
                name=f"blind audit reviewer {index}",
            )
        )
    if audit_ids != reviewer_ids:
        raise RetrievalComparisonError("blind audit reviewer order drifted")
    audit_key_sets = [_flat_judgment_keys(rows) for rows in audit_maps]
    if (
        audit_key_sets[0] != audit_key_sets[1]
        or len(audit_key_sets[0]) != 84
    ):
        raise RetrievalComparisonError("blind audit candidate set drifted")
    computed_kappa = _linear_weighted_kappa(audit_maps[0], audit_maps[1])
    agreement = _mapping(audit["agreement"], "blind audit agreement")
    if agreement != {
        "metric": "linear_weighted_cohen_kappa",
        "value": computed_kappa,
    }:
        raise RetrievalComparisonError("blind audit agreement does not replay")
    joint = _mapping(human["joint_resolution"], "assisted joint resolution")
    if set(joint) != {"reviewer_ids", "judgments"} or joint["reviewer_ids"] != reviewer_ids:
        raise RetrievalComparisonError("assisted joint resolution keys drifted")
    joint_map = _parse_partial_judgments(
        rows=joint["judgments"],
        expected=expected,
        name="assisted joint resolution",
    )
    if _flat_judgment_keys(joint_map) != audit_key_sets[0]:
        raise RetrievalComparisonError("assisted joint resolution set drifted")
    primary_keys = [_flat_judgment_keys(rows) for rows in primary_maps]
    if primary_keys[0] & primary_keys[1] or any(
        keys & audit_key_sets[0] for keys in primary_keys
    ):
        raise RetrievalComparisonError("assisted review partitions overlap")
    expected_keys = {
        (case_id, role, doc_id)
        for (case_id, role), (_, doc_ids) in expected.items()
        for doc_id in doc_ids
    }
    if primary_keys[0] | primary_keys[1] | audit_key_sets[0] != expected_keys:
        raise RetrievalComparisonError("assisted review does not cover the pool")
    judgments = _parse_judgments(
        rows=human["final_judgments"],
        expected=expected,
        name="assisted final judgments",
    )
    composed: dict[tuple[str, str], dict[str, int]] = {}
    for source in (*primary_maps, joint_map):
        for key, bucket in source.items():
            composed.setdefault(key, {}).update(bucket)
    if judgments != composed:
        raise RetrievalComparisonError("assisted final judgments do not replay")
    insufficient_pool_units = [
        {"case_id": key[0], "role": key[1]}
        for key, bucket in sorted(judgments.items())
        if not any(grade >= RELEVANT_GRADE_THRESHOLD for grade in bucket.values())
    ]
    return judgments, {
        "schema_version": RETRIEVAL_ASSISTED_QRELS_SCHEMA_VERSION,
        "label_source": "llm_assisted_split_human_review_exploratory",
        "model_provider": model["provider"],
        "model_id": model["model_id"],
        "human_reviewer_count": 2,
        "reviewer_ids": reviewer_ids,
        "full_human_coverage": True,
        "blind_overlap_audit_count": 84,
        "agreement_metric": "linear_weighted_cohen_kappa_on_blind_overlap",
        "agreement_value": computed_kappa,
        "procedural_independence_machine_verifiable": False,
        "independent_human_gold_claim_allowed": False,
        "insufficient_pool_unit_count": len(insufficient_pool_units),
        "insufficient_pool_units": insufficient_pool_units,
    }


def _load_qrels(
    *,
    path: Path,
    case_set: RegressionCaseSet,
    units: Sequence[Mapping[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, int]], dict[str, Any]]:
    value = _read_json(path, "retrieval qrels")
    if value.get("schema_version") == RETRIEVAL_QRELS_SCHEMA_VERSION:
        return _load_legacy_qrels(path=path, case_set=case_set, units=units)
    if value.get("schema_version") == RETRIEVAL_ASSISTED_QRELS_SCHEMA_VERSION:
        return _load_assisted_qrels(value=value, case_set=case_set, units=units)
    raise RetrievalComparisonError("qrels schema version is unsupported")


def _unit_metrics(
    ranked_doc_ids: Sequence[str],
    judgments: Mapping[str, int],
    *,
    top_k: int,
) -> dict[str, float]:
    retrieved = list(ranked_doc_ids[:top_k])
    positive_total = sum(
        grade >= RELEVANT_GRADE_THRESHOLD for grade in judgments.values()
    )
    if positive_total == 0:
        raise RetrievalComparisonError("ranking metrics require positive qrels")
    relevant_seen = 0
    precision_sum = 0.0
    reciprocal_rank = 0.0
    discounted_gain = 0.0
    for rank, doc_id in enumerate(retrieved, start=1):
        grade = judgments.get(doc_id, 0)
        if grade >= RELEVANT_GRADE_THRESHOLD:
            relevant_seen += 1
            precision_sum += relevant_seen / rank
            if reciprocal_rank == 0.0:
                reciprocal_rank = 1.0 / rank
        discounted_gain += (2**grade - 1) / math.log2(rank + 1)
    ideal_grades = sorted(judgments.values(), reverse=True)[:top_k]
    ideal_discounted_gain = sum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(ideal_grades, start=1)
    )
    return {
        f"pooled_precision_at_{top_k}": round(relevant_seen / top_k, 6),
        f"pooled_recall_at_{top_k}": round(relevant_seen / positive_total, 6),
        f"pooled_mrr_at_{top_k}": round(reciprocal_rank, 6),
        f"pooled_map_at_{top_k}": round(precision_sum / positive_total, 6),
        f"pooled_ndcg_at_{top_k}": round(
            discounted_gain / ideal_discounted_gain
            if ideal_discounted_gain
            else 0.0,
            6,
        ),
    }


def _paired_rank_biserial(differences: Sequence[float]) -> float:
    nonzero = [value for value in differences if value != 0.0]
    if not nonzero:
        return 0.0
    indexed = sorted(enumerate(nonzero), key=lambda item: abs(item[1]))
    ranks = [0.0] * len(nonzero)
    position = 0
    while position < len(indexed):
        end = position + 1
        while (
            end < len(indexed)
            and abs(indexed[end][1]) == abs(indexed[position][1])
        ):
            end += 1
        average_rank = (position + 1 + end) / 2.0
        for original_index, _ in indexed[position:end]:
            ranks[original_index] = average_rank
        position = end
    positive = sum(
        rank for rank, value in zip(ranks, nonzero, strict=True) if value > 0
    )
    negative = sum(
        rank for rank, value in zip(ranks, nonzero, strict=True) if value < 0
    )
    denominator = len(nonzero) * (len(nonzero) + 1) / 2.0
    return round((positive - negative) / denominator, 6)


def _bootstrap_mean_interval(differences: Sequence[float]) -> list[float]:
    if not differences:
        raise RetrievalComparisonError("paired analysis requires case differences")
    rng = random.Random(BOOTSTRAP_SEED)
    count = len(differences)
    estimates = sorted(
        fmean(differences[rng.randrange(count)] for _ in range(count))
        for _ in range(BOOTSTRAP_REPLICATES)
    )
    lower_index = int(0.025 * (BOOTSTRAP_REPLICATES - 1))
    upper_index = int(0.975 * (BOOTSTRAP_REPLICATES - 1))
    return [round(estimates[lower_index], 6), round(estimates[upper_index], 6)]


def _exact_sign_flip_p_value(differences: Sequence[float]) -> float:
    nonzero = [float(value) for value in differences if value != 0.0]
    if not nonzero:
        return 1.0
    observed = abs(fmean(nonzero))
    extreme = 0
    total = 0
    for signs in product((-1.0, 1.0), repeat=len(nonzero)):
        permuted = abs(
            fmean(
                sign * value
                for sign, value in zip(signs, nonzero, strict=True)
            )
        )
        extreme += permuted >= observed - 1e-12
        total += 1
    return round(extreme / total, 6)


def _holm_adjust(rows: Sequence[dict[str, Any]]) -> None:
    by_metric: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_metric.setdefault(str(row["metric"]), []).append(row)
    for metric_rows in by_metric.values():
        ordered = sorted(
            metric_rows,
            key=lambda row: (
                float(row["exact_sign_flip_p_value"]),
                str(row["comparison"]),
            ),
        )
        running = 0.0
        count = len(ordered)
        for index, row in enumerate(ordered):
            adjusted = min(
                1.0,
                (count - index) * float(row["exact_sign_flip_p_value"]),
            )
            running = max(running, adjusted)
            row["holm_adjusted_p_value"] = round(running, 6)


def _paired_case_analysis(
    method_results: Sequence[Mapping[str, Any]],
    metric_names: Sequence[str],
    primary_metric_names: Sequence[str],
) -> dict[str, Any]:
    if len(method_results) < 2:
        raise RetrievalComparisonError("paired analysis requires at least two methods")
    comparisons_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    for first_raw, second_raw in combinations(method_results, 2):
        first = _mapping(first_raw, "first method result")
        second = _mapping(second_raw, "second method result")
        first_cases = {
            row["case_id"]: row
            for row in _list(first["case_macros"], "first method case macros")
        }
        second_cases = {
            row["case_id"]: row
            for row in _list(second["case_macros"], "second method case macros")
        }
        if set(first_cases) != set(second_cases):
            raise RetrievalComparisonError("paired method case sets disagree")
        comparison = f"{first['backend']}_minus_{second['backend']}"
        rows = []
        for name in metric_names:
            differences = [
                float(first_cases[case_id][name])
                - float(second_cases[case_id][name])
                for case_id in sorted(first_cases)
            ]
            row = {
                "comparison": comparison,
                "metric": name,
                "mean_paired_difference": round(fmean(differences), 6),
                "bootstrap_95_percent_interval": _bootstrap_mean_interval(
                    differences
                ),
                "exact_sign_flip_p_value": _exact_sign_flip_p_value(differences),
                "paired_rank_biserial": _paired_rank_biserial(differences),
                "positive_case_count": sum(value > 0 for value in differences),
                "tie_case_count": sum(value == 0 for value in differences),
                "negative_case_count": sum(value < 0 for value in differences),
            }
            rows.append(row)
            metric_rows.append(row)
        comparisons_rows.append(
            {
                "method_order": [first["backend"], second["backend"]],
                "difference_direction": comparison,
                "metrics": rows,
            }
        )
    _holm_adjust(metric_rows)
    primary_names = set(primary_metric_names)
    for comparison in comparisons_rows:
        primary = [
            row for row in comparison["metrics"] if row["metric"] in primary_names
        ]
        positive = len(primary) == len(primary_names) and all(
            row["bootstrap_95_percent_interval"][0] > 0
            and row["holm_adjusted_p_value"] < 0.05
            for row in primary
        )
        negative = len(primary) == len(primary_names) and all(
            row["bootstrap_95_percent_interval"][1] < 0
            and row["holm_adjusted_p_value"] < 0.05
            for row in primary
        )
        comparison["primary_metrics_same_supported_direction"] = (
            positive or negative
        )
        comparison["eligible_for_manager_winner_review"] = positive or negative
        comparison["supported_method_if_any"] = (
            comparison["method_order"][0]
            if positive
            else comparison["method_order"][1]
            if negative
            else None
        )
        comparison["winner_declared"] = False
    candidates = []
    method_names = [str(row["backend"]) for row in method_results]
    for method in method_names:
        relevant_comparisons = [
            comparison
            for comparison in comparisons_rows
            if method in comparison["method_order"]
        ]
        if len(relevant_comparisons) == len(method_names) - 1 and all(
            comparison["supported_method_if_any"] == method
            for comparison in relevant_comparisons
        ):
            candidates.append(method)
    return {
        "method_order": method_names,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "exact_test": "two_sided_paired_sign_flip_over_nonzero_case_differences",
        "multiple_comparison_control": "Holm within each metric across method pairs",
        "comparisons": comparisons_rows,
        "manager_review_candidates": candidates,
        "winner_declared": False,
    }


def evaluate_rankings(
    *,
    units: Sequence[Mapping[str, Any]],
    qrels: Mapping[tuple[str, str], Mapping[str, int]],
    methods: Sequence[str],
    top_k: int,
) -> dict[str, Any]:
    """Compute fully judged macro ranking metrics."""

    method_results: list[dict[str, Any]] = []
    metric_names = (
        f"pooled_precision_at_{top_k}",
        f"pooled_recall_at_{top_k}",
        f"pooled_mrr_at_{top_k}",
        f"pooled_map_at_{top_k}",
        f"pooled_ndcg_at_{top_k}",
    )
    for method in methods:
        rows: list[dict[str, Any]] = []
        for unit in units:
            key = (str(unit["case_id"]), str(unit["role"]))
            candidates = _mapping(unit["candidates"], "unit candidates")
            ranked = [item["doc_id"] for item in candidates[method]]
            rows.append(
                {
                    "case_id": key[0],
                    "role": key[1],
                    **_unit_metrics(ranked, qrels[key], top_k=top_k),
                }
            )
        unit_macro = {
            name: round(fmean(float(row[name]) for row in rows), 6)
            for name in metric_names
        }
        case_macros = []
        for case_id in sorted({str(row["case_id"]) for row in rows}):
            case_rows = [row for row in rows if row["case_id"] == case_id]
            if len(case_rows) != len(ROLE_ORDER):
                raise RetrievalComparisonError(
                    "ranking evaluation has an incomplete case-role block"
                )
            case_macros.append(
                {
                    "case_id": case_id,
                    **{
                        name: round(
                            fmean(float(row[name]) for row in case_rows),
                            6,
                        )
                        for name in metric_names
                    },
                }
            )
        case_macro = {
            name: round(
                fmean(float(row[name]) for row in case_macros),
                6,
            )
            for name in metric_names
        }
        by_role = []
        for role in ROLE_ORDER:
            role_rows = [row for row in rows if row["role"] == role]
            by_role.append(
                {
                    "role": role,
                    **{
                        name: round(
                            fmean(float(row[name]) for row in role_rows),
                            6,
                        )
                        for name in metric_names
                    },
                }
            )
        method_results.append(
            {
                "backend": method,
                "macro_over_cases": case_macro,
                "diagnostic_macro_over_query_role_units": unit_macro,
                "macro_by_role": by_role,
                "case_macros": case_macros,
            }
        )
    paired = _paired_case_analysis(
        method_results,
        metric_names,
        (f"pooled_recall_at_{top_k}", f"pooled_ndcg_at_{top_k}"),
    )
    return {
        "status": "computed_from_complete_two_annotator_pooled_qrels",
        "top_k": top_k,
        "query_role_unit_count": len(units),
        "statistical_unit": "case",
        "case_count": len({str(unit["case_id"]) for unit in units}),
        "methods": method_results,
        "paired_case_analysis": paired,
        "winner_declared": False,
    }


def _ranking_evaluation(
    *,
    qrels_path: Path | None,
    case_set: RegressionCaseSet,
    units: Sequence[Mapping[str, Any]],
    methods: Sequence[str],
    top_k: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if qrels_path is None:
        return (
            {
                "status": "not_computed_no_independent_qrels",
                "top_k": top_k,
                "winner_declared": False,
                "reason": (
                    "candidate overlap and downstream utility are diagnostics, "
                    "not relevance judgments"
                ),
            },
            None,
        )
    judgments, qrels_metadata = _load_qrels(
        path=qrels_path,
        case_set=case_set,
        units=units,
    )
    qrels_binding = {
        "schema_version": qrels_metadata["schema_version"],
        "sha256": _file_sha256(qrels_path),
        "judgment_count": sum(len(bucket) for bucket in judgments.values()),
        **qrels_metadata,
    }
    if qrels_metadata["insufficient_pool_unit_count"]:
        return (
            {
                "status": "not_computed_insufficient_pooled_relevance",
                "top_k": top_k,
                "winner_declared": False,
                "insufficient_pool_units": qrels_metadata[
                    "insufficient_pool_units"
                ],
                "reason": (
                    "at least one case-role pool has no adjudicated grade-2+ "
                    "candidate; honest ratings are retained and no winner is computed"
                ),
            },
            qrels_binding,
        )
    evaluation = evaluate_rankings(
        units=units,
        qrels=judgments,
        methods=methods,
        top_k=top_k,
    )
    if qrels_metadata["schema_version"] == RETRIEVAL_ASSISTED_QRELS_SCHEMA_VERSION:
        evaluation["status"] = (
            "computed_from_llm_assisted_human_reviewed_pooled_qrels"
        )
    return (
        evaluation,
        qrels_binding,
    )


def _metric_policy(top_k: int) -> dict[str, Any]:
    return {
        "protocol_revision": RETRIEVAL_METRIC_PROTOCOL_REVISION,
        "ranking_primary": [
            f"pooled_ndcg_at_{top_k}",
            f"pooled_recall_at_{top_k}",
        ],
        "ranking_secondary": [
            f"pooled_mrr_at_{top_k}",
            f"pooled_map_at_{top_k}",
            f"pooled_precision_at_{top_k}",
        ],
        "aggregation": (
            "primary comparison uses paired macros over twelve cases; role and "
            "query-unit macros are diagnostic"
        ),
        "judgment_scope": (
            "pooled candidates from the compared methods at the fixed ranking depth"
        ),
        "qrels_gate": (
            "validated qrels must completely cover the method-blind pool; the "
            "current low-human-time protocol uses one full LLM prelabel pass, "
            "split full-coverage human review, an 84-candidate blind two-human "
            f"overlap audit, and joint resolution; every case-role query has at "
            f"least one grade-{RELEVANT_GRADE_THRESHOLD}+ judgment"
        ),
        "relevance_grades": {
            "0": "not relevant",
            "1": "marginally relevant",
            "2": "relevant",
            "3": "highly relevant",
        },
        "binary_relevance_threshold": RELEVANT_GRADE_THRESHOLD,
        "ndcg_gain": "2**relevance - 1",
        "agreement": "linear weighted Cohen kappa over raw 0-3 ratings",
        "paired_uncertainty": (
            f"deterministic {BOOTSTRAP_REPLICATES}-replicate case bootstrap with "
            f"seed {BOOTSTRAP_SEED}, exact paired sign-flip tests, Holm correction "
            "within each metric, and paired rank-biserial effect size"
        ),
        "task_utility_diagnostics": [
            "guidance_count",
            "adopted",
            "ignored",
            "fallback",
            "role_has_influence_count",
            "consistency_pass",
        ],
        "difference_only_diagnostics": [
            "top1_agreement",
            "overlap_count_at_k",
            "jaccard_at_k",
        ],
        "efficiency": (
            "measure load-plus-first-suite latency, warm suite latency, and Python "
            "allocation peak separately; do not merge efficiency with relevance "
            "or downstream utility"
        ),
    }


def _build_report(
    *,
    case_set: RegressionCaseSet,
    fixture_path: Path,
    index_dir: Path,
    method_root: Path,
    methods: Sequence[str],
    ranking_top_k: int,
    pipeline_top_k: int,
    qrels_path: Path | None,
) -> dict[str, Any]:
    units = build_candidate_units(
        case_set=case_set,
        index_dir=index_dir,
        methods=methods,
        top_k=ranking_top_k,
    )
    method_runs = []
    for method in methods:
        aggregate = _read_json(
            method_root / method / "aggregate_report.json",
            f"{method} aggregate report",
        )
        method_runs.append(
            {
                "backend": method,
                "pipeline_top_k": pipeline_top_k,
                "package_count": aggregate.get("case_count"),
                "utility": _method_utility(aggregate),
            }
        )
    ranking, qrels_binding = _ranking_evaluation(
        qrels_path=qrels_path,
        case_set=case_set,
        units=units,
        methods=methods,
        top_k=ranking_top_k,
    )
    return {
        "schema_version": RETRIEVAL_COMPARISON_SCHEMA_VERSION,
        "status": (
            "comparison_complete_with_validated_qrels"
            if qrels_binding is not None
            else "diagnostic_comparison_complete_no_independent_qrels"
        ),
        "case_set_id": case_set.case_set_id,
        "case_count": len(case_set.cases),
        "role_count": len(ROLE_ORDER),
        "query_role_unit_count": len(units),
        "methods": list(methods),
        "ranking_top_k": ranking_top_k,
        "pipeline_top_k": pipeline_top_k,
        "source_identities": _source_identities(fixture_path, index_dir),
        "method_runs": method_runs,
        "pairwise_summary": _pairwise_summary(units),
        "ranking_evaluation": ranking,
        "qrels_binding": qrels_binding,
        "metric_policy": _metric_policy(ranking_top_k),
        "units": units,
        "claim_boundary": (
            "local deterministic retrieval diagnostics on the frozen Demo v2 "
            "cases only; without independent qrels no method-quality winner is "
            "declared; this is not H1/gold, formal evaluation, model quality, "
            "broad generalization, or production evidence"
        ),
    }


def _manifest_files(root: Path, methods: Sequence[str]) -> list[dict[str, Any]]:
    relative_paths = ["comparison_report.json"]
    for method in methods:
        relative_paths.extend(
            [
                f"methods/{method}/aggregate_manifest.json",
                f"methods/{method}/aggregate_matrix.csv",
                f"methods/{method}/aggregate_report.json",
            ]
        )
    rows = []
    for relative in sorted(relative_paths):
        _safe_relative(relative)
        path = root / Path(relative)
        rows.append(
            {
                "path": relative,
                "sha256": _file_sha256(path),
                "byte_length": path.stat().st_size,
            }
        )
    return rows


def _write_json(path: Path, value: object) -> None:
    if path.exists() or path.is_symlink():
        raise RetrievalComparisonError(f"comparison output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json_bytes(value))


def run_retrieval_comparison(
    *,
    fixture_path: Path,
    index_dir: Path,
    output_root: Path,
    methods: Sequence[str] = SUPPORTED_METHODS,
    ranking_top_k: int = DEFAULT_RANKING_TOP_K,
    pipeline_top_k: int = DEFAULT_PIPELINE_TOP_K,
    qrels_path: Path | None = None,
) -> dict[str, Any]:
    """Run three deterministic backends and publish one comparison report."""

    fixture = Path(fixture_path).resolve(strict=True)
    index = Path(index_dir).resolve(strict=True)
    qrels = Path(qrels_path).resolve(strict=True) if qrels_path is not None else None
    target = Path(output_root).resolve()
    if target.exists() or target.is_symlink():
        raise RetrievalComparisonError("comparison output root already exists")
    if tuple(methods) != tuple(sorted(set(methods))):
        raise RetrievalComparisonError("methods must be sorted and unique")
    if tuple(methods) != SUPPORTED_METHODS:
        raise RetrievalComparisonError(
            "the v2 comparison fixes methods to bm25, rrf, and tfidf"
        )
    case_set = RegressionCaseSet.load(fixture)
    build_root = target.with_name(f".{target.name}.building")
    if build_root.exists() or build_root.is_symlink():
        raise RetrievalComparisonError("comparison build root already exists")
    build_root.mkdir(parents=True)
    try:
        method_root = build_root / "methods"
        for method in methods:
            DemoV2RegressionRunner(
                index_dir=index,
                top_k=pipeline_top_k,
                backend=method,
            ).run(case_set, method_root / method)
        report = _build_report(
            case_set=case_set,
            fixture_path=fixture,
            index_dir=index,
            method_root=method_root,
            methods=methods,
            ranking_top_k=ranking_top_k,
            pipeline_top_k=pipeline_top_k,
            qrels_path=qrels,
        )
        _write_json(build_root / "comparison_report.json", report)
        manifest = {
            "schema_version": RETRIEVAL_COMPARISON_MANIFEST_SCHEMA_VERSION,
            "status": "complete",
            "methods": list(methods),
            "files": _manifest_files(build_root, methods),
        }
        _write_json(build_root / "comparison_manifest.json", manifest)
        validate_retrieval_comparison(
            fixture_path=fixture,
            index_dir=index,
            output_root=build_root,
            qrels_path=qrels,
        )
        build_root.rename(target)
        return validate_retrieval_comparison(
            fixture_path=fixture,
            index_dir=index,
            output_root=target,
            qrels_path=qrels,
        )
    except Exception:
        shutil.rmtree(build_root, ignore_errors=True)
        raise


def validate_retrieval_comparison(
    *,
    fixture_path: Path,
    index_dir: Path,
    output_root: Path,
    qrels_path: Path | None = None,
) -> dict[str, Any]:
    """Pure-read replay of one complete retrieval comparison."""

    fixture = Path(fixture_path).resolve(strict=True)
    index = Path(index_dir).resolve(strict=True)
    root = Path(output_root).resolve(strict=True)
    qrels = Path(qrels_path).resolve(strict=True) if qrels_path is not None else None
    if root.is_symlink() or not root.is_dir():
        raise RetrievalComparisonError("comparison output root is invalid")
    case_set = RegressionCaseSet.load(fixture)
    for method in SUPPORTED_METHODS:
        RegressionSuiteValidator().validate(
            case_set,
            root / "methods" / method,
        )
    report = _read_json(root / "comparison_report.json", "comparison report")
    expected = _build_report(
        case_set=case_set,
        fixture_path=fixture,
        index_dir=index,
        method_root=root / "methods",
        methods=SUPPORTED_METHODS,
        ranking_top_k=DEFAULT_RANKING_TOP_K,
        pipeline_top_k=DEFAULT_PIPELINE_TOP_K,
        qrels_path=qrels,
    )
    if report != expected:
        raise RetrievalComparisonError("comparison report does not replay exactly")
    manifest = _read_json(
        root / "comparison_manifest.json",
        "comparison manifest",
    )
    if set(manifest) != {"schema_version", "status", "methods", "files"}:
        raise RetrievalComparisonError("comparison manifest keys drifted")
    if (
        manifest["schema_version"]
        != RETRIEVAL_COMPARISON_MANIFEST_SCHEMA_VERSION
        or manifest["status"] != "complete"
        or manifest["methods"] != list(SUPPORTED_METHODS)
        or manifest["files"] != _manifest_files(root, SUPPORTED_METHODS)
    ):
        raise RetrievalComparisonError("comparison manifest drifted")
    return report


__all__ = [
    "RETRIEVAL_ASSISTED_QRELS_SCHEMA_VERSION",
    "BOOTSTRAP_REPLICATES",
    "BOOTSTRAP_SEED",
    "DEFAULT_PIPELINE_TOP_K",
    "DEFAULT_RANKING_TOP_K",
    "RELEVANT_GRADE_THRESHOLD",
    "RETRIEVAL_COMPARISON_MANIFEST_SCHEMA_VERSION",
    "RETRIEVAL_COMPARISON_SCHEMA_VERSION",
    "RETRIEVAL_METRIC_PROTOCOL_REVISION",
    "RETRIEVAL_QRELS_SCHEMA_VERSION",
    "RetrievalComparisonError",
    "SUPPORTED_METHODS",
    "build_candidate_units",
    "evaluate_rankings",
    "run_retrieval_comparison",
    "validate_retrieval_comparison",
]
