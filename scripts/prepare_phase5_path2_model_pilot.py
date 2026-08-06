"""Prepare repository-external Path 2 model-pilot authority artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from req2web_runtime.phase5_path2_model_pilot import (
    prepare_phase5_path2_model_pilot,
)


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "phase5_sealed_action_package_synthetic_v1.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a bounded project-authored Path 2 real-model pilot. "
            "This writes no H1/gold and does not connect to a server or load a model."
        )
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-action-commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--gpu-uuid", required=True)
    parser.add_argument("--ssh-fingerprint-sha256", required=True)
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--model-integrity-evidence", required=True)
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--hourly-rate-minor-units", type=int, default=0)
    parser.add_argument("--time-cap-seconds", type=int, default=7200)
    parser.add_argument("--cost-cap-minor-units", type=int, default=5000)
    parser.add_argument("--storage-cap-bytes", type=int, default=1073741824)
    return parser


def main() -> int:
    args = _parser().parse_args()
    prepared = prepare_phase5_path2_model_pilot(
        fixture_path=FIXTURE,
        output_root=args.output_root.resolve(strict=False),
        source_action_commit=args.source_action_commit,
        run_id=args.run_id,
        instance_id=args.instance_id,
        gpu_uuid=args.gpu_uuid,
        ssh_fingerprint_sha256=args.ssh_fingerprint_sha256,
        repository_root=args.repository_root,
        model_root=args.model_root,
        model_integrity_evidence=args.model_integrity_evidence,
        python_executable=args.python_executable,
        result_root=args.result_root,
        hourly_rate_minor_units=args.hourly_rate_minor_units,
        time_cap_seconds=args.time_cap_seconds,
        cost_cap_minor_units=args.cost_cap_minor_units,
        storage_cap_bytes=args.storage_cap_bytes,
    )
    print(
        "[Phase 5] "
        f"status={prepared.receipt['status']} "
        f"run_id={prepared.receipt['run_id']} "
        f"package_sha256={prepared.package.sha256()} "
        f"authority_sha256={prepared.authority.sha256()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
