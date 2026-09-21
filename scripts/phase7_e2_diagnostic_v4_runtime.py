"""Bounded runtime adapter for the local-only Phase 7 E2 v4 protocol.

The public v3 packets, model revision, budgets, raw-first persistence, strict
semantic scorer, and zero-retry behavior are preserved.  V4 changes only the
model wire protocol: B uses a grounded trace_lookup action, and both arms use
the same shallow final action.  This file performs no action without an exact
approved action-time configuration.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import phase7_e2_diagnostic_runtime as legacy
import phase7_e2_diagnostic_v3_candidate as candidate
import phase7_e2_diagnostic_v3_runtime as v3
import phase7_e2_diagnostic_v4_protocol as protocol


ACTION_SCHEMA = "req2web.phase7.e2_action.v4"
RESULT_SCHEMA = "req2web.phase7.e2_runtime_result.v4"
SUPERVISION_SCHEMA = "req2web.phase7.e2_supervision.v4"
PROTOCOL_REVISION = protocol.PROTOCOL_REVISION
EXPECTED_TORCH_VERSION = "2.7.1+cu128"
EXPECTED_TRANSFORMERS_VERSION = "5.14.1"
CONFIG_KEYS = v3.CONFIG_KEYS | {"runtime_environment"}
RUNTIME_ENVIRONMENT_KEYS = {
    "python_executable",
    "torch_version",
    "transformers_version",
    "cuda_required",
    "gpu_name_substring",
}
ACCEPTED_DEVELOPMENT_KEYS = {
    "accepted",
    "freeze_identity",
    "summary_sha256",
    "assessment_path",
    "assessment_sha256",
}
RUNTIME_CAP_KEYS = {
    "reads",
    "input_tokens",
    "output_tokens",
    "provider_turns",
    "session_seconds",
}

COMMON_PROMPT = (
    "You are an isolated experiment diagnostic agent. Diagnose only from the "
    "listed JSON artifacts. The hard limits are five reads, six provider turns, "
    "12000 cumulative input tokens, 2048 output tokens per reply, and 120 seconds "
    "excluding model load. First read readme.json. Reply with exactly one JSON "
    "object and no Markdown. Raw read: {\"action\":\"read\",\"artifact\":"
    "\"name.json\",\"pointer\":\"\"}. Never read trace_index.json directly. "
    "When trace_index.json is listed and an exact entity ID has already appeared "
    "in a raw tool response, request one lookup as {\"action\":\"trace_lookup\","
    "\"entity_id\":\"EXACT-ID\"}; the host constructs the exact pointer. The index "
    "is a verdict-free navigation aid. Final action: {\"action\":\"final\","
    "\"status\":\"fault\",\"origins\":[\"handoff.json::/links/0/use_case_id\"],"
    "\"use_cases\":[\"UC-01\"],\"edges\":[\"handoff.json::/links/0/use_case_id"
    "=>specification.json::/sections/0/use_case_ids\"],\"uncertainty\":\"concise\"}. "
    "Status is exactly fault, no_fault, unknown, or unsupported_input. Report the "
    "smallest set of independent first faulty locations and only the evidence "
    "edges needed to support them. A no-progress sequence originates at its first "
    "advance event whose after_state equals before_state. Final evidence must cite "
    "raw artifacts, never the trace index. The host executes actions; never invent "
    "a tool response. On the last available turn or after five reads, return final."
)


def _validate_caps(value: Any) -> dict[str, int]:
    if type(value) is not dict or set(value) != RUNTIME_CAP_KEYS:
        raise legacy.RuntimeErrorClosed("caps must have exact runtime budget keys")
    expected = {key: candidate.BUDGETS[key] for key in RUNTIME_CAP_KEYS}
    if value != expected:
        raise legacy.RuntimeErrorClosed("runtime caps must equal frozen v3 public budgets")
    return dict(value)


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
        assessment.get("schema_version") != "req2web.phase7.e2.trace_navigation_assessment.v4"
        or assessment.get("split") != "development"
        or assessment.get("development_gate_passed") is not True
    ):
        raise legacy.RuntimeErrorClosed("development assessment does not pass the v4 gate")
    sources = assessment.get("sources")
    if (
        type(sources) is not dict
        or sources.get("runtime_result_sha256") != f"sha256:{value['summary_sha256']}"
    ):
        raise legacy.RuntimeErrorClosed("development assessment does not bind the accepted summary")
    return dict(value)


def load_config(config_path: Path) -> dict[str, Any]:
    config = legacy._strict_json(config_path.resolve(strict=True).read_bytes())
    legacy._exact_keys(config, CONFIG_KEYS, "v4 action config")
    if config["schema_version"] != ACTION_SCHEMA or type(config["approved"]) is not bool:
        raise legacy.RuntimeErrorClosed("invalid v4 action schema/approval")
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
        raise legacy.RuntimeErrorClosed("v4 action must bind the fixed Qwen3.5-9B identity and revision")
    expected_state = Path(config["public_root"]).resolve().parent / "runtime_state_v4"
    if Path(config["state_root"]).resolve() != expected_state:
        raise legacy.RuntimeErrorClosed("v4 state_root must be public_root.parent/runtime_state_v4")
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
        raise legacy.RuntimeErrorClosed("invalid fixed v4 runtime environment")

    accepted = _validate_accepted_development(config["accepted_development"])
    if config["split"] == "development" and accepted is not None:
        raise legacy.RuntimeErrorClosed("development split cannot carry an accepted development")
    if config["split"] == "measured" and accepted is None:
        raise legacy.RuntimeErrorClosed("measured split requires a passing v4 development assessment")
    return config


def validate_runtime_environment(config: Mapping[str, Any]) -> dict[str, Any]:
    expected = config["runtime_environment"]
    expected_python = Path(expected["python_executable"]).resolve(strict=True)
    actual_python = Path(sys.executable).resolve(strict=True)
    if actual_python != expected_python:
        raise legacy.RuntimeErrorClosed("worker interpreter does not match runtime_environment.python_executable")
    try:
        import torch
        import transformers
    except ImportError as exc:
        raise legacy.RuntimeErrorClosed(f"v4 runtime dependency unavailable: {exc.name}") from exc
    if torch.__version__ != expected["torch_version"]:
        raise legacy.RuntimeErrorClosed("v4 torch version mismatch")
    if transformers.__version__ != expected["transformers_version"]:
        raise legacy.RuntimeErrorClosed("v4 Transformers version mismatch")
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise legacy.RuntimeErrorClosed("v4 requires exactly one available CUDA GPU")
    gpu_name = str(torch.cuda.get_device_name(0))
    if expected["gpu_name_substring"] not in gpu_name:
        raise legacy.RuntimeErrorClosed("v4 CUDA device does not match the approved GPU")
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
    integrity_sha = legacy.sha256_bytes(
        Path(config["integrity_evidence"]).resolve(strict=True).read_bytes()
    )
    material = {
        "v4_runtime_sha256": legacy.sha256_bytes(Path(__file__).read_bytes()),
        "v4_protocol_sha256": legacy.sha256_bytes(Path(protocol.__file__).read_bytes()),
        "v4_contract_sha256": legacy.sha256_bytes(
            (ROOT / "fixtures/phase7_e2_diagnostic_v4_protocol.json").read_bytes()
        ),
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


BASE_PREFLIGHT = legacy.preflight_run


def preflight_run(config_path: Path) -> dict[str, Any]:
    result = BASE_PREFLIGHT(config_path)
    config = load_config(config_path)
    result["runtime_environment"] = validate_runtime_environment(config)
    result["claim_namespace"] = "runtime_state_v4"
    result["protocol_schema"] = protocol.PROTOCOL_SCHEMA
    return result


def _run_session_v4(
    *,
    row: Mapping[str, Any],
    packet: Mapping[str, Any],
    root: Path,
    out: Path,
    backend: legacy.Backend,
    caps: Mapping[str, int],
    freeze: str,
    active_path: Path,
) -> dict[str, Any]:
    sid, arm = row["session_id"], row["arm"]
    files = legacy._packet_files(root, packet)
    inventory = sorted(files) if arm == "B" else sorted(name for name in files if name != protocol.TRACE_INDEX_ARTIFACT)
    if "readme.json" not in inventory:
        raise legacy.RuntimeErrorClosed("every packet must provide readme.json")
    trace_index: dict[str, Any] = {"kind": protocol.TRACE_INDEX_KIND, "entities": {}}
    if arm == "B":
        if protocol.TRACE_INDEX_ARTIFACT not in files:
            raise legacy.RuntimeErrorClosed("arm B packet is missing trace_index.json")
        index_path, index_sha = files[protocol.TRACE_INDEX_ARTIFACT]
        index_raw = index_path.read_bytes()
        if legacy.sha256_bytes(index_raw) != index_sha:
            raise legacy.RuntimeErrorClosed("trace index drifted before the session")
        trace_index = legacy._strict_json(index_raw)
        protocol.grounded_entities({}, trace_index)

    messages = [
        {"role": "system", "content": COMMON_PROMPT},
        {
            "role": "user",
            "content": legacy.canonical_bytes(
                {"packet_id": row["packet_id"], "artifacts": inventory}
            ).decode(),
        },
    ]
    started_at = time.monotonic()
    deadline = started_at + caps["session_seconds"]
    reads = turns = input_tokens = 0
    output_tokens: int | None = 0
    raw_refs: list[dict[str, Any]] = []
    observed_entities: set[str] = set()
    answer = None
    status = "invalid_response"
    fatal_error: str | None = None
    protocol_error: str | None = None

    for turn in range(1, caps["provider_turns"] + 1):
        if time.monotonic() >= deadline:
            status = "timeout"
            break
        raw_path = out / sid / f"turn_{turn:02d}.raw"
        started_path = out / sid / f"turn_{turn:02d}.started.json"
        stream_path = out / sid / f"turn_{turn:02d}.partial"

        def call_started(meta: Mapping[str, Any]) -> None:
            nonlocal turns, input_tokens
            actual_input = meta.get("input_tokens")
            if type(actual_input) is not int or isinstance(actual_input, bool) or actual_input < 0:
                raise legacy.RuntimeErrorClosed("started metadata requires actual input_tokens")
            formatted_prompt = meta.get("formatted_prompt")
            if type(formatted_prompt) is not str:
                raise legacy.RuntimeErrorClosed("started metadata requires the exact formatted model prompt")
            turns += 1
            input_tokens += actual_input
            prompt_path = out / sid / f"turn_{turn:02d}.prompt.txt"
            legacy._write_exclusive(prompt_path, formatted_prompt.encode("utf-8"))
            public_meta = {key: value for key, value in meta.items() if key != "formatted_prompt"}
            legacy._write_exclusive(
                started_path,
                legacy.canonical_bytes(
                    {
                        "session_id": sid,
                        "turn": turn,
                        "freeze_identity": freeze,
                        "event": "generation_started",
                        "automatic_retry_count": 0,
                        "input_tokens": actual_input,
                        "formatted_prompt_sha256": legacy.sha256_bytes(formatted_prompt.encode("utf-8")),
                        "backend": public_meta,
                    }
                ),
            )

        def chunk(raw: bytes) -> None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("ab") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())

        try:
            legacy._write_active(
                active_path,
                {
                    "phase": "session",
                    "session_id": sid,
                    "started_monotonic": started_at,
                    "deadline_unix": time.time() + max(0.0, deadline - time.monotonic()),
                },
            )
            result = backend.generate(
                messages,
                max_new_tokens=caps["output_tokens"],
                remaining_input_tokens=caps["input_tokens"] - input_tokens,
                deadline=deadline,
                started=call_started,
                chunk=chunk,
            )
            raw = result["raw"]
            if type(raw) is str:
                raw = raw.encode("utf-8")
            if type(raw) is not bytes:
                raise legacy.RuntimeErrorClosed("backend raw must be bytes or string")
            legacy._write_exclusive(raw_path, raw)
            if "generation_evidence" in result:
                legacy._write_exclusive(
                    out / sid / f"turn_{turn:02d}.generation.json",
                    legacy.canonical_bytes(result["generation_evidence"]),
                )
            turn_in, turn_out = result["input_tokens"], result["output_tokens"]
            if (
                type(turn_in) is not int
                or type(turn_out) is not int
                or isinstance(turn_in, bool)
                or isinstance(turn_out, bool)
                or turn_in < 0
                or turn_out < 0
            ):
                raise legacy.RuntimeErrorClosed("backend token counts must be nonnegative integers")
            if turns == 0 or turn_in != int(legacy._strict_json(started_path.read_bytes())["input_tokens"]):
                raise legacy.RuntimeErrorClosed("backend input token accounting drifted from started ledger")
            output_tokens += turn_out
            raw_refs.append(
                {
                    "turn": turn,
                    "path": str(raw_path.relative_to(out)).replace("\\", "/"),
                    "sha256": legacy.sha256_bytes(raw),
                    "bytes": len(raw),
                }
            )
            if input_tokens > caps["input_tokens"] or turn_out > caps["output_tokens"]:
                status = "budget_exceeded"
                break
            if time.monotonic() > deadline:
                status = "timeout"
                break

            model_action = protocol.parse_model_object(raw)
            parsed = protocol.validate_action(
                model_action,
                arm=arm,
                inventory=inventory,
                observed_entities=observed_entities,
                trace_index=trace_index,
                reads_used=reads,
                turn=turn,
            )
            if parsed["kind"] == "final":
                answer = parsed["answer"]
                status = answer["status"]
                break

            request = parsed["read"]
            reads += 1
            if reads > caps["reads"]:
                status = "read_budget_exceeded"
                break
            path, digest = files[request["artifact"]]
            source_raw = path.read_bytes()
            if legacy.sha256_bytes(source_raw) != digest:
                raise legacy.RuntimeErrorClosed("artifact drifted at tool-read time")
            value = legacy._pointer(legacy._strict_json(source_raw), request["pointer"])
            if parsed["kind"] == "raw_read" and arm == "B":
                observed_entities.update(protocol.grounded_entities(value, trace_index))
            tool_response = {
                "artifact": request["artifact"],
                "sha256": digest,
                "pointer": request["pointer"],
                "value": value,
            }
            receipt: dict[str, Any] = {
                "request": request,
                "response": tool_response,
                "source_bytes": len(source_raw),
                "source_sha256": legacy.sha256_bytes(source_raw),
            }
            if parsed["kind"] == "trace_lookup":
                receipt["model_action"] = parsed["model_action"]
            legacy._write_exclusive(
                out / sid / f"turn_{turn:02d}.tool_read.json",
                legacy.canonical_bytes(receipt),
            )
            control = {
                "remaining_reads": caps["reads"] - reads,
                "remaining_provider_turns": caps["provider_turns"] - turn,
                "remaining_cumulative_input_tokens": caps["input_tokens"] - input_tokens,
                "final_required": reads >= caps["reads"] or turn >= caps["provider_turns"] - 1,
            }
            messages.extend(
                [
                    {"role": "assistant", "content": raw.decode("utf-8")},
                    {
                        "role": "user",
                        "content": legacy.canonical_bytes(
                            {**tool_response, "host_control": control}
                        ).decode("utf-8"),
                    },
                ]
            )
        except legacy.FatalBackendError as exc:
            if stream_path.exists():
                partial = stream_path.read_bytes()
                raw_refs.append(
                    {
                        "turn": turn,
                        "path": str(stream_path.relative_to(out)).replace("\\", "/"),
                        "sha256": legacy.sha256_bytes(partial),
                        "bytes": len(partial),
                        "partial": True,
                    }
                )
                output_tokens = None
            status = "fatal_backend_error"
            fatal_error = f"{type(exc).__name__}: {exc}"
            break
        except TimeoutError:
            if stream_path.exists():
                partial = stream_path.read_bytes()
                raw_refs.append(
                    {
                        "turn": turn,
                        "path": str(stream_path.relative_to(out)).replace("\\", "/"),
                        "sha256": legacy.sha256_bytes(partial),
                        "bytes": len(partial),
                        "partial": True,
                    }
                )
                output_tokens = None
            status = "timeout"
            break
        except Exception as exc:
            protocol_error = f"{type(exc).__name__}: {exc}"
            if stream_path.exists():
                partial = stream_path.read_bytes()
                raw_refs.append(
                    {
                        "turn": turn,
                        "path": str(stream_path.relative_to(out)).replace("\\", "/"),
                        "sha256": legacy.sha256_bytes(partial),
                        "bytes": len(partial),
                        "partial": True,
                    }
                )
            status = (
                "invalid_response"
                if isinstance(exc, (legacy.RuntimeErrorClosed, protocol.ProtocolV4Error))
                else "backend_exception"
            )
            break

    legacy._write_active(
        active_path,
        {"phase": "idle", "session_id": None, "started_monotonic": None, "deadline_unix": None},
    )
    return {
        "session_id": sid,
        "packet_id": row["packet_id"],
        "arm": arm,
        "status": status,
        "answer": answer,
        "reads": reads,
        "provider_turns": turns,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "raw_refs": raw_refs,
        "elapsed_seconds": time.monotonic() - started_at,
        "fatal_error": fatal_error,
        "protocol_error": protocol_error,
    }


def install_contract() -> None:
    v3.install_contract()
    legacy.RESULT_SCHEMA = RESULT_SCHEMA
    legacy.COMMON_PROMPT = COMMON_PROMPT
    legacy.PROTOCOL_REVISION = PROTOCOL_REVISION
    legacy._load_config = load_config
    legacy._freeze_identity = freeze_identity
    legacy.preflight_run = preflight_run
    legacy._run_session = _run_session_v4


def main(argv: list[str] | None = None) -> int:
    install_contract()
    return legacy.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
