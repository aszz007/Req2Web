from __future__ import annotations

import json
import copy
from pathlib import Path
import tempfile
import unittest
import uuid

from req2web_orchestration.phase4_graph import (
    NODE_ORDER,
    phase4_create_portable_authority_state,
    phase4_register_node_output,
    phase4_synthetic_assembler_bindings,
    phase4_synthetic_fixture_output,
    synthetic_commerce_b_input,
)
from scripts.phase7_e1_v2_structured import RAW_SCHEMA, StructuredComparatorError, adapt_and_render, build_structured_prompt, canonical_bytes, parse_single_response

TEST_ROOT=Path(__file__).resolve().parents[1]/"outputs/phase7_e1_v2_runtime/test-tmp"
TEST_ROOT.mkdir(parents=True,exist_ok=True)

def fresh_root(prefix: str) -> Path:
    return TEST_ROOT/f"{prefix}-{uuid.uuid4().hex}"


class StructuredComparatorTests(unittest.TestCase):
    def fixture(self):
        b=synthetic_commerce_b_input(); state=phase4_create_portable_authority_state(b); payload={"schema_version":RAW_SCHEMA}
        for node in NODE_ORDER:
            out=phase4_synthetic_fixture_output(node,state)
            # The historical synthetic fixture predates the shared executable
            # capability registry. Keep this test-only input explicit rather
            # than weakening the owning validator.
            if node=="F1":
                replacements={"cart_panel":"data_view","button":"primary_action"}
                for component in out["components"]:
                    component["component_type"]=replacements.get(component["component_type"],component["component_type"])
            payload_output = copy.deepcopy(out)
            if node == "F4":
                stable_to_local = {
                    row["stable_id"]: row["local_id"]
                    for row in state["registry_inventory"]
                    if row["node_id"] == "F2"
                }
                for check in payload_output["acceptance_checks"]:
                    check["state_ref"] = {
                        "ref_type": "registry_local",
                        "ref_id": stable_to_local[check["state_ref"]["ref_id"]],
                        "ref_revision": "req2web.phase7.e1_v2.single_structured.v2.registry_local.v1",
                    }
            payload[node]=payload_output; state=phase4_register_node_output(state,node,out)
        context,guidance=phase4_synthetic_assembler_bindings(state)
        return b,payload,context,guidance

    def test_prompt_has_no_evaluator_material(self):
        text=build_structured_prompt(synthetic_commerce_b_input())
        self.assertIn("single response",text)
        self.assertNotIn("gold",text.casefold())
        self.assertNotIn("obligation",text.casefold())

    def test_parser_is_exact_and_duplicate_safe(self):
        _,payload,_,_=self.fixture()
        self.assertEqual(parse_single_response(canonical_bytes(payload))["schema_version"],RAW_SCHEMA)
        bad=canonical_bytes({**payload,"extra":1})
        with self.assertRaises(StructuredComparatorError): parse_single_response(bad)
        with self.assertRaises(StructuredComparatorError): parse_single_response(b'{"schema_version":"x","schema_version":"y"}')

    def test_owning_adapter_renders_without_adding_semantics(self):
        b,payload,context,guidance=self.fixture()
        tmp=fresh_root("structured-success")
        result=adapt_and_render(raw=json.dumps(payload,separators=(",",":")).encode(),b_input=b,context=context,guidance=guidance,output_root=tmp)
        self.assertEqual(result["status"],"structured_artifact_ready")
        self.assertFalse(result["adapter_semantics_added"])
        self.assertEqual(result["adapter_contract_constant_materialization_count"],0)
        self.assertEqual(result["adapter_contract_order_normalization_count"],0)
        self.assertTrue((tmp/"page/index.html").is_file())

    def test_fixed_contract_constant_can_be_materialized_without_semantic_repair(self):
        b,payload,context,guidance=self.fixture()
        del payload["F1"]["sections"][0]["refs"]
        tmp=fresh_root("structured-fixed-constant")
        result=adapt_and_render(raw=canonical_bytes(payload),b_input=b,context=context,guidance=guidance,output_root=tmp)
        self.assertEqual(result["status"],"structured_artifact_ready")
        self.assertFalse(result["adapter_semantics_added"])
        self.assertEqual(result["adapter_contract_constant_materialization_count"],1)
        self.assertGreaterEqual(result["adapter_contract_order_normalization_count"],1)

    def test_conflicting_contract_constant_fails_closed(self):
        b,payload,context,guidance=self.fixture()
        payload["F1"]["sections"][0]["refs"]=[{"unexpected":"semantic reference"}]
        with self.assertRaises(StructuredComparatorError):
            adapt_and_render(raw=canonical_bytes(payload),b_input=b,context=context,guidance=guidance,output_root=fresh_root("structured-constant-conflict"))

    def test_missing_model_semantics_fail_closed(self):
        b,payload,context,guidance=self.fixture(); payload["F3"]["interactions"]=[]
        with self.assertRaises(Exception): adapt_and_render(raw=canonical_bytes(payload),b_input=b,context=context,guidance=guidance,output_root=fresh_root("structured-failure"))

if __name__=="__main__": unittest.main()
