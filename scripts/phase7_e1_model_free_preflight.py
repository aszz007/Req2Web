"""Exercise the actual E1 upstream and runtime prerequisites without a model.

Outputs are deployment diagnostics, never measured rows or model evidence.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def check(preparation: Path, output: Path) -> dict:
    preparation = preparation.resolve(strict=True)
    output = output.resolve()
    from phase7_experiment1 import validate, load_and_validate_sources, model_visible_case
    from req2web_runtime.phase4_canonical_full_flow import _build_upstream
    from req2web_orchestration.phase4_graph import (
        validate_runtime_environment, phase4_create_portable_authority_state,
        Phase4RealModelGraphRuntime,
    )
    from req2web_runtime.phase4_fresh_delivery import build_phase4_actual_context_delivery_materials
    from req2web_runtime import phase4_remote_qwen_fresh_integrated as shared

    validate(preparation)
    validate_runtime_environment()
    if output.exists():
        raise ValueError("diagnostic output already exists")
    if ROOT.resolve() == output.resolve() or ROOT.resolve() in output.resolve().parents:
        raise ValueError("diagnostic output must be outside the extracted payload")
    output.mkdir(parents=True)
    cases, _ = load_and_validate_sources()
    schedule = json.loads((preparation / "freeze_manifest.json").read_bytes())["schedule"]
    case = model_visible_case(next(c for c in cases["cases"] if c["case_id"] == schedule[0]["case_id"]))
    owned = {key: case[key] for key in ("case_id", "requirement", "target_device", "task_type", "constraints")}
    owned["request_id"] = "request-" + case["case_id"]
    upstream = _build_upstream(case=owned, index_dir=ROOT / "data/processed/rag", output_root=output / "upstream")
    b = upstream["adaptation"].b_input
    state = phase4_create_portable_authority_state(b)
    materials = build_phase4_actual_context_delivery_materials(
        graph_state=state, context=upstream["context"], guidance=upstream["guidance"], material_root=output / "materials")
    materials.validate()

    def forbidden(*args, **kwargs):
        raise AssertionError("model or delivery execution is forbidden in deployment preflight")

    Phase4RealModelGraphRuntime(node_executor=forbidden, context=upstream["context"],
                               guidance=upstream["guidance"], delivery_executor=forbidden)
    node_input = shared._node_input(node_id="F1", b_input=b, state=state, authority_projection={})
    prompt = shared._node_prompt(node_id="F1", input_bytes=node_input, prompt_revision=shared.P4_05_FULL_DIRECT_PROMPT_REVISION)
    receipt = {"ready": True, "scope": "actual upstream, dependency closure, G0 materials, graph compilation and F1 prompt only",
               "case_id": case["case_id"], "model_loaded": False, "model_calls": 0, "graph_invoked": False,
               "f1_prompt_sha256": hashlib.sha256(prompt).hexdigest()}
    with (output / "receipt.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True)
    return receipt


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--preparation", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(check(a.preparation, a.output), sort_keys=True))
