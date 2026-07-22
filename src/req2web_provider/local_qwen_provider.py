"""Local-only Local Qwen preparation and fail-closed invocation seam."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, NoReturn

from req2web_agent.schema import AgentContextBundle
from req2web_provider.d17_audit import D17Path3TierAPreInvocationAuditRecord
from req2web_provider.d17_input_view import D17Path3SelectedInput
from req2web_provider.d17_manifest import (
    D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
    D17Path3FieldPolicy,
    D17Path3TierAManifest,
)
from req2web_provider.d17_serializer import D17Path3LocalRequestArtifact


LOCAL_QWEN_PROVIDER_PREPARATION_SCHEMA_VERSION = "req2web.provider.local_qwen.preparation.v1"
LOCAL_QWEN_PROVIDER_ERROR_ENVELOPE_SCHEMA_VERSION = "req2web.provider.local_qwen.error.v1"

_AUTHORIZATION = "tier_a_local_implementation_only"
_PATH_3 = "path_3"
_CANDIDATE_MODEL_FAMILY = "Qwen3.5-9B"
_TIER_B_UNAPPROVED = "tier_b_unapproved"
_BINDING_CONTENT_STATUS = "identity_hash_and_length_only"

_SAFE_MESSAGES = {
    "preparation_schema_invalid": "Local Qwen preparation schema is invalid.",
    "preparation_exact_keys_invalid": "Local Qwen preparation field shape is invalid.",
    "preparation_identity_invalid": "Local Qwen preparation identity is invalid.",
    "preparation_manifest_binding_invalid": "Local Qwen preparation manifest binding is invalid.",
    "preparation_selection_binding_invalid": "Local Qwen preparation selection binding is invalid.",
    "preparation_request_binding_invalid": "Local Qwen preparation request binding is invalid.",
    "preparation_audit_binding_invalid": "Local Qwen preparation audit binding is invalid.",
    "preparation_artifact_binding_invalid": "Local Qwen preparation artifact binding is invalid.",
    "preparation_local_boundary_invalid": "Local Qwen preparation local boundary is invalid.",
    "preparation_tier_b_declaration_invalid": "Local Qwen Tier B declaration is invalid.",
    "preparation_bytes_invalid": "Local Qwen preparation canonical bytes are invalid.",
    "preparation_live_cross_binding_invalid": "Local Qwen preparation live binding is invalid.",
    "tier_b_unapproved": "Local Qwen invocation is blocked because Tier B is unapproved.",
}


class _DuplicateJsonKey(ValueError):
    """Raised only while rejecting duplicate JSON keys."""


@dataclass(frozen=True)
class LocalQwenProviderErrorEnvelope:
    """Fixed payload-free error metadata."""

    code: str
    schema_version: str = LOCAL_QWEN_PROVIDER_ERROR_ENVELOPE_SCHEMA_VERSION
    stage: str = "d17"
    payload_disclosure: str = "none"
    retryable: bool = False

    def to_dict(self) -> dict[str, object]:
        if self.code not in _SAFE_MESSAGES:
            raise ValueError("Local Qwen error envelope code is invalid")
        if (
            self.schema_version,
            self.stage,
            self.payload_disclosure,
            self.retryable,
        ) != (LOCAL_QWEN_PROVIDER_ERROR_ENVELOPE_SCHEMA_VERSION, "d17", "none", False):
            raise ValueError("Local Qwen error envelope is invalid")
        return {
            "schema_version": self.schema_version,
            "code": self.code,
            "stage": self.stage,
            "payload_disclosure": self.payload_disclosure,
            "retryable": self.retryable,
        }


class LocalQwenProviderPreparationValidationError(ValueError):
    """Stable fail-closed error without caller payload disclosure."""

    def __init__(self, code: str) -> None:
        if code not in _SAFE_MESSAGES:
            raise ValueError("Local Qwen preparation error code is invalid")
        super().__init__(_SAFE_MESSAGES[code])
        self.code = code
        self.envelope = LocalQwenProviderErrorEnvelope(code)


class LocalQwenProviderInvocationBlockedError(RuntimeError):
    """Fixed error for the deliberately unavailable invocation seam."""

    def __init__(self) -> None:
        super().__init__(_SAFE_MESSAGES["tier_b_unapproved"])
        self.code = "tier_b_unapproved"
        self.envelope = LocalQwenProviderErrorEnvelope(self.code)


def _fail(code: str) -> NoReturn:
    raise LocalQwenProviderPreparationValidationError(code)


def _canonical_bytes(payload: object) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        _fail("preparation_schema_invalid")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity(prefix: str, root: Mapping[str, object]) -> str:
    return prefix + _sha256(_canonical_bytes(root))


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _constant(_: str) -> object:
    raise ValueError("non-finite JSON constants are forbidden")


def _loads(raw: bytes) -> object:
    if not isinstance(raw, bytes) or raw.startswith(b"\xef\xbb\xbf"):
        _fail("preparation_bytes_invalid")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError):
        _fail("preparation_bytes_invalid")


def _mapping(value: object, keys: tuple[str, ...], *, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        _fail(code)
    return dict(value)


def _text(value: object, *, code: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    return value


def _hex(value: object, *, code: str) -> str:
    value = _text(value, code=code)
    if len(value) != 64 or any(item not in "0123456789abcdef" for item in value):
        _fail(code)
    return value


def _length(value: object, *, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(code)
    return value


def _bool(value: object, *, code: str) -> bool:
    if not isinstance(value, bool):
        _fail(code)
    return value


def _prefixed_identity(value: object, prefix: str, *, code: str) -> str:
    value = _text(value, code=code)
    digest = value.removeprefix(prefix)
    if value == digest:
        _fail(code)
    _hex(digest, code=code)
    return value


def _validated_manifest(manifest: object) -> D17Path3TierAManifest:
    if not isinstance(manifest, D17Path3TierAManifest):
        _fail("preparation_manifest_binding_invalid")
    try:
        manifest.validate()
    except (TypeError, ValueError):
        _fail("preparation_manifest_binding_invalid")
    return manifest


def _validated_selected(context: AgentContextBundle, manifest: D17Path3TierAManifest, selected: object) -> D17Path3SelectedInput:
    if not isinstance(selected, D17Path3SelectedInput):
        _fail("preparation_selection_binding_invalid")
    try:
        selected.validate_against(context, manifest)
    except (TypeError, ValueError):
        _fail("preparation_selection_binding_invalid")
    return selected


def _validated_request(
    context: AgentContextBundle,
    manifest: D17Path3TierAManifest,
    selected: D17Path3SelectedInput,
    local_request: object,
) -> D17Path3LocalRequestArtifact:
    if not isinstance(local_request, D17Path3LocalRequestArtifact):
        _fail("preparation_request_binding_invalid")
    try:
        local_request.validate_against(context, manifest)
    except (TypeError, ValueError):
        _fail("preparation_request_binding_invalid")
    if (
        local_request.local_only is not True
        or local_request.not_sent is not True
        or local_request.external_egress_allowed is not False
        or local_request.provider_invocation_state != "not_invoked"
        or local_request.transport_state != "not_sent"
    ):
        _fail("preparation_request_binding_invalid")
    artifact = local_request.input_view_artifact
    if (
        artifact.selection_record.canonical_bytes() != selected.selection_record.canonical_bytes()
        or artifact.provider_visible_input.canonical_bytes() != selected.provider_visible_input.canonical_bytes()
    ):
        _fail("preparation_request_binding_invalid")
    return local_request


def _validated_audit(
    context: AgentContextBundle,
    manifest: D17Path3TierAManifest,
    selected: D17Path3SelectedInput,
    local_request: D17Path3LocalRequestArtifact,
    pre_invocation_audit: object,
) -> D17Path3TierAPreInvocationAuditRecord:
    if not isinstance(pre_invocation_audit, D17Path3TierAPreInvocationAuditRecord):
        _fail("preparation_audit_binding_invalid")
    try:
        pre_invocation_audit.validate_against(context, manifest, selected, local_request)
    except (TypeError, ValueError):
        _fail("preparation_audit_binding_invalid")
    if (
        pre_invocation_audit.local_only is not True
        or pre_invocation_audit.not_sent is not True
        or pre_invocation_audit.external_egress_allowed is not False
        or pre_invocation_audit.provider_invocation_state != "not_invoked"
    ):
        _fail("preparation_audit_binding_invalid")
    return pre_invocation_audit


@dataclass(frozen=True)
class LocalQwenProviderArtifactBinding:
    """Payload-free identity, digest, and length binding."""

    artifact_kind: str
    artifact_id: str
    sha256: str
    byte_length: int
    content_status: str

    @classmethod
    def from_bytes(cls, artifact_kind: str, artifact_id: str, raw: bytes) -> "LocalQwenProviderArtifactBinding":
        result = cls(artifact_kind, artifact_id, _sha256(raw), len(raw), _BINDING_CONTENT_STATUS)
        result.validate(expected_kind=artifact_kind)
        return result

    @classmethod
    def from_dict(cls, payload: object) -> "LocalQwenProviderArtifactBinding":
        data = _mapping(payload, ("artifact_kind", "artifact_id", "sha256", "byte_length", "content_status"), code="preparation_exact_keys_invalid")
        result = cls(
            _text(data["artifact_kind"], code="preparation_artifact_binding_invalid"),
            _text(data["artifact_id"], code="preparation_artifact_binding_invalid"),
            _hex(data["sha256"], code="preparation_artifact_binding_invalid"),
            _length(data["byte_length"], code="preparation_artifact_binding_invalid"),
            _text(data["content_status"], code="preparation_artifact_binding_invalid"),
        )
        result.validate()
        return result

    def validate(self, *, expected_kind: str | None = None) -> None:
        prefixes = {
            "input_selection_record": "d17-input-selection-",
            "provider_visible_input_view": "d17-input-view-",
            "input_view_artifact": "d17-input-view-artifact-",
            "prompt_artifact": "d17-prompt-artifact-",
            "config_artifact": "d17-config-artifact-",
            "local_request_artifact": "d17-local-request-",
            "pre_invocation_audit_record": "d17-pre-invocation-audit-",
        }
        if expected_kind is not None and self.artifact_kind != expected_kind:
            _fail("preparation_artifact_binding_invalid")
        prefix = prefixes.get(self.artifact_kind)
        if prefix is None:
            _fail("preparation_artifact_binding_invalid")
        _prefixed_identity(self.artifact_id, prefix, code="preparation_artifact_binding_invalid")
        _hex(self.sha256, code="preparation_artifact_binding_invalid")
        _length(self.byte_length, code="preparation_artifact_binding_invalid")
        if self.content_status != _BINDING_CONTENT_STATUS:
            _fail("preparation_artifact_binding_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "artifact_kind": self.artifact_kind,
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "content_status": self.content_status,
        }


@dataclass(frozen=True)
class LocalQwenTierBUnapprovedDeclaration:
    """Names-only declaration that all Tier B execution choices remain unsigned."""

    model_revision: str
    runtime_or_image: str
    precision_or_quantization: str
    decode_context_or_seed: str
    timeout_retry_or_network: str
    resource_or_budget: str

    @classmethod
    def create(cls) -> "LocalQwenTierBUnapprovedDeclaration":
        return cls(*(_TIER_B_UNAPPROVED for _ in range(6)))

    @classmethod
    def from_dict(cls, payload: object) -> "LocalQwenTierBUnapprovedDeclaration":
        data = _mapping(
            payload,
            (
                "model_revision",
                "runtime_or_image",
                "precision_or_quantization",
                "decode_context_or_seed",
                "timeout_retry_or_network",
                "resource_or_budget",
            ),
            code="preparation_exact_keys_invalid",
        )
        result = cls(
            _text(data["model_revision"], code="preparation_tier_b_declaration_invalid"),
            _text(data["runtime_or_image"], code="preparation_tier_b_declaration_invalid"),
            _text(data["precision_or_quantization"], code="preparation_tier_b_declaration_invalid"),
            _text(data["decode_context_or_seed"], code="preparation_tier_b_declaration_invalid"),
            _text(data["timeout_retry_or_network"], code="preparation_tier_b_declaration_invalid"),
            _text(data["resource_or_budget"], code="preparation_tier_b_declaration_invalid"),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if any(value != _TIER_B_UNAPPROVED for value in self.to_values()):
            _fail("preparation_tier_b_declaration_invalid")

    def to_values(self) -> tuple[str, ...]:
        return (
            self.model_revision,
            self.runtime_or_image,
            self.precision_or_quantization,
            self.decode_context_or_seed,
            self.timeout_retry_or_network,
            self.resource_or_budget,
        )

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "model_revision": self.model_revision,
            "runtime_or_image": self.runtime_or_image,
            "precision_or_quantization": self.precision_or_quantization,
            "decode_context_or_seed": self.decode_context_or_seed,
            "timeout_retry_or_network": self.timeout_retry_or_network,
            "resource_or_budget": self.resource_or_budget,
        }


@dataclass(frozen=True)
class LocalQwenProviderPreparationRecord:
    """Canonical payload-free preparation identity for a future Tier B interface."""

    schema_version: str
    authorization: str
    d17_path: str
    local_only: bool
    not_sent: bool
    external_egress_allowed: bool
    model_loaded: bool
    provider_invocation_state: str
    transport_state: str
    tier_b_authorization: str
    candidate_model_family: str
    tier_b_unapproved_declaration: LocalQwenTierBUnapprovedDeclaration
    manifest_id: str
    field_policy_identity: str
    field_policy_snapshot_sha256: str
    complete_local_context_sha256: str
    selection_record_binding: LocalQwenProviderArtifactBinding
    input_view_binding: LocalQwenProviderArtifactBinding
    input_view_artifact_binding: LocalQwenProviderArtifactBinding
    prompt_artifact_binding: LocalQwenProviderArtifactBinding
    config_artifact_binding: LocalQwenProviderArtifactBinding
    local_request_binding: LocalQwenProviderArtifactBinding
    pre_invocation_audit_binding: LocalQwenProviderArtifactBinding
    preparation_record_id: str

    @classmethod
    def create(
        cls,
        context: AgentContextBundle,
        manifest: D17Path3TierAManifest,
        selected: D17Path3SelectedInput,
        local_request: D17Path3LocalRequestArtifact,
        pre_invocation_audit: D17Path3TierAPreInvocationAuditRecord,
    ) -> "LocalQwenProviderPreparationRecord":
        manifest = _validated_manifest(manifest)
        selected = _validated_selected(context, manifest, selected)
        local_request = _validated_request(context, manifest, selected, local_request)
        pre_invocation_audit = _validated_audit(
            context, manifest, selected, local_request, pre_invocation_audit
        )
        bindings = {
            "selection_record_binding": LocalQwenProviderArtifactBinding.from_bytes(
                "input_selection_record",
                selected.selection_record.selection_record_id,
                selected.selection_record.canonical_bytes(),
            ),
            "input_view_binding": LocalQwenProviderArtifactBinding.from_bytes(
                "provider_visible_input_view",
                selected.selection_record.input_view_id,
                selected.provider_visible_input.canonical_bytes(),
            ),
            "input_view_artifact_binding": LocalQwenProviderArtifactBinding.from_bytes(
                "input_view_artifact",
                local_request.input_view_artifact.artifact_id,
                local_request.input_view_artifact.canonical_bytes(),
            ),
            "prompt_artifact_binding": LocalQwenProviderArtifactBinding.from_bytes(
                "prompt_artifact",
                local_request.prompt_artifact.artifact_id,
                local_request.prompt_artifact.canonical_bytes(),
            ),
            "config_artifact_binding": LocalQwenProviderArtifactBinding.from_bytes(
                "config_artifact",
                local_request.config_artifact.artifact_id,
                local_request.config_artifact.canonical_bytes(),
            ),
            "local_request_binding": LocalQwenProviderArtifactBinding.from_bytes(
                "local_request_artifact",
                local_request.artifact_id,
                local_request.canonical_bytes(),
            ),
            "pre_invocation_audit_binding": LocalQwenProviderArtifactBinding.from_bytes(
                "pre_invocation_audit_record",
                pre_invocation_audit.audit_record_id,
                pre_invocation_audit.canonical_bytes(),
            ),
        }
        root = cls._root_for(
            manifest=manifest,
            complete_local_context_sha256=selected.selection_record.local_context_sha256,
            **bindings,
        )
        result = cls(
            schema_version=root["schema_version"],
            authorization=root["authorization"],
            d17_path=root["d17_path"],
            local_only=root["local_only"],
            not_sent=root["not_sent"],
            external_egress_allowed=root["external_egress_allowed"],
            model_loaded=root["model_loaded"],
            provider_invocation_state=root["provider_invocation_state"],
            transport_state=root["transport_state"],
            tier_b_authorization=root["tier_b_authorization"],
            candidate_model_family=root["candidate_model_family"],
            tier_b_unapproved_declaration=LocalQwenTierBUnapprovedDeclaration.create(),
            manifest_id=root["manifest_id"],
            field_policy_identity=root["field_policy_identity"],
            field_policy_snapshot_sha256=root["field_policy_snapshot_sha256"],
            complete_local_context_sha256=root["complete_local_context_sha256"],
            preparation_record_id=_identity("local-qwen-provider-preparation-", root),
            **bindings,
        )
        result.validate()
        return result

    @classmethod
    def from_dict(cls, payload: object) -> "LocalQwenProviderPreparationRecord":
        keys = (
            "schema_version", "authorization", "d17_path", "local_only", "not_sent",
            "external_egress_allowed", "model_loaded", "provider_invocation_state",
            "transport_state", "tier_b_authorization", "candidate_model_family",
            "tier_b_unapproved_declaration", "manifest_id", "field_policy_identity",
            "field_policy_snapshot_sha256", "complete_local_context_sha256",
            "selection_record_binding", "input_view_binding", "input_view_artifact_binding",
            "prompt_artifact_binding", "config_artifact_binding", "local_request_binding",
            "pre_invocation_audit_binding", "preparation_record_id",
        )
        data = _mapping(payload, keys, code="preparation_exact_keys_invalid")
        result = cls(
            _text(data["schema_version"], code="preparation_schema_invalid"),
            _text(data["authorization"], code="preparation_schema_invalid"),
            _text(data["d17_path"], code="preparation_schema_invalid"),
            _bool(data["local_only"], code="preparation_local_boundary_invalid"),
            _bool(data["not_sent"], code="preparation_local_boundary_invalid"),
            _bool(data["external_egress_allowed"], code="preparation_local_boundary_invalid"),
            _bool(data["model_loaded"], code="preparation_local_boundary_invalid"),
            _text(data["provider_invocation_state"], code="preparation_local_boundary_invalid"),
            _text(data["transport_state"], code="preparation_local_boundary_invalid"),
            _text(data["tier_b_authorization"], code="preparation_tier_b_declaration_invalid"),
            _text(data["candidate_model_family"], code="preparation_schema_invalid"),
            LocalQwenTierBUnapprovedDeclaration.from_dict(data["tier_b_unapproved_declaration"]),
            _text(data["manifest_id"], code="preparation_manifest_binding_invalid"),
            _text(data["field_policy_identity"], code="preparation_manifest_binding_invalid"),
            _hex(data["field_policy_snapshot_sha256"], code="preparation_manifest_binding_invalid"),
            _hex(data["complete_local_context_sha256"], code="preparation_selection_binding_invalid"),
            LocalQwenProviderArtifactBinding.from_dict(data["selection_record_binding"]),
            LocalQwenProviderArtifactBinding.from_dict(data["input_view_binding"]),
            LocalQwenProviderArtifactBinding.from_dict(data["input_view_artifact_binding"]),
            LocalQwenProviderArtifactBinding.from_dict(data["prompt_artifact_binding"]),
            LocalQwenProviderArtifactBinding.from_dict(data["config_artifact_binding"]),
            LocalQwenProviderArtifactBinding.from_dict(data["local_request_binding"]),
            LocalQwenProviderArtifactBinding.from_dict(data["pre_invocation_audit_binding"]),
            _text(data["preparation_record_id"], code="preparation_identity_invalid"),
        )
        result.validate()
        return result

    @classmethod
    def from_bytes(cls, raw: bytes) -> "LocalQwenProviderPreparationRecord":
        result = cls.from_dict(_loads(raw))
        if result.canonical_bytes() != raw:
            _fail("preparation_bytes_invalid")
        return result


    @staticmethod
    def _root_for(
        *,
        manifest: D17Path3TierAManifest,
        complete_local_context_sha256: str,
        selection_record_binding: LocalQwenProviderArtifactBinding,
        input_view_binding: LocalQwenProviderArtifactBinding,
        input_view_artifact_binding: LocalQwenProviderArtifactBinding,
        prompt_artifact_binding: LocalQwenProviderArtifactBinding,
        config_artifact_binding: LocalQwenProviderArtifactBinding,
        local_request_binding: LocalQwenProviderArtifactBinding,
        pre_invocation_audit_binding: LocalQwenProviderArtifactBinding,
    ) -> dict[str, object]:
        return {
            "schema_version": LOCAL_QWEN_PROVIDER_PREPARATION_SCHEMA_VERSION,
            "authorization": _AUTHORIZATION,
            "d17_path": _PATH_3,
            "local_only": True,
            "not_sent": True,
            "external_egress_allowed": False,
            "model_loaded": False,
            "provider_invocation_state": "not_invoked",
            "transport_state": "not_sent",
            "tier_b_authorization": _TIER_B_UNAPPROVED,
            "candidate_model_family": _CANDIDATE_MODEL_FAMILY,
            "tier_b_unapproved_declaration": LocalQwenTierBUnapprovedDeclaration.create().to_dict(),
            "manifest_id": manifest.manifest_id,
            "field_policy_identity": D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
            "field_policy_snapshot_sha256": manifest.field_policy.approved_snapshot_sha256(),
            "complete_local_context_sha256": complete_local_context_sha256,
            "selection_record_binding": selection_record_binding.to_dict(),
            "input_view_binding": input_view_binding.to_dict(),
            "input_view_artifact_binding": input_view_artifact_binding.to_dict(),
            "prompt_artifact_binding": prompt_artifact_binding.to_dict(),
            "config_artifact_binding": config_artifact_binding.to_dict(),
            "local_request_binding": local_request_binding.to_dict(),
            "pre_invocation_audit_binding": pre_invocation_audit_binding.to_dict(),
        }

    def _root(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "authorization": self.authorization,
            "d17_path": self.d17_path,
            "local_only": self.local_only,
            "not_sent": self.not_sent,
            "external_egress_allowed": self.external_egress_allowed,
            "model_loaded": self.model_loaded,
            "provider_invocation_state": self.provider_invocation_state,
            "transport_state": self.transport_state,
            "tier_b_authorization": self.tier_b_authorization,
            "candidate_model_family": self.candidate_model_family,
            "tier_b_unapproved_declaration": self.tier_b_unapproved_declaration.to_dict(),
            "manifest_id": self.manifest_id,
            "field_policy_identity": self.field_policy_identity,
            "field_policy_snapshot_sha256": self.field_policy_snapshot_sha256,
            "complete_local_context_sha256": self.complete_local_context_sha256,
            "selection_record_binding": self.selection_record_binding.to_dict(),
            "input_view_binding": self.input_view_binding.to_dict(),
            "input_view_artifact_binding": self.input_view_artifact_binding.to_dict(),
            "prompt_artifact_binding": self.prompt_artifact_binding.to_dict(),
            "config_artifact_binding": self.config_artifact_binding.to_dict(),
            "local_request_binding": self.local_request_binding.to_dict(),
            "pre_invocation_audit_binding": self.pre_invocation_audit_binding.to_dict(),
        }

    def validate(self) -> None:
        if (
            self.schema_version != LOCAL_QWEN_PROVIDER_PREPARATION_SCHEMA_VERSION
            or self.authorization != _AUTHORIZATION
            or self.d17_path != _PATH_3
            or self.candidate_model_family != _CANDIDATE_MODEL_FAMILY
        ):
            _fail("preparation_schema_invalid")
        if (
            self.local_only is not True
            or self.not_sent is not True
            or self.external_egress_allowed is not False
            or self.model_loaded is not False
            or self.provider_invocation_state != "not_invoked"
            or self.transport_state != "not_sent"
        ):
            _fail("preparation_local_boundary_invalid")
        if self.tier_b_authorization != _TIER_B_UNAPPROVED:
            _fail("preparation_tier_b_declaration_invalid")
        self.tier_b_unapproved_declaration.validate()
        _prefixed_identity(self.manifest_id, "d17-path3-tier-a-", code="preparation_manifest_binding_invalid")
        if (
            self.field_policy_identity != D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY
            or self.field_policy_snapshot_sha256 != D17Path3FieldPolicy.approved_snapshot_sha256()
        ):
            _fail("preparation_manifest_binding_invalid")
        _hex(self.complete_local_context_sha256, code="preparation_selection_binding_invalid")
        for binding, kind in (
            (self.selection_record_binding, "input_selection_record"),
            (self.input_view_binding, "provider_visible_input_view"),
            (self.input_view_artifact_binding, "input_view_artifact"),
            (self.prompt_artifact_binding, "prompt_artifact"),
            (self.config_artifact_binding, "config_artifact"),
            (self.local_request_binding, "local_request_artifact"),
            (self.pre_invocation_audit_binding, "pre_invocation_audit_record"),
        ):
            binding.validate(expected_kind=kind)
        _prefixed_identity(
            self.preparation_record_id,
            "local-qwen-provider-preparation-",
            code="preparation_identity_invalid",
        )
        if self.preparation_record_id != _identity("local-qwen-provider-preparation-", self._root()):
            _fail("preparation_identity_invalid")

    def validate_against(
        self,
        context: AgentContextBundle,
        manifest: D17Path3TierAManifest,
        selected: D17Path3SelectedInput,
        local_request: D17Path3LocalRequestArtifact,
        pre_invocation_audit: D17Path3TierAPreInvocationAuditRecord,
    ) -> None:
        manifest = _validated_manifest(manifest)
        self.validate()
        expected = self.create(context, manifest, selected, local_request, pre_invocation_audit)
        if self.to_dict() != expected.to_dict():
            _fail("preparation_live_cross_binding_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        result = self._root()
        result["preparation_record_id"] = self.preparation_record_id
        return result

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def sha256(self) -> str:
        return _sha256(self.canonical_bytes())


def prepare_local_qwen_provider_interface(
    context: AgentContextBundle,
    manifest: D17Path3TierAManifest,
    selected: D17Path3SelectedInput,
    local_request: D17Path3LocalRequestArtifact,
    pre_invocation_audit: D17Path3TierAPreInvocationAuditRecord,
) -> LocalQwenProviderPreparationRecord:
    """Create only the local preparation record; no Provider invocation occurs."""

    return LocalQwenProviderPreparationRecord.create(
        context, manifest, selected, local_request, pre_invocation_audit
    )


def invoke_local_qwen_provider() -> NoReturn:
    """Always stop before any backend, callback, endpoint, credential, or handle."""

    raise LocalQwenProviderInvocationBlockedError()
