from __future__ import annotations

import json
from pathlib import Path
import tempfile
import shutil
import uuid
import unittest

from scripts import phase7_e1_v2_runtime as runtime

TEST_ROOT=Path(__file__).resolve().parents[1]/"outputs/phase7_e1_v2_runtime/test-tmp"
TEST_ROOT.mkdir(parents=True,exist_ok=True)

def fresh_root(prefix: str) -> Path:
    root=TEST_ROOT/f"{prefix}-{uuid.uuid4().hex}"
    root.mkdir()
    return root

def sample_cases():
    rows=[]
    for split,count in (("development",4),("measured",12)):
        for i in range(count):
            rows.append({"case_id":f"{split}-{i}","split":split,"family":f"family-{i%4}","requirement":"Build an explicit offline workflow.","target_device":"desktop","task_type":"workflow","constraints":["Remain offline."]})
    return rows

class RuntimeControlTests(unittest.TestCase):
    def test_balanced_schedule_and_exact_caps(self):
        schedule=runtime._schedule(sample_cases())
        self.assertEqual(len(schedule),48)
        self.assertEqual(sum(x["call_cap"] for x in schedule if x["split"]=="development"),24)
        self.assertEqual(sum(x["call_cap"] for x in schedule if x["split"]=="measured"),72)
        for split,count in (("development",4),("measured",12)):
            rows=[x for x in schedule if x["split"]==split]
            self.assertEqual({x["arm"] for x in rows},{"A","B","C"})
            self.assertEqual(len({x["row_id"] for x in rows}),count*3)

    def test_write_once_is_atomic_claim_semantics(self):
        root=fresh_root("claim")
        try:
            path=root/"claim.json"; runtime.write_once(path,{"split":"development"}); runtime.write_once(path,{"split":"development"})
            with self.assertRaises(runtime.RuntimeErrorClosed): runtime.write_once(path,{"split":"measured"})
        finally: shutil.rmtree(root,ignore_errors=True)

    def test_measured_gate_binds_freeze_and_capabilities(self):
        root=fresh_root("gate")
        try:
            path=root/"receipt.json"; freeze={"input_identity":"sha256:input","observer_identity":"sha256:observer"}
            freeze_path=root/"freeze.json"; freeze_path.write_text("{}",encoding="utf-8"); freeze_sha=runtime.sha(freeze_path.read_bytes())
            manifest_path=root/"manifest.json"; manifest_path.write_text(json.dumps({"split":"development","freeze_sha256":freeze_sha,"started_calls":24}),encoding="utf-8")
            observation_path=root/"observation.json"; observation_path.write_text(json.dumps({"status":"observation_complete","split":"development","input_identity":"sha256:input","observer_identity":"sha256:observer","artifact_manifest_sha256":runtime.sha(manifest_path.read_bytes())}),encoding="utf-8")
            files=[{"path":file.name,"sha256":runtime.sha(file.read_bytes())} for file in (freeze_path,manifest_path,observation_path)]
            value={"schema_version":"req2web.phase7.e1_v2.observer.v4.development_gate.v1","status":"development_gate_passed","freeze_sha256":freeze_sha,"input_identity":"sha256:input","observer_identity":"sha256:observer","actual_rendering_capability":True,"usable_protocol_output":True,"observation_operable":True,"development_rows":12,"development_available_deliveries":8,"development_terminal_product_failures":4,"development_started_calls":24,"quality_pass_required":True,"quality_passed":True,"quality_policy":{"arm":"A","minimum_total_passed_obligations":20,"minimum_passed_obligations_per_case":4,"required_available_deliveries":4,"minimum_completed_model_deliveries":3,"unknown_rows_allowed":0},"quality_observed":{"total_passed_obligations":20,"passed_obligations_per_case":{"development-0":5,"development-1":5,"development-2":5,"development-3":5},"available_deliveries":4,"completed_model_deliveries":3,"unknown_rows":0},"freeze_path":"freeze.json","development_runtime_manifest_path":"manifest.json","observation_path":"observation.json","files":files}
            path.write_text(json.dumps(value),encoding="utf-8"); runtime._gate(path,freeze,freeze_sha)
            value["actual_rendering_capability"]=False; path.write_text(json.dumps(value),encoding="utf-8")
            with self.assertRaises(runtime.RuntimeErrorClosed): runtime._gate(path,freeze,freeze_sha)
        finally: shutil.rmtree(root,ignore_errors=True)

    def test_inventory_hashes_failed_partial_evidence_too(self):
        root=fresh_root("inventory")
        try:
            (root/"failed").mkdir(); (root/"failed/raw.bin").write_bytes(b"partial")
            self.assertEqual(runtime._inventory(root),[{"path":"failed/raw.bin","sha256":runtime.sha(b"partial")}])
        finally: shutil.rmtree(root,ignore_errors=True)

if __name__=="__main__": unittest.main()
