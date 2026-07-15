from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest import TestCase


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import AgentContextBundle, UseCase  # noqa: E402
from req2web_generation import RetrievalGuidanceBuilder, RetrievalGuidedPageSpecBuilder  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_rag.index import TfidfIndex, build_tfidf_index  # noqa: E402
from req2web_rag.validation_signals import (  # noqa: E402
    VALIDATION_CATEGORY_TO_CONTROLLED_VALUE,
    build_validation_signals,
)


def _reference(uri: str = "https://example.test/issues/1") -> list[dict[str, str]]:
    return [{"kind": "issue", "uri": uri}, {"kind": "pull_request", "uri": "https://example.test/pull/1"}]


def _document(doc_id: str, category: str = "input_error", references: list[dict[str, str]] | None = None) -> dict:
    return {
        "schema_version": "req2web.rag.document.v1", "doc_id": doc_id, "role": "validation", "dataset": "github_issues_prs", "subset": "ghpr",
        "sample_id": doc_id.rsplit(":", 1)[-1], "title": "Plain validation evidence", "summary": "Structured validation evidence.",
        "content": "validation evidence", "tags": ["validation"], "references": _reference() if references is None else references,
        "source": {"manifest_path": "data/processed/selection_manifest.csv", "manifest_source_path": "derived", "record_path": "data/processed/github_issues_prs_rag_records.jsonl"},
        "metadata": {"category": category},
    }


def _result(role: str, doc_id: str, title: str, summary: str, references: list[dict[str, str]]) -> dict:
    return {"score": 1.0, "doc_id": doc_id, "role": role, "dataset": "fixture", "subset": "v1", "sample_id": doc_id.rsplit(":", 1)[-1], "title": title, "summary": summary, "references": references}


def _context(validation: list[dict]) -> AgentContextBundle:
    results = {
        "requirement": [_result("requirement", "requirement:fixture:one", "Search", "search result", [])],
        "ui_reference": [_result("ui_reference", "ui_reference:fixture:one", "Search", "search result", [])],
        "interaction_flow": [_result("interaction_flow", "interaction_flow:fixture:one", "tap", "tap flow", [])],
        "implementation": [_result("implementation", "implementation:fixture:one", "structure", "layout", [])],
        "validation": validation,
    }
    return AgentContextBundle(
        original_requirement="做一个搜索页面，输入错误时允许修改并重试。",
        requirement_summary="搜索和可恢复输入错误。", target_device="mobile", task_type="search",
        constraints=["输入错误时允许修改并重试"],
        use_cases=[UseCase("UC-01", "搜索", "用户", "搜索内容", "查看结果"), UseCase("UC-02", "修改输入", "用户", "修改错误输入", "重新搜索")],
        retrieval_queries={role: role for role in ROLE_ORDER}, retrieval_results=results,
    )


def _validation_result(doc_id: str, category: str = "input_error") -> dict:
    document = _document(doc_id, category)
    value = _result("validation", doc_id, "Plain validation evidence", "Structured validation evidence.", document["references"])
    value["validation_signals"] = build_validation_signals(document)
    return value


class ValidationSignalAdapterTest(TestCase):
    def test_compact_signal_has_structured_source_and_whitelisted_reference(self) -> None:
        signal = build_validation_signals(_document("validation:fixture:input"))[0]
        self.assertEqual(signal["value"], "retry_recovery")
        self.assertEqual(signal["source_field"], "metadata.category")
        self.assertEqual(signal["source_value"], "input_error")
        self.assertEqual(signal["outcome"], "candidate")
        self.assertTrue(signal["reference_uri"].startswith("https://"))

    def test_unknown_or_insufficient_source_never_becomes_candidate(self) -> None:
        audit = build_validation_signals(_document("validation:fixture:unknown", "ui_feedback"))
        self.assertEqual(audit[0]["outcome"], "audit_only")
        self.assertEqual(audit[0]["value"], "evidence_only")
        self.assertEqual(build_validation_signals(_document("validation:fixture:missing", references=[])), [])

        guidance = RetrievalGuidanceBuilder().build(_context([_validation_result("validation:fixture:unknown", "ui_feedback")]))
        audit_items = [item for item in guidance.validation_guidance if item.source.adapter_evidence]
        self.assertEqual(len(audit_items), 1)
        self.assertEqual(audit_items[0].value, "regression_case")
        self.assertEqual(audit_items[0].source.adapter_evidence[0]["outcome"], "audit_only")

    def test_tampered_signal_or_source_field_falls_back_without_adoption(self) -> None:
        invalid = _validation_result("validation:fixture:tampered")
        invalid["validation_signals"][0]["source_value"] = "auth_access"
        guidance = RetrievalGuidanceBuilder().build(_context([invalid]))
        self.assertNotIn("retry_recovery", {item.value for item in guidance.validation_guidance})
        result = RetrievalGuidedPageSpecBuilder().build(_context([invalid]), guidance)
        self.assertFalse(any(item.role == "validation" for item in result.adopted))
        self.assertTrue(any(item.role == "validation" and item.rule.endswith("evidence_only") for item in result.fallback))

        invalid_reference = _validation_result("validation:fixture:bad-reference")
        invalid_reference["validation_signals"][0]["reference_uri"] = "https://example.test/issues/not-whitelisted"
        guidance = RetrievalGuidanceBuilder().build(_context([invalid_reference]))
        self.assertNotIn("retry_recovery", {item.value for item in guidance.validation_guidance})

    def test_explicit_semantic_gate_rejects_mismatched_recovery_signal(self) -> None:
        context = _context([_validation_result("validation:fixture:permission", "auth_access")])
        guidance = RetrievalGuidanceBuilder().build(context)
        self.assertIn("permission_recovery", {item.value for item in guidance.validation_guidance})
        result = RetrievalGuidedPageSpecBuilder().build(context, guidance)
        self.assertFalse(any(item.role == "validation" for item in result.adopted))
        self.assertTrue(any(
            item.role == "validation" and item.rule == "validation_guidance:v1:explicit_context_gate:permission_recovery"
            for item in result.ignored
        ))

    def test_old_compact_result_remains_compatible(self) -> None:
        old = _validation_result("validation:fixture:legacy")
        old.pop("validation_signals")
        guidance = RetrievalGuidanceBuilder().build(_context([old]))
        self.assertNotIn("retry_recovery", {item.value for item in guidance.validation_guidance})
        self.assertIn("regression_case", {item.value for item in guidance.validation_guidance})

    def test_different_doc_ids_same_signal_are_deterministically_deduplicated(self) -> None:
        context = _context([_validation_result("validation:fixture:first"), _validation_result("validation:fixture:second")])
        first = RetrievalGuidanceBuilder().build(context)
        second = RetrievalGuidanceBuilder().build(deepcopy(context))
        self.assertEqual(first.to_dict(), second.to_dict())
        candidates = [item for item in first.validation_guidance if item.value == "retry_recovery"]
        self.assertEqual([item.source.doc_id for item in candidates], ["validation:fixture:first", "validation:fixture:second"])
        guided = RetrievalGuidedPageSpecBuilder().build(context, first)
        decisions = {item.guidance_id: item for group in (guided.adopted, guided.fallback) for item in group if item.role == "validation"}
        self.assertEqual(sum(decisions[item.guidance_id].rule.endswith("explicit_context_gate:retry_recovery") for item in candidates), 1)
        self.assertEqual(sum(decisions[item.guidance_id].rule.endswith("duplicate:retry_recovery") for item in candidates), 1)

    def test_retriever_compact_result_exposes_optional_signal_only_for_validation(self) -> None:
        document = _document("validation:fixture:index")
        index = TfidfIndex([document], build_tfidf_index([document]))
        result = index.search("validation", roles=("validation",))[0]
        self.assertEqual(result["validation_signals"][0]["value"], "retry_recovery")

    def test_mapping_has_no_case_specific_constants(self) -> None:
        self.assertEqual(VALIDATION_CATEGORY_TO_CONTROLLED_VALUE, {"input_error": "retry_recovery", "auth_access": "permission_recovery"})
        source = (ROOT / "src" / "req2web_rag" / "validation_signals.py").read_text(encoding="utf-8")
        for forbidden in ("ecommerce", "pet-recognition", "mobile-auth", "desktop-dashboard"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    import unittest

    unittest.main()
