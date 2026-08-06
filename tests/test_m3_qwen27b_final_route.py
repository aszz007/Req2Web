"""Focused no-model tests for the Qwen3.5-27B final route adapter."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_runtime import qwen27b_final_route as final_route  # noqa: E402
from req2web_runtime.autodl_trusted_remote_case_loader import (  # noqa: E402
    build_fixed_trusted_remote_case_materials_v2,
)
from test_m3_gate_delivery import gate_delivery_candidate_bytes  # noqa: E402
from test_m3_qwen27b_case_bundle import Qwen27BCaseBundleTests  # noqa: E402
from test_m3_semantic_candidate_assembly import raw_bytes  # noqa: E402


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class Qwen27BFinalRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Qwen27BCaseBundleTests.setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        Qwen27BCaseBundleTests.tearDownClass()

    def setUp(self) -> None:
        self.work = ROOT / ("tmp-qwen27b-final-route-" + uuid4().hex)
        self.work.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def _fixture(self, raw_by_case: dict[str, bytes]):
        run_root = self.work / ("run-" + uuid4().hex)
        cases = []
        for case_id, raw in raw_by_case.items():
            relative = f"cases/{case_id}/raw_response.bin"
            path = run_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            cases.append(
                {
                    "case_id": case_id,
                    "raw_response": {
                        "relative_path": relative,
                        "byte_length": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    },
                }
            )
        run = {
            "run_id": "a" * 64,
            "cases": cases,
        }
        bundle = SimpleNamespace(
            root=self.work / "bundle",
            manifest={"cases": []},
        )
        return run_root, run, bundle

    def _evaluate(self, raw_by_case: dict[str, bytes]):
        run_root, run, bundle = self._fixture(raw_by_case)
        with (
            patch.object(
                final_route,
                "validate_qwen27b_recovery_return_against",
                return_value=run,
            ),
            patch.object(
                final_route._case_bundle,
                "validate_qwen27b_case_bundle",
                return_value=bundle,
            ),
            patch.object(final_route, "_cross_bind_case_bundle"),
            patch.object(
                final_route,
                "_extract_archive_source_tree",
                return_value=ROOT,
            ),
        ):
            return final_route.evaluate_qwen27b_final_route(
                run_root=run_root,
                predeploy_manifest=self.work / "manifest",
                predeploy_ready=self.work / "ready",
                repository_archive=self.work / "archive",
                repository_archive_manifest=self.work / "archive-manifest",
                case_bundle_root=self.work / "bundle",
                selected_copy="none",
                work_root=self.work / ("route-work-" + uuid4().hex),
            )

    @staticmethod
    def _media_route_and_semantic_raw(work: Path) -> bytes:
        material_root = final_route._prepare_work_root(
            work / ("material-" + uuid4().hex)
        )
        try:
            materials = build_fixed_trusted_remote_case_materials_v2(
                ROOT,
                material_root,
            )
            context = materials.case_inputs[
                "path3-media-analysis"
            ]["context"]
            value = json.loads(gate_delivery_candidate_bytes(context))
            concepts = {
                "UC-01": (
                    "upload file photo image media select input browse "
                    "permission denied error retry fallback recover"
                ),
                "UC-02": "analyze analysis result output insight",
            }
            sections = {
                row["stable_id"]: row for row in value["sections"]
            }
            for mapping in value["use_case_mappings"]:
                suffix = concepts[mapping["use_case_id"]]
                for stable_id in mapping["section_stable_ids"]:
                    sections[stable_id]["purpose"] += " " + suffix
            return raw_bytes(value)
        finally:
            shutil.rmtree(material_root, ignore_errors=True)

    def test_real_a07_route_and_semantic_coverage_are_both_required(self):
        record = self._evaluate(
            {
                "path3-commerce-checkout": b"not-json-commerce",
                "path3-media-analysis": self._media_route_and_semantic_raw(
                    self.work
                ),
            }
        )
        by_case = {case["case_id"]: case for case in record["cases"]}
        self.assertEqual(record["overall_decision"], "fail_closed")
        self.assertEqual(
            by_case["path3-commerce-checkout"]["decision"],
            "fail_closed",
        )
        media = by_case["path3-media-analysis"]
        self.assertIn(
            media["generic_route"]["status"],
            {"first_pass_success", "recovered_success"},
        )
        self.assertIn(
            media["generic_route"]["delivery_source"],
            {"model_first_pass_v1", "model_repaired_v1"},
        )
        self.assertEqual(media["semantic_coverage"]["decision"], "pass")
        self.assertEqual(media["decision"], "recovery_pass")
        self.assertFalse(media["claims"]["real_browser_executed"])
        self.assertFalse(media["claims"]["formal_browser_success"])
        self.assertFalse(record["claims"]["real_browser_executed"])
        self.assertFalse(record["claims"]["formal_browser_success"])

    def test_standalone_forged_pass_cannot_promote_fallback(self):
        forged_pass = {
            "decision": "pass",
            "input_statuses": {"generic_gate": "passed"},
        }
        self.assertEqual(forged_pass["decision"], "pass")
        record = self._evaluate(
            {
                "path3-commerce-checkout": b"not-json-commerce",
                "path3-media-analysis": b"not-json-media",
            }
        )
        self.assertEqual(record["overall_decision"], "fail_closed")
        for case in record["cases"]:
            self.assertEqual(case["decision"], "fail_closed")
            self.assertNotEqual(
                case["generic_route"]["delivery_source"],
                "model_first_pass_v1",
            )
            self.assertIsNone(case["semantic_coverage"])

    def test_materials_are_built_from_archive_not_dirty_repository(self):
        dirty_case = (
            Qwen27BCaseBundleTests.repo
            / "fixtures"
            / "stage3_path3_compatibility_cases_v1.json"
        )
        working_original = dirty_case.read_bytes()
        with zipfile.ZipFile(
            Qwen27BCaseBundleTests.first_archive_path, "r"
        ) as archive:
            clean_archive_bytes = archive.read(
                "fixtures/stage3_path3_compatibility_cases_v1.json"
            )
        dirty_case.write_bytes(working_original + b" ")
        route_work = final_route._prepare_work_root(
            self.work / "archive-route-work"
        )
        try:
            source_root = final_route._extract_archive_source_tree(
                Qwen27BCaseBundleTests.first_archive_path,
                Qwen27BCaseBundleTests.first_manifest_path,
                route_work,
            )
            extracted = (
                source_root
                / "fixtures"
                / "stage3_path3_compatibility_cases_v1.json"
            )
            self.assertEqual(extracted.read_bytes(), clean_archive_bytes)
            self.assertNotEqual(extracted.read_bytes(), dirty_case.read_bytes())
        finally:
            dirty_case.write_bytes(working_original)

    def test_context_guidance_cross_binding_drift_fails_closed(self):
        bundle = final_route._case_bundle.build_qwen27b_case_bundle(
            Qwen27BCaseBundleTests.first_archive_path,
            Qwen27BCaseBundleTests.first_manifest_path,
            self.work / "cross-bind-bundle",
        )
        material_root = final_route._prepare_work_root(
            self.work / "cross-bind-materials"
        )
        materials = build_fixed_trusted_remote_case_materials_v2(
            ROOT,
            material_root,
        )
        final_route._cross_bind_case_bundle(bundle, materials)
        materials.manifest["cases"][0]["retrieval_guidance"][
            "source_context"
        ]["sha256"] = "0" * 64
        with self.assertRaisesRegex(
            final_route.Qwen27BFinalRouteError,
            "retrieval_guidance_cross_binding",
        ):
            final_route._cross_bind_case_bundle(bundle, materials)


if __name__ == "__main__":
    unittest.main()
