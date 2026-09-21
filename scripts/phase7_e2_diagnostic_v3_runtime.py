"""Bounded Qwen runtime adapter for the Phase 7 E2 v3 protocol candidate.

The mature v2 execution machinery remains the owner of raw-first persistence,
model loading, split claims, deadlines, and no-retry behavior.  This adapter
installs only the independently versioned v3 public contract, prompt, budgets,
action schema, and freeze identity before delegating to that machinery.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import phase7_e2_diagnostic_runtime as legacy
import phase7_e2_diagnostic_v3_candidate as candidate


ACTION_SCHEMA = "req2web.phase7.e2_action.v3"
RESULT_SCHEMA = "req2web.phase7.e2_runtime_result.v3"
PROTOCOL_REVISION = "req2web.phase7.e2_turn_boundary.v3"
CONFIG_KEYS = {
    "schema_version", "approved", "model_root", "model_identity",
    "model_revision", "integrity_evidence", "public_root",
    "public_manifest_sha256", "output_root", "state_root", "split", "caps",
    "accepted_development",
}


def validate_public_root(public_root: Path, expected_manifest_sha256: str) -> dict[str, Any]:
    root = public_root.resolve(strict=True)
    manifest_path = legacy._regular_contained(root, "manifest.json")
    raw = manifest_path.read_bytes()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256) or legacy.sha256_bytes(raw) != expected_manifest_sha256:
        raise legacy.RuntimeErrorClosed("v3 public manifest SHA-256 mismatch")
    manifest = candidate.validate_public(root, expected_manifest_sha256)
    if manifest.get("schema_version") != candidate.PUBLIC_SCHEMA or manifest.get("budgets") != candidate.BUDGETS:
        raise legacy.RuntimeErrorClosed("v3 public schema or budget mismatch")
    return manifest


def load_config(config_path: Path) -> dict[str, Any]:
    config = legacy._strict_json(config_path.resolve(strict=True).read_bytes())
    legacy._exact_keys(config, CONFIG_KEYS, "v3 action config")
    if config["schema_version"] != ACTION_SCHEMA or type(config["approved"]) is not bool:
        raise legacy.RuntimeErrorClosed("invalid v3 action schema/approval")
    for key in (
        "model_root", "model_identity", "model_revision", "integrity_evidence",
        "public_root", "public_manifest_sha256", "output_root", "state_root",
        "split",
    ):
        if type(config[key]) is not str or not config[key]:
            raise legacy.RuntimeErrorClosed(f"{key} must be a non-empty string")
    if config["split"] not in {"development", "measured"}:
        raise legacy.RuntimeErrorClosed("split must be development or measured")
    if config["model_identity"] != legacy.QWEN_MODEL_ID or config["model_revision"] != legacy.QWEN_MODEL_REVISION:
        raise legacy.RuntimeErrorClosed("v3 action must bind the fixed Qwen3.5-9B identity and revision")
    expected_state = Path(config["public_root"]).resolve().parent / "runtime_state_v3"
    if Path(config["state_root"]).resolve() != expected_state:
        raise legacy.RuntimeErrorClosed("v3 state_root must be public_root.parent/runtime_state_v3")
    legacy._validate_caps(config["caps"])
    accepted = config["accepted_development"]
    if accepted is not None:
        legacy._exact_keys(accepted, {"accepted", "freeze_identity", "summary_sha256"}, "accepted development")
        if accepted["accepted"] is not True or not all(
            type(accepted[key]) is str and re.fullmatch(r"[0-9a-f]{64}", accepted[key])
            for key in ("freeze_identity", "summary_sha256")
        ):
            raise legacy.RuntimeErrorClosed("invalid accepted-development binding")
    if config["split"] == "measured" and accepted is None:
        raise legacy.RuntimeErrorClosed("measured split requires explicit accepted-development binding")
    return config


def freeze_identity(config: Mapping[str, Any], manifest_sha: str) -> str:
    integrity_sha = legacy.sha256_bytes(Path(config["integrity_evidence"]).resolve(strict=True).read_bytes())
    material = {
        "runtime_adapter_sha256": legacy.sha256_bytes(Path(__file__).read_bytes()),
        "legacy_runtime_sha256": legacy.sha256_bytes(Path(legacy.__file__).read_bytes()),
        "candidate_contract_sha256": legacy.sha256_bytes(Path(candidate.__file__).read_bytes()),
        "prompt_sha256": legacy.sha256_bytes(candidate.COMMON_PROMPT.encode("utf-8")),
        "model_identity": config["model_identity"],
        "model_revision": config["model_revision"],
        "model_integrity_evidence_sha256": integrity_sha,
        "budgets": config["caps"],
        "public_manifest_sha256": manifest_sha,
    }
    return legacy.sha256_bytes(legacy.canonical_bytes(material))


def validate_final(answer: Any) -> dict[str, Any]:
    action = candidate.validate_action(
        {"action": "final", "answer": answer},
        inventory=set(), arm="A", reads_used=0, turn=1,
    )
    return dict(action["answer"])


def resolve_pointer(value: Any, location: str) -> Any:
    if isinstance(value, dict) and value.get("kind") == "experiment_only_source_occurrence_index_v3":
        if not location.startswith("/entities/"):
            raise legacy.RuntimeErrorClosed("v3 trace index requires an exact /entities/<ID> lookup")
    try:
        return candidate.pointer(value, location)
    except candidate.CandidateError as exc:
        raise legacy.RuntimeErrorClosed(str(exc)) from exc


def install_contract() -> None:
    """Install v3 globals into the reused bounded runtime module."""
    legacy.PUBLIC_SCHEMA = candidate.PUBLIC_SCHEMA
    legacy.RESULT_SCHEMA = RESULT_SCHEMA
    legacy.EXPECTED_BUDGETS = dict(candidate.BUDGETS)
    legacy.COMMON_PROMPT = candidate.COMMON_PROMPT
    legacy.PROTOCOL_REVISION = PROTOCOL_REVISION
    legacy.validate_public_root = validate_public_root
    legacy._load_config = load_config
    legacy._freeze_identity = freeze_identity
    legacy._validate_final = validate_final
    legacy._pointer = resolve_pointer


def main(argv: list[str] | None = None) -> int:
    install_contract()
    return legacy.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
