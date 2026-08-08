"""No-model Phase 5 preparation bound to the accepted Phase 4 authorities.

The synthetic backend is a component test only. It builds the minimum
route-specific Provider view for F1-F4, preserves every synthetic raw response
before parsing, and runs the shared Phase 4 prompt, LangGraph topology, domain
validators, registry, mapping, composition, and assembler.

Real hidden inputs and result roots are owner-custody material and must stay
outside the repository.  The tracked synthetic fixture exercises structure
only; it is not the active workflow, H1, model, GPU, remote, or formal-quality
evidence. Phase 4 is closed, but real package execution remains fail-closed at
the separate model/H1 action gate and may not enable this historical loop.
"""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import time
from typing import Callable, Mapping, Protocol, Sequence

from req2web_agent import (
    AgentContextBundle,
    PROMPT_AUTHORITY_IDENTITY,
    PROMPT_AUTHORITY_REVISION,
    PROMPT_SCHEMA_VERSION as SHARED_PROMPT_SCHEMA_VERSION,
    UseCase,
    build_canonical_f3_interaction_plan,
    build_canonical_f1_f4_prompt,
)
from req2web_acceptance import (
    SEMANTIC_ALIGNMENT_CONTRACT_REVISION,
    SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION,
    SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
)
from req2web_generation import RetrievalGuidanceBuilder
from req2web_orchestration.phase4_canonical_b_adapter import ADAPTER_REVISION
from req2web_orchestration.phase4_graph import (
    NO_CAPTURE_SHA256,
    REAL_MODEL_SOURCE_KIND,
    REAL_MODEL_GRAPH_REVISION,
    REGISTRY_REVISION,
    Phase4RealModelGraphRuntime,
    create_real_model_graph_state,
    make_identity,
    make_real_model_raw_capture,
    phase4_synthetic_fixture_output,
    phase4_validate_node_output,
)
from req2web_rag.corpus import ROLE_ORDER
from req2web_runtime.phase4_browser_acceptance import (
    CANARY_RECEIPT_SCHEMA_VERSION as PHASE4_BROWSER_CANARY_SCHEMA_VERSION,
    CASE_AUDIT_SCHEMA_VERSION as PHASE4_BROWSER_AUDIT_SCHEMA_VERSION,
    FINAL_BROWSER_SUMMARY_SCHEMA_VERSION as PHASE4_BROWSER_SUMMARY_SCHEMA_VERSION,
)
from req2web_runtime.phase4_canonical_full_flow import (
    ACTIVE_DEFAULT_ENTRY as PHASE4_CANONICAL_ACTIVE_DEFAULT_ENTRY,
    FLOW_AUTHORITY_ROLE as PHASE4_CANONICAL_FLOW_AUTHORITY_ROLE,
    FLOW_SCHEMA_VERSION as PHASE4_CANONICAL_FLOW_SCHEMA_VERSION,
    run_phase4_canonical_full_flow,
)

from .phase5_action_authority import Phase5FinalActionAuthority
from .phase5_sealed_action_package import (
    PATH2_ROUTE,
    Phase5SealedActionPackage,
    build_phase5_node_static_projection,
)


RUNNER_SCHEMA_VERSION = "req2web.phase5.formal_runner.v1"
RUN_MANIFEST_SCHEMA_VERSION = "req2web.phase5.formal_run_manifest.v1"
RUN_CLOCK_SCHEMA_VERSION = "req2web.phase5.formal_run_clock.v1"
NODE_INPUT_SCHEMA_VERSION = "req2web.phase5.formal_node_input.v1"
NODE_PROMPT_SCHEMA_VERSION = SHARED_PROMPT_SCHEMA_VERSION
NODE_CONFIG_SCHEMA_VERSION = "req2web.phase5.formal_node_config.v1"
NODE_REQUEST_SCHEMA_VERSION = "req2web.phase5.formal_node_request.v1"
PRE_CALL_SCHEMA_VERSION = "req2web.phase5.formal_pre_call_record.v1"
GENERATION_STARTED_SCHEMA_VERSION = (
    "req2web.phase5.formal_generation_started.v1"
)
ATTEMPT_RESULT_SCHEMA_VERSION = "req2web.phase5.formal_attempt_result.v1"
CALL_EVENT_SCHEMA_VERSION = "req2web.phase5.formal_call_event.v1"
CALL_LEDGER_SCHEMA_VERSION = "req2web.phase5.formal_call_ledger.v1"
CASE_RESULT_SCHEMA_VERSION = "req2web.phase5.formal_case_result.v1"
RUN_SUMMARY_SCHEMA_VERSION = "req2web.phase5.formal_run_summary.v1"
PHASE5_FORMAL_EXECUTION_STATUS = (
    "canonical_phase4_authority_inherited_no_model_action"
)
PHASE5_FORMAL_MODEL_ACTION_ENABLED = False
PHASE4_CLOSURE_AUTHORITY_COMMIT = (
    "af09483bb774c9e2e2bd32eaca4cb0c9009da31f"
)
ACCEPTED_PHASE4_CANONICAL_FULL_FLOW_RUNNER = (
    "scripts/run_phase4_canonical_full_flow.py"
    "@78b1a12e4814f025cfe4737de196729272941ada"
)
EXPECTED_PROMPT_AUTHORITY_REVISION = (
    "f3_f4_explicit_actual_state_plan_a07a_direct_english_v13"
)
EXPECTED_PROMPT_AUTHORITY_SHA256 = (
    "sha256:94c337157e652a4e1e7315e06e33a59466985e3a8e15e36c58d7b231d1f3c4a1"
)
SYNTHETIC_VALIDATION_FLOW_ROLE = (
    "shared_phase4_langgraph_synthetic_no_model_validation_only"
)
HISTORICAL_PROMPT_REVISION = "phase5_path1_f3_f4_explicit_plan_v1"
HISTORICAL_PATH2_PROMPT_REVISION = "phase5_path2_f3_f4_explicit_plan_v1"
PROMPT_REVISION = PROMPT_AUTHORITY_REVISION
PATH2_PROMPT_REVISION = PROMPT_AUTHORITY_REVISION
NODE_ORDER = ("F1", "F2", "F3", "F4")
MODEL_CONTEXT_TOKENS = 262_144
PER_NODE_TIMEOUT_SECONDS = 1_200
DECODE_SEED = 0
RTX5090_PROFILE_SHA256 = (
    "db47a61e183cfa8e809c19c41f68857b5f7e45fd0083dea7ec2e9606631e5f03"
)

_ROLE_ORDER = ("ui_reference", "interaction_flow", "implementation", "validation")
_PROHIBITED_PROVIDER_KEYS = {
    "acceptance_verdict",
    "block_hint",
    "core_or_reserve_identity",
    "deep_label_gold",
    "deep_labels",
    "duplicate_manual_adjudication",
    "evaluator_gold",
    "evaluator_score",
    "gold",
    "gold_payload",
    "group_hint",
    "matrix_row_id",
    "opaque_case_ref",
    "owner_score",
    "reference_only_asset",
    "rico_asset",
    "runtime_case_id",
    "secret",
    "source_path",
    "third_party_original_text",
    "uri",
}


class Phase5FormalRunnerError(ValueError):
    """Raised when a sealed formal run must fail closed."""


def _validate_inherited_phase4_authority() -> None:
    if (
        PHASE4_CANONICAL_ACTIVE_DEFAULT_ENTRY is not True
        or PHASE4_CANONICAL_FLOW_AUTHORITY_ROLE
        != "active_canonical_full_flow"
        or run_phase4_canonical_full_flow.__module__
        != "req2web_runtime.phase4_canonical_full_flow"
        or build_canonical_f1_f4_prompt.__module__
        != "req2web_agent.prompt_authority"
        or Phase4RealModelGraphRuntime.__module__
        != "req2web_orchestration.phase4_graph"
        or PROMPT_AUTHORITY_REVISION != EXPECTED_PROMPT_AUTHORITY_REVISION
        or PROMPT_AUTHORITY_IDENTITY.get("sha256")
        != EXPECTED_PROMPT_AUTHORITY_SHA256
    ):
        raise Phase5FormalRunnerError(
            "accepted Phase 4 canonical authority symbols drifted"
        )


class Phase5CaseWorker(Protocol):
    """One case-bound worker; real implementations must own one process."""

    worker_id: str

    def bind_graph_state(self, state: Mapping[str, object]) -> None:
        """Bind the latest validated graph state before a node call."""

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
        """Perform exactly one generate call for a node."""

    def close(self) -> Mapping[str, object]:
        """Close the case worker and return a terminal supervisor receipt."""


Phase5WorkerFactory = Callable[[Mapping[str, object]], Phase5CaseWorker]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _identity(raw: bytes) -> dict[str, object]:
    return {"sha256": _sha(raw), "byte_length": len(raw)}


def _record_id(prefix: str, value: object) -> str:
    return f"{prefix}-{_sha(_canonical(value))}"


def _strict_json(raw: bytes, name: str) -> object:
    if type(raw) is not bytes or not raw:
        raise Phase5FormalRunnerError(f"{name} must be non-empty bytes")

    def reject(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise Phase5FormalRunnerError(f"{name} has a duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject,
            parse_constant=lambda value: (_ for _ in ()).throw(
                Phase5FormalRunnerError(
                    f"{name} contains a non-finite JSON constant: {value}"
                )
            ),
        )
    except UnicodeDecodeError as exc:
        raise Phase5FormalRunnerError(f"{name} is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise Phase5FormalRunnerError(f"{name} is not valid JSON") from exc


def _scan_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_scan_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_scan_keys(item))
    return keys


def _write_once(path: Path, raw: bytes) -> int:
    if type(raw) is not bytes or not raw:
        raise Phase5FormalRunnerError(f"cannot write empty artifact: {path.name}")
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
            raise Phase5FormalRunnerError(
                f"write-once artifact drifted: {path}"
            )
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return len(raw)


def _read_canonical(path: Path, name: str) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise Phase5FormalRunnerError(f"{name} is unavailable")
    raw = path.read_bytes()
    value = _strict_json(raw, name)
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise Phase5FormalRunnerError(f"{name} is not canonical")
    return value


def _sealed_formal_canonical_b_projection(
    row: Mapping[str, object],
) -> dict[str, object]:
    """Project owner-sealed canonical fields into the shared P4 validator."""

    source = row["b_input"]
    if not isinstance(source, Mapping):
        raise Phase5FormalRunnerError("sealed action B input is invalid")
    return {
        "case_id": str(row["runtime_case_id"]),
        "request_id": str(source["request_id"]),
        "requirement": str(source["requirement_projection"]),
        "requirement_summary": str(source["requirement_summary"]),
        "target_device": str(source["target_device"]),
        "task_type": str(source["task_type"]),
        "constraints": copy.deepcopy(source["normalized_constraints"]),
        "use_cases": [
            {
                "use_case_id": str(item["use_case_id"]),
                "title": str(item["title"]),
                "actor": str(item["actor"]),
                "goal": str(item["goal"]),
                "expected_outcome": str(item["expected_outcome"]),
            }
            for item in source["canonical_use_cases"]
        ],
    }


def _validated_node_output(
    state: Mapping[str, object],
    node_id: str,
) -> Mapping[str, object]:
    results = state.get("node_results")
    if not isinstance(results, Mapping):
        raise Phase5FormalRunnerError("formal graph node results are unavailable")
    record = results.get(node_id)
    if not isinstance(record, Mapping):
        raise Phase5FormalRunnerError(f"{node_id} validated result is unavailable")
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        raise Phase5FormalRunnerError(f"{node_id} validated payload is unavailable")
    output = payload.get("node_output")
    if not isinstance(output, Mapping):
        raise Phase5FormalRunnerError(f"{node_id} validated output is unavailable")
    return output


def _registry_row(
    authority: Mapping[str, object],
    *,
    node_id: str,
    entity_type: str,
    local_id: str,
) -> Mapping[str, object]:
    rows = authority.get("registry_rows")
    if not isinstance(rows, list):
        raise Phase5FormalRunnerError("formal authority registry is unavailable")
    matches = [
        item
        for item in rows
        if isinstance(item, Mapping)
        and item.get("node_id") == node_id
        and item.get("entity_type") == entity_type
        and item.get("local_id") == local_id
    ]
    if len(matches) != 1:
        raise Phase5FormalRunnerError(
            f"formal registry target is not unique: {node_id}/{local_id}"
        )
    return matches[0]


def _f1_view(
    state: Mapping[str, object],
    authority: Mapping[str, object],
) -> dict[str, object]:
    output = _validated_node_output(state, "F1")
    component_rows: dict[str, Mapping[str, object]] = {}
    components: list[dict[str, object]] = []
    for component in output["components"]:  # type: ignore[index]
        row = _registry_row(
            authority,
            node_id="F1",
            entity_type="component",
            local_id=str(component["local_id"]),
        )
        component_rows[str(component["local_id"])] = row
        components.append(
            {
                "local_id": component["local_id"],
                "stable_id": row["stable_id"],
                "component_type": component["component_type"],
                "section_local_id": component["section_local_id"],
                "label": component["label"],
                "purpose": component["purpose"],
            }
        )
    sections: list[dict[str, object]] = []
    for section in output["sections"]:  # type: ignore[index]
        row = _registry_row(
            authority,
            node_id="F1",
            entity_type="section",
            local_id=str(section["local_id"]),
        )
        sections.append(
            {
                "local_id": section["local_id"],
                "stable_id": row["stable_id"],
                "title": section["title"],
                "purpose": section["purpose"],
                "component_local_ids": list(section["component_local_ids"]),
                "component_stable_ids": [
                    component_rows[str(local_id)]["stable_id"]
                    for local_id in section["component_local_ids"]
                ],
            }
        )
    identities = authority.get("registry_identities")
    if not isinstance(identities, Mapping):
        raise Phase5FormalRunnerError("formal registry identities are unavailable")
    return {
        "registry_identity": copy.deepcopy(identities["F1"]),
        "page_title": output["page_title"],
        "layout_pattern": output["layout_pattern"],
        "sections": sections,
        "components": components,
    }


def _f2_view(
    state: Mapping[str, object],
    authority: Mapping[str, object],
) -> dict[str, object]:
    output = _validated_node_output(state, "F2")
    f1 = _f1_view(state, authority)
    components = {
        str(item["local_id"]): item
        for item in f1["components"]  # type: ignore[index]
    }
    states: list[dict[str, object]] = []
    for item in output["states"]:  # type: ignore[index]
        row = _registry_row(
            authority,
            node_id="F2",
            entity_type="state",
            local_id=str(item["local_id"]),
        )
        states.append(
            {
                "local_id": item["local_id"],
                "stable_id": row["stable_id"],
                "name": item["name"],
                "description": item["description"],
                "visible_component_local_ids": list(
                    item["visible_component_local_ids"]
                ),
                "visible_component_stable_ids": [
                    components[str(local_id)]["stable_id"]
                    for local_id in item["visible_component_local_ids"]
                ],
            }
        )
    identities = authority.get("registry_identities")
    if not isinstance(identities, Mapping):
        raise Phase5FormalRunnerError("formal registry identities are unavailable")
    return {
        "registry_identity": copy.deepcopy(identities["F2"]),
        "states": states,
    }


def _f3_view(
    state: Mapping[str, object],
    authority: Mapping[str, object],
) -> dict[str, object]:
    output = _validated_node_output(state, "F3")
    f1 = _f1_view(state, authority)
    f2 = _f2_view(state, authority)
    components = {
        str(item["local_id"]): item
        for item in f1["components"]  # type: ignore[index]
    }
    states = {
        str(item["local_id"]): item
        for item in f2["states"]  # type: ignore[index]
    }
    interactions: list[dict[str, object]] = []
    for item in output["interactions"]:  # type: ignore[index]
        row = _registry_row(
            authority,
            node_id="F3",
            entity_type="interaction",
            local_id=str(item["local_id"]),
        )
        interactions.append(
            {
                "local_id": item["local_id"],
                "stable_id": row["stable_id"],
                "trigger_component_local_id": item[
                    "trigger_component_local_id"
                ],
                "trigger_component_stable_id": components[
                    str(item["trigger_component_local_id"])
                ]["stable_id"],
                "source_state_local_id": item["source_state_local_id"],
                "source_state_stable_id": states[
                    str(item["source_state_local_id"])
                ]["stable_id"],
                "action": item["action"],
                "target_state_local_id": item["target_state_local_id"],
                "target_state_stable_id": states[
                    str(item["target_state_local_id"])
                ]["stable_id"],
                "user_feedback": item["user_feedback"],
            }
        )
    identities = authority.get("registry_identities")
    if not isinstance(identities, Mapping):
        raise Phase5FormalRunnerError("formal registry identities are unavailable")
    return {
        "registry_identity": copy.deepcopy(identities["F3"]),
        "interactions": interactions,
    }


def _dynamic_projection(
    *,
    node_id: str,
    state: Mapping[str, object],
    authority: Mapping[str, object],
) -> dict[str, object]:
    projection: dict[str, object] = {}
    if node_id in {"F2", "F3", "F4"}:
        projection["f1_registered_structure_view"] = _f1_view(state, authority)
    if node_id in {"F3", "F4"}:
        projection["f2_registered_state_visibility_view"] = _f2_view(
            state,
            authority,
        )
    if node_id == "F4":
        projection["f3_registered_interaction_view"] = _f3_view(
            state,
            authority,
        )
        projection["deterministic_use_case_mapping_view"] = {
            "mapping_identity": copy.deepcopy(authority["mapping_identity"]),
            "ordered_mappings": copy.deepcopy(authority["ordered_mappings"]),
        }
    return projection


def _f3_plan(dynamic: Mapping[str, object]) -> list[dict[str, object]]:
    f1_view = dynamic.get("f1_registered_structure_view")
    state_view = dynamic.get("f2_registered_state_visibility_view")
    if not isinstance(f1_view, Mapping) or not isinstance(state_view, Mapping):
        raise Phase5FormalRunnerError("F3 authority views are unavailable")
    try:
        return build_canonical_f3_interaction_plan(
            f1_registered_structure_view=f1_view,
            f2_registered_state_visibility_view=state_view,
        )
    except ValueError as exc:
        raise Phase5FormalRunnerError("F3 trigger plan is invalid") from exc


def _f4_plan(
    static_payload: Mapping[str, object],
    dynamic: Mapping[str, object],
) -> list[dict[str, object]]:
    use_cases = static_payload.get("canonical_use_cases")
    state_view = dynamic.get("f2_registered_state_visibility_view")
    if not isinstance(use_cases, list) or not use_cases:
        raise Phase5FormalRunnerError("F4 use-case order is unavailable")
    if not isinstance(state_view, Mapping):
        raise Phase5FormalRunnerError("F4 state view is unavailable")
    states = state_view.get("states")
    if not isinstance(states, list) or not states:
        raise Phase5FormalRunnerError("F4 state order is unavailable")
    plan: list[dict[str, object]] = []
    for index, use_case in enumerate(use_cases):
        state = states[min(index, len(states) - 1)]
        if (
            not isinstance(use_case, Mapping)
            or not isinstance(use_case.get("use_case_id"), str)
            or not isinstance(state, Mapping)
            or not isinstance(state.get("stable_id"), str)
        ):
            raise Phase5FormalRunnerError("F4 target binding is invalid")
        plan.append(
            {
                "position": index,
                "use_case_ref": {
                    "ref_type": "canonical_b_use_case",
                    "ref_id": str(use_case["use_case_id"]),
                    "ref_revision": "canonical_b.use_case.v1",
                },
                "state_ref": {
                    "ref_type": "registry_stable",
                    "ref_id": str(state["stable_id"]),
                    "ref_revision": REGISTRY_REVISION,
                },
            }
        )
    return plan


def _node_input(
    *,
    package: Phase5SealedActionPackage,
    matrix_row_id: str,
    node_id: str,
    state: Mapping[str, object],
    authority: Mapping[str, object],
) -> bytes:
    static = build_phase5_node_static_projection(
        package,
        matrix_row_id=matrix_row_id,
        node_id=node_id,
    )
    payload = static["provider_payload"]
    if not isinstance(payload, Mapping):
        raise Phase5FormalRunnerError("formal static projection is invalid")
    dynamic = _dynamic_projection(
        node_id=node_id,
        state=state,
        authority=authority,
    )
    static_key = (
        "path2_static_projection"
        if package.to_dict()["route"] == PATH2_ROUTE
        else "path1_static_projection"
    )
    value = {
        "schema_version": NODE_INPUT_SCHEMA_VERSION,
        "node_id": node_id,
        "provider_case_ref": payload["provider_case_ref"],
        "provider_request_ref": payload["provider_request_ref"],
        static_key: copy.deepcopy(dict(payload)),
        "same_run_validated_upstream_projection": dynamic,
    }
    prohibited = _scan_keys(value) & _PROHIBITED_PROVIDER_KEYS
    if prohibited:
        raise Phase5FormalRunnerError(
            f"formal Provider input contains prohibited keys: {sorted(prohibited)}"
        )
    return _canonical(value)


def _output_contract(node_id: str) -> dict[str, object]:
    contracts: dict[str, dict[str, object]] = {
        "F1": {
            "exact_top_level_keys": [
                "page_title",
                "layout_pattern",
                "sections",
                "components",
            ],
            "section_exact_keys": [
                "local_id",
                "entity_type",
                "title",
                "purpose",
                "component_local_ids",
                "refs",
            ],
            "component_exact_keys": [
                "local_id",
                "entity_type",
                "component_type",
                "section_local_id",
                "label",
                "purpose",
                "refs",
            ],
            "constants": {
                "section.entity_type": "section",
                "component.entity_type": "component",
                "all.refs": [],
            },
        },
        "F2": {
            "exact_top_level_keys": ["states"],
            "state_exact_keys": [
                "local_id",
                "entity_type",
                "name",
                "description",
                "visible_component_local_ids",
                "refs",
            ],
            "constants": {"state.entity_type": "state", "all.refs": []},
        },
        "F3": {
            "exact_top_level_keys": ["interactions"],
            "interaction_exact_keys": [
                "local_id",
                "entity_type",
                "trigger_component_local_id",
                "source_state_local_id",
                "action",
                "target_state_local_id",
                "user_feedback",
                "refs",
            ],
            "constants": {
                "interaction.entity_type": "interaction",
                "all.refs": [],
            },
        },
        "F4": {
            "exact_top_level_keys": ["acceptance_checks"],
            "acceptance_check_exact_keys": [
                "local_id",
                "entity_type",
                "description",
                "use_case_refs",
                "state_ref",
                "refs",
            ],
            "reference_exact_keys": [
                "ref_type",
                "ref_id",
                "ref_revision",
            ],
            "constants": {
                "acceptance_check.entity_type": "candidate_acceptance_check",
                "all.refs": [],
            },
        },
    }
    return copy.deepcopy(contracts[node_id])


def _node_prompt(node_id: str, input_bytes: bytes) -> bytes:
    """Build Phase 5 prompts through the project-wide shared authority."""

    input_value = _strict_json(input_bytes, f"{node_id} formal input")
    if not isinstance(input_value, Mapping):
        raise Phase5FormalRunnerError(f"{node_id} formal input is invalid")
    dynamic = input_value["same_run_validated_upstream_projection"]
    static_keys = [
        key
        for key in ("path1_static_projection", "path2_static_projection")
        if key in input_value
    ]
    if len(static_keys) != 1:
        raise Phase5FormalRunnerError(
            f"{node_id} formal static projection route is invalid"
        )
    static = input_value[static_keys[0]]
    if not isinstance(dynamic, Mapping) or not isinstance(static, Mapping):
        raise Phase5FormalRunnerError(f"{node_id} formal projections are invalid")
    try:
        return build_canonical_f1_f4_prompt(
            node_id=node_id,
            input_bytes=input_bytes,
            required_interaction_plan=(
                _f3_plan(dynamic) if node_id == "F3" else None
            ),
            required_acceptance_target_plan=(
                _f4_plan(static, dynamic) if node_id == "F4" else None
            ),
        )
    except ValueError as exc:
        raise Phase5FormalRunnerError(
            "shared F1-F4 prompt authority rejected the Phase 5 projection"
        ) from exc


def _node_config(
    *,
    node_id: str,
    final_action_receipt_sha256: str | None,
) -> bytes:
    return _canonical(
        {
            "schema_version": NODE_CONFIG_SCHEMA_VERSION,
            "node_id": node_id,
            "selected_profile_sha256": RTX5090_PROFILE_SHA256,
            "final_action_receipt_sha256": final_action_receipt_sha256,
            "device": "cuda:0",
            "dtype": "bfloat16",
            "compute_dtype": "bfloat16",
            "quantization": "none",
            "cpu_offload": False,
            "local_files_only": True,
            "model_context_tokens": MODEL_CONTEXT_TOKENS,
            "fixed_max_new_tokens": None,
            "input_truncation": False,
            "output_truncation": False,
            "complete_json_stopping": True,
            "do_sample": False,
            "temperature": 0.0,
            "top_p": 1.0,
            "decode_seed": DECODE_SEED,
            "per_node_wall_timeout_seconds": PER_NODE_TIMEOUT_SECONDS,
        }
    )


def _node_request(
    *,
    node_id: str,
    input_bytes: bytes,
    prompt_bytes: bytes,
    config_bytes: bytes,
) -> bytes:
    input_value = _strict_json(input_bytes, f"{node_id} request input")
    if not isinstance(input_value, Mapping):
        raise Phase5FormalRunnerError(f"{node_id} request input is invalid")
    return _canonical(
        {
            "schema_version": NODE_REQUEST_SCHEMA_VERSION,
            "source_kind": "sealed_formal_holdout",
            "node_id": node_id,
            "provider_case_ref": input_value["provider_case_ref"],
            "provider_request_ref": input_value["provider_request_ref"],
            "input_identity": _identity(input_bytes),
            "prompt_identity": _identity(prompt_bytes),
            "config_identity": _identity(config_bytes),
            "generate_call_cap": 1,
            "automatic_retry": False,
            "retry_count": 0,
        }
    )


def _selected_evidence(row: Mapping[str, object]) -> list[Mapping[str, object]]:
    intervention = row["intervention"]
    if not isinstance(intervention, Mapping):
        raise Phase5FormalRunnerError("formal intervention is invalid")
    selected = []
    for item in row["evidence_items"]:  # type: ignore[index]
        if not isinstance(item, Mapping):
            raise Phase5FormalRunnerError("formal evidence item is invalid")
        if (
            intervention["kind"] == "remove_critical_role"
            and item["role"] == intervention["critical_role"]
        ):
            continue
        if (
            item["adoption_or_intervention_signal"] == "irrelevant"
            and intervention["kind"] != "irrelevant_evidence"
        ):
            continue
        selected.append(item)
    return selected


def build_phase5_local_assembly_bindings(
    package: Phase5SealedActionPackage,
    *,
    matrix_row_id: str,
) -> tuple[AgentContextBundle, object]:
    """Build local-only context/guidance without adding Provider-visible fields."""

    package.validate()
    rows = [
        item
        for item in package.to_dict()["runtime_rows"]
        if item["matrix_row_id"] == matrix_row_id
    ]
    if len(rows) != 1:
        raise Phase5FormalRunnerError("formal assembly row is not unique")
    row = rows[0]
    b_input = row["b_input"]
    if not isinstance(b_input, Mapping):
        raise Phase5FormalRunnerError("formal assembly B input is invalid")
    static = build_phase5_node_static_projection(
        package,
        matrix_row_id=matrix_row_id,
        node_id="F1",
    )["provider_payload"]
    if not isinstance(static, Mapping):
        raise Phase5FormalRunnerError("formal assembly static projection is invalid")
    provider_case_ref = str(static["provider_case_ref"])
    selected = _selected_evidence(row)
    retrieval_results: dict[str, list[dict[str, object]]] = {}
    requirement_summary = str(b_input["requirement_summary"])
    retrieval_results["requirement"] = [
        {
            "score": 1.0,
            "doc_id": f"phase5-requirement:{provider_case_ref}",
            "role": "requirement",
            "dataset": "phase5_owner_sealed_requirement_projection",
            "subset": "path1_formal",
            "sample_id": provider_case_ref,
            "title": "Owner-sealed requirement projection",
            "summary": requirement_summary,
            "references": [],
        }
    ]
    for role in _ROLE_ORDER:
        role_items = [item for item in selected if item["role"] == role]
        if role_items:
            retrieval_results[role] = [
                {
                    "score": 1.0,
                    "doc_id": str(item["opaque_doc_id"]),
                    "role": role,
                    "dataset": "phase5_owner_sealed_licensed_summary",
                    "subset": "path1_formal",
                    "sample_id": str(item["opaque_doc_id"]),
                    "title": f"Licensed {role} summary",
                    "summary": str(
                        item["project_authored_nonverbatim_short_summary"]
                    ),
                    "references": [],
                }
                for item in role_items
            ]
        else:
            retrieval_results[role] = [
                {
                    "score": 1.0,
                    "doc_id": f"phase5-local-absence:{provider_case_ref}:{role}",
                    "role": role,
                    "dataset": "phase5_local_absence_declaration",
                    "subset": "path1_formal",
                    "sample_id": f"{provider_case_ref}:{role}",
                    "title": f"No Provider-visible {role} evidence",
                    "summary": (
                        "This experiment cell contains no Provider-visible "
                        f"{role} evidence."
                    ),
                    "references": [],
                }
            ]
    context = AgentContextBundle(
        original_requirement=str(b_input["requirement_projection"]),
        requirement_summary=requirement_summary,
        target_device=str(b_input["target_device"]),
        task_type=str(b_input["task_type"]),
        constraints=[str(item) for item in b_input["normalized_constraints"]],
        use_cases=[
            UseCase(
                use_case_id=str(item["use_case_id"]),
                title=str(item["title"]),
                actor=str(item["actor"]),
                goal=str(item["goal"]),
                expected_outcome=str(item["expected_outcome"]),
            )
            for item in b_input["canonical_use_cases"]
        ],
        retrieval_queries={
            role: f"phase5 sealed local binding {role}" for role in ROLE_ORDER
        },
        retrieval_results=retrieval_results,  # type: ignore[arg-type]
    )
    context.validate()
    guidance = RetrievalGuidanceBuilder().build(context)
    return context, guidance


@dataclass
class SyntheticPhase5CaseWorker:
    """No-model worker used only by focused synthetic tests."""

    worker_id: str
    _state: Mapping[str, object] | None = None
    _closed: bool = False
    _calls: dict[str, int] | None = None

    def __post_init__(self) -> None:
        self._calls = {node_id: 0 for node_id in NODE_ORDER}

    def bind_graph_state(self, state: Mapping[str, object]) -> None:
        if self._closed:
            raise Phase5FormalRunnerError("synthetic worker is closed")
        self._state = copy.deepcopy(dict(state))

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
        del input_bytes, prompt_bytes, config_bytes, request_bytes
        if self._closed or self._state is None or self._calls is None:
            raise Phase5FormalRunnerError("synthetic worker state is unavailable")
        if node_id not in NODE_ORDER or self._calls[node_id] != 0:
            raise Phase5FormalRunnerError("synthetic worker call scope drifted")
        self._calls[node_id] += 1
        on_generation_started()
        return json.dumps(
            phase4_synthetic_fixture_output(node_id, self._state),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def close(self) -> Mapping[str, object]:
        if self._calls is None:
            raise Phase5FormalRunnerError("synthetic worker call ledger is unavailable")
        self._closed = True
        return {
            "schema_version": "req2web.phase5.synthetic_worker_receipt.v1",
            "worker_id": self.worker_id,
            "worker_kind": "synthetic_no_model_validation_only",
            "terminal_status": "normal_completed",
            "worker_exit_verified": True,
            "stdout_thread_joined": True,
            "stderr_thread_joined": True,
            "stderr_capture_completed": True,
            "model_loaded": False,
            "generate_calls": dict(self._calls),
        }


def synthetic_phase5_worker_factory(
    row: Mapping[str, object],
) -> SyntheticPhase5CaseWorker:
    return SyntheticPhase5CaseWorker(
        worker_id=f"synthetic-phase5-worker-{int(row['row_order']):04d}"
    )


def _case_leaf(
    package: Phase5SealedActionPackage,
    row: Mapping[str, object],
) -> str:
    static = build_phase5_node_static_projection(
        package,
        matrix_row_id=str(row["matrix_row_id"]),
        node_id="F1",
    )
    provider = static["provider_payload"]
    if not isinstance(provider, Mapping):
        raise Phase5FormalRunnerError("formal case provider binding is invalid")
    token = str(provider["provider_case_ref"]).rsplit("-", 1)[-1][:16]
    return f"{int(row['row_order']):04d}-{token}"


def _artifact_inventory(root: Path, *, exclude: set[Path]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        if path in exclude or path.is_symlink():
            continue
        raw = path.read_bytes()
        rows.append(
            {
                "relative_path": path.relative_to(root).as_posix(),
                "sha256": _sha(raw),
                "byte_length": len(raw),
            }
        )
    return rows


def _validate_inventory(root: Path, rows: object) -> None:
    if not isinstance(rows, list):
        raise Phase5FormalRunnerError("case artifact inventory is invalid")
    expected_paths: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "relative_path",
            "sha256",
            "byte_length",
        }:
            raise Phase5FormalRunnerError("case artifact inventory row is invalid")
        relative = str(row["relative_path"])
        if relative in expected_paths or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise Phase5FormalRunnerError("case artifact inventory path is invalid")
        path = root / Path(relative)
        if not path.is_file() or path.is_symlink():
            raise Phase5FormalRunnerError("case artifact inventory file is missing")
        raw = path.read_bytes()
        if _sha(raw) != row["sha256"] or len(raw) != row["byte_length"]:
            raise Phase5FormalRunnerError("case artifact inventory drifted")
        expected_paths.append(relative)
    if expected_paths != sorted(expected_paths):
        raise Phase5FormalRunnerError("case artifact inventory order drifted")
    actual_paths = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.name != "case_result.json"
    )
    if actual_paths != expected_paths:
        raise Phase5FormalRunnerError("case artifact inventory is incomplete")


def _existing_call_events(call_root: Path) -> list[dict[str, object]]:
    if not call_root.exists():
        return []
    if not call_root.is_dir() or call_root.is_symlink():
        raise Phase5FormalRunnerError("formal call ledger root is invalid")
    paths = sorted(call_root.glob("generate-start-*.json"))
    events = [_read_canonical(path, "formal call event") for path in paths]
    for index, (path, event) in enumerate(zip(paths, events, strict=True), start=1):
        if path.name != f"generate-start-{index:06d}.json":
            raise Phase5FormalRunnerError("formal call event sequence drifted")
        if (
            event.get("schema_version") != CALL_EVENT_SCHEMA_VERSION
            or event.get("call_index") != index
            or event.get("automatic_retry") is not False
            or event.get("retry_count") != 0
        ):
            raise Phase5FormalRunnerError("formal call event binding drifted")
    return events


def _elapsed_cost_minor_units(elapsed_seconds: float, hourly_rate: int) -> int:
    if hourly_rate < 0:
        raise Phase5FormalRunnerError("hourly rate must be non-negative")
    return math.ceil(max(0.0, elapsed_seconds) * hourly_rate / 3600.0)


def _pre_call_limits(
    *,
    package_payload: Mapping[str, object],
    started_count: int,
    started_epoch_seconds: float,
    hourly_rate_minor_units: int,
    written_bytes: int,
) -> None:
    budget = package_payload["budget"]
    if not isinstance(budget, Mapping):
        raise Phase5FormalRunnerError("formal budget is invalid")
    elapsed = max(0.0, time.time() - started_epoch_seconds)
    if started_count >= int(budget["node_generate_call_cap"]):
        raise Phase5FormalRunnerError("formal node generate-call cap reached")
    if elapsed >= int(budget["time_cap_seconds"]):
        raise Phase5FormalRunnerError("formal wall-time cap reached")
    if _elapsed_cost_minor_units(elapsed, hourly_rate_minor_units) >= int(
        budget["cost_cap_minor_units"]
    ):
        raise Phase5FormalRunnerError("formal cost cap reached")
    if written_bytes >= int(budget["storage_cap_bytes"]):
        raise Phase5FormalRunnerError("formal storage cap reached")


def _attempt_result(
    *,
    node_id: str,
    status: str,
    generate_started: bool,
    raw: bytes | None,
    error_code: str | None,
) -> dict[str, object]:
    body = {
        "schema_version": ATTEMPT_RESULT_SCHEMA_VERSION,
        "node_id": node_id,
        "attempt_index": 1,
        "status": status,
        "generate_started": generate_started,
        "generate_call_count": int(generate_started),
        "automatic_retry": False,
        "retry_count": 0,
        "raw_response_sha256": None if raw is None else _sha(raw),
        "raw_response_byte_length": None if raw is None else len(raw),
        "raw_contract_pass": status == "raw_contract_pass",
        "normalization_attempted": False,
        "repair_attempted": False,
        "fallback_attempted": False,
        "post_generation_semantic_adjustment": False,
        "error_code": error_code,
    }
    return {"attempt_result_id": _record_id("phase5-formal-attempt", body), **body}


def _case_result(
    *,
    package: Phase5SealedActionPackage,
    row: Mapping[str, object],
    status: str,
    node_results: Sequence[Mapping[str, object]],
    supervisor_receipt: Mapping[str, object] | None,
    case_root: Path,
    error_code: str | None,
    resumed: bool = False,
) -> dict[str, object]:
    case_result_path = case_root / "case_result.json"
    inventory = _artifact_inventory(case_root, exclude={case_result_path})
    body = {
        "schema_version": CASE_RESULT_SCHEMA_VERSION,
        "package_id": package.to_dict()["package_id"],
        "package_sha256": package.sha256(),
        "run_id": package.to_dict()["run_id"],
        "row_order": row["row_order"],
        "matrix_row_id": row["matrix_row_id"],
        "opaque_case_ref": row["opaque_case_ref"],
        "runtime_case_id": row["runtime_case_id"],
        "status": status,
        "node_order": list(NODE_ORDER),
        "node_results": [copy.deepcopy(dict(item)) for item in node_results],
        "generate_started_count": sum(
            int(bool(item["generate_started"])) for item in node_results
        ),
        "automatic_retry_count": 0,
        "normalization_count": 0,
        "repair_count": 0,
        "fallback_count": 0,
        "post_generation_semantic_adjustment_count": 0,
        "g0_prefreeze_reference_sha256": row["g0_reference"]["package_sha256"],
        "supervisor_receipt": (
            None
            if supervisor_receipt is None
            else copy.deepcopy(dict(supervisor_receipt))
        ),
        "artifact_inventory": inventory,
        "error_code": error_code,
        "resumed_from_complete_case_boundary": resumed,
        "owner_evaluation_executed": False,
        "formal_quality_claimed": False,
    }
    return {"case_result_id": _record_id("phase5-formal-case", body), **body}


def _validate_complete_case(
    *,
    package: Phase5SealedActionPackage,
    row: Mapping[str, object],
    case_root: Path,
) -> dict[str, object]:
    result = _read_canonical(case_root / "case_result.json", "formal case result")
    if (
        result.get("schema_version") != CASE_RESULT_SCHEMA_VERSION
        or result.get("package_sha256") != package.sha256()
        or result.get("run_id") != package.to_dict()["run_id"]
        or result.get("row_order") != row["row_order"]
        or result.get("matrix_row_id") != row["matrix_row_id"]
        or result.get("runtime_case_id") != row["runtime_case_id"]
        or result.get("status")
        not in {
            "assembled_candidate_pending_owner_evaluation",
            "failed_closed",
            "incomplete_experiment",
        }
    ):
        raise Phase5FormalRunnerError("formal complete-case binding drifted")
    body = {key: result[key] for key in result if key != "case_result_id"}
    if result.get("case_result_id") != _record_id("phase5-formal-case", body):
        raise Phase5FormalRunnerError("formal case result identity drifted")
    _validate_inventory(case_root, result["artifact_inventory"])
    return result


def _run_manifest(package: Phase5SealedActionPackage) -> dict[str, object]:
    payload = package.to_dict()
    body = {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "runner_schema_version": RUNNER_SCHEMA_VERSION,
        "package_id": payload["package_id"],
        "package_sha256": package.sha256(),
        "package_kind": payload["package_kind"],
        "route": payload["route"],
        "run_id": payload["run_id"],
        "source_action_commit": payload["source_action_commit"],
        "final_action_receipt_sha256": payload["authority_bindings"][
            "final_action_receipt_sha256"
        ],
        "node_order": list(NODE_ORDER),
        "runtime_row_count": len(payload["runtime_rows"]),
        "prompt_revision": PROMPT_AUTHORITY_REVISION,
        "prompt_authority_identity": copy.deepcopy(PROMPT_AUTHORITY_IDENTITY),
        "historical_phase_specific_prompt_revisions": [
            HISTORICAL_PROMPT_REVISION,
            HISTORICAL_PATH2_PROMPT_REVISION,
        ],
        "historical_phase_specific_prompts_are_runtime_sources": False,
        "phase5_formal_execution_status": PHASE5_FORMAL_EXECUTION_STATUS,
        "accepted_phase4_canonical_full_flow_runner": (
            ACCEPTED_PHASE4_CANONICAL_FULL_FLOW_RUNNER
        ),
        "phase4_closure_authority_commit": (
            PHASE4_CLOSURE_AUTHORITY_COMMIT
        ),
        "phase4_canonical_flow_schema_version": (
            PHASE4_CANONICAL_FLOW_SCHEMA_VERSION
        ),
        "phase4_canonical_b_adapter_revision": ADAPTER_REVISION,
        "phase4_real_model_graph_revision": REAL_MODEL_GRAPH_REVISION,
        "phase4_real_model_graph_runtime_class": (
            Phase4RealModelGraphRuntime.__name__
        ),
        "phase4_browser_audit_schema_version": (
            PHASE4_BROWSER_AUDIT_SCHEMA_VERSION
        ),
        "phase4_browser_canary_schema_version": (
            PHASE4_BROWSER_CANARY_SCHEMA_VERSION
        ),
        "phase4_browser_summary_schema_version": (
            PHASE4_BROWSER_SUMMARY_SCHEMA_VERSION
        ),
        "semantic_alignment_contract_revision": (
            SEMANTIC_ALIGNMENT_CONTRACT_REVISION
        ),
        "semantic_alignment_request_schema_version": (
            SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION
        ),
        "semantic_alignment_result_schema_version": (
            SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION
        ),
        "phase5_independent_b_allowed": False,
        "phase5_independent_prompt_allowed": False,
        "phase5_manual_formal_f1_f4_loop_allowed": False,
        "phase5_manual_f1_f4_loop_used": False,
        "phase5_independent_downstream_allowed": False,
        "synthetic_validation_flow_role": SYNTHETIC_VALIDATION_FLOW_ROLE,
        "shared_phase4_langgraph_used_for_synthetic_validation": True,
        "synthetic_validation_is_active_full_flow": False,
        "case_boundary_recovery_only": True,
        "partial_case_generate_resume_allowed": False,
        "automatic_retry_allowed": False,
        "owner_evaluation_in_runner": False,
    }
    return {"run_manifest_id": _record_id("phase5-formal-run-manifest", body), **body}


def validate_phase5_formal_result_root(
    *,
    package: Phase5SealedActionPackage,
    result_root: Path,
) -> dict[str, object]:
    """Replay the complete result root without model, GPU, network, or writes."""

    package.validate()
    payload = package.to_dict()
    if (
        not isinstance(result_root, Path)
        or not result_root.is_dir()
        or result_root.is_symlink()
    ):
        raise Phase5FormalRunnerError("formal result root is invalid")
    manifest = _read_canonical(
        result_root / "run_manifest.json",
        "formal run manifest",
    )
    if manifest != _run_manifest(package):
        raise Phase5FormalRunnerError("formal run manifest replay drifted")
    clock = _read_canonical(result_root / "run_clock.json", "formal run clock")
    if (
        set(clock)
        != {
            "run_clock_id",
            "schema_version",
            "package_sha256",
            "run_id",
            "started_epoch_seconds",
            "hourly_rate_minor_units",
            "time_cap_seconds",
            "cost_cap_minor_units",
            "new_process_does_not_reset_clock",
        }
        or clock["schema_version"] != RUN_CLOCK_SCHEMA_VERSION
        or clock["package_sha256"] != package.sha256()
        or clock["run_id"] != payload["run_id"]
        or clock["time_cap_seconds"] != payload["budget"]["time_cap_seconds"]
        or clock["cost_cap_minor_units"] != payload["budget"]["cost_cap_minor_units"]
        or clock["new_process_does_not_reset_clock"] is not True
    ):
        raise Phase5FormalRunnerError("formal run clock replay drifted")
    clock_body = {key: clock[key] for key in clock if key != "run_clock_id"}
    if clock["run_clock_id"] != _record_id("phase5-formal-run-clock", clock_body):
        raise Phase5FormalRunnerError("formal run clock identity drifted")
    events = _existing_call_events(result_root / "call-ledger")
    ledger = _read_canonical(
        result_root / "aggregate_call_ledger.json",
        "formal aggregate call ledger",
    )
    if set(ledger) != {
        "call_ledger_id",
        "schema_version",
        "package_sha256",
        "run_id",
        "node_generate_call_cap",
        "actual_generate_started_count",
        "automatic_retry_count",
        "budget_reset_count",
        "events",
    }:
        raise Phase5FormalRunnerError("formal aggregate call ledger keys drifted")
    ledger_body = {
        key: ledger[key] for key in ledger if key != "call_ledger_id"
    }
    if (
        ledger["schema_version"] != CALL_LEDGER_SCHEMA_VERSION
        or ledger["package_sha256"] != package.sha256()
        or ledger["run_id"] != payload["run_id"]
        or ledger["node_generate_call_cap"]
        != payload["budget"]["node_generate_call_cap"]
        or ledger["actual_generate_started_count"] != len(events)
        or ledger["automatic_retry_count"] != 0
        or ledger["budget_reset_count"] != 0
        or ledger["events"] != events
        or ledger["call_ledger_id"]
        != _record_id("phase5-formal-call-ledger", ledger_body)
    ):
        raise Phase5FormalRunnerError("formal aggregate call ledger drifted")
    rows = payload["runtime_rows"]
    results = []
    for row in rows:
        case_root = result_root / "cases" / _case_leaf(package, row)
        results.append(
            _validate_complete_case(
                package=package,
                row=row,
                case_root=case_root,
            )
        )
    summary = _read_canonical(
        result_root / "run_summary.json",
        "formal run summary",
    )
    expected_counts = {
        status: sum(1 for item in results if item["status"] == status)
        for status in (
            "assembled_candidate_pending_owner_evaluation",
            "failed_closed",
            "incomplete_experiment",
        )
    }
    if set(summary) != {
        "run_summary_id",
        "schema_version",
        "runner_schema_version",
        "package_id",
        "package_sha256",
        "package_kind",
        "route",
        "run_id",
        "source_action_commit",
        "node_order",
        "runtime_row_count",
        "terminal_row_count",
        "status_counts",
        "actual_generate_started_count",
        "node_generate_call_cap",
        "automatic_retry_count",
        "normalization_count",
        "repair_count",
        "fallback_count",
        "post_generation_semantic_adjustment_count",
        "elapsed_seconds",
        "estimated_cost_minor_units",
        "written_bytes_before_summary",
        "call_ledger_sha256",
        "case_result_ids",
        "all_rows_terminal",
        "owner_evaluation_executed",
        "formal_quality_claimed",
        "claim_boundary",
    }:
        raise Phase5FormalRunnerError("formal run summary keys drifted")
    summary_body = {
        key: summary[key] for key in summary if key != "run_summary_id"
    }
    if (
        summary["schema_version"] != RUN_SUMMARY_SCHEMA_VERSION
        or summary["runner_schema_version"] != RUNNER_SCHEMA_VERSION
        or summary["package_id"] != payload["package_id"]
        or summary["package_sha256"] != package.sha256()
        or summary["package_kind"] != payload["package_kind"]
        or summary["route"] != payload["route"]
        or summary["run_id"] != payload["run_id"]
        or summary["source_action_commit"] != payload["source_action_commit"]
        or summary["node_order"] != list(NODE_ORDER)
        or summary["runtime_row_count"] != len(rows)
        or summary["terminal_row_count"] != len(results)
        or summary["status_counts"] != expected_counts
        or summary["actual_generate_started_count"] != len(events)
        or summary["node_generate_call_cap"]
        != payload["budget"]["node_generate_call_cap"]
        or any(
            summary[key] != 0
            for key in (
                "automatic_retry_count",
                "normalization_count",
                "repair_count",
                "fallback_count",
                "post_generation_semantic_adjustment_count",
            )
        )
        or summary["call_ledger_sha256"] != _sha(_canonical(ledger))
        or summary["case_result_ids"]
        != [item["case_result_id"] for item in results]
        or summary["all_rows_terminal"] is not True
        or summary["owner_evaluation_executed"] is not False
        or summary["formal_quality_claimed"] is not False
        or summary["run_summary_id"]
        != _record_id("phase5-formal-run-summary", summary_body)
    ):
        raise Phase5FormalRunnerError("formal run summary replay drifted")
    for key in (
        "elapsed_seconds",
        "estimated_cost_minor_units",
        "written_bytes_before_summary",
    ):
        value = summary[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise Phase5FormalRunnerError(
                f"formal run summary {key} is invalid"
            )
    return summary


def run_phase5_formal_runner(
    *,
    package: Phase5SealedActionPackage,
    result_root: Path,
    worker_factory: Phase5WorkerFactory,
    action_authority: Phase5FinalActionAuthority | None = None,
    allow_synthetic_validation_only: bool = False,
    resume_existing: bool = False,
    hourly_rate_minor_units: int = 0,
    console: object | None = None,
) -> dict[str, object]:
    """Run the synthetic structure check or fail closed at the formal gate."""

    _validate_inherited_phase4_authority()
    package.validate()
    payload = package.to_dict()
    if payload["package_kind"] == "synthetic_validation_only":
        if allow_synthetic_validation_only is not True:
            raise Phase5FormalRunnerError(
                "synthetic runner execution requires explicit validation-only confirmation"
            )
        if action_authority is not None:
            raise Phase5FormalRunnerError(
                "synthetic runner execution must not carry final action authority"
            )
    elif payload["package_kind"] in {
        "owner_sealed_formal_h1",
        "project_authored_path2_model_pilot",
    }:
        raise Phase5FormalRunnerError(
            "Phase 5 inherited the accepted Phase 4 canonical authority, "
            "but formal model/H1 action remains disabled"
        )
    else:
        raise Phase5FormalRunnerError("formal package kind is unsupported")
    if not isinstance(result_root, Path) or not result_root.is_absolute():
        raise Phase5FormalRunnerError("formal result root must be absolute")
    if result_root.is_symlink():
        raise Phase5FormalRunnerError("formal result root must not be a symlink")
    if isinstance(hourly_rate_minor_units, bool) or hourly_rate_minor_units < 0:
        raise Phase5FormalRunnerError("formal hourly rate is invalid")

    manifest = _run_manifest(package)
    manifest_path = result_root / "run_manifest.json"
    clock_path = result_root / "run_clock.json"
    summary_path = result_root / "run_summary.json"
    if result_root.exists() and not result_root.is_dir():
        raise Phase5FormalRunnerError("formal result root is not a directory")
    if result_root.exists() and summary_path.exists():
        if not resume_existing:
            raise Phase5FormalRunnerError("formal result root is already terminal")
        return validate_phase5_formal_result_root(
            package=package,
            result_root=result_root,
        )
    result_root.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        if not resume_existing or _read_canonical(
            manifest_path,
            "formal run manifest",
        ) != manifest:
            raise Phase5FormalRunnerError("formal run manifest drifted")
    else:
        if any(result_root.iterdir()):
            raise Phase5FormalRunnerError(
                "formal result root is non-empty without a manifest"
            )
        _write_once(manifest_path, _canonical(manifest))

    if clock_path.exists():
        clock = _read_canonical(clock_path, "formal run clock")
        if set(clock) != {
            "run_clock_id",
            "schema_version",
            "package_sha256",
            "run_id",
            "started_epoch_seconds",
            "hourly_rate_minor_units",
            "time_cap_seconds",
            "cost_cap_minor_units",
            "new_process_does_not_reset_clock",
        }:
            raise Phase5FormalRunnerError("formal run clock keys drifted")
        clock_body = {
            key: clock[key] for key in clock if key != "run_clock_id"
        }
        if (
            clock.get("schema_version") != RUN_CLOCK_SCHEMA_VERSION
            or clock.get("package_sha256") != package.sha256()
            or clock.get("run_id") != payload["run_id"]
            or clock.get("hourly_rate_minor_units") != hourly_rate_minor_units
            or clock.get("time_cap_seconds")
            != payload["budget"]["time_cap_seconds"]
            or clock.get("cost_cap_minor_units")
            != payload["budget"]["cost_cap_minor_units"]
            or not isinstance(clock.get("started_epoch_seconds"), (int, float))
            or isinstance(clock.get("started_epoch_seconds"), bool)
            or clock.get("new_process_does_not_reset_clock") is not True
            or clock.get("run_clock_id")
            != _record_id("phase5-formal-run-clock", clock_body)
        ):
            raise Phase5FormalRunnerError("formal run clock drifted")
    else:
        clock_body = {
            "schema_version": RUN_CLOCK_SCHEMA_VERSION,
            "package_sha256": package.sha256(),
            "run_id": payload["run_id"],
            "started_epoch_seconds": time.time(),
            "hourly_rate_minor_units": hourly_rate_minor_units,
            "time_cap_seconds": payload["budget"]["time_cap_seconds"],
            "cost_cap_minor_units": payload["budget"]["cost_cap_minor_units"],
            "new_process_does_not_reset_clock": True,
        }
        clock = {
            "run_clock_id": _record_id("phase5-formal-run-clock", clock_body),
            **clock_body,
        }
        _write_once(clock_path, _canonical(clock))
    started_epoch_seconds = float(clock["started_epoch_seconds"])

    written_bytes = sum(
        item.stat().st_size
        for item in result_root.rglob("*")
        if item.is_file() and not item.is_symlink()
    )
    call_root = result_root / "call-ledger"
    call_events = _existing_call_events(call_root)
    started_count = len(call_events)
    if started_count > int(payload["budget"]["node_generate_call_cap"]):
        raise Phase5FormalRunnerError("existing formal call ledger exceeds cap")

    rows = payload["runtime_rows"]
    if not isinstance(rows, list) or not rows:
        raise Phase5FormalRunnerError("formal runtime rows are unavailable")
    cases_root = result_root / "cases"
    complete_prefix: list[dict[str, object]] = []
    first_missing = 0
    for index, row in enumerate(rows):
        case_root = cases_root / _case_leaf(package, row)
        if (case_root / "case_result.json").exists():
            if index != len(complete_prefix):
                raise Phase5FormalRunnerError(
                    "formal complete-case results are not a contiguous prefix"
                )
            complete_prefix.append(
                _validate_complete_case(
                    package=package,
                    row=row,
                    case_root=case_root,
                )
            )
            first_missing = index + 1
        else:
            if case_root.exists():
                raise Phase5FormalRunnerError(
                    "partial formal case cannot resume generation"
                )
            break
    for row in rows[first_missing + 1 :]:
        if (cases_root / _case_leaf(package, row) / "case_result.json").exists():
            raise Phase5FormalRunnerError(
                "formal complete-case results are not a contiguous prefix"
            )
    if complete_prefix and not resume_existing:
        raise Phase5FormalRunnerError(
            "formal complete cases require explicit case-boundary resume"
        )

    results = list(complete_prefix)
    global_stop: str | None = None
    for row in rows[first_missing:]:
        case_root = cases_root / _case_leaf(package, row)
        if global_stop is not None:
            case_root.mkdir(parents=True, exist_ok=False)
            result = _case_result(
                package=package,
                row=row,
                status="incomplete_experiment",
                node_results=[],
                supervisor_receipt=None,
                case_root=case_root,
                error_code=global_stop,
            )
            written_bytes += _write_once(
                case_root / "case_result.json",
                _canonical(result),
            )
            results.append(result)
            continue

        case_root.mkdir(parents=True, exist_ok=False)
        binding = {
            "schema_version": "req2web.phase5.formal_case_binding.v1",
            "package_sha256": package.sha256(),
            "run_id": payload["run_id"],
            "row_order": row["row_order"],
            "matrix_row_id": row["matrix_row_id"],
            "opaque_case_ref": row["opaque_case_ref"],
            "runtime_case_id": row["runtime_case_id"],
            "repeat_index": row["repeat_index"],
            "matrix_seed": row["seed"],
            "intervention": copy.deepcopy(row["intervention"]),
        }
        written_bytes += _write_once(
            case_root / "case_binding.json",
            _canonical(binding),
        )
        context, guidance = build_phase5_local_assembly_bindings(
            package,
            matrix_row_id=str(row["matrix_row_id"]),
        )
        written_bytes += _write_once(
            case_root / "local_agent_context.json",
            _canonical(context.to_dict()),
        )
        written_bytes += _write_once(
            case_root / "local_retrieval_guidance.json",
            _canonical(guidance.to_dict()),
        )
        b_input = _sealed_formal_canonical_b_projection(row)
        worker: Phase5CaseWorker | None = None
        supervisor_receipt: Mapping[str, object] | None = None
        node_results: list[Mapping[str, object]] = []
        error_code: str | None = None
        status = "failed_closed"
        graph_result: Mapping[str, object] | None = None
        try:
            _pre_call_limits(
                package_payload=payload,
                started_count=started_count,
                started_epoch_seconds=started_epoch_seconds,
                hourly_rate_minor_units=hourly_rate_minor_units,
                written_bytes=written_bytes,
            )
            worker = worker_factory(row)

            def node_executor(
                node_id: str,
                authority_state: Mapping[str, object],
                authority: Mapping[str, object],
            ) -> Mapping[str, object]:
                nonlocal error_code, started_count, written_bytes
                input_bytes = _node_input(
                    package=package,
                    matrix_row_id=str(row["matrix_row_id"]),
                    node_id=node_id,
                    state=authority_state,
                    authority=authority,
                )
                prompt_bytes = _node_prompt(node_id, input_bytes)
                config_bytes = _node_config(
                    node_id=node_id,
                    final_action_receipt_sha256=payload["authority_bindings"][
                        "final_action_receipt_sha256"
                    ],
                )
                request_bytes = _node_request(
                    node_id=node_id,
                    input_bytes=input_bytes,
                    prompt_bytes=prompt_bytes,
                    config_bytes=config_bytes,
                )
                attempt_root = case_root / "attempts" / node_id
                pre_call = {
                    "schema_version": PRE_CALL_SCHEMA_VERSION,
                    "package_sha256": package.sha256(),
                    "run_id": payload["run_id"],
                    "row_order": row["row_order"],
                    "matrix_row_id": row["matrix_row_id"],
                    "node_id": node_id,
                    "attempt_index": 1,
                    "input_identity": _identity(input_bytes),
                    "prompt_identity": _identity(prompt_bytes),
                    "config_identity": _identity(config_bytes),
                    "request_identity": _identity(request_bytes),
                    "generate_call_cap": 1,
                    "automatic_retry": False,
                    "retry_count": 0,
                }
                for filename, raw in (
                    ("config.json", config_bytes),
                    ("input.json", input_bytes),
                    ("pre_call_record.json", _canonical(pre_call)),
                    ("prompt.json", prompt_bytes),
                    ("request.json", request_bytes),
                ):
                    written_bytes += _write_once(attempt_root / filename, raw)
                _pre_call_limits(
                    package_payload=payload,
                    started_count=started_count,
                    started_epoch_seconds=started_epoch_seconds,
                    hourly_rate_minor_units=hourly_rate_minor_units,
                    written_bytes=written_bytes,
                )
                generation_started = False

                def mark_started() -> None:
                    nonlocal generation_started, started_count, written_bytes
                    if generation_started:
                        raise Phase5FormalRunnerError(
                            f"{node_id} emitted generation_started twice"
                        )
                    generation_started = True
                    started_count += 1
                    started = {
                        "schema_version": GENERATION_STARTED_SCHEMA_VERSION,
                        "call_index": started_count,
                        "run_id": payload["run_id"],
                        "row_order": row["row_order"],
                        "node_id": node_id,
                        "attempt_index": 1,
                        "generate_call_consumed": True,
                        "automatic_retry": False,
                        "retry_count": 0,
                    }
                    event = {
                        "schema_version": CALL_EVENT_SCHEMA_VERSION,
                        **{
                            key: started[key]
                            for key in (
                                "call_index",
                                "run_id",
                                "row_order",
                                "node_id",
                                "attempt_index",
                                "automatic_retry",
                                "retry_count",
                            )
                        },
                    }
                    written_bytes += _write_once(
                        attempt_root / "generation_started.json",
                        _canonical(started),
                    )
                    written_bytes += _write_once(
                        call_root / f"generate-start-{started_count:06d}.json",
                        _canonical(event),
                    )

                raw: bytes | None = None
                try:
                    worker.bind_graph_state(authority_state)  # type: ignore[union-attr]
                    raw = worker.generate(
                        node_id=node_id,
                        input_bytes=input_bytes,
                        prompt_bytes=prompt_bytes,
                        config_bytes=config_bytes,
                        request_bytes=request_bytes,
                        on_generation_started=mark_started,
                    )
                    if not generation_started:
                        raise Phase5FormalRunnerError(
                            f"{node_id} returned without generation_started"
                        )
                    if type(raw) is not bytes or not raw:
                        raise Phase5FormalRunnerError(
                            f"{node_id} returned empty raw bytes"
                        )
                    written_bytes += _write_once(
                        attempt_root / "raw_response.bin",
                        raw,
                    )
                    parsed = _strict_json(raw, f"{node_id} raw response")
                    if not isinstance(parsed, Mapping):
                        raise Phase5FormalRunnerError(
                            f"{node_id} raw response root is not an object"
                        )
                    phase4_validate_node_output(
                        node_id,
                        parsed,
                        authority_state,
                    )
                    attempt = _attempt_result(
                        node_id=node_id,
                        status="raw_contract_pass",
                        generate_started=True,
                        raw=raw,
                        error_code=None,
                    )
                    raw_capture = make_real_model_raw_capture(raw)
                    execution_status = "validated"
                    output: Mapping[str, object] | None = copy.deepcopy(
                        dict(parsed)
                    )
                    failure = None
                except Exception as exc:
                    error_code = (
                        f"{node_id.lower()}_"
                        f"{type(exc).__name__.lower()}_failed_closed"
                    )
                    attempt = _attempt_result(
                        node_id=node_id,
                        status="failed_closed",
                        generate_started=generation_started,
                        raw=raw,
                        error_code=error_code,
                    )
                    raw_capture = (
                        make_real_model_raw_capture(raw)
                        if type(raw) is bytes and raw
                        else {
                            "state": "not_formed",
                            "byte_length": 0,
                            "sha256": NO_CAPTURE_SHA256,
                            "source_kind": REAL_MODEL_SOURCE_KIND,
                        }
                    )
                    execution_status = "failed_closed"
                    output = None
                    failure = {
                        "failure_code": "phase5_formal_node_failed_closed",
                        "failure_stage": node_id,
                        "retry_allowed": False,
                        "fallback_allowed": False,
                        "message_code": type(exc).__name__,
                    }
                node_results.append(attempt)
                written_bytes += _write_once(
                    attempt_root / "attempt_result.json",
                    _canonical(attempt),
                )
                attempt_binding = {
                    "phase5_attempt_result": attempt,
                    "raw_capture": raw_capture,
                    "input_identity": _identity(input_bytes),
                    "prompt_identity": _identity(prompt_bytes),
                    "config_identity": _identity(config_bytes),
                    "request_identity": _identity(request_bytes),
                }
                return {
                    "schema_version": (
                        "req2web.phase4.real_model_node_execution.v1"
                    ),
                    "node_id": node_id,
                    "status": execution_status,
                    "source_kind": REAL_MODEL_SOURCE_KIND,
                    "generate_call_count": int(generation_started),
                    "raw_capture": raw_capture,
                    "attempt_identity": make_identity(
                        attempt_binding,
                        revision=(
                            "req2web.phase5.formal_langgraph_attempt.v1"
                        ),
                    ),
                    "output": output,
                    "raw_model_contract_success": (
                        execution_status == "validated"
                    ),
                    "normalized_node_contract_success": (
                        execution_status == "validated"
                    ),
                    "failure": failure,
                }

            def delivery_executor(
                final_state: Mapping[str, object],
                page_spec: Mapping[str, object],
                assembly_report: Mapping[str, object],
            ) -> Mapping[str, object]:
                nonlocal written_bytes
                mapping = final_state.get("mapping_record")
                composition = final_state.get(
                    "candidate_composition_record"
                )
                if not isinstance(mapping, Mapping) or not isinstance(
                    composition,
                    Mapping,
                ):
                    raise Phase5FormalRunnerError(
                        "shared LangGraph delivery inputs are unavailable"
                    )
                written_bytes += _write_once(
                    case_root / "mapping.json",
                    _canonical(mapping),
                )
                written_bytes += _write_once(
                    case_root / "candidate_composition_record.json",
                    _canonical(composition),
                )
                written_bytes += _write_once(
                    case_root / "assembled_page_spec.json",
                    _canonical(page_spec),
                )
                written_bytes += _write_once(
                    case_root / "assembly_report.json",
                    _canonical(assembly_report),
                )
                return {
                    "graph_delivery_success": True,
                    "terminal_status": (
                        "assembled_candidate_pending_owner_evaluation"
                    ),
                    "owner_evaluation_executed": False,
                    "formal_quality_claimed": False,
                }

            runtime = Phase4RealModelGraphRuntime(
                node_executor=node_executor,
                context=context,
                guidance=guidance,
                delivery_executor=delivery_executor,
            )
            initial_state = create_real_model_graph_state(
                run_id=(
                    f"{payload['run_id']}:row-{int(row['row_order']):04d}"
                ),
                b_input=b_input,
                upstream_binding={
                    "schema_version": (
                        "req2web.phase5.formal_langgraph_upstream.v1"
                    ),
                    "package_sha256": package.sha256(),
                    "matrix_row_id": row["matrix_row_id"],
                    "runtime_case_id": row["runtime_case_id"],
                    "phase4_canonical_flow_runner": (
                        ACCEPTED_PHASE4_CANONICAL_FULL_FLOW_RUNNER
                    ),
                    "phase4_graph_revision": REAL_MODEL_GRAPH_REVISION,
                    "source_kind": "sealed_formal_holdout",
                    "model_action_enabled": False,
                    "h1_or_gold_opened": False,
                },
            )
            graph_result = runtime.invoke(
                initial_state,
                thread_id=(
                    f"{payload['run_id']}:row-{int(row['row_order']):04d}"
                ),
            )
            written_bytes += _write_once(
                case_root / "langgraph_final_state.json",
                _canonical(graph_result),
            )
            written_bytes += _write_once(
                case_root / "langgraph_events.json",
                _canonical(graph_result["events"]),
            )
            if graph_result["status"] == "completed":
                error_code = None
                status = "assembled_candidate_pending_owner_evaluation"
            else:
                failure = graph_result.get("failure")
                if isinstance(failure, Mapping):
                    error_code = (
                        f"langgraph_{failure.get('failure_stage')}_"
                        f"{failure.get('failure_code')}"
                    )
        except Exception as exc:
            error_code = (
                "worker_start_or_global_"
                f"{type(exc).__name__.lower()}_failed_closed"
            )
            global_stop = error_code
        finally:
            if worker is not None:
                try:
                    supervisor_receipt = worker.close()
                except Exception as exc:
                    supervisor_receipt = {
                        "schema_version": (
                            "req2web.phase5.worker_close_failure_receipt.v1"
                        ),
                        "worker_id": getattr(worker, "worker_id", None),
                        "terminal_status": "worker_teardown_unverified",
                        "error_type": type(exc).__name__,
                    }
                    if error_code is None:
                        error_code = "worker_teardown_unverified"
                        status = "failed_closed"
                        global_stop = error_code
                written_bytes += _write_once(
                    case_root / "supervisor_receipt.json",
                    _canonical(dict(supervisor_receipt)),
                )
        result = _case_result(
            package=package,
            row=row,
            status=status,
            node_results=node_results,
            supervisor_receipt=supervisor_receipt,
            case_root=case_root,
            error_code=error_code,
        )
        written_bytes += _write_once(
            case_root / "case_result.json",
            _canonical(result),
        )
        results.append(result)
        if console is not None:
            console.write(  # type: ignore[union-attr]
                f"[Phase 5] row={row['row_order']} status={status}\n"
            )
            console.flush()  # type: ignore[union-attr]

    call_events = _existing_call_events(call_root)
    ledger_body = {
        "schema_version": CALL_LEDGER_SCHEMA_VERSION,
        "package_sha256": package.sha256(),
        "run_id": payload["run_id"],
        "node_generate_call_cap": payload["budget"]["node_generate_call_cap"],
        "actual_generate_started_count": len(call_events),
        "automatic_retry_count": 0,
        "budget_reset_count": 0,
        "events": call_events,
    }
    ledger = {
        "call_ledger_id": _record_id("phase5-formal-call-ledger", ledger_body),
        **ledger_body,
    }
    written_bytes += _write_once(
        result_root / "aggregate_call_ledger.json",
        _canonical(ledger),
    )
    elapsed = max(0.0, time.time() - started_epoch_seconds)
    status_counts = {
        status: sum(1 for item in results if item["status"] == status)
        for status in (
            "assembled_candidate_pending_owner_evaluation",
            "failed_closed",
            "incomplete_experiment",
        )
    }
    summary_body = {
        "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
        "runner_schema_version": RUNNER_SCHEMA_VERSION,
        "package_id": payload["package_id"],
        "package_sha256": package.sha256(),
        "package_kind": payload["package_kind"],
        "route": payload["route"],
        "run_id": payload["run_id"],
        "source_action_commit": payload["source_action_commit"],
        "node_order": list(NODE_ORDER),
        "runtime_row_count": len(rows),
        "terminal_row_count": len(results),
        "status_counts": status_counts,
        "actual_generate_started_count": len(call_events),
        "node_generate_call_cap": payload["budget"]["node_generate_call_cap"],
        "automatic_retry_count": 0,
        "normalization_count": 0,
        "repair_count": 0,
        "fallback_count": 0,
        "post_generation_semantic_adjustment_count": 0,
        "elapsed_seconds": round(elapsed, 6),
        "estimated_cost_minor_units": _elapsed_cost_minor_units(
            elapsed,
            hourly_rate_minor_units,
        ),
        "written_bytes_before_summary": written_bytes,
        "call_ledger_sha256": _sha(_canonical(ledger)),
        "case_result_ids": [item["case_result_id"] for item in results],
        "all_rows_terminal": len(results) == len(rows),
        "owner_evaluation_executed": False,
        "formal_quality_claimed": False,
        "claim_boundary": (
            "sealed generation and assembly only; owner-only evaluation and "
            "formal quality remain separate"
        ),
    }
    summary = {
        "run_summary_id": _record_id("phase5-formal-run-summary", summary_body),
        **summary_body,
    }
    _write_once(summary_path, _canonical(summary))
    return summary


__all__ = [
    "ACCEPTED_PHASE4_CANONICAL_FULL_FLOW_RUNNER",
    "NODE_ORDER",
    "PHASE5_FORMAL_EXECUTION_STATUS",
    "PHASE5_FORMAL_MODEL_ACTION_ENABLED",
    "PROMPT_REVISION",
    "Phase5CaseWorker",
    "Phase5FormalRunnerError",
    "SYNTHETIC_VALIDATION_FLOW_ROLE",
    "SyntheticPhase5CaseWorker",
    "build_phase5_local_assembly_bindings",
    "run_phase5_formal_runner",
    "synthetic_phase5_worker_factory",
    "validate_phase5_formal_result_root",
]
