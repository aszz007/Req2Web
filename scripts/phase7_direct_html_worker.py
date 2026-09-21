"""Offline, single-call direct-HTML baseline for Phase 7 Experiment 1.

This experimental worker is not a Req2Web runtime. It deliberately produces no
PageSpec, product receipt, fallback, repair, or retry. The caller must append a
started-call record before invoking :func:`generate_direct_html`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from typing import Mapping
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


SCHEMA_VERSION = "req2web.phase7.experiment1.direct_html.v1"
MAX_NEW_TOKENS = 8192


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def identity(raw: bytes, revision: str) -> dict[str, object]:
    return {"identity_kind": "raw_bytes", "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(), "byte_length": len(raw), "revision": revision}


def build_direct_prompt(case: Mapping[str, object], shared_evidence: Mapping[str, object]) -> str:
    """Build the frozen arm-B request without evaluator-only material."""
    payload = {
        "requirement": case["requirement"],
        "target_device": case["target_device"],
        "task_type": case["task_type"],
        "constraints": case["constraints"],
        "sample_data": case["sample_data"],
        "admissible_retrieval_evidence": shared_evidence,
    }
    return (
        "Create one complete self-contained HTML document for the following offline prototype. "
        "Use inline CSS and JavaScript only. Do not use external dependencies, network calls, "
        "markdown fences, or an explanation. Return only the HTML document. You may choose any "
        "suitable page design.\n\nINPUT_JSON:\n" + canonical_bytes(payload).decode("utf-8")
    )


def extract_html(raw: bytes) -> tuple[bytes | None, str]:
    """Accept a whole document or one predefined whole-document HTML fence."""
    text = raw.decode("utf-8", errors="strict").strip()
    lowered = text.lower()
    if lowered.startswith("<!doctype html") or lowered.startswith("<html"):
        return text.encode("utf-8"), "none"
    if text.startswith("```html\n") and text.endswith("\n```"):
        inner = text[8:-4].strip()
        low_inner = inner.lower()
        if low_inner.startswith("<!doctype html") or low_inner.startswith("<html"):
            return inner.encode("utf-8"), "whole_document_html_fence"
    return None, "unsupported_transport_shape"


def _write_raw_first(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw); handle.flush(); os.fsync(handle.fileno())


def generate_direct_html(*, model_root: Path, integrity_evidence: Path, prompt: str, raw_path: Path, started_path: Path, timeout_seconds: int = 1200) -> dict[str, object]:
    """Load the pinned local model and perform exactly one greedy generation.

    This function is intentionally never called by the preparation command.
    """
    os.environ.update({"HF_HUB_OFFLINE":"1", "TRANSFORMERS_OFFLINE":"1", "HF_HUB_DISABLE_TELEMETRY":"1", "DO_NOT_TRACK":"1"})
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor
    from req2web_runtime.phase4_local_qwen import validate_model_inventory_metadata

    inventory = validate_model_inventory_metadata(model_root=model_root, integrity_evidence=integrity_evidence, allow_relocated_model_root=True)

    started = time.monotonic()
    processor = AutoProcessor.from_pretrained(str(model_root), local_files_only=True, trust_remote_code=False)
    model = AutoModelForImageTextToText.from_pretrained(
        str(model_root), local_files_only=True, trust_remote_code=False,
        torch_dtype=torch.bfloat16, device_map={"": 0}, attn_implementation="sdpa"
    )
    load_seconds = time.monotonic() - started
    messages = [{"role":"user", "content":[{"type":"text", "text":prompt}]}]
    rendered = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    inputs = processor(text=[rendered], return_tensors="pt", add_special_tokens=False).to("cuda:0")
    generation_started = time.monotonic()
    _write_raw_first(started_path, canonical_bytes({"event":"generation_started","automatic_retry_count":0,"max_new_tokens":MAX_NEW_TOKENS}))
    with torch.inference_mode():
        output = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False, use_cache=True)
    generation_seconds = time.monotonic() - generation_started
    new_tokens = output[0, inputs["input_ids"].shape[1]:]
    raw = processor.decode(new_tokens, skip_special_tokens=True).encode("utf-8")
    _write_raw_first(raw_path, raw)
    html, extraction = extract_html(raw)
    return {
        "schema_version": SCHEMA_VERSION,
        "raw": raw,
        "html": html,
        "transport_extraction": extraction,
        "input_tokens": int(inputs["input_ids"].shape[1]),
        "output_tokens": int(new_tokens.shape[0]),
        "load_seconds": load_seconds,
        "generation_seconds": generation_seconds,
        "timeout_seconds": timeout_seconds,
        "automatic_retry_count": 0,
        "model_inventory_identity": inventory["inventory_identity"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One raw-first Phase 7 direct HTML call")
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--integrity-evidence", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--started-output", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = generate_direct_html(model_root=args.model_root.resolve(strict=True), integrity_evidence=args.integrity_evidence.resolve(strict=True), prompt=args.prompt.read_text(encoding="utf-8"), raw_path=args.raw_output, started_path=args.started_output)
        serializable = {k:v for k,v in result.items() if k not in {"raw","html"}}
        if result["html"] is not None:
            _write_raw_first(args.result.with_name("page.html"), result["html"])
        _write_raw_first(args.result, canonical_bytes(serializable))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
