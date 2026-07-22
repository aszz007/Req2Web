"""Payload-free D17 path-3 Tier A pre-invocation audit records.

This module creates only local, canonical audit records before any future
Provider interface could be considered. It does not invoke a Provider, load a
model, choose a runtime, perform transport, or verify copyright, licensing, or
redistribution rights.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from req2web_agent.schema import AgentContextBundle
from req2web_provider.d17_input_view import D17Path3SelectedInput
from req2web_provider.d17_manifest import (
    D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
    D17Path3TierAManifest,
)
from req2web_provider.d17_serializer import D17Path3LocalRequestArtifact


D17_PATH3_TIER_A_PRE_INVOCATION_AUDIT_SCHEMA_VERSION = (
    "req2web.d17.path3.tier_a.pre_invocation_audit.v1"
)
D17_PATH3_TIER_A_AUDIT_ERROR_ENVELOPE_SCHEMA_VERSION = (
    "req2web.d17.path3.tier_a.audit.error.v1"
)

_AUTHORIZATION = "tier_a_local_implementation_only"
_PATH_3 = "path_3"
_PRE_INVOCATION_STATE = "not_invoked"
_VISIBILITY_STATUS = "declared_provider_visible_without_values"
_EXCLUSION_STATUS = "not_included_in_audit_bytes"
_SOURCE_ATTESTATION_SCOPE = "local_attestation_only"
_RIGHTS_STATUS = "not_verified_or_asserted"
_BINDING_CONTENT_STATUS = "identity_hash_and_length_only"

_SAFE_MESSAGES = {
    "audit_schema_invalid": "D17 pre-invocation audit schema is invalid.",
    "audit_exact_keys_invalid": "D17 pre-invocation audit field shape is invalid.",
    "audit_identity_invalid": "D17 pre-invocation audit identity is invalid.",
    "audit_manifest_binding_invalid": "D17 pre-invocation audit manifest binding is invalid.",
    "audit_local_boundary_invalid": "D17 pre-invocation audit local boundary is invalid.",
    "audit_selection_binding_invalid": "D17 pre-invocation audit selection binding is invalid.",
    "audit_request_binding_invalid": "D17 pre-invocation audit request binding is invalid.",
    "audit_artifact_binding_invalid": "D17 pre-invocation audit artifact binding is invalid.",
    "audit_source_attestation_invalid": "D17 pre-invocation audit source attestation is invalid.",
    "audit_visibility_declaration_invalid": "D17 pre-invocation audit visibility declaration is invalid.",
    "audit_exclusion_declaration_invalid": "D17 pre-invocation audit exclusion declaration is invalid.",
    "audit_bytes_invalid": "D17 pre-invocation audit canonical bytes are invalid.",
    "audit_live_cross_binding_invalid": "D17 pre-invocation audit live binding is invalid.",
}


class _DuplicateJsonKey(ValueError):
    """Raised only while rejecting duplicate JSON keys."""


@dataclass(frozen=True)
class D17Path3TierAAuditErrorEnvelope:
    """A fixed, payload-free error record for local D17 audit failures."""

    code: str
    schema_version: str = D17_PATH3_TIER_A_AUDIT_ERROR_ENVELOPE_SCHEMA_VERSION
    stage: str = "d17"
    phase: str = "pre_invocation_audit"
    payload_disclosure: str = "none"
    retryable: bool = False

    def to_dict(self) -> dict[str, object]:
        if self.code not in _SAFE_MESSAGES:
            raise ValueError("D17 audit error envelope code is invalid")
        if (
            self.schema_version,
            self.stage,
            self.phase,
            self.payload_disclosure,
            self.retryable,
        ) != (
            D17_PATH3_TIER_A_AUDIT_ERROR_ENVELOPE_SCHEMA_VERSION,
            "d17",
            "pre_invocation_audit",
            "none",
            False,
        ):
            raise ValueError("D17 audit error envelope is invalid")
        return {
            "schema_version": self.schema_version,
            "code": self.code,
            "stage": self.stage,
            "phase": self.phase,
            "payload_disclosure": self.payload_disclosure,
            "retryable": self.retryable,
        }


class D17Path3TierAAuditValidationError(ValueError):
    """Stable, payload-free fail-closed error for D17 Tier A audit records."""

    def __init__(self, code: str) -> None:
        if code not in _SAFE_MESSAGES:
            raise ValueError("D17 audit error code is invalid")
        super().__init__(_SAFE_MESSAGES[code])
        self.code = code
        self.envelope = D17Path3TierAAuditErrorEnvelope(code)


def _fail(code: str) -> None:
    raise D17Path3TierAAuditValidationError(code)


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
        _fail("audit_schema_invalid")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity(prefix: str, root: Mapping[str, object]) -> str:
    return prefix + _sha256(_canonical_bytes(root))


def _exact_mapping(
    value: object, keys: tuple[str, ...], *, code: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        _fail(code)
    return dict(value)


def _text(value: object, *, code: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    return value


def _sha256_hex(value: object, *, code: str) -> str:
    value = _text(value, code=code)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        _fail(code)
    return value


def _byte_length(value: object, *, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(code)
    return value


def _bool(value: object, *, code: str) -> bool:
    if not isinstance(value, bool):
        _fail(code)
    return value


def _tuple_of_text(value: object, *, code: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        _fail(code)
    result = tuple(_text(item, code=code) for item in value)
    if not result and not allow_empty:
        _fail(code)
    return result


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
        _fail("audit_bytes_invalid")
    try:
        text = raw.decode("utf-8")
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError):
        _fail("audit_bytes_invalid")


def _validated_manifest(manifest: object) -> D17Path3TierAManifest:
    if not isinstance(manifest, D17Path3TierAManifest):
        _fail("audit_manifest_binding_invalid")
    try:
        manifest.validate()
    except (TypeError, ValueError):
        _fail("audit_manifest_binding_invalid")
    return manifest


def _derived_manifest(
    structural_signal_names: tuple[str, ...],
) -> D17Path3TierAManifest:
    try:
        return D17Path3TierAManifest.create(
            structural_signal_names=structural_signal_names
        )
    except (TypeError, ValueError):
        _fail("audit_manifest_binding_invalid")


def _validated_selected(
    context: AgentContextBundle,
    manifest: D17Path3TierAManifest,
    selected: object,
) -> D17Path3SelectedInput:
    if not isinstance(selected, D17Path3SelectedInput):
        _fail("audit_selection_binding_invalid")
    try:
        selected.validate_against(context, manifest)
    except (TypeError, ValueError):
        _fail("audit_selection_binding_invalid")
    return selected


def _validated_request(
    context: AgentContextBundle,
    manifest: D17Path3TierAManifest,
    request: object,
) -> D17Path3LocalRequestArtifact:
    if not isinstance(request, D17Path3LocalRequestArtifact):
        _fail("audit_request_binding_invalid")
    try:
        request.validate_against(context, manifest)
    except (TypeError, ValueError):
        _fail("audit_request_binding_invalid")
    if (
        request.local_only is not True
        or request.not_sent is not True
        or request.external_egress_allowed is not False
        or request.provider_invocation_state != _PRE_INVOCATION_STATE
        or request.transport_state != "not_sent"
    ):
        _fail("audit_request_binding_invalid")
    return request


@dataclass(frozen=True)
class D17Path3SourceAttestation:
    """Local source-class attestation without a rights or license claim."""

    original_requirement_source_class: str
    attestation_scope: str
    license_verified: bool
    copyright_license_or_redistribution_rights_status: str

    @classmethod
    def create(cls, original_requirement_source_class: str) -> "D17Path3SourceAttestation":
        return cls(
            original_requirement_source_class=original_requirement_source_class,
            attestation_scope=_SOURCE_ATTESTATION_SCOPE,
            license_verified=False,
            copyright_license_or_redistribution_rights_status=_RIGHTS_STATUS,
        )

    @classmethod
    def from_dict(cls, payload: object) -> "D17Path3SourceAttestation":
        data = _exact_mapping(
            payload,
            (
                "original_requirement_source_class",
                "attestation_scope",
                "license_verified",
                "copyright_license_or_redistribution_rights_status",
            ),
            code="audit_exact_keys_invalid",
        )
        result = cls(
            original_requirement_source_class=_text(
                data["original_requirement_source_class"],
                code="audit_source_attestation_invalid",
            ),
            attestation_scope=_text(
                data["attestation_scope"],
                code="audit_source_attestation_invalid",
            ),
            license_verified=_bool(
                data["license_verified"],
                code="audit_source_attestation_invalid",
            ),
            copyright_license_or_redistribution_rights_status=_text(
                data["copyright_license_or_redistribution_rights_status"],
                code="audit_source_attestation_invalid",
            ),
        )
        result.validate()
        return result

    def validate(self, manifest: D17Path3TierAManifest | None = None) -> None:
        if (
            self.attestation_scope != _SOURCE_ATTESTATION_SCOPE
            or self.license_verified is not False
            or self.copyright_license_or_redistribution_rights_status != _RIGHTS_STATUS
        ):
            _fail("audit_source_attestation_invalid")
        if manifest is not None:
            if (
                self.original_requirement_source_class
                not in manifest.field_policy.original_requirement_source_classes
            ):
                _fail("audit_source_attestation_invalid")
        elif self.original_requirement_source_class not in (
            "project_authored",
            "synthetic",
        ):
            _fail("audit_source_attestation_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "original_requirement_source_class": self.original_requirement_source_class,
            "attestation_scope": self.attestation_scope,
            "license_verified": self.license_verified,
            "copyright_license_or_redistribution_rights_status": (
                self.copyright_license_or_redistribution_rights_status
            ),
        }


@dataclass(frozen=True)
class D17Path3VisibilityDeclaration:
    """Names-only declaration of the approved six-category input boundary."""

    declaration_status: str
    provider_visible_categories: tuple[str, ...]
    use_case_item_keys: tuple[str, ...]
    structural_signal_names: tuple[str, ...]
    structural_signal_policy: str
    unknown_or_unlisted_fields: str

    @classmethod
    def create(cls, manifest: D17Path3TierAManifest) -> "D17Path3VisibilityDeclaration":
        manifest = _validated_manifest(manifest)
        policy = manifest.field_policy
        result = cls(
            declaration_status=_VISIBILITY_STATUS,
            provider_visible_categories=tuple(policy.provider_visible_fields),
            use_case_item_keys=tuple(policy.use_case_item_keys),
            structural_signal_names=tuple(policy.structural_signal_names),
            structural_signal_policy=policy.structural_signal_policy,
            unknown_or_unlisted_fields=policy.unknown_or_unlisted_fields,
        )
        result.validate(manifest)
        return result

    @classmethod
    def from_dict(cls, payload: object) -> "D17Path3VisibilityDeclaration":
        data = _exact_mapping(
            payload,
            (
                "declaration_status",
                "provider_visible_categories",
                "use_case_item_keys",
                "structural_signal_names",
                "structural_signal_policy",
                "unknown_or_unlisted_fields",
            ),
            code="audit_exact_keys_invalid",
        )
        result = cls(
            declaration_status=_text(
                data["declaration_status"],
                code="audit_visibility_declaration_invalid",
            ),
            provider_visible_categories=_tuple_of_text(
                data["provider_visible_categories"],
                code="audit_visibility_declaration_invalid",
            ),
            use_case_item_keys=_tuple_of_text(
                data["use_case_item_keys"],
                code="audit_visibility_declaration_invalid",
            ),
            structural_signal_names=_tuple_of_text(
                data["structural_signal_names"],
                code="audit_visibility_declaration_invalid",
                allow_empty=True,
            ),
            structural_signal_policy=_text(
                data["structural_signal_policy"],
                code="audit_visibility_declaration_invalid",
            ),
            unknown_or_unlisted_fields=_text(
                data["unknown_or_unlisted_fields"],
                code="audit_visibility_declaration_invalid",
            ),
        )
        result.validate()
        return result

    def validate(self, manifest: D17Path3TierAManifest | None = None) -> None:
        if self.declaration_status != _VISIBILITY_STATUS:
            _fail("audit_visibility_declaration_invalid")
        if len(set(self.structural_signal_names)) != len(self.structural_signal_names):
            _fail("audit_visibility_declaration_invalid")
        policy_manifest = manifest or _derived_manifest(self.structural_signal_names)
        policy = policy_manifest.field_policy
        if (
            self.provider_visible_categories != policy.provider_visible_fields
            or self.use_case_item_keys != policy.use_case_item_keys
            or self.structural_signal_names != policy.structural_signal_names
            or self.structural_signal_policy != policy.structural_signal_policy
            or self.unknown_or_unlisted_fields != policy.unknown_or_unlisted_fields
        ):
            _fail("audit_visibility_declaration_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "declaration_status": self.declaration_status,
            "provider_visible_categories": list(self.provider_visible_categories),
            "use_case_item_keys": list(self.use_case_item_keys),
            "structural_signal_names": list(self.structural_signal_names),
            "structural_signal_policy": self.structural_signal_policy,
            "unknown_or_unlisted_fields": self.unknown_or_unlisted_fields,
        }


@dataclass(frozen=True)
class D17Path3ExclusionRecord:
    """One manifest-declared prohibited category with no copied content."""

    category: str
    status: str

    @classmethod
    def from_dict(cls, payload: object) -> "D17Path3ExclusionRecord":
        data = _exact_mapping(
            payload,
            ("category", "status"),
            code="audit_exact_keys_invalid",
        )
        result = cls(
            category=_text(data["category"], code="audit_exclusion_declaration_invalid"),
            status=_text(data["status"], code="audit_exclusion_declaration_invalid"),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.status != _EXCLUSION_STATUS:
            _fail("audit_exclusion_declaration_invalid")

    def to_dict(self) -> dict[str, str]:
        self.validate()
        return {"category": self.category, "status": self.status}


@dataclass(frozen=True)
class D17Path3ExclusionDeclaration:
    """Complete per-category exclusion declaration derived from the manifest."""

    declaration_status: str
    prohibited_data_categories: tuple[str, ...]
    records: tuple[D17Path3ExclusionRecord, ...]

    @classmethod
    def create(cls, manifest: D17Path3TierAManifest) -> "D17Path3ExclusionDeclaration":
        manifest = _validated_manifest(manifest)
        categories = tuple(manifest.prohibited_data_categories)
        result = cls(
            declaration_status=_EXCLUSION_STATUS,
            prohibited_data_categories=categories,
            records=tuple(
                D17Path3ExclusionRecord(category=category, status=_EXCLUSION_STATUS)
                for category in categories
            ),
        )
        result.validate(manifest)
        return result

    @classmethod
    def from_dict(cls, payload: object) -> "D17Path3ExclusionDeclaration":
        data = _exact_mapping(
            payload,
            ("declaration_status", "prohibited_data_categories", "records"),
            code="audit_exact_keys_invalid",
        )
        raw_records = data["records"]
        if not isinstance(raw_records, (list, tuple)):
            _fail("audit_exclusion_declaration_invalid")
        result = cls(
            declaration_status=_text(
                data["declaration_status"],
                code="audit_exclusion_declaration_invalid",
            ),
            prohibited_data_categories=_tuple_of_text(
                data["prohibited_data_categories"],
                code="audit_exclusion_declaration_invalid",
            ),
            records=tuple(D17Path3ExclusionRecord.from_dict(item) for item in raw_records),
        )
        result.validate()
        return result

    def validate(self, manifest: D17Path3TierAManifest | None = None) -> None:
        if self.declaration_status != _EXCLUSION_STATUS:
            _fail("audit_exclusion_declaration_invalid")
        if len(self.records) != len(self.prohibited_data_categories):
            _fail("audit_exclusion_declaration_invalid")
        record_categories = tuple(record.category for record in self.records)
        if record_categories != self.prohibited_data_categories:
            _fail("audit_exclusion_declaration_invalid")
        for record in self.records:
            record.validate()
        policy_manifest = manifest or _derived_manifest(())
        if self.prohibited_data_categories != policy_manifest.prohibited_data_categories:
            _fail("audit_exclusion_declaration_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "declaration_status": self.declaration_status,
            "prohibited_data_categories": list(self.prohibited_data_categories),
            "records": [record.to_dict() for record in self.records],
        }


@dataclass(frozen=True)
class D17Path3ArtifactBinding:
    """Payload-free identity binding for one already-local artifact."""

    artifact_kind: str
    artifact_id: str
    sha256: str
    byte_length: int
    content_status: str = _BINDING_CONTENT_STATUS

    @classmethod
    def from_dict(cls, payload: object) -> "D17Path3ArtifactBinding":
        data = _exact_mapping(
            payload,
            ("artifact_kind", "artifact_id", "sha256", "byte_length", "content_status"),
            code="audit_exact_keys_invalid",
        )
        result = cls(
            artifact_kind=_text(data["artifact_kind"], code="audit_artifact_binding_invalid"),
            artifact_id=_text(data["artifact_id"], code="audit_artifact_binding_invalid"),
            sha256=_sha256_hex(data["sha256"], code="audit_artifact_binding_invalid"),
            byte_length=_byte_length(
                data["byte_length"], code="audit_artifact_binding_invalid"
            ),
            content_status=_text(
                data["content_status"], code="audit_artifact_binding_invalid"
            ),
        )
        result.validate()
        return result

    @classmethod
    def from_bytes(
        cls, artifact_kind: str, artifact_id: str, raw: bytes
    ) -> "D17Path3ArtifactBinding":
        return cls(
            artifact_kind=artifact_kind,
            artifact_id=artifact_id,
            sha256=_sha256(raw),
            byte_length=len(raw),
        )

    def validate(self, *, expected_kind: str | None = None) -> None:
        if (
            self.content_status != _BINDING_CONTENT_STATUS
            or (expected_kind is not None and self.artifact_kind != expected_kind)
        ):
            _fail("audit_artifact_binding_invalid")
        _text(self.artifact_kind, code="audit_artifact_binding_invalid")
        _text(self.artifact_id, code="audit_artifact_binding_invalid")
        _sha256_hex(self.sha256, code="audit_artifact_binding_invalid")
        _byte_length(self.byte_length, code="audit_artifact_binding_invalid")

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
class D17Path3TierAPreInvocationAuditRecord:
    """Canonical local proof of approved visibility and exclusion before invocation."""

    schema_version: str
    authorization: str
    d17_path: str
    local_only: bool
    not_sent: bool
    external_egress_allowed: bool
    provider_invocation_state: str
    manifest_id: str
    field_policy_identity: str
    field_policy_snapshot_sha256: str
    source_attestation: D17Path3SourceAttestation
    visibility_declaration: D17Path3VisibilityDeclaration
    exclusion_declaration: D17Path3ExclusionDeclaration
    selection_record_binding: D17Path3ArtifactBinding
    complete_local_context_sha256: str
    input_view_binding: D17Path3ArtifactBinding
    input_view_artifact_binding: D17Path3ArtifactBinding
    prompt_artifact_binding: D17Path3ArtifactBinding
    config_artifact_binding: D17Path3ArtifactBinding
    local_request_binding: D17Path3ArtifactBinding
    audit_record_id: str

    @classmethod
    def create(
        cls,
        context: AgentContextBundle,
        manifest: D17Path3TierAManifest,
        selected: D17Path3SelectedInput,
        local_request: D17Path3LocalRequestArtifact,
    ) -> "D17Path3TierAPreInvocationAuditRecord":
        manifest = _validated_manifest(manifest)
        selected = _validated_selected(context, manifest, selected)
        local_request = _validated_request(context, manifest, local_request)
        cls._validate_request_matches_selected(manifest, selected, local_request)

        selection_record = selected.selection_record
        source_attestation = D17Path3SourceAttestation.create(
            selection_record.original_requirement_source_class
        )
        visibility_declaration = D17Path3VisibilityDeclaration.create(manifest)
        exclusion_declaration = D17Path3ExclusionDeclaration.create(manifest)
        root = cls._root_for(
            manifest=manifest,
            source_attestation=source_attestation,
            visibility_declaration=visibility_declaration,
            exclusion_declaration=exclusion_declaration,
            selection_record_binding=D17Path3ArtifactBinding.from_bytes(
                "input_selection_record",
                selection_record.selection_record_id,
                selection_record.canonical_bytes(),
            ),
            complete_local_context_sha256=selection_record.local_context_sha256,
            input_view_binding=D17Path3ArtifactBinding.from_bytes(
                "provider_visible_input_view",
                selection_record.input_view_id,
                selected.provider_visible_input.canonical_bytes(),
            ),
            input_view_artifact_binding=D17Path3ArtifactBinding.from_bytes(
                "input_view_artifact",
                local_request.input_view_artifact.artifact_id,
                local_request.input_view_artifact.canonical_bytes(),
            ),
            prompt_artifact_binding=D17Path3ArtifactBinding.from_bytes(
                "prompt_artifact",
                local_request.prompt_artifact.artifact_id,
                local_request.prompt_artifact.canonical_bytes(),
            ),
            config_artifact_binding=D17Path3ArtifactBinding.from_bytes(
                "config_artifact",
                local_request.config_artifact.artifact_id,
                local_request.config_artifact.canonical_bytes(),
            ),
            local_request_binding=D17Path3ArtifactBinding.from_bytes(
                "local_request_artifact",
                local_request.artifact_id,
                local_request.canonical_bytes(),
            ),
        )
        result = cls(
            schema_version=root["schema_version"],
            authorization=root["authorization"],
            d17_path=root["d17_path"],
            local_only=root["local_only"],
            not_sent=root["not_sent"],
            external_egress_allowed=root["external_egress_allowed"],
            provider_invocation_state=root["provider_invocation_state"],
            manifest_id=root["manifest_id"],
            field_policy_identity=root["field_policy_identity"],
            field_policy_snapshot_sha256=root["field_policy_snapshot_sha256"],
            source_attestation=source_attestation,
            visibility_declaration=visibility_declaration,
            exclusion_declaration=exclusion_declaration,
            selection_record_binding=D17Path3ArtifactBinding.from_dict(root["selection_record_binding"]),
            complete_local_context_sha256=root["complete_local_context_sha256"],
            input_view_binding=D17Path3ArtifactBinding.from_dict(root["input_view_binding"]),
            input_view_artifact_binding=D17Path3ArtifactBinding.from_dict(root["input_view_artifact_binding"]),
            prompt_artifact_binding=D17Path3ArtifactBinding.from_dict(root["prompt_artifact_binding"]),
            config_artifact_binding=D17Path3ArtifactBinding.from_dict(root["config_artifact_binding"]),
            local_request_binding=D17Path3ArtifactBinding.from_dict(root["local_request_binding"]),
            audit_record_id=_identity("d17-pre-invocation-audit-", root),
        )
        result.validate()
        return result

    @classmethod
    def from_dict(cls, payload: object) -> "D17Path3TierAPreInvocationAuditRecord":
        data = _exact_mapping(
            payload,
            (
                "schema_version",
                "authorization",
                "d17_path",
                "local_only",
                "not_sent",
                "external_egress_allowed",
                "provider_invocation_state",
                "manifest_id",
                "field_policy_identity",
                "field_policy_snapshot_sha256",
                "source_attestation",
                "visibility_declaration",
                "exclusion_declaration",
                "selection_record_binding",
                "complete_local_context_sha256",
                "input_view_binding",
                "input_view_artifact_binding",
                "prompt_artifact_binding",
                "config_artifact_binding",
                "local_request_binding",
                "audit_record_id",
            ),
            code="audit_exact_keys_invalid",
        )
        result = cls(
            schema_version=_text(data["schema_version"], code="audit_schema_invalid"),
            authorization=_text(data["authorization"], code="audit_schema_invalid"),
            d17_path=_text(data["d17_path"], code="audit_schema_invalid"),
            local_only=_bool(data["local_only"], code="audit_schema_invalid"),
            not_sent=_bool(data["not_sent"], code="audit_schema_invalid"),
            external_egress_allowed=_bool(
                data["external_egress_allowed"], code="audit_schema_invalid"
            ),
            provider_invocation_state=_text(
                data["provider_invocation_state"], code="audit_schema_invalid"
            ),
            manifest_id=_text(data["manifest_id"], code="audit_schema_invalid"),
            field_policy_identity=_text(
                data["field_policy_identity"], code="audit_schema_invalid"
            ),
            field_policy_snapshot_sha256=_sha256_hex(
                data["field_policy_snapshot_sha256"], code="audit_schema_invalid"
            ),
            source_attestation=D17Path3SourceAttestation.from_dict(
                data["source_attestation"]
            ),
            visibility_declaration=D17Path3VisibilityDeclaration.from_dict(
                data["visibility_declaration"]
            ),
            exclusion_declaration=D17Path3ExclusionDeclaration.from_dict(
                data["exclusion_declaration"]
            ),
            selection_record_binding=D17Path3ArtifactBinding.from_dict(
                data["selection_record_binding"]
            ),
            complete_local_context_sha256=_sha256_hex(
                data["complete_local_context_sha256"], code="audit_schema_invalid"
            ),
            input_view_binding=D17Path3ArtifactBinding.from_dict(
                data["input_view_binding"]
            ),
            input_view_artifact_binding=D17Path3ArtifactBinding.from_dict(
                data["input_view_artifact_binding"]
            ),
            prompt_artifact_binding=D17Path3ArtifactBinding.from_dict(
                data["prompt_artifact_binding"]
            ),
            config_artifact_binding=D17Path3ArtifactBinding.from_dict(
                data["config_artifact_binding"]
            ),
            local_request_binding=D17Path3ArtifactBinding.from_dict(
                data["local_request_binding"]
            ),
            audit_record_id=_text(data["audit_record_id"], code="audit_schema_invalid"),
        )
        result.validate()
        return result

    @classmethod
    def from_bytes(cls, raw: bytes) -> "D17Path3TierAPreInvocationAuditRecord":
        result = cls.from_dict(_loads(raw))
        if result.canonical_bytes() != raw:
            _fail("audit_bytes_invalid")
        return result

    @classmethod
    def _root_for(
        cls,
        *,
        manifest: D17Path3TierAManifest,
        source_attestation: D17Path3SourceAttestation,
        visibility_declaration: D17Path3VisibilityDeclaration,
        exclusion_declaration: D17Path3ExclusionDeclaration,
        selection_record_binding: D17Path3ArtifactBinding,
        complete_local_context_sha256: str,
        input_view_binding: D17Path3ArtifactBinding,
        input_view_artifact_binding: D17Path3ArtifactBinding,
        prompt_artifact_binding: D17Path3ArtifactBinding,
        config_artifact_binding: D17Path3ArtifactBinding,
        local_request_binding: D17Path3ArtifactBinding,
    ) -> dict[str, object]:
        return {
            "schema_version": D17_PATH3_TIER_A_PRE_INVOCATION_AUDIT_SCHEMA_VERSION,
            "authorization": _AUTHORIZATION,
            "d17_path": _PATH_3,
            "local_only": True,
            "not_sent": True,
            "external_egress_allowed": False,
            "provider_invocation_state": _PRE_INVOCATION_STATE,
            "manifest_id": manifest.manifest_id,
            "field_policy_identity": D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY,
            "field_policy_snapshot_sha256": (
                manifest.field_policy.approved_snapshot_sha256()
            ),
            "source_attestation": source_attestation.to_dict(),
            "visibility_declaration": visibility_declaration.to_dict(),
            "exclusion_declaration": exclusion_declaration.to_dict(),
            "selection_record_binding": selection_record_binding.to_dict(),
            "complete_local_context_sha256": complete_local_context_sha256,
            "input_view_binding": input_view_binding.to_dict(),
            "input_view_artifact_binding": input_view_artifact_binding.to_dict(),
            "prompt_artifact_binding": prompt_artifact_binding.to_dict(),
            "config_artifact_binding": config_artifact_binding.to_dict(),
            "local_request_binding": local_request_binding.to_dict(),
        }

    @staticmethod
    def _validate_request_matches_selected(
        manifest: D17Path3TierAManifest,
        selected: D17Path3SelectedInput,
        local_request: D17Path3LocalRequestArtifact,
    ) -> None:
        input_artifact = local_request.input_view_artifact
        if (
            input_artifact.manifest_id != manifest.manifest_id
            or input_artifact.selection_record.to_dict()
            != selected.selection_record.to_dict()
            or input_artifact.provider_visible_input.to_dict()
            != selected.provider_visible_input.to_dict()
            or input_artifact.input_view_id != selected.selection_record.input_view_id
        ):
            _fail("audit_live_cross_binding_invalid")

    def _root(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "authorization": self.authorization,
            "d17_path": self.d17_path,
            "local_only": self.local_only,
            "not_sent": self.not_sent,
            "external_egress_allowed": self.external_egress_allowed,
            "provider_invocation_state": self.provider_invocation_state,
            "manifest_id": self.manifest_id,
            "field_policy_identity": self.field_policy_identity,
            "field_policy_snapshot_sha256": self.field_policy_snapshot_sha256,
            "source_attestation": self.source_attestation.to_dict(),
            "visibility_declaration": self.visibility_declaration.to_dict(),
            "exclusion_declaration": self.exclusion_declaration.to_dict(),
            "selection_record_binding": self.selection_record_binding.to_dict(),
            "complete_local_context_sha256": self.complete_local_context_sha256,
            "input_view_binding": self.input_view_binding.to_dict(),
            "input_view_artifact_binding": self.input_view_artifact_binding.to_dict(),
            "prompt_artifact_binding": self.prompt_artifact_binding.to_dict(),
            "config_artifact_binding": self.config_artifact_binding.to_dict(),
            "local_request_binding": self.local_request_binding.to_dict(),
        }

    def validate(self) -> None:
        if (
            self.schema_version
            != D17_PATH3_TIER_A_PRE_INVOCATION_AUDIT_SCHEMA_VERSION
            or self.authorization != _AUTHORIZATION
            or self.d17_path != _PATH_3
        ):
            _fail("audit_schema_invalid")
        if (
            self.local_only is not True
            or self.not_sent is not True
            or self.external_egress_allowed is not False
            or self.provider_invocation_state != _PRE_INVOCATION_STATE
        ):
            _fail("audit_local_boundary_invalid")
        self.visibility_declaration.validate()
        manifest = _derived_manifest(self.visibility_declaration.structural_signal_names)
        if (
            self.manifest_id != manifest.manifest_id
            or self.field_policy_identity != D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY
            or self.field_policy_snapshot_sha256
            != manifest.field_policy.approved_snapshot_sha256()
        ):
            _fail("audit_manifest_binding_invalid")
        self.source_attestation.validate(manifest)
        self.exclusion_declaration.validate(manifest)
        self.selection_record_binding.validate(expected_kind="input_selection_record")
        self.input_view_binding.validate(expected_kind="provider_visible_input_view")
        self.input_view_artifact_binding.validate(expected_kind="input_view_artifact")
        self.prompt_artifact_binding.validate(expected_kind="prompt_artifact")
        self.config_artifact_binding.validate(expected_kind="config_artifact")
        self.local_request_binding.validate(expected_kind="local_request_artifact")
        _sha256_hex(
            self.complete_local_context_sha256, code="audit_selection_binding_invalid"
        )
        if (
            not self.selection_record_binding.artifact_id.startswith("d17-input-selection-")
            or not self.input_view_binding.artifact_id.startswith("d17-input-view-")
            or not self.input_view_artifact_binding.artifact_id.startswith(
                "d17-input-view-artifact-"
            )
            or not self.prompt_artifact_binding.artifact_id.startswith("d17-prompt-")
            or not self.config_artifact_binding.artifact_id.startswith("d17-config-")
            or not self.local_request_binding.artifact_id.startswith("d17-local-request-")
        ):
            _fail("audit_artifact_binding_invalid")
        if self.audit_record_id != _identity("d17-pre-invocation-audit-", self._root()):
            _fail("audit_identity_invalid")

    def validate_against(
        self,
        context: AgentContextBundle,
        manifest: D17Path3TierAManifest,
        selected: D17Path3SelectedInput,
        local_request: D17Path3LocalRequestArtifact,
    ) -> None:
        manifest = _validated_manifest(manifest)
        self.validate()
        expected = self.create(context, manifest, selected, local_request)
        if self.to_dict() != expected.to_dict():
            _fail("audit_live_cross_binding_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        result = self._root()
        result["audit_record_id"] = self.audit_record_id
        return result

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def sha256(self) -> str:
        return _sha256(self.canonical_bytes())


def create_d17_path3_pre_invocation_audit_record(
    context: AgentContextBundle,
    manifest: D17Path3TierAManifest,
    selected: D17Path3SelectedInput,
    local_request: D17Path3LocalRequestArtifact,
) -> D17Path3TierAPreInvocationAuditRecord:
    """Create a local-only, payload-free audit record before any invocation."""

    return D17Path3TierAPreInvocationAuditRecord.create(
        context,
        manifest,
        selected,
        local_request,
    )
