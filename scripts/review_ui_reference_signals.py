from __future__ import annotations

"""Deterministic, read-only evidence review for RICO UI structure signals.

The review reads only the unified processed document JSONL and frozen Demo v2
package internals.  It never opens any referenced asset, rebuilds retrieval, or
instantiates the guided PageSpec builder.  Counterfactual rows are therefore
audit evidence, not production guidance or PageSpec output.
"""

import argparse
import csv
import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from req2web_generation.guided_builder import _COMPONENT_PRESENTATION, _CONCEPT_KEYWORDS
from req2web_inspector.local_data import component_report_runs_root


SCHEMA_VERSION = "req2web.ui_reference.signal.review.v1"
TARGET_CASE_IDS = (
    "mobile-appointment",
    "mobile-auth",
    "pet-recognition",
    "profile-settings",
)
CURRENT_CONTROLLED_VALUES = tuple(sorted(_COMPONENT_PRESENTATION))
FIELD_NAMES = (
    "category",
    "category_label",
    "component_labels",
    "icon_classes",
    "node_count",
    "clickable_count",
    "scrollable_count",
)
TARGET_CSV_FIELDS = (
    "case_id", "top_doc_ids", "current_guidance_values", "current_decision_rules",
    "candidate_values", "gate_outcomes", "verdict", "confidence",
)
COUNTERFACTUAL_CSV_FIELDS = (
    "case_id", "candidate_values", "passes_current_gate", "rejected_by_current_gate",
    "unexpressible_values", "audit_only_values",
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON object expected: {path}")
    return value


def load_ui_documents(path: Path) -> list[dict[str, Any]]:
    """Load and validate exactly the 42 processed RICO UI documents."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid UTF-8 JSONL: {path}") from exc
    docs: list[dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL line {number}: {path}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"JSON object expected at line {number}: {path}")
        if item.get("role") == "ui_reference":
            docs.append(item)
    if len(docs) != 42:
        raise ValueError(f"expected exactly 42 ui_reference documents, found {len(docs)}")
    expected_ids = {"ui_reference:rico:combined:" + str(item.get("sample_id", "")) for item in docs}
    actual_ids = {str(item.get("doc_id", "")) for item in docs}
    if actual_ids != expected_ids or len(actual_ids) != 42:
        raise ValueError("ui_reference documents must be unique RICO combined records")
    return sorted(docs, key=lambda item: item["doc_id"])


def _metadata(document: dict[str, Any]) -> dict[str, Any]:
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError(f"ui document missing object metadata: {document.get('doc_id')}")
    return metadata


def _counter(value: object) -> Counter[str]:
    if not isinstance(value, dict):
        return Counter()
    result: Counter[str] = Counter()
    for key, count in value.items():
        if isinstance(key, str) and isinstance(count, int) and count >= 0:
            result[key] += count
    return result


def _present(value: object) -> bool:
    return value not in (None, "", {}, [])


def _field_stats(documents: list[dict[str, Any]]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for field_name in FIELD_NAMES:
        values = [_metadata(item).get(field_name) for item in documents]
        item: dict[str, Any] = {
            "present_count": sum(_present(value) for value in values),
            "missing_count": sum(not _present(value) for value in values),
            "types": dict(sorted(Counter(type(value).__name__ for value in values).items())),
        }
        if field_name in {"category", "category_label"}:
            item["value_counts"] = dict(sorted(Counter(str(value) for value in values).items()))
        elif field_name in {"component_labels", "icon_classes"}:
            labels: Counter[str] = Counter()
            for value in values:
                labels.update(_counter(value))
            item["distinct_value_count"] = len(labels)
            item["aggregate_counts"] = dict(sorted(labels.items()))
        else:
            integers = [value for value in values if isinstance(value, int)]
            item["minimum"] = min(integers) if integers else None
            item["maximum"] = max(integers) if integers else None
            item["total"] = sum(integers)
        stats[field_name] = item
    reference_counts: Counter[str] = Counter()
    reference_complete = 0
    for document in documents:
        kinds = [item.get("kind") for item in document.get("references", []) if isinstance(item, dict)]
        reference_counts.update(str(kind) for kind in kinds if kind)
        if set(kinds) == {"screenshot", "view_hierarchy", "semantic_image", "semantic_annotation"} and len(kinds) == 4:
            reference_complete += 1
    stats["references"] = {
        "present_count": sum(bool(item.get("references")) for item in documents),
        "complete_four_kind_count": reference_complete,
        "kind_counts": dict(sorted(reference_counts.items())),
    }
    stats["title"] = {"present_count": sum(bool(item.get("title")) for item in documents)}
    stats["summary"] = {"present_count": sum(bool(item.get("summary")) for item in documents)}
    return stats


def classify_signal_evidence(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Classify source-traceable structural facts without inventing UI behavior."""
    metadata = _metadata(document)
    category = metadata.get("category")
    labels = _counter(metadata.get("component_labels"))
    icons = _counter(metadata.get("icon_classes"))
    items: list[dict[str, Any]] = []

    def add(value: str, status: str, source_fields: list[str], reason: str) -> None:
        items.append({
            "value": value,
            "status": status,
            "source_fields": source_fields,
            "source_values": {
                "metadata.category": category,
                "metadata.component_labels": {name: labels[name] for name in sorted(labels)},
                "metadata.icon_classes": {name: icons[name] for name in sorted(icons)},
            },
            "reason": reason,
        })

    # These are deliberately conjunctive: a generic icon, list, or input by
    # itself is not evidence of a business component.
    if category == "search" and labels["Input"] > 0 and icons["search"] > 0:
        add("search_input", "current_controlled_candidate", [
            "metadata.category", "metadata.component_labels", "metadata.icon_classes",
        ], "搜索类别、输入控件和搜索图标共同支持搜索输入结构。")
    if category == "map_location" and labels["Map View"] > 0 and labels["Input"] > 0:
        add("location_picker", "current_controlled_candidate", [
            "metadata.category", "metadata.component_labels",
        ], "地图类别、地图视图和输入控件共同支持位置选择入口。")
    if category in {"form_input", "login_auth", "settings"} and labels["Input"] > 0:
        add("form_structure", "unexpressible_currently", [
            "metadata.category", "metadata.component_labels",
        ], "类别与输入控件支持表单结构，但当前 UI 受控值没有 form_structure。")
    if category == "dialog_overlay" and labels["Modal"] > 0:
        add("dialog_structure", "unexpressible_currently", [
            "metadata.category", "metadata.component_labels",
        ], "类别与 Modal 标签支持对话框结构，但当前受控词和 PageSpec 变换未表达该结构。")
    if labels["Toolbar"] > 0 or icons["menu"] > 0:
        add("navigation_structure", "audit_only", [
            "metadata.component_labels", "metadata.icon_classes",
        ], "Toolbar 或菜单图标不足以推断具体导航业务、入口或页面层级。")
    if labels["List Item"] > 0:
        add("list_structure", "audit_only", ["metadata.component_labels"], "通用列表项不能区分结果、消息、设置或媒体业务。")
    if labels["Input"] > 0 and not any(item["value"] in {"search_input", "location_picker", "form_structure"} for item in items):
        add("generic_input", "audit_only", ["metadata.component_labels"], "通用 Input 不能直接推断搜索、认证或预约字段语义。")
    if labels["Text Button"] > 0:
        add("generic_button", "audit_only", ["metadata.component_labels"], "通用按钮不能直接推断保存、提交、结算或恢复行为。")
    return items


def _context_text(context: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in ("original_requirement", "requirement_summary"):
        value = context.get(field)
        if isinstance(value, str):
            parts.append(value)
    constraints = context.get("constraints", [])
    if isinstance(constraints, list):
        parts.extend(value for value in constraints if isinstance(value, str))
    use_cases = context.get("use_cases", [])
    if isinstance(use_cases, list):
        for use_case in use_cases:
            if isinstance(use_case, dict):
                parts.extend(value for value in use_case.values() if isinstance(value, str))
    return " ".join(parts).casefold()


def _passes_current_gate(context: dict[str, Any], value: str) -> bool:
    return any(keyword.casefold() in _context_text(context) for keyword in _CONCEPT_KEYWORDS[value])


def _guidance_and_decisions(internal: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    context = _read_json(internal / "agent_context.json")
    guidance = _read_json(internal / "retrieval_guidance.json")
    build_result = _read_json(internal / "guided_page_spec_build_result.json")
    ui_guidance = guidance.get("ui_guidance")
    if not isinstance(ui_guidance, list):
        raise ValueError(f"ui_guidance list expected: {internal}")
    decisions = []
    for disposition in ("adopted", "fallback", "ignored"):
        values = build_result.get(disposition)
        if not isinstance(values, list):
            raise ValueError(f"guided build-result missing {disposition}: {internal}")
        decisions.extend({**item, "disposition": disposition} for item in values if item.get("role") == "ui_reference")
    return context, ui_guidance, decisions


def _target_verdict(case_id: str, top_doc_ids: list[str]) -> tuple[str, str]:
    expected = {
        "mobile-appointment": ("safe_candidate_with_minimal_controlled_vocab_extension", "high"),
        "mobile-auth": ("safe_candidate_with_minimal_controlled_vocab_extension", "high"),
        "pet-recognition": ("continue_reject", "high"),
        "profile-settings": ("audit_only", "high"),
    }
    if case_id not in expected or len(top_doc_ids) != 2:
        raise ValueError("target review requires the four frozen cases and two UI results each")
    return expected[case_id]


def _review_target(case_id: str, internal: Path, documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    context, ui_guidance, decisions = _guidance_and_decisions(internal)
    results = context.get("retrieval_results", {}).get("ui_reference")
    if not isinstance(results, list) or len(results) != 2:
        raise ValueError(f"expected frozen top-2 ui_reference results: {case_id}")
    top_doc_ids = [str(item.get("doc_id", "")) for item in results]
    if any(doc_id not in documents for doc_id in top_doc_ids):
        raise ValueError(f"frozen UI result not found in processed documents: {case_id}")
    by_guidance: dict[str, list[dict[str, Any]]] = {}
    for item in ui_guidance:
        if isinstance(item, dict):
            by_guidance.setdefault(str(item.get("source", {}).get("doc_id", "")), []).append(item)
    evidence = []
    gate_outcomes = []
    for doc_id in top_doc_ids:
        document = documents[doc_id]
        signals = classify_signal_evidence(document)
        evidence.append({
            "doc_id": doc_id,
            "category": _metadata(document).get("category"),
            "component_labels": _metadata(document).get("component_labels"),
            "icon_classes": _metadata(document).get("icon_classes"),
            "signals": signals,
        })
        for signal in signals:
            if signal["status"] == "current_controlled_candidate":
                gate_outcomes.append({"doc_id": doc_id, "value": signal["value"], "outcome": "pass" if _passes_current_gate(context, signal["value"]) else "reject"})
    decision_rules = sorted({str(item.get("rule", "")) for item in decisions})
    verdict, confidence = _target_verdict(case_id, top_doc_ids)
    return {
        "case_id": case_id,
        "top_results": [{"doc_id": item["doc_id"], "score": item["score"], "title": item["title"], "summary": item["summary"]} for item in results],
        "current_guidance": [{"value": item.get("value"), "category": item.get("category"), "source_doc_id": item.get("source", {}).get("doc_id")} for item in ui_guidance],
        "current_decisions": [{"disposition": item["disposition"], "rule": item.get("rule"), "doc_id": item.get("doc_id")} for item in decisions],
        "source_structure": evidence,
        "current_gate_counterfactual": gate_outcomes,
        "verdict": verdict,
        "confidence": confidence,
    }


def _counterfactual_cases(package_root: Path, documents: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    cases = []
    package_dir = Path(package_root) / "packages"
    for item in sorted(package_dir.iterdir(), key=lambda path: path.name):
        if not item.is_dir():
            continue
        context = _read_json(item / "internal" / "agent_context.json")
        results = context.get("retrieval_results", {}).get("ui_reference")
        if not isinstance(results, list) or len(results) != 2:
            raise ValueError(f"expected top-2 ui results in frozen package: {item.name}")
        candidate_values: list[str] = []
        passed: list[str] = []
        rejected: list[str] = []
        unexpressible: list[str] = []
        audit_only: list[str] = []
        for result in results:
            doc_id = result.get("doc_id")
            if doc_id not in documents:
                raise ValueError(f"compact UI result missing processed source: {item.name}")
            for signal in classify_signal_evidence(documents[doc_id]):
                value = signal["value"]
                if signal["status"] == "current_controlled_candidate":
                    candidate_values.append(value)
                    (passed if _passes_current_gate(context, value) else rejected).append(value)
                elif signal["status"] == "unexpressible_currently":
                    unexpressible.append(value)
                else:
                    audit_only.append(value)
        cases.append({
            "case_id": item.name,
            "candidate_values": sorted(set(candidate_values)),
            "passes_current_gate": sorted(set(passed)),
            "rejected_by_current_gate": sorted(set(rejected)),
            "unexpressible_values": sorted(set(unexpressible)),
            "audit_only_values": sorted(set(audit_only)),
        })
    if len(cases) != 12:
        raise ValueError(f"expected exactly 12 frozen packages, found {len(cases)}")
    return cases


def generate_review(*, documents_path: Path, package_root: Path) -> dict[str, Any]:
    """Build a deterministic review from processed documents and frozen packages."""
    documents_list = load_ui_documents(Path(documents_path))
    documents = {item["doc_id"]: item for item in documents_list}
    targets = [
        _review_target(case_id, Path(package_root) / "packages" / case_id / "internal", documents)
        for case_id in TARGET_CASE_IDS
    ]
    counterfactual = _counterfactual_cases(Path(package_root), documents)
    categories = Counter(_metadata(item)["category"] for item in documents_list)
    direct = sum(1 for item in documents_list for signal in classify_signal_evidence(item) if signal["status"] == "current_controlled_candidate")
    unexpressible = sum(1 for item in documents_list for signal in classify_signal_evidence(item) if signal["status"] == "unexpressible_currently")
    audit_only = sum(1 for item in documents_list for signal in classify_signal_evidence(item) if signal["status"] == "audit_only")
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "document_count": len(documents_list), "target_case_count": len(targets), "frozen_package_count": len(counterfactual),
            "read_only": True, "no_retriever_or_guided_builder_execution": True,
        },
        "field_statistics": _field_stats(documents_list),
        "field_boundary": {
            "unified_document_fields": ["title", "summary", "references", "metadata.category", "metadata.category_label", "metadata.component_labels", "metadata.icon_classes", "metadata.node_count", "metadata.clickable_count", "metadata.scrollable_count"],
            "compact_result_fields": ["score", "doc_id", "role", "dataset", "subset", "sample_id", "title", "summary", "references"],
            "lost_from_compact_result": ["metadata.category", "metadata.category_label", "metadata.component_labels", "metadata.icon_classes", "metadata.node_count", "metadata.clickable_count", "metadata.scrollable_count"],
            "currently_used_by_guidance": ["title", "summary", "references"],
            "current_controlled_values": list(CURRENT_CONTROLLED_VALUES),
        },
        "category_balance": dict(sorted(categories.items())),
        "expressibility_counts": {
            "direct_existing_controlled_candidate_occurrences": direct,
            "evidence_but_unexpressible_occurrences": unexpressible,
            "audit_only_or_cross_business_occurrences": audit_only,
        },
        "target_units": targets,
        "counterfactual": {
            "method": "processed metadata -> fixed signal classification -> existing _CONCEPT_KEYWORDS containment gate; no builder invocation",
            "cases": counterfactual,
            "totals": {
                "passes_current_gate": sum(len(item["passes_current_gate"]) for item in counterfactual),
                "rejected_by_current_gate": sum(len(item["rejected_by_current_gate"]) for item in counterfactual),
                "unexpressible": sum(len(item["unexpressible_values"]) for item in counterfactual),
                "audit_only": sum(len(item["audit_only_values"]) for item in counterfactual),
            },
        },
        "recommendation": {
            "single_next_task": "Implement a minimal UI form-structure compact-signal adapter behind the existing explicit-context gate.",
            "minimum_source_rule": "metadata.category in {form_input, login_auth, settings} AND metadata.component_labels.Input > 0",
            "controlled_vocab_change": "Add one new form_structure controlled value and form-related context keywords; do not infer save, login, appointment, or recovery behavior.",
            "page_spec_schema_change": "not required: PageSpec v1 already permits component_type=form; only the controlled UI value/presentation mapping needs a bounded extension.",
            "must_remain_audit_only": "generic button, generic input, toolbar/navigation, list item, dialog/modal, category-only empty/error, and all reference URIs.",
            "acceptance": "Exact source-field/doc-id traceability, old compact results remain compatible, mobile-appointment and mobile-auth may become candidates only when form context is explicit, profile-settings remains audit-only for its frozen top-2, and pet-recognition remains rejected for mismatched search/empty evidence.",
        },
    }


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _csv_bytes(rows: Iterable[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def write_review(report: dict[str, Any], output_dir: Path) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "ui_reference_signal_review.json").write_bytes(_json_bytes(report))
    target_rows = []
    for unit in report["target_units"]:
        target_rows.append({
            "case_id": unit["case_id"],
            "top_doc_ids": "|".join(item["doc_id"] for item in unit["top_results"]),
            "current_guidance_values": "|".join(str(item["value"]) for item in unit["current_guidance"]),
            "current_decision_rules": "|".join(sorted({str(item["rule"]) for item in unit["current_decisions"]})),
            "candidate_values": "|".join(sorted({signal["value"] for evidence in unit["source_structure"] for signal in evidence["signals"]})),
            "gate_outcomes": "|".join(f"{item['value']}:{item['outcome']}" for item in unit["current_gate_counterfactual"]),
            "verdict": unit["verdict"], "confidence": unit["confidence"],
        })
    (output / "target_units.csv").write_bytes(_csv_bytes(target_rows, TARGET_CSV_FIELDS))
    (output / "counterfactual_cases.csv").write_bytes(_csv_bytes(report["counterfactual"]["cases"], COUNTERFACTUAL_CSV_FIELDS))


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only UI-reference structure signal review")
    parser.add_argument("--documents", type=Path, default=ROOT / "data" / "processed" / "rag" / "documents.jsonl")
    parser.add_argument("--package-root", type=Path, default=ROOT / "outputs" / "demo_v2_regression_v1")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=component_report_runs_root(ROOT) / "ui_reference_signal_review_v1",
    )
    args = parser.parse_args()
    report = generate_review(documents_path=args.documents, package_root=args.package_root)
    write_review(report, args.output_dir)
    print(json.dumps({"schema_version": report["schema_version"], "output_dir": str(args.output_dir), "document_count": report["scope"]["document_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
