from __future__ import annotations

import ast
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import req2web_faults  # noqa: E402
import req2web_faults.repair_policy as repair_policy_module  # noqa: E402
from req2web_faults.bundle import _make_bundle_record  # noqa: E402
from req2web_faults.detector import (  # noqa: E402
    AMBIGUOUS_MULTIPLE_FAULTS,
    FAULT_DETECTED,
    FaultDetectionError,
    NO_FAULT_DETECTED,
    UNCLASSIFIED_FAILURE,
    detect_blinded_fault_bundle,
)
from req2web_faults.repair_policy import (  # noqa: E402
    DETERMINISTIC_REPAIR_AUTHORIZED,
    FALLBACK_REQUIRED,
    NO_ACTION,
    RepairPolicyError,
    authorize_fault_detection_report,
    write_repair_authorization,
)
import test_fault_detector as detector_fixtures  # noqa: E402


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class FaultRepairPolicyTest(TestCase):
    """Exercise authorization with reports from the existing M2 artifact chain."""

    @classmethod
    def setUpClass(cls) -> None:
        detector_fixtures.FaultDetectorTest.setUpClass()
        cls.fixture = detector_fixtures.FaultDetectorTest
        cls.root = ROOT / "tests" / ".tmp_fault_repair_policy"
        shutil.rmtree(cls.root, ignore_errors=True)
        cls.root.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)
        detector_fixtures.FaultDetectorTest.tearDownClass()

    def output(self, name: str) -> Path:
        path = self.root / self._testMethodName / name
        shutil.rmtree(path, ignore_errors=True)
        return path

    def report(self, name: str):
        if name == "clean":
            bundle_dir = self.fixture.clean_dir
        else:
            bundle_dir = self.fixture.fault_dirs[name]
        return detect_blinded_fault_bundle(bundle_dir, self.fixture.parity)

    def reseal_bundle(self, bundle_dir: Path) -> None:
        manifest_path = bundle_dir / "fault_case_bundle_manifest.json"
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = {
            path.relative_to(bundle_dir).as_posix(): path.read_bytes()
            for path in sorted((bundle_dir / "artifact").rglob("*"))
            if path.is_file()
        }
        record = _make_bundle_record(previous["case_id"], previous["page_id"], files)
        manifest_path.write_bytes(detector_fixtures.canonical_json_bytes(record.to_dict()))

    def test_clean_and_nonmechanical_reports_require_no_action_or_fallback(self) -> None:
        clean = self.report("clean")
        authorization = authorize_fault_detection_report(clean)
        self.assertEqual(clean.status, NO_FAULT_DETECTED)
        self.assertEqual(authorization.status, NO_ACTION)
        self.assertEqual(authorization.action, "none")
        self.assertEqual(authorization.error_code, "none")
        self.assertEqual(authorization.predicted_repair_scope, ())
        self.assertEqual(authorization.policy_allowed_scope, ())
        self.assertEqual(authorization.effective_repair_scope, ())
        self.assertEqual(authorization.fallback_recommendation, "not_applicable")

        for name in ("page_spec", "inspector"):
            with self.subTest(name=name):
                report = self.report(name)
                item = authorize_fault_detection_report(report)
                self.assertEqual(report.status, FAULT_DETECTED)
                self.assertEqual(item.status, FALLBACK_REQUIRED)
                self.assertEqual(item.action, "deterministic_fallback")
                self.assertEqual(item.predicted_repair_scope, ())
                self.assertEqual(item.policy_allowed_scope, ())
                self.assertEqual(item.effective_repair_scope, ())
                self.assertEqual(item.fallback_recommendation, "deterministic_fallback")

    def test_render_and_package_reports_authorize_only_exact_policy_scope(self) -> None:
        for name in ("render", "package_path", "package_sha256"):
            with self.subTest(name=name):
                report = self.report(name)
                authorization = authorize_fault_detection_report(report)
                self.assertEqual(report.status, FAULT_DETECTED)
                self.assertTrue(report.predicted_repairable)
                self.assertEqual(
                    authorization.status, DETERMINISTIC_REPAIR_AUTHORIZED
                )
                self.assertEqual(authorization.action, "deterministic_repair")
                self.assertEqual(
                    authorization.predicted_repair_scope,
                    authorization.policy_allowed_scope,
                )
                self.assertEqual(
                    authorization.effective_repair_scope,
                    authorization.policy_allowed_scope,
                )
                self.assertEqual(
                    authorization.report_id,
                    report.report_id,
                )
                self.assertEqual(authorization.report_sha256, report.sha256())

    def test_authorization_integrity_is_separate_from_report_binding(self) -> None:
        report = self.report("render")
        authorization = authorize_fault_detection_report(report)
        authorization.validate_against(report)

        other_report = self.report("package_path")
        wrong_reference = replace(
            authorization,
            report_id=other_report.report_id,
            report_sha256=other_report.sha256(),
        )
        wrong_reference = replace(
            wrong_reference,
            authorization_id=(
                "repair-authorization-"
                + repair_policy_module._canonical_sha256(
                    wrong_reference.to_payload()
                )[:20]
            ),
        )
        wrong_reference.validate()
        with self.assertRaisesRegex(RepairPolicyError, "referenced report report_id"):
            wrong_reference.validate_against(report)
        with self.assertRaisesRegex(RepairPolicyError, "referenced report error_code"):
            wrong_reference.validate_against(other_report)
    def test_unknown_and_ambiguous_real_reports_require_fallback(self) -> None:
        unknown_dir = self.output("unknown")
        shutil.copytree(self.fixture.clean_dir, unknown_dir)
        plan_path = unknown_dir / "artifact" / "acceptance" / "acceptance_plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["unexpected_field"] = "visible outside approved classifier"
        plan_path.write_bytes(detector_fixtures.canonical_json_bytes(plan))
        self.reseal_bundle(unknown_dir)
        unknown = detect_blinded_fault_bundle(unknown_dir, self.fixture.parity)
        self.assertEqual(unknown.status, UNCLASSIFIED_FAILURE)

        ambiguous_dir = self.output("ambiguous")
        shutil.copytree(self.fixture.fault_dirs["page_spec"], ambiguous_dir)
        source_manifest = (
            self.fixture.fault_dirs["package_sha256"]
            / "artifact" / "result_package" / "package_manifest.json"
        )
        target_manifest = (
            ambiguous_dir
            / "artifact" / "result_package" / "package_manifest.json"
        )
        target_manifest.write_bytes(source_manifest.read_bytes())
        self.reseal_bundle(ambiguous_dir)
        ambiguous = detect_blinded_fault_bundle(ambiguous_dir, self.fixture.parity)
        self.assertEqual(ambiguous.status, AMBIGUOUS_MULTIPLE_FAULTS)

        for report in (unknown, ambiguous):
            with self.subTest(status=report.status):
                authorization = authorize_fault_detection_report(report)
                self.assertEqual(authorization.status, FALLBACK_REQUIRED)
                self.assertEqual(authorization.action, "deterministic_fallback")
                self.assertEqual(authorization.predicted_repair_scope, ())
                self.assertEqual(authorization.policy_allowed_scope, ())
                self.assertEqual(authorization.effective_repair_scope, ())

    def test_policy_is_independent_and_rejects_under_over_or_disjoint_scope(self) -> None:
        report = self.report("render")
        registry = repair_policy_module._frozen_policy_registry()
        rule = repair_policy_module._rule_for_error_code(
            registry, report.predicted_error_code
        )
        self.assertEqual(
            rule.policy_allowed_scope,
            (
                "artifact/render/index.html",
                "artifact/render/render_manifest.json",
            ),
        )
        variants = {
            "under": ("artifact/render/index.html",),
            "over": (
                "artifact/page_spec.json",
                "artifact/render/index.html",
                "artifact/render/render_manifest.json",
            ),
            "disjoint": ("artifact/page_spec.json",),
        }
        for name, scope in variants.items():
            with self.subTest(name=name):
                decision = repair_policy_module._authorization_decision_from_fields(
                    report_status=FAULT_DETECTED,
                    error_code=report.predicted_error_code,
                    predicted_scope=scope,
                    policy=registry,
                    predicted_repairable=True,
                )
                self.assertEqual(decision.status, FALLBACK_REQUIRED)
                self.assertEqual(
                    decision.effective_repair_scope,
                    tuple(sorted(set(scope).intersection(rule.policy_allowed_scope))),
                )
        correct = repair_policy_module._authorization_decision_from_fields(
            report_status=FAULT_DETECTED,
            error_code=report.predicted_error_code,
            predicted_scope=rule.policy_allowed_scope,
            policy=registry,
            predicted_repairable=True,
        )
        self.assertEqual(correct.status, DETERMINISTIC_REPAIR_AUTHORIZED)

    def test_contradictory_repairability_fails_closed(self) -> None:
        registry = repair_policy_module._frozen_policy_registry()
        page_report = self.report("page_spec")
        inspector_report = self.report("inspector")
        contradictions = (
            (NO_FAULT_DETECTED, "none", ()),
            (UNCLASSIFIED_FAILURE, "unclassified_artifact_failure", ()),
            (AMBIGUOUS_MULTIPLE_FAULTS, "multiple_faults", ()),
            (FAULT_DETECTED, page_report.predicted_error_code, ()),
            (FAULT_DETECTED, inspector_report.predicted_error_code, ()),
        )
        for status, error_code, scope in contradictions:
            with self.subTest(status=status, error_code=error_code):
                with self.assertRaisesRegex(
                    RepairPolicyError, "must not predict repairability"
                ):
                    repair_policy_module._authorization_decision_from_fields(
                        report_status=status,
                        error_code=error_code,
                        predicted_scope=scope,
                        policy=registry,
                        predicted_repairable=True,
                    )

        render_report = self.report("render")
        mechanical_fallback = repair_policy_module._authorization_decision_from_fields(
            report_status=FAULT_DETECTED,
            error_code=render_report.predicted_error_code,
            predicted_scope=render_report.predicted_repair_scope,
            policy=registry,
            predicted_repairable=False,
        )
        self.assertEqual(mechanical_fallback.status, FALLBACK_REQUIRED)

        page_authorization = authorize_fault_detection_report(page_report)
        contradictory = replace(page_authorization, predicted_repairable=True)
        contradictory = replace(
            contradictory,
            authorization_id=(
                "repair-authorization-"
                + repair_policy_module._canonical_sha256(
                    contradictory.to_payload()
                )[:20]
            ),
        )
        with self.assertRaisesRegex(
            RepairPolicyError, "fallback-only error must not predict repairability"
        ):
            contradictory.validate()
    def test_hash_tamper_and_canonical_identity_fail_closed(self) -> None:
        report = self.report("render")
        with self.assertRaisesRegex(FaultDetectionError, "report_id"):
            authorize_fault_detection_report(
                replace(report, report_id="fault-detection-report-" + "0" * 20)
            )
        registry = repair_policy_module._frozen_policy_registry()
        with self.assertRaisesRegex(RepairPolicyError, "policy_sha256"):
            replace(registry, policy_sha256="0" * 64).validate()
        authorization = authorize_fault_detection_report(report)
        with self.assertRaisesRegex(RepairPolicyError, "authorization_id"):
            replace(
                authorization,
                authorization_id="repair-authorization-" + "0" * 20,
            ).validate()
        with self.assertRaisesRegex(RepairPolicyError, "frozen policy"):
            replace(authorization, policy_sha256="0" * 64).validate()

    def test_writer_is_deterministic_safe_and_non_overwriting(self) -> None:
        authorization = authorize_fault_detection_report(self.report("render"))
        first = self.output("first")
        second = self.output("second")
        write_repair_authorization(authorization, first)
        write_repair_authorization(authorization, second)
        self.assertEqual(tree_bytes(first), tree_bytes(second))
        self.assertEqual(
            sorted(tree_bytes(first)),
            ["repair_authorization.json", "repair_authorization_manifest.json"],
        )
        occupied = self.output("occupied")
        occupied.mkdir(parents=True)
        (occupied / "sentinel.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(RepairPolicyError, "empty"):
            write_repair_authorization(authorization, occupied)
        self.assertEqual(
            (occupied / "sentinel.txt").read_text(encoding="utf-8"), "preserve"
        )
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(RepairPolicyError, "symlink"):
                write_repair_authorization(authorization, self.output("symlink"))

    def test_public_surface_and_serialization_remain_isolated(self) -> None:
        source = (ROOT / "src" / "req2web_faults" / "repair_policy.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        for forbidden in ("mutation", "injector_audit", "evaluator_gold", "bundle"):
            self.assertFalse(any(forbidden in name for name in imports), forbidden)
        report = self.report("render")
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected artifact read")):
            authorization = authorize_fault_detection_report(report)
        serialized = json.dumps(authorization.to_dict(), sort_keys=True)
        for forbidden in (
            "gold_allowed_scope", "FaultGoldManifest", "injector_audit",
            "mutation_request", "bundle_id", "fault_copy",
        ):
            self.assertNotIn(forbidden, serialized)
        for hidden in ("RepairPolicyRule", "RepairPolicyRegistry"):
            self.assertFalse(hasattr(req2web_faults, hidden), hidden)
            self.assertNotIn(hidden, req2web_faults.__all__)
        self.assertIn("authorize_fault_detection_report", req2web_faults.__all__)
        with self.assertRaisesRegex(RepairPolicyError, "FaultDetectionReport"):
            authorize_fault_detection_report(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    import unittest
    unittest.main()