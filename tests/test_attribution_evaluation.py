from __future__ import annotations

import copy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_evaluation_decision_units import make_page_spec, make_plan  # noqa: E402
from req2web_evaluation import (  # noqa: E402
    ATTRIBUTION_EVALUATION_SCHEMA_VERSION,
    CandidateAttributionEdgeSet,
    aggregate_attribution_evaluations,
    create_candidate_attribution_edge,
    create_decision_alignment,
    create_evaluator_only_gold_edge,
    evaluate_attribution,
    evaluate_no_valid_candidate,
    freeze_candidate_attribution_edges,
    freeze_decision_alignments,
    freeze_evaluator_only_gold_edges,
    normalize_candidate_decisions,
    normalize_gold_obligations,
)
from req2web_generation.schema import ConstraintSpec  # noqa: E402


class AttributionEvaluationTests(unittest.TestCase):
    def _inputs(self, case_id: str, *, extra_constraint: bool = False):
        plan = make_plan()
        spec = copy.deepcopy(make_page_spec())
        if extra_constraint:
            spec.constraints.append(ConstraintSpec("extra-constraint", "Keep the secondary layout stable.", "agent_context.constraints"))
            spec.validate()
        gold = normalize_gold_obligations(case_id, plan)
        candidate = normalize_candidate_decisions(case_id, spec)
        return plan, spec, gold, candidate

    def _enrich(self, candidate, declarations):
        """Declarations: (decision_index, kind, source_id, relation, visible, preregistered, status)."""
        edges = []
        for index, kind, source_id, relation, visible, preregistered, edge_status in declarations:
            decision = candidate.decisions[index]
            edges.append(create_candidate_attribution_edge(
                case_id=candidate.case_id, decision_id=decision.decision_id, attribution_kind=kind,
                source_id=source_id, relation=relation, provider_visible=visible,
                pre_registered=preregistered, identity_validation_status=edge_status,
            ))
        grouped = {record.decision_id: [] for record in candidate.decisions}
        for edge in edges:
            grouped[edge.decision_id].append(edge)
        records = []
        label_for_kind = {
            "requirement": "requirement_attributed", "evidence": "evidence_attributed",
            "pre_registered_policy": "pre_registered_policy_attributed",
            "local_identity_scaffold": "local_identity_scaffold",
        }
        for decision in candidate.decisions:
            owned = grouped[decision.decision_id]
            statuses = {edge.identity_validation_status for edge in owned}
            status = "no_claim" if not owned else "mixed" if statuses == {"validated", "rejected"} else next(iter(statuses))
            records.append(replace(
                decision,
                attribution_labels=tuple(sorted({label_for_kind[edge.attribution_kind] for edge in owned})),
                attribution_edge_ids=tuple(sorted(edge.edge_id for edge in owned)),
                identity_validation_status=status,
            ))
        enriched = replace(candidate, decisions=tuple(records))
        enriched.validate()
        return enriched, tuple(edges)

    def _align(self, gold, candidate, decision_index: int = 0):
        target = gold.obligations[0]
        selected = candidate.decisions[decision_index].decision_id
        records = []
        for obligation in gold.obligations:
            decision_ids = (selected,) if obligation.gold_unit_id == target.gold_unit_id else ()
            records.append(create_decision_alignment(
                gold_unit_id=obligation.gold_unit_id, decision_ids=decision_ids,
                match_type="one_decision" if decision_ids else "zero_decision", evaluation_result="pass",
            ))
        return target, freeze_decision_alignments(gold, candidate, records)

    @staticmethod
    def _metric(report, scope: str):
        return next(item for item in report.coverage_metrics if item.attribution_scope == scope)

    def test_candidate_coverage_valid_rejected_mixed_and_no_claim(self) -> None:
        _, spec, gold, base = self._inputs("case-coverage")
        candidate, edges = self._enrich(base, (
            (0, "requirement", "requirement-search", "supports", True, False, "validated"),
            (1, "evidence", "evidence-rejected", "supports", True, False, "rejected"),
            (2, "pre_registered_policy", "policy-visible", "governs", True, True, "validated"),
            (2, "evidence", "evidence-rejected-2", "supports", True, False, "rejected"),
        ))
        _, alignments = self._align(gold, candidate)
        edge_set = freeze_candidate_attribution_edges(candidate_decisions=candidate, d17_path="path_1", edges=edges)
        report = evaluate_attribution("case-coverage", spec, gold, candidate, alignments, edge_set)

        self.assertEqual(report.candidate_case_status, "eligible_candidate_present")
        self.assertEqual(self._metric(report, "all_eligible").numerator, 2)
        self.assertEqual(self._metric(report, "requirement").numerator, 1)
        self.assertEqual(self._metric(report, "evidence").numerator, 0)
        self.assertEqual(self._metric(report, "pre_registered_policy").numerator, 1)
        self.assertEqual(self._metric(report, "all_eligible").denominator, len(candidate.decisions))

    def test_d17_path_eligibility_and_scaffold_exclusion(self) -> None:
        _, spec, gold, base = self._inputs("case-path")
        candidate, edges = self._enrich(base, (
            (0, "requirement", "requirement-search", "supports", True, False, "validated"),
            (1, "pre_registered_policy", "policy-visible", "governs", True, True, "validated"),
            (2, "pre_registered_policy", "policy-hidden", "governs", False, True, "validated"),
            (3, "local_identity_scaffold", "canonical-trace", "binds", False, False, "validated"),
            (4, "evidence", "evidence-visible", "supports", True, False, "validated"),
        ))
        _, alignments = self._align(gold, candidate)
        path_three = freeze_candidate_attribution_edges(candidate_decisions=candidate, d17_path="path_3", edges=edges)
        report = evaluate_attribution("case-path", spec, gold, candidate, alignments, path_three)
        self.assertEqual(self._metric(report, "all_eligible").numerator, 2)
        self.assertEqual(self._metric(report, "requirement").numerator, 1)
        self.assertEqual(self._metric(report, "pre_registered_policy").numerator, 1)
        self.assertEqual(self._metric(report, "evidence").applicability, "not_applicable")
        self.assertEqual(report.deep_label_correctness.applicability, "not_applicable")
        self.assertEqual(report.excluded_local_identity_scaffold_edge_count, 1)

        path_one = freeze_candidate_attribution_edges(candidate_decisions=candidate, d17_path="path_1", edges=edges)
        report_one = evaluate_attribution("case-path", spec, gold, candidate, alignments, path_one)
        self.assertEqual(self._metric(report_one, "evidence").numerator, 1)
        self.assertEqual(self._metric(report_one, "all_eligible").numerator, 3)

    def test_deep_label_exact_id_relation_tp_fp_fn_and_rejected_claim(self) -> None:
        _, spec, gold, base = self._inputs("case-deep")
        candidate, edges = self._enrich(base, (
            (0, "evidence", "evidence-a", "supports", True, False, "validated"),
            (0, "evidence", "evidence-extra", "supports", True, False, "validated"),
            (1, "evidence", "evidence-a", "supports", True, False, "rejected"),
        ))
        target, alignments = self._align(gold, candidate, 0)
        gold_edges = freeze_evaluator_only_gold_edges(gold_obligations=gold, edges=(
            create_evaluator_only_gold_edge(case_id="case-deep", gold_unit_id=target.gold_unit_id, source_id="evidence-a", relation="supports"),
            create_evaluator_only_gold_edge(case_id="case-deep", gold_unit_id=target.gold_unit_id, source_id="evidence-missing", relation="supports"),
        ))
        edge_set = freeze_candidate_attribution_edges(candidate_decisions=candidate, d17_path="path_2", edges=edges)
        report = evaluate_attribution("case-deep", spec, gold, candidate, alignments, edge_set, gold_edges)
        deep = report.deep_label_correctness
        self.assertEqual((deep.true_positive_count, deep.false_positive_count, deep.false_negative_count), (1, 2, 1))
        self.assertEqual((deep.precision, deep.recall), ("0.333333", "0.500000"))

    def test_legal_candidate_without_claims_keeps_candidate_denominator(self) -> None:
        _, spec, gold, candidate = self._inputs("case-valid-no-claims")
        target, alignments = self._align(gold, candidate)
        gold_edges = freeze_evaluator_only_gold_edges(gold_obligations=gold, edges=(
            create_evaluator_only_gold_edge(case_id="case-valid-no-claims", gold_unit_id=target.gold_unit_id, source_id="evidence-a", relation="supports"),
        ))
        report = evaluate_attribution("case-valid-no-claims", spec, gold, candidate, alignments,
            freeze_candidate_attribution_edges(candidate_decisions=candidate, d17_path="path_1", edges=()), gold_edges)
        self.assertEqual((report.source_mode, report.candidate_case_status), ("valid_candidate", "valid_candidate_no_claims"))
        coverage = self._metric(report, "all_eligible")
        self.assertEqual((coverage.numerator, coverage.denominator, coverage.decimal_value, coverage.forced_zero_reason),
                         (0, len(candidate.decisions), "0.000000", None))
        self.assertEqual((report.deep_label_correctness.precision, report.deep_label_correctness.recall), ("undefined", "0.000000"))
        report.validate_against(page_spec=spec, gold_obligations=gold, candidate_decisions=candidate,
            alignments=alignments, candidate_edges=freeze_candidate_attribution_edges(candidate_decisions=candidate, d17_path="path_1", edges=()),
            evaluator_only_gold_edges=gold_edges)

    def test_no_valid_candidate_uses_independent_failure_api_and_enters_macro(self) -> None:
        _, spec_a, gold_a, base_a = self._inputs("case-a")
        candidate_a, edges_a = self._enrich(base_a, ((0, "requirement", "requirement-search", "supports", True, False, "validated"),))
        _, align_a = self._align(gold_a, candidate_a)
        report_a = evaluate_attribution("case-a", spec_a, gold_a, candidate_a, align_a,
            freeze_candidate_attribution_edges(candidate_decisions=candidate_a, d17_path="path_1", edges=edges_a))

        _, _, gold_b, _ = self._inputs("case-no-valid", extra_constraint=True)
        target_b = gold_b.obligations[0]
        gold_edges_b = freeze_evaluator_only_gold_edges(gold_obligations=gold_b, edges=(
            create_evaluator_only_gold_edge(case_id="case-no-valid", gold_unit_id=target_b.gold_unit_id, source_id="evidence-b", relation="supports"),
        ))
        report_b = evaluate_no_valid_candidate("case-no-valid", "path_1", gold_b, gold_edges_b)
        self.assertEqual((report_b.source_mode, report_b.candidate_case_status), ("no_valid_candidate", "no_valid_candidate"))
        for metric in report_b.coverage_metrics:
            if metric.applicability == "applicable":
                self.assertEqual((metric.numerator, metric.denominator, metric.decimal_value, metric.forced_zero_reason),
                                 (0, 0, "0.000000", "model_did_not_produce_legal_candidate_pagespec"))
        self.assertEqual((report_b.source_page_spec_sha256, report_b.source_candidate_decision_set_sha256,
                          report_b.source_alignment_set_sha256, report_b.source_candidate_attribution_edge_set_sha256),
                         (None, None, None, None))
        self.assertEqual((report_b.deep_label_correctness.precision, report_b.deep_label_correctness.recall), ("undefined", "0.000000"))
        report_b.validate_no_valid_candidate_against(gold_obligations=gold_b, evaluator_only_gold_edges=gold_edges_b)
        with self.assertRaises(ValueError):
            report_b.validate_against(page_spec=spec_a, gold_obligations=gold_a, candidate_decisions=candidate_a,
                alignments=align_a, candidate_edges=freeze_candidate_attribution_edges(candidate_decisions=candidate_a, d17_path="path_1", edges=edges_a))

        macro = aggregate_attribution_evaluations((report_b, report_a))
        coverage = next(item for item in macro.metrics if item.metric_name == "candidate_attribution_coverage:all_eligible")
        expected_macro = (Decimal(self._metric(report_a, "all_eligible").decimal_value) / Decimal(2)).quantize(Decimal("0.000001"))
        self.assertEqual(coverage.decimal_value, str(expected_macro))
        self.assertEqual(coverage.applicable_case_count, 2)
        self.assertEqual(coverage.defined_case_count, 2)

    def test_no_valid_candidate_path_three_rejects_gold_fixture_and_is_na(self) -> None:
        _, _, gold, _ = self._inputs("case-no-valid-path-three")
        report = evaluate_no_valid_candidate("case-no-valid-path-three", "path_3", gold)
        self.assertEqual(self._metric(report, "evidence").applicability, "not_applicable")
        self.assertEqual(report.deep_label_correctness.applicability, "not_applicable")
        gold_edge = freeze_evaluator_only_gold_edges(gold_obligations=gold, edges=(
            create_evaluator_only_gold_edge(case_id="case-no-valid-path-three", gold_unit_id=gold.obligations[0].gold_unit_id, source_id="evidence-c", relation="supports"),
        ))
        with self.assertRaises(ValueError):
            evaluate_no_valid_candidate("case-no-valid-path-three", "path_3", gold, gold_edge)

    def test_duplicate_tampered_and_source_path_mismatches_fail_closed(self) -> None:
        _, spec, gold, base = self._inputs("case-integrity")
        candidate, edges = self._enrich(base, ((0, "evidence", "evidence-a", "supports", True, False, "validated"),))
        target, alignments = self._align(gold, candidate)
        gold_edges = freeze_evaluator_only_gold_edges(gold_obligations=gold, edges=(
            create_evaluator_only_gold_edge(case_id="case-integrity", gold_unit_id=target.gold_unit_id, source_id="evidence-a", relation="supports"),
        ))
        edge_set = freeze_candidate_attribution_edges(candidate_decisions=candidate, d17_path="path_1", edges=edges)
        report = evaluate_attribution("case-integrity", spec, gold, candidate, alignments, edge_set, gold_edges)
        with self.subTest("duplicate"):
            with self.assertRaises(ValueError):
                freeze_candidate_attribution_edges(candidate_decisions=candidate, d17_path="path_1", edges=(edges[0], edges[0]))
        with self.subTest("tampered_edge"):
            with self.assertRaises(ValueError):
                freeze_candidate_attribution_edges(candidate_decisions=candidate, d17_path="path_1", edges=(replace(edges[0], source_id="tampered"),))
        with self.subTest("report_source"):
            with self.assertRaises(ValueError):
                replace(report, source_candidate_decision_set_sha256="0" * 64).validate_against(
                    page_spec=spec, gold_obligations=gold, candidate_decisions=candidate,
                    alignments=alignments, candidate_edges=edge_set, evaluator_only_gold_edges=gold_edges,
                )
        with self.subTest("path_three_gold"):
            path_three = freeze_candidate_attribution_edges(candidate_decisions=candidate, d17_path="path_3", edges=edges)
            with self.assertRaises(ValueError):
                evaluate_attribution("case-integrity", spec, gold, candidate, alignments, path_three, gold_edges)


if __name__ == "__main__":
    unittest.main()
