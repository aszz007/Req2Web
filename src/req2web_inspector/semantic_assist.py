"""Advisory-only semantic requirement assistance for the Req2Web Inspector.

The assistant consumes a deterministic canonical-B preview and produces an
independent B-Aux-style sidecar. It never mutates canonical B and is never an
input to deterministic draft creation or the F1-F4 model graph.
"""

from __future__ import annotations

import argparse
import base64
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unicodedata
import uuid
from typing import Any, Mapping, Sequence

from req2web_runtime.phase4_local_qwen import (
    QWEN_MODEL_ID,
    QWEN_MODEL_REVISION,
    validate_model_inventory_metadata,
)


PROFILE_SCHEMA_VERSION = "req2web.semantic_requirement_assist.profile.v3"
PROMPT_SCHEMA_VERSION = "req2web.semantic_requirement_assist.prompt.v2"
RUN_SCHEMA_VERSION = "req2web.semantic_requirement_assist.run.v2"
SIDECAR_REVISION = "req2web.semantic_requirement_assist.b_aux.v1"
STORE_SCHEMA_VERSION = "req2web.semantic_requirement_assist.store.v1"
STORE_MARKER = ".req2web-semantic-assist-store.json"
LOCAL_LOW_GPU_PROFILE = "local_low_gpu_nf4"
LOCAL_INTEGRITY_PROFILE = "local_integrity_nf4"
HIGH_GPU_PROFILE = "high_gpu_bf16"
LOCAL_PROVIDER = "local_qwen"
CLOSED_API_PROVIDER = "closed_api_reserved"
MAX_ADVISORY_ITEMS = 6
MAX_STATEMENT_CHARS = 600
ZERO_SHA256 = "sha256:" + ("0" * 64)
_RUN_ID_PREFIX = "assist-"
_ADVISORY_KINDS = {
    "ambiguity",
    "conflict",
    "missing_information",
    "risk",
    "suggestion",
}
_CONTROL = set(range(0, 9)) | {11, 12} | set(range(14, 32)) | {127}


class SemanticRequirementAssistError(ValueError):
    """Raised when semantic assistance must fail closed."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SemanticRequirementAssistError(
            "semantic-assist value is not canonical JSON"
        ) from exc


def _identity(value: object, *, revision: str) -> dict[str, object]:
    raw = value if isinstance(value, bytes) else _canonical_bytes(value)
    return {
        "identity_kind": "raw_bytes" if isinstance(value, bytes) else "canonical_json",
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": revision,
    }


def _atomic_bytes(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_json(path: Path, value: object) -> None:
    _atomic_bytes(path, _canonical_bytes(value) + b"\n")


def _read_json(path: Path, name: str) -> object:
    if not path.is_file() or path.is_symlink():
        raise SemanticRequirementAssistError(f"{name} is unavailable")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemanticRequirementAssistError(f"{name} is not readable JSON") from exc


def _text(value: object, name: str, *, maximum: int = MAX_STATEMENT_CHARS) -> str:
    if not isinstance(value, str):
        raise SemanticRequirementAssistError(f"{name} must be text")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or len(normalized) > maximum:
        raise SemanticRequirementAssistError(f"{name} length is invalid")
    if any(ord(character) in _CONTROL for character in normalized):
        raise SemanticRequirementAssistError(f"{name} contains a control character")
    return normalized


def _object_pairs(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SemanticRequirementAssistError(
                "semantic model output contains a duplicate JSON key"
            )
        result[key] = value
    return result


def _single_json_object(raw: bytes) -> dict[str, object]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SemanticRequirementAssistError(
            "semantic model output is not UTF-8"
        ) from exc
    decoder = json.JSONDecoder(object_pairs_hook=_object_pairs)
    try:
        value, end = decoder.raw_decode(text.lstrip())
    except (json.JSONDecodeError, SemanticRequirementAssistError) as exc:
        raise SemanticRequirementAssistError(
            "semantic model output is not one complete JSON object"
        ) from exc
    leading = len(text) - len(text.lstrip())
    if text[leading + end :].strip():
        raise SemanticRequirementAssistError(
            "semantic model output contains text outside the JSON object"
        )
    if not isinstance(value, dict):
        raise SemanticRequirementAssistError(
            "semantic model output must be a JSON object"
        )
    return value


@dataclass(frozen=True)
class SemanticAssistProfile:
    """One exact local or high-GPU precision profile."""

    profile_name: str
    quantization: str
    dtype: str
    compute_dtype: str
    min_total_vram_bytes: int
    min_free_vram_bytes: int
    max_input_tokens: int
    max_new_tokens: int
    timeout_seconds: int
    formal_quality_eligible: bool

    def validate(self) -> None:
        if self.profile_name not in {
            LOCAL_LOW_GPU_PROFILE,
            LOCAL_INTEGRITY_PROFILE,
            HIGH_GPU_PROFILE,
        }:
            raise SemanticRequirementAssistError("unknown semantic-assist profile")
        if (
            self.dtype != "bfloat16"
            or self.compute_dtype != "bfloat16"
            or self.max_input_tokens < 4_096
            or self.max_new_tokens < 1_024
            or self.timeout_seconds < 60
        ):
            raise SemanticRequirementAssistError(
                "semantic-assist profile boundary drifted"
            )
        if self.profile_name == LOCAL_LOW_GPU_PROFILE:
            if (
                self.quantization != "nf4_double_quant"
                or self.min_total_vram_bytes != 8_000_000_000
                or self.min_free_vram_bytes < 5_000_000_000
                or self.formal_quality_eligible is not False
            ):
                raise SemanticRequirementAssistError(
                    "local semantic-assist profile drifted"
                )
        elif self.profile_name == LOCAL_INTEGRITY_PROFILE:
            if (
                self.quantization != "nf4_single_quant"
                or self.min_total_vram_bytes != 8_000_000_000
                or self.min_free_vram_bytes < 6_000_000_000
                or self.formal_quality_eligible is not False
            ):
                raise SemanticRequirementAssistError(
                    "local integrity semantic-assist profile drifted"
                )
        elif (
            self.quantization != "none"
            or self.min_total_vram_bytes < 30_000_000_000
            or self.formal_quality_eligible is not True
        ):
            raise SemanticRequirementAssistError(
                "high-GPU semantic-assist profile drifted"
            )

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
            "min_total_vram_bytes": self.min_total_vram_bytes,
            "min_free_vram_bytes": self.min_free_vram_bytes,
            "max_input_tokens": self.max_input_tokens,
            "max_new_tokens": self.max_new_tokens,
            "timeout_seconds": self.timeout_seconds,
            "local_files_only": True,
            "offline": True,
            "network": False,
            "telemetry": False,
            "tracing": False,
            "do_sample": False,
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 0,
            "automatic_retry_limit": 0,
            "input_truncation": False,
            "output_truncation": False,
            "complete_json_required": True,
            "attention_implementation": "sdpa",
            "cuda_allocator_config": "expandable_segments:True",
            "formal_quality_eligible": self.formal_quality_eligible,
        }


def semantic_assist_profile(profile_name: str) -> SemanticAssistProfile:
    if profile_name == LOCAL_LOW_GPU_PROFILE:
        profile = SemanticAssistProfile(
            profile_name=profile_name,
            quantization="nf4_double_quant",
            dtype="bfloat16",
            compute_dtype="bfloat16",
            min_total_vram_bytes=8_000_000_000,
            min_free_vram_bytes=5_500_000_000,
            max_input_tokens=8_192,
            max_new_tokens=1_280,
            timeout_seconds=1_200,
            formal_quality_eligible=False,
        )
    elif profile_name == LOCAL_INTEGRITY_PROFILE:
        profile = SemanticAssistProfile(
            profile_name=profile_name,
            quantization="nf4_single_quant",
            dtype="bfloat16",
            compute_dtype="bfloat16",
            min_total_vram_bytes=8_000_000_000,
            min_free_vram_bytes=6_250_000_000,
            max_input_tokens=8_192,
            max_new_tokens=1_280,
            timeout_seconds=1_800,
            formal_quality_eligible=False,
        )
    elif profile_name == HIGH_GPU_PROFILE:
        profile = SemanticAssistProfile(
            profile_name=profile_name,
            quantization="none",
            dtype="bfloat16",
            compute_dtype="bfloat16",
            min_total_vram_bytes=30_000_000_000,
            min_free_vram_bytes=24_000_000_000,
            max_input_tokens=32_768,
            max_new_tokens=2_048,
            timeout_seconds=1_200,
            formal_quality_eligible=True,
        )
    else:
        raise SemanticRequirementAssistError("unknown semantic-assist profile")
    profile.validate()
    return profile


def provider_capabilities() -> dict[str, object]:
    """Describe the implemented local provider and reserved closed-API seam."""

    return {
        LOCAL_PROVIDER: {
            "status": "implemented_explicit_local_action",
            "model_id": QWEN_MODEL_ID,
            "profiles": [
                LOCAL_LOW_GPU_PROFILE,
                LOCAL_INTEGRITY_PROFILE,
                HIGH_GPU_PROFILE,
            ],
        },
        CLOSED_API_PROVIDER: {
            "status": "reserved_not_connected",
            "network_calls_implemented": False,
            "credentials_accepted": False,
        },
    }


def _canonical_b(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise SemanticRequirementAssistError("canonical B preview is absent")
    expected = {
        "requirement_summary",
        "target_device",
        "task_type",
        "constraints",
        "use_cases",
    }
    if set(value) != expected:
        raise SemanticRequirementAssistError("canonical B preview keys drifted")
    result = copy.deepcopy(dict(value))
    for key in ("requirement_summary", "target_device", "task_type"):
        result[key] = _text(result[key], f"canonical_b.{key}", maximum=12_000)
    constraints = result["constraints"]
    use_cases = result["use_cases"]
    if not isinstance(constraints, list) or not isinstance(use_cases, list) or not use_cases:
        raise SemanticRequirementAssistError("canonical B arrays are invalid")
    result["constraints"] = [
        _text(item, f"canonical_b.constraints[{index}]", maximum=1_000)
        for index, item in enumerate(constraints)
    ]
    checked_cases: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(use_cases):
        if not isinstance(item, Mapping) or set(item) != {
            "use_case_id",
            "title",
            "actor",
            "goal",
            "expected_outcome",
        }:
            raise SemanticRequirementAssistError(
                f"canonical_b.use_cases[{index}] is invalid"
            )
        row = {
            key: _text(item[key], f"canonical_b.use_cases[{index}].{key}")
            for key in (
                "use_case_id",
                "title",
                "actor",
                "goal",
                "expected_outcome",
            )
        }
        if row["use_case_id"] in seen_ids:
            raise SemanticRequirementAssistError("canonical B use-case IDs repeat")
        seen_ids.add(row["use_case_id"])
        checked_cases.append(row)
    result["use_cases"] = checked_cases
    return result


def _allowed_refs(canonical_b: Mapping[str, object]) -> list[dict[str, str]]:
    rows = [
        {
            "ref_type": "b_requirement",
            "ref_id": "REQ-01",
            "ref_revision": "canonical_b.requirement.v1",
        }
    ]
    for use_case in canonical_b["use_cases"]:  # type: ignore[index]
        rows.append(
            {
                "ref_type": "b_use_case",
                "ref_id": str(use_case["use_case_id"]),
                "ref_revision": "canonical_b.use_case.v1",
            }
        )
    for index, _ in enumerate(canonical_b["constraints"], start=1):  # type: ignore[arg-type]
        rows.append(
            {
                "ref_type": "b_constraint",
                "ref_id": f"CON-{index:02d}",
                "ref_revision": "canonical_b.constraint.v1",
            }
        )
    rows.sort(key=lambda item: (item["ref_type"], item["ref_id"], item["ref_revision"]))
    return rows


def build_semantic_assist_prompt(canonical_b_value: object) -> dict[str, object]:
    """Build the sole B-Aux prompt authority for the Inspector assistant."""

    canonical_b = _canonical_b(canonical_b_value)
    allowed_refs = _allowed_refs(canonical_b)
    return {
        "schema_version": PROMPT_SCHEMA_VERSION,
        "node_id": "B-Aux",
        "role": "advisory_only_semantic_requirement_reviewer",
        "instructions": [
            "Review only the supplied canonical requirement understanding.",
            "Identify concrete ambiguity, conflict, missing information, risk, or a useful suggestion.",
            "Do not rewrite, replace, expand, or annotate canonical B in place.",
            "Do not invent implementation facts, retrieved evidence, UI states, or acceptance verdicts.",
            "Return exactly one complete JSON object and no text outside it.",
            "Return between one and six non-overlapping advisory items in source priority order; keep only the highest-value findings.",
            "Every target_b_refs array must use only the supplied allowed refs, sorted by ref_type, ref_id, and ref_revision, without duplicates.",
            "Before returning, verify every target_b_refs array: b_constraint precedes b_requirement, and b_requirement precedes b_use_case; for example, REQ-01 must precede UC-01.",
        ],
        "output_contract": {
            "exact_top_level_keys": ["advisory_items"],
            "advisory_item_exact_keys": [
                "advisory_kind",
                "statement",
                "target_b_refs",
            ],
            "advisory_kinds": sorted(_ADVISORY_KINDS),
            "minimum_items": 1,
            "maximum_items": MAX_ADVISORY_ITEMS,
            "statement_maximum_characters": MAX_STATEMENT_CHARS,
        },
        "allowed_target_refs": allowed_refs,
        "canonical_b": canonical_b,
    }


def validate_semantic_assist_raw(
    raw: bytes,
    *,
    canonical_b_value: object,
) -> list[dict[str, object]]:
    """Validate one immutable raw response against the advisory contract."""

    if not isinstance(raw, bytes) or not raw:
        raise SemanticRequirementAssistError("semantic model raw output is empty")
    canonical_b = _canonical_b(canonical_b_value)
    value = _single_json_object(raw)
    if set(value) != {"advisory_items"}:
        raise SemanticRequirementAssistError(
            "semantic model output has unsupported top-level keys"
        )
    items = value["advisory_items"]
    if (
        not isinstance(items, list)
        or not 1 <= len(items) <= MAX_ADVISORY_ITEMS
    ):
        raise SemanticRequirementAssistError(
            "semantic advisory item count is outside the supported range"
        )
    allowed = {
        (item["ref_type"], item["ref_id"], item["ref_revision"])
        for item in _allowed_refs(canonical_b)
    }
    checked: list[dict[str, object]] = []
    seen_items: set[bytes] = set()
    for index, item in enumerate(items):
        if not isinstance(item, Mapping) or set(item) != {
            "advisory_kind",
            "statement",
            "target_b_refs",
        }:
            raise SemanticRequirementAssistError(
                f"advisory_items[{index}] keys drifted"
            )
        kind = item["advisory_kind"]
        if kind not in _ADVISORY_KINDS:
            raise SemanticRequirementAssistError(
                f"advisory_items[{index}].advisory_kind is invalid"
            )
        statement = _text(
            item["statement"], f"advisory_items[{index}].statement"
        )
        refs = item["target_b_refs"]
        if not isinstance(refs, list) or not refs:
            raise SemanticRequirementAssistError(
                f"advisory_items[{index}].target_b_refs is empty"
            )
        checked_refs: list[dict[str, str]] = []
        ref_tuples: list[tuple[str, str, str]] = []
        for ref_index, ref in enumerate(refs):
            if not isinstance(ref, Mapping) or set(ref) != {
                "ref_type",
                "ref_id",
                "ref_revision",
            }:
                raise SemanticRequirementAssistError(
                    f"advisory_items[{index}].target_b_refs[{ref_index}] is invalid"
                )
            row = {
                key: _text(
                    ref[key],
                    f"advisory_items[{index}].target_b_refs[{ref_index}].{key}",
                    maximum=160,
                )
                for key in ("ref_type", "ref_id", "ref_revision")
            }
            ref_tuple = (row["ref_type"], row["ref_id"], row["ref_revision"])
            if ref_tuple not in allowed:
                raise SemanticRequirementAssistError(
                    f"advisory_items[{index}] references material outside canonical B"
                )
            checked_refs.append(row)
            ref_tuples.append(ref_tuple)
        if ref_tuples != sorted(ref_tuples) or len(ref_tuples) != len(set(ref_tuples)):
            raise SemanticRequirementAssistError(
                f"advisory_items[{index}].target_b_refs order or uniqueness drifted"
            )
        checked_item: dict[str, object] = {
            "advisory_kind": kind,
            "statement": statement,
            "target_b_refs": checked_refs,
        }
        item_bytes = _canonical_bytes(checked_item)
        if item_bytes in seen_items:
            raise SemanticRequirementAssistError("duplicate semantic advisory item")
        seen_items.add(item_bytes)
        checked.append(checked_item)
    return checked


def _failure() -> dict[str, object]:
    return {
        "failure_code": "advisory_unavailable",
        "failure_stage": "b_aux_sidecar",
        "retry_allowed": False,
        "fallback_allowed": False,
        "source_refs": [],
        "message_code": "p4_01_advisory_unavailable",
    }


def build_sidecar(
    *,
    canonical_b_value: object,
    call_count: int,
    raw: bytes | None,
) -> dict[str, object]:
    canonical_b = _canonical_b(canonical_b_value)
    canonical_b_identity = _identity(
        canonical_b, revision="req2web.canonical_b.preview.v1"
    )
    if type(call_count) is not int or call_count not in {0, 1}:
        raise SemanticRequirementAssistError("B-Aux call count must be zero or one")
    try:
        items = validate_semantic_assist_raw(
            raw or b"", canonical_b_value=canonical_b
        )
    except SemanticRequirementAssistError:
        return {
            "advisory_revision": SIDECAR_REVISION,
            "call_count": call_count,
            "canonical_b_identity": canonical_b_identity,
            "advisory_items": [],
            "sidecar_status": "advisory_unavailable",
            "failure": _failure(),
        }
    return {
        "advisory_revision": SIDECAR_REVISION,
        "call_count": call_count,
        "canonical_b_identity": canonical_b_identity,
        "advisory_items": items,
        "sidecar_status": "advisory_available",
        "failure": None,
    }


def _safe_root(root: Path, marker_name: str, schema_version: str) -> Path:
    resolved = Path(root).expanduser().resolve(strict=False)
    if resolved.exists():
        if resolved.is_symlink() or not resolved.is_dir():
            raise SemanticRequirementAssistError("semantic-assist root is invalid")
        marker = resolved / marker_name
        if not marker.is_file():
            if any(resolved.iterdir()):
                raise SemanticRequirementAssistError(
                    "refusing a non-empty semantic-assist root without its marker"
                )
            _atomic_json(marker, {"schema_version": schema_version})
    else:
        resolved.mkdir(parents=True)
        _atomic_json(resolved / marker_name, {"schema_version": schema_version})
    if _read_json(resolved / marker_name, "semantic-assist store marker") != {
        "schema_version": schema_version
    }:
        raise SemanticRequirementAssistError("semantic-assist store marker drifted")
    return resolved


class SemanticRequirementAssistStore:
    """Run one isolated local Qwen call and retain immutable evidence."""

    def __init__(
        self,
        *,
        root: Path,
        model_root: Path,
        integrity_evidence: Path,
        profile_name: str,
        worker_executable: Path | None = None,
    ) -> None:
        self.root = _safe_root(root, STORE_MARKER, STORE_SCHEMA_VERSION)
        self.model_root = Path(model_root).expanduser().resolve(strict=True)
        self.integrity_evidence = Path(integrity_evidence).expanduser().resolve(
            strict=True
        )
        if self.model_root.is_symlink() or not self.model_root.is_dir():
            raise SemanticRequirementAssistError("local Qwen model root is invalid")
        if self.integrity_evidence.is_symlink() or not self.integrity_evidence.is_file():
            raise SemanticRequirementAssistError("model integrity evidence is invalid")
        self.profile = semantic_assist_profile(profile_name)
        self.worker_executable = Path(worker_executable or sys.executable).resolve(
            strict=True
        )
        self._lock = threading.Lock()

    def capability(self) -> dict[str, object]:
        return {
            "status": "available_explicit_local_qwen",
            "provider": LOCAL_PROVIDER,
            "profile": self.profile.to_dict(),
            "model_root_present": True,
            "one_call_no_retry": True,
            "canonical_b_writeback": False,
            "f1_f4_input": False,
        }

    def _run_path(self, run_id: str) -> Path:
        if not run_id.startswith(_RUN_ID_PREFIX) or len(run_id) != len(_RUN_ID_PREFIX) + 12:
            raise SemanticRequirementAssistError("semantic-assist run ID is invalid")
        return self.root / run_id

    def run(self, value: object) -> dict[str, object]:
        from req2web_inspector.live_draft import analyze_requirement

        diagnostics = analyze_requirement(value)
        if not diagnostics["accepted_for_deterministic_draft"]:
            raise SemanticRequirementAssistError(
                "requirement diagnostics contain a blocking finding"
            )
        canonical_b = _canonical_b(diagnostics["canonical_b_preview"])
        prompt = build_semantic_assist_prompt(canonical_b)
        run_id = _RUN_ID_PREFIX + uuid.uuid4().hex[:12]
        run_path = self._run_path(run_id)
        staging = self.root / f".{run_id}.staging-{uuid.uuid4().hex}"
        if not self._lock.acquire(blocking=False):
            raise SemanticRequirementAssistError(
                "another local semantic-assist model call is already running"
            )
        try:
            staging.mkdir()
            _atomic_json(staging / "canonical_b.json", canonical_b)
            _atomic_json(staging / "prompt.json", prompt)
            _atomic_json(staging / "profile.json", self.profile.to_dict())
            pre_call = {
                "schema_version": f"{RUN_SCHEMA_VERSION}.pre_call.v1",
                "run_id": run_id,
                "provider": LOCAL_PROVIDER,
                "model_id": QWEN_MODEL_ID,
                "model_revision": QWEN_MODEL_REVISION,
                "canonical_b_identity": _identity(
                    canonical_b, revision="req2web.canonical_b.preview.v1"
                ),
                "prompt_identity": _identity(prompt, revision=PROMPT_SCHEMA_VERSION),
                "profile_identity": _identity(
                    self.profile.to_dict(), revision=PROFILE_SCHEMA_VERSION
                ),
                "call_cap": 1,
                "automatic_retry_limit": 0,
                "raw_first": True,
                "canonical_b_writeback": False,
                "f1_f4_consumption": False,
                "closed_api_connected": False,
            }
            _atomic_json(staging / "pre_call.json", pre_call)
            command = [
                str(self.worker_executable),
                "-m",
                "req2web_inspector.semantic_assist",
                "_worker",
                "--model-root",
                str(self.model_root),
                "--integrity-evidence",
                str(self.integrity_evidence),
                "--work-root",
                str(staging),
                "--profile",
                self.profile.profile_name,
            ]
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
            completed: subprocess.CompletedProcess[bytes] | None = None
            timed_out = False
            try:
                completed = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.profile.timeout_seconds,
                    check=False,
                    env=environment,
                )
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                _atomic_bytes(staging / "worker_stdout.bin", exc.stdout or b"")
                _atomic_bytes(staging / "worker_stderr.bin", exc.stderr or b"")
            else:
                _atomic_bytes(staging / "worker_stdout.bin", completed.stdout)
                _atomic_bytes(staging / "worker_stderr.bin", completed.stderr)

            generation_started = (staging / "generation_started.json").is_file()
            raw_path = staging / "raw_response.bin"
            raw = raw_path.read_bytes() if raw_path.is_file() else None
            call_count = 1 if generation_started else 0
            sidecar = build_sidecar(
                canonical_b_value=canonical_b,
                call_count=call_count,
                raw=raw,
            )
            raw_contract_error: str | None = None
            if raw is not None:
                try:
                    validate_semantic_assist_raw(raw, canonical_b_value=canonical_b)
                except SemanticRequirementAssistError as exc:
                    raw_contract_error = str(exc)
            worker_result = (
                _read_json(staging / "worker_result.json", "semantic worker result")
                if (staging / "worker_result.json").is_file()
                else None
            )
            worker_failure = (
                _read_json(staging / "worker_failure.json", "semantic worker failure")
                if (staging / "worker_failure.json").is_file()
                else None
            )
            success = (
                not timed_out
                and completed is not None
                and completed.returncode == 0
                and sidecar["sidecar_status"] == "advisory_available"
            )
            record = {
                "schema_version": RUN_SCHEMA_VERSION,
                "run_id": run_id,
                "created_at": _now(),
                "status": "advisory_available" if success else "failed_closed",
                "provider": LOCAL_PROVIDER,
                "profile": self.profile.to_dict(),
                "model": {
                    "model_id": QWEN_MODEL_ID,
                    "model_revision": QWEN_MODEL_REVISION,
                },
                "input": diagnostics["normalized_request"],
                "canonical_b": canonical_b,
                "canonical_b_identity": _identity(
                    canonical_b, revision="req2web.canonical_b.preview.v1"
                ),
                "sidecar": sidecar,
                "raw_response": (
                    None
                    if raw is None
                    else {
                        **_identity(raw, revision=f"{SIDECAR_REVISION}.raw.v1"),
                        "relative_path": "raw_response.bin",
                    }
                ),
                "worker": {
                    "timed_out": timed_out,
                    "return_code": None if completed is None else completed.returncode,
                    "generation_started": generation_started,
                    "worker_result": worker_result,
                    "worker_failure": worker_failure,
                    "raw_contract_error": raw_contract_error,
                },
                "authority_boundary": {
                    "advisory_only": True,
                    "canonical_b_writeback": False,
                    "f1_f4_input": False,
                    "automatic_retry_count": 0,
                    "closed_api_connected": False,
                    "h1_or_gold_access": False,
                    "formal_quality_claimed": False,
                    "deterministic_draft_blocked_on_failure": False,
                },
            }
            _atomic_json(staging / "run.json", record)
            if run_path.exists():
                raise SemanticRequirementAssistError(
                    "semantic-assist run identity already exists"
                )
            os.replace(staging, run_path)
            return record
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            self._lock.release()


def _worker_execute(args: argparse.Namespace) -> int:
    work_root = args.work_root.resolve(strict=True)
    model_root = args.model_root.resolve(strict=True)
    evidence = args.integrity_evidence.resolve(strict=True)
    profile = semantic_assist_profile(args.profile)

    def record_stage(stage: str, **details: object) -> None:
        _atomic_json(
            work_root / "worker_status.json",
            {
                "schema_version": f"{RUN_SCHEMA_VERSION}.worker_status.v1",
                "stage": stage,
                "updated_at": _now(),
                **details,
            },
        )

    try:
        record_stage("validating_model_inventory")
        inventory = validate_model_inventory_metadata(
            model_root=model_root,
            integrity_evidence=evidence,
            allow_relocated_model_root=True,
        )
        prompt = _read_json(work_root / "prompt.json", "semantic prompt")
        expected_prompt = build_semantic_assist_prompt(
            _read_json(work_root / "canonical_b.json", "canonical B")
        )
        if prompt != expected_prompt:
            raise SemanticRequirementAssistError("semantic prompt replay drifted")

        record_stage("loading_runtime")

        import torch
        import transformers
        from req2web_runtime.phase4_local_qwen_f4 import (
            F4CompleteSingleJSONStoppingCriteria,
        )

        if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
            raise SemanticRequirementAssistError("CUDA GPU0 is unavailable")
        properties = torch.cuda.get_device_properties(0)
        free_bytes, total_bytes = torch.cuda.mem_get_info(0)
        if (
            int(total_bytes) < profile.min_total_vram_bytes
            or int(free_bytes) < profile.min_free_vram_bytes
        ):
            raise SemanticRequirementAssistError(
                "GPU memory is below the selected semantic-assist profile"
            )
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats(0)
        record_stage(
            "loading_model",
            free_vram_bytes_at_preflight=int(free_bytes),
            total_vram_bytes=int(total_bytes),
        )
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
            raise SemanticRequirementAssistError(
                "local semantic model did not load in four-bit mode"
            )
        if profile.quantization == "none" and (
            getattr(model, "is_loaded_in_4bit", False) is True
            or getattr(model, "is_loaded_in_8bit", False) is True
        ):
            raise SemanticRequirementAssistError(
                "high-GPU semantic model was unexpectedly quantized"
            )
        record_stage("model_loaded", quantization=profile.quantization)
        model_text = (
            "SEMANTIC_REQUIREMENT_ASSIST_PROMPT_JSON\n"
            + _canonical_bytes(prompt).decode("utf-8")
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
            raise SemanticRequirementAssistError(
                "semantic model tokenizer did not produce input IDs"
            )
        input_length = int(encoded["input_ids"].shape[1])
        if input_length > profile.max_input_tokens:
            raise SemanticRequirementAssistError(
                "semantic model input exceeds the selected profile without truncation"
            )
        tokenizer = getattr(processor, "tokenizer", processor)
        stopping = transformers.StoppingCriteriaList(
            [
                F4CompleteSingleJSONStoppingCriteria(
                    tokenizer=tokenizer,
                    prompt_length=input_length,
                )
            ]
        )
        _atomic_json(
            work_root / "generation_started.json",
            {
                "schema_version": f"{RUN_SCHEMA_VERSION}.generation_started.v1",
                "call_count": 1,
                "automatic_retry_count": 0,
            },
        )
        record_stage(
            "generating",
            input_token_length=input_length,
            max_new_tokens=profile.max_new_tokens,
        )
        torch.manual_seed(0)
        torch.cuda.manual_seed_all(0)
        with torch.inference_mode():
            generated = model.generate(
                **encoded,
                stopping_criteria=stopping,
                max_new_tokens=profile.max_new_tokens,
                do_sample=False,
                num_return_sequences=1,
                use_cache=True,
            )
        generated_only = generated[:, input_length:]
        raw_text = processor.batch_decode(
            generated_only,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]
        raw = raw_text.encode("utf-8")
        if not raw:
            raise SemanticRequirementAssistError("semantic model returned no bytes")
        _atomic_bytes(work_root / "raw_response.bin", raw)
        output_token_length = int(generated_only.shape[1])
        result = {
            "schema_version": f"{RUN_SCHEMA_VERSION}.worker_result.v1",
            "status": "raw_captured",
            "model_id": QWEN_MODEL_ID,
            "model_revision": QWEN_MODEL_REVISION,
            "profile_name": profile.profile_name,
            "quantization": profile.quantization,
            "device_name": str(properties.name),
            "total_vram_bytes": int(total_bytes),
            "free_vram_bytes_at_preflight": int(free_bytes),
            "input_token_length": input_length,
            "max_input_tokens": profile.max_input_tokens,
            "max_new_tokens": profile.max_new_tokens,
            "output_token_length": output_token_length,
            "raw_identity": _identity(raw, revision=f"{SIDECAR_REVISION}.raw.v1"),
            "cuda_peak_allocated_bytes": int(torch.cuda.max_memory_allocated(0)),
            "cuda_peak_reserved_bytes": int(torch.cuda.max_memory_reserved(0)),
            "model_inventory_identity": inventory["inventory_identity"],
            "automatic_retry_count": 0,
        }
        _atomic_json(work_root / "worker_result.json", result)
        record_stage(
            "raw_captured",
            output_token_length=output_token_length,
            raw_byte_length=len(raw),
        )
        return 0
    except Exception as exc:
        _atomic_json(
            work_root / "worker_failure.json",
            {
                "schema_version": f"{RUN_SCHEMA_VERSION}.worker_failure.v1",
                "status": "failed_closed",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "automatic_retry_count": 0,
            },
        )
        print(f"semantic assist failed closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", choices=("_worker",))
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--integrity-evidence", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument(
        "--profile",
        choices=(LOCAL_LOW_GPU_PROFILE, LOCAL_INTEGRITY_PROFILE, HIGH_GPU_PROFILE),
        required=True,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return _worker_execute(args)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CLOSED_API_PROVIDER",
    "HIGH_GPU_PROFILE",
    "LOCAL_INTEGRITY_PROFILE",
    "LOCAL_LOW_GPU_PROFILE",
    "LOCAL_PROVIDER",
    "SemanticAssistProfile",
    "SemanticRequirementAssistError",
    "SemanticRequirementAssistStore",
    "build_semantic_assist_prompt",
    "build_sidecar",
    "provider_capabilities",
    "semantic_assist_profile",
    "validate_semantic_assist_raw",
]
