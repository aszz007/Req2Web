"""No-model preparation and strict parsing for Phase 5 semantic evaluation.

Phase 4 freezes a SemanticAlignmentRequest and objective browser evidence.
This module validates those exact evidence bytes, builds the only eligible
Phase 5 evaluator prompt, and parses one future raw response.  It does not
load or call a model, control a browser, retry, repair, or change an objective
browser/PageSpec-conformance result.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping

from req2web_acceptance import (
    SEMANTIC_ALIGNMENT_CONTRACT_REVISION,
    SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION,
    SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
    SEMANTIC_ALIGNMENT_VERDICTS,
    SemanticAlignmentEvidence,
    SemanticAlignmentRequest,
    SemanticAlignmentResult,
    SemanticAlignmentReviewItem,
    SemanticAlignmentVerdict,
)
from req2web_generation import (
    PublicationLanguageError,
    validate_english_publication_value,
)


SEMANTIC_EVALUATOR_PROMPT_SCHEMA_VERSION = (
    "req2web.phase5.semantic_evaluator_prompt.v1"
)
SEMANTIC_EVALUATOR_PROMPT_REVISION = (
    "frozen_evidence_single_call_no_browser_control_v2"
)
SEMANTIC_EVALUATOR_NO_ACTION_SCHEMA_VERSION = (
    "req2web.phase5.semantic_evaluator_no_action.v1"
)
SEMANTIC_EVALUATOR_EXECUTION_STATUS = "prepared_no_action"


class Phase5SemanticEvaluatorError(ValueError):
    """Raised when evaluator preparation or raw-result parsing drifts."""


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
        raise Phase5SemanticEvaluatorError(
            "semantic evaluator artifact is not canonical JSON"
        ) from exc


def _strict_json(raw: bytes, name: str) -> object:
    if type(raw) is not bytes or not raw:
        raise Phase5SemanticEvaluatorError(f"{name} is empty")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise Phase5SemanticEvaluatorError(f"{name} has a UTF-8 BOM")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5SemanticEvaluatorError(f"{name} is not JSON") from exc
    if _canonical(value) != raw:
        raise Phase5SemanticEvaluatorError(f"{name} is not canonical JSON")
    return value


def _raw_json(raw: bytes, name: str) -> object:
    if type(raw) is not bytes or not raw:
        raise Phase5SemanticEvaluatorError(f"{name} is empty")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise Phase5SemanticEvaluatorError(f"{name} has a UTF-8 BOM")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase5SemanticEvaluatorError(f"{name} is not JSON") from exc


def _identity(
    raw: bytes,
    *,
    revision: str,
    identity_kind: str = "raw_bytes",
) -> dict[str, object]:
    if type(raw) is not bytes:
        raise Phase5SemanticEvaluatorError("identity input must be bytes")
    return {
        "identity_kind": identity_kind,
        "sha256": "sha256:" + sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "revision": revision,
    }


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Phase5SemanticEvaluatorError(
            f"{name} must be a non-empty string"
        )
    return value


def load_phase5_semantic_alignment_request(
    raw: bytes,
) -> SemanticAlignmentRequest:
    """Load one exact canonical Phase 4 semantic-alignment request."""

    value = _strict_json(raw, "semantic alignment request")
    if not isinstance(value, Mapping):
        raise Phase5SemanticEvaluatorError(
            "semantic alignment request must be an object"
        )
    try:
        validate_english_publication_value(
            value,
            artifact_name="semantic_alignment_request",
        )
    except PublicationLanguageError as exc:
        raise Phase5SemanticEvaluatorError(
            "semantic alignment request is not English-only"
        ) from exc
    data = dict(value)
    expected_keys = {
        "allowed_verdicts",
        "automatic_retry_limit",
        "browser_control_allowed",
        "case_id",
        "contract_revision",
        "evidence",
        "model_generate_call_limit",
        "response_schema_version",
        "review_items",
        "schema_version",
        "source_acceptance_plan_sha256",
        "source_binding_plan_sha256",
        "source_browser_execution_sha256",
        "source_page_spec_sha256",
    }
    if set(data) != expected_keys:
        raise Phase5SemanticEvaluatorError(
            "semantic alignment request exact keys drifted"
        )
    evidence_rows = data["evidence"]
    review_rows = data["review_items"]
    if not isinstance(evidence_rows, list) or not isinstance(
        review_rows,
        list,
    ):
        raise Phase5SemanticEvaluatorError(
            "semantic alignment request rows are invalid"
        )
    evidence: list[SemanticAlignmentEvidence] = []
    for index, row in enumerate(evidence_rows):
        if not isinstance(row, Mapping) or set(row) != {
            "artifact_role",
            "byte_length",
            "evidence_id",
            "identity_kind",
            "revision",
            "sha256",
        }:
            raise Phase5SemanticEvaluatorError(
                f"semantic alignment evidence {index} exact keys drifted"
            )
        evidence.append(
            SemanticAlignmentEvidence(
                evidence_id=_text(
                    row["evidence_id"],
                    f"evidence {index}.evidence_id",
                ),
                artifact_role=_text(
                    row["artifact_role"],
                    f"evidence {index}.artifact_role",
                ),
                identity_kind=_text(
                    row["identity_kind"],
                    f"evidence {index}.identity_kind",
                ),
                sha256=_text(
                    row["sha256"],
                    f"evidence {index}.sha256",
                ),
                byte_length=row["byte_length"],  # type: ignore[arg-type]
                revision=_text(
                    row["revision"],
                    f"evidence {index}.revision",
                ),
            )
        )
    review_items: list[SemanticAlignmentReviewItem] = []
    for index, row in enumerate(review_rows):
        if not isinstance(row, Mapping) or set(row) != {
            "abstract_expected_outcome",
            "criterion_id",
            "evidence_ids",
            "interaction_id",
            "observed_user_feedback",
            "use_case_id",
        }:
            raise Phase5SemanticEvaluatorError(
                f"semantic alignment review item {index} exact keys drifted"
            )
        evidence_ids = row["evidence_ids"]
        if not isinstance(evidence_ids, list):
            raise Phase5SemanticEvaluatorError(
                f"semantic alignment review item {index} evidence_ids drifted"
            )
        review_items.append(
            SemanticAlignmentReviewItem(
                criterion_id=_text(
                    row["criterion_id"],
                    f"review item {index}.criterion_id",
                ),
                use_case_id=_text(
                    row["use_case_id"],
                    f"review item {index}.use_case_id",
                ),
                abstract_expected_outcome=_text(
                    row["abstract_expected_outcome"],
                    f"review item {index}.abstract_expected_outcome",
                ),
                interaction_id=_text(
                    row["interaction_id"],
                    f"review item {index}.interaction_id",
                ),
                observed_user_feedback=_text(
                    row["observed_user_feedback"],
                    f"review item {index}.observed_user_feedback",
                ),
                evidence_ids=tuple(
                    _text(item, f"review item {index}.evidence_id")
                    for item in evidence_ids
                ),
            )
        )
    allowed = data["allowed_verdicts"]
    if not isinstance(allowed, list):
        raise Phase5SemanticEvaluatorError(
            "semantic alignment allowed verdicts drifted"
        )
    request = SemanticAlignmentRequest(
        schema_version=_text(data["schema_version"], "schema_version"),
        contract_revision=_text(
            data["contract_revision"],
            "contract_revision",
        ),
        case_id=_text(data["case_id"], "case_id"),
        source_acceptance_plan_sha256=_text(
            data["source_acceptance_plan_sha256"],
            "source_acceptance_plan_sha256",
        ),
        source_binding_plan_sha256=_text(
            data["source_binding_plan_sha256"],
            "source_binding_plan_sha256",
        ),
        source_browser_execution_sha256=_text(
            data["source_browser_execution_sha256"],
            "source_browser_execution_sha256",
        ),
        source_page_spec_sha256=_text(
            data["source_page_spec_sha256"],
            "source_page_spec_sha256",
        ),
        browser_control_allowed=data["browser_control_allowed"],  # type: ignore[arg-type]
        model_generate_call_limit=data["model_generate_call_limit"],  # type: ignore[arg-type]
        automatic_retry_limit=data["automatic_retry_limit"],  # type: ignore[arg-type]
        allowed_verdicts=tuple(
            _text(item, "allowed verdict") for item in allowed
        ),
        response_schema_version=_text(
            data["response_schema_version"],
            "response_schema_version",
        ),
        evidence=tuple(evidence),
        review_items=tuple(review_items),
    )
    request.validate()
    if (
        request.schema_version
        != SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION
        or request.contract_revision
        != SEMANTIC_ALIGNMENT_CONTRACT_REVISION
        or request.allowed_verdicts != SEMANTIC_ALIGNMENT_VERDICTS
    ):
        raise Phase5SemanticEvaluatorError(
            "semantic alignment request authority drifted"
        )
    return request


def validate_phase5_semantic_evidence_payloads(
    *,
    request: SemanticAlignmentRequest,
    evidence_payloads: Mapping[str, bytes],
) -> dict[str, bytes]:
    """Validate every frozen artifact byte against the Phase 4 request."""

    request.validate()
    if not isinstance(evidence_payloads, Mapping):
        raise Phase5SemanticEvaluatorError(
            "semantic evidence payloads must be an object"
        )
    expected = {item.evidence_id: item for item in request.evidence}
    if set(evidence_payloads) != set(expected):
        raise Phase5SemanticEvaluatorError(
            "semantic evidence payload inventory drifted"
        )
    validated: dict[str, bytes] = {}
    for evidence_id in sorted(expected):
        raw = evidence_payloads[evidence_id]
        if type(raw) is not bytes:
            raise Phase5SemanticEvaluatorError(
                f"semantic evidence {evidence_id} must be bytes"
            )
        evidence = expected[evidence_id]
        if (
            len(raw) != evidence.byte_length
            or "sha256:" + sha256(raw).hexdigest() != evidence.sha256
        ):
            raise Phase5SemanticEvaluatorError(
                f"semantic evidence {evidence_id} identity drifted"
            )
        if evidence.identity_kind == "canonical_json":
            value = _strict_json(raw, f"semantic evidence {evidence_id}")
            try:
                validate_english_publication_value(
                    value,
                    artifact_name=f"semantic_evidence.{evidence_id}",
                )
            except PublicationLanguageError as exc:
                raise Phase5SemanticEvaluatorError(
                    f"semantic evidence {evidence_id} is not English-only"
                ) from exc
        validated[evidence_id] = raw
    return validated


def build_phase5_semantic_evaluator_prompt(
    request: SemanticAlignmentRequest,
) -> bytes:
    """Build the frozen one-call prompt without embedding artifact paths."""

    request.validate()
    return _canonical(
        {
            "schema_version": SEMANTIC_EVALUATOR_PROMPT_SCHEMA_VERSION,
            "prompt_revision": SEMANTIC_EVALUATOR_PROMPT_REVISION,
            "source_contract_revision": (
                SEMANTIC_ALIGNMENT_CONTRACT_REVISION
            ),
            "request_sha256": request.sha256(),
            "browser_control_allowed": False,
            "automatic_retry_allowed": False,
            "automatic_repair_allowed": False,
            "objective_failure_override_allowed": False,
            "output_schema_version": (
                SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION
            ),
            "allowed_verdicts": list(request.allowed_verdicts),
            "required_output_shape": {
                "schema_version": SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
                "verdicts": [
                    {
                        "criterion_id": "<exact requested criterion_id>",
                        "verdict": "<supported|violated|partial|unknown>",
                        "evidence_ids": [
                            "<only evidence IDs bound to this criterion>"
                        ],
                        "reason_summary": "<short evidence-grounded reason>",
                        "limitations": "<short limitation or none>",
                    }
                ],
            },
            "instructions": [
                (
                    "Judge only whether each abstract expected outcome is "
                    "semantically aligned with the frozen observed behavior."
                ),
                (
                    "Use only the attached evidence identified by the "
                    "criterion evidence_ids; do not infer unobserved behavior."
                ),
                (
                    "Return supported, violated, partial, or unknown for every "
                    "criterion exactly once. Evidence insufficiency is unknown."
                ),
                (
                    "Do not operate a browser, propose a repair, rewrite the "
                    "requirement/PageSpec, or override an objective failure."
                ),
            ],
            "review_items": [
                item.to_dict() for item in request.review_items
            ],
            "evidence_inventory": [
                item.to_dict() for item in request.evidence
            ],
        }
    )


def build_phase5_semantic_evaluator_no_action_bundle(
    *,
    request: SemanticAlignmentRequest,
    evidence_payloads: Mapping[str, bytes],
    generator_model_identity: str,
) -> dict[str, object]:
    """Bind prompt and evidence while keeping every action flag false."""

    validated = validate_phase5_semantic_evidence_payloads(
        request=request,
        evidence_payloads=evidence_payloads,
    )
    generator = _text(
        generator_model_identity,
        "generator_model_identity",
    )
    prompt = build_phase5_semantic_evaluator_prompt(request)
    root = {
        "schema_version": SEMANTIC_EVALUATOR_NO_ACTION_SCHEMA_VERSION,
        "status": SEMANTIC_EVALUATOR_EXECUTION_STATUS,
        "request_sha256": request.sha256(),
        "prompt_identity": _identity(
            prompt,
            revision=SEMANTIC_EVALUATOR_PROMPT_REVISION,
            identity_kind="canonical_json",
        ),
        "evidence_identities": [
            {
                "evidence_id": item.evidence_id,
                "artifact_role": item.artifact_role,
                "identity_kind": item.identity_kind,
                "sha256": item.sha256,
                "byte_length": item.byte_length,
                "revision": item.revision,
            }
            for item in request.evidence
        ],
        "validated_evidence_ids": sorted(validated),
        "generator_model_identity": generator,
        "evaluator_model_identity": generator,
        "same_model_evaluation_allowed": True,
        "cross_call_context_reuse_allowed": False,
        "browser_control_allowed": False,
        "model_generate_call_limit": 1,
        "automatic_retry_limit": 0,
        "time_cap_seconds": None,
        "cost_cap_minor_units": None,
        "storage_cap_bytes": None,
        "partial_or_unknown_requires_human_review": True,
        "objective_failure_override_allowed": False,
        "evaluator_action_eligible": False,
        "actual_model_generate_calls": 0,
        "model_loaded": False,
        "gpu_or_remote_action": False,
        "semantic_alignment_executed": False,
        "formal_quality_claimed": False,
    }
    return {
        **root,
        "bundle_identity": _identity(
            _canonical(root),
            revision=SEMANTIC_EVALUATOR_NO_ACTION_SCHEMA_VERSION,
            identity_kind="canonical_json",
        ),
    }


def validate_phase5_semantic_evaluator_no_action_bundle(
    *,
    request: SemanticAlignmentRequest,
    evidence_payloads: Mapping[str, bytes],
    value: object,
) -> dict[str, object]:
    """Replay a no-action bundle from the exact request and evidence."""

    if not isinstance(value, Mapping):
        raise Phase5SemanticEvaluatorError(
            "semantic evaluator no-action bundle must be an object"
        )
    data = copy.deepcopy(dict(value))
    generator = _text(
        data.get("generator_model_identity"),
        "generator_model_identity",
    )
    expected = build_phase5_semantic_evaluator_no_action_bundle(
        request=request,
        evidence_payloads=evidence_payloads,
        generator_model_identity=generator,
    )
    if data != expected:
        raise Phase5SemanticEvaluatorError(
            "semantic evaluator no-action bundle drifted"
        )
    return data


def parse_phase5_semantic_evaluator_raw_response(
    *,
    request: SemanticAlignmentRequest,
    raw_response: bytes,
    evaluator_model_identity: str,
    generator_model_identity: str,
) -> SemanticAlignmentResult:
    """Strictly parse one already-captured response; this performs no call."""

    request.validate()
    evaluator = _text(
        evaluator_model_identity,
        "evaluator_model_identity",
    )
    generator = _text(
        generator_model_identity,
        "generator_model_identity",
    )
    value = _raw_json(raw_response, "semantic evaluator raw response")
    if not isinstance(value, Mapping):
        raise Phase5SemanticEvaluatorError(
            "semantic evaluator raw response must be an object"
        )
    try:
        validate_english_publication_value(
            value,
            artifact_name="semantic_evaluator_raw_response",
        )
    except PublicationLanguageError as exc:
        raise Phase5SemanticEvaluatorError(
            "semantic evaluator raw response is not English-only"
        ) from exc
    data = dict(value)
    if set(data) != {"schema_version", "verdicts"}:
        raise Phase5SemanticEvaluatorError(
            "semantic evaluator raw response exact keys drifted"
        )
    if data["schema_version"] != SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION:
        raise Phase5SemanticEvaluatorError(
            "semantic evaluator response schema drifted"
        )
    rows = data["verdicts"]
    if not isinstance(rows, list):
        raise Phase5SemanticEvaluatorError(
            "semantic evaluator verdicts must be an array"
        )
    verdicts: list[SemanticAlignmentVerdict] = []
    expected_keys = {
        "criterion_id",
        "verdict",
        "evidence_ids",
        "reason_summary",
        "limitations",
    }
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != expected_keys:
            raise Phase5SemanticEvaluatorError(
                f"semantic evaluator verdict {index} exact keys drifted"
            )
        evidence_ids = row["evidence_ids"]
        if not isinstance(evidence_ids, list):
            raise Phase5SemanticEvaluatorError(
                f"semantic evaluator verdict {index} evidence_ids drifted"
            )
        verdicts.append(
            SemanticAlignmentVerdict(
                criterion_id=_text(
                    row["criterion_id"],
                    f"verdict {index}.criterion_id",
                ),
                verdict=_text(
                    row["verdict"],
                    f"verdict {index}.verdict",
                ),
                evidence_ids=tuple(
                    _text(item, f"verdict {index}.evidence_id")
                    for item in evidence_ids
                ),
                reason_summary=_text(
                    row["reason_summary"],
                    f"verdict {index}.reason_summary",
                ),
                limitations=_text(
                    row["limitations"],
                    f"verdict {index}.limitations",
                ),
            )
        )
    result = SemanticAlignmentResult(
        schema_version=SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
        request_sha256=request.sha256(),
        evaluator_model_identity=evaluator,
        evaluator_prompt_revision=SEMANTIC_EVALUATOR_PROMPT_REVISION,
        raw_response_sha256=sha256(raw_response).hexdigest(),
        raw_response_byte_length=len(raw_response),
        model_generate_calls=1,
        automatic_retry_count=0,
        verdicts=tuple(verdicts),
    )
    result.validate_against(request)
    return result


__all__ = [
    "SEMANTIC_EVALUATOR_EXECUTION_STATUS",
    "SEMANTIC_EVALUATOR_NO_ACTION_SCHEMA_VERSION",
    "SEMANTIC_EVALUATOR_PROMPT_REVISION",
    "SEMANTIC_EVALUATOR_PROMPT_SCHEMA_VERSION",
    "Phase5SemanticEvaluatorError",
    "build_phase5_semantic_evaluator_no_action_bundle",
    "build_phase5_semantic_evaluator_prompt",
    "load_phase5_semantic_alignment_request",
    "parse_phase5_semantic_evaluator_raw_response",
    "validate_phase5_semantic_evaluator_no_action_bundle",
    "validate_phase5_semantic_evidence_payloads",
]
