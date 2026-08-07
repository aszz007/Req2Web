from __future__ import annotations

import hashlib
import json
import shutil
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_generation import (  # noqa: E402
    PublicationLanguageError,
    contains_cjk_text,
    validate_english_publication_tree,
    validate_english_publication_value,
)


class PublicationLanguageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = (
            ROOT
            / "tests"
            / ".tmp_publication_language"
            / self._testMethodName
        )
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        try:
            self.root.parent.rmdir()
        except OSError:
            pass

    def test_detector_covers_han_and_cjk_punctuation(self) -> None:
        self.assertTrue(contains_cjk_text("\u4e2d\u6587"))
        self.assertTrue(contains_cjk_text("\u3002"))
        self.assertTrue(contains_cjk_text("\uff1a"))
        self.assertFalse(
            contains_cjk_text(
                "English page copy, runtime records, and test evidence."
            )
        )

    def test_nested_internal_value_fails_closed(self) -> None:
        value = {
            "case_id": "case-01",
            "internal": {
                "retrieval": [
                    {
                        "summary": (
                            "English prefix "
                            "\u4e2d\u6587"
                        )
                    }
                ]
            },
        }
        with self.assertRaisesRegex(
            PublicationLanguageError,
            "internal.retrieval\\[0\\].summary",
        ):
            validate_english_publication_value(
                value,
                artifact_name="result",
            )

    def test_complete_text_tree_accepts_english_only_artifacts(self) -> None:
        root = self.root / "english"
        (root / "internal").mkdir(parents=True)
        (root / "result_summary.json").write_text(
            json.dumps(
                {
                    "status": "completed",
                    "summary": "The page passed deterministic checks.",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (root / "internal" / "trace.jsonl").write_text(
            '{"event":"validated","message":"English only"}\n',
            encoding="utf-8",
        )
        (root / "page.html").write_text(
            '<!doctype html><html lang="en"><body>Ready</body></html>',
            encoding="utf-8",
        )
        validate_english_publication_tree(root)

    def test_mixed_historical_tree_is_rejected_without_overwrite(self) -> None:
        root = self.root / "historical"
        root.mkdir(parents=True)
        artifact = root / "historical_result.json"
        original = json.dumps(
            {
                "status": "historical_failed_language_gate",
                "summary": "\u4e2d\u82f1\u6df7\u5408",
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        artifact.write_bytes(original)
        before = hashlib.sha256(artifact.read_bytes()).hexdigest()

        with self.assertRaises(PublicationLanguageError):
            validate_english_publication_tree(root)

        after = hashlib.sha256(artifact.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(artifact.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
