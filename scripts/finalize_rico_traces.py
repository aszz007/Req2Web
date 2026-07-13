from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


FIRST_IDS = {"TS-01", "TS-02", "TM-01", "TM-05", "SM-01", "SM-03", "MS-06", "MM-03", "MM-06"}
REPLACEMENT_IDS = {"RSS-01", "RSS-02", "RMS-02"}


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


def update_review_status(path: Path, id_field: str, selected_ids: set[str]) -> None:
    rows = read_csv(path)
    for row in rows:
        row["review_status"] = "selected" if row[id_field] in selected_ids else "not_selected"
    write_csv_atomic(path, rows, list(rows[0]))


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    processed = root / "data/processed"
    first_path = processed / "rico_trace_review_candidates.csv"
    replacement_path = processed / "rico_trace_replacement_candidates.csv"
    final_path = processed / "rico_trace_final_selections.csv"
    rag_path = processed / "rico_trace_rag_records.jsonl"
    manifest_path = processed / "selection_manifest.csv"

    first = {row["review_id"]: row for row in read_csv(first_path)}
    replacements = {row["replacement_id"]: row for row in read_csv(replacement_path)}
    chosen: list[dict[str, str]] = []
    for review_id in sorted(FIRST_IDS):
        row = dict(first[review_id])
        row["final_review_id"] = review_id
        chosen.append(row)
    for review_id in sorted(REPLACEMENT_IDS):
        row = dict(replacements[review_id])
        row["final_review_id"] = review_id
        chosen.append(row)

    counts = Counter(row["recommended_pattern"] for row in chosen)
    expected = {"tap_short", "tap_medium", "swipe_short", "swipe_medium", "mixed_short", "mixed_medium"}
    if len(chosen) != 12 or set(counts) != expected or any(value != 2 for value in counts.values()):
        raise ValueError(f"expected six patterns x two traces: {dict(counts)}")

    final_rows: list[dict[str, str]] = []
    rag_rows: list[dict[str, Any]] = []
    for row in chosen:
        trace_path = root / row["trace_path"]
        with (trace_path / "gestures.json").open("r", encoding="utf-8") as stream:
            gesture_points = json.load(stream)
        screen_ids = row["screen_ids"].split("|")
        gesture_types = row["gesture_types"].split("|")
        screenshots = row["screenshot_paths"].split("|")
        hierarchies = row["hierarchy_paths"].split("|")
        steps = []
        for index, screen_id in enumerate(screen_ids):
            steps.append(
                {
                    "step": index + 1,
                    "screen_id": screen_id,
                    "gesture_type": gesture_types[index] if index < len(gesture_types) else "unknown",
                    "gesture_points": gesture_points.get(screen_id, []),
                    "screenshot_path": screenshots[index],
                    "hierarchy_path": hierarchies[index],
                }
            )
        rag_rows.append(
            {
                "sample_id": row["sample_id"],
                "review_id": row["final_review_id"],
                "pattern": row["recommended_pattern"],
                "trace_path": row["trace_path"],
                "step_count": int(row["step_count"]),
                "activity_names": row["activity_names"].split("|") if row["activity_names"] else [],
                "steps": steps,
            }
        )
        final_rows.append(
            {
                "review_id": row["final_review_id"],
                "sample_id": row["sample_id"],
                "app_id": row["app_id"],
                "trace_id": row["trace_id"],
                "pattern": row["recommended_pattern"],
                "trace_path": row["trace_path"],
                "step_count": row["step_count"],
                "gesture_types": row["gesture_types"],
                "screenshot_paths": row["screenshot_paths"],
                "hierarchy_paths": row["hierarchy_paths"],
                "status": "keep",
                "notes": "用户人工确认流程典型且连续（2026-07-12）",
            }
        )

    write_csv_atomic(final_path, final_rows, list(final_rows[0]))
    write_jsonl_atomic(rag_path, rag_rows)
    update_review_status(first_path, "review_id", FIRST_IDS)
    update_review_status(replacement_path, "replacement_id", REPLACEMENT_IDS)

    manifest_rows = read_csv(manifest_path)
    manifest_rows = [
        row
        for row in manifest_rows
        if not (row["dataset"] == "rico" and row["subset"] == "filtered_traces")
    ]
    for row in final_rows:
        manifest_rows.append(
            {
                "dataset": "rico",
                "subset": "filtered_traces",
                "sample_id": row["sample_id"],
                "source_path": row["trace_path"],
                "role": "interaction_flow",
                "status": "keep",
                "reason": f"人工确认的{row['pattern']}交互流程",
                "notes": f"review_id={row['review_id']}；步骤={row['step_count']}；手势={row['gesture_types']}",
            }
        )
    fields = ["dataset", "subset", "sample_id", "source_path", "role", "status", "reason", "notes"]
    write_csv_atomic(manifest_path, manifest_rows, fields)

    print(f"Final trace rows: {len(final_rows)}")
    print(f"Pattern counts: {dict(counts)}")
    print(f"Manifest trace rows: {sum(row['dataset'] == 'rico' and row['subset'] == 'filtered_traces' for row in manifest_rows)}")
    print(f"RAG records: {rag_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
