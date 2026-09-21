from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


direct = load("phase7_direct_html_worker", ROOT / "scripts/phase7_direct_html_worker.py")
experiment = load("phase7_experiment1", ROOT / "scripts/phase7_experiment1.py")


class Phase7Experiment1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.test_temp_root = ROOT / "outputs/phase7_experiment1_v1/test"
        cls.test_temp_root.mkdir(parents=True, exist_ok=True)

    def test_sources_freeze_exact_denominators_and_separation(self) -> None:
        cases, obligations = experiment.load_and_validate_sources()
        self.assertEqual(len(cases["cases"]), 12)
        self.assertEqual(sum(map(len, obligations["cases"].values())), 48)
        self.assertTrue(obligations["evaluator_only"])
        self.assertNotIn("obligations", json.dumps(cases))

    def test_schedule_is_deterministic_balanced_and_complete(self) -> None:
        cases, _ = experiment.load_and_validate_sources()
        first = experiment.fixed_schedule(cases["cases"])
        self.assertEqual(first, experiment.fixed_schedule(cases["cases"]))
        self.assertEqual(len(first), 24)
        self.assertEqual({row["arm"] for row in first}, {"A", "B"})
        self.assertEqual(len({row["row_id"] for row in first}), 24)
        for position in range(12):
            pair = first[position * 2:position * 2 + 2]
            self.assertNotEqual(pair[0]["arm"], pair[1]["arm"])

    def test_direct_prompt_has_public_input_but_no_evaluator_labels(self) -> None:
        cases, _ = experiment.load_and_validate_sources()
        prompt = direct.build_direct_prompt(cases["cases"][0], {"retrieval_guidance": []})
        self.assertIn(cases["cases"][0]["requirement"], prompt)
        self.assertNotIn("expected_verdict", prompt)
        self.assertNotIn("criterion_id", prompt)
        self.assertIn("Return only the HTML document", prompt)

    def test_transport_extraction_is_fixed_and_does_not_repair(self) -> None:
        html = b"<!doctype html><html><body>ok</body></html>"
        self.assertEqual(direct.extract_html(html), (html, "none"))
        fenced = b"```html\n<html><body>ok</body></html>\n```"
        self.assertEqual(direct.extract_html(fenced)[1], "whole_document_html_fence")
        self.assertEqual(direct.extract_html(b"explanation <html></html>"), (None, "unsupported_transport_shape"))

    def test_append_ledger_preserves_started_failures(self) -> None:
        path = self.test_temp_root / "manager-correction-v1" / "calls.jsonl"
        experiment.append_ledger(path, {"event":"generation_started","call_id":"one"})
        experiment.append_ledger(path, {"event":"generation_failed","call_id":"one"})
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([row["event"] for row in rows][-2:], ["generation_started", "generation_failed"])

    def test_write_once_refuses_drift(self) -> None:
        path = self.test_temp_root / "manager-correction-v1" / "value.json"
        experiment.write_once(path, {"a": 1})
        experiment.write_once(path, {"a": 1})
        with self.assertRaisesRegex(experiment.ExperimentError, "drifted"):
            experiment.write_once(path, {"a": 2})

    def test_metrics_keep_unknown_out_of_pass_score(self) -> None:
        rows = []; artifacts={}; _, obligations=experiment.load_and_validate_sources(); root=self.test_temp_root/"metrics-v2"; root.mkdir(parents=True,exist_ok=True)
        for case_index,case_id in enumerate(experiment.EXPECTED_CASE_IDS):
            for arm in ("A", "B"):
                labels = ["pass"] * 4
                if arm == "B" and case_index == 0:
                    labels[-1] = "unknown"
                alias=f"alias-{case_index}-{arm}"; page=root/f"{alias}.html"; experiment.write_once(page,b"<html></html>",raw=True); digest="sha256:"+experiment.hashlib.sha256(page.read_bytes()).hexdigest(); artifacts[alias]=(case_id,arm,digest,page)
                rows.extend({"case_id":case_id,"arm":arm,"criterion_id":criterion,"label":label,"alias":alias,"artifact_sha256":digest,"actions":[],"elapsed_seconds":1,"evidence":"dom","reason":"observed"} for criterion, label in zip(obligations["cases"][case_id],labels))
        result = experiment.metrics(rows,artifacts)
        self.assertEqual(result["arms"]["A"]["mean_case_score"], 1.0)
        self.assertEqual(result["arms"]["B"]["known_passes"], 47)
        self.assertEqual(result["arms"]["B"]["unknown"], 1)
        self.assertEqual(result["arms"]["B"]["sensitivity_upper_mean"], 1.0)

    def test_metrics_withhold_incomplete_comparison(self) -> None:
        page=self.test_temp_root/"metrics-v2/incomplete.html"; experiment.write_once(page,b"<html></html>",raw=True); digest="sha256:"+experiment.hashlib.sha256(page.read_bytes()).hexdigest(); alias="incomplete"
        result = experiment.metrics([{"case_id":experiment.EXPECTED_CASE_IDS[0],"arm":"A","criterion_id":"reject_missing_description","label":"fail","alias":alias,"artifact_sha256":digest,"actions":[],"elapsed_seconds":1,"evidence":"dom","reason":"observed"}],{alias:(experiment.EXPECTED_CASE_IDS[0],"A",digest,page)})
        self.assertEqual(result["comparative_conclusion"], "withheld_incomplete_inventory")
        self.assertIsNone(result["arms"]["A"]["mean_case_score"])

    def test_metrics_reject_duplicate_criterion(self) -> None:
        page=self.test_temp_root/"metrics-v2/duplicate.html"; experiment.write_once(page,b"<html></html>",raw=True); digest="sha256:"+experiment.hashlib.sha256(page.read_bytes()).hexdigest(); alias="duplicate"
        row={"case_id":experiment.EXPECTED_CASE_IDS[0],"arm":"A","criterion_id":"reject_missing_description","label":"pass","alias":alias,"artifact_sha256":digest,"actions":[],"elapsed_seconds":1,"evidence":"dom","reason":"observed"}
        with self.assertRaisesRegex(experiment.ExperimentError,"inventory"):
            experiment.metrics([row,row],{alias:(experiment.EXPECTED_CASE_IDS[0],"A",digest,page)})

    def test_run_requires_manager_approval_before_runtime(self) -> None:
        with self.assertRaisesRegex(experiment.ExperimentError,"manager run approval"):
            experiment.run_experiment(preparation=ROOT/"outputs/phase7_experiment1_v1/preparation",destination=ROOT/"outputs/phase7_experiment1_v1/never-created",model_root=ROOT,integrity_evidence=ROOT/"README.md",manager_approved=False)


    def test_b_keyboard_interrupt_reaps_active_process(self) -> None:
        class Process:
            killed=False
            def poll(self): return None
            def kill(self): self.killed=True
            def communicate(self, timeout=None): return b"",b""
        process=Process(); root=self.test_temp_root/"interrupt-v4"; prep=ROOT/"outputs/phase7_experiment1_v1/preparation-v2-final-authoritative"
        def interrupted(**kwargs): experiment._ACTIVE_RUN["process"]=process; raise KeyboardInterrupt()
        with patch.object(experiment,"DEFAULT_ROOT",root), patch.object(experiment,"validate"), patch.object(experiment,"runtime_preflight",return_value={"runtime_eligible":True}), patch.object(experiment,"_run_experiment_unlocked",side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt): experiment.run_experiment(preparation=prep,destination=root/"run",model_root=ROOT,integrity_evidence=ROOT/"README.md",manager_approved=True)
        self.assertTrue(process.killed)

    def test_measured_claim_rejects_second_output(self) -> None:
        root=self.test_temp_root/"claim-v4"; prep=ROOT/"outputs/phase7_experiment1_v1/preparation-v2-final-authoritative"
        with patch.object(experiment,"DEFAULT_ROOT",root), patch.object(experiment,"validate"), patch.object(experiment,"runtime_preflight",return_value={"runtime_eligible":True}), patch.object(experiment,"_run_experiment_unlocked",return_value={"status":"test-only"}):
            experiment.run_experiment(preparation=prep,destination=root/"run-a",model_root=ROOT,integrity_evidence=ROOT/"README.md",manager_approved=True)
            with self.assertRaisesRegex(experiment.ExperimentError,"already claimed"): experiment.run_experiment(preparation=prep,destination=root/"run-b",model_root=ROOT,integrity_evidence=ROOT/"README.md",manager_approved=True)

    def test_a_uncertain_cleanup_retains_lock(self) -> None:
        class Store:
            def get(self, run_id): return {"status":"failed_closed"}
            def is_busy(self): return True
        root=self.test_temp_root/"a-cleanup-v5"; prep=ROOT/"outputs/phase7_experiment1_v1/preparation-v2-final-authoritative"
        def interrupted(**kwargs): experiment._ACTIVE_RUN.update({"store":Store(),"run_id":"canonical-test"}); raise RuntimeError("test-only interruption")
        with patch.object(experiment,"DEFAULT_ROOT",root), patch.object(experiment,"validate"), patch.object(experiment,"runtime_preflight",return_value={"runtime_eligible":True}), patch.object(experiment,"_run_experiment_unlocked",side_effect=interrupted):
            with self.assertRaises(RuntimeError): experiment.run_experiment(preparation=prep,destination=root/"run",model_root=ROOT,integrity_evidence=ROOT/"README.md",manager_approved=True)
        self.assertTrue((root/".experiment1-single-flight.lock").is_file())

    def test_artifact_import_rejects_alias_misuse_and_asset_tamper(self) -> None:
        prep=ROOT/"outputs/phase7_experiment1_v1/preparation-v2-final-authoritative"; root=self.test_temp_root/"bindings-v5"; root.mkdir(parents=True,exist_ok=True)
        page=root/"page.html"; asset=root/"app.js"; experiment.write_once(page,b"<html></html>",raw=True); experiment.write_once(asset,b"ok",raw=True)
        ph="sha256:"+experiment.hashlib.sha256(page.read_bytes()).hexdigest(); ah="sha256:"+experiment.hashlib.sha256(asset.read_bytes()).hexdigest(); schedule=experiment.read_json(prep/"freeze_manifest.json")["schedule"]; row=schedule[0]
        base={"alias":"artifact-01","row_id":row["row_id"],"case_id":row["case_id"],"arm":row["arm"],"artifact_sha256":ph,"entrypoint":"page.html","package_assets":[{"path":"app.js","sha256":ah}],"selected_delivery_kind":"test-only"}
        inv=root/"inventory.json"; experiment.write_once(inv,{"artifacts":[base,{**base}]})
        with patch.object(experiment,"validate"):
            with self.assertRaisesRegex(experiment.ExperimentError,"duplicated"): experiment.load_artifact_bindings(prep,inv)
        bad=root/"bad.json"; experiment.write_once(bad,{"artifacts":[{**base,"arm":"B" if row["arm"]=="A" else "A"}]})
        with patch.object(experiment,"validate"):
            with self.assertRaisesRegex(experiment.ExperimentError,"schedule"): experiment.load_artifact_bindings(prep,bad)
        asset.write_bytes(b"tampered")
        one=root/"one.json"; experiment.write_once(one,{"artifacts":[base]})
        with patch.object(experiment,"validate"):
            with self.assertRaisesRegex(experiment.ExperimentError,"asset hash"): experiment.load_artifact_bindings(prep,one)


if __name__ == "__main__":
    unittest.main()
