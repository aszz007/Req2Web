from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from unittest import TestCase

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import AgentContextBundle, UseCase  # noqa: E402
from req2web_generation import RetrievalGuidanceBuilder, RetrievalGuidedPageSpecBuilder  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_rag.index import TfidfIndex, build_tfidf_index  # noqa: E402
from req2web_rag.ui_structure_signals import (  # noqa: E402
    FORM_STRUCTURE_CATEGORIES,
    build_ui_structure_signals,
)


def _references() -> list[dict[str, str]]:
    return [{"kind": "screenshot", "uri": "fixtures/ui/form.png"}, {"kind": "semantic_annotation", "uri": "fixtures/ui/form.json"}]


def _document(doc_id: str = "ui_reference:fixture:form", category: str = "form_input", input_value: object = 2) -> dict:
    return {
        "schema_version": "req2web.rag.document.v1", "doc_id": doc_id, "role": "ui_reference", "dataset": "rico", "subset": "combined", "sample_id": doc_id.rsplit(":", 1)[-1],
        "title": "Reference screen", "summary": "generic visual reference", "content": "reference", "tags": ["ui"], "references": _references(),
        "source": {"manifest_path": "data/processed/selection_manifest.csv", "manifest_source_path": "derived", "record_path": "data/processed/rico.jsonl"},
        "metadata": {"category": category, "component_labels": {"Input": input_value}},
    }


def _result(role: str, doc_id: str) -> dict:
    return {"score": 1.0, "doc_id": doc_id, "role": role, "dataset": "fixture", "subset": "v1", "sample_id": doc_id.rsplit(":", 1)[-1], "title": "Plain evidence", "summary": "generic evidence", "references": []}


def _context(requirement: str, ui: dict) -> AgentContextBundle:
    ui_result = _result("ui_reference", ui["doc_id"])
    ui_result["references"] = ui["references"]
    ui_result["ui_structure_signals"] = build_ui_structure_signals(ui)
    results = {role: [_result(role, f"{role}:fixture:one")] for role in ROLE_ORDER}
    results["ui_reference"] = [ui_result]
    return AgentContextBundle(
        original_requirement=requirement, requirement_summary=requirement, target_device="mobile", task_type="fixture", constraints=[],
        use_cases=[
            UseCase("UC-01", "主流程", "用户", requirement, "完成"),
            UseCase("UC-02", "确认", "用户", "确认当前内容", "完成"),
        ],
        retrieval_queries={role: role for role in ROLE_ORDER}, retrieval_results=results,
    )


class UiFormStructureAdapterTest(TestCase):
    def test_joint_source_contract_and_compact_index_field(self) -> None:
        document = _document()
        signal = build_ui_structure_signals(document)[0]
        self.assertEqual(signal["value"], "form_structure")
        self.assertEqual(signal["source_doc_id"], document["doc_id"])
        self.assertEqual(signal["source_fields"], ["metadata.category", "metadata.component_labels.Input"])
        self.assertEqual(signal["source_values"], ["form_input", 2])
        result = TfidfIndex([document], build_tfidf_index([document])).search("reference", roles=("ui_reference",))[0]
        self.assertEqual(result["ui_structure_signals"], [signal])

    def test_missing_invalid_or_uncontrolled_facts_emit_no_candidate(self) -> None:
        for category, value in (("unknown", 2), ("form_input", 0), ("form_input", True), ("form_input", "2")):
            with self.subTest(category=category, value=value):
                self.assertEqual(build_ui_structure_signals(_document(category=category, input_value=value)), [])
        missing = _document()
        del missing["metadata"]["component_labels"]
        self.assertEqual(build_ui_structure_signals(missing), [])
        self.assertEqual(FORM_STRUCTURE_CATEGORIES, frozenset({"form_input", "login_auth", "settings"}))

    def test_tampering_id_parent_values_or_references_cannot_reach_guidance(self) -> None:
        for field, value in (("signal_id", "forged"), ("source_doc_id", "ui_reference:other"), ("source_values", ["form_input", 0]), ("reference_uris", ["https://bad.test/nope"])):
            context = _context("填写联系信息并提交预约", _document())
            context.retrieval_results["ui_reference"][0]["ui_structure_signals"][0][field] = value
            guidance = RetrievalGuidanceBuilder().build(context)
            self.assertNotIn("form_structure", {item.value for item in guidance.ui_guidance})

    def test_explicit_form_semantics_adopt_and_generic_terms_do_not(self) -> None:
        accepted = _context("填写联系信息并提交预约", _document())
        guidance = RetrievalGuidanceBuilder().build(accepted)
        item = next(item for item in guidance.ui_guidance if item.value == "form_structure")
        self.assertEqual(item.source.adapter_evidence[0]["source_doc_id"], accepted.retrieval_results["ui_reference"][0]["doc_id"])
        guided = RetrievalGuidedPageSpecBuilder().build(accepted, guidance)
        self.assertTrue(any(item.role == "ui_reference" and item.rule.endswith("context_confirmed:form_structure") for item in guided.adopted))
        for text in ("设置页面有按钮和输入", "拍照上传后识别宠物"):
            context = _context(text, _document())
            result = RetrievalGuidedPageSpecBuilder().build(context, RetrievalGuidanceBuilder().build(context))
            self.assertTrue(any(item.role == "ui_reference" and item.rule.endswith("semantic_gate:form_structure") for item in result.ignored))

    def test_old_compact_and_duplicate_sources_remain_stable(self) -> None:
        old = _context("填写表单", _document())
        old.retrieval_results["ui_reference"][0].pop("ui_structure_signals")
        self.assertNotIn("form_structure", {item.value for item in RetrievalGuidanceBuilder().build(old).ui_guidance})
        context = _context("登录并填写账号信息", _document("ui_reference:fixture:first", "login_auth", 3))
        second = deepcopy(context.retrieval_results["ui_reference"][0])
        second["doc_id"] = "ui_reference:fixture:second"
        document = _document("ui_reference:fixture:second", "form_input", 2)
        second["references"], second["ui_structure_signals"] = document["references"], build_ui_structure_signals(document)
        context.retrieval_results["ui_reference"].append(second)
        guidance = RetrievalGuidanceBuilder().build(context)
        result = RetrievalGuidedPageSpecBuilder().build(context, guidance)
        decisions = [item for group in (result.adopted, result.fallback) for item in group if item.role == "ui_reference" and item.guidance_id in {value.guidance_id for value in guidance.ui_guidance if value.value == "form_structure"}]
        self.assertEqual(sum(item.rule.endswith("context_confirmed:form_structure") for item in decisions), 1)
        self.assertEqual(sum(item.rule.endswith("duplicate:form_structure") for item in decisions), 1)
