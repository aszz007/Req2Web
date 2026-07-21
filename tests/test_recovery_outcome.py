from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path
import shutil
import sys
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import req2web_faults  # noqa: E402
import req2web_faults.recovery_outcome as outcome_module  # noqa: E402
from req2web_faults import (  # noqa: E402
    DeterministicRecoveryOutcomeError,
    FAILED_DELIVERY,
    FALLBACK_DELIVERY,
    FIRST_PASS_SUCCESS,
    RECOVERED_SUCCESS,
    RepairExecutionError,
    authorize_fault_detection_report,
    detect_blinded_fault_bundle,
    execute_deterministic_development_recovery,
    freeze_g0_fallback_package,
)
from req2web_faults.fallback_delivery import FallbackDeliveryError, _canonical_sha256  # noqa: E402
import test_fault_detector as detector_fixtures  # noqa: E402


_DEFAULT = object()

def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }

def reseal_bundle(bundle_dir: Path) -> None:
    from req2web_faults.bundle import _make_bundle_record
    manifest_path = bundle_dir / "fault_case_bundle_manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = {
        path.relative_to(bundle_dir).as_posix(): path.read_bytes()
        for path in sorted((bundle_dir / "artifact").rglob("*")) if path.is_file()
    }
    record = _make_bundle_record(previous["case_id"], previous["page_id"], files)
    manifest_path.write_bytes(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


class RecoveryOutcomeTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        detector_fixtures.FaultDetectorTest.setUpClass()
        cls.fixture = detector_fixtures.FaultDetectorTest
        cls.root = ROOT / "tests" / ".tmp_recovery_outcome"
        shutil.rmtree(cls.root, ignore_errors=True)
        cls.root.mkdir(parents=True)
        cls.snapshot = cls.root / "snapshot"
        cls.fallback_record = freeze_g0_fallback_package(
            cls.fixture.source.case_id, cls.fixture.package, cls.snapshot,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)
        detector_fixtures.FaultDetectorTest.tearDownClass()

    def output(self, name: str) -> Path:
        path = self.root / self._testMethodName / name
        shutil.rmtree(path, ignore_errors=True)
        return path

    def route_inputs(self, name: str):
        bundle = self.fixture.clean_dir if name == "clean" else self.fixture.fault_dirs[name]
        report = detect_blinded_fault_bundle(bundle, self.fixture.parity)
        return bundle, report, authorize_fault_detection_report(report)

    def invoke(self, name: str, output: Path, *, snapshot=_DEFAULT, record=_DEFAULT):
        bundle, report, authorization = self.route_inputs(name)
        return execute_deterministic_development_recovery(
            bundle, self.fixture.parity, report, authorization,
            self.snapshot if snapshot is _DEFAULT else snapshot,
            self.fallback_record if record is _DEFAULT else record,
            output,
        )

    def test_route_matrix_uses_only_the_frozen_permitted_branch(self) -> None:
        clean_output = self.output("clean")
        with patch.object(outcome_module, "execute_authorized_deterministic_repair") as repair, patch.object(
            outcome_module, "deliver_frozen_g0_fallback"
        ) as fallback:
            clean = self.invoke("clean", clean_output)
        self.assertEqual(clean.status, FIRST_PASS_SUCCESS)
        self.assertEqual(clean.source_package_validation_status, "valid")
        self.assertFalse(repair.called)
        self.assertFalse(fallback.called)
        self.assertEqual(set(tree_bytes(clean_output)), {
            "deterministic_recovery_outcome.json", "deterministic_recovery_outcome_manifest.json",
        })
        clean.validate_against(
            self.fixture.clean_dir, self.fixture.parity,
            detect_blinded_fault_bundle(self.fixture.clean_dir, self.fixture.parity),
            authorize_fault_detection_report(detect_blinded_fault_bundle(self.fixture.clean_dir, self.fixture.parity)),
            self.snapshot, self.fallback_record, clean_output,
        )

        for name in ("page_spec", "inspector"):
            output = self.output(name)
            with self.subTest(name=name), patch.object(
                outcome_module, "execute_authorized_deterministic_repair", side_effect=AssertionError("repair must not run")
            ):
                outcome = self.invoke(name, output)
            self.assertEqual(outcome.status, FALLBACK_DELIVERY)
            self.assertFalse((output / "repair").exists())
            self.assertTrue((output / "fallback" / "result_package").is_dir())

        repaired_output = self.output("repaired")
        with patch.object(
            outcome_module, "deliver_frozen_g0_fallback", side_effect=AssertionError("fallback must not run")
        ):
            repaired = self.invoke("render", repaired_output)
        self.assertEqual(repaired.status, RECOVERED_SUCCESS)
        self.assertEqual(repaired.source_package_validation_status, "valid")
        self.assertTrue((repaired_output / "repair" / "repaired_bundle").is_dir())
        self.assertFalse((repaired_output / "fallback").exists())

    def test_forced_repair_failure_attempts_fallback_once(self) -> None:
        output = self.output("repair-failure")
        original = outcome_module.deliver_frozen_g0_fallback
        with patch.object(
            outcome_module, "execute_authorized_deterministic_repair", side_effect=RepairExecutionError("forced")
        ), patch.object(outcome_module, "deliver_frozen_g0_fallback", wraps=original) as delivery:
            outcome = self.invoke("package_path", output)
        self.assertEqual(outcome.status, FALLBACK_DELIVERY)
        self.assertTrue(outcome.repair_attempted)
        self.assertFalse(outcome.repair_succeeded)
        self.assertEqual(outcome.repair_failure_reason_code, "repair_execution_failed")
        self.assertEqual(delivery.call_count, 1)
        self.assertFalse((output / "repair").exists())

    def test_missing_mismatched_tampered_and_delivery_failure_end_failed_delivery(self) -> None:
        missing = self.invoke("page_spec", self.output("missing"), snapshot=None)
        self.assertEqual(missing.status, FAILED_DELIVERY)
        self.assertEqual(missing.failure_reason_code, "fallback_snapshot_missing")
        self.assertFalse((self.output("missing") / "fallback").exists())

        seed = replace(self.fallback_record, case_id="other", record_id="", record_sha256="0" * 64)
        digest = _canonical_sha256(seed.to_payload())
        mismatch_record = replace(seed, record_id="frozen-g0-fallback-" + digest[:20], record_sha256=digest)
        mismatch_output = self.output("mismatch")
        with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "case_id does not match"):
            self.invoke("inspector", mismatch_output, record=mismatch_record)
        self.assertFalse(mismatch_output.exists())
        self.assertEqual([], list(mismatch_output.parent.glob("." + mismatch_output.name + ".staging-*")))

        tampered_snapshot = self.root / self._testMethodName / "tampered-snapshot"
        shutil.copytree(self.snapshot, tampered_snapshot)
        (tampered_snapshot / "extra.txt").write_text("x", encoding="utf-8")
        tampered = self.invoke("page_spec", self.output("tampered"), snapshot=tampered_snapshot)
        self.assertEqual(tampered.status, FAILED_DELIVERY)
        self.assertEqual(tampered.failure_reason_code, "fallback_snapshot_invalid")

        with patch.object(
            outcome_module, "deliver_frozen_g0_fallback", side_effect=FallbackDeliveryError("forced")
        ):
            failed = self.invoke("page_spec", self.output("delivery-failed"))
        self.assertEqual(failed.status, FAILED_DELIVERY)
        self.assertEqual(failed.failure_reason_code, "fallback_delivery_failed")
        self.assertFalse((self.output("delivery-failed") / "fallback").exists())

    def test_wrong_report_authorization_and_path_overlap_fail_before_output(self) -> None:
        bundle, report, authorization = self.route_inputs("render")
        stale_report = detect_blinded_fault_bundle(self.fixture.clean_dir, self.fixture.parity)
        output = self.output("stale-report")
        with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "exactly match"):
            execute_deterministic_development_recovery(
                bundle, self.fixture.parity, stale_report, authorization,
                self.snapshot, self.fallback_record, output,
            )
        self.assertFalse(output.exists())
        with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "authorization"):
            execute_deterministic_development_recovery(
                bundle, self.fixture.parity, report,
                authorize_fault_detection_report(detect_blinded_fault_bundle(self.fixture.clean_dir, self.fixture.parity)),
                self.snapshot, self.fallback_record, self.output("wrong-auth"),
            )
        with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "disjoint"):
            execute_deterministic_development_recovery(
                bundle, self.fixture.parity, report, authorization,
                self.snapshot, self.fallback_record, bundle / "child",
            )

    def test_wrapper_tamper_outer_inventory_and_post_commit_cleanup_fail_closed(self) -> None:
        output = self.output("valid")
        outcome = self.invoke("render", output)
        payload = output / "deterministic_recovery_outcome.json"
        payload.write_bytes(payload.read_bytes() + b"\n")
        bundle, report, authorization = self.route_inputs("render")
        with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "canonical"):
            outcome.validate_against(bundle, self.fixture.parity, report, authorization, self.snapshot, self.fallback_record, output)

        output = self.output("post-commit")
        original = outcome_module.DeterministicRecoveryOutcome.validate_against
        calls = {"count": 0}
        def fail_after_commit(self, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise DeterministicRecoveryOutcomeError("post commit")
            return original(self, *args, **kwargs)
        with patch.object(outcome_module.DeterministicRecoveryOutcome, "validate_against", new=fail_after_commit):
            with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "post commit"):
                self.invoke("page_spec", output)
        self.assertFalse(output.exists())
        self.assertEqual([], list(output.parent.glob("." + output.name + ".staging-*")))

    def test_public_surface_and_runtime_imports_are_isolated(self) -> None:
        for name in (
            "DeterministicRecoveryOutcome", "RecoveryOutcomeFile",
            "execute_deterministic_development_recovery", "FIRST_PASS_SUCCESS",
        ):
            self.assertTrue(hasattr(req2web_faults, name))
        source = (ROOT / "src" / "req2web_faults" / "recovery_outcome.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertNotIn("req2web_faults.evaluator_gold", source)
        self.assertNotIn("req2web_faults.injector_audit", source)
        self.assertNotIn("req2web_faults.mutation", source)
        self.assertNotIn("traceback", source)


    def test_unknown_and_ambiguous_routes_do_not_invoke_repair(self) -> None:
        unknown_bundle = self.root / self._testMethodName / "unknown-bundle"
        shutil.copytree(self.fixture.clean_dir, unknown_bundle)
        plan_path = unknown_bundle / "artifact" / "acceptance" / "acceptance_plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["unexpected_field"] = "outside classifier"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        reseal_bundle(unknown_bundle)

        ambiguous_bundle = self.root / self._testMethodName / "ambiguous-bundle"
        shutil.copytree(self.fixture.fault_dirs["page_spec"], ambiguous_bundle)
        shutil.copyfile(
            self.fixture.fault_dirs["package_sha256"] / "artifact" / "result_package" / "package_manifest.json",
            ambiguous_bundle / "artifact" / "result_package" / "package_manifest.json",
        )
        reseal_bundle(ambiguous_bundle)
        for name, bundle in (("unknown", unknown_bundle), ("ambiguous", ambiguous_bundle)):
            report = detect_blinded_fault_bundle(bundle, self.fixture.parity)
            authorization = authorize_fault_detection_report(report)
            output = self.output(name + "-out")
            with self.subTest(name=name), patch.object(
                outcome_module, "execute_authorized_deterministic_repair", side_effect=AssertionError("repair must not run")
            ):
                outcome = execute_deterministic_development_recovery(
                    bundle, self.fixture.parity, report, authorization,
                    self.snapshot, self.fallback_record, output,
                )
            self.assertEqual(outcome.status, FALLBACK_DELIVERY)
            self.assertFalse((output / "repair").exists())

    def test_forged_parity_route_state_and_outer_tree_fail_closed(self) -> None:
        from req2web_faults.bundle import canonical_sha256
        bundle, report, authorization = self.route_inputs("render")
        paths = self.fixture.parity.paths[:-1]
        wrong_parity = replace(
            self.fixture.parity,
            paths=paths,
            path_count=len(paths),
            path_set_sha256=canonical_sha256({"paths": list(paths), "slots": list(self.fixture.parity.slots)}),
        )
        with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "source bundle"):
            execute_deterministic_development_recovery(
                bundle, wrong_parity, report, authorization,
                self.snapshot, self.fallback_record, self.output("wrong-parity"),
            )

        output = self.output("forged-route")
        outcome = self.invoke("render", output)
        forged_seed = replace(outcome, status=FALLBACK_DELIVERY, outcome_id="", outcome_sha256="0" * 64)
        forged = replace(
            forged_seed,
            outcome_id="deterministic-recovery-outcome-" + outcome_module._digest(forged_seed.to_payload())[:20],
            outcome_sha256=outcome_module._digest(forged_seed.to_payload()),
        )
        with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "route state"):
            forged.validate()

        (output / "extra.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "inventory"):
            outcome.validate_against(bundle, self.fixture.parity, report, authorization, self.snapshot, self.fallback_record, output)

    def test_fixed_outcome_text_and_symlink_guard_exclude_runtime_details(self) -> None:
        output = self.output("failure")
        outcome = self.invoke("page_spec", output, record=None)
        self.assertEqual(outcome.status, FAILED_DELIVERY)
        serialized = json.dumps(outcome.to_dict(), ensure_ascii=False, sort_keys=True)
        for forbidden in ("traceback", str(self.root), "gold", "injector", "mutation", "H1", "Qwen", "Provider"):
            self.assertNotIn(forbidden, serialized)
        bundle, report, authorization = self.route_inputs("page_spec")
        guarded = self.output("symlink")
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "symlink"):
                execute_deterministic_development_recovery(
                    bundle, self.fixture.parity, report, authorization,
                    self.snapshot, self.fallback_record, guarded,
                )
        self.assertFalse(guarded.exists())

    def test_provided_fallback_record_is_prevalidated_for_clean_and_recovered_routes(self) -> None:
        forged = replace(self.fallback_record, record_sha256="0" * 64)
        clean_output = self.output("clean-forged-record")
        bundle, report, authorization = self.route_inputs("clean")
        with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "provided fallback_record is invalid"):
            execute_deterministic_development_recovery(
                bundle, self.fixture.parity, report, authorization,
                None, forged, clean_output,
            )
        self.assertFalse(clean_output.exists())
        self.assertEqual([], list(clean_output.parent.glob("." + clean_output.name + ".staging-*")))

        seed = replace(self.fallback_record, case_id="mismatched-case", record_id="", record_sha256="0" * 64)
        digest = _canonical_sha256(seed.to_payload())
        mismatched = replace(seed, record_id="frozen-g0-fallback-" + digest[:20], record_sha256=digest)
        recovered_output = self.output("recovered-mismatched-record")
        bundle, report, authorization = self.route_inputs("render")
        with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "case_id does not match"):
            execute_deterministic_development_recovery(
                bundle, self.fixture.parity, report, authorization,
                None, mismatched, recovered_output,
            )
        self.assertFalse(recovered_output.exists())
        self.assertEqual([], list(recovered_output.parent.glob("." + recovered_output.name + ".staging-*")))

        clean = self.invoke("clean", self.output("clean-valid-without-snapshot"), snapshot=None)
        recovered = self.invoke("render", self.output("recovered-valid-without-snapshot"), snapshot=None)
        self.assertEqual(clean.status, FIRST_PASS_SUCCESS)
        self.assertEqual(recovered.status, RECOVERED_SUCCESS)

    def test_recovered_execution_manifest_is_canonical_and_binds_exact_report_bytes(self) -> None:
        output = self.output("recovered-manifest")
        outcome = self.invoke("render", output)
        manifest_path = output / "repair" / "repair_execution_report" / "repair_execution_report_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["sha256"] = "0" * 64
        manifest_path.write_bytes(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        report_path = output / "repair" / "repair_execution_report" / "repair_execution_report.json"
        with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "repair execution report manifest"):
            outcome_module._load_execution(report_path)
        bundle, report, authorization = self.route_inputs("render")
        with self.assertRaisesRegex(DeterministicRecoveryOutcomeError, "repair execution report manifest"):
            outcome.validate_against(bundle, self.fixture.parity, report, authorization, self.snapshot, self.fallback_record, output)

    def test_detector_classified_invalid_source_package_still_routes_to_frozen_fallback(self) -> None:
        bundles: dict[str, Path] = {}
        for name in ("unsupported-schema", "invalid-json"):
            bundle = self.root / self._testMethodName / name
            shutil.copytree(self.fixture.clean_dir, bundle)
            manifest_path = bundle / "artifact" / "result_package" / "package_manifest.json"
            if name == "unsupported-schema":
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["schema_version"] = "req2web.unsupported.package.v0"
                manifest_path.write_bytes(
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                )
            else:
                manifest_path.write_bytes(b"{invalid json")
            reseal_bundle(bundle)
            bundles[name] = bundle

        for name, bundle in bundles.items():
            with self.subTest(name=name):
                report = detect_blinded_fault_bundle(bundle, self.fixture.parity)
                authorization = authorize_fault_detection_report(report)
                self.assertIn(report.status, {"unclassified_failure", "ambiguous_multiple_faults"})
                self.assertEqual(authorization.status, "fallback_required")
                output = self.output(name + "-fallback")
                outcome = execute_deterministic_development_recovery(
                    bundle, self.fixture.parity, report, authorization,
                    self.snapshot, self.fallback_record, output,
                )
                self.assertEqual(outcome.status, FALLBACK_DELIVERY)
                self.assertEqual(outcome.source_package_validation_status, "invalid_observed")
                self.assertIsNone(outcome.source_package_id)
                self.assertIsNone(outcome.source_package_schema_version)
                self.assertEqual(
                    tree_bytes(self.snapshot / "result_package"),
                    tree_bytes(output / "fallback" / "result_package"),
                )

        missing_bundle = bundles["invalid-json"]
        missing_report = detect_blinded_fault_bundle(missing_bundle, self.fixture.parity)
        missing_authorization = authorize_fault_detection_report(missing_report)
        missing_output = self.output("invalid-source-missing-snapshot")
        missing = execute_deterministic_development_recovery(
            missing_bundle, self.fixture.parity, missing_report, missing_authorization,
            None, self.fallback_record, missing_output,
        )
        self.assertEqual(missing.status, FAILED_DELIVERY)
        self.assertEqual(missing.failure_reason_code, "fallback_snapshot_missing")
        self.assertEqual(missing.source_package_validation_status, "invalid_observed")

        invalid_snapshot = self.root / self._testMethodName / "invalid-snapshot"
        shutil.copytree(self.snapshot, invalid_snapshot)
        (invalid_snapshot / "extra.txt").write_text("unexpected", encoding="utf-8")
        invalid_output = self.output("invalid-source-invalid-snapshot")
        invalid = execute_deterministic_development_recovery(
            missing_bundle, self.fixture.parity, missing_report, missing_authorization,
            invalid_snapshot, self.fallback_record, invalid_output,
        )
        self.assertEqual(invalid.status, FAILED_DELIVERY)
        self.assertEqual(invalid.failure_reason_code, "fallback_snapshot_invalid")
        self.assertEqual(invalid.source_package_validation_status, "invalid_observed")

if __name__ == "__main__":
    import unittest
    unittest.main()
