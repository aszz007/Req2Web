from __future__ import annotations

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

from req2web_faults import (
    assemble_blinded_fault_bundle,
    authorize_fault_detection_report,
    detect_blinded_fault_bundle,
    execute_deterministic_development_recovery,
    freeze_g0_fallback_package,
    validate_blinded_bundle_inventory_parity,
)
from req2web_faults.evaluator_gold import build_fault_gold_manifest
from req2web_faults.injector_audit import build_injector_mutation_audit
from req2web_evaluation.fault_recovery_evaluation import (
    aggregate_fault_recovery_evaluations,
    evaluate_fault_recovery_case,
    write_fault_recovery_evaluation_case,
    write_fault_recovery_evaluation_suite,
)
import req2web_evaluation.m2_closure_readiness as closure_module
from req2web_evaluation.m2_closure_readiness import (
    DEPENDENCY_DEFERRED_BROWSER_RUNTIME_EVIDENCE,
    DEPENDENCY_DEFERRED_M3_PROVIDER,
    DEPENDENCY_DEFERRED_SEMANTIC_EVIDENCE,
    NOT_READY,
    READY_FOR_MANAGER_REVIEW,
    M2ClosureReadinessError,
    ManualFaultEvidenceBinding,
    NegativeControlEvidenceBinding,
    build_m2_closure_readiness_matrix,
    validate_m2_closure_readiness_artifact,
    write_m2_closure_readiness_matrix,
)
from req2web_evaluation.negative_control import write_negative_control_artifact
import test_fault_recovery_evaluation as recovery_fixtures
import test_ignored_evidence_misattribution as ignored_fixtures


class M2ClosureReadinessTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = ROOT / "tests" / ".tmp_m2_closure_readiness"
        shutil.rmtree(cls.root, ignore_errors=True)
        cls.root.mkdir(parents=True)
        recovery_fixtures.FaultRecoveryEvaluationTest.setUpClass()
        ignored_fixtures.IgnoredEvidenceMisattributionTest.setUpClass()
        cls.recovery = recovery_fixtures.FaultRecoveryEvaluationTest("runTest")
        cls.ignored = ignored_fixtures.IgnoredEvidenceMisattributionTest("runTest")
        cls.manual_bindings, reports = cls._make_manual_artifacts()
        cls.suite_dir = cls.root / "suite"
        suite = aggregate_fault_recovery_evaluations(reports)
        write_fault_recovery_evaluation_suite(suite, cls.suite_dir)
        cls.negative_binding = cls._make_negative_binding()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)
        ignored_fixtures.IgnoredEvidenceMisattributionTest.tearDownClass()
        recovery_fixtures.FaultRecoveryEvaluationTest.tearDownClass()

    @classmethod
    def _make_manual_artifacts(cls):
        (cls.root / "manual").mkdir(parents=True, exist_ok=True)
        bindings = []
        reports = []
        routes = (
            ("d03_1_required_component_removal", "page_spec"),
            ("d03_5_missing_evidence_adoption_relation", "inspector"),
            ("d03_6_dom_component_stable_id_tamper", "render"),
            ("d03_7a_manifest_path_tamper", "package_path"),
            ("d03_7b_manifest_sha256_tamper", "package_sha256"),
        )
        for variant_id, route in routes:
            report, external = cls.recovery.make_case(route)
            artifact_dir = cls.root / "manual" / variant_id
            write_fault_recovery_evaluation_case(report, artifact_dir, **external)
            bindings.append(ManualFaultEvidenceBinding(variant_id, artifact_dir, external))
            reports.append(report)
        report, external = cls._make_misattribution_case()
        artifact_dir = cls.root / "manual" / "d03_4a_ignored_evidence_misattribution"
        write_fault_recovery_evaluation_case(report, artifact_dir, **external)
        bindings.append(ManualFaultEvidenceBinding("d03_4a_ignored_evidence_misattribution", artifact_dir, external))
        reports.append(report)
        return tuple(bindings), tuple(reports)

    @classmethod
    def _make_negative_binding(cls) -> NegativeControlEvidenceBinding:
        artifact_dir = cls.root / "negative-control"
        write_negative_control_artifact(cls.ignored.control_run.spec, cls.ignored.control_run.report, artifact_dir)
        return NegativeControlEvidenceBinding(
            artifact_dir=artifact_dir,
            run=cls.ignored.control_run,
            baseline_source=cls.ignored.baseline_source,
            controlled_source=cls.ignored.controlled_source,
        )

    @classmethod
    def _make_misattribution_case(cls):
        build, fragment_dir = cls.ignored._build("closure-misattribution")
        audit = build_injector_mutation_audit(build)
        gold = build_fault_gold_manifest(
            fault_copy_record=build.runtime_record,
            injector_audit=audit,
            gold_stage="guidance/adoption",
            gold_error="ignored_evidence_misattributed_to_page_spec",
            gold_repairable=False,
            gold_allowed_scope=(),
            expected_fallback="pre_frozen_same_case_g0_v2",
        )
        bundle_dir = cls.root / "misattribution-bundle"
        fault_bundle = assemble_blinded_fault_bundle(cls.ignored.controlled_source, bundle_dir, fragment_dir=fragment_dir)
        parity = validate_blinded_bundle_inventory_parity((cls.ignored.baseline_bundle, cls.ignored.controlled_bundle, fault_bundle))
        detector = detect_blinded_fault_bundle(bundle_dir, parity)
        authorization = authorize_fault_detection_report(detector)
        snapshot_dir = cls.root / "misattribution-snapshot"
        fallback = freeze_g0_fallback_package(cls.ignored.case_id, cls.ignored.controlled_source.result_package, snapshot_dir)
        outcome_dir = cls.root / "misattribution-outcome"
        outcome = execute_deterministic_development_recovery(bundle_dir, parity, detector, authorization, snapshot_dir, fallback, outcome_dir)
        external = {
            "fault_copy_record": build.runtime_record,
            "injector_audit": audit,
            "gold_manifest": gold,
            "clean_source": cls.ignored.controlled_source,
            "fragment_dir": fragment_dir,
            "bundle_dir": bundle_dir,
            "parity_report": parity,
            "detector_report": detector,
            "authorization": authorization,
            "recovery_outcome": outcome,
            "recovery_outcome_dir": outcome_dir,
            "fallback_snapshot_dir": snapshot_dir,
            "fallback_record": fallback,
        }
        return evaluate_fault_recovery_case(**external), external

    def output(self, name: str) -> Path:
        path = self.root / self._testMethodName / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(path, ignore_errors=True)
        return path

    def test_complete_matrix_rebinds_manual_rows_suite_and_passing_control(self) -> None:
        matrix = build_m2_closure_readiness_matrix(
            self.manual_bindings,
            suite_artifact_dir=self.suite_dir,
            negative_control_binding=self.negative_binding,
        )
        self.assertEqual(matrix.readiness_status, READY_FOR_MANAGER_REVIEW)
        self.assertEqual(
            tuple(row.variant_id for row in matrix.manual_fault_rows),
            (
                "d03_1_required_component_removal",
                "d03_4a_ignored_evidence_misattribution",
                "d03_5_missing_evidence_adoption_relation",
                "d03_6_dom_component_stable_id_tamper",
                "d03_7a_manifest_path_tamper",
                "d03_7b_manifest_sha256_tamper",
            ),
        )
        self.assertFalse(matrix.negative_control_row.manual_metric_eligible)
        deferred = {row.variant_id: row.coverage_status for row in matrix.deferred_rows}
        self.assertEqual(deferred["d03_2_wrong_target_state"], DEPENDENCY_DEFERRED_SEMANTIC_EVIDENCE)
        self.assertEqual(deferred["d03_8_provider_generation"], DEPENDENCY_DEFERRED_M3_PROVIDER)
        self.assertEqual(deferred["d03_10_browser_runtime"], DEPENDENCY_DEFERRED_BROWSER_RUNTIME_EVIDENCE)
        artifact = self.output("matrix")
        write_m2_closure_readiness_matrix(
            matrix, artifact, self.manual_bindings,
            suite_artifact_dir=self.suite_dir,
            negative_control_binding=self.negative_binding,
        )
        loaded = validate_m2_closure_readiness_artifact(
            artifact, self.manual_bindings,
            suite_artifact_dir=self.suite_dir,
            negative_control_binding=self.negative_binding,
        )
        self.assertEqual(loaded.to_dict(), matrix.to_dict())
        self.assertEqual(sorted(item.name for item in artifact.iterdir()), ["m2_closure_readiness.json", "m2_closure_readiness_manifest.json"])

    def test_missing_implemented_row_is_not_ready_but_deferred_rows_are_not_failures(self) -> None:
        partial = tuple(item for item in self.manual_bindings if item.variant_id != "d03_7b_manifest_sha256_tamper")
        matrix = build_m2_closure_readiness_matrix(partial, negative_control_binding=self.negative_binding)
        self.assertEqual(matrix.readiness_status, NOT_READY)
        self.assertEqual(matrix.missing_coverage_ids, ("d03_7b_manifest_sha256_tamper",))
        self.assertEqual(len(matrix.deferred_rows), 6)
        matrix.validate_against(partial, suite_artifact_dir=None, negative_control_binding=self.negative_binding)

    def test_d03_4a_requires_explicit_passed_control_wrapper_binding(self) -> None:
        without_control = tuple(item for item in self.manual_bindings if item.variant_id == "d03_4a_ignored_evidence_misattribution")
        with self.assertRaises(M2ClosureReadinessError):
            build_m2_closure_readiness_matrix(without_control)

    def test_report_local_artifact_cannot_replace_external_rebinding(self) -> None:
        first = next(item for item in self.manual_bindings if item.variant_id == "d03_1_required_component_removal")
        wrong = next(item for item in self.manual_bindings if item.variant_id == "d03_5_missing_evidence_adoption_relation")
        forged_inputs = dict(first.case_bindings)
        forged_inputs["detector_report"] = wrong.case_bindings["detector_report"]
        forged = ManualFaultEvidenceBinding(first.variant_id, first.case_artifact_dir, forged_inputs)
        with self.assertRaises(M2ClosureReadinessError):
            build_m2_closure_readiness_matrix((forged,), negative_control_binding=self.negative_binding)


    def test_writer_rejects_existing_targets_and_forged_matrix_identity(self) -> None:
        matrix = build_m2_closure_readiness_matrix(
            self.manual_bindings,
            suite_artifact_dir=self.suite_dir,
            negative_control_binding=self.negative_binding,
        )
        artifact = self.output("matrix")
        write_m2_closure_readiness_matrix(matrix, artifact, self.manual_bindings, suite_artifact_dir=self.suite_dir, negative_control_binding=self.negative_binding)
        with self.assertRaises(M2ClosureReadinessError):
            write_m2_closure_readiness_matrix(matrix, artifact, self.manual_bindings, suite_artifact_dir=self.suite_dir, negative_control_binding=self.negative_binding)
        with self.assertRaises(M2ClosureReadinessError):
            write_m2_closure_readiness_matrix(matrix, Path("tests") / ".." / "matrix-escape", self.manual_bindings, suite_artifact_dir=self.suite_dir, negative_control_binding=self.negative_binding)
        extra = artifact / "unexpected.txt"
        extra.write_text("not part of the exact manifest", encoding="utf-8")
        with self.assertRaises(M2ClosureReadinessError):
            validate_m2_closure_readiness_artifact(artifact, self.manual_bindings, suite_artifact_dir=self.suite_dir, negative_control_binding=self.negative_binding)
        extra.unlink()
        payload_path = artifact / "m2_closure_readiness.json"
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        payload["matrix_id"] = "m2-closure-readiness-forged"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        with self.assertRaises(M2ClosureReadinessError):
            validate_m2_closure_readiness_artifact(artifact, self.manual_bindings, suite_artifact_dir=self.suite_dir, negative_control_binding=self.negative_binding)



    def test_writer_ownership_cleanup_and_inventory_guards(self) -> None:
        matrix = build_m2_closure_readiness_matrix(
            self.manual_bindings,
            suite_artifact_dir=self.suite_dir,
            negative_control_binding=self.negative_binding,
        )
        concurrent = self.output("concurrent-destination")
        original_rename = Path.rename
        foreign_matrix = b"foreign matrix bytes"
        foreign_manifest = b"foreign manifest bytes"

        def create_concurrent_destination(path: Path, target: Path) -> Path:
            target = Path(target)
            if target == concurrent:
                target.mkdir()
                (target / "m2_closure_readiness.json").write_bytes(foreign_matrix)
                (target / "m2_closure_readiness_manifest.json").write_bytes(foreign_manifest)
                raise FileExistsError("simulated concurrent destination")
            return original_rename(path, target)

        with patch.object(Path, "rename", new=create_concurrent_destination):
            with self.assertRaises(FileExistsError):
                closure_module._write_matrix_artifact(concurrent, matrix)
        self.assertEqual((concurrent / "m2_closure_readiness.json").read_bytes(), foreign_matrix)
        self.assertEqual((concurrent / "m2_closure_readiness_manifest.json").read_bytes(), foreign_manifest)

        owned = self.output("owned-post-rename-failure")
        original_validate = closure_module._validate_static_artifact

        def fail_only_after_rename(root: Path):
            if Path(root) == owned:
                raise M2ClosureReadinessError("simulated post-rename validation failure")
            return original_validate(root)

        with patch.object(closure_module, "_validate_static_artifact", side_effect=fail_only_after_rename):
            with self.assertRaises(M2ClosureReadinessError):
                closure_module._write_matrix_artifact(owned, matrix)
        self.assertFalse(owned.exists())

        retained = self.output("ownership-mismatch")

        def replace_then_fail(root: Path):
            if Path(root) == retained:
                (retained / "m2_closure_readiness.json").write_bytes(foreign_matrix)
                raise M2ClosureReadinessError("simulated post-rename validation failure")
            return original_validate(root)

        with patch.object(closure_module, "_validate_static_artifact", side_effect=replace_then_fail):
            with self.assertRaises(M2ClosureReadinessError):
                closure_module._write_matrix_artifact(retained, matrix)
        self.assertTrue(retained.exists())
        self.assertEqual((retained / "m2_closure_readiness.json").read_bytes(), foreign_matrix)

        missing = self.output("missing-inventory")
        closure_module._write_matrix_artifact(missing, matrix)
        (missing / "m2_closure_readiness_manifest.json").unlink()
        with self.assertRaises(M2ClosureReadinessError):
            closure_module._read_matrix_artifact(missing)

        simulated_symlink = self.output("simulated-symlink")
        with patch.object(closure_module, "_reject_symlinks", side_effect=M2ClosureReadinessError("simulated symlink guard")):
            with self.assertRaises(M2ClosureReadinessError):
                closure_module._prepare_output(simulated_symlink)
        self.assertFalse(simulated_symlink.exists())
