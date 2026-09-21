"""One-shot recovery adapter for the interrupted Phase 7 E2 v3 launch.

This adapter does not change the v3 public packets, prompt, budgets, model,
result schema, scoring contract, or no-retry policy.  It authorizes one new
claim namespace only after proving that the preceding development claim was
consumed before any model session started because the worker interpreter did
not provide Transformers.  It also validates the exact Python environment,
CUDA device, and package versions before the recovery claim can be created.
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
import phase7_e2_diagnostic_v3_runtime as v3


ACTION_SCHEMA = "req2web.phase7.e2_action.v3.recovery1"
RESULT_SCHEMA = v3.RESULT_SCHEMA
SUPERVISION_SCHEMA = "req2web.phase7.e2_supervision.v3.recovery1"
PROTOCOL_REVISION = v3.PROTOCOL_REVISION
EXPECTED_TORCH_VERSION = "2.7.1+cu128"
EXPECTED_TRANSFORMERS_VERSION = "5.14.1"
EXPECTED_PRIOR_ERROR = "ModuleNotFoundError: No module named 'transformers'"
CONFIG_KEYS = v3.CONFIG_KEYS | {"runtime_environment", "prior_failed_attempt"}
RUNTIME_ENVIRONMENT_KEYS = {
    "python_executable",
    "torch_version",
    "transformers_version",
    "cuda_required",
    "gpu_name_substring",
}
PRIOR_FAILED_ATTEMPT_KEYS = {
    "action_config",
    "claim",
    "interrupted_summary",
    "worker_log",
}
RUNTIME_CAP_KEYS = {
    "reads",
    "input_tokens",
    "output_tokens",
    "provider_turns",
    "session_seconds",
}


def _regular_file(value: Any, name: str) -> Path:
    if type(value) is not str or not value:
        raise legacy.RuntimeErrorClosed(f"{name} must be a non-empty path string")
    path = Path(value)
    if path.is_symlink():
        raise legacy.RuntimeErrorClosed(f"{name} must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise legacy.RuntimeErrorClosed(f"{name} is unavailable") from exc
    if not resolved.is_file():
        raise legacy.RuntimeErrorClosed(f"{name} must be a regular file")
    return resolved


def _validate_v3_caps(value: Any) -> dict[str, int]:
    if type(value) is not dict or set(value) != RUNTIME_CAP_KEYS:
        raise legacy.RuntimeErrorClosed("caps must have exact runtime budget keys")
    expected = {key: candidate.BUDGETS[key] for key in RUNTIME_CAP_KEYS}
    if value != expected:
        raise legacy.RuntimeErrorClosed("runtime caps must equal frozen v3 public budgets")
    return dict(value)


def _load_prior_action(path: Path) -> dict[str, Any]:
    config = legacy._strict_json(path.read_bytes())
    legacy._exact_keys(config, v3.CONFIG_KEYS, "prior v3 action config")
    if config["schema_version"] != v3.ACTION_SCHEMA or config["approved"] is not True:
        raise legacy.RuntimeErrorClosed("prior action must be an approved v3 action")
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
            raise legacy.RuntimeErrorClosed(f"prior action {key} is invalid")
    if config["split"] != "development" or config["accepted_development"] is not None:
        raise legacy.RuntimeErrorClosed("prior action must be the v3 development action")
    if (
        config["model_identity"] != legacy.QWEN_MODEL_ID
        or config["model_revision"] != legacy.QWEN_MODEL_REVISION
    ):
        raise legacy.RuntimeErrorClosed("prior action model identity is invalid")
    expected_state = Path(config["public_root"]).resolve().parent / "runtime_state_v3"
    if Path(config["state_root"]).resolve() != expected_state:
        raise legacy.RuntimeErrorClosed("prior action state root is invalid")
    _validate_v3_caps(config["caps"])
    return config


def load_config(config_path: Path) -> dict[str, Any]:
    config = legacy._strict_json(config_path.resolve(strict=True).read_bytes())
    legacy._exact_keys(config, CONFIG_KEYS, "v3 recovery action config")
    if config["schema_version"] != ACTION_SCHEMA or type(config["approved"]) is not bool:
        raise legacy.RuntimeErrorClosed("invalid v3 recovery action schema/approval")
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
            "v3 recovery action must bind the fixed Qwen3.5-9B identity and revision"
        )
    expected_state = Path(config["public_root"]).resolve().parent / "runtime_state_v3_recovery1"
    if Path(config["state_root"]).resolve() != expected_state:
        raise legacy.RuntimeErrorClosed(
            "v3 recovery state_root must be public_root.parent/runtime_state_v3_recovery1"
        )
    _validate_v3_caps(config["caps"])

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
        raise legacy.RuntimeErrorClosed("invalid fixed recovery runtime environment")

    prior = config["prior_failed_attempt"]
    legacy._exact_keys(prior, PRIOR_FAILED_ATTEMPT_KEYS, "prior failed attempt")
    for key in PRIOR_FAILED_ATTEMPT_KEYS:
        if type(prior[key]) is not str or not prior[key]:
            raise legacy.RuntimeErrorClosed(f"prior_failed_attempt.{key} must be a path")

    accepted = config["accepted_development"]
    if accepted is not None:
        legacy._exact_keys(
            accepted,
            {"accepted", "freeze_identity", "summary_sha256"},
            "accepted development",
        )
        if accepted["accepted"] is not True or not all(
            type(accepted[key]) is str and re.fullmatch(r"[0-9a-f]{64}", accepted[key])
            for key in ("freeze_identity", "summary_sha256")
        ):
            raise legacy.RuntimeErrorClosed("invalid accepted-development binding")
    if config["split"] == "measured" and accepted is None:
        raise legacy.RuntimeErrorClosed(
            "measured split requires explicit accepted-development binding"
        )
    return config


def validate_prior_failed_attempt(config: Mapping[str, Any]) -> dict[str, Any]:
    prior = config["prior_failed_attempt"]
    action_path = _regular_file(prior["action_config"], "prior action config")
    claim_path = _regular_file(prior["claim"], "prior claim")
    interrupted_path = _regular_file(
        prior["interrupted_summary"], "prior interrupted summary"
    )
    worker_log_path = _regular_file(prior["worker_log"], "prior worker log")

    prior_config = _load_prior_action(action_path)
    for key in (
        "model_identity",
        "model_revision",
        "public_manifest_sha256",
        "caps",
    ):
        if prior_config[key] != config[key]:
            raise legacy.RuntimeErrorClosed(f"prior action {key} does not match recovery")
    if legacy.sha256_bytes(
        Path(prior_config["integrity_evidence"]).resolve(strict=True).read_bytes()
    ) != legacy.sha256_bytes(
        Path(config["integrity_evidence"]).resolve(strict=True).read_bytes()
    ):
        raise legacy.RuntimeErrorClosed(
            "prior model integrity evidence does not match recovery"
        )
    v3.validate_public_root(
        Path(prior_config["public_root"]), prior_config["public_manifest_sha256"]
    )

    claim = legacy._strict_json(claim_path.read_bytes())
    legacy._exact_keys(
        claim,
        {"public_manifest_sha256", "freeze_identity", "split", "output_root"},
        "prior claim",
    )
    expected_prior_freeze = v3.freeze_identity(
        prior_config, prior_config["public_manifest_sha256"]
    )
    if claim != {
        "public_manifest_sha256": prior_config["public_manifest_sha256"],
        "freeze_identity": expected_prior_freeze,
        "split": "development",
        "output_root": str(Path(prior_config["output_root"]).resolve()),
    }:
        raise legacy.RuntimeErrorClosed("prior claim does not bind the failed v3 action")

    interrupted = legacy._strict_json(interrupted_path.read_bytes())
    legacy._exact_keys(
        interrupted,
        {
            "schema_version",
            "split",
            "public_manifest_sha256",
            "fatal",
            "sessions",
            "scope",
        },
        "prior interrupted summary",
    )
    if (
        interrupted["schema_version"] != v3.RESULT_SCHEMA
        or interrupted["split"] != "development"
        or interrupted["public_manifest_sha256"] != config["public_manifest_sha256"]
        or interrupted["fatal"] != "worker_exit_2_without_summary"
        or type(interrupted["sessions"]) is not list
        or len(interrupted["sessions"]) != candidate.BUDGETS["development_sessions"]
    ):
        raise legacy.RuntimeErrorClosed("prior interrupted summary is not recoverable")

    expected_schedule = candidate.validate_public(
        Path(config["public_root"]), config["public_manifest_sha256"]
    )["schedule"]["development"]
    for actual, scheduled in zip(interrupted["sessions"], expected_schedule, strict=True):
        legacy._exact_keys(
            actual,
            {
                "session_id",
                "packet_id",
                "arm",
                "status",
                "answer",
                "reads",
                "provider_turns",
                "input_tokens",
                "output_tokens",
                "raw_refs",
                "reason",
            },
            "prior interrupted session",
        )
        if (
            {key: actual[key] for key in ("session_id", "packet_id", "arm")}
            != scheduled
            or actual["status"] != "not_started_after_interruption"
            or actual["answer"] is not None
            or actual["reads"] is not None
            or actual["provider_turns"] != 0
            or actual["input_tokens"] != 0
            or actual["output_tokens"] is not None
            or actual["raw_refs"] != []
            or actual["reason"] != interrupted["fatal"]
        ):
            raise legacy.RuntimeErrorClosed(
                "prior interrupted summary contains started or ambiguous model work"
            )

    try:
        worker_log = worker_log_path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as exc:
        raise legacy.RuntimeErrorClosed("prior worker log is not UTF-8") from exc
    if worker_log != EXPECTED_PRIOR_ERROR:
        raise legacy.RuntimeErrorClosed("prior worker failure is not the approved cause")

    material = {
        "action_config_sha256": legacy.sha256_bytes(action_path.read_bytes()),
        "claim_sha256": legacy.sha256_bytes(claim_path.read_bytes()),
        "interrupted_summary_sha256": legacy.sha256_bytes(interrupted_path.read_bytes()),
        "worker_log_sha256": legacy.sha256_bytes(worker_log_path.read_bytes()),
        "prior_freeze_identity": expected_prior_freeze,
        "failure_reason": interrupted["fatal"],
        "worker_error": EXPECTED_PRIOR_ERROR,
        "model_sessions_started": 0,
    }
    return {
        "identity": legacy.sha256_bytes(legacy.canonical_bytes(material)),
        "material": material,
    }


def validate_runtime_environment(config: Mapping[str, Any]) -> dict[str, Any]:
    expected = config["runtime_environment"]
    expected_python = Path(expected["python_executable"]).resolve(strict=True)
    actual_python = Path(sys.executable).resolve(strict=True)
    if actual_python != expected_python:
        raise legacy.RuntimeErrorClosed(
            "worker interpreter does not match runtime_environment.python_executable"
        )
    try:
        import torch
        import transformers
    except ImportError as exc:
        raise legacy.RuntimeErrorClosed(
            f"recovery runtime dependency unavailable: {exc.name}"
        ) from exc
    if torch.__version__ != expected["torch_version"]:
        raise legacy.RuntimeErrorClosed("recovery torch version mismatch")
    if transformers.__version__ != expected["transformers_version"]:
        raise legacy.RuntimeErrorClosed("recovery Transformers version mismatch")
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise legacy.RuntimeErrorClosed("recovery requires exactly one available CUDA GPU")
    gpu_name = str(torch.cuda.get_device_name(0))
    if expected["gpu_name_substring"] not in gpu_name:
        raise legacy.RuntimeErrorClosed("recovery CUDA device does not match the approved GPU")
    return {
        "python_executable": str(actual_python),
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "torch_version": torch.__version__,
        "transformers_version": transformers.__version__,
        "cuda_available": True,
        "cuda_device_count": 1,
        "cuda_device_name": gpu_name,
    }


def freeze_identity(config: Mapping[str, Any], manifest_sha: str) -> str:
    prior = validate_prior_failed_attempt(config)
    integrity_sha = legacy.sha256_bytes(
        Path(config["integrity_evidence"]).resolve(strict=True).read_bytes()
    )
    material = {
        "recovery_runtime_sha256": legacy.sha256_bytes(Path(__file__).read_bytes()),
        "v3_runtime_adapter_sha256": legacy.sha256_bytes(Path(v3.__file__).read_bytes()),
        "legacy_runtime_sha256": legacy.sha256_bytes(Path(legacy.__file__).read_bytes()),
        "candidate_contract_sha256": legacy.sha256_bytes(Path(candidate.__file__).read_bytes()),
        "prompt_sha256": legacy.sha256_bytes(candidate.COMMON_PROMPT.encode("utf-8")),
        "model_identity": config["model_identity"],
        "model_revision": config["model_revision"],
        "model_integrity_evidence_sha256": integrity_sha,
        "runtime_environment": config["runtime_environment"],
        "prior_failed_attempt_identity": prior["identity"],
        "budgets": config["caps"],
        "public_manifest_sha256": manifest_sha,
    }
    return legacy.sha256_bytes(legacy.canonical_bytes(material))


BASE_PREFLIGHT = legacy.preflight_run


def preflight_run(config_path: Path) -> dict[str, Any]:
    result = BASE_PREFLIGHT(config_path)
    config = load_config(config_path)
    result["runtime_environment"] = validate_runtime_environment(config)
    result["prior_failed_attempt_identity"] = validate_prior_failed_attempt(config)[
        "identity"
    ]
    result["recovery_claim_namespace"] = "runtime_state_v3_recovery1"
    return result


def install_contract() -> None:
    """Install the unchanged v3 protocol plus the recovery-only controls."""
    v3.install_contract()
    legacy.RESULT_SCHEMA = RESULT_SCHEMA
    legacy.PROTOCOL_REVISION = PROTOCOL_REVISION
    legacy._load_config = load_config
    legacy._freeze_identity = freeze_identity
    legacy.preflight_run = preflight_run


def main(argv: list[str] | None = None) -> int:
    install_contract()
    return legacy.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
