from __future__ import annotations

"""Build and validate the versioned 12-case Demo v2 regression suite."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_generation import (  # noqa: E402
    DemoV2RegressionRunner,
    RegressionCaseSet,
    RegressionSuiteValidator,
)


ENTRYPOINT_ISOLATION_SCHEMA = "req2web.entrypoint.isolation.v1"
ENTRYPOINT_CLASSIFICATION = "frozen_regression"
ACTIVE_DEFAULT_ENTRY = False
ENTRYPOINT_SCOPE_NOTICE = (
    "Frozen regression utility; it is not the current complete formal flow or "
    "the active default entry."
)
ENTRYPOINT_HELP = (
    "Isolation: entrypoint_classification=frozen_regression; "
    "active_default_entry=false. Build mode rebuilds the frozen regression "
    "outputs; --validate-only only validates existing on-disk results."
)
REGRESSION_MODES = ("build", "validate_only")


def entrypoint_isolation_metadata(*, execution_mode: str) -> dict[str, object]:
    if execution_mode not in REGRESSION_MODES:
        raise ValueError("unsupported frozen regression execution mode")
    mode_notice = (
        "Build the frozen regression outputs."
        if execution_mode == "build"
        else "Validate existing on-disk regression results without rebuilding them."
    )
    return {
        "schema": ENTRYPOINT_ISOLATION_SCHEMA,
        "event": "entrypoint_isolation",
        "entrypoint": "scripts/run_demo_v2_regression.py",
        "entrypoint_classification": ENTRYPOINT_CLASSIFICATION,
        "active_default_entry": ACTIVE_DEFAULT_ENTRY,
        "execution_mode": execution_mode,
        "mode_notice": mode_notice,
        "scope_notice": ENTRYPOINT_SCOPE_NOTICE,
    }


def emit_entrypoint_isolation(*, execution_mode: str) -> None:
    sys.stderr.write(
        json.dumps(
            entrypoint_isolation_metadata(execution_mode=execution_mode),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or validate the deterministic Demo v2 12-case regression suite.",
        epilog=ENTRYPOINT_HELP,
    )
    parser.add_argument("--fixture", type=Path, default=ROOT / "fixtures" / "demo_v2_regression_cases_v1.json")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data" / "processed" / "rag")
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    execution_mode = "validate_only" if args.validate_only else "build"
    emit_entrypoint_isolation(execution_mode=execution_mode)
    try:
        case_set = RegressionCaseSet.load(args.fixture)
        if args.validate_only:
            RegressionSuiteValidator().validate(case_set, args.output_root)
            payload = {"validated": True, "case_set_id": case_set.case_set_id, "output_root": args.output_root.as_posix()}
        else:
            payload = DemoV2RegressionRunner(index_dir=args.index_dir, top_k=args.top_k).run(case_set, args.output_root)
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
