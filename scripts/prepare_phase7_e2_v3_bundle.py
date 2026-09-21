"""Build the isolated E2 v3 upload candidate without private scoring gold."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_phase7_autodl_bundle as archive_api
import phase7_e2_diagnostic_v3_candidate as candidate


EXPLICIT = (
    "scripts/phase7_e2_diagnostic_runtime.py",
    "scripts/phase7_e2_diagnostic_v3_candidate.py",
    "scripts/phase7_e2_diagnostic_v3_runtime.py",
    "scripts/phase7_e2_supervise.py",
    "scripts/prepare_phase7_autodl_bundle.py",
    "fixtures/phase7_e2_action_v3.template.json",
)
EXCLUDED_SOURCE_PATHS = {
    # Evaluator-only production helper; not required by the diagnostic runtime
    # and excluded to keep every gold-bearing source outside the remote bundle.
    "src/req2web_faults/evaluator_gold.py",
}


def build(preparation: Path, base_payload: Path, destination: Path):
    candidate.validate(preparation)
    receipt = json.loads((preparation / "preparation.json").read_bytes())
    manifest = candidate.validate_public(preparation / "public", receipt["public_manifest_sha256"])
    handoff = json.loads((base_payload / "handoff_manifest.json").read_bytes())
    archive = base_payload / handoff["archive_filename"]
    archive_api.validate_archive(archive, handoff["archive_sha256"])
    files = {}
    with zipfile.ZipFile(archive) as source:
        base = json.loads(source.read("payload_manifest.json"))
        for row in base["files"]:
            path = row["path"]
            if path in EXCLUDED_SOURCE_PATHS:
                continue
            if path.startswith("src/") or path in {
                "runtime/model_integrity.json",
                "runtime/requirements-model.txt",
                "runtime/runtime_profile.json",
                "requirements-phase4-agent-lock.txt",
            }:
                files[path] = source.read(path)
    for relative in EXPLICIT:
        files[relative] = archive_api.checked_bytes(ROOT / relative)
    files["e2-v3/public/manifest.json"] = (preparation / "public" / "manifest.json").read_bytes()
    for packet in manifest["packets"]:
        for row in packet["files"]:
            relative = f"packets/{packet['packet_id']}/{row['path']}"
            files[f"e2-v3/public/{relative}"] = archive_api.checked_bytes(preparation / "public" / relative)
    metadata = {
        "purpose": "E2 v3 development-gated diagnostic assistance candidate; independent of E1 and historical E2 results",
        "source_commit": base["source_commit"],
        "base_E1_archive_sha256": handoff["archive_sha256"],
        "public_manifest_sha256": receipt["public_manifest_sha256"],
        "preparation_sha256": archive_api.sha((preparation / "preparation.json").read_bytes()),
        "builder_sha256": archive_api.sha(Path(__file__).read_bytes()),
        "runtime_adapter_sha256": archive_api.sha((ROOT / "scripts/phase7_e2_diagnostic_v3_runtime.py").read_bytes()),
        "model_selection": "Qwen3.5-9B BF16; future owner action approval required",
        "development_gate": "four protocol-complete sessions required before any measured split",
        "automatic_retry_cap": 0,
        "excluded": [
            "private gold",
            "src/req2web_faults/evaluator_gold.py",
            "case-to-family mapping",
            "historical E2 raw results",
            "local native source outputs",
            "raw datasets",
            "credentials",
            "model weights",
            "E1 inputs and outputs",
        ],
        "development_sessions_executed": 0,
        "measured_sessions_executed": 0,
        "runtime_selected_or_approved": False,
    }
    return archive_api.write_archive(files, destination, metadata)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument(
        "--base-payload", type=Path,
        default=ROOT / "outputs/phase7_autodl_prep_v1/payload-final-20260914",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.preparation, args.base_payload, args.output), indent=2))
