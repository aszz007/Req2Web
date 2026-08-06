from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.phase5_action_authority import (  # noqa: E402
    Phase5FinalActionAuthority,
    create_phase5_final_action_authority,
    validate_phase5_action_authority_against_package,
)
from req2web_runtime.phase5_sealed_action_package import (  # noqa: E402
    create_phase5_sealed_action_package,
)


FIXTURE = ROOT / "fixtures" / "phase5_sealed_action_package_synthetic_v1.json"


def _source(package_payload: dict[str, object]) -> dict[str, object]:
    bindings = dict(package_payload["authority_bindings"])
    bindings.pop("final_action_receipt_sha256")
    return {
        "schema_version": "req2web.phase5.final_action_authority.v1",
        "status": "owner_approved_ready_for_exact_action",
        "route": "path_1_licensed_minimal_real_material",
        "run_id": package_payload["run_id"],
        "source_action_commit": package_payload["source_action_commit"],
        "authority_bindings": bindings,
        "instance": {
            "instance_id": "synthetic-autodl-instance-001",
            "gpu_uuid": "GPU-synthetic-phase5-5090",
            "gpu_class": "nvidia_geforce_rtx_5090",
            "device_index": 0,
            "ssh_fingerprint_sha256": "9" * 64,
        },
        "limits": {
            "hourly_rate_minor_units": 100,
            "time_cap_seconds": package_payload["budget"]["time_cap_seconds"],
            "cost_cap_minor_units": package_payload["budget"][
                "cost_cap_minor_units"
            ],
            "storage_cap_bytes": package_payload["budget"]["storage_cap_bytes"],
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
                "synthetic-action"
            ),
        },
        "owner_confirmation_sha256": "a" * 64,
        "authorization": {
            "owner_approved_exact_run": True,
            "real_h1_projection_open_allowed": True,
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
            "ssh_connected": False,
            "model_loaded": False,
            "holdout_executed": False,
            "formal_quality_claimed": False,
        },
    }


class Phase5ActionAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.synthetic_package = create_phase5_sealed_action_package(self.fixture)
        self.source = _source(self.synthetic_package.to_dict())
        self.authority = create_phase5_final_action_authority(self.source)

    def test_hash_only_receipt_round_trip(self) -> None:
        replayed = Phase5FinalActionAuthority.from_json_bytes(
            self.authority.canonical_json_bytes()
        )
        self.assertEqual(replayed, self.authority)
        encoded = self.authority.canonical_json
        for prohibited in (
            '"gold"',
            "gold_payload",
            "requirement_projection",
            "private_key",
            '"token"',
        ):
            self.assertNotIn(prohibited, encoded)
        self.assertFalse(self.authority.to_dict()["action_state"]["ssh_connected"])
        self.assertFalse(self.authority.to_dict()["action_state"]["model_loaded"])

    def test_receipt_binds_a_formal_package_without_circular_package_hash(self) -> None:
        formal = json.loads(json.dumps(self.fixture))
        formal["package_kind"] = "owner_sealed_formal_h1"
        formal["authority_bindings"][
            "final_action_receipt_sha256"
        ] = self.authority.sha256()
        for key in (
            "owner_sealed",
            "contains_real_h1_projection",
            "final_action_authorized",
            "model_action_authorized",
            "gpu_or_remote_action_authorized",
        ):
            formal["action_state"][key] = True
        package = create_phase5_sealed_action_package(formal)
        validate_phase5_action_authority_against_package(
            self.authority,
            package,
        )

    def test_binding_or_limit_drift_fails_closed(self) -> None:
        formal = json.loads(json.dumps(self.fixture))
        formal["package_kind"] = "owner_sealed_formal_h1"
        formal["authority_bindings"][
            "final_action_receipt_sha256"
        ] = self.authority.sha256()
        for key in (
            "owner_sealed",
            "contains_real_h1_projection",
            "final_action_authorized",
            "model_action_authorized",
            "gpu_or_remote_action_authorized",
        ):
            formal["action_state"][key] = True
        formal["budget"]["time_cap_seconds"] += 1
        package = create_phase5_sealed_action_package(formal)
        with self.assertRaisesRegex(ValueError, "limit drifted"):
            validate_phase5_action_authority_against_package(
                self.authority,
                package,
            )

    def test_training_path3_and_result_driven_change_remain_prohibited(self) -> None:
        tampered = json.loads(json.dumps(self.source))
        tampered["authorization"]["training_allowed"] = True
        with self.assertRaisesRegex(ValueError, "must be false"):
            create_phase5_final_action_authority(tampered)


if __name__ == "__main__":
    unittest.main()
