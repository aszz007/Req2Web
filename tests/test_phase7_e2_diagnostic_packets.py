"""Focused model-free tests for experimental packet projection and scoring."""
from copy import deepcopy
from pathlib import Path
import sys
import json
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import phase7_e2_diagnostic_packets as p


def example(version=2):
    return {
        "requirements.json":{"requirement":"Two independent form goals"},
        "specification.json":{"revision":version,"page_id":"page", "use_cases":[{"use_case_id":"u1"},{"use_case_id":"u2"}],
            "sections":[{"section_id":"s1","use_case_ids":["u1"]},{"section_id":"s2","use_case_ids":["u2"]}],
            "components":[{"component_id":"c1","section_id":"s1","label":"Submit"},{"component_id":"c2","section_id":"s2","label":"Review"}]},
        "handoff.json":{"revision":version,"page_id":"page", "links":[
            {"link_id":"l1","component_id":"c1","section_id":"s1","use_case_id":"u1"},
            {"link_id":"l2","component_id":"c2","section_id":"s2","use_case_id":"u2"}]},
        "render_bindings.json":{"revision":version,"page_id":"page","bindings":[
            {"element_id":"e1","component_id":"c1","label":"Submit"},{"element_id":"e2","component_id":"c2","label":"Review"}]},
    }


def answer(gold):
    return {"status":gold["status"],"origin_candidates":deepcopy(gold["origins"]),
            "affected_use_cases":gold["affected_use_cases"],
            "evidence_edges":[g[0] for g in gold["evidence_groups"]],"uncertainty":""}


class Packets(unittest.TestCase):
    def test_raw_answer_identity_and_summary_binding(self):
        _,gold=p.make_packet(example(),example(1),"wrong_relation")
        a=answer(gold)
        raw=p.canonical({"action":"final","answer":a})
        row={"session_id":"m001","answer":a,"raw_refs":[
            {"path":"m001/turn_01.raw","sha256":p.sha(raw),"bytes":len(raw)}]}
        root=Path(__file__).resolve().parents[1]/"outputs"/"raw_binding_mock_no_files"
        with patch.object(Path,"read_bytes",return_value=raw):
            p.verify_raw_answer(row,root)
            changed=deepcopy(row);changed["answer"]["status"]="unknown"
            with self.assertRaisesRegex(ValueError,"differs"):
                p.verify_raw_answer(changed,root)
            changed=deepcopy(row);changed["raw_refs"][0]["sha256"]="0"*64
            with self.assertRaisesRegex(ValueError,"identity"):
                p.verify_raw_answer(changed,root)
            for unsafe in ("", "../turn_01.raw", "m002/turn_01.raw"):
                changed=deepcopy(row);changed["raw_refs"][0]["path"]=unsafe
                with self.assertRaisesRegex(ValueError,"unsafe"):
                    p.verify_raw_answer(changed,root)

    def test_each_family_identifiable_from_shared_sources(self):
        for family in ("wrong_relation","stale_revision","two_defects","explicit_error","no_progress","clean"):
            with self.subTest(family=family):
                files,gold=p.make_packet(example(),example(1),family)
                p.check_index(files)
                p.check_index(json.loads(p.canonical(files)))
                result=p.score_answer(answer(gold),gold,files)
                self.assertTrue(result["complete_supported_diagnosis"])
                self.assertNotIn("gold",files)
                self.assertNotIn("family",files["trace_index.json"])

    def test_two_defects_partial_and_false_origin_not_complete(self):
        files,gold=p.make_packet(example(),example(1),"two_defects")
        a=answer(gold);a["origin_candidates"].pop()
        r=p.score_answer(a,gold,files)
        self.assertEqual(r["origin"]["recall"],.5)
        self.assertFalse(r["complete_supported_diagnosis"])
        a=answer(gold);a["origin_candidates"].append(p.origin("specification.json","c1","/components/0/label"))
        self.assertLess(p.score_answer(a,gold,files)["origin"]["precision"],1)

    def test_source_index_does_not_repair_wrong_link(self):
        files,gold=p.make_packet(example(),example(1),"wrong_relation")
        locs=files["trace_index.json"]["entities"]["u2"]
        self.assertTrue(any(x["artifact"]=="handoff.json" and x["pointer"]=="/links/0/use_case_id" for x in locs))
        files["trace_index.json"]["entities"]["u1"].append({"artifact":"handoff.json","pointer":"/links/0/use_case_id","sha256":"0"*64})
        with self.assertRaises(ValueError):p.check_index(files)

    def test_unrelated_or_unresolvable_evidence_not_complete(self):
        files,gold=p.make_packet(example(),example(1),"wrong_relation")
        a=answer(gold);a["evidence_edges"].append({"from":p.endpoint("runtime.json","/events/0"),"to":p.endpoint("versions.json","/active_revision")})
        self.assertFalse(p.score_answer(a,gold,files)["complete_supported_diagnosis"])
        a["evidence_edges"][-1]["from"]["pointer"]="/absent"
        self.assertFalse(p.score_answer(a,gold,files)["evidence"]["raw_endpoints_resolvable"])

    def test_actual_fixture_records_and_clean_repetition(self):
        clean=p.runtime_events("clean","u1")
        self.assertEqual(clean["events"][0]["after_state"],clean["events"][1]["after_state"])
        self.assertIsNone(clean["events"][1]["error"])
        self.assertEqual(clean["events"][2]["after_state"],1)
        bad=p.runtime_events("explicit_error","u1",True)
        self.assertEqual(bad["events"][1]["error"]["type"],"ValueError")
        stalled=p.runtime_events("no_progress","u1")
        self.assertEqual(len(stalled["events"]),5)
        self.assertTrue(all(x["after_state"]==0 for x in stalled["events"]))


if __name__ == "__main__":unittest.main()
