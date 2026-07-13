from __future__ import annotations

import csv
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv_atomic(path: Path, rows: list[dict[str, Any]], fields: Iterable[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def index_screen_annotations(root: Path, wanted: set[str]) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for split in ("train", "valid", "test"):
        with (root / f"{split}.csv").open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                screen_id = row["screen_id"]
                if screen_id in wanted:
                    found[screen_id] = (split, row["screen_annotation"])
        if len(found) == len(wanted):
            break
    return found


def load_inventory(path: Path, wanted: set[str]) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["screen_id"] in wanted:
                found[row["screen_id"]] = row
    return found


def walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def hierarchy_summary(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    nodes = list(walk_dicts(value.get("activity", {}).get("root", {})))
    texts: list[str] = []
    for node in nodes:
        raw = node.get("text")
        values = raw if isinstance(raw, list) else [raw]
        for item in values:
            if isinstance(item, str) and item.strip() and item.strip() not in texts:
                texts.append(item.strip())
    return {
        "activity_name": str(value.get("activity_name", "")),
        "node_count": len(nodes),
        "clickable_count": sum(node.get("clickable") is True for node in nodes),
        "scrollable_count": sum(
            node.get("scrollable-horizontal") is True or node.get("scrollable-vertical") is True
            for node in nodes
        ),
        "visible_text_preview": texts[:30],
    }


def semantic_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"component_labels": {}, "icon_classes": {}}
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    nodes = list(walk_dicts(value))
    labels = Counter(str(node["componentLabel"]) for node in nodes if node.get("componentLabel"))
    icons = Counter(str(node["iconClass"]) for node in nodes if node.get("iconClass"))
    return {"component_labels": dict(labels), "icon_classes": dict(icons)}


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    processed = root / "data/processed"
    final_path = processed / "rico_combined_final_selection.csv"
    manifest_path = processed / "selection_manifest.csv"
    enriched_csv_path = processed / "rico_combined_selected_enriched.csv"
    rag_jsonl_path = processed / "rico_combined_rag_records.jsonl"

    final_rows = read_csv(final_path)
    if len(final_rows) != 42 or len({row["screen_id"] for row in final_rows}) != 42:
        raise ValueError("final RICO combined selection must contain 42 unique screens")
    category_counts = Counter(row["category"] for row in final_rows)
    if len(category_counts) != 14 or any(count != 3 for count in category_counts.values()):
        raise ValueError(f"expected 14 categories x 3 screens: {dict(category_counts)}")

    for row in final_rows:
        row["final_review_status"] = "keep"
        row["final_review_notes"] = "用户终审通过（2026-07-12）"
    write_csv_atomic(final_path, final_rows, final_rows[0].keys())

    wanted = {row["screen_id"] for row in final_rows}
    inventory = load_inventory(processed / "rico_combined_inventory.csv", wanted)
    annotations = index_screen_annotations(
        root / "data/raw/screen_annotation/extracted/screen_annotation-main", wanted
    )
    semantic_root = root / "data/raw/rico/extracted/semantic_annotations"

    enriched_rows: list[dict[str, Any]] = []
    rag_rows: list[dict[str, Any]] = []
    for selected in final_rows:
        screen_id = selected["screen_id"]
        relation = inventory[screen_id]
        hierarchy_path = root / relation["hierarchy_path"]
        semantic_json = semantic_root / f"{screen_id}.json"
        semantic_png = semantic_root / f"{screen_id}.png"
        hierarchy = hierarchy_summary(hierarchy_path)
        semantic = semantic_summary(semantic_json)
        annotation_split, annotation_text = annotations.get(screen_id, ("", ""))
        rico_sources = [
            name
            for name, flag in (
                ("grouping", relation.get("has_grouping")),
                ("iconnet", relation.get("has_iconnet")),
                ("semantics", relation.get("has_rico_semantics")),
            )
            if flag == "True"
        ]
        record = {
            "screen_id": screen_id,
            "category": selected["category"],
            "category_label": selected["category_label"],
            "image_summary": selected["image_summary"],
            "screenshot_path": relation["screenshot_path"],
            "hierarchy_path": relation["hierarchy_path"],
            "semantic_annotation_png": semantic_png.relative_to(root).as_posix()
            if semantic_png.is_file()
            else "",
            "semantic_annotation_json": semantic_json.relative_to(root).as_posix()
            if semantic_json.is_file()
            else "",
            "screen_annotation_split": annotation_split,
            "screen_annotation": annotation_text,
            "rico_semantics_sources": rico_sources,
            "activity_name": hierarchy["activity_name"],
            "node_count": hierarchy["node_count"],
            "clickable_count": hierarchy["clickable_count"],
            "scrollable_count": hierarchy["scrollable_count"],
            "visible_text_preview": hierarchy["visible_text_preview"],
            "component_labels": semantic["component_labels"],
            "icon_classes": semantic["icon_classes"],
        }
        rag_rows.append(record)
        enriched_rows.append(
            {
                **{key: value for key, value in record.items() if key not in {"visible_text_preview", "component_labels", "icon_classes"}},
                "rico_semantics_sources": "|".join(rico_sources),
                "visible_text_preview": " | ".join(hierarchy["visible_text_preview"]),
                "component_labels": json.dumps(semantic["component_labels"], ensure_ascii=False, separators=(",", ":")),
                "icon_classes": json.dumps(semantic["icon_classes"], ensure_ascii=False, separators=(",", ":")),
            }
        )

    write_csv_atomic(enriched_csv_path, enriched_rows, enriched_rows[0].keys())
    write_jsonl_atomic(rag_jsonl_path, rag_rows)

    manifest_rows = read_csv(manifest_path)
    manifest_rows = [
        row
        for row in manifest_rows
        if not (row["dataset"] == "rico" and row["subset"] == "combined")
    ]
    for record in rag_rows:
        sources = ["combined", "semantic_annotations"]
        if record["screen_annotation"]:
            sources.append("screen_annotation")
        sources.extend(record["rico_semantics_sources"])
        manifest_rows.append(
            {
                "dataset": "rico",
                "subset": "combined",
                "sample_id": record["screen_id"],
                "source_path": record["screenshot_path"],
                "role": "ui_reference",
                "status": "keep",
                "reason": f"人工终审通过；类别={record['category_label']}",
                "notes": f"关联来源={'|'.join(sources)}；结构节点={record['node_count']}",
            }
        )
    manifest_fields = ["dataset", "subset", "sample_id", "source_path", "role", "status", "reason", "notes"]
    write_csv_atomic(manifest_path, manifest_rows, manifest_fields)

    print(f"Final RICO combined keep rows: {len(final_rows)}")
    print(f"Manifest RICO combined rows: {sum(row['dataset'] == 'rico' and row['subset'] == 'combined' for row in manifest_rows)}")
    print(f"Screen annotations linked: {sum(bool(row['screen_annotation']) for row in rag_rows)}")
    print(f"Semantic annotation JSON linked: {sum(bool(row['semantic_annotation_json']) for row in rag_rows)}")
    print(f"RAG JSONL: {rag_jsonl_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
