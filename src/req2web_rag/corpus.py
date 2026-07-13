from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .schema import DOCUMENT_SCHEMA_VERSION, validate_document


ROLE_ORDER = (
    "requirement",
    "ui_reference",
    "interaction_flow",
    "implementation",
    "validation",
)

ROLE_CONTEXT = {
    "requirement": "需求 产品需求 用户场景 核心功能 页面约束 requirement product user story feature constraint",
    "ui_reference": "界面参考 视觉布局 控件结构 UI截图 ui reference screen layout component visual",
    "interaction_flow": "交互流程 页面状态 点击 滑动 操作步骤 interaction flow state tap swipe gesture",
    "implementation": "前端实现 响应式网页 HTML 原型 布局 implementation frontend responsive webpage prototype layout",
    "validation": "验收标准 测试点 异常流程 错误 权限 validation acceptance test error edge case permission",
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _read_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                yield line_number, json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {path}:{line_number}: {exc}") from exc


def _ref(kind: str, uri: str | None) -> dict[str, str] | None:
    if not uri:
        return None
    return {"kind": kind, "uri": str(uri).replace("\\", "/")}


def _refs(*items: dict[str, str] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not item:
            continue
        key = (item["kind"], item["uri"])
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _strings(values: Iterable[Any]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def _workflow_text(steps: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for step in steps:
        chunks.extend(_strings([step.get("summary")]))
        for test_case in step.get("test_cases", []):
            if isinstance(test_case, dict):
                chunks.extend(_strings(test_case.values()))
            else:
                chunks.extend(_strings([test_case]))
    return "\n".join(chunks)


def _payload_key(source_name: str, record: dict[str, Any]) -> tuple[str, str, str]:
    if source_name == "design2code_rag_records.jsonl":
        return str(record["dataset"]), str(record["subset"]), str(record["sample_id"])
    if source_name == "github_issues_prs_rag_records.jsonl":
        return "github_issues_prs", "ghpr", str(record["sample_id"])
    if source_name == "rico_combined_rag_records.jsonl":
        return "rico", "combined", str(record["screen_id"])
    if source_name == "rico_trace_rag_records.jsonl":
        return "rico", "filtered_traces", str(record["sample_id"])
    if source_name == "sketch2code_rag_records.jsonl":
        return "sketch2code", "paired", str(record["sample_id"])
    if source_name == "vision2web_frontend_rag_records.jsonl":
        return "vision2web", "frontend", str(record["sample_id"]).removeprefix("frontend/")
    if source_name == "vision2web_webpage_rag_records.jsonl":
        return "vision2web", "webpage", str(record["sample_id"]).removeprefix("webpage/")
    if source_name == "vision2web_website_rag_records.jsonl":
        return "vision2web", "website", str(record["sample_id"]).removeprefix("website/")
    raise ValueError(f"no adapter registered for {source_name}")


def _load_payloads(processed: Path) -> dict[tuple[str, str, str], tuple[str, dict[str, Any]]]:
    payloads: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
    for path in sorted(processed.glob("*_rag_records.jsonl")):
        for line_number, record in _read_jsonl(path):
            key = _payload_key(path.name, record)
            if key in payloads:
                previous = payloads[key][0]
                raise ValueError(f"duplicate source record {key}: {previous} and {path.name}:{line_number}")
            payloads[key] = (path.name, record)
    return payloads


def _base_document(
    manifest: dict[str, str],
    source_record: str,
    title: str,
    summary: str,
    content_parts: Iterable[Any],
    tags: Iterable[Any],
    references: list[dict[str, str]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    role = manifest["role"]
    dataset = manifest["dataset"]
    subset = manifest["subset"]
    sample_id = manifest["sample_id"]
    content = "\n".join(_strings([*content_parts, ROLE_CONTEXT[role]]))
    document = {
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "doc_id": f"{role}:{dataset}:{subset}:{sample_id}",
        "role": role,
        "dataset": dataset,
        "subset": subset,
        "sample_id": sample_id,
        "title": title.strip() or sample_id,
        "summary": summary.strip() or manifest["reason"].strip(),
        "content": content,
        "tags": sorted(set(_strings([role, dataset, subset, *tags]))),
        "references": references,
        "source": {
            "manifest_path": "data/processed/selection_manifest.csv",
            "manifest_source_path": manifest["source_path"],
            "record_path": source_record,
        },
        "metadata": metadata,
    }
    validate_document(document)
    return document


def _requirement_document(manifest: dict[str, str], source: str, row: dict[str, Any]) -> dict[str, Any]:
    workflow_steps = row.get("workflow_steps", [])
    prototypes = row.get("prototype_paths", [])
    references = _refs(
        _ref("requirement", row.get("requirement_path")),
        _ref("workflow", row.get("workflow_path")),
        *(_ref("prototype", path) for path in prototypes),
        _ref("resource_directory", row.get("resource_root")),
    )
    metadata = {
        key: row.get(key)
        for key in (
            "level",
            "requirement_type",
            "resource_count",
            "extra_local_resource_count",
            "workflow_steps",
        )
    }
    return _base_document(
        manifest,
        source,
        str(row.get("task_name") or manifest["sample_id"]),
        manifest["reason"],
        [row.get("requirement_text"), _workflow_text(workflow_steps), manifest["notes"]],
        [row.get("level"), row.get("requirement_type"), row.get("task_name")],
        references,
        metadata,
    )


def _ui_document(manifest: dict[str, str], source: str, row: dict[str, Any]) -> dict[str, Any]:
    visible_text = row.get("visible_text_preview", [])
    components = row.get("component_labels", {})
    icons = row.get("icon_classes", {})
    references = _refs(
        _ref("screenshot", row.get("screenshot_path")),
        _ref("view_hierarchy", row.get("hierarchy_path")),
        _ref("semantic_image", row.get("semantic_annotation_png")),
        _ref("semantic_annotation", row.get("semantic_annotation_json")),
    )
    metadata = {
        key: row.get(key)
        for key in (
            "category",
            "category_label",
            "activity_name",
            "node_count",
            "clickable_count",
            "scrollable_count",
            "screen_annotation_split",
            "rico_semantics_sources",
            "component_labels",
            "icon_classes",
        )
    }
    return _base_document(
        manifest,
        source,
        f"{row.get('category_label', 'UI reference')} · {manifest['sample_id']}",
        str(row.get("image_summary") or manifest["reason"]),
        [
            row.get("image_summary"),
            row.get("screen_annotation"),
            " ".join(_strings(visible_text)),
            " ".join(_strings(components.keys())),
            " ".join(_strings(icons.keys())),
            manifest["notes"],
        ],
        [row.get("category"), row.get("category_label"), *components.keys(), *icons.keys()],
        references,
        metadata,
    )


def _flow_document(manifest: dict[str, str], source: str, row: dict[str, Any]) -> dict[str, Any]:
    steps = row.get("steps", [])
    gestures = [str(step.get("gesture_type", "unknown")) for step in steps]
    references: list[dict[str, str]] = _refs(_ref("trace_directory", row.get("trace_path")))
    for step in steps:
        references.extend(
            _refs(
                _ref("step_screenshot", step.get("screenshot_path")),
                _ref("step_hierarchy", step.get("hierarchy_path")),
            )
        )
    metadata = {
        "review_id": row.get("review_id"),
        "pattern": row.get("pattern"),
        "step_count": row.get("step_count"),
        "activity_names": row.get("activity_names", []),
        "steps": steps,
    }
    return _base_document(
        manifest,
        source,
        f"{row.get('pattern', 'interaction flow')} · {row.get('review_id', manifest['sample_id'])}",
        manifest["reason"],
        [
            manifest["reason"],
            manifest["notes"],
            " ".join(_strings(row.get("activity_names", []))),
            "gesture sequence " + " ".join(gestures),
        ],
        [row.get("pattern"), row.get("review_id"), *gestures],
        references,
        metadata,
    )


def _design_document(manifest: dict[str, str], source: str, row: dict[str, Any]) -> dict[str, Any]:
    references = _refs(
        _ref("html", row.get("html_path")),
        _ref("screenshot", row.get("screenshot_path")),
    )
    metadata = {
        key: row.get(key)
        for key in ("review_id", "category", "page_subtype", "html_sha256", "structure", "image")
    }
    return _base_document(
        manifest,
        source,
        str(row.get("title") or row.get("page_subtype") or manifest["sample_id"]),
        str(row.get("summary") or manifest["reason"]),
        [row.get("title"), row.get("summary"), row.get("text_preview"), manifest["notes"]],
        [row.get("category"), row.get("page_subtype"), row.get("review_id")],
        references,
        metadata,
    )


def _sketch_document(manifest: dict[str, str], source: str, row: dict[str, Any]) -> dict[str, Any]:
    references = _refs(
        _ref("input_sketch", row.get("input_sketch_path")),
        _ref("target_screenshot", row.get("target_screenshot_path")),
        _ref("target_html", row.get("target_html_path")),
    )
    metadata = {
        key: row.get(key)
        for key in (
            "webpage_id",
            "sketch_id",
            "source_design2code_review_id",
            "category",
            "page_subtype",
            "sketch",
        )
    }
    return _base_document(
        manifest,
        source,
        str(row.get("page_subtype") or manifest["sample_id"]),
        str(row.get("summary") or manifest["reason"]),
        [row.get("summary"), row.get("page_subtype"), manifest["notes"]],
        [row.get("category"), row.get("page_subtype"), "sketch to code"],
        references,
        metadata,
    )


def _validation_document(manifest: dict[str, str], source: str, row: dict[str, Any]) -> dict[str, Any]:
    issue = row.get("issue", {})
    fix = row.get("fix_reference", {})
    references = _refs(
        _ref("issue", issue.get("url")),
        _ref("pull_request", fix.get("pull_url")),
    )
    metadata = {
        key: value
        for key, value in row.items()
        if key not in {"issue"}
    }
    metadata["issue"] = {key: issue.get(key) for key in ("number", "url")}
    return _base_document(
        manifest,
        source,
        str(issue.get("title") or manifest["sample_id"]),
        str(row.get("problem_summary_zh") or manifest["reason"]),
        [
            issue.get("title"),
            issue.get("body"),
            row.get("problem_summary_zh"),
            row.get("validation_focus_zh"),
            row.get("category_label"),
            row.get("source_limitations"),
        ],
        [row.get("category"), row.get("category_label"), row.get("repository")],
        references,
        metadata,
    )


def _webpage_inventory_document(
    manifest: dict[str, str], inventory: dict[str, str]
) -> dict[str, Any]:
    root = manifest["source_path"].rstrip("/\\")
    references = _refs(
        _ref("workflow", f"{root}/workflow.json"),
        _ref("desktop_prototype", f"{root}/prototypes/desktop.jpg"),
        _ref("tablet_prototype", f"{root}/prototypes/tablet.jpg"),
        _ref("mobile_prototype", f"{root}/prototypes/mobile.jpg"),
        _ref("resource_directory", f"{root}/resources"),
    )
    metadata: dict[str, Any] = {
        "level": inventory.get("level"),
        "workflow_devices": inventory.get("workflow_devices", "").split("|"),
        "workflow_steps": int(inventory.get("local_workflow_steps") or 0),
        "resource_count": int(inventory.get("local_resources_count") or 0),
        "resource_extensions": inventory.get("resource_extensions"),
        "manually_verified": inventory.get("manually_verified") == "True",
        "derived_from": "data/processed/vision2web_webpage_inventory.csv",
    }
    return _base_document(
        manifest,
        "data/processed/vision2web_webpage_inventory.csv",
        manifest["sample_id"],
        manifest["reason"],
        [
            manifest["sample_id"],
            manifest["reason"],
            manifest["notes"],
            "responsive desktop tablet mobile webpage implementation",
        ],
        ["Level 1", "responsive", "desktop", "tablet", "mobile"],
        references,
        metadata,
    )


def _webpage_document(
    manifest: dict[str, str], source: str, row: dict[str, Any]
) -> dict[str, Any]:
    prototypes = row.get("prototype_paths", [])
    prototype_references = []
    for path in prototypes:
        device = Path(str(path)).stem.casefold()
        kind = f"{device}_prototype" if device in {"desktop", "tablet", "mobile"} else "prototype"
        prototype_references.append(_ref(kind, str(path)))
    references = _refs(
        _ref("workflow", row.get("workflow_path")),
        *prototype_references,
        _ref("resource_directory", row.get("resource_root")),
    )
    metadata = {
        key: row.get(key)
        for key in (
            "level",
            "implementation_type",
            "workflow_devices",
            "workflow_steps",
            "resource_count",
            "resource_extensions",
            "manually_verified",
            "inventory_audit_status",
            "inventory_issues",
            "secondary_role",
        )
    }
    return _base_document(
        manifest,
        source,
        str(row.get("task_name") or manifest["sample_id"]),
        str(row.get("reason") or manifest["reason"]),
        [
            row.get("task_name"),
            row.get("reason"),
            row.get("notes"),
            row.get("implementation_type"),
            " ".join(_strings(row.get("workflow_devices", []))),
            "responsive desktop tablet mobile webpage implementation",
        ],
        [
            row.get("level"),
            row.get("implementation_type"),
            row.get("secondary_role"),
            *_strings(row.get("workflow_devices", [])),
        ],
        references,
        metadata,
    )


def _adapt_document(
    manifest: dict[str, str], source: str, row: dict[str, Any]
) -> dict[str, Any]:
    role = manifest["role"]
    if role == "requirement":
        return _requirement_document(manifest, source, row)
    if role == "ui_reference":
        return _ui_document(manifest, source, row)
    if role == "interaction_flow":
        return _flow_document(manifest, source, row)
    if role == "validation":
        return _validation_document(manifest, source, row)
    if manifest["dataset"] == "vision2web" and manifest["subset"] == "webpage":
        return _webpage_document(manifest, source, row)
    if manifest["dataset"] == "sketch2code":
        return _sketch_document(manifest, source, row)
    return _design_document(manifest, source, row)


def build_unified_documents(processed: Path) -> list[dict[str, Any]]:
    """Create one unified document for every keep row in the authoritative manifest."""

    manifest_path = processed / "selection_manifest.csv"
    manifest_rows = [row for row in _read_csv(manifest_path) if row["status"] == "keep"]
    payloads = _load_payloads(processed)
    webpage_inventory = {
        row["task_name"]: row
        for row in _read_csv(processed / "vision2web_webpage_inventory.csv")
    }

    documents: list[dict[str, Any]] = []
    missing: list[tuple[str, str, str]] = []
    for manifest in sorted(
        manifest_rows,
        key=lambda row: (ROLE_ORDER.index(row["role"]), row["dataset"], row["subset"], row["sample_id"]),
    ):
        key = (manifest["dataset"], manifest["subset"], manifest["sample_id"])
        payload = payloads.get(key)
        if payload:
            documents.append(_adapt_document(manifest, payload[0], payload[1]))
        elif key[:2] == ("vision2web", "webpage") and key[2] in webpage_inventory:
            documents.append(_webpage_inventory_document(manifest, webpage_inventory[key[2]]))
        else:
            missing.append(key)

    if missing:
        raise ValueError(f"manifest records without RAG source adapters: {missing[:10]}")
    doc_ids = [document["doc_id"] for document in documents]
    if len(doc_ids) != len(set(doc_ids)):
        duplicates = [key for key, count in Counter(doc_ids).items() if count > 1]
        raise ValueError(f"duplicate unified document IDs: {duplicates[:10]}")
    if len(documents) != len(manifest_rows):
        raise ValueError(f"expected {len(manifest_rows)} documents, built {len(documents)}")
    return documents


def write_documents(path: Path, documents: Iterable[dict[str, Any]]) -> int:
    temporary = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for document in documents:
            validate_document(document)
            stream.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    temporary.replace(path)
    return count
