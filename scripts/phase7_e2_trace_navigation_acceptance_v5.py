"""V5 wrapper around the frozen E2 trace-navigation acceptance semantics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import phase7_e2_diagnostic_v5_protocol as protocol
import phase7_e2_trace_navigation_acceptance_v4 as v4


ASSESSMENT_SCHEMA = "req2web.phase7.e2.trace_navigation_assessment.v5"
RUNTIME_SCHEMA = "req2web.phase7.e2_runtime_result.v5"
SCORE_SCHEMA = "req2web.phase7.e2_diagnostic_score.v5"
base = v4.base
audit_tool_reads = v4.audit_tool_reads


def assess_files(
    *, preparation_root: Path, score_path: Path, runtime_path: Path, raw_root: Path
) -> dict[str, Any]:
    old_values = (
        v4.ASSESSMENT_SCHEMA,
        v4.RUNTIME_SCHEMA,
        v4.SCORE_SCHEMA,
        v4.protocol,
    )
    v4.ASSESSMENT_SCHEMA = ASSESSMENT_SCHEMA
    v4.RUNTIME_SCHEMA = RUNTIME_SCHEMA
    v4.SCORE_SCHEMA = SCORE_SCHEMA
    v4.protocol = protocol
    try:
        return v4.assess_files(
            preparation_root=preparation_root,
            score_path=score_path,
            runtime_path=runtime_path,
            raw_root=raw_root,
        )
    finally:
        (
            v4.ASSESSMENT_SCHEMA,
            v4.RUNTIME_SCHEMA,
            v4.SCORE_SCHEMA,
            v4.protocol,
        ) = old_values


def write_new(path: Path, value: dict[str, Any]) -> None:
    v4.write_new(path, value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preparation-root", type=Path, required=True)
    parser.add_argument("--score", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = assess_files(
            preparation_root=args.preparation_root,
            score_path=args.score,
            runtime_path=args.runtime,
            raw_root=args.raw_root,
        )
        write_new(args.output, result)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "assessed",
                "split": result["split"],
                "development_gate_passed": result.get("development_gate_passed"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
