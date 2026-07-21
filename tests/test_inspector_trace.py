from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
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
    evaluate_acceptance,
    normalize_candidate_decisions,
    normalize_gold_obligations,
)
from req2web_generation import (
    DeterministicPageRenderer,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
from req2web_generation.retrieval_influence import _report_id
from req2web_inspector import (
    InspectorFactSourceIsolationError,
    InspectorSourceUseCaseLink,
    InspectorTraceError,
    TraceGap,
    build_inspector_element_acceptance_trace,
    candidate_decision_ids_for_entity,
    canonical_sha256,
    project_g0_inspector_facts,
    project_model_inspector_facts,
)
from req2web_inspector.trace import _stable_id
from req2web_rag.corpus import ROLE_ORDER
from test_acceptance_browser_executor import FakeBrowserBackend
from test_guided_page_spec import build_context


class InspectorTraceTest(unittest.TestCase):
    case_id = "development-inspector-trace"

    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_inspector_trace" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.context = build_context()
        self.guidance = RetrievalGuidanceBuilder().build(self.context)
        self.builder = RetrievalGuidedPageSpecBuilder()
        self.guided = self.builder.build(self.context, self.guidance)
        self.ablations = {
            role: self.builder.build(self.context, self.guidance, disabled_roles=(role,))
            for role in ROLE_ORDER
        }
        self.render = DeterministicPageRenderer().render(self.guided.page_spec, self.root / "page")
        self.influence = RetrievalInfluenceChecker().check(
            self.context,
            self.guidance,
            PageSpecBuilder().build(self.context),
            self.guided,
            self.ablations,
            self.render,
        )
        self.view = project_requirement_view(self.context)
        self.plan = compile_acceptance_plan(self.view)
        self.binding = compile_acceptance_binding(self.view, self.plan, self.guided.page_spec, self.render)
        self.gold = normalize_gold_obligations(self.case_id, self.plan)
        self.candidate = normalize_candidate_decisions(self.case_id, self.guided.page_spec)
        self.fact_set = project_g0_inspector_facts(
            self.guidance, self.guided, self.guided.page_spec, self.influence
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        if self.root.parent.exists() and not any(self.root.parent.iterdir()):
            self.root.parent.rmdir()

    def _trace(self, **backend_options):
        browser = execute_acceptance_binding_plan(
            self.binding,
            self.render.index_html.resolve().as_uri(),
            backend=FakeBrowserBackend(self.binding, **backend_options),
        )
        evaluation = evaluate_acceptance(
            self.case_id, self.plan, self.binding, browser,
            self.guided.page_spec, self.gold, self.candidate,
        )
        return build_inspector_element_acceptance_trace(
            self.fact_set,
            guidance=self.guidance,
            guided_build_result=self.guided,
            page_spec=self.guided.page_spec,
            retrieval_influence_report=self.influence,
            requirement_view=self.view,
            acceptance_plan=self.plan,
            render_result=self.render,
            binding_plan=self.binding,
            browser_report=browser,
            gold_obligations=self.gold,
            candidate_decisions=self.candidate,
            acceptance_evaluation_report=evaluation,
        )

    def test_real_g0_trace_is_canonical_immutable_and_non_mutating(self) -> None:
        before = (
            deepcopy(self.guidance.to_dict()), deepcopy(self.guided.to_dict()),
            deepcopy(self.guided.page_spec.to_dict()), deepcopy(self.fact_set.to_dict()),
        )
        first = self._trace()
        second = self._trace()
        self.assertEqual(first.canonical_json_bytes(), second.canonical_json_bytes())
        self.assertEqual(first.sha256(), second.sha256())
        self.assertEqual(len(first.decision_observations), len(self.fact_set.facts))
        self.assertEqual({item.disposition for item in first.decision_observations}, {"adopted", "ignored", "fallback"})
        self.assertTrue(any(not item.trace_link_ids for item in first.decision_observations))
        self.assertTrue(first.element_traces)
        self.assertTrue(first.alignment_traces)
        self.assertTrue(first.criterion_traces)
        self.assertTrue(any(item.runtime_status == "not_supported" for item in first.acceptance_observations))
        self.assertTrue(all(item.runtime_status in {"pass", "fail", "unknown", "not_supported"} for item in first.criterion_traces))
        with self.assertRaises(FrozenInstanceError):
            first.page_id = "forged"  # type: ignore[misc]
        self.assertEqual(before[0], self.guidance.to_dict())
        self.assertEqual(before[1], self.guided.to_dict())
        self.assertEqual(before[2], self.guided.page_spec.to_dict())
        self.assertEqual(before[3], self.fact_set.to_dict())

    def test_exact_join_matrix_retains_component_interaction_constraint_state_and_acceptance_entities(self) -> None:
        trace = self._trace()
        kinds = {item.field_path.split("[", 1)[0].split(".", 1)[0] for item in trace.element_traces}
        self.assertTrue({"component", "interaction", "constraint"}.issubset(kinds))
        candidate_by_entity = {
            entity_id: set(candidate_decision_ids_for_entity(self.candidate, self.guided.page_spec, entity_id))
            for entity_id in [
                self.guided.page_spec.components[0].component_id,
                self.guided.page_spec.states[0].state_id,
                self.guided.page_spec.interactions[0].interaction_id,
                self.guided.page_spec.constraints[0].constraint_id,
                self.guided.page_spec.acceptance_checks[0].check_id,
            ]
        }
        self.assertTrue(candidate_by_entity[self.guided.page_spec.components[0].component_id])
        self.assertTrue(candidate_by_entity[self.guided.page_spec.states[0].state_id])
        self.assertTrue(candidate_by_entity[self.guided.page_spec.interactions[0].interaction_id])
        self.assertTrue(candidate_by_entity[self.guided.page_spec.constraints[0].constraint_id])
        self.assertTrue(candidate_by_entity[self.guided.page_spec.acceptance_checks[0].check_id])
        for element in trace.element_traces:
            expected = candidate_by_entity.get(element.entity_id)
            if expected is not None:
                self.assertEqual(element.candidate_decision_ids, tuple(sorted(expected)))

    def test_one_to_many_alignments_and_gaps_are_retained_without_text_matching(self) -> None:
        trace = self._trace()
        by_element: dict[str, set[str]] = {}
        for item in trace.alignment_traces:
            by_element.setdefault(item.element_trace_id, set()).add(item.alignment_id)
        self.assertTrue(any(len(items) > 1 for items in by_element.values()))
        gaps = {(item.hop, item.status) for item in trace.gaps}
        self.assertIn(("alignment", "missing"), gaps)
        self.assertIn(("candidate_decision", "missing"), gaps)
        self.assertIn(("candidate_decision", "not_applicable"), gaps)

    def test_source_use_case_verified_missing_and_context_not_applicable_are_explicit(self) -> None:
        trace = self._trace()
        self.assertTrue(any(item.status == "verified" for item in trace.source_use_case_links))
        self.assertTrue(any(item.source_kind == "guided_agent_context_fallback" and item.status == "not_applicable" for item in trace.source_use_case_links))
        unsigned = InspectorSourceUseCaseLink("", "source-1", "guided_retrieval", "missing", ())
        missing = replace(unsigned, source_use_case_link_id=_stable_id("inspector-source-use-case", unsigned.to_payload()))
        missing.validate()
        self.assertEqual(missing.status, "missing")

    def test_terminal_fail_not_supported_and_unknown_runtime_are_preserved(self) -> None:
        not_supported = self._trace()
        self.assertTrue(any(item.binding_terminal_status == "not_supported" and item.runtime_status == "not_supported" for item in not_supported.acceptance_observations))
        trigger = next(item.selector for item in self.binding.steps if item.action_kind == "trigger_interaction")
        failed = self._trace(fail_trigger_selectors={trigger})
        self.assertTrue(any(item.runtime_status == "fail" for item in failed.criterion_traces))
        unknown = self._trace(raise_on_navigate=True)
        self.assertTrue(any(item.runtime_status == "unknown" for item in unknown.criterion_traces))

    def test_failed_influence_report_remains_visible(self) -> None:
        failed_check = replace(self.influence.checks[0], status="fail")
        altered = replace(self.influence, checks=[failed_check, *self.influence.checks[1:]], passed=False, report_id="")
        altered = replace(altered, report_id=_report_id(altered))
        failed_fact_set = project_g0_inspector_facts(
            self.guidance, self.guided, self.guided.page_spec, altered
        )
        browser = execute_acceptance_binding_plan(
            self.binding, self.render.index_html.resolve().as_uri(), backend=FakeBrowserBackend(self.binding)
        )
        evaluation = evaluate_acceptance(self.case_id, self.plan, self.binding, browser, self.guided.page_spec, self.gold, self.candidate)
        trace = build_inspector_element_acceptance_trace(
            failed_fact_set,
            guidance=self.guidance, guided_build_result=self.guided,
            page_spec=self.guided.page_spec, retrieval_influence_report=altered,
            requirement_view=self.view, acceptance_plan=self.plan, render_result=self.render,
            binding_plan=self.binding, browser_report=browser, gold_obligations=self.gold,
            candidate_decisions=self.candidate, acceptance_evaluation_report=evaluation,
        )
        self.assertFalse(trace.retrieval_influence_report_passed)
        self.assertIn((failed_check.check_id, "fail"), trace.influence_checks)

    def test_tampered_fact_source_case_and_path_fail_closed(self) -> None:
        with self.assertRaises((InspectorTraceError, ValueError)):
            self._call_with_facts(replace(self.fact_set, page_spec_sha256="0" * 64))
        bad_link = replace(self.fact_set.trace_links[0], field_path="component[forged].label")
        forged_facts = replace(self.fact_set, trace_links=(bad_link, *self.fact_set.trace_links[1:]))
        with self.assertRaises((InspectorTraceError, ValueError)):
            self._call_with_facts(forged_facts)
        other_candidate = normalize_candidate_decisions("other-case", self.guided.page_spec)
        with self.assertRaises((InspectorTraceError, ValueError)):
            self._call_with_facts(self.fact_set, candidate=other_candidate)

    def test_rehashed_forged_candidate_without_alignment_fails_closed(self) -> None:
        trace = self._trace()
        original = next(item for item in trace.element_traces if not item.candidate_decision_ids)
        forged = self._resign(
            original,
            "element_trace_id",
            "inspector-element-trace",
            candidate_decision_ids=("forged-candidate",),
        )
        gaps = tuple(
            self._resign(item, "gap_id", "inspector-trace-gap", owner_id=forged.element_trace_id)
            if item.owner_kind == "element_trace" and item.owner_id == original.element_trace_id
            else item
            for item in trace.gaps
        )
        forged_report = replace(
            trace,
            trace_report_id="",
            element_traces=tuple(sorted(
                (forged if item.element_trace_id == original.element_trace_id else item for item in trace.element_traces),
                key=lambda item: item.element_trace_id,
            )),
            gaps=tuple(sorted(gaps, key=lambda item: item.gap_id)),
        )
        forged_report = replace(
            forged_report,
            trace_report_id=_stable_id("inspector-element-acceptance-trace", forged_report.to_payload()),
        )
        with self.assertRaises(InspectorTraceError):
            forged_report.validate()

    def test_duplicate_non_influence_evidence_key_fails_closed(self) -> None:
        trace = self._trace()
        step = next(item for criterion in trace.criterion_traces for item in criterion.runtime_steps)
        with self.assertRaises(InspectorTraceError):
            replace(step, evidence=(("same-key", "one"), ("same-key", "two"))).validate()

    def test_rehashed_extra_alignment_missing_gap_fails_closed(self) -> None:
        trace = self._trace()
        element = next(item for item in trace.element_traces if item.candidate_decision_ids)
        extra_gap = TraceGap(
            "", element.element_trace_id, "element_trace", "alignment", "missing", ("forged-candidate",)
        )
        extra_gap = self._resign(extra_gap, "gap_id", "inspector-trace-gap")
        forged_report = replace(
            trace,
            trace_report_id="",
            gaps=tuple(sorted((*trace.gaps, extra_gap), key=lambda item: item.gap_id)),
        )
        forged_report = replace(
            forged_report,
            trace_report_id=_stable_id("inspector-element-acceptance-trace", forged_report.to_payload()),
        )
        with self.assertRaises(InspectorTraceError):
            forged_report.validate()

    def test_rehashed_invalid_runtime_step_status_fails_closed(self) -> None:
        trace = self._trace()
        original = next(item for item in trace.criterion_traces if item.runtime_steps)
        bad_step = replace(original.runtime_steps[0], status="forged-status")
        forged_criterion = self._resign(
            original,
            "criterion_trace_id",
            "inspector-criterion-trace",
            runtime_steps=(bad_step, *original.runtime_steps[1:]),
        )
        forged_report = replace(
            trace,
            trace_report_id="",
            criterion_traces=tuple(sorted(
                (forged_criterion if item.criterion_trace_id == original.criterion_trace_id else item for item in trace.criterion_traces),
                key=lambda item: item.criterion_trace_id,
            )),
        )
        forged_report = replace(
            forged_report,
            trace_report_id=_stable_id("inspector-element-acceptance-trace", forged_report.to_payload()),
        )
        with self.assertRaises(InspectorTraceError):
            forged_report.validate()

    def test_rehashed_duplicate_influence_check_key_fails_closed(self) -> None:
        trace = self._trace()
        check_id, status = trace.influence_checks[0]
        duplicate_status = "fail" if status != "fail" else "pass"
        forged_report = replace(
            trace,
            trace_report_id="",
            influence_checks=tuple(sorted((*trace.influence_checks, (check_id, duplicate_status)))),
        )
        forged_report = replace(
            forged_report,
            trace_report_id=_stable_id("inspector-element-acceptance-trace", forged_report.to_payload()),
        )
        with self.assertRaises(InspectorTraceError):
            forged_report.validate()

    def test_g1_g2_unavailable_is_structured_and_mixed_artifacts_are_rejected(self) -> None:
        unavailable = project_model_inspector_facts("G1", provider_claim={"untrusted": "claim"})
        trace = build_inspector_element_acceptance_trace(unavailable)
        self.assertEqual(trace.run_group, "G1")
        self.assertEqual(trace.status, "fail_closed")
        self.assertEqual(trace.missing_artifacts, tuple(sorted(("d14_evaluation_bundle", "model_candidate_audit_record"))) )
        with self.assertRaises(InspectorFactSourceIsolationError):
            build_inspector_element_acceptance_trace(unavailable, guidance=self.guidance)

    @staticmethod
    def _resign(record, id_field: str, prefix: str, **changes):
        unsigned = replace(record, **{**changes, id_field: ""})
        return replace(unsigned, **{id_field: _stable_id(prefix, unsigned.to_payload())})

    def _call_with_facts(self, facts, *, candidate=None):
        browser = execute_acceptance_binding_plan(
            self.binding, self.render.index_html.resolve().as_uri(), backend=FakeBrowserBackend(self.binding)
        )
        candidate = self.candidate if candidate is None else candidate
        evaluation = evaluate_acceptance(
            self.case_id, self.plan, self.binding, browser,
            self.guided.page_spec, self.gold, candidate,
        )
        return build_inspector_element_acceptance_trace(
            facts,
            guidance=self.guidance, guided_build_result=self.guided,
            page_spec=self.guided.page_spec, retrieval_influence_report=self.influence,
            requirement_view=self.view, acceptance_plan=self.plan, render_result=self.render,
            binding_plan=self.binding, browser_report=browser, gold_obligations=self.gold,
            candidate_decisions=candidate, acceptance_evaluation_report=evaluation,
        )


if __name__ == "__main__":
    unittest.main()
