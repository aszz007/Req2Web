from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


EXPECTED_RECORD_COUNT = 100
EXPECTED_DEVICES = ("desktop", "tablet", "mobile")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _integer(value: str, field: str, task_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field} for Vision2Web webpage task {task_name}: {value!r}") from exc


def _boolean(value: str) -> bool:
    return str(value).strip().casefold() == "true"


def build_records(manifest_path: Path, inventory_path: Path) -> list[dict[str, Any]]:
    manifest_rows = [
        row
        for row in _read_csv(manifest_path)
        if row.get("dataset") == "vision2web"
        and row.get("subset") == "webpage"
        and row.get("status") == "keep"
    ]
    inventory_rows = _read_csv(inventory_path)
    inventory: dict[str, dict[str, str]] = {}
    for row in inventory_rows:
        task_name = row.get("task_name", "")
        if not task_name:
            raise ValueError("Vision2Web webpage inventory contains an empty task_name")
        if task_name in inventory:
            raise ValueError(f"duplicate Vision2Web webpage inventory task: {task_name}")
        inventory[task_name] = row

    if len(manifest_rows) != EXPECTED_RECORD_COUNT:
        raise ValueError(
            f"expected {EXPECTED_RECORD_COUNT} kept Vision2Web webpage manifest rows, "
            f"found {len(manifest_rows)}"
        )

    records: list[dict[str, Any]] = []
    seen_tasks: set[str] = set()
    for manifest in sorted(manifest_rows, key=lambda row: row["sample_id"].casefold()):
        task_name = manifest["sample_id"]
        if task_name in seen_tasks:
            raise ValueError(f"duplicate kept Vision2Web webpage manifest task: {task_name}")
        seen_tasks.add(task_name)
        audit = inventory.get(task_name)
        if audit is None:
            raise ValueError(f"Vision2Web webpage manifest task missing from inventory: {task_name}")

        source_path = manifest["source_path"].replace("\\", "/").rstrip("/")
        inventory_source = audit.get("source_path", "").replace("\\", "/").rstrip("/")
        if source_path != inventory_source:
            raise ValueError(
                f"source path mismatch for Vision2Web webpage task {task_name}: "
                f"manifest={source_path!r}, inventory={inventory_source!r}"
            )
        if manifest.get("role") != "implementation":
            raise ValueError(
                f"Vision2Web webpage task {task_name} must use implementation role, "
                f"found {manifest.get('role')!r}"
            )

        inventory_devices = {
            device.strip().casefold()
            for device in audit.get("workflow_devices", "").split("|")
            if device.strip()
        }
        workflow_devices = [device for device in EXPECTED_DEVICES if device in inventory_devices]
        workflow_devices.extend(sorted(inventory_devices.difference(EXPECTED_DEVICES)))
        records.append(
            {
                "sample_id": f"webpage/{task_name}",
                "dataset": "vision2web",
                "subset": "webpage",
                "level": audit.get("level") or "Level 1",
                "task_name": task_name,
                "implementation_type": "responsive_webpage_from_visual_prototypes",
                "workflow_path": f"{source_path}/workflow.json",
                "prototype_paths": [
                    f"{source_path}/prototypes/{device}.jpg" for device in EXPECTED_DEVICES
                ],
                "resource_root": f"{source_path}/resources",
                "resource_count": _integer(
                    audit.get("local_resources_count", ""), "local_resources_count", task_name
                ),
                "workflow_devices": workflow_devices,
                "workflow_steps": _integer(
                    audit.get("local_workflow_steps", ""), "local_workflow_steps", task_name
                ),
                "resource_extensions": audit.get("resource_extensions", ""),
                "manually_verified": _boolean(audit.get("manually_verified", "")),
                "inventory_audit_status": audit.get("audit_status", ""),
                "inventory_issues": audit.get("issues", ""),
                "secondary_role": "ui_reference",
                "reason": manifest.get("reason", ""),
                "notes": manifest.get("notes", ""),
            }
        )

    if len(records) != EXPECTED_RECORD_COUNT:
        raise ValueError(f"expected {EXPECTED_RECORD_COUNT} webpage records, built {len(records)}")
    return records


def write_jsonl_atomic(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def build_webpage_rag(project_root: Path) -> tuple[Path, list[dict[str, Any]]]:
    processed = project_root / "data/processed"
    output_path = processed / "vision2web_webpage_rag_records.jsonl"
    records = build_records(
        processed / "selection_manifest.csv",
        processed / "vision2web_webpage_inventory.csv",
    )
    write_jsonl_atomic(output_path, records)
    return output_path, records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Vision2Web Level 1 webpage RAG records from processed CSV files."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path, records = build_webpage_rag(args.project_root.resolve())
    print(f"Vision2Web webpage RAG records: {len(records)}")
    print(f"Output: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
