from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def _read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


class Phase5StageCloseoutTests(unittest.TestCase):
    def test_current_authorities_close_only_the_bounded_scope(self) -> None:
        handoff = _read("phase5_exit_phase6_entry_handoff.md")
        registry = _read("active_flow_authority_registry.md")
        contract = _read("phase5_formal_holdout_no_action_contract.md")
        memory = _read("project_memory.md")

        for text in (handoff, registry, contract, memory):
            self.assertIn("closed_bounded_engineering_evaluation", text)
            self.assertIn("deferred_not_executed", text)

        self.assertIn(
            "phase6_entry = ready_for_release_inspector_replay_paper_and_demo_delivery",
            registry,
        )
        self.assertIn("h1_opened=false", handoff)
        self.assertIn("gold_accessed=false", handoff)
        self.assertIn("formal_evaluation=false", handoff)
        self.assertIn("formal_quality_claimed=false", handoff)

    def test_handoff_matches_owning_terminal_closeouts(self) -> None:
        handoff = _read("phase5_exit_phase6_entry_handoff.md")
        browser = _read("phase5_publication_v16_policy_browser_closeout.md")
        semantic = _read("phase5_publication_semantic_closeout.md")

        expected_pairs = (
            ("publication_action_source_commit", "source_action_commit"),
            ("publication_run_id", "run_id"),
            ("publication_return_tar_sha256", "result_return_tar_sha256"),
            ("semantic_action_source_commit", "action_source_commit"),
            ("semantic_manifest_identity", "semantic_manifest_identity"),
            ("semantic_summary_identity", "publication_summary_identity"),
            ("semantic_summary_file_sha256", "publication_summary_file_sha256"),
        )
        for handoff_key, source_key in expected_pairs:
            source = semantic if handoff_key.startswith("semantic_") else browser
            handoff_match = re.search(rf"^{handoff_key}=(.+)$", handoff, re.MULTILINE)
            source_match = re.search(rf"^{source_key}=(.+)$", source, re.MULTILINE)
            self.assertIsNotNone(handoff_match, handoff_key)
            self.assertIsNotNone(source_match, source_key)
            self.assertEqual(handoff_match.group(1), source_match.group(1))

    def test_handoff_keeps_generation_and_semantic_ledgers_separate(self) -> None:
        handoff = _read("phase5_exit_phase6_entry_handoff.md")
        self.assertIn("f1_f4_generate_started_count=48", handoff)
        self.assertIn("historical_downstream_first_pass_success_count=9", handoff)
        self.assertIn("browser_execution_pass_count=12", handoff)
        self.assertIn("semantic_generate_started_count=12", handoff)
        self.assertIn("semantic_accepted_result_count=12", handoff)
        self.assertIn("semantic_supported_criterion_count=24", handoff)
        self.assertIn("must never be summed", handoff)


if __name__ == "__main__":
    unittest.main()
