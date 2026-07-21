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

import req2web_faults.recovery_outcome as recovery_outcome_module  # noqa: E402
import req2web_evaluation.fault_recovery_evaluation as fault_evaluation_module  # noqa: E402
from req2web_faults import (  # noqa: E402
    DOM_COMPONENT_STABLE_ID_MISMATCH,
    INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH,
    PACKAGE_MANIFEST_PATH_MISMATCH,
    PACKAGE_MANIFEST_SHA256_MISMATCH,
    PAGE_SPEC_DANGLING_COMPONENT_REFERENCE,
    RepairExecutionError,
    assemble_blinded_fault_bundle,
    authorize_fault_detection_report,
    detect_blinded_fault_bundle,
    execute_deterministic_development_recovery,
    freeze_g0_fallback_package,
    validate_blinded_bundle_inventory_parity,
)
from req2web_faults.evaluator_gold import build_fault_gold_manifest  # noqa: E402
from req2web_faults.injector_audit import build_injector_mutation_audit  # noqa: E402
from req2web_faults.mutation import FaultMutationRequest, build_fault_copy  # noqa: E402
from req2web_evaluation import (  # noqa: E402
    FaultRecoveryEvaluationError,
    aggregate_fault_recovery_evaluations,
    evaluate_fault_recovery_case,
    validate_fault_recovery_evaluation_case_artifact,
    validate_fault_recovery_evaluation_suite_artifact,
    write_fault_recovery_evaluation_case,
    write_fault_recovery_evaluation_suite,
)
import test_fault_detector as detector_fixtures  # noqa: E402


class FaultRecoveryEvaluationTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        detector_fixtures.FaultDetectorTest.setUpClass()
        cls.fixture = detector_fixtures.FaultDetectorTest
        cls.root = ROOT / "tests" / ".tmp_fault_recovery_evaluation"
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

    def relabel_for_aggregate(self, report, case_id: str, *, localization_success: bool):
        candidate = replace(report, report_id="", case_id=case_id)
        if not localization_success:
            candidate = replace(
                candidate,
                gold_stage="acceptance",
                stage_match=False,
                fault_localization_exact=False,
            )
        return replace(
            candidate,
            report_id="fault-recovery-evaluation-case-"
            + fault_evaluation_module._canonical_sha256(candidate.to_payload())[:20],
        )

    def make_case(self, name: str, *, with_fallback: bool = True, repair_failure: bool = False):
        component_id = self.fixture.page_spec.components[0].component_id
        trace_link_id = self.fixture.fact_set.trace_links[0].trace_link_id
        routes = {
            "page_spec": (
                FaultMutationRequest(self.fixture.source.case_id, "page_spec_component_removed", component_id),
                self.fixture.page_spec, "page_spec", PAGE_SPEC_DANGLING_COMPONENT_REFERENCE, False, (),
            ),
            "inspector": (
                FaultMutationRequest(self.fixture.source.case_id, "inspector_trace_relation_removed", trace_link_id),
                self.fixture.fact_set, "guidance/adoption", INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH, False, (),
            ),
            "render": (
                FaultMutationRequest(self.fixture.source.case_id, "render_component_stable_id_tampered", component_id),
                self.fixture.render, "render_binding", DOM_COMPONENT_STABLE_ID_MISMATCH, True,
                ("artifact/render/index.html", "artifact/render/render_manifest.json"),
            ),
            "package_path": (
                FaultMutationRequest(self.fixture.source.case_id, "package_manifest_path_tampered", "page/index.html", "path"),
                self.fixture.package, "package", PACKAGE_MANIFEST_PATH_MISMATCH, True,
                ("artifact/result_package/package_manifest.json",),
            ),
            "package_sha256": (
                FaultMutationRequest(self.fixture.source.case_id, "package_manifest_sha256_tampered", "page/index.html", "sha256"),
                self.fixture.package, "package", PACKAGE_MANIFEST_SHA256_MISMATCH, True,
                ("artifact/result_package/package_manifest.json",),
            ),
        }
        request, artifact, stage, error, repairable, allowed_scope = routes[name]
        fragment_dir = self.output(name + "-fragment")
        build = build_fault_copy(request, artifact, fragment_dir)
        audit = build_injector_mutation_audit(build)
        gold = build_fault_gold_manifest(
            fault_copy_record=build.runtime_record,
            injector_audit=audit,
            gold_stage=stage,
            gold_error=error,
            gold_repairable=repairable,
            gold_allowed_scope=allowed_scope,
            expected_fallback="pre_registered_route_preserved_without_string_mapping",
        )
        bundle_dir = self.output(name + "-bundle")
        bundle = assemble_blinded_fault_bundle(self.fixture.source, bundle_dir, fragment_dir=fragment_dir)
        parity = validate_blinded_bundle_inventory_parity((self.fixture.clean_record, bundle))
        detector = detect_blinded_fault_bundle(bundle_dir, parity)
        authorization = authorize_fault_detection_report(detector)
        snapshot_dir = self.output(name + "-snapshot") if with_fallback else None
        fallback_record = freeze_g0_fallback_package(self.fixture.source.case_id, self.fixture.package, snapshot_dir) if snapshot_dir else None
        outcome_dir = self.output(name + "-outcome")
        if repair_failure:
            with patch.object(
                recovery_outcome_module,
                "execute_authorized_deterministic_repair",
                side_effect=RepairExecutionError("injected repair failure"),
            ):
                outcome = execute_deterministic_development_recovery(
                    bundle_dir, parity, detector, authorization, snapshot_dir, fallback_record, outcome_dir
                )
        else:
            outcome = execute_deterministic_development_recovery(
                bundle_dir, parity, detector, authorization, snapshot_dir, fallback_record, outcome_dir
            )
        bindings = dict(
            fault_copy_record=build.runtime_record,
            injector_audit=audit,
            gold_manifest=gold,
            clean_source=self.fixture.source,
            fragment_dir=fragment_dir,
            bundle_dir=bundle_dir,
            parity_report=parity,
            detector_report=detector,
            authorization=authorization,
            recovery_outcome=outcome,
            recovery_outcome_dir=outcome_dir,
            fallback_snapshot_dir=snapshot_dir,
            fallback_record=fallback_record,
        )
        report = evaluate_fault_recovery_case(**bindings)
        return report, bindings

    def test_development_fault_rows_score_all_supported_routes_and_keep_exclusions_separate(self) -> None:
        reports = []
        for name in ("page_spec", "inspector", "render", "package_path", "package_sha256"):
            with self.subTest(name=name):
                report, _ = self.make_case(name)
                reports.append(report)
                self.assertTrue(report.fault_localization_exact)
                self.assertTrue(report.repairability_match)
        self.assertEqual(reports[0].predicted_scope_relation, "empty_both")
        self.assertEqual(reports[1].predicted_scope_relation, "empty_both")
        self.assertEqual(reports[0].predicted_scope_relation in {"exact", "empty_both"}, True)
        self.assertEqual(reports[1].predicted_scope_relation in {"exact", "empty_both"}, True)
        self.assertEqual(reports[0].fallback_delivery_status, "success")
        self.assertEqual(reports[1].fallback_delivery_status, "success")
        for report in reports[2:]:
            self.assertEqual(report.one_repair_success, "success")
            self.assertEqual(report.actual_repair_locality, "within_gold_allowed_scope")
            self.assertEqual(report.fallback_delivery_status, "not_applicable")
        baseline_suite = aggregate_fault_recovery_evaluations(reports)
        suite = aggregate_fault_recovery_evaluations(
            reports,
            negative_control_case_ids=(self.fixture.source.case_id,),
            qwen_natural_error_case_ids=(self.fixture.source.case_id,),
        )
        self.assertEqual(
            tuple(item.to_dict() for item in suite.metrics),
            tuple(item.to_dict() for item in baseline_suite.metrics),
        )
        metrics = {item.metric_name: item for item in suite.metrics}
        for name, rows in (
            ("fault_localization_accuracy", (5, 5)),
            ("predicted_repairability_accuracy", (5, 5)),
            ("predicted_scope_exact", (5, 5)),
            ("deterministic_one_repair_success", (3, 3)),
            ("actual_repair_locality", (3, 3)),
            ("fallback_delivery", (2, 2)),
        ):
            with self.subTest(metric=name):
                metric = metrics[name]
                self.assertEqual((metric.row_numerator, metric.row_denominator), rows)
                self.assertEqual((metric.total_case_count, metric.applicable_case_count, metric.defined_case_count, metric.undefined_case_count), (1, 1, 1, 0))
                self.assertEqual(metric.decimal_value, "1.000000")
        self.assertEqual(suite.negative_control_case_ids, (self.fixture.source.case_id,))
        self.assertEqual(suite.qwen_natural_error_case_ids, (self.fixture.source.case_id,))

    def test_repair_failure_routes_to_fallback_but_never_scores_as_repair_success(self) -> None:
        report, _ = self.make_case("render", repair_failure=True)
        self.assertEqual(report.one_repair_success, "failure")
        self.assertEqual(report.repair_attempt_status, "attempted")
        self.assertEqual(report.actual_repair_scope_relation, "attempted_without_execution")
        self.assertEqual(report.fallback_delivery_status, "success")

    def test_fallback_unavailable_is_failed_delivery(self) -> None:
        report, _ = self.make_case("page_spec", with_fallback=False)
        self.assertEqual(report.final_outcome_status, "failed_delivery")
        self.assertEqual(report.fallback_delivery_applicability, "applicable")
        self.assertEqual(report.fallback_delivery_status, "failure")

    def test_case_writer_rebinds_all_external_inputs_and_rejects_mismatched_bundle(self) -> None:
        report, bindings = self.make_case("package_path")
        artifact_dir = self.output("case-artifact")
        write_fault_recovery_evaluation_case(report, artifact_dir, **bindings)
        loaded = validate_fault_recovery_evaluation_case_artifact(artifact_dir, **bindings)
        self.assertEqual(loaded.to_dict(), report.to_dict())
        manifest_path = artifact_dir / "fault_recovery_evaluation_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["report_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        with self.assertRaises(FaultRecoveryEvaluationError):
            validate_fault_recovery_evaluation_case_artifact(artifact_dir, **bindings)
        with self.assertRaises(FaultRecoveryEvaluationError):
            write_fault_recovery_evaluation_case(report, artifact_dir, **bindings)
        _, wrong_bindings = self.make_case("package_sha256")
        mismatched = dict(bindings)
        mismatched["bundle_dir"] = wrong_bindings["bundle_dir"]
        mismatched["parity_report"] = wrong_bindings["parity_report"]
        mismatched["detector_report"] = wrong_bindings["detector_report"]
        mismatched["authorization"] = wrong_bindings["authorization"]
        with self.assertRaises(FaultRecoveryEvaluationError):
            report.validate_against(**mismatched)

    def test_suite_writer_is_canonical_and_zero_denominator_is_explicit_na(self) -> None:
        nonrepairable, _ = self.make_case("inspector")
        suite = aggregate_fault_recovery_evaluations((nonrepairable,))
        metrics = {item.metric_name: item for item in suite.metrics}
        for name in ("deterministic_one_repair_success", "actual_repair_locality"):
            metric = metrics[name]
            self.assertEqual((metric.total_case_count, metric.applicable_case_count, metric.defined_case_count, metric.undefined_case_count), (1, 0, 0, 0))
            self.assertEqual((metric.row_numerator, metric.row_denominator, metric.decimal_value), (0, 0, "not_applicable"))
        output = self.output("suite-artifact")
        write_fault_recovery_evaluation_suite(suite, output)
        loaded = validate_fault_recovery_evaluation_suite_artifact(output)
        self.assertEqual(loaded.to_dict(), suite.to_dict())


    def test_case_macro_uses_equal_case_weights_not_mutation_row_micro_average(self) -> None:
        page_spec, _ = self.make_case("page_spec")
        inspector, _ = self.make_case("inspector")
        render, _ = self.make_case("render")
        package, _ = self.make_case("package_path")
        reports = (
            self.relabel_for_aggregate(page_spec, "macro-case-a", localization_success=True),
            self.relabel_for_aggregate(inspector, "macro-case-a", localization_success=True),
            self.relabel_for_aggregate(render, "macro-case-a", localization_success=False),
            self.relabel_for_aggregate(package, "macro-case-b", localization_success=False),
        )
        suite = aggregate_fault_recovery_evaluations(reports)
        metric = {item.metric_name: item for item in suite.metrics}["fault_localization_accuracy"]
        self.assertEqual((metric.total_case_count, metric.applicable_case_count, metric.defined_case_count, metric.undefined_case_count), (2, 2, 2, 0))
        self.assertEqual((metric.row_numerator, metric.row_denominator), (2, 4))
        self.assertEqual(metric.decimal_value, "0.333333")
        self.assertNotEqual(metric.decimal_value, "0.500000")
