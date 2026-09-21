"""Bounded runtime adapter for the local-only Phase 7 E2 v5 protocol.

The v4 returned evidence is immutable.  V5 is a new one-shot namespace that
preserves the public packets, model, budgets, raw-first persistence, and zero
retry while making the B treatment mandatory and using parallel final edge
endpoints.  No action occurs without an exact approved action-time config.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import phase7_e2_diagnostic_runtime as legacy
import phase7_e2_diagnostic_v3_candidate as candidate
import phase7_e2_diagnostic_v3_runtime as v3
import phase7_e2_diagnostic_v4_runtime as v4
import phase7_e2_diagnostic_v5_protocol as protocol


ACTION_SCHEMA = "req2web.phase7.e2_action.v5"
RESULT_SCHEMA = "req2web.phase7.e2_runtime_result.v5"
SUPERVISION_SCHEMA = "req2web.phase7.e2_supervision.v5"
PROTOCOL_REVISION = protocol.PROTOCOL_REVISION
EXPECTED_TORCH_VERSION = v4.EXPECTED_TORCH_VERSION
EXPECTED_TRANSFORMERS_VERSION = v4.EXPECTED_TRANSFORMERS_VERSION
CONFIG_KEYS = set(v4.CONFIG_KEYS)
RUNTIME_ENVIRONMENT_KEYS = set(v4.RUNTIME_ENVIRONMENT_KEYS)
ACCEPTED_DEVELOPMENT_KEYS = set(v4.ACCEPTED_DEVELOPMENT_KEYS)
RUNTIME_CAP_KEYS = set(v4.RUNTIME_CAP_KEYS)


COMMON_PROMPT = (
    "You are an isolated experiment diagnostic agent. Diagnose only from the "
    "listed JSON artifacts. The hard limits are five reads, six provider turns, "
    "12000 cumulative input tokens, 2048 output tokens per reply, and 120 seconds "
    "excluding model load. First read readme.json. Reply with exactly one JSON "
    "object and no Markdown. Raw read: {\"action\":\"read\",\"artifact\":"
    "\"name.json\",\"pointer\":\"\"}. Never read trace_index.json directly. "
    "If trace_index.json is listed, you are in the trace-assisted condition: "
    "exactly one successful trace_lookup is mandatory before final. Immediately "
    "after the first raw tool response containing an exact index-backed entity "
    "ID, your next action must be {\"action\":\"trace_lookup\",\"entity_id\":"
    "\"THE-EXACT-ID-YOU-OBSERVED\"}. You choose the ID; the host supplies no "
    "verdict and only constructs its exact occurrence pointer. Do not make "
    "another raw read first, and never request a second lookup. If "
    "trace_index.json is not listed, trace_lookup is forbidden. Final action: "
    "{\"action\":\"final\",\"status\":\"fault\",\"origins\":[\"artifact.json"
    "::/path/to/origin\"],\"use_cases\":[\"EXACT-USE-CASE-ID\"],\"edge_from\":["
    "\"artifact.json::/path/to/source\"],\"edge_to\":[\"other.json"
    "::/path/to/target\"],\"uncertainty\":\"concise\"}. The artifact names, "
    "IDs, and pointers in this syntax illustration are placeholders, not packet "
    "evidence or an answer. edge_from "
    "and edge_to must have equal lengths; pair items at the same array position. "
    "Every endpoint is artifact.json::/nonempty/RFC6901/pointer, never a scalar "
    "value. Status is exactly fault, no_fault, unknown, or unsupported_input. "
    "Report the smallest set of independent first faulty locations and only the "
    "evidence pairs needed to support them. A no-progress sequence originates at "
    "its first advance event whose after_state equals before_state. Final "
    "evidence must cite raw artifacts, never the trace index. The host executes "
    "actions; never invent a tool response. On the last available turn or after "
    "five reads, return final."
)


def _validate_caps(value: Any) -> dict[str, int]:
    return v4._validate_caps(value)


def _regular_file(value: Any, name: str) -> Path:
    return v4._regular_file(value, name)


def _validate_accepted_development(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    legacy._exact_keys(value, ACCEPTED_DEVELOPMENT_KEYS, "accepted development")
    if value["accepted"] is not True:
        raise legacy.RuntimeErrorClosed("accepted development must be explicitly true")
    for key in ("freeze_identity", "summary_sha256", "assessment_sha256"):
        if type(value[key]) is not str or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
            raise legacy.RuntimeErrorClosed(f"accepted development {key} is invalid")
    assessment_path = _regular_file(value["assessment_path"], "development assessment")
    raw = assessment_path.read_bytes()
    if legacy.sha256_bytes(raw) != value["assessment_sha256"]:
        raise legacy.RuntimeErrorClosed("development assessment SHA-256 mismatch")
    assessment = legacy._strict_json(raw)
    if (
        assessment.get("schema_version")
        != "req2web.phase7.e2.trace_navigation_assessment.v5"
        or assessment.get("split") != "development"
        or assessment.get("development_gate_passed") is not True
    ):
        raise legacy.RuntimeErrorClosed("development assessment does not pass the v5 gate")
    sources = assessment.get("sources")
    if (
        type(sources) is not dict
        or sources.get("runtime_result_sha256")
        != f"sha256:{value['summary_sha256']}"
    ):
        raise legacy.RuntimeErrorClosed(
            "development assessment does not bind the accepted summary"
        )
    return dict(value)


def load_config(config_path: Path) -> dict[str, Any]:
    config = legacy._strict_json(config_path.resolve(strict=True).read_bytes())
    legacy._exact_keys(config, CONFIG_KEYS, "v5 action config")
    if config["schema_version"] != ACTION_SCHEMA or type(config["approved"]) is not bool:
        raise legacy.RuntimeErrorClosed("invalid v5 action schema/approval")
    for key in (
        "model_root",
        "model_identity",
        "model_revision",
        "integrity_evidence",
        "public_root",
        "public_manifest_sha256",
        "output_root",
        "state_root",
        "split",
    ):
        if type(config[key]) is not str or not config[key]:
            raise legacy.RuntimeErrorClosed(f"{key} must be a non-empty string")
    if config["split"] not in {"development", "measured"}:
        raise legacy.RuntimeErrorClosed("split must be development or measured")
    if (
        config["model_identity"] != legacy.QWEN_MODEL_ID
        or config["model_revision"] != legacy.QWEN_MODEL_REVISION
    ):
        raise legacy.RuntimeErrorClosed(
            "v5 action must bind the fixed Qwen3.5-9B identity and revision"
        )
    expected_state = Path(config["public_root"]).resolve().parent / "runtime_state_v5"
    if Path(config["state_root"]).resolve() != expected_state:
        raise legacy.RuntimeErrorClosed(
            "v5 state_root must be public_root.parent/runtime_state_v5"
        )
    _validate_caps(config["caps"])

    environment = config["runtime_environment"]
    legacy._exact_keys(environment, RUNTIME_ENVIRONMENT_KEYS, "runtime environment")
    if (
        type(environment["python_executable"]) is not str
        or not environment["python_executable"]
        or environment["torch_version"] != EXPECTED_TORCH_VERSION
        or environment["transformers_version"] != EXPECTED_TRANSFORMERS_VERSION
        or environment["cuda_required"] is not True
        or type(environment["gpu_name_substring"]) is not str
        or not environment["gpu_name_substring"]
    ):
        raise legacy.RuntimeErrorClosed("invalid fixed v5 runtime environment")

    accepted = _validate_accepted_development(config["accepted_development"])
    if config["split"] == "development" and accepted is not None:
        raise legacy.RuntimeErrorClosed(
            "development split cannot carry an accepted development"
        )
    if config["split"] == "measured" and accepted is None:
        raise legacy.RuntimeErrorClosed(
            "measured split requires a passing v5 development assessment"
        )
    return config


def validate_runtime_environment(config: Mapping[str, Any]) -> dict[str, Any]:
    return v4.validate_runtime_environment(config)


def freeze_identity(config: Mapping[str, Any], manifest_sha: str) -> str:
    integrity_sha = legacy.sha256_bytes(
        Path(config["integrity_evidence"]).resolve(strict=True).read_bytes()
    )
    material = {
        "v5_runtime_sha256": legacy.sha256_bytes(Path(__file__).read_bytes()),
        "v5_protocol_sha256": legacy.sha256_bytes(Path(protocol.__file__).read_bytes()),
        "v5_contract_sha256": legacy.sha256_bytes(
            (ROOT / "fixtures/phase7_e2_diagnostic_v5_protocol.json").read_bytes()
        ),
        "v4_runtime_base_sha256": legacy.sha256_bytes(Path(v4.__file__).read_bytes()),
        "legacy_runtime_sha256": legacy.sha256_bytes(Path(legacy.__file__).read_bytes()),
        "v3_candidate_sha256": legacy.sha256_bytes(Path(candidate.__file__).read_bytes()),
        "prompt_sha256": legacy.sha256_bytes(COMMON_PROMPT.encode("utf-8")),
        "model_identity": config["model_identity"],
        "model_revision": config["model_revision"],
        "model_integrity_evidence_sha256": integrity_sha,
        "runtime_environment": config["runtime_environment"],
        "budgets": config["caps"],
        "public_manifest_sha256": manifest_sha,
    }
    return legacy.sha256_bytes(legacy.canonical_bytes(material))


BASE_PREFLIGHT = v4.BASE_PREFLIGHT


def preflight_run(config_path: Path) -> dict[str, Any]:
    result = BASE_PREFLIGHT(config_path)
    config = load_config(config_path)
    result["runtime_environment"] = validate_runtime_environment(config)
    result["claim_namespace"] = "runtime_state_v5"
    result["protocol_schema"] = protocol.PROTOCOL_SCHEMA
    return result


def _run_session_v5(**kwargs: Any) -> dict[str, Any]:
    """Use the reviewed v4 raw-first loop with versioned v5 authorities.

    The adapter is process-local and sequential.  It restores the imported v4
    module immediately after each session so tests or replay callers cannot
    accidentally reinterpret a v4 session under v5 semantics.
    """
    previous_protocol = v4.protocol
    previous_prompt = v4.COMMON_PROMPT
    v4.protocol = protocol
    v4.COMMON_PROMPT = COMMON_PROMPT
    try:
        return v4._run_session_v4(**kwargs)
    finally:
        v4.protocol = previous_protocol
        v4.COMMON_PROMPT = previous_prompt


def install_contract() -> None:
    v4.install_contract()
    legacy.RESULT_SCHEMA = RESULT_SCHEMA
    legacy.COMMON_PROMPT = COMMON_PROMPT
    legacy.PROTOCOL_REVISION = PROTOCOL_REVISION
    legacy._load_config = load_config
    legacy._freeze_identity = freeze_identity
    legacy.preflight_run = preflight_run
    legacy._run_session = _run_session_v5


def main(argv: list[str] | None = None) -> int:
    install_contract()
    return legacy.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
