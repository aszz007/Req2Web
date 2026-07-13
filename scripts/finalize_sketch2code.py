from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


SELECTED_IDS = {
    "S2C-1037-0",
    "S2C-16888-1",
    "S2C-58-0",
    "S2C-10297-1",
    "S2C-14423-0",
    "S2C-15864-1",
    "S2C-17633-0",
    "S2C-2504-0",
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


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    processed = root / "data/processed"
    candidate_path = processed / "sketch2code_review_candidates.csv"
    final_path = processed / "sketch2code_final_selections.csv"
    rag_path = processed / "sketch2code_rag_records.jsonl"
    manifest_path = processed / "selection_manifest.csv"

    candidates = read_csv(candidate_path)
    by_id = {row["review_id"]: row for row in candidates}
    missing = SELECTED_IDS - set(by_id)
    if missing:
        raise ValueError(f"unknown review IDs: {sorted(missing)}")
    selected = [by_id[review_id] for review_id in sorted(SELECTED_IDS)]
    if len(selected) != 8 or len({row["webpage_id"] for row in selected}) != 8:
        raise ValueError("expected eight sketches from eight different webpage groups")

    for row in candidates:
        row["review_status"] = "selected" if row["review_id"] in SELECTED_IDS else "not_selected"
    write_csv_atomic(candidate_path, candidates, list(candidates[0]))

    final_rows: list[dict[str, Any]] = []
    rag_rows: list[dict[str, Any]] = []
    for row in selected:
        final_rows.append(
            {
                "review_id": row["review_id"],
                "webpage_id": row["webpage_id"],
                "sketch_id": row["sketch_id"],
                "source_design2code_review_id": row["source_design2code_review_id"],
                "category": row["category"],
                "page_subtype": row["page_subtype"],
                "content_summary": row["content_summary"],
                "sketch_path": row["sketch_path"],
                "target_screenshot_path": row["target_screenshot_path"],
                "html_path": row["html_path"],
                "status": "keep",
                "notes": "用户人工确认草图清楚且与目标布局对应（2026-07-13）",
            }
        )
        rag_rows.append(
            {
                "sample_id": row["review_id"],
                "webpage_id": row["webpage_id"],
                "sketch_id": row["sketch_id"],
                "source_design2code_review_id": row["source_design2code_review_id"],
                "category": row["category"],
                "page_subtype": row["page_subtype"],
                "summary": row["content_summary"],
                "input_sketch_path": row["sketch_path"],
                "target_screenshot_path": row["target_screenshot_path"],
                "target_html_path": row["html_path"],
                "sketch": {
                    "width": int(row["sketch_width"]),
                    "height": int(row["sketch_height"]),
                    "sha256": row["sketch_sha256"],
                },
            }
        )

    write_csv_atomic(final_path, final_rows, list(final_rows[0]))
    write_jsonl_atomic(rag_path, rag_rows)

    manifest_rows = read_csv(manifest_path)
    manifest_rows = [row for row in manifest_rows if row["dataset"] != "sketch2code"]
    for row in final_rows:
        manifest_rows.append(
            {
                "dataset": "sketch2code",
                "subset": "paired",
                "sample_id": row["review_id"],
                "source_path": row["sketch_path"],
                "role": "implementation",
                "status": "keep",
                "reason": f"人工确认的{row['page_subtype']}草图到网页实现参考",
                "notes": f"webpage_id={row['webpage_id']}；target={row['target_screenshot_path']}；html={row['html_path']}",
            }
        )
    fields = ["dataset", "subset", "sample_id", "source_path", "role", "status", "reason", "notes"]
    write_csv_atomic(manifest_path, manifest_rows, fields)

    print(f"Final Sketch2Code rows: {len(final_rows)}")
    print(f"Unique webpage groups: {len({row['webpage_id'] for row in final_rows})}")
    print(f"RAG records: {rag_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
