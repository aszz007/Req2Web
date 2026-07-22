"""Payload-free D17 Tier A path-3 manifest policy and validator.

This module records only the approved field boundary for a future non-H1 path-3
compatibility-pilot request. It deliberately does not contain a selector,
serializer, prompt, Provider invocation, runtime configuration, model loading,
network operation, or Provider-visible payload values.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Mapping


D17_PATH3_TIER_A_FIELD_POLICY_SCHEMA_VERSION = "req2web.d17.path3.tier_a.field_policy.v1"
D17_PATH3_TIER_A_FIELD_POLICY_SHA256_HEX = (
    "fb12efc6a0bd0d61b7ab4fea020c5e8053283b58406da37326d7cc02a7e47438"
)
D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY = (
    f"{D17_PATH3_TIER_A_FIELD_POLICY_SCHEMA_VERSION} / "
    f"sha256:{D17_PATH3_TIER_A_FIELD_POLICY_SHA256_HEX}"
)
D17_PATH3_TIER_A_MANIFEST_SCHEMA_VERSION = "req2web.d17.path3.tier_a.manifest.v1"
D17_PATH3_TIER_A_ERROR_ENVELOPE_SCHEMA_VERSION = "req2web.d17.path3.tier_a.error.v1"

_TIER_A_AUTHORIZATION = "tier_a_local_implementation_only"
_PATH_3 = "path_3"
_PATH_UNAUTHORIZED = "N/A / \u672a\u6388\u6743"
_NON_H1_SCOPE = "future_non_h1_compatibility_pilot_preparation_only"
_PAYLOAD_STATUS = "payload_not_present_in_tier_a_manifest"
_APPROVED_PROVIDER_VISIBLE_FIELDS = (
    "original_requirement",
    "use_cases",
    "constraints",
    "target_device",
    "task_type",
    "structural_signals",
)
_FIXED_PROVIDER_VISIBLE_FIELDS = _APPROVED_PROVIDER_VISIBLE_FIELDS
_APPROVED_USE_CASE_ITEM_KEYS = (
    "use_case_id",
    "title",
    "actor",
    "goal",
    "expected_outcome",
)
_FIXED_USE_CASE_ITEM_KEYS = _APPROVED_USE_CASE_ITEM_KEYS
_APPROVED_ORIGINAL_REQUIREMENT_SOURCE_CLASSES = ("project_authored", "synthetic")
_FIXED_ORIGINAL_REQUIREMENT_SOURCE_CLASSES = _APPROVED_ORIGINAL_REQUIREMENT_SOURCE_CLASSES
_APPROVED_STRUCTURAL_SIGNAL_POLICY = "explicit_per_manifest_enumeration_only"
_FIXED_STRUCTURAL_SIGNAL_POLICY = _APPROVED_STRUCTURAL_SIGNAL_POLICY
_APPROVED_UNKNOWN_OR_UNLISTED_FIELDS_POLICY = "fail_closed"
_FIXED_UNKNOWN_OR_UNLISTED_FIELDS_POLICY = _APPROVED_UNKNOWN_OR_UNLISTED_FIELDS_POLICY
_APPROVED_PROHIBITED_DATA_CATEGORIES = (
    "full_agent_context",
    "requirement_summary",
    "retrieval_queries",
    "retrieval_results",
    "full_retrieval_guidance",
    "guidance_item_values",
    "third_party_evidence_text",
    "evidence_title",
    "evidence_uri",
    "source_path",
    "evidence_doc_id_or_adoption_identifier",
    "rico_or_reference_only_assets",
    "secrets_or_credentials",
    "h1",
    "gold",
    "deep_labels",
    "local_canonical_identity_or_traceability_fields",
    "g0_package_paths_or_bytes",
)
_FIXED_PROHIBITED_DATA_CATEGORIES = _APPROVED_PROHIBITED_DATA_CATEGORIES
_FIXED_SERIALIZER_POLICY = {
    "bom": False,
    "encoding": "UTF-8",
    "exact_byte_length_and_sha256_required": True,
    "prompt_and_config_separately_versioned": True,
    "schema_and_version_fixed": True,
    "unknown_or_unlisted_fields": "fail_closed",
}
_SIGNAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PROHIBITED_SIGNAL_NAME_TOKENS = (
    "adoption",
    "credential",
    "doc_id",
    "evidence",
    "gold",
    "guidance",
    "h1",
    "path",
    "reference",
    "retrieval",
    "rico",
    "secret",
    "source",
    "summary",
    "traceability",
    "uri",
)
_SAFE_ERROR_MESSAGES = {
    "manifest_schema_invalid": "D17 manifest schema is invalid.",
    "manifest_exact_keys_invalid": "D17 manifest has an unapproved field shape.",
    "manifest_identity_invalid": "D17 manifest identity does not bind approved facts.",
    "field_policy_invalid": "D17 field policy is not the approved Tier A policy.",
    "field_policy_identity_invalid": "D17 field policy identity is invalid.",
    "field_policy_snapshot_invalid": "D17 field policy snapshot does not match approval.",
    "path_selection_invalid": "D17 path selection is not authorized for Tier A.",
    "tier_a_boundary_invalid": "D17 Tier A execution boundary is invalid.",
    "prohibited_data_declaration_invalid": "D17 prohibited-data declaration is incomplete.",
    "structural_signal_enumeration_invalid": "D17 structural-signal enumeration is invalid.",
}


@dataclass(frozen=True)
class D17ManifestErrorEnvelope:
    """A fixed, payload-free error record suitable for local audit output."""

    code: str
    schema_version: str = D17_PATH3_TIER_A_ERROR_ENVELOPE_SCHEMA_VERSION
    stage: str = "d17"
    phase: str = "manifest_validation"
    payload_disclosure: str = "none"
    retryable: bool = False

    def to_dict(self) -> dict[str, object]:
        if self.code not in _SAFE_ERROR_MESSAGES:
            raise ValueError("D17 error envelope code is invalid")
        if (
            self.schema_version != D17_PATH3_TIER_A_ERROR_ENVELOPE_SCHEMA_VERSION
            or self.stage != "d17"
            or self.phase != "manifest_validation"
            or self.payload_disclosure != "none"
            or self.retryable is not False
        ):
            raise ValueError("D17 error envelope is invalid")
        return {
            "schema_version": self.schema_version,
            "code": self.code,
            "stage": self.stage,
            "phase": self.phase,
            "payload_disclosure": self.payload_disclosure,
            "retryable": self.retryable,
        }


class D17ManifestValidationError(ValueError):
    """Stable fail-closed error that never embeds caller supplied content."""

    def __init__(self, code: str) -> None:
        if code not in _SAFE_ERROR_MESSAGES:
            raise ValueError("D17 manifest error code is invalid")
        super().__init__(_SAFE_ERROR_MESSAGES[code])
        self.code = code
        self.envelope = D17ManifestErrorEnvelope(code)


def _fail(code: str) -> None:
    raise D17ManifestValidationError(code)


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _approved_provider_visible_fields_snapshot() -> dict[str, object]:
    definitions = {
        "constraints": {
            "content": "normalized",
            "source_classes": list(_FIXED_ORIGINAL_REQUIREMENT_SOURCE_CLASSES),
        },
        "original_requirement": {
            "content": "minimal_requirement_text",
            "source_classes": list(_FIXED_ORIGINAL_REQUIREMENT_SOURCE_CLASSES),
        },
        "structural_signals": {
            "content": "evidence_free",
            "policy": _FIXED_STRUCTURAL_SIGNAL_POLICY,
        },
        "target_device": {"content": "normalized"},
        "task_type": {"content": "normalized"},
        "use_cases": {"content": "normalized_canonical_requirement_structure"},
    }
    return {
        field: definitions.get(field, {"unapproved_field": field})
        for field in _FIXED_PROVIDER_VISIBLE_FIELDS
    }


def _approved_field_policy_snapshot() -> dict[str, object]:
    """Return the exact payload-free approval-packet snapshot for Tier A."""

    return {
        "authorization": _TIER_A_AUTHORIZATION,
        "d17_path": _PATH_3,
        "external_egress_allowed": False,
        "formal_quality_allowed": False,
        "h1_allowed": False,
        "path_1_status": _PATH_UNAUTHORIZED,
        "path_2_status": _PATH_UNAUTHORIZED,
        "provider_visibility_prohibited": list(_FIXED_PROHIBITED_DATA_CATEGORIES),
        "provider_visible_fields": _approved_provider_visible_fields_snapshot(),
        "schema": D17_PATH3_TIER_A_FIELD_POLICY_SCHEMA_VERSION,
        "serializer_policy": dict(_FIXED_SERIALIZER_POLICY),
    }


def _approved_field_policy_snapshot_sha256() -> str:
    return _sha256(_approved_field_policy_snapshot())


def _manifest_id(root: Mapping[str, object]) -> str:
    return f"d17-path3-tier-a-{_sha256(root)}"


def _require_exact_mapping(
    value: object,
    expected_keys: tuple[str, ...],
    *,
    code: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(expected_keys):
        _fail(code)
    return value


def _require_tuple_of_text(value: object, *, code: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(item, str) or not item or item != item.strip() for item in value
    ):
        _fail(code)
    return tuple(value)


@dataclass(frozen=True)
class D17Path3FieldPolicy:
    """Approved path-3 field categories without any Provider-visible values."""

    @staticmethod
    def approved_snapshot() -> dict[str, object]:
        """Return a fresh canonical snapshot of the owner-approved field policy."""

        return _approved_field_policy_snapshot()

    @staticmethod
    def approved_snapshot_sha256() -> str:
        """Return the canonical SHA-256 hex digest for the approval snapshot."""

        return _approved_field_policy_snapshot_sha256()

    schema_version: str
    policy_identity: str
    provider_visible_fields: tuple[str, ...]
    original_requirement_source_classes: tuple[str, ...]
    use_case_item_keys: tuple[str, ...]
    structural_signal_names: tuple[str, ...]
    structural_signal_policy: str
    unknown_or_unlisted_fields: str

    @classmethod
    def create(
        cls, *, structural_signal_names: tuple[str, ...] = ()
    ) -> "D17Path3FieldPolicy":
        policy = cls(
            schema_version=D17_PATH3_TIER_A_FIELD_POLICY_SCHEMA_VERSION,
            policy_identity=(
                D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY
            ),
            provider_visible_fields=_FIXED_PROVIDER_VISIBLE_FIELDS,
            original_requirement_source_classes=_FIXED_ORIGINAL_REQUIREMENT_SOURCE_CLASSES,
            use_case_item_keys=_FIXED_USE_CASE_ITEM_KEYS,
            structural_signal_names=tuple(structural_signal_names),
            structural_signal_policy=_FIXED_STRUCTURAL_SIGNAL_POLICY,
            unknown_or_unlisted_fields=_FIXED_UNKNOWN_OR_UNLISTED_FIELDS_POLICY,
        )
        policy.validate()
        return policy

    @classmethod
    def from_dict(cls, payload: object) -> "D17Path3FieldPolicy":
        data = _require_exact_mapping(
            payload,
            (
                "schema_version",
                "policy_identity",
                "provider_visible_fields",
                "original_requirement_source_classes",
                "use_case_item_keys",
                "structural_signal_names",
                "structural_signal_policy",
                "unknown_or_unlisted_fields",
            ),
            code="field_policy_invalid",
        )
        policy = cls(
            schema_version=data["schema_version"],
            policy_identity=data["policy_identity"],
            provider_visible_fields=_require_tuple_of_text(
                data["provider_visible_fields"], code="field_policy_invalid"
            ),
            original_requirement_source_classes=_require_tuple_of_text(
                data["original_requirement_source_classes"], code="field_policy_invalid"
            ),
            use_case_item_keys=_require_tuple_of_text(
                data["use_case_item_keys"], code="field_policy_invalid"
            ),
            structural_signal_names=_require_tuple_of_text(
                data["structural_signal_names"],
                code="structural_signal_enumeration_invalid",
            ),
            structural_signal_policy=data["structural_signal_policy"],
            unknown_or_unlisted_fields=data["unknown_or_unlisted_fields"],
        )
        policy.validate()
        return policy

    def validate(self) -> None:
        if self.approved_snapshot_sha256() != D17_PATH3_TIER_A_FIELD_POLICY_SHA256_HEX:
            _fail("field_policy_snapshot_invalid")
        if self.schema_version != D17_PATH3_TIER_A_FIELD_POLICY_SCHEMA_VERSION:
            _fail("field_policy_invalid")
        if self.policy_identity != (
            D17_PATH3_TIER_A_FIELD_POLICY_IDENTITY
        ):
            _fail("field_policy_identity_invalid")
        if (
            _FIXED_PROVIDER_VISIBLE_FIELDS != _APPROVED_PROVIDER_VISIBLE_FIELDS
            or _FIXED_USE_CASE_ITEM_KEYS != _APPROVED_USE_CASE_ITEM_KEYS
            or (
                _FIXED_ORIGINAL_REQUIREMENT_SOURCE_CLASSES
                != _APPROVED_ORIGINAL_REQUIREMENT_SOURCE_CLASSES
            )
            or _FIXED_STRUCTURAL_SIGNAL_POLICY != _APPROVED_STRUCTURAL_SIGNAL_POLICY
            or (
                _FIXED_UNKNOWN_OR_UNLISTED_FIELDS_POLICY
                != _APPROVED_UNKNOWN_OR_UNLISTED_FIELDS_POLICY
            )
        ):
            _fail("field_policy_snapshot_invalid")
        if self.provider_visible_fields != _APPROVED_PROVIDER_VISIBLE_FIELDS:
            _fail("field_policy_invalid")
        if (
            self.original_requirement_source_classes
            != _APPROVED_ORIGINAL_REQUIREMENT_SOURCE_CLASSES
        ):
            _fail("field_policy_invalid")
        if self.use_case_item_keys != _APPROVED_USE_CASE_ITEM_KEYS:
            _fail("field_policy_invalid")
        if (
            self.structural_signal_policy != _APPROVED_STRUCTURAL_SIGNAL_POLICY
            or (
                self.unknown_or_unlisted_fields
                != _APPROVED_UNKNOWN_OR_UNLISTED_FIELDS_POLICY
            )
        ):
            _fail("field_policy_invalid")
        if len(set(self.structural_signal_names)) != len(self.structural_signal_names):
            _fail("structural_signal_enumeration_invalid")
        for name in self.structural_signal_names:
            if not isinstance(name, str) or not _SIGNAL_NAME_RE.fullmatch(name):
                _fail("structural_signal_enumeration_invalid")
            if any(token in name for token in _PROHIBITED_SIGNAL_NAME_TOKENS):
                _fail("structural_signal_enumeration_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "policy_identity": self.policy_identity,
            "provider_visible_fields": list(self.provider_visible_fields),
            "original_requirement_source_classes": list(
                self.original_requirement_source_classes
            ),
            "use_case_item_keys": list(self.use_case_item_keys),
            "structural_signal_names": list(self.structural_signal_names),
            "structural_signal_policy": self.structural_signal_policy,
            "unknown_or_unlisted_fields": self.unknown_or_unlisted_fields,
        }


@dataclass(frozen=True)
class D17Path3TierAManifest:
    """Validated Tier A policy manifest, intentionally separate from input bytes."""

    schema_version: str
    authorization: str
    d17_path: str
    path_1_status: str
    path_2_status: str
    execution_scope: str
    external_egress_allowed: bool
    formal_quality_allowed: bool
    h1_allowed: bool
    payload_status: str
    field_policy: D17Path3FieldPolicy
    prohibited_data_declared: bool
    prohibited_data_categories: tuple[str, ...]
    manifest_id: str

    @classmethod
    def create(
        cls, *, structural_signal_names: tuple[str, ...] = ()
    ) -> "D17Path3TierAManifest":
        field_policy = D17Path3FieldPolicy.create(
            structural_signal_names=structural_signal_names
        )
        root = cls._root_for(field_policy=field_policy)
        manifest = cls(
            schema_version=D17_PATH3_TIER_A_MANIFEST_SCHEMA_VERSION,
            authorization=_TIER_A_AUTHORIZATION,
            d17_path=_PATH_3,
            path_1_status=_PATH_UNAUTHORIZED,
            path_2_status=_PATH_UNAUTHORIZED,
            execution_scope=_NON_H1_SCOPE,
            external_egress_allowed=False,
            formal_quality_allowed=False,
            h1_allowed=False,
            payload_status=_PAYLOAD_STATUS,
            field_policy=field_policy,
            prohibited_data_declared=True,
            prohibited_data_categories=_FIXED_PROHIBITED_DATA_CATEGORIES,
            manifest_id=_manifest_id(root),
        )
        manifest.validate()
        return manifest

    @classmethod
    def from_dict(cls, payload: object) -> "D17Path3TierAManifest":
        data = _require_exact_mapping(
            payload,
            (
                "schema_version",
                "authorization",
                "d17_path",
                "path_1_status",
                "path_2_status",
                "execution_scope",
                "external_egress_allowed",
                "formal_quality_allowed",
                "h1_allowed",
                "payload_status",
                "field_policy",
                "prohibited_data_declared",
                "prohibited_data_categories",
                "manifest_id",
            ),
            code="manifest_exact_keys_invalid",
        )
        manifest = cls(
            schema_version=data["schema_version"],
            authorization=data["authorization"],
            d17_path=data["d17_path"],
            path_1_status=data["path_1_status"],
            path_2_status=data["path_2_status"],
            execution_scope=data["execution_scope"],
            external_egress_allowed=data["external_egress_allowed"],
            formal_quality_allowed=data["formal_quality_allowed"],
            h1_allowed=data["h1_allowed"],
            payload_status=data["payload_status"],
            field_policy=D17Path3FieldPolicy.from_dict(data["field_policy"]),
            prohibited_data_declared=data["prohibited_data_declared"],
            prohibited_data_categories=_require_tuple_of_text(
                data["prohibited_data_categories"],
                code="prohibited_data_declaration_invalid",
            ),
            manifest_id=data["manifest_id"],
        )
        manifest.validate()
        return manifest

    @staticmethod
    def _root_for(*, field_policy: D17Path3FieldPolicy) -> dict[str, object]:
        return {
            "schema_version": D17_PATH3_TIER_A_MANIFEST_SCHEMA_VERSION,
            "authorization": _TIER_A_AUTHORIZATION,
            "d17_path": _PATH_3,
            "path_1_status": _PATH_UNAUTHORIZED,
            "path_2_status": _PATH_UNAUTHORIZED,
            "execution_scope": _NON_H1_SCOPE,
            "external_egress_allowed": False,
            "formal_quality_allowed": False,
            "h1_allowed": False,
            "payload_status": _PAYLOAD_STATUS,
            "field_policy": field_policy.to_dict(),
            "prohibited_data_declared": True,
            "prohibited_data_categories": list(_FIXED_PROHIBITED_DATA_CATEGORIES),
        }

    def _root(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "authorization": self.authorization,
            "d17_path": self.d17_path,
            "path_1_status": self.path_1_status,
            "path_2_status": self.path_2_status,
            "execution_scope": self.execution_scope,
            "external_egress_allowed": self.external_egress_allowed,
            "formal_quality_allowed": self.formal_quality_allowed,
            "h1_allowed": self.h1_allowed,
            "payload_status": self.payload_status,
            "field_policy": self.field_policy.to_dict(),
            "prohibited_data_declared": self.prohibited_data_declared,
            "prohibited_data_categories": list(self.prohibited_data_categories),
        }

    def validate(self) -> None:
        if self.schema_version != D17_PATH3_TIER_A_MANIFEST_SCHEMA_VERSION:
            _fail("manifest_schema_invalid")
        if self.authorization != _TIER_A_AUTHORIZATION:
            _fail("tier_a_boundary_invalid")
        if self.d17_path != _PATH_3:
            _fail("path_selection_invalid")
        if self.path_1_status != _PATH_UNAUTHORIZED or self.path_2_status != _PATH_UNAUTHORIZED:
            _fail("path_selection_invalid")
        if self.execution_scope != _NON_H1_SCOPE:
            _fail("tier_a_boundary_invalid")
        if (
            self.external_egress_allowed is not False
            or self.formal_quality_allowed is not False
            or self.h1_allowed is not False
            or self.payload_status != _PAYLOAD_STATUS
        ):
            _fail("tier_a_boundary_invalid")
        if self.prohibited_data_declared is not True:
            _fail("prohibited_data_declaration_invalid")
        if self.prohibited_data_categories != _APPROVED_PROHIBITED_DATA_CATEGORIES:
            _fail("prohibited_data_declaration_invalid")
        self.field_policy.validate()
        if self.manifest_id != _manifest_id(self._root()):
            _fail("manifest_identity_invalid")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        payload = self._root()
        payload["manifest_id"] = self.manifest_id
        return payload

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def validate_d17_path3_tier_a_manifest(payload: object) -> D17Path3TierAManifest:
    """Replay a serialized manifest and fail closed on any unapproved change."""

    return D17Path3TierAManifest.from_dict(payload)