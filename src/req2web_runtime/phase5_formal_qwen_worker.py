"""Case-isolated Qwen worker for the sealed Phase 5 action runner.

This module reuses the Phase 4 BF16/no-quantization loader and complete-JSON
stopping behavior, but validates the distinct Phase 5 route-specific input
schema.
Importing the module performs no model, GPU, subprocess, network, or remote
action.
"""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Mapping
import uuid

from req2web_runtime import phase4_local_qwen as _local
from req2web_runtime import phase4_remote_qwen as _remote
from req2web_runtime.phase4_remote_qwen_fresh_integrated import (
    RemoteFreshIntegratedProfile,
)

from . import phase5_formal_runner as _formal


WORKER_PROTOCOL = "req2web.phase5.formal_qwen_worker.v1"
STREAM_SCHEMA_VERSION = "req2web.phase5.formal_qwen_stream.v1"


class Phase5FormalQwenWorkerError(_formal.Phase5FormalRunnerError):
    """Raised when the real worker/supervisor must fail closed."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _decode_b64(
    value: object,
    name: str,
    *,
    allow_empty: bool = False,
) -> bytes:
    if not isinstance(value, str) or (not value and not allow_empty):
        boundary = "base64 text" if allow_empty else "non-empty base64"
        raise Phase5FormalQwenWorkerError(f"{name} must be {boundary}")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise Phase5FormalQwenWorkerError(f"{name} is invalid base64") from exc


def _offline_process() -> None:
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "LANGSMITH_TRACING": "0",
            "LANGCHAIN_TRACING_V2": "0",
            "CUDA_VISIBLE_DEVICES": "0",
            "PYTHONUNBUFFERED": "1",
        }
    )


def _emit_event(
    event: str,
    *,
    node_id: str | None = None,
    delta: bytes = b"",
) -> None:
    raw = _formal._canonical(
        {
            "schema_version": STREAM_SCHEMA_VERSION,
            "event": event,
            "node_id": node_id,
            "delta_b64": _b64(delta),
        }
    ) + b"\n"
    stream = getattr(sys.stderr, "buffer", None)
    if stream is not None:
        stream.write(raw)
        stream.flush()
    else:
        sys.stderr.write(raw.decode("utf-8"))
        sys.stderr.flush()


def validate_phase5_formal_generation_artifacts(
    *,
    node_id: str,
    input_bytes: bytes,
    prompt_bytes: bytes,
    config_bytes: bytes,
    request_bytes: bytes,
    require_formal_action_receipt: bool,
) -> dict[str, object]:
    """Validate one Phase 5 request without loading or calling a model."""

    if node_id not in _formal.NODE_ORDER:
        raise Phase5FormalQwenWorkerError("formal worker node is unsupported")
    input_value = _formal._strict_json(input_bytes, f"{node_id} input")
    prompt = _formal._strict_json(prompt_bytes, f"{node_id} prompt")
    config = _formal._strict_json(config_bytes, f"{node_id} config")
    request = _formal._strict_json(request_bytes, f"{node_id} request")
    if not all(
        isinstance(value, Mapping)
        for value in (input_value, prompt, config, request)
    ):
        raise Phase5FormalQwenWorkerError(
            "formal worker artifacts must be JSON objects"
        )
    static_keys = [
        key
        for key in ("path1_static_projection", "path2_static_projection")
        if key in input_value
    ]
    if len(static_keys) != 1 or set(input_value) != {
        "schema_version",
        "node_id",
        "provider_case_ref",
        "provider_request_ref",
        static_keys[0],
        "same_run_validated_upstream_projection",
    }:
        raise Phase5FormalQwenWorkerError("formal worker input keys drifted")
    if (
        input_value["schema_version"] != _formal.NODE_INPUT_SCHEMA_VERSION
        or input_value["node_id"] != node_id
        or not str(input_value["provider_case_ref"]).startswith(
            "phase5-provider-case-"
        )
        or not str(input_value["provider_request_ref"]).startswith(
            "phase5-provider-request-"
        )
    ):
        raise Phase5FormalQwenWorkerError("formal worker input binding drifted")
    prohibited = _formal._scan_keys(input_value) & _formal._PROHIBITED_PROVIDER_KEYS
    if prohibited:
        raise Phase5FormalQwenWorkerError(
            f"formal worker input contains prohibited keys: {sorted(prohibited)}"
        )
    expected_prompt_keys = {
        "schema_version",
        "prompt_revision",
        "node_id",
        "input_identity",
        "output_format",
        "instructions",
        "exact_output_contract",
    }
    if node_id == "F3":
        expected_prompt_keys.add("required_interaction_plan")
    if node_id == "F4":
        expected_prompt_keys.add("required_acceptance_target_plan")
    if set(prompt) != expected_prompt_keys:
        raise Phase5FormalQwenWorkerError("formal worker prompt keys drifted")
    if (
        prompt["schema_version"] != _formal.NODE_PROMPT_SCHEMA_VERSION
        or prompt["prompt_revision"]
        != (
            _formal.PATH2_PROMPT_REVISION
            if static_keys[0] == "path2_static_projection"
            else _formal.PROMPT_REVISION
        )
        or prompt["node_id"] != node_id
        or prompt["input_identity"] != _formal._identity(input_bytes)
    ):
        raise Phase5FormalQwenWorkerError("formal worker prompt binding drifted")
    if set(config) != {
        "schema_version",
        "node_id",
        "selected_profile_sha256",
        "final_action_receipt_sha256",
        "device",
        "dtype",
        "compute_dtype",
        "quantization",
        "cpu_offload",
        "local_files_only",
        "model_context_tokens",
        "fixed_max_new_tokens",
        "input_truncation",
        "output_truncation",
        "complete_json_stopping",
        "do_sample",
        "temperature",
        "top_p",
        "decode_seed",
        "per_node_wall_timeout_seconds",
    }:
        raise Phase5FormalQwenWorkerError("formal worker config keys drifted")
    if (
        config["schema_version"] != _formal.NODE_CONFIG_SCHEMA_VERSION
        or config["node_id"] != node_id
        or config["selected_profile_sha256"]
        != _formal.RTX5090_PROFILE_SHA256
        or config["device"] != "cuda:0"
        or config["dtype"] != "bfloat16"
        or config["compute_dtype"] != "bfloat16"
        or config["quantization"] != "none"
        or config["cpu_offload"] is not False
        or config["local_files_only"] is not True
        or config["model_context_tokens"] != _formal.MODEL_CONTEXT_TOKENS
        or config["fixed_max_new_tokens"] is not None
        or config["input_truncation"] is not False
        or config["output_truncation"] is not False
        or config["complete_json_stopping"] is not True
        or config["do_sample"] is not False
        or config["temperature"] != 0.0
        or config["top_p"] != 1.0
        or config["decode_seed"] != _formal.DECODE_SEED
        or config["per_node_wall_timeout_seconds"]
        != _formal.PER_NODE_TIMEOUT_SECONDS
    ):
        raise Phase5FormalQwenWorkerError("formal worker config binding drifted")
    action_receipt = config["final_action_receipt_sha256"]
    if require_formal_action_receipt:
        if (
            not isinstance(action_receipt, str)
            or len(action_receipt) != 64
            or any(character not in "0123456789abcdef" for character in action_receipt)
        ):
            raise Phase5FormalQwenWorkerError(
                "formal worker requires the final action receipt hash"
            )
    elif action_receipt is not None:
        raise Phase5FormalQwenWorkerError(
            "synthetic worker validation must not carry a final action receipt"
        )
    if set(request) != {
        "schema_version",
        "source_kind",
        "node_id",
        "provider_case_ref",
        "provider_request_ref",
        "input_identity",
        "prompt_identity",
        "config_identity",
        "generate_call_cap",
        "automatic_retry",
        "retry_count",
    }:
        raise Phase5FormalQwenWorkerError("formal worker request keys drifted")
    if (
        request["schema_version"] != _formal.NODE_REQUEST_SCHEMA_VERSION
        or request["source_kind"] != "sealed_formal_holdout"
        or request["node_id"] != node_id
        or request["provider_case_ref"] != input_value["provider_case_ref"]
        or request["provider_request_ref"] != input_value["provider_request_ref"]
        or request["input_identity"] != _formal._identity(input_bytes)
        or request["prompt_identity"] != _formal._identity(prompt_bytes)
        or request["config_identity"] != _formal._identity(config_bytes)
        or request["generate_call_cap"] != 1
        or request["automatic_retry"] is not False
        or request["retry_count"] != 0
    ):
        raise Phase5FormalQwenWorkerError("formal worker request binding drifted")
    return {
        "input": copy.deepcopy(dict(input_value)),
        "prompt": copy.deepcopy(dict(prompt)),
        "config": copy.deepcopy(dict(config)),
        "request": copy.deepcopy(dict(request)),
    }


class _Phase5FormalBackend(_remote._RemoteTransformersBackend):
    """Phase 4 verified loader with the separate Phase 5 Provider schema."""

    def generate_formal_stream(
        self,
        *,
        node_id: str,
        input_bytes: bytes,
        prompt_bytes: bytes,
        config_bytes: bytes,
        request_bytes: bytes,
        emit_delta: Callable[[bytes], None],
    ) -> bytes:
        if (
            self._model is None
            or self._processor is None
            or self._torch is None
            or self._transformers is None
        ):
            raise Phase5FormalQwenWorkerError("formal backend is not loaded")
        artifacts = validate_phase5_formal_generation_artifacts(
            node_id=node_id,
            input_bytes=input_bytes,
            prompt_bytes=prompt_bytes,
            config_bytes=config_bytes,
            request_bytes=request_bytes,
            require_formal_action_receipt=True,
        )
        model_text = (
            "PHASE5_FORMAL_NODE_INPUT\n"
            + _formal._canonical(artifacts["input"]).decode("utf-8")
            + "\nPHASE5_FORMAL_PROMPT_CONTRACT\n"
            + _formal._canonical(artifacts["prompt"]).decode("utf-8")
        )
        try:
            rendered = self._processor.apply_chat_template(
                [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": model_text}],
                    }
                ],
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            encoded = self._processor(text=[rendered], return_tensors="pt")
            keys = self._validated_text_inputs(encoded)
            encoded = {key: encoded[key].to("cuda:0") for key in keys}
            input_length = int(encoded["input_ids"].shape[1])
            if input_length > _formal.MODEL_CONTEXT_TOKENS:
                raise Phase5FormalQwenWorkerError(
                    f"{node_id} input exceeds native context; no truncation is allowed"
                )
            remaining = _formal.MODEL_CONTEXT_TOKENS - input_length
            if remaining < 1:
                raise Phase5FormalQwenWorkerError(
                    f"{node_id} has no context remaining"
                )
            tokenizer = getattr(self._processor, "tokenizer", self._processor)
            streamer = _local._P4D1TokenDeltaStreamer(
                tokenizer=tokenizer,
                emit_delta=emit_delta,
            )
            stopping = self._transformers.StoppingCriteriaList(
                [
                    _remote._RemoteCompleteJsonStoppingCriteria(
                        tokenizer=tokenizer,
                        prompt_length=input_length,
                    )
                ]
            )
            self._torch.manual_seed(_formal.DECODE_SEED)
            self._torch.cuda.manual_seed_all(_formal.DECODE_SEED)
            generated = self._model.generate(
                **encoded,
                streamer=streamer,
                max_new_tokens=remaining,
                stopping_criteria=stopping,
                do_sample=False,
                num_return_sequences=1,
            )
            generated_only = generated[:, input_length:]
            text = self._processor.batch_decode(
                generated_only,
                skip_special_tokens=True,
            )[0]
            if type(text) is not str or not text:
                raise Phase5FormalQwenWorkerError(
                    f"{node_id} returned empty raw text"
                )
            return text.encode("utf-8")
        except Phase5FormalQwenWorkerError:
            raise
        except Exception as exc:
            raise Phase5FormalQwenWorkerError(
                f"{node_id} BF16 generation failed closed"
            ) from exc


def run_worker_protocol(*, model_root: Path) -> int:
    """Child entrypoint; stdout is strict IPC and stderr is stream events."""

    _offline_process()
    worker_id = f"phase5-formal-worker-{uuid.uuid4().hex}"
    generated_nodes: set[str] = set()
    try:
        load_raw = sys.stdin.buffer.readline()
        load = _formal._strict_json(load_raw.rstrip(b"\r\n"), "worker load")
        if (
            not isinstance(load, Mapping)
            or set(load) != {"protocol", "kind", "profile_b64"}
            or load["protocol"] != WORKER_PROTOCOL
            or load["kind"] != "load"
        ):
            raise Phase5FormalQwenWorkerError("formal worker load request drifted")
        profile = RemoteFreshIntegratedProfile.from_bytes(
            _decode_b64(load["profile_b64"], "formal worker profile")
        )
        _emit_event("load_started")
        backend = _Phase5FormalBackend(model_root=model_root, profile=profile)
        backend.load()
        loaded_facts = backend.loaded_facts
        if not isinstance(loaded_facts, Mapping):
            raise Phase5FormalQwenWorkerError("formal loaded facts are unavailable")
        _emit_event("load_completed")
        sys.stdout.buffer.write(
            _formal._canonical(
                {
                    "protocol": WORKER_PROTOCOL,
                    "kind": "loaded",
                    "worker_id": worker_id,
                    "worker_pid": os.getpid(),
                    "loaded_facts": dict(loaded_facts),
                }
            )
            + b"\n"
        )
        sys.stdout.buffer.flush()
        while True:
            raw = sys.stdin.buffer.readline()
            if not raw:
                return 3
            message = _formal._strict_json(
                raw.rstrip(b"\r\n"),
                "formal worker message",
            )
            if not isinstance(message, Mapping) or message.get(
                "protocol"
            ) != WORKER_PROTOCOL:
                raise Phase5FormalQwenWorkerError("formal worker protocol drifted")
            if message.get("kind") == "shutdown":
                sys.stdout.buffer.write(
                    _formal._canonical(
                        {
                            "protocol": WORKER_PROTOCOL,
                            "kind": "shutdown_ack",
                            "worker_id": worker_id,
                        }
                    )
                    + b"\n"
                )
                sys.stdout.buffer.flush()
                return 0
            if set(message) != {
                "protocol",
                "kind",
                "call_id",
                "node_id",
                "input_b64",
                "prompt_b64",
                "config_b64",
                "request_b64",
            } or message.get("kind") != "generate":
                raise Phase5FormalQwenWorkerError(
                    "formal worker generate request drifted"
                )
            node_id = message["node_id"]
            if (
                node_id not in _formal.NODE_ORDER
                or node_id in generated_nodes
                or not isinstance(message["call_id"], str)
            ):
                raise Phase5FormalQwenWorkerError(
                    "formal worker node call scope drifted"
                )
            generated_nodes.add(str(node_id))
            _emit_event("generation_started", node_id=str(node_id))
            generated = backend.generate_formal_stream(
                node_id=str(node_id),
                input_bytes=_decode_b64(message["input_b64"], "formal input"),
                prompt_bytes=_decode_b64(message["prompt_b64"], "formal prompt"),
                config_bytes=_decode_b64(message["config_b64"], "formal config"),
                request_bytes=_decode_b64(message["request_b64"], "formal request"),
                emit_delta=lambda delta: _emit_event(
                    "token_delta",
                    node_id=str(node_id),
                    delta=delta,
                ),
            )
            sys.stdout.buffer.write(
                _formal._canonical(
                    {
                        "protocol": WORKER_PROTOCOL,
                        "kind": "generation_result",
                        "call_id": message["call_id"],
                        "worker_id": worker_id,
                        "node_id": node_id,
                        "raw_b64": _b64(generated),
                    }
                )
                + b"\n"
            )
            sys.stdout.buffer.flush()
            _emit_event("generation_completed", node_id=str(node_id))
    except Exception as exc:
        _emit_event("worker_failed")
        sys.stderr.write(
            f"[Phase 5 formal worker] {type(exc).__name__}: {exc}\n"
        )
        sys.stderr.flush()
        return 2


class _StreamMirror:
    def __init__(self, target: object | None) -> None:
        self.target = target if target is not None else sys.stderr
        self.stderr_bytes = bytearray()
        self.token_bytes = bytearray()
        self.started_nodes: set[str] = set()
        self.completed_nodes: set[str] = set()
        self._callbacks: dict[str, Callable[[], None]] = {}
        self._lock = threading.Lock()

    def register_started_callback(
        self,
        node_id: str,
        callback: Callable[[], None],
    ) -> None:
        with self._lock:
            if node_id in self._callbacks:
                raise Phase5FormalQwenWorkerError(
                    "formal worker start callback already registered"
                )
            self._callbacks[node_id] = callback

    def feed(self, raw: bytes) -> None:
        with self._lock:
            self.stderr_bytes.extend(raw)
        try:
            event = _formal._strict_json(
                raw.rstrip(b"\r\n"),
                "formal worker stream event",
            )
        except Phase5FormalRunnerError:
            self.target.write(raw.decode("utf-8", errors="replace"))  # type: ignore[union-attr]
            self.target.flush()  # type: ignore[union-attr]
            return
        if not isinstance(event, Mapping) or event.get(
            "schema_version"
        ) != STREAM_SCHEMA_VERSION:
            self.target.write(raw.decode("utf-8", errors="replace"))  # type: ignore[union-attr]
            self.target.flush()  # type: ignore[union-attr]
            return
        node_id = event.get("node_id")
        event_name = event.get("event")
        delta = _decode_b64(
            event.get("delta_b64"),
            "formal stream delta",
            allow_empty=True,
        )
        if event_name == "generation_started":
            if node_id not in _formal.NODE_ORDER:
                raise Phase5FormalQwenWorkerError(
                    "formal generation start node drifted"
                )
            with self._lock:
                if str(node_id) in self.started_nodes:
                    raise Phase5FormalQwenWorkerError(
                        "formal generation start was emitted twice"
                    )
                self.started_nodes.add(str(node_id))
                callback = self._callbacks.pop(str(node_id), None)
            if callback is None:
                raise Phase5FormalQwenWorkerError(
                    "formal generation start has no parent callback"
                )
            callback()
        elif event_name == "generation_completed":
            if node_id not in _formal.NODE_ORDER:
                raise Phase5FormalQwenWorkerError(
                    "formal generation completion node drifted"
                )
            with self._lock:
                self.completed_nodes.add(str(node_id))
        elif event_name == "token_delta":
            if node_id not in _formal.NODE_ORDER:
                raise Phase5FormalQwenWorkerError("formal token node drifted")
            with self._lock:
                self.token_bytes.extend(delta)
            self.target.write(delta.decode("utf-8", errors="replace"))  # type: ignore[union-attr]
            self.target.flush()  # type: ignore[union-attr]
            return
        if delta:
            raise Phase5FormalQwenWorkerError(
                "non-token formal stream event contains delta"
            )
        self.target.write(f"\n[Phase 5] {event_name}\n")  # type: ignore[union-attr]
        self.target.flush()  # type: ignore[union-attr]


def _stdout_reader(
    stream: object,
    messages: queue.Queue[dict[str, object]],
) -> None:
    try:
        for line in stream:  # type: ignore[union-attr]
            raw = line.encode("utf-8")
            value = _formal._strict_json(
                raw.rstrip(b"\r\n"),
                "formal worker stdout",
            )
            if not isinstance(value, dict):
                raise Phase5FormalQwenWorkerError(
                    "formal worker stdout root is invalid"
                )
            messages.put(value)
    except Exception as exc:
        messages.put(
            {
                "protocol": WORKER_PROTOCOL,
                "kind": "reader_error",
                "error_type": type(exc).__name__,
            }
        )


def _stderr_reader(stream: object, mirror: _StreamMirror) -> None:
    for line in stream:  # type: ignore[union-attr]
        mirror.feed(line.encode("utf-8"))


class Phase5FormalQwenWorker:
    """Parent-supervised worker that loads once and serves F1-F4 for one case."""

    def __init__(
        self,
        *,
        model_root: Path,
        profile: RemoteFreshIntegratedProfile,
        console: object | None = None,
    ) -> None:
        if (
            not isinstance(model_root, Path)
            or not model_root.is_dir()
            or model_root.is_symlink()
        ):
            raise Phase5FormalQwenWorkerError("formal model root is invalid")
        profile.validate()
        executable, pythonpath = _local._supervised_worker_python_runtime()
        environment = dict(os.environ)
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "DO_NOT_TRACK": "1",
                "LANGSMITH_TRACING": "0",
                "LANGCHAIN_TRACING_V2": "0",
                "PYTHONUNBUFFERED": "1",
                "PYTHONPATH": pythonpath,
            }
        )
        self.process = subprocess.Popen(
            [
                executable,
                "-m",
                "req2web_runtime.phase5_formal_qwen_worker",
                "--worker",
                "--model-root",
                str(model_root),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=environment,
            start_new_session=(os.name != "nt"),
            creationflags=(
                (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
                if os.name == "nt"
                else 0
            ),
        )
        if (
            self.process.stdin is None
            or self.process.stdout is None
            or self.process.stderr is None
        ):
            self.process.terminate()
            self.process.wait(timeout=10)
            raise Phase5FormalQwenWorkerError("formal worker IPC is unavailable")
        self.worker_id = "pending"
        self.worker_pid: int | None = None
        self.loaded_facts: Mapping[str, object] | None = None
        self._profile = profile
        self._closed = False
        self._calls = {node_id: 0 for node_id in _formal.NODE_ORDER}
        self._messages: queue.Queue[dict[str, object]] = queue.Queue()
        self._mirror = _StreamMirror(console)
        self._stdout_thread = threading.Thread(
            target=_stdout_reader,
            args=(self.process.stdout, self._messages),
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=_stderr_reader,
            args=(self.process.stderr, self._mirror),
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        self._send(
            {
                "protocol": WORKER_PROTOCOL,
                "kind": "load",
                "profile_b64": _b64(profile.canonical_bytes()),
            }
        )
        loaded = self._receive(profile.timeout_seconds)
        if (
            loaded.get("kind") != "loaded"
            or loaded.get("protocol") != WORKER_PROTOCOL
            or not isinstance(loaded.get("worker_id"), str)
            or not isinstance(loaded.get("worker_pid"), int)
            or not isinstance(loaded.get("loaded_facts"), Mapping)
        ):
            self._force_teardown("load_failed")
            raise Phase5FormalQwenWorkerError("formal worker load failed closed")
        self.worker_id = str(loaded["worker_id"])
        self.worker_pid = int(loaded["worker_pid"])
        self.loaded_facts = copy.deepcopy(dict(loaded["loaded_facts"]))

    def _send(self, value: object) -> None:
        if self.process.stdin is None:
            raise Phase5FormalQwenWorkerError("formal worker stdin is unavailable")
        self.process.stdin.write(_formal._canonical(value).decode("utf-8") + "\n")
        self.process.stdin.flush()

    def _receive(self, timeout_seconds: int) -> dict[str, object]:
        try:
            return self._messages.get(timeout=timeout_seconds)
        except queue.Empty as exc:
            self._force_teardown("worker_timeout")
            raise Phase5FormalQwenWorkerError("formal worker timed out") from exc

    def bind_graph_state(self, state: Mapping[str, object]) -> None:
        del state
        if self._closed:
            raise Phase5FormalQwenWorkerError("formal worker is closed")

    def generate(
        self,
        *,
        node_id: str,
        input_bytes: bytes,
        prompt_bytes: bytes,
        config_bytes: bytes,
        request_bytes: bytes,
        on_generation_started: Callable[[], None],
    ) -> bytes:
        if self._closed or node_id not in _formal.NODE_ORDER:
            raise Phase5FormalQwenWorkerError("formal worker call is invalid")
        if self._calls[node_id] != 0:
            raise Phase5FormalQwenWorkerError("formal worker retry is prohibited")
        validate_phase5_formal_generation_artifacts(
            node_id=node_id,
            input_bytes=input_bytes,
            prompt_bytes=prompt_bytes,
            config_bytes=config_bytes,
            request_bytes=request_bytes,
            require_formal_action_receipt=True,
        )
        self._calls[node_id] = 1
        self._mirror.register_started_callback(node_id, on_generation_started)
        call_id = f"phase5-{node_id.lower()}-{uuid.uuid4().hex}"
        self._send(
            {
                "protocol": WORKER_PROTOCOL,
                "kind": "generate",
                "call_id": call_id,
                "node_id": node_id,
                "input_b64": _b64(input_bytes),
                "prompt_b64": _b64(prompt_bytes),
                "config_b64": _b64(config_bytes),
                "request_b64": _b64(request_bytes),
            }
        )
        response = self._receive(_formal.PER_NODE_TIMEOUT_SECONDS)
        if (
            response.get("kind") != "generation_result"
            or response.get("protocol") != WORKER_PROTOCOL
            or response.get("call_id") != call_id
            or response.get("worker_id") != self.worker_id
            or response.get("node_id") != node_id
        ):
            self._force_teardown("worker_failed")
            raise Phase5FormalQwenWorkerError(
                "formal worker generation response drifted"
            )
        return _decode_b64(response.get("raw_b64"), f"{node_id} raw")

    def _join_readers(self) -> tuple[bool, bool]:
        self._stdout_thread.join(timeout=5)
        self._stderr_thread.join(timeout=5)
        return not self._stdout_thread.is_alive(), not self._stderr_thread.is_alive()

    def _force_teardown(self, terminal_status: str) -> None:
        if self._closed:
            return
        try:
            if os.name != "nt":
                os.killpg(self.process.pid, signal.SIGTERM)
            else:
                self.process.terminate()
            self.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                if os.name != "nt":
                    os.killpg(self.process.pid, signal.SIGKILL)
                else:
                    self.process.kill()
                self.process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self._closed = True
        self._terminal_status = terminal_status
        self._stdout_joined, self._stderr_joined = self._join_readers()

    def close(self) -> Mapping[str, object]:
        if not self._closed:
            try:
                self._send({"protocol": WORKER_PROTOCOL, "kind": "shutdown"})
                response = self._receive(20)
                if (
                    response.get("kind") != "shutdown_ack"
                    or response.get("worker_id") != self.worker_id
                ):
                    raise Phase5FormalQwenWorkerError(
                        "formal worker shutdown acknowledgement drifted"
                    )
                self.process.wait(timeout=20)
                self._closed = True
                self._terminal_status = "normal_completed"
                self._stdout_joined, self._stderr_joined = self._join_readers()
            except Exception:
                self._force_teardown("worker_failed")
        return {
            "schema_version": "req2web.phase5.formal_worker_receipt.v1",
            "worker_id": self.worker_id,
            "worker_pid": self.worker_pid,
            "worker_exit_code": self.process.poll(),
            "worker_exit_verified": self.process.poll() is not None,
            "terminal_status": getattr(
                self,
                "_terminal_status",
                "worker_teardown_unverified",
            ),
            "generate_calls": dict(self._calls),
            "stdout_thread_joined": getattr(self, "_stdout_joined", False),
            "stderr_thread_joined": getattr(self, "_stderr_joined", False),
            "stderr_capture_completed": (
                self.process.poll() is not None
                and not self._stderr_thread.is_alive()
            ),
            "worker_stderr_sha256": _formal._sha(bytes(self._mirror.stderr_bytes)),
            "worker_stderr_byte_length": len(self._mirror.stderr_bytes),
            "streamed_token_byte_length": len(self._mirror.token_bytes),
            "model_loaded": self.loaded_facts is not None,
            "loaded_facts": (
                None
                if self.loaded_facts is None
                else copy.deepcopy(dict(self.loaded_facts))
            ),
        }


@dataclass(frozen=True)
class Phase5FormalQwenWorkerFactory:
    model_root: Path
    profile: RemoteFreshIntegratedProfile
    console: object | None = None

    def __call__(self, row: Mapping[str, object]) -> Phase5FormalQwenWorker:
        del row
        return Phase5FormalQwenWorker(
            model_root=self.model_root,
            profile=self.profile,
            console=self.console,
        )


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--model-root", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.worker:
        raise Phase5FormalQwenWorkerError("--worker is required")
    return run_worker_protocol(model_root=args.model_root.resolve(strict=True))


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))


__all__ = [
    "Phase5FormalQwenWorker",
    "Phase5FormalQwenWorkerError",
    "Phase5FormalQwenWorkerFactory",
    "STREAM_SCHEMA_VERSION",
    "WORKER_PROTOCOL",
    "validate_phase5_formal_generation_artifacts",
]
