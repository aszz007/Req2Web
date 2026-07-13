from __future__ import annotations

import argparse
import csv
import json
import math
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


INVENTORY_FIELDS = (
    "sample_id",
    "app_id",
    "trace_id",
    "trace_path",
    "audit_status",
    "issues",
    "screenshot_count",
    "hierarchy_count",
    "gesture_screen_count",
    "matched_screen_count",
    "screen_ids",
    "gesture_order",
    "gesture_types",
    "tap_count",
    "swipe_count",
    "combined_link_count",
    "screen_annotation_link_count",
    "semantic_annotation_link_count",
    "rico_semantics_link_count",
    "missing_screenshots",
    "missing_hierarchies",
    "gestures_without_screen",
    "screens_without_gesture",
    "candidate_eligible",
)

CANDIDATE_FIELDS = (
    "sample_id",
    "app_id",
    "trace_id",
    "recommended_pattern",
    "candidate_score",
    "selection_reason",
    "trace_path",
    "step_count",
    "screen_ids",
    "gesture_types",
    "screenshot_paths",
    "hierarchy_paths",
    "combined_link_count",
    "screen_annotation_link_count",
    "semantic_annotation_link_count",
    "rico_semantics_link_count",
    "images_valid",
    "hierarchies_valid",
    "distinct_image_count",
    "activity_names",
    "node_count_total",
    "manual_status",
    "manual_notes",
)

PATTERN_ORDER = (
    "tap_short",
    "tap_medium",
    "swipe_short",
    "swipe_medium",
    "mixed_short",
    "mixed_medium",
)


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Audit RICO filtered traces and generate short interaction-flow candidates."
    )
    parser.add_argument("--project-root", type=Path, default=project_root)
    parser.add_argument("--candidate-limit", type=int, default=60)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Audit only the first N traces for a bounded test run.",
    )
    return parser.parse_args()


def relative_path(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def write_csv_atomic(path: Path, rows: list[dict[str, Any]], fields: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    os.close(handle)
    temporary_path = Path(temporary_name)
    try:
        with temporary_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def numeric_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value.lower())


def list_trace_directories(root: Path) -> list[Path]:
    traces: list[Path] = []
    for app_path in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda p: p.name.lower()):
        traces.extend(
            sorted(
                (
                    path
                    for path in app_path.iterdir()
                    if path.is_dir() and path.name.startswith("trace_")
                ),
                key=lambda p: p.name.lower(),
            )
        )
    return traces


def index_step_files(directory: Path, suffix: str) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    return {
        path.stem: path
        for path in directory.glob(f"*{suffix}")
        if path.is_file() and not path.name.startswith("._")
    }


def parse_gestures(path: Path) -> tuple[dict[str, list[Any]], str]:
    if not path.is_file():
        return {}, "missing gestures.json"
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {}, f"gestures.json parse error: {error}"
    if not isinstance(value, dict):
        return {}, "gestures.json root is not an object"
    gestures: dict[str, list[Any]] = {}
    for screen_id, points in value.items():
        gestures[str(screen_id)] = points if isinstance(points, list) else []
    return gestures, ""


def gesture_type(points: list[Any]) -> str:
    if len(points) <= 0:
        return "unknown"
    return "tap" if len(points) == 1 else "swipe"


def load_combined_relations(path: Path) -> dict[str, dict[str, bool]]:
    relations: dict[str, dict[str, bool]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            screen_id = str(row.get("screen_id", ""))
            if not screen_id:
                continue
            relations[screen_id] = {
                "combined": row.get("relation_status") == "complete_core",
                "screen_annotation": row.get("has_screen_annotation") == "True",
                "semantic_annotation": (
                    row.get("has_semantic_png") == "True"
                    and row.get("has_semantic_json") == "True"
                ),
                "rico_semantics": any(
                    row.get(field) == "True"
                    for field in ("has_grouping", "has_iconnet", "has_rico_semantics")
                ),
            }
    return relations


def classify_pattern(gesture_types: list[str], step_count: int) -> str:
    length = "short" if step_count <= 3 else "medium"
    known = {value for value in gesture_types if value in {"tap", "swipe"}}
    if known == {"tap"}:
        return f"tap_{length}"
    if known == {"swipe"}:
        return f"swipe_{length}"
    return f"mixed_{length}"


def build_inventory_row(
    trace_path: Path,
    project_root: Path,
    relations: dict[str, dict[str, bool]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    screenshots = index_step_files(trace_path / "screenshots", ".jpg")
    hierarchies = index_step_files(trace_path / "view_hierarchies", ".json")
    gestures, gesture_issue = parse_gestures(trace_path / "gestures.json")
    screenshot_ids = set(screenshots)
    hierarchy_ids = set(hierarchies)
    gesture_ids = set(gestures)
    all_screen_ids = screenshot_ids | hierarchy_ids | gesture_ids
    ordered_ids = list(gestures)
    ordered_ids.extend(sorted(all_screen_ids - gesture_ids, key=numeric_key))
    gesture_types = [gesture_type(gestures[screen_id]) for screen_id in gestures]
    issues: list[str] = []
    if gesture_issue:
        issues.append(gesture_issue)
    if not screenshots:
        issues.append("no screenshots")
    if not hierarchies:
        issues.append("no view hierarchies")
    missing_screenshots = sorted((hierarchy_ids | gesture_ids) - screenshot_ids, key=numeric_key)
    missing_hierarchies = sorted((screenshot_ids | gesture_ids) - hierarchy_ids, key=numeric_key)
    gestures_without_screen = sorted(gesture_ids - screenshot_ids, key=numeric_key)
    screens_without_gesture = sorted(screenshot_ids - gesture_ids, key=numeric_key)
    if missing_screenshots:
        issues.append("missing screenshots")
    if missing_hierarchies:
        issues.append("missing view hierarchies")
    if gestures_without_screen:
        issues.append("gesture IDs without screenshots")

    relation_counts = {
        name: sum(bool(relations.get(screen_id, {}).get(name)) for screen_id in screenshot_ids)
        for name in ("combined", "screen_annotation", "semantic_annotation", "rico_semantics")
    }
    exact_sets = screenshot_ids == hierarchy_ids == gesture_ids
    candidate_eligible = not issues and exact_sets and 2 <= len(screenshot_ids) <= 6
    sample_id = f"{trace_path.parent.name}/{trace_path.name}"
    row = {
        "sample_id": sample_id,
        "app_id": trace_path.parent.name,
        "trace_id": trace_path.name,
        "trace_path": relative_path(trace_path, project_root),
        "audit_status": "complete" if not issues else "hold",
        "issues": "; ".join(issues),
        "screenshot_count": len(screenshot_ids),
        "hierarchy_count": len(hierarchy_ids),
        "gesture_screen_count": len(gesture_ids),
        "matched_screen_count": len(screenshot_ids & hierarchy_ids & gesture_ids),
        "screen_ids": "|".join(sorted(screenshot_ids, key=numeric_key)),
        "gesture_order": "|".join(ordered_ids),
        "gesture_types": "|".join(gesture_types),
        "tap_count": gesture_types.count("tap"),
        "swipe_count": gesture_types.count("swipe"),
        "combined_link_count": relation_counts["combined"],
        "screen_annotation_link_count": relation_counts["screen_annotation"],
        "semantic_annotation_link_count": relation_counts["semantic_annotation"],
        "rico_semantics_link_count": relation_counts["rico_semantics"],
        "missing_screenshots": "|".join(missing_screenshots),
        "missing_hierarchies": "|".join(missing_hierarchies),
        "gestures_without_screen": "|".join(gestures_without_screen),
        "screens_without_gesture": "|".join(screens_without_gesture),
        "candidate_eligible": candidate_eligible,
    }
    detail = {
        "trace_path": trace_path,
        "screenshots": screenshots,
        "hierarchies": hierarchies,
        "gestures": gestures,
        "ordered_ids": ordered_ids,
        "gesture_types_list": gesture_types,
    }
    return row, detail


def image_hash(path: Path) -> tuple[bool, int]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            grayscale = image.convert("L").resize((9, 8))
            pixels = list(grayscale.get_flattened_data())
        value = 0
        for row in range(8):
            for column in range(8):
                left = pixels[row * 9 + column]
                right = pixels[row * 9 + column + 1]
                value = (value << 1) | int(left > right)
        return True, value
    except (OSError, ValueError):
        return False, 0


def inspect_hierarchy(path: Path) -> tuple[bool, str, int]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False, "", 0
    node_count = 0
    stack: list[Any] = [data.get("activity", {}).get("root", {})]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if "class" in current or "bounds" in current:
                node_count += 1
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return True, str(data.get("activity_name", "")), node_count


def deep_candidate(
    row: dict[str, Any],
    detail: dict[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    ordered_ids = detail["ordered_ids"]
    image_validity: list[bool] = []
    hashes: list[int] = []
    hierarchy_validity: list[bool] = []
    activities: list[str] = []
    node_count_total = 0
    screenshot_paths: list[str] = []
    hierarchy_paths: list[str] = []
    for screen_id in ordered_ids:
        screenshot = detail["screenshots"][screen_id]
        hierarchy = detail["hierarchies"][screen_id]
        valid_image, hash_value = image_hash(screenshot)
        valid_hierarchy, activity_name, node_count = inspect_hierarchy(hierarchy)
        image_validity.append(valid_image)
        hierarchy_validity.append(valid_hierarchy)
        if valid_image:
            hashes.append(hash_value)
        if activity_name:
            activities.append(activity_name)
        node_count_total += node_count
        screenshot_paths.append(relative_path(screenshot, project_root))
        hierarchy_paths.append(relative_path(hierarchy, project_root))

    step_count = len(ordered_ids)
    pattern = classify_pattern(detail["gesture_types_list"], step_count)
    relation_total = (
        int(row["screen_annotation_link_count"])
        + int(row["rico_semantics_link_count"])
    )
    distinct_images = len(set(hashes))
    score = 10
    score += min(relation_total, step_count * 2)
    score += 3 if 3 <= step_count <= 5 else 1
    score += 3 if distinct_images >= 2 else -5
    score += 2 if "mixed" in pattern else 0
    score += 2 if all(image_validity) and all(hierarchy_validity) else -20
    reason = (
        f"pattern={pattern};steps={step_count};distinct_images={distinct_images};"
        f"screen_annotations={row['screen_annotation_link_count']}"
    )
    return {
        "sample_id": row["sample_id"],
        "app_id": row["app_id"],
        "trace_id": row["trace_id"],
        "recommended_pattern": pattern,
        "candidate_score": score,
        "selection_reason": reason,
        "trace_path": row["trace_path"],
        "step_count": step_count,
        "screen_ids": "|".join(ordered_ids),
        "gesture_types": "|".join(detail["gesture_types_list"]),
        "screenshot_paths": "|".join(screenshot_paths),
        "hierarchy_paths": "|".join(hierarchy_paths),
        "combined_link_count": row["combined_link_count"],
        "screen_annotation_link_count": row["screen_annotation_link_count"],
        "semantic_annotation_link_count": row["semantic_annotation_link_count"],
        "rico_semantics_link_count": row["rico_semantics_link_count"],
        "images_valid": all(image_validity),
        "hierarchies_valid": all(hierarchy_validity),
        "distinct_image_count": distinct_images,
        "activity_names": "|".join(dict.fromkeys(activities)),
        "node_count_total": node_count_total,
        "manual_status": "",
        "manual_notes": "",
    }


def select_balanced_candidates(
    eligible: list[tuple[dict[str, Any], dict[str, Any]]],
    project_root: Path,
    candidate_limit: int,
) -> list[dict[str, Any]]:
    preliminary: dict[str, list[tuple[int, dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for row, detail in eligible:
        pattern = classify_pattern(detail["gesture_types_list"], int(row["screenshot_count"]))
        score = (
            int(row["screen_annotation_link_count"])
            + int(row["rico_semantics_link_count"])
            + (3 if 3 <= int(row["screenshot_count"]) <= 5 else 1)
        )
        preliminary[pattern].append((score, row, detail))
    for values in preliminary.values():
        values.sort(key=lambda item: (-item[0], str(item[1]["sample_id"])))

    deep_limit = min(len(eligible), max(candidate_limit * 4, 180))
    per_pattern = max(1, math.ceil(deep_limit / len(PATTERN_ORDER)))
    deep_items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    deep_ids: set[str] = set()
    for pattern in PATTERN_ORDER:
        for _, row, detail in preliminary.get(pattern, [])[:per_pattern]:
            deep_items.append((row, detail))
            deep_ids.add(str(row["sample_id"]))
    all_preliminary = sorted(
        (item for values in preliminary.values() for item in values),
        key=lambda item: (-item[0], str(item[1]["sample_id"])),
    )
    for _, row, detail in all_preliminary:
        if len(deep_items) >= deep_limit:
            break
        if str(row["sample_id"]) not in deep_ids:
            deep_items.append((row, detail))
            deep_ids.add(str(row["sample_id"]))

    deep_rows: list[dict[str, Any]] = []
    for index, (row, detail) in enumerate(deep_items, start=1):
        deep_rows.append(deep_candidate(row, detail, project_root))
        if index % 50 == 0 or index == len(deep_items):
            print(f"Deep trace validation: {index}/{len(deep_items)}", flush=True)

    valid_rows = [
        row
        for row in deep_rows
        if row["images_valid"]
        and row["hierarchies_valid"]
        and int(row["distinct_image_count"]) >= 2
    ]
    by_pattern: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid_rows:
        by_pattern[str(row["recommended_pattern"])].append(row)
    for rows in by_pattern.values():
        rows.sort(key=lambda row: (-int(row["candidate_score"]), str(row["sample_id"])))

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    app_counts: Counter[str] = Counter()
    offsets = {pattern: 0 for pattern in PATTERN_ORDER}
    while len(selected) < candidate_limit:
        progress = False
        for pattern in PATTERN_ORDER:
            rows = by_pattern.get(pattern, [])
            while offsets[pattern] < len(rows):
                row = rows[offsets[pattern]]
                offsets[pattern] += 1
                sample_id = str(row["sample_id"])
                app_id = str(row["app_id"])
                if sample_id in selected_ids or app_counts[app_id] >= 1:
                    continue
                selected.append(row)
                selected_ids.add(sample_id)
                app_counts[app_id] += 1
                progress = True
                break
            if len(selected) >= candidate_limit:
                break
        if not progress:
            break
    return sorted(
        selected,
        key=lambda row: (
            PATTERN_ORDER.index(str(row["recommended_pattern"])),
            -int(row["candidate_score"]),
            str(row["sample_id"]),
        ),
    )


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    traces_root = project_root / "data/raw/rico/extracted/filtered_traces"
    relation_path = project_root / "data/processed/rico_combined_inventory.csv"
    inventory_path = project_root / "data/processed/rico_traces_inventory.csv"
    candidate_path = project_root / "data/processed/rico_trace_candidate_pool.csv"
    if not traces_root.is_dir():
        raise FileNotFoundError(f"RICO filtered_traces directory not found: {traces_root}")
    if not relation_path.is_file():
        raise FileNotFoundError(f"RICO combined inventory not found: {relation_path}")

    print("Loading combined relation inventory...", flush=True)
    relations = load_combined_relations(relation_path)
    print("Indexing trace directories...", flush=True)
    trace_paths = list_trace_directories(traces_root)
    if args.limit is not None:
        trace_paths = trace_paths[: args.limit]

    inventory_rows: list[dict[str, Any]] = []
    eligible: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, trace_path in enumerate(trace_paths, start=1):
        row, detail = build_inventory_row(trace_path, project_root, relations)
        inventory_rows.append(row)
        if row["candidate_eligible"]:
            eligible.append((row, detail))
        if index % 1000 == 0 or index == len(trace_paths):
            print(f"Trace inventory: {index}/{len(trace_paths)}", flush=True)

    candidates = select_balanced_candidates(
        eligible,
        project_root,
        min(args.candidate_limit, len(eligible)),
    )
    write_csv_atomic(inventory_path, inventory_rows, INVENTORY_FIELDS)
    write_csv_atomic(candidate_path, candidates, CANDIDATE_FIELDS)

    status_counts = Counter(str(row["audit_status"]) for row in inventory_rows)
    length_counts = Counter(int(row["screenshot_count"]) for row in inventory_rows)
    pattern_counts = Counter(str(row["recommended_pattern"]) for row in candidates)
    print("RICO filtered_traces audit complete")
    print(f"Trace rows:          {len(inventory_rows)}")
    print(f"Complete traces:     {status_counts['complete']}")
    print(f"Hold traces:         {status_counts['hold']}")
    print(f"Eligible 2-6 steps:  {len(eligible)}")
    print(f"Candidate rows:      {len(candidates)}")
    print(f"Inventory:           {inventory_path}")
    print(f"Candidates:          {candidate_path}")
    print("Trace lengths:")
    for length in sorted(length_counts):
        print(f"- {length}: {length_counts[length]}")
    print("Candidate patterns:")
    for pattern in PATTERN_ORDER:
        if pattern_counts[pattern]:
            print(f"- {pattern}: {pattern_counts[pattern]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
