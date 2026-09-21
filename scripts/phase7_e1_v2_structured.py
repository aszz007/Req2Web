"""Single-generation structured comparator for the Phase 7 E1 v2 study.

This is an experiment-only adapter.  It accepts one raw response containing
the four owning node contracts, then delegates ID registration, mapping,
composition, assembly, rendering, and consistency checking to Req2Web's
existing authorities.  It never fills absent model semantics.
"""

from __future__ import annotations

import hashlib
import json
import os
import copy
from pathlib import Path
from typing import Mapping

from req2web_generation import DeterministicPageRenderer, MinimalConsistencyChecker
from req2web_agent import prompt_authority_manifest
from req2web_orchestration.phase4_graph import (
    NODE_ORDER,
    phase4_assemble_candidate,
    phase4_compose_candidate,
    phase4_create_mapping,
    phase4_create_portable_authority_state,
    phase4_register_node_output,
)

SCHEMA = "req2web.phase7.e1_v2.single_structured.v2"
RAW_SCHEMA = f"{SCHEMA}.raw"

_CONSTANT_ROWS = {
    "F1": (("sections", "section_constants"), ("components", "component_constants")),
    "F2": (("states", "state_constants"),),
    "F3": (("interactions", "interaction_constants"),),
    "F4": (("acceptance_checks", "acceptance_check_constants"),),
}


class StructuredComparatorError(ValueError):
    pass


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _pairs(rows: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in rows:
        if key in result:
            raise StructuredComparatorError("structured raw response contains a duplicate JSON key")
        result[key] = value
    return result


def build_structured_prompt(b_input: Mapping[str, object]) -> str:
    """Return the sole Arm-C prompt; no evaluator obligation is included."""
    contracts = copy.deepcopy(prompt_authority_manifest()["output_contracts"])
    contracts["F4"]["transport_override_for_single_call"] = {
        "state_ref": {
            "exact_keys": ["ref_type", "ref_id", "ref_revision"],
            "ref_type": "registry_local",
            "ref_id": "one exact F2 states[].local_id from this response",
            "ref_revision": f"{SCHEMA}.registry_local.v1",
        },
        "deterministic_adapter": (
            "After F2 validation and stable-ID registration, replace only this "
            "local state reference with the corresponding registry_stable reference."
        ),
    }
    contracts["deterministic_constant_adapter"] = {
        "scope": "fixed contract constants only",
        "behavior": (
            "If a row omits a key declared in its owning *_constants object, the adapter "
            "materializes that exact fixed value. A conflicting value or any missing "
            "non-constant field fails closed. This adds no workflow semantics."
        ),
    }
    payload = {
        "canonical_b": dict(b_input),
        "output_contract": {
            "schema_version": RAW_SCHEMA,
            "exact_top_level_keys": ["schema_version", *NODE_ORDER],
            "active_node_contracts": contracts,
        },
    }
    return (
        "Generate one complete structured workflow prototype in a single response. "
        "Return exactly one JSON object and no markdown. The F1, F2, F3, and F4 values "
        "must independently satisfy the embedded active Req2Web node contracts. Use only the seven "
        "declared executable component capabilities. Every interaction trigger must be visible "
        "in its exact source state; transitions may target any actual state needed by the public "
        "workflow. Include every fixed field declared by each *_constants object exactly as shown. "
        "Do not omit semantics for a local adapter to infer or repair.\n\nINPUT_JSON:\n"
        + canonical_bytes(payload).decode("utf-8")
    )


def parse_single_response(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes or not raw or raw.startswith(b"\xef\xbb\xbf"):
        raise StructuredComparatorError("structured raw response must be non-empty UTF-8 bytes without BOM")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=lambda value: (_ for _ in ()).throw(StructuredComparatorError(f"invalid JSON constant: {value}")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StructuredComparatorError("structured raw response is not strict JSON") from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", *NODE_ORDER}:
        raise StructuredComparatorError("structured raw response top-level keys are invalid")
    if value["schema_version"] != RAW_SCHEMA or any(not isinstance(value[node], dict) for node in NODE_ORDER):
        raise StructuredComparatorError("structured raw response schema or node payload is invalid")
    return value


def _materialize_contract_constants(node_id: str, output: Mapping[str, object]) -> tuple[dict[str, object], int, int]:
    """Fill fixed schema boilerplate and canonicalize JSON-object key order."""
    normalized = copy.deepcopy(dict(output))
    contracts = prompt_authority_manifest()["output_contracts"]
    node_contract = contracts[node_id]
    count = 0
    order_count = 0
    for row_key, constants_key in _CONSTANT_ROWS[node_id]:
        rows = normalized.get(row_key)
        constants = node_contract[constants_key]
        exact_keys = node_contract[constants_key.replace("_constants", "_exact_keys")]
        if not isinstance(rows, list):
            raise StructuredComparatorError(f"{node_id}.{row_key} must be an array")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise StructuredComparatorError(f"{node_id}.{row_key} rows must be objects")
            for key, expected in constants.items():
                if key not in row:
                    row[key] = copy.deepcopy(expected)
                    count += 1
                elif row[key] != expected:
                    raise StructuredComparatorError(
                        f"{node_id}.{row_key} conflicts with fixed contract constant {key}"
                    )
            if set(row) != set(exact_keys):
                raise StructuredComparatorError(f"{node_id}.{row_key} row keys are invalid")
            if list(row) != exact_keys:
                order_count += 1
            rows[index] = {key: row[key] for key in exact_keys}
    top_level_keys = node_contract["exact_top_level_keys"]
    if set(normalized) != set(top_level_keys):
        raise StructuredComparatorError(f"{node_id} top-level keys are invalid")
    if list(normalized) != top_level_keys:
        order_count += 1
    normalized = {key: normalized[key] for key in top_level_keys}
    if node_id == "F4":
        reference_keys = node_contract["reference_exact_keys"]
        for check in normalized["acceptance_checks"]:
            references = check.get("use_case_refs")
            if not isinstance(references, list):
                raise StructuredComparatorError("F4.use_case_refs must be an array")
            for index, reference in enumerate(references):
                if not isinstance(reference, dict) or set(reference) != set(reference_keys):
                    raise StructuredComparatorError("F4.use_case_refs row keys are invalid")
                if list(reference) != reference_keys:
                    order_count += 1
                references[index] = {key: reference[key] for key in reference_keys}
            state_ref = check.get("state_ref")
            if not isinstance(state_ref, dict) or set(state_ref) != set(reference_keys):
                raise StructuredComparatorError("F4.state_ref keys are invalid")
            if list(state_ref) != reference_keys:
                order_count += 1
            check["state_ref"] = {key: state_ref[key] for key in reference_keys}
    return normalized, count, order_count


def adapt_and_render(*, raw: bytes, b_input: Mapping[str, object], context: object, guidance: object, output_root: Path) -> dict[str, object]:
    """Apply the owning authorities once; missing/invalid semantics fail closed."""
    parsed = parse_single_response(raw)
    state = phase4_create_portable_authority_state(dict(b_input))
    state_ref_rebinding_count = 0
    contract_constant_materialization_count = 0
    contract_order_normalization_count = 0
    for node_id in NODE_ORDER:
        output, materialized, reordered = _materialize_contract_constants(node_id, parsed[node_id])
        contract_constant_materialization_count += materialized
        contract_order_normalization_count += reordered
        if node_id=="F4":
            state_ids={row["local_id"]:row["stable_id"] for row in state["registry_inventory"] if row["node_id"]=="F2"}
            output=json.loads(json.dumps(output))
            for check in output["acceptance_checks"]:
                ref=check["state_ref"]
                if (
                    set(ref) == {"ref_type", "ref_id", "ref_revision"}
                    and ref.get("ref_type") == "registry_local"
                    and ref.get("ref_revision") == f"{SCHEMA}.registry_local.v1"
                    and ref.get("ref_id") in state_ids
                ):
                    check["state_ref"]={"ref_type":"registry_stable","ref_id":state_ids[ref["ref_id"]],"ref_revision":"req2web.phase4.registry.p4_02a.v1"}
                    state_ref_rebinding_count += 1
                else:
                    raise StructuredComparatorError("F4 local state reference is invalid")
        state = phase4_register_node_output(state, node_id, output)
    composition = phase4_compose_candidate(state)
    candidate = __import__("base64").b64decode(composition["model_semantic_candidate_canonical_b64"], validate=True)
    assembled = phase4_assemble_candidate(candidate, context, guidance)
    assembled.validate()
    page = output_root / "page"
    rendered = DeterministicPageRenderer().render(assembled.page_spec, page)
    consistency = MinimalConsistencyChecker().check(assembled.page_spec, rendered)
    result = {
        "schema_version": f"{SCHEMA}.result",
        "status": "structured_artifact_ready" if consistency.passed else "failed_closed_consistency",
        "raw_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "raw_byte_length": len(raw),
        "candidate_sha256": "sha256:" + hashlib.sha256(candidate).hexdigest(),
        "page_spec": assembled.page_spec.to_dict(),
        "assembly_report": assembled.report.to_dict(),
        "consistency": consistency.to_dict(),
        "entrypoint": "page/index.html",
        "automatic_retry_count": 0,
        "generation_call_count": 1,
        "adapter_semantics_added": False,
        "adapter_contract_constant_materialization_count": contract_constant_materialization_count,
        "adapter_contract_order_normalization_count": contract_order_normalization_count,
        "adapter_identity_only_rebinding_count": state_ref_rebinding_count,
        "internal_contract_adapter_source_label": "deterministic_transport_validation_not_model_graph_provenance",
    }
    output_root.mkdir(parents=True, exist_ok=True)
    temporary = output_root / ".structured-result.tmp"
    with temporary.open("xb") as handle:
        handle.write(canonical_bytes(result))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, output_root / "structured-result.json")
    return result


__all__ = ["RAW_SCHEMA", "StructuredComparatorError", "adapt_and_render", "build_structured_prompt", "parse_single_response"]
