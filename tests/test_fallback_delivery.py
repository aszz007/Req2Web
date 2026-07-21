from __future__ import annotations

from dataclasses import replace
import ast
import json
from pathlib import Path
import shutil
import sys
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_agent import UseCase  # noqa: E402
from req2web_faults.fallback_delivery import (  # noqa: E402
    FallbackDeliveryError,
    FallbackDeliveryReport,
    FrozenG0FallbackRecord,
    _canonical_sha256,
    deliver_frozen_g0_fallback,
    freeze_g0_fallback_package,
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
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from test_guided_page_spec import build_context  # noqa: E402


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class FallbackDeliveryTest(TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_fallback_delivery" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root.parent, ignore_errors=True)

    def _package(self, name: str = "source"):
        context = build_context()
        guidance = RetrievalGuidanceBuilder().build(context)
        baseline = PageSpecBuilder().build(context)
        guided = RetrievalGuidedPageSpecBuilder().build(context, guidance)
        ablations = {
            role: RetrievalGuidedPageSpecBuilder().build(context, guidance, disabled_roles=(role,))
            for role in ROLE_ORDER
        }
        render = DeterministicPageRenderer().render(guided.page_spec, self.root / name / "render")
        consistency = MinimalConsistencyChecker().check(guided.page_spec, render)
        influence = RetrievalInfluenceChecker().check(context, guidance, baseline, guided, ablations, render)
        package = DeterministicRetrievalEnhancedResultPackager().package(
            context, guidance, guided, guided.page_spec, render, consistency, influence,
            self.root / name / "package",
        )
        package.validate()
        return package, (context, guided.page_spec, render, consistency)

    def _freeze(self, label: str = "snapshot"):
        package, artifacts = self._package(label + "-source")
        source_bytes = tree_bytes(package.package_dir)
        snapshot = self.root / label
        record = freeze_g0_fallback_package("case-ecommerce-001", package, snapshot)
        return package, artifacts, source_bytes, snapshot, record

    def _forged_record(self, record: FrozenG0FallbackRecord) -> FrozenG0FallbackRecord:
        seed = replace(record, case_id="forged-case", record_id="", record_sha256="0" * 64)
        digest = _canonical_sha256(seed.to_payload())
        return replace(seed, record_id="frozen-g0-fallback-" + digest[:20], record_sha256=digest)

    def _forged_report(self, report: FallbackDeliveryReport) -> FallbackDeliveryReport:
        seed = replace(report, case_id="forged-case", report_id="", report_sha256="0" * 64)
        digest = _canonical_sha256(seed.to_payload())
        return replace(seed, report_id="fallback-delivery-" + digest[:20], report_sha256=digest)

    def test_freeze_and_delivery_are_canonical_byte_exact_and_source_immutable(self) -> None:
        package, _, source_bytes, snapshot, record = self._freeze("first")
        self.assertEqual(source_bytes, tree_bytes(package.package_dir))
        record.validate()
        record.validate_against(snapshot)
        second_package, _ = self._package("second-source")
        second = self.root / "second"
        second_record = freeze_g0_fallback_package("case-ecommerce-001", second_package, second)
        self.assertEqual(record.to_dict(), second_record.to_dict())
        self.assertEqual(tree_bytes(snapshot), tree_bytes(second))
        delivery = self.root / "delivery"
        report = deliver_frozen_g0_fallback(snapshot, record, delivery)
        report.validate()
        report.validate_against(snapshot, delivery / "result_package", record)
        self.assertEqual(tree_bytes(snapshot / "result_package"), tree_bytes(delivery / "result_package"))
        self.assertEqual(report.status, "fallback_delivery")
        self.assertEqual(report.delivery_source, "g0_frozen_fallback")
        self.assertNotIn("Qwen", str(report.to_dict()))
        self.assertNotIn("model_success", str(report.to_dict()))

    def test_guards_reject_v1_existing_overlap_traversal_and_symlinks(self) -> None:
        package, artifacts = self._package("guard-source")
        v1 = DeterministicResultPackager().package(
            artifacts[0], artifacts[1], artifacts[2], artifacts[3], self.root / "v1",
        )
        with self.assertRaisesRegex(FallbackDeliveryError, "RetrievalEnhancedResultPackage"):
            freeze_g0_fallback_package("case-v1", v1, self.root / "v1-snapshot")
        occupied = self.root / "occupied"
        occupied.mkdir()
        with self.assertRaisesRegex(FallbackDeliveryError, "new path"):
            freeze_g0_fallback_package("case", package, occupied)
        with self.assertRaisesRegex(FallbackDeliveryError, "source-disjoint"):
            freeze_g0_fallback_package("case", package, package.package_dir / "snapshot")
        with self.assertRaisesRegex(FallbackDeliveryError, "source-disjoint"):
            freeze_g0_fallback_package("case", package, package.package_dir.parent)
        with self.assertRaisesRegex(FallbackDeliveryError, "traverse"):
            freeze_g0_fallback_package("case", package, self.root / "traversal" / ".." / "escaped")
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(FallbackDeliveryError, "symlink"):
                freeze_g0_fallback_package("case", package, self.root / "symlink")
        _, _, _, snapshot, record = self._freeze("delivery-guard")
        with self.assertRaisesRegex(FallbackDeliveryError, "source-disjoint"):
            deliver_frozen_g0_fallback(snapshot, record, snapshot / "delivery")

    def test_forgery_tamper_and_post_commit_failures_fail_closed(self) -> None:
        _, _, _, snapshot, record = self._freeze("tamper")
        forged = self._forged_record(record)
        forged.validate()
        with self.assertRaisesRegex(FallbackDeliveryError, "does not match expected|bytes are not canonical"):
            forged.validate_against(snapshot)
        package_file = snapshot / "result_package" / "page" / "app.js"
        package_file.write_bytes(package_file.read_bytes() + b"\n// tamper\n")
        with self.assertRaisesRegex(FallbackDeliveryError, "snapshot root file inventory|fails the ResultPackage v2 validator"):
            record.validate_against(snapshot)
        _, _, _, clean_snapshot, clean_record = self._freeze("clean")
        delivery = self.root / "delivery"
        report = deliver_frozen_g0_fallback(clean_snapshot, clean_record, delivery)
        forged_report = self._forged_report(report)
        forged_report.validate()
        with self.assertRaisesRegex(FallbackDeliveryError, "does not match expected report|bytes are not canonical"):
            forged_report.validate_against(clean_snapshot, delivery / "result_package", clean_record)
        failing_output = self.root / "post-commit-delivery"
        original = FallbackDeliveryReport.validate_against
        calls = {"count": 0}
        def fail_after_commit(self, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise FallbackDeliveryError("injected post-commit failure")
            return original(self, *args, **kwargs)
        with patch.object(FallbackDeliveryReport, "validate_against", new=fail_after_commit):
            with self.assertRaisesRegex(FallbackDeliveryError, "injected post-commit"):
                deliver_frozen_g0_fallback(clean_snapshot, clean_record, failing_output)
        self.assertFalse(failing_output.exists())
        self.assertEqual([], list(self.root.glob(".post-commit-delivery.staging-*")))

    def test_extra_missing_and_provenance_mismatch_are_rejected(self) -> None:
        _, _, _, snapshot, record = self._freeze("integrity")
        (snapshot / "result_package" / "extra.txt").write_text("extra", encoding="utf-8")
        with self.assertRaisesRegex(FallbackDeliveryError, "snapshot root file inventory|fails the ResultPackage v2 validator"):
            record.validate_against(snapshot)
        _, _, _, clean_snapshot, clean_record = self._freeze("provenance")
        altered = replace(clean_record, provenance=replace(clean_record.provenance, page_spec_id="wrong"), record_id="", record_sha256="0" * 64)
        digest = _canonical_sha256(altered.to_payload())
        altered = replace(altered, record_id="frozen-g0-fallback-" + digest[:20], record_sha256=digest)
        altered.validate()
        with self.assertRaisesRegex(FallbackDeliveryError, "does not match expected|bytes are not canonical"):
            altered.validate_against(clean_snapshot)

    def test_production_module_isolated_from_builders_models_and_raw_data(self) -> None:
        source = (ROOT / "src" / "req2web_faults" / "fallback_delivery.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Name):
                names.append(node.id)
        forbidden_import_fragments = ("guided_builder", "provider", "qwen", "mutation", "injector", "evaluator")
        for value in forbidden_import_fragments:
            self.assertFalse(any(value in item.lower() for item in imports), value)
        self.assertEqual("req2web_generation.result_package_v2", imports[-1])


    def test_source_v2_manifest_and_tree_mismatches_are_rejected_without_output(self) -> None:
        mutations = {
            "schema": lambda manifest: manifest.__setitem__("schema_version", "req2web.result.package.v1"),
            "package-id": lambda manifest: manifest.__setitem__("package_id", "wrong-package"),
            "path": lambda manifest: manifest["files"][0].__setitem__("path", "page/not-index.html"),
            "role": lambda manifest: manifest["files"][0].__setitem__("role", "wrong-role"),
            "size": lambda manifest: manifest["files"][0].__setitem__("size", 0),
            "sha": lambda manifest: manifest["files"][0].__setitem__("sha256", "0" * 64),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                package, _ = self._package("manifest-" + label)
                manifest_path = package.package_dir / "package_manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutate(manifest)
                manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
                output = self.root / ("rejected-" + label)
                with self.assertRaisesRegex(FallbackDeliveryError, "ResultPackage v2 validator"):
                    freeze_g0_fallback_package("case", package, output)
                self.assertFalse(output.exists())
                self.assertEqual([], list(output.parent.glob("." + output.name + ".staging-*")))
        package, _ = self._package("source-tree-tamper")
        source_file = package.package_dir / "page" / "app.js"
        source_file.write_bytes(source_file.read_bytes() + b"\n// source tamper\n")
        with self.assertRaisesRegex(FallbackDeliveryError, "ResultPackage v2 validator"):
            freeze_g0_fallback_package("case", package, self.root / "source-tamper")
        package, _ = self._package("source-tree-missing")
        (package.package_dir / "page" / "app.js").unlink()
        with self.assertRaisesRegex(FallbackDeliveryError, "ResultPackage v2 validator"):
            freeze_g0_fallback_package("case", package, self.root / "source-missing")

    def test_freeze_post_commit_validation_failure_removes_generated_snapshot(self) -> None:
        package, _ = self._package("freeze-post-commit")
        output = self.root / "freeze-post-commit-output"
        original = FrozenG0FallbackRecord.validate_against
        calls = {"count": 0}
        def fail_after_commit(self, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise FallbackDeliveryError("injected frozen post-commit failure")
            return original(self, *args, **kwargs)
        with patch.object(FrozenG0FallbackRecord, "validate_against", new=fail_after_commit):
            with self.assertRaisesRegex(FallbackDeliveryError, "injected frozen post-commit"):
                freeze_g0_fallback_package("case", package, output)
        self.assertFalse(output.exists())
        self.assertEqual([], list(self.root.glob(".freeze-post-commit-output.staging-*")))

    def test_delivered_package_tamper_and_wrong_snapshot_are_rejected(self) -> None:
        _, _, _, snapshot, record = self._freeze("delivery-verify")
        delivery = self.root / "delivery-verify-output"
        report = deliver_frozen_g0_fallback(snapshot, record, delivery)
        delivered_file = delivery / "result_package" / "page" / "app.js"
        delivered_file.write_bytes(delivered_file.read_bytes() + b"\n// delivered tamper\n")
        with self.assertRaisesRegex(FallbackDeliveryError, "snapshot root file inventory|fails the ResultPackage v2 validator"):
            report.validate_against(snapshot, delivery / "result_package", record)
        with self.assertRaisesRegex(FallbackDeliveryError, "snapshot_dir"):
            deliver_frozen_g0_fallback(self.root / "not-a-snapshot", record, self.root / "wrong-snapshot-output")
    def test_wrapper_record_report_bytes_and_root_inventory_are_exact(self) -> None:
        _, _, _, snapshot, record = self._freeze("wrapper-record")
        record_path = snapshot / "frozen_g0_fallback_record.json"
        record_path.write_text(json.dumps(json.loads(record_path.read_text(encoding="utf-8")), indent=2), encoding="utf-8")
        with self.assertRaisesRegex(FallbackDeliveryError, "record.*bytes are not canonical"):
            record.validate_against(snapshot)

        _, _, _, extra_snapshot, extra_record = self._freeze("wrapper-snapshot-extra")
        (extra_snapshot / "extra.txt").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(FallbackDeliveryError, "snapshot root file inventory"):
            extra_record.validate_against(extra_snapshot)

        _, _, _, clean_snapshot, clean_record = self._freeze("wrapper-delivery")
        delivery = self.root / "wrapper-delivery-output"
        report = deliver_frozen_g0_fallback(clean_snapshot, clean_record, delivery)
        report_path = delivery / "fallback_delivery_report.json"
        report_path.write_text(json.dumps(json.loads(report_path.read_text(encoding="utf-8")), indent=2), encoding="utf-8")
        with self.assertRaisesRegex(FallbackDeliveryError, "report.*bytes are not canonical"):
            report.validate_against(clean_snapshot, delivery / "result_package", clean_record)

        clean_delivery = self.root / "wrapper-delivery-extra-output"
        clean_report = deliver_frozen_g0_fallback(clean_snapshot, clean_record, clean_delivery)
        (clean_delivery / "extra.txt").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(FallbackDeliveryError, "delivery root file inventory"):
            clean_report.validate_against(clean_snapshot, clean_delivery / "result_package", clean_record)
if __name__ == "__main__":
    import unittest
    unittest.main()
