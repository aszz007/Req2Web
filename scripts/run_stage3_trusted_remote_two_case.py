"""Validate and execute the local-only two-case runner dry-run contract."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime import autodl_trusted_remote_two_case_runner as runner


ENTRYPOINT_STATUS = "historical"
ENTRYPOINT_MODE = "replay_only"
ACTIVE_DEFAULT_ENTRY = False
_ENTRYPOINT_HELP = (
    "HISTORICAL STAGE 3 ENTRYPOINT; NOT THE ACTIVE DEFAULT FULL-FLOW ENTRY. "
    "entrypoint_status=historical active_default_entry=false "
    "entrypoint_mode=replay_only."
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


def _candidate_records(path: Path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise SystemExit("candidate records manifest must be a JSON object")
    return {case_id: Path(record_path).read_bytes() for case_id, record_path in manifest.items()}


def main():
    parser = argparse.ArgumentParser(
        description=_ENTRYPOINT_HELP
        + " Dry-run only; never imports a model runtime or contacts AutoDL."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--action-time-plan", required=True, type=Path)
    parser.add_argument("--candidate-records", required=True, type=Path)
    args = parser.parse_args()
    _emit_entrypoint_notice()
    if not args.dry_run:
        raise SystemExit("live execution is unavailable in this local preparation slice")
    result = runner.run_trusted_remote_two_case_dry_run(
        args.package.read_bytes(), args.action_time_plan.read_bytes(), _candidate_records(args.candidate_records), dry_run=True
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
