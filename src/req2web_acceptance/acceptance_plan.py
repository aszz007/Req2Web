"""Independent deterministic AcceptancePlan compilation for Stage 3 M1.

This module receives only a validated internal ``RequirementView``.  It does not
inspect candidate PageSpecs, DOM artifacts, retrieval state, or files.  It
freezes *what* must be checked before a later M1 binding/executor slice decides
whether and how those obligations can be executed.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any
import unicodedata

from req2web_rag.validation_signals import SUPPORTED_VALIDATION_VALUES

from .requirement_view import (
    INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION,
    RequirementView,
)


ACCEPTANCE_PLAN_SCHEMA_VERSION = "req2web.acceptance.plan.v1"

_SOURCE_KIND_ORDER = {
    "requirement": 0,
    "target_device": 1,
    "use_case": 2,
    "constraint": 3,
    "validation_signal": 4,
}
_SOURCE_PROVENANCE = {
    "requirement": "requirement_view.requirement",
    "target_device": "requirement_view.requirement.target_device",
    "use_case": "requirement_view.use_cases",
    "constraint": "requirement_view.constraints",
    "validation_signal": "requirement_view.validation_evidence",
}
_CONTEXT_GATE_SOURCE = (
    "requirement_view.requirement.original_requirement+requirement_view.constraints"
)
_CONTEXT_GATE_RULES = {
    "retry_recovery": (
        "acceptance_context_gate:v1:retry_recovery",
        (
            (
                "recovery_or_retry",
                (
                    "retry",
                    "recover",
                    "recovery",
                    "try again",
                    "\u6062\u590d",
                    "\u91cd\u8bd5",
                    "\u4fee\u6539",
                    "\u624b\u52a8",
                ),
            ),
            (
                "error_failure_or_input",
                (
                    "error",
                    "failure",
                    "failed",
                    "invalid",
                    "input",
                    "exception",
                    "\u9519\u8bef",
                    "\u5931\u8d25",
                    "\u8f93\u5165",
                    "\u5f02\u5e38",
                    "\u65e0\u6548",
                ),
            ),
        ),
    ),
    "permission_recovery": (
        "acceptance_context_gate:v1:permission_recovery",
        (
            (
                "recovery_or_retry",
                (
                    "retry",
                    "recover",
                    "recovery",
                    "try again",
                    "\u6062\u590d",
                    "\u91cd\u8bd5",
                    "\u4fee\u6539",
                    "\u624b\u52a8",
                ),
            ),
            (
                "permission_or_denial",
                (
                    "permission",
                    "permissions",
                    "denied",
                    "deny",
                    "access denied",
                    "unauthorized",
                    "forbidden",
                    "\u6743\u9650",
                    "\u62d2\u7edd",
                ),
            ),
        ),
    ),
    "empty_state": (
        "acceptance_context_gate:v1:empty_state",
        (
            (
                "explicit_empty_state",
                (
                    "empty state",
                    "no results",
                    "no result",
                    "no matches",
                    "no match",
                    "no data",
                    "empty list",
                    "\u7a7a\u72b6\u6001",
                    "\u65e0\u5339\u914d",
                    "\u6ca1\u6709\u5339\u914d",
                    "\u65e0\u6570\u636e",
                    "\u6ca1\u6709\u6570\u636e",
                ),
            ),
        ),
    ),
}


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_sha256(value: object, field_name: str) -> str:
    value = _require_text(value, field_name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


def _payload_tuple(payload: dict[str, str]) -> tuple[tuple[str, str], ...]:
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in payload.items()):
        raise ValueError("criterion payload values must be strings")
    return tuple(sorted(payload.items()))


def _payload_dict(payload: tuple[tuple[str, str], ...]) -> dict[str, str]:
    if not isinstance(payload, tuple):
        raise ValueError("criterion.expected_payload must be a tuple")
    result: dict[str, str] = {}
    previous_key: str | None = None
    for index, item in enumerate(payload):
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(f"criterion.expected_payload[{index}] must be a key/value pair")
        key, value = item
        _require_text(key, f"criterion.expected_payload[{index}].key")
        _require_text(value, f"criterion.expected_payload[{index}].value")
        if previous_key is not None and key <= previous_key:
            raise ValueError("criterion.expected_payload keys must be unique and sorted")
        result[key] = value
        previous_key = key
    return result


def _require_exact_payload_keys(payload: dict[str, str], expected_keys: set[str]) -> None:
    if set(payload) != expected_keys:
        raise ValueError("criterion.expected_payload keys do not match criterion kind")


def _normalized_context_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _context_sources(requirement_view: RequirementView) -> tuple[tuple[str, str], ...]:
    return (
        ("original_requirement", requirement_view.requirement.original_requirement),
        *(
            (f"constraint:{constraint.constraint_id}", constraint.description)
            for constraint in requirement_view.constraints
        ),
    )


@dataclass(frozen=True)
class _ContextGateResult:
    rule: str
    source: str
    matches_json: str


def _context_gate_for_signal(
    requirement_view: RequirementView,
    semantic_value: str,
) -> _ContextGateResult | None:
    """Admit a compact signal only when explicit allowed requirement context supports it."""

    rule_spec = _CONTEXT_GATE_RULES.get(semantic_value)
    if rule_spec is None:
        return None
    rule, required_groups = rule_spec
    sources = tuple(
        (source_name, _normalized_context_text(text))
        for source_name, text in _context_sources(requirement_view)
    )
    groups: list[dict[str, object]] = []
    for group_name, terms in required_groups:
        matches = [
            {"source": source_name, "term": term}
            for source_name, source_text in sources
            for term in terms
            if term in source_text
        ]
        if not matches:
            return None
        groups.append({"group": group_name, "matches": matches})
    return _ContextGateResult(
        rule=rule,
        source=_CONTEXT_GATE_SOURCE,
        matches_json=_canonical_json_bytes(groups).decode("utf-8"),
    )


@dataclass(frozen=True)
class RequirementMetadata:
    """Traceability context retained alongside pre-bound requirement obligations."""

    requirement_id: str
    original_requirement: str
    requirement_summary: str
    target_device: str
    task_type: str

    def to_dict(self) -> dict[str, str]:
        return {
            "original_requirement": self.original_requirement,
            "requirement_id": self.requirement_id,
            "requirement_summary": self.requirement_summary,
            "target_device": self.target_device,
            "task_type": self.task_type,
        }


@dataclass(frozen=True)
class AcceptanceCriterion:
    """One immutable requirement-side acceptance obligation.

    ``expected_payload`` is an ordered tuple of string key/value pairs so the
    frozen dataclass has no mutable mapping field.  ``to_dict`` serializes it as
    a normal JSON object for audit artifacts.
    """

    criterion_id: str
    source_kind: str
    source_id: str
    source_provenance: str
    obligation_kind: str
    semantic_kind: str
    must_have: bool
    criticality: str
    expected_payload: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "criticality": self.criticality,
            "criterion_id": self.criterion_id,
            "expected_payload": _payload_dict(self.expected_payload),
            "must_have": self.must_have,
            "obligation_kind": self.obligation_kind,
            "semantic_kind": self.semantic_kind,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "source_provenance": self.source_provenance,
        }


def _criterion_identifier(
    *,
    source_requirement_view_sha256: str,
    source_kind: str,
    source_id: str,
    source_provenance: str,
    obligation_kind: str,
    semantic_kind: str,
    must_have: bool,
    criticality: str,
    expected_payload: tuple[tuple[str, str], ...],
) -> str:
    material = {
        "criticality": criticality,
        "expected_payload": _payload_dict(expected_payload),
        "must_have": must_have,
        "obligation_kind": obligation_kind,
        "schema_version": ACCEPTANCE_PLAN_SCHEMA_VERSION,
        "semantic_kind": semantic_kind,
        "source_id": source_id,
        "source_kind": source_kind,
        "source_provenance": source_provenance,
        "source_requirement_view_sha256": source_requirement_view_sha256,
    }
    return "acceptance-criterion-" + sha256(_canonical_json_bytes(material)).hexdigest()


def _criterion_order_key(criterion: AcceptanceCriterion) -> tuple[int, str, str]:
    return (_SOURCE_KIND_ORDER[criterion.source_kind], criterion.source_id, criterion.criterion_id)


def _validate_context_gate_payload(payload: dict[str, str], semantic_kind: str) -> None:
    _require_exact_payload_keys(
        payload,
        {
            "adapter_rule",
            "context_gate_matches_json",
            "context_gate_rule",
            "context_gate_source",
            "evidence_id",
            "outcome",
            "reference_uri",
            "schema_version",
            "signal_id",
            "source_doc_id",
            "source_field",
            "source_value",
            "value",
        },
    )
    if payload["outcome"] != "candidate":
        raise ValueError("only candidate validation signals can be AcceptancePlan criteria")
    if payload["value"] != semantic_kind or semantic_kind not in SUPPORTED_VALIDATION_VALUES:
        raise ValueError("validation_signal criterion semantic_kind is unsupported")
    expected_rule = _CONTEXT_GATE_RULES.get(semantic_kind)
    if expected_rule is None or payload["context_gate_rule"] != expected_rule[0]:
        raise ValueError("validation_signal context gate rule does not match semantic_kind")
    if payload["context_gate_source"] != _CONTEXT_GATE_SOURCE:
        raise ValueError("validation_signal context gate source is invalid")
    try:
        matches = json.loads(payload["context_gate_matches_json"])
    except json.JSONDecodeError as error:
        raise ValueError("validation_signal context gate matches must be JSON") from error
    if not isinstance(matches, list) or not matches:
        raise ValueError("validation_signal context gate matches must be a non-empty list")
    if _canonical_json_bytes(matches).decode("utf-8") != payload["context_gate_matches_json"]:
        raise ValueError("validation_signal context gate matches must be canonical JSON")


def _validate_criterion(
    criterion: AcceptanceCriterion,
    *,
    source_requirement_view_sha256: str,
) -> None:
    if not isinstance(criterion, AcceptanceCriterion):
        raise ValueError("criteria must contain AcceptanceCriterion instances")
    _require_text(criterion.criterion_id, "criterion.criterion_id")
    _require_text(criterion.source_id, "criterion.source_id")
    if criterion.source_kind not in _SOURCE_KIND_ORDER:
        raise ValueError(f"unsupported criterion.source_kind: {criterion.source_kind}")
    if criterion.source_provenance != _SOURCE_PROVENANCE[criterion.source_kind]:
        raise ValueError("criterion.source_provenance does not match criterion.source_kind")
    if criterion.must_have is not True or criterion.criticality != "must_have":
        raise ValueError("AcceptancePlan v1 criteria must be must_have")

    payload = _payload_dict(criterion.expected_payload)
    if criterion.source_kind == "requirement":
        if criterion.obligation_kind != "requirement_semantics" or criterion.semantic_kind != "requirement_semantics":
            raise ValueError("requirement criterion kinds are invalid")
        _require_exact_payload_keys(payload, {"original_requirement", "requirement_id", "requirement_summary"})
        expected_requirement_id = "requirement-" + sha256(
            payload["original_requirement"].encode("utf-8")
        ).hexdigest()
        if payload["requirement_id"] != expected_requirement_id or criterion.source_id != expected_requirement_id:
            raise ValueError("requirement criterion source identity is invalid")
    elif criterion.source_kind == "target_device":
        if criterion.obligation_kind != "target_device" or criterion.semantic_kind != "target_device":
            raise ValueError("target_device criterion kinds are invalid")
        _require_exact_payload_keys(payload, {"requirement_id", "target_device"})
        if criterion.source_id != payload["requirement_id"]:
            raise ValueError("target_device criterion source_id must equal expected_payload.requirement_id")
    elif criterion.source_kind == "use_case":
        if criterion.obligation_kind != "use_case_outcome" or criterion.semantic_kind != "expected_outcome":
            raise ValueError("use_case criterion kinds are invalid")
        _require_exact_payload_keys(payload, {"actor", "expected_outcome", "goal", "title", "use_case_id"})
        if payload["use_case_id"] != criterion.source_id:
            raise ValueError("use_case criterion source_id must equal expected_payload.use_case_id")
    elif criterion.source_kind == "constraint":
        if criterion.obligation_kind != "constraint" or criterion.semantic_kind != "constraint":
            raise ValueError("constraint criterion kinds are invalid")
        _require_exact_payload_keys(payload, {"description", "source"})
        if payload["source"] != "agent_context.constraints":
            raise ValueError("constraint criterion must retain agent_context.constraints provenance")
        expected_constraint_id = "constraint-" + sha256(payload["description"].encode("utf-8")).hexdigest()
        if criterion.source_id != expected_constraint_id:
            raise ValueError("constraint criterion source_id must match expected_payload.description")
    else:
        if criterion.obligation_kind != "validation_signal":
            raise ValueError("validation_signal criterion obligation kind is invalid")
        _validate_context_gate_payload(payload, criterion.semantic_kind)
        if criterion.source_id != payload["signal_id"]:
            raise ValueError("validation_signal criterion source_id must equal expected_payload.signal_id")

    expected_id = _criterion_identifier(
        source_requirement_view_sha256=source_requirement_view_sha256,
        source_kind=criterion.source_kind,
        source_id=criterion.source_id,
        source_provenance=criterion.source_provenance,
        obligation_kind=criterion.obligation_kind,
        semantic_kind=criterion.semantic_kind,
        must_have=criterion.must_have,
        criticality=criterion.criticality,
        expected_payload=criterion.expected_payload,
    )
    if criterion.criterion_id != expected_id:
        raise ValueError("criterion.criterion_id does not match canonical criterion content")


@dataclass(frozen=True)
class AcceptancePlan:
    """Frozen independent acceptance obligations before PageSpec/DOM binding."""

    schema_version: str
    source_requirement_view_schema_version: str
    source_requirement_view_sha256: str
    requirement_metadata: RequirementMetadata
    criteria: tuple[AcceptanceCriterion, ...]

    def validate(self) -> None:
        if self.schema_version != ACCEPTANCE_PLAN_SCHEMA_VERSION:
            raise ValueError(f"unsupported AcceptancePlan schema: {self.schema_version}")
        if self.source_requirement_view_schema_version != INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION:
            raise ValueError(
                "AcceptancePlan source requirement-view schema is unsupported: "
                f"{self.source_requirement_view_schema_version}"
            )
        _require_sha256(self.source_requirement_view_sha256, "source_requirement_view_sha256")

        metadata = self.requirement_metadata
        if not isinstance(metadata, RequirementMetadata):
            raise ValueError("requirement_metadata must be RequirementMetadata")
        for field_name in (
            "requirement_id",
            "original_requirement",
            "requirement_summary",
            "target_device",
            "task_type",
        ):
            _require_text(getattr(metadata, field_name), f"requirement_metadata.{field_name}")
        expected_requirement_id = "requirement-" + sha256(
            metadata.original_requirement.encode("utf-8")
        ).hexdigest()
        if metadata.requirement_id != expected_requirement_id:
            raise ValueError("requirement_metadata.requirement_id does not match original_requirement")

        if not isinstance(self.criteria, tuple) or not self.criteria:
            raise ValueError("criteria must be a non-empty tuple")
        criterion_ids: set[str] = set()
        for criterion in self.criteria:
            _validate_criterion(
                criterion,
                source_requirement_view_sha256=self.source_requirement_view_sha256,
            )
            if criterion.criterion_id in criterion_ids:
                raise ValueError("criteria must not contain duplicate criterion_id values")
            criterion_ids.add(criterion.criterion_id)
        if tuple(sorted(self.criteria, key=_criterion_order_key)) != self.criteria:
            raise ValueError("criteria must use canonical source order")

    def validate_against(self, requirement_view: RequirementView) -> None:
        """Verify that this plan exactly represents one validated RequirementView."""

        if not isinstance(requirement_view, RequirementView):
            raise TypeError("AcceptancePlan validation input must be a RequirementView")
        requirement_view.validate()
        self.validate()
        expected = _build_acceptance_plan(requirement_view)
        if self != expected:
            raise ValueError("AcceptancePlan does not exactly match its RequirementView source")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "criteria": [criterion.to_dict() for criterion in self.criteria],
            "requirement_metadata": self.requirement_metadata.to_dict(),
            "schema_version": self.schema_version,
            "source_requirement_view_schema_version": self.source_requirement_view_schema_version,
            "source_requirement_view_sha256": self.source_requirement_view_sha256,
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


def _criterion(
    *,
    source_requirement_view_sha256: str,
    source_kind: str,
    source_id: str,
    obligation_kind: str,
    semantic_kind: str,
    expected_payload: dict[str, str],
) -> AcceptanceCriterion:
    payload = _payload_tuple(expected_payload)
    source_provenance = _SOURCE_PROVENANCE[source_kind]
    return AcceptanceCriterion(
        criterion_id=_criterion_identifier(
            source_requirement_view_sha256=source_requirement_view_sha256,
            source_kind=source_kind,
            source_id=source_id,
            source_provenance=source_provenance,
            obligation_kind=obligation_kind,
            semantic_kind=semantic_kind,
            must_have=True,
            criticality="must_have",
            expected_payload=payload,
        ),
        source_kind=source_kind,
        source_id=source_id,
        source_provenance=source_provenance,
        obligation_kind=obligation_kind,
        semantic_kind=semantic_kind,
        must_have=True,
        criticality="must_have",
        expected_payload=payload,
    )


def _build_acceptance_plan(requirement_view: RequirementView) -> AcceptancePlan:
    """Build without re-validating so ``validate_against`` can compare exact content."""

    source_hash = requirement_view.sha256()
    requirement = requirement_view.requirement
    criteria: list[AcceptanceCriterion] = [
        _criterion(
            source_requirement_view_sha256=source_hash,
            source_kind="requirement",
            source_id=requirement.requirement_id,
            obligation_kind="requirement_semantics",
            semantic_kind="requirement_semantics",
            expected_payload={
                "original_requirement": requirement.original_requirement,
                "requirement_id": requirement.requirement_id,
                "requirement_summary": requirement.requirement_summary,
            },
        ),
        _criterion(
            source_requirement_view_sha256=source_hash,
            source_kind="target_device",
            source_id=requirement.requirement_id,
            obligation_kind="target_device",
            semantic_kind="target_device",
            expected_payload={
                "requirement_id": requirement.requirement_id,
                "target_device": requirement.target_device,
            },
        ),
    ]
    for use_case in requirement_view.use_cases:
        criteria.append(
            _criterion(
                source_requirement_view_sha256=source_hash,
                source_kind="use_case",
                source_id=use_case.use_case_id,
                obligation_kind="use_case_outcome",
                semantic_kind="expected_outcome",
                expected_payload={
                    "actor": use_case.actor,
                    "expected_outcome": use_case.expected_outcome,
                    "goal": use_case.goal,
                    "title": use_case.title,
                    "use_case_id": use_case.use_case_id,
                },
            )
        )
    for constraint in requirement_view.constraints:
        criteria.append(
            _criterion(
                source_requirement_view_sha256=source_hash,
                source_kind="constraint",
                source_id=constraint.constraint_id,
                obligation_kind="constraint",
                semantic_kind="constraint",
                expected_payload={
                    "description": constraint.description,
                    "source": constraint.source,
                },
            )
        )

    eligible_by_value: dict[str, list[tuple[object, object, _ContextGateResult]]] = {}
    for evidence in requirement_view.validation_evidence:
        for signal in evidence.signals:
            if signal.outcome != "candidate":
                continue
            gate = _context_gate_for_signal(requirement_view, signal.value)
            if gate is None:
                continue
            eligible_by_value.setdefault(signal.value, []).append((evidence, signal, gate))
    for semantic_value in sorted(eligible_by_value):
        evidence, signal, gate = min(
            eligible_by_value[semantic_value],
            key=lambda item: (item[0].source_doc_id, item[0].evidence_id, item[1].signal_id),
        )
        criteria.append(
            _criterion(
                source_requirement_view_sha256=source_hash,
                source_kind="validation_signal",
                source_id=signal.signal_id,
                obligation_kind="validation_signal",
                semantic_kind=semantic_value,
                expected_payload={
                    "adapter_rule": signal.adapter_rule,
                    "context_gate_matches_json": gate.matches_json,
                    "context_gate_rule": gate.rule,
                    "context_gate_source": gate.source,
                    "evidence_id": evidence.evidence_id,
                    "outcome": signal.outcome,
                    "reference_uri": signal.reference_uri,
                    "schema_version": signal.schema_version,
                    "signal_id": signal.signal_id,
                    "source_doc_id": evidence.source_doc_id,
                    "source_field": signal.source_field,
                    "source_value": signal.source_value,
                    "value": signal.value,
                },
            )
        )
    criteria.sort(key=_criterion_order_key)
    return AcceptancePlan(
        schema_version=ACCEPTANCE_PLAN_SCHEMA_VERSION,
        source_requirement_view_schema_version=requirement_view.schema_version,
        source_requirement_view_sha256=source_hash,
        requirement_metadata=RequirementMetadata(
            requirement_id=requirement.requirement_id,
            original_requirement=requirement.original_requirement,
            requirement_summary=requirement.requirement_summary,
            target_device=requirement.target_device,
            task_type=requirement.task_type,
        ),
        criteria=tuple(criteria),
    )


def compile_acceptance_plan(requirement_view: RequirementView) -> AcceptancePlan:
    """Compile deterministic independent obligations from a validated RequirementView.

    Candidate PageSpec/DOM artifacts are deliberately absent from this signature.
    Binding, browser execution, and criterion status semantics belong to later M1
    slices and must not influence this compiler.
    """

    if not isinstance(requirement_view, RequirementView):
        raise TypeError("compile_acceptance_plan input must be a RequirementView")
    requirement_view.validate()
    plan = _build_acceptance_plan(requirement_view)
    plan.validate()
    return plan
