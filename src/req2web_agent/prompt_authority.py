"""Project-wide canonical F1-F4 prompt authority.

The authority is shared by development, Phase 4, Phase 5, real execution, and
release runners.  A caller may project different stage-specific input bytes,
but it may not redefine node output contracts, invariants, or instructions.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Mapping


PROMPT_AUTHORITY_SCHEMA_VERSION = "req2web.agent.f1_f4_prompt_authority.v1"
PROMPT_SCHEMA_VERSION = "req2web.agent.f1_f4_prompt.v1"
PROMPT_AUTHORITY_REVISION = (
    "f3_f4_explicit_actual_state_plan_a07a_direct_english_v7"
)
REGISTRY_REVISION = "req2web.phase4.registry.p4_02a.v1"
NODE_ORDER = ("F1", "F2", "F3", "F4")


class PromptAuthorityError(ValueError):
    """Raised when a caller cannot use the canonical prompt authority."""


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
        raise PromptAuthorityError("prompt value is not canonical JSON") from exc


def _strict_json(raw: bytes, name: str) -> Mapping[str, object]:
    if type(raw) is not bytes or not raw or raw.startswith(b"\xef\xbb\xbf"):
        raise PromptAuthorityError(f"{name} must be non-empty canonical JSON bytes")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptAuthorityError(f"{name} is not valid JSON") from exc
    if not isinstance(value, Mapping) or _canonical(value) != raw:
        raise PromptAuthorityError(f"{name} is not a canonical JSON object")
    return value


def _identity(raw: bytes, *, revision: str) -> dict[str, object]:
    return {
        "identity_kind": "raw_bytes",
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": revision,
    }


def _base_output_contracts() -> dict[str, dict[str, object]]:
    return {
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
                "treat the first row in the supplied F2 states array as the initial workflow state; do not require its name to be the literal word initial",
                "follow canonical B use-case order and create deterministic state progressions from the initial state",
                "for every supplied F2 state, include at least one same-state interaction that performs in-state work so that state is a non-empty acceptance target",
                "for every adjacent pair in supplied F2 state order, include at least one forward transition from the earlier state to the later state",
                "every interaction trigger component must be visible in its source state's visible_component_local_ids",
                "same-state actions are allowed, but cannot replace the required forward transitions toward later workflow states",
                "avoid backward transitions and avoid multiple equivalent forward paths for the same workflow step",
                "do not add or fabricate use-case reference fields",
                "let N be the number of supplied F2 states and emit exactly 2*N-1 interactions",
                "use this exact interaction order: same-state work for state 0, forward transition state 0 to state 1, same-state work for state 1, then continue alternating until same-state work for the final state",
                "every same-state interaction must use the same supplied state local ID for source_state_local_id and target_state_local_id",
                "every forward transition must connect one supplied state directly to the next supplied state in array order; never skip a state",
                "choose each trigger_component_local_id only from the source state's visible_component_local_ids; never use a component that is visible only in the target state",
                "the initial state's same-state interaction performs the first canonical use case's in-state work, and each later state's same-state interaction performs that state's main work or validation",
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
            "reference_exact_keys": ["ref_type", "ref_id", "ref_revision"],
            "field_sources": {
                "use_case_refs": {
                    "source": "supplied canonical B use-case view",
                    "required_ref_type": "canonical_b_use_case",
                    "required_ref_revision": "canonical_b.use_case.v1",
                    "forbidden_ref_type": "registry_stable",
                },
                "state_ref": {
                    "source": "supplied F2 registered state visibility view",
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
                "emit exactly one acceptance check for each canonical use case, in canonical use-case order, and put exactly that one use-case reference in the check",
                "derive every state_ref from the actual supplied F3 interaction plan; the selected stable state ID must be an actual validated target for that use case",
                "do not select the first supplied F2 state merely because it is the initial state",
                "keep selected state positions monotonically non-decreasing across canonical use-case order",
                "when later use cases have no later eligible target, reuse only the final eligible target state; never invent an interaction, state, or registry identity",
                "never emit a second acceptance target for the same use case",
            ],
        },
    }


def _base_instructions() -> list[str]:
    return [
        "Return exactly one JSON object and no prose.",
        "Use strict RFC 8259 JSON syntax: double-quoted keys and string values, a colon between every key and value, and no trailing commas.",
        "Every object must contain exactly the keys listed in exact_output_contract; do not add properties or alternate nesting.",
        "Every listed key is mandatory, including refs fields whose required value is the empty array [].",
        "For F4, use_case_refs and state_ref use different namespaces; never copy a state_ref or any registry_stable reference into use_case_refs.",
        "Preserve the required node schema and semantic array order.",
        "Do not emit authoritative IDs, mappings, acceptance verdicts, or browser evidence.",
        "Do not abbreviate, truncate, omit, or split the object.",
        (
            "Write every user-visible natural-language value in English only. "
            "This includes page titles, section and component copy, state "
            "names and descriptions, interaction actions and feedback, and "
            "acceptance descriptions. Do not emit Han characters or CJK "
            "punctuation in those fields."
        ),
        "F4 must emit candidate acceptance semantics only.",
        "The shared direct-v3 authority may not change the registry, mapping, composition, assembler, consistency, acceptance, repair, or fallback authorities.",
    ]


def _node_specific_instructions() -> dict[str, list[str]]:
    return {
        "F2": [
            (
                "For every state, construct visible_component_local_ids only "
                "by scanning required_f1_component_order from left to right "
                "and selecting the IDs visible in that state. The emitted "
                "array must be an exact subsequence of "
                "required_f1_component_order. Never regroup components by "
                "section, workflow meaning, or state purpose, and never place "
                "an earlier component ID after a later component ID."
            ),
            (
                "Treat the first emitted state as the explicit initial "
                "workflow state. If any supplied requirement or constraint "
                "mentions validation, error, failure, recovery, retry, "
                "preserving valid fields, or error feedback, emit both a "
                "separate error state and a later recovery or success state. "
                "The error state's name or description must explicitly use "
                "Error, Validation, or Failure. The later state's name or "
                "description must explicitly use Recovery, Retry, Recovered, "
                "or Success. The error state must not be the final state, and "
                "both states must expose the relevant form, feedback, and "
                "recovery controls in required F1 order."
            ),
        ],
        "F3": [
            (
                "When the supplied F2 state order contains an error state "
                "followed by a recovery or success state, make the forward "
                "transition into the error state describe the invalid input "
                "or failure and make the next forward transition out of the "
                "error state describe correction, retry, or recovery. A "
                "successful recovery must target the later non-error state; "
                "never represent successful recovery as a self-loop in the "
                "error state."
            ),
        ],
    }


def prompt_authority_manifest() -> dict[str, object]:
    root = {
        "schema_version": PROMPT_AUTHORITY_SCHEMA_VERSION,
        "revision": PROMPT_AUTHORITY_REVISION,
        "node_order": list(NODE_ORDER),
        "output_contracts": _base_output_contracts(),
        "instructions": _base_instructions(),
        "node_specific_instructions": _node_specific_instructions(),
        "stage_specific_fields": [
            "input_projection",
            "custody_visibility",
            "authority",
            "budget",
            "result_handling",
        ],
        "stage_specific_prompt_semantics_allowed": False,
        "historical_phase_specific_revisions_are_runtime_sources": False,
    }
    raw = _canonical(root)
    return {
        **root,
        "authority_identity": _identity(
            raw,
            revision=PROMPT_AUTHORITY_SCHEMA_VERSION,
        ),
    }


PROMPT_AUTHORITY_IDENTITY = prompt_authority_manifest()["authority_identity"]


def _required_f1_component_order(
    input_value: Mapping[str, object],
) -> list[str]:
    f1_views: list[Mapping[str, object]] = []
    for projection_key in (
        "projection",
        "same_run_validated_upstream_projection",
    ):
        projection = input_value.get(projection_key)
        if not isinstance(projection, Mapping):
            continue
        candidate = projection.get("f1_registered_structure_view")
        if isinstance(candidate, Mapping):
            f1_views.append(candidate)
    if len(f1_views) != 1:
        raise PromptAuthorityError("F2 registered F1 structure is unavailable")
    f1_view = f1_views[0]
    components = f1_view.get("components")
    if not isinstance(components, list) or not components:
        raise PromptAuthorityError("F2 registered F1 components are unavailable")
    order: list[str] = []
    for component in components:
        if not isinstance(component, Mapping):
            raise PromptAuthorityError(
                "F2 registered F1 component row is invalid"
            )
        local_id = component.get("local_id")
        if not isinstance(local_id, str) or not local_id:
            raise PromptAuthorityError(
                "F2 registered F1 component local_id is invalid"
            )
        order.append(local_id)
    if len(order) != len(set(order)):
        raise PromptAuthorityError(
            "F2 registered F1 component order contains duplicates"
        )
    return order


def _validate_plan(
    value: object,
    *,
    name: str,
) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise PromptAuthorityError(f"{name} must be a non-empty array")
    rows: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping) or not item:
            raise PromptAuthorityError(f"{name} contains an invalid row")
        rows.append(copy.deepcopy(dict(item)))
    return rows


def build_canonical_f1_f4_prompt(
    *,
    node_id: str,
    input_bytes: bytes,
    required_interaction_plan: object | None = None,
    required_acceptance_target_plan: object | None = None,
) -> bytes:
    """Build the sole active prompt structure for one F1-F4 model call."""

    if node_id not in NODE_ORDER:
        raise PromptAuthorityError("node_id is not an F1-F4 node")
    input_value = _strict_json(input_bytes, f"{node_id} input")
    if input_value.get("node_id") != node_id:
        raise PromptAuthorityError("prompt input node binding drifted")
    if node_id == "F3":
        interaction_plan = _validate_plan(
            required_interaction_plan,
            name="required_interaction_plan",
        )
    elif required_interaction_plan is not None:
        raise PromptAuthorityError("only F3 may receive an interaction plan")
    else:
        interaction_plan = None
    if node_id == "F4":
        acceptance_plan = _validate_plan(
            required_acceptance_target_plan,
            name="required_acceptance_target_plan",
        )
    elif required_acceptance_target_plan is not None:
        raise PromptAuthorityError("only F4 may receive an acceptance plan")
    else:
        acceptance_plan = None

    instructions = [
        *_base_instructions(),
        *_node_specific_instructions().get(node_id, []),
    ]
    payload: dict[str, object] = {
        "schema_version": PROMPT_SCHEMA_VERSION,
        "prompt_revision": PROMPT_AUTHORITY_REVISION,
        "prompt_authority_identity": copy.deepcopy(PROMPT_AUTHORITY_IDENTITY),
        "node_id": node_id,
        "input_schema_version": input_value.get("schema_version"),
        "input_identity": _identity(
            input_bytes,
            revision=str(input_value.get("schema_version")),
        ),
        "output_format": "one_complete_canonical_json_object",
        "instructions": instructions,
        "node_output_keys": {
            "F1": ["page_title", "layout_pattern", "sections", "components"],
            "F2": ["states"],
            "F3": ["interactions"],
            "F4": ["acceptance_checks"],
        }[node_id],
        "exact_output_contract": _base_output_contracts()[node_id],
    }
    if node_id == "F2":
        payload["required_f1_component_order"] = (
            _required_f1_component_order(input_value)
        )
    if interaction_plan is not None:
        payload["required_interaction_plan"] = interaction_plan
        payload["instructions"] = [
            *instructions,
            (
                "Copy every source state, target state, row position, and one "
                "allowed trigger component from required_interaction_plan. Emit "
                "exactly one interaction for every plan row in the same order; "
                "do not omit, merge, or add rows."
            ),
        ]
    if acceptance_plan is not None:
        payload["required_acceptance_target_plan"] = acceptance_plan
        payload["instructions"] = [
            *instructions,
            (
                "Emit exactly one acceptance check for every row in "
                "required_acceptance_target_plan and in the same order. Copy "
                "each use_case_ref and state_ref object exactly; do not select "
                "a different state, omit a row, or add a row."
            ),
        ]
    return _canonical(payload)


def validate_canonical_prompt(
    raw: bytes,
    *,
    node_id: str,
    input_bytes: bytes,
    required_interaction_plan: object | None = None,
    required_acceptance_target_plan: object | None = None,
) -> dict[str, object]:
    """Replay the complete prompt from its exact input and plan bindings."""

    value = dict(_strict_json(raw, f"{node_id} prompt"))
    expected = build_canonical_f1_f4_prompt(
        node_id=node_id,
        input_bytes=input_bytes,
        required_interaction_plan=required_interaction_plan,
        required_acceptance_target_plan=required_acceptance_target_plan,
    )
    if raw != expected:
        raise PromptAuthorityError("prompt bytes drifted from the shared authority")
    return value


__all__ = [
    "NODE_ORDER",
    "PROMPT_AUTHORITY_IDENTITY",
    "PROMPT_AUTHORITY_REVISION",
    "PROMPT_AUTHORITY_SCHEMA_VERSION",
    "PROMPT_SCHEMA_VERSION",
    "PromptAuthorityError",
    "build_canonical_f1_f4_prompt",
    "prompt_authority_manifest",
    "validate_canonical_prompt",
]
