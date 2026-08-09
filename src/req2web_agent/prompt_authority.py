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
    "f3_f4_explicit_actual_state_plan_a07a_direct_english_v15"
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
                "for every final F2 state, include one same-state interaction that performs in-state work so that state is a non-empty acceptance target",
                "for a non-final F2 state with at least two visible components, include one same-state interaction and one adjacent forward transition with distinct triggers",
                "for a non-final F2 state with exactly one visible component, emit only the adjacent forward transition so the source-state trigger remains unambiguous",
                "for every adjacent pair in supplied F2 state order, include at least one forward transition from the earlier state to the later state",
                "every interaction trigger component must be visible in its source state's visible_component_local_ids",
                "same-state actions are allowed, but cannot replace the required forward transitions toward later workflow states",
                "avoid backward transitions and avoid multiple equivalent forward paths for the same workflow step",
                "do not add or fabricate use-case reference fields",
                "emit the exact number of rows in required_interaction_plan; a non-final state contributes two rows when it has distinct same-state and forward triggers, otherwise it contributes its single forward row, and the final state contributes one same-state row",
                "preserve required_interaction_plan order: optional same-state work for a non-final state precedes its forward transition, and final-state same-state work is last",
                "every same-state interaction must use the same supplied state local ID for source_state_local_id and target_state_local_id",
                "every forward transition must connect one supplied state directly to the next supplied state in array order; never skip a state",
                "choose each trigger_component_local_id only from the source state's visible_component_local_ids; never use a component that is visible only in the target state",
                "within one source state, each trigger_component_local_id may appear in at most one interaction; whenever both same-state work and a forward transition exist, they must use different trigger components",
                "when a state has a same-state interaction, it performs that state's main work or validation; a one-visible non-final state advances through its sole unambiguous forward interaction instead",
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
        "F1": [
            (
                "Before emitting the F1 JSON object, finalize the complete "
                "components array. Then, in components-array order, place "
                "every component local_id exactly once in the "
                "component_local_ids of the section identified by that "
                "component's section_local_id. This includes every submit, "
                "retry, and feedback component. The concatenation of "
                "sections[].component_local_ids must exactly equal the "
                "components array local_id order."
            ),
            (
                "When the requirement includes form submission, validation, "
                "error recovery, retry, or final confirmation, represent data "
                "entry, submission or retry, and feedback as separate "
                "components. Include an explicit button or action component "
                "for submission or retry; never collapse editable fields and "
                "the advancing action into one generic form component."
            ),
            (
                "For every major workflow step that performs in-state work and "
                "then advances, include a dedicated advancement control "
                "distinct from the input, list, summary, or feedback component "
                "used for the in-state work."
            ),
            (
                "Name and describe each advancement control with its actual "
                "transition purpose, such as Proceed to Checkout, Continue, "
                "Submit, or Retry. A generic cart-update or remove-items action "
                "group does not replace a dedicated control that advances from "
                "cart review to checkout."
            ),
        ],
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
            (
                "For every non-final state, keep visible at least two distinct "
                "interactive controls when the F1 structure provides them: "
                "one control for required same-state work and a different "
                "control for the forward transition to the next state. A "
                "control that is not visible in the source state cannot be "
                "used by F3."
            ),
            (
                "When F1 provides a dedicated submit, proceed, retry, or other "
                "advancement control, keep it visible in the relevant "
                "non-final state together with a separate work or feedback "
                "control. Do not count a display-only success message as the "
                "advancement control."
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
            (
                "Within the same source state, never assign the same "
                "trigger_component_local_id to both the required same-state "
                "interaction and the required forward transition. One visible "
                "control cannot represent two different actions in the same "
                "state; choose distinct visible trigger components."
            ),
            (
                "For every interaction, copy trigger_component_local_id "
                "directly from the source state's "
                "visible_component_local_ids. Never select a preferred action "
                "control that is absent from that exact source-state list."
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


def _f3_text(value: Mapping[str, object]) -> str:
    return " ".join(
        str(value.get(key, "")).strip().lower()
        for key in ("component_type", "label", "purpose", "name", "description")
    )


def _f3_has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _f3_is_error_state(value: Mapping[str, object]) -> bool:
    name = str(value.get("name", "")).strip().lower()
    description = str(value.get("description", "")).strip().lower()
    if _f3_has_any(
        name,
        ("error", "failed", "failure", "invalid", "denied"),
    ):
        return True
    return _f3_has_any(
        description,
        (
            "validation error",
            "validation failed",
            "validation fails",
            "validation failure",
            "failed validation",
            "invalid input",
            "invalid data",
            "error state",
            "failure state",
            "permission denied",
            "access denied",
        ),
    )


def _f3_is_success_state(value: Mapping[str, object]) -> bool:
    name = str(value.get("name", "")).strip().lower()
    description = str(value.get("description", "")).strip().lower()
    if _f3_has_any(
        name,
        ("success", "confirmed", "recovered", "recovery"),
    ):
        return True
    return _f3_has_any(
        description,
        (
            "final state",
            "success message",
            "success feedback",
            "successfully completed",
            "order confirmed",
            "recovery complete",
        ),
    )


def _f3_forward_score(
    component: Mapping[str, object],
    *,
    source_state: Mapping[str, object],
    target_state: Mapping[str, object],
) -> int:
    text = _f3_text(component)
    component_type = str(component.get("component_type", "")).lower()
    target_is_error = _f3_is_error_state(target_state)
    source_is_error = _f3_is_error_state(source_state)
    target_is_success = _f3_is_success_state(target_state)
    is_button = _f3_has_any(component_type, ("button", "action", "control"))
    is_form = _f3_has_any(
        component_type,
        ("form", "input", "field", "select"),
    )
    is_feedback = _f3_has_any(
        component_type,
        ("alert", "message", "feedback"),
    )
    has_submit = _f3_has_any(
        text,
        ("submit", "place order", "retry", "resubmit", "confirm order"),
    )
    has_advance = _f3_has_any(
        text,
        ("proceed", "checkout", "continue", "next", "advance"),
    )

    score = 0
    if target_is_error:
        score += 500 if has_submit else 0
        score += 320 if is_form else 0
        score += 180 if is_button else 0
        score += 40 if has_advance else 0
    elif source_is_error or target_is_success:
        score += 520 if has_submit else 0
        score += 300 if is_form else 0
        score += 200 if is_button else 0
        score += 40 if has_advance else 0
    else:
        score += 500 if has_advance else 0
        score += 400 if has_submit else 0
        score += 240 if is_button else 0
        score += 100 if is_form else 0
    if is_feedback:
        score -= 300
    return score


def _f3_same_state_score(
    component: Mapping[str, object],
    *,
    source_state: Mapping[str, object],
) -> int:
    text = _f3_text(component)
    component_type = str(component.get("component_type", "")).lower()
    source_is_error = _f3_is_error_state(source_state)
    is_feedback = _f3_has_any(
        component_type + " " + text,
        ("alert", "message", "feedback", "error"),
    )
    is_work = _f3_has_any(
        component_type,
        (
            "input",
            "field",
            "filter",
            "list",
            "grid",
            "summary",
            "form",
            "select",
            "table",
        ),
    )
    score = 0
    if source_is_error:
        score += 500 if is_feedback else 0
        score += 250 if is_work else 0
    else:
        score += 400 if is_work else 0
        score += 100 if is_feedback else 0
    return score


def build_canonical_f3_interaction_plan(
    *,
    f1_registered_structure_view: Mapping[str, object],
    f2_registered_state_visibility_view: Mapping[str, object],
) -> list[dict[str, object]]:
    """Build the shared exact-state and exact-trigger plan for F3."""

    components_value = f1_registered_structure_view.get("components")
    states_value = f2_registered_state_visibility_view.get("states")
    if not isinstance(components_value, list) or not components_value:
        raise PromptAuthorityError("F3 registered F1 components are unavailable")
    if not isinstance(states_value, list) or not states_value:
        raise PromptAuthorityError("F3 registered F2 states are unavailable")

    components: dict[str, Mapping[str, object]] = {}
    for item in components_value:
        if not isinstance(item, Mapping):
            raise PromptAuthorityError("F3 registered F1 component row is invalid")
        local_id = item.get("local_id")
        if not isinstance(local_id, str) or not local_id or local_id in components:
            raise PromptAuthorityError("F3 registered F1 component ID is invalid")
        components[local_id] = item

    states: list[Mapping[str, object]] = []
    for item in states_value:
        if not isinstance(item, Mapping):
            raise PromptAuthorityError("F3 registered F2 state row is invalid")
        local_id = item.get("local_id")
        visible = item.get("visible_component_local_ids")
        if (
            not isinstance(local_id, str)
            or not local_id
            or not isinstance(visible, list)
            or not visible
            or any(
                not isinstance(component_id, str)
                or component_id not in components
                for component_id in visible
            )
        ):
            raise PromptAuthorityError("F3 registered F2 visibility is invalid")
        states.append(item)

    plan: list[dict[str, object]] = []
    for state_index, state in enumerate(states):
        local_id = str(state["local_id"])
        visible = [str(item) for item in state["visible_component_local_ids"]]
        next_state = (
            states[state_index + 1]
            if state_index + 1 < len(states)
            else None
        )
        if next_state is not None:
            forward_trigger = max(
                visible,
                key=lambda component_id: (
                    _f3_forward_score(
                        components[component_id],
                        source_state=state,
                        target_state=next_state,
                    ),
                    visible.index(component_id),
                ),
            )
            if len(visible) == 1:
                plan.append(
                    {
                        "position": len(plan),
                        "transition_kind": "forward_transition",
                        "source_state_local_id": local_id,
                        "target_state_local_id": str(next_state["local_id"]),
                        "allowed_trigger_component_local_ids": [
                            forward_trigger
                        ],
                        "required_trigger_component_local_id": forward_trigger,
                    }
                )
                continue
        else:
            forward_trigger = None
        same_candidates = [
            component_id
            for component_id in visible
            if component_id != forward_trigger
        ]
        if not same_candidates:
            raise PromptAuthorityError(
                "F3 same-state trigger is unavailable"
            )
        same_trigger = max(
            same_candidates,
            key=lambda component_id: (
                _f3_same_state_score(
                    components[component_id],
                    source_state=state,
                ),
                -visible.index(component_id),
            ),
        )
        plan.append(
            {
                "position": len(plan),
                "transition_kind": "same_state_work",
                "source_state_local_id": local_id,
                "target_state_local_id": local_id,
                "allowed_trigger_component_local_ids": [same_trigger],
                "required_trigger_component_local_id": same_trigger,
            }
        )
        if next_state is not None and forward_trigger is not None:
            plan.append(
                {
                    "position": len(plan),
                    "transition_kind": "forward_transition",
                    "source_state_local_id": local_id,
                    "target_state_local_id": str(next_state["local_id"]),
                    "allowed_trigger_component_local_ids": [forward_trigger],
                    "required_trigger_component_local_id": forward_trigger,
                }
            )
    return plan


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
        component_order = _required_f1_component_order(input_value)
        payload["required_f1_component_order"] = component_order
        payload["required_f1_component_positions"] = [
            {
                "position": position,
                "component_local_id": component_local_id,
            }
            for position, component_local_id in enumerate(component_order)
        ]
        payload["required_f1_component_order_literal"] = " < ".join(
            f"{position}:{component_local_id}"
            for position, component_local_id in enumerate(component_order)
        )
        payload["instructions"] = [
            *instructions,
            (
                "For every F2 state, first choose the visible component IDs, "
                "then emit them only by scanning "
                "required_f1_component_positions from position 0 upward. The "
                "position numbers in each emitted visible_component_local_ids "
                "array must be strictly increasing. "
                "required_f1_component_order_literal is the exact concrete "
                "order for this call; copy selected IDs from left to right and "
                "never move a feedback component before an earlier submit or "
                "advancement control. Do not emit the position numbers."
            ),
        ]
    if interaction_plan is not None:
        payload["required_interaction_plan"] = interaction_plan
        payload["instructions"] = [
            *instructions,
            (
                "Copy every source state, target state, row position, and "
                "required_trigger_component_local_id from "
                "required_interaction_plan. Each allowed trigger list is a "
                "singleton containing that exact required trigger. Emit "
                "exactly one interaction for every plan row in the same order; "
                "do not substitute a trigger, omit, merge, or add rows."
            ),
        ]
    if acceptance_plan is not None:
        payload["required_acceptance_target_plan"] = acceptance_plan
        payload["instructions"] = [
            *instructions,
            (
                "Emit exactly one acceptance check for every row in "
                "required_acceptance_target_plan and in the same order. Copy "
                "the reference values exactly, but reconstruct every "
                "use_case_refs item and state_ref object in the contract key "
                "order ref_type, ref_id, ref_revision. The canonical prompt "
                "serialization may display those source-object keys in "
                "lexical order; never copy that display order into the output. "
                "Do not select a different state, omit a row, or add a row."
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
    "build_canonical_f3_interaction_plan",
    "build_canonical_f1_f4_prompt",
    "prompt_authority_manifest",
    "validate_canonical_prompt",
]
