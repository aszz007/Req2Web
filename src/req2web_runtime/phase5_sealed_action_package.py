"""Owner-sealed Phase 5 action package and Path 1 static projections.

Real packages may contain the frozen Provider-visible requirement projection
and licensed short evidence summaries, but never gold. They must be created and
stored outside the repository by the project owner. Repository tests use only
the synthetic fixture.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence


SCHEMA_VERSION = "req2web.phase5.sealed_action_package.v1"
PROJECTION_SCHEMA_VERSION = "req2web.phase5.path1.static_provider_projection.v1"
FORMAL_AUTHORITY_SHA256 = (
    "60ea4aabd352ae5ac863a963a9963cf576ca2334354f8708a43771038861eb87"
)
FORMAL_PLAN_SHA256 = (
    "3f71e4b1b7178ac3b619b0c79d8d61ad9c0ca7c81c2ce6082f47b86fa3c6cd0b"
)
RTX5090_PROFILE_SHA256 = (
    "db47a61e183cfa8e809c19c41f68857b5f7e45fd0083dea7ec2e9606631e5f03"
)
SINGLE_OWNER_PROTOCOL_SHA256 = (
    "0b6168ae7a5db7a34bdbcf407fed75f2e024aaa38a97b861d29dfee5040c1fe6"
)
OWNER_CUSTODY_LAYOUT_SHA256 = (
    "a9aaf0e2fa6ca8c98ab3f735877a19e48c38ee464ee8e7a2c08aaaf982d6065b"
)
_PACKAGE_KINDS = (
    "owner_sealed_formal_h1",
    "synthetic_validation_only",
)
_NODES = ("F1", "F2", "F3", "F4")
_ROLE_ORDER = (
    "ui_reference",
    "interaction_flow",
    "implementation",
    "validation",
)
_NODE_ROLES = {
    "F1": ("ui_reference", "implementation"),
    "F2": ("ui_reference", "implementation"),
    "F3": ("interaction_flow",),
    "F4": ("validation",),
}
_INTERVENTIONS = (
    "irrelevant_evidence",
    "none",
    "remove_critical_role",
)
_EVIDENCE_SIGNALS = ("baseline", "critical", "irrelevant")
_PROHIBITED_KEYS = {
    "acceptance_verdict",
    "block_hint",
    "core_or_reserve_identity",
    "deep_label_gold",
    "deep_labels",
    "duplicate_manual_adjudication",
    "evaluator_gold",
    "evaluator_score",
    "gold",
    "gold_payload",
    "group_hint",
    "owner_score",
    "reference_only_asset",
    "rico_asset",
    "secret",
    "source_path",
    "third_party_original_text",
    "uri",
}
_ABSOLUTE_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_id(prefix: str, value: object) -> str:
    return f"{prefix}-{sha256(_canonical(value)).hexdigest()}"


def _exact(value: object, keys: Sequence[str], name: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ValueError(f"{name} has invalid keys")
    return dict(value)


def _array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return list(value)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value


def _digest(value: object, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _optional_digest(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _digest(value, name)


def _commit(value: object, name: str) -> str:
    value = _text(value, name)
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase 40-character commit")
    return value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _sorted_texts(value: object, name: str, *, allow_empty: bool = False) -> list[str]:
    values = [_text(item, f"{name} item") for item in _array(value, name)]
    if (not allow_empty and not values) or values != sorted(set(values)):
        raise ValueError(f"{name} must be sorted and unique")
    return values


def _scan_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_scan_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_scan_keys(item))
    return keys


def _safe_summary(value: object, name: str) -> str:
    value = _text(value, name)
    if (
        "://" in value
        or _ABSOLUTE_WINDOWS_PATH.match(value)
        or value.startswith("/")
        or value.startswith("\\\\")
    ):
        raise ValueError(f"{name} contains a URI or absolute path")
    return value


def _validate_use_case(value: object, name: str) -> dict[str, str]:
    use_case = _exact(
        value,
        (
            "use_case_id",
            "title",
            "actor",
            "goal",
            "expected_outcome",
        ),
        name,
    )
    return {
        key: _text(use_case[key], f"{name}.{key}")
        for key in use_case
    }


def _validate_b_input(value: object, name: str) -> dict[str, object]:
    b_input = _exact(
        value,
        (
            "request_id",
            "requirement_projection",
            "requirement_summary",
            "target_device",
            "task_type",
            "normalized_constraints",
            "canonical_use_cases",
            "structural_signals",
        ),
        name,
    )
    use_cases = [
        _validate_use_case(item, f"{name}.canonical_use_cases")
        for item in _array(
            b_input["canonical_use_cases"],
            f"{name}.canonical_use_cases",
        )
    ]
    if not 2 <= len(use_cases) <= 4:
        raise ValueError(f"{name}.canonical_use_cases must contain 2-4 rows")
    use_case_ids = [item["use_case_id"] for item in use_cases]
    if len(use_case_ids) != len(set(use_case_ids)):
        raise ValueError(f"{name}.canonical_use_cases IDs must be unique")
    return {
        "request_id": _text(b_input["request_id"], f"{name}.request_id"),
        "requirement_projection": _safe_summary(
            b_input["requirement_projection"],
            f"{name}.requirement_projection",
        ),
        "requirement_summary": _safe_summary(
            b_input["requirement_summary"],
            f"{name}.requirement_summary",
        ),
        "target_device": _text(
            b_input["target_device"],
            f"{name}.target_device",
        ),
        "task_type": _text(b_input["task_type"], f"{name}.task_type"),
        "normalized_constraints": _sorted_texts(
            b_input["normalized_constraints"],
            f"{name}.normalized_constraints",
        ),
        "canonical_use_cases": use_cases,
        "structural_signals": _sorted_texts(
            b_input["structural_signals"],
            f"{name}.structural_signals",
            allow_empty=True,
        ),
    }


def _validate_evidence(value: object, name: str) -> dict[str, object]:
    evidence = _exact(
        value,
        (
            "role",
            "opaque_doc_id",
            "adoption_or_intervention_signal",
            "project_authored_nonverbatim_short_summary",
            "license_receipt_sha256",
        ),
        name,
    )
    role = _text(evidence["role"], f"{name}.role")
    if role not in _ROLE_ORDER:
        raise ValueError(f"{name}.role is unsupported")
    signal = _text(
        evidence["adoption_or_intervention_signal"],
        f"{name}.adoption_or_intervention_signal",
    )
    if signal not in _EVIDENCE_SIGNALS:
        raise ValueError(f"{name}.adoption_or_intervention_signal is unsupported")
    return {
        "role": role,
        "opaque_doc_id": _text(
            evidence["opaque_doc_id"],
            f"{name}.opaque_doc_id",
        ),
        "adoption_or_intervention_signal": signal,
        "project_authored_nonverbatim_short_summary": _safe_summary(
            evidence["project_authored_nonverbatim_short_summary"],
            f"{name}.project_authored_nonverbatim_short_summary",
        ),
        "license_receipt_sha256": _digest(
            evidence["license_receipt_sha256"],
            f"{name}.license_receipt_sha256",
        ),
    }


def _evidence_order(item: Mapping[str, object]) -> tuple[int, str, int]:
    return (
        _ROLE_ORDER.index(str(item["role"])),
        str(item["opaque_doc_id"]),
        _EVIDENCE_SIGNALS.index(str(item["adoption_or_intervention_signal"])),
    )


def _validate_runtime_row(value: object, index: int) -> dict[str, object]:
    name = f"sealed action runtime row {index}"
    row = _exact(
        value,
        (
            "row_order",
            "matrix_row_id",
            "opaque_case_ref",
            "runtime_case_id",
            "repeat_index",
            "seed",
            "intervention",
            "b_input",
            "evidence_items",
            "g0_reference",
        ),
        name,
    )
    intervention = _exact(
        row["intervention"],
        ("kind", "critical_role"),
        f"{name}.intervention",
    )
    kind = _text(intervention["kind"], f"{name}.intervention.kind")
    if kind not in _INTERVENTIONS:
        raise ValueError(f"{name}.intervention.kind is unsupported")
    critical_role = intervention["critical_role"]
    if kind == "remove_critical_role":
        critical_role = _text(
            critical_role,
            f"{name}.intervention.critical_role",
        )
        if critical_role not in _ROLE_ORDER:
            raise ValueError(f"{name}.intervention.critical_role is unsupported")
    elif critical_role is not None:
        raise ValueError(f"{name}.intervention.critical_role must remain null")
    evidence = [
        _validate_evidence(item, f"{name}.evidence_items")
        for item in _array(row["evidence_items"], f"{name}.evidence_items")
    ]
    if not evidence or evidence != sorted(evidence, key=_evidence_order):
        raise ValueError(f"{name}.evidence_items must be canonical and non-empty")
    evidence_ids = [
        (item["role"], item["opaque_doc_id"])
        for item in evidence
    ]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError(f"{name}.evidence_items identities must be unique")
    if kind == "remove_critical_role" and not any(
        item["role"] == critical_role
        and item["adoption_or_intervention_signal"] == "critical"
        for item in evidence
    ):
        raise ValueError(f"{name} has no critical evidence for the removed role")
    if kind == "irrelevant_evidence" and not any(
        item["adoption_or_intervention_signal"] == "irrelevant"
        for item in evidence
    ):
        raise ValueError(f"{name} has no irrelevant evidence")
    g0 = _exact(
        row["g0_reference"],
        ("status", "package_id", "package_sha256"),
        f"{name}.g0_reference",
    )
    if g0["status"] != "prefrozen_valid":
        raise ValueError(f"{name}.g0_reference status drifted")
    return {
        "row_order": _integer(row["row_order"], f"{name}.row_order", minimum=1),
        "matrix_row_id": _text(row["matrix_row_id"], f"{name}.matrix_row_id"),
        "opaque_case_ref": _text(
            row["opaque_case_ref"],
            f"{name}.opaque_case_ref",
        ),
        "runtime_case_id": _text(
            row["runtime_case_id"],
            f"{name}.runtime_case_id",
        ),
        "repeat_index": _integer(
            row["repeat_index"],
            f"{name}.repeat_index",
            minimum=1,
        ),
        "seed": _integer(row["seed"], f"{name}.seed"),
        "intervention": {"kind": kind, "critical_role": critical_role},
        "b_input": _validate_b_input(row["b_input"], f"{name}.b_input"),
        "evidence_items": evidence,
        "g0_reference": {
            "status": "prefrozen_valid",
            "package_id": _text(
                g0["package_id"],
                f"{name}.g0_reference.package_id",
            ),
            "package_sha256": _digest(
                g0["package_sha256"],
                f"{name}.g0_reference.package_sha256",
            ),
        },
    }


def _validate_payload(value: object) -> dict[str, object]:
    package = _exact(
        value,
        (
            "package_id",
            "schema_version",
            "package_kind",
            "route",
            "run_id",
            "source_action_commit",
            "authority_bindings",
            "budget",
            "runtime_rows",
            "visibility",
            "action_state",
        ),
        "sealed action package",
    )
    package_kind = _text(package["package_kind"], "package_kind")
    if (
        package["schema_version"] != SCHEMA_VERSION
        or package_kind not in _PACKAGE_KINDS
        or package["route"] != "path_1_licensed_minimal_real_material"
    ):
        raise ValueError("sealed action package authority drifted")
    _text(package["run_id"], "sealed action run id")
    _commit(package["source_action_commit"], "sealed action source commit")
    bindings = _exact(
        package["authority_bindings"],
        (
            "formal_authority_sha256",
            "formal_plan_sha256",
            "rtx5090_profile_sha256",
            "single_owner_protocol_sha256",
            "owner_custody_layout_sha256",
            "formal_manifest_sha256",
            "duplicate_audit_sha256",
            "gold_commitment_sha256",
            "serializer_sha256",
            "provider_parity_sha256",
            "metric_threshold_sha256",
            "g0_inventory_sha256",
            "final_action_receipt_sha256",
        ),
        "sealed action authority bindings",
    )
    expected_static = {
        "formal_authority_sha256": FORMAL_AUTHORITY_SHA256,
        "formal_plan_sha256": FORMAL_PLAN_SHA256,
        "rtx5090_profile_sha256": RTX5090_PROFILE_SHA256,
        "single_owner_protocol_sha256": SINGLE_OWNER_PROTOCOL_SHA256,
        "owner_custody_layout_sha256": OWNER_CUSTODY_LAYOUT_SHA256,
    }
    if {
        key: bindings[key]
        for key in expected_static
    } != expected_static:
        raise ValueError("sealed action static authority binding drifted")
    for key in (
        "formal_manifest_sha256",
        "duplicate_audit_sha256",
        "gold_commitment_sha256",
        "serializer_sha256",
        "provider_parity_sha256",
        "metric_threshold_sha256",
        "g0_inventory_sha256",
    ):
        _digest(bindings[key], f"sealed action binding {key}")
    final_action_receipt = _optional_digest(
        bindings["final_action_receipt_sha256"],
        "sealed action final receipt hash",
    )

    rows = [
        _validate_runtime_row(item, index)
        for index, item in enumerate(
            _array(package["runtime_rows"], "sealed action runtime rows"),
            start=1,
        )
    ]
    if not rows or [row["row_order"] for row in rows] != list(
        range(1, len(rows) + 1)
    ):
        raise ValueError("sealed action runtime row order drifted")
    for key in ("matrix_row_id", "runtime_case_id"):
        values = [row[key] for row in rows]
        if len(values) != len(set(values)):
            raise ValueError(f"sealed action {key} values must be unique")
    independent_case_count = len({row["opaque_case_ref"] for row in rows})
    budget = _exact(
        package["budget"],
        (
            "independent_case_count",
            "runtime_row_count",
            "node_count_per_runtime_row",
            "node_generate_call_cap",
            "actual_generate_started_count",
            "automatic_retry_count",
            "budget_reset_count",
            "time_cap_seconds",
            "cost_cap_minor_units",
            "storage_cap_bytes",
        ),
        "sealed action budget",
    )
    if (
        budget["independent_case_count"] != independent_case_count
        or budget["runtime_row_count"] != len(rows)
        or budget["node_count_per_runtime_row"] != len(_NODES)
        or budget["node_generate_call_cap"] != len(rows) * len(_NODES)
    ):
        raise ValueError("sealed action budget formula drifted")
    for key in (
        "actual_generate_started_count",
        "automatic_retry_count",
        "budget_reset_count",
    ):
        if budget[key] != 0:
            raise ValueError(f"sealed action budget {key} must remain zero")
    for key in ("time_cap_seconds", "cost_cap_minor_units", "storage_cap_bytes"):
        _integer(budget[key], f"sealed action budget {key}", minimum=1)

    visibility = _exact(
        package["visibility"],
        (
            "gold_content_present",
            "owner_scores_present",
            "deep_labels_present",
            "core_or_reserve_identity_present",
            "third_party_original_text_present",
            "uri_or_source_path_present",
        ),
        "sealed action visibility",
    )
    if any(value is not False for value in visibility.values()):
        raise ValueError("sealed action visibility must remain payload-minimal")
    prohibited = _scan_keys(rows) & _PROHIBITED_KEYS
    if prohibited:
        raise ValueError(
            f"sealed action runtime rows contain prohibited keys: {sorted(prohibited)}"
        )
    state = _exact(
        package["action_state"],
        (
            "owner_sealed",
            "contains_real_h1_projection",
            "final_action_authorized",
            "model_action_authorized",
            "gpu_or_remote_action_authorized",
            "external_action_occurred",
            "holdout_executed",
        ),
        "sealed action state",
    )
    if package_kind == "synthetic_validation_only":
        if final_action_receipt is not None or any(
            value is not False
            for value in state.values()
        ):
            raise ValueError("synthetic sealed action package action state drifted")
        if any(
            not str(row["opaque_case_ref"]).startswith("synthetic-")
            for row in rows
        ):
            raise ValueError("synthetic sealed action package has a non-synthetic case")
    else:
        if final_action_receipt is None:
            raise ValueError("formal sealed action package requires a final receipt")
        for key in (
            "owner_sealed",
            "contains_real_h1_projection",
            "final_action_authorized",
            "model_action_authorized",
            "gpu_or_remote_action_authorized",
        ):
            if state[key] is not True:
                raise ValueError(f"formal sealed action state {key} must be true")
        for key in ("external_action_occurred", "holdout_executed"):
            if state[key] is not False:
                raise ValueError(f"formal sealed action state {key} must remain false")

    normalized = {
        **package,
        "package_kind": package_kind,
        "authority_bindings": {
            **bindings,
            "final_action_receipt_sha256": final_action_receipt,
        },
        "budget": budget,
        "runtime_rows": rows,
        "visibility": visibility,
        "action_state": state,
    }
    body = {key: normalized[key] for key in normalized if key != "package_id"}
    if normalized["package_id"] != _record_id("phase5-sealed-action-package", body):
        raise ValueError("sealed action package id drifted")
    return normalized


@dataclass(frozen=True)
class Phase5SealedActionPackage:
    canonical_json: str
    digest_sha256: str

    @classmethod
    def from_dict(cls, value: object) -> "Phase5SealedActionPackage":
        payload = _validate_payload(value)
        canonical = _canonical(payload)
        result = cls(canonical.decode("utf-8"), sha256(canonical).hexdigest())
        result.validate()
        return result

    @classmethod
    def from_json_bytes(cls, value: bytes) -> "Phase5SealedActionPackage":
        if not isinstance(value, bytes) or not value:
            raise ValueError("sealed action package JSON must be non-empty bytes")
        try:
            parsed = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("sealed action package JSON is invalid") from exc
        if _canonical(parsed) != value:
            raise ValueError("sealed action package JSON is not canonical")
        return cls.from_dict(parsed)

    def validate(self) -> None:
        if not isinstance(self.canonical_json, str) or not self.canonical_json:
            raise ValueError("stored sealed action package must be non-empty")
        try:
            parsed = json.loads(self.canonical_json)
        except json.JSONDecodeError as exc:
            raise ValueError("stored sealed action package JSON is invalid") from exc
        canonical = _canonical(parsed)
        if canonical.decode("utf-8") != self.canonical_json:
            raise ValueError("stored sealed action package JSON is not canonical")
        if sha256(canonical).hexdigest() != self.digest_sha256:
            raise ValueError("stored sealed action package digest drifted")
        _validate_payload(parsed)

    def to_dict(self) -> dict[str, object]:
        self.validate()
        value = json.loads(self.canonical_json)
        if not isinstance(value, dict):
            raise ValueError("stored sealed action package root is not an object")
        return value

    def canonical_json_bytes(self) -> bytes:
        self.validate()
        return self.canonical_json.encode("utf-8")

    def sha256(self) -> str:
        self.validate()
        return self.digest_sha256


def create_phase5_sealed_action_package(value: object) -> Phase5SealedActionPackage:
    """Create a package from an owner-side source object whose ID may be omitted."""

    if not isinstance(value, Mapping):
        raise ValueError("sealed action package source must be an object")
    payload = dict(value)
    if "package_id" in payload:
        return Phase5SealedActionPackage.from_dict(payload)
    body = dict(payload)
    return Phase5SealedActionPackage.from_dict(
        {"package_id": _record_id("phase5-sealed-action-package", body), **body}
    )


def build_phase5_node_static_projection(
    package: Phase5SealedActionPackage,
    *,
    matrix_row_id: str,
    node_id: str,
) -> dict[str, object]:
    """Build the model-visible static Path 1 projection for one runtime row."""

    package.validate()
    if node_id not in _NODES:
        raise ValueError("Phase 5 projection node is unsupported")
    rows = [
        row
        for row in package.to_dict()["runtime_rows"]
        if row["matrix_row_id"] == matrix_row_id
    ]
    if len(rows) != 1:
        raise ValueError("Phase 5 projection matrix row is not unique")
    row = rows[0]
    intervention = row["intervention"]
    relevant_roles = _NODE_ROLES[node_id]
    evidence = []
    for item in row["evidence_items"]:
        if item["role"] not in relevant_roles:
            continue
        if (
            intervention["kind"] == "remove_critical_role"
            and item["role"] == intervention["critical_role"]
        ):
            continue
        if (
            item["adoption_or_intervention_signal"] == "irrelevant"
            and intervention["kind"] != "irrelevant_evidence"
        ):
            continue
        evidence.append(item)
    b_input = row["b_input"]
    provider_payload = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "node_id": node_id,
        "provider_case_ref": (
            "phase5-provider-case-"
            + sha256(row["runtime_case_id"].encode("utf-8")).hexdigest()
        ),
        "provider_request_ref": (
            "phase5-provider-request-"
            + sha256(b_input["request_id"].encode("utf-8")).hexdigest()
        ),
        "project_authored_hidden_requirement_projection": b_input[
            "requirement_projection"
        ],
        "requirement_summary": b_input["requirement_summary"],
        "canonical_use_cases": b_input["canonical_use_cases"],
        "normalized_constraints": b_input["normalized_constraints"],
        "target_device": b_input["target_device"],
        "task_type": b_input["task_type"],
        "consumer_specific_preregistered_structural_signals": b_input[
            "structural_signals"
        ],
        "licensed_evidence": evidence,
    }
    if _scan_keys(provider_payload) & _PROHIBITED_KEYS:
        raise ValueError("Phase 5 provider payload contains a prohibited key")
    payload_bytes = _canonical(provider_payload)
    binding = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "package_id": package.to_dict()["package_id"],
        "package_sha256": package.sha256(),
        "matrix_row_id": matrix_row_id,
        "node_id": node_id,
        "provider_payload": provider_payload,
        "provider_payload_sha256": sha256(payload_bytes).hexdigest(),
        "provider_payload_byte_length": len(payload_bytes),
        "gold_visible": False,
        "owner_score_visible": False,
        "core_or_reserve_identity_visible": False,
        "intervention_name_visible": False,
    }
    return {
        "projection_id": _record_id("phase5-path1-static-projection", binding),
        **binding,
    }


def load_phase5_sealed_action_package_file(
    path: Path,
    *,
    owner_only_local_confirmed: bool,
) -> Phase5SealedActionPackage:
    """Load a real package only after explicit owner-side confirmation."""

    if owner_only_local_confirmed is not True:
        raise ValueError("explicit owner-only local confirmation is required")
    if not isinstance(path, Path) or not path.is_file():
        raise ValueError("sealed action package path must be an existing file")
    return Phase5SealedActionPackage.from_json_bytes(path.read_bytes())


def write_phase5_sealed_action_package(
    path: Path,
    package: Phase5SealedActionPackage,
) -> None:
    """Write exact canonical bytes to a new repository-external file."""

    package.validate()
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("sealed action package output path must be absolute")
    if not path.parent.is_dir():
        raise ValueError("sealed action package output parent must exist")
    data = package.canonical_json_bytes()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            path.unlink()
        except OSError:
            pass
        raise
