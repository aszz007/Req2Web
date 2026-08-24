from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.guispector_optional import (  # noqa: E402
    GUISpectorOptionalVerificationError,
    optional_provider_capabilities,
    test_optional_provider_connection,
)


class GUISpectorOptionalProviderTests(unittest.TestCase):
    def test_capability_is_disabled_by_default_and_non_persistent(self) -> None:
        value = optional_provider_capabilities()
        self.assertFalse(value["default_enabled"])
        self.assertEqual(value["credential_persistence"], "disabled")
        self.assertEqual(value["automatic_retry_count"], 0)
        self.assertEqual(
            [item["provider_id"] for item in value["providers"]],
            ["zhipu_bigmodel", "alibaba_gui_plus"],
        )

    def test_zhipu_connection_requires_confirmation_and_uses_one_call(self) -> None:
        calls: list[str] = []
        payload = {
            "provider_id": "zhipu_bigmodel",
            "model": "glm-4.6v",
            "api_key": "local-test-secret",
            "confirm_external_model_action": True,
        }
        result = test_optional_provider_connection(
            payload,
            bigmodel_test=lambda key: calls.append(key),
        )
        self.assertEqual(calls, ["local-test-secret"])
        self.assertEqual(result["external_model_call_count"], 1)
        self.assertEqual(result["automatic_retry_count"], 0)
        self.assertFalse(result["api_key_persisted"])
        self.assertFalse(result["guispector_verification_executed"])
        self.assertNotIn("local-test-secret", str(result))

        payload["confirm_external_model_action"] = False
        with self.assertRaisesRegex(
            GUISpectorOptionalVerificationError,
            "explicit confirmation",
        ):
            test_optional_provider_connection(payload)

    def test_gui_plus_requires_workspace_and_exact_model(self) -> None:
        payload = {
            "provider_id": "alibaba_gui_plus",
            "model": "gui-plus-2026-02-26",
            "api_key": "local-test-secret",
            "confirm_external_model_action": True,
        }
        with self.assertRaisesRegex(
            GUISpectorOptionalVerificationError,
            "workspace ID",
        ):
            test_optional_provider_connection(payload)

        calls: list[tuple[str, str]] = []
        payload["workspace_id"] = "example-workspace"
        result = test_optional_provider_connection(
            payload,
            gui_plus_test=lambda key, workspace: calls.append((key, workspace)),
        )
        self.assertEqual(calls, [("local-test-secret", "example-workspace")])
        self.assertEqual(result["provider_model"], "gui-plus-2026-02-26")

        payload["model"] = "some-other-model"
        with self.assertRaisesRegex(
            GUISpectorOptionalVerificationError,
            "model must match",
        ):
            test_optional_provider_connection(payload)

    def test_provider_error_redacts_exact_key(self) -> None:
        def fail(key: str) -> None:
            raise OSError(f"transport rejected Bearer {key}")

        with self.assertRaises(GUISpectorOptionalVerificationError) as caught:
            test_optional_provider_connection(
                {
                    "provider_id": "zhipu_bigmodel",
                    "model": "glm-4.6v",
                    "api_key": "local-test-secret",
                    "confirm_external_model_action": True,
                },
                bigmodel_test=fail,
            )
        self.assertNotIn("local-test-secret", str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
