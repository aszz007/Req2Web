from __future__ import annotations

from copy import deepcopy
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

import req2web_orchestration.model_route as model_route_module  # noqa: E402
import req2web_runtime.qwen_profile as qwen_profile_module  # noqa: E402
from req2web_faults.fallback_delivery import freeze_g0_fallback_package  # noqa: E402
from req2web_orchestration.model_route import (  # noqa: E402
    SCRIPTED_ACCEPTANCE_PASS_KEY,
    SCRIPTED_FIXTURE_GATE_DELIVERY_VALID_KEY,
    ScriptedAcceptanceFixture,
    ScriptedLocalFixture,
    TierA07bOneRepairOrchestrator,
    TierA07bRepairPatch,
    TierA08RealRunBundleError,
    TierA08RealRunBundlePlaceholder,
    TierAModelRouteOrchestrator,
    create_tier_a_08_real_run_bundle,
    validate_serialized_tier_a_08_real_run_bundle,
)
from req2web_provider.semantic_candidate import ProviderRawResponse  # noqa: E402
from req2web_runtime.qwen_profile import (  # noqa: E402
    QwenProfilePlaceholder,
    QwenProfilePlaceholderError,
    create_qwen_profile_placeholder,
)
from test_m3_gate_delivery import gate_delivery_candidate_bytes  # noqa: E402
from test_m3_model_route import build_chain, build_g0  # noqa: E402
from test_m3_semantic_candidate_assembly import build_context  # noqa: E402


TEST_ROOT = ROOT / "outputs" / "_m3_real_run_bundle_tests"


class TierA08RealRunBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = TEST_ROOT / uuid4().hex
        self.work.mkdir(parents=True)
        self.package, self.context, self.guidance, self.reference = build_g0(self.work / "g0")
        self.manifest, self.selected, self.request, self.audit, self.preparation = build_chain(self.context)
        self.profile = create_qwen_profile_placeholder()
        self.fixture = ScriptedLocalFixture.create(
            SCRIPTED_FIXTURE_GATE_DELIVERY_VALID_KEY,
            ProviderRawResponse.from_bytes(gate_delivery_candidate_bytes(self.context)),
        )
        self.model_outcome = TierAModelRouteOrchestrator().run(
            frozen_g0_reference=self.reference, package=self.package, context=self.context,
            guidance=self.guidance, manifest=self.manifest, selected=self.selected,
            local_request=self.request, pre_invocation_audit=self.audit,
            local_qwen_preparation=self.preparation, execution_branch="scripted_local_fixture",
            scripted_local_fixture=self.fixture,
        )
        self.case_id = "case-tier-a-08"
        self.snapshot = self.work / "snapshot"
        self.fallback_record = freeze_g0_fallback_package(self.case_id, self.package, self.snapshot)
        first_page = model_route_module._07b_live_first_page_spec(
            self.model_outcome, self.fixture, self.context, self.guidance
        )
        self.receipt = model_route_module.create_tier_a_07b_field_gate_report(
            case_id=self.case_id, page_spec=first_page, local_request=self.request
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)
        if TEST_ROOT.exists() and not any(TEST_ROOT.iterdir()):
            TEST_ROOT.rmdir()

    def route_args(self, suffix: str, *, repair_value=None):
        patch = TierA07bRepairPatch.create(
            report_id=self.receipt.report_id, report_sha256=self.receipt.sha256(),
            first_page_id=model_route_module._07b_live_first_page_spec(
                self.model_outcome, self.fixture, self.context, self.guidance
            ).page_id,
            first_page_spec_sha256=model_route_module._07b_page_binding(
                model_route_module._07b_live_first_page_spec(
                    self.model_outcome, self.fixture, self.context, self.guidance
                )
            )["page_spec_sha256"],
            attempt_index=1,
            operations=((self.receipt.reported_field,
                         self.receipt.expected if repair_value is None else repair_value),),
        )
        return {
            "model_route_outcome": self.model_outcome, "frozen_g0_reference": self.reference,
            "package": self.package, "context": self.context, "guidance": self.guidance,
            "manifest": self.manifest, "selected": self.selected, "local_request": self.request,
            "pre_invocation_audit": self.audit, "local_qwen_preparation": self.preparation,
            "execution_branch": "scripted_local_fixture", "scripted_local_fixture": self.fixture,
            "case_id": self.case_id, "fallback_record": self.fallback_record,
            "fallback_snapshot_dir": self.snapshot, "render_output_dir": self.work / f"render-{suffix}",
            "model_package_output_dir": self.work / f"model-package-{suffix}",
            "fallback_output_dir": self.work / f"fallback-{suffix}",
            "scripted_acceptance_fixture": ScriptedAcceptanceFixture.create(SCRIPTED_ACCEPTANCE_PASS_KEY),
            "field_gate_report": self.receipt.canonical_bytes(), "repair_patch": patch.canonical_bytes(),
        }

    def bundle(self, outcome):
        return create_tier_a_08_real_run_bundle(
            self.context, self.manifest, self.selected, self.request, self.audit,
            self.preparation, self.profile, self.model_outcome, outcome,
        )

    def assert_rejected(self, payload):
        root = {key: value for key, value in payload.items() if key != "bundle_id"}
        payload["bundle_id"] = model_route_module._08_ID_PREFIX + model_route_module._sha256(
            model_route_module._canonical_json_bytes(root)
        )[:20]
        with self.assertRaises(TierA08RealRunBundleError):
            TierA08RealRunBundlePlaceholder.from_dict(payload)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self.assertRaises(TierA08RealRunBundleError):
            TierA08RealRunBundlePlaceholder.from_bytes(raw)

    def test_profile_is_exact_canonical_and_unselected(self):
        profile = self.profile
        self.assertEqual(profile.candidate_model, "Qwen/Qwen3.5-9B")
        self.assertEqual(profile.allowed_profile_names, (
            "quality_experiment", "local_smoke", "deterministic_fallback"
        ))
        self.assertFalse(profile.external_egress_allowed)
        self.assertFalse(profile.model_loaded)
        self.assertFalse(profile.run_occurred)
        self.assertEqual(QwenProfilePlaceholder.from_bytes(profile.canonical_bytes()), profile)
        self.assertEqual(profile.canonical_bytes(), create_qwen_profile_placeholder().canonical_bytes())
        payload = profile.to_dict()
        payload["budget"] = 1
        with self.assertRaises(QwenProfilePlaceholderError):
            QwenProfilePlaceholder.from_dict(payload)
        payload = profile.to_dict()
        payload["run_occurred"] = 0
        with self.assertRaises(QwenProfilePlaceholderError):
            QwenProfilePlaceholder.from_dict(payload)
        with self.assertRaises(QwenProfilePlaceholderError):
            QwenProfilePlaceholder.from_bytes(profile.canonical_bytes() + b"\n")

    def test_happy_path_round_trip_and_live_replay(self):
        outcome = TierA07bOneRepairOrchestrator().run(**self.route_args("success"))
        self.assertEqual(outcome.status, "recovered_success")
        bundle = self.bundle(outcome)
        self.assertFalse(bundle.run_occurred)
        self.assertEqual(
            bundle.gate_delivery_binding["a07b_outcome"]["final_package_schema_version"],
            "req2web.result.package.v1",
        )
        self.assertEqual(bundle.gate_delivery_binding["route_evidence_kind"], "tier_a_07b_local_synthetic_fixture")
        self.assertFalse(bundle.gate_delivery_binding["real_run_route_occurred"])
        self.assertEqual(bundle.run_facts["provider_invocation_state"], "not_run")
        self.assertEqual(
            TierA08RealRunBundlePlaceholder.from_bytes(bundle.canonical_bytes()).to_dict(), bundle.to_dict()
        )
        bundle.validate_against(
            self.context, self.manifest, self.selected, self.request, self.audit,
            self.preparation, self.profile, self.model_outcome, outcome,
        )
        self.assertEqual(validate_serialized_tier_a_08_real_run_bundle(bundle.to_dict()), bundle)

    def test_repeated_construction_is_byte_identical_and_does_not_write(self):
        outcome = TierA07bOneRepairOrchestrator().run(**self.route_args("repeat"))
        before = tuple(sorted(item.relative_to(self.work).as_posix() for item in self.work.rglob("*")))
        first = self.bundle(outcome)
        second = self.bundle(outcome)
        after = tuple(sorted(item.relative_to(self.work).as_posix() for item in self.work.rglob("*")))
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(before, after)

    def test_fallback_and_failed_delivery_package_matrix(self):
        fallback = TierA07bOneRepairOrchestrator().run(
            **self.route_args("fallback", repair_value="still invalid")
        )
        self.assertEqual(fallback.status, "fallback_delivery")
        fallback_bundle = self.bundle(fallback)
        self.assertEqual(
            fallback_bundle.gate_delivery_binding["a07b_outcome"]["final_package_schema_version"],
            "req2web.result.package.v2",
        )
        failed_args = self.route_args("failed")
        failed_args["render_output_dir"] = self.package.package_dir
        failed = TierA07bOneRepairOrchestrator().run(**failed_args)
        self.assertEqual(failed.status, "failed_delivery")
        failed_bundle = self.bundle(failed)
        self.assertIsNone(failed_bundle.gate_delivery_binding["a07b_outcome"]["final_package_id"])
        self.assertEqual(failed_bundle.gate_delivery_binding["a07b_outcome"]["g2_action"], "failed_delivery")

    def test_route_no_run_cleanup_and_extra_key_drift_are_rejected(self):
        outcome = TierA07bOneRepairOrchestrator().run(**self.route_args("strict"))
        payload = self.bundle(outcome).to_dict()
        self.assertEqual(
            payload["cleanup_linkage"],
            {
                "tier_a_step_8_status": "implemented_local_controls_schema_only",
                "cleanup_policy_status": "not_created",
                "cleanup_receipt_status": "not_created",
                "cleanup_action_status": "not_executed",
            },
        )
        variants = []
        extra = deepcopy(payload); extra["provider_url"] = "https://example.invalid"; variants.append(extra)
        run = deepcopy(payload); run["run_occurred"] = True; variants.append(run)
        facts = deepcopy(payload); facts["run_facts"]["cost"] = 1; variants.append(facts)
        cleanup = deepcopy(payload); cleanup["cleanup_linkage"]["cleanup_action_status"] = "executed"; variants.append(cleanup)
        route = deepcopy(payload); route["gate_delivery_binding"]["real_run_route_occurred"] = True; variants.append(route)
        policy = deepcopy(payload); policy["d17_binding"]["field_policy_identity"] = "other-policy"; variants.append(policy)
        for value in variants:
            with self.subTest(value=value):
                self.assert_rejected(value)

    def test_live_identity_and_profile_drift_are_rejected(self):
        outcome = TierA07bOneRepairOrchestrator().run(**self.route_args("drift"))
        bundle = self.bundle(outcome)
        profile_payload = self.profile.to_dict()
        profile_payload["profile_id"] = self.profile.profile_id
        profile_payload["runtime_or_image"] = "chosen"
        with self.assertRaises(QwenProfilePlaceholderError):
            QwenProfilePlaceholder.from_dict(profile_payload)
        changed = deepcopy(bundle.to_dict())
        changed["profile_binding"]["profile_sha256"] = "0" * 64
        root = {key: value for key, value in changed.items() if key != "bundle_id"}
        changed["bundle_id"] = model_route_module._08_ID_PREFIX + model_route_module._sha256(
            model_route_module._canonical_json_bytes(root)
        )[:20]
        with self.assertRaises(TierA08RealRunBundleError):
            TierA08RealRunBundlePlaceholder.from_dict(changed)
        changed = deepcopy(bundle.to_dict())
        changed["d17_binding"]["complete_local_context_sha256"] = "1" * 64
        root = {key: value for key, value in changed.items() if key != "bundle_id"}
        changed["bundle_id"] = model_route_module._08_ID_PREFIX + model_route_module._sha256(
            model_route_module._canonical_json_bytes(root)
        )[:20]
        altered = TierA08RealRunBundlePlaceholder.from_dict(changed)
        with self.assertRaises(TierA08RealRunBundleError):
            altered.validate_against(
                self.context, self.manifest, self.selected, self.request, self.audit,
                self.preparation, self.profile, self.model_outcome, outcome,
            )

    def test_different_model_route_chain_cannot_be_combined_with_a07b_outcome(self):
        context_b = build_context()
        context_b.original_requirement = "A distinct synthetic local requirement."
        package_b, context_b, guidance_b, reference_b = build_g0(self.work / "other-g0", context=context_b)
        manifest_b, selected_b, request_b, audit_b, preparation_b = build_chain(context_b)
        fixture_b = ScriptedLocalFixture.create(
            SCRIPTED_FIXTURE_GATE_DELIVERY_VALID_KEY,
            ProviderRawResponse.from_bytes(gate_delivery_candidate_bytes(context_b)),
        )
        model_b = TierAModelRouteOrchestrator().run(
            frozen_g0_reference=reference_b, package=package_b, context=context_b, guidance=guidance_b,
            manifest=manifest_b, selected=selected_b, local_request=request_b,
            pre_invocation_audit=audit_b, local_qwen_preparation=preparation_b,
            execution_branch="scripted_local_fixture", scripted_local_fixture=fixture_b,
        )
        outcome_a = TierA07bOneRepairOrchestrator().run(**self.route_args("chain-a"))
        with self.assertRaises(TierA08RealRunBundleError):
            create_tier_a_08_real_run_bundle(
                self.context, self.manifest, self.selected, self.request, self.audit,
                self.preparation, self.profile, model_b, outcome_a,
            )

    def test_complete_a07b_record_rejects_resigned_state_drift(self):
        outcome = TierA07bOneRepairOrchestrator().run(**self.route_args("record"))
        payload = self.bundle(outcome).to_dict()

        def resign(value):
            inner = value["gate_delivery_binding"]["a07b_outcome"]
            root = {key: item for key, item in inner.items() if key != "outcome_id"}
            inner["outcome_id"] = model_route_module._07B_OUTCOME_ID_PREFIX + model_route_module._07b_hash(root)[:20]
            value["gate_delivery_binding"]["outcome_id"] = inner["outcome_id"]
            value["gate_delivery_binding"]["outcome_sha256"] = model_route_module._sha256(
                model_route_module._canonical_json_bytes(inner)
            )
            outer = {key: item for key, item in value.items() if key != "bundle_id"}
            value["bundle_id"] = model_route_module._08_ID_PREFIX + model_route_module._sha256(
                model_route_module._canonical_json_bytes(outer)
            )[:20]
            return value

        variants = []
        reason = deepcopy(payload); reason["gate_delivery_binding"]["a07b_outcome"]["fallback_reason"] = "repair_not_eligible"; variants.append(reason)
        status = deepcopy(payload); status["gate_delivery_binding"]["a07b_outcome"]["repair_status"] = "not_attempted"; variants.append(status)
        counts = deepcopy(payload); counts["gate_delivery_binding"]["a07b_outcome"]["acceptance_counts"]["pass"] -= 1; variants.append(counts)
        gate = deepcopy(payload); gate["gate_delivery_binding"]["a07b_outcome"]["final_gate_status"] = "blocked"; variants.append(gate)
        binding = deepcopy(payload); binding["gate_delivery_binding"]["a07b_outcome"]["model_route_binding"]["outcome_id"] = "other-outcome"; variants.append(binding)
        cross_case = deepcopy(payload); cross_case["gate_delivery_binding"]["a07b_outcome"]["case_id"] = "case-tier-a-08-other"; variants.append(cross_case)
        for value in variants:
            with self.subTest(value=value):
                self.assert_rejected(resign(value))

    def test_module_rebinding_cannot_change_existing_bundle_facts(self):
        outcome = TierA07bOneRepairOrchestrator().run(**self.route_args("rebind"))
        bundle = self.bundle(outcome)
        profile_bytes = self.profile.canonical_bytes()
        with patch.object(qwen_profile_module, "QwenProfilePlaceholder", object), patch.object(
            qwen_profile_module, "_canonical_bytes", side_effect=RuntimeError("rebound")
        ), patch.object(qwen_profile_module, "_sha256", side_effect=RuntimeError("rebound")), patch.object(
            qwen_profile_module, "QWEN_PROFILE_PLACEHOLDER_SCHEMA_VERSION", "rebound"
        ), patch.object(qwen_profile_module, "_PROFILE_NAMES", ("rebound",)), patch.object(
            qwen_profile_module, "_TIER_B_FIELDS", ("rebound",)
        ), patch.object(qwen_profile_module, "_PROFILE_KEYS", ("rebound",)), patch.object(
            qwen_profile_module, "QwenProfilePlaceholderError", RuntimeError
        ), patch.object(model_route_module, "TIER_A_08_REAL_RUN_BUNDLE_SCHEMA_VERSION", "rebound"), patch.object(
            model_route_module, "_08_KEYS", ("rebound",)
        ), patch.object(model_route_module, "_08_BINDING_KEYS", ("rebound",)
        ), patch.object(model_route_module, "_08_ROUTE_KEYS", ("rebound",)
        ), patch.object(model_route_module, "_08_binding", side_effect=RuntimeError("rebound")), patch.object(
            model_route_module, "TierA08RealRunBundleError", RuntimeError
        ), patch.object(
            model_route_module, "TierA07bGateDeliveryOutcome", object
        ), patch.object(model_route_module, "TierA08RealRunBundlePlaceholder", object), patch.object(
            model_route_module, "_08_profile_projection", side_effect=RuntimeError("rebound")
        ), patch.object(model_route_module, "_08_route_binding", side_effect=RuntimeError("rebound")):
            bundle.validate_against(
                self.context, self.manifest, self.selected, self.request, self.audit,
                self.preparation, self.profile, self.model_outcome, outcome,
            )
            self.assertEqual(TierA08RealRunBundlePlaceholder.from_bytes(bundle.canonical_bytes()), bundle)
            fresh_profile = create_qwen_profile_placeholder()
            self.assertEqual(fresh_profile.canonical_bytes(), profile_bytes)
            self.assertEqual(QwenProfilePlaceholder.from_bytes(profile_bytes), self.profile)
            fresh_bundle = create_tier_a_08_real_run_bundle(
                self.context, self.manifest, self.selected, self.request, self.audit,
                self.preparation, fresh_profile, self.model_outcome, outcome,
            )
            self.assertEqual(fresh_bundle.canonical_bytes(), bundle.canonical_bytes())

    def test_schema_contains_no_payload_path_or_sensitive_fields(self):
        outcome = TierA07bOneRepairOrchestrator().run(**self.route_args("safe"))
        payload = self.bundle(outcome).to_dict()

        def keys(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    yield key
                    yield from keys(nested)
            elif isinstance(value, list):
                for nested in value:
                    yield from keys(nested)

        key_text = " ".join(keys(payload)).lower()
        for prohibited in ("payload", "path", "url", "secret", "credential", "raw_bytes", "raw_payload"):
            self.assertNotIn(prohibited, key_text)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("original_requirement", encoded)
        self.assertNotIn("http://", encoded)
        self.assertNotIn("https://", encoded)


if __name__ == "__main__":
    unittest.main()
