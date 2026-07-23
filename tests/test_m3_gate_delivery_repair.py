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
from req2web_faults.fallback_delivery import freeze_g0_fallback_package  # noqa: E402
from req2web_orchestration.model_route import (  # noqa: E402
    SCRIPTED_ACCEPTANCE_FAIL_KEY,
    SCRIPTED_ACCEPTANCE_PASS_KEY,
    SCRIPTED_FIXTURE_ASSEMBLY_INVALID_KEY,
    SCRIPTED_FIXTURE_GATE_DELIVERY_VALID_KEY,
    SCRIPTED_FIXTURE_SEMANTIC_INVALID_KEY,
    ScriptedAcceptanceFixture,
    ScriptedLocalFixture,
    TierA07aGateDeliveryOrchestrator,
    TierA07bFieldGateReport,
    TierA07bGateDeliveryOutcome,
    TierA07bGateDeliveryOutcomeError,
    TierA07bOneRepairOrchestrator,
    TierA07bRepairPatch,
    TierAModelRouteOrchestrator,
)
from req2web_provider.semantic_candidate import ProviderRawResponse  # noqa: E402
from test_m3_gate_delivery import gate_delivery_candidate_bytes  # noqa: E402
from test_m3_model_route import (  # noqa: E402
    SEMANTIC_INVALID_BYTES,
    assembly_invalid_bytes,
    build_chain,
    build_g0,
)

TEST_ROOT = ROOT / "outputs" / "_m3_gate_delivery_repair_tests"


class TierA07bOneRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = TEST_ROOT / f"case-{uuid4().hex}"
        self.work.mkdir(parents=True)
        self.package, self.context, self.guidance, self.reference = build_g0(self.work / "g0")
        self.manifest, self.selected, self.request, self.audit, self.preparation = build_chain(self.context)
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
        self.case_id = "case-tier-a-07b"
        self.snapshot = self.work / "snapshot"
        self.fallback_record = freeze_g0_fallback_package(self.case_id, self.package, self.snapshot)
        self.first_page = model_route_module._07b_live_first_page_spec(
            self.model_outcome, self.fixture, self.context, self.guidance
        )
        self.receipt = model_route_module.create_tier_a_07b_field_gate_report(
            case_id=self.case_id, page_spec=self.first_page, local_request=self.request
        )
        self.scope = self.receipt.predicted_repair_scope

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)
        if TEST_ROOT.exists() and not any(TEST_ROOT.iterdir()):
            TEST_ROOT.rmdir()

    def repair_patch(self, receipt=None, *, value=None, operations=None):
        receipt = self.receipt if receipt is None else receipt
        if operations is None:
            operations = ((receipt.reported_field, receipt.expected if value is None else value),)
        return TierA07bRepairPatch.create(
            report_id=receipt.report_id, report_sha256=receipt.sha256(),
            first_page_id=self.first_page.page_id,
            first_page_spec_sha256=model_route_module._07b_page_binding(self.first_page)["page_spec_sha256"],
            attempt_index=1, operations=operations,
        )

    def args(self, suffix: str, *, receipt=None, repair_patch=None, acceptance=None):
        receipt = self.receipt if receipt is None else receipt
        repair_patch = self.repair_patch(receipt) if repair_patch is None else repair_patch
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
            "scripted_acceptance_fixture": ScriptedAcceptanceFixture.create(SCRIPTED_ACCEPTANCE_PASS_KEY) if acceptance is None else acceptance,
            "field_gate_report": receipt.canonical_bytes(), "repair_patch": repair_patch.canonical_bytes(),
        }

    def run_route(self, suffix: str, **changes):
        args = self.args(suffix, **changes)
        return TierA07bOneRepairOrchestrator().run(**args), args

    def build_run(self, **overrides):
        values = {
            "outcome_type": TierA07bGateDeliveryOutcome,
            "report_type": TierA07bFieldGateReport,
            "patch_type": TierA07bRepairPatch,
            "model_type": model_route_module.ModelRouteOutcome,
            "model_validate": model_route_module.ModelRouteOutcome.validate_against,
            "model_structural": model_route_module.ModelRouteOutcome.validate,
            "model_bytes": model_route_module.ModelRouteOutcome.canonical_bytes,
            "fixture_authority": model_route_module._FIXED_LIVE_FIXTURE_AUTHORITY,
            "assembly_authority": model_route_module._FIXED_CANONICAL_ASSEMBLY_AUTHORITY,
            "field_gate_authority": model_route_module._FIXED_07B_FIELD_GATE_AUTHORITY,
            "renderer_type": model_route_module.DeterministicPageRenderer,
            "render_result_type": model_route_module.RenderResult,
            "render_projection": model_route_module._FIXED_07A_RENDER_PROJECTION,
            "consistency_type": model_route_module.MinimalConsistencyChecker,
            "consistency_projection": model_route_module._FIXED_07A_CONSISTENCY_PROJECTION,
            "requirement_projector": model_route_module.project_requirement_view,
            "requirement_projection": model_route_module._FIXED_07A_REQUIREMENT_PROJECTION,
            "plan_compiler": model_route_module.compile_acceptance_plan,
            "plan_projection": model_route_module._FIXED_07A_PLAN_PROJECTION,
            "binding_compiler": model_route_module.compile_acceptance_binding,
            "binding_projection": model_route_module._FIXED_07A_BINDING_PROJECTION,
            "acceptance_fixture_authority": model_route_module._FIXED_ACCEPTANCE_FIXTURE_AUTHORITY,
            "acceptance_executor": model_route_module._FIXED_ACCEPTANCE_EXECUTION_AUTHORITY,
            "browser_projection": model_route_module._FIXED_07A_BROWSER_PROJECTION,
            "count_projection": model_route_module._FIXED_07A_ACCEPTANCE_COUNTS,
            "packager_type": model_route_module.DeterministicResultPackager,
            "package_type": model_route_module.ResultPackage,
            "package_projection": model_route_module._FIXED_07A_PACKAGE_PROJECTION,
            "fallback_binding_authority": model_route_module._FIXED_07A_FALLBACK_BINDING_AUTHORITY,
            "fallback_deliverer": model_route_module.deliver_frozen_g0_fallback,
            "load_fallback_report": model_route_module._FIXED_07A_LOAD_FALLBACK_REPORT,
            "fallback_projection": model_route_module._FIXED_07A_FALLBACK_REPORT_PROJECTION,
            "path_preflight": model_route_module._FIXED_07A_PATH_PREFLIGHT,
            "staging_path": model_route_module._FIXED_07A_STAGING_PATH,
            "cleanup": model_route_module._FIXED_07A_CLEANUP_DIRECTORY,
            "commit": model_route_module._FIXED_07A_COMMIT_STAGING,
            "rollback": model_route_module._FIXED_07A_ROLLBACK_COMMIT,
        }
        values.update(overrides)
        return model_route_module._build_tier_a_07b_run(**values)

    @staticmethod
    def path_snapshot(path: Path):
        if not path.exists():
            return ("missing",)
        if path.is_file():
            return ("file", path.read_bytes())
        return (
            "dir",
            tuple(
                (item.relative_to(path).as_posix(), item.is_dir(), None if item.is_dir() else item.read_bytes())
                for item in sorted(path.rglob("*"), key=lambda value: value.as_posix())
            ),
        )

    def forged_receipt_bytes(self, **changes) -> bytes:
        payload = self.receipt.to_dict()
        payload.update(changes)
        root = {key: value for key, value in payload.items() if key != "report_id"}
        payload["report_id"] = model_route_module._07B_REPORT_ID_PREFIX + model_route_module._07b_hash(root)[:20]
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def resign_outcome(payload):
        root = {key: value for key, value in payload.items() if key != "outcome_id"}
        payload["outcome_id"] = model_route_module._07B_OUTCOME_ID_PREFIX + model_route_module._07b_hash(root)[:20]
        return payload

    def assert_rejected_outcome_object_and_bytes(self, payload):
        payload = self.resign_outcome(payload)
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bGateDeliveryOutcome.from_dict(payload)
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bGateDeliveryOutcome.from_bytes(raw)

    def test_recovered_success_round_trip_and_replay(self):
        outcome, args = self.run_route("success")
        self.assertEqual(outcome.status, "recovered_success")
        self.assertEqual(outcome.completed_steps, model_route_module._07B_SUCCESS)
        self.assertEqual(outcome.effective_repair_scope, self.scope)
        self.assertEqual(outcome.first_candidate["traceability_sha256"], outcome.repaired_candidate["traceability_sha256"])
        self.assertEqual(outcome.repair_report["authority_id"], model_route_module._FIXED_07B_FIELD_GATE_AUTHORITY.authority_id)
        outcome.validate_against(**args)
        self.assertEqual(TierA07bGateDeliveryOutcome.from_bytes(outcome.canonical_bytes()).to_dict(), outcome.to_dict())

    def test_forged_receipt_fields_fail_closed_without_repair(self):
        forged_values = [
            {"expected": self.receipt.expected + " forged"},
            {"authority_id": "tier-a-07b-forged-authority"},
            {"authority_sha256": "0" * 64},
            {"rule_version": "tier-a-07b-forged-rule"},
            {"field_gate_input_sha256": "1" * 64},
            {"error_code": "page_title_invalid"},
            {"repair_eligible": False},
        ]
        for index, changes in enumerate(forged_values):
            args = self.args(f"forged-{index}")
            args["field_gate_report"] = self.forged_receipt_bytes(**changes)
            outcome = TierA07bOneRepairOrchestrator().run(**args)
            self.assertEqual(outcome.status, "fallback_delivery")
            self.assertIn(outcome.fallback_reason, {"field_gate_receipt_mismatch", "repair_receipt_invalid"})
            self.assertEqual(outcome.repair_attempted, 0)
        malformed = self.receipt.to_dict()
        malformed["predicted_repair_scope"] = ["pagespec.title"]
        args = self.args("forged-scope")
        args["field_gate_report"] = json.dumps(malformed, sort_keys=True, separators=(",", ":")).encode("utf-8")
        outcome = TierA07bOneRepairOrchestrator().run(**args)
        self.assertEqual(outcome.status, "fallback_delivery")
        self.assertEqual(outcome.fallback_reason, "repair_receipt_invalid")
        self.assertEqual(outcome.repair_attempted, 0)

    def test_post_repair_gate_must_explicitly_pass(self):
        patch = self.repair_patch(value=self.receipt.actual + " alternative")
        outcome, _ = self.run_route("post-not-pass", repair_patch=patch)
        self.assertEqual(outcome.status, "fallback_delivery")
        self.assertEqual(outcome.fallback_reason, "repair_post_gate_not_pass")
        self.assertEqual(outcome.repair_attempted, 1)
        self.assertEqual(outcome.affected_gate_status, "failed")

    def test_module_rebinding_cannot_replace_captured_authorities(self):
        args = self.args("rebind")
        fallback_args = self.args(
            "rebind-fallback", repair_patch=self.repair_patch(value="still invalid")
        )
        with patch.object(model_route_module, "project_requirement_view", side_effect=RuntimeError("rebound")), patch.object(model_route_module, "deliver_frozen_g0_fallback", side_effect=RuntimeError("rebound")), patch.object(model_route_module, "_FIXED_07B_FIELD_GATE_AUTHORITY", side_effect=RuntimeError("rebound")), patch.object(model_route_module, "DeterministicResultPackager", side_effect=RuntimeError("rebound")), patch.object(model_route_module, "_07b_model_binding", side_effect=RuntimeError("rebound")), patch.object(model_route_module, "_07a_g0_binding", side_effect=RuntimeError("rebound")), patch.object(model_route_module, "_07b_binding_projection", side_effect=RuntimeError("rebound")), patch.object(model_route_module, "_07b_report_binding", side_effect=RuntimeError("rebound")), patch.object(model_route_module, "_07b_intersection", side_effect=RuntimeError("rebound")), patch.object(model_route_module, "_07b_validate_counts", side_effect=RuntimeError("rebound")), patch.object(model_route_module, "_07b_page_binding", side_effect=RuntimeError("rebound")), patch.object(model_route_module, "_07B_REASON_STATE", {}), patch.object(model_route_module, "_07B_PREFIX_INDEX", {}), patch.object(model_route_module, "_07b_make_early_failure_outcome", side_effect=RuntimeError("rebound")):
            self.receipt.validate_against(
                self.case_id, self.first_page, self.receipt.request_sha256
            )
            self.assertEqual(
                TierA07bFieldGateReport.from_bytes(self.receipt.canonical_bytes()).to_dict(),
                self.receipt.to_dict(),
            )
            self.assertEqual(
                TierA07bRepairPatch.from_bytes(args["repair_patch"]).to_dict(),
                TierA07bRepairPatch.from_dict(
                    json.loads(args["repair_patch"].decode("utf-8"))
                ).to_dict(),
            )
            outcome = TierA07bOneRepairOrchestrator().run(**args)
            self.assertEqual(
                TierA07bGateDeliveryOutcome.from_bytes(outcome.canonical_bytes()).to_dict(),
                TierA07bGateDeliveryOutcome.from_dict(outcome.to_dict()).to_dict(),
            )
            outcome.validate_against(**args)
            fallback = TierA07bOneRepairOrchestrator().run(**fallback_args)
            self.assertEqual(fallback.fallback_reason, "repair_post_gate_not_pass")
            fallback.validate_against(**fallback_args)
        self.assertEqual(outcome.status, "recovered_success")

    def test_rule_steps_and_safe_id_are_captured_at_a07b_definition_time(self):
        original_rule = model_route_module._FIXED_07B_FIELD_GATE_AUTHORITY.rule_version
        original_success = model_route_module._07B_SUCCESS
        args = self.args("captured-identities")
        with patch.object(
            model_route_module, "TIER_A_07B_FIELD_GATE_RULE_VERSION",
            "tier-a-07b-rebound-rule",
        ), patch.object(
            model_route_module, "_07B_SUCCESS",
            (*original_success, "rebound-extra-step"),
        ):
            rebound_receipt = model_route_module.create_tier_a_07b_field_gate_report(
                case_id=self.case_id, page_spec=self.first_page,
                local_request=self.request,
            )
            rebound_patch = self.repair_patch(rebound_receipt)
            args["field_gate_report"] = rebound_receipt.canonical_bytes()
            args["repair_patch"] = rebound_patch.canonical_bytes()
            outcome = TierA07bOneRepairOrchestrator().run(**args)
            self.assertEqual(rebound_receipt.rule_version, original_rule)
            self.assertEqual(outcome.repair_report["rule_version"], original_rule)
            self.assertEqual(outcome.completed_steps, original_success)
            self.assertNotIn("rebound-extra-step", outcome.completed_steps)
            self.assertEqual(
                TierA07bGateDeliveryOutcome.from_bytes(
                    outcome.canonical_bytes()
                ).to_dict(),
                outcome.to_dict(),
            )
            outcome.validate_against(**args)

        fallback, fallback_args = self.run_route(
            "captured-safe-id-fallback",
            repair_patch=self.repair_patch(value="still invalid"),
        )
        with patch.object(
            model_route_module, "_07a_safe_id",
            side_effect=RuntimeError("rebound safe id"),
        ):
            outcome.validate()
            TierA07bGateDeliveryOutcome.from_dict(outcome.to_dict())
            TierA07bGateDeliveryOutcome.from_bytes(outcome.canonical_bytes())
            outcome.validate_against(**args)
            fallback.validate()
            TierA07bGateDeliveryOutcome.from_dict(fallback.to_dict())
            TierA07bGateDeliveryOutcome.from_bytes(fallback.canonical_bytes())
            fallback.validate_against(**fallback_args)

    def test_page_spec_type_is_captured_for_receipt_replay_and_new_run(self):
        args = self.args("captured-pagespec")
        original_rule = self.receipt.rule_version
        with patch.object(model_route_module, "PageSpec", object):
            self.receipt.validate_against(
                self.case_id, self.first_page, self.receipt.request_sha256
            )
            outcome = TierA07bOneRepairOrchestrator().run(**args)
            self.assertEqual(outcome.status, "recovered_success")
            self.assertEqual(outcome.repair_report["rule_version"], original_rule)
            outcome.validate_against(**args)
        self.receipt.validate_against(
            self.case_id, self.first_page, self.receipt.request_sha256
        )

    def test_identity_key_and_sha_validators_are_captured_at_definition_time(self):
        success, success_args = self.run_route("captured-identity-success")
        fallback, fallback_args = self.run_route(
            "captured-identity-fallback",
            repair_patch=self.repair_patch(value="still invalid"),
        )

        def assert_all_entry_points(outcome, args):
            outcome.validate()
            self.assertEqual(
                TierA07bGateDeliveryOutcome.from_dict(outcome.to_dict()).to_dict(),
                outcome.to_dict(),
            )
            self.assertEqual(
                TierA07bGateDeliveryOutcome.from_bytes(
                    outcome.canonical_bytes()
                ).to_dict(),
                outcome.to_dict(),
            )
            outcome.validate_against(**args)

        with patch.object(
            model_route_module, "_is_sha256",
            side_effect=RuntimeError("rebound sha validator"),
        ):
            assert_all_entry_points(success, success_args)
            assert_all_entry_points(fallback, fallback_args)

        invalid_sha = deepcopy(success.to_dict())
        invalid_sha["model_route_binding"]["outcome_sha256"] = "not-a-sha256"
        self.assert_rejected_outcome_object_and_bytes(invalid_sha)
        with patch.object(model_route_module, "_is_sha256", return_value=True):
            self.assert_rejected_outcome_object_and_bytes(invalid_sha)

        rebound_values = {
            "_07A_MODEL_ROUTE_BINDING_KEYS": ("rebound-model",),
            "_07A_G0_BINDING_KEYS": ("rebound-g0",),
            "_07A_FALLBACK_BINDING_KEYS": ("rebound-fallback",),
            "_07B_FIRST_KEYS": ("rebound-first",),
            "_07B_REPORT_KEYS": ("rebound-report",),
            "_07B_PATCH_KEYS": ("rebound-patch",),
            "_07B_COUNT_KEYS": ("rebound-count",),
            "RESULT_PACKAGE_SCHEMA_VERSION": "rebound-result-package-v1",
            "RESULT_PACKAGE_V2_SCHEMA_VERSION": "rebound-result-package-v2",
        }
        with patch.multiple(model_route_module, **rebound_values):
            assert_all_entry_points(success, success_args)
            assert_all_entry_points(fallback, fallback_args)

    def test_path_authority_rejects_protected_overlap_before_writes(self):
        args = self.args("protected-overlap")
        args["render_output_dir"] = self.package.package_dir
        outcome = TierA07bOneRepairOrchestrator().run(**args)
        self.assertEqual(outcome.status, "failed_delivery")
        self.assertEqual(outcome.failure_code, "output_destination_invalid")
        outcome.validate_against(**args)


    def test_reparse_path_is_rejected_or_controlled_skipped(self):
        target = self.work / "symlink-target"
        target.mkdir()
        link = self.work / "symlink-output"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Windows symlink privilege unavailable: {exc}")
        args = self.args("symlink")
        args["render_output_dir"] = link / "render"
        outcome = TierA07bOneRepairOrchestrator().run(**args)
        self.assertEqual(outcome.status, "failed_delivery")
        self.assertEqual(outcome.failure_code, "output_destination_invalid")

    def test_strict_outcome_matrix_and_noncanonical_payload_rejection(self):
        outcome, _ = self.run_route("strict")
        payload = deepcopy(outcome.to_dict())
        payload["completed_steps"].append("arbitrary_step")
        root = {key: value for key, value in payload.items() if key != "outcome_id"}
        payload["outcome_id"] = model_route_module._07B_OUTCOME_ID_PREFIX + model_route_module._07b_hash(root)[:20]
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bGateDeliveryOutcome.from_dict(payload)
        payload = deepcopy(outcome.to_dict())
        payload["repair_attempted"] = True
        root = {key: value for key, value in payload.items() if key != "outcome_id"}
        payload["outcome_id"] = model_route_module._07B_OUTCOME_ID_PREFIX + model_route_module._07b_hash(root)[:20]
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bGateDeliveryOutcome.from_dict(payload)
        for field, value in (("repair_status", "arbitrary"), ("fallback_reason", "arbitrary"), ("delivery_source", "arbitrary"), ("failure_code", "arbitrary"), ("repair_limit", 1.0)):
            payload = deepcopy(outcome.to_dict())
            payload[field] = value
            root = {key: item for key, item in payload.items() if key != "outcome_id"}
            payload["outcome_id"] = model_route_module._07B_OUTCOME_ID_PREFIX + model_route_module._07b_hash(root)[:20]
            with self.assertRaises(TierA07bGateDeliveryOutcomeError):
                TierA07bGateDeliveryOutcome.from_dict(payload)
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bGateDeliveryOutcome.from_bytes(b" " + outcome.canonical_bytes())
        payload = deepcopy(outcome.to_dict())
        payload["unexpected"] = "value"
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bGateDeliveryOutcome.from_dict(payload)
        duplicate = self.receipt.canonical_bytes().replace(b'"schema_version":', b'"schema_version":"x","schema_version":', 1)
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bFieldGateReport.from_bytes(duplicate)

    def test_out_of_scope_patch_and_scripted_acceptance_failure_use_fallback(self):
        patch = self.repair_patch(operations=(("pagespec.title", self.receipt.expected),))
        outcome, _ = self.run_route("scope", repair_patch=patch)
        self.assertEqual(outcome.status, "fallback_delivery")
        self.assertEqual(outcome.fallback_reason, "repair_patch_invalid")
        failed, args = self.run_route("acceptance", acceptance=ScriptedAcceptanceFixture.create(SCRIPTED_ACCEPTANCE_FAIL_KEY))
        self.assertEqual(failed.status, "fallback_delivery")
        self.assertEqual(failed.fallback_reason, "consistency_or_acceptance_blocked")
        failed.validate_against(**args)

    def test_exact_expected_rejects_same_pattern_forgery_and_replay_tamper(self):
        forged_patch = self.repair_patch(value="forged repaired")
        fallback, _ = self.run_route("forged-exact", repair_patch=forged_patch)
        self.assertEqual(fallback.status, "fallback_delivery")
        self.assertEqual(fallback.fallback_reason, "repair_post_gate_not_pass")
        self.assertEqual(fallback.repair_attempted, 1)

        success, args = self.run_route("exact-replay")
        args["repair_patch"] = forged_patch.canonical_bytes()
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            success.validate_against(**args)

    def test_early_failure_replay_rejects_stale_claim_without_output_side_effects(self):
        early_cases = []
        model_args = self.args("p1-model-early")
        model_args["model_route_outcome"] = object()
        early_cases.append(("model", model_args, "model_route_validation_failed"))

        frozen_args = self.args("p1-frozen-early")
        frozen_args["fallback_record"] = object()
        early_cases.append(("frozen", frozen_args, "frozen_g0_fallback_invalid"))

        output_args = self.args("p1-output-early")
        output_args["render_output_dir"] = self.package.package_dir
        early_cases.append(("output", output_args, "output_destination_invalid"))

        for label, early_args, code in early_cases:
            with self.subTest(stage=label):
                caller_outputs = (
                    early_args["render_output_dir"],
                    early_args["model_package_output_dir"],
                    early_args["fallback_output_dir"],
                )
                before = tuple(self.path_snapshot(path) for path in caller_outputs)
                outcome = TierA07bOneRepairOrchestrator().run(**early_args)
                self.assertEqual(outcome.status, "failed_delivery")
                self.assertEqual(outcome.failure_code, code)
                outcome.validate_against(**early_args)
                self.assertEqual(
                    tuple(self.path_snapshot(path) for path in caller_outputs), before
                )

                stale_args = self.args(f"p1-{label}-stale")
                stale_outputs = (
                    stale_args["render_output_dir"],
                    stale_args["model_package_output_dir"],
                    stale_args["fallback_output_dir"],
                )
                stale_before = tuple(self.path_snapshot(path) for path in stale_outputs)
                with self.assertRaises(TierA07bGateDeliveryOutcomeError):
                    outcome.validate_against(**stale_args)
                self.assertEqual(
                    tuple(self.path_snapshot(path) for path in stale_outputs), stale_before
                )

    def test_early_projection_and_factory_failures_are_live_replay_invalid(self):
        def raising(*_args, **_kwargs):
            raise RuntimeError("controlled helper failure")

        for helper in (
            "model_binding_projection", "g0_projection", "fallback_binding_projection"
        ):
            with self.subTest(helper=helper):
                args = self.args(f"projection-{helper}")
                outputs = (
                    args["render_output_dir"], args["model_package_output_dir"],
                    args["fallback_output_dir"],
                )
                before = tuple(self.path_snapshot(path) for path in outputs)
                run = self.build_run(**{helper: raising})
                with self.assertRaisesRegex(
                    TierA07bGateDeliveryOutcomeError, "^live_replay_invalid$"
                ):
                    run(object(), **args)
                self.assertEqual(
                    tuple(self.path_snapshot(path) for path in outputs), before
                )

        factory_args = self.args("factory-failure")
        factory_args["model_route_outcome"] = object()
        factory_outputs = (
            factory_args["render_output_dir"], factory_args["model_package_output_dir"],
            factory_args["fallback_output_dir"],
        )
        factory_before = tuple(self.path_snapshot(path) for path in factory_outputs)
        run = self.build_run(early_failure_factory=raising)
        with self.assertRaisesRegex(
            TierA07bGateDeliveryOutcomeError, "^live_replay_invalid$"
        ):
            run(object(), **factory_args)
        self.assertEqual(
            tuple(self.path_snapshot(path) for path in factory_outputs), factory_before
        )

        for label, changes in (
            ("model", {"model_route_outcome": object()}),
            ("frozen", {"fallback_record": object()}),
        ):
            with self.subTest(factory_stage=label):
                args = self.args(f"factory-{label}")
                args.update(changes)
                outputs = tuple(
                    args[key] for key in (
                        "render_output_dir", "model_package_output_dir",
                        "fallback_output_dir",
                    )
                )
                before = tuple(self.path_snapshot(path) for path in outputs)
                with self.assertRaisesRegex(
                    TierA07bGateDeliveryOutcomeError, "^live_replay_invalid$"
                ):
                    self.build_run(early_failure_factory=raising)(object(), **args)
                self.assertEqual(tuple(self.path_snapshot(path) for path in outputs), before)

        path_args = self.args("factory-path")
        path_outputs = tuple(
            path_args[key] for key in (
                "render_output_dir", "model_package_output_dir", "fallback_output_dir",
            )
        )
        path_before = tuple(self.path_snapshot(path) for path in path_outputs)
        with self.assertRaisesRegex(
            TierA07bGateDeliveryOutcomeError, "^live_replay_invalid$"
        ):
            self.build_run(
                early_failure_factory=raising,
                path_preflight=raising,
            )(object(), **path_args)
        self.assertEqual(tuple(self.path_snapshot(path) for path in path_outputs), path_before)

        page_args = self.args("page-projection")
        page_outputs = tuple(
            page_args[key] for key in (
                "render_output_dir", "model_package_output_dir", "fallback_output_dir",
            )
        )
        page_before = tuple(self.path_snapshot(path) for path in page_outputs)
        with self.assertRaisesRegex(
            TierA07bGateDeliveryOutcomeError, "^live_replay_invalid$"
        ):
            self.build_run(page_binding_projection=raising)(object(), **page_args)
        self.assertEqual(tuple(self.path_snapshot(path) for path in page_outputs), page_before)

    def test_early_authority_invocation_failures_keep_stage_specific_codes(self):
        def raising(*_args, **_kwargs):
            raise RuntimeError("controlled authority failure")

        frozen_args = self.args("authority-frozen")
        frozen = self.build_run(fallback_binding_authority=raising)(
            object(), **frozen_args
        )
        self.assertEqual(frozen.failure_code, "frozen_g0_fallback_invalid")

        output_args = self.args("authority-output")
        output = self.build_run(path_preflight=raising)(object(), **output_args)
        self.assertEqual(output.failure_code, "output_destination_invalid")
        self.assertTrue(
            all(
                not output_args[key].exists()
                for key in ("render_output_dir", "model_package_output_dir", "fallback_output_dir")
            )
        )

    def test_p1_state_matrix_rejects_tampered_object_and_bytes(self):
        success, success_args = self.run_route("p1-success")
        self.assertEqual(success.status, "recovered_success")
        success.validate_against(**success_args)

        post_patch = self.repair_patch(value=self.receipt.actual + " p1-post")
        post_failure, post_args = self.run_route("p1-post", repair_patch=post_patch)
        self.assertEqual(post_failure.fallback_reason, "repair_post_gate_not_pass")
        post_failure.validate_against(**post_args)

        acceptance_failure, acceptance_args = self.run_route(
            "p1-acceptance",
            acceptance=ScriptedAcceptanceFixture.create(SCRIPTED_ACCEPTANCE_FAIL_KEY),
        )
        self.assertEqual(acceptance_failure.fallback_reason, "consistency_or_acceptance_blocked")
        acceptance_failure.validate_against(**acceptance_args)

        recovered_counts = deepcopy(success.to_dict())
        self.assertGreater(recovered_counts["acceptance_counts"]["pass"], 0)
        recovered_counts["acceptance_counts"]["pass"] -= 1
        recovered_counts["acceptance_counts"]["fail"] = 1
        self.assert_rejected_outcome_object_and_bytes(recovered_counts)

        forged_post = deepcopy(post_failure.to_dict())
        forged_post["post_repair_gate"]["decision"] = "pass"
        forged_post["post_repair_gate"]["repair_eligible"] = False
        self.assert_rejected_outcome_object_and_bytes(forged_post)

        forged_blocked = deepcopy(acceptance_failure.to_dict())
        forged_blocked["consistency_status"] = "pass"
        forged_blocked["acceptance_status"] = "pass"
        forged_blocked["final_gate_status"] = "passed"
        counts = forged_blocked["acceptance_counts"]
        counts["pass"] = counts["total"]
        counts["fail"] = 0
        counts["unknown"] = 0
        counts["not_supported"] = 0
        self.assert_rejected_outcome_object_and_bytes(forged_blocked)

    def test_reason_specific_templates_reject_five_resigned_counterexamples(self):
        post_failure, post_args = self.run_route(
            "reason-post", repair_patch=self.repair_patch(value="still invalid")
        )
        self.assertEqual(post_failure.fallback_reason, "repair_post_gate_not_pass")
        post_failure.validate_against(**post_args)

        blocked, blocked_args = self.run_route(
            "reason-blocked",
            acceptance=ScriptedAcceptanceFixture.create(SCRIPTED_ACCEPTANCE_FAIL_KEY),
        )
        self.assertEqual(blocked.fallback_reason, "consistency_or_acceptance_blocked")
        blocked.validate_against(**blocked_args)

        not_eligible = deepcopy(post_failure.to_dict())
        not_eligible["fallback_reason"] = "repair_not_eligible"
        not_eligible["completed_steps"] = [
            *model_route_module._07b_fallback_prefix("repair_not_eligible"),
            model_route_module._07B_STEP_FALLBACK,
        ]
        not_eligible["repaired_candidate"] = {
            key: None for key in not_eligible["repaired_candidate"]
        }
        not_eligible["repair_patch"] = {
            key: None for key in not_eligible["repair_patch"]
        }
        not_eligible["post_repair_gate"] = {
            key: None for key in not_eligible["post_repair_gate"]
        }
        not_eligible["repair_attempted"] = 0
        not_eligible["repair_status"] = "not_attempted_not_eligible"
        not_eligible["affected_gate_status"] = "field_error"
        not_eligible["final_gate_status"] = "not_executed"
        not_eligible["consistency_status"] = "not_executed"
        not_eligible["acceptance_status"] = "not_executed"
        not_eligible["acceptance_counts"] = model_route_module._07b_empty_counts()

        valid_scope_rejection = deepcopy(not_eligible)
        valid_scope_rejection["fallback_reason"] = "repair_scope_not_authorized"
        valid_scope_rejection["completed_steps"] = [
            *model_route_module._07b_fallback_prefix("repair_scope_not_authorized"),
            model_route_module._07B_STEP_FALLBACK,
        ]
        valid_scope_rejection["policy_allowed_scope"] = []
        valid_scope_rejection["effective_repair_scope"] = []
        valid_scope_rejection["repair_status"] = "not_attempted_scope_rejected"
        valid_scope_rejection = self.resign_outcome(valid_scope_rejection)
        valid_scope = TierA07bGateDeliveryOutcome.from_dict(valid_scope_rejection)
        self.assertEqual(
            TierA07bGateDeliveryOutcome.from_bytes(valid_scope.canonical_bytes()).to_dict(),
            valid_scope.to_dict(),
        )

        counterexamples = [("repair-not-eligible", not_eligible)]
        for reason in ("model_package_failed", "model_package_live_replay_invalid"):
            missing_execution_state = deepcopy(blocked.to_dict())
            missing_execution_state["fallback_reason"] = reason
            missing_execution_state["completed_steps"] = [
                *model_route_module._07b_fallback_prefix(reason),
                model_route_module._07B_STEP_FALLBACK,
            ]
            missing_execution_state["final_gate_status"] = "passed"
            missing_execution_state["consistency_status"] = "not_executed"
            missing_execution_state["acceptance_status"] = "not_executed"
            missing_execution_state["acceptance_counts"] = model_route_module._07b_empty_counts()
            counterexamples.append((f"{reason}-missing-execution-state", missing_execution_state))

            zero_total_pass = deepcopy(missing_execution_state)
            zero_total_pass["consistency_status"] = "pass"
            zero_total_pass["acceptance_status"] = "pass"
            counterexamples.append((f"{reason}-zero-total-pass", zero_total_pass))

        self.assertEqual(len(counterexamples), 5)
        for label, payload in counterexamples:
            with self.subTest(counterexample=label):
                self.assert_rejected_outcome_object_and_bytes(payload)

    def test_repair_not_eligible_requires_captured_pass_receipt_template(self):
        success, _ = self.run_route("not-eligible-pass")
        fallback, _ = self.run_route(
            "not-eligible-source", repair_patch=self.repair_patch(value="still invalid")
        )
        self.assertEqual(success.status, "recovered_success")
        self.assertEqual(fallback.fallback_reason, "repair_post_gate_not_pass")

        valid = deepcopy(fallback.to_dict())
        valid["fallback_reason"] = "repair_not_eligible"
        valid["completed_steps"] = [
            *model_route_module._07b_fallback_prefix("repair_not_eligible"),
            model_route_module._07B_STEP_FALLBACK,
        ]
        valid["repaired_candidate"] = {
            key: None for key in valid["repaired_candidate"]
        }
        valid["repair_patch"] = {key: None for key in valid["repair_patch"]}
        valid["post_repair_gate"] = {
            key: None for key in valid["post_repair_gate"]
        }
        valid["repair_report"] = deepcopy(success.to_dict()["post_repair_gate"])
        valid["predicted_repair_scope"] = []
        valid["policy_allowed_scope"] = []
        valid["effective_repair_scope"] = []
        valid["repair_attempted"] = 0
        valid["repair_status"] = "not_attempted_not_eligible"
        valid["affected_gate_status"] = "field_error"
        valid["final_gate_status"] = "not_executed"
        valid["consistency_status"] = "not_executed"
        valid["acceptance_status"] = "not_executed"
        valid["acceptance_counts"] = model_route_module._07b_empty_counts()
        valid = self.resign_outcome(valid)
        accepted = TierA07bGateDeliveryOutcome.from_dict(valid)
        self.assertEqual(
            TierA07bGateDeliveryOutcome.from_bytes(accepted.canonical_bytes()).to_dict(),
            accepted.to_dict(),
        )

        variants = []
        repair_false = deepcopy(valid)
        repair_false["repair_report"]["decision"] = "repair"
        repair_false["repair_report"]["repair_eligible"] = False
        variants.append(("repair-false", repair_false))

        pass_true = deepcopy(valid)
        pass_true["repair_report"]["decision"] = "pass"
        pass_true["repair_report"]["repair_eligible"] = True
        variants.append(("pass-true", pass_true))

        pass_scopes = deepcopy(valid)
        pass_scopes["predicted_repair_scope"] = list(self.scope)
        pass_scopes["policy_allowed_scope"] = list(self.scope)
        pass_scopes["effective_repair_scope"] = list(self.scope)
        variants.append(("pass-nonempty-scopes", pass_scopes))

        for label, payload in variants:
            with self.subTest(variant=label):
                self.assert_rejected_outcome_object_and_bytes(payload)

    def test_validate_against_replays_failure_stages_in_exact_order(self):
        # A model-route failure claim is accepted only while the current model
        # route still fails; changing it to a valid route is a false claim.
        bad_model_args = self.args("model-failure")
        bad_model_args["model_route_outcome"] = object()
        model_failure = TierA07bOneRepairOrchestrator().run(**bad_model_args)
        self.assertEqual(model_failure.failure_code, "model_route_validation_failed")
        model_failure.validate_against(**bad_model_args)
        valid_model_args = self.args("model-failure-valid")
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            model_failure.validate_against(**valid_model_args)

        # A frozen-G0 failure claim requires a valid earlier model route and a
        # currently failing captured fallback-binding authority.
        bad_frozen_args = self.args("frozen-failure")
        bad_frozen_args["fallback_record"] = object()
        frozen_failure = TierA07bOneRepairOrchestrator().run(**bad_frozen_args)
        self.assertEqual(frozen_failure.failure_code, "frozen_g0_fallback_invalid")
        frozen_failure.validate_against(**bad_frozen_args)
        valid_frozen_args = self.args("frozen-failure-valid")
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            frozen_failure.validate_against(**valid_frozen_args)

        # The output failure is accepted only after current model and frozen-G0
        # validation succeed.  A later unsafe path cannot mask a broken model.
        output_args = self.args("output-failure")
        output_args["render_output_dir"] = self.package.package_dir
        output_failure = TierA07bOneRepairOrchestrator().run(**output_args)
        self.assertEqual(output_failure.failure_code, "output_destination_invalid")
        output_failure.validate_against(**output_args)
        masked_earlier = dict(output_args)
        masked_earlier["model_route_outcome"] = object()
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            output_failure.validate_against(**masked_earlier)
        false_output_claim = self.args("output-failure-valid")
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            output_failure.validate_against(**false_output_claim)

    def fail_closed_model_args(self, suffix: str, *, fixture_key=None, raw=None):
        values = {
            "frozen_g0_reference": self.reference, "package": self.package,
            "context": self.context, "guidance": self.guidance,
            "manifest": self.manifest, "selected": self.selected,
            "local_request": self.request, "pre_invocation_audit": self.audit,
            "local_qwen_preparation": self.preparation,
        }
        if fixture_key is None:
            outcome = TierAModelRouteOrchestrator().run(
                **values, execution_branch="tier_b_authorization_probe"
            )
            args = self.args(suffix)
            args.update({
                "model_route_outcome": outcome,
                "execution_branch": "tier_b_authorization_probe",
                "scripted_local_fixture": None,
            })
            return outcome, args
        fixture = ScriptedLocalFixture.create(
            fixture_key, ProviderRawResponse.from_bytes(raw)
        )
        outcome = TierAModelRouteOrchestrator().run(
            **values, execution_branch="scripted_local_fixture",
            scripted_local_fixture=fixture,
        )
        args = self.args(suffix)
        args.update({
            "model_route_outcome": outcome,
            "execution_branch": "scripted_local_fixture",
            "scripted_local_fixture": fixture,
        })
        return outcome, args

    def test_replayable_a06_fail_closed_outcomes_fallback_without_repair(self):
        cases = (
            ("semantic", SCRIPTED_FIXTURE_SEMANTIC_INVALID_KEY, SEMANTIC_INVALID_BYTES, "semantic_candidate_invalid"),
            ("assembly", SCRIPTED_FIXTURE_ASSEMBLY_INVALID_KEY, assembly_invalid_bytes(), "page_spec_assembly_invalid"),
            ("tier-b", None, None, "tier_b_unapproved"),
        )
        for suffix, fixture_key, raw, expected_failure in cases:
            with self.subTest(case=suffix):
                model_outcome, args = self.fail_closed_model_args(
                    suffix, fixture_key=fixture_key, raw=raw
                )
                self.assertEqual(model_outcome.disposition, "fail_closed")
                self.assertEqual(model_outcome.failure.code, expected_failure)
                outcome = TierA07bOneRepairOrchestrator().run(**args)
                self.assertEqual(outcome.status, "fallback_delivery")
                self.assertEqual(outcome.fallback_reason, "model_route_not_assembled")
                self.assertEqual(outcome.completed_steps, (*model_route_module._07B_BASE, model_route_module._07B_STEP_FALLBACK))
                self.assertEqual(outcome.repair_attempted, 0)
                self.assertEqual(outcome.model_route_binding["disposition"], "fail_closed")
                self.assertEqual(outcome.model_route_binding["failure_code"], expected_failure)
                self.assertIsNone(outcome.model_route_binding["assembled_page_id"])
                self.assertIsNone(outcome.model_route_binding["assembled_page_spec_sha256"])
                self.assertTrue(all(value is None for value in outcome.first_candidate.values()))
                outcome.validate_against(**args)

    def test_early_failure_cannot_be_cross_case_resigned(self):
        args = self.args("early-case")
        args["model_route_outcome"] = object()
        outcome = TierA07bOneRepairOrchestrator().run(**args)
        self.assertEqual(outcome.failure_code, "model_route_validation_failed")
        outcome.validate_against(**args)
        payload = deepcopy(outcome.to_dict())
        payload["case_id"] = "case-tier-a-07b-forged"
        root = {key: value for key, value in payload.items() if key != "outcome_id"}
        payload["outcome_id"] = model_route_module._07B_OUTCOME_ID_PREFIX + model_route_module._07b_hash(root)[:20]
        forged = TierA07bGateDeliveryOutcome.from_dict(payload)
        with patch.object(
            model_route_module, "_07b_make_early_failure_outcome",
            return_value=forged,
        ):
            with self.assertRaises(TierA07bGateDeliveryOutcomeError):
                forged.validate_against(**args)

        valid_args = self.args("early-case-valid")
        with patch.object(
            model_route_module, "_07b_model_binding",
            side_effect=RuntimeError("rebound projection"),
        ):
            with self.assertRaises(TierA07bGateDeliveryOutcomeError):
                outcome.validate_against(**valid_args)

    def test_state_matrix_rejects_package_role_and_nested_binding_forgery(self):
        success, success_args = self.run_route("matrix-success")
        fallback, _ = self.run_route(
            "matrix-fallback", repair_patch=self.repair_patch(value="forged repaired")
        )

        def resign(payload):
            root = {key: value for key, value in payload.items() if key != "outcome_id"}
            payload["outcome_id"] = model_route_module._07B_OUTCOME_ID_PREFIX + model_route_module._07b_hash(root)[:20]
            return payload

        recovered_as_v2 = resign(deepcopy(success.to_dict()))
        recovered_as_v2["final_package_schema_version"] = model_route_module.RESULT_PACKAGE_V2_SCHEMA_VERSION
        recovered_as_v2 = resign(recovered_as_v2)
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bGateDeliveryOutcome.from_dict(recovered_as_v2)

        fallback_as_v1 = resign(deepcopy(fallback.to_dict()))
        fallback_as_v1["final_package_schema_version"] = model_route_module.RESULT_PACKAGE_SCHEMA_VERSION
        fallback_as_v1 = resign(fallback_as_v1)
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bGateDeliveryOutcome.from_dict(fallback_as_v1)

        bad_binding = resign(deepcopy(fallback.to_dict()))
        bad_binding["model_route_binding"]["failure_code"] = {"not": "scalar"}
        bad_binding = resign(bad_binding)
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bGateDeliveryOutcome.from_dict(bad_binding)

        missing_assembled_first = resign(deepcopy(fallback.to_dict()))
        missing_assembled_first["first_candidate"] = {
            key: None for key in missing_assembled_first["first_candidate"]
        }
        missing_assembled_first = resign(missing_assembled_first)
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bGateDeliveryOutcome.from_dict(missing_assembled_first)

        mismatched_model_binding = resign(deepcopy(success.to_dict()))
        mismatched_model_binding["model_route_binding"]["assembled_page_spec_sha256"] = "0" * 64
        mismatched_model_binding = resign(mismatched_model_binding)
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bGateDeliveryOutcome.from_dict(mismatched_model_binding)

        wrong_post_gate_state = resign(deepcopy(fallback.to_dict()))
        wrong_post_gate_state["repair_status"] = "repair_completed_final_gate_failed"
        wrong_post_gate_state = resign(wrong_post_gate_state)
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            TierA07bGateDeliveryOutcome.from_dict(wrong_post_gate_state)

        for field, value in (("final_gate_status", "blocked"), ("repair_attempted", 0)):
            payload = resign(deepcopy(success.to_dict()))
            payload[field] = value
            payload = resign(payload)
            with self.assertRaises(TierA07bGateDeliveryOutcomeError):
                TierA07bGateDeliveryOutcome.from_dict(payload)

        forged_counts = resign(deepcopy(success.to_dict()))
        forged_counts["acceptance_counts"]["pass"] += 1
        forged_counts["acceptance_counts"]["total"] += 1
        forged_counts = resign(forged_counts)
        forged = TierA07bGateDeliveryOutcome.from_dict(forged_counts)
        with self.assertRaises(TierA07bGateDeliveryOutcomeError):
            forged.validate_against(**success_args)

    def test_acceptance_executor_failure_keeps_fixture_step(self):
        def raising_executor(*_args):
            raise RuntimeError("controlled executor failure")

        run = self.build_run(acceptance_executor=raising_executor)
        outcome = run(object(), **self.args("executor-error"))
        self.assertEqual(outcome.status, "fallback_delivery")
        self.assertEqual(outcome.fallback_reason, "acceptance_execution_failed")
        self.assertIn(model_route_module._07B_STEP_FIXTURE, outcome.completed_steps)
        self.assertNotIn(model_route_module._07B_STEP_EXECUTION, outcome.completed_steps)
        self.assertEqual(
            outcome.completed_steps,
            (*model_route_module._07b_fallback_prefix("acceptance_execution_failed"), model_route_module._07B_STEP_FALLBACK),
        )

    def test_a07a_first_pass_regression_remains_compatible(self):
        args = self.args("a07a")
        args.pop("field_gate_report"); args.pop("repair_patch")
        outcome = TierA07aGateDeliveryOrchestrator().run(**args)
        self.assertEqual(outcome.status, "first_pass_success")
        self.assertEqual(outcome.repair_attempted, 0)


if __name__ == "__main__":
    unittest.main()
