"""D09 development-only robustness negative controls for deterministic G0.

This module evaluates a caller-pre-registered injected retrieval result without
writing control labels into AgentContext, RetrievalGuidance, PageSpec, Inspector,
or detector-visible bundles. It reuses the approved decision-unit normalizer for
non-provenance operational stability.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import os
import re
import shutil
from typing import Any, Iterable, Mapping
import uuid

from req2web_agent import AgentContextBundle, UseCase
from req2web_evaluation.decision_units import CandidateDecisionSet, normalize_candidate_decisions
from req2web_generation import (
    GuidedPageSpecBuildResult,
    RetrievalGuidance,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
)
from req2web_generation.schema import PageSpec
from req2web_inspector.facts import InspectorFactSet
from req2web_faults.mutation import (
    IGNORED_EVIDENCE_MISATTRIBUTED_TO_PAGE_SPEC,
    FaultCopyBuildResult,
    FaultMutationRequest,
    build_fault_copy,
)
from req2web_rag.corpus import ROLE_ORDER


NEGATIVE_CONTROL_SPEC_SCHEMA_VERSION = "req2web.evaluation.negative_control_spec.v1"
NEGATIVE_CONTROL_REPORT_SCHEMA_VERSION = "req2web.evaluation.negative_control_report.v1"
NEGATIVE_CONTROL_MANIFEST_SCHEMA_VERSION = "req2web.evaluation.negative_control_manifest.v1"
EXPECTED_OUTCOME = "ignored_and_operationally_stable"
PASSED = "passed"
NEGATIVE_CONTROL_FAILED = "negative_control_failed"

FAILURE_CODES = frozenset({
    "injected_doc_present_in_baseline_guidance",
    "injected_guidance_identity_missing_or_extra",
    "injected_guidance_missing",
    "injected_guidance_decision_missing",
    "injected_decision_identity_missing_or_extra",
    "injected_evidence_not_ignored",
    "operational_decision_projection_changed",
    "non_provenance_page_spec_delta",
})
_RESERVED_FIELD_MARKERS = frozenset({
    "negative_control", "expected_outcome", "control", "spec", "report", "evaluator", "evaluator_only",
    "gold", "fault", "mutation", "control_label", "gold_label", "fault_label",
    "mutation_label", "provenance_metadata",
})
_RESERVED_METADATA_MARKERS = frozenset({
    "negative_control", "irrelevant_evidence", "adoption_probe", "expected_outcome",
    "gold", "fault", "mutation", "evaluator", "evaluator_only", "control_label",
    "gold_label", "fault_label", "mutation_label",
})
_IDENTITY_METADATA_FIELDS = ("doc_id", "dataset", "subset", "sample_id")

_SPEC_FILE = "negative_control_spec.json"
_REPORT_FILE = "negative_control_report.json"
_MANIFEST_FILE = "negative_control_manifest.json"
_REQUIRED_FILES = (_MANIFEST_FILE, _REPORT_FILE, _SPEC_FILE)


class NegativeControlError(ValueError):
    """A negative-control artifact is malformed or does not validate."""


class NegativeControlInputError(NegativeControlError):
    """The pre-registered binding or supplied baseline cannot be trusted."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NegativeControlError(field_name + " must be non-empty text")
    return value.strip()


def _require_sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise NegativeControlError(field_name + " must be a lowercase SHA-256")
    return value


def _normalized_marker(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _validate_injected_label_isolation(payload: Mapping[str, object]) -> None:
    """Reject explicit control-sidecar labels without reading natural-language text."""
    for field_name in payload:
        if not isinstance(field_name, str):
            raise NegativeControlInputError("injected retrieval result field names must be text")
        normalized = _normalized_marker(field_name)
        if normalized in _RESERVED_FIELD_MARKERS:
            raise NegativeControlInputError("injected retrieval result contains a reserved control-sidecar field")
    for field_name in _IDENTITY_METADATA_FIELDS:
        value = payload.get(field_name)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise NegativeControlInputError("injected retrieval result identity metadata must be non-empty text")
        normalized = _normalized_marker(value)
        tokens = frozenset(token for token in normalized.split("_") if token)
        if normalized in _RESERVED_METADATA_MARKERS or tokens.intersection({"gold", "fault", "mutation", "evaluator"}):
            raise NegativeControlInputError("injected retrieval identity metadata contains a reserved control marker")


def _record_id(prefix: str, payload: Mapping[str, object]) -> str:
    return prefix + "-" + _sha256_bytes(_canonical_json_bytes(dict(payload)))[:20]


def _context_payload(context: AgentContextBundle) -> dict[str, Any]:
    context.validate()
    return context.to_dict()


def _context_sha256(context: AgentContextBundle) -> str:
    return _sha256_bytes(_canonical_json_bytes(_context_payload(context)))


def _page_spec_sha256(page_spec: object) -> str:
    to_dict = getattr(page_spec, "to_dict", None)
    if not callable(to_dict):
        raise NegativeControlError("page_spec must expose to_dict")
    return _sha256_bytes(_canonical_json_bytes(to_dict()))


def _decision_projection(candidate_set: CandidateDecisionSet) -> dict[str, object]:
    candidate_set.validate()
    return {
        "case_id": candidate_set.case_id,
        "decisions": [record.to_dict() for record in candidate_set.decisions],
        "schema_version": candidate_set.schema_version,
    }


def _decision_projection_sha256(candidate_set: CandidateDecisionSet) -> str:
    return _sha256_bytes(_canonical_json_bytes(_decision_projection(candidate_set)))


def _decision_ids(candidate_set: CandidateDecisionSet) -> tuple[str, ...]:
    return tuple(record.decision_id for record in candidate_set.decisions)


def _guidance_items(guidance: object) -> tuple[object, ...]:
    names = (
        "requirement_guidance",
        "ui_guidance",
        "interaction_guidance",
        "implementation_guidance",
        "validation_guidance",
    )
    values: list[object] = []
    for name in names:
        values.extend(getattr(guidance, name))
    return tuple(values)


def _context_from_payload(payload: Mapping[str, object]) -> AgentContextBundle:
    expected = {
        "original_requirement", "requirement_summary", "target_device", "task_type",
        "constraints", "use_cases", "retrieval_queries", "retrieval_results", "schema_version",
    }
    if set(payload) != expected:
        raise NegativeControlInputError("baseline AgentContext payload has an unexpected field set")
    use_cases = payload["use_cases"]
    if not isinstance(use_cases, list) or any(not isinstance(item, dict) for item in use_cases):
        raise NegativeControlInputError("baseline AgentContext use_cases are invalid")
    try:
        context = AgentContextBundle(
            original_requirement=payload["original_requirement"],
            requirement_summary=payload["requirement_summary"],
            target_device=payload["target_device"],
            task_type=payload["task_type"],
            constraints=list(payload["constraints"]),
            use_cases=[UseCase(**item) for item in use_cases],
            retrieval_queries={
                role: dict(payload["retrieval_queries"])[role]
                for role in ROLE_ORDER
            },
            retrieval_results={
                role: [dict(result) for result in dict(payload["retrieval_results"])[role]]
                for role in ROLE_ORDER
            },
            schema_version=payload["schema_version"],
        )
    except (TypeError, ValueError) as error:
        raise NegativeControlInputError("baseline AgentContext payload cannot be reconstructed") from error
    context.validate()
    return context


def _diff_paths(left: object, right: object, prefix: str = "") -> tuple[str, ...]:
    if type(left) is not type(right):
        return (prefix or "<root>",)
    if isinstance(left, dict):
        result: list[str] = []
        for key in sorted(set(left) | set(right)):
            path = key if not prefix else prefix + "." + key
            if key not in left or key not in right:
                result.append(path)
            else:
                result.extend(_diff_paths(left[key], right[key], path))
        return tuple(result)
    if isinstance(left, list):
        result: list[str] = []
        for index in range(max(len(left), len(right))):
            path = f"{prefix}[{index}]"
            if index >= len(left) or index >= len(right):
                result.append(path)
            else:
                result.extend(_diff_paths(left[index], right[index], path))
        return tuple(result)
    return () if left == right else (prefix or "<root>",)


def _provenance_only(paths: Iterable[str]) -> bool:
    return all(path == "page_id" or path.startswith("traceability") for path in paths)


@dataclass(frozen=True)
class InjectedRetrievalResult:
    """Immutable exact bytes/identity binding for one injected retrieval result."""

    doc_id: str
    canonical_json: str
    sha256: str

    @classmethod
    def from_result(cls, result: Mapping[str, object]) -> "InjectedRetrievalResult":
        if not isinstance(result, Mapping):
            raise NegativeControlInputError("injected retrieval result must be a mapping")
        payload = dict(result)
        _validate_injected_label_isolation(payload)
        doc_id = _require_text(payload.get("doc_id"), "injected retrieval result doc_id")
        canonical = _canonical_json_bytes(payload).decode("utf-8")
        item = cls(doc_id=doc_id, canonical_json=canonical, sha256=_sha256_bytes(canonical.encode("utf-8")))
        item.validate()
        return item

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "InjectedRetrievalResult":
        if set(payload) != {"doc_id", "canonical_json", "sha256"}:
            raise NegativeControlError("injected retrieval result has an unexpected field set")
        item = cls(doc_id=payload["doc_id"], canonical_json=payload["canonical_json"], sha256=payload["sha256"])
        item.validate()
        return item

    def materialize(self) -> dict[str, Any]:
        _require_text(self.doc_id, "injected retrieval result doc_id")
        _require_text(self.canonical_json, "injected retrieval result canonical_json")
        _require_sha256(self.sha256, "injected retrieval result sha256")
        try:
            payload = json.loads(self.canonical_json)
        except json.JSONDecodeError as error:
            raise NegativeControlInputError("injected retrieval result canonical JSON is unreadable") from error
        if not isinstance(payload, dict):
            raise NegativeControlInputError("injected retrieval result must be a JSON object")
        _validate_injected_label_isolation(payload)
        if _canonical_json_bytes(payload).decode("utf-8") != self.canonical_json:
            raise NegativeControlInputError("injected retrieval result JSON is not canonical")
        if _sha256_bytes(self.canonical_json.encode("utf-8")) != self.sha256:
            raise NegativeControlInputError("injected retrieval result SHA-256 does not match canonical JSON")
        if payload.get("doc_id") != self.doc_id:
            raise NegativeControlInputError("injected retrieval result doc_id does not match canonical JSON")
        return payload

    def validate(self) -> None:
        self.materialize()

    def to_dict(self) -> dict[str, str]:
        self.validate()
        return {"canonical_json": self.canonical_json, "doc_id": self.doc_id, "sha256": self.sha256}


@dataclass(frozen=True)
class NegativeControlSpec:
    """Pre-registered D09 expectation, bound before the control run."""

    spec_id: str
    case_id: str
    role: str
    injected_results: tuple[InjectedRetrievalResult, ...]
    baseline_context_sha256: str
    baseline_page_spec_sha256: str
    baseline_decision_projection_sha256: str
    baseline_decision_ids: tuple[str, ...]
    expected_outcome: str = EXPECTED_OUTCOME
    schema_version: str = NEGATIVE_CONTROL_SPEC_SCHEMA_VERSION

    @classmethod
    def preregister(
        cls,
        case_id: str,
        baseline_context: AgentContextBundle,
        role: str,
        injected_results: Iterable[Mapping[str, object]],
    ) -> "NegativeControlSpec":
        case_id = _require_text(case_id, "case_id")
        if role not in ROLE_ORDER:
            raise NegativeControlInputError("negative control role must be one supported retrieval role")
        baseline_payload = _context_payload(baseline_context)
        frozen = tuple(InjectedRetrievalResult.from_result(item) for item in injected_results)
        if not frozen:
            raise NegativeControlInputError("negative control requires at least one injected retrieval result")
        doc_ids = tuple(item.doc_id for item in frozen)
        if len(doc_ids) != len(set(doc_ids)):
            raise NegativeControlInputError("injected retrieval result doc_ids must be unique")
        materialized = tuple(item.materialize() for item in frozen)
        if any(item.get("role") != role for item in materialized):
            raise NegativeControlInputError("injected retrieval results must all bind the registered role")
        baseline_doc_ids = {
            str(result["doc_id"])
            for results in baseline_context.retrieval_results.values()
            for result in results
        }
        if baseline_doc_ids.intersection(doc_ids):
            raise NegativeControlInputError("injected retrieval doc_id already exists in the baseline context")
        guidance = RetrievalGuidanceBuilder().build(baseline_context)
        guided = RetrievalGuidedPageSpecBuilder().build(baseline_context, guidance)
        decisions = normalize_candidate_decisions(case_id, guided.page_spec)
        body = {
            "baseline_context_sha256": _sha256_bytes(_canonical_json_bytes(baseline_payload)),
            "baseline_decision_ids": list(_decision_ids(decisions)),
            "baseline_decision_projection_sha256": _decision_projection_sha256(decisions),
            "baseline_page_spec_sha256": _page_spec_sha256(guided.page_spec),
            "case_id": case_id,
            "expected_outcome": EXPECTED_OUTCOME,
            "injected_results": [item.to_dict() for item in frozen],
            "role": role,
            "schema_version": NEGATIVE_CONTROL_SPEC_SCHEMA_VERSION,
        }
        item = cls(
            spec_id=_record_id("negative-control-spec", body),
            case_id=case_id,
            role=role,
            injected_results=frozen,
            baseline_context_sha256=body["baseline_context_sha256"],
            baseline_page_spec_sha256=body["baseline_page_spec_sha256"],
            baseline_decision_projection_sha256=body["baseline_decision_projection_sha256"],
            baseline_decision_ids=tuple(body["baseline_decision_ids"]),
            expected_outcome=EXPECTED_OUTCOME,
            schema_version=NEGATIVE_CONTROL_SPEC_SCHEMA_VERSION,
        )
        item.validate()
        return item

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "NegativeControlSpec":
        expected = {
            "spec_id", "case_id", "role", "injected_results", "baseline_context_sha256",
            "baseline_page_spec_sha256", "baseline_decision_projection_sha256",
            "baseline_decision_ids", "expected_outcome", "schema_version",
        }
        if set(payload) != expected:
            raise NegativeControlError("negative control spec has an unexpected field set")
        injected = payload["injected_results"]
        if not isinstance(injected, list) or any(not isinstance(value, dict) for value in injected):
            raise NegativeControlError("negative control spec injected_results must be objects")
        item = cls(
            spec_id=payload["spec_id"], case_id=payload["case_id"], role=payload["role"],
            injected_results=tuple(InjectedRetrievalResult.from_dict(value) for value in injected),
            baseline_context_sha256=payload["baseline_context_sha256"],
            baseline_page_spec_sha256=payload["baseline_page_spec_sha256"],
            baseline_decision_projection_sha256=payload["baseline_decision_projection_sha256"],
            baseline_decision_ids=tuple(payload["baseline_decision_ids"]),
            expected_outcome=payload["expected_outcome"], schema_version=payload["schema_version"],
        )
        item.validate()
        return item

    def to_payload(self) -> dict[str, object]:
        return {
            "baseline_context_sha256": self.baseline_context_sha256,
            "baseline_decision_ids": list(self.baseline_decision_ids),
            "baseline_decision_projection_sha256": self.baseline_decision_projection_sha256,
            "baseline_page_spec_sha256": self.baseline_page_spec_sha256,
            "case_id": self.case_id,
            "expected_outcome": self.expected_outcome,
            "injected_results": [item.to_dict() for item in self.injected_results],
            "role": self.role,
            "schema_version": self.schema_version,
        }

    def validate(self) -> None:
        _require_text(self.spec_id, "negative control spec_id")
        _require_text(self.case_id, "negative control case_id")
        if self.schema_version != NEGATIVE_CONTROL_SPEC_SCHEMA_VERSION:
            raise NegativeControlError("unsupported negative control spec schema")
        if self.expected_outcome != EXPECTED_OUTCOME:
            raise NegativeControlError("negative control expected_outcome is unsupported")
        if self.role not in ROLE_ORDER:
            raise NegativeControlError("negative control role is unsupported")
        if not isinstance(self.injected_results, tuple) or not self.injected_results:
            raise NegativeControlError("negative control injected_results must be a non-empty tuple")
        for item in self.injected_results:
            if not isinstance(item, InjectedRetrievalResult):
                raise NegativeControlError("negative control injected_results contains an invalid record")
            item.validate()
            if item.materialize().get("role") != self.role:
                raise NegativeControlError("injected retrieval result is not bound to the registered role")
        doc_ids = tuple(item.doc_id for item in self.injected_results)
        if len(doc_ids) != len(set(doc_ids)):
            raise NegativeControlError("negative control injected doc_ids must be unique")
        for field_name in (
            "baseline_context_sha256", "baseline_page_spec_sha256", "baseline_decision_projection_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        if not isinstance(self.baseline_decision_ids, tuple) or not self.baseline_decision_ids:
            raise NegativeControlError("baseline_decision_ids must be a non-empty tuple")
        if len(self.baseline_decision_ids) != len(set(self.baseline_decision_ids)):
            raise NegativeControlError("baseline_decision_ids must be unique")
        for decision_id in self.baseline_decision_ids:
            _require_text(decision_id, "baseline_decision_ids item")
        if self.spec_id != _record_id("negative-control-spec", self.to_payload()):
            raise NegativeControlError("negative control spec_id does not match canonical payload")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"spec_id": self.spec_id, **self.to_payload()}

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return _sha256_bytes(self.canonical_json_bytes())


@dataclass(frozen=True)
class NegativeControlReport:
    """D08/D09 observation, never a fault report or a semantic-gold verdict."""

    report_id: str
    spec_sha256: str
    case_id: str
    role: str
    expected_outcome: str
    status: str
    failure_codes: tuple[str, ...]
    injected_doc_ids: tuple[str, ...]
    injected_result_sha256s: tuple[str, ...]
    injected_guidance_ids: tuple[str, ...]
    injected_decision_ids: tuple[str, ...]
    injected_decision_dispositions: tuple[tuple[str, str], ...]
    baseline_context_sha256: str
    controlled_context_sha256: str
    baseline_guidance_sha256: str
    controlled_guidance_sha256: str
    baseline_page_spec_sha256: str
    controlled_page_spec_sha256: str
    full_page_spec_identity: bool
    provenance_only_delta: bool
    full_page_spec_diff_paths: tuple[str, ...]
    expected_decision_projection_sha256: str
    baseline_decision_projection_sha256: str
    controlled_decision_projection_sha256: str
    stable_decision_projection_sha256: str | None
    baseline_decision_ids: tuple[str, ...]
    controlled_decision_ids: tuple[str, ...]
    schema_version: str = NEGATIVE_CONTROL_REPORT_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "NegativeControlReport":
        expected = {
            "report_id", "spec_sha256", "case_id", "role", "expected_outcome", "status", "failure_codes",
            "injected_doc_ids", "injected_result_sha256s", "injected_guidance_ids", "injected_decision_ids",
            "injected_decision_dispositions", "baseline_context_sha256", "controlled_context_sha256",
            "baseline_guidance_sha256", "controlled_guidance_sha256", "baseline_page_spec_sha256",
            "controlled_page_spec_sha256", "full_page_spec_identity", "provenance_only_delta",
            "full_page_spec_diff_paths", "expected_decision_projection_sha256",
            "baseline_decision_projection_sha256", "controlled_decision_projection_sha256",
            "stable_decision_projection_sha256", "baseline_decision_ids", "controlled_decision_ids", "schema_version",
        }
        if set(payload) != expected:
            raise NegativeControlError("negative control report has an unexpected field set")
        dispositions = payload["injected_decision_dispositions"]
        if not isinstance(dispositions, list) or any(not isinstance(value, list) or len(value) != 2 for value in dispositions):
            raise NegativeControlError("negative control report injected decision dispositions are invalid")
        item = cls(
            report_id=payload["report_id"], spec_sha256=payload["spec_sha256"], case_id=payload["case_id"], role=payload["role"],
            expected_outcome=payload["expected_outcome"], status=payload["status"], failure_codes=tuple(payload["failure_codes"]),
            injected_doc_ids=tuple(payload["injected_doc_ids"]), injected_result_sha256s=tuple(payload["injected_result_sha256s"]),
            injected_guidance_ids=tuple(payload["injected_guidance_ids"]), injected_decision_ids=tuple(payload["injected_decision_ids"]),
            injected_decision_dispositions=tuple((value[0], value[1]) for value in dispositions),
            baseline_context_sha256=payload["baseline_context_sha256"], controlled_context_sha256=payload["controlled_context_sha256"],
            baseline_guidance_sha256=payload["baseline_guidance_sha256"], controlled_guidance_sha256=payload["controlled_guidance_sha256"],
            baseline_page_spec_sha256=payload["baseline_page_spec_sha256"], controlled_page_spec_sha256=payload["controlled_page_spec_sha256"],
            full_page_spec_identity=payload["full_page_spec_identity"], provenance_only_delta=payload["provenance_only_delta"],
            full_page_spec_diff_paths=tuple(payload["full_page_spec_diff_paths"]),
            expected_decision_projection_sha256=payload["expected_decision_projection_sha256"],
            baseline_decision_projection_sha256=payload["baseline_decision_projection_sha256"],
            controlled_decision_projection_sha256=payload["controlled_decision_projection_sha256"],
            stable_decision_projection_sha256=payload["stable_decision_projection_sha256"],
            baseline_decision_ids=tuple(payload["baseline_decision_ids"]), controlled_decision_ids=tuple(payload["controlled_decision_ids"]),
            schema_version=payload["schema_version"],
        )
        item.validate()
        return item

    def to_payload(self) -> dict[str, object]:
        return {
            "baseline_context_sha256": self.baseline_context_sha256,
            "baseline_decision_ids": list(self.baseline_decision_ids),
            "baseline_decision_projection_sha256": self.baseline_decision_projection_sha256,
            "baseline_guidance_sha256": self.baseline_guidance_sha256,
            "baseline_page_spec_sha256": self.baseline_page_spec_sha256,
            "case_id": self.case_id,
            "controlled_context_sha256": self.controlled_context_sha256,
            "controlled_decision_ids": list(self.controlled_decision_ids),
            "controlled_decision_projection_sha256": self.controlled_decision_projection_sha256,
            "controlled_guidance_sha256": self.controlled_guidance_sha256,
            "controlled_page_spec_sha256": self.controlled_page_spec_sha256,
            "expected_decision_projection_sha256": self.expected_decision_projection_sha256,
            "expected_outcome": self.expected_outcome,
            "failure_codes": list(self.failure_codes),
            "full_page_spec_diff_paths": list(self.full_page_spec_diff_paths),
            "full_page_spec_identity": self.full_page_spec_identity,
            "injected_decision_dispositions": [list(value) for value in self.injected_decision_dispositions],
            "injected_decision_ids": list(self.injected_decision_ids),
            "injected_doc_ids": list(self.injected_doc_ids),
            "injected_guidance_ids": list(self.injected_guidance_ids),
            "injected_result_sha256s": list(self.injected_result_sha256s),
            "provenance_only_delta": self.provenance_only_delta,
            "role": self.role,
            "schema_version": self.schema_version,
            "spec_sha256": self.spec_sha256,
            "stable_decision_projection_sha256": self.stable_decision_projection_sha256,
            "status": self.status,
        }

    def validate(self) -> None:
        _require_text(self.report_id, "negative control report_id")
        _require_text(self.case_id, "negative control report case_id")
        if self.schema_version != NEGATIVE_CONTROL_REPORT_SCHEMA_VERSION:
            raise NegativeControlError("unsupported negative control report schema")
        if self.expected_outcome != EXPECTED_OUTCOME or self.role not in ROLE_ORDER:
            raise NegativeControlError("negative control report binding is unsupported")
        if self.status not in {PASSED, NEGATIVE_CONTROL_FAILED}:
            raise NegativeControlError("negative control report status is unsupported")
        for field_name in (
            "spec_sha256", "baseline_context_sha256", "controlled_context_sha256", "baseline_guidance_sha256",
            "controlled_guidance_sha256", "baseline_page_spec_sha256", "controlled_page_spec_sha256",
            "expected_decision_projection_sha256", "baseline_decision_projection_sha256", "controlled_decision_projection_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        if self.stable_decision_projection_sha256 is not None:
            _require_sha256(self.stable_decision_projection_sha256, "stable_decision_projection_sha256")
        if not all(isinstance(value, bool) for value in (self.full_page_spec_identity, self.provenance_only_delta)):
            raise NegativeControlError("negative control identity flags must be boolean")
        for name in (
            "failure_codes", "injected_doc_ids", "injected_result_sha256s", "injected_guidance_ids",
            "injected_decision_ids", "full_page_spec_diff_paths", "baseline_decision_ids", "controlled_decision_ids",
        ):
            value = getattr(self, name)
            if not isinstance(value, tuple) or len(value) != len(set(value)):
                raise NegativeControlError(name + " must be a unique immutable tuple")
        if tuple(sorted(self.failure_codes)) != self.failure_codes:
            raise NegativeControlError("failure_codes must be sorted")
        if any(code not in FAILURE_CODES for code in self.failure_codes):
            raise NegativeControlError("negative control report contains an unsupported failure code")
        for digest in self.injected_result_sha256s:
            _require_sha256(digest, "injected result sha256")
        for identifier in (*self.injected_guidance_ids, *self.injected_decision_ids, *self.baseline_decision_ids, *self.controlled_decision_ids):
            _require_text(identifier, "negative control identifier")
        if not isinstance(self.injected_decision_dispositions, tuple) or tuple(sorted(self.injected_decision_dispositions)) != self.injected_decision_dispositions:
            raise NegativeControlError("injected_decision_dispositions must be a sorted immutable tuple")
        if any(disposition not in {"ignored", "adopted", "fallback"} for _, disposition in self.injected_decision_dispositions):
            raise NegativeControlError("injected decision disposition is unsupported")
        if self.full_page_spec_identity != (self.baseline_page_spec_sha256 == self.controlled_page_spec_sha256):
            raise NegativeControlError("full_page_spec_identity does not match bound hashes")
        if self.provenance_only_delta != _provenance_only(self.full_page_spec_diff_paths):
            raise NegativeControlError("provenance_only_delta does not match diff paths")
        if self.status == PASSED:
            if self.failure_codes or not self.injected_guidance_ids or not self.injected_decision_ids:
                raise NegativeControlError("passed negative control has incomplete evidence")
            if any(disposition != "ignored" for _, disposition in self.injected_decision_dispositions):
                raise NegativeControlError("passed negative control must retain only ignored injected decisions")
            if not self.provenance_only_delta or self.baseline_decision_projection_sha256 != self.controlled_decision_projection_sha256:
                raise NegativeControlError("passed negative control must be operationally stable")
            if self.stable_decision_projection_sha256 != self.baseline_decision_projection_sha256:
                raise NegativeControlError("passed negative control stable projection hash is invalid")
        elif not self.failure_codes:
            raise NegativeControlError("failed negative control must contain failure codes")
        if self.report_id != _record_id("negative-control-report", self.to_payload()):
            raise NegativeControlError("negative control report_id does not match canonical payload")

    def validate_against_spec(self, spec: NegativeControlSpec) -> None:
        """Validate local report/spec bindings without rebuilding a baseline."""
        if not isinstance(spec, NegativeControlSpec):
            raise NegativeControlError("report binding requires NegativeControlSpec")
        self.validate()
        spec.validate()
        if self.spec_sha256 != spec.sha256():
            raise NegativeControlError("report spec_sha256 does not match the supplied spec")
        if self.case_id != spec.case_id or self.role != spec.role or self.expected_outcome != spec.expected_outcome:
            raise NegativeControlError("report case/role/outcome does not match the supplied spec")
        if self.baseline_context_sha256 != spec.baseline_context_sha256:
            raise NegativeControlError("report baseline context binding does not match the supplied spec")
        if self.baseline_page_spec_sha256 != spec.baseline_page_spec_sha256:
            raise NegativeControlError("report baseline PageSpec binding does not match the supplied spec")
        if self.expected_decision_projection_sha256 != spec.baseline_decision_projection_sha256:
            raise NegativeControlError("report expected decision projection does not match the supplied spec")
        if self.baseline_decision_projection_sha256 != spec.baseline_decision_projection_sha256:
            raise NegativeControlError("report baseline decision projection does not match the supplied spec")
        if self.baseline_decision_ids != spec.baseline_decision_ids:
            raise NegativeControlError("report baseline decision IDs do not match the supplied spec")
        if self.injected_doc_ids != tuple(sorted(item.doc_id for item in spec.injected_results)):
            raise NegativeControlError("report injected doc IDs do not match the supplied spec")
        if self.injected_result_sha256s != tuple(sorted(item.sha256 for item in spec.injected_results)):
            raise NegativeControlError("report injected result SHA-256 values do not match the supplied spec")
        disposition_ids = tuple(decision_id for decision_id, _ in self.injected_decision_dispositions)
        if disposition_ids != self.injected_decision_ids:
            raise NegativeControlError("report injected decision IDs do not match disposition records")
        if self.status == PASSED:
            if (
                self.expected_decision_projection_sha256
                != self.baseline_decision_projection_sha256
                or self.baseline_decision_projection_sha256
                != self.controlled_decision_projection_sha256
                or self.stable_decision_projection_sha256
                != self.expected_decision_projection_sha256
            ):
                raise NegativeControlError("passed report decision projection bindings are inconsistent")
            if any(disposition != "ignored" for _, disposition in self.injected_decision_dispositions):
                raise NegativeControlError("passed report contains a non-ignored injected disposition")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"report_id": self.report_id, **self.to_payload()}

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return _sha256_bytes(self.canonical_json_bytes())


@dataclass(frozen=True)
class NegativeControlRun:
    """In-memory artifacts retained for development-only bundle integration tests."""

    spec: NegativeControlSpec
    report: NegativeControlReport
    baseline_context: AgentContextBundle
    controlled_context: AgentContextBundle
    baseline_guidance: object
    controlled_guidance: object
    baseline_guided_build: object
    controlled_guided_build: object
    baseline_decisions: CandidateDecisionSet
    controlled_decisions: CandidateDecisionSet


def run_negative_control(spec: NegativeControlSpec, baseline_context: AgentContextBundle) -> NegativeControlRun:
    """Rebuild baseline/control G0 artifacts and evaluate a pre-registered control."""
    if not isinstance(spec, NegativeControlSpec) or not isinstance(baseline_context, AgentContextBundle):
        raise NegativeControlInputError("runner requires NegativeControlSpec and AgentContextBundle")
    spec.validate()
    baseline_before = _context_payload(baseline_context)
    if _sha256_bytes(_canonical_json_bytes(baseline_before)) != spec.baseline_context_sha256:
        raise NegativeControlInputError("supplied baseline context does not match the pre-registered spec")
    baseline_doc_ids = {
        str(result["doc_id"])
        for results in baseline_context.retrieval_results.values()
        for result in results
    }
    injected_results = tuple(item.materialize() for item in spec.injected_results)
    injected_doc_ids = tuple(item.doc_id for item in spec.injected_results)
    if baseline_doc_ids.intersection(injected_doc_ids):
        raise NegativeControlInputError("baseline context already contains an injected doc_id")

    baseline_guidance = RetrievalGuidanceBuilder().build(baseline_context)
    baseline_guided = RetrievalGuidedPageSpecBuilder().build(baseline_context, baseline_guidance)
    baseline_decisions = normalize_candidate_decisions(spec.case_id, baseline_guided.page_spec)
    baseline_projection_sha256 = _decision_projection_sha256(baseline_decisions)
    baseline_page_sha256 = _page_spec_sha256(baseline_guided.page_spec)
    if baseline_page_sha256 != spec.baseline_page_spec_sha256:
        raise NegativeControlInputError("pre-registered baseline PageSpec hash does not match the supplied baseline")
    if baseline_projection_sha256 != spec.baseline_decision_projection_sha256 or _decision_ids(baseline_decisions) != spec.baseline_decision_ids:
        raise NegativeControlInputError("pre-registered decision projection does not match the supplied baseline")

    controlled_payload = json.loads(_canonical_json_bytes(baseline_before))
    controlled_payload["retrieval_results"][spec.role] = [
        *controlled_payload["retrieval_results"][spec.role], *injected_results,
    ]
    controlled_context = _context_from_payload(controlled_payload)
    if _context_payload(baseline_context) != baseline_before:
        raise NegativeControlInputError("baseline context mutated during control construction")
    if _context_sha256(controlled_context) != _sha256_bytes(_canonical_json_bytes(controlled_payload)):
        raise NegativeControlInputError("controlled context does not retain the exact copy-on-write binding")
    for role in ROLE_ORDER:
        if role != spec.role and _canonical_json_bytes(controlled_context.retrieval_results[role]) != _canonical_json_bytes(baseline_context.retrieval_results[role]):
            raise NegativeControlInputError("non-injected retrieval role drifted during control construction")
    original_role = baseline_context.retrieval_results[spec.role]
    if _canonical_json_bytes(controlled_context.retrieval_results[spec.role][:len(original_role)]) != _canonical_json_bytes(original_role):
        raise NegativeControlInputError("baseline retrieval results drifted during control construction")
    if tuple(_canonical_json_bytes(item).decode("utf-8") for item in controlled_context.retrieval_results[spec.role][len(original_role):]) != tuple(item.canonical_json for item in spec.injected_results):
        raise NegativeControlInputError("controlled injected retrieval bytes do not match the pre-registered spec")

    controlled_guidance = RetrievalGuidanceBuilder().build(controlled_context)
    controlled_guided = RetrievalGuidedPageSpecBuilder().build(controlled_context, controlled_guidance)
    controlled_decisions = normalize_candidate_decisions(spec.case_id, controlled_guided.page_spec)
    controlled_projection_sha256 = _decision_projection_sha256(controlled_decisions)
    baseline_guidance_doc_ids = {item.source.doc_id for item in _guidance_items(baseline_guidance)}
    injected_guidance = tuple(item for item in _guidance_items(controlled_guidance) if item.source.doc_id in injected_doc_ids)
    all_control_decisions = tuple(("adopted", item) for item in controlled_guided.adopted) + tuple(("ignored", item) for item in controlled_guided.ignored) + tuple(("fallback", item) for item in controlled_guided.fallback)
    injected_decisions = tuple((disposition, decision) for disposition, decision in all_control_decisions if decision.doc_id in injected_doc_ids)
    diff_paths = _diff_paths(baseline_guided.page_spec.to_dict(), controlled_guided.page_spec.to_dict())
    provenance_only_delta = _provenance_only(diff_paths)
    full_identity = baseline_guided.page_spec.to_dict() == controlled_guided.page_spec.to_dict()
    failure_codes: set[str] = set()
    if baseline_guidance_doc_ids.intersection(injected_doc_ids):
        failure_codes.add("injected_doc_present_in_baseline_guidance")
    if {item.source.doc_id for item in injected_guidance} != set(injected_doc_ids):
        failure_codes.add("injected_guidance_identity_missing_or_extra")
    if not injected_guidance:
        failure_codes.add("injected_guidance_missing")
    if not injected_decisions:
        failure_codes.add("injected_guidance_decision_missing")
    if {decision.doc_id for _, decision in injected_decisions} != set(injected_doc_ids):
        failure_codes.add("injected_decision_identity_missing_or_extra")
    if any(disposition != "ignored" or decision.affected_fields for disposition, decision in injected_decisions):
        failure_codes.add("injected_evidence_not_ignored")
    if baseline_projection_sha256 != controlled_projection_sha256 or _decision_projection(baseline_decisions) != _decision_projection(controlled_decisions):
        failure_codes.add("operational_decision_projection_changed")
    if not provenance_only_delta:
        failure_codes.add("non_provenance_page_spec_delta")
    if _context_payload(baseline_context) != baseline_before:
        raise NegativeControlInputError("baseline context mutated during deterministic builders")

    body = {
        "baseline_context_sha256": _context_sha256(baseline_context),
        "baseline_decision_ids": list(_decision_ids(baseline_decisions)),
        "baseline_decision_projection_sha256": baseline_projection_sha256,
        "baseline_guidance_sha256": _sha256_bytes(_canonical_json_bytes(baseline_guidance.to_dict())),
        "baseline_page_spec_sha256": baseline_page_sha256,
        "case_id": spec.case_id,
        "controlled_context_sha256": _context_sha256(controlled_context),
        "controlled_decision_ids": list(_decision_ids(controlled_decisions)),
        "controlled_decision_projection_sha256": controlled_projection_sha256,
        "controlled_guidance_sha256": _sha256_bytes(_canonical_json_bytes(controlled_guidance.to_dict())),
        "controlled_page_spec_sha256": _page_spec_sha256(controlled_guided.page_spec),
        "expected_decision_projection_sha256": spec.baseline_decision_projection_sha256,
        "expected_outcome": spec.expected_outcome,
        "failure_codes": sorted(failure_codes),
        "full_page_spec_diff_paths": list(diff_paths),
        "full_page_spec_identity": full_identity,
        "injected_decision_dispositions": sorted((decision.decision_id, disposition) for disposition, decision in injected_decisions),
        "injected_decision_ids": sorted(decision.decision_id for _, decision in injected_decisions),
        "injected_doc_ids": sorted(injected_doc_ids),
        "injected_guidance_ids": sorted(item.guidance_id for item in injected_guidance),
        "injected_result_sha256s": sorted(item.sha256 for item in spec.injected_results),
        "provenance_only_delta": provenance_only_delta,
        "role": spec.role,
        "schema_version": NEGATIVE_CONTROL_REPORT_SCHEMA_VERSION,
        "spec_sha256": spec.sha256(),
        "stable_decision_projection_sha256": baseline_projection_sha256 if baseline_projection_sha256 == controlled_projection_sha256 else None,
        "status": PASSED if not failure_codes else NEGATIVE_CONTROL_FAILED,
    }
    report = NegativeControlReport(
        report_id=_record_id("negative-control-report", body),
        spec_sha256=body["spec_sha256"],
        case_id=body["case_id"],
        role=body["role"],
        expected_outcome=body["expected_outcome"],
        status=body["status"],
        failure_codes=tuple(body["failure_codes"]),
        injected_doc_ids=tuple(body["injected_doc_ids"]),
        injected_result_sha256s=tuple(body["injected_result_sha256s"]),
        injected_guidance_ids=tuple(body["injected_guidance_ids"]),
        injected_decision_ids=tuple(body["injected_decision_ids"]),
        injected_decision_dispositions=tuple(tuple(value) for value in body["injected_decision_dispositions"]),
        baseline_context_sha256=body["baseline_context_sha256"],
        controlled_context_sha256=body["controlled_context_sha256"],
        baseline_guidance_sha256=body["baseline_guidance_sha256"],
        controlled_guidance_sha256=body["controlled_guidance_sha256"],
        baseline_page_spec_sha256=body["baseline_page_spec_sha256"],
        controlled_page_spec_sha256=body["controlled_page_spec_sha256"],
        full_page_spec_identity=body["full_page_spec_identity"],
        provenance_only_delta=body["provenance_only_delta"],
        full_page_spec_diff_paths=tuple(body["full_page_spec_diff_paths"]),
        expected_decision_projection_sha256=body["expected_decision_projection_sha256"],
        baseline_decision_projection_sha256=body["baseline_decision_projection_sha256"],
        controlled_decision_projection_sha256=body["controlled_decision_projection_sha256"],
        stable_decision_projection_sha256=body["stable_decision_projection_sha256"],
        baseline_decision_ids=tuple(body["baseline_decision_ids"]),
        controlled_decision_ids=tuple(body["controlled_decision_ids"]),
        schema_version=body["schema_version"],
    )
    report.validate()
    return NegativeControlRun(
        spec=spec, report=report, baseline_context=baseline_context, controlled_context=controlled_context,
        baseline_guidance=baseline_guidance, controlled_guidance=controlled_guidance,
        baseline_guided_build=baseline_guided, controlled_guided_build=controlled_guided,
        baseline_decisions=baseline_decisions, controlled_decisions=controlled_decisions,
    )


def build_passing_negative_control_ignored_evidence_misattribution_fault_copy(
    *,
    run: NegativeControlRun,
    inspector_fact_set: InspectorFactSet,
    page_spec: PageSpec,
    source_ref_id: str,
    page_spec_entity_id: str,
    page_spec_field_name: str,
    output_dir: Path,
) -> FaultCopyBuildResult:
    """Bind the approved D03-4 misattribution fixture to one passed M2-07a run.

    This evaluator-side wrapper is the only API that proves the source fact is
    derived from this run's injected negative-control evidence. It writes no
    control artifact into the runtime fragment or detector-visible bundle; the
    returned build remains the existing injector-only primitive result.
    """
    if not isinstance(run, NegativeControlRun):
        raise NegativeControlError("misattribution binding requires NegativeControlRun")
    if not isinstance(inspector_fact_set, InspectorFactSet):
        raise NegativeControlError("misattribution binding requires InspectorFactSet")
    if not isinstance(page_spec, PageSpec):
        raise NegativeControlError("misattribution binding requires PageSpec")
    _require_text(source_ref_id, "source_ref_id")
    _require_text(page_spec_entity_id, "page_spec_entity_id")
    _require_text(page_spec_field_name, "page_spec_field_name")
    try:
        run.spec.validate()
        run.report.validate_against_spec(run.spec)
        controlled_guidance = run.controlled_guidance
        controlled_guided = run.controlled_guided_build
        if not isinstance(controlled_guidance, RetrievalGuidance):
            raise TypeError("controlled_guidance has the wrong type")
        if not isinstance(controlled_guided, GuidedPageSpecBuildResult):
            raise TypeError("controlled_guided_build has the wrong type")
        run.controlled_context.validate()
        controlled_guidance.validate()
        controlled_guided.validate(controlled_guidance)
        page_spec.validate()
        inspector_fact_set.validate()
    except (TypeError, ValueError) as error:
        raise NegativeControlError("misattribution binding inputs are not canonical") from error
    report = run.report
    if report.status != PASSED:
        raise NegativeControlError("misattribution binding requires a passed negative control")
    if report.case_id != run.spec.case_id:
        raise NegativeControlError("misattribution run report case does not match its spec")
    if _context_sha256(run.controlled_context) != report.controlled_context_sha256:
        raise NegativeControlError("controlled context does not match the passed report")
    if _sha256_bytes(_canonical_json_bytes(controlled_guidance.to_dict())) != report.controlled_guidance_sha256:
        raise NegativeControlError("controlled guidance does not match the passed report")
    if _page_spec_sha256(page_spec) != report.controlled_page_spec_sha256:
        raise NegativeControlError("PageSpec does not match the passed report")
    if controlled_guided.page_spec.to_dict() != page_spec.to_dict():
        raise NegativeControlError("PageSpec does not match the controlled guided build")
    if (
        inspector_fact_set.page_id != page_spec.page_id
        or inspector_fact_set.guidance_bundle_id != controlled_guidance.guidance_bundle_id
        or inspector_fact_set.guided_build_result_id != controlled_guided.build_result_id
        or inspector_fact_set.guidance_sha256
        != _sha256_bytes(_canonical_json_bytes(controlled_guidance.to_dict()))
        or inspector_fact_set.guided_build_result_sha256
        != _sha256_bytes(_canonical_json_bytes(controlled_guided.to_dict()))
        or inspector_fact_set.page_spec_sha256 != _page_spec_sha256(page_spec)
    ):
        raise NegativeControlError("Inspector facts do not bind the passed controlled G0 artifacts")
    sources_by_id = {
        item.source_ref_id: item for item in inspector_fact_set.source_refs
    }
    source = sources_by_id.get(source_ref_id)
    if (
        source is None
        or source.source_kind != "guided_retrieval"
        or source.role != run.spec.role
        or source.guidance_id is None
        or source.doc_id is None
        or source.doc_id not in report.injected_doc_ids
        or source.guidance_id not in report.injected_guidance_ids
    ):
        raise NegativeControlError("requested source is not this passed control's injected guidance")
    injected_dispositions = dict(report.injected_decision_dispositions)
    facts = [
        item
        for item in inspector_fact_set.facts
        if item.source_ref_id == source_ref_id
    ]
    if len(facts) != 1:
        raise NegativeControlError("requested injected source must bind exactly one Inspector fact")
    fact = facts[0]
    if (
        fact.disposition != "ignored"
        or fact.trace_link_ids
        or fact.decision_id not in report.injected_decision_ids
        or injected_dispositions.get(fact.decision_id) != "ignored"
    ):
        raise NegativeControlError("requested Inspector fact is not this control's ignored decision")
    decisions = [
        item for item in controlled_guided.ignored if item.decision_id == fact.decision_id
    ]
    if len(decisions) != 1:
        raise NegativeControlError("requested ignored decision is absent from the controlled guided build")
    decision = decisions[0]
    if (
        decision.source_kind != "retrieval_guidance"
        or decision.guidance_id != source.guidance_id
        or decision.doc_id != source.doc_id
        or decision.affected_fields
    ):
        raise NegativeControlError("requested ignored decision is not an empty injected retrieval decision")
    context_doc_ids = {
        str(item["doc_id"])
        for item in run.controlled_context.retrieval_results[run.spec.role]
    }
    if source.doc_id not in context_doc_ids:
        raise NegativeControlError("requested injected source is absent from the controlled context")
    request = FaultMutationRequest(
        case_id=run.spec.case_id,
        mutation_kind=IGNORED_EVIDENCE_MISATTRIBUTED_TO_PAGE_SPEC,
        target_id=source_ref_id,
        page_spec_entity_id=page_spec_entity_id,
        page_spec_field_name=page_spec_field_name,
    )
    try:
        return build_fault_copy(
            request,
            inspector_fact_set,
            output_dir,
            page_spec=page_spec,
            guided_build_result=controlled_guided,
        )
    except (TypeError, ValueError) as error:
        raise NegativeControlError("misattribution injector primitive rejected bound inputs") from error


def _reject_output_traversal(output_dir: Path) -> None:
    if any(part == ".." for part in output_dir.parts):
        raise NegativeControlError("negative-control output_dir must not contain '..' traversal")


def _reject_symlink_ancestors(path: Path) -> None:
    for candidate in (path.absolute(), *path.absolute().parents):
        if candidate.exists() and candidate.is_symlink():
            raise NegativeControlError("symlinked negative-control output paths are not allowed")


def _prepare_output_paths(output_dir: Path) -> tuple[Path, Path]:
    raw = Path(output_dir)
    _reject_output_traversal(raw)
    destination = raw.absolute()
    _reject_symlink_ancestors(destination)
    if destination.exists():
        raise NegativeControlError("negative-control output_dir already exists; refusing to overwrite")
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_ancestors(destination)
    staging = parent / ("." + destination.name + ".staging-" + uuid.uuid4().hex)
    if staging.exists():
        raise NegativeControlError("negative-control staging path already exists")
    return destination, staging


def _remove_owned_directory(
    path: Path,
    *,
    parent: Path,
    expected_name: str,
    require_complete_inventory: bool,
) -> None:
    """Remove only this writer's exact sibling output after strict safety checks."""
    if not path.exists():
        return
    if path.parent != parent or path.name != expected_name or path.is_symlink() or not path.is_dir():
        raise NegativeControlError("refusing unsafe negative-control cleanup")
    _reject_symlink_ancestors(path)
    children = tuple(path.iterdir())
    if any(child.is_symlink() or child.is_dir() or not child.is_file() for child in children):
        raise NegativeControlError("refusing cleanup of an unexpected negative-control directory")
    names = {child.name for child in children}
    allowed = {_SPEC_FILE, _REPORT_FILE, _MANIFEST_FILE}
    if not names.issubset(allowed) or (require_complete_inventory and names != allowed):
        raise NegativeControlError("refusing cleanup of an unrecognized negative-control directory")
    shutil.rmtree(path)


def _write_exact_bytes(path: Path, content: bytes) -> None:
    _reject_symlink_ancestors(path.parent)
    if path.exists():
        raise NegativeControlError("negative-control writer refuses to overwrite a file")
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise NegativeControlError("negative-control writer did not round-trip exact bytes")


def _validate_static_artifact_directory(root: Path) -> tuple[NegativeControlSpec, NegativeControlReport]:
    """Validate only exact files, canonical JSON, hashes, and local spec/report binding."""
    if not root.exists() or root.is_symlink() or not root.is_dir():
        raise NegativeControlError("negative-control artifact directory must be a real directory")
    children = tuple(sorted(root.iterdir(), key=lambda item: item.name))
    if any(child.is_symlink() or not child.is_file() for child in children):
        raise NegativeControlError("negative-control artifact directory must contain only real files")
    if tuple(child.name for child in children) != _REQUIRED_FILES:
        raise NegativeControlError("negative-control artifact has an unexpected file inventory")
    spec = NegativeControlSpec.from_dict(_load_canonical_json(root / _SPEC_FILE))
    report = NegativeControlReport.from_dict(_load_canonical_json(root / _REPORT_FILE))
    report.validate_against_spec(spec)
    manifest = _load_canonical_json(root / _MANIFEST_FILE)
    if set(manifest) != {"files", "report_sha256", "schema_version", "spec_sha256"}:
        raise NegativeControlError("negative-control manifest has an unexpected field set")
    if manifest.get("schema_version") != NEGATIVE_CONTROL_MANIFEST_SCHEMA_VERSION:
        raise NegativeControlError("negative-control manifest schema is unsupported")
    if manifest.get("spec_sha256") != spec.sha256() or manifest.get("report_sha256") != report.sha256():
        raise NegativeControlError("negative-control manifest identity binding is invalid")
    entries = manifest.get("files")
    expected_entries = [
        {"path": _REPORT_FILE, "sha256": _sha256_bytes((root / _REPORT_FILE).read_bytes()), "size": (root / _REPORT_FILE).stat().st_size},
        {"path": _SPEC_FILE, "sha256": _sha256_bytes((root / _SPEC_FILE).read_bytes()), "size": (root / _SPEC_FILE).stat().st_size},
    ]
    if entries != expected_entries:
        raise NegativeControlError("negative-control manifest does not match exact artifact bytes")
    return spec, report


def write_negative_control_artifact(spec: NegativeControlSpec, report: NegativeControlReport, output_dir: Path) -> NegativeControlReport:
    """Atomically publish canonical pre-registration/report/manifest files."""
    if not isinstance(spec, NegativeControlSpec) or not isinstance(report, NegativeControlReport):
        raise NegativeControlError("writer requires NegativeControlSpec and NegativeControlReport")
    spec.validate()
    report.validate_against_spec(spec)
    files = {_SPEC_FILE: spec.canonical_json_bytes(), _REPORT_FILE: report.canonical_json_bytes()}
    manifest = {
        "files": [{"path": path, "sha256": _sha256_bytes(content), "size": len(content)} for path, content in sorted(files.items())],
        "report_sha256": report.sha256(),
        "schema_version": NEGATIVE_CONTROL_MANIFEST_SCHEMA_VERSION,
        "spec_sha256": spec.sha256(),
    }
    manifest_bytes = _canonical_json_bytes(manifest)
    destination, staging = _prepare_output_paths(Path(output_dir))
    committed = False
    try:
        staging.mkdir()
        _write_exact_bytes(staging / _SPEC_FILE, files[_SPEC_FILE])
        _write_exact_bytes(staging / _REPORT_FILE, files[_REPORT_FILE])
        _write_exact_bytes(staging / _MANIFEST_FILE, manifest_bytes)
        _validate_static_artifact_directory(staging)
        os.replace(staging, destination)
        committed = True
        _validate_static_artifact_directory(destination)
        return report
    except Exception:
        if staging.exists():
            _remove_owned_directory(
                staging,
                parent=destination.parent,
                expected_name=staging.name,
                require_complete_inventory=False,
            )
        if committed and destination.exists():
            _remove_owned_directory(
                destination,
                parent=destination.parent,
                expected_name=destination.name,
                require_complete_inventory=True,
            )
        raise


def _load_canonical_json(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    if content.startswith(b"\xef\xbb\xbf"):
        raise NegativeControlError(path.name + " must not contain a UTF-8 BOM")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NegativeControlError(path.name + " is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict) or _canonical_json_bytes(payload) != content:
        raise NegativeControlError(path.name + " is not exact canonical JSON")
    return payload


def validate_negative_control_artifact(output_dir: Path, baseline_context: AgentContextBundle) -> NegativeControlReport:
    """Rebind a written artifact to builders, exact JSON, hashes, and inventory."""
    raw = Path(output_dir)
    _reject_output_traversal(raw)
    root = raw.absolute()
    _reject_symlink_ancestors(root)
    spec, report = _validate_static_artifact_directory(root)
    rebound = run_negative_control(spec, baseline_context).report
    if rebound.to_dict() != report.to_dict():
        raise NegativeControlError("negative-control report does not rebind to the supplied baseline/builders")
    return report


__all__ = [
    "EXPECTED_OUTCOME", "NEGATIVE_CONTROL_FAILED", "NEGATIVE_CONTROL_MANIFEST_SCHEMA_VERSION",
    "NEGATIVE_CONTROL_REPORT_SCHEMA_VERSION", "NEGATIVE_CONTROL_SPEC_SCHEMA_VERSION", "PASSED",
    "InjectedRetrievalResult", "NegativeControlError", "NegativeControlInputError", "NegativeControlReport",
    "NegativeControlRun", "NegativeControlSpec", "run_negative_control", "validate_negative_control_artifact",
    "write_negative_control_artifact",
]
