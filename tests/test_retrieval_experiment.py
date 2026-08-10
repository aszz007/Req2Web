from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
from uuid import uuid4


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_rag.retrieval_comparison import (  # noqa: E402
    run_retrieval_comparison,
)
from req2web_rag.retrieval_experiment import (  # noqa: E402
    RetrievalExperimentError,
    prepare_retrieval_experiment,
    validate_retrieval_experiment,
)
from req2web_rag.retrieval_qrels import (  # noqa: E402
    RetrievalQrelsError,
    assemble_retrieval_qrels,
    prepare_human_review_packets,
    validate_human_review_packets,
)


class RetrievalExperimentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = ROOT / "fixtures" / "demo_v2_regression_cases_v1.json"
        cls.index_dir = ROOT / "data" / "processed" / "rag"
        cls.temp_root = ROOT / "tests" / f".tmp-retrieval-experiment-{uuid4().hex}"
        cls.comparison_root = cls.temp_root / "comparison"
        cls.experiment_root = cls.temp_root / "experiment"
        cls.temp_root.mkdir()
        run_retrieval_comparison(
            fixture_path=cls.fixture,
            index_dir=cls.index_dir,
            output_root=cls.comparison_root,
        )
        prepare_retrieval_experiment(
            fixture_path=cls.fixture,
            index_dir=cls.index_dir,
            comparison_root=cls.comparison_root,
            output_root=cls.experiment_root,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp_root, ignore_errors=True)

    def _completed_llm_packet(self) -> Path:
        packet = json.loads(
            (
                self.experiment_root
                / "annotation"
                / "llm_prelabel_template.json"
            ).read_text(encoding="utf-8")
        )
        packet["status"] = "completed"
        packet["model_run"] = {
            "provider": "synthetic-test-provider",
            "model_id": "synthetic-test-model-v1",
            "raw_response_sha256": "a" * 64,
            "paid_api_used": False,
        }
        for unit in packet["units"]:
            for candidate in unit["candidates"]:
                candidate["suggested_relevance"] = 2
                candidate["rationale"] = "Relevant synthetic test evidence."
                candidate["confidence"] = "high"
        path = self.temp_root / f"completed-llm-{uuid4().hex}.json"
        path.write_text(
            json.dumps(packet, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def _prepared_human_review(self) -> Path:
        root = self.temp_root / f"human-review-{uuid4().hex}"
        prepare_human_review_packets(
            fixture_path=self.fixture,
            index_dir=self.index_dir,
            comparison_root=self.comparison_root,
            experiment_root=self.experiment_root,
            llm_prelabel_packet=self._completed_llm_packet(),
            output_root=root,
        )
        return root

    def _complete_review_packets(
        self, review_root: Path
    ) -> tuple[Path, Path, Path]:
        completed = []
        for slot in ("human_reviewer_1", "human_reviewer_2"):
            packet = json.loads(
                (review_root / f"{slot}_template.json").read_text(encoding="utf-8")
            )
            packet["status"] = "completed"
            for item in packet["items"]:
                item["human_relevance"] = 2
            path = self.temp_root / f"completed-{slot}-{uuid4().hex}.json"
            path.write_text(json.dumps(packet, sort_keys=True), encoding="utf-8")
            completed.append(path)
        joint = json.loads(
            (review_root / "joint_resolution_template.json").read_text(
                encoding="utf-8"
            )
        )
        joint["status"] = "completed"
        for item in joint["items"]:
            item["final_relevance"] = 2
        joint_path = self.temp_root / f"completed-joint-{uuid4().hex}.json"
        joint_path.write_text(json.dumps(joint, sort_keys=True), encoding="utf-8")
        return completed[0], completed[1], joint_path

    def test_prelabel_template_and_two_human_assignment_are_complete(self) -> None:
        packet = json.loads(
            (
                self.experiment_root
                / "annotation"
                / "llm_prelabel_template.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(packet["status"], "template_unscored")
        self.assertEqual(len(packet["units"]), 60)
        self.assertNotRegex(json.dumps(packet, ensure_ascii=False), r"[\u3400-\u9fff]")
        candidates = [
            candidate
            for unit in packet["units"]
            for candidate in unit["candidates"]
        ]
        self.assertEqual(len(candidates), 420)
        self.assertTrue(all(row["suggested_relevance"] is None for row in candidates))
        assignment = json.loads(
            (
                self.experiment_root
                / "annotation"
                / "human_review_assignment.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(assignment["candidate_count"], 420)
        self.assertEqual(assignment["blind_overlap_audit_count"], 84)
        self.assertEqual(
            assignment["judgments_per_reviewer"],
            {"human_reviewer_1": 252, "human_reviewer_2": 252},
        )
        audit = [
            row
            for row in assignment["assignments"]
            if row["review_mode"] == "blind_overlap_audit"
        ]
        self.assertEqual(len(audit), 84)
        self.assertTrue(
            all(not row["llm_suggestion_visible_during_initial_review"] for row in audit)
        )

    def test_efficiency_evidence_binds_the_same_ranked_results(self) -> None:
        result = validate_retrieval_experiment(
            fixture_path=self.fixture,
            index_dir=self.index_dir,
            comparison_root=self.comparison_root,
            output_root=self.experiment_root,
        )
        self.assertEqual(result["status"], "prepared_awaiting_llm_prelabels")
        self.assertEqual(result["llm_prelabel_template_count"], 1)
        self.assertEqual(result["human_reviewer_count"], 2)
        self.assertEqual(result["blind_overlap_audit_count"], 84)
        self.assertFalse(result["ranking_quality_computed"])
        self.assertFalse(result["winner_declared"])
        for method in result["efficiency"]["methods"]:
            self.assertTrue(method["all_repetitions_exactly_match_comparison"])

    def test_validation_rejects_prelabel_template_tampering(self) -> None:
        tampered = self.temp_root / f"tampered-{uuid4().hex}"
        shutil.copytree(self.experiment_root, tampered)
        packet_path = tampered / "annotation" / "llm_prelabel_template.json"
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        packet["units"][0]["candidates"][0]["title"] = "Tampered title"
        packet_path.write_text(json.dumps(packet, sort_keys=True), encoding="utf-8")
        with self.assertRaises(RetrievalExperimentError):
            validate_retrieval_experiment(
                fixture_path=self.fixture,
                index_dir=self.index_dir,
                comparison_root=self.comparison_root,
                output_root=tampered,
            )

    def test_completed_assisted_review_assembles_replayable_qrels(self) -> None:
        review_root = self._prepared_human_review()
        validated = validate_human_review_packets(
            fixture_path=self.fixture,
            index_dir=self.index_dir,
            comparison_root=self.comparison_root,
            experiment_root=self.experiment_root,
            output_root=review_root,
        )
        self.assertEqual(validated["judgments_per_reviewer"], 252)
        reviewer_1, reviewer_2, joint = self._complete_review_packets(review_root)
        output = self.temp_root / f"qrels-{uuid4().hex}.json"
        result = assemble_retrieval_qrels(
            fixture_path=self.fixture,
            index_dir=self.index_dir,
            comparison_root=self.comparison_root,
            experiment_root=self.experiment_root,
            review_root=review_root,
            reviewer_1_packet=reviewer_1,
            reviewer_2_packet=reviewer_2,
            joint_resolution_packet=joint,
            reviewer_1_id="reviewer-a",
            reviewer_2_id="reviewer-b",
            output_path=output,
        )
        self.assertEqual(result["status"], "complete_assisted_qrels_assembled")
        self.assertEqual(result["model_prelabel_count"], 420)
        self.assertEqual(result["human_reviewed_candidate_count"], 420)
        self.assertEqual(result["audit_agreement_value"], 1.0)
        self.assertFalse(result["independent_human_gold_claim_allowed"])
        qrels = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(qrels["model_prelabels"]["judgments"]), 420)
        self.assertEqual(len(qrels["human_review"]["final_judgments"]), 420)
        self.assertEqual(
            qrels["human_review"]["blind_overlap_audit"]["candidate_count"],
            84,
        )
        with self.assertRaisesRegex(RetrievalQrelsError, "distinct"):
            assemble_retrieval_qrels(
                fixture_path=self.fixture,
                index_dir=self.index_dir,
                comparison_root=self.comparison_root,
                experiment_root=self.experiment_root,
                review_root=review_root,
                reviewer_1_packet=reviewer_1,
                reviewer_2_packet=reviewer_2,
                joint_resolution_packet=joint,
                reviewer_1_id="reviewer-a",
                reviewer_2_id="reviewer-a",
                output_path=self.temp_root / f"invalid-ids-{uuid4().hex}.json",
            )

    def test_prelabel_and_human_packet_tampering_fail_closed(self) -> None:
        packet = json.loads(self._completed_llm_packet().read_text(encoding="utf-8"))
        packet["units"][0]["candidates"][0]["title"] = "Changed title"
        tampered_llm = self.temp_root / f"tampered-llm-{uuid4().hex}.json"
        tampered_llm.write_text(json.dumps(packet), encoding="utf-8")
        with self.assertRaises(RetrievalQrelsError):
            prepare_human_review_packets(
                fixture_path=self.fixture,
                index_dir=self.index_dir,
                comparison_root=self.comparison_root,
                experiment_root=self.experiment_root,
                llm_prelabel_packet=tampered_llm,
                output_root=self.temp_root / f"invalid-review-{uuid4().hex}",
            )
        review_root = self._prepared_human_review()
        reviewer_1, reviewer_2, joint = self._complete_review_packets(review_root)
        tampered = json.loads(reviewer_1.read_text(encoding="utf-8"))
        tampered["items"][0]["candidate"]["title"] = "Changed human packet"
        reviewer_1.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaises(RetrievalQrelsError):
            assemble_retrieval_qrels(
                fixture_path=self.fixture,
                index_dir=self.index_dir,
                comparison_root=self.comparison_root,
                experiment_root=self.experiment_root,
                review_root=review_root,
                reviewer_1_packet=reviewer_1,
                reviewer_2_packet=reviewer_2,
                joint_resolution_packet=joint,
                reviewer_1_id="reviewer-a",
                reviewer_2_id="reviewer-b",
                output_path=self.temp_root / f"invalid-qrels-{uuid4().hex}.json",
            )


if __name__ == "__main__":
    unittest.main()
