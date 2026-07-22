from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
TEST_ROOT = ROOT / "outputs" / "_m3_model_run_bundle_tests"

from req2web_generation import RetrievalGuidanceBuilder  # noqa: E402
from req2web_provider import ModelCandidateAuditAdapter  # noqa: E402
from req2web_evaluation.model_run_bundle import (  # noqa: E402
    BundleInventoryEntry,
    ModelRunBundleError,
    ModelRunBundleWriter,
    validate_model_run_bundle,
)
from tests.test_m3_candidate_audit import (  # noqa: E402
    MOCK_SERIALIZER_CONFIG_BYTES,
    SYNTHETIC_INPUT_BYTES,
    build_assembled,
    build_context,
    build_receipt,
    edge,
)


POSITIVE_EDGES = [
    edge("component-search", "requirement", "req-search"),
    edge("component-results", "evidence", "evidence-ui-search"),
    edge("constraint-feedback", "policy", "policy-layout"),
]


def canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_live_artifacts(
    *,
    synthetic_input_bytes: bytes = SYNTHETIC_INPUT_BYTES,
    mock_serializer_config_bytes: bytes = MOCK_SERIALIZER_CONFIG_BYTES,
):
    context = build_context()
    guidance = RetrievalGuidanceBuilder().build(context)
    assembled = build_assembled(POSITIVE_EDGES)
    receipt = build_receipt(
        synthetic_input_bytes=synthetic_input_bytes,
        mock_serializer_config_bytes=mock_serializer_config_bytes,
    )
    audit = ModelCandidateAuditAdapter().build(
        assembled,
        receipt,
        synthetic_input_bytes=synthetic_input_bytes,
        mock_serializer_config_bytes=mock_serializer_config_bytes,
    )
    return context, guidance, assembled, receipt, audit


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def load_manifest(bundle_directory: Path) -> dict[str, object]:
    return json.loads((bundle_directory / "bundle_manifest.json").read_text(encoding="utf-8"))


def save_resigned_manifest(bundle_directory: Path, payload: dict[str, object]) -> None:
    root = {key: value for key, value in payload.items() if key != "bundle_id"}
    payload["bundle_id"] = "synthetic-mock-bundle-" + sha256_bytes(canonical_json_bytes(root))
    (bundle_directory / "bundle_manifest.json").write_bytes(canonical_json_bytes(payload))


def resign_json_artifact(bundle_directory: Path, relative_path: str, mutate) -> None:
    artifact_path = bundle_directory.joinpath(*Path(relative_path).parts)
    artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    mutate(artifact_payload)
    artifact_bytes = canonical_json_bytes(artifact_payload)
    artifact_path.write_bytes(artifact_bytes)
    manifest = load_manifest(bundle_directory)
    inventory = manifest["artifact_inventory"]
    assert isinstance(inventory, list)
    matching = [item for item in inventory if item["relative_path"] == relative_path]
    assert len(matching) == 1
    entry = matching[0]
    entry["sha256"] = sha256_bytes(artifact_bytes)
    entry["byte_length"] = len(artifact_bytes)
    roots = manifest["artifact_roots"]
    assert isinstance(roots, dict)
    roots[entry["artifact_kind"]]["sha256"] = entry["sha256"]
    save_resigned_manifest(bundle_directory, manifest)


class ModelRunBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.writer = ModelRunBundleWriter()
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.work = TEST_ROOT / f"case-{uuid4().hex}"
        self.work.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def write_bundle(self, output_directory: Path, *, synthetic_input_bytes: bytes = SYNTHETIC_INPUT_BYTES, mock_serializer_config_bytes: bytes = MOCK_SERIALIZER_CONFIG_BYTES):
        context, guidance, assembled, receipt, audit = build_live_artifacts(
            synthetic_input_bytes=synthetic_input_bytes,
            mock_serializer_config_bytes=mock_serializer_config_bytes,
        )
        return self.writer.write(
            output_directory,
            context=context,
            guidance=guidance,
            assembled_page_spec=assembled,
            visibility_receipt=receipt,
            synthetic_input_bytes=synthetic_input_bytes,
            mock_serializer_config_bytes=mock_serializer_config_bytes,
            candidate_audit_record=audit,
        )

    def assert_bundle_error(self, callback, code: str) -> None:
        with self.assertRaises(ModelRunBundleError) as raised:
            callback()
        self.assertEqual((raised.exception.code, raised.exception.stage, raised.exception.phase), (code, "package", "evaluation_bundle"))

    def test_happy_path_reloads_complete_provenance_closed_metadata_and_explicit_downstream_status(self) -> None:
        written = self.write_bundle(self.work / "bundle")
        manifest = validate_model_run_bundle(written.bundle_directory)
        self.assertEqual(manifest.bundle_id, written.manifest.bundle_id)
        self.assertEqual(tuple(item.relative_path for item in manifest.artifact_inventory), (
            "artifacts/agent_context.json", "artifacts/retrieval_guidance.json", "artifacts/provider_raw_response.bin",
            "artifacts/model_semantic_candidate.json", "artifacts/page_spec_assembly_report.json",
            "artifacts/assembled_page_spec.json", "artifacts/synthetic_mock_visibility_receipt.json",
            "artifacts/model_candidate_audit_record.json", "inputs/synthetic_input.bin", "inputs/mock_serializer_config.bin",
        ))
        self.assertEqual(manifest.assembly["local_identity_scaffold"], "provenance_only_not_model_attribution")
        self.assertTrue(manifest.candidate_audit["semantic_correctness_not_evaluated"])
        self.assertEqual(manifest.closed_run_metadata, {
            "group": "synthetic_mock_not_experiment", "seed": None, "start_time": None, "end_time": None,
            "duration_ms": None, "unexpected_authoritative_field_findings": [],
        })
        self.assertEqual(manifest.downstream_status, {
            "gate": {"status": "not_executed", "report_sha256": None},
            "acceptance": {"status": "not_executed", "report_sha256": None},
            "repair": {"status": "not_applicable_no_gate_result", "delta_sha256": None},
            "package_route": {"status": "not_executed", "route": None, "package_id": None, "package_sha256": None},
            "fallback": {"status": "not_applicable_no_failure", "source_package_id": None, "source_package_sha256": None},
            "final_package": {"status": "not_executed", "package_id": None, "package_sha256": None},
        })
        payload = load_manifest(written.bundle_directory)
        self.assertTrue(payload["synthetic_mock_only"])
        self.assertFalse(payload["external_egress_allowed"])
        self.assertEqual(payload["provider_status"], "not_selected_or_executed")
        self.assertEqual(payload["model_status"], "not_selected_or_executed")

    def test_same_inputs_create_byte_identical_contents_under_distinct_roots(self) -> None:
        first = self.write_bundle(self.work / "first")
        second = self.write_bundle(self.work / "second")
        self.assertEqual(first.manifest.bundle_id, second.manifest.bundle_id)
        self.assertEqual(tree_bytes(first.bundle_directory), tree_bytes(second.bundle_directory))

    def test_live_context_guidance_receipt_audit_and_storage_eligibility_fail_closed(self) -> None:
        context, guidance, assembled, receipt, audit = build_live_artifacts()
        self.assert_bundle_error(
            lambda: self.writer.write(self.work / "bad-context", context=replace(context, task_type="other"), guidance=guidance,
                assembled_page_spec=assembled, visibility_receipt=receipt, synthetic_input_bytes=SYNTHETIC_INPUT_BYTES,
                mock_serializer_config_bytes=MOCK_SERIALIZER_CONFIG_BYTES, candidate_audit_record=audit),
            "guidance_context_binding_invalid",
        )
        self.assert_bundle_error(
            lambda: self.writer.write(self.work / "bad-guidance", context=context, guidance=replace(guidance, task_type="other"),
                assembled_page_spec=assembled, visibility_receipt=receipt, synthetic_input_bytes=SYNTHETIC_INPUT_BYTES,
                mock_serializer_config_bytes=MOCK_SERIALIZER_CONFIG_BYTES, candidate_audit_record=audit),
            "guidance_context_binding_invalid",
        )
        self.assert_bundle_error(
            lambda: self.writer.write(self.work / "bad-input", context=context, guidance=guidance, assembled_page_spec=assembled,
                visibility_receipt=receipt, synthetic_input_bytes=b'{"synthetic":"wrong"}',
                mock_serializer_config_bytes=MOCK_SERIALIZER_CONFIG_BYTES, candidate_audit_record=audit),
            "synthetic_input_receipt_binding_mismatch",
        )
        self.assert_bundle_error(
            lambda: self.writer.write(self.work / "bad-config", context=context, guidance=guidance, assembled_page_spec=assembled,
                visibility_receipt=receipt, synthetic_input_bytes=SYNTHETIC_INPUT_BYTES,
                mock_serializer_config_bytes=b'{"mock_serializer":"wrong"}', candidate_audit_record=audit),
            "mock_serializer_config_receipt_binding_mismatch",
        )
        self.assert_bundle_error(
            lambda: self.writer.write(self.work / "bad-receipt", context=context, guidance=guidance, assembled_page_spec=assembled,
                visibility_receipt=replace(receipt, external_egress_allowed=True), synthetic_input_bytes=SYNTHETIC_INPUT_BYTES,
                mock_serializer_config_bytes=MOCK_SERIALIZER_CONFIG_BYTES, candidate_audit_record=audit),
            "visibility_receipt_invalid",
        )
        self.assert_bundle_error(
            lambda: self.writer.write(self.work / "bad-audit", context=context, guidance=guidance, assembled_page_spec=assembled,
                visibility_receipt=receipt, synthetic_input_bytes=SYNTHETIC_INPUT_BYTES,
                mock_serializer_config_bytes=MOCK_SERIALIZER_CONFIG_BYTES,
                candidate_audit_record=replace(audit, raw_response_sha256="0" * 64)),
            "candidate_audit_invalid",
        )
        self.assert_bundle_error(
            lambda: self.writer.write(self.work / "bad-report", context=context, guidance=guidance,
                assembled_page_spec=replace(assembled, report=replace(assembled.report, context_sha256="0" * 64)),
                visibility_receipt=receipt, synthetic_input_bytes=SYNTHETIC_INPUT_BYTES,
                mock_serializer_config_bytes=MOCK_SERIALIZER_CONFIG_BYTES, candidate_audit_record=audit),
            "live_assembly_invalid",
        )
        sensitive_input = canonical_json_bytes({"nested": {"Authorization": "synthetic-only-but-disallowed"}})
        self.assert_bundle_error(lambda: self.write_bundle(self.work / "sensitive", synthetic_input_bytes=sensitive_input), "bundle_storage_sensitive_data")
        absolute_config = canonical_json_bytes({"fixture_path": "C:\\absolute\\fixture.json"})
        self.assert_bundle_error(lambda: self.write_bundle(self.work / "absolute", mock_serializer_config_bytes=absolute_config), "bundle_storage_sensitive_data")
        noncanonical_input = b'{"synthetic": "whitespace is noncanonical"}'
        self.assert_bundle_error(lambda: self.write_bundle(self.work / "noncanonical-storage", synthetic_input_bytes=noncanonical_input), "bundle_storage_json_invalid")

    def test_resigned_manifest_rejects_inventory_semantics_and_strict_scalars(self) -> None:
        variations = (
            ("artifact_kind", "incorrect_kind"),
            ("schema_version", "incorrect.schema.v1"),
            ("input_visibility", "provider_output_only"),
        )
        for index, (field, value) in enumerate(variations):
            with self.subTest(field=field):
                bundle = self.write_bundle(self.work / f"inventory-{index}").bundle_directory
                payload = load_manifest(bundle)
                payload["artifact_inventory"][0][field] = value
                save_resigned_manifest(bundle, payload)
                self.assert_bundle_error(lambda: validate_model_run_bundle(bundle), "bundle_manifest_invalid")
        for index, (field, value) in enumerate((("synthetic_mock_only", 1), ("external_egress_allowed", 0))):
            with self.subTest(field=field):
                bundle = self.write_bundle(self.work / f"bool-{index}").bundle_directory
                payload = load_manifest(bundle)
                payload[field] = value
                save_resigned_manifest(bundle, payload)
                self.assert_bundle_error(lambda: validate_model_run_bundle(bundle), "bundle_manifest_invalid")
        bundle = self.write_bundle(self.work / "audit-bool").bundle_directory
        payload = load_manifest(bundle)
        payload["candidate_audit"]["accepted_count"] = True
        save_resigned_manifest(bundle, payload)
        self.assert_bundle_error(lambda: validate_model_run_bundle(bundle), "bundle_manifest_invalid")

    def test_resigned_unknown_json_fields_fail_exact_round_trip_for_context_report_receipt_and_audit(self) -> None:
        cases = (
            ("artifacts/agent_context.json", "live_context_invalid"),
            ("artifacts/page_spec_assembly_report.json", "bundle_provenance_invalid"),
            ("artifacts/synthetic_mock_visibility_receipt.json", "visibility_receipt_invalid"),
            ("artifacts/model_candidate_audit_record.json", "candidate_audit_invalid"),
        )
        for index, (relative_path, code) in enumerate(cases):
            with self.subTest(relative_path=relative_path):
                bundle = self.write_bundle(self.work / f"unknown-{index}").bundle_directory
                resign_json_artifact(bundle, relative_path, lambda payload: payload.update({"unexpected_top_level": "rejected"}))
                self.assert_bundle_error(lambda: validate_model_run_bundle(bundle), code)

    def test_resigned_invalid_or_unknown_pagespec_fails_semantic_reconstruction(self) -> None:
        for index, mutate in enumerate((
            lambda payload: payload.update({"unexpected_top_level": "rejected"}),
            lambda payload: payload.update({"sections": []}),
        )):
            with self.subTest(index=index):
                bundle = self.write_bundle(self.work / f"page-spec-{index}").bundle_directory
                resign_json_artifact(bundle, "artifacts/assembled_page_spec.json", mutate)
                self.assert_bundle_error(lambda: validate_model_run_bundle(bundle), "bundle_page_spec_invalid")

    def test_disk_tamper_missing_unexpected_and_noncanonical_manifest_fail_closed(self) -> None:
        written = self.write_bundle(self.work / "original")
        tampered = self.work / "tampered"
        shutil.copytree(written.bundle_directory, tampered)
        candidate_path = tampered / "artifacts" / "model_semantic_candidate.json"
        candidate_path.write_bytes(candidate_path.read_bytes() + b"\n")
        self.assert_bundle_error(lambda: validate_model_run_bundle(tampered), "bundle_file_integrity_invalid")

        missing = self.work / "missing"
        shutil.copytree(written.bundle_directory, missing)
        (missing / "inputs" / "synthetic_input.bin").unlink()
        self.assert_bundle_error(lambda: validate_model_run_bundle(missing), "bundle_file_missing")

        unexpected = self.work / "unexpected"
        shutil.copytree(written.bundle_directory, unexpected)
        (unexpected / "unexpected.txt").write_text("x", encoding="utf-8")
        self.assert_bundle_error(lambda: validate_model_run_bundle(unexpected), "bundle_file_unexpected")

        noncanonical = self.work / "noncanonical"
        shutil.copytree(written.bundle_directory, noncanonical)
        manifest_path = noncanonical / "bundle_manifest.json"
        manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
        self.assert_bundle_error(lambda: validate_model_run_bundle(noncanonical), "bundle_json_noncanonical")

    def test_existing_destination_path_escape_and_symlink_guard_fail_closed_without_temporary_directory(self) -> None:
        destination = self.work / "existing"
        destination.mkdir()
        self.assert_bundle_error(lambda: self.write_bundle(destination), "output_destination_exists")
        with self.assertRaises(ValueError):
            BundleInventoryEntry("../escape", "x", "v1", "0" * 64, 1, "local_only").validate()
        written = self.write_bundle(self.work / "bundle")
        with patch("req2web_evaluation.model_run_bundle.Path.is_symlink", return_value=True):
            self.assert_bundle_error(lambda: validate_model_run_bundle(written.bundle_directory), "bundle_directory_invalid")

    def test_source_is_utf8_without_bom_and_has_no_evaluator_or_gold_reverse_import(self) -> None:
        source_path = ROOT / "src" / "req2web_evaluation" / "model_run_bundle.py"
        source = source_path.read_text(encoding="utf-8")
        self.assertFalse(source_path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertNotIn("GoldObligation", source)
        self.assertNotIn("req2web_inspector", source)
        self.assertNotIn("semantic verdict", source)


if __name__ == "__main__":
    unittest.main()
