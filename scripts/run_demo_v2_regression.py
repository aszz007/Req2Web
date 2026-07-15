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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or validate the deterministic Demo v2 12-case regression suite.")
    parser.add_argument("--fixture", type=Path, default=ROOT / "fixtures" / "demo_v2_regression_cases_v1.json")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data" / "processed" / "rag")
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
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
