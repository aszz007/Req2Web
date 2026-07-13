from __future__ import annotations

import csv
import json
import tarfile
from collections import defaultdict
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

import pandas as pd
from PIL import Image

from extract_vision2web_windows import sanitize_part


SUBSETS = {
    "frontend": {"level": "Level 2", "requirement_file": "prompt.txt", "preview_field": "prompt_preview"},
    "website": {"level": "Level 3", "requirement_file": "prd.md", "preview_field": "prd_preview"},
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


def image_valid(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    return [str(item) for item in value]


def archive_resource_paths(archive_path: Path, subset: str) -> dict[str, set[str]]:
    resources: dict[str, set[str]] = defaultdict(set)
    with tarfile.open(archive_path, "r:gz") as bundle:
        for member in bundle:
            if not member.isfile():
                continue
            pure = PurePosixPath(member.name)
            if (
                len(pure.parts) < 4
                or pure.parts[0] != subset
                or pure.parts[2] != "resources"
                or ".git" in pure.parts
                or any(part.startswith("._") for part in pure.parts)
            ):
                continue
            local_relative = Path(*(sanitize_part(part) for part in pure.parts[3:]))
            resources[pure.parts[1]].add(local_relative.as_posix().casefold())
    return resources


def audit_subset(root: Path, subset: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    config = SUBSETS[subset]
    dataset_root = root / "data/raw/vision2web/Vision2Web"
    extracted_root = dataset_root / "extracted" / subset
    frame = pd.read_parquet(dataset_root / subset / "test.parquet")
    archived_resources = archive_resource_paths(dataset_root / "archives" / f"{subset}.tar.gz", subset)
    inventory: list[dict[str, Any]] = []
    rag_rows: list[dict[str, Any]] = []

    for metadata in frame.to_dict(orient="records"):
        task_name = str(metadata["task_name"])
        task_root = extracted_root / task_name
        requirement_path = task_root / config["requirement_file"]
        workflow_path = task_root / "workflow.json"
        prototypes_root = task_root / "prototypes"
        resources_root = task_root / "resources"
        expected_prototypes = string_list(metadata["prototypes"])

        requirement_text = ""
        requirement_valid = False
        if requirement_path.is_file():
            requirement_text = requirement_path.read_text(encoding="utf-8", errors="replace").strip()
            requirement_valid = bool(requirement_text)

        workflow: list[dict[str, Any]] = []
        workflow_valid = False
        if workflow_path.is_file():
            try:
                parsed = json.loads(workflow_path.read_text(encoding="utf-8"))
                if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
                    workflow = parsed
                    workflow_valid = True
            except (json.JSONDecodeError, OSError, UnicodeError):
                workflow_valid = False

        actual_workflow_steps = len(workflow)
        actual_test_cases = sum(
            bool(case.get("validations"))
            for item in workflow
            for case in item.get("content", [])
            if isinstance(case, dict)
        )
        prototype_paths = [prototypes_root / name for name in expected_prototypes]
        prototype_files_complete = bool(expected_prototypes) and all(path.is_file() for path in prototype_paths)
        prototypes_valid = prototype_files_complete and all(image_valid(path) for path in prototype_paths)
        local_resource_paths = {
            path.relative_to(resources_root).as_posix().casefold()
            for path in resources_root.rglob("*")
            if path.is_file()
        } if resources_root.is_dir() else set()
        expected_archive_resources = archived_resources.get(task_name, set())
        missing_archive_resources = expected_archive_resources - local_resource_paths
        extra_local_resources = local_resource_paths - expected_archive_resources
        archive_resource_count = len(expected_archive_resources)
        canonical_resources_present = len(expected_archive_resources & local_resource_paths)
        local_resource_count = len(local_resource_paths)

        checks = {
            "task_directory": task_root.is_dir(),
            "requirement": requirement_valid,
            "workflow": workflow_valid,
            "workflow_steps": actual_workflow_steps == int(metadata["workflow_steps"]),
            "test_cases": actual_test_cases == int(metadata["num_test_cases"]),
            "prototypes": prototypes_valid,
            "resources": (
                archive_resource_count == int(metadata["resources_count"])
                and not missing_archive_resources
            ),
            "level": str(metadata["level"]) == config["level"],
        }
        failed_checks = [name for name, passed in checks.items() if not passed]
        audit_status = "keep" if not failed_checks else "hold"
        task_relative = task_root.relative_to(root).as_posix()
        requirement_relative = requirement_path.relative_to(root).as_posix()
        workflow_relative = workflow_path.relative_to(root).as_posix()

        inventory.append(
            {
                "dataset": "vision2web",
                "subset": subset,
                "task_name": task_name,
                "level": metadata["level"],
                "task_path": task_relative,
                "requirement_path": requirement_relative,
                "requirement_chars": len(requirement_text),
                "workflow_path": workflow_relative,
                "expected_workflow_steps": int(metadata["workflow_steps"]),
                "actual_workflow_steps": actual_workflow_steps,
                "expected_test_cases": int(metadata["num_test_cases"]),
                "actual_test_cases": actual_test_cases,
                "expected_prototype_count": len(expected_prototypes),
                "actual_expected_prototypes": sum(path.is_file() for path in prototype_paths),
                "all_prototypes_readable": prototypes_valid,
                "expected_resource_count": int(metadata["resources_count"]),
                "archive_resource_count": archive_resource_count,
                "canonical_resources_present": canonical_resources_present,
                "local_resource_count": local_resource_count,
                "missing_archive_resource_count": len(missing_archive_resources),
                "extra_local_resource_count": len(extra_local_resources),
                "audit_status": audit_status,
                "failed_checks": "|".join(failed_checks),
                "requirement_preview": requirement_text[:800].replace("\r", " ").replace("\n", " "),
            }
        )

        if audit_status == "keep":
            workflow_summaries = []
            for item in workflow:
                workflow_summaries.append(
                    {
                        "index": item.get("index"),
                        "summary": item.get("summary", ""),
                        "depends_on": item.get("depends_on", []),
                        "test_cases": [
                            {
                                "objective": case.get("objective", ""),
                                "actions": case.get("actions", []),
                                "validations": case.get("validations", []),
                            }
                            for case in item.get("content", [])
                        ],
                    }
                )
            rag_rows.append(
                {
                    "sample_id": f"{subset}/{task_name}",
                    "dataset": "vision2web",
                    "subset": subset,
                    "level": metadata["level"],
                    "task_name": task_name,
                    "requirement_type": "natural_language_prompt" if subset == "frontend" else "product_requirements_document",
                    "requirement_text": requirement_text,
                    "requirement_path": requirement_relative,
                    "workflow_path": workflow_relative,
                    "workflow_steps": workflow_summaries,
                    "prototype_paths": [path.relative_to(root).as_posix() for path in prototype_paths],
                    "resource_root": resources_root.relative_to(root).as_posix(),
                    "resource_count": archive_resource_count,
                    "extra_local_resource_count": len(extra_local_resources),
                }
            )
    return inventory, rag_rows


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    processed = root / "data/processed"
    manifest_path = processed / "selection_manifest.csv"
    all_inventory: list[dict[str, Any]] = []
    all_rag: list[dict[str, Any]] = []

    for subset in SUBSETS:
        inventory, rag_rows = audit_subset(root, subset)
        all_inventory.extend(inventory)
        all_rag.extend(rag_rows)
        write_csv_atomic(
            processed / f"vision2web_{subset}_inventory.csv",
            inventory,
            list(inventory[0]),
        )
        write_jsonl_atomic(processed / f"vision2web_{subset}_rag_records.jsonl", rag_rows)

    manifest_rows = read_csv(manifest_path)
    manifest_rows = [
        row
        for row in manifest_rows
        if not (row["dataset"] == "vision2web" and row["subset"] in SUBSETS)
    ]
    for row in all_inventory:
        if row["audit_status"] != "keep":
            continue
        manifest_rows.append(
            {
                "dataset": "vision2web",
                "subset": row["subset"],
                "sample_id": row["task_name"],
                "source_path": row["task_path"],
                "role": "requirement",
                "status": "keep",
                "reason": f"官方{row['level']}任务的需求、workflow、原型和资源自动审计通过",
                "notes": f"workflow_steps={row['actual_workflow_steps']}；test_cases={row['actual_test_cases']}；prototypes={row['actual_expected_prototypes']}；resources={row['archive_resource_count']}；extra_local_resources={row['extra_local_resource_count']}",
            }
        )
    fields = ["dataset", "subset", "sample_id", "source_path", "role", "status", "reason", "notes"]
    write_csv_atomic(manifest_path, manifest_rows, fields)

    counts = {
        subset: {
            "total": sum(row["subset"] == subset for row in all_inventory),
            "keep": sum(row["subset"] == subset and row["audit_status"] == "keep" for row in all_inventory),
            "hold": sum(row["subset"] == subset and row["audit_status"] == "hold" for row in all_inventory),
        }
        for subset in SUBSETS
    }
    print(f"Audit counts: {counts}")
    print(f"RAG records: {len(all_rag)}")
    print(f"Manifest requirement rows: {sum(row['dataset'] == 'vision2web' and row['subset'] in SUBSETS for row in manifest_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
