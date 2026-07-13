from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_rag.retriever import (  # noqa: E402
    DEFAULT_RETRIEVER_REGISTRY,
    Retriever,
    RetrieverConfig,
    RetrieverRegistry,
    TfidfRetriever,
    create_retriever,
)


DEMO_REQUIREMENT = (
    "我想做一个移动端电商应用，支持登录、搜索筛选商品、查看详情、加入购物车和结算，"
    "需要响应式页面、清楚的点击流程，以及输入错误和权限异常的验收。"
)


class AgentChainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.retriever = create_retriever(
            RetrieverConfig(index_dir=ROOT / "data/processed/rag")
        )
        cls.bundle = MinimalAgentChain(
            DeterministicRequirementProvider(),
            cls.retriever,
            top_k_per_role=2,
        ).run(DEMO_REQUIREMENT)

    def test_generates_two_to_four_use_cases(self) -> None:
        self.assertGreaterEqual(len(self.bundle.use_cases), 2)
        self.assertLessEqual(len(self.bundle.use_cases), 4)

    def test_generates_all_role_queries_and_results(self) -> None:
        self.assertEqual(tuple(self.bundle.retrieval_queries), ROLE_ORDER)
        self.assertEqual(tuple(self.bundle.retrieval_results), ROLE_ORDER)
        for role in ROLE_ORDER:
            self.assertTrue(self.bundle.retrieval_queries[role].strip(), role)
            self.assertTrue(self.bundle.retrieval_results[role], role)
            self.assertTrue(
                all(item["role"] == role for item in self.bundle.retrieval_results[role]),
                role,
            )

    def test_bundle_is_json_serializable(self) -> None:
        payload = self.bundle.to_dict()
        self.assertTrue(
            {
                "original_requirement",
                "requirement_summary",
                "target_device",
                "task_type",
                "constraints",
                "use_cases",
                "retrieval_queries",
                "retrieval_results",
            }.issubset(payload)
        )
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertIn('"original_requirement"', encoded)
        self.assertIn('"retrieval_results"', encoded)

    def test_tfidf_uses_unified_retriever_interface(self) -> None:
        self.assertIsInstance(self.retriever, TfidfRetriever)
        self.assertIsInstance(self.retriever, Retriever)
        direct = self.retriever.search(
            "移动电商登录", top_k=1, roles=("requirement",)
        )
        grouped = self.retriever.search_by_role("移动电商登录", top_k=1)
        self.assertTrue(direct)
        self.assertEqual(tuple(grouped), ROLE_ORDER)
        self.assertTrue(all(grouped.values()))

    def test_requirement_provider_is_deterministic_and_local(self) -> None:
        provider = DeterministicRequirementProvider()
        requirement = "做一个宠物情绪识别 App，用户拍照后展示识别结果"
        first = provider.understand(requirement)
        second = provider.understand(requirement)
        self.assertEqual(first, second)
        self.assertEqual(first.target_device, "mobile")
        self.assertEqual(first.task_type, "recognition_tool")
        self.assertGreaterEqual(len(first.use_cases), 2)
        self.assertLessEqual(len(first.use_cases), 4)

    def test_registry_keeps_future_backends_pluggable_but_disabled(self) -> None:
        self.assertEqual(DEFAULT_RETRIEVER_REGISTRY.available_backends(), ("tfidf",))
        registry = RetrieverRegistry()
        registry.register("test_backend", lambda config: self.retriever)
        configured = registry.create(
            RetrieverConfig(
                index_dir=ROOT / "data/processed/rag",
                backend="test_backend",
            )
        )
        self.assertIs(configured, self.retriever)
        with self.assertRaisesRegex(ValueError, "unregistered retriever backend"):
            registry.create(
                RetrieverConfig(
                    index_dir=ROOT / "data/processed/rag",
                    backend="bge_m3",
                )
            )


if __name__ == "__main__":
    unittest.main()
