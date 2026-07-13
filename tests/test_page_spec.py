from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain  # noqa: E402
from req2web_generation import (  # noqa: E402
    PAGE_SPEC_SCHEMA_VERSION,
    PageSpecBuilder,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402


ECOMMERCE_REQUIREMENT = (
    "我想做一个移动端电商应用，支持搜索筛选商品、查看详情、加入购物车和结算，"
    "需要清楚的异常反馈。"
)
PET_REQUIREMENT = (
    "做一个宠物情绪识别 App，用户拍照后系统分析宠物情绪并展示结果，"
    "相机权限被拒绝时要给出恢复提示。"
)


class FixtureRetriever:
    def search(
        self,
        query: str,
        top_k: int = 5,
        roles: Iterable[str] | None = None,
    ) -> list[dict[str, object]]:
        role = tuple(roles or ("requirement",))[0]
        return [
            {
                "score": 1.0 - index / 10,
                "doc_id": f"{role}:fixture:sample-{index}",
                "role": role,
                "title": f"{role} fixture {index}",
                "summary": "not copied into PageSpec evidence",
                "references": [
                    {
                        "kind": "fixture",
                        "uri": f"fixtures/{role}/sample-{index}.json",
                    }
                ],
            }
            for index in range(1, top_k + 1)
        ]

    def search_by_role(
        self, query: str, top_k: int = 2
    ) -> dict[str, list[dict[str, object]]]:
        return {
            role: self.search(query, top_k=top_k, roles=(role,))
            for role in ROLE_ORDER
        }


def build_context(requirement: str):
    return MinimalAgentChain(
        DeterministicRequirementProvider(),
        FixtureRetriever(),
        top_k_per_role=2,
    ).run(requirement)


class PageSpecTest(unittest.TestCase):
    def setUp(self) -> None:
        self.context = build_context(ECOMMERCE_REQUIREMENT)
        self.spec = PageSpecBuilder().build(self.context)

    def test_page_spec_is_json_serializable(self) -> None:
        payload = self.spec.to_dict()
        self.assertEqual(payload["schema_version"], PAGE_SPEC_SCHEMA_VERSION)
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertIn('"traceability"', encoded)
        self.assertNotIn("not copied into PageSpec evidence", encoded)

    def test_entity_ids_are_unique_and_stable(self) -> None:
        second = PageSpecBuilder().build(self.context)
        self.assertEqual(self.spec.to_dict(), second.to_dict())
        entity_ids = [
            *(item.section_id for item in self.spec.sections),
            *(item.component_id for item in self.spec.components),
            *(item.interaction_id for item in self.spec.interactions),
        ]
        self.assertEqual(len(entity_ids), len(set(entity_ids)))

    def test_interactions_reference_existing_components_and_states(self) -> None:
        component_ids = {item.component_id for item in self.spec.components}
        state_ids = {item.state_id for item in self.spec.states}
        for interaction in self.spec.interactions:
            self.assertIn(interaction.trigger_component_id, component_ids)
            self.assertIn(interaction.source_state_id, state_ids)
            self.assertIn(interaction.target_state_id, state_ids)

    def test_preserves_core_use_cases_and_five_role_evidence(self) -> None:
        self.assertGreaterEqual(len(self.spec.use_cases), 2)
        self.assertLessEqual(len(self.spec.use_cases), 4)
        evidence_by_id = {
            item.doc_id: item for item in self.spec.traceability.evidence
        }
        self.assertEqual(
            {item.role for item in evidence_by_id.values()}, set(ROLE_ORDER)
        )
        self.assertTrue(
            all(len(item.reference_uris) <= 3 for item in evidence_by_id.values())
        )
        for trace in self.spec.traceability.use_cases:
            self.assertEqual(
                {evidence_by_id[doc_id].role for doc_id in trace.evidence_doc_ids},
                set(ROLE_ORDER),
            )

    def test_builder_supports_ecommerce_and_recognition_requirements(self) -> None:
        recognition_spec = PageSpecBuilder().build(build_context(PET_REQUIREMENT))
        self.assertEqual(self.spec.page_type, "ecommerce")
        self.assertEqual(recognition_spec.page_type, "recognition_tool")
        self.assertNotEqual(self.spec.page_id, recognition_spec.page_id)
        self.assertIn(
            "media_input",
            {item.component_type for item in recognition_spec.components},
        )
        recognition_spec.validate()

    def test_empty_context_input_is_rejected(self) -> None:
        empty = replace(self.context, original_requirement="")
        with self.assertRaisesRegex(ValueError, "original_requirement"):
            PageSpecBuilder().build(empty)

    def test_illegal_page_spec_schema_is_rejected(self) -> None:
        self.spec.schema_version = "req2web.page_spec.invalid"
        with self.assertRaisesRegex(ValueError, "unsupported PageSpec schema"):
            self.spec.validate()

    def test_broken_interaction_reference_is_rejected(self) -> None:
        self.spec.interactions[0].trigger_component_id = "component-missing"
        with self.assertRaisesRegex(ValueError, "unknown component"):
            self.spec.validate()

    def test_broken_rag_trace_reference_is_rejected(self) -> None:
        self.spec.traceability.use_cases[0].evidence_doc_ids = ["doc-missing"]
        with self.assertRaisesRegex(ValueError, "unknown references"):
            self.spec.validate()

    def test_illegal_agent_context_schema_is_rejected(self) -> None:
        invalid = replace(self.context, schema_version="req2web.agent.context.invalid")
        with self.assertRaisesRegex(ValueError, "unsupported Agent context schema"):
            PageSpecBuilder().build(invalid)


if __name__ == "__main__":
    unittest.main()
