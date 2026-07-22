from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import AgentContextBundle, UseCase  # noqa: E402
from req2web_generation import RetrievalGuidanceBuilder  # noqa: E402
from req2web_provider import (  # noqa: E402
    CandidateAuditConstructionError,
    CandidateAuditDecision,
    CanonicalPageSpecAssembler,
    MockEvidenceVisibilitySource,
    MockPolicyVisibilitySource,
    MockRequirementVisibilitySource,
    ModelCandidateAuditAdapter,
    ProviderRawResponse,
    ResolvedVisibilitySource,
    SyntheticMockVisibilityReceipt,
    SyntheticMockVisibilitySourceRegistry,
    expected_audit_decision_from_resolution,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402

SYNTHETIC_INPUT_BYTES = b'{"synthetic":"candidate-audit-fixture"}'
MOCK_SERIALIZER_CONFIG_BYTES = b'{"mock_serializer":"v1"}'


def _result(role: str, doc_id: str, title: str) -> dict[str, object]:
    return {
        "score": 1.0,
        "doc_id": doc_id,
        "role": role,
        "dataset": "synthetic_fixture_dataset",
        "subset": "synthetic_fixture_subset",
        "sample_id": doc_id.rsplit(":", 1)[-1],
        "title": title,
        "summary": f"synthetic {role} source",
        "references": [{"kind": "fixture", "uri": f"fixtures/{role}/{doc_id.rsplit(':', 1)[-1]}.json"}],
    }


def build_context() -> AgentContextBundle:
    retrieval_results = {
        "requirement": [_result("requirement", "requirement:fixture:search", "Search requirement")],
        "ui_reference": [_result("ui_reference", "ui_reference:fixture:search", "Search layout")],
        "interaction_flow": [_result("interaction_flow", "interaction_flow:fixture:search", "Search interaction")],
        "implementation": [_result("implementation", "implementation:fixture:search", "Search implementation")],
        "validation": [_result("validation", "validation:fixture:search", "Search validation")],
    }
    return AgentContextBundle(
        original_requirement="Build a mobile search and checkout page.",
        requirement_summary="A mobile page supports search and checkout.",
        target_device="mobile",
        task_type="ecommerce",
        constraints=["Display a clear user-facing result after each action."],
        use_cases=[
            UseCase("UC-01", "Search products", "user", "search products", "view matching results"),
            UseCase("UC-02", "Checkout products", "user", "complete checkout", "receive confirmation"),
        ],
        retrieval_queries={role: f"synthetic {role}" for role in ROLE_ORDER},
        retrieval_results=retrieval_results,
    )


def candidate_payload(edges: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "schema_version": "req2web.provider.semantic_candidate.v1",
        "title": "Product search and checkout",
        "layout": {"pattern": "two_task_sections", "section_stable_ids": ["section-search", "section-checkout"]},
        "sections": [
            {"stable_id": "section-search", "title": "Search products", "purpose": "Find products and show matching results.", "component_stable_ids": ["component-search", "component-results"], "use_case_ids": ["UC-01"]},
            {"stable_id": "section-checkout", "title": "Checkout products", "purpose": "Complete a selected-product checkout.", "component_stable_ids": ["component-checkout", "component-confirmation"], "use_case_ids": ["UC-02"]},
        ],
        "components": [
            {"stable_id": "component-search", "section_stable_id": "section-search", "component_type": "search_input", "label": "Search", "purpose": "Enter a product query."},
            {"stable_id": "component-results", "section_stable_id": "section-search", "component_type": "status_panel", "label": "Results", "purpose": "Show matching products."},
            {"stable_id": "component-checkout", "section_stable_id": "section-checkout", "component_type": "primary_action", "label": "Checkout", "purpose": "Submit checkout."},
            {"stable_id": "component-confirmation", "section_stable_id": "section-checkout", "component_type": "status_panel", "label": "Confirmation", "purpose": "Show checkout confirmation."},
        ],
        "states": [
            {"stable_id": "state-ready", "name": "ready", "description": "The user can start either task.", "visible_component_stable_ids": ["component-search", "component-checkout"]},
            {"stable_id": "state-success", "name": "success", "description": "The page shows clear outcome feedback.", "visible_component_stable_ids": ["component-results", "component-confirmation"]},
        ],
        "interactions": [
            {"stable_id": "interaction-search", "trigger_component_stable_id": "component-search", "source_state_stable_id": "state-ready", "action": "run product search", "target_state_stable_id": "state-success", "user_feedback": "Matching results are displayed.", "use_case_ids": ["UC-01"]},
            {"stable_id": "interaction-checkout", "trigger_component_stable_id": "component-checkout", "source_state_stable_id": "state-ready", "action": "submit checkout", "target_state_stable_id": "state-success", "user_feedback": "A checkout confirmation is displayed.", "use_case_ids": ["UC-02"]},
        ],
        "constraints": [{"stable_id": "constraint-feedback", "description": "Each user action produces an explicit visible outcome."}],
        "acceptance_checks": [
            {"stable_id": "check-search", "description": "A search action displays a visible matching-results outcome.", "use_case_ids": ["UC-01"], "state_stable_id": "state-success"},
            {"stable_id": "check-checkout", "description": "A checkout action displays a visible confirmation outcome.", "use_case_ids": ["UC-02"], "state_stable_id": "state-success"},
        ],
        "use_case_mappings": [
            {"use_case_id": "UC-01", "section_stable_ids": ["section-search"], "component_stable_ids": ["component-search", "component-results"], "interaction_stable_ids": ["interaction-search"]},
            {"use_case_id": "UC-02", "section_stable_ids": ["section-checkout"], "component_stable_ids": ["component-checkout", "component-confirmation"], "interaction_stable_ids": ["interaction-checkout"]},
        ],
        "claimed_attribution_edges": list(edges or []),
    }


def raw_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def build_assembled(edges: list[dict[str, str]] | None = None):
    context = build_context()
    return CanonicalPageSpecAssembler().assemble(
        ProviderRawResponse.from_bytes(raw_bytes(candidate_payload(edges))),
        context,
        RetrievalGuidanceBuilder().build(context),
    )


def policy_hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def build_receipt(
    registry: SyntheticMockVisibilitySourceRegistry | None = None,
    *,
    synthetic_input_bytes: bytes = SYNTHETIC_INPUT_BYTES,
    mock_serializer_config_bytes: bytes = MOCK_SERIALIZER_CONFIG_BYTES,
) -> SyntheticMockVisibilityReceipt:
    default_hash = policy_hash("fixture-policy-v1")
    registry = registry or SyntheticMockVisibilitySourceRegistry(
        requirements=(MockRequirementVisibilitySource("req-search", "REQ-SEARCH", True, True),),
        evidences=(MockEvidenceVisibilitySource("evidence-ui-search", "ui_reference", "ui_reference:fixture:search", True, True, True),),
        policies=(MockPolicyVisibilitySource("policy-layout", "v1", "v1", default_hash, default_hash, True, True),),
    )
    return SyntheticMockVisibilityReceipt.from_mock_bytes(
        synthetic_input_bytes,
        mock_serializer_config_bytes,
        registry,
    )


def edge(entity_id: str, source_kind: str, source_id: str) -> dict[str, str]:
    return {
        "candidate_entity_stable_id": entity_id,
        "source_kind": source_kind,
        "source_id": source_id,
    }


class CandidateAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = ModelCandidateAuditAdapter()
        self.positive_edges = [
            edge("component-search", "requirement", "req-search"),
            edge("component-results", "evidence", "evidence-ui-search"),
            edge("constraint-feedback", "policy", "policy-layout"),
        ]

    def build_record(self, assembled, receipt):
        return self.adapter.build(
            assembled,
            receipt,
            synthetic_input_bytes=SYNTHETIC_INPUT_BYTES,
            mock_serializer_config_bytes=MOCK_SERIALIZER_CONFIG_BYTES,
        )

    def assert_construction_error(self, callback, code: str) -> None:
        with self.assertRaises(CandidateAuditConstructionError) as raised:
            callback()
        self.assertEqual((raised.exception.code, raised.exception.stage, raised.exception.phase), (code, "guidance/adoption", "candidate_audit"))

    def test_external_bytes_rebinding_is_mandatory_and_d03_aligned(self) -> None:
        assembled = build_assembled(self.positive_edges)
        receipt = build_receipt()
        record = self.build_record(assembled, receipt)
        self.assertEqual((record.status, record.accepted_count, record.rejected_count), ("clean", 3, 0))

        self.assert_construction_error(
            lambda: self.adapter.build(assembled, receipt, synthetic_input_bytes=b'{"synthetic":"different"}', mock_serializer_config_bytes=MOCK_SERIALIZER_CONFIG_BYTES),
            "synthetic_input_receipt_binding_mismatch",
        )
        self.assert_construction_error(
            lambda: self.adapter.build(assembled, receipt, synthetic_input_bytes=SYNTHETIC_INPUT_BYTES, mock_serializer_config_bytes=b'{"mock_serializer":"different"}'),
            "mock_serializer_config_receipt_binding_mismatch",
        )
        for input_bytes, config_bytes, code in (
            ("not-bytes", MOCK_SERIALIZER_CONFIG_BYTES, "synthetic_input_bytes_invalid"),
            (b"", MOCK_SERIALIZER_CONFIG_BYTES, "synthetic_input_bytes_invalid"),
            (SYNTHETIC_INPUT_BYTES, "not-bytes", "mock_serializer_config_bytes_invalid"),
            (SYNTHETIC_INPUT_BYTES, b"", "mock_serializer_config_bytes_invalid"),
        ):
            with self.subTest(code=code):
                self.assert_construction_error(
                    lambda input_bytes=input_bytes, config_bytes=config_bytes: self.adapter.build(
                        assembled,
                        receipt,
                        synthetic_input_bytes=input_bytes,
                        mock_serializer_config_bytes=config_bytes,
                    ),
                    code,
                )

    def test_table_driven_fixed_reason_branches_preserve_every_edge(self) -> None:
        fixture_hash = policy_hash("fixture-policy-v1")
        other_hash = policy_hash("different-policy")
        table = [
            ("component-search", "requirement", "req-accepted", "identity_visibility_license_shape_valid"),
            ("component-results", "requirement", "req-hidden", "requirement_not_visible"),
            ("component-checkout", "requirement", "req-ineligible", "requirement_not_eligible"),
            ("component-confirmation", "requirement", "unseen source id", "unseen_source"),
            ("section-search", "evidence", "evidence-accepted", "identity_visibility_license_shape_valid"),
            ("section-checkout", "evidence", "evidence-invalid-role", "evidence_role_invalid"),
            ("state-ready", "evidence", "evidence-doc-unbound", "evidence_doc_not_bound_to_assembled_page_spec"),
            ("state-success", "evidence", "evidence-role-mismatch", "evidence_role_trace_mismatch"),
            ("interaction-search", "evidence", "evidence-hidden", "evidence_not_visible"),
            ("interaction-checkout", "evidence", "evidence-permission", "evidence_fixture_permission_denied"),
            ("constraint-feedback", "evidence", "evidence-ineligible", "evidence_not_eligible"),
            ("check-search", "policy", "policy-accepted", "identity_visibility_license_shape_valid"),
            ("check-checkout", "policy", "policy-version-mismatch", "policy_version_mismatch"),
            ("component-search", "policy", "policy-hash-mismatch", "policy_sha256_mismatch"),
            ("component-results", "policy", "policy-hidden", "policy_not_visible"),
            ("component-checkout", "policy", "policy-ineligible", "policy_not_eligible"),
        ]
        registry = SyntheticMockVisibilitySourceRegistry(
            requirements=(
                MockRequirementVisibilitySource("req-accepted", "REQ-ACCEPTED", True, True),
                MockRequirementVisibilitySource("req-hidden", "REQ-HIDDEN", False, True),
                MockRequirementVisibilitySource("req-ineligible", "REQ-INELIGIBLE", True, False),
            ),
            evidences=(
                MockEvidenceVisibilitySource("evidence-accepted", "ui_reference", "ui_reference:fixture:search", True, True, True),
                MockEvidenceVisibilitySource("evidence-invalid-role", "unrecognized_role", "ui_reference:fixture:search", True, True, True),
                MockEvidenceVisibilitySource("evidence-doc-unbound", "ui_reference", "ui_reference:fixture:missing", True, True, True),
                MockEvidenceVisibilitySource("evidence-role-mismatch", "validation", "ui_reference:fixture:search", True, True, True),
                MockEvidenceVisibilitySource("evidence-hidden", "ui_reference", "ui_reference:fixture:search", False, True, True),
                MockEvidenceVisibilitySource("evidence-permission", "ui_reference", "ui_reference:fixture:search", True, False, True),
                MockEvidenceVisibilitySource("evidence-ineligible", "ui_reference", "ui_reference:fixture:search", True, True, False),
            ),
            policies=(
                MockPolicyVisibilitySource("policy-accepted", "v1", "v1", fixture_hash, fixture_hash, True, True),
                MockPolicyVisibilitySource("policy-version-mismatch", "v2", "v1", fixture_hash, fixture_hash, True, True),
                MockPolicyVisibilitySource("policy-hash-mismatch", "v1", "v1", other_hash, fixture_hash, True, True),
                MockPolicyVisibilitySource("policy-hidden", "v1", "v1", fixture_hash, fixture_hash, False, True),
                MockPolicyVisibilitySource("policy-ineligible", "v1", "v1", fixture_hash, fixture_hash, True, False),
            ),
        )
        record = self.build_record(
            build_assembled([edge(entity_id, kind, source_id) for entity_id, kind, source_id, _ in table]),
            build_receipt(registry),
        )
        self.assertEqual((record.status, record.accepted_count, record.rejected_count), ("with_rejections", 3, 13))
        self.assertEqual(
            [decision.reason_code for decision in record.edge_decisions],
            [reason for _, _, _, reason in table],
        )
        self.assertEqual(len(record.edge_decisions), len(table))
        self.assertEqual(len(record.verified_edge_projection()), 3)
        for decision in record.edge_decisions:
            with self.subTest(source_id=decision.source_id):
                self.assertEqual(
                    (decision.status, decision.reason_code),
                    expected_audit_decision_from_resolution(decision.resolved_source),
                )

    def test_standalone_decision_validation_replays_resolution_facts(self) -> None:
        record = self.build_record(build_assembled(self.positive_edges), build_receipt())
        requirement_decision, _, policy_decision = record.edge_decisions

        invisible_requirement = replace(
            requirement_decision,
            resolved_source=replace(requirement_decision.resolved_source, requirement_visible=False),
        )
        with self.assertRaisesRegex(ValueError, "does not match resolved source facts"):
            invisible_requirement.validate()
        with self.assertRaisesRegex(ValueError, "does not match resolved source facts"):
            replace(record, edge_decisions=(invisible_requirement, *record.edge_decisions[1:])).validate()

        wrong_reason = replace(policy_decision, status="rejected", reason_code="policy_sha256_mismatch")
        with self.assertRaisesRegex(ValueError, "does not match resolved source facts"):
            wrong_reason.validate()

        fixture_mismatch = replace(
            policy_decision,
            resolved_source=replace(policy_decision.resolved_source, fixture_policy_sha256=policy_hash("other-fixture")),
        )
        with self.assertRaisesRegex(ValueError, "does not match resolved source facts"):
            fixture_mismatch.validate()

        unseen_resolution = ResolvedVisibilitySource(
            source_kind="requirement",
            source_id="unseen source id",
            registry_found=False,
        )
        forged_unseen_accept = CandidateAuditDecision(
            candidate_entity_stable_id="component-search",
            source_kind="requirement",
            source_id="unseen source id",
            status="accepted",
            reason_code="identity_visibility_license_shape_valid",
            resolved_source=unseen_resolution,
        )
        with self.assertRaisesRegex(ValueError, "does not match resolved source facts"):
            forged_unseen_accept.validate()

        verified = record.verified_edge_projection()[0]
        forged_projection = replace(
            verified,
            resolved_source=replace(verified.resolved_source, requirement_eligible=False),
        )
        with self.assertRaisesRegex(ValueError, "does not match resolved source facts"):
            forged_projection.validate()

    def test_no_claims_receipt_and_record_ids_are_deterministic(self) -> None:
        receipt_a = build_receipt()
        receipt_b = build_receipt()
        self.assertEqual(receipt_a.receipt_id, receipt_b.receipt_id)
        self.assertEqual(receipt_a.canonical_json_bytes(), receipt_b.canonical_json_bytes())
        record_a = self.build_record(build_assembled([]), receipt_a)
        record_b = self.build_record(build_assembled([]), receipt_b)
        self.assertEqual((record_a.status, record_a.accepted_count, record_a.rejected_count), ("no_claims", 0, 0))
        self.assertEqual(record_a.record_id, record_b.record_id)
        record_a.validate()
        with self.assertRaisesRegex(ValueError, "record ID"):
            replace(record_a, record_id="candidate-audit-" + "0" * 64).validate()
        with self.assertRaisesRegex(ValueError, "counts"):
            replace(record_a, accepted_count=1).validate()

    def test_forged_receipt_and_artifact_binding_fail_closed_with_d03_fields(self) -> None:
        assembled = build_assembled(self.positive_edges)
        receipt = build_receipt()
        self.assert_construction_error(
            lambda: self.build_record(assembled, replace(receipt, external_egress_allowed=True)),
            "visibility_receipt_invalid",
        )
        self.assert_construction_error(
            lambda: self.build_record(assembled, replace(receipt, receipt_id="mock-receipt-" + "0" * 64)),
            "visibility_receipt_invalid",
        )
        forged_assembled = replace(assembled, report=replace(assembled.report, raw_response_sha256="0" * 64))
        self.assert_construction_error(
            lambda: self.build_record(forged_assembled, receipt),
            "assembled_artifact_invalid",
        )
        duplicate_registry = SyntheticMockVisibilitySourceRegistry(
            requirements=(
                MockRequirementVisibilitySource("req-search", "REQ-SEARCH", True, True),
                MockRequirementVisibilitySource("req-search", "REQ-OTHER", True, True),
            ),
            evidences=(),
            policies=(),
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_receipt(duplicate_registry)

    def test_source_has_no_evaluator_or_gold_reverse_import_and_uses_utf8_without_bom(self) -> None:
        source_path = ROOT / "src" / "req2web_provider" / "candidate_audit.py"
        source = source_path.read_text(encoding="utf-8")
        self.assertFalse(source_path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertNotIn("req2web_evaluation", source)
        self.assertNotIn("gold_obligation", source)
        self.assertNotIn("InspectorFactSet", source)


if __name__ == "__main__":
    unittest.main()
