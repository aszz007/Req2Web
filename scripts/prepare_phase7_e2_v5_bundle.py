"""Build the local-only E2 v5 server payload from the fixed v4 payload.

Public packet bytes remain unchanged.  The archive updates the shared
supervisor and adds only the separately versioned v5 protocol/runtime/action
files.  It contains no model weights, credentials, private gold, evaluator
output, or historical returned results.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_phase7_autodl_bundle as archive_api


REPLACEMENTS = {"scripts/phase7_e2_supervise.py"}
ADDITIONS = {
    "scripts/phase7_e2_diagnostic_v5_protocol.py",
    "scripts/phase7_e2_diagnostic_v5_runtime.py",
    "fixtures/phase7_e2_diagnostic_v5_protocol.json",
    "fixtures/phase7_e2_action_v5.template.json",
}


def build(source_payload: Path, destination: Path) -> dict[str, object]:
    handoff = json.loads((source_payload / "handoff_manifest.json").read_bytes())
    archive = source_payload / handoff["archive_filename"]
    archive_api.validate_archive(archive, handoff["archive_sha256"])

    files: dict[str, bytes] = {}
    with zipfile.ZipFile(archive) as source:
        old_manifest = json.loads(source.read("payload_manifest.json"))
        for row in old_manifest["files"]:
            relative = row["path"]
            files[relative] = (
                archive_api.checked_bytes(ROOT / relative)
                if relative in REPLACEMENTS
                else source.read(relative)
            )
    for relative in ADDITIONS:
        files[relative] = archive_api.checked_bytes(ROOT / relative)

    metadata = {
        "purpose": (
            "E2 v5 one-batch development readiness with mandatory agent-selected "
            "grounded trace treatment and parallel final endpoints"
        ),
        "source_commit": old_manifest["source_commit"],
        "base_e2_v4_archive_sha256": handoff["archive_sha256"],
        "public_manifest_sha256": old_manifest["metadata"]["public_manifest_sha256"],
        "builder_sha256": archive_api.sha(Path(__file__).read_bytes()),
        "v5_protocol_sha256": archive_api.sha(
            (ROOT / "scripts/phase7_e2_diagnostic_v5_protocol.py").read_bytes()
        ),
        "v5_runtime_sha256": archive_api.sha(
            (ROOT / "scripts/phase7_e2_diagnostic_v5_runtime.py").read_bytes()
        ),
        "v5_contract_sha256": archive_api.sha(
            (ROOT / "fixtures/phase7_e2_diagnostic_v5_protocol.json").read_bytes()
        ),
        "public_packets_changed": False,
        "model_visible_protocol_changed": True,
        "model_selection": "Qwen3.5-9B BF16, unchanged from E2 v4",
        "automatic_retry_cap": 0,
        "development_claim_cap": 1,
        "runtime_selected_or_approved": False,
        "development_sessions_executed": 0,
        "measured_sessions_executed": 0,
        "measured_requires_passing_v5_assessment": True,
        "excluded": old_manifest["metadata"]["excluded"],
    }
    return archive_api.write_archive(files, destination, metadata)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-payload",
        type=Path,
        default=ROOT / "outputs/phase7_e2_diagnostic_v4/payload-20260919-ready",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source_payload, args.output), indent=2))
