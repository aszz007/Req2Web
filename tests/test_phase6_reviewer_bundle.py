from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.phase6_replay import (  # noqa: E402
    _APP_JS,
    _INDEX_HTML,
    _STYLES_CSS,
    Phase6ReplayError,
    validate_phase6_reviewer_bundle,
)


class Phase6ReviewerBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle_root = ROOT / "release" / "phase6_reviewer_v17"
        cls.manifest = validate_phase6_reviewer_bundle(cls.bundle_root)

    def test_frozen_counts_and_separate_ledgers(self) -> None:
        counts = self.manifest["counts"]
        self.assertEqual(counts["row_count"], 12)
        self.assertEqual(counts["generation_call_count"], 48)
        self.assertEqual(counts["semantic_call_count"], 12)
        self.assertEqual(counts["historical_first_pass_count"], 9)
        self.assertEqual(counts["delivery_evidence_available_count"], 12)
        self.assertFalse(
            self.manifest["generation_and_semantic_call_ledgers_merged"]
        )

    def test_historical_failures_remain_f4_fail_closed(self) -> None:
        catalog = json.loads(
            (self.bundle_root / "catalog.json").read_text(encoding="utf-8")
        )
        failed: list[int] = []
        for item in catalog["cases"]:
            case = json.loads(
                (self.bundle_root / item["case_record"]).read_text(encoding="utf-8")
            )
            if not case["historical_first_pass"]["passed"]:
                failed.append(case["execution_index"])
                self.assertEqual(
                    case["historical_failure_location"]["failure_stage"],
                    "F4",
                )
                self.assertTrue(
                    case["historical_failure_location"][
                        "zero_model_policy_revalidated"
                    ]
                )
        self.assertEqual(failed, [2, 5, 9])

    def test_material_inventory_keeps_license_gate_open(self) -> None:
        materials = json.loads(
            (self.bundle_root / "MATERIALS.json").read_text(encoding="utf-8")
        )
        self.assertFalse(materials["public_release_ready"])
        self.assertEqual(
            materials["blocking_gate"],
            "owner_license_decision_required",
        )
        self.assertFalse(materials["network_required_after_bundle_creation"])
        self.assertFalse(materials["gpu_required_after_bundle_creation"])
        exclusions = " ".join(materials["excluded_materials"]).lower()
        self.assertIn("h1", exclusions)
        self.assertIn("rico", exclusions)
        self.assertIn("raw datasets", exclusions)

    def test_retrieval_comparison_is_diagnostic_and_qrels_gated(self) -> None:
        catalog = json.loads(
            (self.bundle_root / "catalog.json").read_text(encoding="utf-8")
        )
        report = json.loads(
            (self.bundle_root / catalog["retrieval_comparison"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["methods"], ["bm25", "rrf", "tfidf"])
        self.assertEqual(report["query_role_unit_count"], 60)
        self.assertEqual(len(report["units"]), 60)
        self.assertEqual(
            report["ranking_evaluation"]["status"],
            "not_computed_no_independent_qrels",
        )
        self.assertFalse(report["ranking_evaluation"]["winner_declared"])
        self.assertFalse(report["pairwise_summary"]["quality_winner_declared"])
        self.assertEqual(report["pairwise_summary"]["comparison_count"], 3)
        experiment = json.loads(
            (self.bundle_root / catalog["retrieval_experiment"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(experiment["llm_prelabel_template_count"], 1)
        self.assertEqual(experiment["human_reviewer_count"], 2)
        self.assertEqual(experiment["blind_overlap_audit_count"], 84)
        self.assertEqual(experiment["judgments_per_human_reviewer"], 252)
        self.assertEqual(
            experiment["protocol"]["primary_metrics"],
            ["pooled_ndcg_at_5", "pooled_recall_at_5"],
        )
        self.assertEqual(experiment["protocol"]["statistical_unit"], "case")
        self.assertFalse(experiment["ranking_quality_computed"])
        self.assertFalse(experiment["winner_declared"])
        for method in experiment["efficiency"]["methods"]:
            self.assertTrue(method["all_repetitions_exactly_match_comparison"])

    def test_optional_guispector_packet_is_prepared_but_not_executed(self) -> None:
        catalog = json.loads(
            (self.bundle_root / "catalog.json").read_text(encoding="utf-8")
        )
        packet = json.loads(
            (self.bundle_root / catalog["guispector_evaluation"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(packet["status"], "prepared_not_executed")
        self.assertFalse(packet["execution_performed"])
        self.assertEqual(packet["scope"]["result_package_count"], 12)
        self.assertEqual(packet["scope"]["acceptance_criterion_count"], 24)
        self.assertFalse(packet["scope"]["canonical_flow_modified"])
        self.assertFalse(packet["reference_profile"]["independent_human_gold"])
        self.assertFalse(packet["reference_profile"]["paper_comparison_eligible"])
        self.assertEqual(len(packet["cases"]), 12)
        for item in packet["cases"]:
            requirement = item["guispector_requirement"]
            self.assertEqual(len(requirement["acceptance_criteria"]), 2)
            self.assertTrue(
                item["start_url_template"].startswith(
                    "{REQ2WEB_INSPECTOR_BASE_URL}/"
                )
            )

    def test_manifest_detects_file_tampering(self) -> None:
        test_root = ROOT / f".phase6-replay-test-{uuid.uuid4().hex}"
        test_root.mkdir()
        try:
            copied = test_root / "bundle"
            shutil.copytree(self.bundle_root, copied)
            target = copied / "cases" / "01" / "requirement.json"
            target.write_bytes(target.read_bytes() + b"\n")
            with self.assertRaisesRegex(Phase6ReplayError, "file drifted"):
                validate_phase6_reviewer_bundle(copied)
        finally:
            shutil.rmtree(test_root, ignore_errors=True)

    def test_inspector_assets_have_no_remote_runtime_dependency(self) -> None:
        for relative in ("index.html", "app.js", "styles.css"):
            text = (self.bundle_root / relative).read_text(encoding="utf-8")
            self.assertNotIn("https://", text)
            self.assertNotIn("http://", text)

    def test_single_inspector_has_viewport_comparison_and_state_monitor(self) -> None:
        page = _INDEX_HTML.decode("utf-8")
        styles = _STYLES_CSS.decode("utf-8")
        script = _APP_JS.decode("utf-8")
        self.assertIn("<title>Req2Web Inspector</title>", page)
        self.assertNotIn("Phase 6 Inspector", page)
        self.assertIn('id="preview-fit"', page)
        self.assertIn('id="preview-audit"', page)
        self.assertIn('id="preview-state-name"', page)
        self.assertIn('id="browser-viewport"', page)
        self.assertIn(".browser-capture", styles)
        self.assertIn("function renderPreviewLayout()", script)
        self.assertIn("function applyPreviewSafetyStyles()", script)
        self.assertIn("function connectPreviewStateMonitor()", script)
        self.assertIn("req2web-inspector-preview-safety", script)
        self.assertIn('id="external-verification"', page)
        self.assertIn('id="guispector-case-select"', page)
        self.assertIn("function renderGuispector(report)", script)
        self.assertIn(".external-summary", styles)

    def test_single_inspector_has_compact_user_entry_and_explanations(self) -> None:
        page = _INDEX_HTML.decode("utf-8")
        styles = _STYLES_CSS.decode("utf-8")
        script = _APP_JS.decode("utf-8")
        self.assertIn('id="requirement-form"', page)
        self.assertIn('id="run-history"', page)
        self.assertIn('id="import-form"', page)
        self.assertIn('id="import-package"', page)
        self.assertIn("Choose ZIP", page)
        self.assertIn("No file selected", page)
        self.assertIn("Semantic requirement assistant", page)
        self.assertIn("not connected", page.lower())
        self.assertIn('id="semantic-assist"', page)
        self.assertGreaterEqual(page.count('class="info-tip"'), 12)
        self.assertIn(".info-tip:hover::after", styles)
        self.assertIn(".info-tip:focus-visible::after", styles)
        self.assertIn("overflow-x: clip", styles)
        self.assertIn(".intake-side .info-tip::after", styles)
        self.assertIn(".intake-side { grid-template-columns: repeat(3, minmax(0, 1fr)); }", styles)
        self.assertIn("select { width: 100%; min-width: 0;", styles)
        self.assertIn("function initializeLiveInspector()", script)
        self.assertIn("function canonicalOutcomeItems(record)", script)
        self.assertIn("function outcomeSummaryMarkup(record, imported, canonical)", script)
        self.assertIn("model failed; deterministic package ready", script)
        self.assertIn("The raw model result was rejected", script)
        self.assertIn("raw response</a>", script)
        self.assertIn("Browser execution</a>", script)
        self.assertIn("A same-input deterministic package is available", script)
        self.assertIn(".outcome-summary", styles)
        self.assertIn("/api/intake/analyze", script)
        self.assertIn("/api/intake/semantic-assist", script)
        self.assertIn("/api/runs", script)
        self.assertIn("/api/imports", script)
        self.assertIn(".split(/\\r?\\n/)", script)
        self.assertIn("Support keyboard operation\\nKeep recovery guidance", script)
        self.assertIn("Copy selected advice into constraints", script)
        self.assertIn("Advice is never written into canonical B", script)
        self.assertIn("data-advice-index", script)
        self.assertIn("Stop safely after the current stage", script)
        self.assertIn("/cancel", script)
        self.assertIn("interrupted by service restart", script)
        self.assertIn("function recoveryGuidance(record)", script)
        self.assertIn("'cancel_requested'", script)
        self.assertIn(".recovery-box", styles)

    def test_standalone_validator_uses_only_the_bundle(self) -> None:
        completed = subprocess.run(
            [sys.executable, "validate_bundle.py"],
            cwd=self.bundle_root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "validated_precomputed_replay")
        self.assertEqual(result["counts"]["row_count"], 12)

    def test_final_inspector_omits_development_surfaces(self) -> None:
        page = _INDEX_HTML.decode("utf-8")
        script = _APP_JS.decode("utf-8")
        self.assertNotIn('id="human-work"', page)
        self.assertNotIn("Human handoff", page)
        self.assertNotIn('id="retrieval"', page)
        self.assertNotIn("Retrieval laboratory", page)
        self.assertNotIn("Paper</th>", page)
        self.assertNotIn("renderRetrievalComparison", script)

    def test_guispector_is_default_off_and_has_explicit_provider_controls(self) -> None:
        page = _INDEX_HTML.decode("utf-8")
        script = _APP_JS.decode("utf-8")
        self.assertIn('id="guispector-enable" type="checkbox"', page)
        self.assertNotIn('id="guispector-enable" type="checkbox" checked', page)
        self.assertIn('id="guispector-provider"', page)
        self.assertIn('id="guispector-api-key" type="password"', page)
        self.assertIn('id="guispector-confirm" type="checkbox"', page)
        self.assertIn("function testGuispectorConnection()", script)
        self.assertIn("/api/guispector/test-connection", script)
        self.assertIn("apiKey.value = '';", script)
        self.assertNotIn("published_reference_metrics: published", script)
        self.assertNotIn("localStorage", script)


if __name__ == "__main__":
    unittest.main()
