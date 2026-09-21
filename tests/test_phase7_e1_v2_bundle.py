from __future__ import annotations

import json
from pathlib import Path
import unittest
import zipfile

from scripts import prepare_phase7_e1_v2_bundle as bundle


ROOT = Path(__file__).resolve().parents[1]


class E1V2BundlePolicyTests(unittest.TestCase):
    def test_explicit_overlay_contains_runtime_but_not_observer(self) -> None:
        self.assertIn("scripts/phase7_e1_v2_runtime.py", bundle.OVERLAY)
        self.assertIn("scripts/phase7_e1_v2_cases.py", bundle.OVERLAY)
        self.assertNotIn("scripts/phase7_e1_v2_observe.py", bundle.OVERLAY)
        self.assertFalse(bundle._allowed_base("tests/test_phase7_e1_v2_observe.py"))
        self.assertFalse(bundle._allowed_base("src/req2web_faults/evaluator_gold.py"))

    def test_current_payload_if_present_excludes_private_evaluator(self) -> None:
        archive = ROOT / "outputs/phase7_experiment1_v2/payload-20260917-final-v4/phase7-autodl-payload.zip"
        if not archive.is_file():
            self.skipTest("final E1 v2 payload has not been built")
        with zipfile.ZipFile(archive) as source:
            names = set(source.namelist())
            manifest = json.loads(source.read("payload_manifest.json"))
        self.assertNotIn("scripts/phase7_e1_v2_observe.py", names)
        self.assertFalse(any("evaluator_gold" in name for name in names))
        self.assertEqual(manifest["metadata"]["automatic_retry_count"], 0)


if __name__ == "__main__":
    unittest.main()
