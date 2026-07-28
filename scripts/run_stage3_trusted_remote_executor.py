#!/usr/bin/env python3
"""Run the fixed Req2Web trusted-remote two-case executor.

This command never accepts a backend, callback, command, worker, or authority
override.  The detached pre-run signature is verified against the explicit
manager-selected public key before the fixed child process is started.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.autodl_trusted_remote_executor import (
    ExpectedPreRunSigner,
    TrustedRemoteExecutionPackageV2,
    TrustedRemotePreRunAuthorizationReceiptV2,
    create_trusted_remote_project_temp_root_v2,
)
from req2web_runtime.autodl_trusted_remote_case_loader import load_fixed_trusted_remote_case_inputs_v2
from req2web_runtime.autodl_trusted_remote_live_route import run_trusted_remote_two_case_live_slice_v2
from req2web_runtime.autodl_trusted_remote_records import TrustedRemoteActionTimePlan


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the fixed trusted-remote Qwen two-case executor.")
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
    args = parser.parse_args(argv)

    import json

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
    )
    summary = {"state": run.return_manifest["state"], "route_bundle_id": run.route_bundle.to_dict()["bundle_id"], "route_bundle_sha256": run.route_bundle.sha256(), "return_manifest": run.return_manifest}
    sys.stdout.buffer.write(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
