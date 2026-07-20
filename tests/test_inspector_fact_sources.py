from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import shutil
import sys
from unittest import TestCase


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
from req2web_inspector import (  # noqa: E402
    InspectorFactError,
    InspectorFactSourceIsolationError,
    InspectorSourceRef,
    canonical_json_bytes,
    canonical_sha256,
    page_spec_field_path,
    project_g0_inspector_facts,
    project_model_inspector_facts,
    validate_source_isolation,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from test_guided_page_spec import build_context  # noqa: E402


class InspectorFactSourcesTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = ROOT / "tests" / ".tmp_inspector_fact_sources"
        shutil.rmtree(cls.root, ignore_errors=True)
        context = build_context()
        cls.context = context
        cls.guidance = RetrievalGuidanceBuilder().build(context)
        builder = RetrievalGuidedPageSpecBuilder()
        cls.guided = builder.build(context, cls.guidance)
        ablations = {
            role: builder.build(context, cls.guidance, disabled_roles=(role,))
            for role in ROLE_ORDER
        }
        cls.ablations = ablations
        rendered = DeterministicPageRenderer().render(cls.guided.page_spec, cls.root / "rendered")
        cls.influence = RetrievalInfluenceChecker().check(
            context,
            cls.guidance,
            PageSpecBuilder().build(context),
            cls.guided,
            ablations,
            rendered,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def _project(self):
        return project_g0_inspector_facts(
            self.guidance,
            self.guided,
            self.guided.page_spec,
            self.influence,
        )

    @staticmethod
    def _resign(record, id_field: str, prefix: str, **changes):
        unsigned = replace(record, **{**changes, id_field: ""})
        return replace(unsigned, **{id_field: f"{prefix}-{canonical_sha256(unsigned.to_payload())[:20]}"})

    def test_projects_real_g0_guidance_decisions_and_exact_links(self) -> None:
        fact_set = self._project()
        self.assertEqual(fact_set.run_group, "G0")
        self.assertEqual(fact_set.page_id, self.guided.page_spec.page_id)
        self.assertEqual(len(fact_set.facts), len(self.guided.adopted) + len(self.guided.ignored) + len(self.guided.fallback))
        self.assertEqual({item.disposition for item in fact_set.facts}, {"adopted", "ignored", "fallback"})
        self.assertTrue(all(item.influence_trace_status == "trace_matched" for item in fact_set.facts))
        self.assertTrue(all(item.source_kind != "provider_claim" for item in fact_set.source_refs))
        self.assertTrue(any(item.field_path.startswith("component[") for item in fact_set.trace_links))
        self.assertTrue(any(item.field_path.startswith("interaction[") for item in fact_set.trace_links))
        self.assertTrue(fact_set.retrieval_influence_report_passed)
        self.assertTrue(all(item.status == "pass" for item in fact_set.influence_checks))

    def test_canonical_stability_and_immutable_output(self) -> None:
        first = self._project()
        second = self._project()
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        with self.assertRaises(FrozenInstanceError):
            first.page_id = "tampered"  # type: ignore[misc]
        exported = first.to_dict()
        exported["facts"][0]["decision_id"] = "tampered"
        self.assertNotEqual(exported["facts"][0]["decision_id"], first.facts[0].decision_id)

    def test_rejects_wrong_guidance_build_and_pagespec_identity(self) -> None:
        bad_guidance = deepcopy(self.guidance)
        bad_guidance.ui_guidance[0].source.role = "requirement"
        with self.assertRaises(ValueError):
            project_g0_inspector_facts(bad_guidance, self.guided, self.guided.page_spec, self.influence)

        bad_guided_doc = deepcopy(self.guided)
        bad_guided_doc.adopted[0] = replace(bad_guided_doc.adopted[0], doc_id="wrong-doc")
        with self.assertRaises(ValueError):
            project_g0_inspector_facts(self.guidance, bad_guided_doc, self.guided.page_spec, self.influence)

        bad_build = deepcopy(self.guided)
        bad_build.build_result_id = "guided-page-spec-tampered"
        with self.assertRaisesRegex(ValueError, "build_result_id"):
            project_g0_inspector_facts(self.guidance, bad_build, self.guided.page_spec, self.influence)

        bad_page = deepcopy(self.guided.page_spec)
        bad_page.title = "Different deterministic page"
        with self.assertRaisesRegex(InspectorFactError, "does not match"):
            project_g0_inspector_facts(self.guidance, self.guided, bad_page, self.influence)

    def test_rejects_missing_entity_or_field_from_frozen_v1_grammar(self) -> None:
        component_id = self.guided.page_spec.components[0].component_id
        self.assertEqual(
            page_spec_field_path(self.guided.page_spec, component_id, "label"),
            f"component[{component_id}].label",
        )
        with self.assertRaisesRegex(InspectorFactError, "entity does not exist"):
            page_spec_field_path(self.guided.page_spec, "missing-entity", "label")
        with self.assertRaisesRegex(InspectorFactError, "frozen v1 grammar"):
            page_spec_field_path(self.guided.page_spec, component_id, "missing_field")

    def test_projects_structure_valid_failed_influence_report_as_observation(self) -> None:
        rendered = DeterministicPageRenderer().render(
            self.guided.page_spec,
            self.root / "failed-rendered",
        )
        rendered.app_js.write_text("const PAGE_DATA = Object.freeze({});", encoding="utf-8")
        failed_report = RetrievalInfluenceChecker().check(
            self.context,
            self.guidance,
            PageSpecBuilder().build(self.context),
            self.guided,
            self.ablations,
            rendered,
        )
        self.assertFalse(failed_report.passed)
        fact_set = project_g0_inspector_facts(
            self.guidance,
            self.guided,
            self.guided.page_spec,
            failed_report,
        )
        self.assertFalse(fact_set.retrieval_influence_report_passed)
        expected_failed = {
            (item.check_id, item.category, item.status)
            for item in failed_report.checks
            if item.status == "fail"
        }
        actual_failed = {
            (item.check_id, item.category, item.status)
            for item in fact_set.influence_checks
            if item.status == "fail"
        }
        self.assertTrue(expected_failed)
        self.assertEqual(actual_failed, expected_failed)
        self.assertTrue(all(item.influence_trace_status == "trace_matched" for item in fact_set.facts))

        tampered_trace = deepcopy(failed_report)
        tampered_trace.decision_traceability[0]["doc_id"] = "tampered-doc"
        with self.assertRaises(ValueError):
            project_g0_inspector_facts(
                self.guidance,
                self.guided,
                self.guided.page_spec,
                tampered_trace,
            )
        tampered_identity = deepcopy(failed_report)
        tampered_identity.report_id = "retrieval-influence-tampered"
        with self.assertRaises(ValueError):
            project_g0_inspector_facts(
                self.guidance,
                self.guided,
                self.guided.page_spec,
                tampered_identity,
            )

    def test_rejects_source_ref_shape_and_cross_record_tampering(self) -> None:
        fact_set = self._project()
        retrieval_ref = next(item for item in fact_set.source_refs if item.source_kind == "guided_retrieval")
        fallback_ref = next(item for item in fact_set.source_refs if item.source_kind == "guided_agent_context_fallback")
        influence_ref = next(item for item in fact_set.source_refs if item.source_kind == "retrieval_influence_v1")

        bad_schema = self._resign(
            retrieval_ref,
            "source_ref_id",
            "inspector-source",
            artifact_schema_version="wrong.schema.v1",
        )
        with self.assertRaisesRegex(InspectorFactError, "wrong artifact schema"):
            bad_schema.validate()
        bad_fallback = self._resign(
            fallback_ref,
            "source_ref_id",
            "inspector-source",
            role="requirement",
        )
        with self.assertRaisesRegex(InspectorFactError, "invalid guidance fields"):
            bad_fallback.validate()
        bad_influence = self._resign(
            influence_ref,
            "source_ref_id",
            "inspector-source",
            source_id="other-report",
        )
        with self.assertRaisesRegex(InspectorFactError, "source_id must equal artifact_id"):
            bad_influence.validate()

        fact = next(item for item in fact_set.facts if item.trace_link_ids)
        link = next(item for item in fact_set.trace_links if item.trace_link_id == fact.trace_link_ids[0])
        other_guided_source = next(
            item
            for item in fact_set.source_refs
            if item.source_kind in {"guided_retrieval", "guided_agent_context_fallback"}
            and item.source_ref_id != fact.source_ref_id
        )
        cross_link_fact = self._resign(
            fact,
            "fact_id",
            "inspector-fact",
            source_ref_id=other_guided_source.source_ref_id,
        )
        cross_link_set = replace(
            fact_set,
            facts=tuple(sorted((cross_link_fact if item.fact_id == fact.fact_id else item for item in fact_set.facts), key=lambda item: item.fact_id)),
        )
        with self.assertRaisesRegex(InspectorFactError, "link source does not match"):
            cross_link_set.validate()

        wrong_verification_fact = self._resign(
            fact,
            "fact_id",
            "inspector-fact",
            verification_source_ref_id=fact.source_ref_id,
        )
        wrong_verification_set = replace(
            fact_set,
            facts=tuple(sorted((wrong_verification_fact if item.fact_id == fact.fact_id else item for item in fact_set.facts), key=lambda item: item.fact_id)),
        )
        with self.assertRaisesRegex(InspectorFactError, "verification source must be retrieval_influence_v1"):
            wrong_verification_set.validate()

        fallback_fact = next(item for item in fact_set.facts if item.disposition == "fallback")
        fallback_as_influence = self._resign(
            fallback_fact,
            "fact_id",
            "inspector-fact",
            source_ref_id=influence_ref.source_ref_id,
        )
        fallback_as_influence_set = replace(
            fact_set,
            facts=tuple(sorted((fallback_as_influence if item.fact_id == fallback_fact.fact_id else item for item in fact_set.facts), key=lambda item: item.fact_id)),
        )
        with self.assertRaisesRegex(InspectorFactError, "fallback requires one of"):
            fallback_as_influence_set.validate()

        adopted_fact = next(item for item in fact_set.facts if item.disposition == "adopted")
        adopted_as_fallback = self._resign(
            adopted_fact,
            "fact_id",
            "inspector-fact",
            source_ref_id=fallback_ref.source_ref_id,
        )
        adopted_as_fallback_set = replace(
            fact_set,
            facts=tuple(sorted((adopted_as_fallback if item.fact_id == adopted_fact.fact_id else item for item in fact_set.facts), key=lambda item: item.fact_id)),
        )
        with self.assertRaisesRegex(InspectorFactError, "adopted requires one of: guided_retrieval"):
            adopted_as_fallback_set.validate()

        missing_source_link = self._resign(
            link,
            "trace_link_id",
            "inspector-trace",
            source_ref_id="missing-source",
        )
        missing_source_fact = self._resign(
            fact,
            "fact_id",
            "inspector-fact",
            trace_link_ids=tuple(
                sorted(missing_source_link.trace_link_id if item == link.trace_link_id else item for item in fact.trace_link_ids)
            ),
        )
        missing_source_set = replace(
            fact_set,
            trace_links=tuple(sorted((missing_source_link if item.trace_link_id == link.trace_link_id else item for item in fact_set.trace_links), key=lambda item: item.trace_link_id)),
            facts=tuple(sorted((missing_source_fact if item.fact_id == fact.fact_id else item for item in fact_set.facts), key=lambda item: item.fact_id)),
        )
        with self.assertRaisesRegex(InspectorFactError, "trace link references an unknown source"):
            missing_source_set.validate()

        orphan_link = self._resign(
            link,
            "trace_link_id",
            "inspector-trace",
            entity_id="orphan-entity",
            field_path="component[orphan-entity].label",
        )
        orphan_set = replace(
            fact_set,
            trace_links=tuple(sorted((*fact_set.trace_links, orphan_link), key=lambda item: item.trace_link_id)),
        )
        with self.assertRaisesRegex(InspectorFactError, "referenced exactly once"):
            orphan_set.validate()

        wrong_decision_link = self._resign(
            link,
            "trace_link_id",
            "inspector-trace",
            decision_id="missing-decision",
        )
        wrong_decision_set = replace(
            fact_set,
            trace_links=tuple(sorted((wrong_decision_link if item.trace_link_id == link.trace_link_id else item for item in fact_set.trace_links), key=lambda item: item.trace_link_id)),
        )
        with self.assertRaisesRegex(InspectorFactError, "trace link references an unknown decision"):
            wrong_decision_set.validate()

    def test_rejects_tampered_influence_trace(self) -> None:
        tampered = deepcopy(self.influence)
        tampered.decision_traceability[0]["doc_id"] = "tampered-doc"
        with self.assertRaises(ValueError):
            project_g0_inspector_facts(self.guidance, self.guided, self.guided.page_spec, tampered)

    def test_rejects_mixed_or_unverified_source_kinds(self) -> None:
        mixed = InspectorSourceRef(
            source_ref_id="forged",
            run_group="G1",
            source_kind="guided_retrieval",
            artifact_schema_version="req2web.retrieval.guidance.v1",
            artifact_id="guidance",
            artifact_sha256="0" * 64,
            source_id="guidance",
        )
        with self.assertRaisesRegex(InspectorFactSourceIsolationError, "mix run groups"):
            validate_source_isolation("G0", (mixed,))
        for source_kind in ("candidate_attribution_edge_set", "local_identity_scaffold", "provider_prompt", "provider_log"):
            with self.subTest(source_kind=source_kind):
                unverified = InspectorSourceRef(
                    source_ref_id="forged",
                    run_group="G0",
                    source_kind=source_kind,
                    artifact_schema_version="untrusted.v1",
                    artifact_id="untrusted",
                    artifact_sha256="0" * 64,
                    source_id="untrusted",
                )
                with self.assertRaisesRegex(InspectorFactSourceIsolationError, "unverified G0"):
                    validate_source_isolation("G0", (unverified,))

    def test_rejects_report_flag_and_unused_source_reference_tampering(self) -> None:
        fact_set = self._project()
        contradictory = replace(fact_set, retrieval_influence_report_passed=False)
        with self.assertRaisesRegex(InspectorFactError, "does not match stored influence check statuses"):
            contradictory.validate()

        retrieval_ref = next(item for item in fact_set.source_refs if item.source_kind == "guided_retrieval")
        unused_ref = self._resign(
            retrieval_ref,
            "source_ref_id",
            "inspector-source",
            source_id="unused-guidance",
            guidance_id="unused-guidance",
            doc_id="unused-doc",
        )
        unused_set = replace(
            fact_set,
            source_refs=tuple(sorted((*fact_set.source_refs, unused_ref), key=lambda item: item.source_ref_id)),
        )
        with self.assertRaisesRegex(InspectorFactError, "unused decision source reference"):
            unused_set.validate()

        influence_ref = next(item for item in fact_set.source_refs if item.source_kind == "retrieval_influence_v1")
        alternate_influence = self._resign(
            influence_ref,
            "source_ref_id",
            "inspector-source",
            artifact_id="alternate-report",
            source_id="alternate-report",
        )
        first_fact = fact_set.facts[0]
        alternate_verification_fact = self._resign(
            first_fact,
            "fact_id",
            "inspector-fact",
            verification_source_ref_id=alternate_influence.source_ref_id,
        )
        split_verification_set = replace(
            fact_set,
            source_refs=tuple(sorted((*fact_set.source_refs, alternate_influence), key=lambda item: item.source_ref_id)),
            facts=tuple(sorted((alternate_verification_fact if item.fact_id == first_fact.fact_id else item for item in fact_set.facts), key=lambda item: item.fact_id)),
        )
        with self.assertRaisesRegex(InspectorFactError, "verification source must be used by every fact"):
            split_verification_set.validate()

    def test_model_groups_return_structured_fail_closed_results(self) -> None:
        result = project_model_inspector_facts(
            "G1",
            candidate_attribution_edge_set=object(),
            local_identity_scaffold=object(),
            provider_prompt=object(),
            provider_log=object(),
        )
        self.assertEqual(result.status, "fail_closed")
        self.assertEqual(result.error_code, "model_audit_bundle_unavailable")
        self.assertEqual(result.missing_artifacts, ("model_candidate_audit_record", "d14_evaluation_bundle"))
        self.assertEqual(
            result.rejected_input_kinds,
            ("candidate_attribution_edge_set", "local_identity_scaffold", "provider_log", "provider_prompt"),
        )
        with self.assertRaisesRegex(InspectorFactError, "limited to G1/G2"):
            project_model_inspector_facts("G0")


if __name__ == "__main__":
    import unittest

    unittest.main()
