from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
from unittest import TestCase
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_generation import (  # noqa: E402
    DeterministicDeliverySidecarBuilder,
    DeterministicPageRenderer,
    DeterministicRetrievalEnhancedResultPackager,
    DeliverySidecarError,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
    build_delivery_sidecar_batch,
)
from req2web_generation.delivery_sidecar import _safe_posix_path  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from test_guided_page_spec import build_context  # noqa: E402


class DeliverySidecarTest(TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_delivery_sidecar" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root.parent, ignore_errors=True)

    def _package(self, *, asset_refs: bool = False, injected_title: str | None = None):
        context = build_context(constraints=["输入错误时显示错误并允许修改输入后重试"])
        if asset_refs:
            refs = [
                {"kind": "screenshot", "uri": "fixtures/ui.png"},
                {"kind": "semantic_image", "uri": "fixtures/ui-semantic.png"},
                {"kind": "view_hierarchy", "uri": "fixtures/ui.json"},
                {"kind": "semantic_annotation", "uri": "fixtures/ui-annotation.json"},
            ]
            context.retrieval_results["ui_reference"][0]["references"] = refs
        if injected_title is not None:
            context.retrieval_results["ui_reference"][0]["title"] = injected_title
        guidance = RetrievalGuidanceBuilder().build(context)
        baseline = PageSpecBuilder().build(context)
        guided = RetrievalGuidedPageSpecBuilder().build(context, guidance)
        ablations = {role: RetrievalGuidedPageSpecBuilder().build(context, guidance, disabled_roles=(role,)) for role in ROLE_ORDER}
        render = DeterministicPageRenderer().render(guided.page_spec, self.root / "render")
        consistency = MinimalConsistencyChecker().check(guided.page_spec, render)
        influence = RetrievalInfluenceChecker().check(context, guidance, baseline, guided, ablations, render)
        package = DeterministicRetrievalEnhancedResultPackager().package(context, guidance, guided, guided.page_spec, render, consistency, influence, self.root / "package")
        package.validate()
        return package

    def _reference_root(self) -> Path:
        root = self.root / "reference-root"
        fixture = root / "fixtures"
        fixture.mkdir(parents=True)
        (fixture / "ui.png").write_bytes(b"png-source-one")
        (fixture / "ui-semantic.png").write_bytes(b"png-source-two")
        (fixture / "ui.json").write_text('{"ignored":true}', encoding="utf-8")
        (fixture / "ui-annotation.json").write_text('{"ignored":true}', encoding="utf-8")
        return root

    def test_reference_only_sidecar_has_fixed_offline_files_and_storyboard(self) -> None:
        package = self._package()
        result = DeterministicDeliverySidecarBuilder().build(package.package_dir, self.root / "sidecar")
        result.validate()
        paths = sorted(path.relative_to(result.output_dir).as_posix() for path in result.output_dir.rglob("*") if path.is_file())
        self.assertEqual(paths, ["delivery_manifest.json", "index.html", "storyboard.json", "styles.css", "ui_references.json"])
        refs = json.loads((result.output_dir / "ui_references.json").read_text(encoding="utf-8"))
        self.assertTrue(all(item["delivery_mode"] == "reference_only" for item in refs["references"]))
        self.assertEqual(refs["source_statement"], "参考图来自检索数据，只作设计参考，不代表生成页面截图或视觉理解。")
        storyboard = json.loads((result.output_dir / "storyboard.json").read_text(encoding="utf-8"))
        self.assertTrue(storyboard["semantics"]["scenario_boards_not_single_global_journey"])
        self.assertGreater(result.step_count, 0)
        scenario_names = {item["scenario"] for board in storyboard["use_cases"] for item in board["scenarios"]}
        self.assertTrue({"normal", "error_entry", "recovery"}.issubset(scenario_names))
        html = (result.output_dir / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("https://", html)

    def test_explicit_asset_root_copies_only_allowed_images_and_dedupes_uri(self) -> None:
        package = self._package(asset_refs=True)
        result = DeterministicDeliverySidecarBuilder().build(package.package_dir, self.root / "sidecar", self._reference_root())
        refs = json.loads((result.output_dir / "ui_references.json").read_text(encoding="utf-8"))["references"]
        copied = [item for item in refs if item["delivery_mode"] == "packaged_image"]
        self.assertEqual(len(copied), 2)
        self.assertTrue(all((result.output_dir / item["local_file"]).is_file() for item in copied))
        self.assertTrue(all(item["delivery_mode"] == "reference_only" for item in refs if item["reference_kind"] in {"view_hierarchy", "semantic_annotation"}))
        self.assertEqual(result.packaged_image_count, 2)

    def test_rejects_v1_or_tampered_v2_input_before_output(self) -> None:
        invalid = self.root / "v1"; invalid.mkdir(); (invalid / "package_manifest.json").write_text('{"schema_version":"req2web.result.package.v1"}', encoding="utf-8")
        with self.assertRaisesRegex(DeliverySidecarError, "identity|v2 package"):
            DeterministicDeliverySidecarBuilder().build(invalid, self.root / "out")
        package = self._package()
        path = package.package_dir / "internal/page_spec.json"
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(DeliverySidecarError):
            DeterministicDeliverySidecarBuilder().build(package.package_dir, self.root / "tampered")
        self.assertFalse((self.root / "tampered" / "delivery_manifest.json").exists())

    def test_paths_external_traversal_and_symlink_escape_are_refused(self) -> None:
        for value in ("/absolute/image.png", "../escape.png", "https://example.test/image.png", "C:/escape.png", "folder\\image.png"):
            with self.subTest(value=value), self.assertRaises(DeliverySidecarError):
                _safe_posix_path(value, "test URI")
        root = self._reference_root()
        outside = self.root / "outside.png"; outside.write_bytes(b"outside")
        link = root / "fixtures" / "link.png"; link.write_bytes(b"placeholder")
        with patch("pathlib.Path.is_symlink", autospec=True, side_effect=lambda path: path.name == "link.png"):
            with self.assertRaisesRegex(DeliverySidecarError, "symlink"):
                DeterministicDeliverySidecarBuilder._safe_asset_path(root, "fixtures/link.png")

    def test_missing_declared_image_degrades_to_reference_only(self) -> None:
        package = self._package(asset_refs=True)
        root = self._reference_root(); (root / "fixtures" / "ui-semantic.png").unlink()
        result = DeterministicDeliverySidecarBuilder().build(package.package_dir, self.root / "sidecar", root)
        refs = json.loads((result.output_dir / "ui_references.json").read_text(encoding="utf-8"))["references"]
        semantic = next(item for item in refs if item["reference_kind"] == "semantic_image")
        self.assertEqual(semantic["delivery_mode"], "reference_only")

    def test_html_escape_and_reference_kind_extension_mismatch_degrade_safely(self) -> None:
        package = self._package(asset_refs=True, injected_title='<img src=x onerror="bad">')
        context = json.loads((package.package_dir / "internal/agent_context.json").read_text(encoding="utf-8"))
        self.assertEqual(context["retrieval_results"]["ui_reference"][0]["title"], '<img src=x onerror="bad">')
        root = self._reference_root()
        result = DeterministicDeliverySidecarBuilder().build(package.package_dir, self.root / "sidecar", root)
        html = (result.output_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn("&lt;img src=x onerror=&quot;bad&quot;&gt;", html)
        self.assertNotIn('<img src=x onerror="bad">', html)
        refs = json.loads((result.output_dir / "ui_references.json").read_text(encoding="utf-8"))["references"]
        self.assertEqual(next(item for item in refs if item["reference_kind"] == "view_hierarchy")["delivery_mode"], "reference_only")

    def test_nonempty_destination_manifest_tamper_and_atomic_failure_boundary(self) -> None:
        package = self._package()
        occupied = self.root / "occupied"; occupied.mkdir(); (occupied / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(DeliverySidecarError, "refusing to overwrite"):
            DeterministicDeliverySidecarBuilder().build(package.package_dir, occupied)
        result = DeterministicDeliverySidecarBuilder().build(package.package_dir, self.root / "sidecar")
        manifest = json.loads((result.output_dir / "delivery_manifest.json").read_text(encoding="utf-8")); manifest["files"][0]["sha256"] = "0" * 64
        (result.output_dir / "delivery_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(DeliverySidecarError):
            result.validate()

    def test_atomic_publish_failure_leaves_no_manifest(self) -> None:
        package = self._package()
        destination = self.root / "failed"
        with patch("req2web_generation.delivery_sidecar.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                DeterministicDeliverySidecarBuilder().build(package.package_dir, destination)
        self.assertFalse((destination / "delivery_manifest.json").exists())
        self.assertFalse(list(self.root.glob(".failed.staging-*")))

    def test_asset_change_during_staging_is_rejected(self) -> None:
        changed = Mock(); changed.read_bytes.return_value = b"changed"
        references = [{"delivery_mode": "packaged_image", "local_file": "assets/a.png", "original_uri": "fixtures/a.png"}]
        with patch.object(DeterministicDeliverySidecarBuilder, "_safe_asset_path", return_value=changed):
            with self.assertRaisesRegex(DeliverySidecarError, "changed while staging"):
                DeterministicDeliverySidecarBuilder._verify_asset_sources({"assets/a.png": b"original"}, self.root, references)

    def test_cross_directory_determinism_and_no_absolute_path_leak(self) -> None:
        package = self._package(asset_refs=True)
        reference_root = self._reference_root()
        first = DeterministicDeliverySidecarBuilder().build(package.package_dir, self.root / "first", reference_root)
        second = DeterministicDeliverySidecarBuilder().build(package.package_dir, self.root / "second", reference_root)
        files = sorted(path.relative_to(first.output_dir).as_posix() for path in first.output_dir.rglob("*") if path.is_file())
        self.assertEqual(files, sorted(path.relative_to(second.output_dir).as_posix() for path in second.output_dir.rglob("*") if path.is_file()))
        for path in files:
            self.assertEqual((first.output_dir / path).read_bytes(), (second.output_dir / path).read_bytes(), path)
            self.assertNotIn(str(ROOT).encode("utf-8"), (first.output_dir / path).read_bytes())

    def test_batch_builds_aggregate_report_without_package_mutation(self) -> None:
        package = self._package()
        packages = self.root / "packages"; packages.mkdir(); shutil.copytree(package.package_dir, packages / "case-a")
        report = build_delivery_sidecar_batch(packages, self.root / "batch", expected_count=1)
        self.assertEqual(report["package_count"], 1)
        self.assertEqual(report["aggregate"]["packaged_image"], 0)
        self.assertTrue((self.root / "batch" / "aggregate_manifest.json").is_file())
        self.assertTrue((packages / "case-a" / "package_manifest.json").is_file())


if __name__ == "__main__":
    import unittest
    unittest.main()
