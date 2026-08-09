"""Create one repository-external typed owner receipt for the publication run."""

from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from req2web_runtime.phase5_publication_action import (  # noqa: E402
    _canonical,
    _repository_head,
    create_phase5_publication_owner_action_receipt,
    load_phase5_publication_fixtures,
    phase5_publication_fixture_blob_ids,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a typed external receipt for one owner-approved, "
            "project-authored Phase 5 publication action."
        )
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-action-commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--owner-confirmation-sha256", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--gpu-uuid", required=True)
    parser.add_argument("--ssh-fingerprint-sha256", required=True)
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--integrity-evidence", required=True)
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--hourly-rate-minor-units", type=int, default=0)
    parser.add_argument("--time-cap-seconds", type=int, default=57600)
    parser.add_argument("--cost-cap-minor-units", type=int, default=50000)
    parser.add_argument("--storage-cap-bytes", type=int, default=4294967296)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if _repository_head(ROOT) != args.source_action_commit:
        raise SystemExit("source action commit does not match local HEAD")
    fixture_blob_ids = phase5_publication_fixture_blob_ids(
        ROOT,
        args.source_action_commit,
    )
    receipt = create_phase5_publication_owner_action_receipt(
        source_action_commit=args.source_action_commit,
        run_id=args.run_id,
        fixtures=load_phase5_publication_fixtures(
            ROOT,
            args.source_action_commit,
        ),
        fixture_blob_ids=fixture_blob_ids,
        owner_confirmation_sha256=args.owner_confirmation_sha256,
        instance_id=args.instance_id,
        gpu_uuid=args.gpu_uuid,
        ssh_fingerprint_sha256=args.ssh_fingerprint_sha256,
        repository_root=args.repository_root,
        model_root=args.model_root,
        integrity_evidence=args.integrity_evidence,
        python_executable=args.python_executable,
        result_root=args.result_root,
        hourly_rate_minor_units=args.hourly_rate_minor_units,
        time_cap_seconds=args.time_cap_seconds,
        cost_cap_minor_units=args.cost_cap_minor_units,
        storage_cap_bytes=args.storage_cap_bytes,
    )
    raw = _canonical(receipt)
    output = args.output.resolve(strict=False)
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise SystemExit("owner receipt output parent must already exist")
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    print(
        "[PHASE5-PUBLICATION-AUTHORITY] "
        f"receipt_id={receipt['receipt_id']} "
        f"sha256={sha256(raw).hexdigest()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
