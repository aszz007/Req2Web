from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_acceptance import (
    compile_acceptance_binding,
    compile_acceptance_plan,
    execute_acceptance_binding_plan,
    project_requirement_view,
)
from req2web_evaluation import (
    ACCEPTANCE_EVALUATION_SCHEMA_VERSION,
    GoldAcceptanceResult,
    aggregate_requirement_outcomes,
    classify_alignment_match,
    evaluate_acceptance,
    map_acceptance_criteria_to_gold_obligations,
    normalize_candidate_decisions,
    normalize_gold_obligations,
)
from req2web_agent import UseCase
from req2web_generation import DeterministicPageRenderer, PageSpecBuilder
from test_acceptance_binding import make_bundle
from test_acceptance_browser_executor import FakeBrowserBackend


class AcceptanceEvaluationTest(unittest.TestCase):
    case_id = "case-acceptance-evaluation"

    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_acceptance_evaluation" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.bundle = make_bundle()
        self.view = project_requirement_view(self.bundle)
        self.plan = compile_acceptance_plan(self.view)
        self.spec = PageSpecBuilder().build(self.bundle)
        self.render = DeterministicPageRenderer().render(self.spec, self.root / "page")
        self.binding = compile_acceptance_binding(self.view, self.plan, self.spec, self.render)
        self.gold = normalize_gold_obligations(self.case_id, self.plan)
        self.candidate = normalize_candidate_decisions(self.case_id, self.spec)
        self.page_url = self.render.index_html.resolve().as_uri()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        if self.root.parent.exists() and not any(self.root.parent.iterdir()):
            self.root.parent.rmdir()

    def browser_report(self, **backend_options):
        return execute_acceptance_binding_plan(
            self.binding,
            self.page_url,
            backend=FakeBrowserBackend(self.binding, **backend_options),
        )

    def evaluate(self, **backend_options):
        return evaluate_acceptance(
            self.case_id,
            self.plan,
            self.binding,
            self.browser_report(**backend_options),
            self.spec,
            self.gold,
            self.candidate,
        )

    def test_all_pass_report_has_complete_identity_metrics_and_exact_alignment(self) -> None:
        browser = self.browser_report()
        report = evaluate_acceptance(self.case_id, self.plan, self.binding, browser, self.spec, self.gold, self.candidate)

        self.assertEqual(report.schema_version, ACCEPTANCE_EVALUATION_SCHEMA_VERSION)
        report.validate_against(self.plan, self.binding, browser, self.spec, self.gold, self.candidate)
        self.assertEqual(len(report.gold_results), len(self.gold.obligations))
        self.assertEqual(report.source_browser_execution_sha256, browser.sha256())
        self.assertEqual(report.source_binding_plan_sha256, self.binding.sha256())
        self.assertEqual(report.source_page_spec_sha256, self.candidate.source_page_spec_sha256)
        metrics = {item.metric_name: item for item in report.metrics}
        self.assertEqual(metrics["independent_acceptance_coverage"].denominator, len(self.gold.obligations))
        self.assertEqual(metrics["all_gold_criterion_success"].denominator, len(self.gold.obligations))
        by_gold = {item.gold_unit_id: item for item in report.gold_results}
        for alignment in report.alignment_set.alignments:
            self.assertEqual(alignment.evaluation_result, by_gold[alignment.gold_unit_id].evaluation_result)
            expected_match = "zero_decision" if not alignment.decision_ids else "one_decision" if len(alignment.decision_ids) == 1 else "many_decisions"
            self.assertEqual(alignment.match_type, expected_match)
        self.assertTrue(any(item.evaluation_result == "not_supported" for item in report.gold_results))
        self.assertEqual(self.gold.to_dict(), normalize_gold_obligations(self.case_id, self.plan).to_dict())

    def test_candidate_authenticity_rejects_entity_or_semantic_forgery_but_allows_attribution_enrichment(self) -> None:
        entity_forged = replace(
            self.candidate,
            decisions=tuple(
                replace(item, candidate_entity_ids=(f"forged-entity-{index:03d}",))
                for index, item in enumerate(self.candidate.decisions)
            ),
        )
        entity_forged.validate()
        with self.assertRaises(ValueError):
            entity_forged.validate_against(self.spec)
        with self.assertRaises(ValueError):
            evaluate_acceptance(self.case_id, self.plan, self.binding, self.browser_report(), self.spec, self.gold, entity_forged)

        semantic_forged = replace(
            self.candidate,
            decisions=(replace(self.candidate.decisions[0], semantic_key="forged-semantic"), *self.candidate.decisions[1:]),
        )
        with self.assertRaises(ValueError):
            semantic_forged.validate_against(self.spec)

        original = self.candidate.decisions[0]
        enriched = replace(
            self.candidate,
            decisions=(
                replace(
                    original,
                    attribution_labels=("audited_candidate",),
                    attribution_edge_ids=("audit-edge-001",),
                    identity_validation_status="validated",
                ),
                *self.candidate.decisions[1:],
            ),
        )
        enriched.validate_against(self.spec)
        report = evaluate_acceptance(self.case_id, self.plan, self.binding, self.browser_report(), self.spec, self.gold, enriched)
        self.assertEqual(report.source_candidate_decision_set_sha256, enriched.sha256())

    def test_public_match_label_function_exercises_zero_one_and_many_labels(self) -> None:
        self.assertEqual(classify_alignment_match(()), "zero_decision")
        self.assertEqual(classify_alignment_match(("decision-a",)), "one_decision")
        self.assertEqual(classify_alignment_match(("decision-b", "decision-a")), "many_decisions")
        with self.assertRaises(ValueError):
            classify_alignment_match(("decision-a", "decision-a"))

    def test_real_pagespec_label_text_cannot_create_terminal_alignment(self) -> None:
        spec = copy.deepcopy(self.spec)
        constraint = next(item for item in self.plan.criteria if item.source_kind == "constraint")
        constraint_text = dict(constraint.expected_payload)["description"]
        spec.components[0].label = constraint_text
        spec.validate()
        render = DeterministicPageRenderer().render(spec, self.root / "label-text-page")
        binding = compile_acceptance_binding(self.view, self.plan, spec, render)
        browser = execute_acceptance_binding_plan(binding, render.index_html.resolve().as_uri(), backend=FakeBrowserBackend(binding))
        candidate = normalize_candidate_decisions(self.case_id, spec)
        candidate.validate_against(spec)
        report = evaluate_acceptance(self.case_id, self.plan, binding, browser, spec, self.gold, candidate)
        mapped = dict(map_acceptance_criteria_to_gold_obligations(self.plan, self.gold))
        terminal_alignment = next(item for item in report.alignment_set.alignments if item.gold_unit_id == mapped[constraint.criterion_id])
        self.assertEqual(spec.components[0].label, constraint_text)
        self.assertEqual(terminal_alignment.match_type, "zero_decision")
        self.assertEqual(terminal_alignment.evaluation_result, "not_supported")

    def test_browser_fail_unknown_and_inherited_not_supported_preserve_d04_states(self) -> None:
        selector = next(
            item.selector for item in self.binding.steps
            if item.action_kind == "assert_element_exists"
        )
        fail_report = self.evaluate(missing_selectors={selector})
        unknown_report = self.evaluate(raise_on_navigate=True)

        self.assertTrue(any(item.evaluation_result == "fail" for item in fail_report.gold_results))
        self.assertTrue(any(item.evaluation_result == "unknown" for item in unknown_report.gold_results))
        self.assertTrue(any(item.evaluation_result == "not_supported" for item in fail_report.gold_results))
        unknown_metric = next(item for item in unknown_report.metrics if item.metric_name == "executable_pass_rate")
        self.assertGreater(unknown_metric.unknown_count, 0)
        self.assertEqual(unknown_metric.numerator, 0)
        self.assertEqual(unknown_metric.denominator, 0)
        self.assertEqual(unknown_metric.decimal_value, "not_applicable")

    def test_deduplicated_gold_unions_candidate_ids_and_uses_conservative_status_priority(self) -> None:
        duplicated_bundle = make_bundle()
        duplicated_bundle.use_cases.append(UseCase(
            "use-case-search-duplicate", "Duplicate search", "shopper",
            "find a matching product", "a result list is shown",
        ))
        view = project_requirement_view(duplicated_bundle)
        plan = compile_acceptance_plan(view)
        gold = normalize_gold_obligations(self.case_id, plan)
        mapping = map_acceptance_criteria_to_gold_obligations(plan, gold)
        self.assertLess(len(gold.obligations), len(plan.criteria))
        self.assertLess(len({gold_id for _, gold_id in mapping}), len(mapping))

        spec = PageSpecBuilder().build(duplicated_bundle)
        render = DeterministicPageRenderer().render(spec, self.root / "duplicate-page")
        binding = compile_acceptance_binding(view, plan, spec, render)
        duplicate_criterion = next(
            item
            for item in plan.criteria
            if item.source_id == "use-case-search-duplicate"
        )
        duplicate_binding = next(
            item
            for item in binding.bindings
            if item.criterion_id == duplicate_criterion.criterion_id
        )
        steps_by_id = {item.step_id: item for item in binding.steps}
        missing_selector = next(
            steps_by_id[step_id].selector
            for step_id in duplicate_binding.step_ids
            if steps_by_id[step_id].action_kind == "assert_element_exists"
        )
        browser = execute_acceptance_binding_plan(
            binding,
            render.index_html.resolve().as_uri(),
            backend=FakeBrowserBackend(
                binding,
                missing_selectors={missing_selector},
            ),
        )
        candidate = normalize_candidate_decisions(self.case_id, spec)
        report = evaluate_acceptance(self.case_id, plan, binding, browser, spec, gold, candidate)
        deduplicated = next(item for item in report.gold_results if len(item.source_criterion_ids) > 1)
        self.assertEqual(deduplicated.evaluation_result, "fail")
        alignment = next(item for item in report.alignment_set.alignments if item.gold_unit_id == deduplicated.gold_unit_id)
        self.assertEqual(alignment.decision_ids, deduplicated.matched_decision_ids)

    def test_hash_and_case_mismatches_fail_closed(self) -> None:
        browser = self.browser_report()
        with self.subTest("case"):
            with self.assertRaises(ValueError):
                evaluate_acceptance("different-case", self.plan, self.binding, browser, self.spec, self.gold, self.candidate)
        with self.subTest("gold-source"):
            forged_gold = replace(self.gold, source_acceptance_plan_sha256="0" * 64)
            with self.assertRaises(ValueError):
                evaluate_acceptance(self.case_id, self.plan, self.binding, browser, self.spec, forged_gold, self.candidate)
        with self.subTest("candidate-page"):
            forged_candidate = replace(self.candidate, source_page_spec_sha256="0" * 64)
            with self.assertRaises(ValueError):
                evaluate_acceptance(self.case_id, self.plan, self.binding, browser, self.spec, self.gold, forged_candidate)
        with self.subTest("browser-page"):
            forged_browser = replace(browser, source_page_spec_sha256="0" * 64)
            with self.assertRaises(ValueError):
                evaluate_acceptance(self.case_id, self.plan, self.binding, forged_browser, self.spec, self.gold, self.candidate)
        with self.subTest("binding-plan"):
            forged_binding = replace(self.binding, source_acceptance_plan_sha256="0" * 64)
            with self.assertRaises(ValueError):
                evaluate_acceptance(self.case_id, self.plan, forged_binding, browser, self.spec, self.gold, self.candidate)

    def test_requirement_outcome_d08_hard_soft_matrix(self) -> None:
        def result(unit_id: str, hardness: str, status: str) -> GoldAcceptanceResult:
            return GoldAcceptanceResult(
                unit_id, ("requirement-synthetic",), hardness, status,
                (f"criterion-{unit_id}",), (), status != "not_supported",
            )

        cases = {
            "unmet": (result("a-hard-fail", "hard", "fail"), result("b-soft-pass", "soft", "pass")),
            "unknown": (result("a-hard-unknown", "hard", "unknown"), result("b-soft-pass", "soft", "pass")),
            "met": (result("a-hard-pass", "hard", "pass"), result("b-soft-pass", "soft", "pass")),
            "partial": (result("a-hard-pass", "hard", "pass"), result("b-soft-not-supported", "soft", "not_supported")),
        }
        for expected, records in cases.items():
            with self.subTest(expected=expected):
                outcomes = aggregate_requirement_outcomes(records)
                self.assertEqual(outcomes[0].outcome, expected)

    def test_canonical_report_is_stable_and_does_not_mutate_inputs(self) -> None:
        browser = self.browser_report()
        before = copy.deepcopy((
            self.plan.to_dict(), self.binding.to_dict(), browser.to_dict(),
            self.gold.to_dict(), self.candidate.to_dict(),
        ))
        first = evaluate_acceptance(self.case_id, self.plan, self.binding, browser, self.spec, self.gold, self.candidate)
        second = evaluate_acceptance(self.case_id, self.plan, self.binding, browser, self.spec, self.gold, self.candidate)

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.sha256(), second.sha256())
        self.assertEqual(before, (
            self.plan.to_dict(), self.binding.to_dict(), browser.to_dict(),
            self.gold.to_dict(), self.candidate.to_dict(),
        ))
        with self.assertRaises(ValueError):
            replace(first, source_browser_execution_sha256="f" * 64).validate_against(
                self.plan, self.binding, browser, self.spec, self.gold, self.candidate
            )


if __name__ == "__main__":
    unittest.main()
