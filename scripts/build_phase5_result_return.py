"""Build or validate the deterministic owner-custody Phase 5 result tar."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_runtime.phase5_result_return import (  # noqa: E402
    create_phase5_result_return,
    validate_phase5_result_return,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build or validate a deterministic Phase 5 result tar and exact "
            "inventory. Real paths must remain outside the repository."
        )
    )
    parser.add_argument("--result-root", type=Path)
    parser.add_argument("--tar", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def _outside_repository(path: Path, *, must_exist: bool, name: str) -> Path:
    resolved = path.resolve(strict=must_exist)
    try:
        resolved.relative_to(ROOT.resolve(strict=True))
    except ValueError:
        return resolved
    raise ValueError(f"{name} must remain outside the repository")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        tar_path = _outside_repository(
            args.tar,
            must_exist=args.validate_only,
            name="result tar",
        )
        manifest_path = _outside_repository(
            args.manifest,
            must_exist=args.validate_only,
            name="result return manifest",
        )
        if args.validate_only:
            if args.result_root is not None:
                parser.error("--result-root is not used with --validate-only")
            manifest = validate_phase5_result_return(
                tar_path=tar_path,
                manifest_path=manifest_path,
            )
        else:
            if args.result_root is None:
                parser.error("--result-root is required when creating a return")
            result_root = _outside_repository(
                args.result_root,
                must_exist=True,
                name="formal result root",
            )
            manifest = create_phase5_result_return(
                result_root=result_root,
                tar_path=tar_path,
                manifest_path=manifest_path,
            )
    except (OSError, ValueError) as exc:
        print(f"[Phase 5] failed closed: {exc}", file=sys.stderr, flush=True)
        return 2
    payload = manifest.to_dict()
    if payload["schema_version"] == "req2web.phase5.result_return_manifest.v1":
        source_kind = "formal_run"
        source_id = payload["run_id"]
    else:
        source = payload["source_binding"]
        source_kind = source["source_kind"]
        source_id = (
            source["case_id"]
            if source_kind == "semantic_evaluator_case"
            else source["run_id"]
        )
    print(
        "[Phase 5] "
        f"source_kind={source_kind} "
        f"source_id={source_id} "
        f"tar_sha256={payload['tar_sha256']} "
        f"file_count={payload['file_count']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
