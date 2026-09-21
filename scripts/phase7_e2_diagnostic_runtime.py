"""Bounded runtime for the Phase 7 E2 experimental reasoning agent.

This is an experiment harness, not the production Req2Web F1--F4 flow.  The
validation and preflight paths are model-free.  The optional Qwen backend is
loaded lazily and once per process, then reused with a clean conversation for
each scheduled session (no KV state is shared).
"""

from __future__ import annotations

import argparse
import queue
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import sys
import time
from typing import Any, Callable, Mapping, Protocol

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

PUBLIC_SCHEMA = "req2web.phase7.e2_public.v1"
RESULT_SCHEMA = "req2web.phase7.e2_runtime_result.v1"
EXPECTED_BUDGETS = {
    "reads": 6, "input_tokens": 12000, "output_tokens": 2048,
    "provider_turns": 7, "session_seconds": 120,
    "measured_sessions": 40, "development_sessions": 4,
}
QWEN_MODEL_ID = "Qwen/Qwen3.5-9B"
QWEN_MODEL_REVISION = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
MODEL_LOAD_SECONDS = 180
PROTOCOL_REVISION = "req2web.phase7.e2_turn_boundary.v2"
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^(?:p|d|m)[0-9]{3}$")
ARTIFACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.json$")
FINAL_STATUSES = {"fault", "no_fault", "unknown", "unsupported_input"}

COMMON_PROMPT = """You are an isolated EXPERIMENT diagnostic reasoning agent, not a production Req2Web agent. Diagnose only from listed JSON artifacts. Do not guess unavailable values or evaluator labels. The hard session budgets are 6 reads, 7 provider turns, 12000 cumulative input tokens, 2048 output tokens per reply, and 120 seconds excluding model load. Reply with exactly one JSON object and no Markdown. First read readme.json for the neutral artifact overview. To read, use {\"action\":\"read\",\"artifact\":\"name.json\",\"pointer\":\"\"}; pointer is empty or RFC6901. Finish with {\"action\":\"final\",\"answer\":{\"status\":\"fault\",\"origin_candidates\":[{\"artifact\":\"name.json\",\"entity\":\"...\",\"field\":\"...\"}],\"affected_use_cases\":[\"...\"],\"evidence_edges\":[{\"from\":{\"artifact\":\"name.json\",\"pointer\":\"/...\"},\"to\":{\"artifact\":\"name.json\",\"pointer\":\"/...\"}}],\"uncertainty\":\"...\"}}. The exact allowed status strings are fault, no_fault, unknown, and unsupported_input. The final answer must use exact keys and JSON types."""
COMMON_PROMPT += " After one JSON object, end your assistant turn. The host executes reads; never generate a user message or invent a tool result. On the last available turn or after six reads, return your final answer using only observed evidence. Keep uncertainty concise; use unknown if evidence is insufficient."


def generation_stop_ids(tokenizer: Any, configured_eos: Any) -> list[int]:
    """Honor the pinned chat tokenizer's turn boundary as well as model EOS."""
    chat_eos = tokenizer.convert_tokens_to_ids("<|im_end|>")
    if type(chat_eos) is not int or tokenizer.eos_token_id != chat_eos:
        raise RuntimeErrorClosed("chat end token does not match tokenizer EOS")
    if tokenizer.convert_ids_to_tokens(chat_eos) != "<|im_end|>":
        raise RuntimeErrorClosed("chat end token is unavailable")
    configured = configured_eos if isinstance(configured_eos, list) else [configured_eos]
    values = [chat_eos, *(x for x in configured if x is not None)]
    if any(type(x) is not int or x < 0 for x in values):
        raise RuntimeErrorClosed("invalid generation EOS ids")
    return sorted(set(values))


class RuntimeErrorClosed(ValueError):
    """Fail-closed input, protocol, or runtime error."""


class FatalBackendError(RuntimeError):
    """Uncertain/fatal backend state; no later session may start."""


class Backend(Protocol):
    def generate(self, messages: list[dict[str, str]], *, max_new_tokens: int,
                 remaining_input_tokens: int, deadline: float,
                 started: Callable[[Mapping[str, Any]], None],
                 chunk: Callable[[bytes], None]) -> Mapping[str, Any]: ...


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strict_json(raw: bytes | str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise RuntimeErrorClosed(f"duplicate JSON key: {key}")
            out[key] = value
        return out
    def bad_constant(value: str) -> None:
        raise RuntimeErrorClosed(f"non-finite JSON number: {value}")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeErrorClosed(f"invalid JSON: {exc}") from exc


def _exact_keys(value: Mapping[str, Any], keys: set[str], where: str) -> None:
    if type(value) is not dict or set(value) != keys:
        raise RuntimeErrorClosed(f"{where} must have exact keys {sorted(keys)}")


def _regular_contained(root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if posix.is_absolute() or not relative or "\\" in relative or any(p in {"", ".", ".."} for p in posix.parts):
        raise RuntimeErrorClosed(f"unsafe relative path: {relative!r}")
    candidate = root.joinpath(*posix.parts)
    if any(part.is_symlink() for part in [root, *candidate.parents, candidate] if part.exists()):
        raise RuntimeErrorClosed(f"symlink forbidden: {relative}")
    try:
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeErrorClosed(f"missing artifact: {relative}") from exc
    if resolved.parent != resolved_root and resolved_root not in resolved.parents:
        raise RuntimeErrorClosed(f"path escape: {relative}")
    if not resolved.is_file() or resolved.is_symlink():
        raise RuntimeErrorClosed(f"artifact is not a regular file: {relative}")
    return resolved


def validate_public_root(public_root: Path, expected_manifest_sha256: str) -> dict[str, Any]:
    """Strictly validate and hash-bind the entire public packet manifest."""
    root = public_root.resolve(strict=True)
    manifest_path = _regular_contained(root, "manifest.json")
    raw = manifest_path.read_bytes()
    if not HEX_RE.fullmatch(expected_manifest_sha256) or sha256_bytes(raw) != expected_manifest_sha256:
        raise RuntimeErrorClosed("public manifest SHA-256 mismatch")
    manifest = _strict_json(raw)
    _exact_keys(manifest, {"schema_version", "budgets", "packets", "schedule"}, "manifest")
    if manifest["schema_version"] != PUBLIC_SCHEMA or manifest["budgets"] != EXPECTED_BUDGETS:
        raise RuntimeErrorClosed("public schema or budgets mismatch")
    packets: dict[str, dict[str, Any]] = {}
    if type(manifest["packets"]) is not list:
        raise RuntimeErrorClosed("packets must be a list")
    for packet in manifest["packets"]:
        _exact_keys(packet, {"packet_id", "files"}, "packet")
        pid = packet["packet_id"]
        if type(pid) is not str or not re.fullmatch(r"p[0-9]{3}", pid) or pid in packets or type(packet["files"]) is not list:
            raise RuntimeErrorClosed("invalid or duplicate packet_id/files")
        seen: set[str] = set()
        for item in packet["files"]:
            _exact_keys(item, {"path", "sha256", "bytes"}, "packet file")
            path, digest, size = item["path"], item["sha256"], item["bytes"]
            if type(path) is not str or not ARTIFACT_RE.fullmatch(path) or path in seen:
                raise RuntimeErrorClosed("packet file must be a unique JSON basename")
            if type(digest) is not str or not HEX_RE.fullmatch(digest) or type(size) is not int or type(size) is bool or size < 0:
                raise RuntimeErrorClosed("invalid packet file identity")
            file_raw = _regular_contained(root, f"packets/{pid}/{path}").read_bytes()
            if len(file_raw) != size or sha256_bytes(file_raw) != digest:
                raise RuntimeErrorClosed(f"packet file identity mismatch: {pid}/{path}")
            _strict_json(file_raw)
            seen.add(path)
        packets[pid] = packet
    _exact_keys(manifest["schedule"], {"development", "measured"}, "schedule")
    sessions: set[str] = set()
    packet_arms: dict[str, set[str]] = {key: set() for key in packets}
    split_packets: dict[str, set[str]] = {"development": set(), "measured": set()}
    for split, prefix, count in (("development", "d", 4), ("measured", "m", 40)):
        rows = manifest["schedule"][split]
        if type(rows) is not list or len(rows) != count:
            raise RuntimeErrorClosed(f"{split} schedule count mismatch")
        for row in rows:
            _exact_keys(row, {"session_id", "packet_id", "arm"}, "schedule row")
            sid, pid, arm = row["session_id"], row["packet_id"], row["arm"]
            if type(sid) is not str or not re.fullmatch(prefix + r"[0-9]{3}", sid) or sid in sessions or pid not in packets or arm not in {"A", "B"}:
                raise RuntimeErrorClosed("invalid schedule row")
            sessions.add(sid); packet_arms[pid].add(arm)
            split_packets[split].add(pid)
    if len(packets) != 22 or len(split_packets["development"]) != 2 or len(split_packets["measured"]) != 20 or split_packets["development"] & split_packets["measured"]:
        raise RuntimeErrorClosed("schedule must use 22 packets: 2 development and 20 disjoint measured")
    for pid, arms in packet_arms.items():
        names = {item["path"] for item in packets[pid]["files"]}
        if ("B" in arms) != ("trace_index.json" in names):
            raise RuntimeErrorClosed(f"trace_index.json presence does not match arm B for {pid}")
        if arms != {"A", "B"}:
            raise RuntimeErrorClosed(f"packet {pid} must be used by exactly paired A/B sessions")
    return manifest


def _validate_caps(caps: object) -> dict[str, int]:
    if type(caps) is not dict or set(caps) != {"reads", "input_tokens", "output_tokens", "provider_turns", "session_seconds"}:
        raise RuntimeErrorClosed("caps must have exact runtime budget keys")
    expected = {k: EXPECTED_BUDGETS[k] for k in caps}
    if caps != expected:
        raise RuntimeErrorClosed("runtime caps must equal frozen public budgets")
    return dict(caps)


def _load_config(config_path: Path) -> dict[str, Any]:
    config = _strict_json(config_path.resolve(strict=True).read_bytes())
    keys = {"schema_version", "approved", "model_root", "model_identity", "model_revision", "integrity_evidence", "public_root", "public_manifest_sha256", "output_root", "state_root", "split", "caps", "accepted_development"}
    _exact_keys(config, keys, "action config")
    if config["schema_version"] != "req2web.phase7.e2_action.v1" or type(config["approved"]) is not bool:
        raise RuntimeErrorClosed("invalid action config schema/approved")
    for key in ("model_root", "model_identity", "model_revision", "integrity_evidence", "public_root", "public_manifest_sha256", "output_root", "state_root", "split"):
        if type(config[key]) is not str or not config[key]:
            raise RuntimeErrorClosed(f"{key} must be a non-empty string")
    if config["split"] not in {"development", "measured"}:
        raise RuntimeErrorClosed("split must be development or measured")
    if config["model_identity"] != QWEN_MODEL_ID or config["model_revision"] != QWEN_MODEL_REVISION:
        raise RuntimeErrorClosed("action config must bind the fixed Qwen3.5-9B identity and revision")
    expected_state = Path(config["public_root"]).resolve().parent / "runtime_state"
    if Path(config["state_root"]).resolve() != expected_state:
        raise RuntimeErrorClosed("state_root must be public_root.parent/runtime_state")
    _validate_caps(config["caps"])
    if config["accepted_development"] is not None:
        _exact_keys(config["accepted_development"], {"accepted", "freeze_identity", "summary_sha256"}, "accepted_development")
        ad = config["accepted_development"]
        if ad["accepted"] is not True or not all(type(ad[k]) is str and HEX_RE.fullmatch(ad[k]) for k in ("freeze_identity", "summary_sha256")):
            raise RuntimeErrorClosed("invalid accepted-development binding")
    if config["split"] == "measured" and config["accepted_development"] is None:
        raise RuntimeErrorClosed("measured split requires explicit accepted-development binding")
    return config


def _freeze_identity(config: Mapping[str, Any], manifest_sha: str) -> str:
    integrity_sha = sha256_bytes(Path(config["integrity_evidence"]).resolve(strict=True).read_bytes())
    material = {"runtime_sha256": sha256_bytes(Path(__file__).read_bytes()), "prompt_sha256": sha256_bytes(COMMON_PROMPT.encode()), "model_identity": config["model_identity"], "model_revision": config["model_revision"], "model_integrity_evidence_sha256": integrity_sha, "budgets": config["caps"], "public_manifest_sha256": manifest_sha}
    return sha256_bytes(canonical_bytes(material))


def preflight_run(config_path: Path) -> dict[str, Any]:
    """Validate action inputs without importing or loading model libraries."""
    config = _load_config(config_path)
    public_root = Path(config["public_root"]).resolve(strict=True)
    manifest = validate_public_root(public_root, config["public_manifest_sha256"])
    model_root = Path(config["model_root"]).resolve(strict=True)
    integrity = Path(config["integrity_evidence"]).resolve(strict=True)
    from req2web_runtime.phase4_local_qwen import validate_model_inventory_metadata
    inventory = validate_model_inventory_metadata(model_root=model_root, integrity_evidence=integrity, allow_relocated_model_root=True)
    if inventory.get("model_id") != QWEN_MODEL_ID or inventory.get("model_revision") != QWEN_MODEL_REVISION:
        raise RuntimeErrorClosed("validated model inventory identity/revision mismatch")
    freeze = _freeze_identity(config, config["public_manifest_sha256"])
    return {"ready": bool(config["approved"]), "model_loaded": False, "run_occurred": False, "split": config["split"], "freeze_identity": freeze, "manifest": manifest, "model_inventory_identity": inventory["inventory_identity"], "validated_inventory": inventory}


def _write_exclusive(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw); handle.flush(); os.fsync(handle.fileno())


def _append_durable(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical_bytes(value) + b"\n"); handle.flush(); os.fsync(handle.fileno())


def _write_active(path: Path, value: Mapping[str, Any]) -> None:
    raw = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    if temporary.exists(): temporary.unlink()
    _write_exclusive(temporary, raw)
    os.replace(temporary, path)


def _pointer(value: Any, pointer: str) -> Any:
    if pointer == "": return value
    if type(pointer) is not str or not pointer.startswith("/"):
        raise RuntimeErrorClosed("pointer must be empty or RFC6901")
    current = value
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if "~" in token:
            raise RuntimeErrorClosed("invalid RFC6901 escape")
        if type(current) is dict and token in current: current = current[token]
        elif type(current) is list and re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(current): current = current[int(token)]
        else: raise RuntimeErrorClosed("JSON pointer does not resolve")
    return current


def _validate_final(answer: Any) -> dict[str, Any]:
    _exact_keys(answer, {"status", "origin_candidates", "affected_use_cases", "evidence_edges", "uncertainty"}, "final answer")
    if answer["status"] not in FINAL_STATUSES or type(answer["uncertainty"]) is not str:
        raise RuntimeErrorClosed("invalid final status/uncertainty")
    if type(answer["origin_candidates"]) is not list or type(answer["affected_use_cases"]) is not list or not all(type(x) is str for x in answer["affected_use_cases"]):
        raise RuntimeErrorClosed("invalid final answer lists")
    for row in answer["origin_candidates"]:
        _exact_keys(row, {"artifact", "entity", "field"}, "origin candidate")
        if not all(type(row[k]) is str for k in row): raise RuntimeErrorClosed("origin candidate values must be strings")
    if type(answer["evidence_edges"]) is not list: raise RuntimeErrorClosed("evidence_edges must be a list")
    for edge in answer["evidence_edges"]:
        _exact_keys(edge, {"from", "to"}, "evidence edge")
        for endpoint in (edge["from"], edge["to"]):
            _exact_keys(endpoint, {"artifact", "pointer"}, "evidence endpoint")
            if not all(type(endpoint[k]) is str for k in endpoint): raise RuntimeErrorClosed("evidence endpoint values must be strings")
    return dict(answer)


def _packet_files(root: Path, packet: Mapping[str, Any]) -> dict[str, tuple[Path, str]]:
    pid = packet["packet_id"]
    return {row["path"]: (_regular_contained(root, f"packets/{pid}/{row['path']}"), row["sha256"]) for row in packet["files"]}


def _run_session(*, row: Mapping[str, Any], packet: Mapping[str, Any], root: Path, out: Path,
                 backend: Backend, caps: Mapping[str, int], freeze: str, active_path: Path) -> dict[str, Any]:
    sid, arm = row["session_id"], row["arm"]
    files = _packet_files(root, packet)
    inventory = sorted(files) if arm == "B" else sorted(name for name in files if name != "trace_index.json")
    if "readme.json" not in inventory:
        raise RuntimeErrorClosed("every packet must provide readme.json")
    messages = [{"role": "system", "content": COMMON_PROMPT}, {"role": "user", "content": canonical_bytes({"packet_id": row["packet_id"], "artifacts": inventory}).decode()}]
    started_at = time.monotonic(); deadline = started_at + caps["session_seconds"]
    reads = turns = input_tokens = 0
    output_tokens: int | None = 0
    raw_refs: list[dict[str, Any]] = []
    answer = None; status = "invalid_response"
    fatal_error: str | None = None
    protocol_error: str | None = None
    for turn in range(1, caps["provider_turns"] + 1):
        if time.monotonic() >= deadline: status = "timeout"; break
        raw_path = out / sid / f"turn_{turn:02d}.raw"
        started_path = out / sid / f"turn_{turn:02d}.started.json"
        stream_path = out / sid / f"turn_{turn:02d}.partial"
        def call_started(meta: Mapping[str, Any]) -> None:
            nonlocal turns, input_tokens
            actual_input = meta.get("input_tokens")
            if type(actual_input) is not int or isinstance(actual_input, bool) or actual_input < 0:
                raise RuntimeErrorClosed("started metadata requires actual input_tokens")
            formatted_prompt = meta.get("formatted_prompt")
            if type(formatted_prompt) is not str:
                raise RuntimeErrorClosed("started metadata requires the exact formatted model prompt")
            turns += 1; input_tokens += actual_input
            prompt_path = out / sid / f"turn_{turn:02d}.prompt.txt"
            _write_exclusive(prompt_path, formatted_prompt.encode("utf-8"))
            public_meta = {key: value for key, value in meta.items() if key != "formatted_prompt"}
            _write_exclusive(started_path, canonical_bytes({"session_id": sid, "turn": turn, "freeze_identity": freeze, "event": "generation_started", "automatic_retry_count": 0, "input_tokens": actual_input, "formatted_prompt_sha256": sha256_bytes(formatted_prompt.encode("utf-8")), "backend": public_meta}))
        def chunk(raw: bytes) -> None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("ab") as handle: handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        try:
            _write_active(active_path, {"phase": "session", "session_id": sid, "started_monotonic": started_at, "deadline_unix": time.time() + max(0.0, deadline - time.monotonic())})
            result = backend.generate(messages, max_new_tokens=caps["output_tokens"], remaining_input_tokens=caps["input_tokens"] - input_tokens, deadline=deadline, started=call_started, chunk=chunk)
            raw = result["raw"]
            if type(raw) is str: raw = raw.encode("utf-8")
            if type(raw) is not bytes: raise RuntimeErrorClosed("backend raw must be bytes or string")
            _write_exclusive(raw_path, raw)
            if "generation_evidence" in result:
                _write_exclusive(out / sid / f"turn_{turn:02d}.generation.json", canonical_bytes(result["generation_evidence"]))
            turn_in, turn_out = result["input_tokens"], result["output_tokens"]
            if type(turn_in) is not int or type(turn_out) is not int or isinstance(turn_in, bool) or isinstance(turn_out, bool) or turn_in < 0 or turn_out < 0:
                raise RuntimeErrorClosed("backend token counts must be nonnegative integers")
            if turns == 0 or turn_in != int(_strict_json(started_path.read_bytes())["input_tokens"]):
                raise RuntimeErrorClosed("backend input token accounting drifted from started ledger")
            output_tokens += turn_out
            raw_refs.append({"turn": turn, "path": str(raw_path.relative_to(out)).replace("\\", "/"), "sha256": sha256_bytes(raw), "bytes": len(raw)})
            if input_tokens > caps["input_tokens"] or turn_out > caps["output_tokens"]:
                status = "budget_exceeded"; break
            if time.monotonic() > deadline:
                status = "timeout"; break
            response = _strict_json(raw)
            if type(response) is not dict or response.get("action") not in {"read", "final"}: raise RuntimeErrorClosed("invalid action")
            if response["action"] == "final":
                _exact_keys(response, {"action", "answer"}, "final response"); answer = _validate_final(response["answer"]); status = answer["status"]; break
            _exact_keys(response, {"action", "artifact", "pointer"}, "read request")
            if type(response["artifact"]) is not str or type(response["pointer"]) is not str or response["artifact"] not in files or (arm == "A" and response["artifact"] == "trace_index.json"):
                raise RuntimeErrorClosed("unlisted or invalid artifact read")
            reads += 1
            if reads > caps["reads"]: status = "read_budget_exceeded"; break
            path, digest = files[response["artifact"]]
            source_raw = path.read_bytes()
            if sha256_bytes(source_raw) != digest:
                raise RuntimeErrorClosed("artifact drifted at tool-read time")
            value = _pointer(_strict_json(source_raw), response["pointer"])
            tool_response = {"artifact": response["artifact"], "sha256": digest, "pointer": response["pointer"], "value": value}
            _write_exclusive(out / sid / f"turn_{turn:02d}.tool_read.json", canonical_bytes({"request": response, "response": tool_response, "source_bytes": len(source_raw), "source_sha256": sha256_bytes(source_raw)}))
            control = {"remaining_reads": caps["reads"] - reads, "remaining_provider_turns": caps["provider_turns"] - turn,
                       "remaining_cumulative_input_tokens": caps["input_tokens"] - input_tokens,
                       "final_required": reads >= caps["reads"] or turn >= caps["provider_turns"] - 1}
            messages.extend([{"role": "assistant", "content": raw.decode("utf-8")}, {"role": "user", "content": canonical_bytes({**tool_response, "host_control": control}).decode("utf-8")}])
        except FatalBackendError as exc:
            if stream_path.exists():
                partial = stream_path.read_bytes()
                raw_refs.append({"turn": turn, "path": str(stream_path.relative_to(out)).replace("\\", "/"), "sha256": sha256_bytes(partial), "bytes": len(partial), "partial": True})
                output_tokens = None
            status = "fatal_backend_error"; fatal_error = f"{type(exc).__name__}: {exc}"; break
        except TimeoutError:
            if stream_path.exists():
                partial = stream_path.read_bytes()
                raw_refs.append({"turn": turn, "path": str(stream_path.relative_to(out)).replace("\\", "/"), "sha256": sha256_bytes(partial), "bytes": len(partial), "partial": True})
                output_tokens = None
            status = "timeout"; break
        except Exception as exc:
            protocol_error = f"{type(exc).__name__}: {exc}"
            if stream_path.exists():
                partial = stream_path.read_bytes()
                raw_refs.append({"turn": turn, "path": str(stream_path.relative_to(out)).replace("\\", "/"), "sha256": sha256_bytes(partial), "bytes": len(partial), "partial": True})
            status = "invalid_response" if isinstance(exc, RuntimeErrorClosed) else "backend_exception"
            break
    _write_active(active_path, {"phase": "idle", "session_id": None, "started_monotonic": None, "deadline_unix": None})
    return {"session_id": sid, "packet_id": row["packet_id"], "arm": arm, "status": status, "answer": answer, "reads": reads, "provider_turns": turns, "input_tokens": input_tokens, "output_tokens": output_tokens, "raw_refs": raw_refs, "elapsed_seconds": time.monotonic() - started_at, "fatal_error": fatal_error, "protocol_error": protocol_error}


def run_experiment(config_path: Path, backend: Backend | None = None) -> dict[str, Any]:
    """Run one explicitly approved split; never retries or resumes sessions."""
    preflight = preflight_run(config_path)
    config = _load_config(config_path)
    if not config["approved"]: raise RuntimeErrorClosed("explicit approved=true is required")
    freeze = preflight["freeze_identity"]
    if config["split"] == "measured" and config["accepted_development"]["freeze_identity"] != freeze:
        raise RuntimeErrorClosed("accepted development freeze does not match this run")
    state_root = Path(config["state_root"]).resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    if config["split"] == "measured":
        development_claim = state_root / "claims" / config["public_manifest_sha256"] / "development" / "claim.json"
        if not development_claim.is_file():
            raise RuntimeErrorClosed("measured split requires a completed development claim")
        claim = _strict_json(development_claim.read_bytes())
        development_summary = Path(claim["output_root"]) / "summary.json"
        if not development_summary.is_file():
            raise RuntimeErrorClosed("development summary is absent")
        summary_raw = development_summary.read_bytes()
        if sha256_bytes(summary_raw) != config["accepted_development"]["summary_sha256"]:
            raise RuntimeErrorClosed("accepted development summary SHA-256 mismatch")
        development_result = _strict_json(summary_raw)
        development_sessions = development_result.get("sessions", [])
        development_complete = (
            type(development_sessions) is list
            and len(development_sessions) == EXPECTED_BUDGETS["development_sessions"]
            and development_result.get("fatal") is None
            and all(type(row.get("answer")) is dict and row.get("status") in FINAL_STATUSES and row["answer"].get("status") == row["status"] for row in development_sessions)
        )
        if development_result.get("freeze_identity") != freeze or development_result.get("split") != "development" or not development_complete:
            raise RuntimeErrorClosed("development result is not complete for this freeze")
    claim_dir = state_root / "claims" / config["public_manifest_sha256"] / config["split"]
    try: claim_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc: raise RuntimeErrorClosed("split already claimed; automatic resume/retry forbidden") from exc
    output = Path(config["output_root"]).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=False)
    _write_exclusive(claim_dir / "claim.json", canonical_bytes({"public_manifest_sha256": config["public_manifest_sha256"], "freeze_identity": freeze, "split": config["split"], "output_root": str(output)}))
    if backend is None: backend = LocalQwenBackend(Path(config["model_root"]), Path(config["integrity_evidence"]), preflight["validated_inventory"])
    rows = preflight["manifest"]["schedule"][config["split"]]
    packets = {p["packet_id"]: p for p in preflight["manifest"]["packets"]}
    sessions: list[dict[str, Any]] = []
    fatal: str | None = None
    active_path = output / "active.json"
    if hasattr(backend, "prepare"):
        try:
            _write_active(active_path, {"phase": "loading", "session_id": None, "started_monotonic": time.monotonic(), "deadline_unix": time.time() + MODEL_LOAD_SECONDS})
            backend.prepare()
        except FatalBackendError as exc:
            fatal = f"{type(exc).__name__}: {exc}"
            sessions = [{"session_id": row["session_id"], "packet_id": row["packet_id"], "arm": row["arm"], "status": "not_started_after_fatal", "answer": None, "reads": 0, "provider_turns": 0, "input_tokens": 0, "output_tokens": 0, "raw_refs": [], "fatal_error": None} for row in rows]
    for index, row in enumerate(rows):
        if fatal is not None: break
        try:
            result = _run_session(row=row, packet=packets[row["packet_id"]], root=Path(config["public_root"]), out=output, backend=backend, caps=config["caps"], freeze=freeze, active_path=active_path)
            sessions.append(result)
            _append_durable(output / "session_ledger.jsonl", result)
            if result["fatal_error"] is not None:
                fatal = result["fatal_error"]
                for later in rows[index + 1:]:
                    sessions.append({"session_id": later["session_id"], "packet_id": later["packet_id"], "arm": later["arm"], "status": "not_started_after_fatal", "answer": None, "reads": 0, "provider_turns": 0, "input_tokens": 0, "output_tokens": 0, "raw_refs": [], "fatal_error": None})
                break
        except FatalBackendError as exc:
            fatal = f"{type(exc).__name__}: {exc}"
            sessions.append({"session_id": row["session_id"], "packet_id": row["packet_id"], "arm": row["arm"], "status": "fatal_backend_error", "answer": None, "reads": 0, "provider_turns": 0, "input_tokens": 0, "output_tokens": 0, "raw_refs": [], "fatal_error": fatal})
            for later in rows[index + 1:]:
                sessions.append({"session_id": later["session_id"], "packet_id": later["packet_id"], "arm": later["arm"], "status": "not_started_after_fatal", "answer": None, "reads": 0, "provider_turns": 0, "input_tokens": 0, "output_tokens": 0, "raw_refs": [], "fatal_error": None})
            break
    _write_active(active_path, {"phase": "idle", "session_id": None, "started_monotonic": None, "deadline_unix": None})
    summary = {"schema_version": RESULT_SCHEMA, "public_manifest_sha256": config["public_manifest_sha256"], "freeze_identity": freeze, "split": config["split"], "fatal": fatal, "sessions": sessions}
    _write_exclusive(output / "summary.json", canonical_bytes(summary))
    return summary


def _run_cli(config_path: Path) -> dict[str, Any]:
    """Install the CLI interruption receipt handler and always restore it."""
    config = _load_config(config_path)
    active_path = Path(config["output_root"]).resolve() / "active.json"
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    def handle_sigterm(signum: int, frame: object) -> None:
        _write_active(active_path, {"phase": "interrupted", "session_id": None, "started_monotonic": time.monotonic(), "deadline_unix": None, "signal": signum})
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        return run_experiment(config_path)
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


class LocalQwenBackend:
    """Lazy, load-once local Qwen3.5-9B BF16 backend.

    Python-side deadline checks are supported, but a hard wall during a blocked
    CUDA kernel requires a parent-process Linux watchdog.  Therefore this
    backend refuses non-Linux execution. Streaming callbacks preserve partial
    text where Transformers exposes it; fatal CUDA errors propagate.
    """
    def __init__(self, model_root: Path, integrity_evidence: Path, validated_inventory: Mapping[str, Any]):
        self.model_root = model_root; self.integrity_evidence = integrity_evidence
        if validated_inventory.get("model_id") != QWEN_MODEL_ID or validated_inventory.get("model_revision") != QWEN_MODEL_REVISION:
            raise RuntimeErrorClosed("backend requires the action's already validated fixed inventory")
        self.validated_inventory = dict(validated_inventory)
        self._processor = self._model = self._torch = None

    def _load(self) -> None:
        if self._model is not None: return
        if sys.platform != "linux": raise FatalBackendError("real backend is gated to Linux with an external process watchdog")
        os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"})
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor
        self._processor = AutoProcessor.from_pretrained(str(self.model_root), local_files_only=True, trust_remote_code=False)
        self._model = AutoModelForImageTextToText.from_pretrained(str(self.model_root), local_files_only=True, trust_remote_code=False, torch_dtype=torch.bfloat16, device_map={"": 0}, attn_implementation="sdpa")
        self._torch = torch

    def prepare(self) -> None:
        """Load once outside every session wall-clock budget."""
        self._load()

    def generate(self, messages: list[dict[str, str]], *, max_new_tokens: int, remaining_input_tokens: int, deadline: float,
                 started: Callable[[Mapping[str, Any]], None], chunk: Callable[[bytes], None]) -> Mapping[str, Any]:
        self._load()
        if time.monotonic() >= deadline: raise TimeoutError("session deadline reached before generation")
        import threading
        from transformers import StoppingCriteria, StoppingCriteriaList, TextIteratorStreamer
        chat = [{"role": m["role"], "content": [{"type": "text", "text": m["content"]}]} for m in messages]
        rendered = self._processor.apply_chat_template(chat, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        inputs = self._processor(text=[rendered], return_tensors="pt", add_special_tokens=False).to("cuda:0")
        exact_input_tokens = int(inputs["input_ids"].shape[1])
        if exact_input_tokens > remaining_input_tokens:
            raise RuntimeErrorClosed("exact tokenized conversation exceeds remaining input budget")
        eos_ids = generation_stop_ids(self._processor.tokenizer, self._model.generation_config.eos_token_id)
        started({"input_tokens": exact_input_tokens, "formatted_prompt": rendered, "max_new_tokens": max_new_tokens, "do_sample": False, "device": "cuda:0", "dtype": "bf16", "eos_token_ids": eos_ids, "protocol_revision": PROTOCOL_REVISION})
        class DeadlineCriteria(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return time.monotonic() >= deadline
        streamer = TextIteratorStreamer(self._processor.tokenizer, skip_prompt=True, skip_special_tokens=True, timeout=0.25)
        holder: dict[str, Any] = {}
        def worker() -> None:
            try:
                with self._torch.inference_mode():
                    holder["output"] = self._model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False, use_cache=True, eos_token_id=eos_ids, streamer=streamer, stopping_criteria=StoppingCriteriaList([DeadlineCriteria()]))
            except BaseException as exc:
                holder["error"] = exc
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        pieces: list[bytes] = []
        iterator = iter(streamer)
        while True:
            try:
                text_piece = next(iterator)
            except queue.Empty:
                if not thread.is_alive():
                    break
                if time.monotonic() >= deadline:
                    raise FatalBackendError("generation thread remained in flight after the session deadline")
                continue
            except StopIteration:
                break
            raw_piece = text_piece.encode("utf-8")
            pieces.append(raw_piece); chunk(raw_piece)
        thread.join()
        if "error" in holder:
            exc = holder["error"]
            if "cuda" in f"{type(exc).__name__}: {exc}".lower(): raise FatalBackendError(str(exc)) from exc
            raise exc
        output = holder["output"]
        new_tokens = output[0, inputs["input_ids"].shape[1]:]
        raw = b"".join(pieces)
        token_ids = new_tokens.tolist()
        return {"raw": raw, "input_tokens": exact_input_tokens, "output_tokens": int(new_tokens.shape[0]), "deadline_exceeded": time.monotonic() > deadline,
                "generation_evidence": {"protocol_revision": PROTOCOL_REVISION, "eos_token_ids": eos_ids, "generated_token_ids": token_ids,
                                        "ended_on_eos": bool(token_ids and token_ids[-1] in eos_ids), "raw_sha256": sha256_bytes(raw)}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate, preflight, or run the bounded Phase 7 E2 diagnostic experiment")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate"); validate.add_argument("--public-root", type=Path, required=True); validate.add_argument("--manifest-sha256", required=True)
    for name in ("preflight", "run"):
        p = sub.add_parser(name); p.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            manifest = validate_public_root(args.public_root, args.manifest_sha256)
            result = {"valid": True, "schema_version": manifest["schema_version"], "public_manifest_sha256": args.manifest_sha256, "packet_count": len(manifest["packets"]), "development_sessions": len(manifest["schedule"]["development"]), "measured_sessions": len(manifest["schedule"]["measured"])}
        elif args.command == "preflight":
            preflight = preflight_run(args.config)
            result = {key: value for key, value in preflight.items() if key not in {"manifest", "validated_inventory"}}
        else: result = _run_cli(args.config)
        print(canonical_bytes(result).decode("utf-8")); return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
