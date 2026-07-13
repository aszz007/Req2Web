from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import ijson
from PIL import Image


INVENTORY_FIELDS = (
    "screen_id",
    "relation_status",
    "screenshot_path",
    "hierarchy_path",
    "screenshot_bytes",
    "hierarchy_bytes",
    "has_screenshot",
    "has_hierarchy",
    "has_semantic_png",
    "has_semantic_json",
    "has_screen_annotation",
    "screen_annotation_split",
    "has_grouping",
    "grouping_elements",
    "has_iconnet",
    "iconnet_elements",
    "has_rico_semantics",
    "rico_semantics_elements",
    "relation_source_count",
)

CANDIDATE_FIELDS = (
    "screen_id",
    "recommended_category",
    "candidate_score",
    "manual_status",
    "manual_notes",
    "selection_reason",
    "source_path",
    "screenshot_path",
    "hierarchy_path",
    "image_valid",
    "image_width",
    "image_height",
    "hierarchy_valid",
    "activity_name",
    "package_name",
    "node_count",
    "leaf_count",
    "text_node_count",
    "clickable_count",
    "scrollable_count",
    "edit_text_count",
    "image_node_count",
    "list_node_count",
    "semantic_component_labels",
    "semantic_icon_classes",
    "semantic_text_button_classes",
    "screen_annotation_split",
    "screen_annotation_preview",
    "has_semantic_annotation",
    "has_screen_annotation",
    "has_grouping",
    "grouping_elements",
    "has_iconnet",
    "iconnet_elements",
    "has_rico_semantics",
    "rico_semantics_elements",
)

CATEGORY_ORDER = (
    "login_auth",
    "empty_error",
    "settings",
    "map_location",
    "dashboard_data",
    "commerce",
    "messaging_social",
    "search",
    "form_input",
    "dialog_overlay",
    "list_feed",
    "media_content",
    "navigation_home",
    "general",
)

CATEGORY_PATTERNS = {
    "login_auth": (
        r"\bpassword\b",
        r"\blog[ -]?in\b",
        r"\bsign[ -]?in\b",
        r"\bsign[ -]?up\b",
        r"\bregister\b",
        r"\bforgot password\b",
    ),
    "empty_error": (
        r"\bno results?\b",
        r"\bno items?\b",
        r"\bempty\b",
        r"\berror\b",
        r"\btry again\b",
        r"\boffline\b",
        r"\bnot found\b",
    ),
    "settings": (r"\bsettings?\b", r"\bpreferences?\b", r"\baccount settings\b"),
    "map_location": (
        r"\bmap\b",
        r"\blocation\b",
        r"\bnearby\b",
        r"\bdirections?\b",
        r"\baddress\b",
    ),
    "dashboard_data": (
        r"\bchart\b",
        r"\bgraph\b",
        r"\bstatistics?\b",
        r"\banalytics?\b",
        r"\breport\b",
        r"\bdashboard\b",
    ),
    "commerce": (
        r"\bcart\b",
        r"\bcheckout\b",
        r"\bprice\b",
        r"\bproduct\b",
        r"\border\b",
        r"\bbuy\b",
        r"\bshop\b",
        r"\bpayment\b",
    ),
    "messaging_social": (
        r"\bchat\b",
        r"\bmessage\b",
        r"\bcomment\b",
        r"\bcontact\b",
        r"\bfollow\b",
        r"\bshare\b",
    ),
    "search": (r"\bsearch\b", r"magnifying[_ ]glass"),
    "form_input": (
        r"text[_ ]input",
        r"edittext",
        r"radio[_ ]button",
        r"checkbox",
        r"dropdown",
        r"spinner",
        r"\bsubmit\b",
    ),
    "dialog_overlay": (r"\bdialog\b", r"\bpopup\b", r"\bmodal\b", r"\balert\b"),
    "list_feed": (r"list[_ ]item", r"recyclerview", r"listview", r"\bfeed\b"),
    "media_content": (
        r"\bvideo\b",
        r"\bmusic\b",
        r"\baudio\b",
        r"\bphoto\b",
        r"\bgallery\b",
        r"\bplay\b",
    ),
    "navigation_home": (
        r"navigation[_ ]bar",
        r"bottomnavigation",
        r"\bhome\b",
        r"three[_ ]bars",
        r"\bmenu\b",
    ),
}


def parse_args() -> argparse.Namespace:
    script_root = Path(__file__).resolve().parent
    project_root = script_root.parent
    parser = argparse.ArgumentParser(
        description="Build a lightweight RICO relation inventory and a reviewed candidate pool."
    )
    parser.add_argument("--project-root", type=Path, default=project_root)
    parser.add_argument("--candidate-limit", type=int, default=180)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N screen IDs for a bounded test run.",
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


def load_manual_reviews(*paths: Path) -> dict[str, tuple[str, str]]:
    reviews: dict[str, tuple[str, str]] = {}
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                screen_id = str(row.get("screen_id", "")).strip()
                status = str(row.get("manual_status", "")).strip()
                notes = str(row.get("manual_notes", "")).strip()
                if screen_id and (status or notes):
                    reviews[screen_id] = (status, notes)
    return reviews


def numeric_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value.lower())


def index_files(directory: Path, suffix: str) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    return {path.stem: path for path in directory.glob(f"*{suffix}") if path.is_file()}


def load_screen_annotations(directory: Path, allowed_ids: set[str]) -> tuple[dict[str, dict[str, str]], int]:
    annotations: dict[str, dict[str, str]] = {}
    duplicate_count = 0
    for path in sorted(directory.glob("*.csv")):
        split = path.stem.lower().replace("valid", "validation")
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            for row in reader:
                screen_id = str(row.get("screen_id") or row.get("image_id") or "").strip()
                if not screen_id or screen_id not in allowed_ids:
                    continue
                label = str(row.get("screen_annotation") or row.get("label") or "").strip()
                if screen_id in annotations:
                    duplicate_count += 1
                    continue
                annotations[screen_id] = {"split": split, "label": label}
    return annotations, duplicate_count


def load_rico_semantics(
    root: Path, allowed_ids: set[str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "grouping": False,
            "grouping_elements": 0,
            "iconnet": False,
            "iconnet_elements": 0,
            "semantics": False,
            "semantics_elements": 0,
            "labels": [],
        }
    )
    for task in ("grouping", "iconnet", "semantics"):
        task_root = root / task
        for path in sorted(task_root.glob("*.json")):
            with path.open("rb") as stream:
                for item in ijson.items(stream, "item"):
                    screen_id = str(item.get("screen_id", ""))
                    if screen_id not in allowed_ids:
                        continue
                    elements = item.get("screen_elements") or []
                    record = result[screen_id]
                    record[task] = True
                    record[f"{task}_elements"] += len(elements)
                    record["labels"].extend(
                        str(element.get("label", ""))
                        for element in elements
                        if element.get("label")
                    )
    return result


def walk_nodes(value: Any) -> Iterable[dict[str, Any]]:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if any(key in current for key in ("class", "bounds", "componentLabel")):
                yield current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def inspect_image(path: Path) -> tuple[bool, int, int]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
        return True, width, height
    except (OSError, ValueError):
        return False, 0, 0


def inspect_hierarchy(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "hierarchy_valid": False,
        "activity_name": "",
        "package_name": "",
        "node_count": 0,
        "leaf_count": 0,
        "text_node_count": 0,
        "clickable_count": 0,
        "scrollable_count": 0,
        "edit_text_count": 0,
        "image_node_count": 0,
        "list_node_count": 0,
        "search_text": "",
    }
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return result

    activity_name = str(data.get("activity_name", ""))
    result["hierarchy_valid"] = True
    result["activity_name"] = activity_name
    result["package_name"] = activity_name.split("/", 1)[0]
    searchable: list[str] = [activity_name]

    for node in walk_nodes(data.get("activity", {}).get("root", {})):
        result["node_count"] += 1
        children = node.get("children")
        if not children:
            result["leaf_count"] += 1
        class_name = str(node.get("class", "")).lower()
        text_values = [str(node.get(key, "")).strip() for key in ("text", "content-desc", "resource-id")]
        text_values = [value for value in text_values if value and value.lower() != "none"]
        if text_values:
            result["text_node_count"] += 1
            searchable.extend(text_values)
        if node.get("clickable") is True:
            result["clickable_count"] += 1
        if node.get("scrollable-horizontal") is True or node.get("scrollable-vertical") is True:
            result["scrollable_count"] += 1
        if "edittext" in class_name:
            result["edit_text_count"] += 1
        if "image" in class_name:
            result["image_node_count"] += 1
        if "listview" in class_name or "recyclerview" in class_name:
            result["list_node_count"] += 1

    result["search_text"] = " ".join(searchable)[:8000]
    return result


def inspect_semantic_hierarchy(path: Path) -> dict[str, str]:
    labels: Counter[str] = Counter()
    icons: Counter[str] = Counter()
    buttons: Counter[str] = Counter()
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "semantic_component_labels": "",
            "semantic_icon_classes": "",
            "semantic_text_button_classes": "",
            "search_text": "",
        }

    for node in walk_nodes(data):
        if node.get("componentLabel"):
            labels[str(node["componentLabel"])] += 1
        if node.get("iconClass"):
            icons[str(node["iconClass"])] += 1
        if node.get("textButtonClass"):
            buttons[str(node["textButtonClass"])] += 1

    def summarize(values: Counter[str]) -> str:
        return ";".join(f"{name}={count}" for name, count in values.most_common(20))

    search_text = " ".join((*labels.keys(), *icons.keys(), *buttons.keys()))
    return {
        "semantic_component_labels": summarize(labels),
        "semantic_icon_classes": summarize(icons),
        "semantic_text_button_classes": summarize(buttons),
        "search_text": search_text,
    }


def classify(text: str) -> tuple[str, int, list[str]]:
    normalized = text.lower().replace("-", "_")
    matches: dict[str, int] = {}
    evidence: dict[str, list[str]] = {}
    for category, patterns in CATEGORY_PATTERNS.items():
        matched = [pattern for pattern in patterns if re.search(pattern, normalized)]
        if matched:
            matches[category] = len(matched)
            evidence[category] = matched
    if not matches:
        return "general", 0, []
    category = min(
        matches,
        key=lambda name: (-matches[name], CATEGORY_ORDER.index(name)),
    )
    return category, matches[category], evidence[category]


def relation_score(
    has_semantic: bool,
    screen_annotation: dict[str, str] | None,
    semantics: dict[str, Any],
) -> int:
    score = 2 if has_semantic else 0
    if screen_annotation:
        score += 4
        score += min(len(screen_annotation.get("label", "")) // 500, 4)
    score += int(bool(semantics.get("grouping")))
    score += int(bool(semantics.get("iconnet")))
    score += int(bool(semantics.get("semantics"))) * 2
    score += min(
        (
            int(semantics.get("grouping_elements", 0))
            + int(semantics.get("iconnet_elements", 0))
            + int(semantics.get("semantics_elements", 0))
        )
        // 10,
        3,
    )
    return score


def build_inventory(
    screen_ids: list[str],
    project_root: Path,
    screenshots: dict[str, Path],
    hierarchies: dict[str, Path],
    semantic_pngs: dict[str, Path],
    semantic_jsons: dict[str, Path],
    screen_annotations: dict[str, dict[str, str]],
    rico_semantics: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for screen_id in screen_ids:
        screenshot = screenshots.get(screen_id)
        hierarchy = hierarchies.get(screen_id)
        semantic_png = semantic_pngs.get(screen_id)
        semantic_json = semantic_jsons.get(screen_id)
        annotation = screen_annotations.get(screen_id)
        semantics = rico_semantics.get(screen_id, {})
        core_pair = bool(screenshot and hierarchy)
        semantic_pair = bool(semantic_png and semantic_json)
        source_count = sum(
            (
                core_pair,
                semantic_pair,
                bool(annotation),
                bool(semantics.get("grouping")),
                bool(semantics.get("iconnet")),
                bool(semantics.get("semantics")),
            )
        )
        rows.append(
            {
                "screen_id": screen_id,
                "relation_status": "complete_core" if core_pair else "missing_core_file",
                "screenshot_path": relative_path(screenshot, project_root) if screenshot else "",
                "hierarchy_path": relative_path(hierarchy, project_root) if hierarchy else "",
                "screenshot_bytes": screenshot.stat().st_size if screenshot else 0,
                "hierarchy_bytes": hierarchy.stat().st_size if hierarchy else 0,
                "has_screenshot": bool(screenshot),
                "has_hierarchy": bool(hierarchy),
                "has_semantic_png": bool(semantic_png),
                "has_semantic_json": bool(semantic_json),
                "has_screen_annotation": bool(annotation),
                "screen_annotation_split": annotation.get("split", "") if annotation else "",
                "has_grouping": bool(semantics.get("grouping")),
                "grouping_elements": semantics.get("grouping_elements", 0),
                "has_iconnet": bool(semantics.get("iconnet")),
                "iconnet_elements": semantics.get("iconnet_elements", 0),
                "has_rico_semantics": bool(semantics.get("semantics")),
                "rico_semantics_elements": semantics.get("semantics_elements", 0),
                "relation_source_count": source_count,
            }
        )
    return rows


def build_candidates(
    screen_ids: list[str],
    project_root: Path,
    screenshots: dict[str, Path],
    hierarchies: dict[str, Path],
    semantic_pngs: dict[str, Path],
    semantic_jsons: dict[str, Path],
    screen_annotations: dict[str, dict[str, str]],
    rico_semantics: dict[str, dict[str, Any]],
    candidate_limit: int,
) -> list[dict[str, Any]]:
    preliminary: list[tuple[int, str, str]] = []
    for screen_id in screen_ids:
        if screen_id not in screenshots or screen_id not in hierarchies:
            continue
        annotation = screen_annotations.get(screen_id)
        semantics = rico_semantics.get(screen_id, {})
        if not annotation and not any(semantics.get(name) for name in ("grouping", "iconnet", "semantics")):
            continue
        search_text = " ".join(
            (
                annotation.get("label", "") if annotation else "",
                " ".join(semantics.get("labels", [])),
            )
        )
        category, confidence, _ = classify(search_text)
        score = relation_score(screen_id in semantic_jsons, annotation, semantics) + confidence * 2
        preliminary.append((score, screen_id, category))

    deep_limit = min(len(preliminary), max(candidate_limit * 4, 400))
    preliminary.sort(key=lambda item: (-item[0], numeric_sort_key(item[1])))
    preliminary_by_category: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for item in preliminary:
        preliminary_by_category[item[2]].append(item)
    deep_ids: list[str] = []
    deep_id_set: set[str] = set()
    deep_quota = max(1, math.ceil(deep_limit / len(CATEGORY_ORDER)))
    for category in CATEGORY_ORDER:
        for _, screen_id, _ in preliminary_by_category.get(category, [])[:deep_quota]:
            if screen_id not in deep_id_set:
                deep_ids.append(screen_id)
                deep_id_set.add(screen_id)
    for _, screen_id, _ in preliminary:
        if len(deep_ids) >= deep_limit:
            break
        if screen_id not in deep_id_set:
            deep_ids.append(screen_id)
            deep_id_set.add(screen_id)
    deep_limit = len(deep_ids)
    deep_rows: list[dict[str, Any]] = []

    for index, screen_id in enumerate(deep_ids, start=1):
        screenshot = screenshots[screen_id]
        hierarchy = hierarchies[screen_id]
        semantic_json = semantic_jsons.get(screen_id)
        annotation = screen_annotations.get(screen_id)
        semantics = rico_semantics.get(screen_id, {})
        image_valid, width, height = inspect_image(screenshot)
        hierarchy_info = inspect_hierarchy(hierarchy)
        semantic_info = (
            inspect_semantic_hierarchy(semantic_json)
            if semantic_json
            else {
                "semantic_component_labels": "",
                "semantic_icon_classes": "",
                "semantic_text_button_classes": "",
                "search_text": "",
            }
        )
        search_text = " ".join(
            (
                annotation.get("label", "") if annotation else "",
                " ".join(semantics.get("labels", [])),
                hierarchy_info["search_text"],
                semantic_info["search_text"],
            )
        )
        category, confidence, evidence = classify(search_text)
        score = relation_score(bool(semantic_json), annotation, semantics)
        score += confidence * 2
        score += min(int(hierarchy_info["node_count"]) // 25, 4)
        score += 2 if image_valid and hierarchy_info["hierarchy_valid"] else -20
        source_names = ["combined"]
        if semantic_json:
            source_names.append("semantic_annotations")
        if annotation:
            source_names.append("screen_annotation")
        for task in ("grouping", "iconnet", "semantics"):
            if semantics.get(task):
                source_names.append(task)
        reason = f"category={category};sources={'+'.join(source_names)}"
        if evidence:
            reason += f";signals={len(evidence)}"

        deep_rows.append(
            {
                "screen_id": screen_id,
                "recommended_category": category,
                "candidate_score": score,
                "selection_reason": reason,
                "source_path": relative_path(screenshot.parent, project_root),
                "screenshot_path": relative_path(screenshot, project_root),
                "hierarchy_path": relative_path(hierarchy, project_root),
                "image_valid": image_valid,
                "image_width": width,
                "image_height": height,
                "hierarchy_valid": hierarchy_info["hierarchy_valid"],
                "activity_name": hierarchy_info["activity_name"],
                "package_name": hierarchy_info["package_name"],
                "node_count": hierarchy_info["node_count"],
                "leaf_count": hierarchy_info["leaf_count"],
                "text_node_count": hierarchy_info["text_node_count"],
                "clickable_count": hierarchy_info["clickable_count"],
                "scrollable_count": hierarchy_info["scrollable_count"],
                "edit_text_count": hierarchy_info["edit_text_count"],
                "image_node_count": hierarchy_info["image_node_count"],
                "list_node_count": hierarchy_info["list_node_count"],
                "semantic_component_labels": semantic_info["semantic_component_labels"],
                "semantic_icon_classes": semantic_info["semantic_icon_classes"],
                "semantic_text_button_classes": semantic_info["semantic_text_button_classes"],
                "screen_annotation_split": annotation.get("split", "") if annotation else "",
                "screen_annotation_preview": annotation.get("label", "")[:500] if annotation else "",
                "has_semantic_annotation": bool(semantic_json and screen_id in semantic_pngs),
                "has_screen_annotation": bool(annotation),
                "has_grouping": bool(semantics.get("grouping")),
                "grouping_elements": semantics.get("grouping_elements", 0),
                "has_iconnet": bool(semantics.get("iconnet")),
                "iconnet_elements": semantics.get("iconnet_elements", 0),
                "has_rico_semantics": bool(semantics.get("semantics")),
                "rico_semantics_elements": semantics.get("semantics_elements", 0),
                "manual_status": "",
                "manual_notes": "",
            }
        )
        if index % 100 == 0 or index == deep_limit:
            print(f"Deep validation: {index}/{deep_limit}", flush=True)

    valid_rows = [
        row for row in deep_rows if row["image_valid"] and row["hierarchy_valid"]
    ]
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid_rows:
        by_category[row["recommended_category"]].append(row)
    for rows in by_category.values():
        rows.sort(key=lambda row: (-int(row["candidate_score"]), numeric_sort_key(row["screen_id"])))

    print(
        "Deep category availability: "
        + ", ".join(
            f"{category}={len(by_category.get(category, []))}"
            for category in CATEGORY_ORDER
            if by_category.get(category)
        ),
        flush=True,
    )

    quota = max(1, math.ceil(candidate_limit / len(CATEGORY_ORDER)))
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    package_counts: Counter[str] = Counter()

    def can_add(row: dict[str, Any]) -> bool:
        package = str(row["package_name"])
        return row["screen_id"] not in selected_ids and (not package or package_counts[package] < 2)

    def add(row: dict[str, Any]) -> None:
        selected.append(row)
        selected_ids.add(str(row["screen_id"]))
        package = str(row["package_name"])
        if package:
            package_counts[package] += 1

    for category in CATEGORY_ORDER:
        if len(selected) >= candidate_limit:
            break
        added = 0
        for row in by_category.get(category, []):
            if len(selected) >= candidate_limit:
                break
            if can_add(row):
                add(row)
                added += 1
            if added >= quota:
                break

    offsets = {category: 0 for category in CATEGORY_ORDER}
    while len(selected) < candidate_limit:
        progress = False
        for category in CATEGORY_ORDER:
            rows = by_category.get(category, [])
            while offsets[category] < len(rows):
                row = rows[offsets[category]]
                offsets[category] += 1
                if can_add(row):
                    add(row)
                    progress = True
                    break
            if len(selected) >= candidate_limit:
                break
        if not progress:
            break

    return sorted(
        selected,
        key=lambda row: (
            CATEGORY_ORDER.index(str(row["recommended_category"])),
            -int(row["candidate_score"]),
            numeric_sort_key(str(row["screen_id"])),
        ),
    )


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    combined_root = project_root / "data/raw/rico/extracted/combined"
    semantic_root = project_root / "data/raw/rico/extracted/semantic_annotations"
    rico_semantics_root = (
        project_root / "data/raw/rico_semantics/extracted/rico_semantics-main/data"
    )
    screen_annotation_root = (
        project_root / "data/raw/screen_annotation/extracted/screen_annotation-main"
    )
    inventory_path = project_root / "data/processed/rico_combined_inventory.csv"
    candidate_path = project_root / "data/processed/rico_candidate_pool.csv"
    decision_path = project_root / "data/processed/rico_manual_decisions.csv"

    for required in (combined_root, semantic_root, rico_semantics_root, screen_annotation_root):
        if not required.is_dir():
            raise FileNotFoundError(f"Required dataset directory not found: {required}")

    print("Indexing RICO file names...", flush=True)
    screenshots = index_files(combined_root, ".jpg")
    hierarchies = index_files(combined_root, ".json")
    semantic_pngs = index_files(semantic_root, ".png")
    semantic_jsons = index_files(semantic_root, ".json")
    all_ids = sorted(set(screenshots) | set(hierarchies), key=numeric_sort_key)
    if args.limit is not None:
        all_ids = all_ids[: args.limit]
    allowed_ids = set(all_ids)

    print("Loading Screen Annotation CSV files...", flush=True)
    screen_annotations, annotation_duplicates = load_screen_annotations(
        screen_annotation_root, allowed_ids
    )
    print("Streaming RICO Semantics JSON files...", flush=True)
    rico_semantics = load_rico_semantics(rico_semantics_root, allowed_ids)

    inventory = build_inventory(
        all_ids,
        project_root,
        screenshots,
        hierarchies,
        semantic_pngs,
        semantic_jsons,
        screen_annotations,
        rico_semantics,
    )
    candidates = build_candidates(
        all_ids,
        project_root,
        screenshots,
        hierarchies,
        semantic_pngs,
        semantic_jsons,
        screen_annotations,
        rico_semantics,
        min(args.candidate_limit, len(all_ids)),
    )
    manual_reviews = load_manual_reviews(decision_path, candidate_path)
    for row in candidates:
        review = manual_reviews.get(str(row["screen_id"]))
        if review:
            row["manual_status"], row["manual_notes"] = review
    write_csv_atomic(inventory_path, inventory, INVENTORY_FIELDS)
    write_csv_atomic(candidate_path, candidates, CANDIDATE_FIELDS)

    missing_core = sum(row["relation_status"] != "complete_core" for row in inventory)
    category_counts = Counter(row["recommended_category"] for row in candidates)
    print("RICO combined audit complete")
    print(f"Indexed screen IDs:       {len(inventory)}")
    print(f"Missing core pairs:       {missing_core}")
    print(f"Semantic annotation pair: {sum(bool(row['has_semantic_png'] and row['has_semantic_json']) for row in inventory)}")
    print(f"Screen Annotation links:  {sum(bool(row['has_screen_annotation']) for row in inventory)}")
    print(f"RICO Semantics links:     {sum(bool(row['has_grouping'] or row['has_iconnet'] or row['has_rico_semantics']) for row in inventory)}")
    print(f"Annotation duplicate IDs: {annotation_duplicates}")
    print(f"Candidate rows:           {len(candidates)}")
    print(f"Inventory:                {inventory_path}")
    print(f"Candidates:               {candidate_path}")
    print("Candidate categories:")
    for category in CATEGORY_ORDER:
        if category_counts[category]:
            print(f"- {category}: {category_counts[category]}")
    if args.limit is None and len(inventory) != 66261:
        print(f"WARNING: official RICO count is 66261, local index contains {len(inventory)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
