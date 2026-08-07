"""Evidence-only semantic alignment contracts for browser acceptance.

Phase 4 creates immutable review requests but does not invoke an evaluator.
Later phases may attach one evaluator result to the exact frozen request.  The
evaluator has no browser control and cannot override objective browser or
PageSpec-conformance failures.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from req2web_generation.schema import PageSpec

from .acceptance_plan import AcceptancePlan
from .binding import AcceptanceBindingPlan
from .browser_executor import BrowserExecutionReport


SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION = (
    "req2web.acceptance.semantic_alignment_request.v1"
)
SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION = (
    "req2web.acceptance.semantic_alignment_result.v1"
)
SEMANTIC_ALIGNMENT_CONTRACT_REVISION = (
    "evidence_only_no_browser_control_no_retry_v1"
)
SEMANTIC_ALIGNMENT_VERDICTS = (
    "supported",
    "violated",
    "partial",
    "unknown",
)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVIDENCE_ROLES = {
    "acceptance_binding",
    "acceptance_plan",
    "browser_execution_report",
    "browser_screenshot",
    "page_spec",
    "result_package_manifest",
}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _sha256_prefixed(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{field_name} must be a sha256: prefixed digest")
    return text


def _plain_sha256(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return text


def _sorted_unique_text(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    if not allow_empty and not value:
        raise ValueError(f"{field_name} must not be empty")
    result = tuple(_text(item, f"{field_name} item") for item in value)
    if result != tuple(sorted(set(result))):
        raise ValueError(f"{field_name} must be sorted and unique")
    return result


@dataclass(frozen=True)
class SemanticAlignmentEvidence:
    evidence_id: str
    artifact_role: str
    identity_kind: str
    sha256: str
    byte_length: int
    revision: str

    def validate(self) -> None:
        _text(self.evidence_id, "evidence.evidence_id")
        if self.artifact_role not in _EVIDENCE_ROLES:
            raise ValueError("evidence.artifact_role is unsupported")
        if self.identity_kind not in {"canonical_json", "raw_bytes"}:
            raise ValueError("evidence.identity_kind is unsupported")
        _sha256_prefixed(self.sha256, "evidence.sha256")
        if (
            not isinstance(self.byte_length, int)
            or isinstance(self.byte_length, bool)
            or self.byte_length < 0
        ):
            raise ValueError("evidence.byte_length must be a non-negative integer")
        _text(self.revision, "evidence.revision")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "artifact_role": self.artifact_role,
            "byte_length": self.byte_length,
            "evidence_id": self.evidence_id,
            "identity_kind": self.identity_kind,
            "revision": self.revision,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class SemanticAlignmentReviewItem:
    criterion_id: str
    use_case_id: str
    abstract_expected_outcome: str
    interaction_id: str
    observed_user_feedback: str
    evidence_ids: tuple[str, ...]

    def validate(self) -> None:
        for field_name in (
            "criterion_id",
            "use_case_id",
            "abstract_expected_outcome",
            "interaction_id",
            "observed_user_feedback",
        ):
            _text(getattr(self, field_name), f"review_item.{field_name}")
        _sorted_unique_text(self.evidence_ids, "review_item.evidence_ids")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "abstract_expected_outcome": self.abstract_expected_outcome,
            "criterion_id": self.criterion_id,
            "evidence_ids": list(self.evidence_ids),
            "interaction_id": self.interaction_id,
            "observed_user_feedback": self.observed_user_feedback,
            "use_case_id": self.use_case_id,
        }


@dataclass(frozen=True)
class SemanticAlignmentRequest:
    schema_version: str
    contract_revision: str
    case_id: str
    source_acceptance_plan_sha256: str
    source_binding_plan_sha256: str
    source_browser_execution_sha256: str
    source_page_spec_sha256: str
    browser_control_allowed: bool
    model_generate_call_limit: int
    automatic_retry_limit: int
    allowed_verdicts: tuple[str, ...]
    response_schema_version: str
    evidence: tuple[SemanticAlignmentEvidence, ...]
    review_items: tuple[SemanticAlignmentReviewItem, ...]

    def validate(self) -> None:
        if self.schema_version != SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION:
            raise ValueError("semantic alignment request schema is unsupported")
        if self.contract_revision != SEMANTIC_ALIGNMENT_CONTRACT_REVISION:
            raise ValueError("semantic alignment contract revision drifted")
        _text(self.case_id, "request.case_id")
        for field_name in (
            "source_acceptance_plan_sha256",
            "source_binding_plan_sha256",
            "source_browser_execution_sha256",
            "source_page_spec_sha256",
        ):
            _plain_sha256(getattr(self, field_name), f"request.{field_name}")
        if self.browser_control_allowed is not False:
            raise ValueError("semantic alignment evaluator must not control the browser")
        if self.model_generate_call_limit != 1 or self.automatic_retry_limit != 0:
            raise ValueError("semantic alignment request must allow one call and no retry")
        if self.allowed_verdicts != SEMANTIC_ALIGNMENT_VERDICTS:
            raise ValueError("semantic alignment verdict order drifted")
        if self.response_schema_version != SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION:
            raise ValueError("semantic alignment response schema drifted")
        if not isinstance(self.evidence, tuple) or not self.evidence:
            raise ValueError("semantic alignment evidence must not be empty")
        evidence_ids: list[str] = []
        evidence_roles: set[str] = set()
        for item in self.evidence:
            if not isinstance(item, SemanticAlignmentEvidence):
                raise ValueError("semantic alignment evidence contains an invalid item")
            item.validate()
            evidence_ids.append(item.evidence_id)
            evidence_roles.add(item.artifact_role)
        if evidence_ids != sorted(set(evidence_ids)):
            raise ValueError("semantic alignment evidence must be sorted and unique")
        if not {
            "acceptance_binding",
            "acceptance_plan",
            "browser_execution_report",
            "browser_screenshot",
            "page_spec",
            "result_package_manifest",
        }.issubset(evidence_roles):
            raise ValueError("semantic alignment evidence inventory is incomplete")
        if not isinstance(self.review_items, tuple) or not self.review_items:
            raise ValueError("semantic alignment review_items must not be empty")
        criterion_ids: list[str] = []
        known_evidence_ids = set(evidence_ids)
        for item in self.review_items:
            if not isinstance(item, SemanticAlignmentReviewItem):
                raise ValueError("semantic alignment review_items contains an invalid item")
            item.validate()
            if not set(item.evidence_ids).issubset(known_evidence_ids):
                raise ValueError("semantic alignment item references unknown evidence")
            criterion_ids.append(item.criterion_id)
        if criterion_ids != sorted(set(criterion_ids)):
            raise ValueError("semantic alignment review_items must be sorted and unique")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "allowed_verdicts": list(self.allowed_verdicts),
            "automatic_retry_limit": self.automatic_retry_limit,
            "browser_control_allowed": self.browser_control_allowed,
            "case_id": self.case_id,
            "contract_revision": self.contract_revision,
            "evidence": [item.to_dict() for item in self.evidence],
            "model_generate_call_limit": self.model_generate_call_limit,
            "response_schema_version": self.response_schema_version,
            "review_items": [item.to_dict() for item in self.review_items],
            "schema_version": self.schema_version,
            "source_acceptance_plan_sha256": self.source_acceptance_plan_sha256,
            "source_binding_plan_sha256": self.source_binding_plan_sha256,
            "source_browser_execution_sha256": self.source_browser_execution_sha256,
            "source_page_spec_sha256": self.source_page_spec_sha256,
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


@dataclass(frozen=True)
class SemanticAlignmentVerdict:
    criterion_id: str
    verdict: str
    evidence_ids: tuple[str, ...]
    reason_summary: str
    limitations: str

    def validate(self) -> None:
        _text(self.criterion_id, "verdict.criterion_id")
        if self.verdict not in SEMANTIC_ALIGNMENT_VERDICTS:
            raise ValueError("semantic alignment verdict is unsupported")
        _sorted_unique_text(self.evidence_ids, "verdict.evidence_ids")
        _text(self.reason_summary, "verdict.reason_summary")
        _text(self.limitations, "verdict.limitations")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "criterion_id": self.criterion_id,
            "evidence_ids": list(self.evidence_ids),
            "limitations": self.limitations,
            "reason_summary": self.reason_summary,
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class SemanticAlignmentResult:
    schema_version: str
    request_sha256: str
    evaluator_model_identity: str
    evaluator_prompt_revision: str
    raw_response_sha256: str
    raw_response_byte_length: int
    model_generate_calls: int
    automatic_retry_count: int
    verdicts: tuple[SemanticAlignmentVerdict, ...]

    def validate_against(self, request: SemanticAlignmentRequest) -> None:
        request.validate()
        if self.schema_version != SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION:
            raise ValueError("semantic alignment result schema is unsupported")
        if self.request_sha256 != request.sha256():
            raise ValueError("semantic alignment result request identity drifted")
        _text(self.evaluator_model_identity, "result.evaluator_model_identity")
        _text(self.evaluator_prompt_revision, "result.evaluator_prompt_revision")
        _plain_sha256(self.raw_response_sha256, "result.raw_response_sha256")
        if (
            not isinstance(self.raw_response_byte_length, int)
            or isinstance(self.raw_response_byte_length, bool)
            or self.raw_response_byte_length <= 0
        ):
            raise ValueError("result.raw_response_byte_length must be positive")
        if self.model_generate_calls != 1 or self.automatic_retry_count != 0:
            raise ValueError("semantic alignment result must bind one call and no retry")
        if not isinstance(self.verdicts, tuple):
            raise ValueError("semantic alignment verdicts must be a tuple")
        expected_items = {item.criterion_id: item for item in request.review_items}
        received_ids: list[str] = []
        for verdict in self.verdicts:
            if not isinstance(verdict, SemanticAlignmentVerdict):
                raise ValueError("semantic alignment result contains an invalid verdict")
            verdict.validate()
            item = expected_items.get(verdict.criterion_id)
            if item is None:
                raise ValueError("semantic alignment result references an unknown criterion")
            if not set(verdict.evidence_ids).issubset(set(item.evidence_ids)):
                raise ValueError("semantic alignment verdict references unbound evidence")
            received_ids.append(verdict.criterion_id)
        if received_ids != sorted(expected_items):
            raise ValueError("semantic alignment result must cover each criterion exactly once")

    def to_dict(self, request: SemanticAlignmentRequest) -> dict[str, object]:
        self.validate_against(request)
        return {
            "automatic_retry_count": self.automatic_retry_count,
            "evaluator_model_identity": self.evaluator_model_identity,
            "evaluator_prompt_revision": self.evaluator_prompt_revision,
            "model_generate_calls": self.model_generate_calls,
            "raw_response_byte_length": self.raw_response_byte_length,
            "raw_response_sha256": self.raw_response_sha256,
            "request_sha256": self.request_sha256,
            "schema_version": self.schema_version,
            "verdicts": [item.to_dict() for item in self.verdicts],
        }


def _identity_evidence(
    evidence_id: str,
    artifact_role: str,
    identity: Mapping[str, object],
) -> SemanticAlignmentEvidence:
    expected = {"identity_kind", "sha256", "byte_length", "revision"}
    if set(identity) != expected:
        raise ValueError(f"{evidence_id} identity exact keys drifted")
    return SemanticAlignmentEvidence(
        evidence_id=evidence_id,
        artifact_role=artifact_role,
        identity_kind=str(identity["identity_kind"]),
        sha256=str(identity["sha256"]),
        byte_length=identity["byte_length"],  # type: ignore[arg-type]
        revision=str(identity["revision"]),
    )


def build_semantic_alignment_request(
    *,
    case_id: str,
    acceptance_plan: AcceptancePlan,
    binding_plan: AcceptanceBindingPlan,
    browser_report: BrowserExecutionReport,
    page_spec: PageSpec,
    evidence_identities: Mapping[str, tuple[str, Mapping[str, object]]],
) -> SemanticAlignmentRequest:
    """Freeze semantic-review inputs after objective browser checks pass."""

    acceptance_plan.validate()
    binding_plan.validate()
    page_spec.validate()
    browser_report.validate_against(binding_plan)
    if (
        binding_plan.source_acceptance_plan_sha256 != acceptance_plan.sha256()
        or binding_plan.source_page_spec_sha256
        != sha256(_canonical_json_bytes(page_spec.to_dict())).hexdigest()
        or browser_report.source_binding_plan_sha256 != binding_plan.sha256()
    ):
        raise ValueError("semantic alignment source identity drifted")

    expected_evidence_ids = {
        "acceptance_binding",
        "acceptance_plan",
        "browser_execution_report",
        "browser_screenshot",
        "page_spec",
        "result_package_manifest",
    }
    if set(evidence_identities) != expected_evidence_ids:
        raise ValueError("semantic alignment evidence identity set drifted")
    evidence = tuple(
        sorted(
            (
                _identity_evidence(evidence_id, role, identity)
                for evidence_id, (role, identity) in evidence_identities.items()
            ),
            key=lambda item: item.evidence_id,
        )
    )
    common_evidence_ids = tuple(item.evidence_id for item in evidence)
    criteria_by_id = {
        item.criterion_id: item
        for item in acceptance_plan.criteria
        if item.source_kind == "use_case"
        and item.semantic_kind == "expected_outcome"
    }
    bindings_by_id = {
        item.criterion_id: item for item in binding_plan.bindings
    }
    steps_by_id = {item.step_id: item for item in binding_plan.steps}
    browser_by_id = {
        item.criterion_id: item for item in browser_report.criteria
    }
    interactions_by_id = {
        item.interaction_id: item for item in page_spec.interactions
    }
    review_items: list[SemanticAlignmentReviewItem] = []
    for criterion_id in sorted(criteria_by_id):
        criterion = criteria_by_id[criterion_id]
        runtime = browser_by_id.get(criterion_id)
        binding = bindings_by_id.get(criterion_id)
        if runtime is None or binding is None or binding.disposition != "bound":
            raise ValueError("semantic alignment criterion lacks an executable binding")
        if runtime.status != "pass":
            raise ValueError(
                "semantic alignment cannot override a non-passing browser criterion"
            )
        trigger_steps = [
            steps_by_id[step_id]
            for step_id in binding.step_ids
            if steps_by_id[step_id].action_kind == "trigger_interaction"
        ]
        feedback_steps = [
            steps_by_id[step_id]
            for step_id in binding.step_ids
            if steps_by_id[step_id].action_kind == "assert_feedback"
        ]
        if not trigger_steps or len(feedback_steps) != 1:
            raise ValueError("semantic alignment criterion has an invalid step shape")
        interaction = interactions_by_id.get(trigger_steps[-1].target_id)
        if interaction is None:
            raise ValueError("semantic alignment interaction is missing")
        feedback_payload = dict(feedback_steps[0].expected_payload)
        if (
            feedback_steps[0].source != "page_spec.interactions.user_feedback"
            or feedback_payload.get("feedback") != interaction.user_feedback
        ):
            raise ValueError("semantic alignment feedback is not PageSpec-grounded")
        expected_payload = dict(criterion.expected_payload)
        review_items.append(
            SemanticAlignmentReviewItem(
                criterion_id=criterion_id,
                use_case_id=str(expected_payload["use_case_id"]),
                abstract_expected_outcome=str(
                    expected_payload["expected_outcome"]
                ),
                interaction_id=interaction.interaction_id,
                observed_user_feedback=interaction.user_feedback,
                evidence_ids=common_evidence_ids,
            )
        )
    request = SemanticAlignmentRequest(
        schema_version=SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION,
        contract_revision=SEMANTIC_ALIGNMENT_CONTRACT_REVISION,
        case_id=_text(case_id, "case_id"),
        source_acceptance_plan_sha256=acceptance_plan.sha256(),
        source_binding_plan_sha256=binding_plan.sha256(),
        source_browser_execution_sha256=browser_report.sha256(),
        source_page_spec_sha256=binding_plan.source_page_spec_sha256,
        browser_control_allowed=False,
        model_generate_call_limit=1,
        automatic_retry_limit=0,
        allowed_verdicts=SEMANTIC_ALIGNMENT_VERDICTS,
        response_schema_version=SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
        evidence=evidence,
        review_items=tuple(review_items),
    )
    request.validate()
    return request


__all__ = [
    "SEMANTIC_ALIGNMENT_CONTRACT_REVISION",
    "SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION",
    "SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION",
    "SEMANTIC_ALIGNMENT_VERDICTS",
    "SemanticAlignmentEvidence",
    "SemanticAlignmentRequest",
    "SemanticAlignmentResult",
    "SemanticAlignmentReviewItem",
    "SemanticAlignmentVerdict",
    "build_semantic_alignment_request",
]
