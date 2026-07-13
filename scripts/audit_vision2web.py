from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image


IMAGE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".ico",
    ".jfif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}
FONT_EXTENSIONS = {".eot", ".otf", ".ttf", ".woff", ".woff2"}
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mov", ".mp4", ".webm"}
AUDIO_EXTENSIONS = {".aac", ".m4a", ".mp3", ".ogg", ".wav"}
EXPECTED_DEVICES = ("desktop", "tablet", "mobile")
MANIFEST_FIELDS = (
    "dataset",
    "subset",
    "sample_id",
    "source_path",
    "role",
    "status",
    "reason",
    "notes",
)
INVENTORY_FIELDS = (
    "task_name",
    "level",
    "source_path",
    "audit_status",
    "issues",
    "warnings",
    "official_metadata",
    "official_workflow_steps",
    "local_workflow_steps",
    "workflow_devices",
    "num_test_cases",
    "official_prototypes",
    "desktop_prototype",
    "tablet_prototype",
    "mobile_prototype",
    "resources_directory",
    "official_resources_count",
    "local_resources_count",
    "image_count",
    "svg_count",
    "font_count",
    "video_count",
    "audio_count",
    "other_count",
    "invalid_resource_count",
    "resource_extensions",
    "manually_verified",
)


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Audit all Vision2Web Level 1 tasks without modifying raw data."
    )
    parser.add_argument("--project-root", type=Path, default=project_root)
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--no-manifest-update", action="store_true")
    return parser.parse_args()


def read_manifest(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv_atomic(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_image(path: Path) -> str | None:
    if not path.is_file():
        return "file missing"
    if path.stat().st_size == 0:
        return "empty file"
    try:
        if path.suffix.lower() == ".svg":
            root = ET.parse(path).getroot()
            if root.tag.rsplit("}", 1)[-1].lower() != "svg":
                return "XML root is not svg"
        else:
            with Image.open(path) as image:
                image.verify()
    except Exception as exc:  # Pillow and XML expose several format-specific errors.
        return str(exc)
    return None


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def load_official_metadata(path: Path) -> dict[str, dict[str, Any]]:
    frame = pd.read_parquet(path)
    required = {
        "task_name",
        "level",
        "workflow_steps",
        "num_test_cases",
        "resources_count",
        "prototypes",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Official Parquet is missing columns: {sorted(missing)}")
    if frame["task_name"].duplicated().any():
        duplicates = frame.loc[frame["task_name"].duplicated(), "task_name"].tolist()
        raise ValueError(f"Duplicate task names in official Parquet: {duplicates}")
    return {str(row["task_name"]): row.to_dict() for _, row in frame.iterrows()}


def audit_task(
    task_name: str,
    task_dir: Path,
    official: dict[str, Any] | None,
    manually_verified: bool,
) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    workflow_steps = 0
    workflow_devices: set[str] = set()
    workflow_path = task_dir / "workflow.json"

    if not task_dir.is_dir():
        issues.append("task directory missing")
    elif not workflow_path.is_file():
        issues.append("missing workflow.json")
    else:
        try:
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
            if not isinstance(workflow, list):
                raise ValueError("root must be a JSON array")
            workflow_steps = len(workflow)
            for step in workflow:
                if not isinstance(step, dict):
                    continue
                summary = step.get("summary")
                if summary:
                    workflow_devices.add(str(summary).lower())
                prototype = step.get("prototype")
                if isinstance(prototype, dict):
                    workflow_devices.update(str(key).lower() for key in prototype)
        except Exception as exc:
            issues.append(f"workflow.json parse error: {exc}")

    if workflow_path.is_file() and workflow_steps >= 0:
        for device in EXPECTED_DEVICES:
            if device not in workflow_devices:
                issues.append(f"workflow missing {device} entry")

    prototype_states: dict[str, bool] = {}
    for device in EXPECTED_DEVICES:
        prototype_path = task_dir / "prototypes" / f"{device}.jpg"
        error = validate_image(prototype_path)
        prototype_states[device] = error is None
        if error:
            issues.append(f"invalid prototype {device}.jpg: {error}")

    resources_dir = task_dir / "resources"
    resource_files: list[Path] = []
    if not resources_dir.is_dir():
        issues.append("missing resources directory")
    else:
        resource_files = sorted(
            path
            for path in resources_dir.rglob("*")
            if path.is_file() and not path.name.startswith("._")
        )

    extension_counts = Counter(path.suffix.lower() or "[no-extension]" for path in resource_files)
    invalid_resources: list[str] = []
    for path in resource_files:
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            error = validate_image(path)
            if error:
                relative = path.relative_to(task_dir).as_posix()
                invalid_resources.append(f"{relative}: {error}")
    if invalid_resources:
        issues.append("invalid image resources: " + "; ".join(invalid_resources))

    official_prototypes: list[str] = []
    if official is None:
        issues.append("missing official Parquet metadata")
        level = "Level 1"
        official_workflow_steps = ""
        num_test_cases = ""
        official_resources_count = ""
    else:
        level = str(official["level"])
        official_workflow_steps = int(official["workflow_steps"])
        num_test_cases = int(official["num_test_cases"])
        official_resources_count = int(official["resources_count"])
        official_prototypes = normalize_list(official["prototypes"])
        if workflow_steps != official_workflow_steps:
            issues.append(
                f"workflow step mismatch: official={official_workflow_steps} local={workflow_steps}"
            )
        if len(resource_files) != official_resources_count:
            issues.append(
                f"resource count mismatch: official={official_resources_count} local={len(resource_files)}"
            )
        local_prototypes = {f"{device}.jpg" for device, valid in prototype_states.items() if valid}
        if set(official_prototypes) != local_prototypes:
            issues.append(
                "prototype list mismatch: "
                f"official={'|'.join(official_prototypes)} local={'|'.join(sorted(local_prototypes))}"
            )

    image_count = sum(path.suffix.lower() in IMAGE_EXTENSIONS for path in resource_files)
    font_count = sum(path.suffix.lower() in FONT_EXTENSIONS for path in resource_files)
    video_count = sum(path.suffix.lower() in VIDEO_EXTENSIONS for path in resource_files)
    audio_count = sum(path.suffix.lower() in AUDIO_EXTENSIONS for path in resource_files)
    known_count = image_count + font_count + video_count + audio_count
    status = "keep" if not issues else "hold"
    source_path = f"data/raw/vision2web/Vision2Web/extracted/webpage/webpage/{task_name}"

    return {
        "task_name": task_name,
        "level": level,
        "source_path": source_path,
        "audit_status": status,
        "issues": " | ".join(issues),
        "warnings": " | ".join(warnings),
        "official_metadata": official is not None,
        "official_workflow_steps": official_workflow_steps,
        "local_workflow_steps": workflow_steps,
        "workflow_devices": "|".join(sorted(workflow_devices)),
        "num_test_cases": num_test_cases,
        "official_prototypes": "|".join(official_prototypes),
        "desktop_prototype": prototype_states.get("desktop", False),
        "tablet_prototype": prototype_states.get("tablet", False),
        "mobile_prototype": prototype_states.get("mobile", False),
        "resources_directory": resources_dir.is_dir(),
        "official_resources_count": official_resources_count,
        "local_resources_count": len(resource_files),
        "image_count": image_count,
        "svg_count": extension_counts[".svg"],
        "font_count": font_count,
        "video_count": video_count,
        "audio_count": audio_count,
        "other_count": len(resource_files) - known_count,
        "invalid_resource_count": len(invalid_resources),
        "resource_extensions": ";".join(
            f"{extension}={extension_counts[extension]}" for extension in sorted(extension_counts)
        ),
        "manually_verified": manually_verified,
    }


def update_manifest(
    path: Path, existing: list[dict[str, str]], audit_rows: list[dict[str, Any]]
) -> None:
    preserved = [
        row
        for row in existing
        if row.get("dataset") != "vision2web"
        or row.get("subset") != "webpage"
        or not row.get("reason", "").startswith("自动")
    ]
    preserved_keys = {
        row.get("sample_id", "")
        for row in preserved
        if row.get("dataset") == "vision2web" and row.get("subset") == "webpage"
    }
    generated: list[dict[str, Any]] = []
    for audit in audit_rows:
        if audit["task_name"] in preserved_keys:
            continue
        status = audit["audit_status"]
        reason = "自动结构审计通过" if status == "keep" else "自动审计发现异常"
        summary = (
            f"workflow={audit['local_workflow_steps']};"
            f"prototypes=desktop|tablet|mobile;"
            f"resources={audit['local_resources_count']}"
        )
        details = audit["issues"] or f"extensions={audit['resource_extensions']}"
        generated.append(
            {
                "dataset": "vision2web",
                "subset": "webpage",
                "sample_id": audit["task_name"],
                "source_path": audit["source_path"],
                "role": "implementation",
                "status": status,
                "reason": reason,
                "notes": f"{details};{summary}",
            }
        )
    rows = preserved + sorted(generated, key=lambda row: str(row["sample_id"]).lower())
    write_csv_atomic(path, MANIFEST_FIELDS, rows)


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    dataset_root = (args.dataset_root or project_root / "data/raw/vision2web/Vision2Web/extracted/webpage/webpage").resolve()
    metadata_path = (args.metadata or project_root / "data/raw/vision2web/Vision2Web/webpage/test.parquet").resolve()
    inventory_path = (args.inventory or project_root / "data/processed/vision2web_webpage_inventory.csv").resolve()
    manifest_path = (args.manifest or project_root / "data/processed/selection_manifest.csv").resolve()

    if not dataset_root.is_dir():
        raise FileNotFoundError(f"Vision2Web webpage directory not found: {dataset_root}")
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Vision2Web webpage Parquet not found: {metadata_path}")

    official = load_official_metadata(metadata_path)
    existing_manifest = read_manifest(manifest_path)
    manual_keys = {
        row.get("sample_id", "")
        for row in existing_manifest
        if row.get("dataset") == "vision2web"
        and row.get("subset") == "webpage"
        and not row.get("reason", "").startswith("自动")
    }
    local_names = {path.name for path in dataset_root.iterdir() if path.is_dir()}
    all_names = sorted(set(official) | local_names, key=str.lower)
    audit_rows = [
        audit_task(name, dataset_root / name, official.get(name), name in manual_keys)
        for name in all_names
    ]

    write_csv_atomic(inventory_path, INVENTORY_FIELDS, audit_rows)
    if not args.no_manifest_update:
        update_manifest(manifest_path, existing_manifest, audit_rows)

    keep_rows = [row for row in audit_rows if row["audit_status"] == "keep"]
    hold_rows = [row for row in audit_rows if row["audit_status"] == "hold"]
    print("Vision2Web Level 1 audit complete")
    print(f"Official tasks: {len(official)}")
    print(f"Local tasks:    {len(local_names)}")
    print(f"Keep:           {len(keep_rows)}")
    print(f"Hold:           {len(hold_rows)}")
    print(f"Inventory:      {inventory_path}")
    if not args.no_manifest_update:
        print(f"Manifest:       {manifest_path}")
    if hold_rows:
        print("Hold tasks:")
        for row in hold_rows:
            print(f"- {row['task_name']}: {row['issues']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Audit failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
