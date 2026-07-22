from __future__ import annotations

import ast
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import uuid
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import req2web_faults  # noqa: E402
import req2web_faults.bundle as bundle_module  # noqa: E402
import req2web_faults.detector as detector_module  # noqa: E402
from req2web_acceptance import (  # noqa: E402
    compile_acceptance_binding,
    compile_acceptance_plan,
    project_requirement_view,
)
from req2web_faults import (  # noqa: E402
    AMBIGUOUS_MULTIPLE_FAULTS,
    DOM_COMPONENT_STABLE_ID_MISMATCH,
    FAULT_DETECTED,
    INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH,
    NO_FAULT_DETECTED,
    PACKAGE_MANIFEST_PATH_MISMATCH,
    PACKAGE_MANIFEST_SHA256_MISMATCH,
    PAGE_SPEC_DANGLING_COMPONENT_REFERENCE,
    UNCLASSIFIED_FAILURE,
    BlindedFaultBundleSource,
    FaultDetectionError,
    assemble_blinded_fault_bundle,
    detect_blinded_fault_bundle,
    validate_blinded_bundle_inventory_parity,
    write_fault_detection_report,
)
from req2web_faults.mutation import (  # noqa: E402
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


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


class FaultDetectorTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = (
            ROOT / "tests" / ".tmp_fault_detector" / ("run-" + uuid.uuid4().hex)
        )
        cls.root.mkdir(parents=True)
        cls.context = build_context()
        cls.view = project_requirement_view(cls.context)
        cls.plan = compile_acceptance_plan(cls.view)
        cls.guidance = RetrievalGuidanceBuilder().build(cls.context)
        cls.guided = RetrievalGuidedPageSpecBuilder().build(cls.context, cls.guidance)
        cls.page_spec = cls.guided.page_spec
        cls.ablations = {
            role: RetrievalGuidedPageSpecBuilder().build(
                cls.context, cls.guidance, disabled_roles=(role,)
            )
            for role in ROLE_ORDER
        }
        cls.render = DeterministicPageRenderer().render(
            cls.page_spec, cls.root / "source" / "render"
        )
        cls.binding = compile_acceptance_binding(
            cls.view, cls.plan, cls.page_spec, cls.render
        )
        cls.consistency = MinimalConsistencyChecker().check(
            cls.page_spec, cls.render
        )
        cls.influence = RetrievalInfluenceChecker().check(
            cls.context,
            cls.guidance,
            PageSpecBuilder().build(cls.context),
            cls.guided,
            cls.ablations,
            cls.render,
        )
        cls.fact_set = project_g0_inspector_facts(
            cls.guidance, cls.guided, cls.page_spec, cls.influence
        )
        cls.package = DeterministicRetrievalEnhancedResultPackager().package(
            cls.context,
            cls.guidance,
            cls.guided,
            cls.page_spec,
            cls.render,
            cls.consistency,
            cls.influence,
            cls.root / "source" / "package-v2",
        )
        cls.source = BlindedFaultBundleSource(
            case_id="dev-case-detector",
            requirement_view=cls.view,
            acceptance_plan=cls.plan,
            acceptance_binding=cls.binding,
            page_spec=cls.page_spec,
            inspector_fact_set=cls.fact_set,
            render_result=cls.render,
            result_package=cls.package,
        )
        cls.bundle_root = cls.root / "bundles"
        cls.clean_dir = cls.bundle_root / "clean"
        cls.clean_record = assemble_blinded_fault_bundle(cls.source, cls.clean_dir)

        component_id = cls.page_spec.components[0].component_id
        trace_link_id = cls.fact_set.trace_links[0].trace_link_id
        routes = {
            "page_spec": (
                FaultMutationRequest(
                    cls.source.case_id,
                    "page_spec_component_removed",
                    component_id,
                ),
                cls.page_spec,
            ),
            "inspector": (
                FaultMutationRequest(
                    cls.source.case_id,
                    "inspector_trace_relation_removed",
                    trace_link_id,
                ),
                cls.fact_set,
            ),
            "render": (
                FaultMutationRequest(
                    cls.source.case_id,
                    "render_component_stable_id_tampered",
                    component_id,
                ),
                cls.render,
            ),
            "package_path": (
                FaultMutationRequest(
                    cls.source.case_id,
                    "package_manifest_path_tampered",
                    "page/index.html",
                    "path",
                ),
                cls.package,
            ),
            "package_sha256": (
                FaultMutationRequest(
                    cls.source.case_id,
                    "package_manifest_sha256_tampered",
                    "page/index.html",
                    "sha256",
                ),
                cls.package,
            ),
        }
        cls.fault_dirs = {}
        cls.fault_records = {}
        for name, (request, artifact) in routes.items():
            fragment_dir = cls.root / "fragments" / name
            build_fault_copy(request, artifact, fragment_dir)
            bundle_dir = cls.bundle_root / name
            cls.fault_dirs[name] = bundle_dir
            cls.fault_records[name] = assemble_blinded_fault_bundle(
                cls.source, bundle_dir, fragment_dir=fragment_dir
            )
        cls.parity = validate_blinded_bundle_inventory_parity([
            cls.clean_record,
            *cls.fault_records.values(),
        ])


    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def output(self, name: str) -> Path:
        path = self.root / "test-output" / self._testMethodName / name
        shutil.rmtree(path, ignore_errors=True)
        return path

    def test_clean_bundle_is_structural_no_fault_and_deterministic(self) -> None:
        first = detect_blinded_fault_bundle(self.clean_dir, self.parity)
        second = detect_blinded_fault_bundle(self.clean_dir, self.parity)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.status, NO_FAULT_DETECTED)
        self.assertEqual(first.predicted_stage, "none")
        self.assertEqual(first.predicted_error_code, "none")
        self.assertFalse(first.predicted_repairable)
        self.assertEqual(first.findings, ())
        self.assertEqual(first.diagnostics, ())
        self.assertEqual(first.bundle_id, self.clean_record.bundle_id)
        self.assertEqual(first.case_id, self.source.case_id)
        self.assertEqual(first.page_id, self.page_spec.page_id)

    def test_current_bundle_route_is_g0_v2_only(self) -> None:
        manifest = json.loads(
            (
                self.clean_dir
                / "artifact"
                / "result_package"
                / "package_manifest.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["schema_version"],
            "req2web.result.package.v2",
        )
        paths = {entry["path"] for entry in manifest["files"]}
        self.assertIn("internal/retrieval_guidance.json", paths)
        self.assertIn("internal/guided_page_spec_build_result.json", paths)
        self.assertIn("internal/retrieval_influence_report.json", paths)
        report = detect_blinded_fault_bundle(self.clean_dir, self.parity)
        self.assertEqual(report.status, NO_FAULT_DETECTED)
        self.assertEqual(report.diagnostics, ())

    def test_four_approved_families_are_localized_without_cascade_duplication(self) -> None:
        expected = {
            "page_spec": (
                "page_spec",
                PAGE_SPEC_DANGLING_COMPONENT_REFERENCE,
                False,
                (),
            ),
            "inspector": (
                "guidance/adoption",
                INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH,
                False,
                (),
            ),
            "render": (
                "render_binding",
                DOM_COMPONENT_STABLE_ID_MISMATCH,
                True,
                (
                    "artifact/render/index.html",
                    "artifact/render/render_manifest.json",
                ),
            ),
            "package_path": (
                "package",
                PACKAGE_MANIFEST_PATH_MISMATCH,
                True,
                ("artifact/result_package/package_manifest.json",),
            ),
            "package_sha256": (
                "package",
                PACKAGE_MANIFEST_SHA256_MISMATCH,
                True,
                ("artifact/result_package/package_manifest.json",),
            ),
        }
        for name, values in expected.items():
            with self.subTest(name=name):
                report = detect_blinded_fault_bundle(
                    self.fault_dirs[name], self.parity
                )
                stage, code, repairable, scope = values
                self.assertEqual(report.status, FAULT_DETECTED)
                self.assertEqual(report.predicted_stage, stage)
                self.assertEqual(report.predicted_error_code, code)
                self.assertEqual(report.predicted_repairable, repairable)
                self.assertEqual(report.predicted_repair_scope, scope)
                self.assertEqual(len(report.findings), 1)
                self.assertEqual(report.diagnostics, ())
                self.assertTrue(report.expected)
                self.assertTrue(report.actual)
                if not repairable:
                    self.assertEqual(
                        report.fallback_recommendation,
                        "deterministic_fallback",
                    )

    def test_unknown_and_ambiguous_inputs_never_become_no_fault_or_repairable(self) -> None:
        unknown_dir = self.output("unknown")
        shutil.copytree(self.clean_dir, unknown_dir)
        plan_path = unknown_dir / "artifact" / "acceptance" / "acceptance_plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["unexpected_field"] = "visible but outside approved classifier"
        plan_path.write_bytes(canonical_json_bytes(plan))
        self._reseal_bundle(unknown_dir)
        unknown = detect_blinded_fault_bundle(unknown_dir, self.parity)
        self.assertEqual(unknown.status, UNCLASSIFIED_FAILURE)
        self.assertFalse(unknown.predicted_repairable)
        self.assertEqual(unknown.predicted_repair_scope, ())
        self.assertTrue(unknown.diagnostics)

        ambiguous_dir = self.output("ambiguous")
        shutil.copytree(self.fault_dirs["page_spec"], ambiguous_dir)
        source_manifest = (
            self.fault_dirs["package_sha256"]
            / "artifact"
            / "result_package"
            / "package_manifest.json"
        )
        target_manifest = (
            ambiguous_dir
            / "artifact"
            / "result_package"
            / "package_manifest.json"
        )
        target_manifest.write_bytes(source_manifest.read_bytes())
        self._reseal_bundle(ambiguous_dir)
        ambiguous = detect_blinded_fault_bundle(ambiguous_dir, self.parity)
        self.assertEqual(ambiguous.status, AMBIGUOUS_MULTIPLE_FAULTS)
        self.assertFalse(ambiguous.predicted_repairable)
        self.assertEqual(ambiguous.predicted_repair_scope, ())
        self.assertGreaterEqual(len(ambiguous.findings), 2)

    def test_inspector_nonrelation_tampering_is_unclassified_after_recanonicalization(self) -> None:
        variants = ("disposition", "source", "verification", "decision")
        for name in variants:
            with self.subTest(name=name):
                bundle_dir = self.output("inspector-" + name)
                shutil.copytree(self.clean_dir, bundle_dir)
                artifact = bundle_dir / "artifact" / "inspector_fact_set.json"
                payload = json.loads(artifact.read_text(encoding="utf-8"))
                fact = next(item for item in payload["facts"] if item["trace_link_ids"])
                if name == "disposition":
                    fact["disposition"] = (
                        "ignored" if fact["disposition"] != "ignored" else "adopted"
                    )
                elif name == "source":
                    source = next(
                        item
                        for item in payload["source_refs"]
                        if item["source_ref_id"] == fact["source_ref_id"]
                    )
                    source["doc_id"] = source["doc_id"] + "-tampered"
                elif name == "verification":
                    source = next(
                        item
                        for item in payload["source_refs"]
                        if item["source_ref_id"] == fact["verification_source_ref_id"]
                    )
                    source["artifact_sha256"] = "0" * 64
                    payload["retrieval_influence_report_sha256"] = "0" * 64
                else:
                    old_decision_id = fact["decision_id"]
                    fact["decision_id"] = old_decision_id + "-tampered"
                    linked = set(fact["trace_link_ids"])
                    for link in payload["trace_links"]:
                        if link["trace_link_id"] in linked:
                            link["decision_id"] = fact["decision_id"]
                self._recanonicalize_inspector_payload(payload)
                artifact.write_bytes(canonical_json_bytes(payload))
                self._reseal_bundle(bundle_dir)
                report = detect_blinded_fault_bundle(bundle_dir, self.parity)
                self.assertEqual(report.status, UNCLASSIFIED_FAILURE)
                self.assertFalse(report.predicted_repairable)
                self.assertEqual(report.predicted_repair_scope, ())
                self.assertNotIn(
                    INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH,
                    {item.error_code for item in report.findings},
                )
                self.assertIn(
                    "inspector_expected_projection_mismatch",
                    {item.code for item in report.diagnostics},
                )

    def test_multiple_missing_components_are_unclassified_or_ambiguous(self) -> None:
        multiple_dir = self.output("multiple-page-components")
        shutil.copytree(self.clean_dir, multiple_dir)
        page_path = multiple_dir / "artifact" / "page_spec.json"
        page = json.loads(page_path.read_text(encoding="utf-8"))
        referenced = set()
        for section in page["sections"]:
            referenced.update(section["component_ids"])
        for state in page["states"]:
            referenced.update(state["visible_component_ids"])
        referenced.update(item["trigger_component_id"] for item in page["interactions"])
        for trace in page["traceability"]["use_cases"]:
            referenced.update(trace["component_ids"])
        removed = [
            item["component_id"]
            for item in page["components"]
            if item["component_id"] in referenced
        ][:2]
        self.assertEqual(len(removed), 2)
        page["components"] = [
            item for item in page["components"] if item["component_id"] not in removed
        ]
        page_path.write_bytes(canonical_json_bytes(page))
        self._reseal_bundle(multiple_dir)
        report = detect_blinded_fault_bundle(multiple_dir, self.parity)
        self.assertEqual(report.status, UNCLASSIFIED_FAILURE)
        self.assertEqual(report.findings, ())
        self.assertIn(
            "page_spec_multiple_missing_component_references",
            {item.code for item in report.diagnostics},
        )
        self.assertFalse(report.predicted_repairable)
        self.assertEqual(report.predicted_repair_scope, ())

        ambiguous_dir = self.output("multiple-page-components-plus-package")
        shutil.copytree(multiple_dir, ambiguous_dir)
        source_manifest = (
            self.fault_dirs["package_sha256"]
            / "artifact"
            / "result_package"
            / "package_manifest.json"
        )
        target_manifest = (
            ambiguous_dir
            / "artifact"
            / "result_package"
            / "package_manifest.json"
        )
        target_manifest.write_bytes(source_manifest.read_bytes())
        self._reseal_bundle(ambiguous_dir)
        ambiguous = detect_blinded_fault_bundle(ambiguous_dir, self.parity)
        self.assertEqual(ambiguous.status, AMBIGUOUS_MULTIPLE_FAULTS)
        self.assertIn(
            PACKAGE_MANIFEST_SHA256_MISMATCH,
            {item.error_code for item in ambiguous.findings},
        )
        self.assertIn(
            "page_spec_multiple_missing_component_references",
            {item.code for item in ambiguous.diagnostics},
        )
        self.assertFalse(ambiguous.predicted_repairable)
        self.assertEqual(ambiguous.predicted_repair_scope, ())

    def test_parity_mismatch_and_bundle_tamper_fail_before_detection(self) -> None:
        paths = list(self.parity.paths)
        paths[-1] = "artifact/result_package/unexpected-parity-path.json"
        paths = tuple(sorted(paths))
        bad_parity = replace(
            self.parity,
            paths=paths,
            path_set_sha256=detector_module._canonical_sha256({
                "paths": list(paths),
                "slots": list(self.parity.slots),
            }),
        )
        bad_parity.validate()
        with self.assertRaisesRegex(FaultDetectionError, "path inventory"):
            detect_blinded_fault_bundle(self.clean_dir, bad_parity)

        tampered_dir = self.output("tampered")
        shutil.copytree(self.clean_dir, tampered_dir)
        page_path = tampered_dir / "artifact" / "page_spec.json"
        page_path.write_bytes(page_path.read_bytes() + b" ")
        with self.assertRaisesRegex(FaultDetectionError, "bundle or parity"):
            detect_blinded_fault_bundle(tampered_dir, self.parity)

    def test_evaluator_side_files_are_not_imported_or_read(self) -> None:
        source = (ROOT / "src" / "req2web_faults" / "detector.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        for forbidden in ("mutation", "injector_audit", "evaluator_gold"):
            self.assertFalse(any(forbidden in name for name in imports), forbidden)

        sidecar = self.root / "evaluator-side" / "fault_gold_manifest.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text("this file must never be read", encoding="utf-8")
        original_read_bytes = Path.read_bytes

        def guarded_read_bytes(path: Path) -> bytes:
            if path == sidecar:
                raise AssertionError("evaluator-side file was read")
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", guarded_read_bytes):
            report = detect_blinded_fault_bundle(self.clean_dir, self.parity)
        self.assertEqual(report.status, NO_FAULT_DETECTED)
        serialized = json.dumps(report.to_dict(), sort_keys=True)
        for forbidden in (
            "gold", "mutation", "control_label", "fault_copy",
            "target_artifact", "copy_location",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_report_summary_and_identity_are_canonical(self) -> None:
        report = detect_blinded_fault_bundle(
            self.fault_dirs["render"], self.parity
        )
        with self.assertRaisesRegex(FaultDetectionError, "status"):
            replace(report, status=NO_FAULT_DETECTED).validate()
        with self.assertRaisesRegex(FaultDetectionError, "report_id"):
            replace(report, report_id="fault-detection-report-" + "0" * 20).validate()

    def test_writer_is_canonical_safe_and_non_overwriting(self) -> None:
        report = detect_blinded_fault_bundle(
            self.fault_dirs["render"], self.parity
        )
        first = self.output("writer-first")
        second = self.output("writer-second")
        write_fault_detection_report(report, first)
        write_fault_detection_report(report, second)
        self.assertEqual(tree_bytes(first), tree_bytes(second))
        self.assertEqual(
            sorted(tree_bytes(first)),
            [
                "fault_detection_report.json",
                "fault_detection_report_manifest.json",
            ],
        )
        occupied = self.output("occupied")
        occupied.mkdir(parents=True)
        (occupied / "sentinel.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(FaultDetectionError, "empty"):
            write_fault_detection_report(report, occupied)
        self.assertEqual(
            (occupied / "sentinel.txt").read_text(encoding="utf-8"),
            "preserve",
        )
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(FaultDetectionError, "symlink"):
                write_fault_detection_report(report, self.output("symlink"))

    def test_public_api_requires_parity_and_has_no_unblinded_shortcut(self) -> None:
        self.assertIn("detect_blinded_fault_bundle", req2web_faults.__all__)
        self.assertFalse(hasattr(req2web_faults, "detect_fault_copy"))
        with self.assertRaisesRegex(FaultDetectionError, "parity_report"):
            detect_blinded_fault_bundle(self.clean_dir, None)  # type: ignore[arg-type]

    def _recanonicalize_inspector_payload(self, payload: dict[str, object]) -> None:
        source_id_map = {}
        for source in payload["source_refs"]:
            old_id = source["source_ref_id"]
            source_payload = {key: value for key, value in source.items() if key != "source_ref_id"}
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
            link_payload = {key: value for key, value in link.items() if key != "trace_link_id"}
            link["trace_link_id"] = detector_module._stable_id(
                "inspector-trace", link_payload
            )
            link_id_map[old_id] = link["trace_link_id"]

        for fact in payload["facts"]:
            fact["source_ref_id"] = source_id_map.get(
                fact["source_ref_id"], fact["source_ref_id"]
            )
            fact["verification_source_ref_id"] = source_id_map.get(
                fact["verification_source_ref_id"],
                fact["verification_source_ref_id"],
            )
            fact["trace_link_ids"] = sorted(
                link_id_map.get(value, value) for value in fact["trace_link_ids"]
            )
            fact_payload = {key: value for key, value in fact.items() if key != "fact_id"}
            fact["fact_id"] = detector_module._stable_id(
                "inspector-fact", fact_payload
            )

        payload["source_refs"] = sorted(
            payload["source_refs"], key=lambda item: item["source_ref_id"]
        )
        payload["trace_links"] = sorted(
            payload["trace_links"], key=lambda item: item["trace_link_id"]
        )
        payload["facts"] = sorted(
            payload["facts"], key=lambda item: item["fact_id"]
        )
        fact_set_payload = {
            key: value for key, value in payload.items() if key != "fact_set_id"
        }
        payload["fact_set_id"] = detector_module._stable_id(
            "inspector-fact-set", fact_set_payload
        )

    def _reseal_bundle(self, bundle_dir: Path) -> None:
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
        manifest_path.write_bytes(canonical_json_bytes(record.to_dict()))


if __name__ == "__main__":
    import unittest
    unittest.main()