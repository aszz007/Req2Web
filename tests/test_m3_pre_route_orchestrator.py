from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_evaluation.frozen_g0_reference import freeze_verified_g0_package_reference  # noqa: E402
from req2web_evaluation.model_run_bundle import ModelRunBundleError, ModelRunBundleManifest  # noqa: E402
from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    DeterministicRetrievalEnhancedResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
import req2web_orchestration.pre_route as pre_route_module  # noqa: E402
from req2web_orchestration import (  # noqa: E402
    PreRouteOutcomeError,
    SyntheticMockPreRouteOrchestrator,
    validate_serialized_pre_route_outcome,
)
from req2web_provider import (  # noqa: E402
    CandidateAuditConstructionError,
    MockSmokePageSpecProvider,
    ModelCandidateAuditAdapter,
    ProviderRawResponse,
    ScriptedMockProviderOutcome,
    SyntheticMockProviderRequest,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from test_m3_candidate_audit import (  # noqa: E402
    MOCK_SERIALIZER_CONFIG_BYTES,
    SYNTHETIC_INPUT_BYTES,
    build_context,
    build_receipt,
    candidate_payload,
    edge,
    raw_bytes,
)


TEST_ROOT = ROOT / "outputs" / "_m3_pre_route_orchestrator_tests"
POSITIVE_EDGES = [
    edge("component-search", "requirement", "req-search"),
    edge("component-results", "evidence", "evidence-ui-search"),
    edge("constraint-feedback", "policy", "policy-layout"),
]


def resign_outcome(outcome):
    return replace(
        outcome,
        outcome_id=pre_route_module._PRE_ROUTE_ID_PREFIX
        + pre_route_module._sha256(pre_route_module._canonical_json_bytes(outcome._root())),
    )


def build_g0(work: Path, *, context=None):
    context = context or build_context()
    guidance = RetrievalGuidanceBuilder().build(context)
    baseline = PageSpecBuilder().build(context)
    guided = RetrievalGuidedPageSpecBuilder().build(context, guidance)
    ablations = {
        role: RetrievalGuidedPageSpecBuilder().build(context, guidance, disabled_roles=(role,))
        for role in ROLE_ORDER
    }
    render = DeterministicPageRenderer().render(guided.page_spec, work / "render")
    consistency = MinimalConsistencyChecker().check(guided.page_spec, render)
    influence = RetrievalInfluenceChecker().check(context, guidance, baseline, guided, ablations, render)
    package = DeterministicRetrievalEnhancedResultPackager().package(
        context, guidance, guided, guided.page_spec, render, consistency, influence, work / "package"
    )
    return package, context, guidance, freeze_verified_g0_package_reference(package, context, guidance)


def success_provider(payload: dict[str, object] | None = None) -> MockSmokePageSpecProvider:
    raw = ProviderRawResponse.from_bytes(raw_bytes(payload or candidate_payload(POSITIVE_EDGES)))
    return MockSmokePageSpecProvider(ScriptedMockProviderOutcome.success(raw))


class M3PreRouteOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = TEST_ROOT / f"case-{uuid4().hex}"
        self.work.mkdir(parents=True)
        self.package, self.context, self.guidance, self.reference = build_g0(self.work / "g0")
        self.receipt = build_receipt()
        self.orchestrator = SyntheticMockPreRouteOrchestrator()

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def run_outcome(self, provider: MockSmokePageSpecProvider, *, reference=None, receipt=None, bundle_name: str = "bundle"):
        return self.orchestrator.run(
            frozen_g0_reference=reference or self.reference,
            package=self.package,
            context=self.context,
            guidance=self.guidance,
            visibility_receipt=receipt or self.receipt,
            synthetic_input_bytes=SYNTHETIC_INPUT_BYTES,
            mock_serializer_config_bytes=MOCK_SERIALIZER_CONFIG_BYTES,
            mock_provider=provider,
            bundle_output_directory=self.work / bundle_name,
        )

    def assert_failure(self, outcome, code: str, steps: tuple[str, ...]) -> None:
        self.assertEqual(outcome.disposition, "fail_closed")
        self.assertIsNotNone(outcome.failure)
        self.assertEqual((outcome.failure.code, outcome.failure.stage, outcome.failure.phase), (
            code,
            {
                "g0_reference_validation_failed": "package",
                "semantic_candidate_invalid": "provider_generation",
                "page_spec_assembly_invalid": "page_spec",
                "candidate_audit_invalid": "audit",
                "model_run_bundle_invalid": "package",
                "mock_provider_invocation_invalid": "provider_runtime",
            }.get(code, "provider_runtime"),
            {
                "g0_reference_validation_failed": "g0_reference_validation",
                "semantic_candidate_invalid": "semantic_candidate",
                "page_spec_assembly_invalid": "assembly",
                "candidate_audit_invalid": "candidate_audit",
                "model_run_bundle_invalid": "evaluation_bundle",
                "mock_provider_invocation_invalid": "mock_provider_invocation",
            }.get(code, "transport"),
        ))
        self.assertEqual(outcome.completed_steps, steps)
        outcome.validate()

    def test_error_codes_use_fixed_safe_messages_and_reject_unknown_text(self) -> None:
        known = PreRouteOutcomeError("outcome_identity_invalid")
        self.assertEqual(
            (known.code, known.stage, known.phase, known.message, str(known)),
            (
                "outcome_identity_invalid", "orchestration", "pre_route_outcome",
                "outcome identity is invalid", "outcome identity is invalid",
            ),
        )
        caller_text = "caller-controlled-C:/secret/token-value"
        with self.assertRaises(ValueError) as raised:
            PreRouteOutcomeError(caller_text)
        self.assertEqual(str(raised.exception), "unsupported pre-route outcome error code")
        self.assertNotIn(caller_text, str(raised.exception))

    def test_provider_result_and_record_must_bind_to_current_request(self) -> None:
        other_input = b'{"synthetic":"other-request"}'
        other_config = b'{"mock_serializer":"other-v1"}'
        other_receipt = build_receipt(
            synthetic_input_bytes=other_input,
            mock_serializer_config_bytes=other_config,
        )
        other_request = SyntheticMockProviderRequest.from_mock_bytes(
            other_receipt,
            synthetic_input_bytes=other_input,
            mock_serializer_config_bytes=other_config,
        )
        other_result, other_record = success_provider().invoke(other_request)
        provider = success_provider()
        with (
            patch.object(MockSmokePageSpecProvider, "invoke", return_value=(other_result, other_record)),
            patch("req2web_orchestration.pre_route.parse_provider_raw_response") as parser,
        ):
            outcome = self.run_outcome(provider, bundle_name="wrong-request")
        self.assert_failure(outcome, "mock_provider_invocation_invalid", ("g0_reference_validation",))
        parser.assert_not_called()
        self.assertFalse((self.work / "wrong-request").exists())

    def test_success_preserves_replayable_safe_ordered_identity(self) -> None:
        outcome = self.run_outcome(success_provider())
        self.assertEqual(outcome.disposition, "success")
        self.assertEqual(outcome.completed_steps, (
            "g0_reference_validation", "mock_provider_invocation", "semantic_candidate_parse",
            "canonical_page_spec_assembly", "candidate_audit", "model_run_bundle",
        ))
        self.assertEqual(outcome.provider_invocation_record.outcome, "success")
        payload = outcome.to_dict()
        self.assertEqual(len(payload["frozen_g0_reference_sha256"]), 64)
        with self.assertRaises(PreRouteOutcomeError) as artifact_tamper:
            replace(outcome, candidate_audit_record_id="audit-record-" + "0" * 64).validate()
        self.assertEqual(artifact_tamper.exception.code, "audit_artifact_binding_invalid")
        self.assertEqual(payload["execution_declarations"], {
            "synthetic_mock_only": True, "external_egress_allowed": False, "model_loaded": False,
            "remote_called": False, "provider_kind": "mock_smoke_only",
            "real_d17_path_status": "not_selected_or_executed", "g1_g2_status": "not_assigned",
            "delivery_route": "not_executed", "fallback_materialized": False,
            "repair_status": "not_executed", "formal_quality_status": "not_executed",
        })
        validate_serialized_pre_route_outcome(payload)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(str(self.work), encoded)
        self.assertNotIn(self.context.original_requirement, encoded)
        self.assertNotIn(SYNTHETIC_INPUT_BYTES.decode("utf-8"), encoded)
        self.assertNotIn(MOCK_SERIALIZER_CONFIG_BYTES.decode("utf-8"), encoded)
        self.assertNotIn("Product search and checkout", encoded)

    def test_invalid_g0_prevents_provider_call(self) -> None:
        invalid_reference = replace(self.reference, page_id="wrong-page")
        provider = success_provider()
        with patch.object(MockSmokePageSpecProvider, "invoke", side_effect=AssertionError("must not invoke")) as invoked:
            outcome = self.run_outcome(provider, reference=invalid_reference)
        self.assertEqual(invoked.call_count, 0)
        self.assert_failure(outcome, "g0_reference_validation_failed", ())
        self.assertIsNone(outcome.frozen_g0_reference)
        self.assertFalse((self.work / "bundle").exists())

    def test_each_runtime_failure_is_fail_closed_after_mock_invocation(self) -> None:
        expected_retryable = {"timeout": True, "cancelled": False, "resource_exhausted": True, "service_unavailable": True}
        for code, retryable in expected_retryable.items():
            with self.subTest(code=code):
                provider = MockSmokePageSpecProvider(ScriptedMockProviderOutcome.provider_runtime_error(code))
                outcome = self.run_outcome(provider, bundle_name=f"runtime-{code}")
                self.assert_failure(outcome, code, ("g0_reference_validation", "mock_provider_invocation"))
                self.assertEqual(outcome.failure.retryable, retryable)
                self.assertIsNotNone(outcome.frozen_g0_reference)
                self.assertIsNotNone(outcome.provider_invocation_record)
                self.assertFalse((self.work / f"runtime-{code}").exists())
    def test_parse_assembly_audit_and_bundle_failures_do_not_route_or_materialize(self) -> None:
        parse_outcome = self.run_outcome(
            MockSmokePageSpecProvider(ScriptedMockProviderOutcome.success(ProviderRawResponse.from_bytes(b"{}"))),
            bundle_name="parse",
        )
        self.assert_failure(parse_outcome, "semantic_candidate_invalid", (
            "g0_reference_validation", "mock_provider_invocation"
        ))

        assembly_payload = candidate_payload(POSITIVE_EDGES)
        assembly_payload["constraints"][0]["description"] = self.context.constraints[0]
        assembly_outcome = self.run_outcome(success_provider(assembly_payload), bundle_name="assembly")
        self.assert_failure(assembly_outcome, "page_spec_assembly_invalid", (
            "g0_reference_validation", "mock_provider_invocation", "semantic_candidate_parse"
        ))

        with patch(
            "req2web_orchestration.pre_route.ModelCandidateAuditAdapter.build",
            side_effect=CandidateAuditConstructionError("fixture", "ignored unsafe caller text"),
        ):
            audit_outcome = self.run_outcome(success_provider(), bundle_name="audit")
        self.assert_failure(audit_outcome, "candidate_audit_invalid", (
            "g0_reference_validation", "mock_provider_invocation", "semantic_candidate_parse",
            "canonical_page_spec_assembly",
        ))

        with patch(
            "req2web_orchestration.pre_route.ModelRunBundleWriter.write",
            side_effect=ModelRunBundleError("bundle_directory_invalid"),
        ):
            bundle_outcome = self.run_outcome(success_provider(), bundle_name="bundle-failure")
        self.assert_failure(bundle_outcome, "model_run_bundle_invalid", (
            "g0_reference_validation", "mock_provider_invocation", "semantic_candidate_parse",
            "canonical_page_spec_assembly", "candidate_audit",
        ))
        for outcome in (parse_outcome, assembly_outcome, audit_outcome, bundle_outcome):
            payload = outcome.to_dict()
            self.assertEqual(payload["execution_declarations"]["delivery_route"], "not_executed")
            self.assertFalse(payload["execution_declarations"]["fallback_materialized"])
            self.assertEqual(payload["execution_declarations"]["repair_status"], "not_executed")
            self.assertNotIn("ignored unsafe caller text", json.dumps(payload, sort_keys=True))

    def test_cross_case_and_serialized_tampering_fail_closed_validation(self) -> None:
        outcome = self.run_outcome(success_provider())
        other_context_seed = replace(build_context(), requirement_summary="Independent synthetic task.")
        other_package, other_context, other_guidance, other_reference = build_g0(
            self.work / "other-g0", context=other_context_seed
        )
        self.assertNotEqual(self.reference.reference_id, other_reference.reference_id)
        resigned_cross_case = resign_outcome(replace(outcome, frozen_g0_reference=other_reference))
        with self.assertRaises(PreRouteOutcomeError) as raised:
            resigned_cross_case.validate()
        self.assertEqual(raised.exception.code, "assembled_page_reference_binding_invalid")
        cross_case = self.orchestrator.run(
            frozen_g0_reference=self.reference,
            package=other_package,
            context=other_context,
            guidance=other_guidance,
            visibility_receipt=self.receipt,
            synthetic_input_bytes=SYNTHETIC_INPUT_BYTES,
            mock_serializer_config_bytes=MOCK_SERIALIZER_CONFIG_BYTES,
            mock_provider=success_provider(),
            bundle_output_directory=self.work / "cross-case",
        )
        self.assert_failure(cross_case, "g0_reference_validation_failed", ())
        payload = outcome.to_dict()
        serialized_cross_case = deepcopy(payload)
        serialized_cross_case["frozen_g0_reference"] = other_reference.to_dict()
        serialized_cross_case["frozen_g0_reference_sha256"] = pre_route_module._reference_sha256(other_reference)
        serialized_root = {key: value for key, value in serialized_cross_case.items() if key != "outcome_id"}
        serialized_cross_case["outcome_id"] = (
            pre_route_module._PRE_ROUTE_ID_PREFIX
            + pre_route_module._sha256(pre_route_module._canonical_json_bytes(serialized_root))
        )
        with self.assertRaises(PreRouteOutcomeError) as serialized_case:
            validate_serialized_pre_route_outcome(serialized_cross_case)
        self.assertEqual(serialized_case.exception.code, "assembled_page_reference_binding_invalid")
        tampered = deepcopy(payload)
        tampered["artifacts"]["model_run_bundle_id"] = "synthetic-mock-bundle-" + "0" * 64
        with self.assertRaises(PreRouteOutcomeError) as serialized:
            validate_serialized_pre_route_outcome(tampered)
        self.assertEqual(serialized.exception.code, "outcome_identity_invalid")

    def test_audit_and_bundle_roots_must_bind_to_current_provider_record(self) -> None:
        outcome = self.run_outcome(success_provider())
        other_input = b'{"synthetic":"alternate-audit"}'
        other_config = b'{"mock_serializer":"alternate-v1"}'
        other_receipt = build_receipt(
            synthetic_input_bytes=other_input,
            mock_serializer_config_bytes=other_config,
        )
        other_audit = ModelCandidateAuditAdapter().build(
            outcome.assembled_page_spec,
            other_receipt,
            synthetic_input_bytes=other_input,
            mock_serializer_config_bytes=other_config,
        )
        audit_mismatch = resign_outcome(replace(
            outcome,
            candidate_audit_record=other_audit,
            candidate_audit_record_id=other_audit.record_id,
            candidate_audit_record_sha256=pre_route_module._sha256(other_audit.canonical_json_bytes()),
        ))
        with self.assertRaises(PreRouteOutcomeError) as audit_error:
            audit_mismatch.validate()
        self.assertEqual(audit_error.exception.code, "audit_provider_binding_invalid")

        for root_kind in (
            "assembled_page_spec", "synthetic_mock_visibility_receipt",
            "synthetic_input", "mock_serializer_config",
        ):
            with self.subTest(root_kind=root_kind):
                roots = {
                    key: dict(value)
                    for key, value in outcome.model_run_bundle_manifest.artifact_roots.items()
                }
                roots[root_kind]["sha256"] = "0" * 64
                mismatched_manifest = ModelRunBundleManifest.create(
                    artifact_inventory=outcome.model_run_bundle_manifest.artifact_inventory,
                    artifact_roots=roots,
                    assembly=outcome.model_run_bundle_manifest.assembly,
                    candidate_audit=outcome.model_run_bundle_manifest.candidate_audit,
                    downstream_status=outcome.model_run_bundle_manifest.downstream_status,
                )
                bundle_mismatch = resign_outcome(replace(
                    outcome,
                    model_run_bundle_manifest=mismatched_manifest,
                    model_run_bundle_id=mismatched_manifest.bundle_id,
                    model_run_bundle_manifest_sha256=pre_route_module._sha256(
                        mismatched_manifest.canonical_json_bytes()
                    ),
                ))
                with self.assertRaises(PreRouteOutcomeError) as bundle_error:
                    bundle_mismatch.validate()
                self.assertEqual(bundle_error.exception.code, "bundle_artifact_binding_invalid")

    def test_no_reverse_provider_evaluation_import_or_provider_call_before_g0_gate(self) -> None:
        provider_source = (ROOT / "src" / "req2web_provider" / "mock_provider.py").read_text(encoding="utf-8")
        self.assertNotIn("req2web_orchestration", provider_source)
        pre_route_source = (ROOT / "src" / "req2web_orchestration" / "pre_route.py").read_text(encoding="utf-8")
        self.assertIn("validate_against(package, context, guidance)", pre_route_source)
        self.assertLess(
            pre_route_source.index("validate_against(package, context, guidance)"),
            pre_route_source.index("mock_provider.invoke(request)"),
        )


if __name__ == "__main__":
    unittest.main()