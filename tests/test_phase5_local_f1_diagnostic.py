from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_agent.prompt_authority import (  # noqa: E402
    PROMPT_AUTHORITY_REVISION,
)
from req2web_orchestration.phase4_graph import (  # noqa: E402
    phase4_create_portable_authority_state,
    phase4_synthetic_fixture_output,
    synthetic_commerce_b_input,
)
from req2web_runtime import phase4_local_qwen as local_runtime  # noqa: E402
from req2web_runtime.phase4_local_qwen import LocalQwenProfile  # noqa: E402
from req2web_runtime.phase5_local_f1_diagnostic import (  # noqa: E402
    CORE2_EXPECTATIONS,
    PROMPT_AUTHORITY_SHA256,
    Phase5LocalF1DiagnosticError,
    PreparedDiagnostic,
    SourceExpectation,
    SourceMaterial,
    _diagnostic_artifacts,
    _validate_raw_f1,
    load_core2_none_source_material,
    run_phase5_local_f1_diagnostic,
)


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def raw_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def raw_identity(raw: bytes, revision: str) -> dict[str, object]:
    return {
        "identity_kind": "raw_bytes",
        "sha256": f"sha256:{sha256(raw).hexdigest()}",
        "byte_length": len(raw),
        "revision": revision,
    }


class FakeBackend:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.calls = 0
        self.generation_started = False
        self.last_generation_facts = None
        self.stderr_bytes = b"worker stderr"
        self.loaded_facts = {"loaded": True}
        self.fresh_runtime_facts = {
            "schema_version": "test.fresh_runtime_facts",
            "input_truncation": False,
            "output_truncation": False,
        }

    def generate_fresh_integrated(self, **kwargs: object) -> bytes:
        self.calls += 1
        self.generation_started = True
        self.last_generation_facts = {
            "schema_version": "req2web.phase4.p4_03.fresh_integrated.runtime_facts.v1",
            "input_truncation": False,
            "output_truncation": False,
            "complete_single_json_required": True,
        }
        emit = kwargs["emit_delta"]
        emit(b"{")  # type: ignore[operator]
        return self.raw

    def close(self) -> dict[str, object]:
        return {
            "worker_exit_verified": True,
            "terminal_status": "normal_completed",
        }


class Phase5LocalF1DiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.b_input = synthetic_commerce_b_input(
            case_id="case-core2",
            request_id="request-core2",
        )
        self.input_bytes = canonical(
            {
                "schema_version": (
                    "req2web.phase4.p4_05.remote_fresh_integrated.input.v1"
                ),
                "node_id": "F1",
                "case_id": "case-core2",
                "request_id": "request-core2",
                "projection": {"requirement": "Build an English page."},
            }
        )

    def _test_root(self) -> Path:
        root = ROOT / f".phase5-local-f1-test-{uuid.uuid4().hex}"
        root.mkdir()
        self.addCleanup(shutil.rmtree, root, True)
        return root.resolve()

    def _source_return(
        self,
        root: Path,
    ) -> tuple[Path, Path, SourceExpectation]:
        row_id = "row-core2-none"
        row_root = f"rows/09-{row_id}"
        upstream_id = "sha256:" + "1" * 64
        projection_id = "sha256:" + "2" * 64
        members = {
            f"{row_root}/attempts/F1/input.json": self.input_bytes,
            f"{row_root}/attempts/F1/pre_call_record.json": canonical(
                {
                    "input_identity": raw_identity(
                        self.input_bytes,
                        (
                            "req2web.phase4.p4_05.remote_fresh_integrated."
                            "input.v1"
                        ),
                    )
                }
            ),
            f"{row_root}/b_input.json": canonical(self.b_input),
            "row-summaries/09.json": canonical(
                {
                    "execution_index": 9,
                    "row_id": row_id,
                    "case_ref": "case-core2",
                    "condition_id": "none",
                    "shared_case_upstream_identity": {"sha256": upstream_id},
                    "provider_visible_projection_identity": {
                        "sha256": projection_id
                    },
                }
            ),
            "projection-receipts/09.json": canonical(
                {
                    "row_id": row_id,
                    "condition_id": "none",
                    "provider_visible_projection_identity": {
                        "sha256": projection_id
                    },
                }
            ),
            "baseline/02/upstream/upstream_receipt.json": canonical(
                {"receipt_identity": {"sha256": upstream_id}}
            ),
        }
        tar_path = root / "source.tar"
        with tarfile.open(tar_path, "w:") as archive:
            for name, raw in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                info.mtime = 0
                archive.addfile(info, io.BytesIO(raw))
        manifest_path = root / "manifest.json"
        manifest_path.write_bytes(b"{}")
        expectation = SourceExpectation(
            tar_sha256=sha256(tar_path.read_bytes()).hexdigest(),
            manifest_sha256=sha256(b"{}").hexdigest(),
            run_id="source-run",
            case_id="case-core2",
            request_id="request-core2",
            condition_id="none",
            execution_index=9,
            row_id=row_id,
            row_root=row_root,
            input_sha256=sha256(self.input_bytes).hexdigest(),
            input_byte_length=len(self.input_bytes),
            upstream_identity=upstream_id,
            projection_identity=projection_id,
        )
        return tar_path, manifest_path, expectation

    def test_exact_preserved_source_bytes_are_loaded_without_extraction(self) -> None:
        tar_path, manifest_path, expectation = self._source_return(
            self._test_root()
        )
        with patch(
            "req2web_runtime.phase5_local_f1_diagnostic."
            "validate_phase5_result_return"
        ) as validator:
            material = load_core2_none_source_material(
                tar_path=tar_path,
                manifest_path=manifest_path,
                expectation=expectation,
            )
        validator.assert_called_once_with(
            tar_path=tar_path,
            manifest_path=manifest_path,
        )
        self.assertEqual(material.input_bytes, self.input_bytes)
        self.assertEqual(
            material.source_binding["input_provenance"],
            "exact_preserved_row_member_bytes",
        )
        self.assertFalse(material.source_binding["source_result_mutated"])

    def test_source_input_identity_drift_fails_before_action(self) -> None:
        tar_path, manifest_path, expectation = self._source_return(
            self._test_root()
        )
        expectation = replace(expectation, input_sha256="0" * 64)
        with patch(
            "req2web_runtime.phase5_local_f1_diagnostic."
            "validate_phase5_result_return"
        ):
            with self.assertRaisesRegex(
                Phase5LocalF1DiagnosticError,
                "input bytes drifted",
            ):
                load_core2_none_source_material(
                    tar_path=tar_path,
                    manifest_path=manifest_path,
                    expectation=expectation,
                )

    def test_v16_prompt_is_built_by_shared_authority(self) -> None:
        profile = LocalQwenProfile.create(
            model_root_identity={
                "identity_kind": "canonical_json",
                "sha256": "sha256:" + "3" * 64,
                "byte_length": 1,
                "revision": "test",
            },
            model_inventory_identity={
                "identity_kind": "canonical_row_list",
                "sha256": "sha256:" + "4" * 64,
                "byte_length": 1,
                "revision": "test",
            },
            model_file_count=1,
        )
        source = SimpleNamespace(input_bytes=self.input_bytes)
        prompt, config, request = _diagnostic_artifacts(
            source=source,  # type: ignore[arg-type]
            profile=profile,
            diagnostic_run_id="phase5-local-f1-v16-core2-none-test",
        )
        prompt_value = json.loads(prompt)
        self.assertEqual(prompt_value["prompt_revision"], PROMPT_AUTHORITY_REVISION)
        self.assertEqual(
            prompt_value["prompt_authority_identity"]["sha256"],
            PROMPT_AUTHORITY_SHA256,
        )
        self.assertEqual(json.loads(config)["fixed_max_new_tokens"], None)
        self.assertEqual(json.loads(request)["generate_call_cap"], 1)

    def test_strict_f1_validator_accepts_complete_fixture_ownership(self) -> None:
        state = phase4_create_portable_authority_state(self.b_input)
        output = phase4_synthetic_fixture_output("F1", state)
        validated = _validate_raw_f1(raw_json(output), self.b_input)
        self.assertEqual(validated, output)

    def test_strict_f1_validator_rejects_missing_component_ownership(self) -> None:
        state = phase4_create_portable_authority_state(self.b_input)
        output = phase4_synthetic_fixture_output("F1", state)
        output["sections"][0]["component_local_ids"] = [  # type: ignore[index]
            output["sections"][0]["component_local_ids"][0]  # type: ignore[index]
        ]
        with self.assertRaisesRegex(Exception, "ownership"):
            _validate_raw_f1(raw_json(output), self.b_input)

    def _prepared(self, result_root: Path) -> PreparedDiagnostic:
        profile = LocalQwenProfile.create(
            model_root_identity={
                "identity_kind": "canonical_json",
                "sha256": "sha256:" + "5" * 64,
                "byte_length": 1,
                "revision": "test",
            },
            model_inventory_identity={
                "identity_kind": "canonical_row_list",
                "sha256": "sha256:" + "6" * 64,
                "byte_length": 1,
                "revision": "test",
            },
            model_file_count=1,
        )
        prompt = canonical({"schema_version": "test", "node_id": "F1"})
        return PreparedDiagnostic(
            result_root=result_root,
            input_bytes=self.input_bytes,
            prompt_bytes=prompt,
            config_bytes=canonical({"config": "test"}),
            request_bytes=canonical(
                {
                    "call_kind": "integrated",
                    "b_aux_disposition": "absent/not_requested",
                    "node_id": "F1",
                }
            ),
            b_input=self.b_input,
            profile=profile,
        )

    def test_preparation_uses_component_only_v16_bootstrap(self) -> None:
        parent = self._test_root()
        result_root = parent / "diagnostic"
        profile = self._prepared(parent).profile
        source = SourceMaterial(
            input_bytes=self.input_bytes,
            b_input=self.b_input,
            source_binding={
                "schema_version": "test.source_binding",
                "source_run_id": "source-run",
                "input_provenance": "exact_preserved_row_member_bytes",
                "source_result_mutated": False,
            },
        )
        native_context_identity = {
            "identity_kind": "raw_bytes",
            "sha256": "sha256:" + "7" * 64,
            "byte_length": 1,
            "revision": "test.native_context",
        }
        with (
            patch(
                "req2web_runtime.phase5_local_f1_diagnostic."
                "load_core2_source_material",
                return_value=source,
            ),
            patch(
                "req2web_runtime.phase5_local_f1_diagnostic."
                "_local_f4._build_f4_profile",
                return_value=(profile, native_context_identity),
            ),
        ):
            from req2web_runtime.phase5_local_f1_diagnostic import (
                prepare_phase5_local_f1_diagnostic,
            )

            prepared = prepare_phase5_local_f1_diagnostic(
                tar_path=parent / "unused.tar",
                manifest_path=parent / "unused.json",
                model_root=parent,
                integrity_evidence=parent / "unused-evidence.json",
                result_root=result_root,
                diagnostic_run_id="phase5-local-f1-v16-core2-none-test-bootstrap",
            )
        self.assertEqual(prepared.result_root, result_root.resolve())
        self.assertFalse((result_root / "pre_call_manifest.json").exists())
        self.assertFalse((result_root / "fresh_integrated_policy.json").exists())
        preflight = json.loads(
            (result_root / "component_runtime_preflight.json").read_bytes()
        )
        self.assertEqual(preflight["node_scope"], ["F1"])
        self.assertEqual(preflight["unused_nodes"], ["F2", "F3", "F4"])
        self.assertEqual(
            preflight["prompt_revision"],
            PROMPT_AUTHORITY_REVISION,
        )
        self.assertFalse(preflight["historical_pilot_policy_bound"])
        serialized = canonical(preflight)
        self.assertNotIn(b"p4-03-prompt-v1", serialized)
        self.assertNotIn(b"fresh_integrated_policy", serialized)

    def test_three_core2_condition_expectations_are_frozen(self) -> None:
        self.assertEqual(
            set(CORE2_EXPECTATIONS),
            {"none", "irrelevant_evidence", "remove_critical_role"},
        )
        self.assertEqual(CORE2_EXPECTATIONS["none"].execution_index, 9)
        self.assertEqual(
            CORE2_EXPECTATIONS["irrelevant_evidence"].execution_index,
            5,
        )
        self.assertEqual(
            CORE2_EXPECTATIONS["remove_critical_role"].execution_index,
            2,
        )
        self.assertEqual(
            CORE2_EXPECTATIONS["irrelevant_evidence"].input_sha256,
            "a910db8800db1bab9a9b3958462ba4f419db8322ecfa3c21979618def9ba014d",
        )

    def test_component_runtime_starter_is_one_call_and_stage_neutral(self) -> None:
        model_root = self._test_root()
        integrity_evidence = model_root / "integrity.json"
        integrity_evidence.write_text("{}", encoding="utf-8")
        inventory_identity = {
            "identity_kind": "canonical_row_list",
            "sha256": "sha256:" + "8" * 64,
            "byte_length": 1,
            "revision": "test.inventory",
        }
        model_root_identity = local_runtime._identity(
            {
                "model_id": local_runtime.QWEN_MODEL_ID,
                "model_revision": local_runtime.QWEN_MODEL_REVISION,
                "resolved_model_root": str(model_root.resolve(strict=True)),
                "inventory": inventory_identity,
            },
            revision=f"{local_runtime.P4_03_SCHEMA_PREFIX}.model-root.v1",
        )
        profile = LocalQwenProfile.create(
            model_root_identity=model_root_identity,
            model_inventory_identity=inventory_identity,
            model_file_count=1,
        )
        backend = FakeBackend(b"{}")
        with (
            patch.object(
                local_runtime,
                "validate_model_inventory_metadata",
                return_value={
                    "inventory_identity": inventory_identity,
                    "file_count": 1,
                },
            ),
            patch.object(
                local_runtime,
                "_start_supervised_local_qwen_fresh_integrated_backend",
                return_value=backend,
            ) as start,
        ):
            runtime = (
                local_runtime.start_supervised_local_qwen_fresh_integrated_component_runtime(
                    model_root=model_root,
                    integrity_evidence=integrity_evidence,
                    profile=profile,
                )
            )
        self.assertIs(runtime.backend, backend)
        self.assertEqual(runtime.loaded_facts, backend.loaded_facts)
        self.assertEqual(start.call_args.kwargs["generate_call_cap"], 1)
        with self.assertRaisesRegex(Exception, "must be one"):
            local_runtime.start_supervised_local_qwen_fresh_integrated_component_runtime(
                model_root=model_root,
                integrity_evidence=integrity_evidence,
                profile=profile,
                generate_call_cap=2,
            )

    def test_component_runtime_inventory_drift_stops_before_worker_spawn(self) -> None:
        model_root = self._test_root()
        integrity_evidence = model_root / "integrity.json"
        integrity_evidence.write_text("{}", encoding="utf-8")
        frozen_inventory = {
            "identity_kind": "canonical_row_list",
            "sha256": "sha256:" + "9" * 64,
            "byte_length": 1,
            "revision": "test.inventory",
        }
        live_inventory = {
            **frozen_inventory,
            "sha256": "sha256:" + "a" * 64,
        }
        profile = LocalQwenProfile.create(
            model_root_identity=local_runtime._identity(
                {
                    "model_id": local_runtime.QWEN_MODEL_ID,
                    "model_revision": local_runtime.QWEN_MODEL_REVISION,
                    "resolved_model_root": str(model_root.resolve(strict=True)),
                    "inventory": frozen_inventory,
                },
                revision=f"{local_runtime.P4_03_SCHEMA_PREFIX}.model-root.v1",
            ),
            model_inventory_identity=frozen_inventory,
            model_file_count=1,
        )
        with (
            patch.object(
                local_runtime,
                "validate_model_inventory_metadata",
                return_value={
                    "inventory_identity": live_inventory,
                    "file_count": 1,
                },
            ),
            patch.object(
                local_runtime,
                "_start_supervised_local_qwen_fresh_integrated_backend",
            ) as start,
        ):
            with self.assertRaisesRegex(Exception, "live model inventory drifted"):
                local_runtime.start_supervised_local_qwen_fresh_integrated_component_runtime(
                    model_root=model_root,
                    integrity_evidence=integrity_evidence,
                    profile=profile,
                )
        start.assert_not_called()

    def test_run_uses_one_call_cap_and_persists_raw_before_success(self) -> None:
        state = phase4_create_portable_authority_state(self.b_input)
        raw = raw_json(phase4_synthetic_fixture_output("F1", state))
        backend = FakeBackend(raw)
        result_root = self._test_root()
        prepared = self._prepared(result_root)
        runtime = SimpleNamespace(backend=backend, loaded_facts={"loaded": True})
        with (
            patch(
                "req2web_runtime.phase5_local_f1_diagnostic."
                "prepare_phase5_local_f1_diagnostic",
                return_value=prepared,
            ),
            patch(
                "req2web_runtime.phase5_local_f1_diagnostic."
                "_local.start_supervised_local_qwen_fresh_integrated_component_runtime",
                return_value=runtime,
            ) as start,
        ):
            summary = run_phase5_local_f1_diagnostic(
                tar_path=result_root / "unused.tar",
                manifest_path=result_root / "unused.json",
                model_root=result_root,
                integrity_evidence=result_root / "unused-evidence.json",
                result_root=result_root,
                diagnostic_run_id="phase5-local-f1-v16-core2-none-test",
            )
        self.assertEqual((result_root / "raw_response.bin").read_bytes(), raw)
        self.assertTrue(summary["raw_contract_pass"])
        self.assertEqual(summary["generate_started_count"], 1)
        self.assertEqual(backend.calls, 1)
        self.assertEqual(start.call_args.kwargs["generate_call_cap"], 1)

    def test_invalid_raw_is_preserved_and_fails_closed_without_retry(self) -> None:
        raw = b"{}"
        backend = FakeBackend(raw)
        result_root = self._test_root()
        prepared = self._prepared(result_root)
        runtime = SimpleNamespace(backend=backend, loaded_facts={"loaded": True})
        with (
            patch(
                "req2web_runtime.phase5_local_f1_diagnostic."
                "prepare_phase5_local_f1_diagnostic",
                return_value=prepared,
            ),
            patch(
                "req2web_runtime.phase5_local_f1_diagnostic."
                "_local.start_supervised_local_qwen_fresh_integrated_component_runtime",
                return_value=runtime,
            ),
        ):
            summary = run_phase5_local_f1_diagnostic(
                tar_path=result_root / "unused.tar",
                manifest_path=result_root / "unused.json",
                model_root=result_root,
                integrity_evidence=result_root / "unused-evidence.json",
                result_root=result_root,
                diagnostic_run_id="phase5-local-f1-v16-core2-none-test",
            )
        self.assertEqual((result_root / "raw_response.bin").read_bytes(), raw)
        self.assertFalse(summary["raw_contract_pass"])
        self.assertEqual(summary["status"], "failed_closed")
        self.assertEqual(backend.calls, 1)
        self.assertFalse(summary["automatic_retry"])


if __name__ == "__main__":
    unittest.main()
