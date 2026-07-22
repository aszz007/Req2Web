"""Immutable repair-policy authorization over validated detector reports.

This module freezes the current M2 mechanical authorization boundary.  It only
consumes a validated ``FaultDetectionReport`` and never reads bundle files or
performs a repair, patch, or delivery action.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .detector import (
    AMBIGUOUS_MULTIPLE_FAULTS,
    DOM_COMPONENT_STABLE_ID_MISMATCH,
    FAULT_DETECTED,
    NO_FAULT_DETECTED,
    PACKAGE_MANIFEST_PATH_MISMATCH,
    PACKAGE_MANIFEST_SHA256_MISMATCH,
    PAGE_SPEC_DANGLING_COMPONENT_REFERENCE,
    INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH,
    IGNORED_EVIDENCE_MISATTRIBUTED_TO_PAGE_SPEC,
    UNCLASSIFIED_FAILURE,
    FaultDetectionReport,
)


REPAIR_POLICY_SCHEMA_VERSION = "req2web.repair_policy.v1"
REPAIR_AUTHORIZATION_SCHEMA_VERSION = "req2web.repair_authorization.v1"
REPAIR_AUTHORIZATION_MANIFEST_SCHEMA_VERSION = "req2web.repair_authorization_manifest.v1"

NO_ACTION = "no_action"
FALLBACK_REQUIRED = "fallback_required"
DETERMINISTIC_REPAIR_AUTHORIZED = "deterministic_repair_authorized"
REPAIR_AUTHORIZATION_STATUSES = (
    NO_ACTION,
    FALLBACK_REQUIRED,
    DETERMINISTIC_REPAIR_AUTHORIZED,
)

_ACTION_NONE = "none"
_ACTION_DETERMINISTIC_FALLBACK = "deterministic_fallback"
_ACTION_DETERMINISTIC_REPAIR = "deterministic_repair"
_AUTHORIZATION_FILE = "repair_authorization.json"
_AUTHORIZATION_MANIFEST_FILE = "repair_authorization_manifest.json"


class RepairPolicyError(ValueError):
    """The policy, authorization input, or static writer failed closed."""


@dataclass(frozen=True)
class RepairPolicyRule:
    """One pre-registered M2 error-code authorization rule."""

    rule_id: str
    error_code: str
    authorization_mode: str
    policy_allowed_scope: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "authorization_mode": self.authorization_mode,
            "error_code": self.error_code,
            "policy_allowed_scope": list(self.policy_allowed_scope),
        }

    def validate(self) -> None:
        _require_text(self.rule_id, "rule_id")
        if self.error_code not in _REGISTERED_ERROR_CODES:
            raise RepairPolicyError("policy rule error_code is not registered")
        if self.authorization_mode not in {"deterministic_mechanical", "fallback_only"}:
            raise RepairPolicyError("policy rule authorization_mode is unsupported")
        _validate_path_tuple(self.policy_allowed_scope, "policy_allowed_scope")
        if self.authorization_mode == "deterministic_mechanical":
            if not self.policy_allowed_scope:
                raise RepairPolicyError("mechanical policy rule must have an allowed scope")
        elif self.policy_allowed_scope:
            raise RepairPolicyError("fallback-only policy rule must have an empty allowed scope")
        expected_id = "repair-policy-rule-" + _canonical_sha256(self.to_payload())[:20]
        if self.rule_id != expected_id:
            raise RepairPolicyError("rule_id does not match canonical policy payload")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"rule_id": self.rule_id, **self.to_payload()}


@dataclass(frozen=True)
class RepairPolicyRegistry:
    """Versioned immutable registry for the approved M2 mechanical rules."""

    policy_id: str
    policy_version: str
    policy_sha256: str
    rules: tuple[RepairPolicyRule, ...]
    schema_version: str = REPAIR_POLICY_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "rules": [rule.to_dict() for rule in self.rules],
            "schema_version": self.schema_version,
        }

    def validate(self) -> None:
        _require_text(self.policy_id, "policy_id")
        _require_text(self.policy_version, "policy_version")
        if self.schema_version != REPAIR_POLICY_SCHEMA_VERSION:
            raise RepairPolicyError("unsupported repair policy schema")
        _require_sha256(self.policy_sha256, "policy_sha256")
        if tuple(sorted(self.rules, key=lambda rule: rule.error_code)) != self.rules:
            raise RepairPolicyError("policy rules must use canonical error-code order")
        for rule in self.rules:
            rule.validate()
        codes = tuple(rule.error_code for rule in self.rules)
        if codes != tuple(sorted(_REGISTERED_ERROR_CODES)):
            raise RepairPolicyError("policy registry must define every registered error code exactly once")
        expected_hash = _canonical_sha256(self.to_payload())
        if self.policy_sha256 != expected_hash:
            raise RepairPolicyError("policy_sha256 does not match canonical policy payload")
        expected_id = "repair-policy-" + expected_hash[:20]
        if self.policy_id != expected_id:
            raise RepairPolicyError("policy_id does not match canonical policy payload")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "policy_id": self.policy_id,
            "policy_sha256": self.policy_sha256,
            **self.to_payload(),
        }


@dataclass(frozen=True)
class RepairAuthorization:
    """Canonical, report-bound authorization decision without a repair action."""

    authorization_id: str
    report_id: str
    report_sha256: str
    report_status: str
    policy_id: str
    policy_version: str
    policy_sha256: str
    status: str
    action: str
    error_code: str
    predicted_repair_scope: tuple[str, ...]
    predicted_repairable: bool
    policy_allowed_scope: tuple[str, ...]
    effective_repair_scope: tuple[str, ...]
    reason: str
    fallback_recommendation: str
    schema_version: str = REPAIR_AUTHORIZATION_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return {
            "action": self.action,
            "effective_repair_scope": list(self.effective_repair_scope),
            "error_code": self.error_code,
            "fallback_recommendation": self.fallback_recommendation,
            "policy_allowed_scope": list(self.policy_allowed_scope),
            "policy_id": self.policy_id,
            "policy_sha256": self.policy_sha256,
            "policy_version": self.policy_version,
            "predicted_repair_scope": list(self.predicted_repair_scope),
            "predicted_repairable": self.predicted_repairable,
            "reason": self.reason,
            "report_id": self.report_id,
            "report_sha256": self.report_sha256,
            "report_status": self.report_status,
            "schema_version": self.schema_version,
            "status": self.status,
        }

    def validate(self) -> None:
        for field_name in (
            "authorization_id", "report_id", "report_status", "policy_id",
            "policy_version", "status", "action", "error_code", "reason",
            "fallback_recommendation",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.schema_version != REPAIR_AUTHORIZATION_SCHEMA_VERSION:
            raise RepairPolicyError("unsupported repair authorization schema")
        _require_sha256(self.report_sha256, "report_sha256")
        _require_sha256(self.policy_sha256, "policy_sha256")
        if self.report_status not in {
            NO_FAULT_DETECTED,
            FAULT_DETECTED,
            UNCLASSIFIED_FAILURE,
            AMBIGUOUS_MULTIPLE_FAULTS,
        }:
            raise RepairPolicyError("authorization report_status is unsupported")
        if self.status not in REPAIR_AUTHORIZATION_STATUSES:
            raise RepairPolicyError("authorization status is unsupported")
        _validate_path_tuple(self.predicted_repair_scope, "predicted_repair_scope")
        if not isinstance(self.predicted_repairable, bool):
            raise RepairPolicyError("predicted_repairable must be a boolean")
        _validate_path_tuple(self.policy_allowed_scope, "policy_allowed_scope")
        _validate_path_tuple(self.effective_repair_scope, "effective_repair_scope")
        registry = _frozen_policy_registry()
        registry.validate()
        if (
            self.policy_id != registry.policy_id
            or self.policy_version != registry.policy_version
            or self.policy_sha256 != registry.policy_sha256
        ):
            raise RepairPolicyError("authorization does not bind the frozen policy registry")
        expected = _authorization_decision_from_fields(
            report_status=self.report_status,
            error_code=self.error_code,
            predicted_scope=self.predicted_repair_scope,
            policy=registry,
            predicted_repairable=self.predicted_repairable,
        )
        for field_name in (
            "status", "action", "policy_allowed_scope", "effective_repair_scope",
            "reason", "fallback_recommendation",
        ):
            if getattr(self, field_name) != getattr(expected, field_name):
                raise RepairPolicyError(
                    "authorization " + field_name + " does not match frozen policy evaluation"
                )
        expected_id = "repair-authorization-" + _canonical_sha256(self.to_payload())[:20]
        if self.authorization_id != expected_id:
            raise RepairPolicyError("authorization_id does not match canonical payload")

    def validate_against(self, report: FaultDetectionReport) -> None:
        """Validate integrity and exact binding to the referenced detector report."""
        if not isinstance(report, FaultDetectionReport):
            raise RepairPolicyError("report must be FaultDetectionReport")
        report.validate()
        self.validate()
        expected_bindings = {
            "report_id": report.report_id,
            "report_sha256": report.sha256(),
            "report_status": report.status,
            "error_code": report.predicted_error_code,
            "predicted_repairable": report.predicted_repairable,
            "predicted_repair_scope": report.predicted_repair_scope,
        }
        for field_name, expected_value in expected_bindings.items():
            if getattr(self, field_name) != expected_value:
                raise RepairPolicyError(
                    "authorization does not match referenced report " + field_name
                )

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {"authorization_id": self.authorization_id, **self.to_payload()}

    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())


@dataclass(frozen=True)
class _AuthorizationDecision:
    status: str
    action: str
    policy_allowed_scope: tuple[str, ...]
    effective_repair_scope: tuple[str, ...]
    reason: str
    fallback_recommendation: str


_REGISTERED_ERROR_CODES = frozenset({
    DOM_COMPONENT_STABLE_ID_MISMATCH,
    PACKAGE_MANIFEST_PATH_MISMATCH,
    PACKAGE_MANIFEST_SHA256_MISMATCH,
    PAGE_SPEC_DANGLING_COMPONENT_REFERENCE,
    INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH,
    IGNORED_EVIDENCE_MISATTRIBUTED_TO_PAGE_SPEC,
})


def authorize_fault_detection_report(report: FaultDetectionReport) -> RepairAuthorization:
    """Authorize only exact, pre-registered deterministic mechanical scopes."""
    if not isinstance(report, FaultDetectionReport):
        raise RepairPolicyError("report must be FaultDetectionReport")
    report.validate()
    registry = _frozen_policy_registry()
    registry.validate()
    decision = _authorization_decision_from_fields(
        report_status=report.status,
        error_code=report.predicted_error_code,
        predicted_scope=report.predicted_repair_scope,
        policy=registry,
        predicted_repairable=report.predicted_repairable,
    )
    payload = {
        "action": decision.action,
        "effective_repair_scope": list(decision.effective_repair_scope),
        "error_code": report.predicted_error_code,
        "fallback_recommendation": decision.fallback_recommendation,
        "policy_allowed_scope": list(decision.policy_allowed_scope),
        "policy_id": registry.policy_id,
        "policy_sha256": registry.policy_sha256,
        "policy_version": registry.policy_version,
        "predicted_repair_scope": list(report.predicted_repair_scope),
        "predicted_repairable": report.predicted_repairable,
        "reason": decision.reason,
        "report_id": report.report_id,
        "report_sha256": report.sha256(),
        "report_status": report.status,
        "schema_version": REPAIR_AUTHORIZATION_SCHEMA_VERSION,
        "status": decision.status,
    }
    authorization = RepairAuthorization(
        authorization_id="repair-authorization-" + _canonical_sha256(payload)[:20],
        report_id=report.report_id,
        report_sha256=report.sha256(),
        report_status=report.status,
        policy_id=registry.policy_id,
        policy_version=registry.policy_version,
        policy_sha256=registry.policy_sha256,
        status=decision.status,
        action=decision.action,
        error_code=report.predicted_error_code,
        predicted_repair_scope=report.predicted_repair_scope,
        predicted_repairable=report.predicted_repairable,
        policy_allowed_scope=decision.policy_allowed_scope,
        effective_repair_scope=decision.effective_repair_scope,
        reason=decision.reason,
        fallback_recommendation=decision.fallback_recommendation,
    )
    authorization.validate_against(report)
    return authorization


def write_repair_authorization(
    authorization: RepairAuthorization,
    output_dir: Path,
) -> RepairAuthorization:
    """Write one canonical authorization and integrity manifest to an empty directory."""
    if not isinstance(authorization, RepairAuthorization):
        raise RepairPolicyError("authorization must be RepairAuthorization")
    authorization.validate()
    destination = _prepare_empty_output_directory(Path(output_dir))
    authorization_bytes = _canonical_json_bytes(authorization.to_dict())
    _write_exact_bytes(destination / _AUTHORIZATION_FILE, authorization_bytes)
    manifest = {
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization.sha256(),
        "files": [{
            "path": _AUTHORIZATION_FILE,
            "sha256": sha256(authorization_bytes).hexdigest(),
            "size": len(authorization_bytes),
        }],
        "schema_version": REPAIR_AUTHORIZATION_MANIFEST_SCHEMA_VERSION,
    }
    manifest_bytes = _canonical_json_bytes(manifest)
    _write_exact_bytes(destination / _AUTHORIZATION_MANIFEST_FILE, manifest_bytes)
    actual = {
        path.name: path.read_bytes()
        for path in sorted(destination.iterdir())
        if path.is_file()
    }
    expected = {
        _AUTHORIZATION_FILE: authorization_bytes,
        _AUTHORIZATION_MANIFEST_FILE: manifest_bytes,
    }
    if actual != expected:
        raise RepairPolicyError("repair authorization writer did not round-trip exact bytes")
    loaded_manifest = _load_json_object(actual[_AUTHORIZATION_MANIFEST_FILE], _AUTHORIZATION_MANIFEST_FILE)
    if loaded_manifest != manifest:
        raise RepairPolicyError("repair authorization manifest did not round-trip")
    if sha256(actual[_AUTHORIZATION_FILE]).hexdigest() != manifest["files"][0]["sha256"]:
        raise RepairPolicyError("repair authorization hash did not round-trip")
    return authorization


def _authorization_decision_from_fields(
    *,
    report_status: str,
    error_code: str,
    predicted_scope: tuple[str, ...],
    policy: RepairPolicyRegistry,
    predicted_repairable: bool = False,
) -> _AuthorizationDecision:
    """Evaluate frozen policy facts without inspecting any artifact bytes."""
    _validate_path_tuple(predicted_scope, "predicted_scope")
    if not isinstance(predicted_repairable, bool):
        raise RepairPolicyError("predicted_repairable must be a boolean")
    if report_status == NO_FAULT_DETECTED:
        if predicted_repairable:
            raise RepairPolicyError("no-fault report must not predict repairability")
        if error_code != "none" or predicted_scope:
            raise RepairPolicyError("no-fault report fields must not carry a repair scope")
        return _AuthorizationDecision(
            status=NO_ACTION,
            action=_ACTION_NONE,
            policy_allowed_scope=(),
            effective_repair_scope=(),
            reason="validated_detector_report_has_no_fault",
            fallback_recommendation="not_applicable",
        )
    if report_status in {UNCLASSIFIED_FAILURE, AMBIGUOUS_MULTIPLE_FAULTS}:
        if predicted_repairable:
            raise RepairPolicyError(
                "unclassified or ambiguous report must not predict repairability"
            )
        if predicted_scope:
            raise RepairPolicyError("unclassified or ambiguous report must not carry a repair scope")
        return _fallback_decision(
            reason="detector_report_status_requires_fallback",
        )
    if report_status != FAULT_DETECTED:
        raise RepairPolicyError("fault detection report status is unsupported")
    rule = _rule_for_error_code(policy, error_code)
    effective_scope = _scope_intersection(predicted_scope, rule.policy_allowed_scope)
    if rule.authorization_mode != "deterministic_mechanical":
        if predicted_repairable:
            raise RepairPolicyError("fallback-only error must not predict repairability")
        if predicted_scope:
            raise RepairPolicyError("fallback-only error must not carry a repair scope")
        return _fallback_decision(
            reason="frozen_policy_disallows_automatic_repair",
        )
    if not predicted_repairable:
        return _fallback_decision(
            policy_allowed_scope=rule.policy_allowed_scope,
            effective_repair_scope=effective_scope,
            reason="detector_did_not_predict_mechanical_repairability",
        )
    if predicted_scope != rule.policy_allowed_scope:
        return _fallback_decision(
            policy_allowed_scope=rule.policy_allowed_scope,
            effective_repair_scope=effective_scope,
            reason="predicted_scope_must_exactly_match_frozen_policy_scope",
        )
    if effective_scope != rule.policy_allowed_scope:
        return _fallback_decision(
            policy_allowed_scope=rule.policy_allowed_scope,
            effective_repair_scope=effective_scope,
            reason="effective_scope_does_not_cover_frozen_policy_scope",
        )
    return _AuthorizationDecision(
        status=DETERMINISTIC_REPAIR_AUTHORIZED,
        action=_ACTION_DETERMINISTIC_REPAIR,
        policy_allowed_scope=rule.policy_allowed_scope,
        effective_repair_scope=effective_scope,
        reason="predicted_scope_exactly_matches_frozen_policy_scope",
        fallback_recommendation="deterministic_fallback_if_authorized_repair_not_applied",
    )


def _fallback_decision(
    *,
    policy_allowed_scope: tuple[str, ...] = (),
    effective_repair_scope: tuple[str, ...] = (),
    reason: str,
) -> _AuthorizationDecision:
    return _AuthorizationDecision(
        status=FALLBACK_REQUIRED,
        action=_ACTION_DETERMINISTIC_FALLBACK,
        policy_allowed_scope=policy_allowed_scope,
        effective_repair_scope=effective_repair_scope,
        reason=reason,
        fallback_recommendation="deterministic_fallback",
    )


def _rule_for_error_code(policy: RepairPolicyRegistry, error_code: str) -> RepairPolicyRule:
    for rule in policy.rules:
        if rule.error_code == error_code:
            return rule
    raise RepairPolicyError("fault error_code is not registered in the frozen policy")


def _scope_intersection(
    predicted_scope: tuple[str, ...],
    policy_scope: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(sorted(set(predicted_scope).intersection(policy_scope)))


def _frozen_policy_registry() -> RepairPolicyRegistry:
    rules = tuple(sorted((
        _make_rule(
            DOM_COMPONENT_STABLE_ID_MISMATCH,
            "deterministic_mechanical",
            (
                "artifact/render/index.html",
                "artifact/render/render_manifest.json",
            ),
        ),
        _make_rule(
            INSPECTOR_TRACE_RELATION_INTEGRITY_MISMATCH,
            "fallback_only",
            (),
        ),
        _make_rule(
            IGNORED_EVIDENCE_MISATTRIBUTED_TO_PAGE_SPEC,
            "fallback_only",
            (),
        ),
        _make_rule(
            PACKAGE_MANIFEST_PATH_MISMATCH,
            "deterministic_mechanical",
            ("artifact/result_package/package_manifest.json",),
        ),
        _make_rule(
            PACKAGE_MANIFEST_SHA256_MISMATCH,
            "deterministic_mechanical",
            ("artifact/result_package/package_manifest.json",),
        ),
        _make_rule(
            PAGE_SPEC_DANGLING_COMPONENT_REFERENCE,
            "fallback_only",
            (),
        ),
    ), key=lambda rule: rule.error_code))
    payload = {
        "policy_version": "m2-08a-v1",
        "rules": [rule.to_dict() for rule in rules],
        "schema_version": REPAIR_POLICY_SCHEMA_VERSION,
    }
    policy_hash = _canonical_sha256(payload)
    return RepairPolicyRegistry(
        policy_id="repair-policy-" + policy_hash[:20],
        policy_version=payload["policy_version"],
        policy_sha256=policy_hash,
        rules=rules,
    )


def _make_rule(
    error_code: str,
    authorization_mode: str,
    policy_allowed_scope: tuple[str, ...],
) -> RepairPolicyRule:
    payload = {
        "authorization_mode": authorization_mode,
        "error_code": error_code,
        "policy_allowed_scope": list(policy_allowed_scope),
    }
    return RepairPolicyRule(
        rule_id="repair-policy-rule-" + _canonical_sha256(payload)[:20],
        error_code=error_code,
        authorization_mode=authorization_mode,
        policy_allowed_scope=policy_allowed_scope,
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def _load_json_object(content: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RepairPolicyError(name + " must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise RepairPolicyError(name + " must be a JSON object")
    return value


def _validate_path_tuple(values: tuple[str, ...], field_name: str) -> None:
    if not isinstance(values, tuple):
        raise RepairPolicyError(field_name + " must be a tuple")
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise RepairPolicyError(field_name + " must be sorted and unique")
    for value in values:
        if not _is_safe_relative_posix(value):
            raise RepairPolicyError(field_name + " contains an unsafe path")


def _is_safe_relative_posix(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RepairPolicyError(field_name + " must be non-empty text")


def _require_sha256(value: object, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise RepairPolicyError(field_name + " must be a lowercase SHA-256")


def _prepare_empty_output_directory(output_dir: Path) -> Path:
    destination = output_dir.absolute()
    _reject_symlink_ancestors(destination)
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise RepairPolicyError("output_dir must be a real directory path")
        if any(destination.iterdir()):
            raise RepairPolicyError("output_dir must be empty; refusing to overwrite")
    else:
        destination.mkdir(parents=True, exist_ok=False)
    return destination


def _reject_symlink_ancestors(path: Path) -> None:
    for candidate in (path.absolute(), *path.absolute().parents):
        if candidate.exists() and candidate.is_symlink():
            raise RepairPolicyError("symlinked output paths are not allowed")


def _write_exact_bytes(path: Path, content: bytes) -> None:
    _reject_symlink_ancestors(path.parent)
    if path.exists():
        raise RepairPolicyError("writer refuses to overwrite a file")
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise RepairPolicyError("written bytes did not round-trip")