from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import DeterministicRequirementProvider, MinimalAgentChain  # noqa: E402
from req2web_generation import contains_cjk_text  # noqa: E402
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
    "Create a responsive mobile commerce page with sign-in, product search "
    "and filters, product details, a cart, checkout, clear interactions, "
    "and acceptance coverage for invalid input and denied permissions."
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
            DEMO_REQUIREMENT,
            top_k=1,
            roles=("requirement",),
        )
        grouped = self.retriever.search_by_role(
            DEMO_REQUIREMENT,
            top_k=1,
        )
        self.assertTrue(direct)
        self.assertEqual(tuple(grouped), ROLE_ORDER)
        self.assertEqual(grouped["requirement"], direct)

    def test_requirement_provider_is_deterministic_and_local(self) -> None:
        provider = DeterministicRequirementProvider()
        requirement = (
            "Create a mobile pet-emotion recognition app that analyzes a "
            "photo and presents the result."
        )
        first = provider.understand(requirement)
        second = provider.understand(requirement)
        self.assertEqual(first, second)
        self.assertEqual(first.target_device, "mobile")
        self.assertEqual(first.task_type, "recognition_tool")
        self.assertGreaterEqual(len(first.use_cases), 2)
        self.assertLessEqual(len(first.use_cases), 4)

    def test_english_publication_provider_emits_english_only_context(self) -> None:
        provider = DeterministicRequirementProvider(output_language="en")
        result = provider.understand(
            (
                "Create a mobile grocery checkout page with search, filters, "
                "a cart, checkout, and recoverable validation errors."
            ),
            target_device="mobile",
            task_type="ecommerce",
            constraints=("Keep the checkout form accessible.",),
        )
        visible_values = [
            result.requirement_summary,
            *result.constraints,
            *(
                value
                for item in result.use_cases
                for value in (
                    item.title,
                    item.actor,
                    item.goal,
                    item.expected_outcome,
                )
            ),
        ]
        self.assertTrue(visible_values)
        self.assertFalse(any(contains_cjk_text(value) for value in visible_values))

    def test_english_publication_provider_rejects_untranslated_input(self) -> None:
        provider = DeterministicRequirementProvider(
            output_language="en",
            strict_english_input=True,
        )
        with self.assertRaisesRegex(
            ValueError,
            "requires English inputs",
        ):
            provider.understand(
                "\u521b\u5efa\u4e00\u4e2a\u79fb\u52a8\u7aef"
                "\u7ed3\u7b97\u9875\u9762"
            )

    def test_registry_exposes_only_the_three_local_lexical_backends(self) -> None:
        self.assertEqual(
            DEFAULT_RETRIEVER_REGISTRY.available_backends(),
            ("bm25", "rrf", "tfidf"),
        )
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
