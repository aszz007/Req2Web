from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
from uuid import uuid4


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_generation.demo_regression import (  # noqa: E402
    DemoV2RegressionRunner,
    RegressionCaseSet,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_rag.retrieval_comparison import (  # noqa: E402
    RETRIEVAL_METRIC_PROTOCOL_REVISION,
    RETRIEVAL_QRELS_SCHEMA_VERSION,
    RetrievalComparisonError,
    _linear_weighted_kappa,
    _load_qrels,
    _unit_metrics,
    build_candidate_units,
    evaluate_rankings,
)
from req2web_rag.retriever import (  # noqa: E402
    DEFAULT_RETRIEVER_REGISTRY,
    RetrieverConfig,
    create_retriever,
)


class RetrievalComparisonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index_dir = ROOT / "data" / "processed" / "rag"
        cls.fixture = ROOT / "fixtures" / "demo_v2_regression_cases_v1.json"
        cls.case_set = RegressionCaseSet.load(cls.fixture)

    def setUp(self) -> None:
        self.temp_root = ROOT / "tests" / f".tmp-retrieval-comparison-{uuid4().hex}"
        self.temp_root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_retrieval_backends_are_registered_and_preserve_the_contract(self) -> None:
        self.assertEqual(
            DEFAULT_RETRIEVER_REGISTRY.available_backends(),
            ("bm25", "rrf", "tfidf"),
        )
        for backend in ("bm25", "rrf"):
            retriever = create_retriever(
                RetrieverConfig(index_dir=self.index_dir, backend=backend)
            )
            first = retriever.search(
                "responsive checkout validation",
                top_k=3,
                roles=("implementation",),
            )
            second = retriever.search(
                "responsive checkout validation",
                top_k=3,
                roles=("implementation",),
            )
            self.assertEqual(first, second)
            self.assertEqual(len(first), 3)
            self.assertTrue(
                all(row["role"] == "implementation" for row in first)
            )
            self.assertTrue(all(row["score"] > 0 for row in first))
            self.assertTrue(
                all("result_projection_revision" in row for row in first)
            )

    def test_bm25_options_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            create_retriever(
                RetrieverConfig(
                    index_dir=self.index_dir,
                    backend="bm25",
                    options={"unknown": 1},
                )
            )
        with self.assertRaises(ValueError):
            create_retriever(
                RetrieverConfig(
                    index_dir=self.index_dir,
                    backend="rrf",
                    options={"source_depth": 0},
                )
            )
        with self.assertRaises(ValueError):
            create_retriever(
                RetrieverConfig(
                    index_dir=self.index_dir,
                    backend="bm25",
                    options={"k1": 0},
                )
            )

    def test_default_regression_backend_remains_tfidf(self) -> None:
        runner = DemoV2RegressionRunner(index_dir=self.index_dir)
        self.assertEqual(runner.backend, "tfidf")

    def test_metric_definitions_reward_earlier_graded_relevance(self) -> None:
        ideal = _unit_metrics(
            ["high", "medium", "none"],
            {"high": 3, "medium": 2, "none": 0},
            top_k=3,
        )
        reversed_ranking = _unit_metrics(
            ["medium", "none", "high"],
            {"high": 3, "medium": 2, "none": 0},
            top_k=3,
        )
        self.assertEqual(ideal["pooled_recall_at_3"], 1.0)
        self.assertEqual(reversed_ranking["pooled_recall_at_3"], 1.0)
        self.assertGreater(
            ideal["pooled_ndcg_at_3"],
            reversed_ranking["pooled_ndcg_at_3"],
        )
        self.assertGreater(
            ideal["pooled_map_at_3"],
            reversed_ranking["pooled_map_at_3"],
        )

    def test_grade_one_is_marginal_not_binary_relevant(self) -> None:
        result = _unit_metrics(
            ["marginal", "relevant"],
            {"marginal": 1, "relevant": 2},
            top_k=2,
        )
        self.assertEqual(result["pooled_recall_at_2"], 1.0)
        self.assertEqual(result["pooled_precision_at_2"], 0.5)
        self.assertEqual(result["pooled_mrr_at_2"], 0.5)

    def test_evaluation_uses_cases_as_the_statistical_unit(self) -> None:
        units = [
            {
                "case_id": "case-a",
                "role": role,
                "candidates": {
                    "bm25": [{"doc_id": "positive"}, {"doc_id": "negative"}],
                    "rrf": [{"doc_id": "positive"}, {"doc_id": "negative"}],
                    "tfidf": [{"doc_id": "negative"}, {"doc_id": "positive"}],
                },
            }
            for role in ROLE_ORDER
        ]
        qrels = {
            ("case-a", role): {"positive": 3, "negative": 0}
            for role in ROLE_ORDER
        }
        result = evaluate_rankings(
            units=units,
            qrels=qrels,
            methods=("bm25", "rrf", "tfidf"),
            top_k=2,
        )
        bm25, rrf, tfidf = result["methods"]
        self.assertEqual(result["query_role_unit_count"], 5)
        self.assertEqual(result["statistical_unit"], "case")
        self.assertEqual(result["case_count"], 1)
        self.assertEqual(len(bm25["macro_by_role"]), 5)
        self.assertGreater(
            bm25["macro_over_cases"]["pooled_ndcg_at_2"],
            tfidf["macro_over_cases"]["pooled_ndcg_at_2"],
        )
        self.assertEqual(
            len(result["paired_case_analysis"]["comparisons"]),
            3,
        )
        self.assertEqual(
            result["paired_case_analysis"]["comparisons"][0][
                "difference_direction"
            ],
            "bm25_minus_rrf",
        )
        self.assertEqual(
            rrf["macro_over_cases"]["pooled_ndcg_at_2"],
            bm25["macro_over_cases"]["pooled_ndcg_at_2"],
        )
        for comparison in result["paired_case_analysis"]["comparisons"]:
            for metric in comparison["metrics"]:
                self.assertIn("exact_sign_flip_p_value", metric)
                self.assertIn("holm_adjusted_p_value", metric)
        self.assertFalse(result["winner_declared"])

    def test_linear_weighted_kappa_replays_raw_ordinal_ratings(self) -> None:
        first = {("case-a", "requirement"): {"a": 3, "b": 1}}
        identical = {("case-a", "requirement"): {"a": 3, "b": 1}}
        reversed_ratings = {("case-a", "requirement"): {"a": 0, "b": 3}}
        self.assertEqual(_linear_weighted_kappa(first, identical), 1.0)
        self.assertLess(_linear_weighted_kappa(first, reversed_ratings), 0.0)

    def test_qrels_require_a_complete_pool(self) -> None:
        units = build_candidate_units(
            case_set=self.case_set,
            index_dir=self.index_dir,
            top_k=1,
        )
        first = units[0]
        qrels_path = self.temp_root / "incomplete_qrels.json"
        qrels_path.write_text(
            json.dumps(
                {
                    "schema_version": RETRIEVAL_QRELS_SCHEMA_VERSION,
                    "protocol_revision": RETRIEVAL_METRIC_PROTOCOL_REVISION,
                    "case_set_id": self.case_set.case_set_id,
                    "annotators": [
                        {"annotator_id": "a1", "judgments": []},
                        {"annotator_id": "a2", "judgments": []},
                    ],
                    "adjudication": {
                        "adjudicator_id": "lead",
                        "policy_revision": "independent_raw_then_adjudicate_v1",
                        "judgments": [],
                    },
                    "agreement": {
                        "metric": "linear_weighted_cohen_kappa",
                        "value": 1.0,
                    },
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(RetrievalComparisonError):
            _load_qrels(path=qrels_path, case_set=self.case_set, units=units)

    def test_complete_two_annotator_qrels_replay(self) -> None:
        units = build_candidate_units(
            case_set=self.case_set,
            index_dir=self.index_dir,
            top_k=1,
        )
        judgments = []
        for unit in units:
            doc_ids = sorted(
                {
                    candidate["doc_id"]
                    for method in ("bm25", "rrf", "tfidf")
                    for candidate in unit["candidates"][method]
                }
            )
            for index, doc_id in enumerate(doc_ids):
                judgments.append(
                    {
                        "case_id": unit["case_id"],
                        "role": unit["role"],
                        "query_sha256": unit["query_sha256"],
                        "doc_id": doc_id,
                        "relevance": 3 if index == 0 else 0,
                    }
                )
        qrels_path = self.temp_root / "complete_qrels.json"
        qrels_path.write_text(
            json.dumps(
                {
                    "schema_version": RETRIEVAL_QRELS_SCHEMA_VERSION,
                    "protocol_revision": RETRIEVAL_METRIC_PROTOCOL_REVISION,
                    "case_set_id": self.case_set.case_set_id,
                    "annotators": [
                        {"annotator_id": "a1", "judgments": judgments},
                        {"annotator_id": "a2", "judgments": judgments},
                    ],
                    "adjudication": {
                        "adjudicator_id": "lead",
                        "policy_revision": "independent_raw_then_adjudicate_v1",
                        "judgments": judgments,
                    },
                    "agreement": {
                        "metric": "linear_weighted_cohen_kappa",
                        "value": 1.0,
                    },
                }
            ),
            encoding="utf-8",
        )
        loaded, metadata = _load_qrels(
            path=qrels_path,
            case_set=self.case_set,
            units=units,
        )
        self.assertEqual(len(loaded), 60)
        self.assertEqual(metadata["annotator_count"], 2)
        self.assertEqual(metadata["agreement_value"], 1.0)

    def test_honest_all_low_pool_is_retained_as_insufficient(self) -> None:
        units = build_candidate_units(
            case_set=self.case_set,
            index_dir=self.index_dir,
            top_k=1,
        )
        judgments = []
        for unit in units:
            doc_ids = sorted(
                {
                    candidate["doc_id"]
                    for method in ("bm25", "rrf", "tfidf")
                    for candidate in unit["candidates"][method]
                }
            )
            for doc_id in doc_ids:
                judgments.append(
                    {
                        "case_id": unit["case_id"],
                        "role": unit["role"],
                        "query_sha256": unit["query_sha256"],
                        "doc_id": doc_id,
                        "relevance": 0,
                    }
                )
        qrels_path = self.temp_root / "all_low_qrels.json"
        qrels_path.write_text(
            json.dumps(
                {
                    "schema_version": RETRIEVAL_QRELS_SCHEMA_VERSION,
                    "protocol_revision": RETRIEVAL_METRIC_PROTOCOL_REVISION,
                    "case_set_id": self.case_set.case_set_id,
                    "annotators": [
                        {"annotator_id": "a1", "judgments": judgments},
                        {"annotator_id": "a2", "judgments": judgments},
                    ],
                    "adjudication": {
                        "adjudicator_id": "lead",
                        "policy_revision": "independent_raw_then_adjudicate_v1",
                        "judgments": judgments,
                    },
                    "agreement": {
                        "metric": "linear_weighted_cohen_kappa",
                        "value": 1.0,
                    },
                }
            ),
            encoding="utf-8",
        )
        loaded, metadata = _load_qrels(
            path=qrels_path,
            case_set=self.case_set,
            units=units,
        )
        self.assertEqual(len(loaded), 60)
        self.assertEqual(metadata["insufficient_pool_unit_count"], 60)


if __name__ == "__main__":
    unittest.main()
