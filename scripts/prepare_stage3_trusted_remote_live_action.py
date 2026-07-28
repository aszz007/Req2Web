"""Prepare a canonical trusted-remote live-action payload or receipt locally."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime import autodl_trusted_remote_live_authority as authority


def _candidate_records(path: Path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise SystemExit("candidate records manifest must be a JSON object")
    return {case_id: Path(record_path).read_bytes() for case_id, record_path in manifest.items()}


def main():
    parser = argparse.ArgumentParser(description="Prepare or assemble a signed receipt locally; no remote action is performed.")
    parser.add_argument("--action-time-plan", required=True, type=Path)
    parser.add_argument("--candidate-records", required=True, type=Path)
    parser.add_argument("--instance-facts", required=True, type=Path)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--issued-at-utc", required=True)
    parser.add_argument("--expires-at-utc", required=True)
    parser.add_argument("--output-payload", required=True, type=Path)
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--signer-public-key", type=Path)
    parser.add_argument("--signer-principal")
    parser.add_argument("--output-receipt", type=Path)
    args = parser.parse_args()
    candidates = _candidate_records(args.candidate_records)
    instance = json.loads(args.instance_facts.read_text(encoding="utf-8"))
    payload = authority.build_trusted_remote_live_authority_payload(
        args.action_time_plan.read_bytes(), candidates, instance, args.nonce, args.issued_at_utc, args.expires_at_utc
    )
    payload_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    args.output_payload.write_bytes(payload_bytes)
    receipt_args = (args.signature, args.signer_public_key, args.signer_principal, args.output_receipt)
    if any(value is not None for value in receipt_args):
        if not all(value is not None for value in receipt_args):
            raise SystemExit("signature, signer public key, principal, and output receipt must be supplied together")
        signer = authority.ExpectedLiveAuthoritySigner(args.signer_principal, args.signer_public_key.read_text(encoding="utf-8"))
        receipt = authority.create_trusted_remote_live_authority_receipt(payload, args.signature.read_bytes(), signer)
        args.output_receipt.write_bytes(receipt.canonical_bytes())


if __name__ == "__main__":
    main()
