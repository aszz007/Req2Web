from __future__ import annotations

from copy import deepcopy
import inspect
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime.autodl_a3_finalizer as finalizer
from tests.test_m3_autodl_a3_remote_closeout import make_fixture, reidentify


class A3FinalizerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = make_fixture()

    def tearDown(self):
        self.fixture["owner"].tearDown()

    def invoke(self, now="2026-07-25T01:00:00Z"):
        return finalizer._finalize_a3_closeout_for_tests(
            self.fixture["plan"].canonical_bytes(),
            self.fixture["remote_envelope"].canonical_bytes(),
            self.fixture["event_bytes"],
            self.fixture["foundation_bundle"].canonical_bytes(),
            self.fixture["foundation_receipt"].canonical_bytes(),
            now,
        )

    def test_public_bytes_and_factories_only_produce_non_authorizing_structural_replay(self):
        result = self.invoke()
        self.assertEqual(result.report.data["status"], "a3_structural_replay_complete")
        self.assertFalse(result.report.data["manager_consumable"])
        self.assertFalse(result.report.data["cleanup_complete"])
        self.assertFalse(result.report.data["next_run_allowed"])
        self.assertTrue(result.report.data["authorization_invalidated"])
        self.assertFalse(result.report.data["physical_erasure_claimed"])
        self.assertFalse(hasattr(result, "decision"))
        self.assertNotIn("ValidatedA3ManagerCloseoutDecision", finalizer.__all__)
        public = finalizer.finalize_a3_closeout(
            self.fixture["plan"].canonical_bytes(), self.fixture["remote_envelope"].canonical_bytes(),
            self.fixture["event_bytes"], self.fixture["foundation_bundle"].canonical_bytes(),
            self.fixture["foundation_receipt"].canonical_bytes(),
        )
        self.assertFalse(public.report.data["cleanup_complete"])
        self.assertFalse(hasattr(public, "decision"))
        unregistered = object.__new__(finalizer.A3FinalizerResult)
        with self.assertRaisesRegex(finalizer.A3FinalizerError, "not_registered"):
            _ = unregistered.report

    def test_late_or_incomplete_evidence_is_structural_incomplete_and_never_allows_next_run(self):
        result = self.invoke("2026-07-25T02:00:01Z")
        self.assertEqual(result.report.data["status"], "a3_structural_replay_incomplete")
        self.assertEqual(result.receipt.data["status"], "a3_structural_replay_incomplete")
        self.assertFalse(result.receipt.data["cleanup_complete"])
        self.assertFalse(result.receipt.data["next_run_allowed"])
        self.assertTrue(result.receipt.data["authorization_invalidated"])

    def test_direct_constructor_canonical_bytes_mutation_and_cross_bundle_replay_remain_non_authorizing(self):
        result = self.invoke()
        report = deepcopy(result.report.to_dict())
        report["manager_consumable"] = True
        reidentify(report, "report_id", "autodl-a3-finalizer-report-")
        with self.assertRaisesRegex(finalizer.A3FinalizerError, "authority_invalid"):
            finalizer.A3FinalizerReport.from_dict(report)
        returned = result.report.data
        returned["status"] = "a3_structural_replay_incomplete"
        self.assertEqual(result.report.data["status"], "a3_structural_replay_complete")
        envelope = finalizer.finalizer_envelope_from_result(result)
        report_data, receipt_data = finalizer.validate_a3_finalizer_envelope_bytes(envelope)
        self.assertEqual(report_data.sha256(), result.report.sha256())
        bad_receipt = deepcopy(receipt_data.to_dict())
        bad_receipt["report_sha256"] = "f" * 64
        reidentify(bad_receipt, "receipt_id", "autodl-a3-finalizer-receipt-")
        bad_envelope = {"schema_version": "req2web.runtime.a3_finalizer_envelope.v1", "report": report_data.to_dict(), "receipt": bad_receipt}
        bad_bytes = json.dumps(bad_envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        with self.assertRaisesRegex(finalizer.A3FinalizerError, "binding_invalid"):
            finalizer.validate_a3_finalizer_envelope_bytes(bad_bytes)

    def test_exact_type_subclass_and_module_rebinding_cannot_create_authority(self):
        result = self.invoke()
        class EqualResult(finalizer.A3FinalizerResult):
            def __hash__(self):
                return hash(result)
            def __eq__(self, other):
                return other is result
        collision = object.__new__(EqualResult)
        self.assertEqual(collision, result)
        with self.assertRaisesRegex(TypeError, "a3_finalizer_result"):
            _ = collision.report
        import req2web_runtime.autodl_a3_finalizer as module
        with unittest.mock.patch.multiple(
            module,
            type=lambda _: (_ for _ in ()).throw(AssertionError("type")),
            len=lambda _: (_ for _ in ()).throw(AssertionError("len")),
            any=lambda _: (_ for _ in ()).throw(AssertionError("any")),
        ):
            self.assertEqual(result.report.canonical_bytes(), result.report.canonical_bytes())
            self.assertFalse(result.receipt.data["cleanup_complete"])

    def test_envelope_roundtrip_and_public_shape(self):
        result = self.invoke()
        envelope = finalizer.finalizer_envelope_from_result(result)
        report, receipt = finalizer.validate_a3_finalizer_envelope_bytes(envelope)
        self.assertEqual(report.sha256(), result.report.sha256())
        self.assertEqual(receipt.sha256(), result.receipt.sha256())
        parameters = tuple(inspect.signature(finalizer.finalize_a3_closeout).parameters)
        self.assertEqual(parameters, ("plan_bytes", "remote_envelope_bytes", "remote_event_bytes", "foundation_bundle_bytes", "foundation_receipt_bytes"))
        self.assertNotIn("clock", inspect.getsource(finalizer.finalize_a3_closeout))
        self.assertNotIn("ValidatedA3ManagerCloseoutDecision", inspect.getsource(finalizer.finalize_a3_closeout))


if __name__ == "__main__":
    unittest.main()
