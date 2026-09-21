from __future__ import annotations

import json
from pathlib import Path
import unittest

from scripts import phase7_e2_trace_navigation_acceptance as acceptance


ROOT = Path(__file__).resolve().parents[1]
BARE_MANIFEST_SHA = acceptance.PUBLIC_MANIFEST_SHA256.removeprefix("sha256:")


def _gold_packets(split: str) -> list[dict[str, object]]:
    if split == "development":
        families = ["wrong_relation", "no_progress"]
    else:
        families = (
            ["clean"] * 4
            + ["explicit_error"] * 2
            + ["no_progress"] * 2
            + ["stale_revision"] * 4
            + ["two_defects"] * 4
            + ["wrong_relation"] * 4
        )
    rows = []
    for index, family in enumerate(families, start=1):
        clean = family == "clean"
        rows.append(
            {
                "packet_id": f"p{index:03d}",
                "case_id": f"case-{(index - 1) % 4 + 1}",
                "family": family,
                "split": split,
                "status": "no_fault" if clean else "fault",
                "origins": [] if clean else [{"artifact": "handoff.json", "pointer": "/links/0/use_case_id"}],
                "evidence_groups": [] if clean else [[{"from": {"artifact": "handoff.json", "pointer": "/links/0/use_case_id"}, "to": {"artifact": "specification.json", "pointer": "/sections/0/use_case_ids"}}]],
                "affected_use_cases": [] if clean else ["UC-01"],
            }
        )
    return rows


def _payloads(split: str = "measured"):
    gold_rows = _gold_packets(split)
    schedule = []
    runtime_rows = []
    score_rows = []
    audits = {}
    trace_failure_packets = {
        row["packet_id"] for row in gold_rows if row["family"] in acceptance.TRACE_FAMILIES
    }
    a_failures = set(sorted(trace_failure_packets)[:3]) if split == "measured" else set()
    eligible_b_ids = [row["packet_id"] for row in gold_rows if row["family"] in acceptance.INDEX_ELIGIBLE_FAMILIES]
    grounded_ids = set(eligible_b_ids[:5] if split == "measured" else eligible_b_ids[:1])
    session_number = 0
    for gold in gold_rows:
        for arm in ("A", "B"):
            session_number += 1
            session_id = f"s{session_number:03d}"
            schedule.append({"session_id": session_id, "packet_id": gold["packet_id"], "arm": arm})
            complete = not (arm == "A" and gold["packet_id"] in a_failures)
            index_reads = int(arm == "B" and gold["packet_id"] in grounded_ids)
            audits[session_id] = {
                "total_reads": 3 + index_reads,
                "raw_artifact_reads": 3,
                "index_reads": index_reads,
                "grounded_exact_index_reads": index_reads,
                "violations": [],
            }
            runtime_rows.append(
                {
                    "session_id": session_id,
                    "packet_id": gold["packet_id"],
                    "arm": arm,
                    "status": gold["status"],
                    "reads": 3 + index_reads,
                    "provider_turns": 4 + index_reads,
                    "input_tokens": 1000 + 100 * index_reads,
                    "elapsed_seconds": 10.0 + index_reads,
                }
            )
            origins = len(gold["origins"])
            groups = len(gold["evidence_groups"])
            score_rows.append(
                {
                    "session_id": session_id,
                    "packet_id": gold["packet_id"],
                    "arm": arm,
                    "case_id": gold["case_id"],
                    "family": gold["family"],
                    "status": gold["status"],
                    "complete_supported_diagnosis": complete,
                    "clean_false_alarm": False,
                    "origin": {"true_positive": origins if complete else 0},
                    "evidence": {"supported_groups": groups if complete else 0},
                }
            )
    manifest = {
        "schema_version": "req2web.phase7.e2_public.v3",
        "schedule": {split: schedule},
    }
    gold = {"schema_version": "req2web.phase7.e2_gold.v3", "packets": gold_rows}
    runtime = {
        "schema_version": "req2web.phase7.e2_runtime_result.v3",
        "split": split,
        "public_manifest_sha256": BARE_MANIFEST_SHA,
        "sessions": runtime_rows,
    }
    score = {
        "schema_version": "req2web.phase7.e2_diagnostic_score.v3",
        "split": split,
        "public_manifest_sha256": BARE_MANIFEST_SHA,
        "result_sha256": "runtime",
        "rows": score_rows,
    }
    return manifest, gold, runtime, score, audits


def _assess(split: str = "measured"):
    manifest, gold, runtime, score, audits = _payloads(split)
    return acceptance.assess_payloads(
        score=score,
        runtime=runtime,
        manifest=manifest,
        gold=gold,
        split=split,
        runtime_sha256="sha256:runtime",
        manifest_sha256=acceptance.PUBLIC_MANIFEST_SHA256,
        gold_sha256=acceptance.GOLD_SHA256,
        read_audits=audits,
    )


class Phase7E2TraceNavigationAcceptanceTests(unittest.TestCase):
    def test_frozen_strategy_matches_scorer_constants(self) -> None:
        strategy = json.loads(
            (ROOT / "fixtures/phase7_e2_trace_navigation_acceptance_v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            strategy["historical_sources"]["local_comparison_summary_sha256"],
            acceptance.LOCAL_SUMMARY_SHA256,
        )
        self.assertEqual(
            strategy["diagnostic_protocol"]["public_manifest_sha256"],
            acceptance.PUBLIC_MANIFEST_SHA256,
        )
        self.assertEqual(
            set(strategy["measured_acceptance"]["index_eligible_families"]),
            acceptance.INDEX_ELIGIBLE_FAMILIES,
        )
        self.assertEqual(
            strategy["measured_acceptance"]["minimum_net_additional_complete_trace_packets_for_b"],
            acceptance.MIN_NET_ADDITIONAL_TRACE_PACKETS,
        )
        self.assertEqual(
            strategy["measured_acceptance"]["maximum_invalid_or_noncompleted_sessions_per_arm"],
            acceptance.MAX_INVALID_SESSIONS_PER_ARM,
        )

    def test_historical_analysis_retains_external_equivalence(self) -> None:
        result = acceptance.analyze_historical_files(
            ROOT / "outputs/phase7_experiment2_comparison_v1/local-baselines-final/machine_summary.json",
            ROOT / "outputs/phase7_experiment2_comparison_v1/external-epsilon/run-1/summary.json",
        )
        local_gain = result["contrasts"]["req2web_c2_minus_local_reference_c1"]
        external = result["contrasts"]["req2web_c2_minus_external_cross"]
        self.assertEqual(local_gain["additional_detected_mutants"], 6)
        self.assertEqual(local_gain["additional_trace_relation_defects_detected"], 6)
        self.assertEqual(external["detected_mutant_difference"], 0)
        self.assertEqual(external["exact_target_presence_difference"], 0)

    def test_tool_read_audit_requires_prior_raw_observation_and_exact_lookup(self) -> None:
        valid = acceptance.audit_tool_reads(
            [
                {
                    "turn": 1,
                    "request": {"artifact": "handoff.json", "pointer": "/links/0"},
                    "response": {"artifact": "handoff.json", "pointer": "/links/0", "value": {"use_case_id": "UC-01"}},
                },
                {
                    "turn": 2,
                    "request": {"artifact": "trace_index.json", "pointer": "/entities/UC-01"},
                    "response": {"artifact": "trace_index.json", "pointer": "/entities/UC-01", "value": []},
                },
            ]
        )
        self.assertEqual(valid["grounded_exact_index_reads"], 1)
        self.assertEqual(valid["violations"], [])
        invalid = acceptance.audit_tool_reads(
            [
                {
                    "turn": 1,
                    "request": {"artifact": "trace_index.json", "pointer": "/entities/UC-01/0"},
                    "response": {"artifact": "trace_index.json", "pointer": "/entities/UC-01/0", "value": {}},
                }
            ]
        )
        self.assertEqual(invalid["grounded_exact_index_reads"], 0)
        self.assertIn("non_exact_index_pointer", invalid["violations"][0])

    def test_measured_rule_accepts_three_net_trace_wins_without_hiding_other_rows(self) -> None:
        result = _assess("measured")
        self.assertEqual(result["interpretation"], "bounded_trace_navigation_advantage_observed")
        self.assertEqual(result["trace_population"]["packet_pairs"], 12)
        self.assertEqual(result["trace_population"]["net_additional_complete_trace_packets_for_b"], 3)
        self.assertEqual(result["trace_population"]["paired_complete_supported_diagnosis"]["only_B"], 3)
        self.assertEqual(len(result["rows"]), 40)
        self.assertEqual(result["manipulation_check"]["index_eligible_b_sessions"], 8)
        self.assertTrue(result["efficiency_checks"]["b_mean_raw_artifact_reads_do_not_exceed_a"])

    def test_missing_session_fails_closed_instead_of_improving_denominator(self) -> None:
        manifest, gold, runtime, score, audits = _payloads("measured")
        runtime["sessions"].pop()
        with self.assertRaisesRegex(acceptance.AcceptanceError, "all 40"):
            acceptance.assess_payloads(
                score=score,
                runtime=runtime,
                manifest=manifest,
                gold=gold,
                split="measured",
                runtime_sha256="sha256:runtime",
                manifest_sha256=acceptance.PUBLIC_MANIFEST_SHA256,
                gold_sha256=acceptance.GOLD_SHA256,
                read_audits=audits,
            )

    def test_clean_false_alarm_blocks_claim_even_when_trace_accuracy_improves(self) -> None:
        manifest, gold, runtime, score, audits = _payloads("measured")
        for row in score["rows"]:
            if row["arm"] == "B" and row["family"] == "clean":
                row["clean_false_alarm"] = True
                break
        result = acceptance.assess_payloads(
            score=score,
            runtime=runtime,
            manifest=manifest,
            gold=gold,
            split="measured",
            runtime_sha256="sha256:runtime",
            manifest_sha256=acceptance.PUBLIC_MANIFEST_SHA256,
            gold_sha256=acceptance.GOLD_SHA256,
            read_audits=audits,
        )
        self.assertEqual(result["interpretation"], "bounded_trace_navigation_advantage_not_observed")
        self.assertFalse(result["acceptance_checks"]["b_clean_false_alarms_do_not_exceed_a"])

    def test_rejected_read_attempt_is_retained_as_invalid_not_an_inventory_crash(self) -> None:
        manifest, gold, runtime, score, audits = _payloads("measured")
        target = next(row for row in runtime["sessions"] if row["arm"] == "A" and row["packet_id"] == "p005")
        target["status"] = "invalid_response"
        target["reads"] += 1
        scored = next(row for row in score["rows"] if row["session_id"] == target["session_id"])
        scored["status"] = "invalid_response"
        scored["complete_supported_diagnosis"] = False
        scored["scoring_error"] = "rejected read"
        result = acceptance.assess_payloads(
            score=score,
            runtime=runtime,
            manifest=manifest,
            gold=gold,
            split="measured",
            runtime_sha256="sha256:runtime",
            manifest_sha256=acceptance.PUBLIC_MANIFEST_SHA256,
            gold_sha256=acceptance.GOLD_SHA256,
            read_audits=audits,
        )
        row = next(value for value in result["rows"] if value["session_id"] == target["session_id"])
        self.assertTrue(row["invalid_or_noncompleted"])
        self.assertEqual(row["unattributed_rejected_read_attempts"], 1)
        self.assertFalse(result["efficiency_checks"]["all_read_attempts_have_tool_receipts"])

    def test_development_gate_checks_manipulation_without_claiming_accuracy(self) -> None:
        result = _assess("development")
        self.assertTrue(result["development_gate_passed"])
        self.assertFalse(result["measured_thresholds_evaluated"])
        self.assertFalse(result["claim_allowed"])


if __name__ == "__main__":
    unittest.main()
