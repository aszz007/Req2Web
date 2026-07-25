"""Future Linux Real-A1 probe child. Importing this module performs no probe."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import socket
import subprocess
import sys
import time

from .autodl_a1_executor import CHANNEL_SCHEMA, EXPECTED_SUFFIX, FIXED_PROMPT
from .autodl_a1_operational import A1ModelInventory, A1OperationalControls, A1OperationalPlan

_WORKER_VERSION = "1"
_EXTERNAL_DNS_NAME = "example.com"
_EXTERNAL_CONNECT_TARGET = ("1.1.1.1", 443)
_MULTIMEDIA_KEYS = ("pixel_values", "image_grid_thw", "video_grid_thw", "images", "videos")


class ProbeWorkerError(RuntimeError):
    pass


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _emit(sequence, phase, transition, payload=None):
    raw = _canonical({"schema_version": CHANNEL_SCHEMA, "sequence": sequence, "phase": phase, "transition": transition, "payload": payload or {}})
    sys.stdout.buffer.write(raw + b"\n")
    sys.stdout.buffer.flush()


def _listener_count():
    count = 0
    for filename in ("/proc/net/tcp", "/proc/net/tcp6"):
        lines = Path(filename).read_text(encoding="ascii").splitlines()[1:]
        for line in lines:
            fields = line.split()
            if len(fields) > 3 and fields[3] == "0A":
                count += 1
    return count


def _network_checks(parent_netns_id):
    child_netns_id = os.readlink("/proc/self/ns/net")
    if child_netns_id == parent_netns_id:
        raise ProbeWorkerError("netns_invalid")
    try:
        socket.getaddrinfo(_EXTERNAL_DNS_NAME, 443)
    except OSError:
        dns_status = "blocked"
    else:
        raise ProbeWorkerError("dns_invalid")
    try:
        connection = socket.create_connection(_EXTERNAL_CONNECT_TARGET, timeout=1.0)
    except OSError:
        egress_status = "blocked"
    else:
        connection.close()
        raise ProbeWorkerError("egress_invalid")
    listeners = _listener_count()
    if listeners != 0:
        raise ProbeWorkerError("listener_invalid")
    return {"parent_netns_id": parent_netns_id, "child_netns_id": child_netns_id, "netns_distinct": True, "dns_status": dns_status, "egress_status": egress_status, "listener_count": listeners, "http_endpoint_observed": False}


def _nvidia_smi_gpu():
    command = ["/usr/bin/nvidia-smi", "--query-gpu=index,uuid,name,memory.total,driver_version", "--format=csv,noheader,nounits"]
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, text=True, timeout=30)
    rows = [row.strip() for row in completed.stdout.splitlines() if row.strip()]
    if len(rows) != 1:
        raise ProbeWorkerError("gpu_invalid")
    fields = [field.strip() for field in rows[0].split(",")]
    if len(fields) != 5:
        raise ProbeWorkerError("gpu_invalid")
    index, uuid, name, memory_mib, driver = fields
    if index != "0" or name != "NVIDIA GeForce RTX 5090" or int(memory_mib) < 30000 or not uuid or not driver:
        raise ProbeWorkerError("gpu_invalid")
    return {"index": 0, "uuid": uuid, "name": name, "vram_mib": int(memory_mib), "driver": driver}


def _host_resources(model_root):
    page_size = os.sysconf("SC_PAGE_SIZE")
    pages = os.sysconf("SC_PHYS_PAGES")
    ram_gib = (page_size * pages) // (1024**3)
    disk = os.statvfs(model_root)
    free_disk_gib = (disk.f_bavail * disk.f_frsize) // (1024**3)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss_bytes = int(usage.ru_maxrss) * 1024
    return ram_gib, free_disk_gib, rss_bytes


def _parser():
    parser = argparse.ArgumentParser(description="Run the fixed Real-A1 text-only probe child")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--controls", required=True)
    parser.add_argument("--package-root", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--parent-netns-id", required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    sequence = 1
    phase = "setup"
    _emit(sequence, phase, "started")
    setup_started = time.monotonic()
    try:
        if sys.platform != "linux":
            raise ProbeWorkerError("runtime_invalid")
        required_env = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "CUDA_VISIBLE_DEVICES": "0"}
        if any(os.environ.get(key) != value for key, value in required_env.items()):
            raise ProbeWorkerError("runtime_invalid")
        plan = A1OperationalPlan.from_bytes(Path(args.plan).read_bytes())
        controls = A1OperationalControls.from_bytes(Path(args.controls).read_bytes())
        if controls.data["plan_id"] != plan.plan_id or plan.data["model_inventory"]["inventory_mode"] != "production_pinned":
            raise ProbeWorkerError("model_invalid")
        inventory = A1ModelInventory.from_dict(plan.data["model_inventory"])
        inventory.validate_against_root(args.model_root)
        network = _network_checks(args.parent_netns_id)
        gpu_base = _nvidia_smi_gpu()

        # Lazy imports are intentionally inside the explicit child entry only.
        import torch
        import transformers
        from transformers import AutoModelForImageTextToText, AutoProcessor

        if torch.__version__ != "2.7.1+cu128" or transformers.__version__ != "5.14.1" or torch.version.cuda != "12.8" or sys.version_info[:2] != (3, 11):
            raise ProbeWorkerError("runtime_invalid")
        if torch.cuda.device_count() != 1 or not torch.cuda.is_bf16_supported():
            raise ProbeWorkerError("gpu_invalid")
        properties = torch.cuda.get_device_properties(0)
        torch_uuid = str(getattr(properties, "uuid", ""))
        if properties.name != "NVIDIA GeForce RTX 5090" or properties.total_memory // (1024**2) < 30000 or not torch_uuid or torch_uuid != gpu_base["uuid"]:
            raise ProbeWorkerError("gpu_invalid")
        torch.cuda.set_device(0)
        torch.manual_seed(20260724)
        torch.cuda.manual_seed_all(20260724)
        ram_gib, free_disk_gib, rss_before = _host_resources(args.model_root)
        if ram_gib < 60 or free_disk_gib < 80:
            raise ProbeWorkerError("resource_invalid")
        setup_ms = int((time.monotonic() - setup_started) * 1000)
        sequence += 1
        _emit(sequence, "setup", "completed")

        phase = "load"
        sequence += 1
        _emit(sequence, phase, "started")
        load_started = time.monotonic()
        processor = AutoProcessor.from_pretrained(args.model_root, local_files_only=True, trust_remote_code=False)
        model = AutoModelForImageTextToText.from_pretrained(args.model_root, local_files_only=True, trust_remote_code=False, torch_dtype=torch.bfloat16)
        model = model.to("cuda:0")
        model.eval()
        model_device = str(next(model.parameters()).device)
        if model_device != "cuda:0" or next(model.parameters()).dtype != torch.bfloat16:
            raise ProbeWorkerError("device_invalid")
        load_ms = int((time.monotonic() - load_started) * 1000)
        sequence += 1
        _emit(sequence, "load", "completed")

        phase = "probe"
        sequence += 1
        _emit(sequence, phase, "started")
        probe_started = time.monotonic()
        messages = [{"role": "user", "content": [{"type": "text", "text": FIXED_PROMPT}]}]
        prompt_text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        inputs = processor(text=[prompt_text], return_tensors="pt")
        processor_keys = sorted(inputs.keys())
        multimedia_keys = sorted(key for key in processor_keys if key in _MULTIMEDIA_KEYS or "pixel" in key or "image" in key or "video" in key)
        if processor_keys != ["attention_mask", "input_ids"] or multimedia_keys:
            raise ProbeWorkerError("processor_invalid")
        inputs = {key: value.to("cuda:0") for key, value in inputs.items()}
        input_len = inputs["input_ids"].shape[1]
        torch.cuda.reset_peak_memory_stats(0)
        with torch.inference_mode():
            output = model.generate(**inputs, do_sample=False, max_new_tokens=8, num_return_sequences=1)
        generated_suffix_ids = output[:, input_len:]
        generated_token_count = int(generated_suffix_ids.shape[1])
        decoded_suffix = processor.batch_decode(generated_suffix_ids, skip_special_tokens=True)[0].strip()
        output_bytes = decoded_suffix.encode("utf-8")
        suffix_match = decoded_suffix == EXPECTED_SUFFIX
        sentinel_absent = "REQ2WEB_A1_SENTINEL_7f9c" not in decoded_suffix
        if not suffix_match or not sentinel_absent or not 1 <= generated_token_count <= 8:
            raise ProbeWorkerError("probe_invalid")
        probe_ms = int((time.monotonic() - probe_started) * 1000)
        network_after = _network_checks(args.parent_netns_id)
        if network_after != network:
            raise ProbeWorkerError("network_invalid")
        _, _, rss_after = _host_resources(args.model_root)
        payload = {
            "runtime": {"python": "3.11", "torch": torch.__version__, "transformers": transformers.__version__, "cuda": torch.version.cuda},
            "gpu": {**gpu_base, "cuda_device_count": torch.cuda.device_count(), "model_device": model_device, "bf16": True},
            "resources": {"ram_gib": ram_gib, "free_disk_gib": free_disk_gib, "cuda_peak_allocated_bytes": int(torch.cuda.max_memory_allocated(0)), "cuda_peak_reserved_bytes": int(torch.cuda.max_memory_reserved(0)), "host_rss_bytes": rss_after, "host_peak_rss_bytes": max(rss_before, rss_after), "setup_ms": setup_ms, "load_ms": load_ms, "probe_ms": probe_ms},
            "model": {"repository": inventory.data["repository"], "revision": inventory.data["revision"], "inventory_sha256": inventory.sha256(), "inventory_tree_sha256": inventory.data["tree_sha256"], "quantization": "none", "cpu_offload": False, "device_map": "none", "load_status": "loaded"},
            "processor": {"keys": processor_keys, "multimedia_keys": multimedia_keys, "text_only": True},
            "probe": {"prompt_sha256": hashlib.sha256(FIXED_PROMPT.encode("utf-8")).hexdigest(), "prompt_length": len(FIXED_PROMPT), "expected_suffix_sha256": hashlib.sha256(EXPECTED_SUFFIX.encode("utf-8")).hexdigest(), "expected_suffix_length": len(EXPECTED_SUFFIX), "output_sha256": hashlib.sha256(output_bytes).hexdigest(), "output_length": len(decoded_suffix), "generated_token_count": generated_token_count, "suffix_match": suffix_match, "prompt_sentinel_absent_from_output": sentinel_absent},
            "network": network_after,
        }
        sequence += 1
        _emit(sequence, "probe", "completed", payload)
    except Exception as exc:
        code = str(exc)
        if phase == "load" and ("out of memory" in code.lower() or exc.__class__.__name__ == "OutOfMemoryError"):
            code = "load_oom"
        allowed = {"runtime_invalid", "gpu_invalid", "resource_invalid", "model_invalid", "device_invalid", "processor_invalid", "probe_invalid", "network_invalid", "netns_invalid", "listener_invalid", "dns_invalid", "egress_invalid", "load_oom"}
        if code not in allowed:
            code = "child_failed"
        sequence += 1
        _emit(sequence, phase, "failed", {"failure_code": code})
        raise SystemExit(2)


if __name__ == "__main__":
    main()
