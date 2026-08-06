#!/usr/bin/env python
"""Launch the A1 enforcement harness only on an independently approved AutoDL host."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from req2web_runtime import (
    AutoDLA1PreflightError, run_autodl_a1_enforcement,
    validate_autodl_a1_control_evidence_bytes, validate_autodl_a1_preflight_plan_bytes,
)


ENTRYPOINT_STATUS = "historical"
ENTRYPOINT_MODE = "historical_execution"
ACTIVE_DEFAULT_ENTRY = False
_ENTRYPOINT_HELP = (
    "HISTORICAL STAGE 3 ENTRYPOINT; NOT THE ACTIVE DEFAULT FULL-FLOW ENTRY. "
    "entrypoint_status=historical active_default_entry=false "
    "entrypoint_mode=historical_execution."
)


def _emit_entrypoint_notice() -> None:
    print(
        json.dumps(
            {
                "active_default_entry": ACTIVE_DEFAULT_ENTRY,
                "entrypoint_mode": ENTRYPOINT_MODE,
                "entrypoint_status": ENTRYPOINT_STATUS,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )


def _contained(child: Path,parent: Path) -> bool:
    try: child.resolve(strict=False).relative_to(parent.resolve(strict=True)); return True
    except ValueError: return False

def main() -> int:
    parser=argparse.ArgumentParser(description=_ENTRYPOINT_HELP + " Run only the fail-closed AutoDL A1 enforcement harness.")
    parser.add_argument("--plan",required=True,type=Path)
    parser.add_argument("--control-evidence",required=True,type=Path)
    parser.add_argument("--package-root",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--execute-enforcement",action="store_true",required=True)
    args=parser.parse_args()
    _emit_entrypoint_notice()
    try:
        package=args.package_root.resolve(strict=True)
        output=args.output.resolve(strict=False)
        if args.output.exists() or args.output.is_symlink() or _contained(output,package) or _contained(args.plan.resolve(strict=True),package) or _contained(args.control_evidence.resolve(strict=True),package):
            raise AutoDLA1PreflightError("cli_path_overlap_or_existing_output")
        plan=validate_autodl_a1_preflight_plan_bytes(args.plan.read_bytes())
        controls=validate_autodl_a1_control_evidence_bytes(args.control_evidence.read_bytes())
        receipt=run_autodl_a1_enforcement(plan,controls,package)
        raw=receipt.canonical_bytes(); output.parent.mkdir(parents=True,exist_ok=True)
        temporary=output.with_name(output.name+".tmp")
        if temporary.exists(): raise AutoDLA1PreflightError("cli_temporary_output_exists")
        temporary.write_bytes(raw); temporary.replace(output)
    except (OSError,ValueError) as exc:
        print(f"error: {exc}",file=sys.stderr); return 2
    print(receipt.data["status"]); return 0
if __name__=="__main__": raise SystemExit(main())
