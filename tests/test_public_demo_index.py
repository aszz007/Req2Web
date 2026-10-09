from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from req2web_inspector.live_draft import LocalDraftRunStore  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_rag.index import TfidfIndex  # noqa: E402


class PublicDemoIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "public_demo_builder", ROOT / "scripts/build_public_demo_index.py"
        )
        assert spec and spec.loader
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)

    def setUp(self) -> None:
        self.root = ROOT / f".req2web-public-demo-test-{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_separate_index_uses_five_authored_records(self) -> None:
        output = self.root / "index"
        manifest = self.builder.build_demo_index(output)
        self.assertEqual(manifest["document_count"], 5)
        self.assertFalse(manifest["evaluation_evidence"])
        index = TfidfIndex.load(output)
        self.assertEqual({row["role"] for row in index.documents}, set(ROLE_ORDER))
        for document in index.documents:
            self.assertEqual(document["dataset"], "req2web_synthetic_demo")
            self.assertFalse(document["metadata"]["third_party_material"])
            self.assertEqual(document["references"], [])

    def test_existing_output_is_preserved(self) -> None:
        sentinel = self.root / "keep.txt"
        sentinel.write_bytes(b"unrelated owner data")
        with self.assertRaisesRegex(ValueError, "existing data is preserved"):
            self.builder.build_demo_index(self.root)
        self.assertEqual(sentinel.read_bytes(), b"unrelated owner data")

    def test_frozen_corpus_is_rejected_before_writing(self) -> None:
        frozen = ROOT / "data/processed/rag"
        before = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in frozen.glob("*") if path.is_file()
        }
        with self.assertRaisesRegex(ValueError, "frozen retrieval corpus"):
            self.builder.build_demo_index(frozen)
        after = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in frozen.glob("*") if path.is_file()
        }
        self.assertEqual(before, after)

    def test_synthetic_index_supports_model_free_draft_and_import(self) -> None:
        output = self.root / "index"
        self.builder.build_demo_index(output)
        store = LocalDraftRunStore(self.root / "runs", output)
        record = store.create({
            "requirement": "Build a responsive equipment service page where users submit a problem and confirm its status.",
            "target_device": "responsive_web", "top_k": 1,
        })
        self.assertEqual(record["status"], "completed_deterministic_draft")
        self.assertFalse(record["authority_boundary"]["model_or_f1_f4_executed"])
        raw = store.artifact_path(record["run_id"], "result-package.zip").read_bytes()
        imported = store.import_package_zip(raw, "synthetic-demo.zip")
        self.assertEqual(imported["status"], "completed_imported_result_package")
        self.assertEqual(imported["result"]["package_id"], record["result"]["package_id"])
        self.assertEqual(json.loads(store.artifact_path(
            record["run_id"], "package/package_manifest.json"
        ).read_text(encoding="utf-8"))["package_id"], record["result"]["package_id"])


if __name__ == "__main__":
    unittest.main()
