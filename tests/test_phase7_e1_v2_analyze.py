from __future__ import annotations

import unittest
import json
from pathlib import Path
import shutil
import uuid

from scripts import phase7_e1_v2_analyze as analysis
from scripts import phase7_e1_v2_observe as observer
from scripts.phase7_e1_v2_cases import cases, frozen_input_identity


class E1V2AnalysisTests(unittest.TestCase):
    def test_paired_summary_uses_cases_not_obligations_as_resampling_units(self) -> None:
        scores = {
            f"case-{index}": {"A": index % 7, "B": max(0, index % 7 - 1), "C": index % 7}
            for index in range(12)
        }
        result = analysis._paired_bootstrap(scores, "A", "B")
        self.assertEqual(len(result["case_differences"]), 12)
        self.assertEqual(result["bootstrap_seed"], 20260917)
        self.assertFalse(result["generalization_claimed"])
        self.assertEqual(result["unit"], "passed_obligations_per_case_out_of_6")

    def test_complete_bound_fixture_aggregates_three_arms(self) -> None:
        root=Path(__file__).resolve().parents[1]/"outputs/phase7_e1_v2_analysis"/uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            measured=[row for row in cases() if row["split"]=="measured"]
            runtime_rows=[]
            primary=[]
            for case in measured:
                for arm in observer.ARMS:
                    runtime_rows.append({"case_id":case["case_id"],"arm":arm,"status":"completed_model_delivery" if arm=="A" else "artifact_ready","started_calls":4 if arm=="A" else 1,"elapsed_seconds":1.0,"metrics":{"input_tokens":10,"output_tokens":5}})
                    for criterion in observer.obligations_for_case(case["case_id"]):
                        primary.append({"case_id":case["case_id"],"family":case["family"],"arm":arm,"criterion_id":criterion,"label":"pass","after_visible_text":"stable"})
            runtime={"split":"measured","automatic_retry_count":0,"rows":runtime_rows}
            runtime_path=root/"runtime.json"; runtime_path.write_text(json.dumps(runtime),encoding="utf-8")
            replays=[{**row,"run_kind":"supplementary_replay"} for row in primary if row["case_id"] in observer.REPLAY_CASE_IDS]
            observation={"status":"observation_complete","split":"measured","input_identity":frozen_input_identity(),"observer_identity":observer.observer_identity(),"artifact_manifest_sha256":analysis._sha(runtime_path.read_bytes()),"primary":{"rows":primary},"supplementary_replay":{"rows":replays}}
            observation_path=root/"observation.json"; observation_path.write_text(json.dumps(observation),encoding="utf-8")
            result=analysis.analyze(observation_path=observation_path,runtime_manifest_path=runtime_path)
            self.assertEqual(result["arms"]["A"]["primary"]["pass"],72)
            self.assertEqual(result["arms"]["B"]["resources"]["started_calls"],12)
            self.assertEqual(result["supplementary_replay"]["same_label_count"],54)
        finally:
            shutil.rmtree(root,ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
