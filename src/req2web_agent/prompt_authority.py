"""Project-wide canonical F1-F4 prompt authority.

The authority is shared by development, Phase 4, Phase 5, real execution, and
release runners.  A caller may project different stage-specific input bytes,
but it may not redefine node output contracts, invariants, or instructions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Mapping
from req2web_capabilities import SUPPORTED_COMPONENT_TYPES, INTERACTIVE_COMPONENT_TYPES


PROMPT_AUTHORITY_SCHEMA_VERSION = "req2web.agent.f1_f4_prompt_authority.v1"
PROMPT_SCHEMA_VERSION = "req2web.agent.f1_f4_prompt.v1"
PROMPT_AUTHORITY_REVISION = (
    "negative_action_full_context_section_visibility_english_v21"
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


def _historical_v17_output_contracts() -> dict[str, dict[str, object]]:
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
                "page_title and layout_pattern; every section title and purpose; and every component component_type, section_local_id, label, and purpose are non-empty canonical text",
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
                "for each canonical use case, independently select exactly one state_ref from that use case's supplied ordered eligible target list",
                "every eligible target is an actual supplied F3 target state, reachable from the first supplied F2 state, and included in that use case's deterministic mapping",
                "canonical use-case order is output order, not workflow-time order; selected state positions need not be monotonic across different use cases",
                "never invent an interaction, state, registry identity, or cross-use-case ordering constraint",
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


def _historical_v17_node_instructions() -> dict[str, list[str]]:
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


def _base_output_contracts() -> dict[str, dict[str, object]]:
    contracts = _historical_v17_output_contracts()
    contracts["F1"]["component_type_enum"] = list(SUPPORTED_COMPONENT_TYPES)
    contracts["F1"]["invariants"].append(
        "component_type must be one of component_type_enum; primary_action is an executable button and status_panel is display-only"
    )
    contracts["F3"]["invariants"] = [
        "interactions is a non-empty array with unique non-empty local_id values",
        "source_state_local_id and target_state_local_id name actual supplied F2 states",
        "the first F2 state is initial; subsequent array positions do not prescribe transition order",
        "trigger_component_local_id must be a supported interactive F1 component visible in the exact source state",
        "each source-state and trigger-component pair occurs at most once",
        "refs is []; do not add use-case reference fields",
        "action and user_feedback are non-empty English text",
        "implement the public requirement graph, including branches, return, cancellation, reset and recovery when requested",
        "do not invent shortcuts or artificial self-loops; a terminal state may have zero outgoing interactions",
        "required_interaction_plan enumerates allowed source/trigger/target choices, not mandatory edges",
    ]
    return contracts


def _node_specific_instructions() -> dict[str, list[str]]:
    instructions = _historical_v17_node_instructions()
    instructions["F1"] = [instructions["F1"][0],
        "Use primary_action for each separately named user action and status_panel for informational text. Use only the declared component types; never invent button, text_input, alert or other unsupported type names.",
        "Use exact public action labels when supplied. Do not combine actions with different effects into one component. Do not describe unsupported data operations as implemented.",
        "A reusable action is one F1 component with one section owner. List its local_id in exactly one section; F2 may make that same component visible in several states. Never duplicate a Reset, Back, Cancel, Next, or other shared action across section component_local_ids.",
        "Place actions reused across differently named workflow states in a neutral workflow-actions or navigation section. Do not assign a shared Back, Next, Cancel, Reset, or Restart control to a section whose title names only one state or step, because that section may remain visible when the shared control is reused elsewhere.",
        "Treat public_requirement_literal_contexts as a loss-prevention checklist derived only from the public requirement. Classify each literal from its surrounding sentence. Represent every explicitly required enabled user action as its own primary_action with the exact visible label; represent named visible states through F2 rather than inventing extra F1 action components. If the surrounding sentence says an action must be absent, disabled, unavailable, or must not restart or duplicate a terminal outcome, do not reinterpret that negative requirement as an enabled action.",
        "After the components array is final, derive every section.component_local_ids by filtering the complete components array from left to right for rows whose section_local_id equals that section.local_id. Do not hand-order a section list from workflow chronology. Before returning JSON, concatenate the section lists in sections-array order and compare the result item-for-item with components[].local_id; if they differ, replace the section lists with the filtered lists without changing component content or ownership.",
    ]
    instructions["F2"] = [instructions["F2"][0],
        "Put the actual initial workflow state first. Model all public workflow states, including distinct branches, errors and recovery where requested. Copy exact visible state names from the requirement when supplied.",
        "Visibility determines which controls the user can reach. Show only appropriate actions for each state. Terminal states may contain only status panels. Do not expose actions from an alternative branch or an earlier step unless requested.",
        "Create a distinct internal microstate whenever an action changes which controls are enabled, even when the public visible state name remains unchanged. The two microstates may deliberately have the same name but must have different local_id values and visibility lists. Examples include Ready before and after preparation, In review before and after a checkpoint, and an active branch before and after its prerequisite.",
        "For a numbered walkthrough, use the exact required visible names such as Step 1 and Step 2 as state names. Put domain content such as welcome, safety, or preview in descriptions or status panels; it does not replace the required step name. Include explicit reset, cancelled, failure, completion, and restart targets when the public requirement calls for them.",
        "When Reset is stated for the workflow without a narrower source, keep it visible in every active or terminal state from which a user may need to choose again. Likewise, keep Cancel visible in every active walkthrough step. Do not postpone a general Reset until only the terminal state.",
        "A terminal state must not expose a control whose requirement context says it is absent, disabled, unavailable, or must not restart or duplicate the completed outcome. A negatively specified terminal action may remain an F1 component for structural traceability only if it is omitted from terminal visibility and receives no F3 edge.",
    ]
    instructions["F3"] = [
        "Choose transitions from the public requirement, not from F2 array adjacency. Back/reset may target earlier states; branches may target nonadjacent states. Terminal states need no artificial work action.",
        "Every trigger must be an interactive component visible in its source state; status_panel is display-only. Use distinct triggers for different choices in the same source state.",
        "When an attempt must fail before retry succeeds, represent ready, failure and success as distinct states and expose retry only in the failure state. Visible state feedback must reflect the actual transition.",
        "Emit an interaction for every explicitly required action that changes state or control availability, including preparation, checkpoint, Next, Back, Cancel, Reset, Retry, Confirm, and Restart. Preparation and checkpoint actions target a distinct microstate even if source and target use the same public visible name.",
        "A protected action must not be visible in the precondition source state. Its prerequisite transition must target a later microstate where the protected action becomes visible. Do not claim gating only in action text or feedback; encode it in F2 visibility and F3 edges.",
        "Honor negative terminal-action semantics before choosing edges. When the requirement says a terminal action must be absent or disabled, or that using it must leave one completion unchanged rather than restart or duplicate it, emit no state-changing interaction for that action from the terminal state. Prefer no outgoing edge over inventing a restart transition; a terminal state may have zero outgoing interactions.",
    ]
    return instructions


_PUBLIC_LITERAL = re.compile(r"'([^'\r\n]+)'|\"([^\"\r\n]+)\"")
_PUBLIC_LITERAL_CONTEXT_CHARS = 160


def _public_requirement_literal_contexts(
    input_value: Mapping[str, object],
) -> list[dict[str, object]]:
    """Project quoted public literals with bounded source context.

    This is a deterministic attention aid, not evaluator data or a second
    workflow schema. The values are copied only from the public requirement
    already present in the node input.
    """

    projection = input_value.get("projection")
    if not isinstance(projection, Mapping):
        return []
    requirement_view = projection.get("canonical_b_requirement_view")
    if not isinstance(requirement_view, Mapping):
        return []
    requirement = requirement_view.get("requirement")
    if not isinstance(requirement, str) or not requirement:
        return []
    rows: list[dict[str, object]] = []
    for position, match in enumerate(_PUBLIC_LITERAL.finditer(requirement)):
        literal = match.group(1) if match.group(1) is not None else match.group(2)
        if not literal:
            continue
        left = requirement[
            max(0, match.start() - _PUBLIC_LITERAL_CONTEXT_CHARS):match.start()
        ].strip()
        right = requirement[
            match.end():min(
                len(requirement),
                match.end() + _PUBLIC_LITERAL_CONTEXT_CHARS,
            )
        ].strip()
        rows.append(
            {
                "position": position,
                "literal": literal,
                "left_context": left,
                "right_context": right,
            }
        )
    return rows


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


def _validate_f4_acceptance_target_plan(
    value: object,
) -> list[dict[str, object]]:
    rows = _validate_plan(
        value,
        name="required_acceptance_target_plan",
    )
    for position, row in enumerate(rows):
        if set(row) != {
            "position",
            "use_case_ref",
            "ordered_eligible_state_refs",
        } or type(row["position"]) is not int or row["position"] != position:
            raise PromptAuthorityError(
                "required_acceptance_target_plan row shape or order is invalid"
            )
        use_case_ref = row["use_case_ref"]
        eligible_refs = row["ordered_eligible_state_refs"]
        if (
            not isinstance(use_case_ref, Mapping)
            or set(use_case_ref) != {"ref_type", "ref_id", "ref_revision"}
            or use_case_ref.get("ref_type") != "canonical_b_use_case"
            or not isinstance(use_case_ref.get("ref_id"), str)
            or not use_case_ref.get("ref_id")
            or use_case_ref.get("ref_revision") != "canonical_b.use_case.v1"
            or not isinstance(eligible_refs, list)
            or not eligible_refs
        ):
            raise PromptAuthorityError(
                "required_acceptance_target_plan use-case binding is invalid"
            )
        eligible_ids: list[str] = []
        for state_ref in eligible_refs:
            if (
                not isinstance(state_ref, Mapping)
                or set(state_ref) != {"ref_type", "ref_id", "ref_revision"}
                or state_ref.get("ref_type") != "registry_stable"
                or not isinstance(state_ref.get("ref_id"), str)
                or not state_ref.get("ref_id")
                or state_ref.get("ref_revision") != REGISTRY_REVISION
            ):
                raise PromptAuthorityError(
                    "required_acceptance_target_plan state binding is invalid"
                )
            eligible_ids.append(str(state_ref["ref_id"]))
        if len(eligible_ids) != len(set(eligible_ids)):
            raise PromptAuthorityError(
                "required_acceptance_target_plan contains duplicate states"
            )
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


def _historical_v17_linear_interaction_plan(
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


def build_canonical_f3_interaction_plan(
    *, f1_registered_structure_view: Mapping[str, object],
    f2_registered_state_visibility_view: Mapping[str, object],
) -> list[dict[str, object]]:
    """Return capability choices, without manufacturing requirement semantics."""
    components = f1_registered_structure_view.get("components")
    states = f2_registered_state_visibility_view.get("states")
    if not isinstance(components, list) or not components or not isinstance(states, list) or not states:
        raise PromptAuthorityError("F3 component/state inventory is missing")
    types = {}
    for row in components:
        if not isinstance(row, Mapping):
            raise PromptAuthorityError("F3 component row is invalid")
        local_id = row.get("local_id")
        if not isinstance(local_id, str) or not local_id or local_id in types:
            raise PromptAuthorityError("F3 component identity is invalid")
        if row.get("component_type") not in SUPPORTED_COMPONENT_TYPES:
            raise PromptAuthorityError("F3 component capability is unsupported")
        types[local_id] = row["component_type"]
    if any(not isinstance(row, Mapping) for row in states):
        raise PromptAuthorityError("F3 state row is invalid")
    state_ids = [row.get("local_id") for row in states]
    if any(not isinstance(s, str) or not s for s in state_ids) or len(set(state_ids)) != len(state_ids):
        raise PromptAuthorityError("F3 state identities are invalid")
    plan = []
    for position, row in enumerate(states):
        visible = row.get("visible_component_local_ids")
        if not isinstance(visible, list) or any(v not in types for v in visible) or len(set(visible)) != len(visible):
            raise PromptAuthorityError("F3 visibility is invalid")
        plan.append({
            "position": position, "source_state_local_id": row["local_id"],
            "allowed_trigger_component_local_ids": [v for v in visible if types[v] in INTERACTIVE_COMPONENT_TYPES],
            "allowed_target_state_local_ids": list(state_ids),
            "mandatory_interaction_count": 0,
        })
    return plan


def build_canonical_f4_acceptance_target_plan(
    *,
    canonical_b_use_case_view: Mapping[str, object],
    f2_registered_state_visibility_view: Mapping[str, object],
    f3_registered_interaction_view: Mapping[str, object],
    deterministic_use_case_mapping_view: Mapping[str, object],
) -> list[dict[str, object]]:
    """Build ordered, independent eligible target sets for canonical F4 rows."""

    use_cases = canonical_b_use_case_view.get("use_cases")
    states = f2_registered_state_visibility_view.get("states")
    interactions = f3_registered_interaction_view.get("interactions")
    mappings = deterministic_use_case_mapping_view.get("ordered_mappings")
    if (
        not isinstance(use_cases, list)
        or not use_cases
        or not isinstance(states, list)
        or not states
        or not isinstance(interactions, list)
        or not interactions
        or not isinstance(mappings, list)
        or not mappings
    ):
        raise PromptAuthorityError(
            "F4 eligible-target authority arrays are unavailable"
        )

    use_case_ids: list[str] = []
    for use_case in use_cases:
        if (
            not isinstance(use_case, Mapping)
            or not isinstance(use_case.get("use_case_id"), str)
            or not use_case.get("use_case_id")
        ):
            raise PromptAuthorityError("F4 use-case binding is invalid")
        use_case_ids.append(str(use_case["use_case_id"]))
    if len(use_case_ids) != len(set(use_case_ids)):
        raise PromptAuthorityError("F4 use-case identity is duplicated")

    state_ids: list[str] = []
    for state in states:
        if (
            not isinstance(state, Mapping)
            or not isinstance(state.get("stable_id"), str)
            or not state.get("stable_id")
        ):
            raise PromptAuthorityError("F4 state binding is invalid")
        state_ids.append(str(state["stable_id"]))
    if len(state_ids) != len(set(state_ids)):
        raise PromptAuthorityError("F4 state identity is duplicated")
    state_set = set(state_ids)

    interaction_rows: dict[str, tuple[str, str]] = {}
    adjacency: dict[str, list[str]] = {state_id: [] for state_id in state_ids}
    for interaction in interactions:
        if not isinstance(interaction, Mapping):
            raise PromptAuthorityError("F4 interaction binding is invalid")
        interaction_id = interaction.get("stable_id")
        source_state_id = interaction.get("source_state_stable_id")
        target_state_id = interaction.get("target_state_stable_id")
        if (
            not isinstance(interaction_id, str)
            or not interaction_id
            or not isinstance(source_state_id, str)
            or not isinstance(target_state_id, str)
            or interaction_id in interaction_rows
            or source_state_id not in state_set
            or target_state_id not in state_set
        ):
            raise PromptAuthorityError("F4 interaction binding is invalid")
        interaction_rows[interaction_id] = (
            source_state_id,
            target_state_id,
        )
        adjacency[source_state_id].append(target_state_id)

    reachable = {state_ids[0]}
    frontier = [state_ids[0]]
    while frontier:
        source_state_id = frontier.pop(0)
        for target_state_id in adjacency[source_state_id]:
            if target_state_id not in reachable:
                reachable.add(target_state_id)
                frontier.append(target_state_id)

    mapped_interactions: dict[str, tuple[str, ...]] = {}
    for mapping in mappings:
        if (
            not isinstance(mapping, Mapping)
            or not isinstance(mapping.get("use_case_id"), str)
            or not isinstance(mapping.get("interaction_stable_ids"), list)
        ):
            raise PromptAuthorityError("F4 mapping binding is invalid")
        use_case_id = str(mapping["use_case_id"])
        interaction_ids = tuple(mapping["interaction_stable_ids"])
        if (
            use_case_id in mapped_interactions
            or any(
                not isinstance(interaction_id, str)
                or interaction_id not in interaction_rows
                for interaction_id in interaction_ids
            )
        ):
            raise PromptAuthorityError("F4 mapping binding is invalid")
        mapped_interactions[use_case_id] = interaction_ids
    if list(mapped_interactions) != use_case_ids:
        raise PromptAuthorityError("F4 mapping order drifted")

    plan: list[dict[str, object]] = []
    for position, use_case_id in enumerate(use_case_ids):
        mapped_target_ids = {
            interaction_rows[interaction_id][1]
            for interaction_id in mapped_interactions[use_case_id]
        }
        eligible_state_ids = [
            state_id
            for state_id in state_ids
            if state_id in reachable and state_id in mapped_target_ids
        ]
        if not eligible_state_ids:
            raise PromptAuthorityError(
                "F4 use case has no actual reachable mapped target"
            )
        plan.append(
            {
                "position": position,
                "use_case_ref": {
                    "ref_type": "canonical_b_use_case",
                    "ref_id": use_case_id,
                    "ref_revision": "canonical_b.use_case.v1",
                },
                "ordered_eligible_state_refs": [
                    {
                        "ref_type": "registry_stable",
                        "ref_id": state_id,
                        "ref_revision": REGISTRY_REVISION,
                    }
                    for state_id in eligible_state_ids
                ],
            }
        )
    return _validate_f4_acceptance_target_plan(plan)


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
        acceptance_plan = _validate_f4_acceptance_target_plan(
            required_acceptance_target_plan,
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
    if node_id in {"F1", "F2", "F3"}:
        payload["public_requirement_literal_contexts"] = (
            _public_requirement_literal_contexts(input_value)
        )
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
                "Use required_interaction_plan as a capability boundary only. "
                "For each actual required transition, choose a source row, "
                "one allowed trigger and one allowed target. Emit only edges "
                "supported by the public requirement. A plan row may have "
                "zero or several interactions; each source/trigger pair must "
                "be unique. Do not emit plan metadata in the output."
            ),
        ]
    if acceptance_plan is not None:
        payload["required_acceptance_target_plan"] = acceptance_plan
        payload["instructions"] = [
            *instructions,
            (
                "Emit exactly one acceptance check for every row in "
                "required_acceptance_target_plan and in the same order. Copy "
                "the row's use_case_ref values exactly. Independently choose "
                "exactly one state_ref from that row's "
                "ordered_eligible_state_refs; canonical use-case order does "
                "not require selected state positions to be monotonic. "
                "Reconstruct every use_case_refs item and state_ref object in "
                "the contract key order ref_type, ref_id, ref_revision. The "
                "canonical prompt serialization may display source-object "
                "keys in lexical order; never copy that display order into "
                "the output. Do not choose outside the row's eligible list, "
                "omit a row, or add a row."
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
    "build_canonical_f4_acceptance_target_plan",
    "build_canonical_f1_f4_prompt",
    "prompt_authority_manifest",
    "validate_canonical_prompt",
]
