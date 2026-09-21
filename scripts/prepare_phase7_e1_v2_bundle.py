"""Build the E1 v2 server payload without the local browser observer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_phase7_autodl_bundle as archive_api
import phase7_e1_v2_runtime as runtime


BASE_PREFIXES = (
    "src/",
    "data/processed/rag/",
)
BASE_EXACT = {
    "docs/phase4_langgraph_dependency_acquisition_receipt.json",
    "requirements-phase4-agent-lock.txt",
    "requirements-phase4-agent.txt",
    "runtime/model_integrity.json",
    "runtime/requirements-model.txt",
    "runtime/runtime_profile.json",
}
EXCLUDED_SOURCE_PREFIXES = (
    "src/req2web_faults/evaluator_gold.py",
)
OVERLAY = (
    "scripts/phase7_direct_html_worker.py",
    "scripts/phase7_e1_v2_cases.py",
    "scripts/phase7_e1_v2_runtime.py",
    "scripts/phase7_e1_v2_structured.py",
    "src/req2web_capabilities.py",
    "src/req2web_agent/prompt_authority.py",
    "src/req2web_agent/understanding.py",
    "src/req2web_generation/consistency.py",
    "src/req2web_generation/renderer.py",
    "src/req2web_orchestration/phase4_graph.py",
)
PREPARATION_PREFIX = "e1-v2"
PURPOSE = "Phase 7 E1 v2 bounded three-arm workflow-prototype comparison"


class BundleError(ValueError):
    pass


def _allowed_base(path: str) -> bool:
    return (
        (path in BASE_EXACT or any(path.startswith(prefix) for prefix in BASE_PREFIXES))
        and not any(path.startswith(prefix) for prefix in EXCLUDED_SOURCE_PREFIXES)
    )


def build(preparation: Path, base_payload: Path, destination: Path) -> dict[str, object]:
    """Write one allowlisted payload rooted in the current working bytes."""
    runtime.validate(preparation)
    handoff = json.loads((base_payload / "handoff_manifest.json").read_bytes())
    archive = base_payload / handoff["archive_filename"]
    archive_api.validate_archive(archive, handoff["archive_sha256"])
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(archive) as source:
        base_manifest = json.loads(source.read("payload_manifest.json"))
        for row in base_manifest["files"]:
            path = row["path"]
            if _allowed_base(path):
                files[path] = source.read(path)

    for relative in OVERLAY:
        files[relative] = archive_api.checked_bytes(ROOT / relative)
    files["scripts/prepare_phase7_autodl_bundle.py"] = archive_api.checked_bytes(
        ROOT / "scripts/prepare_phase7_autodl_bundle.py"
    )

    freeze = json.loads((preparation / "freeze.json").read_bytes())
    files[f"{PREPARATION_PREFIX}/preparation/freeze.json"] = (preparation / "freeze.json").read_bytes()
    files[f"{PREPARATION_PREFIX}/preparation/summary.json"] = (preparation / "summary.json").read_bytes()
    for row in freeze["prompts"]:
        source_path = preparation / row["path"]
        files[f"{PREPARATION_PREFIX}/preparation/{row['path']}"] = archive_api.checked_bytes(source_path)

    forbidden = [
        path for path in files
        if "observer" in path.casefold()
        or "obligation" in path.casefold()
        or "evaluator_gold" in path.casefold()
        or path.startswith("tests/")
    ]
    if forbidden:
        raise BundleError(f"private evaluator material entered the payload: {forbidden}")
    for relative, expected in freeze["sources"].items():
        raw = files.get(relative)
        if raw is None or archive_api.sha(raw) != expected.removeprefix("sha256:"):
            raise BundleError(f"frozen runtime source is missing or drifted: {relative}")

    metadata = {
        "purpose": PURPOSE,
        "source_commit": base_manifest["source_commit"],
        "source_kind": "allowlisted_base_plus_explicit_current_working_byte_overlays",
        "base_archive_sha256": handoff["archive_sha256"],
        "freeze_sha256": archive_api.sha((preparation / "freeze.json").read_bytes()),
        "input_identity": freeze["input_identity"],
        "observer_identity": freeze["observer_identity"],
        "model": freeze["model"],
        "limits": freeze["limits"],
        "development_gate": "required before measured generation",
        "automatic_retry_count": 0,
        "excluded": [
            "local Playwright observer and evaluator criteria",
            "tests and generated browser verdicts",
            "historical E1 and E2 results",
            "raw datasets and reference-only images",
            "credentials and model weights",
            "evaluator-only gold material",
        ],
        "model_loaded": False,
        "run_occurred": False,
        "remote_action_occurred": False,
    }
    return archive_api.write_archive(files, destination, metadata)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument(
        "--base-payload", type=Path,
        default=ROOT / "outputs/phase7_autodl_prep_v1/payload-corrected-v2-20260916",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.preparation, args.base_payload, args.output), indent=2))
