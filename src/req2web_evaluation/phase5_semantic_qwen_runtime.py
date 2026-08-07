"""Supervised Qwen runtime for bounded Phase 5 semantic evaluation.

The module supports two explicit profiles:

* ``local_low_gpu_smoke_nf4`` for a local 8 GB-class GPU.  It uses NF4 and
  can prove only that the semantic-evaluation path executes.
* ``high_gpu_quality_bf16`` for a 30 GB+ GPU.  It uses BF16 without
  quantization or CPU offload.

Both profiles validate the same frozen Phase 4 evidence, start each call from
a fresh evidence-only context, save the complete raw response before parsing,
allow one model call, and never retry or repair.
"""

from __future__ import annotations

import argparse
import base64
import copy
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Mapping, Sequence

from req2web_evaluation.phase5_semantic_evaluator import (
    Phase5SemanticEvaluatorError,
    build_phase5_semantic_evaluator_no_action_bundle,
    build_phase5_semantic_evaluator_prompt,
    load_phase5_semantic_alignment_request,
    parse_phase5_semantic_evaluator_raw_response,
    validate_phase5_semantic_evidence_payloads,
)


RUNTIME_SCHEMA_VERSION = "req2web.phase5.semantic_qwen_runtime.v2"
PROFILE_SCHEMA_VERSION = "req2web.phase5.semantic_qwen_profile.v2"
MODEL_INPUT_SCHEMA_VERSION = "req2web.phase5.semantic_model_input.v2"
PRE_CALL_SCHEMA_VERSION = "req2web.phase5.semantic_pre_call.v1"
GENERATION_STARTED_SCHEMA_VERSION = (
    "req2web.phase5.semantic_generation_started.v1"
)
WORKER_RECEIPT_SCHEMA_VERSION = (
    "req2web.phase5.semantic_qwen_worker_receipt.v1"
)
RUN_SUMMARY_SCHEMA_VERSION = "req2web.phase5.semantic_run_summary.v2"
LEGACY_RUN_SUMMARY_SCHEMA_VERSION = (
    "req2web.phase5.semantic_run_summary.v1"
)
MODEL_ID = "Qwen/Qwen3.5-9B"
MODEL_REVISION = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
LOW_GPU_PROFILE = "local_low_gpu_smoke_nf4"
HIGH_GPU_PROFILE = "high_gpu_quality_bf16"
EVIDENCE_FILE_BY_ID = {
    "acceptance_binding": "acceptance_binding.json",
    "acceptance_plan": "acceptance_plan.json",
    "browser_execution_report": "browser_execution_report.json",
    "browser_screenshot": "browser_screenshot.png",
    "page_spec": "internal/page_spec.json",
    "result_package_manifest": "package_manifest.json",
}


class Phase5SemanticQwenRuntimeError(ValueError):
    """Raised when the semantic runtime must fail closed."""


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
        raise Phase5SemanticQwenRuntimeError(
            "semantic runtime artifact is not canonical JSON"
        ) from exc


def _sha(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _identity(raw: bytes) -> dict[str, object]:
    return {"sha256": "sha256:" + _sha(raw), "byte_length": len(raw)}


def _write_once(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _read_bytes(path: Path, name: str) -> bytes:
    if (
        not isinstance(path, Path)
        or not path.is_file()
        or path.is_symlink()
    ):
        raise Phase5SemanticQwenRuntimeError(f"{name} is unavailable")
    raw = path.read_bytes()
    if not raw:
        raise Phase5SemanticQwenRuntimeError(f"{name} is empty")
    return raw


@dataclass(frozen=True)
class SemanticQwenRuntimeProfile:
    profile_name: str
    quantization: str
    dtype: str
    compute_dtype: str
    cpu_offload: bool
    min_total_vram_bytes: int
    max_new_tokens: int
    timeout_seconds: int
    formal_quality_eligible: bool
    evidence_projection: str

    def validate(self) -> None:
        if self.profile_name not in {LOW_GPU_PROFILE, HIGH_GPU_PROFILE}:
            raise Phase5SemanticQwenRuntimeError(
                "semantic runtime profile name drifted"
            )
        if (
            self.dtype != "bfloat16"
            or self.compute_dtype != "bfloat16"
            or self.cpu_offload is not False
            or type(self.min_total_vram_bytes) is not int
            or self.min_total_vram_bytes < 8_000_000_000
            or type(self.max_new_tokens) is not int
            or self.max_new_tokens < 512
            or type(self.timeout_seconds) is not int
            or self.timeout_seconds < 60
        ):
            raise Phase5SemanticQwenRuntimeError(
                "semantic runtime profile boundary drifted"
            )
        if self.profile_name == LOW_GPU_PROFILE:
            if (
                self.quantization != "nf4"
                or self.formal_quality_eligible is not False
                or self.evidence_projection
                != "validated_identities_review_items_and_screenshot"
            ):
                raise Phase5SemanticQwenRuntimeError(
                    "low-GPU semantic profile drifted"
                )
        elif (
            self.quantization != "none"
            or self.min_total_vram_bytes < 30_000_000_000
            or self.formal_quality_eligible is not True
            or self.evidence_projection != "full_frozen_evidence"
        ):
            raise Phase5SemanticQwenRuntimeError(
                "high-GPU semantic profile drifted"
            )

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "profile_name": self.profile_name,
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "quantization": self.quantization,
            "dtype": self.dtype,
            "compute_dtype": self.compute_dtype,
            "cpu_offload": self.cpu_offload,
            "device_index": 0,
            "min_total_vram_bytes": self.min_total_vram_bytes,
            "max_new_tokens": self.max_new_tokens,
            "timeout_seconds": self.timeout_seconds,
            "local_files_only": True,
            "do_sample": False,
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 0,
            "automatic_retry_limit": 0,
            "fresh_evidence_only_context": True,
            "formal_quality_eligible": self.formal_quality_eligible,
            "evidence_projection": self.evidence_projection,
        }


def semantic_qwen_runtime_profile(
    profile_name: str,
) -> SemanticQwenRuntimeProfile:
    if profile_name == LOW_GPU_PROFILE:
        profile = SemanticQwenRuntimeProfile(
            profile_name=LOW_GPU_PROFILE,
            quantization="nf4",
            dtype="bfloat16",
            compute_dtype="bfloat16",
            cpu_offload=False,
            min_total_vram_bytes=8_000_000_000,
            max_new_tokens=2048,
            timeout_seconds=1200,
            formal_quality_eligible=False,
            evidence_projection=(
                "validated_identities_review_items_and_screenshot"
            ),
        )
    elif profile_name == HIGH_GPU_PROFILE:
        profile = SemanticQwenRuntimeProfile(
            profile_name=HIGH_GPU_PROFILE,
            quantization="none",
            dtype="bfloat16",
            compute_dtype="bfloat16",
            cpu_offload=False,
            min_total_vram_bytes=30_000_000_000,
            max_new_tokens=4096,
            timeout_seconds=1200,
            formal_quality_eligible=True,
            evidence_projection="full_frozen_evidence",
        )
    else:
        raise Phase5SemanticQwenRuntimeError(
            "unknown semantic runtime profile"
        )
    profile.validate()
    return profile


def _canonical_json_file(path: Path, name: str) -> bytes:
    raw = _read_bytes(path, name)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5SemanticQwenRuntimeError(f"{name} is not JSON") from exc
    return _canonical(value)


def prepare_phase5_semantic_case(
    *,
    audit_root: Path,
    result_package_root: Path,
    generator_model_identity: str,
    profile_name: str,
) -> dict[str, object]:
    """Validate one Phase 4 semantic request and build model-visible input."""

    request_raw = _read_bytes(
        audit_root / "semantic_alignment_request.json",
        "semantic alignment request",
    )
    request = load_phase5_semantic_alignment_request(request_raw)
    evidence_payloads: dict[str, bytes] = {}
    for evidence in request.evidence:
        evidence_id = evidence.evidence_id
        relative = EVIDENCE_FILE_BY_ID.get(evidence_id)
        if relative is None:
            raise Phase5SemanticQwenRuntimeError(
                f"unsupported semantic evidence ID: {evidence_id}"
            )
        root = (
            result_package_root
            if evidence_id in {"page_spec", "result_package_manifest"}
            else audit_root
        )
        source = root / relative
        raw = (
            _canonical_json_file(source, evidence_id)
            if evidence.identity_kind == "canonical_json"
            else _read_bytes(source, evidence_id)
        )
        evidence_payloads[evidence_id] = raw
    validated = validate_phase5_semantic_evidence_payloads(
        request=request,
        evidence_payloads=evidence_payloads,
    )
    profile = semantic_qwen_runtime_profile(profile_name)
    prompt = build_phase5_semantic_evaluator_prompt(request)
    no_action = build_phase5_semantic_evaluator_no_action_bundle(
        request=request,
        evidence_payloads=validated,
        generator_model_identity=generator_model_identity,
    )
    text_evidence: dict[str, object] = {}
    if profile.evidence_projection == "full_frozen_evidence":
        for evidence_id, raw in validated.items():
            if evidence_id == "browser_screenshot":
                continue
            try:
                text_evidence[evidence_id] = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise Phase5SemanticQwenRuntimeError(
                    f"text evidence is not JSON: {evidence_id}"
                ) from exc
    else:
        text_evidence = {
            "validated_evidence_identities": [
                item.to_dict() for item in request.evidence
            ],
            "review_items": [
                item.to_dict() for item in request.review_items
            ],
            "projection_boundary": (
                "All six frozen evidence payloads were validated before this "
                "low-GPU smoke projection. The model receives the review "
                "items, identities, and screenshot only; this is not full "
                "semantic-quality evidence."
            ),
        }
    model_input = _canonical(
        {
            "schema_version": MODEL_INPUT_SCHEMA_VERSION,
            "case_id": request.case_id,
            "evaluator_prompt": json.loads(prompt.decode("utf-8")),
            "frozen_text_evidence": text_evidence,
            "screenshot_evidence_id": "browser_screenshot",
            "evidence_projection": profile.evidence_projection,
            "instructions": (
                "Use the attached screenshot and frozen evidence. Return only "
                "one JSON object matching required_output_shape. Do not use "
                "Markdown fences or text outside the JSON object."
            ),
        }
    )
    return {
        "request": request,
        "request_raw": request_raw,
        "evidence_payloads": validated,
        "prompt_raw": prompt,
        "model_input_raw": model_input,
        "screenshot_path": audit_root / "browser_screenshot.png",
        "no_action_bundle": no_action,
    }


class _TokenDeltaStreamer:
    def __init__(self, *, tokenizer: object) -> None:
        self._tokenizer = tokenizer
        self._prompt_seen = False

    def put(self, value: object) -> None:
        token_ids = value.tolist()  # type: ignore[union-attr]
        if not self._prompt_seen:
            self._prompt_seen = True
            return
        if (
            type(token_ids) is list
            and len(token_ids) == 1
            and type(token_ids[0]) is list
        ):
            token_ids = token_ids[0]
        if type(token_ids) is not list:
            return
        text = self._tokenizer.decode(  # type: ignore[union-attr]
            token_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        if text:
            _emit_worker_event("token_delta", text.encode("utf-8"))

    def end(self) -> None:
        return None


def _emit_worker_event(event: str, raw: bytes = b"") -> None:
    payload = {
        "schema_version": "req2web.phase5.semantic_qwen_stream.v1",
        "event": event,
        "delta_base64": base64.b64encode(raw).decode("ascii"),
    }
    sys.stderr.buffer.write(_canonical(payload) + b"\n")
    sys.stderr.buffer.flush()


def _load_profile(path: Path) -> SemanticQwenRuntimeProfile:
    raw = _read_bytes(path, "semantic runtime profile")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5SemanticQwenRuntimeError(
            "semantic runtime profile is not JSON"
        ) from exc
    if not isinstance(value, Mapping):
        raise Phase5SemanticQwenRuntimeError(
            "semantic runtime profile is not an object"
        )
    profile = semantic_qwen_runtime_profile(str(value.get("profile_name")))
    if value != profile.to_dict():
        raise Phase5SemanticQwenRuntimeError(
            "semantic runtime profile replay drifted"
        )
    return profile


def _worker_execute(
    *,
    model_root: Path,
    profile_path: Path,
    model_input_path: Path,
    screenshot_path: Path,
    screenshot_sha256: str,
    generation_started_path: Path,
    raw_output_path: Path,
) -> dict[str, object]:
    profile = _load_profile(profile_path)
    model_input_raw = _read_bytes(model_input_path, "semantic model input")
    screenshot_raw = _read_bytes(screenshot_path, "semantic screenshot")
    if _sha(screenshot_raw) != screenshot_sha256:
        raise Phase5SemanticQwenRuntimeError(
            "semantic screenshot identity drifted"
        )
    if not model_root.is_dir() or model_root.is_symlink():
        raise Phase5SemanticQwenRuntimeError("model root is invalid")

    _emit_worker_event("load_started")
    import torch
    import transformers
    from PIL import Image
    from req2web_runtime.phase4_local_qwen_f4 import (
        F4CompleteSingleJSONStoppingCriteria,
    )

    if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
        raise Phase5SemanticQwenRuntimeError("CUDA GPU0 is unavailable")
    properties = torch.cuda.get_device_properties(0)
    total_vram = int(properties.total_memory)
    if total_vram < profile.min_total_vram_bytes:
        raise Phase5SemanticQwenRuntimeError(
            "GPU VRAM is below the selected semantic profile"
        )
    torch.cuda.empty_cache()
    processor = transformers.AutoProcessor.from_pretrained(
        str(model_root),
        local_files_only=True,
        trust_remote_code=False,
    )
    load_kwargs: dict[str, object] = {
        "local_files_only": True,
        "trust_remote_code": False,
        "device_map": {"": 0},
        "dtype": torch.bfloat16,
        "low_cpu_mem_usage": True,
    }
    if profile.quantization == "nf4":
        load_kwargs["quantization_config"] = transformers.BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
    model = transformers.AutoModelForImageTextToText.from_pretrained(
        str(model_root),
        **load_kwargs,
    )
    model.eval()
    if profile.quantization == "nf4" and (
        getattr(model, "is_loaded_in_4bit", False) is not True
    ):
        raise Phase5SemanticQwenRuntimeError(
            "low-GPU semantic model is not loaded in 4-bit"
        )
    if profile.quantization == "none" and (
        getattr(model, "is_loaded_in_4bit", False) is True
        or getattr(model, "is_loaded_in_8bit", False) is True
    ):
        raise Phase5SemanticQwenRuntimeError(
            "high-GPU semantic model was quantized"
        )
    _emit_worker_event("load_completed")

    image = Image.open(screenshot_path).convert("RGB")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {
                    "type": "text",
                    "text": model_input_raw.decode("utf-8"),
                },
            ],
        }
    ]
    encoded = processor.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        return_dict=True,
        return_tensors="pt",
        enable_thinking=False,
    )
    encoded = {
        key: value.to("cuda:0")
        for key, value in encoded.items()
        if hasattr(value, "to")
    }
    input_length = int(encoded["input_ids"].shape[1])
    tokenizer = getattr(processor, "tokenizer", processor)
    stopping = transformers.StoppingCriteriaList(
        [
            F4CompleteSingleJSONStoppingCriteria(
                tokenizer=tokenizer,
                prompt_length=input_length,
            )
        ]
    )
    _write_once(
        generation_started_path,
        _canonical(
            {
                "schema_version": GENERATION_STARTED_SCHEMA_VERSION,
                "model_generate_call_consumed": True,
                "automatic_retry": False,
                "retry_count": 0,
                "started_epoch_seconds": time.time(),
            }
        ),
    )
    _emit_worker_event("generation_started")
    torch.manual_seed(0)
    torch.cuda.manual_seed_all(0)
    generated = model.generate(
        **encoded,
        streamer=_TokenDeltaStreamer(tokenizer=tokenizer),
        stopping_criteria=stopping,
        max_new_tokens=profile.max_new_tokens,
        do_sample=False,
        num_return_sequences=1,
    )
    generated_only = generated[:, input_length:]
    text = processor.batch_decode(
        generated_only,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )[0]
    raw = text.encode("utf-8")
    if not raw:
        raise Phase5SemanticQwenRuntimeError(
            "semantic model returned empty raw bytes"
        )
    _write_once(raw_output_path, raw)
    _emit_worker_event("generation_completed")
    return {
        "schema_version": WORKER_RECEIPT_SCHEMA_VERSION,
        "status": "completed",
        "model_class": type(model).__name__,
        "processor_class": type(processor).__name__,
        "profile": profile.to_dict(),
        "gpu": {
            "device_index": 0,
            "device_name": torch.cuda.get_device_name(0),
            "total_vram_bytes": total_vram,
        },
        "input_token_length": input_length,
        "raw_response_identity": _identity(raw),
        "automatic_retry_count": 0,
    }


def _reader(
    stream: object,
    *,
    chunks: list[bytes],
    mirror: bool,
    capture_path: Path | None,
) -> None:
    handle = (
        capture_path.open("xb")
        if capture_path is not None
        else None
    )
    try:
        while True:
            raw = stream.readline()  # type: ignore[union-attr]
            if not raw:
                break
            chunks.append(raw)
            if handle is not None:
                handle.write(raw)
                handle.flush()
            if mirror:
                sys.stderr.buffer.write(raw)
                sys.stderr.buffer.flush()
        if handle is not None:
            os.fsync(handle.fileno())
    finally:
        if handle is not None:
            handle.close()


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def run_phase5_semantic_qwen(
    *,
    audit_root: Path,
    result_package_root: Path,
    model_root: Path,
    result_root: Path,
    profile_name: str,
    confirm_model_action: bool,
    generator_model_identity: str = (
        f"{MODEL_ID}@{MODEL_REVISION}"
    ),
) -> dict[str, object]:
    """Run one supervised semantic-evaluator call with raw-first capture."""

    if confirm_model_action is not True:
        raise Phase5SemanticQwenRuntimeError(
            "semantic model action requires explicit confirmation"
        )
    if result_root.exists():
        raise Phase5SemanticQwenRuntimeError(
            "semantic result root already exists"
        )
    profile = semantic_qwen_runtime_profile(profile_name)
    prepared = prepare_phase5_semantic_case(
        audit_root=audit_root,
        result_package_root=result_package_root,
        generator_model_identity=generator_model_identity,
        profile_name=profile_name,
    )
    request = prepared["request"]
    evidence = prepared["evidence_payloads"]
    screenshot_path = prepared["screenshot_path"]
    if not isinstance(evidence, Mapping) or not isinstance(
        screenshot_path,
        Path,
    ):
        raise Phase5SemanticQwenRuntimeError(
            "semantic prepared evidence is invalid"
        )
    try:
        result_root.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise Phase5SemanticQwenRuntimeError(
            "semantic result root could not be created"
        ) from exc
    request_raw = prepared["request_raw"]
    prompt_raw = prepared["prompt_raw"]
    model_input_raw = prepared["model_input_raw"]
    if not all(
        type(value) is bytes
        for value in (request_raw, prompt_raw, model_input_raw)
    ):
        raise Phase5SemanticQwenRuntimeError(
            "semantic prepared bytes are invalid"
        )
    profile_raw = _canonical(profile.to_dict())
    screenshot_raw = evidence["browser_screenshot"]
    evidence_inventory = [
        item.to_dict() for item in request.evidence  # type: ignore[union-attr]
    ]
    for path, raw in (
        (result_root / "semantic_alignment_request.json", request_raw),
        (result_root / "evaluator_prompt.json", prompt_raw),
        (result_root / "model_input.json", model_input_raw),
        (result_root / "runtime_profile.json", profile_raw),
        (
            result_root / "evidence_inventory.json",
            _canonical(evidence_inventory),
        ),
        (
            result_root / "no_action_preparation.json",
            _canonical(prepared["no_action_bundle"]),
        ),
    ):
        _write_once(path, raw)
    pre_call = {
        "schema_version": PRE_CALL_SCHEMA_VERSION,
        "case_id": request.case_id,  # type: ignore[union-attr]
        "request_identity": _identity(request_raw),
        "prompt_identity": _identity(prompt_raw),
        "model_input_identity": _identity(model_input_raw),
        "screenshot_identity": _identity(screenshot_raw),
        "profile": profile.to_dict(),
        "model_generate_call_limit": 1,
        "automatic_retry_limit": 0,
        "raw_first": True,
    }
    _write_once(result_root / "pre_call_record.json", _canonical(pre_call))
    generation_started_path = result_root / "generation_started.json"
    raw_output_path = result_root / "raw_response.bin"
    command = [
        sys.executable,
        "-m",
        "req2web_evaluation.phase5_semantic_qwen_runtime",
        "--worker",
        "--model-root",
        str(model_root),
        "--profile-path",
        str(result_root / "runtime_profile.json"),
        "--model-input-path",
        str(result_root / "model_input.json"),
        "--screenshot-path",
        str(screenshot_path),
        "--screenshot-sha256",
        _sha(screenshot_raw),
        "--generation-started-path",
        str(generation_started_path),
        "--raw-output-path",
        str(raw_output_path),
    ]
    creationflags = (
        subprocess.CREATE_NEW_PROCESS_GROUP
        if os.name == "nt"
        else 0
    )
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(Path.cwd()),
        env={
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "CUDA_VISIBLE_DEVICES": "0",
        },
        start_new_session=(os.name != "nt"),
        creationflags=creationflags,
    )
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_thread = threading.Thread(
        target=_reader,
        args=(process.stdout,),
        kwargs={
            "chunks": stdout_chunks,
            "mirror": False,
            "capture_path": None,
        },
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_reader,
        args=(process.stderr,),
        kwargs={
            "chunks": stderr_chunks,
            "mirror": True,
            "capture_path": result_root / "worker_stderr.jsonl",
        },
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    timed_out = False
    try:
        process.wait(timeout=profile.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_worker(process)
    stdout_thread.join(timeout=20)
    stderr_thread.join(timeout=20)
    stdout_raw = b"".join(stdout_chunks)
    _write_once(result_root / "worker_stdout.bin", stdout_raw or b"\n")
    worker_receipt: dict[str, object] | None = None
    worker_error: str | None = None
    if stdout_raw.strip():
        try:
            value = json.loads(stdout_raw.decode("utf-8"))
            if isinstance(value, Mapping):
                worker_receipt = copy.deepcopy(dict(value))
        except (UnicodeDecodeError, json.JSONDecodeError):
            worker_error = "worker_stdout_invalid"
    if timed_out:
        worker_error = "worker_timeout"
    elif process.returncode != 0:
        worker_error = "worker_failed"
    semantic_result = None
    parse_error = None
    if raw_output_path.is_file():
        raw = raw_output_path.read_bytes()
        try:
            semantic_result = parse_phase5_semantic_evaluator_raw_response(
                request=request,  # type: ignore[arg-type]
                raw_response=raw,
                evaluator_model_identity=generator_model_identity,
                generator_model_identity=generator_model_identity,
            )
            _write_once(
                result_root / "semantic_alignment_result.json",
                _canonical(semantic_result.to_dict(request)),
            )
        except (Phase5SemanticEvaluatorError, ValueError) as exc:
            parse_error = type(exc).__name__
    status = (
        "semantic_alignment_complete_pending_owner_review"
        if semantic_result is not None and worker_error is None
        else "failed_closed"
    )
    owner_review_required = bool(
        semantic_result is not None
        and any(
            row.verdict in {"partial", "unknown"}
            for row in semantic_result.verdicts
        )
    )
    summary = {
        "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
        "runtime_schema_version": RUNTIME_SCHEMA_VERSION,
        "status": status,
        "case_id": request.case_id,  # type: ignore[union-attr]
        "profile": profile.to_dict(),
        "worker_exit_code": process.returncode,
        "worker_timed_out": timed_out,
        "worker_error": worker_error,
        "worker_receipt": worker_receipt,
        "generate_started_count": int(generation_started_path.is_file()),
        "automatic_retry_count": 0,
        "raw_response_formed": raw_output_path.is_file(),
        "raw_response_identity": (
            _identity(raw_output_path.read_bytes())
            if raw_output_path.is_file()
            else None
        ),
        "parse_error": parse_error,
        "semantic_alignment_executed": semantic_result is not None,
        "owner_review_required": owner_review_required,
        "formal_quality_claimed": False,
        "finalized_without_model_call": False,
        "claim_boundary": (
            "local low-GPU quantized smoke only"
            if profile.profile_name == LOW_GPU_PROFILE
            else "bounded high-GPU semantic evaluation pending owner review"
        ),
    }
    _write_once(result_root / "run_summary.json", _canonical(summary))
    return summary


def finalize_existing_phase5_semantic_qwen_result(
    *,
    audit_root: Path,
    result_package_root: Path,
    result_root: Path,
    profile_name: str,
    generator_model_identity: str = (
        f"{MODEL_ID}@{MODEL_REVISION}"
    ),
) -> dict[str, object]:
    """Finish one captured raw response without loading or calling a model."""

    if not result_root.is_dir() or result_root.is_symlink():
        raise Phase5SemanticQwenRuntimeError(
            "existing semantic result root is invalid"
        )
    if (result_root / "run_summary.json").exists():
        raise Phase5SemanticQwenRuntimeError(
            "existing semantic result is already terminal"
        )
    profile = semantic_qwen_runtime_profile(profile_name)
    stored_profile = _load_profile(result_root / "runtime_profile.json")
    if stored_profile != profile:
        raise Phase5SemanticQwenRuntimeError(
            "existing semantic profile drifted"
        )
    prepared = prepare_phase5_semantic_case(
        audit_root=audit_root,
        result_package_root=result_package_root,
        generator_model_identity=generator_model_identity,
        profile_name=profile_name,
    )
    request = prepared["request"]
    request_raw = prepared["request_raw"]
    prompt_raw = prepared["prompt_raw"]
    model_input_raw = prepared["model_input_raw"]
    if (
        not isinstance(request_raw, bytes)
        or not isinstance(prompt_raw, bytes)
        or not isinstance(model_input_raw, bytes)
        or _read_bytes(
            result_root / "semantic_alignment_request.json",
            "stored semantic request",
        )
        != request_raw
        or _read_bytes(
            result_root / "evaluator_prompt.json",
            "stored evaluator prompt",
        )
        != prompt_raw
        or _read_bytes(
            result_root / "model_input.json",
            "stored semantic model input",
        )
        != model_input_raw
    ):
        raise Phase5SemanticQwenRuntimeError(
            "existing semantic pre-call artifacts drifted"
        )
    generation_started_path = result_root / "generation_started.json"
    raw_path = result_root / "raw_response.bin"
    raw = _read_bytes(raw_path, "captured semantic raw response")
    semantic_result = parse_phase5_semantic_evaluator_raw_response(
        request=request,  # type: ignore[arg-type]
        raw_response=raw,
        evaluator_model_identity=generator_model_identity,
        generator_model_identity=generator_model_identity,
    )
    result_path = result_root / "semantic_alignment_result.json"
    result_raw = _canonical(semantic_result.to_dict(request))
    if result_path.exists():
        if result_path.read_bytes() != result_raw:
            raise Phase5SemanticQwenRuntimeError(
                "existing semantic parsed result drifted"
            )
    else:
        _write_once(result_path, result_raw)
    stdout_raw = _read_bytes(
        result_root / "worker_stdout.bin",
        "semantic worker stdout",
    )
    try:
        worker_value = json.loads(stdout_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5SemanticQwenRuntimeError(
            "semantic worker stdout is not JSON"
        ) from exc
    if not isinstance(worker_value, Mapping):
        raise Phase5SemanticQwenRuntimeError(
            "semantic worker receipt is invalid"
        )
    worker_receipt = copy.deepcopy(dict(worker_value))
    if (
        worker_receipt.get("status") != "completed"
        or worker_receipt.get("raw_response_identity") != _identity(raw)
        or not generation_started_path.is_file()
    ):
        raise Phase5SemanticQwenRuntimeError(
            "semantic worker completion binding drifted"
        )
    owner_review_required = any(
        row.verdict in {"partial", "unknown"}
        for row in semantic_result.verdicts
    )
    summary = {
        "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
        "runtime_schema_version": RUNTIME_SCHEMA_VERSION,
        "status": "semantic_alignment_complete_pending_owner_review",
        "case_id": request.case_id,  # type: ignore[union-attr]
        "profile": profile.to_dict(),
        "worker_exit_code": 0,
        "worker_timed_out": False,
        "worker_error": None,
        "worker_receipt": worker_receipt,
        "generate_started_count": 1,
        "automatic_retry_count": 0,
        "raw_response_formed": True,
        "raw_response_identity": _identity(raw),
        "parse_error": None,
        "semantic_alignment_executed": True,
        "owner_review_required": owner_review_required,
        "formal_quality_claimed": False,
        "finalized_without_model_call": True,
        "claim_boundary": (
            "local low-GPU quantized smoke only"
            if profile.profile_name == LOW_GPU_PROFILE
            else "bounded high-GPU semantic evaluation pending owner review"
        ),
    }
    _write_once(result_root / "run_summary.json", _canonical(summary))
    return summary


def validate_existing_phase5_semantic_qwen_result(
    *,
    audit_root: Path,
    result_package_root: Path,
    result_root: Path,
    profile_name: str,
    generator_model_identity: str = (
        f"{MODEL_ID}@{MODEL_REVISION}"
    ),
) -> dict[str, object]:
    """Replay one terminal semantic result without model, GPU, or writes."""

    if not result_root.is_dir() or result_root.is_symlink():
        raise Phase5SemanticQwenRuntimeError(
            "terminal semantic result root is invalid"
        )
    profile = semantic_qwen_runtime_profile(profile_name)
    if _load_profile(result_root / "runtime_profile.json") != profile:
        raise Phase5SemanticQwenRuntimeError(
            "terminal semantic profile drifted"
        )
    prepared = prepare_phase5_semantic_case(
        audit_root=audit_root,
        result_package_root=result_package_root,
        generator_model_identity=generator_model_identity,
        profile_name=profile_name,
    )
    request = prepared["request"]
    raw = _read_bytes(
        result_root / "raw_response.bin",
        "terminal semantic raw response",
    )
    semantic_result = parse_phase5_semantic_evaluator_raw_response(
        request=request,  # type: ignore[arg-type]
        raw_response=raw,
        evaluator_model_identity=generator_model_identity,
        generator_model_identity=generator_model_identity,
    )
    if _read_bytes(
        result_root / "semantic_alignment_result.json",
        "terminal semantic result",
    ) != _canonical(semantic_result.to_dict(request)):
        raise Phase5SemanticQwenRuntimeError(
            "terminal semantic parsed result drifted"
        )
    summary_raw = _read_bytes(
        result_root / "run_summary.json",
        "terminal semantic summary",
    )
    try:
        summary_value = json.loads(summary_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5SemanticQwenRuntimeError(
            "terminal semantic summary is not JSON"
        ) from exc
    if not isinstance(summary_value, Mapping):
        raise Phase5SemanticQwenRuntimeError(
            "terminal semantic summary is invalid"
        )
    summary = copy.deepcopy(dict(summary_value))
    if (
        _canonical(summary) != summary_raw
        or summary.get("schema_version")
        not in {
            LEGACY_RUN_SUMMARY_SCHEMA_VERSION,
            RUN_SUMMARY_SCHEMA_VERSION,
        }
        or summary.get("runtime_schema_version") != RUNTIME_SCHEMA_VERSION
        or summary.get("status")
        != "semantic_alignment_complete_pending_owner_review"
        or summary.get("case_id") != request.case_id  # type: ignore[union-attr]
        or summary.get("profile") != profile.to_dict()
        or summary.get("generate_started_count") != 1
        or summary.get("automatic_retry_count") != 0
        or summary.get("raw_response_formed") is not True
        or summary.get("raw_response_identity") != _identity(raw)
        or summary.get("semantic_alignment_executed") is not True
        or summary.get("formal_quality_claimed") is not False
    ):
        raise Phase5SemanticQwenRuntimeError(
            "terminal semantic summary drifted"
        )
    return summary


def _worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--profile-path", type=Path)
    parser.add_argument("--model-input-path", type=Path)
    parser.add_argument("--screenshot-path", type=Path)
    parser.add_argument("--screenshot-sha256")
    parser.add_argument("--generation-started-path", type=Path)
    parser.add_argument("--raw-output-path", type=Path)
    return parser


def _worker_entry(argv: Sequence[str]) -> int:
    args = _worker_parser().parse_args(argv)
    if args.worker is not True:
        return 2
    try:
        receipt = _worker_execute(
            model_root=args.model_root,
            profile_path=args.profile_path,
            model_input_path=args.model_input_path,
            screenshot_path=args.screenshot_path,
            screenshot_sha256=args.screenshot_sha256,
            generation_started_path=args.generation_started_path,
            raw_output_path=args.raw_output_path,
        )
        sys.stdout.buffer.write(_canonical(receipt))
        sys.stdout.buffer.flush()
        return 0
    except Exception as exc:
        _emit_worker_event("worker_failed", str(exc).encode("utf-8"))
        sys.stdout.buffer.write(
            _canonical(
                {
                    "schema_version": WORKER_RECEIPT_SCHEMA_VERSION,
                    "status": "failed_closed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
        )
        sys.stdout.buffer.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(_worker_entry(sys.argv[1:]))


__all__ = [
    "HIGH_GPU_PROFILE",
    "LOW_GPU_PROFILE",
    "MODEL_ID",
    "MODEL_REVISION",
    "Phase5SemanticQwenRuntimeError",
    "SemanticQwenRuntimeProfile",
    "finalize_existing_phase5_semantic_qwen_result",
    "prepare_phase5_semantic_case",
    "run_phase5_semantic_qwen",
    "semantic_qwen_runtime_profile",
    "validate_existing_phase5_semantic_qwen_result",
]
