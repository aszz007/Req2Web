from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
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
import req2web_faults.bundle as bundle_module  # noqa: E402
from req2web_acceptance import (  # noqa: E402
    compile_acceptance_binding,
    compile_acceptance_plan,
    project_requirement_view,
)
from req2web_faults import (  # noqa: E402
    BlindedFaultBundleSource,
    FaultBundleError,
    assemble_blinded_fault_bundle,
    load_blinded_fault_bundle,
    validate_blinded_bundle_inventory_parity,
)
from req2web_faults.mutation import (  # noqa: E402
    FaultMutationRequest,
    build_fault_copy,
    canonical_json_bytes,
    canonical_sha256,
)
from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    DeterministicResultPackager,
    DeterministicRetrievalEnhancedResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
from req2web_generation.renderer import RenderResult  # noqa: E402
from req2web_inspector import project_g0_inspector_facts  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from test_guided_page_spec import build_context  # noqa: E402


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class FaultBundleTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = ROOT / "tests" / ".tmp_fault_bundle"
        shutil.rmtree(cls.root, ignore_errors=True)
        cls.root.mkdir(parents=True)
        cls.context = build_context()
        cls.view = project_requirement_view(cls.context)
        cls.plan = compile_acceptance_plan(cls.view)
        guidance = RetrievalGuidanceBuilder().build(cls.context)
        guided = RetrievalGuidedPageSpecBuilder().build(cls.context, guidance)
        cls.page_spec = guided.page_spec
        ablations = {
            role: RetrievalGuidedPageSpecBuilder().build(cls.context, guidance, disabled_roles=(role,))
            for role in ROLE_ORDER
        }
        cls.render = DeterministicPageRenderer().render(cls.page_spec, cls.root / "source" / "render")
        cls.binding = compile_acceptance_binding(cls.view, cls.plan, cls.page_spec, cls.render)
        consistency = MinimalConsistencyChecker().check(cls.page_spec, cls.render)
        cls.package_v1 = DeterministicResultPackager().package(
            cls.context,
            cls.page_spec,
            cls.render,
            consistency,
            cls.root / "source" / "package-v1",
        )
        influence = RetrievalInfluenceChecker().check(
            cls.context,
            guidance,
            PageSpecBuilder().build(cls.context),
            guided,
            ablations,
            cls.render,
        )
        cls.package = DeterministicRetrievalEnhancedResultPackager().package(
            cls.context,
            guidance,
            guided,
            cls.page_spec,
            cls.render,
            consistency,
            influence,
            cls.root / "source" / "package-v2",
        )
        cls.fact_set = project_g0_inspector_facts(guidance, guided, cls.page_spec, influence)
        cls.source = BlindedFaultBundleSource(
            case_id="dev-case-bundle",
            requirement_view=cls.view,
            acceptance_plan=cls.plan,
            acceptance_binding=cls.binding,
            page_spec=cls.page_spec,
            inspector_fact_set=cls.fact_set,
            render_result=cls.render,
            result_package=cls.package,
        )
        cls.source.validate()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def output(self, name: str) -> Path:
        destination = self.root / "output" / self._testMethodName / name
        shutil.rmtree(destination, ignore_errors=True)
        return destination

    def fragment(self, name: str, request: FaultMutationRequest, artifact: object) -> Path:
        destination = self.root / "fragment" / self._testMethodName / name
        shutil.rmtree(destination, ignore_errors=True)
        build_fault_copy(request, artifact, destination)
        return destination

    def test_clean_caller_provided_source_has_fixed_inventory_and_no_runtime_provenance(self) -> None:
        output = self.output("caller-provided-negative-control")
        record = assemble_blinded_fault_bundle(self.source, output)
        loaded = load_blinded_fault_bundle(output)
        self.assertEqual(record.to_dict(), loaded.to_dict())
        self.assertEqual(record.case_id, self.source.case_id)
        self.assertEqual(record.page_id, self.page_spec.page_id)
        paths = [entry.path for entry in record.files]
        self.assertEqual(paths, sorted(paths))
        self.assertIn("artifact/acceptance/acceptance_binding.json", paths)
        self.assertIn("artifact/result_package/package_manifest.json", paths)
        self.assertEqual(set(tree_bytes(output)), {"fault_case_bundle_manifest.json", *paths})
        manifest_text = (output / "fault_case_bundle_manifest.json").read_text(encoding="utf-8")
        for forbidden in (
            "mutation_kind", "changed_path", "target_artifact_kind", "target_artifact_id",
            "copy_location", "injector", "gold", "predicted", "repair", "fallback",
            "fault_copy_id", "fault_copy_record_sha256",
        ):
            self.assertNotIn(forbidden, manifest_text)
        self.assertFalse((output / "injector_mutation_audit.json").exists())
        self.assertFalse((output / "fault_gold_manifest.json").exists())
        # This source is supplied by the caller.  The assembler never claims to
        # generate a semantic negative control just because no fragment is used.
        self.assertNotIn("FaultCopyRecord", req2web_faults.__all__)
        self.assertFalse(hasattr(req2web_faults, "FaultCopyRecord"))

    def test_page_spec_and_render_fragments_replace_only_their_registered_slots(self) -> None:
        clean_dir = self.output("clean")
        clean = assemble_blinded_fault_bundle(self.source, clean_dir)
        clean_files = tree_bytes(clean_dir / "artifact")
        requests = (
            (
                "page-spec",
                FaultMutationRequest(
                    self.source.case_id,
                    "page_spec_component_removed",
                    self.page_spec.components[0].component_id,
                ),
                self.page_spec,
                {"page_spec.json"},
            ),
            (
                "render",
                FaultMutationRequest(
                    self.source.case_id,
                    "render_component_stable_id_tampered",
                    self.page_spec.components[0].component_id,
                ),
                self.render,
                {"render/index.html", "render/render_manifest.json"},
            ),
        )
        records = [clean]
        for name, request, artifact, expected_changed in requests:
            with self.subTest(slot=name):
                fragment_dir = self.fragment(name, request, artifact)
                output = self.output(name)
                record = assemble_blinded_fault_bundle(self.source, output, fragment_dir=fragment_dir)
                records.append(record)
                actual_files = tree_bytes(output / "artifact")
                changed = {path for path in clean_files if clean_files[path] != actual_files[path]}
                self.assertEqual(changed, expected_changed)
                self.assertEqual(set(clean_files), set(actual_files))
                self.assertEqual(
                    {path: clean_files[path] for path in clean_files if path not in expected_changed},
                    {path: actual_files[path] for path in actual_files if path not in expected_changed},
                )
        parity = validate_blinded_bundle_inventory_parity(records)
        self.assertEqual(parity.bundle_count, 3)
        self.assertEqual(tuple(entry.path for entry in clean.files), parity.paths)
        parity_payload = parity.to_dict()
        self.assertNotIn("bundle_id", parity_payload)
        self.assertNotIn("case_id", parity_payload)
        self.assertNotIn("fault", json.dumps(parity_payload, sort_keys=True))

    def test_inspector_and_result_package_fragments_preserve_all_other_bundle_files(self) -> None:
        clean_dir = self.output("clean-inspector-package")
        assemble_blinded_fault_bundle(self.source, clean_dir)
        clean_files = tree_bytes(clean_dir / "artifact")
        requests = (
            (
                "inspector",
                FaultMutationRequest(
                    self.source.case_id,
                    "inspector_trace_relation_removed",
                    self.fact_set.trace_links[0].trace_link_id,
                ),
                self.fact_set,
                {"inspector_fact_set.json"},
            ),
            (
                "package",
                FaultMutationRequest(
                    self.source.case_id,
                    "package_manifest_sha256_tampered",
                    "page/index.html",
                    "sha256",
                ),
                self.package,
                {"result_package/package_manifest.json"},
            ),
        )
        for name, request, artifact, expected_changed in requests:
            with self.subTest(slot=name):
                fragment_dir = self.fragment(name, request, artifact)
                output = self.output(name)
                assemble_blinded_fault_bundle(self.source, output, fragment_dir=fragment_dir)
                actual_files = tree_bytes(output / "artifact")
                changed = {path for path in clean_files if clean_files[path] != actual_files[path]}
                self.assertEqual(changed, expected_changed)
                self.assertEqual(set(clean_files), set(actual_files))
    def test_fragment_target_kind_and_identity_are_checked_after_canonical_rewrite(self) -> None:
        routes = (
            (
                "page-spec",
                FaultMutationRequest(
                    self.source.case_id,
                    "page_spec_component_removed",
                    self.page_spec.components[0].component_id,
                ),
                self.page_spec,
            ),
            (
                "inspector",
                FaultMutationRequest(
                    self.source.case_id,
                    "inspector_trace_relation_removed",
                    self.fact_set.trace_links[0].trace_link_id,
                ),
                self.fact_set,
            ),
            (
                "render",
                FaultMutationRequest(
                    self.source.case_id,
                    "render_component_stable_id_tampered",
                    self.page_spec.components[0].component_id,
                ),
                self.render,
            ),
            (
                "package",
                FaultMutationRequest(
                    self.source.case_id,
                    "package_manifest_sha256_tampered",
                    "page/index.html",
                    "sha256",
                ),
                self.package,
            ),
        )
        for name, request, artifact in routes:
            for field, bad_value in (
                ("target_artifact_kind", "wrong_registered_kind"),
                ("target_artifact_id", "wrong-canonical-identity"),
            ):
                with self.subTest(route=name, field=field):
                    fragment = self.fragment(name + "-" + field, request, artifact)
                    self._rewrite_record_fields(fragment, **{field: bad_value})
                    with self.assertRaisesRegex(FaultBundleError, field):
                        assemble_blinded_fault_bundle(
                            self.source,
                            self.output(name + "-" + field),
                            fragment_dir=fragment,
                        )

    def test_v1_package_is_rejected_and_v2_render_alignment_remains_exact(self) -> None:
        with self.assertRaisesRegex(
            FaultBundleError,
            "RetrievalEnhancedResultPackage v2",
        ):
            replace(self.source, result_package=self.package_v1).validate()

        copied_render_root = self.root / "mismatch" / self._testMethodName / "caller-render"
        shutil.copytree(self.render.output_dir, copied_render_root)
        styles_path = copied_render_root / "styles.css"
        styles_path.write_bytes(
            styles_path.read_bytes() + b"\n/* caller-provided valid mismatch */\n"
        )
        render_manifest_path = copied_render_root / "render_manifest.json"
        render_manifest = json.loads(render_manifest_path.read_text(encoding="utf-8"))
        for entry in render_manifest["files"]:
            if entry["name"] == "styles.css":
                entry["sha256"] = sha256(styles_path.read_bytes()).hexdigest()
        render_manifest_path.write_text(
            json.dumps(render_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        caller_render = RenderResult(
            page_id=self.render.page_id,
            output_dir=copied_render_root,
            index_html=copied_render_root / "index.html",
            styles_css=styles_path,
            app_js=copied_render_root / "app.js",
            render_manifest=render_manifest_path,
        )
        caller_binding = compile_acceptance_binding(
            self.view, self.plan, self.page_spec, caller_render
        )
        with self.assertRaisesRegex(FaultBundleError, "page/styles.css"):
            replace(
                self.source,
                render_result=caller_render,
                acceptance_binding=caller_binding,
            ).validate()

    def test_v2_package_uses_the_same_real_source_alignment_validation(self) -> None:
        self.source.validate()
        output = self.output("v2-source")
        record = assemble_blinded_fault_bundle(self.source, output)
        paths = {entry.path for entry in record.files}
        self.assertIn("artifact/result_package/internal/page_spec.json", paths)
        self.assertIn("artifact/result_package/internal/retrieval_influence_report.json", paths)
        self.assertEqual(load_blinded_fault_bundle(output).to_dict(), record.to_dict())

    def test_case_fragment_hash_slot_and_multiple_replacement_guards_fail_closed(self) -> None:
        request = FaultMutationRequest(
            self.source.case_id,
            "page_spec_component_removed",
            self.page_spec.components[0].component_id,
        )
        fragment = self.fragment("base", request, self.page_spec)
        mismatch = self.fragment(
            "case-mismatch",
            FaultMutationRequest(
                "other-case",
                "page_spec_component_removed",
                self.page_spec.components[0].component_id,
            ),
            self.page_spec,
        )
        with self.assertRaisesRegex(FaultBundleError, "case_id"):
            assemble_blinded_fault_bundle(self.source, self.output("case-mismatch"), fragment_dir=mismatch)

        tampered_bytes = self.fragment("bytes", request, self.page_spec)
        artifact = tampered_bytes / "artifact" / "page_spec_fault.json"
        artifact.write_bytes(artifact.read_bytes() + b" ")
        with self.assertRaisesRegex(FaultBundleError, "bytes"):
            assemble_blinded_fault_bundle(self.source, self.output("bytes"), fragment_dir=tampered_bytes)

        tampered_manifest = self.fragment("manifest", request, self.page_spec)
        manifest_path = tampered_manifest / "fault_copy_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["fault_copy_record_sha256"] = "0" * 64
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(FaultBundleError, "manifest"):
            assemble_blinded_fault_bundle(self.source, self.output("manifest"), fragment_dir=tampered_manifest)

        wrong_slot = self.fragment("wrong-slot", request, self.page_spec)
        self._rewrite_copy_location(wrong_slot, "artifact/render/index.html")
        with self.assertRaisesRegex(FaultBundleError, "target_artifact_kind"):
            assemble_blinded_fault_bundle(self.source, self.output("wrong-slot"), fragment_dir=wrong_slot)

        multiple = self.fragment("multiple", request, self.page_spec)
        extra = multiple / "artifact" / "render"
        extra.mkdir(parents=True)
        (extra / "index.html").write_bytes(b"not a registered page-spec fragment file")
        multiple_manifest_path = multiple / "fault_copy_manifest.json"
        multiple_manifest = json.loads(multiple_manifest_path.read_text(encoding="utf-8"))
        multiple_manifest["artifact_file_count"] = 2
        multiple_manifest_path.write_bytes(canonical_json_bytes(multiple_manifest))
        with self.assertRaisesRegex(FaultBundleError, "exactly one"):
            assemble_blinded_fault_bundle(self.source, self.output("multiple"), fragment_dir=multiple)

    def test_nonempty_output_path_traversal_and_symlink_guards_fail_closed(self) -> None:
        occupied = self.output("occupied")
        occupied.mkdir(parents=True)
        (occupied / "sentinel.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(FaultBundleError, "empty"):
            assemble_blinded_fault_bundle(self.source, occupied)
        self.assertEqual((occupied / "sentinel.txt").read_text(encoding="utf-8"), "preserve")

        with self.assertRaisesRegex(FaultBundleError, "traverse"):
            bundle_module._safe_relative_posix("../escape", "test path")
        unsafe_inventory = self.source.artifact_files()
        unsafe_inventory["artifact/result_package/injector_mutation_audit.json"] = b"must not be detector-visible"
        with self.assertRaisesRegex(FaultBundleError, "provenance"):
            bundle_module._validate_bundle_file_map(unsafe_inventory)
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(FaultBundleError, "symlink"):
                bundle_module._reject_symlink_ancestors(self.output("symlink") / "child")

    def _rewrite_copy_location(self, fragment_dir: Path, copy_location: str) -> None:
        self._rewrite_record_fields(fragment_dir, copy_location=copy_location)

    def _rewrite_record_fields(self, fragment_dir: Path, **changes: object) -> None:
        record_path = fragment_dir / "fault_copy_record.json"
        original = json.loads(record_path.read_text(encoding="utf-8"))
        unsigned = replace(
            bundle_module.FaultCopyRecord(**original),
            fault_copy_id="",
            **changes,
        )
        rewritten = replace(
            unsigned,
            fault_copy_id="fault-copy-" + canonical_sha256(unsigned.to_payload())[:20],
        )
        record_path.write_bytes(canonical_json_bytes(rewritten.to_dict()))
        manifest_path = fragment_dir / "fault_copy_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["fault_copy_id"] = rewritten.fault_copy_id
        manifest["fault_copy_record_sha256"] = rewritten.sha256()
        manifest_path.write_bytes(canonical_json_bytes(manifest))


if __name__ == "__main__":
    import unittest
    unittest.main()
