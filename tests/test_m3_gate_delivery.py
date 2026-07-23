from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import req2web_acceptance.browser_executor as browser_executor_module  # noqa: E402
from req2web_faults.fallback_delivery import (  # noqa: E402
    FrozenG0FallbackRecord,
    freeze_g0_fallback_package,
)
from req2web_generation.result_package import RESULT_PACKAGE_SCHEMA_VERSION  # noqa: E402
from req2web_generation.result_package_v2 import RESULT_PACKAGE_V2_SCHEMA_VERSION  # noqa: E402
import req2web_orchestration.model_route as model_route_module  # noqa: E402
from req2web_orchestration.model_route import (  # noqa: E402
    SCRIPTED_ACCEPTANCE_FAIL_KEY,
    SCRIPTED_ACCEPTANCE_PASS_KEY,
    SCRIPTED_ACCEPTANCE_UNKNOWN_KEY,
    SCRIPTED_FIXTURE_ASSEMBLY_INVALID_KEY,
    SCRIPTED_FIXTURE_GATE_DELIVERY_VALID_KEY,
    SCRIPTED_FIXTURE_SEMANTIC_INVALID_KEY,
    ScriptedAcceptanceFixture,
    ScriptedLocalFixture,
    TierA07aGateDeliveryOrchestrator,
    TierA07aGateDeliveryOutcome,
    TierA07aGateDeliveryOutcomeError,
    TierAModelRouteOrchestrator,
)
from req2web_provider.semantic_candidate import ProviderRawResponse  # noqa: E402
from test_m3_model_route import (  # noqa: E402
    SEMANTIC_INVALID_BYTES,
    assembly_invalid_bytes,
    build_chain,
    build_g0,
)
from test_m3_semantic_candidate_assembly import (  # noqa: E402
    candidate_payload,
    raw_bytes,
)


TEST_ROOT = ROOT / "outputs" / "_m3_gate_delivery_tests"


def gate_delivery_candidate_bytes(context) -> bytes:
    payload = deepcopy(candidate_payload())
    payload["states"][0]["name"] = "initial"
    payload["states"].insert(
        1,
        {
            "stable_id": "state-error",
            "name": "error",
            "description": "Show a recoverable input error.",
            "visible_component_stable_ids": [
                "component-search",
                "component-search-result",
            ],
        },
    )
    by_id = {item.use_case_id: item for item in context.use_cases}
    for interaction in payload["interactions"]:
        interaction["user_feedback"] = by_id[
            interaction["use_case_ids"][0]
        ].expected_outcome
    payload["interactions"].extend(
        [
            {
                "stable_id": "interaction-error-input",
                "trigger_component_stable_id": "component-search",
                "source_state_stable_id": "state-ready",
                "action": "Show a controlled invalid-input error.",
                "target_state_stable_id": "state-error",
                "user_feedback": "Input requires correction.",
                "use_case_ids": ["UC-01"],
            },
            {
                "stable_id": "interaction-recovery-input",
                "trigger_component_stable_id": "component-search",
                "source_state_stable_id": "state-error",
                "action": "Retry after correcting the input.",
                "target_state_stable_id": "state-success",
                "user_feedback": by_id["UC-01"].expected_outcome,
                "use_case_ids": ["UC-01"],
            },
        ]
    )
    payload["acceptance_checks"].append(
        {
            "stable_id": "check-z-error-input",
            "description": "Invalid input exposes a recoverable error state.",
            "use_case_ids": ["UC-01"],
            "state_stable_id": "state-error",
        }
    )
    payload["use_case_mappings"][0]["interaction_stable_ids"].extend(
        ["interaction-error-input", "interaction-recovery-input"]
    )
    return raw_bytes(payload)


def resign_07a(payload: dict[str, object]) -> dict[str, object]:
    root = dict(payload)
    root.pop("outcome_id")
    payload["outcome_id"] = (
        model_route_module._07A_OUTCOME_ID_PREFIX
        + model_route_module._sha256(
            model_route_module._canonical_json_bytes(root)
        )
    )
    return payload


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class TierA07aGateDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = TEST_ROOT / f"case-{uuid4().hex}"
        self.work.mkdir(parents=True)
        (
            self.package,
            self.context,
            self.guidance,
            self.reference,
        ) = build_g0(self.work / "g0")
        (
            self.manifest,
            self.selected,
            self.request,
            self.audit,
            self.preparation,
        ) = build_chain(self.context)
        raw = gate_delivery_candidate_bytes(self.context)
        self.assertEqual(len(raw), 4115)
        self.model_fixture = ScriptedLocalFixture.create(
            SCRIPTED_FIXTURE_GATE_DELIVERY_VALID_KEY,
            ProviderRawResponse.from_bytes(raw),
        )
        self.model_outcome = self.run_model_route(
            execution_branch="scripted_local_fixture",
            scripted_local_fixture=self.model_fixture,
        )
        self.case_id = "case-tier-a-07a"
        self.snapshot = self.work / "snapshot"
        self.fallback_record = freeze_g0_fallback_package(
            self.case_id,
            self.package,
            self.snapshot,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)
        if TEST_ROOT.exists() and not any(TEST_ROOT.iterdir()):
            TEST_ROOT.rmdir()

    def run_model_route(self, **changes):
        values = {
            "frozen_g0_reference": self.reference,
            "package": self.package,
            "context": self.context,
            "guidance": self.guidance,
            "manifest": self.manifest,
            "selected": self.selected,
            "local_request": self.request,
            "pre_invocation_audit": self.audit,
            "local_qwen_preparation": self.preparation,
        }
        values.update(changes)
        return TierAModelRouteOrchestrator().run(**values)

    def gate_args(
        self,
        suffix: str,
        *,
        model_outcome=None,
        acceptance_fixture=None,
        **changes,
    ) -> dict[str, object]:
        values = {
            "model_route_outcome": (
                self.model_outcome if model_outcome is None else model_outcome
            ),
            "frozen_g0_reference": self.reference,
            "package": self.package,
            "context": self.context,
            "guidance": self.guidance,
            "manifest": self.manifest,
            "selected": self.selected,
            "local_request": self.request,
            "pre_invocation_audit": self.audit,
            "local_qwen_preparation": self.preparation,
            "execution_branch": "scripted_local_fixture",
            "scripted_local_fixture": self.model_fixture,
            "case_id": self.case_id,
            "fallback_record": self.fallback_record,
            "fallback_snapshot_dir": self.snapshot,
            "render_output_dir": self.work / f"render-{suffix}",
            "model_package_output_dir": self.work / f"model-package-{suffix}",
            "fallback_output_dir": self.work / f"fallback-{suffix}",
            "scripted_acceptance_fixture": acceptance_fixture,
        }
        values.update(changes)
        return values

    def run_gate(self, suffix: str, **changes):
        args = self.gate_args(suffix, **changes)
        return TierA07aGateDeliveryOrchestrator().run(**args), args

    def assert_live_round_trip(self, outcome, args) -> None:
        outcome.validate_against(**args)
        loaded = TierA07aGateDeliveryOutcome.from_bytes(
            outcome.canonical_bytes()
        )
        loaded.validate_against(**args)
        self.assertEqual(loaded.to_dict(), outcome.to_dict())

    def assert_fallback_exact(self, output_dir: Path) -> None:
        self.assertEqual(
            tree_bytes(self.snapshot / "result_package"),
            tree_bytes(output_dir / "result_package"),
        )

    def failure_projection_from_success(
        self,
        success: TierA07aGateDeliveryOutcome,
        failure_template: TierA07aGateDeliveryOutcome,
    ) -> TierA07aGateDeliveryOutcome:
        payload = deepcopy(success.to_dict())
        template = failure_template.to_dict()
        preserved = {
            "schema_version",
            "case_id",
            "model_route_binding",
            "g0_binding",
            "fallback_binding",
            "outcome_id",
        }
        for key, value in template.items():
            if key not in preserved:
                payload[key] = deepcopy(value)
        return TierA07aGateDeliveryOutcome.from_dict(resign_07a(payload))


    def test_first_pass_success_keeps_not_supported_and_delivers_v1(self) -> None:
        outcome, args = self.run_gate(
            "success",
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_PASS_KEY
            ),
        )
        self.assertEqual(outcome.status, "first_pass_success")
        self.assertEqual(outcome.g1_package_purpose, "evaluation_only")
        self.assertEqual(outcome.g2_action, "no_change")
        self.assertEqual(outcome.delivery_source, "model_first_pass_v1")
        self.assertEqual(outcome.gate_status, "passed")
        self.assertEqual(
            dict(outcome.acceptance_counts),
            {"total": 5, "pass": 2, "fail": 0, "unknown": 0, "not_supported": 3},
        )
        self.assertEqual(
            outcome.final_package_schema_version,
            RESULT_PACKAGE_SCHEMA_VERSION,
        )
        self.assertFalse(outcome.fallback_attempted)
        self.assertEqual(outcome.artifacts["render_file_count"], 4)
        self.assertEqual(outcome.artifacts["model_package_file_count"], 9)
        self.assert_live_round_trip(outcome, args)

        manifest = json.loads(
            (
                args["model_package_output_dir"] / "package_manifest.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schema_version"], RESULT_PACKAGE_SCHEMA_VERSION)
        paths = {entry["path"] for entry in manifest["files"]}
        self.assertNotIn("internal/retrieval_guidance.json", paths)
        self.assertNotIn("internal/retrieval_influence_report.json", paths)
        self.assertNotIn("internal/guided_page_spec_build_result.json", paths)
        self.assertFalse(args["fallback_output_dir"].exists())

    def test_acceptance_fail_and_unknown_deliver_exact_frozen_g0(self) -> None:
        for key, expected_field in (
            (SCRIPTED_ACCEPTANCE_FAIL_KEY, "fail"),
            (SCRIPTED_ACCEPTANCE_UNKNOWN_KEY, "unknown"),
        ):
            with self.subTest(key=key):
                outcome, args = self.run_gate(
                    key,
                    acceptance_fixture=ScriptedAcceptanceFixture.create(key),
                )
                self.assertEqual(outcome.status, "fallback_delivery")
                self.assertEqual(outcome.g2_action, "frozen_g0_fallback")
                self.assertEqual(outcome.delivery_source, "g0_frozen_fallback")
                self.assertEqual(outcome.gate_status, "blocked")
                self.assertGreater(outcome.acceptance_counts[expected_field], 0)
                self.assertEqual(
                    outcome.acceptance_counts["not_supported"],
                    3,
                )
                self.assertEqual(
                    outcome.final_package_schema_version,
                    RESULT_PACKAGE_V2_SCHEMA_VERSION,
                )
                self.assertEqual(outcome.repair_limit, 1)
                self.assertEqual(outcome.repair_attempted, 0)
                self.assertEqual(
                    outcome.repair_status,
                    "not_attempted_tier_a_07a",
                )
                self.assert_fallback_exact(args["fallback_output_dir"])
                self.assertFalse(args["model_package_output_dir"].exists())
                self.assert_live_round_trip(outcome, args)

    def test_model_route_failures_bypass_gate_and_fallback_without_repair(self) -> None:
        semantic_fixture = ScriptedLocalFixture.create(
            SCRIPTED_FIXTURE_SEMANTIC_INVALID_KEY,
            ProviderRawResponse.from_bytes(SEMANTIC_INVALID_BYTES),
        )
        assembly_fixture = ScriptedLocalFixture.create(
            SCRIPTED_FIXTURE_ASSEMBLY_INVALID_KEY,
            ProviderRawResponse.from_bytes(assembly_invalid_bytes()),
        )
        attempts = (
            (
                "semantic",
                self.run_model_route(
                    execution_branch="scripted_local_fixture",
                    scripted_local_fixture=semantic_fixture,
                ),
                "scripted_local_fixture",
                semantic_fixture,
            ),
            (
                "assembly",
                self.run_model_route(
                    execution_branch="scripted_local_fixture",
                    scripted_local_fixture=assembly_fixture,
                ),
                "scripted_local_fixture",
                assembly_fixture,
            ),
            (
                "tier-b",
                self.run_model_route(
                    execution_branch="tier_b_authorization_probe",
                ),
                "tier_b_authorization_probe",
                None,
            ),
        )
        for suffix, model_outcome, branch, fixture in attempts:
            with self.subTest(suffix=suffix):
                outcome, args = self.run_gate(
                    suffix,
                    model_outcome=model_outcome,
                    execution_branch=branch,
                    scripted_local_fixture=fixture,
                    acceptance_fixture=None,
                )
                self.assertEqual(outcome.status, "fallback_delivery")
                self.assertEqual(
                    outcome.fallback_reason,
                    "model_route_not_assembled",
                )
                self.assertEqual(outcome.gate_status, "not_executed")
                self.assertEqual(outcome.repair_attempted, 0)
                self.assertFalse(args["render_output_dir"].exists())
                self.assertFalse(args["model_package_output_dir"].exists())
                self.assert_fallback_exact(args["fallback_output_dir"])
                self.assert_live_round_trip(outcome, args)


    def test_missing_and_cross_case_fallback_fail_delivery(self) -> None:
        missing, missing_args = self.run_gate(
            "missing-fallback",
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_PASS_KEY
            ),
            fallback_record=None,
        )
        self.assertEqual(missing.status, "failed_delivery")
        self.assertEqual(missing.failure.code, "frozen_g0_fallback_invalid")
        self.assertFalse(missing_args["render_output_dir"].exists())
        self.assertFalse(missing_args["fallback_output_dir"].exists())
        self.assert_live_round_trip(missing, missing_args)

        other_snapshot = self.work / "other-snapshot"
        other_record = freeze_g0_fallback_package(
            "other-case",
            self.package,
            other_snapshot,
        )
        cross, cross_args = self.run_gate(
            "cross-case",
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_PASS_KEY
            ),
            fallback_record=other_record,
            fallback_snapshot_dir=other_snapshot,
        )
        self.assertEqual(cross.status, "failed_delivery")
        self.assertEqual(cross.failure.code, "frozen_g0_fallback_invalid")
        self.assert_live_round_trip(cross, cross_args)

    def test_tampered_snapshot_and_occupied_destination_fail_delivery(self) -> None:
        target = self.snapshot / "result_package" / "page" / "index.html"
        target.write_bytes(target.read_bytes() + b"tamper")
        tampered, tampered_args = self.run_gate(
            "tampered",
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_PASS_KEY
            ),
        )
        self.assertEqual(tampered.status, "failed_delivery")
        self.assertEqual(
            tampered.failure.code,
            "frozen_g0_fallback_invalid",
        )
        self.assertFalse(tampered_args["render_output_dir"].exists())
        self.assert_live_round_trip(tampered, tampered_args)

    def test_destination_overlap_or_occupancy_fails_before_write(self) -> None:
        occupied = self.work / "occupied"
        occupied.mkdir()
        (occupied / "user.txt").write_text("keep", encoding="utf-8")
        outcome, args = self.run_gate(
            "occupied",
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_PASS_KEY
            ),
            fallback_output_dir=occupied,
        )
        self.assertEqual(outcome.status, "failed_delivery")
        self.assertEqual(outcome.failure.code, "output_destination_invalid")
        self.assertEqual((occupied / "user.txt").read_text(encoding="utf-8"), "keep")
        self.assertFalse(args["render_output_dir"].exists())
        self.assertFalse(args["model_package_output_dir"].exists())
        self.assert_live_round_trip(outcome, args)

        overlap, overlap_args = self.run_gate(
            "overlap",
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_PASS_KEY
            ),
            render_output_dir=self.work / "shared",
            model_package_output_dir=self.work / "shared" / "package",
        )
        self.assertEqual(overlap.status, "failed_delivery")
        self.assertEqual(overlap.failure.code, "output_destination_invalid")
        self.assert_live_round_trip(overlap, overlap_args)

    def test_cross_bound_model_route_or_outcome_tamper_is_rejected(self) -> None:
        outcome, args = self.run_gate(
            "live-tamper",
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_PASS_KEY
            ),
        )
        forged_payload = json.loads(outcome.canonical_bytes().decode("utf-8"))
        forged_payload["artifacts"]["page_spec_sha256"] = "0" * 64
        with self.assertRaises(TierA07aGateDeliveryOutcomeError):
            TierA07aGateDeliveryOutcome.from_dict(resign_07a(forged_payload))

        wrong_route = self.run_model_route(
            execution_branch="tier_b_authorization_probe",
        )
        with self.assertRaises(TierA07aGateDeliveryOutcomeError):
            outcome.validate_against(
                **{**args, "model_route_outcome": wrong_route}
            )

        package_file = args["model_package_output_dir"] / "page" / "index.html"
        package_file.write_bytes(package_file.read_bytes() + b"tamper")
        with self.assertRaises(TierA07aGateDeliveryOutcomeError):
            outcome.validate_against(**args)


    def test_strict_object_and_bytes_validation_and_no_payload_leakage(self) -> None:
        outcome, _ = self.run_gate(
            "serialization",
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_PASS_KEY
            ),
        )
        raw = outcome.canonical_bytes()
        text = raw.decode("utf-8")
        self.assertNotIn(str(self.work), text)
        self.assertNotIn(self.context.original_requirement, text)
        self.assertNotIn("package_dir", text)
        self.assertNotIn("raw_bytes", text)
        self.assertNotIn("retrieval_results", text)
        self.assertNotIn("reference_uris", text)
        self.assertNotIn("credential", text.lower())

        payload = json.loads(text)
        array_artifacts = deepcopy(payload)
        array_artifacts["artifacts"] = list(array_artifacts["artifacts"].items())
        tuple_steps = deepcopy(payload)
        tuple_steps["completed_steps"] = tuple(tuple_steps["completed_steps"])
        unknown = deepcopy(payload)
        unknown["unexpected"] = True
        path_case = deepcopy(payload)
        path_case["case_id"] = r"D:\unsafe\case"
        status_tamper = deepcopy(payload)
        status_tamper["status"] = "fallback_delivery"
        numeric_tamper = []
        for key, value in (
            ("repair_limit", True),
            ("repair_limit", 1.0),
            ("repair_attempted", False),
        ):
            forged = deepcopy(payload)
            forged[key] = value
            numeric_tamper.append(resign_07a(forged))
        declaration_bool = deepcopy(payload)
        declaration_bool["execution_declarations"]["network_used"] = 0
        numeric_tamper.append(resign_07a(declaration_bool))
        declaration_int = deepcopy(payload)
        declaration_int["execution_declarations"]["repair_limit"] = 1.0
        numeric_tamper.append(resign_07a(declaration_int))
        object_invalid = (
            array_artifacts,
            tuple_steps,
            unknown,
            resign_07a(path_case),
            resign_07a(status_tamper),
            *numeric_tamper,
        )
        for value in object_invalid:
            with self.subTest(kind=type(value).__name__), self.assertRaises(
                TierA07aGateDeliveryOutcomeError
            ):
                TierA07aGateDeliveryOutcome.from_dict(value)
        for value in (
            array_artifacts,
            unknown,
            path_case,
            status_tamper,
            *numeric_tamper,
        ):
            with self.subTest(kind="bytes", value=type(value).__name__), self.assertRaises(
                TierA07aGateDeliveryOutcomeError
            ):
                TierA07aGateDeliveryOutcome.from_bytes(
                    model_route_module._canonical_json_bytes(value)
                )

        duplicate = b'{"schema_version":"x","schema_version":"x"}'
        bom = b"\xef\xbb\xbf" + raw
        nonfinite = b'{"schema_version":NaN}'
        pretty = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        for value in (duplicate, bom, nonfinite, pretty):
            with self.subTest(prefix=value[:12]), self.assertRaises(
                TierA07aGateDeliveryOutcomeError
            ):
                TierA07aGateDeliveryOutcome.from_bytes(value)

    def test_module_name_rebinding_cannot_replace_fixed_authorities(self) -> None:
        class ForgedRenderer:
            def render(self, *args, **kwargs):
                raise AssertionError("forged renderer used")

        class ForgedPackager:
            def package(self, *args, **kwargs):
                raise AssertionError("forged packager used")

        with (
            patch.object(model_route_module, "DeterministicPageRenderer", ForgedRenderer),
            patch.object(model_route_module, "MinimalConsistencyChecker", object),
            patch.object(model_route_module, "BrowserExecutionReport", object),
            patch.object(model_route_module, "ResultPackage", object),
            patch.object(model_route_module, "DeterministicResultPackager", ForgedPackager),
            patch.object(
                model_route_module,
                "execute_acceptance_binding_plan",
                side_effect=AssertionError("forged executor used"),
            ),
            patch.object(
                model_route_module,
                "_FIXED_ACCEPTANCE_EXECUTION_AUTHORITY",
                side_effect=AssertionError("rebound helper used"),
            ),
            patch.object(
                model_route_module,
                "_FIXED_07A_PACKAGE_LIVE_BINDING",
                side_effect=AssertionError("rebound package binding used"),
            ),
        ):
            outcome, args = self.run_gate(
                "fixed-authority",
                acceptance_fixture=ScriptedAcceptanceFixture.create(
                    SCRIPTED_ACCEPTANCE_PASS_KEY
                ),
            )
            outcome.validate_against(**args)
        self.assertEqual(outcome.status, "first_pass_success")
        self.assert_live_round_trip(outcome, args)

        with (
            patch.object(
                model_route_module,
                "deliver_frozen_g0_fallback",
                side_effect=AssertionError("rebound fallback deliverer used"),
            ),
            patch.object(model_route_module, "FallbackDeliveryReport", object),
            patch.object(
                model_route_module,
                "_FIXED_07A_FALLBACK_REPORT_PROJECTION",
                side_effect=AssertionError("rebound fallback projection used"),
            ),
        ):
            fallback, fallback_args = self.run_gate(
                "fixed-fallback-authority",
                acceptance_fixture=ScriptedAcceptanceFixture.create(
                    SCRIPTED_ACCEPTANCE_FAIL_KEY
                ),
            )
            fallback.validate_against(**fallback_args)
        self.assertEqual(fallback.status, "fallback_delivery")
        self.assert_fallback_exact(fallback_args["fallback_output_dir"])
        self.assert_live_round_trip(fallback, fallback_args)

    def test_fixed_acceptance_seam_never_starts_real_browser_or_model_gate(self) -> None:
        with (
            patch.object(
                browser_executor_module,
                "LazyPlaywrightBrowserBackend",
                side_effect=AssertionError("real browser must not start"),
            ),
            patch.object(
                model_route_module,
                "invoke_local_qwen_provider",
                side_effect=AssertionError("model gate must not run"),
            ),
        ):
            outcome, _ = self.run_gate(
                "no-real-runtime",
                acceptance_fixture=ScriptedAcceptanceFixture.create(
                    SCRIPTED_ACCEPTANCE_PASS_KEY
                ),
            )
        self.assertEqual(outcome.status, "first_pass_success")
        declarations = outcome.to_dict()["execution_declarations"]
        self.assertFalse(declarations["real_browser_executed"])
        self.assertFalse(declarations["network_used"])
        self.assertFalse(declarations["service_started"])
        self.assertFalse(declarations["model_loaded"])
        self.assertFalse(declarations["provider_invoked"])

    def test_partial_render_failure_cleans_staging_and_falls_back(self) -> None:
        original = model_route_module.DeterministicPageRenderer.render

        def render_then_fail(renderer, page_spec, output_dir):
            original(renderer, page_spec, output_dir)
            raise RuntimeError("controlled renderer failure")

        with patch.object(
            model_route_module.DeterministicPageRenderer,
            "render",
            new=render_then_fail,
        ):
            outcome, args = self.run_gate(
                "render-failure",
                acceptance_fixture=ScriptedAcceptanceFixture.create(
                    SCRIPTED_ACCEPTANCE_PASS_KEY
                ),
            )
        self.assertEqual(outcome.status, "fallback_delivery")
        self.assertEqual(outcome.fallback_reason, "render_failed")
        self.assertFalse(args["render_output_dir"].exists())
        self.assert_fallback_exact(args["fallback_output_dir"])
        staging = [
            path
            for path in self.work.iterdir()
            if "tier-a-07a-render-staging" in path.name
        ]
        self.assertEqual(staging, [])

    def test_failure_replay_requires_the_captured_stage_to_fail(self) -> None:
        cases = (
            (
                "render",
                model_route_module.DeterministicPageRenderer,
                "render",
                "render_failed",
            ),
            (
                "consistency",
                model_route_module.MinimalConsistencyChecker,
                "check",
                "consistency_failed",
            ),
            (
                "requirement",
                model_route_module.RequirementView,
                "validate",
                "requirement_view_failed",
            ),
            (
                "plan",
                model_route_module.AcceptancePlan,
                "validate_against",
                "acceptance_plan_failed",
            ),
            (
                "binding",
                model_route_module.AcceptanceBindingPlan,
                "validate_against",
                "acceptance_binding_failed",
            ),
            (
                "execution",
                model_route_module.BrowserExecutionReport,
                "validate_against",
                "acceptance_execution_failed",
            ),
            (
                "package",
                model_route_module.DeterministicResultPackager,
                "package",
                "model_package_failed",
            ),
        )
        for suffix, owner, method, reason in cases:
            with self.subTest(stage=suffix):
                with patch.object(
                    owner,
                    method,
                    side_effect=RuntimeError("controlled stage failure"),
                ):
                    outcome, args = self.run_gate(
                        "replay-" + suffix,
                        acceptance_fixture=ScriptedAcceptanceFixture.create(
                            SCRIPTED_ACCEPTANCE_PASS_KEY
                        ),
                    )
                    self.assertEqual(outcome.status, "fallback_delivery")
                    self.assertEqual(outcome.fallback_reason, reason)
                    outcome.validate_against(**args)
                with self.assertRaises(TierA07aGateDeliveryOutcomeError):
                    outcome.validate_against(**args)

    def test_render_failure_replay_rejects_post_return_output_errors(self) -> None:
        success, _ = self.run_gate(
            "render-replay-source-success",
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_PASS_KEY
            ),
        )
        with patch.object(
            model_route_module.DeterministicPageRenderer,
            "render",
            side_effect=RuntimeError("controlled renderer authority failure"),
        ):
            template, args = self.run_gate(
                "render-replay-template",
                acceptance_fixture=ScriptedAcceptanceFixture.create(
                    SCRIPTED_ACCEPTANCE_PASS_KEY
                ),
            )
            template.validate_against(**args)
        forged = self.failure_projection_from_success(success, template)
        with self.assertRaises(TierA07aGateDeliveryOutcomeError):
            forged.validate_against(**args)

        original_render = model_route_module.DeterministicPageRenderer.render

        def render_then_remove_app(renderer, page_spec, output_dir):
            result = original_render(renderer, page_spec, output_dir)
            (Path(output_dir) / "app.js").unlink()
            return result

        with patch.object(
            model_route_module.DeterministicPageRenderer,
            "render",
            new=render_then_remove_app,
        ), self.assertRaises(TierA07aGateDeliveryOutcomeError):
            forged.validate_against(**args)

        original_read_bytes = Path.read_bytes

        def fail_render_projection_read(path):
            if (
                path.name == "app.js"
                and "tier-a-07a-render-failure-replay" in path.parent.name
            ):
                raise OSError("controlled render projection read failure")
            return original_read_bytes(path)

        with patch.object(
            Path,
            "read_bytes",
            new=fail_render_projection_read,
        ), self.assertRaises(TierA07aGateDeliveryOutcomeError):
            forged.validate_against(**args)

    def test_package_failure_replay_rejects_post_return_output_errors(self) -> None:
        success, _ = self.run_gate(
            "package-replay-source-success",
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_PASS_KEY
            ),
        )
        with patch.object(
            model_route_module.DeterministicResultPackager,
            "package",
            side_effect=RuntimeError("controlled packager authority failure"),
        ):
            template, args = self.run_gate(
                "package-replay-template",
                acceptance_fixture=ScriptedAcceptanceFixture.create(
                    SCRIPTED_ACCEPTANCE_PASS_KEY
                ),
            )
            template.validate_against(**args)
        forged = self.failure_projection_from_success(success, template)
        with self.assertRaises(TierA07aGateDeliveryOutcomeError):
            forged.validate_against(**args)

        original_package = model_route_module.DeterministicResultPackager.package

        def package_then_remove_manifest(packager, *call_args, **call_kwargs):
            result = original_package(packager, *call_args, **call_kwargs)
            (Path(result.package_dir) / "package_manifest.json").unlink()
            return result

        with patch.object(
            model_route_module.DeterministicResultPackager,
            "package",
            new=package_then_remove_manifest,
        ), self.assertRaises(TierA07aGateDeliveryOutcomeError):
            forged.validate_against(**args)

        original_read_bytes = Path.read_bytes

        def fail_package_projection_read(path):
            parent_name = path.parent.name
            if (
                path.name == "package_manifest.json"
                and "tier-a-07a-package-failure-replay" in parent_name
                and ".staging-" not in parent_name
            ):
                raise OSError("controlled package projection read failure")
            return original_read_bytes(path)

        with patch.object(
            Path,
            "read_bytes",
            new=fail_package_projection_read,
        ), self.assertRaises(TierA07aGateDeliveryOutcomeError):
            forged.validate_against(**args)

    def test_fallback_failure_replay_rejects_post_return_output_errors(self) -> None:
        success, _ = self.run_gate(
            "fallback-replay-source-success",
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_PASS_KEY
            ),
        )

        def authority_failure(report, *call_args, **call_kwargs):
            raise RuntimeError("controlled fallback authority failure")

        with patch.object(
            model_route_module.FallbackDeliveryReport,
            "validate_against",
            new=authority_failure,
        ):
            template, args = self.run_gate(
                "fallback-replay-template",
                acceptance_fixture=ScriptedAcceptanceFixture.create(
                    SCRIPTED_ACCEPTANCE_FAIL_KEY
                ),
            )
            template.validate_against(**args)
        forged = self.failure_projection_from_success(success, template)
        with self.assertRaises(TierA07aGateDeliveryOutcomeError):
            forged.validate_against(**args)

        original_validate = model_route_module.FallbackDeliveryReport.validate_against

        def validate_then_remove_app(
            report,
            snapshot_dir,
            delivered_dir,
            expected_record,
        ):
            result = original_validate(
                report,
                snapshot_dir,
                delivered_dir,
                expected_record,
            )
            delivered = Path(delivered_dir)
            replay_name = delivered.parent.name
            if (
                "tier-a-07a-fallback-failure-replay" in replay_name
                and ".staging-" not in replay_name
            ):
                (delivered / "page" / "app.js").unlink()
            return result

        with patch.object(
            model_route_module.FallbackDeliveryReport,
            "validate_against",
            new=validate_then_remove_app,
        ), self.assertRaises(TierA07aGateDeliveryOutcomeError):
            forged.validate_against(**args)

        original_read_bytes = Path.read_bytes
        authority_returned = False

        def mark_authority_returned(
            report,
            snapshot_dir,
            delivered_dir,
            expected_record,
        ):
            nonlocal authority_returned
            result = original_validate(
                report,
                snapshot_dir,
                delivered_dir,
                expected_record,
            )
            replay_name = Path(delivered_dir).parent.name
            if (
                "tier-a-07a-fallback-failure-replay" in replay_name
                and ".staging-" not in replay_name
            ):
                authority_returned = True
            return result

        def fail_post_return_fallback_read(path):
            if (
                authority_returned
                and path.name == "app.js"
                and any(
                    "tier-a-07a-fallback-failure-replay" in part
                    for part in path.parts
                )
                and all(".staging-" not in part for part in path.parts)
            ):
                raise OSError("controlled fallback validation read failure")
            return original_read_bytes(path)

        with patch.object(
            model_route_module.FallbackDeliveryReport,
            "validate_against",
            new=mark_authority_returned,
        ), patch.object(
            Path,
            "read_bytes",
            new=fail_post_return_fallback_read,
        ), self.assertRaises(TierA07aGateDeliveryOutcomeError):
            forged.validate_against(**args)

    def test_fallback_failure_replay_and_empty_destination_rollback(self) -> None:
        fallback_output = self.work / "fallback-preexisting-empty"
        fallback_output.mkdir()

        def fail_delivery(report, *args, **kwargs):
            raise RuntimeError("controlled fallback delivery failure")

        with patch.object(
            model_route_module.FallbackDeliveryReport,
            "validate_against",
            new=fail_delivery,
        ):
            outcome, args = self.run_gate(
                "fallback-failure-replay",
                fallback_output_dir=fallback_output,
                acceptance_fixture=ScriptedAcceptanceFixture.create(
                    SCRIPTED_ACCEPTANCE_FAIL_KEY
                ),
            )
            self.assertEqual(outcome.status, "failed_delivery")
            self.assertEqual(outcome.failure.code, "fallback_delivery_failed")
            outcome.validate_against(**args)
        self.assertTrue(fallback_output.is_dir())
        self.assertEqual(tuple(fallback_output.iterdir()), ())
        with self.assertRaises(TierA07aGateDeliveryOutcomeError):
            outcome.validate_against(**args)

    def test_preexisting_empty_fallback_directory_is_preserved_on_success(self) -> None:
        fallback_output = self.work / "fallback-empty-success"
        fallback_output.mkdir()
        before = fallback_output.stat().st_ino
        outcome, args = self.run_gate(
            "fallback-empty-success",
            fallback_output_dir=fallback_output,
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_FAIL_KEY
            ),
        )
        self.assertEqual(outcome.status, "fallback_delivery")
        self.assertTrue(fallback_output.is_dir())
        self.assertEqual(fallback_output.stat().st_ino, before)
        self.assert_fallback_exact(fallback_output)
        self.assert_live_round_trip(outcome, args)

    def test_empty_fallback_directory_survives_final_validation_failure(self) -> None:
        fallback_output = self.work / "fallback-empty-final-failure"
        fallback_output.mkdir()
        original = model_route_module.FallbackDeliveryReport.validate_against

        def fail_only_final(report, snapshot_dir, delivered_dir, expected_record):
            if Path(delivered_dir).parent == fallback_output:
                raise RuntimeError("controlled final validation failure")
            return original(report, snapshot_dir, delivered_dir, expected_record)

        with patch.object(
            model_route_module.FallbackDeliveryReport,
            "validate_against",
            new=fail_only_final,
        ):
            outcome, args = self.run_gate(
                "fallback-empty-final-failure",
                fallback_output_dir=fallback_output,
                acceptance_fixture=ScriptedAcceptanceFixture.create(
                    SCRIPTED_ACCEPTANCE_FAIL_KEY
                ),
            )
        self.assertEqual(outcome.status, "failed_delivery")
        self.assertEqual(outcome.failure.code, "fallback_delivery_failed")
        self.assertTrue(fallback_output.is_dir())
        self.assertEqual(tuple(fallback_output.iterdir()), ())
        with self.assertRaises(TierA07aGateDeliveryOutcomeError):
            outcome.validate_against(**args)

    def test_live_package_binding_rejects_self_consistent_forged_v1(self) -> None:
        outcome, args = self.run_gate(
            "package-live-binding",
            acceptance_fixture=ScriptedAcceptanceFixture.create(
                SCRIPTED_ACCEPTANCE_PASS_KEY
            ),
        )
        assembled = model_route_module._FIXED_CANONICAL_ASSEMBLY_AUTHORITY(
            self.model_fixture.raw_response,
            self.context,
            self.guidance,
        )
        forged_page = replace(assembled.page_spec, title="Forged live package")
        forged_render_dir = self.work / "forged-render"
        forged_render = model_route_module.DeterministicPageRenderer().render(
            forged_page,
            forged_render_dir,
        )
        forged_consistency = model_route_module.MinimalConsistencyChecker().check(
            forged_page,
            forged_render,
        )
        forged_package_dir = self.work / "forged-package"
        forged_package = model_route_module.DeterministicResultPackager().package(
            self.context,
            forged_page,
            forged_render,
            forged_consistency,
            forged_package_dir,
        )
        shutil.rmtree(args["model_package_output_dir"])
        shutil.copytree(forged_package_dir, args["model_package_output_dir"])
        forged_live_package = replace(
            forged_package,
            package_dir=args["model_package_output_dir"],
        )
        forged_projection = model_route_module._FIXED_07A_PACKAGE_PROJECTION(
            forged_live_package,
            args["model_package_output_dir"],
        )
        payload = json.loads(outcome.canonical_bytes().decode("utf-8"))
        payload["artifacts"].update(forged_projection)
        payload["final_package_schema_version"] = forged_projection[
            "model_package_schema_version"
        ]
        payload["final_package_id"] = forged_projection["model_package_id"]
        payload["final_package_manifest_sha256"] = forged_projection[
            "model_package_manifest_sha256"
        ]
        payload["final_package_tree_sha256"] = forged_projection[
            "model_package_tree_sha256"
        ]
        payload["final_package_file_count"] = forged_projection[
            "model_package_file_count"
        ]
        forged_outcome = TierA07aGateDeliveryOutcome.from_dict(
            resign_07a(payload)
        )
        with self.assertRaises(TierA07aGateDeliveryOutcomeError):
            forged_outcome.validate_against(**args)

    def test_original_path_junction_is_rejected_before_resolve(self) -> None:
        if sys.platform != "win32":
            self.skipTest("Windows junction test requires Windows")
        target = self.work / "junction-target"
        target.mkdir()
        junction = self.work / "junction-output"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest("mklink /J is unavailable in this environment")
        try:
            outcome, args = self.run_gate(
                "junction",
                render_output_dir=junction / "render",
                acceptance_fixture=ScriptedAcceptanceFixture.create(
                    SCRIPTED_ACCEPTANCE_PASS_KEY
                ),
            )
            self.assertEqual(outcome.status, "failed_delivery")
            self.assertEqual(outcome.failure.code, "output_destination_invalid")
            self.assertFalse((target / "render").exists())
            self.assert_live_round_trip(outcome, args)
        finally:
            if junction.exists() or junction.is_symlink():
                junction.rmdir()

    def test_source_boundary_has_no_freeze_builder_network_or_reverse_import(self) -> None:
        source_path = ROOT / "src" / "req2web_orchestration" / "model_route.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertNotIn("freeze_g0_fallback_package", source)
        self.assertNotIn("RetrievalGuidedPageSpecBuilder", source)
        self.assertNotIn("PageSpecBuilder", source)
        self.assertNotIn("LazyPlaywrightBrowserBackend", source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        self.assertFalse(
            any(
                name.startswith(
                    ("socket", "requests", "http", "urllib", "subprocess", "req2web_runtime")
                )
                for name in imports
            )
        )
        for provider_file in (ROOT / "src" / "req2web_provider").glob("*.py"):
            provider_tree = ast.parse(provider_file.read_text(encoding="utf-8"))
            provider_imports = []
            for node in ast.walk(provider_tree):
                if isinstance(node, ast.Import):
                    provider_imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    provider_imports.append(node.module or "")
            self.assertFalse(
                any(
                    name.startswith("req2web_orchestration")
                    for name in provider_imports
                )
            )


if __name__ == "__main__":
    unittest.main()
