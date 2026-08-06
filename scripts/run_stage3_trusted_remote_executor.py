#!/usr/bin/env python3
"""Run the fixed Req2Web trusted-remote two-case executor.

This command never accepts a backend, callback, command, worker, or authority
override.  The detached pre-run signature is verified against the explicit
manager-selected public key before the fixed child process is started.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.autodl_trusted_remote_executor import (
    MODEL_TEXT_STREAM_MODES,
    ExpectedPreRunSigner,
    TRUSTED_REMOTE_STREAM_OUTPUT_MODE,
    TrustedRemoteExecutionPackageV2,
    TrustedRemotePreRunAuthorizationReceiptV2,
    create_trusted_remote_project_temp_root_v2,
)
from req2web_runtime.autodl_trusted_remote_case_loader import load_fixed_trusted_remote_case_inputs_v2
from req2web_runtime.autodl_trusted_remote_live_route import run_trusted_remote_two_case_live_slice_v2
from req2web_runtime.autodl_trusted_remote_records import TrustedRemoteActionTimePlan


ENTRYPOINT_STATUS = "historical"
ENTRYPOINT_MODE = "historical_execution"
ACTIVE_DEFAULT_ENTRY = False
_ENTRYPOINT_HELP = (
    "HISTORICAL STAGE 3 ENTRYPOINT; NOT THE ACTIVE DEFAULT FULL-FLOW ENTRY. "
    "entrypoint_status=historical active_default_entry=false "
    "entrypoint_mode=historical_execution."
)


def _emit_entrypoint_notice() -> None:
    sys.stderr.write(
        json.dumps(
            {
                "active_default_entry": ACTIVE_DEFAULT_ENTRY,
                "entrypoint_mode": ENTRYPOINT_MODE,
                "entrypoint_status": ENTRYPOINT_STATUS,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    sys.stderr.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=_ENTRYPOINT_HELP
        + " Run the fixed trusted-remote Qwen two-case executor."
    )
    parser.add_argument("--package", required=True)
    parser.add_argument("--action-time-plan", required=True)
    parser.add_argument("--pre-run-receipt", required=True)
    parser.add_argument("--signer-public-key", required=True, help="Path to the expected public key; never a private key.")
    parser.add_argument("--instance-facts", required=True)
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--package-root", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--return-root", required=True)
    parser.add_argument("--project-temp-root", required=True)
    parser.add_argument("--cancel-request-path")
    parser.add_argument(
        "--stream-output",
        choices=MODEL_TEXT_STREAM_MODES,
        default=TRUSTED_REMOTE_STREAM_OUTPUT_MODE,
        help="Optional non-authoritative model text observation on stderr.",
    )
    args = parser.parse_args(argv)
    _emit_entrypoint_notice()

    signer_text = Path(args.signer_public_key).read_text(encoding="utf-8").strip()
    instance_facts = json.loads(Path(args.instance_facts).read_text(encoding="utf-8"))
    package = TrustedRemoteExecutionPackageV2.from_bytes(Path(args.package).read_bytes())
    plan = TrustedRemoteActionTimePlan.from_bytes(Path(args.action_time_plan).read_bytes())
    project_temp_root = create_trusted_remote_project_temp_root_v2(Path(args.project_temp_root), package)
    materials = load_fixed_trusted_remote_case_inputs_v2(Path(args.repository_root), package, project_temp_root)
    run = run_trusted_remote_two_case_live_slice_v2(
        execution_package=package,
        action_time_plan=plan,
        pre_run_receipt=TrustedRemotePreRunAuthorizationReceiptV2.from_bytes(Path(args.pre_run_receipt).read_bytes()),
        expected_signer=ExpectedPreRunSigner(signer_text),
        expected_instance_facts=instance_facts,
        repository_root=Path(args.repository_root),
        package_root=Path(args.package_root),
        model_root=Path(args.model_root),
        runtime_root=Path(args.runtime_root),
        result_root=Path(args.result_root),
        case_inputs=materials.case_inputs,
        return_root=Path(args.return_root),
        project_temp_root=project_temp_root,
        cancel_request_path=Path(args.cancel_request_path) if args.cancel_request_path else None,
        stream_output=args.stream_output,
    )
    summary = {
        "state": run.return_manifest["state"],
        "route_bundle_id": run.route_bundle.to_dict()["bundle_id"],
        "route_bundle_sha256": run.route_bundle.sha256(),
        "return_manifest": run.return_manifest,
        "real_browser_executed": False,
        "formal_browser_success": False,
    }
    sys.stdout.buffer.write(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
