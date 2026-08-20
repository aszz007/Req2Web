"""Validate GUISpector input packets or score imported decisions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.guispector_sidecar import (  # noqa: E402
    GUISpectorSidecarError,
    build_guispector_comparison,
    guispector_runtime_preflight,
    validate_guispector_evaluation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the read-only GUISpector packet embedded in the Req2Web "
            "Inspector or compute GUISpector-style metrics from a complete "
            "decision import. This command never starts GUISpector or calls a model."
        )
    )
    parser.add_argument(
        "--bundle-root",
        type=Path,
        default=ROOT / "release" / "phase6_reviewer_v17",
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        help=(
            "Complete req2web.guispector.decision_import.v1 JSON produced after "
            "an operator-started external GUISpector run."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="New output path for the descriptive comparison report.",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Report local prerequisite presence without running Docker or a model.",
    )
    return parser


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GUISpectorSidecarError("decision import must be a JSON object")
    return value


def _write_new_json(path: Path, value: object) -> None:
    target = path.resolve(strict=False)
    if target.exists() or target.is_symlink():
        raise GUISpectorSidecarError("output path already exists")
    if not target.parent.is_dir():
        raise GUISpectorSidecarError("output parent directory does not exist")
    target.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        evaluation = validate_guispector_evaluation(args.bundle_root)
        result: dict[str, object] = {
            "status": "validated_prepared_not_executed",
            "evaluation_identity": evaluation["evaluation_identity"],
            "result_package_count": evaluation["scope"]["result_package_count"],
            "acceptance_criterion_count": evaluation["scope"][
                "acceptance_criterion_count"
            ],
        }
        if args.preflight:
            result["runtime_preflight"] = guispector_runtime_preflight()
        if args.decisions is not None:
            comparison = build_guispector_comparison(
                evaluation,
                _read_json(args.decisions),
            )
            result = comparison
            if args.output is not None:
                _write_new_json(args.output, comparison)
                result = {
                    "status": comparison["status"],
                    "comparison_identity": comparison["comparison_identity"],
                    "output": str(args.output.resolve()),
                    "paper_comparison_eligible": False,
                }
        elif args.output is not None:
            raise GUISpectorSidecarError("--output requires --decisions")
    except (OSError, ValueError, json.JSONDecodeError, GUISpectorSidecarError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
