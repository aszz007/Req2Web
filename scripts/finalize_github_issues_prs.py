from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from audit_github_issues_prs import AUTO_SELECTED_IDS


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
    candidate_path = processed / "github_issues_prs_review_candidates.csv"
    final_path = processed / "github_issues_prs_final_selections.csv"
    rag_path = processed / "github_issues_prs_rag_records.jsonl"
    manifest_path = processed / "selection_manifest.csv"
    source_path = "data/raw/github_issues_prs/extracted/ghpr-dataset-main/ghpr.csv"

    candidates = read_csv(candidate_path)
    selected = [row for row in candidates if row["review_id"] in AUTO_SELECTED_IDS]
    counts = Counter(row["category"] for row in selected)
    if len(selected) != 12 or len(counts) != 6 or any(value != 2 for value in counts.values()):
        raise ValueError(f"expected six categories x two selected rows: {dict(counts)}")
    if any(row["review_status"] != "auto_selected" for row in selected):
        raise ValueError("candidate status does not match automatic selection")

    final_rows: list[dict[str, Any]] = []
    rag_rows: list[dict[str, Any]] = []
    for row in sorted(selected, key=lambda item: item["review_id"]):
        sample_id = f"{row['repo_id']}/issue-{row['issue_number']}/pr-{row['pull_number']}"
        final_rows.append(
            {
                "review_id": row["review_id"],
                "sample_id": sample_id,
                "category": row["category"],
                "category_label": row["category_label"],
                "repository": row["repository"],
                "issue_number": row["issue_number"],
                "pull_number": row["pull_number"],
                "issue_title": row["issue_title"],
                "chinese_summary": row["chinese_summary"],
                "validation_focus": row["validation_focus"],
                "issue_url": row["issue_url"],
                "pull_url": row["pull_url"],
                "status": "keep",
                "selection_method": "automatic_content_review",
                "notes": "Issue 描述完整且可转为验收点；GHPR 不含 PR 代码或 diff",
            }
        )
        rag_rows.append(
            {
                "sample_id": sample_id,
                "review_id": row["review_id"],
                "category": row["category"],
                "category_label": row["category_label"],
                "repository": row["repository"],
                "issue": {
                    "number": int(row["issue_number"]),
                    "title": row["issue_title"],
                    "body": row["issue_body_plain"],
                    "url": row["issue_url"],
                },
                "problem_summary_zh": row["chinese_summary"],
                "validation_focus_zh": row["validation_focus"],
                "fix_reference": {
                    "pull_number": int(row["pull_number"]),
                    "pull_url": row["pull_url"],
                    "commits": int(row["pull_commits"]),
                    "changed_files": int(row["pull_changed_files"]),
                    "additions": int(row["pull_additions"]),
                    "deletions": int(row["pull_deletions"]),
                    "solution_content_available": False,
                },
                "source_limitations": "Local GHPR provides the fixed Issue-PR relation and change statistics, not the PR body, diff, or fix code.",
            }
        )

    write_csv_atomic(final_path, final_rows, list(final_rows[0]))
    write_jsonl_atomic(rag_path, rag_rows)

    manifest_rows = read_csv(manifest_path)
    manifest_rows = [row for row in manifest_rows if row["dataset"] != "github_issues_prs"]
    for row in final_rows:
        manifest_rows.append(
            {
                "dataset": "github_issues_prs",
                "subset": "ghpr",
                "sample_id": row["sample_id"],
                "source_path": source_path,
                "role": "validation",
                "status": "keep",
                "reason": f"自动内容复核通过的{row['category_label']}验收案例",
                "notes": f"review_id={row['review_id']}；repository={row['repository']}；issue={row['issue_number']}；pr={row['pull_number']}；无本地PR代码diff",
            }
        )
    fields = ["dataset", "subset", "sample_id", "source_path", "role", "status", "reason", "notes"]
    write_csv_atomic(manifest_path, manifest_rows, fields)

    print(f"Final validation rows: {len(final_rows)}")
    print(f"Category counts: {dict(counts)}")
    print(f"RAG records: {rag_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
