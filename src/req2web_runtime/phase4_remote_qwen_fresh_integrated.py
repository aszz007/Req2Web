"""P4-05 fresh integrated Qwen3.5-9B runner for AutoDL RTX 5090.

The module is intentionally separate from the historical remote F3 runner.
It reuses the existing model inventory and BF16 loader foundation, but owns a
new P4-05 policy, worker protocol, F1-F4 input chain, raw-first ledger, and
truthful delivery-bridge handoff.

No download, network, telemetry, training, quantization, CPU offload, input
truncation, output truncation, automatic retry, or model switching is allowed.
The worker loads once and serves the four fresh integrated generation calls.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import queue
import signal
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping

from req2web_agent import (
    AgentContextBundle,
    PROMPT_AUTHORITY_IDENTITY,
    PROMPT_AUTHORITY_REVISION,
    PROMPT_SCHEMA_VERSION as SHARED_PROMPT_SCHEMA_VERSION,
    build_canonical_f3_interaction_plan,
    build_canonical_f1_f4_prompt,
)
from req2web_generation import RetrievalGuidance
from req2web_generation.publication_language import contains_cjk_text
from req2web_orchestration.phase4_graph import (
    NODE_ORDER,
    REGISTRY_REVISION,
    phase4_assemble_candidate,
    phase4_compose_candidate,
    phase4_create_mapping,
    phase4_create_portable_authority_state,
    phase4_normalize_and_validate_f4_output,
    phase4_project_node_input_authority,
    phase4_register_node_output,
    phase4_synthetic_assembler_bindings,
    phase4_validate_node_output,
    synthetic_commerce_b_input,
    validate_b_input,
)
from req2web_runtime import phase4_local_qwen as _local
from req2web_runtime import phase4_local_qwen_fresh_integrated as _fresh
from req2web_runtime import phase4_remote_qwen as _remote
from req2web_runtime.phase4_fresh_delivery import (
    PHASE4_FRESH_DELIVERY_POLICY_A07A_DIRECT_V1,
    PHASE4_FRESH_DELIVERY_POLICY_FIELD_GATE_V1,
    Phase4GraphBoundDeliveryMaterials,
    build_phase4_actual_context_delivery_materials,
    build_phase4_graph_bound_delivery_materials,
    run_phase4_fresh_delivery,
)


P4_05_SCHEMA_PREFIX = "req2web.phase4.p4_05.remote_fresh_integrated"
FLOW_AUTHORITY_ROLE = (
    "shared_qwen_runtime_component_library_with_historical_manual_entrypoint"
)
ACTIVE_DEFAULT_ENTRY = False
HISTORICAL_MANUAL_ENTRYPOINT = True
P4_05_PROFILE_SCHEMA_VERSION = f"{P4_05_SCHEMA_PREFIX}.profile.v1"
P4_05_POLICY_SCHEMA_VERSION = f"{P4_05_SCHEMA_PREFIX}.policy.v1"
P4_05_RESULT_SCHEMA_VERSION = f"{P4_05_SCHEMA_PREFIX}.result.v1"
P4_05_INPUT_SCHEMA_VERSION = f"{P4_05_SCHEMA_PREFIX}.input.v1"
P4_05_PROVIDER_EVIDENCE_VIEW_SCHEMA_VERSION = (
    "req2web.provider.evidence_projection.v1"
)
P4_05_PROMPT_SCHEMA_VERSION = SHARED_PROMPT_SCHEMA_VERSION
P4_05_PRE_CALL_SCHEMA_VERSION = f"{P4_05_SCHEMA_PREFIX}.pre_call.v1"
P4_05_ATTEMPT_SCHEMA_VERSION = f"{P4_05_SCHEMA_PREFIX}.attempt.v2"
P4_05_LEDGER_SCHEMA_VERSION = f"{P4_05_SCHEMA_PREFIX}.ledger.v1"
P4_05_HISTORY_SCHEMA_VERSION = f"{P4_05_SCHEMA_PREFIX}.history.v1"
P4_05_RESUME_SCHEMA_VERSION = f"{P4_05_SCHEMA_PREFIX}.resume.v3"
P4_05_AGGREGATE_LEDGER_SCHEMA_VERSION = (
    f"{P4_05_SCHEMA_PREFIX}.aggregate_ledger.v1"
)
P4_05_SUPERVISOR_SCHEMA_VERSION = f"{P4_05_SCHEMA_PREFIX}.supervisor.v2"
P4_05_STREAM_SCHEMA_VERSION = f"{P4_05_SCHEMA_PREFIX}.stream.v1"
P4_05_WORKER_PROTOCOL = f"{P4_05_SCHEMA_PREFIX}.worker.v1"
P4_05_PARENT_BINDING_SCHEMA_VERSION = (
    f"{P4_05_SCHEMA_PREFIX}.parent_experiment_binding.v1"
)
P4_05_LEGACY_STABILITY_PROFILE_BINDING_SCHEMA_VERSION = (
    "req2web.phase4.p4_05.remote_qwen_stability.profile_binding.v1"
)
P4_05_STABILITY_PROFILE_BINDING_SCHEMA_VERSION = (
    "req2web.phase4.p4_05.remote_qwen_stability.profile_binding.v2"
)
P4_05_PILOT_ID = "p4-05-remote-qwen-fresh-integrated-v7"
P4_05_RUN_PREFIX = "p4-05-remote-qwen-fresh-integrated-run-"
P4_05_CASE_ID = "path3-commerce-checkout"
P4_05_REQUEST_ID = "p4-02a-synthetic-request-001"
P4_05_ROOT_MARKER = ".req2web-phase4-p4-05-remote-fresh-integrated-root"
P4_05_PROFILE_NAME = "remote_bf16_fresh_integrated"
P4_05_TIMEOUT_SECONDS = 1200
P4_05_LOAD_TIMEOUT_SECONDS = 600
P4_05_GENERATE_CALL_CAP = 1
P4_05_TOTAL_REAL_MODEL_CALL_CAP_PER_NODE = 3
P4_05_RETRY_COUNT = 0
P4_05_F3_F4_PROMPT_REVISION = "f3_f4_unique_reachable_acceptance_v1"
P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_REVISION = (
    "f4_actual_interaction_target_a07a_direct_v2"
)
P4_05_FULL_DIRECT_PROMPT_REVISION = PROMPT_AUTHORITY_REVISION
P4_05_REVISION_PROMPT_NODES = ("F3", "F4")
P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_NODES = ("F4",)
P4_05_FULL_DIRECT_PROMPT_NODES = NODE_ORDER
P4_05_F4_DIRECT_ACCEPTANCE_POLICY_RECEIPT_SCHEMA_VERSION = (
    f"{P4_05_SCHEMA_PREFIX}.f4_direct_acceptance_policy_receipt.v1"
)
P4_05_RESUME_PREFIX_F1_F2 = "F1-F2"
P4_05_RESUME_PREFIX_F1_F3 = "F1-F3"
P4_05_DTYPE = "bfloat16"
P4_05_QUANTIZATION = "none"
P4_05_COMPUTE_DTYPE = "bfloat16"
P4_05_MODEL_CONTEXT_TOKENS = _remote.REMOTE_MODEL_CONTEXT_TOKENS
P4_05_DEVICE_MAP = {"": 0}
P4_05_STOP_POLICY = {
    "fixed_max_new_tokens": False,
    "stop_on_complete_json_object": True,
    "stop_on_model_eos": True,
    "model_context_limit_enforced": True,
    "parent_wall_clock_timeout_enforced": True,
    "input_truncation": False,
    "output_truncation": False,
}
P4_05_PROVIDER_EVIDENCE_CLASS = "provider_evidence_view"
P4_05_PROVIDER_EVIDENCE_ROLE_ORDER = (
    "ui_reference",
    "interaction_flow",
    "implementation",
    "validation",
)
P4_05_PROVIDER_EVIDENCE_NODE_ROLES = {
    "F1": ("ui_reference", "implementation"),
    "F2": ("implementation",),
    "F3": ("interaction_flow",),
    "F4": ("validation",),
}
P4_05_INPUT_CLASSES = {
    "F1": (
        "canonical_b_requirement_view",
        "canonical_b_use_case_view",
        "canonical_b_constraint_view",
        "target_device",
        "task_type",
        "approved_structural_signals",
    ),
    "F2": (
        "canonical_b_requirement_view",
        "canonical_b_use_case_view",
        "canonical_b_constraint_view",
        "target_device",
        "task_type",
        "approved_structural_signals",
        "f1_registered_structure_view",
    ),
    "F3": (
        "canonical_b_requirement_view",
        "canonical_b_use_case_view",
        "canonical_b_constraint_view",
        "target_device",
        "task_type",
        "approved_structural_signals",
        "f1_registered_structure_view",
        "f2_registered_state_visibility_view",
    ),
    "F4": (
        "canonical_b_requirement_view",
        "canonical_b_use_case_view",
        "canonical_b_constraint_view",
        "target_device",
        "task_type",
        "approved_structural_signals",
        "f1_registered_structure_view",
        "f2_registered_state_visibility_view",
        "f3_registered_interaction_view",
        "deterministic_use_case_mapping_view",
    ),
}
P4_05_PROHIBITED_INPUT_CLASSES = (
    "full_agent_context",
    "retrieval_guidance",
    "retrieval_evidence_content",
    "retrieval_evidence_location",
    "rico_or_reference_assets",
    "h1_or_gold",
    "evaluator_only_material",
    "secrets_or_credentials",
    "result_package",
    "downstream_acceptance_evidence",
    "g0_payload",
    "unapproved_b_aux",
)


class Phase4RemoteFreshIntegratedError(ValueError):
    """Raised when the P4-05 remote contract cannot be satisfied."""


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
        raise Phase4RemoteFreshIntegratedError(
            "value is not canonical JSON"
        ) from exc


def _strict_json(raw: bytes, name: str) -> object:
    if type(raw) is not bytes or not raw or raw.startswith(b"\xef\xbb\xbf"):
        raise Phase4RemoteFreshIntegratedError(f"{name} is not valid raw JSON")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase4RemoteFreshIntegratedError(f"{name} is not valid JSON") from exc
    if _canonical_bytes(value) != raw:
        raise Phase4RemoteFreshIntegratedError(f"{name} is not canonical JSON")
    return value


def _parse_model_json(raw: bytes, name: str) -> object:
    """Parse complete model JSON without rewriting or canonicalizing raw bytes."""

    if type(raw) is not bytes or not raw or raw.startswith(b"\xef\xbb\xbf"):
        raise Phase4RemoteFreshIntegratedError(f"{name} is not valid raw JSON")

    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON constant: {value}")

    try:
        return json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Phase4RemoteFreshIntegratedError(f"{name} is not complete JSON") from exc


def _b64(raw: bytes) -> str:
    if type(raw) is not bytes:
        raise Phase4RemoteFreshIntegratedError("base64 input must be bytes")
    return base64.b64encode(raw).decode("ascii")


def _decode_b64(value: object, name: str) -> bytes:
    if not isinstance(value, str):
        raise Phase4RemoteFreshIntegratedError(f"{name} is not base64 text")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise Phase4RemoteFreshIntegratedError(f"{name} is not canonical base64") from exc


def _sha256(raw: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _identity(
    value: object,
    *,
    revision: str,
    identity_kind: str = "canonical_json",
) -> dict[str, object]:
    raw = value if type(value) is bytes else _canonical_bytes(value)
    return {
        "identity_kind": identity_kind,
        "sha256": _sha256(raw),
        "byte_length": len(raw),
        "revision": revision,
    }


def _validate_parent_experiment_binding(
    value: Mapping[str, object] | None,
    *,
    case_id: str,
    request_id: str,
) -> dict[str, object] | None:
    if value is None:
        return None
    data = copy.deepcopy(dict(value))
    expected_keys = (
        "schema_version",
        "experiment_id",
        "experiment_run_id",
        "experiment_policy_identity",
        "case_index",
        "case_id",
        "request_id",
    )
    if set(data) != set(expected_keys):
        raise Phase4RemoteFreshIntegratedError(
            "parent experiment binding exact keys drifted"
        )
    if (
        data["schema_version"] != P4_05_PARENT_BINDING_SCHEMA_VERSION
        or not isinstance(data["experiment_id"], str)
        or not data["experiment_id"]
        or not isinstance(data["experiment_run_id"], str)
        or not data["experiment_run_id"]
        or not isinstance(data["case_index"], int)
        or isinstance(data["case_index"], bool)
        or not 1 <= int(data["case_index"]) <= 10
        or data["case_id"] != case_id
        or data["request_id"] != request_id
    ):
        raise Phase4RemoteFreshIntegratedError(
            "parent experiment binding identity drifted"
        )
    policy_identity = data["experiment_policy_identity"]
    if (
        not isinstance(policy_identity, dict)
        or set(policy_identity)
        != {"identity_kind", "sha256", "byte_length", "revision"}
        or policy_identity["identity_kind"] != "canonical_json"
        or not isinstance(policy_identity["sha256"], str)
        or not str(policy_identity["sha256"]).startswith("sha256:")
        or not isinstance(policy_identity["byte_length"], int)
        or isinstance(policy_identity["byte_length"], bool)
        or int(policy_identity["byte_length"]) <= 0
        or not isinstance(policy_identity["revision"], str)
        or not policy_identity["revision"]
    ):
        raise Phase4RemoteFreshIntegratedError(
            "parent experiment policy identity is invalid"
        )
    return data


def _legacy_stable_profile_binding_identity(
    profile: "RemoteFreshIntegratedProfile",
) -> dict[str, object]:
    profile.validate()
    stable_profile = profile.to_dict()
    stable_profile.pop("profile_id", None)
    stable_profile.pop("free_vram_bytes_at_preflight", None)
    return _identity(
        stable_profile,
        revision=P4_05_LEGACY_STABILITY_PROFILE_BINDING_SCHEMA_VERSION,
    )


def make_stable_profile_binding_identity(
    profile: "RemoteFreshIntegratedProfile",
) -> dict[str, object]:
    """Bind clone-stable compatibility facts and retain live hardware evidence."""

    profile.validate()
    stable_profile = profile.to_dict()
    stable_profile.pop("profile_id", None)
    stable_profile.pop("free_vram_bytes_at_preflight", None)
    stable_profile.pop("device_uuid", None)
    driver_version = str(stable_profile.pop("driver_version"))
    stable_profile["driver_compatibility_series"] = driver_version.split(".", 1)[0]
    return _identity(
        stable_profile,
        revision=P4_05_STABILITY_PROFILE_BINDING_SCHEMA_VERSION,
    )


def migrate_stable_profile_binding_identity(
    profile: "RemoteFreshIntegratedProfile",
    recorded_identity: Mapping[str, object],
) -> dict[str, object]:
    """Validate a saved v1/v2 identity and return the current v2 identity."""

    recorded = dict(recorded_identity)
    revision = recorded.get("revision")
    if revision == P4_05_STABILITY_PROFILE_BINDING_SCHEMA_VERSION:
        expected = make_stable_profile_binding_identity(profile)
    elif revision == P4_05_LEGACY_STABILITY_PROFILE_BINDING_SCHEMA_VERSION:
        expected = _legacy_stable_profile_binding_identity(profile)
    else:
        raise Phase4RemoteFreshIntegratedError(
            "stability profile binding revision is unsupported"
        )
    if _canonical_bytes(recorded) != _canonical_bytes(expected):
        raise Phase4RemoteFreshIntegratedError(
            "saved stability profile identity drifted"
        )
    return make_stable_profile_binding_identity(profile)


def _profile_matches_expected_identity(
    profile: "RemoteFreshIntegratedProfile",
    actual_identity: object,
    expected_identity: Mapping[str, object],
) -> bool:
    expected = dict(expected_identity)
    if expected.get("revision") == P4_05_STABILITY_PROFILE_BINDING_SCHEMA_VERSION:
        actual_identity = make_stable_profile_binding_identity(profile)
    elif (
        expected.get("revision")
        == P4_05_LEGACY_STABILITY_PROFILE_BINDING_SCHEMA_VERSION
    ):
        actual_identity = _legacy_stable_profile_binding_identity(profile)
    return _canonical_bytes(actual_identity) == _canonical_bytes(expected)


def _expected_input_classes(node_id: str) -> tuple[str, ...]:
    try:
        return P4_05_INPUT_CLASSES[node_id]
    except KeyError as exc:
        raise Phase4RemoteFreshIntegratedError(
            f"unknown P4-05 input node: {node_id}"
        ) from exc


def _validate_provider_evidence_view(
    value: object,
    *,
    node_id: str,
) -> dict[str, object]:
    """Validate the optional typed evidence view without changing Phase 4 defaults."""

    if node_id not in NODE_ORDER:
        raise Phase4RemoteFreshIntegratedError(
            f"unknown provider evidence node: {node_id}"
        )
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "items",
    }:
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} provider evidence view keys drifted"
        )
    if value["schema_version"] != P4_05_PROVIDER_EVIDENCE_VIEW_SCHEMA_VERSION:
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} provider evidence view schema drifted"
        )
    raw_items = value["items"]
    if not isinstance(raw_items, list) or len(raw_items) > 4:
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} provider evidence items are invalid"
        )
    allowed_roles = P4_05_PROVIDER_EVIDENCE_NODE_ROLES[node_id]
    items: list[dict[str, str]] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping) or set(raw_item) != {
            "role",
            "opaque_doc_id",
            "project_authored_nonverbatim_short_summary",
        }:
            raise Phase4RemoteFreshIntegratedError(
                f"{node_id} provider evidence item {index} keys drifted"
            )
        role = raw_item["role"]
        doc_id = raw_item["opaque_doc_id"]
        summary = raw_item["project_authored_nonverbatim_short_summary"]
        if role not in allowed_roles:
            raise Phase4RemoteFreshIntegratedError(
                f"{node_id} provider evidence role is not allowed"
            )
        if (
            not isinstance(doc_id, str)
            or not 1 <= len(doc_id) <= 128
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
                for character in doc_id
            )
        ):
            raise Phase4RemoteFreshIntegratedError(
                f"{node_id} provider evidence document ID is invalid"
            )
        if (
            not isinstance(summary, str)
            or not 1 <= len(summary.strip()) <= 1200
            or contains_cjk_text(summary)
            or any(
                marker in summary.lower()
                for marker in (
                    "http://",
                    "https://",
                    "file://",
                    "/root/",
                    "../",
                    "d:\\",
                )
            )
        ):
            raise Phase4RemoteFreshIntegratedError(
                f"{node_id} provider evidence summary is invalid"
            )
        items.append(
            {
                "role": role,
                "opaque_doc_id": doc_id,
                "project_authored_nonverbatim_short_summary": summary,
            }
        )
    role_rank = {
        role: index
        for index, role in enumerate(P4_05_PROVIDER_EVIDENCE_ROLE_ORDER)
    }
    expected = sorted(
        items,
        key=lambda item: (role_rank[item["role"]], item["opaque_doc_id"]),
    )
    if items != expected or len({item["opaque_doc_id"] for item in items}) != len(items):
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} provider evidence order or identity drifted"
        )
    return {
        "schema_version": P4_05_PROVIDER_EVIDENCE_VIEW_SCHEMA_VERSION,
        "items": copy.deepcopy(items),
    }


def _validate_prompt_revision(
    *,
    node_id: str,
    prompt_revision: str | None,
) -> None:
    if prompt_revision is None:
        return
    prompt_nodes = {
        P4_05_F3_F4_PROMPT_REVISION: P4_05_REVISION_PROMPT_NODES,
        P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_REVISION: (
            P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_NODES
        ),
        P4_05_FULL_DIRECT_PROMPT_REVISION: P4_05_FULL_DIRECT_PROMPT_NODES,
    }.get(prompt_revision)
    if prompt_nodes is None or node_id not in prompt_nodes:
        raise Phase4RemoteFreshIntegratedError(
            "unsupported P4-05 prompt revision for node"
        )


def _prompt_revision_for_node(
    *,
    node_id: str,
    prompt_revision: str | None,
) -> str | None:
    _validate_prompt_revision(
        node_id=node_id,
        prompt_revision=prompt_revision,
    )
    prompt_nodes = {
        P4_05_F3_F4_PROMPT_REVISION: P4_05_REVISION_PROMPT_NODES,
        P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_REVISION: (
            P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_NODES
        ),
        P4_05_FULL_DIRECT_PROMPT_REVISION: P4_05_FULL_DIRECT_PROMPT_NODES,
    }.get(prompt_revision, ())
    if node_id not in prompt_nodes:
        return None
    return prompt_revision


def _prompt_nodes_for_revision(prompt_revision: str | None) -> tuple[str, ...]:
    if prompt_revision is None:
        return ()
    prompt_nodes = {
        P4_05_F3_F4_PROMPT_REVISION: P4_05_REVISION_PROMPT_NODES,
        P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_REVISION: (
            P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_NODES
        ),
        P4_05_FULL_DIRECT_PROMPT_REVISION: P4_05_FULL_DIRECT_PROMPT_NODES,
    }.get(prompt_revision)
    if prompt_nodes is None:
        raise Phase4RemoteFreshIntegratedError(
            "unsupported P4-05 prompt revision"
        )
    return prompt_nodes


def _uses_f4_direct_acceptance(prompt_revision: str | None) -> bool:
    return prompt_revision in {
        P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_REVISION,
        P4_05_FULL_DIRECT_PROMPT_REVISION,
    }


def _f3_required_interaction_plan(input_bytes: bytes) -> list[dict[str, object]]:
    value = _strict_json(input_bytes, "F3 full-direct input")
    envelope = _validate_node_input_value(value, "F3")
    projection = envelope["projection"]
    if not isinstance(projection, Mapping):
        raise Phase4RemoteFreshIntegratedError(
            "F3 full-direct projection is invalid"
        )
    state_view = projection.get("f2_registered_state_visibility_view")
    if not isinstance(state_view, Mapping):
        raise Phase4RemoteFreshIntegratedError(
            "F3 full-direct state view is unavailable"
        )
    states = state_view.get("states")
    if not isinstance(states, list) or not states:
        raise Phase4RemoteFreshIntegratedError(
            "F3 full-direct state order is unavailable"
        )
    f1_view = projection.get("f1_registered_structure_view")
    if not isinstance(f1_view, Mapping):
        raise Phase4RemoteFreshIntegratedError(
            "F3 full-direct F1 structure is unavailable"
        )
    try:
        return build_canonical_f3_interaction_plan(
            f1_registered_structure_view=f1_view,
            f2_registered_state_visibility_view=state_view,
        )
    except ValueError as exc:
        raise Phase4RemoteFreshIntegratedError(
            "F3 full-direct trigger plan is invalid"
        ) from exc


def _f4_required_acceptance_target_plan(
    input_bytes: bytes,
) -> list[dict[str, object]]:
    value = _strict_json(input_bytes, "F4 full-direct input")
    envelope = _validate_node_input_value(value, "F4")
    projection = envelope["projection"]
    if not isinstance(projection, Mapping):
        raise Phase4RemoteFreshIntegratedError(
            "F4 full-direct projection is invalid"
        )
    use_case_view = projection.get("canonical_b_use_case_view")
    state_view = projection.get("f2_registered_state_visibility_view")
    if not isinstance(use_case_view, Mapping) or not isinstance(
        state_view, Mapping
    ):
        raise Phase4RemoteFreshIntegratedError(
            "F4 full-direct authority views are unavailable"
        )
    use_cases = use_case_view.get("use_cases")
    states = state_view.get("states")
    if (
        not isinstance(use_cases, list)
        or not use_cases
        or not isinstance(states, list)
        or not states
    ):
        raise Phase4RemoteFreshIntegratedError(
            "F4 full-direct authority arrays are unavailable"
        )

    plan: list[dict[str, object]] = []
    for index, use_case in enumerate(use_cases):
        if not isinstance(use_case, Mapping) or not isinstance(
            use_case.get("use_case_id"), str
        ):
            raise Phase4RemoteFreshIntegratedError(
                "F4 full-direct use-case binding is invalid"
            )
        state = states[min(index, len(states) - 1)]
        if not isinstance(state, Mapping) or not isinstance(
            state.get("stable_id"), str
        ):
            raise Phase4RemoteFreshIntegratedError(
                "F4 full-direct state binding is invalid"
            )
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


def _validated_node_output(
    state: Mapping[str, object],
    node_id: str,
) -> Mapping[str, object]:
    record = state.get("node_results", {}).get(node_id)
    if not isinstance(record, Mapping):
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} validated node result is unavailable"
        )
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} validated node payload is unavailable"
        )
    output = payload.get("node_output")
    if not isinstance(output, Mapping):
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} validated node output is unavailable"
        )
    return output


def _registry_row(
    authority_projection: Mapping[str, object],
    *,
    node_id: str,
    entity_type: str,
    local_id: str,
) -> Mapping[str, object]:
    rows = authority_projection.get("registry_rows")
    if not isinstance(rows, list):
        raise Phase4RemoteFreshIntegratedError(
            "registered input view has no registry rows"
        )
    matches = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and row.get("node_id") == node_id
        and row.get("entity_type") == entity_type
        and row.get("local_id") == local_id
    ]
    if len(matches) != 1:
        raise Phase4RemoteFreshIntegratedError(
            f"registered input view target is not unique: {node_id}/{local_id}"
        )
    return matches[0]


def _f1_registered_structure_view(
    *,
    state: Mapping[str, object],
    authority_projection: Mapping[str, object],
) -> dict[str, object]:
    output = _validated_node_output(state, "F1")
    sections: list[dict[str, object]] = []
    components: list[dict[str, object]] = []
    component_rows: dict[str, Mapping[str, object]] = {}
    for component in output["components"]:
        row = _registry_row(
            authority_projection,
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
    for section in output["sections"]:
        row = _registry_row(
            authority_projection,
            node_id="F1",
            entity_type="section",
            local_id=str(section["local_id"]),
        )
        component_stable_ids = [
            component_rows[str(local_id)]["stable_id"]
            for local_id in section["component_local_ids"]
        ]
        sections.append(
            {
                "local_id": section["local_id"],
                "stable_id": row["stable_id"],
                "title": section["title"],
                "purpose": section["purpose"],
                "component_local_ids": list(section["component_local_ids"]),
                "component_stable_ids": component_stable_ids,
            }
        )
    return {
        "registry_identity": copy.deepcopy(
            authority_projection["registry_identities"]["F1"]
        ),
        "page_title": output["page_title"],
        "layout_pattern": output["layout_pattern"],
        "sections": sections,
        "components": components,
    }


def _f2_registered_state_visibility_view(
    *,
    state: Mapping[str, object],
    authority_projection: Mapping[str, object],
) -> dict[str, object]:
    output = _validated_node_output(state, "F2")
    f1_view = _f1_registered_structure_view(
        state=state,
        authority_projection=authority_projection,
    )
    component_rows = {
        str(component["local_id"]): component
        for component in f1_view["components"]
    }
    states: list[dict[str, object]] = []
    for item in output["states"]:
        row = _registry_row(
            authority_projection,
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
                    component_rows[str(local_id)]["stable_id"]
                    for local_id in item["visible_component_local_ids"]
                ],
            }
        )
    return {
        "registry_identity": copy.deepcopy(
            authority_projection["registry_identities"]["F2"]
        ),
        "states": states,
    }


def _f3_registered_interaction_view(
    *,
    state: Mapping[str, object],
    authority_projection: Mapping[str, object],
) -> dict[str, object]:
    output = _validated_node_output(state, "F3")
    f1_view = _f1_registered_structure_view(
        state=state,
        authority_projection=authority_projection,
    )
    component_rows = {
        str(component["local_id"]): component
        for component in f1_view["components"]
    }
    f2_view = _f2_registered_state_visibility_view(
        state=state,
        authority_projection=authority_projection,
    )
    state_rows = {
        str(item["local_id"]): item
        for item in f2_view["states"]
    }
    interactions: list[dict[str, object]] = []
    for item in output["interactions"]:
        row = _registry_row(
            authority_projection,
            node_id="F3",
            entity_type="interaction",
            local_id=str(item["local_id"]),
        )
        source_state = state_rows[str(item["source_state_local_id"])]
        target_state = state_rows[str(item["target_state_local_id"])]
        interactions.append(
            {
                "local_id": item["local_id"],
                "stable_id": row["stable_id"],
                "trigger_component_local_id": item[
                    "trigger_component_local_id"
                ],
                "trigger_component_stable_id": component_rows[
                    str(item["trigger_component_local_id"])
                ]["stable_id"],
                "source_state_local_id": item["source_state_local_id"],
                "source_state_stable_id": source_state["stable_id"],
                "action": item["action"],
                "target_state_local_id": item["target_state_local_id"],
                "target_state_stable_id": target_state["stable_id"],
                "user_feedback": item["user_feedback"],
            }
        )
    return {
        "registry_identity": copy.deepcopy(
            authority_projection["registry_identities"]["F3"]
        ),
        "interactions": interactions,
    }


def _provider_visible_projection(
    *,
    node_id: str,
    b_input: Mapping[str, object],
    state: Mapping[str, object],
    authority_projection: Mapping[str, object],
    provider_evidence_view: Mapping[str, object] | None = None,
) -> dict[str, object]:
    classes = _expected_input_classes(node_id)
    projection: dict[str, object] = {
        "canonical_b_requirement_view": {
            "requirement": b_input["requirement"],
            "requirement_summary": b_input["requirement_summary"],
        },
        "canonical_b_use_case_view": {
            "use_cases": copy.deepcopy(b_input["use_cases"]),
        },
        "canonical_b_constraint_view": {
            "constraints": copy.deepcopy(b_input["constraints"]),
        },
        "target_device": b_input["target_device"],
        "task_type": b_input["task_type"],
        "approved_structural_signals": [],
    }
    if node_id in {"F2", "F3", "F4"}:
        projection["f1_registered_structure_view"] = (
            _f1_registered_structure_view(
                state=state,
                authority_projection=authority_projection,
            )
        )
    if node_id in {"F3", "F4"}:
        projection["f2_registered_state_visibility_view"] = (
            _f2_registered_state_visibility_view(
                state=state,
                authority_projection=authority_projection,
            )
        )
    if node_id == "F4":
        projection["f3_registered_interaction_view"] = (
            _f3_registered_interaction_view(
                state=state,
                authority_projection=authority_projection,
            )
        )
        projection["deterministic_use_case_mapping_view"] = {
            "mapping_identity": copy.deepcopy(
                authority_projection["mapping_identity"]
            ),
            "ordered_mappings": copy.deepcopy(
                authority_projection["ordered_mappings"]
            ),
        }
    if provider_evidence_view is not None:
        projection[P4_05_PROVIDER_EVIDENCE_CLASS] = (
            _validate_provider_evidence_view(
                provider_evidence_view,
                node_id=node_id,
            )
        )
        classes = (*classes, P4_05_PROVIDER_EVIDENCE_CLASS)
    if tuple(projection) != classes:
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} provider projection classes drifted"
        )
    return projection


def _validate_node_input_value(value: object, node_id: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} input is not an object"
        )
    expected_keys = {
        "schema_version",
        "node_id",
        "case_id",
        "request_id",
        "logical_input_classes",
        "projection",
    }
    if set(value) != expected_keys:
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} input envelope keys drifted"
        )
    base_classes = _expected_input_classes(node_id)
    logical_classes = value["logical_input_classes"]
    if logical_classes == list(base_classes):
        classes = base_classes
    elif logical_classes == [*base_classes, P4_05_PROVIDER_EVIDENCE_CLASS]:
        classes = (*base_classes, P4_05_PROVIDER_EVIDENCE_CLASS)
    else:
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} input logical classes drifted"
        )
    if (
        value["schema_version"] != P4_05_INPUT_SCHEMA_VERSION
        or value["node_id"] != node_id
        or not isinstance(value["case_id"], str)
        or not isinstance(value["request_id"], str)
    ):
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} input envelope binding drifted"
        )
    projection = value["projection"]
    if not isinstance(projection, Mapping) or set(projection) != set(classes):
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} input projection classes drifted"
        )
    if "b_aux_advisory_view" in projection or "upstream_outputs" in value:
        raise Phase4RemoteFreshIntegratedError(
            f"{node_id} input contains an unapproved upstream view"
        )
    if P4_05_PROVIDER_EVIDENCE_CLASS in projection:
        _validate_provider_evidence_view(
            projection[P4_05_PROVIDER_EVIDENCE_CLASS],
            node_id=node_id,
        )
    return value


def _safe_path(value: Path, name: str, *, directory: bool | None = None) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise Phase4RemoteFreshIntegratedError(f"{name} must be an absolute Path")
    if value.is_symlink():
        raise Phase4RemoteFreshIntegratedError(f"{name} must not be a symlink")
    if directory is True and not value.is_dir():
        raise Phase4RemoteFreshIntegratedError(f"{name} must be a directory")
    if directory is False and not value.is_file():
        raise Phase4RemoteFreshIntegratedError(f"{name} must be a file")
    return value.resolve(strict=False)


def _write_fsync(path: Path, raw: bytes) -> None:
    if type(raw) is not bytes or not raw:
        raise Phase4RemoteFreshIntegratedError(f"cannot write empty artifact: {path.name}")
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
            raise Phase4RemoteFreshIntegratedError(f"write-once artifact drifted: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _prepare_graph_bound_delivery_materials(
    *,
    result_root: Path,
    graph_state: Mapping[str, object],
    upstream_context: AgentContextBundle | None = None,
    upstream_guidance: RetrievalGuidance | None = None,
) -> dict[str, object]:
    if upstream_context is None and upstream_guidance is None:
        materials = build_phase4_graph_bound_delivery_materials(
            graph_state=graph_state,
            material_root=result_root / "graph-bound-delivery-materials",
        )
        graph_context, graph_guidance = phase4_synthetic_assembler_bindings(
            graph_state
        )
    elif isinstance(upstream_context, AgentContextBundle) and isinstance(
        upstream_guidance, RetrievalGuidance
    ):
        materials = build_phase4_actual_context_delivery_materials(
            graph_state=graph_state,
            context=upstream_context,
            guidance=upstream_guidance,
            material_root=result_root / "graph-bound-delivery-materials",
        )
        graph_context, graph_guidance = upstream_context, upstream_guidance
    else:
        raise Phase4RemoteFreshIntegratedError(
            "upstream context and guidance must be supplied together"
        )
    if type(materials) is not Phase4GraphBoundDeliveryMaterials:
        raise Phase4RemoteFreshIntegratedError(
            "graph-bound delivery materials have the wrong type"
        )
    materials.validate()
    if (
        _canonical_bytes(materials.context.to_dict())
        != _canonical_bytes(graph_context.to_dict())
        or materials.binding["context_identity"]
        != _identity(
            graph_context.to_dict(),
            revision="req2web.agent.context.v1",
        )
    ):
        raise Phase4RemoteFreshIntegratedError(
            "graph-bound delivery context identity drifted"
        )
    if (
        _canonical_bytes(materials.guidance.to_dict())
        != _canonical_bytes(graph_guidance.to_dict())
        or materials.binding["guidance_identity"]
        != _identity(
            graph_guidance.to_dict(),
            revision="req2web.retrieval.guidance.v1",
        )
    ):
        raise Phase4RemoteFreshIntegratedError(
            "graph-bound delivery guidance identity drifted"
        )
    _write_fsync(
        result_root / "graph_bound_delivery_materials_binding.json",
        _canonical_bytes(materials.binding),
    )
    return {
        "materials": materials,
        "live": materials.live,
        "context": materials.context,
        "guidance": materials.guidance,
        "binding": materials.binding,
    }


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


def _action_state(*, model_action: bool, remote_action: bool) -> dict[str, object]:
    return {
        "schema_version": f"{P4_05_SCHEMA_PREFIX}.action_state.v1",
        "runtime_kind": P4_05_PROFILE_NAME,
        "model_action": model_action,
        "graph_runtime_execution": model_action,
        "dependency_installation": False,
        "training": False,
        "remote_action": remote_action,
        "network": False,
        "telemetry": False,
        "tracing": False,
        "local_files_only": True,
    }


@dataclass(frozen=True)
class RemoteFreshIntegratedProfile:
    """Exact BF16/no-quantization runtime profile used by the worker."""

    schema_version: str
    profile_name: str
    profile_id: str
    model_id: str
    model_revision: str
    model_root_identity: Mapping[str, object]
    model_inventory_identity: Mapping[str, object]
    model_file_count: int
    python_version: str
    transformers_version: str
    torch_version: str
    accelerate_version: str
    device_name: str
    device_uuid: str
    total_vram_bytes: int
    free_vram_bytes_at_preflight: int
    driver_version: str
    cuda_version: str
    device_index: int = 0
    dtype: str = P4_05_DTYPE
    quantization: str = P4_05_QUANTIZATION
    compute_dtype: str = P4_05_COMPUTE_DTYPE
    device_map: Mapping[str, int] = None  # type: ignore[assignment]
    cpu_offload: bool = False
    local_files_only: bool = True
    offline: bool = True
    network: bool = False
    telemetry: bool = False
    tracing: bool = False
    max_input_tokens: int = P4_05_MODEL_CONTEXT_TOKENS
    model_context_tokens: int = P4_05_MODEL_CONTEXT_TOKENS
    timeout_seconds: int = P4_05_TIMEOUT_SECONDS
    seed: int = 0
    decode: Mapping[str, object] = None  # type: ignore[assignment]
    model_loaded: bool = False
    run_occurred: bool = False

    @classmethod
    def create(
        cls,
        *,
        inventory: Mapping[str, object],
        runtime_facts: Mapping[str, object],
        gpu_facts: Mapping[str, object],
        timeout_seconds: int = P4_05_TIMEOUT_SECONDS,
    ) -> "RemoteFreshIntegratedProfile":
        root = {
            "schema_version": P4_05_PROFILE_SCHEMA_VERSION,
            "profile_id": "pending",
            "profile_name": P4_05_PROFILE_NAME,
            "model_id": _remote.REMOTE_MODEL_ID,
            "model_revision": _remote.REMOTE_MODEL_REVISION,
            "model_root_identity": dict(inventory["model_root_identity"]),
            "model_inventory_identity": dict(inventory["inventory_identity"]),
            "model_file_count": int(inventory["file_count"]),
            "python_version": str(runtime_facts["python_version"]),
            "transformers_version": str(runtime_facts["transformers_version"]),
            "torch_version": str(runtime_facts["torch_version"]),
            "accelerate_version": str(runtime_facts["accelerate_version"]),
            "device_name": str(gpu_facts["device_name"]),
            "device_uuid": str(gpu_facts["device_uuid"]),
            "total_vram_bytes": int(gpu_facts["total_vram_bytes"]),
            "free_vram_bytes_at_preflight": int(gpu_facts["free_vram_bytes"]),
            "driver_version": str(gpu_facts["driver_version"]),
            "cuda_version": str(gpu_facts["cuda_version"]),
            "device_index": 0,
            "dtype": P4_05_DTYPE,
            "quantization": P4_05_QUANTIZATION,
            "compute_dtype": P4_05_COMPUTE_DTYPE,
            "device_map": copy.deepcopy(P4_05_DEVICE_MAP),
            "cpu_offload": False,
            "local_files_only": True,
            "offline": True,
            "network": False,
            "telemetry": False,
            "tracing": False,
            "max_input_tokens": P4_05_MODEL_CONTEXT_TOKENS,
            "model_context_tokens": P4_05_MODEL_CONTEXT_TOKENS,
            "timeout_seconds": timeout_seconds,
            "seed": 0,
            "decode": {"do_sample": False, "temperature": 0.0, "top_p": 1.0},
            "model_loaded": False,
            "run_occurred": False,
        }
        root["profile_id"] = _identity(
            {key: value for key, value in root.items() if key != "profile_id"},
            revision=P4_05_PROFILE_SCHEMA_VERSION,
        )["sha256"]
        return cls.from_dict(root)

    @classmethod
    def from_dict(cls, value: object) -> "RemoteFreshIntegratedProfile":
        if not isinstance(value, Mapping):
            raise Phase4RemoteFreshIntegratedError("profile is not an object")
        expected = {
            "schema_version",
            "profile_id",
            "profile_name",
            "model_id",
            "model_revision",
            "model_root_identity",
            "model_inventory_identity",
            "model_file_count",
            "python_version",
            "transformers_version",
            "torch_version",
            "accelerate_version",
            "device_name",
            "device_uuid",
            "total_vram_bytes",
            "free_vram_bytes_at_preflight",
            "driver_version",
            "cuda_version",
            "device_index",
            "dtype",
            "quantization",
            "compute_dtype",
            "device_map",
            "cpu_offload",
            "local_files_only",
            "offline",
            "network",
            "telemetry",
            "tracing",
            "max_input_tokens",
            "model_context_tokens",
            "timeout_seconds",
            "seed",
            "decode",
            "model_loaded",
            "run_occurred",
        }
        if set(value) != expected:
            raise Phase4RemoteFreshIntegratedError("profile keys drifted")
        result = cls(**dict(value))
        result.validate()
        return result

    @classmethod
    def from_bytes(cls, raw: bytes) -> "RemoteFreshIntegratedProfile":
        return cls.from_dict(_strict_json(raw, "profile"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": P4_05_PROFILE_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "profile_name": P4_05_PROFILE_NAME,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "model_root_identity": dict(self.model_root_identity),
            "model_inventory_identity": dict(self.model_inventory_identity),
            "model_file_count": self.model_file_count,
            "python_version": self.python_version,
            "transformers_version": self.transformers_version,
            "torch_version": self.torch_version,
            "accelerate_version": self.accelerate_version,
            "device_name": self.device_name,
            "device_uuid": self.device_uuid,
            "total_vram_bytes": self.total_vram_bytes,
            "free_vram_bytes_at_preflight": self.free_vram_bytes_at_preflight,
            "driver_version": self.driver_version,
            "cuda_version": self.cuda_version,
            "device_index": self.device_index,
            "dtype": self.dtype,
            "quantization": self.quantization,
            "compute_dtype": self.compute_dtype,
            "device_map": dict(self.device_map or P4_05_DEVICE_MAP),
            "cpu_offload": self.cpu_offload,
            "local_files_only": self.local_files_only,
            "offline": self.offline,
            "network": self.network,
            "telemetry": self.telemetry,
            "tracing": self.tracing,
            "max_input_tokens": self.max_input_tokens,
            "model_context_tokens": self.model_context_tokens,
            "timeout_seconds": self.timeout_seconds,
            "seed": self.seed,
            "decode": dict(self.decode or {}),
            "model_loaded": self.model_loaded,
            "run_occurred": self.run_occurred,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def validate(self) -> None:
        data = self.to_dict()
        if data["schema_version"] != P4_05_PROFILE_SCHEMA_VERSION:
            raise Phase4RemoteFreshIntegratedError("profile schema drifted")
        if data["profile_name"] != P4_05_PROFILE_NAME:
            raise Phase4RemoteFreshIntegratedError("profile name drifted")
        if data["model_id"] != _remote.REMOTE_MODEL_ID or data["model_revision"] != _remote.REMOTE_MODEL_REVISION:
            raise Phase4RemoteFreshIntegratedError("profile model identity drifted")
        if data["model_file_count"] != _remote.REMOTE_FORMAL_FILE_COUNT:
            raise Phase4RemoteFreshIntegratedError(
                "profile model inventory file count drifted"
            )
        if (
            data["transformers_version"] != _remote.REMOTE_TRANSFORMERS_VERSION
            or data["torch_version"] != _remote.REMOTE_TORCH_VERSION
            or data["accelerate_version"] != _remote.REMOTE_ACCELERATE_VERSION
        ):
            raise Phase4RemoteFreshIntegratedError(
                "profile runtime package versions drifted"
            )
        if data["device_index"] != 0 or data["device_name"] != _remote.REMOTE_DEVICE_NAME:
            raise Phase4RemoteFreshIntegratedError("profile must use RTX 5090 GPU0")
        if data["total_vram_bytes"] < _remote.REMOTE_MIN_VRAM_BYTES:
            raise Phase4RemoteFreshIntegratedError("profile GPU VRAM is below RTX 5090 floor")
        if (
            not isinstance(data["device_uuid"], str)
            or not data["device_uuid"]
            or data["free_vram_bytes_at_preflight"] < 0
            or data["free_vram_bytes_at_preflight"] > data["total_vram_bytes"]
        ):
            raise Phase4RemoteFreshIntegratedError(
                "profile GPU facts are invalid"
            )
        if data["dtype"] != P4_05_DTYPE or data["compute_dtype"] != P4_05_COMPUTE_DTYPE:
            raise Phase4RemoteFreshIntegratedError("profile dtype must be BF16")
        if data["quantization"] != P4_05_QUANTIZATION:
            raise Phase4RemoteFreshIntegratedError("profile quantization must be none")
        if data["device_map"] != P4_05_DEVICE_MAP or data["cpu_offload"] is not False:
            raise Phase4RemoteFreshIntegratedError("profile placement drifted")
        for key in ("local_files_only", "offline"):
            if data[key] is not True:
                raise Phase4RemoteFreshIntegratedError(f"profile {key} must be true")
        for key in ("network", "telemetry", "tracing", "model_loaded", "run_occurred"):
            if data[key] is not False:
                raise Phase4RemoteFreshIntegratedError(f"profile {key} must be false")
        if data["max_input_tokens"] != P4_05_MODEL_CONTEXT_TOKENS or data["model_context_tokens"] != P4_05_MODEL_CONTEXT_TOKENS:
            raise Phase4RemoteFreshIntegratedError("profile context boundary drifted")
        if data["timeout_seconds"] < 1 or data["seed"] < 0:
            raise Phase4RemoteFreshIntegratedError("profile numeric bounds drifted")
        if data["decode"] != {
            "do_sample": False,
            "temperature": 0.0,
            "top_p": 1.0,
        }:
            raise Phase4RemoteFreshIntegratedError("profile decode settings drifted")
        expected_id = _identity(
            {key: value for key, value in data.items() if key != "profile_id"},
            revision=P4_05_PROFILE_SCHEMA_VERSION,
        )["sha256"]
        if data["profile_id"] != expected_id:
            raise Phase4RemoteFreshIntegratedError("profile identity drifted")


def create_p4_05_policy(
    *,
    run_id: str,
    result_root_marker: str,
    profile: RemoteFreshIntegratedProfile,
    case_id: str = P4_05_CASE_ID,
    request_id: str = P4_05_REQUEST_ID,
    prompt_revision: str | None = P4_05_FULL_DIRECT_PROMPT_REVISION,
    parent_experiment_binding: Mapping[str, object] | None = None,
) -> dict[str, object]:
    prompt_nodes = _prompt_nodes_for_revision(prompt_revision)
    profile.validate()
    parent_binding = _validate_parent_experiment_binding(
        parent_experiment_binding,
        case_id=case_id,
        request_id=request_id,
    )
    if list(_fresh.NODE_ORDER) != list(NODE_ORDER):
        raise Phase4RemoteFreshIntegratedError(
            "fresh local/remote node order authority drifted"
        )
    root = {
        "schema_version": P4_05_POLICY_SCHEMA_VERSION,
        "pilot_id": P4_05_PILOT_ID,
        "run_id": run_id,
        "case_id": case_id,
        "request_id": request_id,
        "parent_experiment_binding": parent_binding,
        "node_order": list(NODE_ORDER),
        "fresh_graph_contract": {
            "schema_version": _fresh.FRESH_INTEGRATED_POLICY_SCHEMA_VERSION,
            "node_order": list(_fresh.NODE_ORDER),
            "integrated_run_cap": 1,
            "per_node_integrated_generate_cap": 1,
            "retry_count_cap": 0,
            "b_aux_disposition": "absent/not_requested",
            "input_context": "model_native_full_context",
            "output_contract": "complete_single_json_context_remaining",
        },
        "b_aux_disposition": "absent/not_requested",
        "node_call_cap": {node_id: P4_05_GENERATE_CALL_CAP for node_id in NODE_ORDER},
        "retry_count": P4_05_RETRY_COUNT,
        "input_class_order": {
            node_id: list(_expected_input_classes(node_id))
            for node_id in NODE_ORDER
        },
        "prohibited_input_classes": list(P4_05_PROHIBITED_INPUT_CLASSES),
        "prompt_authority_identity": copy.deepcopy(PROMPT_AUTHORITY_IDENTITY),
        "pre_call_record_schema_version": P4_05_PRE_CALL_SCHEMA_VERSION,
        "attempt_record_schema_version": P4_05_ATTEMPT_SCHEMA_VERSION,
        "call_ledger_schema_version": P4_05_LEDGER_SCHEMA_VERSION,
        "profile_identity": _identity(
            profile.to_dict(),
            revision=P4_05_PROFILE_SCHEMA_VERSION,
        ),
        "result_root_marker": result_root_marker,
        "action_state": _action_state(model_action=False, remote_action=False),
        "claim_boundary": (
            "one fresh integrated non-H1 AutoDL pilot; raw, normalized, "
            "assembled, and downstream delivery statuses remain separate"
        ),
    }
    if prompt_revision is not None:
        root["prompt_revision"] = prompt_revision
        root["prompt_revision_nodes"] = list(prompt_nodes)
    if _uses_f4_direct_acceptance(prompt_revision):
        root["downstream_policy"] = PHASE4_FRESH_DELIVERY_POLICY_A07A_DIRECT_V1
        root["a07b_status"] = "not_executed_by_policy"
        root["f4_direct_acceptance_policy_receipt_schema_version"] = (
            P4_05_F4_DIRECT_ACCEPTANCE_POLICY_RECEIPT_SCHEMA_VERSION
        )
    root["policy_id"] = _identity(
        {key: value for key, value in root.items()},
        revision=P4_05_POLICY_SCHEMA_VERSION,
    )["sha256"]
    return root


def _emit_worker_event(event: str, *, node_id: str | None = None, delta: bytes = b"") -> None:
    payload = {
        "schema_version": P4_05_STREAM_SCHEMA_VERSION,
        "event": event,
        "node_id": node_id,
        "delta_b64": _b64(delta),
    }
    stream = getattr(sys.stderr, "buffer", None)
    raw = _canonical_bytes(payload) + b"\n"
    if stream is not None:
        stream.write(raw)
        stream.flush()
    else:
        sys.stderr.write(raw.decode("utf-8"))
        sys.stderr.flush()


class _FreshIntegratedBackend(_remote._RemoteTransformersBackend):
    """Reuse the validated BF16/no-quant loader with a fresh-node generator."""

    def generate_fresh_stream(
        self,
        *,
        node_id: str,
        input_bytes: bytes,
        prompt_bytes: bytes,
        config_bytes: bytes,
        request_bytes: bytes,
        emit_delta: Callable[[bytes], None],
    ) -> bytes:
        if self._model is None or self._processor is None:
            raise Phase4RemoteFreshIntegratedError("fresh backend is not loaded")
        config = _strict_json(config_bytes, "fresh runtime config")
        prompt_value = _strict_json(prompt_bytes, f"{node_id} prompt")
        request = _strict_json(request_bytes, f"{node_id} request")
        if not isinstance(config, Mapping):
            raise Phase4RemoteFreshIntegratedError("fresh runtime config is invalid")
        if not isinstance(prompt_value, Mapping) or not isinstance(request, Mapping):
            raise Phase4RemoteFreshIntegratedError(
                f"{node_id} prompt/request is invalid"
            )
        if (
            config.get("schema_version")
            != f"{P4_05_SCHEMA_PREFIX}.config.v1"
            or config.get("node_id") != node_id
            or config.get("profile_id") != self._profile.profile_id
            or config.get("dtype") != P4_05_DTYPE
            or config.get("quantization") != P4_05_QUANTIZATION
            or config.get("compute_dtype") != P4_05_COMPUTE_DTYPE
            or config.get("device_map") != P4_05_DEVICE_MAP
            or config.get("cpu_offload") is not False
            or config.get("model_context_tokens") != P4_05_MODEL_CONTEXT_TOKENS
            or config.get("fixed_max_new_tokens") is not None
            or config.get("stop_policy") != P4_05_STOP_POLICY
            or config.get("input_truncation") is not False
            or config.get("output_truncation") is not False
        ):
            raise Phase4RemoteFreshIntegratedError("fresh runtime config drifted")
        if (
            request.get("schema_version")
            != f"{P4_05_SCHEMA_PREFIX}.request.v1"
            or request.get("node_id") != node_id
            or request.get("generate_call_cap") != P4_05_GENERATE_CALL_CAP
            or request.get("retry_count") != P4_05_RETRY_COUNT
            or request.get("source_kind")
            != "remote_qwen_bf16_fresh_integrated"
        ):
            raise Phase4RemoteFreshIntegratedError(
                f"{node_id} request binding drifted"
            )
        request_prompt_revision = request.get("prompt_revision")
        if request_prompt_revision is not None and not isinstance(
            request_prompt_revision, str
        ):
            raise Phase4RemoteFreshIntegratedError(
                f"{node_id} request prompt revision is invalid"
            )
        _validate_prompt_revision(
            node_id=node_id,
            prompt_revision=request_prompt_revision,
        )
        try:
            input_value = _strict_json(input_bytes, f"{node_id} input")
            _validate_node_input_value(input_value, node_id)
            if (
                request.get("case_id") != input_value["case_id"]
                or request.get("request_id") != input_value["request_id"]
            ):
                raise Phase4RemoteFreshIntegratedError(
                    f"{node_id} request/input scope drifted"
                )
            if (
                prompt_value.get("schema_version")
                != P4_05_PROMPT_SCHEMA_VERSION
                or prompt_value.get("node_id") != node_id
                or prompt_value.get("prompt_revision")
                != request_prompt_revision
                or prompt_value.get("input_identity")
                != _identity(
                    input_bytes,
                    revision=P4_05_INPUT_SCHEMA_VERSION,
                    identity_kind="raw_bytes",
                )
            ):
                raise Phase4RemoteFreshIntegratedError(
                    f"{node_id} prompt input binding drifted"
                )
            model_text = (
                "P4_05_NODE_INPUT\n"
                + _canonical_bytes(input_value).decode("utf-8")
                + "\nP4_05_PROMPT_CONTRACT\n"
                + _canonical_bytes(prompt_value).decode("utf-8")
            )
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
            if input_length > P4_05_MODEL_CONTEXT_TOKENS:
                raise Phase4RemoteFreshIntegratedError(
                    f"{node_id} input exceeds native context; no truncation is allowed"
                )
            remaining = P4_05_MODEL_CONTEXT_TOKENS - input_length
            if remaining < 1:
                raise Phase4RemoteFreshIntegratedError(
                    f"{node_id} has no context remaining; no truncation is allowed"
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
            self._torch.manual_seed(self._profile.seed)
            self._torch.cuda.manual_seed_all(self._profile.seed)
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
                raise Phase4RemoteFreshIntegratedError(
                    f"{node_id} returned empty raw text"
                )
            return text.encode("utf-8")
        except Phase4RemoteFreshIntegratedError:
            raise
        except Exception as exc:
            raise Phase4RemoteFreshIntegratedError(
                f"{node_id} BF16 generation failed closed"
            ) from exc


def _run_worker_protocol(model_root: Path) -> int:
    _offline_process()
    worker_id = f"p4-05-worker-{uuid.uuid4().hex}"
    try:
        first = sys.stdin.buffer.readline()
        load = _strict_json(first.rstrip(b"\r\n"), "worker load")
        if not isinstance(load, Mapping) or set(load) != {
            "protocol",
            "kind",
            "profile_b64",
        } or load["protocol"] != P4_05_WORKER_PROTOCOL or load["kind"] != "load":
            raise Phase4RemoteFreshIntegratedError("worker load request drifted")
        profile = RemoteFreshIntegratedProfile.from_bytes(
            _decode_b64(load["profile_b64"], "worker profile")
        )
        _emit_worker_event("load_started")
        backend = _FreshIntegratedBackend(model_root=model_root, profile=profile)
        backend.load()
        loaded_facts = backend.loaded_facts
        if not isinstance(loaded_facts, Mapping):
            raise Phase4RemoteFreshIntegratedError("worker loaded facts unavailable")
        _emit_worker_event("load_completed")
        sys.stdout.buffer.write(
            _canonical_bytes(
                {
                    "protocol": P4_05_WORKER_PROTOCOL,
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
            line = sys.stdin.buffer.readline()
            if not line:
                return 3
            message = _strict_json(line.rstrip(b"\r\n"), "worker message")
            if not isinstance(message, Mapping):
                raise Phase4RemoteFreshIntegratedError("worker message is invalid")
            kind = message.get("kind")
            if message.get("protocol") != P4_05_WORKER_PROTOCOL:
                raise Phase4RemoteFreshIntegratedError("worker protocol drifted")
            if kind == "shutdown":
                sys.stdout.buffer.write(
                    _canonical_bytes(
                        {
                            "protocol": P4_05_WORKER_PROTOCOL,
                            "kind": "shutdown_ack",
                            "worker_id": worker_id,
                        }
                    )
                    + b"\n"
                )
                sys.stdout.buffer.flush()
                return 0
            if kind != "generate":
                raise Phase4RemoteFreshIntegratedError("worker kind is invalid")
            node_id = message.get("node_id")
            call_id = message.get("call_id")
            if node_id not in NODE_ORDER or not isinstance(call_id, str):
                raise Phase4RemoteFreshIntegratedError("worker call identity is invalid")
            _emit_worker_event("generation_started", node_id=node_id)
            raw = backend.generate_fresh_stream(
                node_id=node_id,
                input_bytes=_decode_b64(message["input_b64"], "worker input"),
                prompt_bytes=_decode_b64(message["prompt_b64"], "worker prompt"),
                config_bytes=_decode_b64(message["config_b64"], "worker config"),
                request_bytes=_decode_b64(message["request_b64"], "worker request"),
                emit_delta=lambda delta: _emit_worker_event(
                    "token_delta",
                    node_id=node_id,
                    delta=delta,
                ),
            )
            sys.stdout.buffer.write(
                _canonical_bytes(
                    {
                        "protocol": P4_05_WORKER_PROTOCOL,
                        "kind": "generation_result",
                        "call_id": call_id,
                        "worker_id": worker_id,
                        "node_id": node_id,
                        "raw_b64": _b64(raw),
                    }
                )
                + b"\n"
            )
            sys.stdout.buffer.flush()
            _emit_worker_event("generation_completed", node_id=node_id)
    except Exception as exc:
        _emit_worker_event("worker_failed")
        sys.stderr.write(f"[P4-05 worker] {type(exc).__name__}: {exc}\n")
        sys.stderr.flush()
        return 2


class FreshIntegratedStreamMirror:
    """Parent-side visible stream mirror with exact raw accumulation."""

    def __init__(self, target: object | None = None) -> None:
        self.target = target if target is not None else sys.stderr
        self.stderr_bytes = bytearray()
        self.token_bytes = bytearray()
        self.started_nodes: set[str] = set()
        self.completed_nodes: set[str] = set()
        self._lock = threading.Lock()

    def feed(self, raw: bytes) -> None:
        with self._lock:
            self.stderr_bytes.extend(raw)
        try:
            event = _strict_json(raw.rstrip(b"\r\n"), "worker stream event")
        except Phase4RemoteFreshIntegratedError:
            self.target.write(raw.decode("utf-8", errors="replace"))  # type: ignore[union-attr]
            self.target.flush()  # type: ignore[union-attr]
            return
        if not isinstance(event, Mapping) or event.get("schema_version") != P4_05_STREAM_SCHEMA_VERSION:
            self.target.write(raw.decode("utf-8", errors="replace"))  # type: ignore[union-attr]
            self.target.flush()  # type: ignore[union-attr]
            return
        event_name = event.get("event")
        delta = _decode_b64(event.get("delta_b64"), "stream delta")
        node_id = event.get("node_id")
        if node_id is not None and node_id not in NODE_ORDER:
            raise Phase4RemoteFreshIntegratedError(
                "worker stream node identity drifted"
            )
        if event_name == "generation_started":
            if not isinstance(node_id, str):
                raise Phase4RemoteFreshIntegratedError(
                    "generation start event has no node"
                )
            with self._lock:
                self.started_nodes.add(node_id)
        elif event_name == "generation_completed":
            if not isinstance(node_id, str):
                raise Phase4RemoteFreshIntegratedError(
                    "generation completion event has no node"
                )
            with self._lock:
                self.completed_nodes.add(node_id)
        if event_name == "token_delta":
            with self._lock:
                self.token_bytes.extend(delta)
            self.target.write(delta.decode("utf-8", errors="replace"))  # type: ignore[union-attr]
            self.target.flush()  # type: ignore[union-attr]
            return
        if delta:
            raise Phase4RemoteFreshIntegratedError("non-token stream event has delta")
        self.target.write(f"\n[P4-05] {event_name}\n")  # type: ignore[union-attr]
        self.target.flush()  # type: ignore[union-attr]

    def generation_started_for(self, node_id: str) -> bool:
        with self._lock:
            return node_id in self.started_nodes


class FreshIntegratedRemoteWorker:
    """Persistent parent-supervised worker with bounded teardown."""

    def __init__(
        self,
        *,
        model_root: Path,
        profile: RemoteFreshIntegratedProfile,
        mirror: FreshIntegratedStreamMirror,
    ) -> None:
        _safe_path(model_root, "model root", directory=True)
        profile.validate()
        self.mirror = mirror
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
                "req2web_runtime.phase4_remote_qwen_fresh_integrated",
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
            raise Phase4RemoteFreshIntegratedError(
                "worker IPC streams are unavailable"
            )
        self.worker_id: str | None = None
        self.worker_pid: int | None = None
        self.generation_started = False
        self.calls: dict[str, int] = {node_id: 0 for node_id in NODE_ORDER}
        self._closed = False
        self._messages: queue.Queue[dict[str, object]] = queue.Queue()
        self._stderr_capture = _local._WorkerStderrCapture()
        self._stdout_thread = threading.Thread(
            target=_local._worker_stdout_reader,
            args=(self.process.stdout, self._messages),
            name="p4-05-worker-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=_local._worker_stderr_reader,
            args=(self.process.stderr, self._stderr_capture, self.mirror.feed),
            name="p4-05-worker-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        self._send(
            {
                "protocol": P4_05_WORKER_PROTOCOL,
                "kind": "load",
                "profile_b64": _b64(profile.canonical_bytes()),
            }
        )
        loaded = self._receive(P4_05_LOAD_TIMEOUT_SECONDS)
        if (
            loaded.get("protocol") != P4_05_WORKER_PROTOCOL
            or loaded.get("kind") != "loaded"
            or loaded.get("worker_pid") != self.process.pid
            or not isinstance(loaded.get("loaded_facts"), Mapping)
        ):
            self._force_teardown("worker_failed")
            raise Phase4RemoteFreshIntegratedError("worker load did not complete")
        self.worker_id = loaded.get("worker_id")
        self.worker_pid = loaded.get("worker_pid")
        if not isinstance(self.worker_id, str) or not isinstance(self.worker_pid, int):
            self._force_teardown("worker_failed")
            raise Phase4RemoteFreshIntegratedError("worker identity is invalid")
        self.loaded_facts = copy.deepcopy(dict(loaded["loaded_facts"]))

    def _send(self, payload: Mapping[str, object]) -> None:
        if self._closed or self.process.stdin is None:
            raise Phase4RemoteFreshIntegratedError("worker stdin is unavailable")
        try:
            self.process.stdin.write(_canonical_bytes(payload).decode("utf-8") + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._force_teardown("worker_failed")
            raise Phase4RemoteFreshIntegratedError("worker IPC failed") from exc

    def _receive(self, timeout_seconds: int) -> Mapping[str, object]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._force_teardown("generation_timeout")
                raise Phase4RemoteFreshIntegratedError("worker deadline exceeded")
            try:
                message = self._messages.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue
            if message.get("kind") == "protocol_error":
                self._force_teardown("worker_failed")
                raise Phase4RemoteFreshIntegratedError("worker protocol failed")
            if not isinstance(message, Mapping):
                self._force_teardown("worker_failed")
                raise Phase4RemoteFreshIntegratedError("worker response is invalid")
            return message

    def _join_readers(self) -> tuple[bool, bool]:
        self._stdout_thread.join(timeout=2)
        self._stderr_thread.join(timeout=2)
        return (
            self._stdout_thread.is_alive() is False,
            self._stderr_thread.is_alive() is False,
        )

    def generate(
        self,
        *,
        node_id: str,
        input_bytes: bytes,
        prompt_bytes: bytes,
        config_bytes: bytes,
        request_bytes: bytes,
    ) -> bytes:
        if self._closed or self.process.poll() is not None:
            raise Phase4RemoteFreshIntegratedError("worker is unavailable")
        if node_id not in NODE_ORDER or self.calls[node_id] >= P4_05_GENERATE_CALL_CAP:
            raise Phase4RemoteFreshIntegratedError("node generate cap is exhausted")
        call_id = f"p4-05-call-{uuid.uuid4().hex}"
        self.calls[node_id] += 1
        self.generation_started = True
        self._send(
            {
                "protocol": P4_05_WORKER_PROTOCOL,
                "kind": "generate",
                "call_id": call_id,
                "node_id": node_id,
                "input_b64": _b64(input_bytes),
                "prompt_b64": _b64(prompt_bytes),
                "config_b64": _b64(config_bytes),
                "request_b64": _b64(request_bytes),
            }
        )
        message = self._receive(P4_05_TIMEOUT_SECONDS)
        if (
            message.get("kind") != "generation_result"
            or message.get("call_id") != call_id
            or message.get("worker_id") != self.worker_id
            or message.get("node_id") != node_id
        ):
            self._force_teardown("worker_failed")
            raise Phase4RemoteFreshIntegratedError("worker generation response drifted")
        return _decode_b64(message.get("raw_b64"), f"{node_id} raw")

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
        self.stdout_thread_joined, self.stderr_thread_joined = self._join_readers()
        self.terminal_status = (
            terminal_status if self.process.poll() is not None else "worker_teardown_unverified"
        )

    def close(self) -> dict[str, object]:
        if not self._closed:
            try:
                self._send(
                    {
                        "protocol": P4_05_WORKER_PROTOCOL,
                        "kind": "shutdown",
                    }
                )
                response = self._receive(20)
                if response.get("kind") != "shutdown_ack":
                    raise Phase4RemoteFreshIntegratedError(
                        "worker shutdown acknowledgement drifted"
                    )
                self.process.wait(timeout=20)
                self._closed = True
                self.terminal_status = (
                    "normal_completed"
                    if self.process.poll() is not None
                    else "worker_teardown_unverified"
                )
                self.stdout_thread_joined, self.stderr_thread_joined = (
                    self._join_readers()
                )
            except (OSError, subprocess.SubprocessError, Phase4RemoteFreshIntegratedError):
                self._force_teardown("worker_failed")
        elif not hasattr(self, "stdout_thread_joined"):
            self.stdout_thread_joined, self.stderr_thread_joined = self._join_readers()
        stderr_snapshot = self._stderr_capture.snapshot(
            stderr_thread=self._stderr_thread,
            worker_exit_verified=self.process.poll() is not None,
        )
        started_calls = {
            node_id: int(self.mirror.generation_started_for(node_id))
            for node_id in NODE_ORDER
        }
        return {
            "worker_id": self.worker_id,
            "worker_pid": self.worker_pid,
            "worker_exit_code": self.process.poll(),
            "worker_exit_verified": self.process.poll() is not None,
            "terminal_status": getattr(
                self, "terminal_status", "worker_teardown_unverified"
            ),
            "generation_started": self.generation_started,
            "generate_calls": started_calls,
            "attempt_envelopes": dict(self.calls),
            "stdout_thread_joined": getattr(self, "stdout_thread_joined", False),
            "stderr_thread_joined": getattr(self, "stderr_thread_joined", False),
            "stderr_capture_completed": stderr_snapshot["completed"],
        }


def _node_input(
    *,
    node_id: str,
    b_input: Mapping[str, object],
    state: Mapping[str, object],
    authority_projection: Mapping[str, object],
    provider_evidence_view: Mapping[str, object] | None = None,
) -> bytes:
    classes = list(_expected_input_classes(node_id))
    if provider_evidence_view is not None:
        classes.append(P4_05_PROVIDER_EVIDENCE_CLASS)
    payload: dict[str, object] = {
        "schema_version": P4_05_INPUT_SCHEMA_VERSION,
        "node_id": node_id,
        "case_id": b_input["case_id"],
        "request_id": b_input["request_id"],
        "logical_input_classes": classes,
        "projection": _provider_visible_projection(
            node_id=node_id,
            b_input=b_input,
            state=state,
            authority_projection=authority_projection,
            provider_evidence_view=provider_evidence_view,
        ),
    }
    _validate_node_input_value(payload, node_id)
    return _canonical_bytes(payload)


def _historical_phase4_node_prompt(
    *,
    node_id: str,
    input_bytes: bytes,
    prompt_revision: str | None = None,
) -> bytes:
    prompt_revision = _prompt_revision_for_node(
        node_id=node_id,
        prompt_revision=prompt_revision,
    )
    output_contracts: dict[str, dict[str, object]] = {
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
            "section_constants": {"entity_type": "section", "refs": []},
            "component_exact_keys": [
                "local_id",
                "entity_type",
                "component_type",
                "section_local_id",
                "label",
                "purpose",
                "refs",
            ],
            "component_constants": {"entity_type": "component", "refs": []},
            "invariants": [
                "sections and components are non-empty arrays",
                "all local_id values are non-empty and unique across sections and components",
                "sections contain component IDs only; components are separate top-level rows and are never nested inside sections",
                "components array order exactly equals the concatenation of sections[].component_local_ids",
                "each component.section_local_id names the section that lists that component local ID",
            ],
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
            "state_constants": {"entity_type": "state", "refs": []},
            "invariants": [
                "states is a non-empty array with unique non-empty local_id values",
                "visible_component_local_ids contains only F1 component local IDs supplied in this input",
                "visible_component_local_ids preserves the F1 component order",
            ],
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
            "interaction_constants": {"entity_type": "interaction", "refs": []},
            "invariants": [
                "interactions is a non-empty array with unique non-empty local_id values",
                "trigger_component_local_id names an F1 component local ID supplied in this input",
                "source_state_local_id and target_state_local_id name F2 state local IDs supplied in this input",
            ],
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
            "acceptance_check_constants": {
                "entity_type": "candidate_acceptance_check",
                "refs": [],
            },
            "reference_exact_keys": [
                "ref_type",
                "ref_id",
                "ref_revision",
            ],
            "field_sources": {
                "use_case_refs": {
                    "source_path": (
                        "projection.canonical_b_use_case_view."
                        "use_cases[].use_case_id"
                    ),
                    "required_ref_type": "canonical_b_use_case",
                    "required_ref_revision": "canonical_b.use_case.v1",
                    "forbidden_ref_type": "registry_stable",
                },
                "state_ref": {
                    "source_path": (
                        "projection.f2_registered_state_visibility_view."
                        "states[].stable_id"
                    ),
                    "required_ref_type": "registry_stable",
                    "required_ref_revision": REGISTRY_REVISION,
                    "forbidden_ref_type": "canonical_b_use_case",
                },
            },
            "invariants": [
                "acceptance_checks is a non-empty array with unique non-empty local_id values",
                "description has at least 8 characters",
                "use_case_refs uses only canonical_b_use_case IDs supplied in this input, revision canonical_b.use_case.v1, and preserves canonical use-case order",
                "the union of use_case_refs covers every canonical use-case ID supplied in this input",
                "state_ref uses ref_type registry_stable, a supplied F2 stable ID, and the supplied registry revision",
            ],
        },
    }
    payload = {
        "schema_version": P4_05_PROMPT_SCHEMA_VERSION,
        "node_id": node_id,
        "output_format": "one_complete_canonical_json_object",
        "input_identity": _identity(
            input_bytes,
            revision=P4_05_INPUT_SCHEMA_VERSION,
            identity_kind="raw_bytes",
        ),
        "instructions": [
            "Return exactly one JSON object and no prose.",
            "Use strict RFC 8259 JSON syntax: double-quoted keys and string values, a colon between every key and value, and no trailing commas.",
            "Every object must contain exactly the keys listed in exact_output_contract; do not add properties or alternate nesting.",
            "Every listed key is mandatory, including refs fields whose required value is the empty array [].",
            "For F4, use_case_refs and state_ref use different namespaces; never copy a state_ref or any registry_stable reference into use_case_refs.",
            "Preserve the required node schema and semantic array order.",
            "Do not emit authoritative IDs, mappings, acceptance verdicts, or browser evidence.",
            "Do not abbreviate, truncate, omit, or split the object.",
            "F4 must emit candidate acceptance semantics only.",
        ],
        "node_output_keys": {
            "F1": ["page_title", "layout_pattern", "sections", "components"],
            "F2": ["states"],
            "F3": ["interactions"],
            "F4": ["acceptance_checks"],
        }[node_id],
        "exact_output_contract": output_contracts[node_id],
    }
    if (
        prompt_revision == P4_05_FULL_DIRECT_PROMPT_REVISION
        and node_id == "F3"
    ):
        payload["required_interaction_plan"] = _f3_required_interaction_plan(
            input_bytes
        )
        payload["instructions"] = [
            *payload["instructions"],
            (
                "Copy every source state, target state, row position, and one "
                "allowed trigger component from required_interaction_plan. "
                "Emit exactly one interaction for every plan row in the same "
                "order; do not omit, merge, or add rows."
            ),
        ]
    if (
        prompt_revision == P4_05_FULL_DIRECT_PROMPT_REVISION
        and node_id == "F4"
    ):
        payload["required_acceptance_target_plan"] = (
            _f4_required_acceptance_target_plan(input_bytes)
        )
        payload["instructions"] = [
            *payload["instructions"],
            (
                "Emit exactly one acceptance check for every row in "
                "required_acceptance_target_plan and in the same order. Copy "
                "each use_case_ref and state_ref object exactly; do not select "
                "a different state, omit a row, or add a row."
            ),
        ]
    applied_prompt_revision = prompt_revision
    if prompt_revision == P4_05_FULL_DIRECT_PROMPT_REVISION:
        applied_prompt_revision = (
            P4_05_F3_F4_PROMPT_REVISION
            if node_id == "F3"
            else P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_REVISION
        )
    if applied_prompt_revision == P4_05_F3_F4_PROMPT_REVISION:
        if node_id == "F3":
            output_contracts["F3"]["invariants"] = [
                *output_contracts["F3"]["invariants"],
                (
                    "treat the first row in the supplied F2 states array as "
                    "the initial workflow state; do not require its name to "
                    "be the literal word initial"
                ),
                (
                    "follow canonical B use-case order and create deterministic "
                    "state progressions from the initial state"
                ),
                (
                    "for every supplied F2 state, include at least one same-state "
                    "interaction that performs in-state work so that state is a "
                    "non-empty acceptance target"
                ),
                (
                    "for every adjacent pair in supplied F2 state order, include "
                    "at least one forward transition from the earlier state to "
                    "the later state"
                ),
                (
                    "every interaction trigger component must be visible in its "
                    "source state's visible_component_local_ids"
                ),
                (
                    "same-state actions are allowed, but cannot replace the "
                    "required forward transitions toward later workflow states"
                ),
                (
                    "avoid backward transitions and avoid multiple equivalent "
                    "forward paths for the same workflow step"
                ),
                "do not add or fabricate use-case reference fields",
            ]
            if prompt_revision == P4_05_FULL_DIRECT_PROMPT_REVISION:
                output_contracts["F3"]["invariants"] = [
                    *output_contracts["F3"]["invariants"],
                    (
                        "let N be the number of supplied F2 states and emit "
                        "exactly 2*N-1 interactions"
                    ),
                    (
                        "use this exact interaction order: same-state work for "
                        "state 0, forward transition state 0 to state 1, "
                        "same-state work for state 1, then continue alternating "
                        "until same-state work for the final state"
                    ),
                    (
                        "every same-state interaction must use the same supplied "
                        "state local ID for source_state_local_id and "
                        "target_state_local_id"
                    ),
                    (
                        "every forward transition must connect one supplied "
                        "state directly to the next supplied state in array "
                        "order; never skip a state"
                    ),
                    (
                        "choose each trigger_component_local_id only from the "
                        "source state's visible_component_local_ids; never use "
                        "a component that is visible only in the target state"
                    ),
                    (
                        "the initial state's same-state interaction performs "
                        "the first canonical use case's in-state work, and each "
                        "later state's same-state interaction performs that "
                        "state's main work or validation"
                    ),
                ]
        else:
            output_contracts["F4"]["invariants"] = [
                *output_contracts["F4"]["invariants"],
                (
                    "emit exactly one acceptance check for each canonical use "
                    "case, in canonical use-case order, and put exactly that one "
                    "use-case reference in the check"
                ),
                (
                    "each state_ref must be uniquely reachable from the initial "
                    "state through the supplied F3 interaction graph"
                ),
                (
                    "assign use cases monotonically across the supplied F2 state "
                    "order: use the state at the same zero-based position when "
                    "available, otherwise reuse only the final supplied state"
                ),
                (
                    "state_ref must match the expected outcome state for its use "
                    "case; do not bind later use cases to the first state merely "
                    "for reachability"
                ),
                (
                    "never emit a second non-error acceptance target for the same "
                    "use case because it would make the target ambiguous"
                ),
            ]
        payload["prompt_revision"] = prompt_revision
        payload["instructions"] = [
            *payload["instructions"],
            (
                "This versioned F3/F4 revision strengthens reachability and "
                "acceptance-target uniqueness only; do not change the schema, "
                "registry, mapping, composition, assembler, or gates."
            ),
        ]
    elif applied_prompt_revision == P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_REVISION:
        output_contracts["F4"]["invariants"] = [
            *output_contracts["F4"]["invariants"],
            (
                "emit exactly one acceptance check for each canonical use "
                "case, in canonical use-case order, and put exactly that one "
                "use-case reference in the check"
            ),
            (
                "derive every state_ref from the actual supplied "
                "f3_registered_interaction_view; the selected stable state ID "
                "must appear as target_state_stable_id on at least one mapped "
                "interaction for that use case"
            ),
            (
                "do not select the first supplied F2 state merely because it "
                "is the initial state; it is eligible only when an actual "
                "supplied interaction targets that state"
            ),
            (
                "choose the earliest eligible target state in supplied F2 "
                "state order that matches the use-case outcome, and keep the "
                "selected state positions monotonically non-decreasing across "
                "canonical use-case order"
            ),
            (
                "when later use cases have no later eligible target, reuse "
                "only the final eligible target state; never invent an "
                "interaction, state, or registry identity"
            ),
            (
                "never emit a second acceptance target for the same use case"
            ),
        ]
        payload["prompt_revision"] = prompt_revision
        payload["instructions"] = [
            *payload["instructions"],
            (
                "This versioned F4 revision selects acceptance targets from "
                "the actual validated F3 graph for the P4-05 direct A-07a "
                "first-pass experiment. Do not change the schema, registry, "
                "mapping, composition, assembler, consistency, or acceptance "
                "authorities."
            ),
        ]
    return _canonical_bytes(payload)


def _node_prompt(
    *,
    node_id: str,
    input_bytes: bytes,
    prompt_revision: str | None = P4_05_FULL_DIRECT_PROMPT_REVISION,
) -> bytes:
    """Build an active node prompt through the project-wide shared authority."""

    if prompt_revision not in {None, P4_05_FULL_DIRECT_PROMPT_REVISION}:
        raise Phase4RemoteFreshIntegratedError(
            "historical Phase 4 prompt revisions are replay-only"
        )
    interaction_plan = (
        _f3_required_interaction_plan(input_bytes) if node_id == "F3" else None
    )
    acceptance_plan = (
        _f4_required_acceptance_target_plan(input_bytes)
        if node_id == "F4"
        else None
    )
    try:
        return build_canonical_f1_f4_prompt(
            node_id=node_id,
            input_bytes=input_bytes,
            required_interaction_plan=interaction_plan,
            required_acceptance_target_plan=acceptance_plan,
        )
    except ValueError as exc:
        raise Phase4RemoteFreshIntegratedError(
            "shared F1-F4 prompt authority rejected the Phase 4 projection"
        ) from exc


def _f4_direct_acceptance_policy_receipt(
    *,
    input_bytes: bytes,
    output: Mapping[str, object],
    raw_bytes: bytes,
) -> dict[str, object]:
    input_value = _strict_json(input_bytes, "F4 direct-acceptance input")
    input_envelope = _validate_node_input_value(input_value, "F4")
    projection = input_envelope["projection"]
    if not isinstance(projection, Mapping):
        raise Phase4RemoteFreshIntegratedError(
            "F4 direct-acceptance projection is invalid"
        )

    use_case_view = projection.get("canonical_b_use_case_view")
    state_view = projection.get("f2_registered_state_visibility_view")
    interaction_view = projection.get("f3_registered_interaction_view")
    mapping_view = projection.get("deterministic_use_case_mapping_view")
    if not all(
        isinstance(value, Mapping)
        for value in (use_case_view, state_view, interaction_view, mapping_view)
    ):
        raise Phase4RemoteFreshIntegratedError(
            "F4 direct-acceptance authority views are unavailable"
        )

    use_cases = use_case_view.get("use_cases")
    states = state_view.get("states")
    interactions = interaction_view.get("interactions")
    mappings = mapping_view.get("ordered_mappings")
    checks = output.get("acceptance_checks")
    if not all(
        isinstance(value, list)
        for value in (use_cases, states, interactions, mappings, checks)
    ):
        raise Phase4RemoteFreshIntegratedError(
            "F4 direct-acceptance authority arrays are invalid"
        )
    if not states:
        raise Phase4RemoteFreshIntegratedError(
            "F4 direct-acceptance state order is empty"
        )

    use_case_ids: list[str] = []
    for row in use_cases:
        if not isinstance(row, Mapping) or not isinstance(
            row.get("use_case_id"), str
        ):
            raise Phase4RemoteFreshIntegratedError(
                "F4 direct-acceptance use-case view is invalid"
            )
        use_case_ids.append(str(row["use_case_id"]))

    state_ids: list[str] = []
    for row in states:
        if not isinstance(row, Mapping) or not isinstance(
            row.get("stable_id"), str
        ):
            raise Phase4RemoteFreshIntegratedError(
                "F4 direct-acceptance state view is invalid"
            )
        state_ids.append(str(row["stable_id"]))
    if len(state_ids) != len(set(state_ids)):
        raise Phase4RemoteFreshIntegratedError(
            "F4 direct-acceptance state identity is duplicated"
        )
    state_position = {
        stable_id: index for index, stable_id in enumerate(state_ids)
    }

    interaction_rows: dict[str, tuple[str, str]] = {}
    adjacency: dict[str, list[str]] = {
        stable_id: [] for stable_id in state_ids
    }
    targeted_states: set[str] = set()
    for row in interactions:
        if not isinstance(row, Mapping):
            raise Phase4RemoteFreshIntegratedError(
                "F4 direct-acceptance interaction view is invalid"
            )
        stable_id = row.get("stable_id")
        source_id = row.get("source_state_stable_id")
        target_id = row.get("target_state_stable_id")
        if (
            not isinstance(stable_id, str)
            or not isinstance(source_id, str)
            or not isinstance(target_id, str)
            or stable_id in interaction_rows
            or source_id not in state_position
            or target_id not in state_position
        ):
            raise Phase4RemoteFreshIntegratedError(
                "F4 direct-acceptance interaction binding is invalid"
            )
        interaction_rows[stable_id] = (source_id, target_id)
        adjacency[source_id].append(target_id)
        targeted_states.add(target_id)

    reachable = {state_ids[0]}
    frontier = [state_ids[0]]
    while frontier:
        source_id = frontier.pop(0)
        for target_id in adjacency[source_id]:
            if target_id not in reachable:
                reachable.add(target_id)
                frontier.append(target_id)

    mapping_rows: dict[str, tuple[str, ...]] = {}
    for row in mappings:
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("use_case_id"), str)
            or not isinstance(row.get("interaction_stable_ids"), list)
        ):
            raise Phase4RemoteFreshIntegratedError(
                "F4 direct-acceptance mapping view is invalid"
            )
        use_case_id = str(row["use_case_id"])
        interaction_ids = tuple(row["interaction_stable_ids"])
        if (
            use_case_id in mapping_rows
            or any(
                not isinstance(interaction_id, str)
                or interaction_id not in interaction_rows
                for interaction_id in interaction_ids
            )
        ):
            raise Phase4RemoteFreshIntegratedError(
                "F4 direct-acceptance mapping binding is invalid"
            )
        mapping_rows[use_case_id] = interaction_ids
    if list(mapping_rows) != use_case_ids:
        raise Phase4RemoteFreshIntegratedError(
            "F4 direct-acceptance mapping order drifted"
        )
    if len(checks) != len(use_case_ids):
        raise Phase4RemoteFreshIntegratedError(
            "F4 direct-acceptance check count drifted"
        )

    selected_rows: list[dict[str, object]] = []
    previous_position = -1
    for use_case_id, check in zip(use_case_ids, checks, strict=True):
        if not isinstance(check, Mapping):
            raise Phase4RemoteFreshIntegratedError(
                "F4 direct-acceptance check is invalid"
            )
        use_case_refs = check.get("use_case_refs")
        state_ref = check.get("state_ref")
        if (
            not isinstance(use_case_refs, list)
            or len(use_case_refs) != 1
            or not isinstance(use_case_refs[0], Mapping)
            or use_case_refs[0].get("ref_type") != "canonical_b_use_case"
            or use_case_refs[0].get("ref_id") != use_case_id
            or use_case_refs[0].get("ref_revision")
            != "canonical_b.use_case.v1"
            or not isinstance(state_ref, Mapping)
            or state_ref.get("ref_type") != "registry_stable"
            or state_ref.get("ref_revision") != REGISTRY_REVISION
            or not isinstance(state_ref.get("ref_id"), str)
        ):
            raise Phase4RemoteFreshIntegratedError(
                "F4 direct-acceptance check binding is invalid"
            )
        selected_state_id = str(state_ref["ref_id"])
        eligible_state_ids = sorted(
            {
                interaction_rows[interaction_id][1]
                for interaction_id in mapping_rows[use_case_id]
                if interaction_rows[interaction_id][1] in reachable
                and interaction_rows[interaction_id][1] in targeted_states
            },
            key=state_position.__getitem__,
        )
        if selected_state_id not in eligible_state_ids:
            raise Phase4RemoteFreshIntegratedError(
                "F4 direct-acceptance target is not an actual reachable "
                "mapped interaction target"
            )
        selected_position = state_position[selected_state_id]
        if selected_position < previous_position:
            raise Phase4RemoteFreshIntegratedError(
                "F4 direct-acceptance target order is not monotonic"
            )
        previous_position = selected_position
        selected_rows.append(
            {
                "use_case_id": use_case_id,
                "selected_state_id": selected_state_id,
                "selected_state_position": selected_position,
                "eligible_state_ids": eligible_state_ids,
            }
        )

    root = {
        "schema_version": (
            P4_05_F4_DIRECT_ACCEPTANCE_POLICY_RECEIPT_SCHEMA_VERSION
        ),
        "policy": P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_REVISION,
        "downstream_policy": PHASE4_FRESH_DELIVERY_POLICY_A07A_DIRECT_V1,
        "a07b_status": "not_executed_by_policy",
        "input_identity": _identity(
            input_bytes,
            revision=P4_05_INPUT_SCHEMA_VERSION,
            identity_kind="raw_bytes",
        ),
        "raw_identity": _identity(
            raw_bytes,
            revision=f"{P4_05_SCHEMA_PREFIX}.raw.v1",
            identity_kind="raw_bytes",
        ),
        "validated_output_identity": _identity(
            output,
            revision=f"{P4_05_SCHEMA_PREFIX}.f4_validated_output.v1",
        ),
        "initial_state_id": state_ids[0],
        "reachable_state_ids": [
            stable_id for stable_id in state_ids if stable_id in reachable
        ],
        "actual_target_state_ids": [
            stable_id for stable_id in state_ids if stable_id in targeted_states
        ],
        "selected_targets": selected_rows,
        "automatic_rewrite": False,
        "automatic_retry": False,
    }
    return {
        **root,
        "receipt_id": _identity(
            root,
            revision=P4_05_F4_DIRECT_ACCEPTANCE_POLICY_RECEIPT_SCHEMA_VERSION,
        )["sha256"],
    }


def _node_config(
    *,
    node_id: str,
    profile: RemoteFreshIntegratedProfile,
) -> bytes:
    return _canonical_bytes(
        {
            "schema_version": f"{P4_05_SCHEMA_PREFIX}.config.v1",
            "node_id": node_id,
            "profile_id": profile.profile_id,
            "profile_name": profile.to_dict()["profile_name"],
            "dtype": P4_05_DTYPE,
            "quantization": P4_05_QUANTIZATION,
            "compute_dtype": P4_05_COMPUTE_DTYPE,
            "device_map": dict(P4_05_DEVICE_MAP),
            "cpu_offload": False,
            "local_files_only": True,
            "offline": True,
            "network": False,
            "telemetry": False,
            "tracing": False,
            "fixed_max_new_tokens": None,
            "model_context_tokens": P4_05_MODEL_CONTEXT_TOKENS,
            "input_truncation": False,
            "output_truncation": False,
            "stop_policy": copy.deepcopy(P4_05_STOP_POLICY),
            "retry_count": P4_05_RETRY_COUNT,
        }
    )


def _node_request(
    *,
    run_id: str,
    case_id: str,
    request_id: str,
    node_id: str,
    index: int,
    prompt_revision: str | None = None,
    parent_experiment_binding: Mapping[str, object] | None = None,
) -> bytes:
    prompt_revision = _prompt_revision_for_node(
        node_id=node_id,
        prompt_revision=prompt_revision,
    )
    parent_binding = _validate_parent_experiment_binding(
        parent_experiment_binding,
        case_id=case_id,
        request_id=request_id,
    )
    payload: dict[str, object] = {
        "schema_version": f"{P4_05_SCHEMA_PREFIX}.request.v1",
        "pilot_id": P4_05_PILOT_ID,
        "run_id": run_id,
        "case_id": case_id,
        "request_id": request_id,
        "node_id": node_id,
        "generate_call_index": index,
        "generate_call_cap": P4_05_GENERATE_CALL_CAP,
        "retry_count": P4_05_RETRY_COUNT,
        "source_kind": "remote_qwen_bf16_fresh_integrated",
        "parent_experiment_binding": parent_binding,
    }
    if prompt_revision is not None:
        payload["prompt_revision"] = prompt_revision
    return _canonical_bytes(payload)


def _pre_call_record(
    *,
    profile: RemoteFreshIntegratedProfile,
    run_id: str,
    case_id: str,
    request_id: str,
    node_id: str,
    index: int,
    input_bytes: bytes,
    prompt_bytes: bytes,
    config_bytes: bytes,
    request_bytes: bytes,
    worker_id: str | None,
    worker_pid: int | None,
    prompt_revision: str | None = None,
    parent_experiment_binding: Mapping[str, object] | None = None,
) -> dict[str, object]:
    prompt_revision = _prompt_revision_for_node(
        node_id=node_id,
        prompt_revision=prompt_revision,
    )
    parent_binding = _validate_parent_experiment_binding(
        parent_experiment_binding,
        case_id=case_id,
        request_id=request_id,
    )
    record: dict[str, object] = {
        "schema_version": P4_05_PRE_CALL_SCHEMA_VERSION,
        "record_id": "pending",
        "pilot_id": P4_05_PILOT_ID,
        "run_id": run_id,
        "case_id": case_id,
        "request_id": request_id,
        "parent_experiment_binding": parent_binding,
        "node_id": node_id,
        "attempt_index": 1,
        "generate_call_index": index,
        "generate_call_cap": P4_05_GENERATE_CALL_CAP,
        "retry_count": P4_05_RETRY_COUNT,
        "profile_identity": _identity(
            profile.to_dict(),
            revision=P4_05_PROFILE_SCHEMA_VERSION,
        ),
        "worker_identity": {
            "worker_id": worker_id,
            "worker_pid": worker_pid,
        },
        "input_identity": _identity(
            input_bytes,
            revision=P4_05_INPUT_SCHEMA_VERSION,
            identity_kind="raw_bytes",
        ),
        "prompt_identity": _identity(
            prompt_bytes,
            revision=P4_05_PROMPT_SCHEMA_VERSION,
            identity_kind="raw_bytes",
        ),
        "config_identity": _identity(
            config_bytes,
            revision=f"{P4_05_SCHEMA_PREFIX}.config.v1",
            identity_kind="raw_bytes",
        ),
        "request_identity": _identity(
            request_bytes,
            revision=f"{P4_05_SCHEMA_PREFIX}.request.v1",
            identity_kind="raw_bytes",
        ),
        "raw_capture_state": "not_created",
        "pre_call_fsync_required": True,
        "action_state": _action_state(model_action=True, remote_action=True),
    }
    if prompt_revision is not None:
        record["prompt_revision"] = prompt_revision
    record["record_id"] = _identity(
        {
            key: value
            for key, value in record.items()
            if key != "record_id"
        },
        revision=P4_05_PRE_CALL_SCHEMA_VERSION,
    )["sha256"]
    return record


def _attempt_record(
    *,
    run_id: str,
    case_id: str,
    request_id: str,
    node_id: str,
    input_bytes: bytes,
    prompt_bytes: bytes,
    config_bytes: bytes,
    request_bytes: bytes,
    pre_call_record: Mapping[str, object],
    worker_id: str | None,
    worker_pid: int | None,
    raw: bytes | None,
    generate_started: bool,
    status: str,
    failure_code: str | None,
    prompt_revision: str | None = None,
    parent_experiment_binding: Mapping[str, object] | None = None,
) -> dict[str, object]:
    prompt_revision = _prompt_revision_for_node(
        node_id=node_id,
        prompt_revision=prompt_revision,
    )
    parent_binding = _validate_parent_experiment_binding(
        parent_experiment_binding,
        case_id=case_id,
        request_id=request_id,
    )
    record = {
        "schema_version": P4_05_ATTEMPT_SCHEMA_VERSION,
        "pilot_id": P4_05_PILOT_ID,
        "run_id": run_id,
        "case_id": case_id,
        "request_id": request_id,
        "parent_experiment_binding": parent_binding,
        "node_id": node_id,
        "attempt_index": 1,
        "call_kind": "integrated",
        "call_count": int(generate_started),
        "retry_count": P4_05_RETRY_COUNT,
        "worker_identity": {
            "worker_id": worker_id,
            "worker_pid": worker_pid,
        },
        "pre_call_identity": _identity(
            pre_call_record,
            revision=P4_05_PRE_CALL_SCHEMA_VERSION,
        ),
        "input_identity": _identity(
            input_bytes,
            revision=P4_05_INPUT_SCHEMA_VERSION,
            identity_kind="raw_bytes",
        ),
        "prompt_identity": _identity(
            prompt_bytes,
            revision=P4_05_PROMPT_SCHEMA_VERSION,
            identity_kind="raw_bytes",
        ),
        "config_identity": _identity(
            config_bytes,
            revision=f"{P4_05_SCHEMA_PREFIX}.config.v1",
            identity_kind="raw_bytes",
        ),
        "request_identity": _identity(
            request_bytes,
            revision=f"{P4_05_SCHEMA_PREFIX}.request.v1",
            identity_kind="raw_bytes",
        ),
        "raw_identity": (
            None
            if raw is None
            else _identity(
                raw,
                revision=f"{P4_05_SCHEMA_PREFIX}.raw.v1",
                identity_kind="raw_bytes",
            )
        ),
        "raw_first": True,
        "generate_started": generate_started,
        "status": status,
        "failure_code": failure_code,
        "automatic_retry": False,
    }
    if prompt_revision is not None:
        record["prompt_revision"] = prompt_revision
    return record


def _call_ledger(
    *,
    run_id: str,
    node_results: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    per_node = {
        node_id: {
            "attempt_count": int(node_id in node_results),
            "generate_started_count": (
                int(node_results[node_id].get("call_count", 0))
                if node_id in node_results
                else 0
            ),
            "generate_call_cap": P4_05_GENERATE_CALL_CAP,
            "retry_count": P4_05_RETRY_COUNT,
        }
        for node_id in NODE_ORDER
    }
    return {
        "schema_version": P4_05_LEDGER_SCHEMA_VERSION,
        "pilot_id": P4_05_PILOT_ID,
        "run_id": run_id,
        "node_order": list(NODE_ORDER),
        "per_node": per_node,
        "total_generate_calls": sum(
            int(row["generate_started_count"]) for row in per_node.values()
        ),
        "automatic_retry": False,
        "budget_reset": False,
    }


def _read_canonical_artifact(path: Path, name: str) -> object:
    if path.is_symlink() or not path.is_file():
        raise Phase4RemoteFreshIntegratedError(f"{name} is unavailable")
    return _strict_json(path.read_bytes(), name)


def _resume_prefix_nodes(resume_prefix: str | None) -> tuple[str, ...] | None:
    if resume_prefix is None:
        return None
    if resume_prefix == P4_05_RESUME_PREFIX_F1_F2:
        return ("F1", "F2")
    if resume_prefix == P4_05_RESUME_PREFIX_F1_F3:
        return ("F1", "F2", "F3")
    raise Phase4RemoteFreshIntegratedError(
        "unsupported explicit resume prefix"
    )


def _build_call_history(
    *,
    history_result_roots: tuple[Path, ...],
    result_root: Path,
) -> dict[str, object]:
    seen_roots: set[str] = set()
    seen_runs: set[tuple[str, str]] = set()
    rows: list[dict[str, object]] = []
    aggregate_per_node = {node_id: 0 for node_id in NODE_ORDER}
    for supplied_root in history_result_roots:
        history_root = _safe_path(
            supplied_root,
            "history result root",
            directory=True,
        )
        if history_root == result_root:
            raise Phase4RemoteFreshIntegratedError(
                "history result root must differ from the new result root"
            )
        root_text = str(history_root)
        if root_text in seen_roots:
            raise Phase4RemoteFreshIntegratedError(
                "history result roots must be unique"
            )
        seen_roots.add(root_text)
        ledger = _read_canonical_artifact(
            history_root / "model_call_ledger.json",
            "history model-call ledger",
        )
        if not isinstance(ledger, Mapping) or set(ledger) != {
            "schema_version",
            "pilot_id",
            "run_id",
            "node_order",
            "per_node",
            "total_generate_calls",
            "automatic_retry",
            "budget_reset",
        }:
            raise Phase4RemoteFreshIntegratedError(
                "history model-call ledger shape drifted"
            )
        if (
            ledger["schema_version"] != P4_05_LEDGER_SCHEMA_VERSION
            or ledger["node_order"] != list(NODE_ORDER)
            or ledger["automatic_retry"] is not False
            or ledger["budget_reset"] is not False
            or not isinstance(ledger["pilot_id"], str)
            or not isinstance(ledger["run_id"], str)
            or not isinstance(ledger["per_node"], Mapping)
        ):
            raise Phase4RemoteFreshIntegratedError(
                "history model-call ledger binding drifted"
            )
        run_key = (str(ledger["pilot_id"]), str(ledger["run_id"]))
        if run_key in seen_runs:
            raise Phase4RemoteFreshIntegratedError(
                "history pilot/run identity is duplicated"
            )
        seen_runs.add(run_key)
        row_counts: dict[str, int] = {}
        for node_id in NODE_ORDER:
            node_row = ledger["per_node"].get(node_id)
            if not isinstance(node_row, Mapping) or set(node_row) != {
                "attempt_count",
                "generate_started_count",
                "generate_call_cap",
                "retry_count",
            }:
                raise Phase4RemoteFreshIntegratedError(
                    "history per-node ledger shape drifted"
                )
            count = node_row["generate_started_count"]
            if type(count) is not int or count not in {0, 1}:
                raise Phase4RemoteFreshIntegratedError(
                    "history per-node generate count is invalid"
                )
            if (
                node_row["attempt_count"] not in {0, 1}
                or node_row["generate_call_cap"] != P4_05_GENERATE_CALL_CAP
                or node_row["retry_count"] != P4_05_RETRY_COUNT
            ):
                raise Phase4RemoteFreshIntegratedError(
                    "history per-node call policy drifted"
                )
            row_counts[node_id] = count
            aggregate_per_node[node_id] += count
        if ledger["total_generate_calls"] != sum(row_counts.values()):
            raise Phase4RemoteFreshIntegratedError(
                "history total model-call count drifted"
            )
        rows.append(
            {
                "result_root": root_text,
                "pilot_id": ledger["pilot_id"],
                "run_id": ledger["run_id"],
                "model_call_ledger_identity": _identity(
                    ledger,
                    revision=P4_05_LEDGER_SCHEMA_VERSION,
                ),
                "per_node_generate_started_count": row_counts,
                "total_generate_calls": ledger["total_generate_calls"],
            }
        )
    if any(
        count > P4_05_TOTAL_REAL_MODEL_CALL_CAP_PER_NODE
        for count in aggregate_per_node.values()
    ):
        raise Phase4RemoteFreshIntegratedError(
            "historical per-node real-model call cap is exhausted"
        )
    body: dict[str, object] = {
        "schema_version": P4_05_HISTORY_SCHEMA_VERSION,
        "pilot_id": P4_05_PILOT_ID,
        "history_rows": rows,
        "aggregate_per_node_generate_started_count": aggregate_per_node,
        "aggregate_total_generate_calls": sum(aggregate_per_node.values()),
        "per_node_total_call_cap": P4_05_TOTAL_REAL_MODEL_CALL_CAP_PER_NODE,
        "budget_reset": False,
        "automatic_retry": False,
        "action_state": _action_state(
            model_action=False,
            remote_action=False,
        ),
    }
    body["history_receipt_id"] = _identity(
        body,
        revision=P4_05_HISTORY_SCHEMA_VERSION,
    )["sha256"]
    return body


def _restore_validated_prefix(
    *,
    resume_from_result_root: Path,
    result_root: Path,
    b_input: Mapping[str, object],
    state: Mapping[str, object],
    history_receipt: Mapping[str, object],
    resume_prefix: str | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    selected_prefix_nodes = _resume_prefix_nodes(resume_prefix)
    source_root = _safe_path(
        resume_from_result_root,
        "resume source result root",
        directory=True,
    )
    history_rows = history_receipt.get("history_rows")
    if not isinstance(history_rows, list):
        raise Phase4RemoteFreshIntegratedError(
            "history receipt rows are unavailable"
        )
    history_root_counts = {
        str(row["result_root"]): sum(
            1
            for candidate in history_rows
            if isinstance(candidate, Mapping)
            and candidate.get("result_root") == row.get("result_root")
        )
        for row in history_rows
        if isinstance(row, Mapping)
        and isinstance(row.get("result_root"), str)
    }
    source_chain_reversed: list[Path] = []
    seen_source_roots: set[str] = set()
    cursor = source_root
    while True:
        cursor_text = str(cursor)
        if cursor_text in seen_source_roots:
            raise Phase4RemoteFreshIntegratedError(
                "resume source ancestry contains a cycle"
            )
        if history_root_counts.get(cursor_text) != 1:
            raise Phase4RemoteFreshIntegratedError(
                "every resume source must be included exactly once in call history"
            )
        seen_source_roots.add(cursor_text)
        source_chain_reversed.append(cursor)
        source_b_input = _read_canonical_artifact(
            cursor / "b_input.json",
            "resume source B input",
        )
        if source_b_input != dict(b_input):
            raise Phase4RemoteFreshIntegratedError(
                "resume source B input binding drifted"
            )
        source_resume_path = cursor / "resume_receipt.json"
        if not source_resume_path.exists():
            break
        source_resume = _read_canonical_artifact(
            source_resume_path,
            "source resume receipt",
        )
        if (
            not isinstance(source_resume, Mapping)
            or source_resume.get("schema_version")
            not in {
                f"{P4_05_SCHEMA_PREFIX}.resume.v1",
                f"{P4_05_SCHEMA_PREFIX}.resume.v2",
                P4_05_RESUME_SCHEMA_VERSION,
            }
            or not isinstance(source_resume.get("source_result_root"), str)
            or source_resume.get("model_generate_calls") != 0
            or source_resume.get("automatic_retry") is not False
            or source_resume.get("budget_reset") is not False
        ):
            raise Phase4RemoteFreshIntegratedError(
                "source resume receipt binding drifted"
            )
        cursor = _safe_path(
            Path(str(source_resume["source_result_root"])),
            "ancestor resume source result root",
            directory=True,
        )
    source_chain = list(reversed(source_chain_reversed))
    primary_source_ledger = _read_canonical_artifact(
        source_root / "model_call_ledger.json",
        "resume source model-call ledger",
    )
    if not isinstance(primary_source_ledger, Mapping):
        raise Phase4RemoteFreshIntegratedError(
            "resume source model-call ledger is invalid"
        )
    restored_state = copy.deepcopy(dict(state))
    resumed_nodes: list[str] = []
    node_bindings: list[dict[str, object]] = []
    encountered_gap = False
    nodes_to_restore = (
        tuple(NODE_ORDER)
        if selected_prefix_nodes is None
        else selected_prefix_nodes
    )
    for node_id in nodes_to_restore:
        candidate_roots = [
            candidate_root
            for candidate_root in source_chain
            if (
                candidate_root
                / "attempts"
                / node_id
                / "validated_node_output.json"
            ).is_file()
        ]
        if not candidate_roots:
            encountered_gap = True
            continue
        if encountered_gap:
            raise Phase4RemoteFreshIntegratedError(
                "resume validated nodes are not a contiguous prefix"
            )
        node_source_root = candidate_roots[-1]
        source_ledger = _read_canonical_artifact(
            node_source_root / "model_call_ledger.json",
            f"resume {node_id} source model-call ledger",
        )
        if not isinstance(source_ledger, Mapping):
            raise Phase4RemoteFreshIntegratedError(
                f"resume {node_id} source ledger is invalid"
            )
        attempt_root = node_source_root / "attempts" / node_id
        output_path = attempt_root / "validated_node_output.json"
        validated_output = _read_canonical_artifact(
            output_path,
            f"resume {node_id} validated output",
        )
        attempt = _read_canonical_artifact(
            attempt_root / "attempt_result.json",
            f"resume {node_id} attempt result",
        )
        raw_path = attempt_root / "raw_response.bin"
        raw_bytes = (
            b""
            if raw_path.is_symlink() or not raw_path.is_file()
            else raw_path.read_bytes()
        )
        output = (
            None
            if not raw_bytes
            else _parse_model_json(
                raw_bytes,
                f"resume {node_id} raw response",
            )
        )
        if (
            not isinstance(validated_output, Mapping)
            or not isinstance(output, Mapping)
            or _canonical_bytes(output) != _canonical_bytes(validated_output)
            or not isinstance(attempt, Mapping)
            or attempt.get("node_id") != node_id
            or attempt.get("pilot_id") != source_ledger.get("pilot_id")
            or attempt.get("run_id") != source_ledger.get("run_id")
            or attempt.get("case_id") != b_input["case_id"]
            or attempt.get("request_id") != b_input["request_id"]
            or attempt.get("status") != "validated"
            or attempt.get("generate_started") is not True
            or attempt.get("call_count") != 1
            or attempt.get("retry_count") != P4_05_RETRY_COUNT
            or attempt.get("raw_identity")
            != _identity(
                raw_bytes,
                revision=f"{P4_05_SCHEMA_PREFIX}.raw.v1",
                identity_kind="raw_bytes",
            )
        ):
            raise Phase4RemoteFreshIntegratedError(
                f"resume {node_id} source binding drifted"
            )
        phase4_validate_node_output(node_id, output, restored_state)
        restored_state = phase4_register_node_output(
            restored_state,
            node_id,
            output,
        )
        resumed_nodes.append(node_id)
        node_bindings.append(
            {
                "node_id": node_id,
                "source_result_root": str(node_source_root),
                "source_attempt_identity": _identity(
                    attempt,
                    revision=P4_05_ATTEMPT_SCHEMA_VERSION,
                ),
                "validated_output_identity": _identity(
                    validated_output,
                    revision=f"{node_id}.output.p4.v1",
                ),
                "raw_identity": attempt["raw_identity"],
            }
        )
    if not resumed_nodes or len(resumed_nodes) == len(NODE_ORDER):
        raise Phase4RemoteFreshIntegratedError(
            "resume source must contain a non-empty proper validated prefix"
        )
    if selected_prefix_nodes is not None and resumed_nodes != list(
        selected_prefix_nodes
    ):
        raise Phase4RemoteFreshIntegratedError(
            "resume source does not contain the requested validated prefix"
        )
    selected_prefix = (
        resume_prefix
        if resume_prefix is not None
        else "-".join(resumed_nodes)
    )
    body: dict[str, object] = {
        "schema_version": P4_05_RESUME_SCHEMA_VERSION,
        "pilot_id": P4_05_PILOT_ID,
        "source_result_root": str(source_root),
        "source_chain_result_roots": [
            str(item) for item in source_chain
        ],
        "source_pilot_id": primary_source_ledger["pilot_id"],
        "source_run_id": primary_source_ledger["run_id"],
        "source_model_call_ledger_identity": _identity(
            primary_source_ledger,
            revision=P4_05_LEDGER_SCHEMA_VERSION,
        ),
        "selected_prefix": selected_prefix,
        "resumed_nodes": resumed_nodes,
        "next_node": NODE_ORDER[len(resumed_nodes)],
        "ignored_source_nodes": [
            node_id for node_id in NODE_ORDER if node_id not in resumed_nodes
        ],
        "node_bindings": node_bindings,
        "model_generate_calls": 0,
        "automatic_retry": False,
        "budget_reset": False,
        "action_state": _action_state(
            model_action=False,
            remote_action=False,
        ),
    }
    body["resume_receipt_id"] = _identity(
        body,
        revision=P4_05_RESUME_SCHEMA_VERSION,
    )["sha256"]
    _write_fsync(
        result_root / "resume_receipt.json",
        _canonical_bytes(body),
    )
    return restored_state, body


def _aggregate_call_ledger(
    *,
    run_id: str,
    history_receipt: Mapping[str, object],
    current_ledger: Mapping[str, object],
) -> dict[str, object]:
    historical = history_receipt[
        "aggregate_per_node_generate_started_count"
    ]
    current = current_ledger["per_node"]
    if not isinstance(historical, Mapping) or not isinstance(current, Mapping):
        raise Phase4RemoteFreshIntegratedError(
            "aggregate model-call inputs are invalid"
        )
    per_node: dict[str, dict[str, object]] = {}
    for node_id in NODE_ORDER:
        prior_count = historical.get(node_id)
        current_row = current.get(node_id)
        if type(prior_count) is not int or not isinstance(current_row, Mapping):
            raise Phase4RemoteFreshIntegratedError(
                "aggregate per-node model-call input drifted"
            )
        current_count = current_row.get("generate_started_count")
        if type(current_count) is not int:
            raise Phase4RemoteFreshIntegratedError(
                "aggregate current model-call count drifted"
            )
        total_count = prior_count + current_count
        if total_count > P4_05_TOTAL_REAL_MODEL_CALL_CAP_PER_NODE:
            raise Phase4RemoteFreshIntegratedError(
                f"{node_id} aggregate real-model call cap exceeded"
            )
        per_node[node_id] = {
            "historical_generate_started_count": prior_count,
            "current_generate_started_count": current_count,
            "aggregate_generate_started_count": total_count,
            "total_real_model_call_cap": (
                P4_05_TOTAL_REAL_MODEL_CALL_CAP_PER_NODE
            ),
        }
    return {
        "schema_version": P4_05_AGGREGATE_LEDGER_SCHEMA_VERSION,
        "pilot_id": P4_05_PILOT_ID,
        "run_id": run_id,
        "history_receipt_identity": _identity(
            history_receipt,
            revision=P4_05_HISTORY_SCHEMA_VERSION,
        ),
        "current_ledger_identity": _identity(
            current_ledger,
            revision=P4_05_LEDGER_SCHEMA_VERSION,
        ),
        "per_node": per_node,
        "aggregate_total_generate_calls": sum(
            int(row["aggregate_generate_started_count"])
            for row in per_node.values()
        ),
        "automatic_retry": False,
        "budget_reset": False,
    }


def prepare_phase4_remote_qwen_fresh_integrated(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    run_id: str | None = None,
    b_input: Mapping[str, object] | None = None,
    prompt_revision: str | None = P4_05_FULL_DIRECT_PROMPT_REVISION,
    parent_experiment_binding: Mapping[str, object] | None = None,
    upstream_context: AgentContextBundle | None = None,
    upstream_guidance: RetrievalGuidance | None = None,
    require_actual_upstream: bool = False,
) -> dict[str, object]:
    """Create the no-generation P4-05 preflight and profile artifacts."""

    if type(require_actual_upstream) is not bool:
        raise Phase4RemoteFreshIntegratedError(
            "require_actual_upstream must be boolean"
        )
    if require_actual_upstream and (
        b_input is None
        or not isinstance(upstream_context, AgentContextBundle)
        or not isinstance(upstream_guidance, RetrievalGuidance)
    ):
        raise Phase4RemoteFreshIntegratedError(
            "active preparation requires canonical B and actual upstream context"
        )
    _offline_process()
    model_root = _safe_path(model_root, "model root", directory=True)
    integrity_evidence = _safe_path(
        integrity_evidence,
        "integrity evidence",
        directory=False,
    )
    result_root = _safe_path(result_root, "result root")
    if result_root.exists():
        if not result_root.is_dir() or any(result_root.iterdir()):
            raise Phase4RemoteFreshIntegratedError(
                "result root must be new and empty"
            )
    result_root.mkdir(parents=True, exist_ok=False)
    marker = P4_05_ROOT_MARKER
    _write_fsync(result_root / P4_05_ROOT_MARKER, marker.encode("ascii"))
    selected_b_input = (
        synthetic_commerce_b_input(
            case_id=P4_05_CASE_ID,
            request_id=P4_05_REQUEST_ID,
        )
        if b_input is None
        else copy.deepcopy(validate_b_input(copy.deepcopy(dict(b_input))))
    )
    state = phase4_create_portable_authority_state(selected_b_input)
    graph_bound_delivery = _prepare_graph_bound_delivery_materials(
        result_root=result_root,
        graph_state=state,
        upstream_context=upstream_context,
        upstream_guidance=upstream_guidance,
    )
    upstream_binding_mode = (
        "actual_agent_context_required"
        if require_actual_upstream
        else (
            "actual_agent_context_supplied"
            if upstream_context is not None
            else "historical_synthetic_binding"
        )
    )
    inventory = _remote.validate_remote_model_inventory(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
    )
    runtime_facts = _remote._collect_remote_runtime_facts()
    gpu_facts = _remote._probe_remote_gpu_facts()
    if gpu_facts["device_name"] != _remote.REMOTE_DEVICE_NAME:
        raise Phase4RemoteFreshIntegratedError("P4-05 requires RTX 5090 GPU0")
    if int(gpu_facts["total_vram_bytes"]) < _remote.REMOTE_MIN_VRAM_BYTES:
        raise Phase4RemoteFreshIntegratedError("P4-05 GPU VRAM is below the RTX 5090 floor")
    profile = RemoteFreshIntegratedProfile.create(
        inventory=inventory,
        runtime_facts=runtime_facts,
        gpu_facts=gpu_facts,
    )
    selected_run_id = run_id or f"{P4_05_RUN_PREFIX}{uuid.uuid4().hex[:16]}"
    prompt_nodes = _prompt_nodes_for_revision(prompt_revision)
    parent_binding = _validate_parent_experiment_binding(
        parent_experiment_binding,
        case_id=str(selected_b_input["case_id"]),
        request_id=str(selected_b_input["request_id"]),
    )
    policy = create_p4_05_policy(
        run_id=selected_run_id,
        result_root_marker=marker,
        profile=profile,
        case_id=str(selected_b_input["case_id"]),
        request_id=str(selected_b_input["request_id"]),
        prompt_revision=prompt_revision,
        parent_experiment_binding=parent_binding,
    )
    preflight = {
        "schema_version": f"{P4_05_SCHEMA_PREFIX}.preflight.v1",
        "pilot_id": P4_05_PILOT_ID,
        "run_id": selected_run_id,
        "model_root": str(model_root),
        "integrity_evidence": str(integrity_evidence),
        "model_inventory_identity": inventory["inventory_identity"],
        "profile_identity": _identity(
            profile.to_dict(),
            revision=P4_05_PROFILE_SCHEMA_VERSION,
        ),
        "policy_identity": _identity(
            policy,
            revision=P4_05_POLICY_SCHEMA_VERSION,
        ),
        "case_id": selected_b_input["case_id"],
        "request_id": selected_b_input["request_id"],
        "parent_experiment_binding": parent_binding,
        "node_order": list(NODE_ORDER),
        "prompt_authority_identity": copy.deepcopy(PROMPT_AUTHORITY_IDENTITY),
        "graph_bound_delivery_materials_binding": graph_bound_delivery[
            "binding"
        ],
        "upstream_binding_mode": upstream_binding_mode,
        "action_state": _action_state(model_action=False, remote_action=False),
        "model_loaded": False,
        "run_occurred": False,
        "downstream": "not_executed",
    }
    if prompt_revision is not None:
        preflight["prompt_revision"] = prompt_revision
        preflight["prompt_revision_nodes"] = list(prompt_nodes)
    if _uses_f4_direct_acceptance(prompt_revision):
        preflight["downstream_policy"] = (
            PHASE4_FRESH_DELIVERY_POLICY_A07A_DIRECT_V1
        )
        preflight["a07b_status"] = "not_executed_by_policy"
    _write_fsync(result_root / "model_inventory.json", _canonical_bytes(inventory))
    _write_fsync(result_root / "remote_profile.json", profile.canonical_bytes())
    _write_fsync(result_root / "p4_05_policy.json", _canonical_bytes(policy))
    _write_fsync(result_root / "preflight_manifest.json", _canonical_bytes(preflight))
    _write_fsync(
        result_root / "b_input.json",
        _canonical_bytes(selected_b_input),
    )
    return {
        "result_root": result_root,
        "run_id": selected_run_id,
        "marker": marker,
        "inventory": inventory,
        "profile": profile,
        "policy": policy,
        "preflight": preflight,
        "b_input": selected_b_input,
        "parent_experiment_binding": parent_binding,
        "state": state,
        "graph_bound_delivery": graph_bound_delivery,
        "upstream_binding_mode": upstream_binding_mode,
    }


def run_phase4_remote_qwen_fresh_integrated(
    *,
    model_root: Path,
    integrity_evidence: Path,
    result_root: Path,
    confirm_one_remote_fresh_integrated_run: bool,
    run_id: str | None = None,
    console: object | None = None,
    history_result_roots: tuple[Path, ...] = (),
    resume_from_result_root: Path | None = None,
    resume_prefix: str | None = None,
    b_input: Mapping[str, object] | None = None,
    prompt_revision: str | None = P4_05_FULL_DIRECT_PROMPT_REVISION,
    expected_profile_identity: Mapping[str, object] | None = None,
    expected_model_inventory_identity: Mapping[str, object] | None = None,
    parent_experiment_binding: Mapping[str, object] | None = None,
    upstream_context: AgentContextBundle | None = None,
    upstream_guidance: RetrievalGuidance | None = None,
) -> dict[str, object]:
    """Run one fresh F1-F4 BF16 experiment through terminal local delivery."""

    if confirm_one_remote_fresh_integrated_run is not True:
        raise Phase4RemoteFreshIntegratedError(
            "explicit P4-05 remote generation confirmation is required"
        )
    prompt_nodes = _prompt_nodes_for_revision(prompt_revision)
    mirror = FreshIntegratedStreamMirror(console)
    prepared = prepare_phase4_remote_qwen_fresh_integrated(
        model_root=model_root,
        integrity_evidence=integrity_evidence,
        result_root=result_root,
        run_id=run_id,
        b_input=b_input,
        prompt_revision=prompt_revision,
        parent_experiment_binding=parent_experiment_binding,
        upstream_context=upstream_context,
        upstream_guidance=upstream_guidance,
    )
    result_root = prepared["result_root"]
    profile: RemoteFreshIntegratedProfile = prepared["profile"]
    run_id = str(prepared["run_id"])
    b_input = prepared["b_input"]
    parent_binding = prepared["parent_experiment_binding"]
    if expected_profile_identity is not None:
        if (
            not isinstance(expected_profile_identity, Mapping)
            or not _profile_matches_expected_identity(
                profile,
                prepared["preflight"]["profile_identity"],
                expected_profile_identity,
            )
        ):
            raise Phase4RemoteFreshIntegratedError(
                "prepared profile identity drifted from the parent experiment"
            )
    if expected_model_inventory_identity is not None:
        if (
            not isinstance(expected_model_inventory_identity, Mapping)
            or _canonical_bytes(prepared["preflight"]["model_inventory_identity"])
            != _canonical_bytes(dict(expected_model_inventory_identity))
        ):
            raise Phase4RemoteFreshIntegratedError(
                "prepared model inventory identity drifted from the parent experiment"
            )
    state = copy.deepcopy(dict(prepared["state"]))
    history_receipt = _build_call_history(
        history_result_roots=history_result_roots,
        result_root=result_root,
    )
    _write_fsync(
        result_root / "model_call_history_receipt.json",
        _canonical_bytes(history_receipt),
    )
    resume_receipt: dict[str, object] | None = None
    if resume_from_result_root is not None:
        state, resume_receipt = _restore_validated_prefix(
            resume_from_result_root=resume_from_result_root,
            result_root=result_root,
            b_input=b_input,
            state=state,
            history_receipt=history_receipt,
            resume_prefix=resume_prefix,
        )
    resumed_nodes = (
        set()
        if resume_receipt is None
        else set(str(node) for node in resume_receipt["resumed_nodes"])
    )
    historical_per_node = history_receipt[
        "aggregate_per_node_generate_started_count"
    ]
    for node_id in NODE_ORDER:
        if node_id in resumed_nodes:
            continue
        if (
            not isinstance(historical_per_node, Mapping)
            or historical_per_node.get(node_id)
            >= P4_05_TOTAL_REAL_MODEL_CALL_CAP_PER_NODE
        ):
            raise Phase4RemoteFreshIntegratedError(
                f"{node_id} aggregate real-model call cap is exhausted"
            )
    graph_bound_delivery = prepared["graph_bound_delivery"]
    live_delivery = graph_bound_delivery["live"]
    context = graph_bound_delivery["context"]
    guidance = graph_bound_delivery["guidance"]
    worker: FreshIntegratedRemoteWorker | None = None
    node_results: dict[str, object] = {}
    source_f4_raw_success = False
    normalized_node_success = False
    f4_direct_acceptance_policy_receipt: dict[str, object] | None = None
    delivery_policy = (
        PHASE4_FRESH_DELIVERY_POLICY_A07A_DIRECT_V1
        if _uses_f4_direct_acceptance(prompt_revision)
        else PHASE4_FRESH_DELIVERY_POLICY_FIELD_GATE_V1
    )
    a07b_status = (
        "not_executed_by_policy"
        if delivery_policy == PHASE4_FRESH_DELIVERY_POLICY_A07A_DIRECT_V1
        else "eligible_under_historical_field_gate_policy"
    )
    failure: dict[str, object] | None = None
    delivery_result: dict[str, object] | None = None
    terminal_status = "not_started"
    aggregate_ledger: dict[str, object] | None = None
    try:
        mirror.target.write("[P4-05] load started\n")  # type: ignore[union-attr]
        mirror.target.flush()  # type: ignore[union-attr]
        worker = FreshIntegratedRemoteWorker(
            model_root=model_root,
            profile=profile,
            mirror=mirror,
        )
        _write_fsync(
            result_root / "load_receipt.json",
            _canonical_bytes(
                {
                    "schema_version": f"{P4_05_SCHEMA_PREFIX}.load_receipt.v1",
                    "parent_experiment_binding": parent_binding,
                    "profile_identity": _identity(
                        profile.to_dict(),
                        revision=P4_05_PROFILE_SCHEMA_VERSION,
                    ),
                    "loaded_facts": worker.loaded_facts,
                    "model_loaded": True,
                    "dtype": P4_05_DTYPE,
                    "quantization": P4_05_QUANTIZATION,
                    "device": "cuda:0",
                    "cpu_offload": False,
                }
            ),
        )
        mirror.target.write("[P4-05] load completed\n")  # type: ignore[union-attr]
        mirror.target.flush()  # type: ignore[union-attr]
        for index, node_id in enumerate(NODE_ORDER, start=1):
            if node_id in resumed_nodes:
                mirror.target.write(
                    f"[P4-05] {node_id} restored from validated checkpoint\n"
                )  # type: ignore[union-attr]
                mirror.target.flush()  # type: ignore[union-attr]
                continue
            if node_id == "F4":
                mapping_before_f4 = phase4_create_mapping(state)
                _write_fsync(
                    result_root / "mapping.json",
                    _canonical_bytes(mapping_before_f4),
                )
            authority = phase4_project_node_input_authority(state, node_id)
            input_bytes = _node_input(
                node_id=node_id,
                b_input=b_input,
                state=state,
                authority_projection=authority,
            )
            node_prompt_revision = (
                prompt_revision if node_id in prompt_nodes else None
            )
            _validate_prompt_revision(
                node_id=node_id,
                prompt_revision=node_prompt_revision,
            )
            prompt_bytes = _node_prompt(
                node_id=node_id,
                input_bytes=input_bytes,
                prompt_revision=node_prompt_revision,
            )
            config_bytes = _node_config(node_id=node_id, profile=profile)
            request_bytes = _node_request(
                run_id=run_id,
                case_id=str(b_input["case_id"]),
                request_id=str(b_input["request_id"]),
                node_id=node_id,
                index=index,
                prompt_revision=node_prompt_revision,
                parent_experiment_binding=parent_binding,
            )
            pre_call = _pre_call_record(
                profile=profile,
                run_id=run_id,
                case_id=str(b_input["case_id"]),
                request_id=str(b_input["request_id"]),
                node_id=node_id,
                index=index,
                input_bytes=input_bytes,
                prompt_bytes=prompt_bytes,
                config_bytes=config_bytes,
                request_bytes=request_bytes,
                worker_id=worker.worker_id,
                worker_pid=worker.worker_pid,
                prompt_revision=node_prompt_revision,
                parent_experiment_binding=parent_binding,
            )
            attempt_root = result_root / "attempts" / node_id
            _write_fsync(attempt_root / "input.json", input_bytes)
            _write_fsync(attempt_root / "prompt.json", prompt_bytes)
            _write_fsync(attempt_root / "config.json", config_bytes)
            _write_fsync(attempt_root / "request.json", request_bytes)
            _write_fsync(
                attempt_root / "pre_call_record.json",
                _canonical_bytes(pre_call),
            )
            mirror.target.write(f"[P4-05] {node_id} generation started\n")  # type: ignore[union-attr]
            mirror.target.flush()  # type: ignore[union-attr]
            raw: bytes | None = None
            generate_started = False
            try:
                raw = worker.generate(
                    node_id=node_id,
                    input_bytes=input_bytes,
                    prompt_bytes=prompt_bytes,
                    config_bytes=config_bytes,
                    request_bytes=request_bytes,
                )
                generate_started = True
                _write_fsync(attempt_root / "raw_response.bin", raw)
                parsed = _parse_model_json(raw, f"{node_id} raw response")
                if not isinstance(parsed, Mapping):
                    raise Phase4RemoteFreshIntegratedError(
                        f"{node_id} raw response is not an object"
                    )
                output = dict(parsed)
                normalization_receipt = None
                if node_id == "F4":
                    output, normalization_receipt = phase4_normalize_and_validate_f4_output(
                        raw_bytes=raw,
                        output=output,
                        state=state,
                    )
                    source_f4_raw_success = (
                        normalization_receipt["raw_model_contract_success"] is True
                    )
                    normalized_node_success = (
                        normalization_receipt["normalized_node_contract_success"]
                        is True
                    )
                    _write_fsync(
                        attempt_root / "normalization_receipt.json",
                        _canonical_bytes(normalization_receipt),
                    )
                    _write_fsync(
                        result_root / "core_f4_normalization_receipt.json",
                        _canonical_bytes(normalization_receipt),
                    )
                    if _uses_f4_direct_acceptance(prompt_revision):
                        f4_direct_acceptance_policy_receipt = (
                            _f4_direct_acceptance_policy_receipt(
                                input_bytes=input_bytes,
                                output=output,
                                raw_bytes=raw,
                            )
                        )
                        _write_fsync(
                            attempt_root
                            / "f4_direct_acceptance_policy_receipt.json",
                            _canonical_bytes(
                                f4_direct_acceptance_policy_receipt
                            ),
                        )
                        _write_fsync(
                            result_root
                            / "f4_direct_acceptance_policy_receipt.json",
                            _canonical_bytes(
                                f4_direct_acceptance_policy_receipt
                            ),
                        )
                else:
                    phase4_validate_node_output(node_id, output, state)
                state = phase4_register_node_output(state, node_id, output)
                _write_fsync(
                    attempt_root / "validated_node_output.json",
                    _canonical_bytes(output),
                )
                attempt = _attempt_record(
                    run_id=run_id,
                    case_id=str(b_input["case_id"]),
                    request_id=str(b_input["request_id"]),
                    node_id=node_id,
                    input_bytes=input_bytes,
                    prompt_bytes=prompt_bytes,
                    config_bytes=config_bytes,
                    request_bytes=request_bytes,
                    pre_call_record=pre_call,
                    worker_id=worker.worker_id,
                    worker_pid=worker.worker_pid,
                    raw=raw,
                    generate_started=True,
                    status="validated",
                    failure_code=None,
                    prompt_revision=node_prompt_revision,
                    parent_experiment_binding=parent_binding,
                )
                node_results[node_id] = attempt
                _write_fsync(
                    attempt_root / "attempt_result.json",
                    _canonical_bytes(attempt),
                )
                mirror.target.write(f"\n[P4-05] {node_id} generation completed\n")  # type: ignore[union-attr]
                mirror.target.flush()  # type: ignore[union-attr]
            except Exception as exc:
                generate_started = (
                    generate_started
                    or mirror.generation_started_for(node_id)
                )
                failure_code = (
                    "node_failed_closed"
                    if generate_started
                    else "worker_not_started"
                )
                failure = {
                    "code": failure_code,
                    "node_id": node_id,
                    "message_code": type(exc).__name__,
                    "retry_allowed": False,
                    "automatic_retry": False,
                    "generate_started": generate_started,
                }
                attempt = _attempt_record(
                    run_id=run_id,
                    case_id=str(b_input["case_id"]),
                    request_id=str(b_input["request_id"]),
                    node_id=node_id,
                    input_bytes=input_bytes,
                    prompt_bytes=prompt_bytes,
                    config_bytes=config_bytes,
                    request_bytes=request_bytes,
                    pre_call_record=pre_call,
                    worker_id=worker.worker_id,
                    worker_pid=worker.worker_pid,
                    raw=raw,
                    generate_started=generate_started,
                    status="failed_closed",
                    failure_code=failure_code,
                    prompt_revision=node_prompt_revision,
                    parent_experiment_binding=parent_binding,
                )
                node_results[node_id] = attempt
                _write_fsync(
                    attempt_root / "attempt_result.json",
                    _canonical_bytes(attempt),
                )
                _write_fsync(
                    result_root / "failure.json",
                    _canonical_bytes(failure),
                )
                terminal_status = "failed_closed"
                break
        if failure is None:
            ledger = _call_ledger(
                run_id=run_id,
                node_results=node_results,
            )
            _write_fsync(
                result_root / "model_call_ledger.json",
                _canonical_bytes(ledger),
            )
            aggregate_ledger = _aggregate_call_ledger(
                run_id=run_id,
                history_receipt=history_receipt,
                current_ledger=ledger,
            )
            _write_fsync(
                result_root / "aggregate_model_call_ledger.json",
                _canonical_bytes(aggregate_ledger),
            )
            mapping = phase4_create_mapping(state)
            _write_fsync(result_root / "mapping.json", _canonical_bytes(mapping))
            composition = phase4_compose_candidate(state)
            _write_fsync(
                result_root / "candidate_composition_record.json",
                _canonical_bytes(composition),
            )
            candidate_bytes = base64.b64decode(
                str(composition["model_semantic_candidate_canonical_b64"]),
                validate=True,
            )
            assembled = phase4_assemble_candidate(
                candidate_bytes,
                context,
                guidance,
            )
            assembled.validate()
            _write_fsync(
                result_root / "assembled_page_spec.json",
                _canonical_bytes(assembled.page_spec.to_dict()),
            )
            _write_fsync(
                result_root / "assembly_report.json",
                _canonical_bytes(assembled.report.to_dict()),
            )
            source_result = {
                "schema_version": P4_05_RESULT_SCHEMA_VERSION,
                "pilot_id": P4_05_PILOT_ID,
                "run_id": run_id,
                "case_id": b_input["case_id"],
                "request_id": b_input["request_id"],
                "parent_experiment_binding": parent_binding,
                "source_kind": "remote_qwen_bf16_fresh_integrated",
                "status": "assembled",
                "model_generate_calls": aggregate_ledger[
                    "aggregate_total_generate_calls"
                ],
                "model_call_ledger_identity": _identity(
                    ledger,
                    revision=P4_05_LEDGER_SCHEMA_VERSION,
                ),
                "aggregate_model_call_ledger_identity": _identity(
                    aggregate_ledger,
                    revision=P4_05_AGGREGATE_LEDGER_SCHEMA_VERSION,
                ),
                "resume_receipt_identity": (
                    None
                    if resume_receipt is None
                    else _identity(
                        resume_receipt,
                        revision=P4_05_RESUME_SCHEMA_VERSION,
                    )
                ),
                "raw_model_contract_success": source_f4_raw_success,
                "normalized_node_contract_success": normalized_node_success,
                "agent_chain_system_output_usable": True,
                "composition_status": "composed",
                "assembler_status": "assembled",
                "downstream": "not_executed",
                "downstream_policy": delivery_policy,
                "a07b_status": a07b_status,
                "f4_direct_acceptance_policy_receipt_identity": (
                    None
                    if f4_direct_acceptance_policy_receipt is None
                    else _identity(
                        f4_direct_acceptance_policy_receipt,
                        revision=(
                            P4_05_F4_DIRECT_ACCEPTANCE_POLICY_RECEIPT_SCHEMA_VERSION
                        ),
                    )
                ),
                "historical_strict_result": "0/2_unchanged",
                "claim_boundary": (
                    "P4-05 remote BF16 fresh integrated result; "
                    "not strict raw-model first-pass success"
                ),
                "failure": None,
            }
            if prompt_revision is not None:
                source_result["prompt_revision"] = prompt_revision
                source_result["resume_prefix"] = resume_prefix
            _write_fsync(
                result_root / "revalidation_result.json",
                _canonical_bytes(source_result),
            )
            try:
                receipt = run_phase4_fresh_delivery(
                    source_root=result_root,
                    delivery_root=result_root / "delivery",
                    context=context,
                    guidance=guidance,
                    manifest=live_delivery["manifest"],
                    selected=live_delivery["selected"],
                    local_request=live_delivery["local_request"],
                    pre_invocation_audit=live_delivery[
                        "pre_invocation_audit"
                    ],
                    local_qwen_preparation=live_delivery[
                        "local_qwen_preparation"
                    ],
                    package=live_delivery["package"],
                    frozen_g0_reference=live_delivery[
                        "frozen_g0_reference"
                    ],
                    fallback_record=live_delivery["fallback_record"],
                    fallback_snapshot_dir=live_delivery[
                        "fallback_snapshot_dir"
                    ],
                    scripted_acceptance_fixture=live_delivery[
                        "scripted_acceptance_fixture"
                    ],
                    delivery_policy=delivery_policy,
                )
            except Exception as exc:
                delivery = {
                    "status": "failed_closed",
                    "failure": {
                        "code": "fresh_delivery_exception",
                        "message_code": type(exc).__name__,
                        "retry_allowed": False,
                        "model_retry_performed": False,
                        "automatic_fallback_performed": False,
                    },
                }
                delivery_result = delivery
                failure = {
                    "code": "fresh_delivery_failed_closed",
                    "node_id": None,
                    "message_code": type(exc).__name__,
                    "retry_allowed": False,
                    "automatic_retry": False,
                    "generate_started": True,
                }
                _write_fsync(
                    result_root / "delivery_failure.json",
                    _canonical_bytes(delivery),
                )
                _write_fsync(
                    result_root / "failure.json",
                    _canonical_bytes(failure),
                )
                terminal_status = "failed_closed"
            else:
                delivery = receipt.to_dict()
                delivery_result = delivery
                if receipt.success_accounting["delivery_success"] is True:
                    terminal_status = "delivery_terminal_success"
                else:
                    failure = {
                        "code": "fresh_delivery_terminal_failed",
                        "node_id": None,
                        "message_code": str(receipt.downstream["status"]),
                        "retry_allowed": False,
                        "automatic_retry": False,
                        "generate_started": True,
                    }
                    _write_fsync(
                        result_root / "failure.json",
                        _canonical_bytes(failure),
                    )
                    terminal_status = "failed_closed"
            _write_fsync(
                result_root / "p4_05_final_result.json",
                _canonical_bytes(
                    {
                        **source_result,
                        "status": terminal_status,
                        "delivery_receipt": (
                            delivery
                            if "receipt_id" in delivery
                            else None
                        ),
                        "delivery_failure": (
                            None
                            if "receipt_id" in delivery
                            else delivery
                        ),
                        "delivery_result_identity": _identity(
                            delivery,
                            revision=f"{P4_05_RESULT_SCHEMA_VERSION}.delivery",
                        ),
                    }
                ),
            )
        elif terminal_status != "failed_closed":
            terminal_status = "failed_closed"
    finally:
        if worker is not None:
            teardown = worker.close()
        else:
            teardown = {
                "worker_id": None,
                "worker_pid": None,
                "worker_exit_code": None,
                "worker_exit_verified": False,
                "terminal_status": "worker_not_started",
                "generation_started": False,
                "generate_calls": {node_id: 0 for node_id in NODE_ORDER},
                "attempt_envelopes": {node_id: 0 for node_id in NODE_ORDER},
            }
        ledger = _call_ledger(
            run_id=run_id,
            node_results=node_results,
        )
        _write_fsync(
            result_root / "model_call_ledger.json",
            _canonical_bytes(ledger),
        )
        aggregate_ledger = _aggregate_call_ledger(
            run_id=run_id,
            history_receipt=history_receipt,
            current_ledger=ledger,
        )
        _write_fsync(
            result_root / "aggregate_model_call_ledger.json",
            _canonical_bytes(aggregate_ledger),
        )
        supervisor = {
            "schema_version": P4_05_SUPERVISOR_SCHEMA_VERSION,
            "pilot_id": P4_05_PILOT_ID,
            "run_id": run_id,
            "parent_experiment_binding": parent_binding,
            "terminal_status": teardown["terminal_status"],
            "worker_id": teardown["worker_id"],
            "worker_pid": teardown["worker_pid"],
            "worker_exit_code": teardown["worker_exit_code"],
            "worker_exit_verified": teardown["worker_exit_verified"],
            "generation_started": teardown["generation_started"],
            "generate_calls": teardown["generate_calls"],
            "attempt_envelopes": teardown.get(
                "attempt_envelopes",
                {node_id: 0 for node_id in NODE_ORDER},
            ),
            "model_call_ledger_identity": _identity(
                ledger,
                revision=P4_05_LEDGER_SCHEMA_VERSION,
            ),
            "aggregate_model_call_ledger_identity": _identity(
                aggregate_ledger,
                revision=P4_05_AGGREGATE_LEDGER_SCHEMA_VERSION,
            ),
            "history_receipt_identity": _identity(
                history_receipt,
                revision=P4_05_HISTORY_SCHEMA_VERSION,
            ),
            "resume_receipt_identity": (
                None
                if resume_receipt is None
                else _identity(
                    resume_receipt,
                    revision=P4_05_RESUME_SCHEMA_VERSION,
                )
            ),
            "stderr_identity": _identity(
                bytes(mirror.stderr_bytes),
                revision=f"{P4_05_SUPERVISOR_SCHEMA_VERSION}.stderr",
                identity_kind="raw_bytes",
            ),
            "action_state": _action_state(
                model_action=bool(teardown["generation_started"]),
                remote_action=bool(teardown["generation_started"]),
            ),
            "stdout_thread_joined": teardown.get("stdout_thread_joined", False),
            "stderr_thread_joined": teardown.get("stderr_thread_joined", False),
            "stderr_capture_completed": teardown.get(
                "stderr_capture_completed", False
            ),
        }
        _write_fsync(
            result_root / "supervisor_receipt.json",
            _canonical_bytes(supervisor),
        )
    result = {
        "schema_version": P4_05_RESULT_SCHEMA_VERSION,
        "pilot_id": P4_05_PILOT_ID,
        "run_id": run_id,
        "case_id": b_input["case_id"],
        "request_id": b_input["request_id"],
        "parent_experiment_binding": parent_binding,
        "status": terminal_status,
        "node_results": node_results,
        "failure": failure,
        "source_f4_raw_contract_success": source_f4_raw_success,
        "normalized_node_contract_success": normalized_node_success,
        "model_generate_calls": aggregate_ledger[
            "aggregate_total_generate_calls"
        ],
        "current_run_model_generate_calls": sum(
            node_results[node]["call_count"] for node in node_results
        ),
        "resumed_nodes": (
            []
            if resume_receipt is None
            else list(resume_receipt["resumed_nodes"])
        ),
        "delivery_status": (
            None
            if delivery_result is None
            else delivery_result.get("status")
            or delivery_result.get("downstream", {}).get("status")
        ),
        "delivery_receipt_terminal": (
            delivery_result is not None
            and isinstance(delivery_result.get("receipt_id"), str)
            and bool(delivery_result["receipt_id"])
        ),
        "supervisor": supervisor,
        "claim_boundary": (
            "P4-05 remote BF16 fresh integrated experiment; "
            "existing downstream delivery authorities are executed or "
            "explicitly fail-closed; not strict raw-model first-pass success, "
            "H1, browser, or formal quality"
        ),
    }
    if prompt_revision is not None:
        result["prompt_revision"] = prompt_revision
        result["resume_prefix"] = resume_prefix
    return result


def main_worker(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "--worker" or argv[1] != "--model-root":
        return 2
    return _run_worker_protocol(Path(argv[2]).resolve(strict=True))


__all__ = [
    "ACTIVE_DEFAULT_ENTRY",
    "FLOW_AUTHORITY_ROLE",
    "HISTORICAL_MANUAL_ENTRYPOINT",
    "P4_05_CASE_ID",
    "P4_05_GENERATE_CALL_CAP",
    "P4_05_INPUT_CLASSES",
    "P4_05_INPUT_SCHEMA_VERSION",
    "P4_05_LEDGER_SCHEMA_VERSION",
    "P4_05_MODEL_CONTEXT_TOKENS",
    "P4_05_PARENT_BINDING_SCHEMA_VERSION",
    "P4_05_PILOT_ID",
    "P4_05_F4_DIRECT_ACCEPTANCE_POLICY_RECEIPT_SCHEMA_VERSION",
    "P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_NODES",
    "P4_05_F4_DIRECT_ACCEPTANCE_PROMPT_REVISION",
    "P4_05_FULL_DIRECT_PROMPT_NODES",
    "P4_05_FULL_DIRECT_PROMPT_REVISION",
    "P4_05_F3_F4_PROMPT_REVISION",
    "P4_05_LEGACY_STABILITY_PROFILE_BINDING_SCHEMA_VERSION",
    "P4_05_PRE_CALL_SCHEMA_VERSION",
    "P4_05_QUANTIZATION",
    "P4_05_REQUEST_ID",
    "P4_05_RUN_PREFIX",
    "P4_05_RESUME_PREFIX_F1_F2",
    "P4_05_RESUME_PREFIX_F1_F3",
    "P4_05_STABILITY_PROFILE_BINDING_SCHEMA_VERSION",
    "P4_05_SUPERVISOR_SCHEMA_VERSION",
    "P4_05_TIMEOUT_SECONDS",
    "Phase4RemoteFreshIntegratedError",
    "RemoteFreshIntegratedProfile",
    "FreshIntegratedRemoteWorker",
    "FreshIntegratedStreamMirror",
    "create_p4_05_policy",
    "make_stable_profile_binding_identity",
    "migrate_stable_profile_binding_identity",
    "prepare_phase4_remote_qwen_fresh_integrated",
    "run_phase4_remote_qwen_fresh_integrated",
]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        raise SystemExit(main_worker(sys.argv[1:]))
