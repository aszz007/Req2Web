"""Tier A-only, no-run Qwen runtime-profile placeholder."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping


QWEN_PROFILE_PLACEHOLDER_SCHEMA_VERSION = "req2web.runtime.qwen_profile_placeholder.v1"
_PROFILE_ID_PREFIX = "qwen-profile-placeholder-"
_CANDIDATE_MODEL = "Qwen/Qwen3.5-9B"
_PROFILE_NAMES = ("quality_experiment", "local_smoke", "deterministic_fallback")
_TIER_B_UNAPPROVED = "tier_b_unapproved"
_TIER_B_FIELDS = (
    "model_revision", "service_mode", "runtime_or_image", "artifact_integrity",
    "precision_or_quantization", "context_or_truncation", "decode", "seed_or_determinism",
    "hardware", "resource_caps", "timeout_or_cancel", "provider_retry",
    "network_or_egress", "budget_or_run_caps", "cache_or_retention",
)
_PROFILE_KEYS = (
    "schema_version", "profile_id", "candidate_model", "allowed_profile_names",
    "requested_profile", "selected_profile", "profile_selection_state", *_TIER_B_FIELDS,
    "tier_b_authorization", "external_egress_allowed", "model_loaded", "run_occurred",
)


class QwenProfilePlaceholderError(ValueError):
    """The profile is not the one exact Tier A placeholder."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _exact_mapping(
    value: object, keys: tuple[str, ...], _error=QwenProfilePlaceholderError
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise _error("profile_exact_keys_invalid")
    return value


def _bool(value: object, _error=QwenProfilePlaceholderError) -> bool:
    if type(value) is not bool:
        raise _error("profile_state_invalid")
    return value


def _profile_root_values(
    _schema=QWEN_PROFILE_PLACEHOLDER_SCHEMA_VERSION,
    _candidate=_CANDIDATE_MODEL,
    _profiles=_PROFILE_NAMES,
    _tier_b=_TIER_B_UNAPPROVED,
    _tier_b_fields=_TIER_B_FIELDS,
) -> dict[str, object]:
    return {
        "schema_version": _schema,
        "candidate_model": _candidate,
        "allowed_profile_names": list(_profiles),
        "requested_profile": _tier_b,
        "selected_profile": _tier_b,
        "profile_selection_state": "not_selected",
        **{name: _tier_b for name in _tier_b_fields},
        "tier_b_authorization": _tier_b,
        "external_egress_allowed": False,
        "model_loaded": False,
        "run_occurred": False,
    }


def _profile_root(instance: "QwenProfilePlaceholder", _root_values=_profile_root_values) -> dict[str, object]:
    root = _root_values()
    for key in root:
        root[key] = list(instance.allowed_profile_names) if key == "allowed_profile_names" else getattr(instance, key)
    return root


def _validate_profile(
    instance: "QwenProfilePlaceholder",
    _root=_profile_root,
    _canonical=_canonical_bytes,
    _hash=_sha256,
    _schema=QWEN_PROFILE_PLACEHOLDER_SCHEMA_VERSION,
    _candidate=_CANDIDATE_MODEL,
    _profiles=_PROFILE_NAMES,
    _tier_b=_TIER_B_UNAPPROVED,
    _tier_b_fields=_TIER_B_FIELDS,
    _prefix=_PROFILE_ID_PREFIX,
    _error=QwenProfilePlaceholderError,
) -> None:
    if (
        instance.schema_version != _schema
        or instance.candidate_model != _candidate
        or instance.allowed_profile_names != _profiles
        or instance.requested_profile != _tier_b
        or instance.selected_profile != _tier_b
        or instance.profile_selection_state != "not_selected"
        or instance.tier_b_authorization != _tier_b
        or instance.external_egress_allowed is not False
        or instance.model_loaded is not False
        or instance.run_occurred is not False
        or any(getattr(instance, field) != _tier_b for field in _tier_b_fields)
    ):
        raise _error("profile_state_invalid")
    if instance.profile_id != _prefix + _hash(_canonical(_root(instance)))[:20]:
        raise _error("profile_identity_invalid")


@dataclass(frozen=True)
class QwenProfilePlaceholder:
    """Exact-key declaration; no runtime profile or execution is selected."""

    schema_version: str
    profile_id: str
    candidate_model: str
    allowed_profile_names: tuple[str, ...]
    requested_profile: str
    selected_profile: str
    profile_selection_state: str
    model_revision: str
    service_mode: str
    runtime_or_image: str
    artifact_integrity: str
    precision_or_quantization: str
    context_or_truncation: str
    decode: str
    seed_or_determinism: str
    hardware: str
    resource_caps: str
    timeout_or_cancel: str
    provider_retry: str
    network_or_egress: str
    budget_or_run_caps: str
    cache_or_retention: str
    tier_b_authorization: str
    external_egress_allowed: bool
    model_loaded: bool
    run_occurred: bool

    @classmethod
    def create(
        cls, _root_values=_profile_root_values, _canonical=_canonical_bytes,
        _hash=_sha256, _prefix=_PROFILE_ID_PREFIX, _validator=_validate_profile,
    ) -> "QwenProfilePlaceholder":
        root = _root_values()
        result = cls(
            profile_id=_prefix + _hash(_canonical(root))[:20],
            **{key: tuple(value) if key == "allowed_profile_names" else value for key, value in root.items()},
        )
        _validator(result)
        return result

    @classmethod
    def from_dict(
        cls, payload: object, _keys=_PROFILE_KEYS, _mapping=_exact_mapping,
        _bool_fn=_bool, _validator=_validate_profile, _tier_b_fields=_TIER_B_FIELDS,
        _error=QwenProfilePlaceholderError,
    ) -> "QwenProfilePlaceholder":
        data = _mapping(payload, _keys)
        names = data["allowed_profile_names"]
        if type(names) is not list or any(type(item) is not str for item in names):
            raise _error("profile_state_invalid")
        kwargs = {key: data[key] for key in _tier_b_fields}
        result = cls(
            schema_version=data["schema_version"], profile_id=data["profile_id"],
            candidate_model=data["candidate_model"], allowed_profile_names=tuple(names),
            requested_profile=data["requested_profile"], selected_profile=data["selected_profile"],
            profile_selection_state=data["profile_selection_state"],
            tier_b_authorization=data["tier_b_authorization"],
            external_egress_allowed=_bool_fn(data["external_egress_allowed"]),
            model_loaded=_bool_fn(data["model_loaded"]), run_occurred=_bool_fn(data["run_occurred"]),
            **kwargs,
        )
        _validator(result)
        return result

    @classmethod
    def from_bytes(
        cls,
        raw: object,
        _loads=json.loads,
        _decode_error=json.JSONDecodeError,
        _canonical=_canonical_bytes,
        _error=QwenProfilePlaceholderError,
    ) -> "QwenProfilePlaceholder":
        if type(raw) is not bytes:
            raise _error("profile_bytes_invalid")
        try:
            payload = _loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, _decode_error) as exc:
            raise _error("profile_bytes_invalid") from exc
        result = cls.from_dict(payload)
        if _canonical(result.to_dict()) != raw:
            raise _error("profile_bytes_noncanonical")
        return result

    def _root(self, _root_fn=_profile_root) -> dict[str, object]:
        return _root_fn(self)

    def validate(self, _validator=_validate_profile) -> None:
        _validator(self)

    def to_dict(self, _validator=_validate_profile) -> dict[str, object]:
        _validator(self)
        return {"schema_version": self.schema_version, "profile_id": self.profile_id, **self._root()}

    def canonical_bytes(self, _canonical=_canonical_bytes) -> bytes:
        return _canonical(self.to_dict())

    def sha256(self, _hash=_sha256) -> str:
        return _hash(self.canonical_bytes())


def create_qwen_profile_placeholder(_profile_type=QwenProfilePlaceholder) -> QwenProfilePlaceholder:
    """Create the fixed Tier A placeholder without selecting runtime values."""

    return _profile_type.create()
