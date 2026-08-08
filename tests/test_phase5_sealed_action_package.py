from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.phase5_sealed_action_package import (  # noqa: E402
    Phase5SealedActionPackage,
    build_phase5_node_static_projection,
    create_phase5_sealed_action_package,
    load_phase5_sealed_action_package_file,
    write_phase5_sealed_action_package,
)


FIXTURE = ROOT / "fixtures" / "phase5_sealed_action_package_synthetic_v1.json"


class Phase5SealedActionPackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.package = create_phase5_sealed_action_package(self.fixture)
        self.payload = self.package.to_dict()

    def test_identity_and_budget_are_frozen(self) -> None:
        self.assertEqual(
            self.package.sha256(),
            "9ab19a2ce8df7bbdf6f2be022d056eab40274859eae6e153e9e9c54f8b68945a",
        )
        self.assertEqual(
            self.payload["package_id"],
            "phase5-sealed-action-package-"
            "567c7d1925399005613153ae44cbc1412c76b71e7af7871645d5f9ad19acec47",
        )
        budget = self.payload["budget"]
        self.assertEqual(budget["independent_case_count"], 1)
        self.assertEqual(budget["runtime_row_count"], 3)
        self.assertEqual(budget["node_generate_call_cap"], 12)
        self.assertEqual(budget["actual_generate_started_count"], 0)
        self.assertEqual(budget["automatic_retry_count"], 0)
        self.assertEqual(budget["budget_reset_count"], 0)

    def test_synthetic_package_has_no_action_or_gold_flags(self) -> None:
        self.assertEqual(
            self.payload["package_kind"],
            "synthetic_validation_only",
        )
        self.assertIsNone(
            self.payload["authority_bindings"]["final_action_receipt_sha256"]
        )
        for key, value in self.payload["visibility"].items():
            self.assertFalse(value, key)
        for key, value in self.payload["action_state"].items():
            self.assertFalse(value, key)

    def test_node_static_projection_uses_exact_role_allowlists(self) -> None:
        expected_counts = {"F1": 2, "F2": 2, "F3": 1, "F4": 1}
        expected_hashes = {
            "F1": "15314e7946b4aa113bd3923127af92f399f3639cc115038c16a6e79069691589",
            "F2": "9ac7a9c3b2df366ac15646d7364d24be812cdafc4777ca43ae70509a3167e6ed",
            "F3": "b04dc5e0a336436f73985675521ec00b610d442d1844455819bb0463d477e547",
            "F4": "5e82cfca0a36548b811ec0842afc7289dec0ab42203007bb7df693a63ed23afb",
        }
        for node_id, count in expected_counts.items():
            projection = build_phase5_node_static_projection(
                self.package,
                matrix_row_id="synthetic-matrix-row-none-001",
                node_id=node_id,
            )
            self.assertEqual(
                len(projection["provider_payload"]["licensed_evidence"]),
                count,
            )
            self.assertEqual(
                projection["provider_payload_sha256"],
                expected_hashes[node_id],
            )

    def test_interventions_change_only_evidence_selection(self) -> None:
        removed = build_phase5_node_static_projection(
            self.package,
            matrix_row_id="synthetic-matrix-row-remove-flow-001",
            node_id="F3",
        )
        irrelevant = build_phase5_node_static_projection(
            self.package,
            matrix_row_id="synthetic-matrix-row-irrelevant-flow-001",
            node_id="F3",
        )
        self.assertEqual(removed["provider_payload"]["licensed_evidence"], [])
        self.assertEqual(
            [
                item["adoption_or_intervention_signal"]
                for item in irrelevant["provider_payload"]["licensed_evidence"]
            ],
            ["critical", "irrelevant"],
        )
        for projection in (removed, irrelevant):
            encoded = json.dumps(
                projection["provider_payload"],
                ensure_ascii=False,
                sort_keys=True,
            )
            self.assertNotIn("matrix_row_id", encoded)
            self.assertNotIn("remove_critical_role", encoded)
            self.assertNotIn("irrelevant_evidence", encoded)
            self.assertFalse(projection["intervention_name_visible"])
            self.assertFalse(projection["core_or_reserve_identity_visible"])

    def test_provider_refs_are_blind_and_do_not_retain_local_ids(self) -> None:
        row = self.payload["runtime_rows"][0]
        projection = build_phase5_node_static_projection(
            self.package,
            matrix_row_id=row["matrix_row_id"],
            node_id="F1",
        )
        provider = projection["provider_payload"]
        self.assertTrue(provider["provider_case_ref"].startswith("phase5-provider-case-"))
        self.assertTrue(
            provider["provider_request_ref"].startswith("phase5-provider-request-")
        )
        encoded = json.dumps(provider, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(row["runtime_case_id"], encoded)
        self.assertNotIn(row["b_input"]["request_id"], encoded)
        self.assertNotIn(row["opaque_case_ref"], encoded)

    def test_prohibited_gold_or_path_content_fails_closed(self) -> None:
        tampered = json.loads(json.dumps(self.fixture))
        tampered["runtime_rows"][0]["b_input"]["gold"] = {"answer": "hidden"}
        with self.assertRaisesRegex(ValueError, "invalid keys"):
            create_phase5_sealed_action_package(tampered)

        tampered = json.loads(json.dumps(self.fixture))
        tampered["runtime_rows"][0]["evidence_items"][0][
            "project_authored_nonverbatim_short_summary"
        ] = "https://example.invalid/reference"
        with self.assertRaisesRegex(ValueError, "URI or absolute path"):
            create_phase5_sealed_action_package(tampered)

    def test_formal_package_requires_final_receipt_and_true_preaction_flags(self) -> None:
        tampered = json.loads(json.dumps(self.fixture))
        tampered["package_kind"] = "owner_sealed_formal_h1"
        with self.assertRaisesRegex(ValueError, "requires a final receipt"):
            create_phase5_sealed_action_package(tampered)

        tampered["authority_bindings"]["final_action_receipt_sha256"] = "9" * 64
        with self.assertRaisesRegex(ValueError, "must be true"):
            create_phase5_sealed_action_package(tampered)

    def test_round_trip_loader_confirmation_and_writer_exact_bytes(self) -> None:
        self.assertEqual(
            Phase5SealedActionPackage.from_json_bytes(
                self.package.canonical_json_bytes()
            ),
            self.package,
        )
        path = ROOT / "synthetic-sealed-action-package.json"
        with patch.object(Path, "is_file", return_value=True), patch.object(
            Path,
            "read_bytes",
            return_value=self.package.canonical_json_bytes(),
        ):
            with self.assertRaisesRegex(ValueError, "confirmation"):
                load_phase5_sealed_action_package_file(
                    path,
                    owner_only_local_confirmed=False,
                )
            self.assertEqual(
                load_phase5_sealed_action_package_file(
                    path,
                    owner_only_local_confirmed=True,
                ),
                self.package,
            )

        output = (ROOT / "synthetic-sealed-action-output.json").resolve()
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.__exit__.return_value = False
        stream.fileno.return_value = 77
        with patch("os.open", return_value=12), patch(
            "os.fdopen",
            return_value=stream,
        ), patch("os.fsync") as fsync_mock:
            write_phase5_sealed_action_package(output, self.package)
        stream.write.assert_called_once_with(self.package.canonical_json_bytes())
        fsync_mock.assert_called_once_with(77)

    def test_cli_rejects_repository_paths(self) -> None:
        script_path = ROOT / "scripts" / "run_phase5_sealed_action_package.py"
        spec = importlib.util.spec_from_file_location(
            "phase5_sealed_action_package_cli_test",
            script_path,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaisesRegex(SystemExit, "outside the repository"):
            module._external(
                ROOT / "owner-sealed-action.json",
                strict=False,
                name="owner action test path",
            )


if __name__ == "__main__":
    unittest.main()
