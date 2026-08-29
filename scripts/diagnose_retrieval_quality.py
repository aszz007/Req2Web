from __future__ import annotations

"""Read-only diagnosis for the Demo v2 low-influence retrieval units.

This deliberately consumes frozen packages and the public Retriever protocol.
It neither rebuilds an Agent context nor invokes a builder, renderer, checker,
or package writer.  The expanded/rewrite searches are counterfactual evidence
only; their output is never fed back into the production chain.
"""

import argparse
import csv
import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from req2web_generation.demo_regression import RegressionCaseSet
from req2web_inspector.local_data import (
    component_report_runs_root,
    framework_evidence_root,
)
from req2web_rag import RetrieverConfig, create_retriever


SCHEMA_VERSION = "req2web.retrieval.quality.diagnosis.v1"
TARGET_ROLES = ("ui_reference", "validation")
GUIDANCE_FIELDS = {"ui_reference": "ui_guidance", "validation": "validation_guidance"}
ROOT_CAUSES = {
    "corpus_coverage_insufficient": "语料覆盖不足",
    "tfidf_query_or_ranking": "TF-IDF 查询词汇错配或排序问题",
    "guidance_extraction_unusable": "检索相关，但指导提取无法形成可用指令",
    "explicit_requirement_or_safety_gate": "证据/安全/显式需求优先门禁拒绝采纳",
    "builder_fallback_or_explicit_requirement_masking": "builder fallback 或显式需求掩盖检索影响",
    "pagespec_or_metric_blind_spot": "PageSpec 或当前消融指标存在测量盲区",
}
CSV_FIELDS = (
    "case_id", "role", "primary_root_cause", "confidence", "outcome",
    "guidance_count", "adopted", "fallback", "ignored", "difference_count",
    "original_top_doc_ids", "expanded_top5_doc_ids", "rewrite_top5_doc_ids",
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON object expected: {path}")
    return value


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _focus_query(context: dict[str, Any], role: str) -> str:
    """A fixed, generic ablation of Agent boilerplate, not a case rule."""
    focus = {
        "ui_reference": "UI 界面参考 视觉布局 控件结构 页面类型",
        "validation": "验收标准 测试点 异常流程 恢复 状态",
    }[role]
    constraints = "；".join(context["constraints"])
    return " ".join(part for part in (context["original_requirement"], constraints, focus) if part)


def _result_view(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": result["doc_id"], "score": result["score"], "dataset": result["dataset"],
        "subset": result["subset"], "role": result["role"], "title": result["title"],
        "summary": result["summary"],
    }


def _annotate_results(results: list[dict[str, Any]], guidance_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach a compact, decision-backed relevance judgment without corpus reads."""
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for item in guidance_items:
        by_doc.setdefault(item["doc_id"], []).append(item)
    annotated = []
    for result in results:
        value = dict(result)
        extracted = by_doc.get(result["doc_id"], [])
        if not extracted:
            value["relevance_judgment"] = "未进入当前 top-2 指导来源；仅作为反事实候选。"
        else:
            values = "/".join(item["value"] for item in extracted)
            dispositions = "/".join(sorted({decision["disposition"] for item in extracted for decision in item["decisions"]}))
            value["relevance_judgment"] = f"提取值 {values}；当前决策为 {dispositions or '无决策'}。"
        annotated.append(value)
    return annotated


def _decisions_by_guidance(build_result: dict[str, Any], role: str) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    for disposition in ("adopted", "fallback", "ignored"):
        for item in build_result[disposition]:
            if item["role"] != role or item["guidance_id"] is None:
                continue
            result.setdefault(item["guidance_id"], []).append({
                "disposition": disposition, "rule": item["rule"], "reason": item["reason"],
            })
    return result


def _root_cause(role: str, decisions: list[dict[str, str]], difference_count: int) -> tuple[str, str, str]:
    rules = [item["rule"] for item in decisions]
    adopted = [item for item in decisions if item["disposition"] == "adopted"]
    if adopted and difference_count == 0:
        return (
            "pagespec_or_metric_blind_spot", "high",
            "存在 adopted 决策但角色消融零差异；这会是双门禁的测量盲区。",
        )
    if any("explicit_context_gate" in item or "semantic_gate" in item for item in rules):
        return (
            "explicit_requirement_or_safety_gate", "high",
            "指导值已被提取，但现有显式需求语义门禁拒绝其成为结构来源。",
        )
    if role == "ui_reference" and any("controlled_value_required" in item for item in rules):
        return (
            "guidance_extraction_unusable", "high",
            "召回只产生 reference_screen；当前受控值表不能把它转换为可采纳的结构指令。",
        )
    if role == "validation" and any("evidence_only" in item or "supported_gate" in item for item in rules):
        return (
            "guidance_extraction_unusable", "high",
            "召回生成的多是 regression_case/URI 或当前不支持的异常值，按证据边界只能 fallback/ignored。",
        )
    return (
        "corpus_coverage_insufficient", "medium",
        "没有可采纳的受控指导或明确门禁证据；需以更大候选集判断是否为覆盖问题。",
    )


def _counterfactual_note(original: list[dict[str, Any]], expanded: list[dict[str, Any]], rewritten: list[dict[str, Any]]) -> str:
    original_ids = [item["doc_id"] for item in original]
    expanded_ids = [item["doc_id"] for item in expanded]
    rewritten_ids = [item["doc_id"] for item in rewritten]
    if rewritten_ids[:2] != original_ids:
        return "轻量改写改变前二候选：存在查询表述或排序敏感性，但未改变生产配置。"
    if expanded_ids[:2] == original_ids:
        return "扩大到 top-5 保留原前二候选：当前低影响不能仅归因于 top-k 截断。"
    return "扩大候选集改变前二关系：存在排序敏感性，但仍需由指导门禁判断是否可采纳。"


def generate_diagnosis(*, fixture: Path, package_root: Path, index_dir: Path) -> dict[str, Any]:
    """Return a deterministic report for the current low-influence target units."""
    case_set = RegressionCaseSet.load(Path(fixture))
    retriever = create_retriever(RetrieverConfig(index_dir=Path(index_dir), backend="tfidf"))
    units: list[dict[str, Any]] = []
    for case in case_set.cases:
        internal = Path(package_root) / "packages" / case.case_id / "internal"
        context = _read_json(internal / "agent_context.json")
        guidance = _read_json(internal / "retrieval_guidance.json")
        build_result = _read_json(internal / "guided_page_spec_build_result.json")
        influence = _read_json(internal / "retrieval_influence_report.json")
        ablations = {item["role"]: item for item in influence["ablations"]}
        for role in TARGET_ROLES:
            ablation = ablations[role]
            if ablation["outcome"] != "guidance_not_applicable_or_ignored":
                continue
            query = context["retrieval_queries"][role]
            by_guidance = _decisions_by_guidance(build_result, role)
            guidance_items = []
            decisions = []
            for item in guidance[GUIDANCE_FIELDS[role]]:
                item_decisions = by_guidance.get(item["guidance_id"], [])
                decisions.extend(item_decisions)
                guidance_items.append({
                    "guidance_id": item["guidance_id"], "category": item["category"], "value": item["value"],
                    "doc_id": item["source"]["doc_id"], "decisions": item_decisions,
                })
            original = _annotate_results([_result_view(item) for item in context["retrieval_results"][role]], guidance_items)
            expanded = _annotate_results([_result_view(item) for item in retriever.search(query, top_k=5, roles=(role,))], guidance_items)
            rewrite = _focus_query(context, role)
            rewritten = _annotate_results([_result_view(item) for item in retriever.search(rewrite, top_k=5, roles=(role,))], guidance_items)
            cause, confidence, evidence = _root_cause(role, decisions, ablation["difference_count"])
            units.append({
                "case_id": case.case_id, "role": role, "original_query": query,
                "original_top_k": original, "guidance": guidance_items,
                "decision_counts": dict(sorted(Counter(item["disposition"] for item in decisions).items())),
                "ablation": ablation, "primary_root_cause": cause, "confidence": confidence,
                "root_cause_evidence": evidence,
                "counterfactual": {
                    "expanded_top_k": 5, "expanded_results": expanded,
                    "rewrite_query": rewrite, "rewrite_results": rewritten,
                    "interpretation": _counterfactual_note(original, expanded, rewritten),
                },
            })
    units.sort(key=lambda item: (item["role"], item["case_id"]))
    causes = Counter(item["primary_root_cause"] for item in units)
    return {
        "schema_version": SCHEMA_VERSION, "case_set_id": case_set.case_set_id,
        "scope": {"target_roles": list(TARGET_ROLES), "target_unit_count": len(units), "read_only": True},
        "counterfactual_config": {"backend": "tfidf", "expanded_top_k": 5, "rewrite": "original_requirement + constraints + fixed role focus"},
        "root_cause_counts": {name: causes.get(name, 0) for name in ROOT_CAUSES},
        "root_cause_labels": ROOT_CAUSES, "units": units,
    }


def write_diagnosis(report: dict[str, Any], output_dir: Path) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for unit in report["units"]:
        counts = unit["decision_counts"]
        rows.append({
            "case_id": unit["case_id"], "role": unit["role"], "primary_root_cause": unit["primary_root_cause"],
            "confidence": unit["confidence"], "outcome": unit["ablation"]["outcome"],
            "guidance_count": len(unit["guidance"]), "adopted": counts.get("adopted", 0),
            "fallback": counts.get("fallback", 0), "ignored": counts.get("ignored", 0),
            "difference_count": unit["ablation"]["difference_count"],
            "original_top_doc_ids": "|".join(item["doc_id"] for item in unit["original_top_k"]),
            "expanded_top5_doc_ids": "|".join(item["doc_id"] for item in unit["counterfactual"]["expanded_results"]),
            "rewrite_top5_doc_ids": "|".join(item["doc_id"] for item in unit["counterfactual"]["rewrite_results"]),
        })
    (output / "diagnosis.json").write_bytes(_json_bytes(report))
    (output / "target_units.csv").write_bytes(_csv_bytes(rows))


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Demo v2 retrieval-quality diagnosis")
    parser.add_argument("--fixture", type=Path, default=Path("fixtures/demo_v2_regression_cases_v1.json"))
    parser.add_argument(
        "--package-root",
        type=Path,
        default=framework_evidence_root(ROOT) / "demo_regression",
    )
    parser.add_argument("--index-dir", type=Path, default=Path("data/processed/rag"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=component_report_runs_root(ROOT) / "retrieval_quality_diagnosis_v1",
    )
    args = parser.parse_args()
    write_diagnosis(generate_diagnosis(fixture=args.fixture, package_root=args.package_root, index_dir=args.index_dir), args.output_dir)


if __name__ == "__main__":
    main()
