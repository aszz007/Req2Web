"""Read-only internal requirement projection for deterministic acceptance planning.

The projection consumes an already-created ``AgentContextBundle``.  It never imports
or inspects PageSpec artifacts, and it does not invoke requirement understanding or
retrieval.  It is intentionally an internal adapter rather than a public
RequirementContract or a Provider-visible payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle
from req2web_rag.corpus import ROLE_ORDER
from req2web_rag.validation_signals import validated_compact_validation_signals


INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION = "req2web.acceptance.requirement_view.v1"
_VALIDATION_ROLE = "validation"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _sha256_identifier(prefix: str, value: str) -> str:
    return f"{prefix}-{sha256(value.encode('utf-8')).hexdigest()}"


def _unique_texts(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_require_text(value, f"{field_name}[{index}]") for index, value in enumerate(values))
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicate values")
    return normalized


def _unique_ids(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_require_text(value, f"{field_name}[{index}]") for index, value in enumerate(values))
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must contain unique values")
    return normalized


@dataclass(frozen=True)
class RequirementViewRequirement:
    """Requirement fields that are authoritative for the internal acceptance adapter."""

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
class UseCaseView:
    use_case_id: str
    title: str
    actor: str
    goal: str
    expected_outcome: str

    def to_dict(self) -> dict[str, str]:
        return {
            "actor": self.actor,
            "expected_outcome": self.expected_outcome,
            "goal": self.goal,
            "title": self.title,
            "use_case_id": self.use_case_id,
        }


@dataclass(frozen=True)
class ConstraintView:
    constraint_id: str
    description: str
    source: str = "agent_context.constraints"

    def to_dict(self) -> dict[str, str]:
        return {
            "constraint_id": self.constraint_id,
            "description": self.description,
            "source": self.source,
        }


@dataclass(frozen=True)
class ValidationSignalView:
    signal_id: str
    outcome: str
    value: str
    source_field: str
    source_value: str
    reference_uri: str
    adapter_rule: str
    schema_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "adapter_rule": self.adapter_rule,
            "outcome": self.outcome,
            "reference_uri": self.reference_uri,
            "schema_version": self.schema_version,
            "signal_id": self.signal_id,
            "source_field": self.source_field,
            "source_value": self.source_value,
            "value": self.value,
        }


@dataclass(frozen=True)
class ValidationEvidenceView:
    evidence_id: str
    source_doc_id: str
    title: str
    summary: str
    reference_uris: tuple[str, ...]
    signals: tuple[ValidationSignalView, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "reference_uris": list(self.reference_uris),
            "signals": [signal.to_dict() for signal in self.signals],
            "source_doc_id": self.source_doc_id,
            "summary": self.summary,
            "title": self.title,
        }


@dataclass(frozen=True)
class RequirementView:
    """Frozen internal view used as input to a future independent AcceptancePlan.

    The class intentionally stores no PageSpec-derived fields.  Candidate PageSpec
    IDs may later be bound by an AcceptancePlan compiler, but they never determine
    the requirement obligations captured here.
    """

    schema_version: str
    source_context_schema_version: str
    requirement: RequirementViewRequirement
    use_cases: tuple[UseCaseView, ...]
    constraints: tuple[ConstraintView, ...]
    validation_evidence: tuple[ValidationEvidenceView, ...]

    def validate(self) -> None:
        if self.schema_version != INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION:
            raise ValueError(
                "unsupported internal requirement view schema: "
                f"{self.schema_version}"
            )
        if self.source_context_schema_version != AGENT_BUNDLE_SCHEMA_VERSION:
            raise ValueError(
                "internal requirement view must identify "
                f"{AGENT_BUNDLE_SCHEMA_VERSION} as its source"
            )

        requirement = self.requirement
        _require_text(requirement.requirement_id, "requirement.requirement_id")
        _require_text(requirement.original_requirement, "requirement.original_requirement")
        _require_text(requirement.requirement_summary, "requirement.requirement_summary")
        _require_text(requirement.target_device, "requirement.target_device")
        _require_text(requirement.task_type, "requirement.task_type")
        expected_requirement_id = _sha256_identifier(
            "requirement", requirement.original_requirement
        )
        if requirement.requirement_id != expected_requirement_id:
            raise ValueError("requirement.requirement_id does not match original_requirement")

        use_case_ids = _unique_ids(
            tuple(item.use_case_id for item in self.use_cases), "use_cases.use_case_id"
        )
        if not 2 <= len(use_case_ids) <= 4:
            raise ValueError("use_cases must contain 2-4 items")
        for index, use_case in enumerate(self.use_cases):
            for field_name in ("title", "actor", "goal", "expected_outcome"):
                _require_text(getattr(use_case, field_name), f"use_cases[{index}].{field_name}")

        descriptions = _unique_texts(
            tuple(item.description for item in self.constraints),
            "constraints.description",
        )
        constraint_ids = _unique_ids(
            tuple(item.constraint_id for item in self.constraints), "constraints.constraint_id"
        )
        for constraint, constraint_id, description in zip(
            self.constraints, constraint_ids, descriptions, strict=True
        ):
            _require_text(constraint.source, "constraint.source")
            if constraint.source != "agent_context.constraints":
                raise ValueError("constraint.source must be agent_context.constraints")
            if constraint_id != _sha256_identifier("constraint", description):
                raise ValueError("constraint.constraint_id does not match description")

        evidence_ids = _unique_ids(
            tuple(item.evidence_id for item in self.validation_evidence),
            "validation_evidence.evidence_id",
        )
        source_doc_ids = _unique_ids(
            tuple(item.source_doc_id for item in self.validation_evidence),
            "validation_evidence.source_doc_id",
        )
        for index, evidence in enumerate(self.validation_evidence):
            if evidence.evidence_id != _sha256_identifier(
                "validation-evidence", evidence.source_doc_id
            ):
                raise ValueError(
                    "validation_evidence.evidence_id does not match source_doc_id"
                )
            _require_text(evidence.title, f"validation_evidence[{index}].title")
            _require_text(evidence.summary, f"validation_evidence[{index}].summary")
            reference_uris = _unique_texts(
                evidence.reference_uris,
                f"validation_evidence[{index}].reference_uris",
            )
            if tuple(sorted(reference_uris)) != reference_uris:
                raise ValueError(
                    f"validation_evidence[{index}].reference_uris must be sorted"
                )
            for signal_index, signal in enumerate(evidence.signals):
                _require_text(
                    signal.signal_id,
                    f"validation_evidence[{index}].signals[{signal_index}].signal_id",
                )
                for field_name in (
                    "outcome",
                    "value",
                    "source_field",
                    "source_value",
                    "reference_uri",
                    "adapter_rule",
                    "schema_version",
                ):
                    _require_text(
                        getattr(signal, field_name),
                        f"validation_evidence[{index}].signals[{signal_index}].{field_name}",
                    )
                if signal.reference_uri not in reference_uris:
                    raise ValueError(
                        f"validation_evidence[{index}] signal reference_uri must belong to reference_uris"
                    )
                validation_result = {
                    "doc_id": evidence.source_doc_id,
                    "references": [
                        {"kind": "issue", "uri": uri} for uri in evidence.reference_uris
                    ],
                    "validation_signals": [signal.to_dict()],
                }
                if validated_compact_validation_signals(validation_result) != [signal.to_dict()]:
                    raise ValueError(
                        f"validation_evidence[{index}].signals[{signal_index}] is not accepted by the validation signal adapter"
                    )

        _unique_ids(
            tuple(
                signal.signal_id
                for evidence in self.validation_evidence
                for signal in evidence.signals
            ),
            "validation_evidence.signals.signal_id",
        )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "constraints": [constraint.to_dict() for constraint in self.constraints],
            "requirement": self.requirement.to_dict(),
            "schema_version": self.schema_version,
            "source_context_schema_version": self.source_context_schema_version,
            "use_cases": [use_case.to_dict() for use_case in self.use_cases],
            "validation_evidence": [evidence.to_dict() for evidence in self.validation_evidence],
        }

    def canonical_json_bytes(self) -> bytes:
        return (
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


def _project_validation_signal(
    raw_signal: object, *, evidence_index: int, signal_index: int
) -> ValidationSignalView:
    signal = _require_mapping(
        raw_signal,
        f"validation evidence {evidence_index} signal {signal_index}",
    )
    required_fields = (
        "signal_id",
        "outcome",
        "value",
        "source_field",
        "source_value",
        "reference_uri",
        "adapter_rule",
        "schema_version",
    )
    return ValidationSignalView(
        **{
            field_name: _require_text(
                signal.get(field_name),
                f"validation evidence {evidence_index} signal {signal_index}.{field_name}",
            )
            for field_name in required_fields
        }
    )


def _project_validation_evidence(context: AgentContextBundle) -> tuple[ValidationEvidenceView, ...]:
    records = context.retrieval_results.get(_VALIDATION_ROLE)
    if not isinstance(records, list):
        raise ValueError("AgentContext validation retrieval results must be a list")

    evidence: list[ValidationEvidenceView] = []
    for evidence_index, raw_record in enumerate(records):
        record = _require_mapping(raw_record, f"validation retrieval result {evidence_index}")
        role = _require_text(record.get("role"), f"validation retrieval result {evidence_index}.role")
        if role != _VALIDATION_ROLE:
            raise ValueError(
                f"validation retrieval result {evidence_index} has an invalid role: {role}"
            )
        source_doc_id = _require_text(
            record.get("doc_id"), f"validation retrieval result {evidence_index}.doc_id"
        )
        raw_references = record.get("references", ())
        if not isinstance(raw_references, list):
            raise ValueError(
                f"validation retrieval result {evidence_index}.references must be a list"
            )
        reference_uris = tuple(
            sorted(
                _require_text(
                    _require_mapping(
                        reference,
                        f"validation retrieval result {evidence_index}.references[{reference_index}]",
                    ).get("uri"),
                    f"validation retrieval result {evidence_index}.references[{reference_index}].uri",
                )
                for reference_index, reference in enumerate(raw_references)
            )
        )
        accepted_signals = validated_compact_validation_signals(dict(record))
        signals = tuple(
            _project_validation_signal(
                raw_signal,
                evidence_index=evidence_index,
                signal_index=signal_index,
            )
            for signal_index, raw_signal in enumerate(accepted_signals)
        )
        evidence.append(
            ValidationEvidenceView(
                evidence_id=_sha256_identifier("validation-evidence", source_doc_id),
                source_doc_id=source_doc_id,
                title=_require_text(
                    record.get("title"),
                    f"validation retrieval result {evidence_index}.title",
                ),
                summary=_require_text(
                    record.get("summary"),
                    f"validation retrieval result {evidence_index}.summary",
                ),
                reference_uris=reference_uris,
                signals=signals,
            )
        )

    evidence.sort(key=lambda item: item.source_doc_id)
    return tuple(evidence)


def project_requirement_view(context: AgentContextBundle) -> RequirementView:
    """Project a validated ``AgentContextBundle`` into an immutable acceptance input.

    The function validates and reads the supplied object only.  It neither calls a
    requirement provider nor a retriever, and it has no PageSpec dependency.
    """

    if not isinstance(context, AgentContextBundle):
        raise TypeError("project_requirement_view input must be an AgentContextBundle")
    context.validate()
    if context.schema_version != AGENT_BUNDLE_SCHEMA_VERSION:
        raise ValueError(
            "unsupported AgentContext schema for internal requirement view: "
            f"{context.schema_version}"
        )
    if tuple(context.retrieval_results) != ROLE_ORDER:
        raise ValueError("AgentContext retrieval result roles must use the canonical order")

    original_requirement = _require_text(
        context.original_requirement, "context.original_requirement"
    )
    constraints = _unique_texts(tuple(context.constraints), "context.constraints")
    use_case_ids = _unique_ids(
        tuple(item.use_case_id for item in context.use_cases), "context.use_cases.use_case_id"
    )
    use_cases = tuple(
        UseCaseView(
            use_case_id=use_case_id,
            title=_require_text(use_case.title, f"context.use_cases[{index}].title"),
            actor=_require_text(use_case.actor, f"context.use_cases[{index}].actor"),
            goal=_require_text(use_case.goal, f"context.use_cases[{index}].goal"),
            expected_outcome=_require_text(
                use_case.expected_outcome,
                f"context.use_cases[{index}].expected_outcome",
            ),
        )
        for index, (use_case_id, use_case) in enumerate(
            zip(use_case_ids, context.use_cases, strict=True)
        )
    )

    view = RequirementView(
        schema_version=INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION,
        source_context_schema_version=context.schema_version,
        requirement=RequirementViewRequirement(
            requirement_id=_sha256_identifier("requirement", original_requirement),
            original_requirement=original_requirement,
            requirement_summary=_require_text(
                context.requirement_summary, "context.requirement_summary"
            ),
            target_device=_require_text(context.target_device, "context.target_device"),
            task_type=_require_text(context.task_type, "context.task_type"),
        ),
        use_cases=use_cases,
        constraints=tuple(
            ConstraintView(
                constraint_id=_sha256_identifier("constraint", description),
                description=description,
            )
            for description in constraints
        ),
        validation_evidence=_project_validation_evidence(context),
    )
    view.validate()
    return view
