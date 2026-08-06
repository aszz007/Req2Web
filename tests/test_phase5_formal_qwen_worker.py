from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_runtime.phase5_formal_qwen_worker import (  # noqa: E402
    Phase5FormalQwenWorkerError,
    STREAM_SCHEMA_VERSION,
    _StreamMirror,
    validate_phase5_formal_generation_artifacts,
)
from req2web_runtime.phase5_formal_runner import (  # noqa: E402
    run_phase5_formal_runner,
    synthetic_phase5_worker_factory,
)
from req2web_runtime.phase5_sealed_action_package import (  # noqa: E402
    create_phase5_sealed_action_package,
)


FIXTURE = ROOT / "fixtures" / "phase5_sealed_action_package_synthetic_v1.json"


class Phase5FormalQwenWorkerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.package = create_phase5_sealed_action_package(
            json.loads(FIXTURE.read_text(encoding="utf-8"))
        )
        self.temp = ROOT / f".phase5-formal-worker-test-{uuid.uuid4().hex}"
        self.temp.mkdir()
        self.result_root = (self.temp / "result").resolve()
        run_phase5_formal_runner(
            package=self.package,
            result_root=self.result_root,
            worker_factory=synthetic_phase5_worker_factory,
            allow_synthetic_validation_only=True,
        )
        self.attempt = (
            sorted((self.result_root / "cases").iterdir())[0]
            / "attempts"
            / "F1"
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _raw(self, name: str) -> bytes:
        return (self.attempt / name).read_bytes()

    def test_artifact_contract_validates_without_loading_a_model(self) -> None:
        artifacts = validate_phase5_formal_generation_artifacts(
            node_id="F1",
            input_bytes=self._raw("input.json"),
            prompt_bytes=self._raw("prompt.json"),
            config_bytes=self._raw("config.json"),
            request_bytes=self._raw("request.json"),
            require_formal_action_receipt=False,
        )
        self.assertEqual(artifacts["request"]["generate_call_cap"], 1)
        self.assertFalse(artifacts["request"]["automatic_retry"])
        self.assertEqual(artifacts["config"]["dtype"], "bfloat16")
        self.assertEqual(artifacts["config"]["quantization"], "none")

    def test_real_worker_contract_rejects_missing_final_action_receipt(self) -> None:
        with self.assertRaisesRegex(
            Phase5FormalQwenWorkerError,
            "requires the final action receipt hash",
        ):
            validate_phase5_formal_generation_artifacts(
                node_id="F1",
                input_bytes=self._raw("input.json"),
                prompt_bytes=self._raw("prompt.json"),
                config_bytes=self._raw("config.json"),
                request_bytes=self._raw("request.json"),
                require_formal_action_receipt=True,
            )

    def test_provider_input_tamper_is_rejected_before_model_action(self) -> None:
        tampered = json.loads(self._raw("input.json"))
        tampered["path1_static_projection"]["gold"] = {"answer": "hidden"}
        with self.assertRaisesRegex(
            Phase5FormalQwenWorkerError,
            "prohibited keys",
        ):
            validate_phase5_formal_generation_artifacts(
                node_id="F1",
                input_bytes=json.dumps(
                    tampered,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                prompt_bytes=self._raw("prompt.json"),
                config_bytes=self._raw("config.json"),
                request_bytes=self._raw("request.json"),
                require_formal_action_receipt=False,
            )

    def test_cli_keeps_real_package_and_result_outside_repository(self) -> None:
        script = ROOT / "scripts" / "run_phase5_formal_holdout.py"
        spec = importlib.util.spec_from_file_location(
            "phase5_formal_holdout_cli_test",
            script,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            module._outside_repository(
                ROOT / "owner-sealed-action.json",
                must_exist=False,
                name="real package",
            )

    def test_stream_mirror_accepts_empty_non_token_delta_and_marks_start(self) -> None:
        class _Target:
            def __init__(self) -> None:
                self.value = ""

            def write(self, value: str) -> None:
                self.value += value

            def flush(self) -> None:
                return None

        target = _Target()
        mirror = _StreamMirror(target)
        mirror.feed(
            json.dumps(
                {
                    "schema_version": STREAM_SCHEMA_VERSION,
                    "event": "load_started",
                    "node_id": None,
                    "delta_b64": "",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        started: list[str] = []
        mirror.register_started_callback("F1", lambda: started.append("F1"))
        mirror.feed(
            json.dumps(
                {
                    "schema_version": STREAM_SCHEMA_VERSION,
                    "event": "generation_started",
                    "node_id": "F1",
                    "delta_b64": "",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        self.assertEqual(started, ["F1"])
        self.assertIn("load_started", target.value)

    def test_stream_mirror_preserves_non_json_worker_stderr(self) -> None:
        class _Target:
            def __init__(self) -> None:
                self.value = ""
                self.flush_count = 0

            def write(self, value: str) -> None:
                self.value += value

            def flush(self) -> None:
                self.flush_count += 1

        target = _Target()
        mirror = _StreamMirror(target)
        raw = b"[transformers] optional kernel unavailable; using torch\n"

        mirror.feed(raw)

        self.assertEqual(target.value, raw.decode("utf-8"))
        self.assertEqual(target.flush_count, 1)
        self.assertEqual(bytes(mirror.stderr_bytes), raw)
        self.assertEqual(bytes(mirror.token_bytes), b"")
        self.assertEqual(mirror.started_nodes, set())
        self.assertEqual(mirror.completed_nodes, set())


if __name__ == "__main__":
    unittest.main()
