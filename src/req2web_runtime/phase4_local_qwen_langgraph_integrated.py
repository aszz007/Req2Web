"""Run one canonical F1-F4 case on a bounded local Qwen GPU profile.

This module is an execution adapter for the existing
``Phase4RealModelGraphRuntime``.  It does not define another F1-F4 scheduler,
prompt, canonical-B adapter, registry, mapping, composition, assembler, or
delivery policy.  The low-GPU profile is intended for integration debugging;
it is not formal-quality evidence.
"""

from __future__ import annotations

import argparse
import base64
import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Callable, Mapping, Sequence
import uuid

from req2web_agent import AgentContextBundle, PROMPT_AUTHORITY_IDENTITY
from req2web_generation import RetrievalGuidance
from req2web_orchestration.phase4_graph import (
    NODE_ORDER,
    NO_CAPTURE_SHA256,
    REAL_MODEL_GRAPH_REVISION,
    REAL_MODEL_SOURCE_KIND,
    Phase4RealModelGraphRuntime,
    create_real_model_graph_state,
    make_identity,
    make_real_model_raw_capture,
    phase4_create_portable_authority_state,
    phase4_create_real_model_mapping,
    phase4_normalize_and_validate_f4_output,
    phase4_validate_node_output,
    validate_b_input,
)
from req2web_runtime.phase4_fresh_delivery import (
    PHASE4_FRESH_DELIVERY_POLICY_A07A_DIRECT_V1,
    build_phase4_actual_context_delivery_materials,
    run_phase4_fresh_delivery,
)
from req2web_runtime import phase4_remote_qwen_fresh_integrated as _shared
from req2web_runtime.phase4_local_qwen import (
    QWEN_MODEL_ID,
    QWEN_MODEL_REVISION,
    validate_model_inventory_metadata,
)


SCHEMA_PREFIX = "req2web.phase4.local_qwen_langgraph_integrated"
RUN_SCHEMA_VERSION = f"{SCHEMA_PREFIX}.run.v1"
PROFILE_SCHEMA_VERSION = f"{SCHEMA_PREFIX}.profile.v5"
PRE_CALL_SCHEMA_VERSION = f"{SCHEMA_PREFIX}.pre_call.v1"
ATTEMPT_SCHEMA_VERSION = f"{SCHEMA_PREFIX}.attempt.v1"
LEDGER_SCHEMA_VERSION = f"{SCHEMA_PREFIX}.call_ledger.v1"
SUPERVISOR_SCHEMA_VERSION = f"{SCHEMA_PREFIX}.supervisor.v1"
WORKER_PROTOCOL = f"{SCHEMA_PREFIX}.worker.v1"
ROOT_MARKER = ".req2web-phase4-local-qwen-langgraph-root"
LOW_GPU_PROFILE = "local_low_gpu_nf4"
INTEGRITY_GPU_PROFILE = "local_integrity_nf4"
HIGH_GPU_PROFILE = "high_gpu_bf16"
PROFILE_NAMES = (LOW_GPU_PROFILE, INTEGRITY_GPU_PROFILE, HIGH_GPU_PROFILE)
NODE_OUTPUT_TOKEN_CAPS = {
    "F1": 3_072,
    "F2": 2_048,
    "F3": 2_560,
    "F4": 2_048,
}
ZERO_SHA256 = "sha256:" + ("0" * 64)


class Phase4LocalQwenLangGraphError(ValueError):
    """Raised when the local canonical graph cannot continue safely."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Phase4LocalQwenLangGraphError(
            "local LangGraph value is not canonical JSON"
        ) from exc


def _identity(value: object, *, revision: str) -> dict[str, object]:
    raw = value if isinstance(value, bytes) else _canonical(value)
    return {
        "identity_kind": "raw_bytes" if isinstance(value, bytes) else "canonical_json",
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": revision,
    }


def _write_once(path: Path, raw: bytes) -> None:
    if not raw:
        raise Phase4LocalQwenLangGraphError("cannot write an empty artifact")
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
            raise Phase4LocalQwenLangGraphError(
                f"write-once artifact drifted: {path.name}"
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, value: object) -> None:
    _write_once(path, _canonical(value))


def _read_json(path: Path, name: str) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise Phase4LocalQwenLangGraphError(f"{name} is unavailable")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase4LocalQwenLangGraphError(f"{name} is not readable JSON") from exc
    if not isinstance(value, dict):
        raise Phase4LocalQwenLangGraphError(f"{name} must be an object")
    return value


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(value: object, name: str) -> bytes:
    if not isinstance(value, str):
        raise Phase4LocalQwenLangGraphError(f"{name} is not base64 text")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise Phase4LocalQwenLangGraphError(f"{name} is invalid base64") from exc


def _safe_root(path: Path) -> Path:
    root = Path(path).resolve(strict=False)
    if root.exists():
        if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
            raise Phase4LocalQwenLangGraphError(
                "local LangGraph result root must be new or empty"
            )
    else:
        root.mkdir(parents=True, exist_ok=False)
    _write_once(root / ROOT_MARKER, (ROOT_MARKER + "\n").encode("ascii"))
    return root


@dataclass(frozen=True)
class LocalLangGraphProfile:
    """One exact local integration, local integrity, or quality profile."""

    profile_name: str
    quantization: str
    dtype: str
    compute_dtype: str
    min_total_vram_bytes: int
    min_free_vram_bytes: int
    max_input_tokens: int
    f4_cache_implementation: str
    f4_prefill_chunk_size: int | None
    f3_required_key_order_decoding: bool
    f4_required_empty_refs_decoding: bool
    timeout_seconds: int
    formal_quality_eligible: bool

    def validate(self) -> None:
        if self.profile_name not in PROFILE_NAMES:
            raise Phase4LocalQwenLangGraphError("unknown local LangGraph profile")
        if (
            self.dtype != "bfloat16"
            or self.compute_dtype != "bfloat16"
            or self.max_input_tokens < 4_096
            or self.timeout_seconds < 60
        ):
            raise Phase4LocalQwenLangGraphError("profile boundary drifted")
        if self.profile_name == LOW_GPU_PROFILE:
            if (
                self.quantization != "nf4_double_quant"
                or self.min_total_vram_bytes != 8_000_000_000
                or self.min_free_vram_bytes < 5_000_000_000
                or self.max_input_tokens != 12_288
                or self.f4_cache_implementation != "offloaded"
                or self.f4_prefill_chunk_size is not None
                or self.f3_required_key_order_decoding is not False
                or self.f4_required_empty_refs_decoding is not False
                or self.timeout_seconds != 3_600
                or self.formal_quality_eligible is not False
            ):
                raise Phase4LocalQwenLangGraphError("low-GPU profile drifted")
        elif self.profile_name == INTEGRITY_GPU_PROFILE:
            if (
                self.quantization != "nf4_single_quant"
                or self.min_total_vram_bytes != 8_000_000_000
                or self.min_free_vram_bytes < 6_000_000_000
                or self.max_input_tokens != 12_288
                or self.f4_cache_implementation != "offloaded"
                or self.f4_prefill_chunk_size is not None
                or self.f3_required_key_order_decoding is not True
                or self.f4_required_empty_refs_decoding is not True
                or self.timeout_seconds != 7_200
                or self.formal_quality_eligible is not False
            ):
                raise Phase4LocalQwenLangGraphError(
                    "local integrity profile drifted"
                )
        elif (
            self.quantization != "none"
            or self.min_total_vram_bytes < 30_000_000_000
            or self.min_free_vram_bytes < 24_000_000_000
            or self.f4_cache_implementation != "default_dynamic"
            or self.f4_prefill_chunk_size is not None
            or self.f3_required_key_order_decoding is not False
            or self.f4_required_empty_refs_decoding is not False
            or self.timeout_seconds != 1_200
            or self.formal_quality_eligible is not True
        ):
            raise Phase4LocalQwenLangGraphError("high-GPU profile drifted")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "profile_name": self.profile_name,
            "model_id": QWEN_MODEL_ID,
            "model_revision": QWEN_MODEL_REVISION,
            "quantization": self.quantization,
            "dtype": self.dtype,
            "compute_dtype": self.compute_dtype,
            "device_index": 0,
            "cpu_offload": False,
            "default_kv_cache_implementation": "default_dynamic",
            "f4_kv_cache_implementation": self.f4_cache_implementation,
            "f4_kv_cache_cpu_offload": self.f4_cache_implementation == "offloaded",
            "f4_prefill_chunk_size": self.f4_prefill_chunk_size,
            "f3_required_key_order_decoding": (
                self.f3_required_key_order_decoding
            ),
            "f4_required_empty_refs_decoding": (
                self.f4_required_empty_refs_decoding
            ),
            "min_total_vram_bytes": self.min_total_vram_bytes,
            "min_free_vram_bytes": self.min_free_vram_bytes,
            "max_input_tokens": self.max_input_tokens,
            "node_output_token_caps": copy.deepcopy(NODE_OUTPUT_TOKEN_CAPS),
            "timeout_seconds": self.timeout_seconds,
            "local_files_only": True,
            "offline": True,
            "network": False,
            "telemetry": False,
            "tracing": False,
            "do_sample": False,
            "seed": 0,
            "automatic_retry_limit": 0,
            "input_truncation": False,
            "output_truncation": False,
            "complete_json_required": True,
            "attention_implementation": "req2web_qwen35_memory_efficient_sdpa",
            "cuda_allocator_config": "expandable_segments:True",
            "formal_quality_eligible": self.formal_quality_eligible,
        }


def local_langgraph_profile(profile_name: str) -> LocalLangGraphProfile:
    if profile_name == LOW_GPU_PROFILE:
        profile = LocalLangGraphProfile(
            profile_name=profile_name,
            quantization="nf4_double_quant",
            dtype="bfloat16",
            compute_dtype="bfloat16",
            min_total_vram_bytes=8_000_000_000,
            min_free_vram_bytes=5_500_000_000,
            max_input_tokens=12_288,
            f4_cache_implementation="offloaded",
            f4_prefill_chunk_size=None,
            f3_required_key_order_decoding=False,
            f4_required_empty_refs_decoding=False,
            timeout_seconds=3_600,
            formal_quality_eligible=False,
        )
    elif profile_name == INTEGRITY_GPU_PROFILE:
        profile = LocalLangGraphProfile(
            profile_name=profile_name,
            quantization="nf4_single_quant",
            dtype="bfloat16",
            compute_dtype="bfloat16",
            min_total_vram_bytes=8_000_000_000,
            min_free_vram_bytes=6_250_000_000,
            max_input_tokens=12_288,
            f4_cache_implementation="offloaded",
            f4_prefill_chunk_size=None,
            f3_required_key_order_decoding=True,
            f4_required_empty_refs_decoding=True,
            timeout_seconds=7_200,
            formal_quality_eligible=False,
        )
    elif profile_name == HIGH_GPU_PROFILE:
        profile = LocalLangGraphProfile(
            profile_name=profile_name,
            quantization="none",
            dtype="bfloat16",
            compute_dtype="bfloat16",
            min_total_vram_bytes=30_000_000_000,
            min_free_vram_bytes=24_000_000_000,
            max_input_tokens=32_768,
            f4_cache_implementation="default_dynamic",
            f4_prefill_chunk_size=None,
            f3_required_key_order_decoding=False,
            f4_required_empty_refs_decoding=False,
            timeout_seconds=1_200,
            formal_quality_eligible=True,
        )
    else:
        raise Phase4LocalQwenLangGraphError("unknown local LangGraph profile")
    profile.validate()
    return profile


def runtime_capabilities() -> dict[str, object]:
    return {
        "status": "implemented_explicit_local_model_action",
        "workflow_runtime": REAL_MODEL_GRAPH_REVISION,
        "prompt_authority_identity": copy.deepcopy(PROMPT_AUTHORITY_IDENTITY),
        "model_id": QWEN_MODEL_ID,
        "profiles": [LOW_GPU_PROFILE, INTEGRITY_GPU_PROFILE, HIGH_GPU_PROFILE],
        "one_call_per_node": True,
        "automatic_retry": False,
        "b_aux_consumed_by_f1_f4": False,
        "low_gpu_formal_quality_eligible": False,
        "low_gpu_complete_context_without_truncation": True,
    }


def _generation_memory_kwargs(
    profile: LocalLangGraphProfile, node_id: str
) -> dict[str, object]:
    """Return profile-owned generation memory controls without changing content."""

    profile.validate()
    if node_id not in NODE_ORDER:
        raise Phase4LocalQwenLangGraphError("unknown node memory profile")
    if node_id == "F4" and profile.f4_cache_implementation == "offloaded":
        return {"cache_implementation": "offloaded"}
    return {}


class _RequiredEmptyRefsLogitsProcessor:
    """Enforce the prompt-owned ``refs: []`` constant during local decoding.

    The processor is deliberately narrow: it activates only after the model has
    already emitted an exact ``\"refs\"`` member name and only while the compact
    ``:[]`` constant is incomplete. It cannot change descriptions, use-case
    references, state choices, IDs, array order, or any completed raw bytes.
    """

    _KEY = '"refs"'
    _TARGET = ":[]"

    def __init__(self, *, tokenizer: object, prompt_length: int) -> None:
        if prompt_length < 0:
            raise Phase4LocalQwenLangGraphError(
                "empty-refs decoding prompt length is invalid"
            )
        self.tokenizer = tokenizer
        self.prompt_length = prompt_length
        self.application_count = 0
        vocabulary = getattr(tokenizer, "get_vocab", lambda: {})()
        if not isinstance(vocabulary, Mapping) or not vocabulary:
            raise Phase4LocalQwenLangGraphError(
                "empty-refs decoding tokenizer vocabulary is unavailable"
            )
        self._token_text: dict[int, str] = {}
        for token_id in vocabulary.values():
            if not isinstance(token_id, int) or token_id < 0:
                continue
            try:
                decoded = tokenizer.decode(
                    [token_id],
                    skip_special_tokens=False,
                    clean_up_tokenization_spaces=False,
                )
            except (TypeError, ValueError):
                continue
            if isinstance(decoded, str):
                self._token_text[token_id] = decoded
        if not self._token_text:
            raise Phase4LocalQwenLangGraphError(
                "empty-refs decoding tokenizer vocabulary could not be decoded"
            )

    @staticmethod
    def _compact(value: str) -> str:
        return "".join(value.split())

    def _incomplete_suffix(self, generated_text: str) -> str | None:
        position = generated_text.rfind(self._KEY)
        if position < 0:
            return None
        compact = self._compact(generated_text[position + len(self._KEY) :])
        if compact.startswith(self._TARGET):
            return None
        if self._TARGET.startswith(compact):
            return compact
        return None

    def __call__(self, input_ids: object, scores: object) -> object:
        sequence = input_ids[0, self.prompt_length :].tolist()
        generated_text = self.tokenizer.decode(
            sequence,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        if not isinstance(generated_text, str):
            return scores
        compact = self._incomplete_suffix(generated_text)
        if compact is None:
            return scores
        allowed: list[int] = []
        for token_id, token_text in self._token_text.items():
            candidate = compact + self._compact(token_text)
            if self._TARGET.startswith(candidate) or candidate.startswith(
                self._TARGET
            ):
                allowed.append(token_id)
        if not allowed:
            raise Phase4LocalQwenLangGraphError(
                "empty-refs decoding has no legal next token"
            )
        constrained = scores.new_full(scores.shape, float("-inf"))
        constrained[:, allowed] = scores[:, allowed]
        self.application_count += 1
        return constrained


class _F3RequiredKeyOrderLogitsProcessor:
    """Keep F3 object keys in the exact prompt-owned order during decoding.

    The processor acts only while an interaction object's next key is being
    written. It cannot choose or alter any interaction value, row, state,
    trigger, action, feedback, or completed raw byte.
    """

    _KEY_ORDER = (
        "local_id",
        "entity_type",
        "trigger_component_local_id",
        "source_state_local_id",
        "action",
        "target_state_local_id",
        "user_feedback",
        "refs",
    )

    def __init__(self, *, tokenizer: object, prompt_length: int) -> None:
        if prompt_length < 0:
            raise Phase4LocalQwenLangGraphError(
                "F3 key-order decoding prompt length is invalid"
            )
        self.tokenizer = tokenizer
        self.prompt_length = prompt_length
        self.application_count = 0
        vocabulary = getattr(tokenizer, "get_vocab", lambda: {})()
        if not isinstance(vocabulary, Mapping) or not vocabulary:
            raise Phase4LocalQwenLangGraphError(
                "F3 key-order decoding tokenizer vocabulary is unavailable"
            )
        self._token_text: dict[int, str] = {}
        for token_id in vocabulary.values():
            if not isinstance(token_id, int) or token_id < 0:
                continue
            try:
                decoded = tokenizer.decode(
                    [token_id],
                    skip_special_tokens=False,
                    clean_up_tokenization_spaces=False,
                )
            except (TypeError, ValueError):
                continue
            if isinstance(decoded, str):
                self._token_text[token_id] = decoded
        if not self._token_text:
            raise Phase4LocalQwenLangGraphError(
                "F3 key-order decoding tokenizer vocabulary could not be decoded"
            )

    @staticmethod
    def _compact(value: str) -> str:
        return "".join(value.split())

    @staticmethod
    def _open_interaction_segment(generated_text: str) -> str | None:
        in_string = False
        escaped = False
        object_depth = 0
        interaction_start: int | None = None
        for position, character in enumerate(generated_text):
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character == "{":
                object_depth += 1
                if object_depth == 2:
                    interaction_start = position + 1
            elif character == "}":
                if object_depth == 2:
                    interaction_start = None
                object_depth -= 1
        if object_depth == 2 and interaction_start is not None:
            return generated_text[interaction_start:]
        return None

    @classmethod
    def _pending_key_prefix(cls, generated_text: str) -> tuple[str, str] | None:
        segment = cls._open_interaction_segment(generated_text)
        if segment is None:
            return None
        in_string = False
        escaped = False
        array_depth = 0
        object_depth = 0
        last_field_start = 0
        field_index = 0
        has_top_level_colon = False
        for position, character in enumerate(segment):
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character == "[":
                array_depth += 1
            elif character == "]":
                array_depth -= 1
            elif character == "{":
                object_depth += 1
            elif character == "}":
                object_depth -= 1
            elif array_depth == 0 and object_depth == 0:
                if character == ":":
                    has_top_level_colon = True
                elif character == ",":
                    field_index += 1
                    last_field_start = position + 1
                    has_top_level_colon = False
        if has_top_level_colon or field_index >= len(cls._KEY_ORDER):
            return None
        prefix = cls._compact(segment[last_field_start:])
        expected = json.dumps(cls._KEY_ORDER[field_index]) + ":"
        if expected.startswith(prefix):
            return prefix, expected
        return None

    def __call__(self, input_ids: object, scores: object) -> object:
        sequence = input_ids[0, self.prompt_length :].tolist()
        generated_text = self.tokenizer.decode(
            sequence,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        if not isinstance(generated_text, str):
            return scores
        pending = self._pending_key_prefix(generated_text)
        if pending is None:
            return scores
        prefix, expected = pending
        allowed: list[int] = []
        for token_id, token_text in self._token_text.items():
            candidate = prefix + self._compact(token_text)
            if expected.startswith(candidate) or candidate.startswith(expected):
                allowed.append(token_id)
        if not allowed:
            raise Phase4LocalQwenLangGraphError(
                "F3 key-order decoding has no legal next token"
            )
        constrained = scores.new_full(scores.shape, float("-inf"))
        constrained[:, allowed] = scores[:, allowed]
        self.application_count += 1
        return constrained


class _Worker:
    """Persistent isolated model process used by the graph node executor."""

    def __init__(
        self,
        *,
        model_root: Path,
        integrity_evidence: Path,
        result_root: Path,
        profile: LocalLangGraphProfile,
        console: object | None,
    ) -> None:
        self.profile = profile
        self.result_root = result_root
        self.console = console or sys.stderr
        self.calls = {node_id: 0 for node_id in NODE_ORDER}
        self._messages: queue.Queue[dict[str, object]] = queue.Queue()
        self._stderr_chunks: list[bytes] = []
        self._closed = False
        environment = os.environ.copy()
        src_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = (
            src_root
            if not environment.get("PYTHONPATH")
            else src_root + os.pathsep + environment["PYTHONPATH"]
        )
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "DO_NOT_TRACK": "1",
                "LANGSMITH_TRACING": "0",
                "LANGCHAIN_TRACING_V2": "0",
                "CUDA_VISIBLE_DEVICES": "0",
                "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
                "PYTHONUNBUFFERED": "1",
            }
        )
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "req2web_runtime.phase4_local_qwen_langgraph_integrated",
                "_worker",
                "--model-root",
                str(model_root),
                "--integrity-evidence",
                str(integrity_evidence),
                "--result-root",
                str(result_root),
                "--profile",
                profile.profile_name,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=(os.name != "nt"),
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            ),
        )
        if self.process.stdin is None or self.process.stdout is None or self.process.stderr is None:
            self._terminate()
            raise Phase4LocalQwenLangGraphError("local worker IPC is unavailable")
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            name="req2web-local-langgraph-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            name="req2web-local-langgraph-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        loaded = self._receive(600)
        if loaded.get("kind") != "loaded" or loaded.get("protocol") != WORKER_PROTOCOL:
            self._terminate()
            raise Phase4LocalQwenLangGraphError("local worker load failed closed")
        self.worker_id = str(loaded["worker_id"])
        self.loaded_facts = copy.deepcopy(dict(loaded["loaded_facts"]))

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for raw in iter(self.process.stdout.readline, b""):
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                value = {"kind": "protocol_error"}
            self._messages.put(value if isinstance(value, dict) else {"kind": "protocol_error"})

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for raw in iter(self.process.stderr.readline, b""):
            self._stderr_chunks.append(raw)
            try:
                self.console.write(raw.decode("utf-8", errors="replace"))  # type: ignore[union-attr]
                self.console.flush()  # type: ignore[union-attr]
            except Exception:
                pass

    def _send(self, value: Mapping[str, object]) -> None:
        if self._closed or self.process.stdin is None:
            raise Phase4LocalQwenLangGraphError("local worker is closed")
        self.process.stdin.write(_canonical(value) + b"\n")
        self.process.stdin.flush()

    def _receive(self, timeout_seconds: int) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                value = self._messages.get(timeout=min(1.0, deadline - time.monotonic()))
            except queue.Empty:
                if self.process.poll() is not None:
                    break
                continue
            if value.get("kind") == "protocol_error":
                break
            return value
        self._terminate()
        raise Phase4LocalQwenLangGraphError("local worker timed out or failed")

    def generate(
        self,
        *,
        node_id: str,
        input_bytes: bytes,
        prompt_bytes: bytes,
        attempt_root: Path,
    ) -> tuple[bytes, dict[str, object]]:
        if node_id not in NODE_ORDER or self.calls[node_id] != 0:
            raise Phase4LocalQwenLangGraphError("local node call cap is exhausted")
        self.calls[node_id] = 1
        call_id = f"local-call-{uuid.uuid4().hex}"
        self._send(
            {
                "protocol": WORKER_PROTOCOL,
                "kind": "generate",
                "call_id": call_id,
                "node_id": node_id,
                "input_b64": _b64(input_bytes),
                "prompt_b64": _b64(prompt_bytes),
                "attempt_root": str(attempt_root),
            }
        )
        value = self._receive(self.profile.timeout_seconds)
        if (
            value.get("protocol") != WORKER_PROTOCOL
            or value.get("call_id") != call_id
            or value.get("node_id") != node_id
            or value.get("kind") not in {"generation_result", "generation_error"}
        ):
            raise Phase4LocalQwenLangGraphError("local worker response drifted")
        if value["kind"] == "generation_error":
            raise Phase4LocalQwenLangGraphError(
                str(value.get("message_code", "local_generation_failed"))
            )
        raw_path = attempt_root / "raw_response.bin"
        raw = raw_path.read_bytes()
        if _identity(raw, revision=f"{SCHEMA_PREFIX}.raw.v1") != value.get("raw_identity"):
            raise Phase4LocalQwenLangGraphError("local raw capture identity drifted")
        metrics = value.get("metrics")
        if not isinstance(metrics, Mapping):
            raise Phase4LocalQwenLangGraphError("local generation metrics are absent")
        return raw, copy.deepcopy(dict(metrics))

    def _terminate(self) -> None:
        if getattr(self, "_closed", False):
            return
        process = getattr(self, "process", None)
        if process is not None and process.poll() is None:
            try:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        self._closed = True

    def close(self) -> dict[str, object]:
        if not self._closed and self.process.poll() is None:
            try:
                self._send({"protocol": WORKER_PROTOCOL, "kind": "shutdown"})
                value = self._receive(30)
                if value.get("kind") != "shutdown_ack":
                    raise Phase4LocalQwenLangGraphError("shutdown acknowledgement drifted")
                self.process.wait(timeout=30)
                self._closed = True
            except (OSError, subprocess.SubprocessError, Phase4LocalQwenLangGraphError):
                self._terminate()
        stderr = b"".join(self._stderr_chunks)
        if stderr:
            _write_once(self.result_root / "worker_stderr.bin", stderr)
        return {
            "worker_id": getattr(self, "worker_id", None),
            "worker_pid": self.process.pid,
            "worker_exit_code": self.process.poll(),
            "worker_exit_verified": self.process.poll() is not None,
            "generate_calls": copy.deepcopy(self.calls),
            "stderr_identity": (
                None if not stderr else _identity(stderr, revision=f"{SCHEMA_PREFIX}.stderr.v1")
            ),
        }


def _not_formed_raw_capture() -> dict[str, object]:
    return {
        "state": "not_formed",
        "byte_length": 0,
        "sha256": NO_CAPTURE_SHA256,
        "source_kind": REAL_MODEL_SOURCE_KIND,
    }


def _local_config(node_id: str, profile: LocalLangGraphProfile) -> dict[str, object]:
    return {
        "schema_version": f"{SCHEMA_PREFIX}.config.v1",
        "node_id": node_id,
        "profile": profile.to_dict(),
        "max_new_tokens": NODE_OUTPUT_TOKEN_CAPS[node_id],
        "automatic_retry_count": 0,
        "input_truncation": False,
        "output_truncation": False,
        "complete_json_required": True,
    }


def _local_request(
    *, run_id: str, case_id: str, request_id: str, node_id: str
) -> dict[str, object]:
    return {
        "schema_version": f"{SCHEMA_PREFIX}.request.v1",
        "run_id": run_id,
        "case_id": case_id,
        "request_id": request_id,
        "node_id": node_id,
        "source_kind": "local_qwen_canonical_langgraph",
        "generate_call_cap": 1,
        "automatic_retry_count": 0,
    }


def _require_actual_context_binding(
    *,
    b_input: Mapping[str, object],
    context: AgentContextBundle,
    guidance: RetrievalGuidance,
) -> None:
    context.validate()
    guidance.validate()
    expected = {
        "requirement": context.original_requirement,
        "requirement_summary": context.requirement_summary,
        "target_device": context.target_device,
        "task_type": context.task_type,
        "constraints": context.constraints,
        "use_cases": [
            {
                "use_case_id": item.use_case_id,
                "title": item.title,
                "actor": item.actor,
                "goal": item.goal,
                "expected_outcome": item.expected_outcome,
            }
            for item in context.use_cases
        ],
    }
    if any(_canonical(b_input[key]) != _canonical(value) for key, value in expected.items()):
        raise Phase4LocalQwenLangGraphError(
            "canonical B differs from the actual upstream AgentContext"
        )


def run_phase4_local_qwen_langgraph_integrated(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    b_input: Mapping[str, object],
    upstream_context: AgentContextBundle,
    upstream_guidance: RetrievalGuidance,
    upstream_binding: Mapping[str, object],
    confirm_one_local_langgraph_run: bool,
    profile_name: str = LOW_GPU_PROFILE,
    run_id: str | None = None,
    b_aux_sidecar: Mapping[str, object] | None = None,
    console: object | None = None,
    progress_callback: Callable[
        [str, str, Mapping[str, object] | None], None
    ]
    | None = None,
    _raw_node_generator: Callable[[str, Mapping[str, object]], bytes] | None = None,
) -> dict[str, object]:
    """Execute one case through the sole formal graph on a local GPU profile."""

    if confirm_one_local_langgraph_run is not True:
        raise Phase4LocalQwenLangGraphError("explicit local model confirmation is required")
    selected_b = copy.deepcopy(validate_b_input(copy.deepcopy(dict(b_input))))
    _require_actual_context_binding(
        b_input=selected_b,
        context=upstream_context,
        guidance=upstream_guidance,
    )
    profile = local_langgraph_profile(profile_name)
    model_root = Path(model_root).resolve(strict=True)
    integrity_evidence = Path(integrity_evidence).resolve(strict=True)
    if model_root.is_symlink() or not model_root.is_dir():
        raise Phase4LocalQwenLangGraphError("model root is invalid")
    if integrity_evidence.is_symlink() or not integrity_evidence.is_file():
        raise Phase4LocalQwenLangGraphError("integrity evidence is invalid")
    root = _safe_root(result_root)
    selected_run_id = run_id or f"local-langgraph-{uuid.uuid4().hex[:16]}"
    inventory = validate_model_inventory_metadata(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        allow_relocated_model_root=True,
    )
    portable_state = phase4_create_portable_authority_state(selected_b)
    materials = build_phase4_actual_context_delivery_materials(
        graph_state=portable_state,
        context=upstream_context,
        guidance=upstream_guidance,
        material_root=root / "graph-bound-delivery-materials",
    )
    materials.validate()
    sidecar_binding = (
        {
            "status": "absent_not_requested",
            "identity": None,
            "canonical_b_writeback": False,
            "f1_f4_input": False,
        }
        if b_aux_sidecar is None
        else {
            "status": "available_advisory_not_consumed",
            "identity": _identity(
                copy.deepcopy(dict(b_aux_sidecar)),
                revision="req2web.semantic_requirement_assist.b_aux.v1",
            ),
            "canonical_b_writeback": False,
            "f1_f4_input": False,
        }
    )
    if b_aux_sidecar is not None:
        _write_json(root / "b_aux_sidecar.json", copy.deepcopy(dict(b_aux_sidecar)))
    preflight = {
        "schema_version": f"{RUN_SCHEMA_VERSION}.preflight.v1",
        "run_id": selected_run_id,
        "case_id": selected_b["case_id"],
        "request_id": selected_b["request_id"],
        "workflow_runtime": REAL_MODEL_GRAPH_REVISION,
        "prompt_authority_identity": copy.deepcopy(PROMPT_AUTHORITY_IDENTITY),
        "profile": profile.to_dict(),
        "model_inventory_identity": inventory["inventory_identity"],
        "upstream_binding": copy.deepcopy(dict(upstream_binding)),
        "delivery_materials_binding": copy.deepcopy(dict(materials.binding)),
        "b_aux_sidecar": sidecar_binding,
        "one_call_per_node": True,
        "automatic_retry": False,
        "manual_f1_f4_loop_used": False,
        "formal_quality_claimed": False,
    }
    _write_json(root / "b_input.json", selected_b)
    _write_json(root / "local_profile.json", profile.to_dict())
    _write_json(root / "model_inventory.json", inventory)
    _write_json(root / "preflight_manifest.json", preflight)
    attempts: dict[str, dict[str, object]] = {}
    worker: _Worker | None = None
    graph_result: dict[str, object] | None = None
    direct_acceptance_receipt: dict[str, object] | None = None

    if _raw_node_generator is None:
        worker = _Worker(
            model_root=model_root,
            integrity_evidence=integrity_evidence,
            result_root=root,
            profile=profile,
            console=console,
        )
        _write_json(
            root / "load_receipt.json",
            {
                "schema_version": f"{RUN_SCHEMA_VERSION}.load_receipt.v1",
                "run_id": selected_run_id,
                "profile": profile.to_dict(),
                "loaded_facts": worker.loaded_facts,
                "model_loaded": True,
            },
        )
        if progress_callback is not None:
            progress_callback("runtime", "model_loaded", None)

    def node_executor(
        node_id: str,
        authority_state: Mapping[str, object],
        authority_projection: Mapping[str, object],
    ) -> Mapping[str, object]:
        nonlocal direct_acceptance_receipt
        if node_id == "F4":
            mapping = phase4_create_real_model_mapping(authority_state)
            _write_json(root / "mapping.json", mapping)
        input_bytes = _shared._node_input(
            node_id=node_id,
            b_input=selected_b,
            state=authority_state,
            authority_projection=authority_projection,
        )
        prompt_bytes = _shared._node_prompt(
            node_id=node_id,
            input_bytes=input_bytes,
            prompt_revision=_shared.P4_05_FULL_DIRECT_PROMPT_REVISION,
        )
        config = _local_config(node_id, profile)
        request = _local_request(
            run_id=selected_run_id,
            case_id=str(selected_b["case_id"]),
            request_id=str(selected_b["request_id"]),
            node_id=node_id,
        )
        attempt_root = root / "attempts" / node_id
        _write_once(attempt_root / "input.json", input_bytes)
        _write_once(attempt_root / "prompt.json", prompt_bytes)
        _write_json(attempt_root / "config.json", config)
        _write_json(attempt_root / "request.json", request)
        pre_call = {
            "schema_version": PRE_CALL_SCHEMA_VERSION,
            "run_id": selected_run_id,
            "case_id": selected_b["case_id"],
            "request_id": selected_b["request_id"],
            "node_id": node_id,
            "input_identity": _identity(input_bytes, revision=_shared.P4_05_INPUT_SCHEMA_VERSION),
            "prompt_identity": _identity(prompt_bytes, revision=str(PROMPT_AUTHORITY_IDENTITY["revision"])),
            "config_identity": _identity(config, revision=f"{SCHEMA_PREFIX}.config.v1"),
            "request_identity": _identity(request, revision=f"{SCHEMA_PREFIX}.request.v1"),
            "generate_call_cap": 1,
            "automatic_retry_count": 0,
            "raw_first": True,
        }
        _write_json(attempt_root / "pre_call_record.json", pre_call)
        if progress_callback is not None:
            progress_callback(node_id, "preparing_generation", None)
        raw: bytes | None = None
        metrics: dict[str, object] | None = None
        output: dict[str, object] | None = None
        generate_started = False
        try:
            if _raw_node_generator is not None:
                raw = _raw_node_generator(node_id, authority_state)
                if not isinstance(raw, bytes) or not raw:
                    raise Phase4LocalQwenLangGraphError("test generator returned no bytes")
                generate_started = True
                _write_once(attempt_root / "generation_started.json", _canonical({"call_count": 1}))
                _write_once(attempt_root / "raw_response.bin", raw)
                metrics = {
                    "test_only": True,
                    "input_token_length": None,
                    "output_token_length": None,
                    "cuda_peak_allocated_bytes": 0,
                    "cuda_peak_reserved_bytes": 0,
                }
            else:
                assert worker is not None
                raw, metrics = worker.generate(
                    node_id=node_id,
                    input_bytes=input_bytes,
                    prompt_bytes=prompt_bytes,
                    attempt_root=attempt_root,
                )
                generate_started = (attempt_root / "generation_started.json").is_file()
            parsed = _shared._parse_model_json(raw, f"{node_id} raw response")
            if not isinstance(parsed, Mapping):
                raise Phase4LocalQwenLangGraphError("model output is not an object")
            output = copy.deepcopy(dict(parsed))
            raw_model_contract_success = True
            if node_id == "F4":
                output, normalization = phase4_normalize_and_validate_f4_output(
                    raw_bytes=raw,
                    output=output,
                    state=authority_state,
                )
                raw_model_contract_success = bool(normalization["raw_model_contract_success"])
                _write_json(attempt_root / "normalization_receipt.json", normalization)
                _write_json(root / "core_f4_normalization_receipt.json", normalization)
                direct_acceptance_receipt = _shared._f4_direct_acceptance_policy_receipt(
                    input_bytes=input_bytes,
                    output=output,
                    raw_bytes=raw,
                )
                _write_json(
                    root / "f4_direct_acceptance_policy_receipt.json",
                    direct_acceptance_receipt,
                )
            else:
                phase4_validate_node_output(node_id, output, authority_state)
            _write_json(attempt_root / "validated_node_output.json", output)
            attempt = {
                "schema_version": ATTEMPT_SCHEMA_VERSION,
                "run_id": selected_run_id,
                "case_id": selected_b["case_id"],
                "request_id": selected_b["request_id"],
                "node_id": node_id,
                "status": "validated",
                "generate_started": True,
                "generate_call_count": 1,
                "automatic_retry_count": 0,
                "raw_identity": _identity(raw, revision=f"{SCHEMA_PREFIX}.raw.v1"),
                "metrics": metrics,
                "failure": None,
            }
            attempts[node_id] = attempt
            _write_json(attempt_root / "attempt_result.json", attempt)
            if progress_callback is not None:
                progress_callback(node_id, "validated", attempt)
            return {
                "schema_version": "req2web.phase4.real_model_node_execution.v1",
                "node_id": node_id,
                "status": "validated",
                "source_kind": REAL_MODEL_SOURCE_KIND,
                "generate_call_count": 1,
                "raw_capture": make_real_model_raw_capture(raw),
                "attempt_identity": make_identity(attempt, revision=ATTEMPT_SCHEMA_VERSION),
                "output": output,
                "raw_model_contract_success": raw_model_contract_success,
                "normalized_node_contract_success": True,
                "failure": None,
            }
        except Exception as exc:
            generate_started = generate_started or (attempt_root / "generation_started.json").is_file()
            if raw is None and (attempt_root / "raw_response.bin").is_file():
                raw = (attempt_root / "raw_response.bin").read_bytes()
            failure = {
                "failure_code": "local_model_node_failed_closed",
                "failure_stage": node_id,
                "retry_allowed": False,
                "fallback_allowed": False,
                "message_code": (
                    f"{type(exc).__name__}: {exc}; output_keys="
                    f"{None if output is None else sorted(output)}"
                ),
            }
            attempt = {
                "schema_version": ATTEMPT_SCHEMA_VERSION,
                "run_id": selected_run_id,
                "case_id": selected_b["case_id"],
                "request_id": selected_b["request_id"],
                "node_id": node_id,
                "status": "failed_closed",
                "generate_started": generate_started,
                "generate_call_count": int(generate_started),
                "automatic_retry_count": 0,
                "raw_identity": (
                    None if raw is None else _identity(raw, revision=f"{SCHEMA_PREFIX}.raw.v1")
                ),
                "metrics": metrics,
                "failure": {
                    **failure,
                    "diagnostic_message": str(exc),
                },
            }
            attempts[node_id] = attempt
            _write_json(attempt_root / "attempt_result.json", attempt)
            if progress_callback is not None:
                progress_callback(node_id, "failed_closed", attempt)
            return {
                "schema_version": "req2web.phase4.real_model_node_execution.v1",
                "node_id": node_id,
                "status": "failed_closed",
                "source_kind": REAL_MODEL_SOURCE_KIND,
                "generate_call_count": int(generate_started),
                "raw_capture": (
                    _not_formed_raw_capture() if raw is None else make_real_model_raw_capture(raw)
                ),
                "attempt_identity": make_identity(attempt, revision=ATTEMPT_SCHEMA_VERSION),
                "output": None,
                "raw_model_contract_success": False,
                "normalized_node_contract_success": False,
                "failure": failure,
            }

    def delivery_executor(
        graph_state: Mapping[str, object],
        page_spec: Mapping[str, object],
        assembly_report: Mapping[str, object],
    ) -> Mapping[str, object]:
        mapping = copy.deepcopy(dict(graph_state["mapping_record"]))
        composition = copy.deepcopy(dict(graph_state["candidate_composition_record"]))
        _write_json(root / "mapping.json", mapping)
        _write_json(root / "candidate_composition_record.json", composition)
        _write_json(root / "assembled_page_spec.json", dict(page_spec))
        _write_json(root / "assembly_report.json", dict(assembly_report))
        f4_execution = copy.deepcopy(dict(graph_state["node_execution_records"]["F4"]))
        source_result = {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": selected_run_id,
            "case_id": selected_b["case_id"],
            "request_id": selected_b["request_id"],
            "source_kind": REAL_MODEL_SOURCE_KIND,
            "status": "assembled",
            "model_generate_calls": sum(int(item["generate_call_count"]) for item in attempts.values()),
            "raw_model_contract_success": f4_execution["raw_model_contract_success"],
            "normalized_node_contract_success": f4_execution["normalized_node_contract_success"],
            "agent_chain_system_output_usable": True,
            "composition_status": "composed_in_langgraph",
            "assembler_status": "assembled_in_langgraph",
            "downstream": "not_executed",
            "prompt_authority_identity": copy.deepcopy(PROMPT_AUTHORITY_IDENTITY),
            "workflow_runtime": REAL_MODEL_GRAPH_REVISION,
            "manual_f1_f4_loop_used": False,
            "automatic_retry_count": 0,
            "formal_quality_claimed": False,
            "failure": None,
        }
        _write_json(root / "revalidation_result.json", source_result)
        live = materials.live
        receipt = run_phase4_fresh_delivery(
            source_root=root,
            delivery_root=root / "delivery",
            context=upstream_context,
            guidance=upstream_guidance,
            manifest=live["manifest"],
            selected=live["selected"],
            local_request=live["local_request"],
            pre_invocation_audit=live["pre_invocation_audit"],
            local_qwen_preparation=live["local_qwen_preparation"],
            package=live["package"],
            frozen_g0_reference=live["frozen_g0_reference"],
            fallback_record=live["fallback_record"],
            fallback_snapshot_dir=live["fallback_snapshot_dir"],
            scripted_acceptance_fixture=live["scripted_acceptance_fixture"],
            delivery_policy=PHASE4_FRESH_DELIVERY_POLICY_A07A_DIRECT_V1,
        )
        delivery = receipt.to_dict()
        final = {
            **source_result,
            "status": "delivery_terminal_success",
            "delivery_receipt": delivery,
            "delivery_result_identity": _identity(
                delivery, revision="req2web.phase4.fresh_delivery.receipt.v1"
            ),
        }
        _write_json(root / "p4_05_final_result.json", final)
        return {
            "graph_delivery_success": True,
            "terminal_status": final["status"],
            "delivery": delivery,
            "final_result_identity": _identity(final, revision=RUN_SCHEMA_VERSION),
        }

    try:
        runtime = Phase4RealModelGraphRuntime(
            node_executor=node_executor,
            context=upstream_context,
            guidance=upstream_guidance,
            delivery_executor=delivery_executor,
        )
        initial = create_real_model_graph_state(
            run_id=selected_run_id,
            b_input=selected_b,
            upstream_binding={
                "actual_upstream_binding": copy.deepcopy(dict(upstream_binding)),
                "b_aux_sidecar": sidecar_binding,
                "prompt_authority_identity": copy.deepcopy(PROMPT_AUTHORITY_IDENTITY),
                "manual_f1_f4_loop_used": False,
            },
        )
        graph_result = runtime.invoke(
            initial,
            thread_id=f"{selected_run_id}:local-langgraph",
        )
        _write_json(root / "langgraph_final_state.json", graph_result)
        _write_json(root / "langgraph_events.json", graph_result["events"])
        final_path = root / "p4_05_final_result.json"
        if final_path.is_file():
            final_result = _read_json(final_path, "local final result")
    finally:
        supervisor = (
            worker.close()
            if worker is not None
            else {
                "worker_id": "test-only-generator",
                "worker_pid": None,
                "worker_exit_code": 0,
                "worker_exit_verified": True,
                "generate_calls": {
                    node_id: int(node_id in attempts) for node_id in NODE_ORDER
                },
                "stderr_identity": None,
            }
        )
        ledger = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "run_id": selected_run_id,
            "node_order": list(NODE_ORDER),
            "per_node": {
                node_id: {
                    "generate_started_count": int(
                        attempts.get(node_id, {}).get("generate_started") is True
                    ),
                    "status": attempts.get(node_id, {}).get("status", "not_started"),
                }
                for node_id in NODE_ORDER
            },
            "total_generate_started_count": sum(
                attempts.get(node_id, {}).get("generate_started") is True
                for node_id in NODE_ORDER
            ),
            "automatic_retry_count": 0,
        }
        _write_json(root / "model_call_ledger.json", ledger)
        _write_json(
            root / "supervisor_result.json",
            {
                "schema_version": SUPERVISOR_SCHEMA_VERSION,
                "run_id": selected_run_id,
                **supervisor,
                "automatic_retry_count": 0,
            },
        )

    graph_completed = bool(graph_result and graph_result.get("status") == "completed")
    model_package_root = root / "delivery" / "result_package_v1"
    fallback_delivery_root = root / "delivery" / "fallback" / "result_package"
    g0_package_root = root / "graph-bound-delivery-materials" / "g0-package-v2"
    if graph_completed and model_package_root.is_dir():
        selected_package_root = model_package_root
        selected_delivery_kind = "model_result_package_v1"
        terminal_status = "completed_model_delivery"
    elif graph_completed and fallback_delivery_root.is_dir():
        selected_package_root = fallback_delivery_root
        selected_delivery_kind = "same_case_g0_fallback_delivery_v2"
        terminal_status = "completed_delivery_via_same_case_g0_fallback"
    else:
        selected_package_root = g0_package_root
        selected_delivery_kind = "deterministic_g0_result_package_v2"
        terminal_status = "model_failed_closed_deterministic_g0_available"
    if not selected_package_root.is_dir():
        raise Phase4LocalQwenLangGraphError(
            "no deliverable ResultPackage is available: "
            f"graph_status={None if graph_result is None else graph_result.get('status')}, "
            f"model_package_exists={model_package_root.is_dir()}, "
            f"g0_package_exists={g0_package_root.is_dir()}, "
            f"delivery_entries={sorted(item.name for item in (root / 'delivery').iterdir()) if (root / 'delivery').is_dir() else []}"
        )
    summary = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": selected_run_id,
        "case_id": selected_b["case_id"],
        "request_id": selected_b["request_id"],
        "status": terminal_status,
        "workflow_runtime": REAL_MODEL_GRAPH_REVISION,
        "profile": profile.to_dict(),
        "b_aux_sidecar": sidecar_binding,
        "graph_status": None if graph_result is None else graph_result.get("status"),
        "graph_failure": None if graph_result is None else graph_result.get("failure"),
        "agent_chain_completed": graph_completed,
        "model_result_available": model_package_root.is_dir(),
        "deterministic_g0_available": True,
        "selected_delivery_kind": selected_delivery_kind,
        "selected_package_root": str(selected_package_root),
        "model_package_root": str(model_package_root) if model_package_root.is_dir() else None,
        "fallback_delivery_root": (
            str(fallback_delivery_root) if fallback_delivery_root.is_dir() else None
        ),
        "g0_package_root": str(g0_package_root),
        "model_call_ledger": ledger,
        "scripted_acceptance_executed": graph_completed,
        "real_browser_executed": False,
        "semantic_acceptance_executed": False,
        "automatic_retry_count": 0,
        "manual_f1_f4_loop_used": False,
        "formal_quality_claimed": False,
        "claim_boundary": (
            "local quantized integration run; deterministic G0 availability is "
            "reported separately and is not raw-model success"
        ),
    }
    _write_json(root / "local_langgraph_run_summary.json", summary)
    return summary


def _worker_send(value: Mapping[str, object]) -> None:
    sys.stdout.buffer.write(_canonical(value) + b"\n")
    sys.stdout.buffer.flush()


def _worker_main(args: argparse.Namespace) -> int:
    profile = local_langgraph_profile(args.profile)
    model_root = args.model_root.resolve(strict=True)
    evidence = args.integrity_evidence.resolve(strict=True)
    result_root = args.result_root.resolve(strict=True)
    worker_id = f"local-langgraph-worker-{uuid.uuid4().hex[:12]}"
    try:
        inventory = validate_model_inventory_metadata(
            model_root=model_root,
            integrity_evidence=evidence,
            allow_relocated_model_root=True,
        )
        import torch
        import transformers
        from req2web_runtime.phase4_local_qwen_f4 import (
            F4CompleteSingleJSONStoppingCriteria,
            _install_f4_memory_efficient_attention,
        )

        if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
            raise Phase4LocalQwenLangGraphError("CUDA GPU0 is unavailable")
        properties = torch.cuda.get_device_properties(0)
        free_bytes, total_bytes = torch.cuda.mem_get_info(0)
        if (
            int(total_bytes) < profile.min_total_vram_bytes
            or int(free_bytes) < profile.min_free_vram_bytes
        ):
            raise Phase4LocalQwenLangGraphError("GPU memory is below the selected profile")
        processor = transformers.AutoProcessor.from_pretrained(
            str(model_root), local_files_only=True, trust_remote_code=False
        )
        load_kwargs: dict[str, object] = {
            "local_files_only": True,
            "trust_remote_code": False,
            "device_map": {"": 0},
            "dtype": torch.bfloat16,
            "low_cpu_mem_usage": True,
            "attn_implementation": "sdpa",
        }
        if profile.quantization in {"nf4_double_quant", "nf4_single_quant"}:
            load_kwargs["quantization_config"] = transformers.BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=(
                    profile.quantization == "nf4_double_quant"
                ),
                bnb_4bit_compute_dtype=torch.bfloat16,
            )
        model = transformers.AutoModelForImageTextToText.from_pretrained(
            str(model_root), **load_kwargs
        )
        model.eval()
        if (
            profile.quantization in {"nf4_double_quant", "nf4_single_quant"}
            and getattr(model, "is_loaded_in_4bit", False) is not True
        ):
            raise Phase4LocalQwenLangGraphError("model did not load in four-bit mode")
        attention = _install_f4_memory_efficient_attention(
            SimpleNamespace(_model=model, _torch=torch)
        )
        _worker_send(
            {
                "protocol": WORKER_PROTOCOL,
                "kind": "loaded",
                "worker_id": worker_id,
                "loaded_facts": {
                    "model_class": type(model).__name__,
                    "processor_class": type(processor).__name__,
                    "device_name": str(properties.name),
                    "total_vram_bytes": int(total_bytes),
                    "free_vram_bytes_at_preflight": int(free_bytes),
                    "quantization": profile.quantization,
                    "attention": attention,
                    "model_inventory_identity": inventory["inventory_identity"],
                },
            }
        )
        seen: set[str] = set()
        while True:
            raw_line = sys.stdin.buffer.readline()
            if not raw_line:
                return 3
            message = json.loads(raw_line.decode("utf-8"))
            if not isinstance(message, dict) or message.get("protocol") != WORKER_PROTOCOL:
                raise Phase4LocalQwenLangGraphError("worker protocol drifted")
            if message.get("kind") == "shutdown":
                _worker_send(
                    {
                        "protocol": WORKER_PROTOCOL,
                        "kind": "shutdown_ack",
                        "worker_id": worker_id,
                    }
                )
                return 0
            node_id = message.get("node_id")
            call_id = message.get("call_id")
            if (
                message.get("kind") != "generate"
                or node_id not in NODE_ORDER
                or not isinstance(call_id, str)
                or node_id in seen
            ):
                raise Phase4LocalQwenLangGraphError("worker call boundary drifted")
            seen.add(str(node_id))
            attempt_root = Path(str(message["attempt_root"])).resolve(strict=True)
            if result_root not in attempt_root.parents:
                raise Phase4LocalQwenLangGraphError("worker attempt root escaped the result root")
            input_bytes = _unb64(message.get("input_b64"), "worker input")
            prompt_bytes = _unb64(message.get("prompt_b64"), "worker prompt")
            _shared._validate_node_input_value(
                json.loads(input_bytes.decode("utf-8")), str(node_id)
            )
            prompt_value = json.loads(prompt_bytes.decode("utf-8"))
            if (
                not isinstance(prompt_value, Mapping)
                or prompt_value.get("prompt_authority_identity") != PROMPT_AUTHORITY_IDENTITY
                or prompt_value.get("node_id") != node_id
            ):
                raise Phase4LocalQwenLangGraphError("worker prompt authority drifted")
            model_text = (
                "P4_05_NODE_INPUT\n"
                + input_bytes.decode("utf-8")
                + "\nP4_05_PROMPT_CONTRACT\n"
                + prompt_bytes.decode("utf-8")
            )
            rendered = processor.apply_chat_template(
                [{"role": "user", "content": [{"type": "text", "text": model_text}]}],
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            encoded = processor(text=[rendered], return_tensors="pt")
            encoded = {
                key: tensor.to("cuda:0")
                for key, tensor in encoded.items()
                if key in {"input_ids", "attention_mask"} and hasattr(tensor, "to")
            }
            if "input_ids" not in encoded:
                raise Phase4LocalQwenLangGraphError("tokenizer did not produce input IDs")
            input_length = int(encoded["input_ids"].shape[1])
            output_cap = NODE_OUTPUT_TOKEN_CAPS[str(node_id)]
            if input_length > profile.max_input_tokens:
                raise Phase4LocalQwenLangGraphError("node input exceeds the profile without truncation")
            if input_length + output_cap > profile.max_input_tokens:
                output_cap = profile.max_input_tokens - input_length
            if output_cap < 512:
                raise Phase4LocalQwenLangGraphError("node has insufficient complete-output budget")
            tokenizer = getattr(processor, "tokenizer", processor)
            stopping = transformers.StoppingCriteriaList(
                [
                    F4CompleteSingleJSONStoppingCriteria(
                        tokenizer=tokenizer,
                        prompt_length=input_length,
                    )
                ]
            )
            empty_refs_processor = (
                _RequiredEmptyRefsLogitsProcessor(
                    tokenizer=tokenizer,
                    prompt_length=input_length,
                )
                if (
                    node_id == "F4"
                    and profile.f4_required_empty_refs_decoding
                )
                else None
            )
            f3_key_order_processor = (
                _F3RequiredKeyOrderLogitsProcessor(
                    tokenizer=tokenizer,
                    prompt_length=input_length,
                )
                if (
                    node_id == "F3"
                    and profile.f3_required_key_order_decoding
                )
                else None
            )
            _write_json(
                attempt_root / "generation_started.json",
                {
                    "schema_version": f"{SCHEMA_PREFIX}.generation_started.v1",
                    "node_id": node_id,
                    "call_count": 1,
                    "automatic_retry_count": 0,
                },
            )
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats(0)
            torch.manual_seed(0)
            torch.cuda.manual_seed_all(0)
            decoding_processors = [
                processor
                for processor in (f3_key_order_processor, empty_refs_processor)
                if processor is not None
            ]
            decoding_controls = (
                {
                    "logits_processor": transformers.LogitsProcessorList(
                        decoding_processors
                    )
                }
                if decoding_processors
                else {}
            )
            try:
                with torch.inference_mode():
                    generated = model.generate(
                        **encoded,
                        stopping_criteria=stopping,
                        **decoding_controls,
                        max_new_tokens=output_cap,
                        do_sample=False,
                        num_return_sequences=1,
                        use_cache=True,
                        **_generation_memory_kwargs(profile, str(node_id)),
                    )
                generated_only = generated[:, input_length:]
                text_value = processor.batch_decode(
                    generated_only,
                    skip_special_tokens=True,
                    clean_up_tokenization_spaces=False,
                )[0]
                raw = text_value.encode("utf-8")
                if not raw:
                    raise Phase4LocalQwenLangGraphError("model returned no bytes")
                _write_once(attempt_root / "raw_response.bin", raw)
                metrics = {
                    "input_token_length": input_length,
                    "max_input_tokens": profile.max_input_tokens,
                    "max_new_tokens": output_cap,
                    "cache_implementation": (
                        profile.f4_cache_implementation
                        if node_id == "F4"
                        else "default_dynamic"
                    ),
                    "prefill_chunk_size": (
                        profile.f4_prefill_chunk_size if node_id == "F4" else None
                    ),
                    "output_token_length": int(generated_only.shape[1]),
                    "empty_refs_constraint_application_count": (
                        empty_refs_processor.application_count
                        if empty_refs_processor is not None
                        else 0
                    ),
                    "f3_key_order_constraint_application_count": (
                        f3_key_order_processor.application_count
                        if f3_key_order_processor is not None
                        else 0
                    ),
                    "cuda_peak_allocated_bytes": int(torch.cuda.max_memory_allocated(0)),
                    "cuda_peak_reserved_bytes": int(torch.cuda.max_memory_reserved(0)),
                }
                _write_json(attempt_root / "generation_metrics.json", metrics)
                _worker_send(
                    {
                        "protocol": WORKER_PROTOCOL,
                        "kind": "generation_result",
                        "call_id": call_id,
                        "node_id": node_id,
                        "raw_identity": _identity(raw, revision=f"{SCHEMA_PREFIX}.raw.v1"),
                        "metrics": metrics,
                    }
                )
                del generated, generated_only, encoded
                torch.cuda.empty_cache()
            except Exception as exc:
                _write_json(
                    attempt_root / "worker_failure.json",
                    {
                        "schema_version": f"{SCHEMA_PREFIX}.worker_failure.v1",
                        "node_id": node_id,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "automatic_retry_count": 0,
                    },
                )
                _worker_send(
                    {
                        "protocol": WORKER_PROTOCOL,
                        "kind": "generation_error",
                        "call_id": call_id,
                        "node_id": node_id,
                        "message_code": type(exc).__name__,
                    }
                )
    except Exception as exc:
        print(
            f"local LangGraph worker failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", choices=("_worker",))
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--integrity-evidence", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--profile", choices=PROFILE_NAMES, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    return _worker_main(_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "HIGH_GPU_PROFILE",
    "INTEGRITY_GPU_PROFILE",
    "LOW_GPU_PROFILE",
    "LocalLangGraphProfile",
    "Phase4LocalQwenLangGraphError",
    "local_langgraph_profile",
    "run_phase4_local_qwen_langgraph_integrated",
    "runtime_capabilities",
]
