"""Pre-registered semantic-coverage sidecar for the Qwen3.5-27B recovery pilot.

This module evaluates a *strictly valid* ``ModelSemanticCandidate`` without
altering it.  Pre-route inspection can only remain pending.  A final pass can
only be derived from a live A-07a/A-07b outcome by the final-route adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from req2web_provider.semantic_candidate import ModelSemanticCandidate


QWEN27B_SEMANTIC_GATE_SCHEMA = "req2web.runtime.qwen27b_semantic_coverage_gate.v1"
_CASE_IDS = ("path3-commerce-checkout", "path3-media-analysis")
_REQUIRED_UCS = {
    "path3-commerce-checkout": ("UC-01", "UC-02", "UC-03"),
    "path3-media-analysis": ("UC-01", "UC-02"),
}
_PARSER_ASSEMBLER_STATUS_VALUES = frozenset({"passed", "failed"})
_GENERIC_GATE_STATUS_VALUES = frozenset(
    {"passed", "failed", "pending_existing_route_replay"}
)
_COVERAGE_KEYS = {
    "use_case_id",
    "mapped_component_ids",
    "mapped_interaction_ids",
    "acceptance_check_ids",
    "concept_groups",
    "exclusive_interaction_ids",
    "trigger_visibility",
    "acceptance_visibility",
    "failure_codes",
}
_RULES = {
    "path3-commerce-checkout": {
        "UC-01": (("search", "query"), ("filter", "refine"), ("product", "item", "result"), 2, 2),
        "UC-02": (("cart", "basket"), ("add", "item", "product"), ("checkout", "order"), 2, 2),
        "UC-03": (("checkout", "order", "submit"), ("delivery", "address", "form"), ("invalid", "error", "validation"), ("retry", "correct", "preserve"), 3, 2),
    },
    "path3-media-analysis": {
        "UC-01": (
            ("upload", "file", "photo", "image", "media"),
            ("select", "input", "browse"),
            ("permission", "denied", "error"),
            ("retry", "fallback", "recover"),
            2,
            2,
        ),
        "UC-02": (("analyze", "analysis"), ("result", "output", "insight"), 1, 1),
    },
}


class Qwen27bSemanticGateError(ValueError):
    """Raised for invalid public gate inputs or non-canonical reports."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _contains_group(scope: str, group: tuple[str, ...]) -> bool:
    lower = scope.lower()
    return any(term in lower for term in group)


def _normalise_candidate(candidate: object) -> ModelSemanticCandidate:
    """Reject direct/untrusted objects by rebuilding from their canonical bytes."""
    if type(candidate) is not ModelSemanticCandidate:
        raise Qwen27bSemanticGateError("model_semantic_candidate_type_invalid")
    try:
        raw = candidate.canonical_json_bytes()
        payload = json.loads(raw.decode("utf-8"))
        rebuilt = ModelSemanticCandidate.from_dict(payload)
    except Exception as exc:
        raise Qwen27bSemanticGateError("model_semantic_candidate_reparse_invalid") from exc
    if rebuilt.canonical_json_bytes() != raw:
        raise Qwen27bSemanticGateError("model_semantic_candidate_canonical_reparse_mismatch")
    return rebuilt


@dataclass(frozen=True)
class Qwen27bSemanticCoverageReport:
    """Canonical, replayable local sidecar report; it conveys no quality claim."""

    data: Mapping[str, Any]

    def validate(self) -> None:
        data = self.data
        keys = {
            "schema_version", "case_id", "candidate_sha256", "input_statuses",
            "use_case_coverage", "failure_codes", "decision",
        }
        if not isinstance(data, Mapping) or set(data) != keys:
            raise Qwen27bSemanticGateError("semantic_gate_report_exact_keys_invalid")
        if data["schema_version"] != QWEN27B_SEMANTIC_GATE_SCHEMA or data["case_id"] not in _CASE_IDS:
            raise Qwen27bSemanticGateError("semantic_gate_report_identity_invalid")
        if (
            not isinstance(data["candidate_sha256"], str)
            or len(data["candidate_sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in data["candidate_sha256"])
        ):
            raise Qwen27bSemanticGateError("semantic_gate_report_candidate_hash_invalid")
        statuses = data["input_statuses"]
        if not isinstance(statuses, Mapping) or set(statuses) != {"parser", "assembler", "generic_gate"}:
            raise Qwen27bSemanticGateError("semantic_gate_report_statuses_invalid")
        if (
            statuses["parser"] not in _PARSER_ASSEMBLER_STATUS_VALUES
            or statuses["assembler"] not in _PARSER_ASSEMBLER_STATUS_VALUES
            or statuses["generic_gate"] not in _GENERIC_GATE_STATUS_VALUES
        ):
            raise Qwen27bSemanticGateError("semantic_gate_report_statuses_invalid")
        coverage = data["use_case_coverage"]
        if (
            not isinstance(coverage, list)
            or [row.get("use_case_id") if isinstance(row, Mapping) else None for row in coverage]
            != list(_REQUIRED_UCS[data["case_id"]])
        ):
            raise Qwen27bSemanticGateError("semantic_gate_report_coverage_invalid")
        for row in coverage:
            if set(row) != _COVERAGE_KEYS:
                raise Qwen27bSemanticGateError("semantic_gate_report_coverage_invalid")
            for key in (
                "mapped_component_ids",
                "mapped_interaction_ids",
                "acceptance_check_ids",
                "exclusive_interaction_ids",
                "failure_codes",
            ):
                values = row[key]
                if (
                    not isinstance(values, list)
                    or any(not isinstance(value, str) or not value for value in values)
                    or len(values) != len(set(values))
                ):
                    raise Qwen27bSemanticGateError("semantic_gate_report_coverage_invalid")
            groups = row["concept_groups"]
            if not isinstance(groups, list) or not groups:
                raise Qwen27bSemanticGateError("semantic_gate_report_coverage_invalid")
            for group in groups:
                if (
                    not isinstance(group, Mapping)
                    or set(group) != {"terms", "covered"}
                    or not isinstance(group["terms"], list)
                    or not group["terms"]
                    or any(not isinstance(term, str) or not term for term in group["terms"])
                    or type(group["covered"]) is not bool
                ):
                    raise Qwen27bSemanticGateError("semantic_gate_report_coverage_invalid")
            if row["trigger_visibility"] not in {"passed", "failed"}:
                raise Qwen27bSemanticGateError("semantic_gate_report_coverage_invalid")
            if row["acceptance_visibility"] not in {"passed", "failed"}:
                raise Qwen27bSemanticGateError("semantic_gate_report_coverage_invalid")
        failures = data["failure_codes"]
        if (
            not isinstance(failures, list)
            or any(not isinstance(code, str) or not code for code in failures)
            or failures != sorted(set(failures))
        ):
            raise Qwen27bSemanticGateError("semantic_gate_report_failures_invalid")
        expected = (
            "pending"
            if not failures
            and statuses["generic_gate"] == "pending_existing_route_replay"
            and statuses["parser"] == statuses["assembler"] == "passed"
            else (
                "pass"
                if not failures and all(value == "passed" for value in statuses.values())
                else "fail_closed"
            )
        )
        if data["decision"] != expected:
            raise Qwen27bSemanticGateError("semantic_gate_report_decision_invalid")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return json.loads(self.canonical_bytes().decode("utf-8"))

    def canonical_bytes(self) -> bytes:
        self.validate()
        return _canonical(self.data)

    def sha256(self) -> str:
        return _sha(self.canonical_bytes())

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Qwen27bSemanticCoverageReport":
        if not isinstance(raw, bytes) or not raw:
            raise Qwen27bSemanticGateError("semantic_gate_report_bytes_invalid")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Qwen27bSemanticGateError("semantic_gate_report_bytes_invalid") from exc
        report = cls(value)
        if report.canonical_bytes() != raw:
            raise Qwen27bSemanticGateError("semantic_gate_report_noncanonical")
        return report


def _scope(candidate: ModelSemanticCandidate, use_case_id: str) -> tuple[dict[str, object], str]:
    mapping = next((item for item in candidate.use_case_mappings if item.use_case_id == use_case_id), None)
    if mapping is None:
        return {"mapping": None, "components": [], "interactions": [], "acceptance": [], "states": []}, ""
    sections = {item.stable_id: item for item in candidate.sections}
    components = {item.stable_id: item for item in candidate.components}
    states = {item.stable_id: item for item in candidate.states}
    interactions = {item.stable_id: item for item in candidate.interactions}
    selected_interactions = [interactions[item] for item in mapping.interaction_stable_ids]
    selected_checks = [item for item in candidate.acceptance_checks if use_case_id in item.use_case_ids]
    state_ids = {item.source_state_stable_id for item in selected_interactions} | {item.target_state_stable_id for item in selected_interactions} | {item.state_stable_id for item in selected_checks}
    selected_states = [states[item] for item in sorted(state_ids)]
    selected_sections = [sections[item] for item in mapping.section_stable_ids]
    selected_components = [components[item] for item in mapping.component_stable_ids]
    text_items: list[str] = []
    for item in (*selected_sections, *selected_components, *selected_interactions, *selected_checks, *selected_states):
        text_items.extend(_text(value) for value in vars(item).values() if isinstance(value, str))
    return {
        "mapping": mapping,
        "components": selected_components,
        "interactions": selected_interactions,
        "acceptance": selected_checks,
        "states": selected_states,
    }, " ".join(text_items).lower()


def evaluate_qwen27b_semantic_coverage(
    *,
    case_id: str,
    candidate: ModelSemanticCandidate,
    parser_status: str,
    assembler_status: str,
) -> Qwen27bSemanticCoverageReport:
    """Evaluate pre-route coverage; this entry point can never produce pass."""

    return _evaluate_qwen27b_semantic_coverage(
        case_id=case_id,
        candidate=candidate,
        parser_status=parser_status,
        assembler_status=assembler_status,
        generic_gate_status="pending_existing_route_replay",
    )


def evaluate_qwen27b_semantic_coverage_after_route(
    *,
    case_id: str,
    candidate: ModelSemanticCandidate,
    route_outcome: object,
) -> Qwen27bSemanticCoverageReport:
    """Derive final generic-gate status from a validated A-07 outcome."""

    import req2web_orchestration.model_route as model_route

    if type(route_outcome) not in (
        model_route.TierA07aGateDeliveryOutcome,
        model_route.TierA07bGateDeliveryOutcome,
    ):
        raise Qwen27bSemanticGateError("generic_route_outcome_type_invalid")
    try:
        route_outcome.validate()
    except Exception as exc:
        raise Qwen27bSemanticGateError(
            "generic_route_outcome_live_validation_failed"
        ) from exc
    route_pair = (route_outcome.status, route_outcome.delivery_source)
    generic_gate_status = (
        "passed"
        if route_pair
        in {
            ("first_pass_success", "model_first_pass_v1"),
            ("recovered_success", "model_repaired_v1"),
        }
        else "failed"
    )
    return _evaluate_qwen27b_semantic_coverage(
        case_id=case_id,
        candidate=candidate,
        parser_status="passed",
        assembler_status="passed",
        generic_gate_status=generic_gate_status,
    )


def _evaluate_qwen27b_semantic_coverage(
    *,
    case_id: str,
    candidate: ModelSemanticCandidate,
    parser_status: str,
    assembler_status: str,
    generic_gate_status: str,
) -> Qwen27bSemanticCoverageReport:
    """Apply the frozen recovery-pilot rules without mutating input."""
    if case_id not in _CASE_IDS:
        raise Qwen27bSemanticGateError("case_id_not_registered")
    statuses = {"parser": parser_status, "assembler": assembler_status, "generic_gate": generic_gate_status}
    if (
        parser_status not in _PARSER_ASSEMBLER_STATUS_VALUES
        or assembler_status not in _PARSER_ASSEMBLER_STATUS_VALUES
        or generic_gate_status not in _GENERIC_GATE_STATUS_VALUES
    ):
        raise Qwen27bSemanticGateError("input_status_invalid")
    candidate = _normalise_candidate(candidate)
    mappings = {item.use_case_id: item for item in candidate.use_case_mappings}
    coverage: list[dict[str, Any]] = []
    failures: list[str] = []
    if parser_status != "passed":
        failures.append("parser_not_passed")
    if assembler_status != "passed":
        failures.append("assembler_not_passed")
    if generic_gate_status == "failed":
        failures.append("generic_gate_not_passed")

    all_interaction_sets = {uc: set(mappings[uc].interaction_stable_ids) if uc in mappings else set() for uc in _REQUIRED_UCS[case_id]}
    signatures = [tuple(sorted(all_interaction_sets[uc])) for uc in _REQUIRED_UCS[case_id]]
    if len(set(signatures)) != len(signatures):
        failures.append("use_case_mapping_signatures_identical")

    for use_case_id, requirements in _RULES[case_id].items():
        scoped, text = _scope(candidate, use_case_id)
        mapping = scoped["mapping"]
        components = scoped["components"]
        interactions = scoped["interactions"]
        acceptance = scoped["acceptance"]
        concept_groups = requirements[:-2]
        min_components, min_interactions = requirements[-2:]
        row_failures: list[str] = []
        if mapping is None or not mapping.section_stable_ids or not mapping.component_stable_ids or not mapping.interaction_stable_ids:
            row_failures.append("use_case_mapping_empty")
        if not acceptance:
            row_failures.append("acceptance_coverage_missing")
        group_results = [_contains_group(text, group) for group in concept_groups]
        if not all(group_results):
            row_failures.append("concept_group_missing")
        if len(components) < min_components:
            row_failures.append("component_count_insufficient")
        if len(interactions) < min_interactions:
            row_failures.append("interaction_count_insufficient")
        visible_by_state = {item.stable_id: set(item.visible_component_stable_ids) for item in candidate.states}
        if any(item.trigger_component_stable_id not in visible_by_state.get(item.source_state_stable_id, set()) for item in interactions):
            row_failures.append("trigger_not_visible_in_source_state")
        if any(not visible_by_state.get(item.state_stable_id, set()) for item in acceptance):
            row_failures.append("acceptance_state_visibility_empty")
        other_sets = [all_interaction_sets[other] for other in _REQUIRED_UCS[case_id] if other != use_case_id]
        exclusive = set(mapping.interaction_stable_ids) - set.intersection(*other_sets) if mapping is not None else set()
        if not exclusive:
            row_failures.append("exclusive_interaction_missing")
        failures.extend(f"{use_case_id.lower().replace('-', '_')}_{code}" for code in row_failures)
        coverage.append({
            "use_case_id": use_case_id,
            "mapped_component_ids": list(mapping.component_stable_ids) if mapping else [],
            "mapped_interaction_ids": list(mapping.interaction_stable_ids) if mapping else [],
            "acceptance_check_ids": [item.stable_id for item in acceptance],
            "concept_groups": [{"terms": list(group), "covered": result} for group, result in zip(concept_groups, group_results)],
            "exclusive_interaction_ids": sorted(exclusive),
            "trigger_visibility": "passed" if "trigger_not_visible_in_source_state" not in row_failures else "failed",
            "acceptance_visibility": "passed" if "acceptance_state_visibility_empty" not in row_failures else "failed",
            "failure_codes": row_failures,
        })
    if generic_gate_status == "pending_existing_route_replay" and not failures:
        decision = "pending"
    else:
        decision = "pass" if not failures and all(value == "passed" for value in statuses.values()) else "fail_closed"
    report = Qwen27bSemanticCoverageReport({
        "schema_version": QWEN27B_SEMANTIC_GATE_SCHEMA,
        "case_id": case_id,
        "candidate_sha256": candidate.sha256(),
        "input_statuses": statuses,
        "use_case_coverage": coverage,
        "failure_codes": sorted(set(failures)),
        "decision": decision,
    })
    report.validate()
    return report


__all__ = (
    "QWEN27B_SEMANTIC_GATE_SCHEMA",
    "Qwen27bSemanticCoverageReport",
    "Qwen27bSemanticGateError",
    "evaluate_qwen27b_semantic_coverage",
    "evaluate_qwen27b_semantic_coverage_after_route",
)
