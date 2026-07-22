from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import AgentContextBundle, UseCase  # noqa: E402
from req2web_generation import RetrievalGuidanceBuilder  # noqa: E402
from req2web_provider import (  # noqa: E402
    CanonicalPageSpecAssembler,
    ProviderError,
    ProviderProtocolError,
    ProviderRawResponse,
    parse_provider_raw_response,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402


def _result(role: str, doc_id: str, title: str, summary: str, references: list[dict[str, str]]) -> dict[str, object]:
    return {
        "score": 1.0,
        "doc_id": doc_id,
        "role": role,
        "dataset": "fixture_dataset",
        "subset": "fixture_subset",
        "sample_id": doc_id.rsplit(":", 1)[-1],
        "title": title,
        "summary": summary,
        "references": references,
    }


def build_context() -> AgentContextBundle:
    results = {
        "requirement": [_result("requirement", "requirement:fixture:ecommerce", "Ecommerce search", "filter cart checkout workflow", [{"kind": "workflow", "uri": "fixtures/requirement/workflow.json"}])],
        "ui_reference": [_result("ui_reference", "ui_reference:fixture:search", "搜索筛选结果", "商品搜索结果与筛选", [{"kind": "screenshot", "uri": "fixtures/ui/search.png"}])],
        "interaction_flow": [_result("interaction_flow", "interaction_flow:fixture:mixed", "mixed_short flow", "click and swipe flow", [{"kind": "step_screenshot", "uri": "fixtures/flow/01.png"}, {"kind": "step_screenshot", "uri": "fixtures/flow/02.png"}])],
        "implementation": [_result("implementation", "implementation:fixture:responsive", "Responsive cart checkout", "responsive cart structure", [{"kind": "html", "uri": "fixtures/implementation/cart.html"}])],
        "validation": [_result("validation", "validation:fixture:retry", "Input error retry", "input error permits retry", [{"kind": "issue", "uri": "https://example.test/issues/1"}])],
    }
    return AgentContextBundle(
        original_requirement="做一个可搜索和结算的移动电商页面。",
        requirement_summary="移动电商搜索与结算。",
        target_device="mobile",
        task_type="ecommerce",
        constraints=["输入错误时允许修改并重试"],
        use_cases=[
            UseCase("UC-01", "搜索商品", "用户", "搜索商品", "查看结果"),
            UseCase("UC-02", "结算商品", "用户", "完成结算", "获得确认"),
        ],
        retrieval_queries={role: f"fixture {role}" for role in ROLE_ORDER},
        retrieval_results=results,
    )


def candidate_payload() -> dict[str, object]:
    return {
        "schema_version": "req2web.provider.semantic_candidate.v1",
        "title": "商品搜索与结算",
        "layout": {"pattern": "two_task_sections", "section_stable_ids": ["section-search", "section-checkout"]},
        "sections": [
            {"stable_id": "section-search", "title": "搜索商品", "purpose": "搜索并查看商品结果", "component_stable_ids": ["component-search", "component-search-result"], "use_case_ids": ["UC-01"]},
            {"stable_id": "section-checkout", "title": "结算商品", "purpose": "确认订单并完成结算", "component_stable_ids": ["component-checkout", "component-checkout-result"], "use_case_ids": ["UC-02"]},
        ],
        "components": [
            {"stable_id": "component-search", "section_stable_id": "section-search", "component_type": "search_input", "label": "搜索商品", "purpose": "输入商品关键词"},
            {"stable_id": "component-search-result", "section_stable_id": "section-search", "component_type": "status_panel", "label": "搜索结果", "purpose": "显示搜索结果"},
            {"stable_id": "component-checkout", "section_stable_id": "section-checkout", "component_type": "primary_action", "label": "提交结算", "purpose": "提交订单结算"},
            {"stable_id": "component-checkout-result", "section_stable_id": "section-checkout", "component_type": "status_panel", "label": "结算结果", "purpose": "显示结算确认"},
        ],
        "states": [
            {"stable_id": "state-ready", "name": "ready", "description": "核心任务入口可操作。", "visible_component_stable_ids": ["component-search", "component-checkout"]},
            {"stable_id": "state-success", "name": "success", "description": "任务完成后显示明确结果。", "visible_component_stable_ids": ["component-search-result", "component-checkout-result"]},
        ],
        "interactions": [
            {"stable_id": "interaction-search", "trigger_component_stable_id": "component-search", "source_state_stable_id": "state-ready", "action": "执行商品搜索", "target_state_stable_id": "state-success", "user_feedback": "显示搜索结果", "use_case_ids": ["UC-01"]},
            {"stable_id": "interaction-checkout", "trigger_component_stable_id": "component-checkout", "source_state_stable_id": "state-ready", "action": "提交商品结算", "target_state_stable_id": "state-success", "user_feedback": "显示结算确认", "use_case_ids": ["UC-02"]},
        ],
        "constraints": [{"stable_id": "constraint-feedback", "description": "页面必须为用户提供明确的任务状态反馈"}],
        "acceptance_checks": [
            {"stable_id": "check-search", "description": "用户搜索商品后页面必须显示可见的搜索结果。", "use_case_ids": ["UC-01"], "state_stable_id": "state-success"},
            {"stable_id": "check-checkout", "description": "用户完成商品结算后页面必须显示明确的结算确认。", "use_case_ids": ["UC-02"], "state_stable_id": "state-success"},
        ],
        "use_case_mappings": [
            {"use_case_id": "UC-01", "section_stable_ids": ["section-search"], "component_stable_ids": ["component-search", "component-search-result"], "interaction_stable_ids": ["interaction-search"]},
            {"use_case_id": "UC-02", "section_stable_ids": ["section-checkout"], "component_stable_ids": ["component-checkout", "component-checkout-result"], "interaction_stable_ids": ["interaction-checkout"]},
        ],
        "claimed_attribution_edges": [{"candidate_entity_stable_id": "component-search", "source_kind": "requirement", "source_id": "requirement-fixture-ecommerce"}],
    }


def raw_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class M3SemanticCandidateAssemblyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.context = build_context()
        self.guidance = RetrievalGuidanceBuilder().build(self.context)
        self.payload = candidate_payload()
        self.assembler = CanonicalPageSpecAssembler()

    def parse(self, payload: object | None = None):
        return parse_provider_raw_response(ProviderRawResponse.from_bytes(raw_bytes(self.payload if payload is None else payload)))

    def test_legal_assembly_binds_local_identity_model_constraints_and_provenance(self) -> None:
        raw = ProviderRawResponse.from_bytes(raw_bytes(self.payload))
        assembled = self.assembler.assemble(raw, self.context, self.guidance)
        self.assertTrue(assembled.page_spec.page_id.startswith("page-ecommerce-"))
        self.assertEqual(assembled.page_spec.summary, self.context.requirement_summary)
        self.assertEqual(assembled.page_spec.target_device, "mobile")
        self.assertEqual(assembled.page_spec.page_type, "ecommerce")
        self.assertEqual(
            [(item.constraint_id, item.source) for item in assembled.page_spec.constraints],
            [("constraint-feedback", "model_semantic_candidate"), (assembled.page_spec.constraints[1].constraint_id, "agent_context")],
        )
        self.assertEqual({item.role for item in assembled.page_spec.traceability.evidence}, set(ROLE_ORDER))
        self.assertEqual(assembled.report.raw_response_sha256, hashlib.sha256(raw.raw_bytes).hexdigest())
        self.assertEqual(assembled.report.assembled_page_spec_sha256, hashlib.sha256(json.dumps(assembled.page_spec.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest())
        origins = {item.field_path: item for item in assembled.report.field_origins}
        self.assertEqual(origins["constraints.model_semantic_candidate"].origin, "model_semantic_candidate")
        self.assertEqual(origins["constraints.agent_context"].origin, "local_identity_scaffold")
        self.assertTrue(all(not item.model_attribution_eligible for item in assembled.report.field_origins if item.origin == "local_identity_scaffold"))
        assembled.page_spec.validate(); assembled.validate()

    def test_report_id_binds_the_complete_provenance_root(self) -> None:
        raw = ProviderRawResponse.from_bytes(raw_bytes(self.payload))
        first = self.assembler.assemble(raw, self.context, self.guidance)

        alternate_context = build_context()
        alternate_context.original_requirement = "做一个兼容搜索和结算流程的移动电商页面。"
        alternate_guidance = RetrievalGuidanceBuilder().build(alternate_context)
        second = self.assembler.assemble(raw, alternate_context, alternate_guidance)

        self.assertEqual(first.raw_response.sha256, second.raw_response.sha256)
        self.assertEqual(first.candidate.sha256(), second.candidate.sha256())
        self.assertNotEqual(first.report.context_sha256, second.report.context_sha256)
        self.assertNotEqual(first.report.guidance_sha256, second.report.guidance_sha256)
        self.assertNotEqual(first.report.assembled_page_spec_sha256, second.report.assembled_page_spec_sha256)
        self.assertNotEqual(first.report.report_id, second.report.report_id)
        with self.assertRaisesRegex(ValueError, "provenance root"):
            replace(first.report, report_id="assembly-forged").validate()

    def test_raw_bytes_are_preserved_while_candidate_hash_is_canonical(self) -> None:
        canonical_raw = ProviderRawResponse.from_bytes(raw_bytes(self.payload))
        variant_bytes = json.dumps(self.payload, ensure_ascii=True, indent=2).encode("utf-8")
        variant_raw = ProviderRawResponse.from_bytes(variant_bytes)
        canonical_candidate = parse_provider_raw_response(canonical_raw)
        variant_candidate = parse_provider_raw_response(variant_raw)
        self.assertNotEqual(canonical_raw.raw_bytes, variant_raw.raw_bytes)
        self.assertNotEqual(variant_raw.sha256, variant_candidate.sha256())
        self.assertEqual(canonical_candidate.canonical_json_bytes(), variant_candidate.canonical_json_bytes())
        self.assertEqual(canonical_candidate.sha256(), variant_candidate.sha256())
        self.assertEqual(variant_raw.sha256, hashlib.sha256(variant_bytes).hexdigest())

    def test_unknown_authoritative_and_invalid_claim_source_fields_fail_closed(self) -> None:
        unknown = deepcopy(self.payload); unknown["unreviewed_extension"] = "no"
        forbidden = deepcopy(self.payload); forbidden["page_id"] = "page-forbidden"
        invalid_kind = deepcopy(self.payload); invalid_kind["claimed_attribution_edges"][0]["source_kind"] = "runtime"
        for payload, expected in ((unknown, "unknown_candidate_field"), (forbidden, "forbidden_authoritative_field"), (invalid_kind, "invalid_claimed_source_kind")):
            with self.subTest(expected=expected):
                with self.assertRaises(ProviderProtocolError) as raised:
                    self.parse(payload)
                self.assertEqual(raised.exception.code, expected)
                self.assertEqual(raised.exception.stage, "provider_generation")
                self.assertEqual(raised.exception.phase, "semantic_candidate")

    def test_malformed_duplicate_nonfinite_and_bom_json_fail_closed(self) -> None:
        duplicate = b'{"schema_version":"req2web.provider.semantic_candidate.v1","schema_version":"req2web.provider.semantic_candidate.v1"}'
        malformed = b'{'
        nonfinite = b'{"schema_version":NaN}'
        bom = b'\xef\xbb\xbf' + raw_bytes(self.payload)
        for raw, expected in ((duplicate, "duplicate_json_key"), (malformed, "invalid_json"), (nonfinite, "invalid_json"), (bom, "non_canonical_json")):
            with self.subTest(expected=expected):
                with self.assertRaises(ProviderProtocolError) as raised:
                    parse_provider_raw_response(ProviderRawResponse.from_bytes(raw))
                self.assertEqual(raised.exception.code, expected)
                self.assertEqual(raised.exception.stage, "provider_generation")
                self.assertEqual(raised.exception.phase, "raw_response")

    def test_canonical_identity_mismatch_missing_relation_and_constraint_impersonation_fail_closed(self) -> None:
        mismatch = deepcopy(self.payload)
        for path in (mismatch["sections"][0]["use_case_ids"], mismatch["interactions"][0]["use_case_ids"], mismatch["acceptance_checks"][0]["use_case_ids"]):
            path[0] = "UC-99"
        mismatch["use_case_mappings"][0]["use_case_id"] = "UC-99"
        candidate = self.parse(mismatch)
        with self.assertRaises(ProviderProtocolError) as raised:
            self.assembler.assemble(ProviderRawResponse.from_bytes(candidate.canonical_json_bytes()), self.context, self.guidance)
        self.assertEqual((raised.exception.code, raised.exception.stage, raised.exception.phase), ("canonical_identity_mismatch", "page_spec", "assembly"))

        missing = deepcopy(self.payload)
        missing["use_case_mappings"][0]["component_stable_ids"] = ["component-search"]
        with self.assertRaises(ProviderProtocolError) as raised:
            self.parse(missing)
        self.assertEqual(raised.exception.code, "missing_semantic_relation")

        impersonation = deepcopy(self.payload)
        impersonation["constraints"][0]["description"] = self.context.constraints[0]
        with self.assertRaises(ProviderProtocolError) as raised:
            self.assembler.assemble(ProviderRawResponse.from_bytes(raw_bytes(impersonation)), self.context, self.guidance)
        self.assertEqual((raised.exception.code, raised.exception.stage, raised.exception.phase), ("canonical_identity_mismatch", "page_spec", "assembly"))

    def test_empty_context_and_empty_candidate_constraints_fail_closed(self) -> None:
        empty_context = build_context()
        empty_context.constraints = []
        empty_guidance = RetrievalGuidanceBuilder().build(empty_context)
        empty_payload = deepcopy(self.payload)
        empty_payload["constraints"] = []
        with self.assertRaises(ProviderProtocolError) as raised:
            self.assembler.assemble(
                ProviderRawResponse.from_bytes(raw_bytes(empty_payload)),
                empty_context,
                empty_guidance,
            )
        self.assertEqual(
            (raised.exception.code, raised.exception.stage, raised.exception.phase),
            ("missing_semantic_relation", "page_spec", "assembly"),
        )

    def test_claimed_edges_are_untrusted_and_not_whitelisted_by_assembler(self) -> None:
        for source_kind in ("requirement", "evidence", "policy"):
            with self.subTest(source_kind=source_kind):
                payload = deepcopy(self.payload)
                payload["claimed_attribution_edges"] = [{
                    "candidate_entity_stable_id": "component-search",
                    "source_kind": source_kind,
                    "source_id": f"unverified-{source_kind}-identity",
                }]
                raw = ProviderRawResponse.from_bytes(raw_bytes(payload))
                assembled = self.assembler.assemble(raw, self.context, self.guidance)
                self.assertEqual(assembled.report.claimed_attribution_edges, assembled.candidate.claimed_attribution_edges)
                self.assertEqual(assembled.report.claimed_attribution_edges[0].source_id, f"unverified-{source_kind}-identity")

    def test_stable_id_reference_conflict_and_report_integrity_fail_closed(self) -> None:
        conflict = deepcopy(self.payload)
        conflict["components"][0]["section_stable_id"] = "section-absent"
        with self.assertRaises(ProviderProtocolError) as raised:
            self.parse(conflict)
        self.assertEqual(raised.exception.code, "stable_id_conflict")

        assembled = self.assembler.assemble(ProviderRawResponse.from_bytes(raw_bytes(self.payload)), self.context, self.guidance)
        with self.assertRaisesRegex(ValueError, "assembled PageSpec hash"):
            replace(assembled, report=replace(assembled.report, assembled_page_spec_sha256="0" * 64)).validate()
        with self.assertRaisesRegex(ValueError, "claimed edges"):
            replace(assembled, report=replace(assembled.report, claimed_attribution_edges=())).validate()
        with self.assertRaisesRegex(ValueError, "visibility inventory"):
            replace(assembled.report, input_visibility=assembled.report.input_visibility[:-1]).validate()
        with self.assertRaisesRegex(ValueError, "field-origin inventory"):
            replace(assembled.report, field_origins=(*assembled.report.field_origins, assembled.report.field_origins[0])).validate()
        for invalid_edge in (
            replace(assembled.report.claimed_attribution_edges[0], candidate_entity_stable_id="not a stable id"),
            replace(assembled.report.claimed_attribution_edges[0], source_kind="runtime"),
            replace(assembled.report.claimed_attribution_edges[0], source_id=""),
        ):
            with self.subTest(invalid_edge=invalid_edge):
                with self.assertRaisesRegex(ValueError, "claimed edge"):
                    replace(assembled.report, claimed_attribution_edges=(invalid_edge,)).validate()

    def test_error_envelope_uses_d03_stage_and_internal_phase_without_evaluator_import(self) -> None:
        error = ProviderError(code="timeout", stage="provider_runtime", phase="transport", retryable=True)
        self.assertEqual(set(error.to_dict()), {"code", "stage", "phase", "retryable", "schema_version"})
        self.assertEqual(error.to_dict()["stage"], "provider_runtime")
        self.assertEqual(error.to_dict()["phase"], "transport")
        source = (ROOT / "src" / "req2web_provider" / "semantic_candidate.py").read_text(encoding="utf-8")
        self.assertNotIn("req2web_evaluation", source)
        self.assertNotIn("gold_obligation", source)


if __name__ == "__main__":
    unittest.main()
