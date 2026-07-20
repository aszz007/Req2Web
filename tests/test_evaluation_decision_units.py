from __future__ import annotations

import copy
from dataclasses import fields, replace
import inspect
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_acceptance import compile_acceptance_plan, project_requirement_view  # noqa: E402
from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle, UseCase  # noqa: E402
from req2web_evaluation import (  # noqa: E402
    DECISION_UNIT_SCHEMA_VERSION,
    IDENTITY_VALIDATION_STATUSES,
    CandidateDecision,
    DecisionAlignment,
    DecisionUnitBundle,
    GoldObligation,
    create_decision_alignment,
    freeze_decision_alignments,
    normalize_candidate_decisions,
    normalize_gold_obligations,
)
from req2web_generation.schema import (  # noqa: E402
    AcceptanceCheck,
    ComponentSpec,
    ConstraintSpec,
    EvidenceReference,
    InteractionSpec,
    LayoutSpec,
    PageSpec,
    PageState,
    PageUseCase,
    SectionSpec,
    TraceabilitySpec,
    UseCaseTrace,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_rag.validation_signals import build_validation_signals  # noqa: E402


def _references() -> list[dict[str, str]]:
    return [{"kind": "issue", "uri": "https://example.test/issues/1"}]


def _record(role: str, doc_id: str, *, category: str | None = None) -> dict[str, object]:
    references = _references()
    record: dict[str, object] = {"doc_id": doc_id, "references": references, "role": role, "summary": f"{role} summary", "title": f"{role} title"}
    if category is not None:
        record["validation_signals"] = build_validation_signals({"doc_id": doc_id, "metadata": {"category": category}, "references": references, "role": role})
    return record


def make_plan():
    retrieval_results = {role: [_record(role, f"{role}:doc-001")] for role in ROLE_ORDER}
    retrieval_results["validation"] = [_record("validation", "validation:doc-001", category="input_error")]
    bundle = AgentContextBundle(
        original_requirement="Create a mobile product search page with input recovery.",
        requirement_summary="A mobile product search flow with explicit input recovery.",
        target_device="mobile",
        task_type="search_list",
        constraints=["Support responsive layout.", "Show input-error recovery."],
        use_cases=[
            UseCase("use-case-search", "Search products", "shopper", "find a matching product", "a result list is shown"),
            UseCase("use-case-retry", "Recover from invalid input", "shopper", "retry an invalid search", "clear feedback and a retry path are shown"),
        ],
        retrieval_queries={role: f"query for {role}" for role in ROLE_ORDER},
        retrieval_results=retrieval_results,
    )
    return compile_acceptance_plan(project_requirement_view(bundle))


def make_page_spec() -> PageSpec:
    use_cases = [
        PageUseCase("uc-search", "Search products", "shopper", "find a product", "results are shown"),
        PageUseCase("uc-retry", "Recover search", "shopper", "retry a search", "feedback and retry are shown"),
    ]
    components = [
        ComponentSpec("search-input", "main", "text_input", "Search", "Search Field"),
        ComponentSpec("search-submit", "main", "button", "Find", "Submit Search"),
        ComponentSpec("results", "main", "list", "Results", "Search Results"),
        ComponentSpec("retry", "main", "button", "Retry", "Retry Search"),
    ]
    evidence = [EvidenceReference(role, f"{role}:doc-001", f"{role} evidence", [f"https://example.test/{role}"]) for role in ROLE_ORDER]
    return PageSpec(
        page_id="search-page",
        title="Product Search",
        summary="Search and recover from invalid input.",
        target_device="mobile",
        page_type="search_list",
        layout=LayoutSpec("single_column", ["main"]),
        use_cases=use_cases,
        sections=[SectionSpec("main", "Search", "Search tasks", [item.component_id for item in components], ["uc-search", "uc-retry"])],
        components=components,
        states=[
            PageState("initial", "Initial", "Ready to search", ["search-input", "search-submit"]),
            PageState("results-state", "Results", "Results are visible", ["search-input", "search-submit", "results"]),
            PageState("error", "Input error", "Input recovery is visible", ["search-input", "retry"]),
        ],
        interactions=[
            InteractionSpec("submit-search", "search-submit", "initial", "click", "results-state", "Results are shown", ["uc-search"]),
            InteractionSpec("retry-search", "retry", "error", "click", "initial", "Retry is ready", ["uc-retry"]),
        ],
        constraints=[ConstraintSpec("responsive", "Support responsive layout.", "agent_context.constraints"), ConstraintSpec("recovery", "Show input-error recovery.", "agent_context.constraints")],
        acceptance_checks=[
            AcceptanceCheck("check-search", "Search results are shown", ["uc-search"], "results-state"),
            AcceptanceCheck("check-retry", "Retry is available", ["uc-retry"], "initial"),
        ],
        traceability=TraceabilitySpec(
            source_context_schema_version=AGENT_BUNDLE_SCHEMA_VERSION,
            evidence=evidence,
            use_cases=[
                UseCaseTrace("uc-search", ["main"], ["search-input", "search-submit", "results"], ["submit-search"], [item.doc_id for item in evidence]),
                UseCaseTrace("uc-retry", ["main"], ["search-input", "retry"], ["retry-search"], [item.doc_id for item in evidence]),
            ],
        ),
    )


class DecisionUnitProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.case_id = "development-search-001"
        self.plan = make_plan()
        self.page_spec = make_page_spec()

    def test_protocol_has_strict_record_field_isolation(self) -> None:
        self.assertEqual({field.name for field in fields(GoldObligation)}, {"schema_version", "gold_unit_id", "case_id", "unit_type", "semantic_key", "source_requirement_ids", "hardness", "expected_change", "evaluation_rule"})
        self.assertEqual({field.name for field in fields(CandidateDecision)}, {"schema_version", "decision_id", "case_id", "unit_type", "semantic_key", "candidate_entity_ids", "attribution_labels", "attribution_edge_ids", "identity_validation_status"})
        self.assertEqual({field.name for field in fields(DecisionAlignment)}, {"schema_version", "alignment_id", "gold_unit_id", "decision_ids", "match_type", "evaluation_result"})

    def test_gold_normalizer_only_reads_acceptance_plan(self) -> None:
        parameters = tuple(inspect.signature(normalize_gold_obligations).parameters)
        self.assertEqual(parameters, ("case_id", "acceptance_plan"))
        gold = normalize_gold_obligations(self.case_id, self.plan)
        self.assertEqual(gold.schema_version, DECISION_UNIT_SCHEMA_VERSION)
        self.assertEqual({item.unit_type for item in gold.obligations}, {"constraint_obligation", "acceptance_binding"})
        self.assertTrue(all(item.hardness == "hard" and item.expected_change == "not_applicable" for item in gold.obligations))
        self.assertEqual(len([item for item in gold.obligations if item.unit_type == "constraint_obligation"]), 4)
        self.assertEqual(len([item for item in gold.obligations if item.unit_type == "acceptance_binding"]), 3)

    def test_candidate_normalizer_only_reads_pagespec_and_emits_no_claim(self) -> None:
        self.assertEqual(tuple(inspect.signature(normalize_candidate_decisions).parameters), ("case_id", "page_spec"))
        candidate = normalize_candidate_decisions(self.case_id, self.page_spec)
        self.assertEqual({item.unit_type for item in candidate.decisions}, {"component_presence", "state_obligation", "interaction_edge", "constraint_obligation", "acceptance_binding"})
        self.assertTrue(all(item.identity_validation_status == "no_claim" for item in candidate.decisions))
        self.assertTrue(all(not item.attribution_labels and not item.attribution_edge_ids for item in candidate.decisions))

    def test_extra_candidates_and_semantic_multi_entity_grouping_are_retained(self) -> None:
        extra = ComponentSpec("search-input-extra", "main", "text_input", "Other Search", "Search Field")
        page = replace(self.page_spec, components=[*self.page_spec.components, extra], sections=[replace(self.page_spec.sections[0], component_ids=[*self.page_spec.sections[0].component_ids, extra.component_id])])
        candidate = normalize_candidate_decisions(self.case_id, page)
        grouped = [item for item in candidate.decisions if {"search-input", "search-input-extra"}.issubset(set(item.candidate_entity_ids))]
        self.assertEqual(len(grouped), 2)
        self.assertTrue(all(item.unit_type == "component_presence" for item in grouped))
        self.assertTrue(any("search-input-extra" in item.candidate_entity_ids for item in candidate.decisions))

    def test_unicode_case_whitespace_and_list_order_do_not_create_new_decisions(self) -> None:
        changed_input = replace(self.page_spec.components[0], label="Renamed", purpose="  SEARCH\u3000FIELD  ")
        page = replace(self.page_spec, components=list(reversed([changed_input, *self.page_spec.components[1:]])))
        original = normalize_candidate_decisions(self.case_id, self.page_spec)
        changed = normalize_candidate_decisions(self.case_id, page)
        project = lambda result: [(item.unit_type, item.decision_id, item.semantic_key, item.candidate_entity_ids) for item in result.decisions]
        self.assertEqual(project(original), project(changed))
        self.assertNotEqual(original.source_page_spec_sha256, changed.source_page_spec_sha256)

    def test_canonical_hashes_are_stable_and_inputs_are_not_mutated(self) -> None:
        before_plan = copy.deepcopy(self.plan.to_dict())
        before_page = copy.deepcopy(self.page_spec.to_dict())
        first_gold = normalize_gold_obligations(self.case_id, self.plan)
        second_gold = normalize_gold_obligations(self.case_id, self.plan)
        first_candidate = normalize_candidate_decisions(self.case_id, self.page_spec)
        second_candidate = normalize_candidate_decisions(self.case_id, self.page_spec)
        self.assertEqual(first_gold.canonical_json_bytes(), second_gold.canonical_json_bytes())
        self.assertEqual(first_gold.sha256(), second_gold.sha256())
        self.assertEqual(first_candidate.canonical_json_bytes(), second_candidate.canonical_json_bytes())
        self.assertEqual(first_candidate.sha256(), second_candidate.sha256())
        self.assertEqual(before_plan, self.plan.to_dict())
        self.assertEqual(before_page, self.page_spec.to_dict())
    def test_tampering_duplicate_and_cross_record_fields_are_rejected(self) -> None:
        gold = normalize_gold_obligations(self.case_id, self.plan)
        candidate = normalize_candidate_decisions(self.case_id, self.page_spec)
        with self.assertRaises(ValueError):
            replace(gold.obligations[0], gold_unit_id="forged").validate()
        with self.assertRaises(ValueError):
            replace(candidate.decisions[0], attribution_labels=("local_identity_scaffold",)).validate()
        with self.assertRaises(ValueError):
            replace(candidate.decisions[0], candidate_entity_ids=("dup", "dup")).validate()
        with self.assertRaises(ValueError):
            replace(candidate.decisions[0], unit_type="gold_obligation").validate()
        with self.assertRaises(ValueError):
            create_decision_alignment(gold.obligations[0].gold_unit_id, (candidate.decisions[0].decision_id, candidate.decisions[0].decision_id), "unmatched", "not_evaluated")

    def test_alignment_is_explicit_and_keeps_collection_hashes_separate(self) -> None:
        gold = normalize_gold_obligations(self.case_id, self.plan)
        candidate = normalize_candidate_decisions(self.case_id, self.page_spec)
        gold_hash = gold.sha256()
        candidate_hash = candidate.sha256()
        alignments = tuple(create_decision_alignment(item.gold_unit_id, (), "unmatched", "not_evaluated") for item in gold.obligations)
        alignment_set = freeze_decision_alignments(gold, candidate, alignments)
        bundle = DecisionUnitBundle(DECISION_UNIT_SCHEMA_VERSION, gold, candidate, alignment_set)
        bundle.validate()
        payload = bundle.to_dict()
        self.assertEqual(payload["gold_obligation_set_sha256"], gold_hash)
        self.assertEqual(payload["candidate_decision_set_sha256"], candidate_hash)
        self.assertEqual(payload["alignment_set_sha256"], alignment_set.sha256())
        self.assertEqual(gold_hash, gold.sha256())
        self.assertEqual(candidate_hash, candidate.sha256())

    def test_alignment_rejects_missing_gold_and_unknown_candidate_reference(self) -> None:
        gold = normalize_gold_obligations(self.case_id, self.plan)
        candidate = normalize_candidate_decisions(self.case_id, self.page_spec)
        incomplete = create_decision_alignment(gold.obligations[0].gold_unit_id, (), "unmatched", "not_evaluated")
        with self.assertRaises(ValueError):
            freeze_decision_alignments(gold, candidate, (incomplete,))
        invalid = tuple(create_decision_alignment(item.gold_unit_id, ("unknown-decision",) if index == 0 else (), "unmatched", "not_evaluated") for index, item in enumerate(gold.obligations))
        with self.assertRaises(ValueError):
            freeze_decision_alignments(gold, candidate, invalid)

    def test_alignment_factory_canonicalizes_ids_and_returns_valid_record(self) -> None:
        gold = normalize_gold_obligations(self.case_id, self.plan)
        candidate = normalize_candidate_decisions(self.case_id, self.page_spec)
        ids = (candidate.decisions[1].decision_id, candidate.decisions[0].decision_id)
        alignment = create_decision_alignment(gold.obligations[0].gold_unit_id, ids, "semantic_match", "pass")
        self.assertEqual(alignment.decision_ids, tuple(sorted(ids)))
        alignment.validate()

    def test_future_audited_statuses_are_schema_compatible_and_invalid_combinations_fail(self) -> None:
        candidate = normalize_candidate_decisions(self.case_id, self.page_spec)
        base = candidate.decisions[0]
        self.assertEqual(IDENTITY_VALIDATION_STATUSES, frozenset({"no_claim", "validated", "rejected", "mixed"}))
        for status in ("validated", "rejected", "mixed"):
            record = replace(base, attribution_labels=("requirement_attributed",), attribution_edge_ids=("audit-edge-001",), identity_validation_status=status)
            record.validate()
        with self.assertRaises(ValueError):
            replace(base, identity_validation_status="validated").validate()
        with self.assertRaises(ValueError):
            replace(base, identity_validation_status="validated", attribution_labels=("invalid label",), attribution_edge_ids=("audit-edge-001",)).validate()
        with self.assertRaises(ValueError):
            replace(base, attribution_labels=("requirement_attributed",), attribution_edge_ids=("audit-edge-001",)).validate()


if __name__ == "__main__":
    unittest.main()