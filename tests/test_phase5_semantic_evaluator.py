from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import unittest

from req2web_acceptance import (
    SEMANTIC_ALIGNMENT_CONTRACT_REVISION,
    SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION,
    SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
    SEMANTIC_ALIGNMENT_VERDICTS,
    SemanticAlignmentEvidence,
    SemanticAlignmentRequest,
    SemanticAlignmentReviewItem,
)
from req2web_evaluation.phase5_semantic_evaluator import (
    SEMANTIC_EVALUATOR_EXECUTION_STATUS,
    Phase5SemanticEvaluatorError,
    build_phase5_semantic_evaluator_no_action_bundle,
    build_phase5_semantic_evaluator_prompt,
    load_phase5_semantic_alignment_request,
    parse_phase5_semantic_evaluator_raw_response,
    validate_phase5_semantic_evaluator_no_action_bundle,
    validate_phase5_semantic_evidence_payloads,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class Phase5SemanticEvaluatorTest(unittest.TestCase):
    def setUp(self) -> None:
        roles = (
            "acceptance_binding",
            "acceptance_plan",
            "browser_execution_report",
            "browser_screenshot",
            "page_spec",
            "result_package_manifest",
        )
        payloads: dict[str, bytes] = {}
        evidence: list[SemanticAlignmentEvidence] = []
        for index, role in enumerate(roles, start=1):
            evidence_id = f"evidence-{index:02d}-{role}"
            raw = (
                b"synthetic-png-bytes"
                if role == "browser_screenshot"
                else _canonical(
                    {
                        "schema_version": f"synthetic.{role}.v1",
                        "case_id": "synthetic-semantic-case",
                    }
                )
            )
            identity_kind = (
                "raw_bytes"
                if role == "browser_screenshot"
                else "canonical_json"
            )
            payloads[evidence_id] = raw
            evidence.append(
                SemanticAlignmentEvidence(
                    evidence_id=evidence_id,
                    artifact_role=role,
                    identity_kind=identity_kind,
                    sha256="sha256:" + sha256(raw).hexdigest(),
                    byte_length=len(raw),
                    revision=f"synthetic.{role}.v1",
                )
            )
        self.payloads = payloads
        self.request = SemanticAlignmentRequest(
            schema_version=SEMANTIC_ALIGNMENT_REQUEST_SCHEMA_VERSION,
            contract_revision=SEMANTIC_ALIGNMENT_CONTRACT_REVISION,
            case_id="synthetic-semantic-case",
            source_acceptance_plan_sha256="1" * 64,
            source_binding_plan_sha256="2" * 64,
            source_browser_execution_sha256="3" * 64,
            source_page_spec_sha256="4" * 64,
            browser_control_allowed=False,
            model_generate_call_limit=1,
            automatic_retry_limit=0,
            allowed_verdicts=SEMANTIC_ALIGNMENT_VERDICTS,
            response_schema_version=SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
            evidence=tuple(evidence),
            review_items=(
                SemanticAlignmentReviewItem(
                    criterion_id="criterion-001",
                    use_case_id="UC-01",
                    abstract_expected_outcome=(
                        "The user can quickly locate relevant candidates."
                    ),
                    interaction_id="interaction-search",
                    observed_user_feedback=(
                        "Search results updated with filtered items."
                    ),
                    evidence_ids=tuple(sorted(payloads)),
                ),
            ),
        )
        self.request.validate()

    def test_no_action_bundle_binds_frozen_evidence(self) -> None:
        bundle = build_phase5_semantic_evaluator_no_action_bundle(
            request=self.request,
            evidence_payloads=self.payloads,
            generator_model_identity="Qwen/Qwen3.5-9B@synthetic",
        )

        self.assertEqual(
            bundle["status"],
            SEMANTIC_EVALUATOR_EXECUTION_STATUS,
        )
        self.assertFalse(bundle["browser_control_allowed"])
        self.assertFalse(bundle["objective_failure_override_allowed"])
        self.assertEqual(bundle["model_generate_call_limit"], 1)
        self.assertEqual(bundle["automatic_retry_limit"], 0)
        self.assertEqual(
            bundle["evaluator_model_identity"],
            bundle["generator_model_identity"],
        )
        self.assertTrue(bundle["same_model_evaluation_allowed"])
        self.assertFalse(bundle["cross_call_context_reuse_allowed"])
        self.assertIsNone(bundle["time_cap_seconds"])
        self.assertIsNone(bundle["cost_cap_minor_units"])
        self.assertIsNone(bundle["storage_cap_bytes"])
        self.assertFalse(bundle["evaluator_action_eligible"])
        self.assertEqual(bundle["actual_model_generate_calls"], 0)
        self.assertFalse(bundle["model_loaded"])
        self.assertFalse(bundle["semantic_alignment_executed"])
        self.assertEqual(
            validate_phase5_semantic_evaluator_no_action_bundle(
                request=self.request,
                evidence_payloads=self.payloads,
                value=bundle,
            ),
            bundle,
        )

    def test_request_loader_replays_exact_phase4_contract(self) -> None:
        loaded = load_phase5_semantic_alignment_request(
            self.request.canonical_json_bytes()
        )

        self.assertEqual(loaded, self.request)
        self.assertEqual(loaded.sha256(), self.request.sha256())

    def test_request_loader_rejects_mixed_language_evidence(self) -> None:
        value = self.request.to_dict()
        value["review_items"][0]["abstract_expected_outcome"] = (
            "Locate relevant candidates "
            "\u4e2d\u6587"
        )
        with self.assertRaisesRegex(
            Phase5SemanticEvaluatorError,
            "request is not English-only",
        ):
            load_phase5_semantic_alignment_request(_canonical(value))

    def test_evidence_identity_drift_fails_closed(self) -> None:
        payloads = dict(self.payloads)
        first_id = sorted(payloads)[0]
        payloads[first_id] += b"drift"

        with self.assertRaisesRegex(
            Phase5SemanticEvaluatorError,
            "identity drifted",
        ):
            validate_phase5_semantic_evidence_payloads(
                request=self.request,
                evidence_payloads=payloads,
            )

    def test_mixed_language_evidence_fails_after_identity_validation(
        self,
    ) -> None:
        payloads = dict(self.payloads)
        evidence_id = "evidence-01-acceptance_binding"
        raw = _canonical(
            {
                "schema_version": "synthetic.acceptance_binding.v1",
                "case_id": "synthetic-semantic-case",
                "message": "\u4e2d\u6587",
            }
        )
        payloads[evidence_id] = raw
        evidence = tuple(
            replace(
                item,
                sha256="sha256:" + sha256(raw).hexdigest(),
                byte_length=len(raw),
            )
            if item.evidence_id == evidence_id
            else item
            for item in self.request.evidence
        )
        request = replace(self.request, evidence=evidence)
        request.validate()

        with self.assertRaisesRegex(
            Phase5SemanticEvaluatorError,
            "evidence-01-acceptance_binding is not English-only",
        ):
            validate_phase5_semantic_evidence_payloads(
                request=request,
                evidence_payloads=payloads,
            )

    def test_prompt_has_no_browser_control_or_repair_authority(self) -> None:
        prompt = json.loads(
            build_phase5_semantic_evaluator_prompt(self.request)
        )

        self.assertFalse(prompt["browser_control_allowed"])
        self.assertFalse(prompt["automatic_retry_allowed"])
        self.assertFalse(prompt["automatic_repair_allowed"])
        self.assertFalse(prompt["objective_failure_override_allowed"])
        self.assertEqual(
            prompt["request_sha256"],
            self.request.sha256(),
        )
        self.assertNotIn("path", json.dumps(prompt).lower())

    def test_strict_parser_builds_one_call_no_retry_result(self) -> None:
        raw = _canonical(
            {
                "schema_version": (
                    SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION
                ),
                "verdicts": [
                    {
                        "criterion_id": "criterion-001",
                        "verdict": "supported",
                        "evidence_ids": sorted(self.payloads),
                        "reason_summary": (
                            "The observed filtered-results feedback supports "
                            "the bounded discovery outcome."
                        ),
                        "limitations": (
                            "This verdict covers only the frozen interaction "
                            "and screenshot evidence."
                        ),
                    }
                ],
            }
        )

        result = parse_phase5_semantic_evaluator_raw_response(
            request=self.request,
            raw_response=raw,
            evaluator_model_identity="independent-evaluator@synthetic",
            generator_model_identity="Qwen/Qwen3.5-9B@synthetic",
        )

        self.assertEqual(result.model_generate_calls, 1)
        self.assertEqual(result.automatic_retry_count, 0)
        self.assertEqual(result.verdicts[0].verdict, "supported")

    def test_formal_parser_allows_same_generator_and_evaluator(self) -> None:
        raw = _canonical(
            {
                "schema_version": (
                    SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION
                ),
                "verdicts": [
                    {
                        "criterion_id": "criterion-001",
                        "verdict": "unknown",
                        "evidence_ids": sorted(self.payloads),
                        "reason_summary": "The evidence is insufficient.",
                        "limitations": "Human review is required.",
                    }
                ],
            }
        )

        result = parse_phase5_semantic_evaluator_raw_response(
            request=self.request,
            raw_response=raw,
            evaluator_model_identity="same-model",
            generator_model_identity="same-model",
        )
        self.assertEqual(result.evaluator_model_identity, "same-model")
        self.assertEqual(result.verdicts[0].verdict, "unknown")

    def test_formal_parser_rejects_mixed_language_verdict_text(self) -> None:
        raw = _canonical(
            {
                "schema_version": (
                    SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION
                ),
                "verdicts": [
                    {
                        "criterion_id": "criterion-001",
                        "verdict": "unknown",
                        "evidence_ids": sorted(self.payloads),
                        "reason_summary": (
                            "The evidence is insufficient "
                            "\u4e2d\u6587"
                        ),
                        "limitations": "Human review is required.",
                    }
                ],
            }
        )
        with self.assertRaisesRegex(
            Phase5SemanticEvaluatorError,
            "raw response is not English-only",
        ):
            parse_phase5_semantic_evaluator_raw_response(
                request=self.request,
                raw_response=raw,
                evaluator_model_identity="same-model",
                generator_model_identity="same-model",
            )

    def test_parser_requires_every_criterion_exactly_once(self) -> None:
        raw = _canonical(
            {
                "schema_version": (
                    SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION
                ),
                "verdicts": [],
            }
        )

        with self.assertRaisesRegex(ValueError, "exactly once"):
            parse_phase5_semantic_evaluator_raw_response(
                request=self.request,
                raw_response=raw,
                evaluator_model_identity="independent-evaluator@synthetic",
                generator_model_identity="Qwen/Qwen3.5-9B@synthetic",
            )

    def test_parser_preserves_noncanonical_raw_json_as_model_evidence(self) -> None:
        raw = json.dumps(
            {
                "schema_version": SEMANTIC_ALIGNMENT_RESULT_SCHEMA_VERSION,
                "verdicts": [
                    {
                        "criterion_id": "criterion-001",
                        "verdict": "supported",
                        "evidence_ids": sorted(self.payloads),
                        "reason_summary": "Observed behavior matches the goal.",
                        "limitations": "This covers the frozen case only.",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        result = parse_phase5_semantic_evaluator_raw_response(
            request=self.request,
            raw_response=raw,
            evaluator_model_identity="same-model",
            generator_model_identity="same-model",
        )
        self.assertEqual(result.raw_response_byte_length, len(raw))


if __name__ == "__main__":
    unittest.main()
