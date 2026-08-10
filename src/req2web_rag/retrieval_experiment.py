"""Prepare a blinded, locally reproducible retrieval experiment packet.

This module does not produce relevance judgments or declare a winning method.
It binds the deterministic BM25/RRF/TF-IDF comparison, emits a provider-neutral
method-blind LLM prelabel packet and a deterministic two-human review plan, and
records descriptive local efficiency measurements for the same fixed workload.
"""

from __future__ import annotations

import gc
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import platform
import shutil
from statistics import median
from time import perf_counter_ns
import tracemalloc
from typing import Any, Mapping, Sequence

from req2web_agent import DeterministicRequirementProvider, build_retrieval_queries
from req2web_generation.demo_regression import RegressionCaseSet
from req2web_rag.corpus import ROLE_ORDER
from req2web_rag.retrieval_comparison import (
    DEFAULT_RANKING_TOP_K,
    RETRIEVAL_ASSISTED_QRELS_SCHEMA_VERSION,
    RETRIEVAL_METRIC_PROTOCOL_REVISION,
    SUPPORTED_METHODS,
    validate_retrieval_comparison,
)
from req2web_rag.retriever import RetrieverConfig, create_retriever


RETRIEVAL_EXPERIMENT_SCHEMA_VERSION = "req2web.retrieval.experiment.v4"
RETRIEVAL_EXPERIMENT_MANIFEST_SCHEMA_VERSION = (
    "req2web.retrieval.experiment.manifest.v4"
)
RETRIEVAL_PRELABEL_SCHEMA_VERSION = "req2web.retrieval.llm_prelabel.v1"
RETRIEVAL_REVIEW_ASSIGNMENT_SCHEMA_VERSION = (
    "req2web.retrieval.human_review_assignment.v1"
)
ANNOTATION_RANDOMIZATION_REVISION = "sha256_llm_unit_document_v1"
HUMAN_REVIEW_ASSIGNMENT_REVISION = "split_full_coverage_overlap84_v1"
LLM_PRELABEL_PROMPT_REVISION = "method_blind_pooled_relevance_0_3_v1"
EFFICIENCY_MEASUREMENT_REVISION = "local_process_sequential_v1"
WARM_SUITE_REPEATS = 5
HUMAN_REVIEWER_SLOTS = ("human_reviewer_1", "human_reviewer_2")
BLIND_OVERLAP_AUDIT_COUNT = 84

ROLE_JUDGMENT_FOCUS = {
    "requirement": (
        "Judge whether the candidate helps specify comparable requirements, "
        "user goals, scope, or constraints for this application."
    ),
    "ui_reference": (
        "Judge whether the candidate provides a useful visual layout, control "
        "structure, page type, or visible-copy reference for this application."
    ),
    "interaction_flow": (
        "Judge whether the candidate provides useful interaction steps, page "
        "states, transitions, or state-change behavior for this application."
    ),
    "implementation": (
        "Judge whether the candidate provides useful frontend structure, "
        "components, responsive layout, or prototype implementation evidence."
    ),
    "validation": (
        "Judge whether the candidate provides useful acceptance checks, error "
        "flows, invalid-input behavior, permissions, or state validation."
    ),
}

SOURCE_SNAPSHOT_PATHS = (
    "src/req2web_agent/chain.py",
    "src/req2web_agent/understanding.py",
    "src/req2web_generation/demo_regression.py",
    "src/req2web_rag/corpus.py",
    "src/req2web_rag/index.py",
    "src/req2web_rag/retrieval_comparison.py",
    "src/req2web_rag/retrieval_experiment.py",
    "src/req2web_rag/retrieval_qrels.py",
    "src/req2web_rag/retriever.py",
    "src/req2web_rag/schema.py",
)


class RetrievalExperimentError(ValueError):
    """Raised when preparation evidence is incomplete or has drifted."""


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
        raise RetrievalExperimentError("value is not canonical JSON") from exc


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RetrievalExperimentError(f"{name} must be an object")
    return dict(value)


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise RetrievalExperimentError(f"{name} must be a list")
    return value


def _read_json(path: Path, name: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RetrievalExperimentError(f"{name} is unavailable")
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalExperimentError(f"{name} is not UTF-8 JSON") from exc
    return _mapping(value, name)


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RetrievalExperimentError(f"source file is unavailable: {path}")
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
        raise RetrievalExperimentError("experiment path is not safely relative")
    return value


def _write_json(path: Path, value: object) -> None:
    if path.exists() or path.is_symlink():
        raise RetrievalExperimentError(f"experiment output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json_bytes(value))


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _source_snapshot() -> list[dict[str, Any]]:
    root = _repository_root()
    rows = []
    for relative in SOURCE_SNAPSHOT_PATHS:
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


def _query_workload(case_set: RegressionCaseSet) -> list[dict[str, str]]:
    provider = DeterministicRequirementProvider()
    rows: list[dict[str, str]] = []
    for case in case_set.cases:
        understanding = provider.understand(
            case.requirement,
            target_device=case.target_device,
            task_type=case.task_type,
            constraints=case.constraints,
        )
        queries = build_retrieval_queries(understanding)
        for role in ROLE_ORDER:
            query = queries[role]
            rows.append(
                {
                    "case_id": case.case_id,
                    "role": role,
                    "query": query,
                    "query_sha256": sha256(query.encode("utf-8")).hexdigest(),
                }
            )
    return rows


def _query_set_sha256(workload: Sequence[Mapping[str, str]]) -> str:
    bindings = [
        {
            "case_id": row["case_id"],
            "role": row["role"],
            "query_sha256": row["query_sha256"],
        }
        for row in workload
    ]
    return sha256(_canonical_json_bytes(bindings)).hexdigest()


def _english_case_contexts(case_set: RegressionCaseSet) -> dict[str, dict[str, Any]]:
    provider = DeterministicRequirementProvider()
    contexts: dict[str, dict[str, Any]] = {}
    for case in case_set.cases:
        understanding = provider.understand(
            case.requirement,
            target_device=case.target_device,
            task_type=case.task_type,
            constraints=case.constraints,
        )
        context = {
            "requirement_summary": understanding.requirement_summary,
            "target_device": understanding.target_device,
            "task_type": understanding.task_type,
            "use_cases": [
                {
                    "title": use_case.title,
                    "expected_outcome": use_case.expected_outcome,
                }
                for use_case in understanding.use_cases
            ],
        }
        visible_text = _canonical_json_bytes(context).decode("utf-8")
        if any("\u3400" <= character <= "\u9fff" for character in visible_text):
            raise RetrievalExperimentError(
                "annotation judgment context must use the English projection"
            )
        contexts[case.case_id] = context
    return contexts


def _pooled_candidates(unit: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates_by_method = _mapping(unit["candidates"], "unit candidates")
    pooled: dict[str, dict[str, Any]] = {}
    for method in SUPPORTED_METHODS:
        for raw in _list(candidates_by_method[method], f"{method} candidates"):
            candidate = _mapping(raw, "candidate")
            view = {
                key: candidate[key]
                for key in ("doc_id", "dataset", "subset", "title", "summary")
            }
            visible_text = _canonical_json_bytes(view).decode("utf-8")
            if any("\u3400" <= character <= "\u9fff" for character in visible_text):
                raise RetrievalExperimentError(
                    "annotation candidate views must use the English projection"
                )
            prior = pooled.setdefault(str(candidate["doc_id"]), view)
            if prior != view:
                raise RetrievalExperimentError(
                    "the same pooled document has inconsistent visible content"
                )
    return [pooled[doc_id] for doc_id in sorted(pooled)]


def _candidate_order_key(
    *,
    slot: str,
    case_id: str,
    role: str,
    query_sha256: str,
    doc_id: str,
) -> str:
    value = "|".join((slot, case_id, role, query_sha256, doc_id))
    return sha256(value.encode("utf-8")).hexdigest()


def _llm_prelabel_packet(
    *,
    report: Mapping[str, Any],
    case_set: RegressionCaseSet,
) -> dict[str, Any]:
    slot = "llm_prelabel"
    contexts = _english_case_contexts(case_set)
    units = []
    for raw in _list(report["units"], "comparison units"):
        unit = _mapping(raw, "comparison unit")
        case_id = str(unit["case_id"])
        role = str(unit["role"])
        query_sha256 = str(unit["query_sha256"])
        candidates = _pooled_candidates(unit)
        candidates.sort(
            key=lambda candidate: _candidate_order_key(
                slot=slot,
                case_id=case_id,
                role=role,
                query_sha256=query_sha256,
                doc_id=str(candidate["doc_id"]),
            )
        )
        units.append(
            {
                "annotation_item_id": sha256(
                    f"{case_id}|{role}|{query_sha256}".encode("utf-8")
                ).hexdigest(),
                "case_id": case_id,
                "role": role,
                "query_sha256": query_sha256,
                "judgment_context": {
                    **contexts[case_id],
                    "role_focus": ROLE_JUDGMENT_FOCUS[role],
                },
                "candidates": [
                    {
                        **candidate,
                        "suggested_relevance": None,
                        "rationale": None,
                        "confidence": None,
                    }
                    for candidate in candidates
                ],
            }
        )
    request_view = {
        "prompt_revision": LLM_PRELABEL_PROMPT_REVISION,
        "instructions": [
            "Judge each candidate only against the displayed application and role context.",
            "Assign one suggested integer relevance grade from 0 through 3.",
            "Give a concise evidence-grounded rationale and low, medium, or high confidence.",
            "Do not force a relevant grade when no pooled candidate deserves one.",
            "Do not infer retrieval method, source rank, or source score.",
        ],
        "units": [
            {
                **unit,
                "candidates": [
                    {
                        key: candidate[key]
                        for key in ("doc_id", "dataset", "subset", "title", "summary")
                    }
                    for candidate in unit["candidates"]
                ],
            }
            for unit in units
        ],
    }
    return {
        "schema_version": RETRIEVAL_PRELABEL_SCHEMA_VERSION,
        "protocol_revision": RETRIEVAL_METRIC_PROTOCOL_REVISION,
        "case_set_id": case_set.case_set_id,
        "annotation_slot": "llm_prelabel",
        "status": "template_unscored",
        "randomization_revision": ANNOTATION_RANDOMIZATION_REVISION,
        "prompt_revision": LLM_PRELABEL_PROMPT_REVISION,
        "request_sha256": sha256(_canonical_json_bytes(request_view)).hexdigest(),
        "model_run": {
            "provider": None,
            "model_id": None,
            "raw_response_sha256": None,
            "paid_api_used": None,
        },
        "method_blinding": {
            "retrieval_method_hidden": True,
            "source_rank_hidden": True,
            "source_score_hidden": True,
            "pooled_candidates_deduplicated": True,
        },
        "instructions": request_view["instructions"],
        "relevance_scale": {
            "0": "Not relevant to the displayed application and role.",
            "1": "Marginally relevant but not sufficient as useful evidence.",
            "2": "Relevant and useful evidence for the displayed role.",
            "3": "Highly relevant and directly useful evidence for the displayed role.",
        },
        "binary_relevance_threshold": 2,
        "judgment_view_boundary": (
            "The exact frozen retrieval query is bound by SHA-256. The packet "
            "shows the existing deterministic English requirement projection "
            "and omits the frozen non-English source requirement and constraints."
        ),
        "units": units,
    }


def _review_assignment(report: Mapping[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for raw in _list(report["units"], "comparison units"):
        unit = _mapping(raw, "comparison unit")
        case_id = str(unit["case_id"])
        role = str(unit["role"])
        query_sha256 = str(unit["query_sha256"])
        candidates = _pooled_candidates(unit)
        candidates.sort(
            key=lambda candidate: _candidate_order_key(
                slot="blind_overlap_seed",
                case_id=case_id,
                role=role,
                query_sha256=query_sha256,
                doc_id=str(candidate["doc_id"]),
            )
        )
        for index, candidate in enumerate(candidates):
            rows.append(
                {
                    "case_id": case_id,
                    "role": role,
                    "query_sha256": query_sha256,
                    "doc_id": str(candidate["doc_id"]),
                    "unit_seed_audit": index == 0,
                }
            )
    if len(rows) != 420:
        raise RetrievalExperimentError("the frozen pooled candidate count drifted")
    audit_keys = {
        (row["case_id"], row["role"], row["doc_id"])
        for row in rows
        if row["unit_seed_audit"]
    }
    remaining = [
        row
        for row in rows
        if (row["case_id"], row["role"], row["doc_id"]) not in audit_keys
    ]
    remaining.sort(
        key=lambda row: sha256(
            (
                "blind_overlap_extra|"
                f"{row['case_id']}|{row['role']}|{row['query_sha256']}|{row['doc_id']}"
            ).encode("utf-8")
        ).hexdigest()
    )
    extra_count = BLIND_OVERLAP_AUDIT_COUNT - len(audit_keys)
    audit_keys.update(
        (row["case_id"], row["role"], row["doc_id"])
        for row in remaining[:extra_count]
    )
    non_audit = [
        row
        for row in rows
        if (row["case_id"], row["role"], row["doc_id"]) not in audit_keys
    ]
    non_audit.sort(
        key=lambda row: sha256(
            (
                "primary_reviewer|"
                f"{row['case_id']}|{row['role']}|{row['query_sha256']}|{row['doc_id']}"
            ).encode("utf-8")
        ).hexdigest()
    )
    primary_slots = {
        (row["case_id"], row["role"], row["doc_id"]): HUMAN_REVIEWER_SLOTS[
            index % len(HUMAN_REVIEWER_SLOTS)
        ]
        for index, row in enumerate(non_audit)
    }
    assignments = []
    for row in sorted(
        rows,
        key=lambda item: (
            item["case_id"],
            ROLE_ORDER.index(item["role"]),
            item["doc_id"],
        ),
    ):
        key = (row["case_id"], row["role"], row["doc_id"])
        is_audit = key in audit_keys
        assignments.append(
            {
                "case_id": row["case_id"],
                "role": row["role"],
                "query_sha256": row["query_sha256"],
                "doc_id": row["doc_id"],
                "review_mode": (
                    "blind_overlap_audit" if is_audit else "llm_assisted_primary"
                ),
                "reviewer_slots": (
                    list(HUMAN_REVIEWER_SLOTS)
                    if is_audit
                    else [primary_slots[key]]
                ),
                "llm_suggestion_visible_during_initial_review": not is_audit,
            }
        )
    counts = {
        slot: sum(slot in row["reviewer_slots"] for row in assignments)
        for slot in HUMAN_REVIEWER_SLOTS
    }
    if (
        len(audit_keys) != BLIND_OVERLAP_AUDIT_COUNT
        or counts != {slot: 252 for slot in HUMAN_REVIEWER_SLOTS}
    ):
        raise RetrievalExperimentError("human review assignment is unbalanced")
    return {
        "schema_version": RETRIEVAL_REVIEW_ASSIGNMENT_SCHEMA_VERSION,
        "assignment_revision": HUMAN_REVIEW_ASSIGNMENT_REVISION,
        "reviewer_slots": list(HUMAN_REVIEWER_SLOTS),
        "candidate_count": len(assignments),
        "blind_overlap_audit_count": BLIND_OVERLAP_AUDIT_COUNT,
        "full_human_coverage": True,
        "judgments_per_reviewer": counts,
        "assignments": assignments,
    }


def _method_result_digest(
    *,
    retriever: object,
    workload: Sequence[Mapping[str, str]],
    top_k: int,
) -> str:
    rows = []
    for query in workload:
        results = retriever.search(
            query["query"],
            top_k=top_k,
            roles=(query["role"],),
        )
        rows.append(
            {
                "case_id": query["case_id"],
                "role": query["role"],
                "query_sha256": query["query_sha256"],
                "results": [
                    {"doc_id": result["doc_id"], "score": result["score"]}
                    for result in results
                ],
            }
        )
    return sha256(_canonical_json_bytes(rows)).hexdigest()


def _report_method_digest(report: Mapping[str, Any], method: str) -> str:
    rows = []
    for raw in _list(report["units"], "comparison units"):
        unit = _mapping(raw, "comparison unit")
        candidates = _mapping(unit["candidates"], "unit candidates")
        rows.append(
            {
                "case_id": unit["case_id"],
                "role": unit["role"],
                "query_sha256": unit["query_sha256"],
                "results": [
                    {"doc_id": row["doc_id"], "score": row["score"]}
                    for row in candidates[method]
                ],
            }
        )
    return sha256(_canonical_json_bytes(rows)).hexdigest()


def _measure_backend(
    *,
    method: str,
    index_dir: Path,
    workload: Sequence[Mapping[str, str]],
    top_k: int,
    expected_digest: str,
) -> dict[str, Any]:
    gc.collect()
    tracemalloc.start()
    try:
        start = perf_counter_ns()
        retriever = create_retriever(
            RetrieverConfig(index_dir=index_dir, backend=method)
        )
        loaded = perf_counter_ns()
        first_digest = _method_result_digest(
            retriever=retriever,
            workload=workload,
            top_k=top_k,
        )
        first_complete = perf_counter_ns()
        _, first_peak = tracemalloc.get_traced_memory()
        if first_digest != expected_digest:
            raise RetrievalExperimentError(
                f"{method} efficiency workload disagrees with the comparison"
            )
        warm_elapsed_ms: list[float] = []
        warm_peaks: list[int] = []
        warm_digests: list[str] = []
        for _ in range(WARM_SUITE_REPEATS):
            tracemalloc.reset_peak()
            warm_start = perf_counter_ns()
            digest = _method_result_digest(
                retriever=retriever,
                workload=workload,
                top_k=top_k,
            )
            warm_complete = perf_counter_ns()
            _, warm_peak = tracemalloc.get_traced_memory()
            warm_elapsed_ms.append(round((warm_complete - warm_start) / 1e6, 6))
            warm_peaks.append(warm_peak)
            warm_digests.append(digest)
        if any(digest != expected_digest for digest in warm_digests):
            raise RetrievalExperimentError(
                f"{method} warm retrieval results are not deterministic"
            )
        return {
            "backend": method,
            "load_elapsed_ms": round((loaded - start) / 1e6, 6),
            "first_suite_elapsed_ms": round(
                (first_complete - loaded) / 1e6,
                6,
            ),
            "load_plus_first_suite_elapsed_ms": round(
                (first_complete - start) / 1e6,
                6,
            ),
            "warm_suite_elapsed_ms": warm_elapsed_ms,
            "warm_suite_median_ms": round(median(warm_elapsed_ms), 6),
            "python_traced_peak_bytes_load_plus_first_suite": first_peak,
            "python_traced_peak_bytes_warm_suite_max": max(warm_peaks),
            "result_digest_sha256": expected_digest,
            "all_repetitions_exactly_match_comparison": True,
        }
    finally:
        tracemalloc.stop()


def _efficiency_report(
    *,
    report: Mapping[str, Any],
    case_set: RegressionCaseSet,
    index_dir: Path,
) -> dict[str, Any]:
    workload = _query_workload(case_set)
    if len(workload) != report["query_role_unit_count"]:
        raise RetrievalExperimentError("efficiency workload count drifted")
    report_bindings = [
        (row["case_id"], row["role"], row["query_sha256"])
        for row in _list(report["units"], "comparison units")
    ]
    live_bindings = [
        (row["case_id"], row["role"], row["query_sha256"])
        for row in workload
    ]
    if live_bindings != report_bindings:
        raise RetrievalExperimentError("efficiency query identities drifted")
    method_rows = []
    for method in SUPPORTED_METHODS:
        method_rows.append(
            _measure_backend(
                method=method,
                index_dir=index_dir,
                workload=workload,
                top_k=DEFAULT_RANKING_TOP_K,
                expected_digest=_report_method_digest(report, method),
            )
        )
    return {
        "schema_version": "req2web.retrieval.efficiency.v1",
        "status": "local_descriptive_measurement_complete",
        "measurement_revision": EFFICIENCY_MEASUREMENT_REVISION,
        "measurement_order": list(SUPPORTED_METHODS),
        "workload": {
            "case_count": len(case_set.cases),
            "role_count": len(ROLE_ORDER),
            "query_role_unit_count": len(workload),
            "top_k": DEFAULT_RANKING_TOP_K,
            "query_set_sha256": _query_set_sha256(workload),
            "warm_suite_repeats": WARM_SUITE_REPEATS,
        },
        "runtime": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "system": platform.system(),
            "system_release": platform.release(),
            "machine": platform.machine(),
            "timer": "time.perf_counter_ns",
            "memory_measurement": "Python tracemalloc process allocations",
        },
        "methods": method_rows,
        "interpretation": (
            "These process-sequential values are descriptive diagnostics. They "
            "are not OS cold-cache measurements, statistical quality evidence, "
            "or a component of the relevance winner rule."
        ),
    }


def _protocol(
    *,
    report: Mapping[str, Any],
    fixture_path: Path,
    index_dir: Path,
    comparison_root: Path,
) -> dict[str, Any]:
    index_manifest = _read_json(index_dir / "index_manifest.json", "index manifest")
    return {
        "schema_version": RETRIEVAL_EXPERIMENT_SCHEMA_VERSION,
        "status": "prepared_awaiting_llm_prelabels",
        "protocol_revision": RETRIEVAL_METRIC_PROTOCOL_REVISION,
        "experiment_name": (
            "Retrieval ranking comparison with deterministic downstream diagnostics"
        ),
        "input_binding": {
            "case_set_id": report["case_set_id"],
            "case_count": report["case_count"],
            "role_count": report["role_count"],
            "query_role_unit_count": report["query_role_unit_count"],
            "corpus_document_count": index_manifest["document_count"],
            "case_fixture_sha256": _file_sha256(fixture_path),
            "index_manifest_sha256": _file_sha256(
                index_dir / "index_manifest.json"
            ),
            "comparison_report_sha256": _file_sha256(
                comparison_root / "comparison_report.json"
            ),
            "comparison_manifest_sha256": _file_sha256(
                comparison_root / "comparison_manifest.json"
            ),
            "source_snapshot_type": "content_hash_inventory_v1",
            "source_snapshot": _source_snapshot(),
            "git_revision_status": "uncommitted_phase6_candidate_not_publicly_reproducible",
        },
        "fixed_methods": [
            {
                "backend": "bm25",
                "parameters": report["source_identities"]["bm25_parameters"],
            },
            {
                "backend": "rrf",
                "parameters": report["source_identities"]["rrf_parameters"],
            },
            {
                "backend": "tfidf",
                "parameters": {
                    "index_sha256": report["source_identities"][
                        "tfidf_index_sha256"
                    ]
                },
            },
        ],
        "ranking_depth": DEFAULT_RANKING_TOP_K,
        "judgment_pool": (
            "Deduplicated union of BM25, RRF, and TF-IDF top-five candidates for each "
            "of the sixty frozen case-role queries. Unpooled corpus documents "
            "are unknown, not irrelevant."
        ),
        "primary_metrics": ["pooled_ndcg_at_5", "pooled_recall_at_5"],
        "secondary_metrics": [
            "pooled_mrr_at_5",
            "pooled_map_at_5",
            "pooled_precision_at_5",
        ],
        "statistical_unit": "case",
        "paired_analysis": {
            "case_count": report["case_count"],
            "uncertainty": "deterministic 5000-replicate paired case bootstrap",
            "exact_test": (
                "two-sided paired sign-flip test over nonzero case differences"
            ),
            "multiple_comparison_control": (
                "Holm within each metric across the three method pairs"
            ),
            "effect_size": "paired rank-biserial correlation",
            "role_and_query_unit_macros": "diagnostic_only",
        },
        "judgment_protocol": {
            "model_prelabel_count": 1,
            "model_prelabels_are_gold": False,
            "human_reviewer_count": 2,
            "assignment_revision": HUMAN_REVIEW_ASSIGNMENT_REVISION,
            "full_human_coverage": True,
            "blind_overlap_audit_count": BLIND_OVERLAP_AUDIT_COUNT,
            "judgments_per_human_reviewer": 252,
            "blind_audit_model_suggestion_hidden": True,
            "joint_resolution_by_both_reviewers": True,
            "raw_model_and_human_ratings_retained": True,
            "agreement": (
                "linear weighted Cohen kappa over the fixed 84-candidate blind "
                "human overlap audit"
            ),
            "relevance_grades": {
                "0": "not relevant",
                "1": "marginally relevant",
                "2": "relevant",
                "3": "highly relevant",
            },
            "binary_relevance_threshold": 2,
            "ndcg_gain": "2**relevance - 1",
            "qrels_schema_version": RETRIEVAL_ASSISTED_QRELS_SCHEMA_VERSION,
            "all_low_unit_policy": (
                "Retain the honest ratings, mark the fixed pool insufficient, "
                "and do not compute a ranking winner or relabel a candidate."
            ),
        },
        "winner_rule": (
            "The tool never declares a winner automatically. Assisted qrels may "
            "support an exploratory pooled comparison only after complete LLM "
            "prelabels, full split human review, the blind overlap audit, and joint "
            "resolution validate. They are not independent human gold or formal H1."
        ),
        "separate_ledgers": [
            "pooled ranking metrics",
            "downstream guidance, adoption, fallback, influence, and consistency",
            "GUI and acceptance evidence",
            "descriptive local efficiency",
            "historical Phase 5 first-pass, browser, and semantic evidence",
        ],
        "claim_boundary": (
            "LLM-assisted, human-reviewed exploratory pooled ranking evidence on "
            "twelve frozen Demo v2 cases only. This is not independent human gold, "
            "full-corpus recall, causal downstream evidence, H1, formal quality, "
            "broad generalization, or production evidence."
        ),
    }


def _manifest_files(root: Path) -> list[dict[str, Any]]:
    paths = [
        "annotation/llm_prelabel_template.json",
        "annotation/human_review_assignment.json",
        "efficiency_report.json",
        "experiment_protocol.json",
    ]
    rows = []
    for relative in paths:
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


def _validate_efficiency_report(
    *,
    value: Mapping[str, Any],
    report: Mapping[str, Any],
    case_set: RegressionCaseSet,
) -> None:
    if value.get("schema_version") != "req2web.retrieval.efficiency.v1":
        raise RetrievalExperimentError("efficiency schema drifted")
    if value.get("measurement_revision") != EFFICIENCY_MEASUREMENT_REVISION:
        raise RetrievalExperimentError("efficiency measurement revision drifted")
    if value.get("measurement_order") != list(SUPPORTED_METHODS):
        raise RetrievalExperimentError("efficiency method order drifted")
    workload = _mapping(value.get("workload"), "efficiency workload")
    expected_workload = _query_workload(case_set)
    expected_workload_view = {
        "case_count": len(case_set.cases),
        "role_count": len(ROLE_ORDER),
        "query_role_unit_count": len(expected_workload),
        "top_k": DEFAULT_RANKING_TOP_K,
        "query_set_sha256": _query_set_sha256(expected_workload),
        "warm_suite_repeats": WARM_SUITE_REPEATS,
    }
    if workload != expected_workload_view:
        raise RetrievalExperimentError("efficiency workload drifted")
    methods = _list(value.get("methods"), "efficiency methods")
    if [row.get("backend") for row in methods] != list(SUPPORTED_METHODS):
        raise RetrievalExperimentError("efficiency method rows drifted")
    for raw in methods:
        row = _mapping(raw, "efficiency method")
        method = str(row["backend"])
        if row.get("result_digest_sha256") != _report_method_digest(report, method):
            raise RetrievalExperimentError("efficiency result digest drifted")
        if row.get("all_repetitions_exactly_match_comparison") is not True:
            raise RetrievalExperimentError("efficiency result replay failed")
        warm = row.get("warm_suite_elapsed_ms")
        numeric_fields = [
            row.get("load_elapsed_ms"),
            row.get("first_suite_elapsed_ms"),
            row.get("load_plus_first_suite_elapsed_ms"),
            row.get("warm_suite_median_ms"),
            row.get("python_traced_peak_bytes_load_plus_first_suite"),
            row.get("python_traced_peak_bytes_warm_suite_max"),
        ]
        if (
            not isinstance(warm, list)
            or len(warm) != WARM_SUITE_REPEATS
            or any(
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or number <= 0
                for number in [*warm, *numeric_fields]
            )
        ):
            raise RetrievalExperimentError("efficiency measurements are invalid")


def prepare_retrieval_experiment(
    *,
    fixture_path: Path,
    index_dir: Path,
    comparison_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Publish a blinded preparation packet and local efficiency evidence."""

    fixture = Path(fixture_path).resolve(strict=True)
    index = Path(index_dir).resolve(strict=True)
    comparison = Path(comparison_root).resolve(strict=True)
    target = Path(output_root).resolve()
    if target.exists() or target.is_symlink():
        raise RetrievalExperimentError("experiment output root already exists")
    report = validate_retrieval_comparison(
        fixture_path=fixture,
        index_dir=index,
        output_root=comparison,
    )
    if report["ranking_evaluation"]["status"] != (
        "not_computed_no_independent_qrels"
    ):
        raise RetrievalExperimentError(
            "experiment preparation requires the no-qrels comparison"
        )
    case_set = RegressionCaseSet.load(fixture)
    build_root = target.with_name(f".{target.name}.building")
    if build_root.exists() or build_root.is_symlink():
        raise RetrievalExperimentError("experiment build root already exists")
    build_root.mkdir(parents=True)
    try:
        protocol = _protocol(
            report=report,
            fixture_path=fixture,
            index_dir=index,
            comparison_root=comparison,
        )
        _write_json(build_root / "experiment_protocol.json", protocol)
        _write_json(
            build_root / "annotation" / "llm_prelabel_template.json",
            _llm_prelabel_packet(report=report, case_set=case_set),
        )
        _write_json(
            build_root / "annotation" / "human_review_assignment.json",
            _review_assignment(report),
        )
        efficiency = _efficiency_report(
            report=report,
            case_set=case_set,
            index_dir=index,
        )
        _write_json(build_root / "efficiency_report.json", efficiency)
        manifest = {
            "schema_version": RETRIEVAL_EXPERIMENT_MANIFEST_SCHEMA_VERSION,
            "status": "complete",
            "files": _manifest_files(build_root),
        }
        _write_json(build_root / "experiment_manifest.json", manifest)
        validate_retrieval_experiment(
            fixture_path=fixture,
            index_dir=index,
            comparison_root=comparison,
            output_root=build_root,
        )
        build_root.rename(target)
        return validate_retrieval_experiment(
            fixture_path=fixture,
            index_dir=index,
            comparison_root=comparison,
            output_root=target,
        )
    except Exception:
        shutil.rmtree(build_root, ignore_errors=True)
        raise


def validate_retrieval_experiment(
    *,
    fixture_path: Path,
    index_dir: Path,
    comparison_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Pure-read validation of a prepared retrieval experiment packet."""

    fixture = Path(fixture_path).resolve(strict=True)
    index = Path(index_dir).resolve(strict=True)
    comparison = Path(comparison_root).resolve(strict=True)
    root = Path(output_root).resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise RetrievalExperimentError("experiment output root is invalid")
    report = validate_retrieval_comparison(
        fixture_path=fixture,
        index_dir=index,
        output_root=comparison,
    )
    case_set = RegressionCaseSet.load(fixture)
    protocol = _read_json(root / "experiment_protocol.json", "experiment protocol")
    expected_protocol = _protocol(
        report=report,
        fixture_path=fixture,
        index_dir=index,
        comparison_root=comparison,
    )
    if protocol != expected_protocol:
        raise RetrievalExperimentError("experiment protocol does not replay exactly")
    packet = _read_json(
        root / "annotation" / "llm_prelabel_template.json",
        "LLM prelabel template",
    )
    if packet != _llm_prelabel_packet(report=report, case_set=case_set):
        raise RetrievalExperimentError("LLM prelabel template does not replay exactly")
    assignment = _read_json(
        root / "annotation" / "human_review_assignment.json",
        "human review assignment",
    )
    if assignment != _review_assignment(report):
        raise RetrievalExperimentError("human review assignment does not replay exactly")
    efficiency = _read_json(root / "efficiency_report.json", "efficiency report")
    _validate_efficiency_report(
        value=efficiency,
        report=report,
        case_set=case_set,
    )
    manifest = _read_json(root / "experiment_manifest.json", "experiment manifest")
    if set(manifest) != {"schema_version", "status", "files"}:
        raise RetrievalExperimentError("experiment manifest keys drifted")
    if (
        manifest["schema_version"]
        != RETRIEVAL_EXPERIMENT_MANIFEST_SCHEMA_VERSION
        or manifest["status"] != "complete"
        or manifest["files"] != _manifest_files(root)
    ):
        raise RetrievalExperimentError("experiment manifest drifted")
    expected_paths = {
        "annotation/llm_prelabel_template.json",
        "annotation/human_review_assignment.json",
        "efficiency_report.json",
        "experiment_manifest.json",
        "experiment_protocol.json",
    }
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        raise RetrievalExperimentError("experiment file inventory drifted")
    return {
        "status": protocol["status"],
        "case_count": report["case_count"],
        "query_role_unit_count": report["query_role_unit_count"],
        "llm_prelabel_template_count": 1,
        "human_reviewer_count": len(HUMAN_REVIEWER_SLOTS),
        "blind_overlap_audit_count": BLIND_OVERLAP_AUDIT_COUNT,
        "judgments_per_human_reviewer": 252,
        "ranking_quality_computed": False,
        "winner_declared": False,
        "efficiency": efficiency,
    }


__all__ = [
    "BLIND_OVERLAP_AUDIT_COUNT",
    "HUMAN_REVIEWER_SLOTS",
    "HUMAN_REVIEW_ASSIGNMENT_REVISION",
    "LLM_PRELABEL_PROMPT_REVISION",
    "RETRIEVAL_PRELABEL_SCHEMA_VERSION",
    "RETRIEVAL_REVIEW_ASSIGNMENT_SCHEMA_VERSION",
    "RETRIEVAL_EXPERIMENT_MANIFEST_SCHEMA_VERSION",
    "RETRIEVAL_EXPERIMENT_SCHEMA_VERSION",
    "RetrievalExperimentError",
    "prepare_retrieval_experiment",
    "validate_retrieval_experiment",
]
