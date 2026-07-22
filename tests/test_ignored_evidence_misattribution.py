from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import shutil
import sys
from unittest import TestCase
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_acceptance import (  # noqa: E402
    compile_acceptance_binding,
    compile_acceptance_plan,
    project_requirement_view,
)
from req2web_evaluation import (  # noqa: E402
    FaultRecoveryEvaluationError,
    aggregate_fault_recovery_evaluations,
    evaluate_fault_recovery_case,
)
import req2web_evaluation.negative_control as negative_control_module  # noqa: E402
from req2web_evaluation.negative_control import (  # noqa: E402
    PASSED,
    NegativeControlError,
    NegativeControlSpec,
    build_passing_negative_control_ignored_evidence_misattribution_fault_copy,
    run_negative_control,
)
import req2web_faults.bundle as bundle_module  # noqa: E402
import req2web_faults.detector as detector_module  # noqa: E402
from req2web_faults import (  # noqa: E402
    FALLBACK_DELIVERY,
    FALLBACK_REQUIRED,
    IGNORED_EVIDENCE_MISATTRIBUTED_TO_PAGE_SPEC,
    NO_FAULT_DETECTED,
    BlindedFaultBundleSource,
    assemble_blinded_fault_bundle,
    authorize_fault_detection_report,
    detect_blinded_fault_bundle,
    execute_deterministic_development_recovery,
    freeze_g0_fallback_package,
    validate_blinded_bundle_inventory_parity,
)
from req2web_faults.evaluator_gold import build_fault_gold_manifest  # noqa: E402
from req2web_faults.injector_audit import build_injector_mutation_audit  # noqa: E402
from req2web_faults.mutation import (  # noqa: E402
    FaultMutationError,
    FaultMutationRequest,
    build_fault_copy,
)
from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    DeterministicRetrievalEnhancedResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
from req2web_inspector import project_g0_inspector_facts  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from test_guided_page_spec import build_context  # noqa: E402
from test_negative_control import irrelevant_implementation_result  # noqa: E402


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class IgnoredEvidenceMisattributionTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = (
            ROOT / "tests" / ".tmp_ignored_evidence_misattribution"
            / ("run-" + uuid.uuid4().hex)
        )
        cls.root.mkdir(parents=True)
        cls.case_id = "dev-ignored-evidence-misattribution"
        cls.context = build_context()
        cls.spec = NegativeControlSpec.preregister(
            cls.case_id,
            cls.context,
            "implementation",
            [irrelevant_implementation_result()],
        )
        cls.control_run = run_negative_control(cls.spec, cls.context)
        if cls.control_run.report.status != PASSED:
            raise AssertionError("test fixture requires a passing M2-07a negative control")
        cls.baseline_source = cls._source(
            cls.control_run.baseline_context,
            cls.control_run.baseline_guidance,
            cls.control_run.baseline_guided_build,
            cls.root / "baseline-source",
        )
        cls.controlled_source = cls._source(
            cls.control_run.controlled_context,
            cls.control_run.controlled_guidance,
            cls.control_run.controlled_guided_build,
            cls.root / "controlled-source",
        )
        cls.baseline_bundle_dir = cls.root / "baseline-bundle"
        cls.controlled_bundle_dir = cls.root / "controlled-bundle"
        cls.baseline_bundle = assemble_blinded_fault_bundle(
            cls.baseline_source, cls.baseline_bundle_dir
        )
        cls.controlled_bundle = assemble_blinded_fault_bundle(
            cls.controlled_source, cls.controlled_bundle_dir
        )
        cls.clean_parity = validate_blinded_bundle_inventory_parity(
            (cls.baseline_bundle, cls.controlled_bundle)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def _source(cls, context, guidance, guided, output: Path) -> BlindedFaultBundleSource:
        page_spec = guided.page_spec
        view = project_requirement_view(context)
        plan = compile_acceptance_plan(view)
        render = DeterministicPageRenderer().render(page_spec, output / "render")
        binding = compile_acceptance_binding(view, plan, page_spec, render)
        consistency = MinimalConsistencyChecker().check(page_spec, render)
        ablations = {
            role: RetrievalGuidedPageSpecBuilder().build(
                context, guidance, disabled_roles=(role,)
            )
            for role in ROLE_ORDER
        }
        influence = RetrievalInfluenceChecker().check(
            context,
            guidance,
            PageSpecBuilder().build(context),
            guided,
            ablations,
            render,
        )
        facts = project_g0_inspector_facts(guidance, guided, page_spec, influence)
        package = DeterministicRetrievalEnhancedResultPackager().package(
            context,
            guidance,
            guided,
            page_spec,
            render,
            consistency,
            influence,
            output / "package-v2",
        )
        return BlindedFaultBundleSource(
            case_id=cls.case_id,
            requirement_view=view,
            acceptance_plan=plan,
            acceptance_binding=binding,
            page_spec=page_spec,
            inspector_fact_set=facts,
            render_result=render,
            result_package=package,
        )

    def output(self, name: str) -> Path:
        path = self.root / self._testMethodName / name
        shutil.rmtree(path, ignore_errors=True)
        return path

    def _injected_ignored_source_ref_id(self) -> str:
        injected_doc_ids = set(self.control_run.report.injected_doc_ids)
        facts = self.controlled_source.inspector_fact_set
        candidates = [
            fact.source_ref_id
            for fact in facts.facts
            if fact.disposition == "ignored"
            and next(
                source
                for source in facts.source_refs
                if source.source_ref_id == fact.source_ref_id
            ).doc_id in injected_doc_ids
        ]
        self.assertEqual(len(candidates), 1)
        return candidates[0]

    def _request(self, *, source_ref_id: str | None = None, entity_id: str | None = None, field_name: str = "title") -> FaultMutationRequest:
        return FaultMutationRequest(
            self.case_id,
            "ignored_evidence_misattributed_to_page_spec",
            source_ref_id or self._injected_ignored_source_ref_id(),
            page_spec_entity_id=entity_id or self.controlled_source.page_spec.page_id,
            page_spec_field_name=field_name,
        )

    def _build(self, name: str):
        """Build the formal D03-4 fixture only through the passed-control wrapper."""
        fragment_dir = self.output(name + "-fragment")
        build = build_passing_negative_control_ignored_evidence_misattribution_fault_copy(
            run=self.control_run,
            inspector_fact_set=self.controlled_source.inspector_fact_set,
            page_spec=self.controlled_source.page_spec,
            source_ref_id=self._injected_ignored_source_ref_id(),
            page_spec_entity_id=self.controlled_source.page_spec.page_id,
            page_spec_field_name="title",
            output_dir=fragment_dir,
        )
        return build, fragment_dir

    def _build_primitive(self, name: str):
        """Exercise the generic injector primitive without claiming control provenance."""
        fragment_dir = self.output(name + "-fragment")
        build = build_fault_copy(
            self._request(),
            self.controlled_source.inspector_fact_set,
            fragment_dir,
            page_spec=self.controlled_source.page_spec,
            guided_build_result=self.control_run.controlled_guided_build,
        )
        return build, fragment_dir

    def test_passing_control_stays_clean_and_one_misattribution_reaches_evaluator_fallback(self) -> None:
        clean = detect_blinded_fault_bundle(
            self.controlled_bundle_dir, self.clean_parity
        )
        self.assertEqual(clean.status, NO_FAULT_DETECTED)
        self.assertEqual(clean.findings, ())
        build, fragment_dir = self._build("misattribution")
        audit = build_injector_mutation_audit(build)
        self.assertEqual(audit.exact_target_id, self._injected_ignored_source_ref_id())
        self.assertEqual(audit.mutation_request_sha256, build.mutation_request.sha256())
        self.assertIn("field_path=page.title", audit.changed_path)
        gold = build_fault_gold_manifest(
            fault_copy_record=build.runtime_record,
            injector_audit=audit,
            gold_stage="guidance/adoption",
            gold_error=IGNORED_EVIDENCE_MISATTRIBUTED_TO_PAGE_SPEC,
            gold_repairable=False,
            gold_allowed_scope=(),
            expected_fallback="pre_frozen_same_case_g0_v2",
        )
        fault_bundle_dir = self.output("misattribution-bundle")
        fault_bundle = assemble_blinded_fault_bundle(
            self.controlled_source, fault_bundle_dir, fragment_dir=fragment_dir
        )
        parity = validate_blinded_bundle_inventory_parity(
            (self.baseline_bundle, self.controlled_bundle, fault_bundle)
        )
        detector = detect_blinded_fault_bundle(fault_bundle_dir, parity)
        self.assertEqual(detector.status, "fault_detected")
        self.assertEqual(detector.predicted_stage, "guidance/adoption")
        self.assertEqual(
            detector.predicted_error_code,
            IGNORED_EVIDENCE_MISATTRIBUTED_TO_PAGE_SPEC,
        )
        self.assertFalse(detector.predicted_repairable)
        self.assertEqual(detector.predicted_repair_scope, ())
        authorization = authorize_fault_detection_report(detector)
        self.assertEqual(authorization.status, FALLBACK_REQUIRED)
        self.assertEqual(authorization.policy_allowed_scope, ())
        snapshot_dir = self.output("frozen-snapshot")
        fallback_record = freeze_g0_fallback_package(
            self.case_id, self.controlled_source.result_package, snapshot_dir
        )
        outcome_dir = self.output("outcome")
        outcome = execute_deterministic_development_recovery(
            fault_bundle_dir,
            parity,
            detector,
            authorization,
            snapshot_dir,
            fallback_record,
            outcome_dir,
        )
        self.assertEqual(outcome.status, FALLBACK_DELIVERY)
        self.assertEqual(
            tree_bytes(snapshot_dir / "result_package"),
            tree_bytes(outcome_dir / "fallback" / "result_package"),
        )
        report = evaluate_fault_recovery_case(
            fault_copy_record=build.runtime_record,
            injector_audit=audit,
            gold_manifest=gold,
            clean_source=self.controlled_source,
            fragment_dir=fragment_dir,
            bundle_dir=fault_bundle_dir,
            parity_report=parity,
            detector_report=detector,
            authorization=authorization,
            recovery_outcome=outcome,
            recovery_outcome_dir=outcome_dir,
            fallback_snapshot_dir=snapshot_dir,
            fallback_record=fallback_record,
        )
        self.assertTrue(report.fault_localization_exact)
        self.assertEqual(report.predicted_scope_relation, "empty_both")
        self.assertEqual(report.fallback_delivery_status, "success")
        suite = aggregate_fault_recovery_evaluations((report,))
        self.assertEqual(suite.negative_control_case_ids, ())
        metrics = {metric.metric_name: metric for metric in suite.metrics}
        self.assertEqual(metrics["fault_localization_accuracy"].decimal_value, "1.000000")
        self.assertEqual(metrics["fallback_delivery"].decimal_value, "1.000000")
        bundle_bytes = b"".join(tree_bytes(fault_bundle_dir).values())
        for forbidden in (
            b"negative_control_spec.json",
            b"negative_control_report.json",
            b"fault_gold_manifest",
            b"injector_mutation_audit",
        ):
            self.assertNotIn(forbidden, bundle_bytes)
        with self.assertRaises(FaultRecoveryEvaluationError):
            evaluate_fault_recovery_case(
                fault_copy_record=build.runtime_record,
                injector_audit=audit,
                gold_manifest=gold,
                clean_source=self.controlled_source,
                fragment_dir=fragment_dir,
                bundle_dir=self.controlled_bundle_dir,
                parity_report=self.clean_parity,
                detector_report=clean,
                authorization=authorize_fault_detection_report(clean),
                recovery_outcome=outcome,
                recovery_outcome_dir=outcome_dir,
                fallback_snapshot_dir=snapshot_dir,
                fallback_record=fallback_record,
            )

    def test_injector_rejects_nonignored_unknown_nonempty_and_invalid_target_inputs(self) -> None:
        fact_set = self.controlled_source.inspector_fact_set
        nonignored_source = next(
            fact.source_ref_id for fact in fact_set.facts if fact.disposition != "ignored"
        )
        source_ref_id = self._injected_ignored_source_ref_id()
        invalid_requests = (
            self._request(source_ref_id="inspector-source-missing"),
            self._request(source_ref_id=nonignored_source),
            self._request(entity_id="missing-page-spec-entity"),
            self._request(field_name="not_a_frozen_page_spec_field"),
        )
        for index, request in enumerate(invalid_requests):
            with self.subTest(index=index), self.assertRaises(FaultMutationError):
                build_fault_copy(
                    request,
                    fact_set,
                    self.output("invalid-" + str(index)),
                    page_spec=self.controlled_source.page_spec,
                    guided_build_result=self.control_run.controlled_guided_build,
                )
        ignored = self.control_run.controlled_guided_build.ignored
        index = next(
            i for i, item in enumerate(ignored)
            if item.decision_id
            == next(
                fact.decision_id
                for fact in fact_set.facts
                if fact.source_ref_id == source_ref_id
            )
        )
        tampered_decision = replace(
            ignored[index],
            affected_fields=[
                type(self.control_run.controlled_guided_build.adopted[0].affected_fields[0])(
                    entity_id=self.controlled_source.page_spec.page_id,
                    field_name="title",
                )
            ],
        )
        tampered = replace(
            self.control_run.controlled_guided_build,
            ignored=[
                tampered_decision if item.decision_id == tampered_decision.decision_id else item
                for item in ignored
            ],
        )
        with self.assertRaises(FaultMutationError):
            build_fault_copy(
                self._request(),
                fact_set,
                self.output("affected-fields-nonempty"),
                page_spec=self.controlled_source.page_spec,
                guided_build_result=tampered,
            )

    def test_wrapper_rejects_forged_report_and_noninjected_source_bindings(self) -> None:
        report = self.control_run.report
        forged = replace(report, report_id="", spec_sha256="0" * 64)
        forged = replace(
            forged,
            report_id=negative_control_module._record_id(
                "negative-control-report", forged.to_payload()
            ),
        )
        with self.assertRaises(NegativeControlError):
            build_passing_negative_control_ignored_evidence_misattribution_fault_copy(
                run=replace(self.control_run, report=forged),
                inspector_fact_set=self.controlled_source.inspector_fact_set,
                page_spec=self.controlled_source.page_spec,
                source_ref_id=self._injected_ignored_source_ref_id(),
                page_spec_entity_id=self.controlled_source.page_spec.page_id,
                page_spec_field_name="title",
                output_dir=self.output("forged-report"),
            )
        noninjected_source_id = next(
            fact.source_ref_id
            for fact in self.controlled_source.inspector_fact_set.facts
            if next(
                source
                for source in self.controlled_source.inspector_fact_set.source_refs
                if source.source_ref_id == fact.source_ref_id
            ).doc_id not in report.injected_doc_ids
        )
        with self.assertRaises(NegativeControlError):
            build_passing_negative_control_ignored_evidence_misattribution_fault_copy(
                run=self.control_run,
                inspector_fact_set=self.controlled_source.inspector_fact_set,
                page_spec=self.controlled_source.page_spec,
                source_ref_id=noninjected_source_id,
                page_spec_entity_id=self.controlled_source.page_spec.page_id,
                page_spec_field_name="title",
                output_dir=self.output("noninjected-source"),
            )

    def test_new_fragment_writer_keeps_metadata_blind_and_rejects_existing_traversal_and_symlink_outputs(self) -> None:
        build, fragment_dir = self._build_primitive("safe")
        metadata = (fragment_dir / "fault_copy_record.json").read_text(encoding="utf-8")
        for forbidden in (
            "ignored_evidence_misattributed_to_page_spec",
            "page_spec_entity_id",
            "page_spec_field_name",
            build.mutation_request.target_id,
            "gold",
            "fallback",
        ):
            self.assertNotIn(forbidden, metadata)
        occupied = self.output("occupied")
        occupied.mkdir(parents=True)
        (occupied / "sentinel.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaises(FaultMutationError):
            build_fault_copy(
                self._request(),
                self.controlled_source.inspector_fact_set,
                occupied,
                page_spec=self.controlled_source.page_spec,
                guided_build_result=self.control_run.controlled_guided_build,
            )
        raw_parent = self.output("raw-parent")
        raw_parent.mkdir(parents=True)
        traversal = raw_parent / "safe" / ".." / "escaped"
        with self.assertRaises(FaultMutationError):
            build_fault_copy(
                self._request(),
                self.controlled_source.inspector_fact_set,
                traversal,
                page_spec=self.controlled_source.page_spec,
                guided_build_result=self.control_run.controlled_guided_build,
            )
        self.assertFalse((raw_parent / "escaped").exists())
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(FaultMutationError):
                build_fault_copy(
                    self._request(),
                    self.controlled_source.inspector_fact_set,
                    self.output("symlink"),
                    page_spec=self.controlled_source.page_spec,
                    guided_build_result=self.control_run.controlled_guided_build,
                )


    def test_detector_fails_closed_for_forged_or_ambiguous_extra_link_shapes(self) -> None:
        build, fragment_dir = self._build("negative-shapes")
        original_bundle_dir = self.output("negative-shapes-bundle")
        fault_bundle = assemble_blinded_fault_bundle(
            self.controlled_source, original_bundle_dir, fragment_dir=fragment_dir
        )
        parity = validate_blinded_bundle_inventory_parity(
            (self.baseline_bundle, self.controlled_bundle, fault_bundle)
        )
        clean_payload = json.loads(
            (self.controlled_bundle_dir / "artifact" / "inspector_fact_set.json").read_text(
                encoding="utf-8"
            )
        )
        base_link_ids = {item["trace_link_id"] for item in clean_payload["trace_links"]}

        def mutate_two_links(payload: dict[str, object]) -> None:
            extra = next(
                item for item in payload["trace_links"]
                if item["trace_link_id"] not in base_link_ids
            )
            second = deepcopy(extra)
            component_id = self.controlled_source.page_spec.components[0].component_id
            second["trace_link_id"] = "temporary-second-extra-link"
            second["entity_id"] = component_id
            second["field_path"] = "component[" + component_id + "].label"
            payload["trace_links"].append(second)
            fact = next(
                item for item in payload["facts"]
                if item["decision_id"] == second["decision_id"]
            )
            fact["trace_link_ids"].append(second["trace_link_id"])

        def mutate_wrong_source(payload: dict[str, object]) -> None:
            extra = next(
                item for item in payload["trace_links"]
                if item["trace_link_id"] not in base_link_ids
            )
            extra["source_ref_id"] = next(
                item["source_ref_id"] for item in payload["source_refs"]
                if item["source_kind"] == "retrieval_influence_v1"
            )

        def mutate_nonignored(payload: dict[str, object]) -> None:
            extra = next(
                item for item in payload["trace_links"]
                if item["trace_link_id"] not in base_link_ids
            )
            fact = next(
                item for item in payload["facts"]
                if item["decision_id"] == extra["decision_id"]
            )
            fact["disposition"] = "adopted"

        def mutate_wrong_target(payload: dict[str, object]) -> None:
            extra = next(
                item for item in payload["trace_links"]
                if item["trace_link_id"] not in base_link_ids
            )
            extra["entity_id"] = "missing-page-spec-entity"
            extra["field_path"] = "component[missing-page-spec-entity].label"

        def mutate_unrelated_fact_delta(payload: dict[str, object]) -> None:
            source = next(
                item for item in payload["source_refs"]
                if item["source_kind"] == "guided_retrieval"
                and item["doc_id"] not in self.control_run.report.injected_doc_ids
            )
            source["doc_id"] = source["doc_id"] + "-forged"

        variants = {
            "two-extra-links": mutate_two_links,
            "wrong-source": mutate_wrong_source,
            "nonignored-decision": mutate_nonignored,
            "wrong-target": mutate_wrong_target,
            "unrelated-fact-delta": mutate_unrelated_fact_delta,
        }
        for name, mutate in variants.items():
            with self.subTest(name=name):
                bundle_dir = self.output(name)
                shutil.copytree(original_bundle_dir, bundle_dir)
                artifact = bundle_dir / "artifact" / "inspector_fact_set.json"
                payload = json.loads(artifact.read_text(encoding="utf-8"))
                mutate(payload)
                self._recanonicalize_inspector_payload(payload)
                artifact.write_bytes(self._canonical_json_bytes(payload))
                self._reseal_bundle(bundle_dir)
                report = detect_blinded_fault_bundle(bundle_dir, parity)
                self.assertEqual(report.status, "unclassified_failure")
                self.assertFalse(report.predicted_repairable)
                self.assertEqual(report.predicted_repair_scope, ())
                self.assertNotIn(
                    IGNORED_EVIDENCE_MISATTRIBUTED_TO_PAGE_SPEC,
                    {item.error_code for item in report.findings},
                )
                self.assertIn(
                    "inspector_expected_projection_mismatch",
                    {item.code for item in report.diagnostics},
                )

    @staticmethod
    def _canonical_json_bytes(value: object) -> bytes:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @staticmethod
    def _recanonicalize_inspector_payload(payload: dict[str, object]) -> None:
        source_id_map = {}
        for source in payload["source_refs"]:
            old_id = source["source_ref_id"]
            source_payload = {
                key: value for key, value in source.items() if key != "source_ref_id"
            }
            source["source_ref_id"] = detector_module._stable_id(
                "inspector-source", source_payload
            )
            source_id_map[old_id] = source["source_ref_id"]
        link_id_map = {}
        for link in payload["trace_links"]:
            old_id = link["trace_link_id"]
            link["source_ref_id"] = source_id_map.get(
                link["source_ref_id"], link["source_ref_id"]
            )
            link_payload = {
                key: value for key, value in link.items() if key != "trace_link_id"
            }
            link["trace_link_id"] = detector_module._stable_id(
                "inspector-trace", link_payload
            )
            link_id_map[old_id] = link["trace_link_id"]
        for fact in payload["facts"]:
            fact["source_ref_id"] = source_id_map.get(
                fact["source_ref_id"], fact["source_ref_id"]
            )
            fact["verification_source_ref_id"] = source_id_map.get(
                fact["verification_source_ref_id"], fact["verification_source_ref_id"]
            )
            fact["trace_link_ids"] = sorted(
                link_id_map.get(value, value) for value in fact["trace_link_ids"]
            )
            fact_payload = {key: value for key, value in fact.items() if key != "fact_id"}
            fact["fact_id"] = detector_module._stable_id("inspector-fact", fact_payload)
        payload["source_refs"] = sorted(
            payload["source_refs"], key=lambda item: item["source_ref_id"]
        )
        payload["trace_links"] = sorted(
            payload["trace_links"], key=lambda item: item["trace_link_id"]
        )
        payload["facts"] = sorted(payload["facts"], key=lambda item: item["fact_id"])
        fact_set_payload = {key: value for key, value in payload.items() if key != "fact_set_id"}
        payload["fact_set_id"] = detector_module._stable_id(
            "inspector-fact-set", fact_set_payload
        )

    @staticmethod
    def _reseal_bundle(bundle_dir: Path) -> None:
        manifest_path = bundle_dir / "fault_case_bundle_manifest.json"
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = {
            path.relative_to(bundle_dir).as_posix(): path.read_bytes()
            for path in sorted((bundle_dir / "artifact").rglob("*"))
            if path.is_file()
        }
        record = bundle_module._make_bundle_record(
            previous["case_id"], previous["page_id"], files
        )
        manifest_path.write_bytes(
            json.dumps(
                record.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
