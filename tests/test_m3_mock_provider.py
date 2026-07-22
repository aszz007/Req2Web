from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import req2web_provider.mock_provider as mock_provider_module  # noqa: E402

from req2web_provider import (  # noqa: E402
    MockProviderRequestError,
    MockSmokePageSpecProvider,
    ProviderError,
    ProviderRawResponse,
    ScriptedMockProviderOutcome,
    SyntheticMockProviderRequest,
    SyntheticMockVisibilityReceipt,
    SyntheticMockVisibilitySourceRegistry,
)


SYNTHETIC_INPUT = b'{"fixture":"synthetic-provider-input"}'
MOCK_CONFIG = b'{"fixture":"mock-serializer-v1"}'
RAW_BYTES = b'{"raw":"must-remain-byte-identical"}'


def build_receipt(
    *,
    synthetic_input: bytes = SYNTHETIC_INPUT,
    mock_config: bytes = MOCK_CONFIG,
) -> SyntheticMockVisibilityReceipt:
    return SyntheticMockVisibilityReceipt.from_mock_bytes(
        synthetic_input,
        mock_config,
        SyntheticMockVisibilitySourceRegistry((), (), ()),
    )


def build_request() -> SyntheticMockProviderRequest:
    return SyntheticMockProviderRequest.from_mock_bytes(
        build_receipt(),
        synthetic_input_bytes=SYNTHETIC_INPUT,
        mock_serializer_config_bytes=MOCK_CONFIG,
    )


class MockProviderTests(unittest.TestCase):
    def assert_request_error(self, callback, code: str) -> None:
        with self.assertRaises(MockProviderRequestError) as raised:
            callback()
        self.assertEqual(
            (raised.exception.code, raised.exception.stage, raised.exception.phase),
            (code, "input_context", "mock_provider_request"),
        )

    def test_success_returns_exact_raw_bytes_and_safe_deterministic_record(self) -> None:
        request = build_request()
        raw = ProviderRawResponse.from_bytes(RAW_BYTES)
        provider = MockSmokePageSpecProvider(ScriptedMockProviderOutcome.success(raw))
        first_result, first_record = provider.invoke(request)
        second_result, second_record = provider.invoke(request)

        self.assertEqual(first_result.outcome, "success")
        self.assertEqual(len(request.request_id.removeprefix("mock-request-")), 64)
        self.assertEqual(len(first_result.result_id.removeprefix("mock-result-")), 64)
        self.assertEqual(len(first_record.record_id.removeprefix("mock-invocation-")), 64)
        self.assertIs(first_result.raw_response, raw)
        self.assertEqual(first_result.raw_response.raw_bytes, RAW_BYTES)
        self.assertIsNone(first_result.provider_error)
        self.assertEqual(first_result.result_id, second_result.result_id)
        self.assertEqual(first_record.to_dict(), second_record.to_dict())
        self.assertEqual(first_record.raw_response_sha256, raw.sha256)
        self.assertEqual(first_record.raw_response_byte_length, len(RAW_BYTES))
        self.assertNotIn(RAW_BYTES.decode("utf-8"), json.dumps(first_record.to_dict(), sort_keys=True))
        self.assertNotIn(SYNTHETIC_INPUT.decode("utf-8"), json.dumps(request.to_dict(), sort_keys=True))
        self.assertFalse(first_record.model_loaded)
        self.assertFalse(first_record.remote_called)
        self.assertEqual(first_record.g1_g2_status, "not_assigned")
        self.assertEqual(first_record.evidence_use_status, "not_evaluated")

    def test_all_fixed_runtime_errors_preserve_retryability(self) -> None:
        expected = {
            "timeout": True,
            "cancelled": False,
            "resource_exhausted": True,
            "service_unavailable": True,
        }
        request = build_request()
        for code, retryable in expected.items():
            with self.subTest(code=code):
                provider = MockSmokePageSpecProvider(ScriptedMockProviderOutcome.provider_runtime_error(code))
                result, record = provider.invoke(request)
                self.assertEqual(result.outcome, "provider_runtime_error")
                self.assertIsNone(result.raw_response)
                self.assertEqual(
                    (result.provider_error.code, result.provider_error.stage, result.provider_error.phase, result.provider_error.retryable),
                    (code, "provider_runtime", "transport", retryable),
                )
                self.assertIsNone(record.raw_response_sha256)
                self.assertIsNone(record.raw_response_byte_length)
                self.assertEqual(record.provider_error, result.provider_error)
                record.validate()

    def test_request_rebinds_receipt_to_exact_bytes_and_rejects_bad_inputs(self) -> None:
        receipt = build_receipt()
        self.assert_request_error(
            lambda: SyntheticMockProviderRequest.from_mock_bytes(receipt, synthetic_input_bytes=b"wrong", mock_serializer_config_bytes=MOCK_CONFIG),
            "synthetic_input_receipt_mismatch",
        )
        self.assert_request_error(
            lambda: SyntheticMockProviderRequest.from_mock_bytes(receipt, synthetic_input_bytes=SYNTHETIC_INPUT, mock_serializer_config_bytes=b"wrong"),
            "mock_serializer_config_receipt_mismatch",
        )
        self.assert_request_error(
            lambda: SyntheticMockProviderRequest.from_mock_bytes(receipt, synthetic_input_bytes=b"", mock_serializer_config_bytes=MOCK_CONFIG),
            "synthetic_input_invalid",
        )
        self.assert_request_error(
            lambda: SyntheticMockProviderRequest.from_mock_bytes(receipt, synthetic_input_bytes="not-bytes", mock_serializer_config_bytes=MOCK_CONFIG),
            "synthetic_input_invalid",
        )
        self.assert_request_error(
            lambda: SyntheticMockProviderRequest.from_mock_bytes(receipt, synthetic_input_bytes=SYNTHETIC_INPUT, mock_serializer_config_bytes=b""),
            "mock_serializer_config_invalid",
        )
        self.assert_request_error(
            lambda: SyntheticMockProviderRequest.from_mock_bytes(receipt, synthetic_input_bytes=SYNTHETIC_INPUT, mock_serializer_config_bytes="not-bytes"),
            "mock_serializer_config_invalid",
        )
        with self.assertRaises(ValueError):
            MockProviderRequestError("unsupported_code")
        with self.assertRaises(MockProviderRequestError) as raised:
            SyntheticMockProviderRequest.from_mock_bytes(
                receipt, synthetic_input_bytes=b"", mock_serializer_config_bytes=MOCK_CONFIG
            )
        self.assertEqual(str(raised.exception), "Synthetic input bytes must be non-empty bytes.")

    def test_forged_or_non_strict_receipt_fails_closed(self) -> None:
        receipt = build_receipt()
        for forged in (
            replace(receipt, external_egress_allowed=True),
            replace(receipt, mock_only=1),
            replace(receipt, is_d17_manifest=True),
            replace(receipt, is_d17_serializer_output=True),
        ):
            with self.subTest(forged=forged):
                self.assert_request_error(
                    lambda forged=forged: SyntheticMockProviderRequest.from_mock_bytes(
                        forged, synthetic_input_bytes=SYNTHETIC_INPUT, mock_serializer_config_bytes=MOCK_CONFIG
                    ),
                    "visibility_receipt_invalid",
                )

    def test_script_outcome_requires_one_valid_branch(self) -> None:
        raw = ProviderRawResponse.from_bytes(RAW_BYTES)
        bad_error = ProviderError("timeout", "provider_runtime", "transport", False)
        with self.assertRaises(ValueError):
            MockSmokePageSpecProvider(ScriptedMockProviderOutcome("success", raw, bad_error))
        with self.assertRaises(ValueError):
            MockSmokePageSpecProvider(ScriptedMockProviderOutcome("provider_runtime_error"))
        with self.assertRaises(ValueError):
            ScriptedMockProviderOutcome.provider_runtime_error("unknown_error")
        with self.assertRaises(ValueError):
            MockSmokePageSpecProvider(
                ScriptedMockProviderOutcome("provider_runtime_error", provider_error=bad_error)
            )

    def test_result_and_record_require_mutually_exclusive_tamper_free_roots(self) -> None:
        request = build_request()
        result, record = MockSmokePageSpecProvider(
            ScriptedMockProviderOutcome.success(ProviderRawResponse.from_bytes(RAW_BYTES))
        ).invoke(request)
        with self.assertRaises(ValueError):
            replace(result, provider_error=ProviderError("timeout", "provider_runtime", "transport", True)).validate()
        with self.assertRaises(ValueError):
            replace(record, raw_response_byte_length=record.raw_response_byte_length + 1).validate()
        with self.assertRaises(ValueError):
            replace(record, synthetic_mock_only=1).validate()
        with self.assertRaises(ValueError):
            replace(record, g1_g2_status="G1").validate()
        with self.assertRaises(ValueError):
            replace(request, remote_called=True).validate()
        with self.assertRaises(ValueError):
            replace(request, request_id="mock-request-forged").validate()

    def test_record_replays_request_and_result_ids_before_record_id(self) -> None:
        request = build_request()
        _, record = MockSmokePageSpecProvider(
            ScriptedMockProviderOutcome.success(ProviderRawResponse.from_bytes(RAW_BYTES))
        ).invoke(request)
        forged_request_root = replace(record, request_id="mock-request-" + ("a" * 64))
        forged_request = replace(
            forged_request_root,
            record_id=mock_provider_module._id("mock-invocation-", forged_request_root._root()),
        )
        with self.assertRaisesRegex(ValueError, "request ID does not bind request facts"):
            forged_request.validate()

        forged_result_root = replace(record, result_id="mock-result-" + ("b" * 64))
        forged_result = replace(
            forged_result_root,
            record_id=mock_provider_module._id("mock-invocation-", forged_result_root._root()),
        )
        with self.assertRaisesRegex(ValueError, "result ID does not bind result facts"):
            forged_result.validate()

    def test_static_boundary_has_no_orchestration_or_reverse_imports(self) -> None:
        source_path = ROOT / "src" / "req2web_provider" / "mock_provider.py"
        source = source_path.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("\ufeff"))
        forbidden = (
            "parse_provider_raw_response",
            "CanonicalPageSpecAssembler",
            "AgentContextBundle",
            "RetrievalGuidance",
            "req2web_evaluation",
            "subprocess",
            "socket",
            "urllib",
            "requests",
            "http.client",
            "time.sleep",
            "open(",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()