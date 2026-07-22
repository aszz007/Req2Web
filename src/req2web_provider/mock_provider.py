from __future__ import annotations

"""Synthetic/mock-only PageSpec Provider invocation seam for Req2Web M3.

It never derives Provider-visible inputs, parses Provider bytes, assembles a
PageSpec, loads a model, or performs I/O. It only exercises the raw-result and
provider-runtime-error boundary with caller-supplied synthetic fixtures.
"""

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from .candidate_audit import SyntheticMockVisibilityReceipt
from .semantic_candidate import (PROVIDER_RAW_RESPONSE_SCHEMA_VERSION, ProviderError, ProviderRawResponse)


MOCK_PROVIDER_REQUEST_SCHEMA_VERSION = "req2web.provider.synthetic_mock_invocation_request.v1"
MOCK_PROVIDER_RESULT_SCHEMA_VERSION = "req2web.provider.synthetic_mock_invocation_result.v1"
MOCK_PROVIDER_INVOCATION_RECORD_SCHEMA_VERSION = "req2web.provider.synthetic_mock_invocation_record.v1"

_DECLARATION = "synthetic_mock_provider_invocation_only_not_real_d17_provider_or_model"
_NOT_EXECUTED = "not_selected_or_executed"
_RUNTIME_RETRYABLE = {"timeout": True, "cancelled": False, "resource_exhausted": True, "service_unavailable": True}
_REQUEST_ERROR_MESSAGES = {
    "visibility_receipt_invalid": "Synthetic/mock visibility receipt is invalid.",
    "synthetic_input_invalid": "Synthetic input bytes must be non-empty bytes.",
    "mock_serializer_config_invalid": "Mock serializer/config bytes must be non-empty bytes.",
    "synthetic_input_receipt_mismatch": "Synthetic input bytes do not match the visibility receipt.",
    "mock_serializer_config_receipt_mismatch": "Mock serializer/config bytes do not match the visibility receipt.",
}


class MockProviderRequestError(ValueError):
    """Fixed safe error for pre-invocation synthetic request binding only."""

    def __init__(self, code: str) -> None:
        if code not in _REQUEST_ERROR_MESSAGES:
            raise ValueError("unsupported synthetic/mock Provider request error code")
        super().__init__(_REQUEST_ERROR_MESSAGES[code])
        self.code = code
        self.stage = "input_context"
        self.phase = "mock_provider_request"
        self.message = _REQUEST_ERROR_MESSAGES[code]


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_bytes(value: object, code: str) -> bytes:
    if not isinstance(value, bytes) or not value:
        raise MockProviderRequestError(code)
    return value


def _require_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{label} must be a lowercase SHA-256 value")
    return value


def _require_length(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _id(prefix: str, payload: dict[str, object]) -> str:
    return prefix + _sha256(_canonical_json_bytes(payload))


def _require_full_id(value: object, prefix: str, label: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError(f"{label} is invalid")
    _require_hash(value[len(prefix):], label)
    return value


def _request_root_from_facts(*, receipt_id: str, receipt_hash: str, input_hash: str, input_length: int, config_hash: str, config_length: int, synthetic_mock_only: bool, external_egress_allowed: bool, model_loaded: bool, remote_called: bool, real_d17_path_status: str, real_provider_status: str, real_model_status: str, g1_g2_status: str, evidence_use_status: str, declaration: str, schema_version: str) -> dict[str, object]:
    return {
        "schema_version": schema_version, "visibility_receipt_id": receipt_id, "visibility_receipt_sha256": receipt_hash,
        "synthetic_input_sha256": input_hash, "synthetic_input_byte_length": input_length,
        "mock_serializer_config_sha256": config_hash, "mock_serializer_config_byte_length": config_length,
        "synthetic_mock_only": synthetic_mock_only, "external_egress_allowed": external_egress_allowed,
        "model_loaded": model_loaded, "remote_called": remote_called, "real_d17_path_status": real_d17_path_status,
        "real_provider_status": real_provider_status, "real_model_status": real_model_status,
        "g1_g2_status": g1_g2_status, "evidence_use_status": evidence_use_status, "declaration": declaration,
    }


def _result_root_from_facts(*, request_id: str, outcome: str, raw_metadata: dict[str, object] | None, provider_error: dict[str, object] | None, synthetic_mock_only: bool, external_egress_allowed: bool, model_loaded: bool, remote_called: bool, real_d17_path_status: str, real_provider_status: str, real_model_status: str, g1_g2_status: str, evidence_use_status: str, declaration: str, schema_version: str) -> dict[str, object]:
    return {
        "schema_version": schema_version, "request_id": request_id, "outcome": outcome,
        "raw_response": raw_metadata, "provider_error": provider_error,
        "synthetic_mock_only": synthetic_mock_only, "external_egress_allowed": external_egress_allowed,
        "model_loaded": model_loaded, "remote_called": remote_called, "real_d17_path_status": real_d17_path_status,
        "real_provider_status": real_provider_status, "real_model_status": real_model_status,
        "g1_g2_status": g1_g2_status, "evidence_use_status": evidence_use_status, "declaration": declaration,
    }


def _record_root_from_facts(*, request_id: str, result_id: str, receipt_id: str, receipt_hash: str, input_hash: str, input_length: int, config_hash: str, config_length: int, outcome: str, raw_hash: str | None, raw_length: int | None, provider_error: dict[str, object] | None, synthetic_mock_only: bool, external_egress_allowed: bool, model_loaded: bool, remote_called: bool, real_d17_path_status: str, real_provider_status: str, real_model_status: str, g1_g2_status: str, evidence_use_status: str, declaration: str, schema_version: str) -> dict[str, object]:
    return {
        "schema_version": schema_version, "request_id": request_id, "result_id": result_id,
        "visibility_receipt_id": receipt_id, "visibility_receipt_sha256": receipt_hash,
        "synthetic_input_sha256": input_hash, "synthetic_input_byte_length": input_length,
        "mock_serializer_config_sha256": config_hash, "mock_serializer_config_byte_length": config_length,
        "outcome": outcome, "raw_response_sha256": raw_hash, "raw_response_byte_length": raw_length,
        "provider_error": provider_error,
        "synthetic_mock_only": synthetic_mock_only, "external_egress_allowed": external_egress_allowed,
        "model_loaded": model_loaded, "remote_called": remote_called, "real_d17_path_status": real_d17_path_status,
        "real_provider_status": real_provider_status, "real_model_status": real_model_status,
        "g1_g2_status": g1_g2_status, "evidence_use_status": evidence_use_status, "declaration": declaration,
    }

def _fixed_declaration_valid(value: object, *, kind: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"synthetic/mock Provider {kind} boolean declaration is invalid")


def _execution_declarations_valid(
    *,
    synthetic_mock_only: object,
    external_egress_allowed: object,
    model_loaded: object,
    remote_called: object,
    real_d17_path_status: object,
    real_provider_status: object,
    real_model_status: object,
    g1_g2_status: object,
    evidence_use_status: object,
    declaration: object,
    kind: str,
) -> None:
    for value in (synthetic_mock_only, external_egress_allowed, model_loaded, remote_called):
        _fixed_declaration_valid(value, kind=kind)
    if synthetic_mock_only is not True or external_egress_allowed is not False:
        raise ValueError(f"synthetic/mock Provider {kind} cannot permit external egress")
    if model_loaded is not False or remote_called is not False:
        raise ValueError(f"synthetic/mock Provider {kind} cannot load a model or call remotely")
    if (
        real_d17_path_status != _NOT_EXECUTED
        or real_provider_status != _NOT_EXECUTED
        or real_model_status != _NOT_EXECUTED
        or g1_g2_status != "not_assigned"
        or evidence_use_status != "not_evaluated"
        or declaration != _DECLARATION
    ):
        raise ValueError(f"synthetic/mock Provider {kind} execution declaration is invalid")


@dataclass(frozen=True)
class SyntheticMockProviderRequest:
    """Receipt-bound input; `to_dict` intentionally contains no input/config bytes."""

    visibility_receipt: SyntheticMockVisibilityReceipt
    synthetic_input_bytes: bytes = field(repr=False)
    mock_serializer_config_bytes: bytes = field(repr=False)
    request_id: str
    synthetic_input_sha256: str
    synthetic_input_byte_length: int
    mock_serializer_config_sha256: str
    mock_serializer_config_byte_length: int
    visibility_receipt_sha256: str
    synthetic_mock_only: bool = True
    external_egress_allowed: bool = False
    model_loaded: bool = False
    remote_called: bool = False
    real_d17_path_status: str = _NOT_EXECUTED
    real_provider_status: str = _NOT_EXECUTED
    real_model_status: str = _NOT_EXECUTED
    g1_g2_status: str = "not_assigned"
    evidence_use_status: str = "not_evaluated"
    declaration: str = _DECLARATION
    schema_version: str = MOCK_PROVIDER_REQUEST_SCHEMA_VERSION

    @classmethod
    def from_mock_bytes(cls, visibility_receipt: SyntheticMockVisibilityReceipt, *, synthetic_input_bytes: bytes, mock_serializer_config_bytes: bytes) -> "SyntheticMockProviderRequest":
        if not isinstance(visibility_receipt, SyntheticMockVisibilityReceipt):
            raise MockProviderRequestError("visibility_receipt_invalid")
        _require_bytes(synthetic_input_bytes, "synthetic_input_invalid")
        _require_bytes(mock_serializer_config_bytes, "mock_serializer_config_invalid")
        try:
            visibility_receipt.validate()
        except (TypeError, ValueError) as error:
            raise MockProviderRequestError("visibility_receipt_invalid") from error
        input_hash, config_hash = _sha256(synthetic_input_bytes), _sha256(mock_serializer_config_bytes)
        if visibility_receipt.synthetic_input_sha256 != input_hash or visibility_receipt.synthetic_input_byte_length != len(synthetic_input_bytes):
            raise MockProviderRequestError("synthetic_input_receipt_mismatch")
        if visibility_receipt.mock_serializer_config_sha256 != config_hash:
            raise MockProviderRequestError("mock_serializer_config_receipt_mismatch")
        root = cls._root_for(
            receipt=visibility_receipt, input_hash=input_hash, input_length=len(synthetic_input_bytes),
            config_hash=config_hash, config_length=len(mock_serializer_config_bytes), receipt_hash=visibility_receipt.sha256(),
            synthetic_mock_only=True, external_egress_allowed=False, model_loaded=False, remote_called=False,
            real_d17_path_status=_NOT_EXECUTED, real_provider_status=_NOT_EXECUTED, real_model_status=_NOT_EXECUTED,
            g1_g2_status="not_assigned", evidence_use_status="not_evaluated", declaration=_DECLARATION,
            schema_version=MOCK_PROVIDER_REQUEST_SCHEMA_VERSION,
        )
        return cls(visibility_receipt, synthetic_input_bytes, mock_serializer_config_bytes, _id("mock-request-", root), input_hash, len(synthetic_input_bytes), config_hash, len(mock_serializer_config_bytes), visibility_receipt.sha256())

    @staticmethod
    def _root_for(*, receipt: SyntheticMockVisibilityReceipt, input_hash: str, input_length: int, config_hash: str, config_length: int, receipt_hash: str, synthetic_mock_only: bool, external_egress_allowed: bool, model_loaded: bool, remote_called: bool, real_d17_path_status: str, real_provider_status: str, real_model_status: str, g1_g2_status: str, evidence_use_status: str, declaration: str, schema_version: str) -> dict[str, object]:
        return _request_root_from_facts(
            receipt_id=receipt.receipt_id, receipt_hash=receipt_hash, input_hash=input_hash,
            input_length=input_length, config_hash=config_hash, config_length=config_length,
            synthetic_mock_only=synthetic_mock_only, external_egress_allowed=external_egress_allowed,
            model_loaded=model_loaded, remote_called=remote_called,
            real_d17_path_status=real_d17_path_status, real_provider_status=real_provider_status,
            real_model_status=real_model_status, g1_g2_status=g1_g2_status,
            evidence_use_status=evidence_use_status, declaration=declaration, schema_version=schema_version,
        )

    def _root(self) -> dict[str, object]:
        return self._root_for(
            receipt=self.visibility_receipt, input_hash=self.synthetic_input_sha256, input_length=self.synthetic_input_byte_length,
            config_hash=self.mock_serializer_config_sha256, config_length=self.mock_serializer_config_byte_length,
            receipt_hash=self.visibility_receipt_sha256, synthetic_mock_only=self.synthetic_mock_only,
            external_egress_allowed=self.external_egress_allowed, model_loaded=self.model_loaded, remote_called=self.remote_called,
            real_d17_path_status=self.real_d17_path_status, real_provider_status=self.real_provider_status,
            real_model_status=self.real_model_status, g1_g2_status=self.g1_g2_status,
            evidence_use_status=self.evidence_use_status, declaration=self.declaration, schema_version=self.schema_version,
        )

    def validate(self) -> None:
        if self.schema_version != MOCK_PROVIDER_REQUEST_SCHEMA_VERSION or not isinstance(self.visibility_receipt, SyntheticMockVisibilityReceipt):
            raise ValueError("synthetic/mock Provider request schema or receipt is invalid")
        self.visibility_receipt.validate()
        _require_bytes(self.synthetic_input_bytes, "synthetic_input_invalid")
        _require_bytes(self.mock_serializer_config_bytes, "mock_serializer_config_invalid")
        _require_hash(self.synthetic_input_sha256, "synthetic input hash")
        _require_length(self.synthetic_input_byte_length, "synthetic input byte length")
        _require_hash(self.mock_serializer_config_sha256, "mock serializer/config hash")
        _require_length(self.mock_serializer_config_byte_length, "mock serializer/config byte length")
        _require_hash(self.visibility_receipt_sha256, "visibility receipt hash")
        if self.synthetic_input_sha256 != _sha256(self.synthetic_input_bytes) or self.synthetic_input_byte_length != len(self.synthetic_input_bytes):
            raise ValueError("synthetic/mock Provider request input binding is invalid")
        if self.mock_serializer_config_sha256 != _sha256(self.mock_serializer_config_bytes) or self.mock_serializer_config_byte_length != len(self.mock_serializer_config_bytes):
            raise ValueError("synthetic/mock Provider request config binding is invalid")
        if self.visibility_receipt_sha256 != self.visibility_receipt.sha256() or self.visibility_receipt.synthetic_input_sha256 != self.synthetic_input_sha256 or self.visibility_receipt.synthetic_input_byte_length != self.synthetic_input_byte_length or self.visibility_receipt.mock_serializer_config_sha256 != self.mock_serializer_config_sha256:
            raise ValueError("synthetic/mock Provider request receipt binding is invalid")
        _execution_declarations_valid(synthetic_mock_only=self.synthetic_mock_only, external_egress_allowed=self.external_egress_allowed, model_loaded=self.model_loaded, remote_called=self.remote_called, real_d17_path_status=self.real_d17_path_status, real_provider_status=self.real_provider_status, real_model_status=self.real_model_status, g1_g2_status=self.g1_g2_status, evidence_use_status=self.evidence_use_status, declaration=self.declaration, kind="request")
        _require_full_id(self.request_id, "mock-request-", "synthetic/mock Provider request ID")
        if self.request_id != _id("mock-request-", self._root()):
            raise ValueError("synthetic/mock Provider request ID does not bind its root")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"request_id": self.request_id, **self._root()}

@dataclass(frozen=True)
class ScriptedMockProviderOutcome:
    """Exactly one caller-configured success raw response or runtime error."""

    outcome: str
    raw_response: ProviderRawResponse | None = field(default=None, repr=False)
    provider_error: ProviderError | None = None

    @classmethod
    def success(cls, raw_response: ProviderRawResponse) -> "ScriptedMockProviderOutcome":
        return cls("success", raw_response=raw_response)

    @classmethod
    def provider_runtime_error(cls, code: str) -> "ScriptedMockProviderOutcome":
        if code not in _RUNTIME_RETRYABLE:
            raise ValueError("unsupported synthetic/mock Provider runtime error code")
        return cls("provider_runtime_error", provider_error=ProviderError(code, "provider_runtime", "transport", _RUNTIME_RETRYABLE[code]))

    def validate(self) -> None:
        if self.outcome == "success":
            if not isinstance(self.raw_response, ProviderRawResponse) or self.provider_error is not None:
                raise ValueError("synthetic/mock Provider success outcome must have only raw response")
            return
        if self.outcome != "provider_runtime_error" or self.raw_response is not None or not isinstance(self.provider_error, ProviderError):
            raise ValueError("synthetic/mock Provider scripted outcome is invalid")
        self.provider_error.validate()
        expected = _RUNTIME_RETRYABLE.get(self.provider_error.code)
        if expected is None or self.provider_error.stage != "provider_runtime" or self.provider_error.phase != "transport" or self.provider_error.retryable is not expected:
            raise ValueError("synthetic/mock Provider runtime error is invalid")


@dataclass(frozen=True)
class SyntheticMockProviderResult:
    """Mutually exclusive exact raw response or ProviderError, without payload serialization."""

    outcome: str
    raw_response: ProviderRawResponse | None = field(repr=False)
    provider_error: ProviderError | None
    result_id: str
    request_id: str
    synthetic_mock_only: bool = True
    external_egress_allowed: bool = False
    model_loaded: bool = False
    remote_called: bool = False
    real_d17_path_status: str = _NOT_EXECUTED
    real_provider_status: str = _NOT_EXECUTED
    real_model_status: str = _NOT_EXECUTED
    g1_g2_status: str = "not_assigned"
    evidence_use_status: str = "not_evaluated"
    declaration: str = _DECLARATION
    schema_version: str = MOCK_PROVIDER_RESULT_SCHEMA_VERSION

    @classmethod
    def from_script(cls, request: SyntheticMockProviderRequest, scripted_outcome: ScriptedMockProviderOutcome) -> "SyntheticMockProviderResult":
        if not isinstance(request, SyntheticMockProviderRequest) or not isinstance(scripted_outcome, ScriptedMockProviderOutcome):
            raise TypeError("synthetic/mock Provider request and scripted outcome are required")
        request.validate()
        scripted_outcome.validate()
        raw_metadata = scripted_outcome.raw_response.to_dict() if scripted_outcome.raw_response is not None else None
        error_payload = scripted_outcome.provider_error.to_dict() if scripted_outcome.provider_error is not None else None
        root = cls._root_for(
            request_id=request.request_id, outcome=scripted_outcome.outcome, raw_metadata=raw_metadata, provider_error=error_payload,
            synthetic_mock_only=True, external_egress_allowed=False, model_loaded=False, remote_called=False,
            real_d17_path_status=_NOT_EXECUTED, real_provider_status=_NOT_EXECUTED, real_model_status=_NOT_EXECUTED,
            g1_g2_status="not_assigned", evidence_use_status="not_evaluated", declaration=_DECLARATION,
            schema_version=MOCK_PROVIDER_RESULT_SCHEMA_VERSION,
        )
        return cls(scripted_outcome.outcome, scripted_outcome.raw_response, scripted_outcome.provider_error, _id("mock-result-", root), request.request_id)

    @staticmethod
    def _root_for(*, request_id: str, outcome: str, raw_metadata: dict[str, Any] | None, provider_error: dict[str, Any] | None, synthetic_mock_only: bool, external_egress_allowed: bool, model_loaded: bool, remote_called: bool, real_d17_path_status: str, real_provider_status: str, real_model_status: str, g1_g2_status: str, evidence_use_status: str, declaration: str, schema_version: str) -> dict[str, object]:
        return _result_root_from_facts(
            request_id=request_id, outcome=outcome, raw_metadata=raw_metadata, provider_error=provider_error,
            synthetic_mock_only=synthetic_mock_only, external_egress_allowed=external_egress_allowed,
            model_loaded=model_loaded, remote_called=remote_called,
            real_d17_path_status=real_d17_path_status, real_provider_status=real_provider_status,
            real_model_status=real_model_status, g1_g2_status=g1_g2_status,
            evidence_use_status=evidence_use_status, declaration=declaration, schema_version=schema_version,
        )

    def _root(self) -> dict[str, object]:
        return self._root_for(
            request_id=self.request_id, outcome=self.outcome,
            raw_metadata=self.raw_response.to_dict() if self.raw_response is not None else None,
            provider_error=self.provider_error.to_dict() if self.provider_error is not None else None,
            synthetic_mock_only=self.synthetic_mock_only, external_egress_allowed=self.external_egress_allowed,
            model_loaded=self.model_loaded, remote_called=self.remote_called, real_d17_path_status=self.real_d17_path_status,
            real_provider_status=self.real_provider_status, real_model_status=self.real_model_status,
            g1_g2_status=self.g1_g2_status, evidence_use_status=self.evidence_use_status,
            declaration=self.declaration, schema_version=self.schema_version,
        )

    def validate(self) -> None:
        if self.schema_version != MOCK_PROVIDER_RESULT_SCHEMA_VERSION:
            raise ValueError("unsupported synthetic/mock Provider result schema")
        _require_full_id(self.request_id, "mock-request-", "synthetic/mock Provider result request ID")
        if self.outcome == "success":
            if not isinstance(self.raw_response, ProviderRawResponse) or self.provider_error is not None:
                raise ValueError("synthetic/mock Provider result success/error fields are inconsistent")
        elif self.outcome == "provider_runtime_error":
            if self.raw_response is not None or not isinstance(self.provider_error, ProviderError):
                raise ValueError("synthetic/mock Provider result success/error fields are inconsistent")
            self.provider_error.validate()
            expected = _RUNTIME_RETRYABLE.get(self.provider_error.code)
            if expected is None or self.provider_error.stage != "provider_runtime" or self.provider_error.phase != "transport" or self.provider_error.retryable is not expected:
                raise ValueError("synthetic/mock Provider result runtime error is invalid")
        else:
            raise ValueError("synthetic/mock Provider result outcome is invalid")
        _execution_declarations_valid(synthetic_mock_only=self.synthetic_mock_only, external_egress_allowed=self.external_egress_allowed, model_loaded=self.model_loaded, remote_called=self.remote_called, real_d17_path_status=self.real_d17_path_status, real_provider_status=self.real_provider_status, real_model_status=self.real_model_status, g1_g2_status=self.g1_g2_status, evidence_use_status=self.evidence_use_status, declaration=self.declaration, kind="result")
        if self.result_id != _id("mock-result-", self._root()):
            raise ValueError("synthetic/mock Provider result ID does not bind its root")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"result_id": self.result_id, **self._root()}

@dataclass(frozen=True)
class SyntheticMockProviderInvocationRecord:
    """Payload-free record; it contains only safe identifiers, hashes, and metadata."""

    record_id: str
    request_id: str
    result_id: str
    visibility_receipt_id: str
    visibility_receipt_sha256: str
    synthetic_input_sha256: str
    synthetic_input_byte_length: int
    mock_serializer_config_sha256: str
    mock_serializer_config_byte_length: int
    outcome: str
    raw_response_sha256: str | None
    raw_response_byte_length: int | None
    provider_error: ProviderError | None
    synthetic_mock_only: bool = True
    external_egress_allowed: bool = False
    model_loaded: bool = False
    remote_called: bool = False
    real_d17_path_status: str = _NOT_EXECUTED
    real_provider_status: str = _NOT_EXECUTED
    real_model_status: str = _NOT_EXECUTED
    g1_g2_status: str = "not_assigned"
    evidence_use_status: str = "not_evaluated"
    declaration: str = _DECLARATION
    schema_version: str = MOCK_PROVIDER_INVOCATION_RECORD_SCHEMA_VERSION

    @classmethod
    def from_request_result(cls, request: SyntheticMockProviderRequest, result: SyntheticMockProviderResult) -> "SyntheticMockProviderInvocationRecord":
        if not isinstance(request, SyntheticMockProviderRequest) or not isinstance(result, SyntheticMockProviderResult):
            raise TypeError("synthetic/mock Provider request and result are required")
        request.validate()
        result.validate()
        if result.request_id != request.request_id:
            raise ValueError("synthetic/mock Provider result does not bind the request")
        raw_hash = result.raw_response.sha256 if result.raw_response is not None else None
        raw_length = len(result.raw_response.raw_bytes) if result.raw_response is not None else None
        root = cls._root_for(
            request_id=request.request_id, result_id=result.result_id, receipt_id=request.visibility_receipt.receipt_id,
            receipt_hash=request.visibility_receipt_sha256, input_hash=request.synthetic_input_sha256,
            input_length=request.synthetic_input_byte_length, config_hash=request.mock_serializer_config_sha256,
            config_length=request.mock_serializer_config_byte_length, outcome=result.outcome, raw_hash=raw_hash,
            raw_length=raw_length, provider_error=result.provider_error,
            synthetic_mock_only=True, external_egress_allowed=False, model_loaded=False, remote_called=False,
            real_d17_path_status=_NOT_EXECUTED, real_provider_status=_NOT_EXECUTED, real_model_status=_NOT_EXECUTED,
            g1_g2_status="not_assigned", evidence_use_status="not_evaluated", declaration=_DECLARATION,
            schema_version=MOCK_PROVIDER_INVOCATION_RECORD_SCHEMA_VERSION,
        )
        return cls(
            _id("mock-invocation-", root), request.request_id, result.result_id, request.visibility_receipt.receipt_id,
            request.visibility_receipt_sha256, request.synthetic_input_sha256, request.synthetic_input_byte_length,
            request.mock_serializer_config_sha256, request.mock_serializer_config_byte_length, result.outcome,
            raw_hash, raw_length, result.provider_error,
        )

    @staticmethod
    def _root_for(*, request_id: str, result_id: str, receipt_id: str, receipt_hash: str, input_hash: str, input_length: int, config_hash: str, config_length: int, outcome: str, raw_hash: str | None, raw_length: int | None, provider_error: ProviderError | None, synthetic_mock_only: bool, external_egress_allowed: bool, model_loaded: bool, remote_called: bool, real_d17_path_status: str, real_provider_status: str, real_model_status: str, g1_g2_status: str, evidence_use_status: str, declaration: str, schema_version: str) -> dict[str, object]:
        return _record_root_from_facts(
            request_id=request_id, result_id=result_id, receipt_id=receipt_id, receipt_hash=receipt_hash,
            input_hash=input_hash, input_length=input_length, config_hash=config_hash, config_length=config_length,
            outcome=outcome, raw_hash=raw_hash, raw_length=raw_length,
            provider_error=provider_error.to_dict() if provider_error is not None else None,
            synthetic_mock_only=synthetic_mock_only, external_egress_allowed=external_egress_allowed,
            model_loaded=model_loaded, remote_called=remote_called, real_d17_path_status=real_d17_path_status,
            real_provider_status=real_provider_status, real_model_status=real_model_status,
            g1_g2_status=g1_g2_status, evidence_use_status=evidence_use_status,
            declaration=declaration, schema_version=schema_version,
        )

    def _root(self) -> dict[str, object]:
        return self._root_for(
            request_id=self.request_id, result_id=self.result_id, receipt_id=self.visibility_receipt_id,
            receipt_hash=self.visibility_receipt_sha256, input_hash=self.synthetic_input_sha256,
            input_length=self.synthetic_input_byte_length, config_hash=self.mock_serializer_config_sha256,
            config_length=self.mock_serializer_config_byte_length, outcome=self.outcome,
            raw_hash=self.raw_response_sha256, raw_length=self.raw_response_byte_length, provider_error=self.provider_error,
            synthetic_mock_only=self.synthetic_mock_only, external_egress_allowed=self.external_egress_allowed,
            model_loaded=self.model_loaded, remote_called=self.remote_called, real_d17_path_status=self.real_d17_path_status,
            real_provider_status=self.real_provider_status, real_model_status=self.real_model_status,
            g1_g2_status=self.g1_g2_status, evidence_use_status=self.evidence_use_status,
            declaration=self.declaration, schema_version=self.schema_version,
        )

    def validate(self) -> None:
        if self.schema_version != MOCK_PROVIDER_INVOCATION_RECORD_SCHEMA_VERSION:
            raise ValueError("unsupported synthetic/mock Provider invocation record schema")
        _require_full_id(self.request_id, "mock-request-", "synthetic/mock Provider record request ID")
        _require_full_id(self.result_id, "mock-result-", "synthetic/mock Provider record result ID")
        _require_full_id(self.visibility_receipt_id, "mock-receipt-", "synthetic/mock Provider record receipt ID")
        _require_hash(self.visibility_receipt_sha256, "visibility receipt hash")
        _require_hash(self.synthetic_input_sha256, "synthetic input hash")
        _require_length(self.synthetic_input_byte_length, "synthetic input byte length")
        _require_hash(self.mock_serializer_config_sha256, "mock serializer/config hash")
        _require_length(self.mock_serializer_config_byte_length, "mock serializer/config byte length")
        if self.outcome == "success":
            _require_hash(self.raw_response_sha256, "raw response hash")
            _require_length(self.raw_response_byte_length, "raw response byte length")
            if self.provider_error is not None:
                raise ValueError("synthetic/mock Provider record success/error fields are inconsistent")
            raw_metadata: dict[str, object] | None = {
                "schema_version": PROVIDER_RAW_RESPONSE_SCHEMA_VERSION,
                "byte_sha256": self.raw_response_sha256,
                "byte_length": self.raw_response_byte_length,
            }
            provider_error_payload: dict[str, object] | None = None
        elif self.outcome == "provider_runtime_error":
            if self.raw_response_sha256 is not None or self.raw_response_byte_length is not None or not isinstance(self.provider_error, ProviderError):
                raise ValueError("synthetic/mock Provider record success/error fields are inconsistent")
            self.provider_error.validate()
            expected = _RUNTIME_RETRYABLE.get(self.provider_error.code)
            if expected is None or self.provider_error.stage != "provider_runtime" or self.provider_error.phase != "transport" or self.provider_error.retryable is not expected:
                raise ValueError("synthetic/mock Provider record runtime error is invalid")
            raw_metadata = None
            provider_error_payload = self.provider_error.to_dict()
        else:
            raise ValueError("synthetic/mock Provider record outcome is invalid")
        _execution_declarations_valid(synthetic_mock_only=self.synthetic_mock_only, external_egress_allowed=self.external_egress_allowed, model_loaded=self.model_loaded, remote_called=self.remote_called, real_d17_path_status=self.real_d17_path_status, real_provider_status=self.real_provider_status, real_model_status=self.real_model_status, g1_g2_status=self.g1_g2_status, evidence_use_status=self.evidence_use_status, declaration=self.declaration, kind="record")
        expected_request_root = _request_root_from_facts(
            receipt_id=self.visibility_receipt_id, receipt_hash=self.visibility_receipt_sha256,
            input_hash=self.synthetic_input_sha256, input_length=self.synthetic_input_byte_length,
            config_hash=self.mock_serializer_config_sha256, config_length=self.mock_serializer_config_byte_length,
            synthetic_mock_only=self.synthetic_mock_only, external_egress_allowed=self.external_egress_allowed,
            model_loaded=self.model_loaded, remote_called=self.remote_called,
            real_d17_path_status=self.real_d17_path_status, real_provider_status=self.real_provider_status,
            real_model_status=self.real_model_status, g1_g2_status=self.g1_g2_status,
            evidence_use_status=self.evidence_use_status, declaration=self.declaration,
            schema_version=MOCK_PROVIDER_REQUEST_SCHEMA_VERSION,
        )
        expected_request_id = _id("mock-request-", expected_request_root)
        if self.request_id != expected_request_id:
            raise ValueError("synthetic/mock Provider record request ID does not bind request facts")
        expected_result_root = _result_root_from_facts(
            request_id=expected_request_id, outcome=self.outcome, raw_metadata=raw_metadata,
            provider_error=provider_error_payload, synthetic_mock_only=self.synthetic_mock_only,
            external_egress_allowed=self.external_egress_allowed, model_loaded=self.model_loaded,
            remote_called=self.remote_called, real_d17_path_status=self.real_d17_path_status,
            real_provider_status=self.real_provider_status, real_model_status=self.real_model_status,
            g1_g2_status=self.g1_g2_status, evidence_use_status=self.evidence_use_status,
            declaration=self.declaration, schema_version=MOCK_PROVIDER_RESULT_SCHEMA_VERSION,
        )
        expected_result_id = _id("mock-result-", expected_result_root)
        if self.result_id != expected_result_id:
            raise ValueError("synthetic/mock Provider record result ID does not bind result facts")
        expected_record_root = _record_root_from_facts(
            request_id=expected_request_id, result_id=expected_result_id, receipt_id=self.visibility_receipt_id,
            receipt_hash=self.visibility_receipt_sha256, input_hash=self.synthetic_input_sha256,
            input_length=self.synthetic_input_byte_length, config_hash=self.mock_serializer_config_sha256,
            config_length=self.mock_serializer_config_byte_length, outcome=self.outcome,
            raw_hash=self.raw_response_sha256, raw_length=self.raw_response_byte_length,
            provider_error=provider_error_payload, synthetic_mock_only=self.synthetic_mock_only,
            external_egress_allowed=self.external_egress_allowed, model_loaded=self.model_loaded,
            remote_called=self.remote_called, real_d17_path_status=self.real_d17_path_status,
            real_provider_status=self.real_provider_status, real_model_status=self.real_model_status,
            g1_g2_status=self.g1_g2_status, evidence_use_status=self.evidence_use_status,
            declaration=self.declaration, schema_version=MOCK_PROVIDER_INVOCATION_RECORD_SCHEMA_VERSION,
        )
        if self.record_id != _id("mock-invocation-", expected_record_root):
            raise ValueError("synthetic/mock Provider invocation record ID does not bind its root")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"record_id": self.record_id, **self._root()}


@dataclass(frozen=True)
class MockSmokePageSpecProvider:
    """One scripted outcome, with neither model execution nor transport code."""

    scripted_outcome: ScriptedMockProviderOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.scripted_outcome, ScriptedMockProviderOutcome):
            raise TypeError("synthetic/mock Provider requires exactly one scripted outcome")
        self.scripted_outcome.validate()

    def invoke(self, request: SyntheticMockProviderRequest) -> tuple[SyntheticMockProviderResult, SyntheticMockProviderInvocationRecord]:
        if not isinstance(request, SyntheticMockProviderRequest):
            raise TypeError("synthetic/mock Provider request is required")
        request.validate()
        result = SyntheticMockProviderResult.from_script(request, self.scripted_outcome)
        return result, SyntheticMockProviderInvocationRecord.from_request_result(request, result)