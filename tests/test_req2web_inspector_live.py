from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_generation import RetrievalEnhancedResultPackage  # noqa: E402
from req2web_inspector.live_draft import (  # noqa: E402
    InspectorLiveDraftError,
    LocalDraftRunStore,
    analyze_requirement,
    normalize_request,
)


class InspectorLiveDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.test_root = ROOT / f".req2web-inspector-live-test-{uuid.uuid4().hex}"
        cls.run_root = cls.test_root / "runs"
        cls.test_root.mkdir()
        cls.store = LocalDraftRunStore(
            cls.run_root,
            ROOT / "data" / "processed" / "rag",
        )
        cls.request = {
            "requirement": (
                "I need some kind of responsive equipment service page. Users should "
                "find an asset, submit a problem, see validation feedback, retry after "
                "a failure, and confirm the final status."
            ),
            "target_device": "responsive_web",
            "constraints": [
                "Support keyboard operation",
                "Keep recovery guidance visible after a failed submission",
            ],
            "retriever_backend": "tfidf",
            "top_k": 2,
        }
        cls.record = cls.store.create(cls.request)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.test_root, ignore_errors=True)

    def test_normalization_rejects_unsafe_or_unregistered_input(self) -> None:
        with self.assertRaisesRegex(InspectorLiveDraftError, "unsupported fields"):
            normalize_request({"requirement": "Build a page", "surprise": True})
        with self.assertRaisesRegex(InspectorLiveDraftError, "only the active TF-IDF"):
            normalize_request(
                {"requirement": "Build a page", "retriever_backend": "bm25"}
            )
        with self.assertRaisesRegex(InspectorLiveDraftError, "control characters"):
            normalize_request({"requirement": "Build\x00a page"})

    def test_diagnostics_preserve_input_and_do_not_claim_b_aux(self) -> None:
        raw = "\u60f3\u505a\u4e00\u4e2a\u8bbe\u5907\u62a5\u4fee\u9875\u9762"
        value = analyze_requirement({"requirement": raw})
        self.assertTrue(value["accepted_for_deterministic_draft"])
        self.assertEqual(value["normalized_request"]["requirement"], raw)
        self.assertEqual(
            value["semantic_assist"]["status"],
            "not_connected_no_model_action",
        )
        self.assertEqual(value["semantic_assist"]["call_count"], 0)
        self.assertIn(
            "multilingual_draft_input",
            {item["code"] for item in value["findings"]},
        )

    def test_multilingual_device_ambiguity_is_visible(self) -> None:
        value = analyze_requirement(
            {
                "requirement": (
                    "\u624b\u673a\u7535\u8111\u90fd\u80fd\u7528, "
                    "\u7528\u6237\u53ef\u4ee5\u641c\u7d22\u5e76"
                    "\u63d0\u4ea4\u95ee\u9898."
                )
            }
        )
        codes = {item["code"] for item in value["findings"]}
        self.assertIn("multiple_device_signals", codes)
        self.assertNotIn("action_not_explicit", codes)

    def test_deterministic_draft_records_boundaries_and_valid_package(self) -> None:
        record = self.record
        self.assertEqual(record["status"], "completed_deterministic_draft")
        self.assertFalse(record["authority_boundary"]["canonical_full_flow_executed"])
        self.assertFalse(record["authority_boundary"]["model_or_f1_f4_executed"])
        self.assertFalse(record["authority_boundary"]["semantic_aux_agent_executed"])
        statuses = {item["stage_id"]: item["status"] for item in record["stages"]}
        self.assertEqual(statuses["model_f1_f4"], "not_executed")
        self.assertEqual(statuses["browser"], "not_executed")
        self.assertEqual(statuses["semantic"], "not_executed")
        self.assertEqual(statuses["package"], "completed")
        result = record["result"]
        package = RetrievalEnhancedResultPackage(
            result["package_id"],
            result["page_id"],
            self.run_root / record["run_id"] / "package",
        )
        package.validate()
        with zipfile.ZipFile(
            self.run_root / record["run_id"] / "result-package.zip"
        ) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            self.assertTrue(all(name.startswith("result-package/") for name in names))

    def test_history_and_artifact_access_are_confined(self) -> None:
        rows = self.store.list()
        self.assertEqual([item["run_id"] for item in rows], [self.record["run_id"]])
        entrypoint = self.store.artifact_path(
            self.record["run_id"],
            "package/page/index.html",
        )
        self.assertTrue(entrypoint.is_file())
        with self.assertRaisesRegex(InspectorLiveDraftError, "escaped"):
            self.store.artifact_path(self.record["run_id"], "../outside.txt")

    def test_validated_result_package_zip_can_be_imported(self) -> None:
        imported_store = LocalDraftRunStore(
            self.test_root / "import-runs",
            ROOT / "data" / "processed" / "rag",
        )
        archive_bytes = (
            self.run_root / self.record["run_id"] / "result-package.zip"
        ).read_bytes()
        record = imported_store.import_package_zip(archive_bytes, "existing-result.zip")
        self.assertEqual(record["status"], "completed_imported_result_package")
        self.assertTrue(record["run_id"].startswith("import-"))
        self.assertEqual(
            record["source_package_schema_version"],
            "req2web.result.package.v2",
        )
        self.assertFalse(record["authority_boundary"]["model_or_f1_f4_executed"])
        self.assertTrue(
            imported_store.artifact_path(
                record["run_id"],
                "package/page/index.html",
            ).is_file()
        )
        self.assertEqual(
            imported_store.artifact_path(
                record["run_id"],
                "result-package.zip",
            ).read_bytes(),
            archive_bytes,
        )

    def test_import_rejects_unsafe_archive_without_creating_a_run(self) -> None:
        imported_store = LocalDraftRunStore(
            self.test_root / "unsafe-import-runs",
            ROOT / "data" / "processed" / "rag",
        )
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("result-package/../outside.txt", b"unsafe")
        with self.assertRaisesRegex(InspectorLiveDraftError, "unsafe path"):
            imported_store.import_package_zip(output.getvalue(), "unsafe.zip")
        self.assertEqual(imported_store.list(), [])

    def test_nonempty_unmarked_store_is_rejected(self) -> None:
        unmarked = self.test_root / "unmarked"
        unmarked.mkdir()
        (unmarked / "unrelated.txt").write_text("user data", encoding="utf-8")
        with self.assertRaisesRegex(InspectorLiveDraftError, "without the Inspector"):
            LocalDraftRunStore(unmarked, ROOT / "data" / "processed" / "rag")
        self.assertEqual(
            (unmarked / "unrelated.txt").read_text(encoding="utf-8"),
            "user data",
        )


if __name__ == "__main__":
    unittest.main()
