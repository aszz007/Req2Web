from __future__ import annotations

import ast
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
import req2web_faults.detector as detector_module  # noqa: E402
import req2web_faults.repair_executor as executor_module  # noqa: E402
from req2web_faults import (  # noqa: E402
    DETERMINISTIC_REPAIR_AUTHORIZED,
    FALLBACK_REQUIRED,
    NO_ACTION,
    NO_FAULT_DETECTED,
    RepairExecutionError,
    authorize_fault_detection_report,
    detect_blinded_fault_bundle,
    execute_authorized_deterministic_repair,
    write_repair_execution_report,
)
import test_fault_detector as detector_fixtures  # noqa: E402


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


def reseal_bundle(bundle_dir: Path) -> None:
    """Test-only rewrites for malformed detector-visible copies."""
    from req2web_faults.bundle import _make_bundle_record

    manifest_path = bundle_dir / "fault_case_bundle_manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = {
        path.relative_to(bundle_dir).as_posix(): path.read_bytes()
        for path in sorted((bundle_dir / "artifact").rglob("*"))
        if path.is_file()
    }
    record = _make_bundle_record(previous["case_id"], previous["page_id"], files)
    manifest_path.write_bytes(canonical_json_bytes(record.to_dict()))


class FaultRepairExecutorTest(TestCase):
    """Exercise M2-05b only through the existing blinded detector chain."""

    @classmethod
    def setUpClass(cls) -> None:
        detector_fixtures.FaultDetectorTest.setUpClass()
        cls.fixture = detector_fixtures.FaultDetectorTest
        cls.root = ROOT / "tests" / ".tmp_fault_repair_executor_v2"
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

    def source_report_and_authorization(self, name: str):
        bundle_dir = self.fixture.fault_dirs[name]
        report = detect_blinded_fault_bundle(bundle_dir, self.fixture.parity)
        authorization = authorize_fault_detection_report(report)
        self.assertEqual(authorization.status, DETERMINISTIC_REPAIR_AUTHORIZED)
        return bundle_dir, report, authorization

    def test_three_happy_paths_are_copy_on_write_canonical_and_post_clean(self) -> None:
        for name in ("render", "package_path", "package_sha256"):
            with self.subTest(name=name):
                bundle_dir, report, authorization = self.source_report_and_authorization(name)
                before = tree_bytes(bundle_dir)
                first_dir = self.output(name + "-first")
                second_dir = self.output(name + "-second")
                first = execute_authorized_deterministic_repair(
                    bundle_dir, self.fixture.parity, report, authorization, first_dir
                )
                second = execute_authorized_deterministic_repair(
                    bundle_dir, self.fixture.parity, report, authorization, second_dir
                )
                self.assertEqual(before, tree_bytes(bundle_dir))
                self.assertEqual(first.to_dict(), second.to_dict())
                self.assertEqual(tree_bytes(first_dir), tree_bytes(second_dir))
                self.assertEqual(first.error_code, report.predicted_error_code)
                self.assertEqual(first.actual_repair_scope, authorization.policy_allowed_scope)
                self.assertEqual(first.administrative_reseal_scope, ("fault_case_bundle_manifest.json",))
                self.assertEqual(first.post_detector_status, NO_FAULT_DETECTED)
                post = detect_blinded_fault_bundle(
                    first_dir / "repaired_bundle", self.fixture.parity
                )
                self.assertEqual(post.status, NO_FAULT_DETECTED)
                source_bundle = tree_bytes(bundle_dir)
                repaired_bundle = tree_bytes(first_dir / "repaired_bundle")
                self.assertEqual(set(source_bundle), set(repaired_bundle))
                changed = sorted(
                    path for path in source_bundle if source_bundle[path] != repaired_bundle[path]
                )
                self.assertEqual(
                    changed,
                    sorted({
                        *authorization.policy_allowed_scope,
                        "fault_case_bundle_manifest.json",
                    }),
                )
                self.assertEqual(
                    sorted(tree_bytes(first_dir / "repair_execution_report")),
                    [
                        "repair_execution_report.json",
                        "repair_execution_report_manifest.json",
                    ],
                )

    def test_source_and_output_paths_must_be_disjoint_before_staging(self) -> None:
        bundle_dir, report, authorization = self.source_report_and_authorization("render")
        cases = (
            ("same", bundle_dir),
            ("descendant", bundle_dir / "nested-repair-output"),
            ("ancestor", bundle_dir.parent),
        )
        for name, output_dir in cases:
            with self.subTest(name=name):
                before = tree_bytes(bundle_dir)
                staging = output_dir.parent / ("." + output_dir.name + ".repair-stage")
                self.assertFalse(staging.exists())
                with self.assertRaisesRegex(RepairExecutionError, "disjoint"):
                    execute_authorized_deterministic_repair(
                        bundle_dir,
                        self.fixture.parity,
                        report,
                        authorization,
                        output_dir,
                    )
                self.assertEqual(before, tree_bytes(bundle_dir))
                self.assertFalse(staging.exists())
                if name == "descendant":
                    self.assertFalse(output_dir.exists())

    def test_execution_report_validate_against_binds_real_external_evidence(self) -> None:
        bundle_dir, report, authorization = self.source_report_and_authorization("render")
        output_dir = self.output("external-binding")
        execution = execute_authorized_deterministic_repair(
            bundle_dir, self.fixture.parity, report, authorization, output_dir
        )
        repaired_dir = output_dir / "repaired_bundle"
        post_report = detect_blinded_fault_bundle(repaired_dir, self.fixture.parity)
        execution.validate_against(
            bundle_dir,
            repaired_dir,
            self.fixture.parity,
            report,
            authorization,
            post_report,
        )

        forged = replace(execution, source_bundle_id="forged-source-bundle")
        forged = replace(
            forged,
            execution_id="repair-execution-" + executor_module._canonical_sha256(
                forged.to_payload()
            )[:20],
        )
        forged.validate()
        with self.assertRaisesRegex(RepairExecutionError, "source_bundle_id"):
            forged.validate_against(
                bundle_dir,
                repaired_dir,
                self.fixture.parity,
                report,
                authorization,
                post_report,
            )

        with self.assertRaisesRegex(RepairExecutionError, "pre_report"):
            execution.validate_against(
                self.fixture.fault_dirs["package_path"],
                repaired_dir,
                self.fixture.parity,
                report,
                authorization,
                post_report,
            )
        with self.assertRaisesRegex(RepairExecutionError, "post_report"):
            execution.validate_against(
                bundle_dir,
                self.fixture.fault_dirs["package_path"],
                self.fixture.parity,
                report,
                authorization,
                post_report,
            )

    def test_executor_rejects_under_over_disjoint_and_partial_scope_even_if_binding_is_bypassed(self) -> None:
        bundle_dir, report, authorization = self.source_report_and_authorization("render")
        expected_scope = authorization.policy_allowed_scope
        variants = {
            "under": {
                "predicted_repair_scope": expected_scope[:1],
                "policy_allowed_scope": expected_scope[:1],
                "effective_repair_scope": expected_scope[:1],
            },
            "over": {
                "predicted_repair_scope": tuple(sorted((*expected_scope, "artifact/render/styles.css"))),
                "policy_allowed_scope": tuple(sorted((*expected_scope, "artifact/render/styles.css"))),
                "effective_repair_scope": tuple(sorted((*expected_scope, "artifact/render/styles.css"))),
            },
            "disjoint": {
                "predicted_repair_scope": ("artifact/render/styles.css",),
                "policy_allowed_scope": ("artifact/render/styles.css",),
                "effective_repair_scope": ("artifact/render/styles.css",),
            },
            "partial": {
                "predicted_repair_scope": expected_scope,
                "policy_allowed_scope": expected_scope,
                "effective_repair_scope": expected_scope[:1],
            },
        }
        for name, changes in variants.items():
            with self.subTest(name=name):
                altered = replace(authorization, **changes)
                output_dir = self.output("scope-" + name)
                before = tree_bytes(bundle_dir)
                with patch.object(type(authorization), "validate_against", return_value=None):
                    with self.assertRaisesRegex(RepairExecutionError, "frozen mechanical scope"):
                        execute_authorized_deterministic_repair(
                            bundle_dir,
                            self.fixture.parity,
                            report,
                            altered,
                            output_dir,
                        )
                self.assertEqual(before, tree_bytes(bundle_dir))
                self.assertFalse(output_dir.exists())
                self.assertFalse(
                    (output_dir.parent / ("." + output_dir.name + ".repair-stage")).exists()
                )
        with self.assertRaisesRegex(RepairExecutionError, "lowercase"):
            executor_module._require_sha256("A" * 64, "test_sha256")

    def test_executor_rejects_wrong_stale_and_report_only_forgery_without_output(self) -> None:
        bundle_dir, report, authorization = self.source_report_and_authorization("render")
        wrong_report = detect_blinded_fault_bundle(self.fixture.clean_dir, self.fixture.parity)
        with self.assertRaisesRegex(RepairExecutionError, "caller report"):
            execute_authorized_deterministic_repair(
                bundle_dir, self.fixture.parity, wrong_report,
                authorize_fault_detection_report(wrong_report), self.output("wrong-report"),
            )
        self.assertFalse(self.output("wrong-report").exists())

        stale_parity = replace(self.fixture.parity, path_set_sha256="0" * 64)
        with self.assertRaisesRegex(RepairExecutionError, "parity"):
            execute_authorized_deterministic_repair(
                bundle_dir, stale_parity, report, authorization, self.output("stale-parity")
            )
        self.assertFalse(self.output("stale-parity").exists())

        forged = replace(report, bundle_id="forged-bundle")
        forged = replace(
            forged,
            report_id="fault-detection-report-" + detector_module._canonical_sha256(
                forged.to_payload()
            )[:20],
        )
        forged.validate()
        forged_authorization = authorize_fault_detection_report(forged)
        with self.assertRaisesRegex(RepairExecutionError, "caller report"):
            execute_authorized_deterministic_repair(
                bundle_dir, self.fixture.parity, forged, forged_authorization,
                self.output("forged-report"),
            )
        self.assertFalse(self.output("forged-report").exists())

    def test_non_authorized_statuses_and_tampered_authorization_fail_closed(self) -> None:
        clean = detect_blinded_fault_bundle(self.fixture.clean_dir, self.fixture.parity)
        clean_authorization = authorize_fault_detection_report(clean)
        self.assertEqual(clean_authorization.status, NO_ACTION)
        with self.assertRaisesRegex(RepairExecutionError, "only one classified"):
            execute_authorized_deterministic_repair(
                self.fixture.clean_dir, self.fixture.parity, clean, clean_authorization,
                self.output("clean"),
            )
        self.assertFalse(self.output("clean").exists())

        page_report = detect_blinded_fault_bundle(
            self.fixture.fault_dirs["page_spec"], self.fixture.parity
        )
        page_authorization = authorize_fault_detection_report(page_report)
        self.assertEqual(page_authorization.status, FALLBACK_REQUIRED)
        with self.assertRaisesRegex(RepairExecutionError, "mechanically executable"):
            execute_authorized_deterministic_repair(
                self.fixture.fault_dirs["page_spec"], self.fixture.parity,
                page_report, page_authorization, self.output("fallback"),
            )
        self.assertFalse(self.output("fallback").exists())

        bundle_dir, report, authorization = self.source_report_and_authorization("package_path")
        tampered = replace(authorization, action="deterministic_fallback")
        with self.assertRaisesRegex(RepairExecutionError, "authorization"):
            execute_authorized_deterministic_repair(
                bundle_dir, self.fixture.parity, report, tampered,
                self.output("tampered-authorization"),
            )
        self.assertFalse(self.output("tampered-authorization").exists())

    def test_render_mechanical_guard_rejects_ambiguous_and_text_only_targets(self) -> None:
        expected = "component-expected"
        actual = "component-unexpected"
        with self.assertRaisesRegex(RepairExecutionError, "exactly one"):
            executor_module._replace_exact_component_id_attribute(
                '<div data-component-id="component-other"></div>', expected, actual
            )
        with self.assertRaisesRegex(RepairExecutionError, "duplicate"):
            executor_module._replace_exact_component_id_attribute(
                '<div data-component-id="component-unexpected"></div>'
                '<p data-component-id="component-unexpected"></p>', expected, actual
            )
        with self.assertRaisesRegex(RepairExecutionError, "no expected"):
            executor_module._replace_exact_component_id_attribute(
                '<div data-component-id="component-unexpected"></div>'
                '<p data-component-id="component-expected"></p>', expected, actual
            )
        html = (
            'component-unexpected text <div data-component-id="component-unexpected"></div>'
        )
        repaired = executor_module._replace_exact_component_id_attribute(html, expected, actual)
        self.assertIn("component-unexpected text", repaired)
        self.assertIn('data-component-id="component-expected"', repaired)

    def test_package_guard_rejects_zero_or_multiple_targets(self) -> None:
        bundle_dir, report, _ = self.source_report_and_authorization("package_sha256")
        record = executor_module.load_blinded_fault_bundle(bundle_dir)
        files = executor_module._read_bundle_artifact_bytes(bundle_dir, record)
        manifest_path = "artifact/result_package/package_manifest.json"
        manifest = json.loads(files[manifest_path].decode("utf-8"))
        target = executor_module._single_related_package_path(report, manifest_path)
        entry = next(item for item in manifest["files"] if item["path"] == target)

        zero = json.loads(json.dumps(manifest))
        entry_zero = next(item for item in zero["files"] if item["path"] == target)
        entry_zero["sha256"] = report.expected[0][1]
        zero_files = {**files, manifest_path: canonical_json_bytes(zero)}
        with self.assertRaisesRegex(RepairExecutionError, "detector report"):
            executor_module._repair_package_manifest_sha256(report, zero_files)

        multiple = json.loads(json.dumps(manifest))
        duplicate = dict(entry)
        duplicate["path"] = "page/styles.css"
        duplicate["sha256"] = report.actual[0][1]
        multiple["files"].append(duplicate)
        multiple_files = {**files, manifest_path: canonical_json_bytes(multiple)}
        with self.assertRaisesRegex(RepairExecutionError, "unique"):
            executor_module._repair_package_manifest_sha256(report, multiple_files)

    def test_post_gate_failure_and_output_writer_guards_never_claim_success(self) -> None:
        bundle_dir, report, authorization = self.source_report_and_authorization("render")
        failed_output = self.output("post-gate-failure")
        with patch.object(
            executor_module,
            "detect_blinded_fault_bundle",
            side_effect=[report, report],
        ):
            with self.assertRaisesRegex(RepairExecutionError, "post-repair"):
                execute_authorized_deterministic_repair(
                    bundle_dir, self.fixture.parity, report, authorization, failed_output
                )
        self.assertFalse(failed_output.exists())

        success_dir = self.output("writer-source")
        execution = execute_authorized_deterministic_repair(
            bundle_dir, self.fixture.parity, report, authorization, success_dir
        )
        first = self.output("writer-first")
        second = self.output("writer-second")
        write_repair_execution_report(execution, first)
        write_repair_execution_report(execution, second)
        self.assertEqual(tree_bytes(first), tree_bytes(second))
        occupied = self.output("writer-occupied")
        occupied.mkdir(parents=True)
        (occupied / "sentinel.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(RepairExecutionError, "empty"):
            write_repair_execution_report(execution, occupied)
        self.assertEqual((occupied / "sentinel.txt").read_text(encoding="utf-8"), "preserve")
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(RepairExecutionError, "symlink"):
                write_repair_execution_report(execution, self.output("writer-symlink"))

    def test_post_commit_external_validation_failure_removes_destination_and_staging(self) -> None:
        bundle_dir, report, authorization = self.source_report_and_authorization("render")
        output_dir = self.output("post-commit-external-validation")
        staging_dir = output_dir.parent / ("." + output_dir.name + ".repair-stage")
        before = tree_bytes(bundle_dir)
        original_validate_against = executor_module.RepairExecutionReport.validate_against

        def fail_only_after_commit(self, source_dir, repaired_dir, *args):
            if Path(repaired_dir).absolute() == (output_dir / "repaired_bundle").absolute():
                raise RepairExecutionError("forced post-commit external validation failure")
            return original_validate_against(self, source_dir, repaired_dir, *args)

        with patch.object(
            executor_module.RepairExecutionReport,
            "validate_against",
            new=fail_only_after_commit,
        ):
            with self.assertRaisesRegex(RepairExecutionError, "forced post-commit"):
                execute_authorized_deterministic_repair(
                    bundle_dir,
                    self.fixture.parity,
                    report,
                    authorization,
                    output_dir,
                )
        self.assertEqual(before, tree_bytes(bundle_dir))
        self.assertFalse(output_dir.exists())
        self.assertFalse(staging_dir.exists())

    def test_production_executor_isolated_from_mutation_evaluator_and_external_paths(self) -> None:
        source = (ROOT / "src" / "req2web_faults" / "repair_executor.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        forbidden = (
            "mutation", "injector", "evaluator", "provider", "qwen", "data.raw",
        )
        self.assertFalse(any(
            any(token in module.lower() for token in forbidden)
            for module in imports
        ))
        self.assertNotIn("data/raw", source)
        self.assertNotIn("FaultMutationRequest", source)
        self.assertNotIn("build_fault_copy", source)
        self.assertIn("execute_authorized_deterministic_repair", req2web_faults.__all__)


if __name__ == "__main__":
    import unittest
    unittest.main()
