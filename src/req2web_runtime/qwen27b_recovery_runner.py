"""Bounded Qwen3.5-27B recovery-pilot GPU runner.

The runner is intentionally narrow: it validates the no-GPU predeploy chain,
loads one exact local model on ``cuda:0``, performs one greedy call for each of
the two approved synthetic cases, and persists the complete raw response before
publishing a bounded run manifest. Incremental text is observational only.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import signal
import subprocess
import sys
import time
from typing import Any, Mapping, Protocol, Sequence, TextIO
import uuid

from req2web_runtime.model_text_stream import (
    MODEL_TEXT_STREAM_MODES,
    ModelTextStreamSession,
    create_model_text_stream_sink,
)
from req2web_runtime import qwen27b_predeploy as _predeploy
from req2web_runtime.qwen27b_case_bundle import (
    CASE_IDS,
    Qwen27BCaseBundle,
    Qwen27BCaseBundleError,
    compose_qwen27b_model_text,
    validate_qwen27b_case_bundle,
)
from req2web_runtime.qwen27b_predeploy import (
    MODEL_REPOSITORY,
    MODEL_REVISION,
    Qwen27BPredeployError,
    Qwen27BPredeployManifest,
    Qwen27BPredeployReadyNoGpu,
    TRANSFER_COPIES,
    collect_directory_inventory,
    collect_file_inventory,
    validate_predeploy_ready_no_gpu_against,
    validate_repository_archive_authority,
)

RUN_MANIFEST_SCHEMA = "req2web.runtime.qwen35_27b_recovery_run_manifest.v1"
RUN_MANIFEST_FILENAME = "recovery_run_manifest.json"
FAILURE_FILENAME = "recovery_run_failure.json"
EXPECTED_DEVICE = "cuda:0"
EXPECTED_GPU_INDEX = 0
EXPECTED_GPU_NAMES = (
    "NVIDIA RTX PRO 6000 Blackwell",
    "NVIDIA RTX PRO 6000 Blackwell Server Edition",
    "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
)
MIN_TOTAL_VRAM_BYTES = 90 * 1024**3
MIN_FREE_VRAM_BEFORE_LOAD_BYTES = 80 * 1024**3
MAX_INPUT_TOKENS = 8192
MAX_NEW_TOKENS = 4096
MAX_RAW_BYTES_PER_CASE = 2 * 1024**2
MAX_TOTAL_RAW_BYTES = 4 * 1024**2
DEFAULT_TIMEOUT_SECONDS = 2 * 60 * 60
_HEX64 = re.compile(r"[0-9a-f]{64}")
_GPU_UUID = re.compile(r"GPU-[A-Za-z0-9-]{4,128}")


class Qwen27BRecoveryRunnerError(RuntimeError):
    """Raised when the recovery pilot must fail closed."""


@dataclass(frozen=True)
class BackendGeneration:
    """One backend generation result before authoritative raw persistence."""

    text: str
    input_tokens: int
    generated_tokens: int
    elapsed_ms: int
    stream_event_count: int
    stream_text: str


class RecoveryBackend(Protocol):
    """Minimal injectable backend used by the real worker and focused tests."""

    def runtime_facts(self, expected_versions: Mapping[str, str]) -> dict[str, Any]:
        """Validate and return the actual runtime and selected GPU facts."""

    def load(self, model_root: Path) -> None:
        """Load the exact local model under the frozen runtime profile."""

    def generate(
        self,
        model_text: str,
        case_id: str,
        *,
        stream_output: str,
        stream: TextIO,
    ) -> BackendGeneration:
        """Perform exactly one generation call."""

    def loaded_facts(self) -> dict[str, Any]:
        """Return device, dtype and post-load memory facts."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _progress(event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    sys.stderr.write(_canonical(payload).decode("utf-8") + "\n")
    sys.stderr.flush()


def _stable_error_code(exc: BaseException) -> str:
    if isinstance(exc, Qwen27BRecoveryRunnerError) and str(exc):
        return str(exc)
    return "recovery_worker_failed"


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise Qwen27BRecoveryRunnerError(f"{label}_not_regular_file")
    return path.resolve(strict=True)


def _existing_root(path: Path | str, label: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_dir():
        raise Qwen27BRecoveryRunnerError(f"{label}_invalid")
    return candidate.resolve(strict=True)


def _new_or_empty_root(path: Path | str) -> Path:
    candidate = Path(path)
    if candidate.exists():
        if candidate.is_symlink() or not candidate.is_dir() or any(candidate.iterdir()):
            raise Qwen27BRecoveryRunnerError("result_root_not_empty")
    else:
        candidate.mkdir(parents=True, exist_ok=False)
    return candidate.resolve(strict=True)


def _contained(root: Path, relative: str, *, parents: bool = False) -> Path:
    if (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or any(part in ("", ".", "..") for part in relative.split("/"))
    ):
        raise Qwen27BRecoveryRunnerError("result_relative_path_invalid")
    target = root.joinpath(*relative.split("/"))
    if parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = target.parent.resolve(strict=True)
    if resolved_parent != root and root not in resolved_parent.parents:
        raise Qwen27BRecoveryRunnerError("result_path_escape")
    if target.exists() or target.is_symlink():
        raise Qwen27BRecoveryRunnerError("result_path_already_exists")
    return target


def _existing_contained_file(
    root: Path,
    relative: str,
    label: str,
) -> Path:
    if (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or any(part in ("", ".", "..") for part in relative.split("/"))
    ):
        raise Qwen27BRecoveryRunnerError(f"{label}_relative_path_invalid")
    try:
        target = root.joinpath(*relative.split("/")).resolve(strict=True)
        target.relative_to(root)
    except (OSError, ValueError) as exc:
        raise Qwen27BRecoveryRunnerError(f"{label}_path_escape") from exc
    return _regular_file(target, label)


def _atomic_write(root: Path, relative: str, raw: bytes, *, cap: int) -> Path:
    if type(raw) is not bytes or len(raw) > cap:
        raise Qwen27BRecoveryRunnerError("result_byte_cap_exceeded")
    target = _contained(root, relative, parents=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def _file_row(root: Path, relative: str) -> dict[str, object]:
    path = _regular_file(root.joinpath(*relative.split("/")), "result_artifact")
    raw = path.read_bytes()
    return {
        "relative_path": relative,
        "byte_length": len(raw),
        "sha256": _sha(raw),
    }


def _runtime_versions(manifest: Qwen27BPredeployManifest) -> dict[str, str]:
    data = manifest.to_dict()["runtime_inventory"]
    return {
        "python": data["python_version"],
        "torch": data["torch_version"],
        "transformers": data["transformers_version"],
        "cuda": data["cuda_version"],
    }


def _validate_runtime_root_against_manifest(
    runtime_root: Path | str,
    manifest: Qwen27BPredeployManifest,
) -> tuple[Path, dict[str, Any]]:
    """Recompute every frozen runtime row from the selected runtime root."""

    root = _existing_root(runtime_root, "runtime_root")
    expected = manifest.to_dict()["runtime_inventory"]
    try:
        actual = _predeploy.collect_runtime_root_inventory(root)
    except Qwen27BPredeployError as exc:
        raise Qwen27BRecoveryRunnerError(
            "runtime_inventory_live_validation_failed"
        ) from exc
    if actual != expected:
        raise Qwen27BRecoveryRunnerError("runtime_inventory_live_mismatch")
    return root, _runtime_inventory_binding(actual)


def _worker_runtime_binding(
    runtime_root: Path,
    runtime_binding: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        executable = Path(sys.executable).resolve(strict=True)
        relative = executable.relative_to(runtime_root).as_posix()
    except (OSError, ValueError) as exc:
        raise Qwen27BRecoveryRunnerError(
            "worker_python_outside_runtime_root"
        ) from exc
    expected_row = next(
        (
            row
            for row in runtime_binding["files"]
            if row["relative_path"] == relative
        ),
        None,
    )
    if expected_row is None:
        raise Qwen27BRecoveryRunnerError(
            "worker_python_missing_from_runtime_inventory"
        )
    raw = _regular_file(executable, "worker_python").read_bytes()
    if expected_row != {
        "relative_path": relative,
        "bytes": len(raw),
        "sha256": _sha(raw),
    }:
        raise Qwen27BRecoveryRunnerError("worker_python_inventory_mismatch")
    return {
        "python_executable_relative_path": relative,
        "runtime_inventory_id": runtime_binding["inventory_id"],
        "runtime_inventory_sha256": _sha(_canonical(runtime_binding)),
        "runtime_tree_sha256": runtime_binding["tree_sha256"],
    }


def _validate_generation(result: BackendGeneration, case_id: str) -> None:
    if type(result) is not BackendGeneration:
        raise Qwen27BRecoveryRunnerError("backend_generation_exact_type_required")
    if type(result.text) is not str or not result.text:
        raise Qwen27BRecoveryRunnerError("empty_raw_response")
    for value, label in (
        (result.input_tokens, "input_tokens"),
        (result.generated_tokens, "generated_tokens"),
        (result.elapsed_ms, "generation_elapsed_ms"),
        (result.stream_event_count, "stream_event_count"),
    ):
        if type(value) is not int or value < 0:
            raise Qwen27BRecoveryRunnerError(f"{label}_invalid")
    if result.input_tokens > MAX_INPUT_TOKENS:
        raise Qwen27BRecoveryRunnerError("max_input_tokens_exceeded")
    if result.generated_tokens > MAX_NEW_TOKENS:
        raise Qwen27BRecoveryRunnerError("max_new_tokens_exceeded")
    if type(result.stream_text) is not str:
        raise Qwen27BRecoveryRunnerError("stream_text_invalid")
    if case_id not in CASE_IDS:
        raise Qwen27BRecoveryRunnerError("case_id_invalid")


def _validate_runtime_facts(
    value: object,
    expected_versions: Mapping[str, str],
    expected_gpu_name: str,
    expected_gpu_uuid: str,
) -> dict[str, Any]:
    expected_keys = {
        "python",
        "torch",
        "transformers",
        "cuda",
        "gpu_index",
        "gpu_uuid",
        "gpu_name",
        "total_vram_bytes",
        "free_vram_bytes_before_load",
        "torch_gpu_name",
        "torch_gpu_uuid",
        "torch_total_vram_bytes",
        "device",
        "dtype",
        "quantization",
        "cpu_offload",
        "device_map",
    }
    if type(value) is not dict or set(value) != expected_keys:
        raise Qwen27BRecoveryRunnerError("runtime_facts_shape_invalid")
    if (
        expected_gpu_name not in EXPECTED_GPU_NAMES
        or type(expected_gpu_uuid) is not str
        or not _GPU_UUID.fullmatch(expected_gpu_uuid)
    ):
        raise Qwen27BRecoveryRunnerError("expected_gpu_identity_invalid")
    if any(value[key] != expected for key, expected in expected_versions.items()):
        raise Qwen27BRecoveryRunnerError("runtime_version_binding_invalid")
    if (
        value["gpu_index"] != EXPECTED_GPU_INDEX
        or type(value["gpu_uuid"]) is not str
        or not value["gpu_uuid"]
        or value["gpu_uuid"] != expected_gpu_uuid
        or value["torch_gpu_uuid"] not in (None, expected_gpu_uuid)
        or value["gpu_name"] != expected_gpu_name
        or value["torch_gpu_name"] != expected_gpu_name
        or type(value["total_vram_bytes"]) is not int
        or type(value["torch_total_vram_bytes"]) is not int
        or type(value["free_vram_bytes_before_load"]) is not int
        or value["total_vram_bytes"] < MIN_TOTAL_VRAM_BYTES
        or value["torch_total_vram_bytes"] < MIN_TOTAL_VRAM_BYTES
        or value["free_vram_bytes_before_load"]
        < MIN_FREE_VRAM_BEFORE_LOAD_BYTES
        or value["device"] != EXPECTED_DEVICE
        or value["dtype"] != "bf16"
        or value["quantization"] != "none"
        or value["cpu_offload"] is not False
        or value["device_map"] != "none"
    ):
        raise Qwen27BRecoveryRunnerError("gpu_profile_mismatch")
    return dict(value)


def _validate_loaded_facts(value: object) -> dict[str, Any]:
    expected_keys = {
        "device",
        "dtype",
        "quantization",
        "cpu_offload",
        "device_map",
        "allocated_vram_bytes",
        "reserved_vram_bytes",
    }
    if type(value) is not dict or set(value) != expected_keys:
        raise Qwen27BRecoveryRunnerError("loaded_facts_shape_invalid")
    if (
        value["device"] != EXPECTED_DEVICE
        or value["dtype"] != "bf16"
        or value["quantization"] != "none"
        or value["cpu_offload"] is not False
        or value["device_map"] != "none"
        or type(value["allocated_vram_bytes"]) is not int
        or type(value["reserved_vram_bytes"]) is not int
        or value["allocated_vram_bytes"] < 1
        or value["reserved_vram_bytes"] < value["allocated_vram_bytes"]
    ):
        raise Qwen27BRecoveryRunnerError("loaded_profile_invalid")
    return dict(value)


class TransformersRecoveryBackend:
    """Lazy real backend fixed to the approved single-GPU BF16 profile."""

    def __init__(self) -> None:
        self._torch: Any = None
        self._transformers: Any = None
        self._processor: Any = None
        self._model: Any = None
        self._text_streamer_type: Any = None
        self._runtime: dict[str, Any] | None = None

    @staticmethod
    def _normalise_gpu_uuid(value: object) -> str:
        text = str(value)
        if re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
            text,
        ):
            return "GPU-" + text
        return text

    @staticmethod
    def _nvidia_smi() -> dict[str, Any]:
        command = [
            "nvidia-smi",
            "--id=0",
            "--query-gpu=index,uuid,name,memory.total,memory.free",
            "--format=csv,noheader,nounits",
        ]
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Qwen27BRecoveryRunnerError("nvidia_smi_unavailable") from exc
        lines = result.stdout.strip().splitlines()
        if result.returncode != 0 or len(lines) != 1:
            raise Qwen27BRecoveryRunnerError("nvidia_smi_invalid")
        parts = [part.strip() for part in lines[0].split(",")]
        if len(parts) != 5:
            raise Qwen27BRecoveryRunnerError("nvidia_smi_invalid")
        try:
            index = int(parts[0])
            total = int(parts[3]) * 1024**2
            free = int(parts[4]) * 1024**2
        except ValueError as exc:
            raise Qwen27BRecoveryRunnerError("nvidia_smi_invalid") from exc
        return {
            "gpu_index": index,
            "gpu_uuid": parts[1],
            "gpu_name": parts[2],
            "total_vram_bytes": total,
            "free_vram_bytes_before_load": free,
        }

    def runtime_facts(self, expected_versions: Mapping[str, str]) -> dict[str, Any]:
        if sys.platform != "linux":
            raise Qwen27BRecoveryRunnerError("linux_worker_required")
        if "CUDA_VISIBLE_DEVICES" in os.environ:
            raise Qwen27BRecoveryRunnerError("cuda_visible_devices_remapping_forbidden")
        import torch
        import transformers
        from transformers import AutoModelForMultimodalLM, AutoProcessor, TextStreamer

        actual_versions = {
            "python": (
                f"{sys.version_info.major}.{sys.version_info.minor}."
                f"{sys.version_info.micro}"
            ),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "cuda": str(torch.version.cuda),
        }
        if actual_versions != dict(expected_versions):
            raise Qwen27BRecoveryRunnerError("runtime_version_binding_invalid")
        if torch.cuda.device_count() != 1 or not torch.cuda.is_bf16_supported():
            raise Qwen27BRecoveryRunnerError("single_cuda_bf16_device_required")
        torch.cuda.set_device(EXPECTED_GPU_INDEX)
        properties = torch.cuda.get_device_properties(EXPECTED_GPU_INDEX)
        observed = self._nvidia_smi()
        torch_gpu_uuid_text = self._normalise_gpu_uuid(
            getattr(properties, "uuid", "")
        )
        torch_gpu_uuid = torch_gpu_uuid_text or None
        if (
            observed["gpu_index"] != EXPECTED_GPU_INDEX
            or properties.name not in EXPECTED_GPU_NAMES
            or observed["gpu_name"] not in EXPECTED_GPU_NAMES
            or (
                torch_gpu_uuid is not None
                and torch_gpu_uuid != observed["gpu_uuid"]
            )
            or properties.total_memory < MIN_TOTAL_VRAM_BYTES
            or observed["total_vram_bytes"] < MIN_TOTAL_VRAM_BYTES
        ):
            raise Qwen27BRecoveryRunnerError("gpu_profile_mismatch")
        self._torch = torch
        self._transformers = transformers
        self._processor_type = AutoProcessor
        self._model_type = AutoModelForMultimodalLM
        self._text_streamer_type = TextStreamer
        self._runtime = {
            **actual_versions,
            **observed,
            "torch_gpu_name": properties.name,
            "torch_gpu_uuid": torch_gpu_uuid,
            "torch_total_vram_bytes": properties.total_memory,
            "device": EXPECTED_DEVICE,
            "dtype": "bf16",
            "quantization": "none",
            "cpu_offload": False,
            "device_map": "none",
        }
        return dict(self._runtime)

    def load(self, model_root: Path) -> None:
        if self._runtime is None:
            raise Qwen27BRecoveryRunnerError("runtime_not_validated")
        self._processor = self._processor_type.from_pretrained(
            str(model_root),
            local_files_only=True,
            trust_remote_code=False,
        )
        self._model = self._model_type.from_pretrained(
            str(model_root),
            local_files_only=True,
            trust_remote_code=False,
            dtype=self._torch.bfloat16,
            device_map=None,
            low_cpu_mem_usage=True,
        )
        self._model = self._model.to(EXPECTED_DEVICE)
        self._model.eval()
        devices: set[str] = set()
        floating_dtypes: set[str] = set()
        for parameter in self._model.parameters():
            devices.add(str(parameter.device))
            if parameter.is_floating_point():
                floating_dtypes.add(str(parameter.dtype))
        if devices != {EXPECTED_DEVICE} or floating_dtypes != {"torch.bfloat16"}:
            raise Qwen27BRecoveryRunnerError("loaded_model_device_or_dtype_invalid")

    @staticmethod
    def _validated_inputs(inputs: object) -> tuple[str, ...]:
        if not isinstance(inputs, Mapping):
            raise Qwen27BRecoveryRunnerError("processor_output_mapping_required")
        keys = set(inputs)
        required = {"input_ids", "attention_mask"}
        allowed = required | {"mm_token_type_ids"}
        multimedia = [
            key
            for key in keys
            if key == "pixel_values"
            or key.startswith(("image_", "video_", "pixel_"))
            or "image" in key
            or "video" in key
        ]
        if multimedia or not required.issubset(keys) or not keys.issubset(allowed):
            raise Qwen27BRecoveryRunnerError("text_only_processor_contract_invalid")
        return tuple(sorted(keys))

    def generate(
        self,
        model_text: str,
        case_id: str,
        *,
        stream_output: str,
        stream: TextIO,
    ) -> BackendGeneration:
        if self._model is None or self._processor is None:
            raise Qwen27BRecoveryRunnerError("model_not_loaded")
        messages = [
            {"role": "user", "content": [{"type": "text", "text": model_text}]}
        ]
        rendered = self._processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        inputs = self._processor(text=[rendered], return_tensors="pt")
        keys = self._validated_inputs(inputs)
        inputs = {key: inputs[key].to(EXPECTED_DEVICE) for key in keys}
        input_tokens = int(inputs["input_ids"].shape[1])
        if input_tokens > MAX_INPUT_TOKENS:
            raise Qwen27BRecoveryRunnerError("max_input_tokens_exceeded")

        session: ModelTextStreamSession | None = None
        streamer = None
        if stream_output != "off":
            session = ModelTextStreamSession(
                case_id,
                create_model_text_stream_sink(stream_output, stream=stream),
            )
            session.start()

            class _SessionTextStreamer(self._text_streamer_type):
                def on_finalized_text(
                    inner_self, text: str, stream_end: bool = False
                ) -> None:
                    if text:
                        session.text_delta(text)
                    if stream_end and not session.terminal:
                        session.end()

            streamer = _SessionTextStreamer(
                self._processor,
                skip_prompt=True,
                skip_special_tokens=True,
            )
        started = time.monotonic()
        try:
            generation_args = {
                **inputs,
                "do_sample": False,
                "max_new_tokens": MAX_NEW_TOKENS,
                "num_return_sequences": 1,
            }
            if streamer is not None:
                generation_args["streamer"] = streamer
            with self._torch.inference_mode():
                output = self._model.generate(**generation_args)
            if session is not None and not session.terminal:
                session.end()
        except BaseException as exc:
            if session is not None and not session.terminal:
                session.error(type(exc).__name__)
            raise
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        generated = output[:, input_tokens:]
        generated_tokens = int(generated.shape[1])
        text = self._processor.batch_decode(
            generated, skip_special_tokens=True
        )[0]
        return BackendGeneration(
            text=text,
            input_tokens=input_tokens,
            generated_tokens=generated_tokens,
            elapsed_ms=elapsed_ms,
            stream_event_count=0 if session is None else session.event_count,
            stream_text="" if session is None else session.text,
        )

    def loaded_facts(self) -> dict[str, Any]:
        if self._model is None or self._runtime is None:
            raise Qwen27BRecoveryRunnerError("model_not_loaded")
        return {
            "device": EXPECTED_DEVICE,
            "dtype": "bf16",
            "quantization": "none",
            "cpu_offload": False,
            "device_map": "none",
            "allocated_vram_bytes": self._torch.cuda.memory_allocated(0),
            "reserved_vram_bytes": self._torch.cuda.memory_reserved(0),
        }


def execute_qwen27b_recovery(
    manifest_path: Path | str,
    ready_path: Path | str,
    model_root: Path | str,
    runtime_root: Path | str,
    repository_archive: Path | str,
    repository_archive_manifest: Path | str,
    case_bundle_root: Path | str,
    result_root: Path | str,
    *,
    selected_copy: str,
    expected_gpu_name: str,
    expected_gpu_uuid: str,
    stream_output: str = "off",
    backend: RecoveryBackend | None = None,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    """Execute the fixed two-case pilot and return its validated manifest."""

    if sys.platform != "linux":
        raise Qwen27BRecoveryRunnerError("linux_worker_required")
    if stream_output not in MODEL_TEXT_STREAM_MODES:
        raise Qwen27BRecoveryRunnerError("stream_output_mode_invalid")
    if (
        expected_gpu_name not in EXPECTED_GPU_NAMES
        or type(expected_gpu_uuid) is not str
        or not _GPU_UUID.fullmatch(expected_gpu_uuid)
    ):
        raise Qwen27BRecoveryRunnerError("expected_gpu_identity_invalid")
    manifest_raw = _regular_file(Path(manifest_path), "predeploy_manifest").read_bytes()
    ready_raw = _regular_file(Path(ready_path), "predeploy_ready").read_bytes()
    try:
        manifest = Qwen27BPredeployManifest.from_bytes(manifest_raw)
        ready = Qwen27BPredeployReadyNoGpu.from_bytes(ready_raw)
    except Qwen27BPredeployError as exc:
        raise Qwen27BRecoveryRunnerError("predeploy_artifact_invalid") from exc
    model = _existing_root(model_root, "model_root")
    runtime_root_path, runtime_inventory_binding = _validate_runtime_root_against_manifest(
        runtime_root,
        manifest,
    )
    worker_runtime = _worker_runtime_binding(
        runtime_root_path,
        manifest.to_dict()["runtime_inventory"],
    )
    archive = _regular_file(Path(repository_archive), "repository_archive")
    archive_manifest_path = _regular_file(
        Path(repository_archive_manifest),
        "repository_archive_manifest",
    )
    try:
        bundle = validate_qwen27b_case_bundle(
            case_bundle_root,
            repository_archive_path=archive,
            repository_archive_manifest_path=archive_manifest_path,
        )
        archive_authority = validate_repository_archive_authority(
            archive,
            archive_manifest_path,
        )
        if (
            archive_authority
            != manifest.to_dict()["repository_archive_authority"]
        ):
            raise Qwen27BPredeployError(
                "repository_archive_authority_live_mismatch"
            )
        validate_predeploy_ready_no_gpu_against(
            ready,
            manifest,
            model,
            archive,
            bundle.root,
            selected_copy,
        )
    except (Qwen27BCaseBundleError, Qwen27BPredeployError) as exc:
        raise Qwen27BRecoveryRunnerError("predeploy_live_validation_failed") from exc
    if manifest.to_dict()["profile"]["model"] != {
        "id": "Qwen3.5-27B",
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
    }:
        raise Qwen27BRecoveryRunnerError("model_identity_invalid")
    output = _new_or_empty_root(result_root)
    actual_backend = backend if backend is not None else TransformersRecoveryBackend()
    display = stream if stream is not None else sys.stderr
    cases: list[dict[str, Any]] = []
    raw_paths: list[str] = []
    provider_calls = 0
    run_started = time.monotonic()
    _progress("recovery_predeploy_validated", case_ids=list(CASE_IDS))
    try:
        expected_versions = _runtime_versions(manifest)
        runtime = _validate_runtime_facts(
            actual_backend.runtime_facts(expected_versions),
            expected_versions,
            expected_gpu_name,
            expected_gpu_uuid,
        )
        _progress(
            "recovery_runtime_validated",
            device=runtime["device"],
            gpu_name=runtime["gpu_name"],
            total_vram_bytes=runtime["total_vram_bytes"],
            dtype=runtime["dtype"],
            quantization=runtime["quantization"],
        )
        load_started = time.monotonic()
        actual_backend.load(model)
        model_load_duration_ms = max(
            0, int((time.monotonic() - load_started) * 1000)
        )
        loaded = _validate_loaded_facts(actual_backend.loaded_facts())
        _progress(
            "recovery_model_loaded",
            duration_ms=model_load_duration_ms,
            device=loaded["device"],
            allocated_vram_bytes=loaded.get("allocated_vram_bytes", 0),
            reserved_vram_bytes=loaded.get("reserved_vram_bytes", 0),
        )
        total_raw_bytes = 0
        for case_id in CASE_IDS:
            _progress(
                "recovery_generation_started",
                case_id=case_id,
                provider_call_index=1,
                retry_count=0,
            )
            model_text = compose_qwen27b_model_text(bundle, case_id)
            provider_calls += 1
            generation = actual_backend.generate(
                model_text,
                case_id,
                stream_output=stream_output,
                stream=display,
            )
            _validate_generation(generation, case_id)
            raw = generation.text.encode("utf-8")
            if len(raw) > MAX_RAW_BYTES_PER_CASE:
                raise Qwen27BRecoveryRunnerError("raw_response_cap_exceeded")
            total_raw_bytes += len(raw)
            if total_raw_bytes > MAX_TOTAL_RAW_BYTES:
                raise Qwen27BRecoveryRunnerError("total_raw_response_cap_exceeded")
            relative = f"cases/{case_id}/raw_response.bin"
            _atomic_write(output, relative, raw, cap=MAX_RAW_BYTES_PER_CASE)
            raw_paths.append(relative)
            rate = (
                0.0
                if generation.elapsed_ms == 0
                else round(
                    generation.generated_tokens / (generation.elapsed_ms / 1000.0),
                    6,
                )
            )
            stream_match = (
                None
                if stream_output == "off"
                else generation.stream_text == generation.text
            )
            cases.append(
                {
                    "case_id": case_id,
                    "provider_call_count": 1,
                    "retry_count": 0,
                    "input_tokens": generation.input_tokens,
                    "generated_tokens": generation.generated_tokens,
                    "generation_elapsed_ms": generation.elapsed_ms,
                    "tokens_per_second": rate,
                    "stream_output": stream_output,
                    "stream_event_count": generation.stream_event_count,
                    "stream_matches_final_raw": stream_match,
                    "raw_response": {
                        "relative_path": relative,
                        "byte_length": len(raw),
                        "sha256": _sha(raw),
                    },
                    "downstream_state": "not_started_raw_authoritative",
                }
            )
            _progress(
                "recovery_raw_persisted",
                case_id=case_id,
                byte_length=len(raw),
                sha256=_sha(raw),
                generated_tokens=generation.generated_tokens,
                tokens_per_second=rate,
                stream_matches_final_raw=stream_match,
            )
        inventory = [_file_row(output, relative) for relative in raw_paths]
        body: dict[str, Any] = {
            "schema_version": RUN_MANIFEST_SCHEMA,
            "run_id": "0" * 64,
            "state": "completed_raw_only",
            "model": {
                "repository": MODEL_REPOSITORY,
                "revision": MODEL_REVISION,
                "local_files_only": True,
                "trust_remote_code": False,
            },
            "profile": {
                "device": EXPECTED_DEVICE,
                "gpu_index": EXPECTED_GPU_INDEX,
                "expected_gpu_name": expected_gpu_name,
                "expected_gpu_uuid": expected_gpu_uuid,
                "dtype": "bf16",
                "quantization": "none",
                "cpu_offload": False,
                "device_map": "none",
                "thinking": False,
                "decode": "greedy",
                "max_input_tokens": MAX_INPUT_TOKENS,
                "max_new_tokens": MAX_NEW_TOKENS,
            },
            "predeploy": {
                "manifest_sha256": _sha(manifest_raw),
                "ready_sha256": _sha(ready_raw),
                "case_bundle_sha256": bundle.sha256(),
                "repository_archive_authority": archive_authority,
                "selected_copy": selected_copy,
                "model_inventory": {
                    "inventory_id": manifest.to_dict()[
                        "expected_model_inventory"
                    ]["inventory_id"],
                    "inventory_sha256": _sha(
                        _canonical(
                            manifest.to_dict()["expected_model_inventory"]
                        )
                    ),
                    "tree_sha256": manifest.to_dict()[
                        "expected_model_inventory"
                    ]["tree_sha256"],
                },
                "runtime_inventory": runtime_inventory_binding,
            },
            "worker_runtime": worker_runtime,
            "actual_runtime": runtime,
            "loaded_model": loaded,
            "model_load_duration_ms": model_load_duration_ms,
            "execution": {
                "case_ids": list(CASE_IDS),
                "provider_call_count": provider_calls,
                "retry_count": 0,
                "elapsed_ms": max(0, int((time.monotonic() - run_started) * 1000)),
            },
            "cases": cases,
            "inventory": {
                "files": inventory,
                "file_count": len(inventory),
                "total_bytes": sum(row["byte_length"] for row in inventory),
                "inventory_sha256": _sha(_canonical(inventory)),
            },
            "claims": {
                "h1": False,
                "formal_quality": False,
                "browser_quality": False,
                "evidence_use": False,
                "semantic_success": False,
            },
        }
        body["run_id"] = _sha(_canonical(body))
        manifest_bytes = _canonical(body)
        _atomic_write(output, RUN_MANIFEST_FILENAME, manifest_bytes, cap=1024**2)
        validated = validate_qwen27b_recovery_result_against(
            output,
            manifest_path,
            ready_path,
            model,
            runtime_root_path,
            archive,
            archive_manifest_path,
            bundle.root,
            selected_copy,
        )
        _progress(
            "recovery_run_manifest_persisted",
            run_id=validated["run_id"],
            sha256=_sha(manifest_bytes),
            provider_call_count=provider_calls,
            retry_count=0,
        )
        return validated
    except BaseException as exc:
        failure = {
            "schema_version": "req2web.runtime.qwen35_27b_recovery_failure.v1",
            "state": "failed_closed",
            "error_code": _stable_error_code(exc),
            "provider_call_count": provider_calls,
            "retry_count": 0,
            "completed_raw_paths": list(raw_paths),
        }
        try:
            if not (output / FAILURE_FILENAME).exists():
                _atomic_write(
                    output,
                    FAILURE_FILENAME,
                    _canonical(failure),
                    cap=64 * 1024,
                )
        except Exception:
            pass
        if isinstance(exc, Qwen27BRecoveryRunnerError):
            raise
        raise Qwen27BRecoveryRunnerError("recovery_worker_failed") from exc


def validate_qwen27b_recovery_result(result_root: Path | str) -> dict[str, Any]:
    """Validate the bounded completed raw-result root without model execution."""

    root = _existing_root(result_root, "result_root")
    manifest_path = _regular_file(root / RUN_MANIFEST_FILENAME, "run_manifest")
    try:
        data = json.loads(manifest_path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Qwen27BRecoveryRunnerError("run_manifest_json_invalid") from exc
    if type(data) is not dict:
        raise Qwen27BRecoveryRunnerError("run_manifest_shape_invalid")
    expected_top = {
        "schema_version",
        "run_id",
        "state",
        "model",
        "profile",
        "predeploy",
        "worker_runtime",
        "actual_runtime",
        "loaded_model",
        "model_load_duration_ms",
        "execution",
        "cases",
        "inventory",
        "claims",
    }
    if set(data) != expected_top:
        raise Qwen27BRecoveryRunnerError("run_manifest_shape_invalid")
    if (
        data["schema_version"] != RUN_MANIFEST_SCHEMA
        or data["state"] != "completed_raw_only"
        or type(data["run_id"]) is not str
        or not _HEX64.fullmatch(data["run_id"])
    ):
        raise Qwen27BRecoveryRunnerError("run_manifest_header_invalid")
    identified = dict(data)
    identified["run_id"] = "0" * 64
    if data["run_id"] != _sha(_canonical(identified)):
        raise Qwen27BRecoveryRunnerError("run_manifest_id_invalid")
    if data["model"] != {
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "local_files_only": True,
        "trust_remote_code": False,
    }:
        raise Qwen27BRecoveryRunnerError("run_model_invalid")
    if data["profile"] != {
        "device": EXPECTED_DEVICE,
        "gpu_index": EXPECTED_GPU_INDEX,
        "expected_gpu_name": data["profile"].get("expected_gpu_name"),
        "expected_gpu_uuid": data["profile"].get("expected_gpu_uuid"),
        "dtype": "bf16",
        "quantization": "none",
        "cpu_offload": False,
        "device_map": "none",
        "thinking": False,
        "decode": "greedy",
        "max_input_tokens": MAX_INPUT_TOKENS,
        "max_new_tokens": MAX_NEW_TOKENS,
    }:
        raise Qwen27BRecoveryRunnerError("run_profile_invalid")
    if (
        data["profile"]["expected_gpu_name"] not in EXPECTED_GPU_NAMES
        or type(data["profile"]["expected_gpu_uuid"]) is not str
        or not _GPU_UUID.fullmatch(data["profile"]["expected_gpu_uuid"])
    ):
        raise Qwen27BRecoveryRunnerError("run_profile_invalid")
    predeploy = data["predeploy"]
    if (
        type(predeploy) is not dict
        or set(predeploy)
        != {
            "manifest_sha256",
            "ready_sha256",
            "case_bundle_sha256",
            "repository_archive_authority",
            "selected_copy",
            "model_inventory",
            "runtime_inventory",
        }
        or any(
            type(predeploy[key]) is not str
            or not _HEX64.fullmatch(predeploy[key])
            for key in (
                "manifest_sha256",
                "ready_sha256",
                "case_bundle_sha256",
            )
        )
        or predeploy["selected_copy"] not in TRANSFER_COPIES
    ):
        raise Qwen27BRecoveryRunnerError("run_predeploy_binding_invalid")
    archive_authority = predeploy["repository_archive_authority"]
    if (
        type(archive_authority) is not dict
        or set(archive_authority)
        != {
            "manifest_id",
            "manifest_sha256",
            "commit_sha",
            "tree_sha",
            "archive_byte_length",
            "archive_sha256",
        }
        or any(
            type(archive_authority[key]) is not str
            or not _HEX64.fullmatch(archive_authority[key])
            for key in (
                "manifest_id",
                "manifest_sha256",
                "archive_sha256",
            )
        )
        or any(
            type(archive_authority[key]) is not str
            or not re.fullmatch(r"[0-9a-f]{40}", archive_authority[key])
            for key in ("commit_sha", "tree_sha")
        )
        or type(archive_authority["archive_byte_length"]) is not int
        or archive_authority["archive_byte_length"] < 1
    ):
        raise Qwen27BRecoveryRunnerError("run_archive_authority_invalid")
    model_inventory = predeploy["model_inventory"]
    if (
        type(model_inventory) is not dict
        or set(model_inventory)
        != {"inventory_id", "inventory_sha256", "tree_sha256"}
        or any(
            type(model_inventory[key]) is not str
            or not _HEX64.fullmatch(model_inventory[key])
            for key in model_inventory
        )
    ):
        raise Qwen27BRecoveryRunnerError("run_model_inventory_binding_invalid")
    runtime_inventory = predeploy["runtime_inventory"]
    if (
        type(runtime_inventory) is not dict
        or set(runtime_inventory)
        != {
            "inventory_id",
            "inventory_sha256",
            "tree_sha256",
            "file_count",
            "symlink_count",
            "entry_count",
        }
        or any(
            type(runtime_inventory[key]) is not str
            or not _HEX64.fullmatch(runtime_inventory[key])
            for key in ("inventory_id", "inventory_sha256", "tree_sha256")
        )
        or type(runtime_inventory["file_count"]) is not int
        or runtime_inventory["file_count"] < 1
        or type(runtime_inventory["symlink_count"]) is not int
        or runtime_inventory["symlink_count"] < 0
        or type(runtime_inventory["entry_count"]) is not int
        or runtime_inventory["entry_count"]
        != runtime_inventory["file_count"] + runtime_inventory["symlink_count"]
    ):
        raise Qwen27BRecoveryRunnerError(
            "run_runtime_inventory_binding_invalid"
        )
    worker_runtime = data["worker_runtime"]
    if (
        type(worker_runtime) is not dict
        or set(worker_runtime)
        != {
            "python_executable_relative_path",
            "runtime_inventory_id",
            "runtime_inventory_sha256",
            "runtime_tree_sha256",
        }
        or type(worker_runtime["python_executable_relative_path"]) is not str
        or not worker_runtime["python_executable_relative_path"]
        or any(
            type(worker_runtime[key]) is not str
            or not _HEX64.fullmatch(worker_runtime[key])
            for key in (
                "runtime_inventory_id",
                "runtime_inventory_sha256",
                "runtime_tree_sha256",
            )
        )
        or worker_runtime["runtime_inventory_id"]
        != runtime_inventory["inventory_id"]
        or worker_runtime["runtime_inventory_sha256"]
        != runtime_inventory["inventory_sha256"]
        or worker_runtime["runtime_tree_sha256"]
        != runtime_inventory["tree_sha256"]
    ):
        raise Qwen27BRecoveryRunnerError("run_worker_runtime_binding_invalid")
    pinned_versions = {
        "python": "3.11.15",
        "torch": "2.7.1+cu128",
        "transformers": "5.14.1",
        "cuda": "12.8",
    }
    _validate_runtime_facts(
        data["actual_runtime"],
        pinned_versions,
        data["profile"]["expected_gpu_name"],
        data["profile"]["expected_gpu_uuid"],
    )
    _validate_loaded_facts(data["loaded_model"])
    if (
        type(data["model_load_duration_ms"]) is not int
        or data["model_load_duration_ms"] < 0
    ):
        raise Qwen27BRecoveryRunnerError("run_model_load_duration_invalid")
    execution = data["execution"]
    if (
        type(execution) is not dict
        or set(execution)
        != {
            "case_ids",
            "provider_call_count",
            "retry_count",
            "elapsed_ms",
        }
        or execution.get("case_ids") != list(CASE_IDS)
        or execution.get("provider_call_count") != len(CASE_IDS)
        or execution.get("retry_count") != 0
        or type(execution.get("elapsed_ms")) is not int
        or execution["elapsed_ms"] < 0
    ):
        raise Qwen27BRecoveryRunnerError("run_execution_invalid")
    case_rows = data["cases"]
    if (
        type(case_rows) is not list
        or [row.get("case_id") for row in case_rows if type(row) is dict]
        != list(CASE_IDS)
    ):
        raise Qwen27BRecoveryRunnerError("run_cases_invalid")
    expected_paths: list[str] = []
    for row in case_rows:
        if (
            set(row)
            != {
                "case_id",
                "provider_call_count",
                "retry_count",
                "input_tokens",
                "generated_tokens",
                "generation_elapsed_ms",
                "tokens_per_second",
                "stream_output",
                "stream_event_count",
                "stream_matches_final_raw",
                "raw_response",
                "downstream_state",
            }
            or row.get("provider_call_count") != 1
            or row.get("retry_count") != 0
            or row.get("downstream_state") != "not_started_raw_authoritative"
            or row.get("stream_output") not in MODEL_TEXT_STREAM_MODES
            or type(row.get("input_tokens")) is not int
            or not 0 <= row["input_tokens"] <= MAX_INPUT_TOKENS
            or type(row.get("generated_tokens")) is not int
            or not 0 <= row["generated_tokens"] <= MAX_NEW_TOKENS
            or type(row.get("generation_elapsed_ms")) is not int
            or row["generation_elapsed_ms"] < 0
            or type(row.get("tokens_per_second")) not in (int, float)
            or row["tokens_per_second"] < 0
            or type(row.get("stream_event_count")) is not int
            or row["stream_event_count"] < 0
            or (
                row["stream_output"] == "off"
                and (
                    row["stream_event_count"] != 0
                    or row["stream_matches_final_raw"] is not None
                )
            )
            or (
                row["stream_output"] != "off"
                and type(row["stream_matches_final_raw"]) is not bool
            )
        ):
            raise Qwen27BRecoveryRunnerError("run_case_contract_invalid")
        raw_binding = row.get("raw_response")
        if type(raw_binding) is not dict:
            raise Qwen27BRecoveryRunnerError("run_raw_binding_invalid")
        relative = raw_binding.get("relative_path")
        expected_relative = f"cases/{row['case_id']}/raw_response.bin"
        if relative != expected_relative:
            raise Qwen27BRecoveryRunnerError("run_raw_path_invalid")
        raw = _regular_file(
            root.joinpath(*relative.split("/")), "raw_response"
        ).read_bytes()
        if not raw or len(raw) > MAX_RAW_BYTES_PER_CASE:
            raise Qwen27BRecoveryRunnerError("run_raw_size_invalid")
        if raw_binding != {
            "relative_path": relative,
            "byte_length": len(raw),
            "sha256": _sha(raw),
        }:
            raise Qwen27BRecoveryRunnerError("run_raw_binding_invalid")
        expected_paths.append(relative)
    actual_paths = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    )
    if actual_paths != sorted((*expected_paths, RUN_MANIFEST_FILENAME)):
        raise Qwen27BRecoveryRunnerError("run_result_inventory_paths_invalid")
    rows = [_file_row(root, relative) for relative in expected_paths]
    expected_inventory = {
        "files": rows,
        "file_count": len(rows),
        "total_bytes": sum(row["byte_length"] for row in rows),
        "inventory_sha256": _sha(_canonical(rows)),
    }
    if data["inventory"] != expected_inventory:
        raise Qwen27BRecoveryRunnerError("run_inventory_invalid")
    if data["claims"] != {
        "h1": False,
        "formal_quality": False,
        "browser_quality": False,
        "evidence_use": False,
        "semantic_success": False,
    }:
        raise Qwen27BRecoveryRunnerError("run_claims_invalid")
    if manifest_path.read_bytes() != _canonical(data):
        raise Qwen27BRecoveryRunnerError("run_manifest_not_canonical")
    return data


def validate_qwen27b_recovery_result_against(
    result_root: Path | str,
    manifest_path: Path | str,
    ready_path: Path | str,
    model_root: Path | str,
    runtime_root: Path | str,
    repository_archive: Path | str,
    repository_archive_manifest: Path | str,
    case_bundle_root: Path | str,
    selected_copy: str,
) -> dict[str, Any]:
    """Validate a completed result against every live pre-run artifact."""

    data = validate_qwen27b_recovery_result(result_root)
    manifest_raw = _regular_file(
        Path(manifest_path), "predeploy_manifest"
    ).read_bytes()
    ready_raw = _regular_file(Path(ready_path), "predeploy_ready").read_bytes()
    try:
        manifest = Qwen27BPredeployManifest.from_bytes(manifest_raw)
        ready = Qwen27BPredeployReadyNoGpu.from_bytes(ready_raw)
        bundle = validate_qwen27b_case_bundle(
            case_bundle_root,
            repository_archive_path=repository_archive,
            repository_archive_manifest_path=repository_archive_manifest,
        )
        archive_authority = validate_repository_archive_authority(
            repository_archive,
            repository_archive_manifest,
        )
        validate_predeploy_ready_no_gpu_against(
            ready,
            manifest,
            model_root,
            repository_archive,
            bundle.root,
            selected_copy,
        )
    except (Qwen27BCaseBundleError, Qwen27BPredeployError) as exc:
        raise Qwen27BRecoveryRunnerError(
            "result_against_predeploy_validation_failed"
        ) from exc
    _runtime, runtime_binding = _validate_runtime_root_against_manifest(
        runtime_root,
        manifest,
    )
    manifest_data = manifest.to_dict()
    expected_model = manifest_data["expected_model_inventory"]
    expected_predeploy = {
        "manifest_sha256": _sha(manifest_raw),
        "ready_sha256": _sha(ready_raw),
        "case_bundle_sha256": bundle.sha256(),
        "repository_archive_authority": archive_authority,
        "selected_copy": selected_copy,
        "model_inventory": {
            "inventory_id": expected_model["inventory_id"],
            "inventory_sha256": _sha(_canonical(expected_model)),
            "tree_sha256": expected_model["tree_sha256"],
        },
        "runtime_inventory": runtime_binding,
    }
    if data["predeploy"] != expected_predeploy:
        raise Qwen27BRecoveryRunnerError(
            "result_against_predeploy_binding_mismatch"
        )
    worker_relative = data["worker_runtime"][
        "python_executable_relative_path"
    ]
    runtime_rows = {
        row["relative_path"]: row
        for row in manifest_data["runtime_inventory"]["files"]
    }
    worker_path = _existing_contained_file(
        _runtime,
        worker_relative,
        "worker_python",
    )
    worker_raw = worker_path.read_bytes()
    if runtime_rows.get(worker_relative) != {
        "relative_path": worker_relative,
        "bytes": len(worker_raw),
        "sha256": _sha(worker_raw),
    }:
        raise Qwen27BRecoveryRunnerError(
            "result_against_worker_runtime_mismatch"
        )
    return data


def _return_exact_mapping(
    value: object,
    expected_keys: tuple[str, ...],
    label: str,
) -> dict[str, Any]:
    if (
        type(value) is not dict
        or len(value) != len(expected_keys)
        or set(value) != set(expected_keys)
    ):
        raise Qwen27BRecoveryRunnerError(f"{label}_shape_invalid")
    return dict(value)


def _return_hex(value: object, label: str, length: int = 64) -> str:
    if (
        type(value) is not str
        or not re.fullmatch(rf"[0-9a-f]{{{length}}}", value)
    ):
        raise Qwen27BRecoveryRunnerError(f"{label}_invalid")
    return value


def _return_identified(data: Mapping[str, Any], key: str, label: str) -> None:
    actual = _return_hex(data.get(key), f"{label}_{key}")
    body = copy.deepcopy(dict(data))
    body[key] = "0" * 64
    if _sha(_canonical(body)) != actual:
        raise Qwen27BRecoveryRunnerError(f"{label}_{key}_invalid")


def _load_return_canonical(raw: bytes, label: str) -> dict[str, Any]:
    if type(raw) is not bytes:
        raise Qwen27BRecoveryRunnerError(f"{label}_bytes_required")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise Qwen27BRecoveryRunnerError(
                    f"{label}_duplicate_json_key"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                Qwen27BRecoveryRunnerError(f"{label}_non_finite_json")
            ),
        )
    except UnicodeDecodeError as exc:
        raise Qwen27BRecoveryRunnerError(f"{label}_not_utf8") from exc
    except Qwen27BRecoveryRunnerError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise Qwen27BRecoveryRunnerError(f"{label}_not_json") from exc
    if type(value) is not dict or _canonical(value) != raw:
        raise Qwen27BRecoveryRunnerError(f"{label}_not_canonical")
    return value


def _return_inventory(value: object, kind: str, label: str) -> dict[str, Any]:
    try:
        inventory = _predeploy.Qwen27BPredeployInventory.from_dict(
            _return_exact_mapping(
                value,
                (
                    "schema_version",
                    "inventory_id",
                    "kind",
                    "files",
                    "tree_sha256",
                    "total_bytes",
                ),
                label,
            )
        ).to_dict()
    except Qwen27BPredeployError as exc:
        raise Qwen27BRecoveryRunnerError(f"{label}_invalid") from exc
    if inventory["kind"] != kind:
        raise Qwen27BRecoveryRunnerError(f"{label}_kind_invalid")
    return inventory


def _return_runtime_inventory(value: object, label: str) -> dict[str, Any]:
    data = _return_exact_mapping(
        value,
        (
            "schema_version",
            "inventory_id",
            "runtime_root",
            "runtime_root_sha256",
            "interpreter_relative_path",
            "python_version",
            "torch_version",
            "transformers_version",
            "cuda_version",
            "files",
            "symlinks",
            "file_count",
            "symlink_count",
            "entry_count",
            "total_bytes",
            "max_file_count",
            "max_entry_count",
            "max_total_bytes",
            "tree_sha256",
        ),
        label,
    )
    try:
        return _predeploy._runtime_inventory(_canonical(data))
    except Qwen27BPredeployError as exc:
        raise Qwen27BRecoveryRunnerError(f"{label}_invalid") from exc
def _return_runtime_probe(
    value: object,
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    data = _return_exact_mapping(
        value,
        (
            "schema_version",
            "probe_id",
            "runtime_inventory_sha256",
            "probe_origin",
            "probe_state",
            "observed",
            "gpu_required",
            "gpu_observed",
            "model_loaded",
            "run_occurred",
            "provider_call_count",
        ),
        "return_runtime_probe",
    )
    observed = _return_exact_mapping(
        data["observed"],
        (
            "python",
            "torch",
            "transformers",
            "cuda",
            "cuda_device_count",
            "sys_executable_relative_path",
        ),
        "return_runtime_probe_observed",
    )
    expected_observed = {
        "python": runtime["python_version"],
        "torch": runtime["torch_version"],
        "transformers": runtime["transformers_version"],
        "cuda": runtime["cuda_version"],
        "cuda_device_count": 0,
        "sys_executable_relative_path": runtime[
            "interpreter_relative_path"
        ],
    }
    expected = {
        "schema_version": _predeploy.RUNTIME_PROBE_SCHEMA,
        "runtime_inventory_sha256": _sha(_canonical(runtime)),
        "probe_origin": "live_subprocess",
        "probe_state": "no_gpu_runtime_probe",
        "observed": expected_observed,
        "gpu_required": False,
        "gpu_observed": False,
        "model_loaded": False,
        "run_occurred": False,
        "provider_call_count": 0,
    }
    if any(data.get(key) != item for key, item in expected.items()):
        raise Qwen27BRecoveryRunnerError("return_runtime_probe_invalid")
    _return_identified(data, "probe_id", "return_runtime_probe")
    return copy.deepcopy(data)


def _parse_return_predeploy(
    manifest_raw: bytes,
    ready_raw: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _return_exact_mapping(
        _load_return_canonical(manifest_raw, "return_manifest"),
        (
            "schema_version",
            "manifest_id",
            "status",
            "source",
            "repository_archive_authority",
            "case_bundle_archive_authority",
            "official_model_inventory_authority",
            "profile",
            "profile_sha256",
            "expected_model_inventory",
            "runtime_inventory",
            "runtime_inventory_sha256",
            "runtime_probe",
            "runtime_probe_sha256",
            "repository_archive_inventory",
            "case_bundle_inventory",
            "transport",
        ),
        "return_manifest",
    )
    if (
        manifest["schema_version"] != _predeploy.MANIFEST_SCHEMA
        or manifest["status"] != "predeploy_manifest_no_gpu_not_action"
    ):
        raise Qwen27BRecoveryRunnerError("return_manifest_header_invalid")
    source = _return_exact_mapping(
        manifest["source"], ("commit", "tree"), "return_manifest_source"
    )
    _return_hex(source["commit"], "return_manifest_commit", 40)
    _return_hex(source["tree"], "return_manifest_tree", 40)
    try:
        archive_authority = _predeploy._repository_archive_authority(
            manifest["repository_archive_authority"]
        )
        case_archive_authority = _predeploy._repository_archive_authority(
            manifest["case_bundle_archive_authority"]
        )
        profile = _predeploy.Qwen27BRecoveryProfile(
            copy.deepcopy(manifest["profile"])
        )
        profile.validate()
        official = _predeploy._official_model_inventory_authority()
    except Qwen27BPredeployError as exc:
        raise Qwen27BRecoveryRunnerError("return_manifest_invalid") from exc
    official_inventory = official.pop("inventory")
    if (
        archive_authority != case_archive_authority
        or manifest["official_model_inventory_authority"] != official
    ):
        raise Qwen27BRecoveryRunnerError(
            "return_manifest_authority_invalid"
        )
    if manifest["profile_sha256"] != profile.sha256():
        raise Qwen27BRecoveryRunnerError("return_manifest_profile_invalid")
    expected_model = _return_inventory(
        manifest["expected_model_inventory"],
        "model_root",
        "return_manifest_model",
    )
    if expected_model != official_inventory:
        raise Qwen27BRecoveryRunnerError(
            "return_manifest_official_model_mismatch"
        )
    runtime = _return_runtime_inventory(
        manifest["runtime_inventory"], "return_manifest_runtime"
    )
    runtime_raw = _canonical(runtime)
    if manifest["runtime_inventory_sha256"] != _sha(runtime_raw):
        raise Qwen27BRecoveryRunnerError("return_manifest_runtime_invalid")
    probe = _return_runtime_probe(manifest["runtime_probe"], runtime)
    if manifest["runtime_probe_sha256"] != _sha(_canonical(probe)):
        raise Qwen27BRecoveryRunnerError("return_manifest_probe_invalid")
    archive_inventory = _return_inventory(
        manifest["repository_archive_inventory"],
        "repository_archive",
        "return_manifest_archive",
    )
    case_inventory = _return_inventory(
        manifest["case_bundle_inventory"],
        "case_bundle",
        "return_manifest_case_bundle",
    )
    archive_row = archive_inventory["files"][0]
    if (
        archive_authority["commit_sha"] != source["commit"]
        or archive_authority["tree_sha"] != source["tree"]
        or archive_authority["archive_byte_length"] != archive_row["bytes"]
        or archive_authority["archive_sha256"] != archive_row["sha256"]
    ):
        raise Qwen27BRecoveryRunnerError(
            "return_manifest_archive_binding_invalid"
        )
    transport = _return_exact_mapping(
        manifest["transport"],
        ("primary_transport", "selected_copy"),
        "return_manifest_transport",
    )
    if (
        transport["primary_transport"] != _predeploy.PRIMARY_TRANSPORT
        or transport["selected_copy"] not in TRANSFER_COPIES
    ):
        raise Qwen27BRecoveryRunnerError(
            "return_manifest_transport_invalid"
        )
    _return_identified(manifest, "manifest_id", "return_manifest")

    ready = _return_exact_mapping(
        _load_return_canonical(ready_raw, "return_ready"),
        (
            "schema_version",
            "ready_id",
            "status",
            "manifest",
            "transport",
            "actual_model_inventory",
            "actual_runtime_inventory",
            "actual_repository_archive_inventory",
            "actual_case_bundle_inventory",
            "claims",
            "next_gate",
        ),
        "return_ready",
    )
    if (
        ready["schema_version"] != _predeploy.READY_SCHEMA
        or ready["status"] != "predeploy_ready_no_gpu"
        or ready["next_gate"] != "fresh_gpu_action_gate_required"
    ):
        raise Qwen27BRecoveryRunnerError("return_ready_header_invalid")
    ready_manifest = _return_exact_mapping(
        ready["manifest"], ("manifest_id", "sha256"), "return_ready_manifest"
    )
    if ready_manifest != {
        "manifest_id": manifest["manifest_id"],
        "sha256": _sha(manifest_raw),
    }:
        raise Qwen27BRecoveryRunnerError("return_ready_manifest_mismatch")
    ready_transport = _return_exact_mapping(
        ready["transport"],
        ("primary_transport", "selected_copy"),
        "return_ready_transport",
    )
    if ready_transport != transport:
        raise Qwen27BRecoveryRunnerError("return_ready_transport_mismatch")
    if (
        _return_inventory(
            ready["actual_model_inventory"],
            "model_root",
            "return_ready_model",
        )
        != expected_model
        or _return_runtime_inventory(
            ready["actual_runtime_inventory"], "return_ready_runtime"
        )
        != runtime
        or _return_inventory(
            ready["actual_repository_archive_inventory"],
            "repository_archive",
            "return_ready_archive",
        )
        != archive_inventory
        or _return_inventory(
            ready["actual_case_bundle_inventory"],
            "case_bundle",
            "return_ready_case_bundle",
        )
        != case_inventory
    ):
        raise Qwen27BRecoveryRunnerError(
            "return_ready_inventory_mismatch"
        )
    if ready["claims"] != {
        "gpu_required": False,
        "gpu_observed": False,
        "model_loaded": False,
        "run_occurred": False,
        "provider_call_count": 0,
        "h1_eligible": False,
        "formal_quality_claim": False,
        "browser_evidence": False,
        "evidence_use_claim": False,
    }:
        raise Qwen27BRecoveryRunnerError("return_ready_claims_invalid")
    _return_identified(ready, "ready_id", "return_ready")
    return copy.deepcopy(manifest), copy.deepcopy(ready)


def _runtime_inventory_binding(
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "inventory_id": runtime["inventory_id"],
        "inventory_sha256": _sha(_canonical(runtime)),
        "tree_sha256": runtime["tree_sha256"],
        "file_count": runtime["file_count"],
        "symlink_count": runtime["symlink_count"],
        "entry_count": runtime["entry_count"],
    }


def _expected_result_predeploy_binding(
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    ready_raw: bytes,
    bundle: Qwen27BCaseBundle,
    archive_authority: Mapping[str, Any],
    selected_copy: str,
) -> dict[str, Any]:
    expected_model = manifest["expected_model_inventory"]
    return {
        "manifest_sha256": _sha(manifest_raw),
        "ready_sha256": _sha(ready_raw),
        "case_bundle_sha256": bundle.sha256(),
        "repository_archive_authority": dict(archive_authority),
        "selected_copy": selected_copy,
        "model_inventory": {
            "inventory_id": expected_model["inventory_id"],
            "inventory_sha256": _sha(_canonical(expected_model)),
            "tree_sha256": expected_model["tree_sha256"],
        },
        "runtime_inventory": _runtime_inventory_binding(
            manifest["runtime_inventory"]
        ),
    }


def validate_qwen27b_recovery_return_against(
    result_root: Path | str,
    manifest_path: Path | str,
    ready_path: Path | str,
    repository_archive: Path | str,
    repository_archive_manifest: Path | str,
    case_bundle_root: Path | str,
    selected_copy: str,
) -> dict[str, Any]:
    """Validate a returned run without requiring remote model/runtime roots.

    The remote live runner still performs full model and runtime filesystem
    replay before publishing its result. This local-return route instead
    validates the canonical captured inventories, cross-binds them to the run,
    and live-replays the returned repository archive and case bundle.
    """

    data = validate_qwen27b_recovery_result(result_root)
    manifest_raw = _regular_file(
        Path(manifest_path), "predeploy_manifest"
    ).read_bytes()
    ready_raw = _regular_file(Path(ready_path), "predeploy_ready").read_bytes()
    manifest, ready = _parse_return_predeploy(manifest_raw, ready_raw)
    if (
        selected_copy not in TRANSFER_COPIES
        or selected_copy != manifest["transport"]["selected_copy"]
        or selected_copy != ready["transport"]["selected_copy"]
    ):
        raise Qwen27BRecoveryRunnerError(
            "return_selected_copy_mismatch"
        )
    try:
        archive_authority = validate_repository_archive_authority(
            repository_archive,
            repository_archive_manifest,
        )
        bundle = validate_qwen27b_case_bundle(
            case_bundle_root,
            repository_archive_path=repository_archive,
            repository_archive_manifest_path=repository_archive_manifest,
        )
        live_archive_inventory = collect_file_inventory(
            repository_archive, "repository_archive"
        ).to_dict()
        live_case_inventory = collect_directory_inventory(
            bundle.root, "case_bundle"
        ).to_dict()
    except (Qwen27BCaseBundleError, Qwen27BPredeployError) as exc:
        raise Qwen27BRecoveryRunnerError(
            "return_live_artifact_validation_failed"
        ) from exc
    if (
        archive_authority != manifest["repository_archive_authority"]
        or bundle.manifest["source"]["repository_archive_authority"]
        != manifest["case_bundle_archive_authority"]
        or live_archive_inventory
        != manifest["repository_archive_inventory"]
        or live_archive_inventory
        != ready["actual_repository_archive_inventory"]
        or live_case_inventory != manifest["case_bundle_inventory"]
        or live_case_inventory != ready["actual_case_bundle_inventory"]
    ):
        raise Qwen27BRecoveryRunnerError(
            "return_live_artifact_binding_mismatch"
        )
    expected_predeploy = _expected_result_predeploy_binding(
        manifest,
        manifest_raw,
        ready_raw,
        bundle,
        archive_authority,
        selected_copy,
    )
    if data["predeploy"] != expected_predeploy:
        raise Qwen27BRecoveryRunnerError(
            "result_against_predeploy_binding_mismatch"
        )
    worker_relative = data["worker_runtime"][
        "python_executable_relative_path"
    ]
    runtime = manifest["runtime_inventory"]
    runtime_rows = {
        row["relative_path"]: row for row in runtime["files"]
    }
    if (
        worker_relative != runtime["interpreter_relative_path"]
        or worker_relative not in runtime_rows
        or data["worker_runtime"]
        != {
            "python_executable_relative_path": worker_relative,
            "runtime_inventory_id": runtime["inventory_id"],
            "runtime_inventory_sha256": _sha(_canonical(runtime)),
            "runtime_tree_sha256": runtime["tree_sha256"],
        }
    ):
        raise Qwen27BRecoveryRunnerError(
            "result_against_worker_runtime_mismatch"
        )
    return data


def terminate_process_group(
    process: subprocess.Popen[bytes],
    grace_seconds: float = 5.0,
    process_group_id: int | None = None,
) -> None:
    """Terminate a worker session, escalating from TERM to KILL on Linux."""

    if sys.platform != "linux":
        if process.poll() is not None:
            return
        process.kill()
        process.wait(timeout=grace_seconds)
        return
    group_id = process.pid if process_group_id is None else process_group_id
    if type(group_id) is not int or group_id < 1:
        raise Qwen27BRecoveryRunnerError("process_group_id_invalid")
    try:
        os.killpg(group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        try:
            os.killpg(group_id, 0)
        except ProcessLookupError:
            return
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    try:
        os.killpg(group_id, signal.SIGKILL)
    except ProcessLookupError:
        return
    if process.poll() is None:
        process.wait(timeout=grace_seconds)


def wait_for_worker(
    process: subprocess.Popen[bytes],
    timeout_seconds: int,
    cancel_path: Path | None = None,
    process_group_id: int | None = None,
) -> tuple[bytes, bytes | None]:
    """Wait for a dedicated worker with bounded timeout and optional cancellation."""

    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise Qwen27BRecoveryRunnerError("timeout_seconds_invalid")
    deadline = time.monotonic() + timeout_seconds
    while True:
        if cancel_path is not None and cancel_path.exists():
            if cancel_path.is_symlink() or not cancel_path.is_file():
                terminate_process_group(
                    process, process_group_id=process_group_id
                )
                raise Qwen27BRecoveryRunnerError("cancel_request_invalid")
            terminate_process_group(process, process_group_id=process_group_id)
            raise Qwen27BRecoveryRunnerError("recovery_cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            terminate_process_group(process, process_group_id=process_group_id)
            raise Qwen27BRecoveryRunnerError("recovery_timeout")
        try:
            return process.communicate(timeout=min(1.0, remaining))
        except subprocess.TimeoutExpired:
            continue


__all__ = (
    "BackendGeneration",
    "DEFAULT_TIMEOUT_SECONDS",
    "EXPECTED_DEVICE",
    "EXPECTED_GPU_NAMES",
    "MAX_INPUT_TOKENS",
    "MAX_NEW_TOKENS",
    "MIN_FREE_VRAM_BEFORE_LOAD_BYTES",
    "MIN_TOTAL_VRAM_BYTES",
    "Qwen27BRecoveryRunnerError",
    "RecoveryBackend",
    "TransformersRecoveryBackend",
    "execute_qwen27b_recovery",
    "terminate_process_group",
    "validate_qwen27b_recovery_result",
    "validate_qwen27b_recovery_result_against",
    "validate_qwen27b_recovery_return_against",
    "wait_for_worker",
)
