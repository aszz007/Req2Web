"""Read-only Inspector fact protocol for the deterministic G0 evidence route.

This module intentionally projects only existing, identity-validated artifacts.
It does not infer semantics, invoke a Provider, or adapt M1 evaluation artifacts
into model-audit facts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import re
from typing import Any, Iterable

from req2web_generation import (
    GUIDED_PAGE_SPEC_BUILD_RESULT_SCHEMA_VERSION,
    PAGE_SPEC_SCHEMA_VERSION,
    RETRIEVAL_GUIDANCE_SCHEMA_VERSION,
    RETRIEVAL_INFLUENCE_REPORT_SCHEMA_VERSION,
    GuidedPageSpecBuildResult,
    PageSpec,
    RetrievalGuidance,
    RetrievalInfluenceReport,
)
from req2web_rag.corpus import ROLE_ORDER


INSPECTOR_FACT_SET_SCHEMA_VERSION = "req2web.inspector.fact_set.v1"
INSPECTOR_FACT_UNAVAILABLE_SCHEMA_VERSION = "req2web.inspector.fact_unavailable.v1"

RUN_GROUPS = frozenset({"G0", "G1", "G2"})
G0_SOURCE_KINDS = frozenset(
    {
        "guided_retrieval",
        "guided_agent_context_fallback",
        "retrieval_influence_v1",
    }
)
MODEL_UNTRUSTED_INPUT_KINDS = frozenset(
    {
        "candidate_attribution_edge_set",
        "candidate_decision_enrichment",
        "local_identity_scaffold",
        "provider_claim",
        "provider_prompt",
        "provider_log",
        "evaluator_only_gold",
    }
)

_FIELD_NAMES_BY_ENTITY_KIND: dict[str, frozenset[str]] = {
    "page": frozenset(
        {
            "title",
            "summary",
            "target_device",
            "page_type",
            "layout.pattern",
            "layout.section_order",
        }
    ),
    "section": frozenset({"title", "purpose", "component_ids", "use_case_ids"}),
    "component": frozenset({"section_id", "component_type", "label", "purpose"}),
    "state": frozenset({"name", "description", "visible_component_ids"}),
    "interaction": frozenset(
        {
            "trigger_component_id",
            "source_state_id",
            "action",
            "target_state_id",
            "user_feedback",
            "use_case_ids",
        }
    ),
    "constraint": frozenset({"description", "source"}),
    "acceptance_check": frozenset({"description", "use_case_ids", "state_id"}),
}
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class InspectorFactError(ValueError):
    """Raised when an Inspector source cannot be used as a verified fact."""


class InspectorFactSourceIsolationError(InspectorFactError):
    """Raised for a mixed group or an unapproved fact source."""


@dataclass(frozen=True)
class InspectorSourceRef:
    """A content-hashed reference to one allowed, local G0 source artifact."""

    source_ref_id: str
    run_group: str
    source_kind: str
    artifact_schema_version: str
    artifact_id: str
    artifact_sha256: str
    source_id: str
    role: str | None = None
    guidance_id: str | None = None
    doc_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("source_ref_id", None)
        return payload

    def validate(self) -> None:
        _require_non_empty(self.source_ref_id, "source_ref_id")
        _require_run_group(self.run_group)
        _require_non_empty(self.source_kind, "source_kind")
        _require_non_empty(self.artifact_schema_version, "artifact_schema_version")
        _require_non_empty(self.artifact_id, "artifact_id")
        _require_non_empty(self.source_id, "source_id")
        if not _HASH_RE.fullmatch(self.artifact_sha256):
            raise InspectorFactError("artifact_sha256 must be a lowercase SHA-256 hex digest")
        for name in ("role", "guidance_id", "doc_id"):
            value = getattr(self, name)
            if value is not None:
                _require_non_empty(value, name)
        if self.run_group != "G0" or self.source_kind not in G0_SOURCE_KINDS:
            raise InspectorFactSourceIsolationError("Inspector source references must be verified G0 sources")
        if self.source_kind == "guided_retrieval":
            if self.artifact_schema_version != RETRIEVAL_GUIDANCE_SCHEMA_VERSION:
                raise InspectorFactError("guided retrieval source has the wrong artifact schema")
            if self.role not in ROLE_ORDER or not self.guidance_id or not self.doc_id:
                raise InspectorFactError("guided retrieval source requires role, guidance_id, and doc_id")
            if self.source_id != self.guidance_id:
                raise InspectorFactError("guided retrieval source_id must equal guidance_id")
        elif self.source_kind == "guided_agent_context_fallback":
            if self.artifact_schema_version != GUIDED_PAGE_SPEC_BUILD_RESULT_SCHEMA_VERSION:
                raise InspectorFactError("agent-context fallback source has the wrong artifact schema")
            if self.role != "context" or self.guidance_id is not None or self.doc_id is not None:
                raise InspectorFactError("agent-context fallback source has invalid guidance fields")
        elif self.source_kind == "retrieval_influence_v1":
            if self.artifact_schema_version != RETRIEVAL_INFLUENCE_REPORT_SCHEMA_VERSION:
                raise InspectorFactError("retrieval influence source has the wrong artifact schema")
            if self.artifact_id != self.source_id:
                raise InspectorFactError("retrieval influence source_id must equal artifact_id")
            if self.role is not None or self.guidance_id is not None or self.doc_id is not None:
                raise InspectorFactError("retrieval influence source must not carry guidance fields")
        else:
            raise InspectorFactSourceIsolationError("unverified Inspector source kind")
        expected = _stable_id("inspector-source", self.to_payload())
        if self.source_ref_id != expected:
            raise InspectorFactError("source_ref_id does not match canonical payload")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class InspectorInfluenceCheckSummary:
    """Immutable status-only summary of one RetrievalInfluenceReport check."""

    check_summary_id: str
    check_id: str
    category: str
    status: str

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("check_summary_id", None)
        return payload

    def validate(self) -> None:
        for name in ("check_summary_id", "check_id", "category", "status"):
            _require_non_empty(getattr(self, name), name)
        if self.status not in {"pass", "fail"}:
            raise InspectorFactError("influence check summaries require pass/fail status")
        if self.check_summary_id != _stable_id("inspector-influence-check", self.to_payload()):
            raise InspectorFactError("check_summary_id does not match canonical payload")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class InspectorTraceLink:
    """An exact PageSpec entity/field adoption link; it is not an acceptance result."""

    trace_link_id: str
    decision_id: str
    entity_id: str
    field_path: str
    source_ref_id: str

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("trace_link_id", None)
        return payload

    def validate(self) -> None:
        for name in ("trace_link_id", "decision_id", "entity_id", "field_path", "source_ref_id"):
            _require_non_empty(getattr(self, name), name)
        if self.trace_link_id != _stable_id("inspector-trace", self.to_payload()):
            raise InspectorFactError("trace_link_id does not match canonical payload")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class InspectorFact:
    """One recorded GuidanceDecision disposition and its verified G0 links."""

    fact_id: str
    run_group: str
    decision_id: str
    disposition: str
    source_ref_id: str
    verification_source_ref_id: str
    influence_trace_status: str
    trace_link_ids: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("fact_id", None)
        return payload

    def validate(self) -> None:
        _require_non_empty(self.fact_id, "fact_id")
        _require_run_group(self.run_group)
        for name in (
            "decision_id",
            "disposition",
            "source_ref_id",
            "verification_source_ref_id",
            "influence_trace_status",
        ):
            _require_non_empty(getattr(self, name), name)
        if self.disposition not in {"adopted", "ignored", "fallback"}:
            raise InspectorFactError("Inspector facts require a real GuidanceDecision disposition")
        if self.influence_trace_status != "trace_matched":
            raise InspectorFactError("Inspector facts require a matched influence trace")
        if not isinstance(self.trace_link_ids, tuple):
            raise InspectorFactError("trace_link_ids must be immutable tuples")
        if len(self.trace_link_ids) != len(set(self.trace_link_ids)):
            raise InspectorFactError("trace_link_ids must be unique")
        if tuple(sorted(self.trace_link_ids)) != self.trace_link_ids:
            raise InspectorFactError("trace_link_ids must be sorted")
        if self.fact_id != _stable_id("inspector-fact", self.to_payload()):
            raise InspectorFactError("fact_id does not match canonical payload")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class InspectorFactSet:
    """A single-run-group, immutable collection of verified Inspector facts."""

    fact_set_id: str
    run_group: str
    page_id: str
    guidance_bundle_id: str
    guided_build_result_id: str
    retrieval_influence_report_id: str
    guidance_sha256: str
    guided_build_result_sha256: str
    page_spec_sha256: str
    retrieval_influence_report_sha256: str
    retrieval_influence_report_passed: bool
    influence_checks: tuple[InspectorInfluenceCheckSummary, ...]
    source_refs: tuple[InspectorSourceRef, ...]
    trace_links: tuple[InspectorTraceLink, ...]
    facts: tuple[InspectorFact, ...]
    schema_version: str = INSPECTOR_FACT_SET_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("fact_set_id", None)
        return payload

    def validate(self) -> None:
        if self.schema_version != INSPECTOR_FACT_SET_SCHEMA_VERSION:
            raise InspectorFactError("unsupported Inspector fact set schema")
        _require_run_group(self.run_group)
        for name in (
            "fact_set_id",
            "page_id",
            "guidance_bundle_id",
            "guided_build_result_id",
            "retrieval_influence_report_id",
        ):
            _require_non_empty(getattr(self, name), name)
        for name in (
            "guidance_sha256",
            "guided_build_result_sha256",
            "page_spec_sha256",
            "retrieval_influence_report_sha256",
        ):
            if not _HASH_RE.fullmatch(getattr(self, name)):
                raise InspectorFactError(f"{name} must be a lowercase SHA-256 hex digest")
        if not isinstance(self.retrieval_influence_report_passed, bool):
            raise InspectorFactError("retrieval_influence_report_passed must be a boolean")
        for name in ("influence_checks", "source_refs", "trace_links", "facts"):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                raise InspectorFactError(f"{name} must be immutable tuples")
        if not self.influence_checks or not self.source_refs or not self.facts:
            raise InspectorFactError("Inspector fact sets require influence checks, source references, and facts")
        _validate_sorted_unique(self.influence_checks, "check_summary_id", "influence_checks")
        _validate_sorted_unique(self.source_refs, "source_ref_id", "source_refs")
        _validate_sorted_unique(self.trace_links, "trace_link_id", "trace_links")
        _validate_sorted_unique(self.facts, "fact_id", "facts")
        check_ids = [item.check_id for item in self.influence_checks]
        if len(check_ids) != len(set(check_ids)):
            raise InspectorFactError("influence check IDs must be unique")
        if self.retrieval_influence_report_passed != all(
            item.status == "pass" for item in self.influence_checks
        ):
            raise InspectorFactError(
                "retrieval_influence_report_passed does not match stored influence check statuses"
            )
        for item in self.influence_checks:
            item.validate()
        for item in self.source_refs:
            item.validate()
        for item in self.trace_links:
            item.validate()
        for item in self.facts:
            item.validate()
        validate_source_isolation(self.run_group, self.source_refs, self.facts)
        sources_by_id = {item.source_ref_id: item for item in self.source_refs}
        links_by_id = {item.trace_link_id: item for item in self.trace_links}
        facts_by_decision = {item.decision_id: item for item in self.facts}
        if len(facts_by_decision) != len(self.facts):
            raise InspectorFactError("Inspector fact decisions must be unique")
        link_reference_count = {item.trace_link_id: 0 for item in self.trace_links}
        decision_source_use = {item.source_ref_id: 0 for item in self.source_refs}
        verification_source_use = {item.source_ref_id: 0 for item in self.source_refs}
        for link in self.trace_links:
            if link.source_ref_id not in sources_by_id:
                raise InspectorFactError("Inspector trace link references an unknown source")
            if link.decision_id not in facts_by_decision:
                raise InspectorFactError("Inspector trace link references an unknown decision")
        expected_decision_source_kinds = {
            "adopted": {"guided_retrieval"},
            "ignored": {"guided_retrieval"},
            # Current GuidedPageSpecBuildResult v1 legitimately records both
            # agent-context and retrieval-guidance fallback decisions.  The
            # Inspector must preserve that source truth rather than relabel it.
            "fallback": {"guided_retrieval", "guided_agent_context_fallback"},
        }
        for fact in self.facts:
            decision_source = sources_by_id.get(fact.source_ref_id)
            verification_source = sources_by_id.get(fact.verification_source_ref_id)
            if decision_source is None or verification_source is None:
                raise InspectorFactError("Inspector fact references an unknown source")
            expected_source_kinds = expected_decision_source_kinds[fact.disposition]
            if decision_source.source_kind not in expected_source_kinds:
                allowed = ", ".join(sorted(expected_source_kinds))
                raise InspectorFactError(
                    f"Inspector fact disposition {fact.disposition} requires one of: {allowed}"
                )
            if verification_source.source_kind != "retrieval_influence_v1":
                raise InspectorFactError("Inspector fact verification source must be retrieval_influence_v1")
            decision_source_use[fact.source_ref_id] += 1
            verification_source_use[fact.verification_source_ref_id] += 1
            for link_id in fact.trace_link_ids:
                link = links_by_id.get(link_id)
                if link is None or link.decision_id != fact.decision_id:
                    raise InspectorFactError("Inspector fact has an invalid entity-field link")
                if link.source_ref_id != fact.source_ref_id:
                    raise InspectorFactError("Inspector fact link source does not match its decision source")
                link_reference_count[link_id] += 1
        orphaned = [link_id for link_id, count in link_reference_count.items() if count != 1]
        if orphaned:
            raise InspectorFactError("Inspector trace links must be referenced exactly once by their decision fact")
        unused_decision_sources = [
            source_id
            for source_id, count in decision_source_use.items()
            if sources_by_id[source_id].source_kind in {"guided_retrieval", "guided_agent_context_fallback"}
            and count == 0
        ]
        if unused_decision_sources:
            raise InspectorFactError("Inspector fact set contains an unused decision source reference")
        influence_sources = [
            source_id
            for source_id, source in sources_by_id.items()
            if source.source_kind == "retrieval_influence_v1"
        ]
        if len(influence_sources) != 1 or verification_source_use[influence_sources[0]] != len(self.facts):
            raise InspectorFactError("the retrieval influence verification source must be used by every fact")
        if self.fact_set_id != _stable_id("inspector-fact-set", self.to_payload()):
            raise InspectorFactError("fact_set_id does not match canonical payload")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class InspectorFactUnavailable:
    """A structured fail-closed result for model groups before M3/D14 exists."""

    error_id: str
    run_group: str
    status: str
    error_code: str
    missing_artifacts: tuple[str, ...]
    rejected_input_kinds: tuple[str, ...]
    schema_version: str = INSPECTOR_FACT_UNAVAILABLE_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("error_id", None)
        return payload

    def validate(self) -> None:
        if self.schema_version != INSPECTOR_FACT_UNAVAILABLE_SCHEMA_VERSION:
            raise InspectorFactError("unsupported Inspector unavailable schema")
        if self.run_group not in {"G1", "G2"}:
            raise InspectorFactError("model unavailable results are limited to G1/G2")
        if self.status != "fail_closed" or self.error_code != "model_audit_bundle_unavailable":
            raise InspectorFactError("model unavailable result has an invalid fail-closed status")
        if self.missing_artifacts != ("model_candidate_audit_record", "d14_evaluation_bundle"):
            raise InspectorFactError("model unavailable result has unexpected missing artifacts")
        if not isinstance(self.missing_artifacts, tuple) or len(self.missing_artifacts) != len(set(self.missing_artifacts)):
            raise InspectorFactError("missing_artifacts must be a unique immutable tuple")
        if (
            not isinstance(self.rejected_input_kinds, tuple)
            or tuple(sorted(self.rejected_input_kinds)) != self.rejected_input_kinds
            or len(self.rejected_input_kinds) != len(set(self.rejected_input_kinds))
        ):
            raise InspectorFactError("rejected_input_kinds must be a sorted unique immutable tuple")
        if any(item not in MODEL_UNTRUSTED_INPUT_KINDS for item in self.rejected_input_kinds):
            raise InspectorFactError("model unavailable result contains an unsupported rejection label")
        if self.error_id != _stable_id("inspector-fact-unavailable", self.to_payload()):
            raise InspectorFactError("error_id does not match canonical payload")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


def canonical_json_bytes(value: Any) -> bytes:
    """Return canonical UTF-8 JSON for a payload or one Inspector artifact."""

    if hasattr(value, "to_payload"):
        value = value.to_payload()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the full SHA-256 of canonical UTF-8 JSON."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def page_spec_field_path(page_spec: PageSpec, entity_id: str, field_name: str) -> str:
    """Resolve one exact PageSpec v1 entity/field pair to its frozen path grammar.

    The emitted grammar is ``page.<field>``, ``section[<id>].<field>``,
    ``component[<id>].<field>``, ``state[<id>].<field>``,
    ``interaction[<id>].<field>``, ``constraint[<id>].<field>``, and
    ``acceptance_check[<id>].<field>``.  It performs no text matching.
    """

    if not isinstance(page_spec, PageSpec):
        raise TypeError("page_spec must be a PageSpec")
    page_spec.validate()
    _require_non_empty(entity_id, "entity_id")
    _require_non_empty(field_name, "field_name")
    entity_kind = _page_spec_entity_kind(page_spec, entity_id)
    if field_name not in _FIELD_NAMES_BY_ENTITY_KIND[entity_kind]:
        raise InspectorFactError(
            f"PageSpec {entity_kind} field is not part of the frozen v1 grammar: {field_name}"
        )
    if entity_kind == "page":
        return f"page.{field_name}"
    return f"{entity_kind}[{entity_id}].{field_name}"


def project_g0_inspector_facts(
    guidance: RetrievalGuidance,
    guided_build_result: GuidedPageSpecBuildResult,
    page_spec: PageSpec,
    retrieval_influence_report: RetrievalInfluenceReport,
) -> InspectorFactSet:
    """Project real G0 guided artifacts into a verified, read-only fact set."""

    _validate_g0_artifacts(guidance, guided_build_result, page_spec, retrieval_influence_report)
    guidance_hash = canonical_sha256(guidance.to_dict())
    build_hash = canonical_sha256(guided_build_result.to_dict())
    page_hash = canonical_sha256(page_spec.to_dict())
    influence_hash = canonical_sha256(retrieval_influence_report.to_dict())
    influence_ref = _source_ref(
        run_group="G0",
        source_kind="retrieval_influence_v1",
        artifact_schema_version=RETRIEVAL_INFLUENCE_REPORT_SCHEMA_VERSION,
        artifact_id=retrieval_influence_report.report_id,
        artifact_sha256=influence_hash,
        source_id=retrieval_influence_report.report_id,
    )
    refs_by_id: dict[str, InspectorSourceRef] = {influence_ref.source_ref_id: influence_ref}
    trace_by_decision = {
        item["decision_id"]: item for item in retrieval_influence_report.decision_traceability
    }
    facts: list[InspectorFact] = []
    trace_links: list[InspectorTraceLink] = []
    influence_checks = tuple(
        sorted(
            (_influence_check_summary(item.check_id, item.category, item.status) for item in retrieval_influence_report.checks),
            key=lambda item: item.check_summary_id,
        )
    )
    for disposition, decision in _ordered_decisions(guided_build_result):
        trace = trace_by_decision[decision.decision_id]
        _validate_influence_trace(decision, disposition, trace)
        source_ref = _g0_decision_source_ref(
            guidance=guidance,
            guided_build_result=guided_build_result,
            guidance_sha256=guidance_hash,
            build_sha256=build_hash,
            decision=decision,
        )
        refs_by_id[source_ref.source_ref_id] = source_ref
        decision_link_ids: list[str] = []
        for affected in decision.affected_fields:
            field_path = page_spec_field_path(page_spec, affected.entity_id, affected.field_name)
            link = _trace_link(
                decision_id=decision.decision_id,
                entity_id=affected.entity_id,
                field_path=field_path,
                source_ref_id=source_ref.source_ref_id,
            )
            trace_links.append(link)
            decision_link_ids.append(link.trace_link_id)
        facts.append(
            _fact(
                decision_id=decision.decision_id,
                disposition=disposition,
                source_ref_id=source_ref.source_ref_id,
                verification_source_ref_id=influence_ref.source_ref_id,
                trace_link_ids=tuple(sorted(decision_link_ids)),
            )
        )
    fact_set = InspectorFactSet(
        fact_set_id="",
        run_group="G0",
        page_id=page_spec.page_id,
        guidance_bundle_id=guidance.guidance_bundle_id,
        guided_build_result_id=guided_build_result.build_result_id,
        retrieval_influence_report_id=retrieval_influence_report.report_id,
        guidance_sha256=guidance_hash,
        guided_build_result_sha256=build_hash,
        page_spec_sha256=page_hash,
        retrieval_influence_report_sha256=influence_hash,
        retrieval_influence_report_passed=retrieval_influence_report.passed,
        influence_checks=influence_checks,
        source_refs=tuple(sorted(refs_by_id.values(), key=lambda item: item.source_ref_id)),
        trace_links=tuple(sorted(trace_links, key=lambda item: item.trace_link_id)),
        facts=tuple(sorted(facts, key=lambda item: item.fact_id)),
    )
    fact_set = replace(fact_set, fact_set_id=_stable_id("inspector-fact-set", fact_set.to_payload()))
    fact_set.validate()
    return fact_set


def project_model_inspector_facts(
    run_group: str,
    *,
    model_candidate_audit_record: object | None = None,
    d14_evaluation_bundle: object | None = None,
    candidate_attribution_edge_set: object | None = None,
    candidate_decision_enrichment: object | None = None,
    local_identity_scaffold: object | None = None,
    provider_claim: object | None = None,
    provider_prompt: object | None = None,
    provider_log: object | None = None,
    evaluator_only_gold: object | None = None,
) -> InspectorFactUnavailable:
    """Return a structured fail-closed G1/G2 result until M3 and D14 exist.

    The arguments are intentionally opaque.  This function does not parse or
    retain them, and therefore cannot accidentally make a prompt, log, M1
    attribution fixture, scaffold, or Provider self-claim into a verified fact.
    """

    if run_group not in {"G1", "G2"}:
        raise InspectorFactError("model Inspector projection is limited to G1/G2")
    supplied = {
        "candidate_attribution_edge_set": candidate_attribution_edge_set,
        "candidate_decision_enrichment": candidate_decision_enrichment,
        "local_identity_scaffold": local_identity_scaffold,
        "provider_claim": provider_claim,
        "provider_prompt": provider_prompt,
        "provider_log": provider_log,
        "evaluator_only_gold": evaluator_only_gold,
    }
    rejected = tuple(sorted(name for name, value in supplied.items() if value is not None))
    # Actual M3/D14 types and identities are deliberately unavailable here.  The
    # presence of opaque objects cannot turn this result into a projection.
    _ = model_candidate_audit_record, d14_evaluation_bundle
    result = InspectorFactUnavailable(
        error_id="",
        run_group=run_group,
        status="fail_closed",
        error_code="model_audit_bundle_unavailable",
        missing_artifacts=("model_candidate_audit_record", "d14_evaluation_bundle"),
        rejected_input_kinds=rejected,
    )
    result = replace(result, error_id=_stable_id("inspector-fact-unavailable", result.to_payload()))
    result.validate()
    return result


def validate_source_isolation(
    run_group: str,
    source_refs: Iterable[InspectorSourceRef],
    facts: Iterable[InspectorFact] = (),
) -> None:
    """Reject mixed group routes and unverified G0 source kinds."""

    _require_run_group(run_group)
    source_refs = tuple(source_refs)
    facts = tuple(facts)
    if run_group in {"G1", "G2"}:
        raise InspectorFactSourceIsolationError(
            "G1/G2 Inspector facts are unavailable until actual M3 audit and D14 bundle types exist"
        )
    if run_group != "G0":
        raise InspectorFactSourceIsolationError("unsupported Inspector fact group")
    for source in source_refs:
        if source.run_group != "G0":
            raise InspectorFactSourceIsolationError("Inspector fact sources may not mix run groups")
        if source.source_kind not in G0_SOURCE_KINDS:
            raise InspectorFactSourceIsolationError(
                f"unverified G0 Inspector source kind: {source.source_kind}"
            )
    for fact in facts:
        if fact.run_group != "G0":
            raise InspectorFactSourceIsolationError("Inspector facts may not mix run groups")


def _validate_g0_artifacts(
    guidance: RetrievalGuidance,
    guided_build_result: GuidedPageSpecBuildResult,
    page_spec: PageSpec,
    retrieval_influence_report: RetrievalInfluenceReport,
) -> None:
    if not isinstance(guidance, RetrievalGuidance):
        raise TypeError("guidance must be a RetrievalGuidance")
    if not isinstance(guided_build_result, GuidedPageSpecBuildResult):
        raise TypeError("guided_build_result must be a GuidedPageSpecBuildResult")
    if not isinstance(page_spec, PageSpec):
        raise TypeError("page_spec must be a PageSpec")
    if not isinstance(retrieval_influence_report, RetrievalInfluenceReport):
        raise TypeError("retrieval_influence_report must be a RetrievalInfluenceReport")
    guidance.validate()
    guided_build_result.validate(guidance)
    page_spec.validate()
    retrieval_influence_report.validate()
    if guidance.schema_version != RETRIEVAL_GUIDANCE_SCHEMA_VERSION:
        raise InspectorFactError("G0 guidance schema is incompatible")
    if guided_build_result.schema_version != GUIDED_PAGE_SPEC_BUILD_RESULT_SCHEMA_VERSION:
        raise InspectorFactError("G0 guided build schema is incompatible")
    if page_spec.schema_version != PAGE_SPEC_SCHEMA_VERSION:
        raise InspectorFactError("G0 PageSpec schema is incompatible")
    if retrieval_influence_report.schema_version != RETRIEVAL_INFLUENCE_REPORT_SCHEMA_VERSION:
        raise InspectorFactError("G0 influence schema is incompatible")
    if guided_build_result.page_spec.to_dict() != page_spec.to_dict():
        raise InspectorFactError("G0 PageSpec does not match the guided build result")
    if guided_build_result.guidance_bundle_id != guidance.guidance_bundle_id:
        raise InspectorFactError("G0 guided build references the wrong guidance bundle")
    if retrieval_influence_report.guidance_bundle_id != guidance.guidance_bundle_id:
        raise InspectorFactError("G0 influence report references the wrong guidance bundle")
    if retrieval_influence_report.page_id != page_spec.page_id:
        raise InspectorFactError("G0 influence report references the wrong PageSpec")
    decisions = _ordered_decisions(guided_build_result)
    trace_ids = [item.get("decision_id") for item in retrieval_influence_report.decision_traceability]
    if set(trace_ids) != {decision.decision_id for _, decision in decisions} or len(trace_ids) != len(decisions):
        raise InspectorFactError("G0 influence trace does not cover the guided decisions exactly")
    expected_counts = {name: sum(disposition == name for disposition, _ in decisions) for name in ("adopted", "ignored", "fallback")}
    if retrieval_influence_report.decision_status_counts != expected_counts:
        raise InspectorFactError("G0 influence decision disposition counts are inconsistent")


def _ordered_decisions(guided_build_result: GuidedPageSpecBuildResult) -> tuple[tuple[str, Any], ...]:
    return tuple(
        (disposition, decision)
        for disposition, decisions in (
            ("adopted", guided_build_result.adopted),
            ("ignored", guided_build_result.ignored),
            ("fallback", guided_build_result.fallback),
        )
        for decision in decisions
    )


def _validate_influence_trace(decision: Any, disposition: str, trace: dict[str, Any]) -> None:
    expected = {
        "disposition": disposition,
        "decision_id": decision.decision_id,
        "source_kind": decision.source_kind,
        "role": decision.role,
        "guidance_id": decision.guidance_id,
        "doc_id": decision.doc_id,
        "affected_fields": [
            {"entity_id": field.entity_id, "field_name": field.field_name}
            for field in decision.affected_fields
        ],
    }
    if trace != expected:
        raise InspectorFactError(
            f"G0 influence trace does not exactly match GuidanceDecision {decision.decision_id}"
        )


def _g0_decision_source_ref(
    *,
    guidance: RetrievalGuidance,
    guided_build_result: GuidedPageSpecBuildResult,
    guidance_sha256: str,
    build_sha256: str,
    decision: Any,
) -> InspectorSourceRef:
    if decision.source_kind == "retrieval_guidance":
        return _source_ref(
            run_group="G0",
            source_kind="guided_retrieval",
            artifact_schema_version=RETRIEVAL_GUIDANCE_SCHEMA_VERSION,
            artifact_id=guidance.guidance_bundle_id,
            artifact_sha256=guidance_sha256,
            source_id=str(decision.guidance_id),
            role=decision.role,
            guidance_id=decision.guidance_id,
            doc_id=decision.doc_id,
        )
    if decision.source_kind == "agent_context":
        return _source_ref(
            run_group="G0",
            source_kind="guided_agent_context_fallback",
            artifact_schema_version=GUIDED_PAGE_SPEC_BUILD_RESULT_SCHEMA_VERSION,
            artifact_id=guided_build_result.build_result_id,
            artifact_sha256=build_sha256,
            source_id=decision.decision_id,
            role=decision.role,
        )
    raise InspectorFactSourceIsolationError(
        f"GuidanceDecision source cannot be projected as a G0 Inspector fact: {decision.source_kind}"
    )


def _source_ref(
    *,
    run_group: str,
    source_kind: str,
    artifact_schema_version: str,
    artifact_id: str,
    artifact_sha256: str,
    source_id: str,
    role: str | None = None,
    guidance_id: str | None = None,
    doc_id: str | None = None,
) -> InspectorSourceRef:
    result = InspectorSourceRef(
        source_ref_id="",
        run_group=run_group,
        source_kind=source_kind,
        artifact_schema_version=artifact_schema_version,
        artifact_id=artifact_id,
        artifact_sha256=artifact_sha256,
        source_id=source_id,
        role=role,
        guidance_id=guidance_id,
        doc_id=doc_id,
    )
    result = replace(result, source_ref_id=_stable_id("inspector-source", result.to_payload()))
    result.validate()
    return result


def _influence_check_summary(check_id: str, category: str, status: str) -> InspectorInfluenceCheckSummary:
    result = InspectorInfluenceCheckSummary(
        check_summary_id="",
        check_id=check_id,
        category=category,
        status=status,
    )
    result = replace(
        result,
        check_summary_id=_stable_id("inspector-influence-check", result.to_payload()),
    )
    result.validate()
    return result


def _trace_link(*, decision_id: str, entity_id: str, field_path: str, source_ref_id: str) -> InspectorTraceLink:
    result = InspectorTraceLink(
        trace_link_id="",
        decision_id=decision_id,
        entity_id=entity_id,
        field_path=field_path,
        source_ref_id=source_ref_id,
    )
    result = replace(result, trace_link_id=_stable_id("inspector-trace", result.to_payload()))
    result.validate()
    return result


def _fact(
    *,
    decision_id: str,
    disposition: str,
    source_ref_id: str,
    verification_source_ref_id: str,
    trace_link_ids: tuple[str, ...],
) -> InspectorFact:
    result = InspectorFact(
        fact_id="",
        run_group="G0",
        decision_id=decision_id,
        disposition=disposition,
        source_ref_id=source_ref_id,
        verification_source_ref_id=verification_source_ref_id,
        influence_trace_status="trace_matched",
        trace_link_ids=trace_link_ids,
    )
    result = replace(result, fact_id=_stable_id("inspector-fact", result.to_payload()))
    result.validate()
    return result


def _page_spec_entity_kind(page_spec: PageSpec, entity_id: str) -> str:
    if entity_id == page_spec.page_id:
        return "page"
    collections = (
        ("section", page_spec.sections, "section_id"),
        ("component", page_spec.components, "component_id"),
        ("state", page_spec.states, "state_id"),
        ("interaction", page_spec.interactions, "interaction_id"),
        ("constraint", page_spec.constraints, "constraint_id"),
        ("acceptance_check", page_spec.acceptance_checks, "check_id"),
    )
    for kind, values, id_field in collections:
        if any(getattr(value, id_field) == entity_id for value in values):
            return kind
    raise InspectorFactError(f"PageSpec entity does not exist: {entity_id}")


def _validate_sorted_unique(values: tuple[Any, ...], field_name: str, collection_name: str) -> None:
    identifiers = tuple(getattr(item, field_name) for item in values)
    if len(identifiers) != len(set(identifiers)):
        raise InspectorFactError(f"{collection_name} must be unique")
    if tuple(sorted(identifiers)) != identifiers:
        raise InspectorFactError(f"{collection_name} must be sorted")


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    return f"{prefix}-{canonical_sha256(payload)[:20]}"


def _require_non_empty(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InspectorFactError(f"{field_name} must be a non-empty string")


def _require_run_group(run_group: str) -> None:
    if run_group not in RUN_GROUPS:
        raise InspectorFactError(f"unsupported Inspector run group: {run_group}")
