"""Run or aggregate Phase 4 real-browser acceptance evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_runtime.phase4_browser_acceptance import (  # noqa: E402
    EVIDENCE_SCOPES,
    Phase4BrowserAcceptanceError,
    build_browser_canary_receipt,
    run_real_browser_case_audit,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the accepted AcceptancePlan/Binding browser executor against "
            "one exact ResultPackage, or aggregate three canonical canary audits."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    case = subparsers.add_parser(
        "case",
        help="Execute one exact package in a real local browser.",
    )
    case.add_argument("--package-root", required=True, type=Path)
    case.add_argument("--output-root", required=True, type=Path)
    case.add_argument("--run-id", required=True)
    case.add_argument("--case-index", required=True, type=int)
    case.add_argument("--case-id", required=True)
    case.add_argument(
        "--evidence-scope",
        required=True,
        choices=tuple(sorted(EVIDENCE_SCOPES)),
    )
    case.add_argument(
        "--source-case-summary",
        type=Path,
        help=(
            "Required for phase4_canonical_canary/full evidence; supplies the "
            "exact source case_summary_identity."
        ),
    )
    case.add_argument("--timeout-ms", type=int, default=5_000)

    canary = subparsers.add_parser(
        "canary",
        help="Aggregate exactly three canonical case audits.",
    )
    canary.add_argument("--flow-result-root", required=True, type=Path)
    canary.add_argument(
        "--case-audit",
        required=True,
        type=Path,
        action="append",
        help="Repeat exactly three times in case order.",
    )
    canary.add_argument("--output", required=True, type=Path)
    return parser


def _case_summary_identity(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase4BrowserAcceptanceError(
            "source case summary is not valid JSON"
        ) from exc
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("case_summary_identity"), dict)
    ):
        raise Phase4BrowserAcceptanceError(
            "source case summary identity is absent"
        )
    return value["case_summary_identity"]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "case":
            audit = run_real_browser_case_audit(
                package_root=args.package_root,
                output_root=args.output_root,
                run_id=args.run_id,
                case_index=args.case_index,
                case_id=args.case_id,
                evidence_scope=args.evidence_scope,
                source_case_summary_identity=_case_summary_identity(
                    args.source_case_summary
                ),
                timeout_ms=args.timeout_ms,
            )
            print(
                "[P4-BROWSER] "
                f"case={audit['case_id']} "
                f"browser_execution={audit['browser_execution_status']} "
                f"page_spec_conformance={audit['page_spec_conformance_status']} "
                "semantic_alignment="
                f"{audit['semantic_alignment']['status']}/"
                f"{audit['semantic_alignment']['disposition']} "
                f"real_browser_executed={str(audit['real_browser_executed']).lower()} "
                f"automation_reliable={str(audit['automation_reliable']).lower()}",
                flush=True,
            )
            return 0 if audit["browser_status"] == "pass" else 2
        receipt = build_browser_canary_receipt(
            flow_result_root=args.flow_result_root,
            case_audit_paths=tuple(args.case_audit),
            output_path=args.output,
        )
        print(
            "[P4-BROWSER] "
            f"canary_cases={receipt['canary_case_count']} "
            f"continuation_allowed={str(receipt['continuation_allowed']).lower()}",
            flush=True,
        )
        return 0
    except (OSError, Phase4BrowserAcceptanceError, ValueError) as exc:
        print(f"[P4-BROWSER] failed closed: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
