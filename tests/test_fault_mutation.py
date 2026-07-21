from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_acceptance import (  # noqa: E402
    compile_acceptance_binding,
    compile_acceptance_plan,
    project_requirement_view,
)
from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    DeterministicResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
    ResultPackageError,
)
from req2web_generation.renderer import RenderResult  # noqa: E402
from req2web_inspector import project_g0_inspector_facts  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_faults.injector_audit import (  # noqa: E402
    build_injector_mutation_audit,
    write_injector_mutation_audit,
)
from req2web_faults.mutation import (  # noqa: E402
    REGISTERED_MUTATION_KINDS,
    FaultMutationError,
    FaultMutationRequest,
    build_fault_copy,
    canonical_json_bytes,
    canonical_sha256,
)
from test_guided_page_spec import build_context  # noqa: E402


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def json_object(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class FaultMutationTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source_root = ROOT / "tests" / ".tmp_fault_mutation_source"
        shutil.rmtree(cls.source_root, ignore_errors=True)
        cls.source_root.mkdir(parents=True)
        cls.context = build_context()
        cls.page_spec = PageSpecBuilder().build(cls.context)
        cls.render = DeterministicPageRenderer().render(cls.page_spec, cls.source_root / "render")
        consistency = MinimalConsistencyChecker().check(cls.page_spec, cls.render)
        cls.package = DeterministicResultPackager().package(
            cls.context, cls.page_spec, cls.render, consistency, cls.source_root / "package"
        )
        cls.guidance = RetrievalGuidanceBuilder().build(cls.context)
        cls.guided = RetrievalGuidedPageSpecBuilder().build(cls.context, cls.guidance)
        ablations = {
            role: RetrievalGuidedPageSpecBuilder().build(cls.context, cls.guidance, disabled_roles=(role,))
            for role in ROLE_ORDER
        }
        guided_render = DeterministicPageRenderer().render(cls.guided.page_spec, cls.source_root / "guided_render")
        influence = RetrievalInfluenceChecker().check(
            cls.context, cls.guidance, PageSpecBuilder().build(cls.context), cls.guided,
            ablations, guided_render,
        )
        cls.fact_set = project_g0_inspector_facts(
            cls.guidance, cls.guided, cls.guided.page_spec, influence
        )
        cls.output_root = ROOT / "tests" / ".tmp_fault_mutation_output"
        shutil.rmtree(cls.output_root, ignore_errors=True)
        cls.output_root.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.source_root, ignore_errors=True)
        shutil.rmtree(cls.output_root, ignore_errors=True)

    def output(self, name: str) -> Path:
        target = self.output_root / self._testMethodName / name
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def assert_runtime_metadata_blind(self, root: Path, request: FaultMutationRequest) -> None:
        forbidden = set(REGISTERED_MUTATION_KINDS)
        forbidden.update({
            "mutation_kind", "changed_path", "gold_", "predicted_", "repairable",
            "allowed_scope", "fallback", "evaluator", "injector", "exact_target",
            "target_id",
        })
        record_payload = json.loads((root / "fault_copy_record.json").read_text(encoding="utf-8"))
        self.assertIs(record_payload["detector_input_ready"], False)
        for name in ("fault_copy_record.json", "fault_copy_manifest.json"):
            text = (root / name).read_text(encoding="utf-8")
            for value in forbidden:
                self.assertNotIn(value, text, name)
            self.assertNotIn(request.target_id, text, name)

    def test_page_spec_copy_has_only_component_entry_delta_and_preserves_source(self) -> None:
        source = deepcopy(self.page_spec.to_dict())
        component_id = self.page_spec.components[0].component_id
        request = FaultMutationRequest("dev-case-page", "page_spec_component_removed", component_id)
        first_dir = self.output("first")
        second_dir = self.output("second")
        first = build_fault_copy(request, self.page_spec, first_dir)
        second = build_fault_copy(request, self.page_spec, second_dir)
        self.assertEqual(first.runtime_record.to_dict(), second.runtime_record.to_dict())
        self.assertEqual(tree_bytes(first_dir), tree_bytes(second_dir))
        self.assert_runtime_metadata_blind(first_dir, request)
        actual = json_object(first_dir / "artifact/page_spec_fault.json")
        expected = deepcopy(source)
        expected["components"] = [item for item in source["components"] if item["component_id"] != component_id]
        self.assertEqual(expected, actual)
        self.assertIn(component_id, json.dumps(actual, ensure_ascii=False))
        self.assertEqual(source, self.page_spec.to_dict())
        artifact = (first_dir / "artifact/page_spec_fault.json").read_bytes()
        self.assertEqual(first.runtime_record.mutated_sha256, canonical_sha256({"files": [{
            "path": "page_spec_fault.json", "size": len(artifact),
            "sha256": sha256(artifact).hexdigest(),
        }]}))
        with self.assertRaisesRegex(FaultMutationError, "empty"):
            build_fault_copy(request, self.page_spec, first_dir)

    def test_detector_input_ready_is_pinned_false(self) -> None:
        component_id = self.page_spec.components[0].component_id
        request = FaultMutationRequest("dev-case-ready", "page_spec_component_removed", component_id)
        record = build_fault_copy(request, self.page_spec, self.output("ready")).runtime_record
        self.assertIs(record.detector_input_ready, False)
        self.assertIs(record.to_dict()["detector_input_ready"], False)
        with self.assertRaisesRegex(FaultMutationError, "not detector-ready"):
            replace(record, detector_input_ready=True).validate()

    def test_injector_audit_is_separate_and_binds_runtime_record(self) -> None:
        component_id = self.page_spec.components[0].component_id
        request = FaultMutationRequest("dev-case-audit", "page_spec_component_removed", component_id)
        build = build_fault_copy(request, self.page_spec, self.output("runtime"))
        audit = build_injector_mutation_audit(build)
        write_injector_mutation_audit(audit, self.output("audit-first"))
        write_injector_mutation_audit(audit, self.output("audit-second"))
        self.assertEqual(tree_bytes(self.output("audit-first")), tree_bytes(self.output("audit-second")))
        audit_text = (self.output("audit-first") / "injector_mutation_audit.json").read_text(encoding="utf-8")
        self.assertIn(request.mutation_kind, audit_text)
        self.assertIn(request.target_id, audit_text)
        self.assertIn('"injector_only":true', audit_text)
        self.assert_runtime_metadata_blind(self.output("runtime"), request)
        self.assertIn(request.target_id, (self.output("runtime") / "artifact/page_spec_fault.json").read_text(encoding="utf-8"))

    def test_inspector_copy_has_only_trace_relation_delta_and_preserves_fallback(self) -> None:
        source = json.loads(canonical_json_bytes(self.fact_set.to_dict()).decode("utf-8"))
        trace_link_id = self.fact_set.trace_links[0].trace_link_id
        request = FaultMutationRequest("dev-case-facts", "inspector_trace_relation_removed", trace_link_id)
        output = self.output("facts")
        record = build_fault_copy(request, self.fact_set, output).runtime_record
        self.assert_runtime_metadata_blind(output, request)
        actual = json_object(output / "artifact/inspector_fact_set_fault.json")
        expected = deepcopy(source)
        expected["trace_links"] = [item for item in source["trace_links"] if item["trace_link_id"] != trace_link_id]
        linked = [item for item in source["facts"] if trace_link_id in item["trace_link_ids"]]
        self.assertEqual(len(linked), 1)
        expected["facts"] = [
            {**item, "trace_link_ids": [value for value in item["trace_link_ids"] if value != trace_link_id]}
            if trace_link_id in item["trace_link_ids"] else item
            for item in source["facts"]
        ]
        self.assertEqual(expected, actual)
        self.assertTrue(any(item["disposition"] == "fallback" for item in actual["facts"]))
        self.assertEqual(source, json.loads(canonical_json_bytes(self.fact_set.to_dict()).decode("utf-8")))
        self.assertNotIn("changed_path", record.to_dict())

    def test_render_copy_has_exact_dom_binding_and_manifest_hash_delta(self) -> None:
        source_files = tree_bytes(self.render.output_dir)
        component_id = self.page_spec.components[0].component_id
        request = FaultMutationRequest("dev-case-render", "render_component_stable_id_tampered", component_id)
        output = self.output("render")
        record = build_fault_copy(request, self.render, output).runtime_record
        self.assert_runtime_metadata_blind(output, request)
        copied_root = output / "artifact/render"
        copied_files = tree_bytes(copied_root)
        self.assertEqual(set(source_files), set(copied_files))
        token = 'data-component-id="' + component_id + '"'
        replacement = 'data-component-id="fault-' + sha256(component_id.encode("utf-8")).hexdigest()[:20] + '"'
        self.assertEqual(copied_files["index.html"], source_files["index.html"].replace(token.encode("utf-8"), replacement.encode("utf-8"), 1))
        self.assertEqual(copied_files["styles.css"], source_files["styles.css"])
        self.assertEqual(copied_files["app.js"], source_files["app.js"])
        source_manifest = json.loads(source_files["render_manifest.json"].decode("utf-8"))
        expected_manifest = deepcopy(source_manifest)
        for entry in expected_manifest["files"]:
            if entry["name"] == "index.html":
                entry["sha256"] = sha256(copied_files["index.html"]).hexdigest()
        self.assertEqual(expected_manifest, json.loads(copied_files["render_manifest.json"].decode("utf-8")))
        copied_render = RenderResult(
            self.render.page_id, copied_root, copied_root / "index.html", copied_root / "styles.css",
            copied_root / "app.js", copied_root / "render_manifest.json",
        )
        view = project_requirement_view(self.context)
        plan = compile_acceptance_plan(view)
        binding = compile_acceptance_binding(view, plan, self.page_spec, copied_render)
        self.assertTrue(any(item.terminal_stage == "render_binding" for item in binding.bindings))
        self.assertEqual(record.copy_location, "artifact/render/index.html")
        self.assertEqual(source_files, tree_bytes(self.render.output_dir))

    def test_package_copy_has_one_manifest_entry_delta_and_preserves_source(self) -> None:
        source_files = tree_bytes(self.package.package_dir)
        for field in ("path", "sha256"):
            with self.subTest(field=field):
                target_path = "page/index.html"
                kind = "package_manifest_path_tampered" if field == "path" else "package_manifest_sha256_tampered"
                request = FaultMutationRequest("dev-case-package", kind, target_path, field)
                output = self.output("package-" + field)
                record = build_fault_copy(request, self.package, output).runtime_record
                self.assert_runtime_metadata_blind(output, request)
                copied_root = output / "artifact/result_package"
                copied_files = tree_bytes(copied_root)
                self.assertEqual(set(source_files), set(copied_files))
                for name in source_files:
                    if name != "package_manifest.json":
                        self.assertEqual(source_files[name], copied_files[name], name)
                source_manifest = json.loads(source_files["package_manifest.json"].decode("utf-8"))
                actual_manifest = json.loads(copied_files["package_manifest.json"].decode("utf-8"))
                expected_manifest = deepcopy(source_manifest)
                matches = [item for item in expected_manifest["files"] if item["path"] == target_path]
                self.assertEqual(len(matches), 1)
                if field == "path":
                    matches[0]["path"] = "fault-missing/" + target_path
                else:
                    matches[0]["sha256"] = "0" * 64
                self.assertEqual(expected_manifest, actual_manifest)
                copied = replace(self.package, package_dir=copied_root)
                with self.assertRaises(ResultPackageError):
                    copied.validate()
                self.assertNotEqual(record.source_sha256, record.mutated_sha256)
        self.assertEqual(source_files, tree_bytes(self.package.package_dir))

    def test_unknown_bad_target_nonempty_and_symlink_outputs_fail_closed(self) -> None:
        component_id = self.page_spec.components[0].component_id
        with self.assertRaisesRegex(FaultMutationError, "unknown"):
            build_fault_copy(
                FaultMutationRequest("dev-case", "unregistered", component_id),
                self.page_spec, self.output("unknown"),
            )
        with self.assertRaises(FaultMutationError):
            build_fault_copy(
                FaultMutationRequest("dev-case", "package_manifest_path_tampered", "../escape.json", "path"),
                self.package, self.output("traversal"),
            )
        self.assertFalse(self.output("traversal").exists())
        occupied = self.output("occupied")
        occupied.mkdir(parents=True)
        (occupied / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(FaultMutationError, "empty"):
            build_fault_copy(
                FaultMutationRequest("dev-case", "page_spec_component_removed", component_id),
                self.page_spec, occupied,
            )
        self.assertEqual((occupied / "keep.txt").read_text(encoding="utf-8"), "keep")

        symlink_target = self.output("symlink-target")
        symlink_target.mkdir(parents=True)
        symlink_output = self.output("symlink-output")
        try:
            os.symlink(symlink_target, symlink_output, target_is_directory=True)
            symlink_context = None
        except OSError:
            original_is_symlink = Path.is_symlink
            symlink_context = patch(
                "req2web_faults.mutation.Path.is_symlink",
                autospec=True,
                side_effect=lambda candidate: candidate == symlink_output or original_is_symlink(candidate),
            )
        request = FaultMutationRequest("dev-case", "page_spec_component_removed", component_id)
        if symlink_context is None:
            with self.assertRaisesRegex(FaultMutationError, "symlink"):
                build_fault_copy(request, self.page_spec, symlink_output)
        else:
            with patch(
                "req2web_faults.mutation._reject_symlink_ancestors",
                side_effect=FaultMutationError("symlinked output paths are not allowed"),
            ) as symlink_guard, self.assertRaisesRegex(FaultMutationError, "symlink"):
                build_fault_copy(request, self.page_spec, symlink_output)
            symlink_guard.assert_called_once()


if __name__ == "__main__":
    import unittest
    unittest.main()
