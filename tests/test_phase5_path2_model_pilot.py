from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.phase5_action_authority import (  # noqa: E402
    PATH2_SCHEMA_VERSION,
    create_phase5_final_action_authority,
    validate_phase5_action_authority_against_package,
)
from req2web_runtime.phase5_formal_runner import (  # noqa: E402
    Phase5FormalRunnerError,
    run_phase5_formal_runner,
)
from req2web_runtime.phase5_path2_model_pilot import (  # noqa: E402
    prepare_phase5_path2_model_pilot,
)
from req2web_runtime.phase5_sealed_action_package import (  # noqa: E402
    PATH2_PROJECTION_SCHEMA_VERSION,
    PATH2_ROUTE,
    build_phase5_node_static_projection,
    create_phase5_sealed_action_package,
)


FIXTURE = ROOT / "fixtures" / "phase5_sealed_action_package_synthetic_v1.json"


def _path2_source() -> tuple[dict[str, object], dict[str, object]]:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source["package_kind"] = "project_authored_path2_model_pilot"
    source["route"] = PATH2_ROUTE
    for row in source["runtime_rows"]:
        row["g0_reference"]["status"] = (
            "not_materialized_path2_pilot_no_fallback"
        )
    source["action_state"].update(
        {
            "owner_sealed": True,
            "contains_real_h1_projection": False,
            "final_action_authorized": True,
            "model_action_authorized": True,
            "gpu_or_remote_action_authorized": True,
        }
    )
    bindings = dict(source["authority_bindings"])
    bindings.pop("final_action_receipt_sha256")
    authority_source = {
        "schema_version": PATH2_SCHEMA_VERSION,
        "status": "owner_approved_ready_for_exact_action",
        "route": PATH2_ROUTE,
        "run_id": source["run_id"],
        "source_action_commit": source["source_action_commit"],
        "authority_bindings": bindings,
        "instance": {
            "instance_id": "synthetic-path2-pilot-instance",
            "gpu_uuid": "GPU-synthetic-path2-pilot",
            "gpu_class": "nvidia_geforce_rtx_5090",
            "device_index": 0,
            "ssh_fingerprint_sha256": "9" * 64,
        },
        "limits": {
            "hourly_rate_minor_units": 100,
            "time_cap_seconds": source["budget"]["time_cap_seconds"],
            "cost_cap_minor_units": source["budget"]["cost_cap_minor_units"],
            "storage_cap_bytes": source["budget"]["storage_cap_bytes"],
        },
        "paths": {
            "repository_root": (
                "/root/autodl-tmp/req2web-phase5-qwen9b/"
                "repository-111111111111"
            ),
            "model_root": "/root/autodl-tmp/req2web-phase4-qwen9b/model",
            "model_integrity_evidence": (
                "/root/autodl-tmp/req2web-phase4-qwen9b/evidence/"
                "local_integrity.json"
            ),
            "python_executable": (
                "/root/autodl-tmp/req2web-qwen35-27b/runtime/bin/python"
            ),
            "result_root": (
                "/root/autodl-tmp/req2web-phase5-qwen9b/results/"
                "synthetic-path2-pilot"
            ),
        },
        "owner_confirmation_sha256": "a" * 64,
        "authorization": {
            "owner_approved_exact_run": True,
            "real_h1_projection_open_allowed": False,
            "model_action_allowed": True,
            "gpu_remote_paid_action_allowed": True,
            "single_owner_evaluation_allowed": True,
            "training_allowed": False,
            "lora_allowed": False,
            "path3_allowed_in_h1": False,
            "result_driven_change_allowed": False,
        },
        "action_state": {
            "receipt_created": True,
            "ssh_connected": True,
            "model_loaded": False,
            "holdout_executed": False,
            "formal_quality_claimed": False,
        },
    }
    return source, authority_source


class Phase5Path2ModelPilotTest(unittest.TestCase):
    def setUp(self) -> None:
        source, authority_source = _path2_source()
        self.authority = create_phase5_final_action_authority(authority_source)
        source["authority_bindings"][
            "final_action_receipt_sha256"
        ] = self.authority.sha256()
        self.package = create_phase5_sealed_action_package(source)
        self.temp = ROOT / f".phase5-path2-pilot-test-{uuid.uuid4().hex}"
        self.temp.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_path2_projection_and_authority_are_route_specific(self) -> None:
        payload = self.package.to_dict()
        self.assertEqual(
            payload["package_kind"],
            "project_authored_path2_model_pilot",
        )
        self.assertEqual(payload["route"], PATH2_ROUTE)
        self.assertFalse(
            payload["action_state"]["contains_real_h1_projection"]
        )
        validate_phase5_action_authority_against_package(
            self.authority,
            self.package,
        )
        projection = build_phase5_node_static_projection(
            self.package,
            matrix_row_id="synthetic-matrix-row-none-001",
            node_id="F3",
        )
        provider = projection["provider_payload"]
        self.assertEqual(
            provider["schema_version"],
            PATH2_PROJECTION_SCHEMA_VERSION,
        )
        self.assertIn("project_authored_evidence", provider)
        self.assertNotIn("licensed_evidence", provider)

    def test_path2_pilot_is_paused_before_worker_or_result_creation(self) -> None:
        result_root = (self.temp / "result").resolve()
        with self.assertRaisesRegex(
            Phase5FormalRunnerError,
            "formal model/H1 action remains disabled",
        ):
            run_phase5_formal_runner(
                package=self.package,
                result_root=result_root,
                worker_factory=lambda row: self.fail(
                    f"paused Path 2 pilot created worker: {row}"
                ),
                action_authority=self.authority,
                hourly_rate_minor_units=100,
            )
        self.assertFalse(result_root.exists())

    def test_path2_authority_cannot_open_real_h1(self) -> None:
        _, authority_source = _path2_source()
        authority_source["authorization"][
            "real_h1_projection_open_allowed"
        ] = True
        with self.assertRaisesRegex(ValueError, "real H1 authorization"):
            create_phase5_final_action_authority(authority_source)

        _, authority_source = _path2_source()
        authority_source["schema_version"] = (
            "req2web.phase5.final_action_authority.v1"
        )
        with self.assertRaisesRegex(ValueError, "schema drifted"):
            create_phase5_final_action_authority(authority_source)

    def test_preparation_writes_replayable_external_artifacts(self) -> None:
        preparation_root = (
            ROOT / f".phase5-path2-preparation-test-{uuid.uuid4().hex}"
        )
        preparation_root.mkdir()
        try:
            output_root = preparation_root / "pilot"
            prepared = prepare_phase5_path2_model_pilot(
                fixture_path=FIXTURE,
                output_root=output_root,
                source_action_commit="1" * 40,
                run_id="phase5-path2-model-pilot-test",
                instance_id="synthetic-path2-instance",
                gpu_uuid="GPU-synthetic-path2",
                ssh_fingerprint_sha256="9" * 64,
                repository_root=(
                    "/root/autodl-tmp/req2web-phase5-qwen9b/"
                    "repository-111111111111"
                ),
                model_root="/root/autodl-tmp/req2web-phase4-qwen9b/model",
                model_integrity_evidence=(
                    "/root/autodl-tmp/req2web-phase4-qwen9b/evidence/"
                    "local_integrity.json"
                ),
                python_executable=(
                    "/root/autodl-tmp/req2web-qwen35-27b/runtime/bin/python"
                ),
                result_root=(
                    "/root/autodl-tmp/req2web-phase5-qwen9b/results/"
                    "path2-pilot-test"
                ),
                hourly_rate_minor_units=0,
            )
            self.assertEqual(
                prepared.receipt["status"],
                "ready_for_bounded_path2_model_pilot",
            )
            self.assertEqual(
                prepared.package.to_dict()["package_kind"],
                "project_authored_path2_model_pilot",
            )
            self.assertTrue(
                (output_root / "final_action_authority.json").is_file()
            )
            self.assertTrue(
                (output_root / "sealed_action_package.json").is_file()
            )
            self.assertTrue(
                (output_root / "preparation_receipt.json").is_file()
            )
        finally:
            shutil.rmtree(preparation_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
