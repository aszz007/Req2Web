from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any


INVALID_WINDOWS_CHARS = re.compile(r'[<>:"|?*]')
RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def sanitize_part(part: str) -> str:
    sanitized = INVALID_WINDOWS_CHARS.sub("_", part).rstrip(" .")
    if not sanitized:
        sanitized = "_"
    if sanitized.split(".", 1)[0].upper() in RESERVED_WINDOWS_NAMES:
        sanitized = f"_{sanitized}"
    return sanitized


def unique_local_path(parts: tuple[str, ...], archive_name: str, used: dict[str, str]) -> tuple[Path, bool]:
    sanitized_parts = tuple(sanitize_part(part) for part in parts)
    local_path = Path(*sanitized_parts)
    key = local_path.as_posix().casefold()
    collision = key in used and used[key] != archive_name
    if collision:
        digest = hashlib.sha1(archive_name.encode("utf-8")).hexdigest()[:8]
        local_path = local_path.with_name(f"{local_path.stem}__{digest}{local_path.suffix}")
        key = local_path.as_posix().casefold()
    used[key] = archive_name
    return local_path, collision


def write_csv_atomic(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def extract_subset(root: Path, subset: str) -> None:
    archive = root / f"data/raw/vision2web/Vision2Web/archives/{subset}.tar.gz"
    destination = root / "data/raw/vision2web/Vision2Web/extracted"
    mapping_path = root / f"data/processed/vision2web_{subset}_windows_path_map.csv"
    destination.mkdir(parents=True, exist_ok=True)

    used: dict[str, str] = {}
    mappings: list[dict[str, Any]] = []
    extracted_files = 0
    skipped_metadata = 0
    renamed_files = 0
    collisions = 0

    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"unsafe archive member: {member.name}")
            if not pure.parts or ".git" in pure.parts or any(part.startswith("._") for part in pure.parts):
                skipped_metadata += 1
                continue
            local_relative, collision = unique_local_path(tuple(pure.parts), member.name, used)
            local_path = destination / local_relative
            changed = local_relative.as_posix() != pure.as_posix()
            if member.isdir():
                local_path.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                skipped_metadata += 1
                mappings.append(
                    {
                        "subset": subset,
                        "archive_path": member.name,
                        "local_path": "",
                        "action": "skipped_non_file",
                        "collision": collision,
                    }
                )
                continue
            local_path.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise OSError(f"cannot read archive member: {member.name}")
            with source, local_path.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            extracted_files += 1
            if changed:
                renamed_files += 1
            if collision:
                collisions += 1
            if changed or collision:
                mappings.append(
                    {
                        "subset": subset,
                        "archive_path": member.name,
                        "local_path": local_relative.as_posix(),
                        "action": "renamed_for_windows",
                        "collision": collision,
                    }
                )

    fields = ["subset", "archive_path", "local_path", "action", "collision"]
    write_csv_atomic(mapping_path, mappings, fields)
    print(f"Subset: {subset}")
    print(f"Extracted files: {extracted_files}")
    print(f"Renamed for Windows: {renamed_files}")
    print(f"Sanitized path collisions: {collisions}")
    print(f"Skipped metadata/non-files: {skipped_metadata}")
    print(f"Path mapping: {mapping_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("subset", choices=("frontend", "website"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    extract_subset(root, args.subset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
