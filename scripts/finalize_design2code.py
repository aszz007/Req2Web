from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


SELECTED_IDS = {
    "D2C-FOR-03",
    "D2C-FOR-04",
    "D2C-DAT-01",
    "D2C-DAT-04",
    "D2C-COM-02",
    "D2C-COM-05",
    "D2C-ART-02",
    "D2C-ART-05",
    "D2C-MED-01",
    "D2C-MED-02",
    "D2C-GEN-01",
    "D2C-GEN-02",
    "HARD-07",
    "HARD-08",
    "HARD-09",
    "HARD-11",
}

EXPECTED_COUNTS = {
    "form_input": 2,
    "data_dashboard": 2,
    "commerce": 2,
    "article_content": 2,
    "media_gallery": 2,
    "general_landing": 2,
    "hard": 4,
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv_atomic(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def as_int(value: str) -> int:
    return int(value or 0)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    processed = root / "data/processed"
    candidate_path = processed / "design2code_review_candidates.csv"
    final_path = processed / "design2code_final_selections.csv"
    rag_path = processed / "design2code_rag_records.jsonl"
    manifest_path = processed / "selection_manifest.csv"

    candidates = read_csv(candidate_path)
    by_id = {row["review_id"]: row for row in candidates}
    missing = SELECTED_IDS - set(by_id)
    if missing:
        raise ValueError(f"unknown review IDs: {sorted(missing)}")

    selected = [by_id[review_id] for review_id in sorted(SELECTED_IDS)]
    counts = Counter(row["review_group"] for row in selected)
    if dict(counts) != EXPECTED_COUNTS:
        raise ValueError(f"unexpected category counts: {dict(counts)}")

    for row in candidates:
        row["review_status"] = "selected" if row["review_id"] in SELECTED_IDS else "not_selected"
    write_csv_atomic(candidate_path, candidates, list(candidates[0]))

    final_rows: list[dict[str, Any]] = []
    rag_rows: list[dict[str, Any]] = []
    for row in selected:
        subset = row["subset"]
        source_dataset = "design2code_hard" if subset == "hard" else "design2code"
        final_rows.append(
            {
                "review_id": row["review_id"],
                "dataset": source_dataset,
                "subset": subset,
                "sample_id": row["sample_id"],
                "category": row["review_group"],
                "page_subtype": row["page_subtype"],
                "content_summary": row["content_summary"],
                "title": row["title"],
                "html_path": row["html_path"],
                "png_path": row["png_path"],
                "status": "keep",
                "notes": "用户依据内容匹配和视觉典型性人工确认（2026-07-13）",
            }
        )
        rag_rows.append(
            {
                "sample_id": row["sample_id"],
                "review_id": row["review_id"],
                "dataset": source_dataset,
                "subset": subset,
                "category": row["review_group"],
                "page_subtype": row["page_subtype"],
                "title": row["title"],
                "summary": row["content_summary"],
                "text_preview": row["text_preview"],
                "html_path": row["html_path"],
                "screenshot_path": row["png_path"],
                "html_sha256": row["html_sha256"],
                "structure": {
                    "tag_count": as_int(row["tag_count"]),
                    "form_control_count": as_int(row["form_control_count"]),
                    "table_count": as_int(row["table_count"]),
                    "nav_count": as_int(row["nav_count"]),
                    "image_count": as_int(row["image_tag_count"]),
                    "link_count": as_int(row["link_count"]),
                    "complexity_score": as_int(row["complexity_score"]),
                },
                "image": {
                    "width": as_int(row["image_width"]),
                    "height": as_int(row["image_height"]),
                    "dhash": row["image_dhash"],
                },
            }
        )

    final_fields = list(final_rows[0])
    write_csv_atomic(final_path, final_rows, final_fields)
    write_jsonl_atomic(rag_path, rag_rows)

    manifest_rows = read_csv(manifest_path)
    manifest_rows = [
        row
        for row in manifest_rows
        if row["dataset"] not in {"design2code", "design2code_hard"}
    ]
    for row in final_rows:
        manifest_rows.append(
            {
                "dataset": row["dataset"],
                "subset": row["subset"],
                "sample_id": row["sample_id"],
                "source_path": row["html_path"],
                "role": "implementation",
                "status": "keep",
                "reason": f"人工确认的{row['page_subtype']}实现参考",
                "notes": f"review_id={row['review_id']}；category={row['category']}；screenshot={row['png_path']}",
            }
        )
    manifest_fields = ["dataset", "subset", "sample_id", "source_path", "role", "status", "reason", "notes"]
    write_csv_atomic(manifest_path, manifest_rows, manifest_fields)

    print(f"Final Design2Code rows: {len(final_rows)}")
    print(f"Category counts: {dict(counts)}")
    print(f"Manifest implementation rows added: {len(final_rows)}")
    print(f"RAG records: {rag_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
