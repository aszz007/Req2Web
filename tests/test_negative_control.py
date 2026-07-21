from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
from unittest import TestCase, mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_acceptance import (  # noqa: E402
    compile_acceptance_binding,
    compile_acceptance_plan,
    project_requirement_view,
)
from req2web_evaluation import normalize_candidate_decisions  # noqa: E402
import req2web_evaluation.negative_control as negative_control_module  # noqa: E402
from req2web_evaluation.negative_control import (  # noqa: E402
    NEGATIVE_CONTROL_FAILED,
    PASSED,
    NegativeControlError,
    NegativeControlInputError,
    NegativeControlSpec,
    run_negative_control,
    validate_negative_control_artifact,
    write_negative_control_artifact,
)
from req2web_faults import (  # noqa: E402
    NO_FAULT_DETECTED,
    BlindedFaultBundleSource,
    assemble_blinded_fault_bundle,
    detect_blinded_fault_bundle,
    validate_blinded_bundle_inventory_parity,
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


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def decision_projection_hash(case_id: str, page_spec: object) -> str:
    decisions = normalize_candidate_decisions(case_id, page_spec)
    projection = {
        "case_id": decisions.case_id,
        "decisions": [item.to_dict() for item in decisions.decisions],
        "schema_version": decisions.schema_version,
    }
    return sha256(canonical_json_bytes(projection)).hexdigest()


def irrelevant_implementation_result() -> dict[str, object]:
    return {
        "score": 0.0,
        "doc_id": "implementation:synthetic:camera-variant-v1",
        "role": "implementation",
        "dataset": "synthetic_development",
        "subset": "ordinary_variant",
        "sample_id": "camera-variant-v1",
        "title": "Camera photo",
        "summary": "camera photo",
        "references": [],
    }


def adopted_implementation_result() -> dict[str, object]:
    return {
        "score": 0.0,
        "doc_id": "implementation:synthetic:search-variant-v1",
        "role": "implementation",
        "dataset": "synthetic_development",
        "subset": "search_variant",
        "sample_id": "search-variant-v1",
        "title": "Search",
        "summary": "search",
        "references": [],
    }


class NegativeControlTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = ROOT / "tests" / ".tmp_negative_control"
        shutil.rmtree(cls.root, ignore_errors=True)
        cls.root.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self) -> None:
        self.context = build_context()
        self.case_id = "dev-negative-control"
        self.spec = NegativeControlSpec.preregister(
            self.case_id,
            self.context,
            "implementation",
            [irrelevant_implementation_result()],
        )
        self.run = run_negative_control(self.spec, self.context)

    def output(self, name: str) -> Path:
        path = self.root / self._testMethodName / name
        shutil.rmtree(path, ignore_errors=True)
        return path

    def test_preregistered_control_reuses_decision_units_and_retains_only_provenance_delta(self) -> None:
        report = self.run.report
        self.assertEqual(report.status, PASSED)
        self.assertFalse(report.full_page_spec_identity)
        self.assertTrue(report.provenance_only_delta)
        self.assertTrue(all(path == "page_id" or path.startswith("traceability") for path in report.full_page_spec_diff_paths))
        self.assertEqual({value for _, value in report.injected_decision_dispositions}, {"ignored"})
        self.assertEqual(report.baseline_decision_projection_sha256, report.controlled_decision_projection_sha256)
        self.assertEqual(report.stable_decision_projection_sha256, report.baseline_decision_projection_sha256)
        self.assertNotEqual(report.baseline_page_spec_sha256, report.controlled_page_spec_sha256)
        self.assertEqual(
            report.baseline_decision_projection_sha256,
            decision_projection_hash(self.case_id, self.run.baseline_guided_build.page_spec),
        )
        self.assertEqual(
            report.controlled_decision_projection_sha256,
            decision_projection_hash(self.case_id, self.run.controlled_guided_build.page_spec),
        )
        self.assertNotEqual(
            self.run.baseline_decisions.source_page_spec_sha256,
            self.run.controlled_decisions.source_page_spec_sha256,
        )
        self.assertEqual(self.context.to_dict(), self.run.baseline_context.to_dict())
        self.assertNotIn("negative_control", canonical_json_bytes(self.run.controlled_context.to_dict()).decode("utf-8"))
        self.assertNotIn("negative_control", canonical_json_bytes(self.run.controlled_guidance.to_dict()).decode("utf-8"))

    def test_adopted_injection_is_structured_failure_not_fault_report(self) -> None:
        spec = NegativeControlSpec.preregister(
            self.case_id,
            self.context,
            "implementation",
            [adopted_implementation_result()],
        )
        report = run_negative_control(spec, self.context).report
        self.assertEqual(report.status, NEGATIVE_CONTROL_FAILED)
        self.assertIn("injected_evidence_not_ignored", report.failure_codes)
        self.assertIn("operational_decision_projection_changed", report.failure_codes)
        self.assertIn("non_provenance_page_spec_delta", report.failure_codes)
        self.assertFalse(hasattr(report, "predicted_repair_scope"))
        self.assertFalse(hasattr(report, "fallback_recommendation"))

    def test_label_sidecar_markers_are_rejected_without_scanning_natural_language(self) -> None:
        natural_language = irrelevant_implementation_result()
        natural_language["title"] = "Gold fault mutation expected outcome"
        natural_language["summary"] = "Negative control evaluator prose is ordinary text here"
        NegativeControlSpec.preregister(self.case_id, self.context, "implementation", [natural_language])
        cases = [
            ("negative_control", True),
            ("control", "sidecar"),
            ("spec", "sidecar"),
            ("expected_outcome", "ignored_and_operationally_stable"),
            ("gold", "sidecar"),
            ("fault", "sidecar"),
            ("mutation", "sidecar"),
        ]
        for field_name, value in cases:
            with self.subTest(field_name=field_name):
                payload = irrelevant_implementation_result()
                payload[field_name] = value
                with self.assertRaises(NegativeControlInputError):
                    NegativeControlSpec.preregister(self.case_id, self.context, "implementation", [payload])
        for field_name, value in (
            ("doc_id", "implementation:synthetic:fault-copy-v1"),
            ("dataset", "gold"),
            ("subset", "irrelevant_evidence"),
            ("subset", "adoption_probe"),
            ("sample_id", "fault-mutation-copy"),
        ):
            with self.subTest(metadata_field=field_name, value=value):
                payload = irrelevant_implementation_result()
                payload[field_name] = value
                with self.assertRaises(NegativeControlInputError):
                    NegativeControlSpec.preregister(self.case_id, self.context, "implementation", [payload])

    def test_report_rejects_unknown_failure_code(self) -> None:
        invalid = replace(
            self.run.report,
            status=NEGATIVE_CONTROL_FAILED,
            failure_codes=("unregistered_failure_code",),
            stable_decision_projection_sha256=self.run.report.stable_decision_projection_sha256,
        )
        invalid = replace(
            invalid,
            report_id=negative_control_module._record_id("negative-control-report", invalid.to_payload()),
        )
        with self.assertRaises(NegativeControlError):
            invalid.validate()

    def test_writer_rejects_forged_locally_valid_report_before_output_exists(self) -> None:
        forged = replace(self.run.report, baseline_context_sha256="0" * 64)
        forged = replace(
            forged,
            report_id=negative_control_module._record_id("negative-control-report", forged.to_payload()),
        )
        forged.validate()
        output = self.output("forged")
        self.assertFalse(output.exists())
        with self.assertRaises(NegativeControlError):
            write_negative_control_artifact(self.spec, forged, output)
        self.assertFalse(output.exists())

    def test_tampered_spec_and_context_fail_closed(self) -> None:
        tampered_injected = replace(self.spec.injected_results[0], sha256="0" * 64)
        tampered_spec = replace(self.spec, injected_results=(tampered_injected,))
        with self.assertRaises(NegativeControlInputError):
            run_negative_control(tampered_spec, self.context)
        changed_context = build_context()
        changed_context.constraints.append("unregistered drift")
        with self.assertRaises(NegativeControlInputError):
            run_negative_control(self.spec, changed_context)

    def test_canonical_writer_and_external_rebind_reject_tampering_and_existing_output(self) -> None:
        artifact_dir = self.output("artifact")
        written = write_negative_control_artifact(self.spec, self.run.report, artifact_dir)
        self.assertEqual(validate_negative_control_artifact(artifact_dir, self.context).to_dict(), written.to_dict())
        self.assertEqual(
            {path.name for path in artifact_dir.iterdir()},
            {"negative_control_spec.json", "negative_control_report.json", "negative_control_manifest.json"},
        )
        with self.assertRaises(NegativeControlError):
            write_negative_control_artifact(self.spec, self.run.report, artifact_dir)
        report_path = artifact_dir / "negative_control_report.json"
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with self.assertRaises(NegativeControlError):
            validate_negative_control_artifact(artifact_dir, self.context)
        extra_dir = self.output("extra-artifact")
        write_negative_control_artifact(self.spec, self.run.report, extra_dir)
        (extra_dir / "unexpected.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(NegativeControlError):
            validate_negative_control_artifact(extra_dir, self.context)
        missing_dir = self.output("missing-artifact")
        write_negative_control_artifact(self.spec, self.run.report, missing_dir)
        (missing_dir / "negative_control_manifest.json").unlink()
        with self.assertRaises(NegativeControlError):
            validate_negative_control_artifact(missing_dir, self.context)

    def _source(self, context, guidance, guided, output: Path) -> BlindedFaultBundleSource:
        page_spec = guided.page_spec
        view = project_requirement_view(context)
        plan = compile_acceptance_plan(view)
        render = DeterministicPageRenderer().render(page_spec, output / "render")
        binding = compile_acceptance_binding(view, plan, page_spec, render)
        consistency = MinimalConsistencyChecker().check(page_spec, render)
        ablations = {
            role: RetrievalGuidedPageSpecBuilder().build(context, guidance, disabled_roles=(role,))
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
            case_id=self.case_id,
            requirement_view=view,
            acceptance_plan=plan,
            acceptance_binding=binding,
            page_spec=page_spec,
            inspector_fact_set=facts,
            render_result=render,
            result_package=package,
        )

    def test_blinded_baseline_control_inventory_parity_and_clean_detector(self) -> None:
        baseline_source = self._source(
            self.run.baseline_context,
            self.run.baseline_guidance,
            self.run.baseline_guided_build,
            self.output("baseline-source"),
        )
        controlled_source = self._source(
            self.run.controlled_context,
            self.run.controlled_guidance,
            self.run.controlled_guided_build,
            self.output("controlled-source"),
        )
        baseline_bundle_dir = self.output("baseline-bundle")
        controlled_bundle_dir = self.output("controlled-bundle")
        baseline_bundle = assemble_blinded_fault_bundle(baseline_source, baseline_bundle_dir)
        controlled_bundle = assemble_blinded_fault_bundle(controlled_source, controlled_bundle_dir)
        parity = validate_blinded_bundle_inventory_parity([baseline_bundle, controlled_bundle])
        self.assertEqual(tuple(item.path for item in baseline_bundle.files), tuple(item.path for item in controlled_bundle.files))
        self.assertEqual(tuple(item.slot for item in baseline_bundle.files), tuple(item.slot for item in controlled_bundle.files))
        detector_report = detect_blinded_fault_bundle(controlled_bundle_dir, parity)
        self.assertEqual(detector_report.status, NO_FAULT_DETECTED)
        self.assertFalse(any("negative_control" in item.path for item in controlled_bundle.files))
        bundle_bytes = b"".join(path.read_bytes() for path in controlled_bundle_dir.rglob("*") if path.is_file())
        for forbidden in (
            b"negative_control",
            b"req2web.evaluation.negative_control_spec.v1",
            b"req2web.evaluation.negative_control_report.v1",
            b"ignored_and_operationally_stable",
            b"irrelevant_evidence",
            b"adoption_probe",
        ):
            self.assertNotIn(forbidden, bundle_bytes)

    def test_writer_transaction_and_raw_path_guards_leave_no_residue(self) -> None:
        write_failure = self.output("write-failure")
        with mock.patch.object(
            negative_control_module,
            "_write_exact_bytes",
            side_effect=NegativeControlError("controlled write failure"),
        ):
            with self.assertRaises(NegativeControlError):
                write_negative_control_artifact(self.spec, self.run.report, write_failure)
        self.assertFalse(write_failure.exists())
        if write_failure.parent.exists():
            self.assertEqual(list(write_failure.parent.glob(".write-failure.staging-*")), [])

        post_commit_failure = self.output("post-commit-failure")
        original_static_validator = negative_control_module._validate_static_artifact_directory
        calls = 0

        def fail_after_publish(path: Path):
            nonlocal calls
            calls += 1
            result = original_static_validator(path)
            if calls == 2:
                raise NegativeControlError("controlled post-commit validation failure")
            return result

        with mock.patch.object(
            negative_control_module,
            "_validate_static_artifact_directory",
            side_effect=fail_after_publish,
        ):
            with self.assertRaises(NegativeControlError):
                write_negative_control_artifact(self.spec, self.run.report, post_commit_failure)
        self.assertEqual(calls, 2)
        self.assertFalse(post_commit_failure.exists())
        if post_commit_failure.parent.exists():
            self.assertEqual(list(post_commit_failure.parent.glob(".post-commit-failure.staging-*")), [])

        raw_parent = self.root / self._testMethodName
        raw_parent.mkdir(parents=True, exist_ok=True)
        traversal = raw_parent / "safe" / ".." / "escaped"
        with self.assertRaises(NegativeControlError):
            write_negative_control_artifact(self.spec, self.run.report, traversal)
        self.assertFalse((raw_parent / "escaped").exists())
