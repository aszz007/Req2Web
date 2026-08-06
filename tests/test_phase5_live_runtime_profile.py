from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.phase4_remote_qwen_fresh_integrated import (  # noqa: E402
    RemoteFreshIntegratedProfile,
)
from req2web_runtime.phase5_live_runtime_profile import (  # noqa: E402
    create_phase5_live_runtime_profile,
)


BASE_PROFILE = (
    ROOT / "p4-05-stability-full-direct-20260805-g" / "remote_profile.json"
)


class Phase5LiveRuntimeProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = ROOT / f".phase5-live-profile-test-{uuid.uuid4().hex}"
        self.temp.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_rebinds_only_current_gpu_facts_and_writes_exact_bytes(self) -> None:
        base = RemoteFreshIntegratedProfile.from_bytes(
            BASE_PROFILE.read_bytes()
        )
        output = (self.temp / "runtime_profile.json").resolve()
        profile = create_phase5_live_runtime_profile(
            base_profile_path=BASE_PROFILE,
            output_path=output,
            gpu_probe=lambda: {
                "device_name": base.device_name,
                "device_uuid": "GPU-phase5-current-instance",
                "total_vram_bytes": base.total_vram_bytes,
                "free_vram_bytes": base.free_vram_bytes_at_preflight - 1,
                "driver_version": base.driver_version,
                "cuda_version": base.cuda_version,
            },
        )
        self.assertEqual(
            profile.device_uuid,
            "GPU-phase5-current-instance",
        )
        self.assertEqual(profile.model_root_identity, base.model_root_identity)
        self.assertEqual(
            profile.model_inventory_identity,
            base.model_inventory_identity,
        )
        self.assertFalse(profile.model_loaded)
        self.assertFalse(profile.run_occurred)
        self.assertEqual(output.read_bytes(), profile.canonical_bytes())

    def test_existing_output_fails_closed(self) -> None:
        output = (self.temp / "runtime_profile.json").resolve()
        output.write_bytes(b"occupied")
        with self.assertRaisesRegex(ValueError, "output is invalid"):
            create_phase5_live_runtime_profile(
                base_profile_path=BASE_PROFILE,
                output_path=output,
                gpu_probe=lambda: self.fail("GPU probe must not run"),
            )


if __name__ == "__main__":
    unittest.main()
