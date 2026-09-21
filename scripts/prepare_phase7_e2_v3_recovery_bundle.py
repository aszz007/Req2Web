"""Build the E2 v3 recovery1 payload from the frozen v3 public archive.

The public packets and model-visible protocol remain byte-identical.  Only the
recovery runtime, bound-interpreter supervisor, and action template are added;
the superseded supervisor byte is replaced in the new archive revision.
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


REPLACEMENTS = {
    "scripts/phase7_e2_supervise.py",
}
ADDITIONS = {
    "scripts/phase7_e2_diagnostic_v3_recovery_runtime.py",
    "fixtures/phase7_e2_action_v3_recovery1.template.json",
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
            "E2 v3 recovery1 for one dependency-preflight failure that consumed "
            "the original development claim before any model session started"
        ),
        "source_commit": old_manifest["source_commit"],
        "base_e2_v3_archive_sha256": handoff["archive_sha256"],
        "public_manifest_sha256": old_manifest["metadata"]["public_manifest_sha256"],
        "builder_sha256": archive_api.sha(Path(__file__).read_bytes()),
        "recovery_runtime_sha256": archive_api.sha(
            (ROOT / "scripts/phase7_e2_diagnostic_v3_recovery_runtime.py").read_bytes()
        ),
        "public_packets_changed": False,
        "model_visible_protocol_changed": False,
        "model_selection": "Qwen3.5-9B BF16, unchanged from E2 v3",
        "automatic_retry_cap": 0,
        "recovery_claim_cap": 1,
        "required_prior_failure": "ModuleNotFoundError before every model session",
        "runtime_selected_or_approved": False,
        "development_sessions_executed": 0,
        "measured_sessions_executed": 0,
        "excluded": old_manifest["metadata"]["excluded"],
    }
    return archive_api.write_archive(files, destination, metadata)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-payload",
        type=Path,
        default=ROOT
        / "outputs/phase7_e2_diagnostic_v3/payload-20260917-final",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source_payload, args.output), indent=2))
