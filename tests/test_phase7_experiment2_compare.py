from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("phase7_experiment2_compare", ROOT / "scripts" / "phase7_experiment2_compare.py")
assert SPEC and SPEC.loader
comparison = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = comparison
SPEC.loader.exec_module(comparison)


class Phase7Experiment2CompareTest(unittest.TestCase):
    def test_observed_render_manifest_binding_uses_raw_bytes(self) -> None:
        raw = b'{ "files": [] }\n'
        path = Mock(is_file=Mock(return_value=True), read_bytes=Mock(return_value=raw))
        parsed = {"artifact/render/render_manifest.json": {"files": []},
                  "artifact/acceptance/acceptance_binding.json": {"observed_render_manifest_sha256": comparison.sha256(raw).hexdigest()}}
        with patch.object(comparison, "_safe_bundle_path", return_value=path):
            self.assertEqual(comparison._declared_hash_checks(Mock(), {}, parsed), [])

    def test_object_bindings_use_canonical_json_not_pretty_file_bytes(self) -> None:
        payload = {"value": "unicode-\u00e9"}
        target = "artifact/result_package/internal/retrieval_guidance.json"
        parsed = {target: payload, "artifact/inspector_fact_set.json": {
            "guidance_sha256": comparison.sha256(comparison.canonical(payload)).hexdigest()}}
        with patch.object(comparison, "_safe_bundle_path", return_value=Mock()):
            self.assertEqual(comparison._declared_hash_checks(Mock(), {}, parsed), [])
        parsed["artifact/inspector_fact_set.json"]["guidance_sha256"] = "0" * 64
        with patch.object(comparison, "_safe_bundle_path", return_value=Mock()):
            self.assertEqual(comparison._declared_hash_checks(Mock(), {}, parsed)[0]["target_candidates"], [target])

    def test_render_inventory_checks_literal_file_bytes(self) -> None:
        path = Mock()
        path.is_file.return_value = True
        path.read_bytes.return_value = b"new html"
        parsed = {"artifact/render/render_manifest.json": {"files": [{"name": "index.html", "sha256": comparison.sha256(b"old html").hexdigest()}]}}
        with patch.object(comparison, "_safe_bundle_path", return_value=path):
            findings = comparison._declared_hash_checks(Mock(), {}, parsed)
        self.assertEqual(findings[0]["target_candidates"], ["artifact/render/index.html"])

    def test_page_spec_contract_returns_exact_missing_component(self) -> None:
        value = {"components": [{"component_id": "kept"}], "sections": [{"section_id": "section", "component_ids": ["missing"]}], "states": [], "interactions": [], "use_cases": [], "traceability": {"use_cases": []}}
        findings = comparison._page_spec_contracts(value, "page.json")
        dangling = [row for row in findings if row["code"] == "dangling_local_reference"]
        self.assertEqual(dangling[0]["target_candidates"], ["missing"])

    def test_inspector_contract_returns_only_missing_trace_target(self) -> None:
        value = {"facts": [{"fact_id": "fact", "trace_link_ids": ["trace-missing"]}], "source_refs": [{"source_ref_id": "source"}], "trace_links": [{"trace_link_id": "trace-kept"}]}
        findings = comparison._inspector_contracts(value, "facts.json")
        self.assertEqual(["trace-missing"], [item for row in findings for item in row["target_candidates"]])

    def test_diagnose_keeps_labels_out_of_algorithm_input(self) -> None:
        source = comparison.Path(comparison.__file__).read_text(encoding="utf-8")
        diagnose_source = source[source.index("def diagnose("):source.index("def _c2_diagnostic(")]
        for forbidden in ("mutation_kind", "target_id", "expected_stage", "expected_error_code"):
            self.assertNotIn(forbidden, diagnose_source)

    def test_writer_refuses_overwrite(self) -> None:
        class ExistingPath:
            def exists(self) -> bool:
                return True

            def __str__(self) -> str:
                return "already-present.json"

        with self.assertRaisesRegex(comparison.ComparisonError, "overwrite"):
            comparison.write_new(ExistingPath(), {"two": 2})

    def test_score_requires_one_exact_candidate(self) -> None:
        labels = [{"slot_id": "slot-001", "case_id": "case", "mutation_kind": "fault", "target_id": "right"}]
        diagnostics = [{"slot_id": "slot-001", "condition": name, "status": "alarm", "candidate_count": len(candidates), "target_candidates": candidates} for name, candidates in (("C0", ["right"]), ("C1", ["right", "extra"]), ("C2", []))]
        scored = comparison._score(diagnostics, labels)
        self.assertEqual(scored["summary"]["C0"]["single_exact_target"], 1)
        self.assertEqual(scored["summary"]["C1"]["candidate_hit"], 1)
        self.assertEqual(scored["summary"]["C1"]["single_exact_target"], 0)
        self.assertEqual(scored["summary"]["C2"]["candidate_hit"], 0)


if __name__ == "__main__":
    unittest.main()
