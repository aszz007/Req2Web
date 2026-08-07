from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_generation import contains_cjk_text  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER, build_unified_documents  # noqa: E402
from req2web_rag.index import (  # noqa: E402
    RETRIEVAL_RESULT_PROJECTION_REVISION,
    TfidfIndex,
    build_tfidf_index,
)
from req2web_rag.schema import validate_document  # noqa: E402


DEMO_QUERY = (
    "Create a responsive mobile commerce page with sign-in, product search "
    "and filters, a cart, checkout, clear interactions, and acceptance "
    "coverage for invalid input and denied permissions."
)


class RagPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.documents = build_unified_documents(ROOT / "data/processed")

    def test_manifest_is_fully_covered(self) -> None:
        self.assertEqual(len(self.documents), 283)
        self.assertEqual(
            Counter(document["role"] for document in self.documents),
            Counter(
                requirement=93,
                ui_reference=42,
                interaction_flow=12,
                implementation=124,
                validation=12,
            ),
        )
        self.assertEqual(len({document["doc_id"] for document in self.documents}), 283)
        for document in self.documents:
            validate_document(document)

    def test_webpage_has_an_explicit_rag_source(self) -> None:
        source_path = ROOT / "data/processed/vision2web_webpage_rag_records.jsonl"
        with source_path.open("r", encoding="utf-8") as stream:
            records = [json.loads(line) for line in stream if line.strip()]
        self.assertEqual(len(records), 100)
        self.assertEqual(len({record["sample_id"] for record in records}), 100)
        required = {
            "sample_id",
            "dataset",
            "subset",
            "level",
            "task_name",
            "implementation_type",
            "workflow_path",
            "prototype_paths",
            "resource_root",
            "resource_count",
            "workflow_devices",
            "reason",
            "notes",
        }
        for record in records:
            self.assertTrue(required.issubset(record), record.get("sample_id"))
            self.assertEqual(record["dataset"], "vision2web")
            self.assertEqual(record["subset"], "webpage")
            self.assertNotIn("requirement_text", record)
            self.assertEqual(len(record["prototype_paths"]), 3)

        webpage_documents = [
            document
            for document in self.documents
            if document["dataset"] == "vision2web" and document["subset"] == "webpage"
        ]
        self.assertEqual(len(webpage_documents), 100)
        self.assertTrue(
            all(
                document["source"]["record_path"]
                == "vision2web_webpage_rag_records.jsonl"
                for document in webpage_documents
            )
        )

    def test_index_retrieves_every_role(self) -> None:
        index = TfidfIndex(self.documents, build_tfidf_index(self.documents))
        results = index.search_by_role(DEMO_QUERY, top_k=2)
        self.assertEqual(tuple(results), ROLE_ORDER)
        for role, items in results.items():
            self.assertGreater(len(items), 0, role)
            self.assertTrue(all(item["role"] == role for item in items), role)
            self.assertTrue(all(item["score"] > 0 for item in items), role)
            for item in items:
                self.assertEqual(
                    item["result_projection_revision"],
                    RETRIEVAL_RESULT_PROJECTION_REVISION,
                )
                self.assertFalse(contains_cjk_text(item["title"]))
                self.assertFalse(contains_cjk_text(item["summary"]))
                for reference in item["references"]:
                    uri = reference.get("uri", "")
                    self.assertFalse(contains_cjk_text(uri))
                    self.assertTrue(uri.isascii())


if __name__ == "__main__":
    unittest.main()
