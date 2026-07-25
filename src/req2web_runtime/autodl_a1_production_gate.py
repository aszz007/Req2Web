"""Independent Linux-only Real-A1 production evidence gate candidate."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
import sys
from types import MappingProxyType
import weakref
from typing import Mapping

from .autodl_a1_executor import (
    A1EvidenceCommitMarker,
    A1ExecutionReceipt,
    A1ProductionObservation,
    A1ProductionPhaseEvent,
    EVIDENCE_COMMIT_FILENAME,
    OBSERVATION_FILENAME,
    PHASE_EVENT_FILENAME,
    RECEIPT_FILENAME,
    WORKER_AUTHORITY,
    WORKER_VERSION,
)
from .autodl_a1_linux_worker import (
    A1CommittedEvidenceRootValidation,
    validate_committed_evidence_roots,
)

GATE_REPORT_SCHEMA = "req2web.runtime.real_a1_production_gate_report.v1"
GATE_RECEIPT_SCHEMA = "req2web.runtime.real_a1_production_gate_receipt.v1"
GATE_BUNDLE_SCHEMA = "req2web.runtime.real_a1_production_gate_bundle.v1"
_GATE_REPORT_KEYS = (
    "schema_version", "report_id", "validation_mode", "status", "plan_id",
    "plan_sha256", "control_id", "control_sha256", "observation_id",
    "observation_sha256", "receipt_id", "receipt_sha256", "commit_id",
    "commit_sha256", "action_time_git_sha", "worker_authority",
    "worker_version", "payload_tree_sha256", "root_invariants_sha256",
    "gate_evaluated_utc", "authority_state", "manager_a1_passed",
    "a2_unlocked", "failure_codes",
)
_GATE_RECEIPT_KEYS = (
    "schema_version", "gate_receipt_id", "report_id", "report_sha256",
    "plan_id", "control_id", "observation_id", "receipt_id", "commit_id",
    "status", "next_state", "manager_a1_passed", "a2_unlocked",
    "provider_invoked", "project_data_transferred", "h1_allowed",
    "formal_quality_allowed",
)
_GATE_BUNDLE_KEYS = ("schema_version", "bundle_id", "report", "receipt")
_ROOT_INVARIANT_KEYS = (
    "role", "normalized_path_sha256", "marker_id", "marker_sha256",
    "expected_state",
)
_EVIDENCE_FILES = (
    PHASE_EVENT_FILENAME,
    OBSERVATION_FILENAME,
    RECEIPT_FILENAME,
    EVIDENCE_COMMIT_FILENAME,
)
_MAX_FILE_BYTES = MappingProxyType({
    PHASE_EVENT_FILENAME: 1_048_576,
    OBSERVATION_FILENAME: 1_048_576,
    RECEIPT_FILENAME: 262_144,
    EVIDENCE_COMMIT_FILENAME: 262_144,
})


class A1ProductionGateError(ValueError):
    pass


def _build_gate_authorities():
    report_schema = GATE_REPORT_SCHEMA
    receipt_schema = GATE_RECEIPT_SCHEMA
    bundle_schema = GATE_BUNDLE_SCHEMA
    report_keys = tuple(_GATE_REPORT_KEYS)
    receipt_keys = tuple(_GATE_RECEIPT_KEYS)
    bundle_keys = tuple(_GATE_BUNDLE_KEYS)
    root_invariant_keys = tuple(_ROOT_INVARIANT_KEYS)
    evidence_files = tuple(_EVIDENCE_FILES)
    max_file_bytes = MappingProxyType(dict(_MAX_FILE_BYTES))
    mapping_type = Mapping
    mapping_proxy_type = MappingProxyType
    exact_type = type
    dumps = json.dumps
    loads = json.loads
    sha256_ctor = hashlib.sha256
    path_type = Path
    is_regular = stat.S_ISREG
    platform = sys.platform
    datetime_type = datetime
    utc_timezone = timezone.utc
    utc_format = "%Y-%m-%dT%H:%M:%SZ"
    error_type = A1ProductionGateError
    root_validation_type = A1CommittedEvidenceRootValidation
    committed_validator = validate_committed_evidence_roots
    phase_from_dict = A1ProductionPhaseEvent.from_dict
    observation_from_bytes = A1ProductionObservation.from_bytes
    observation_validate_against = A1ProductionObservation.validate_against
    receipt_from_bytes = A1ExecutionReceipt.from_bytes
    receipt_validate_against = A1ExecutionReceipt.validate_against
    commit_from_bytes = A1EvidenceCommitMarker.from_bytes
    commit_validate_against = A1EvidenceCommitMarker.validate_against
    worker_authority = WORKER_AUTHORITY
    worker_version = WORKER_VERSION
    weakref_ref = weakref.ref
    identity_of = id
    manager_decision_registry = {}
    synthetic_decision_registry = {}
    result_registry = {}
    roles = ("control", "package", "model", "evidence")

    def utc_now():
        return datetime_type.now(utc_timezone).strftime(utc_format)

    def parse_utc(value):
        if exact_type(value) is not str:
            raise error_type("gate_time_invalid")
        try:
            return datetime_type.strptime(value, utc_format).replace(tzinfo=utc_timezone)
        except (TypeError, ValueError) as exc:
            raise error_type("gate_time_invalid") from exc

    def canon(value):
        return dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    def digest(raw):
        return sha256_ctor(raw).hexdigest()

    def artifact_id(prefix, value):
        return prefix + digest(canon(dict(value)))[:20]

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise error_type("duplicate_json_key")
            result[key] = value
        return result

    def reject_constant(_):
        raise error_type("nonfinite_json")

    def parse_json(raw):
        if exact_type(raw) is not bytes or raw.startswith(b"\xef\xbb\xbf"):
            raise error_type("gate_canonical_bytes_invalid")
        try:
            value = loads(
                raw.decode("utf-8"),
                object_pairs_hook=pairs,
                parse_constant=reject_constant,
            )
        except Exception as exc:
            raise error_type("gate_json_invalid") from exc
        if canon(value) != raw:
            raise error_type("gate_noncanonical_json")
        return value

    def exact_map(value, keys, code):
        if not isinstance(value, mapping_type) or set(value) != set(keys):
            raise error_type(code)
        return value

    def check_hex(value, code, length=64):
        if (
            exact_type(value) is not str
            or len(value) != length
            or any(char not in "0123456789abcdef" for char in value)
        ):
            raise error_type(code)

    def validate_report_data(value):
        data = dict(exact_map(value, report_keys, "gate_report_exact_keys_invalid"))
        if data["schema_version"] != report_schema:
            raise error_type("gate_report_schema_invalid")
        modes = {
            "production_linux": "production_evidence_replayed_record_only",
            "synthetic_test_only": "synthetic_evidence_replayed_not_manager_consumable",
        }
        if data["validation_mode"] not in modes or data["status"] != modes[data["validation_mode"]]:
            raise error_type("gate_report_mode_invalid")
        if (
            data["authority_state"] != "record_only_not_manager_authority"
            or data["manager_a1_passed"] is not False
            or data["a2_unlocked"] is not False
            or data["failure_codes"] != []
        ):
            raise error_type("gate_report_authority_invalid")
        for key in (
            "plan_sha256", "control_sha256", "observation_sha256",
            "receipt_sha256", "commit_sha256", "payload_tree_sha256",
            "root_invariants_sha256",
        ):
            check_hex(data[key], "gate_report_hash_invalid")
        check_hex(data["action_time_git_sha"], "gate_report_action_sha_invalid", 40)
        if data["worker_authority"] != worker_authority or data["worker_version"] != worker_version:
            raise error_type("gate_report_worker_invalid")
        parse_utc(data["gate_evaluated_utc"])
        for key, prefix in (
            ("plan_id", "real-a1-plan-"),
            ("control_id", "real-a1-control-"),
            ("observation_id", "real-a1-production-observation-"),
            ("receipt_id", "real-a1-execution-receipt-"),
            ("commit_id", "real-a1-evidence-commit-"),
        ):
            if exact_type(data[key]) is not str or not data[key].startswith(prefix):
                raise error_type("gate_report_binding_invalid")
        root = {key: data[key] for key in report_keys if key != "report_id"}
        if data["report_id"] != artifact_id("real-a1-production-gate-report-", root):
            raise error_type("gate_report_identity_invalid")
        return data

    class A1ProductionGateReport:
        __slots__ = ("data",)

        def __init__(self, data):
            self.data = validate_report_data(data)

        @classmethod
        def from_dict(cls, value):
            return cls(value)

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_report_data(parse_json(raw)))

        def validate(self):
            validate_report_data(self.data)

        def to_dict(self):
            return dict(validate_report_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_report_data(self.data)))

        def sha256(self):
            return digest(self.canonical_bytes())

    report_type = A1ProductionGateReport
    report_from_dict = A1ProductionGateReport.from_dict

    def parse_report(value):
        if exact_type(value) is not report_type:
            raise error_type("gate_report_type_invalid")
        return report_from_dict(value.data)

    def validate_receipt_data(value):
        data = dict(exact_map(value, receipt_keys, "gate_receipt_exact_keys_invalid"))
        if data["schema_version"] != receipt_schema:
            raise error_type("gate_receipt_schema_invalid")
        statuses = {
            "production_bundle_recorded_not_manager_authority": "requires_owning_bundle_replay",
            "synthetic_not_manager_consumable": "blocked_synthetic_test",
        }
        if data["status"] not in statuses or data["next_state"] != statuses[data["status"]]:
            raise error_type("gate_receipt_state_invalid")
        if any(
            data[key] is not False
            for key in (
                "manager_a1_passed", "a2_unlocked", "provider_invoked",
                "project_data_transferred", "h1_allowed", "formal_quality_allowed",
            )
        ):
            raise error_type("gate_receipt_authority_invalid")
        check_hex(data["report_sha256"], "gate_receipt_report_hash_invalid")
        root = {key: data[key] for key in receipt_keys if key != "gate_receipt_id"}
        if data["gate_receipt_id"] != artifact_id("real-a1-production-gate-receipt-", root):
            raise error_type("gate_receipt_identity_invalid")
        return data

    class A1ProductionGateReceipt:
        __slots__ = ("data",)

        def __init__(self, data):
            self.data = validate_receipt_data(data)

        @classmethod
        def from_dict(cls, value):
            return cls(value)

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_receipt_data(parse_json(raw)))

        def validate(self):
            validate_receipt_data(self.data)

        def to_dict(self):
            return dict(validate_receipt_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_receipt_data(self.data)))

        def sha256(self):
            return digest(self.canonical_bytes())

        def validate_against(self, report):
            parsed_report = parse_report(report)
            expected = build_gate_receipt(parsed_report)
            if validate_receipt_data(self.data) != expected:
                raise error_type("gate_receipt_replay_invalid")

    receipt_type = A1ProductionGateReceipt
    receipt_from_dict = A1ProductionGateReceipt.from_dict

    def parse_gate_receipt(value):
        if exact_type(value) is not receipt_type:
            raise error_type("gate_receipt_type_invalid")
        return receipt_from_dict(value.data)

    def validate_bundle_data(value):
        data = dict(exact_map(value, bundle_keys, "gate_bundle_exact_keys_invalid"))
        if data["schema_version"] != bundle_schema:
            raise error_type("gate_bundle_schema_invalid")
        report = report_from_dict(data["report"])
        receipt = receipt_from_dict(data["receipt"])
        receipt.validate_against(report)
        root = {
            "schema_version": bundle_schema,
            "report": report.to_dict(),
            "receipt": receipt.to_dict(),
        }
        if data["bundle_id"] != artifact_id("real-a1-production-gate-bundle-", root):
            raise error_type("gate_bundle_identity_invalid")
        return {**root, "bundle_id": data["bundle_id"]}

    class A1ProductionGateBundle:
        __slots__ = ("data",)

        def __init__(self, data):
            self.data = validate_bundle_data(data)

        @classmethod
        def from_dict(cls, value):
            return cls(value)

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_bundle_data(parse_json(raw)))

        def validate(self):
            validate_bundle_data(self.data)

        def to_dict(self):
            return dict(validate_bundle_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_bundle_data(self.data)))

        def sha256(self):
            return digest(self.canonical_bytes())

        def report(self):
            return report_from_dict(validate_bundle_data(self.data)["report"])

        def receipt(self):
            return receipt_from_dict(validate_bundle_data(self.data)["receipt"])

    bundle_type = A1ProductionGateBundle

    def register_identity(registry, expected_type, instance, data):
        if exact_type(instance) is not expected_type:
            raise TypeError("identity_registry_exact_type_required")
        identity = identity_of(instance)

        def discard(stored_ref, identity_key=identity, owning_registry=registry):
            current = owning_registry.get(identity_key)
            if current is not None and current[0] is stored_ref:
                owning_registry.pop(identity_key, None)

        instance_ref = weakref_ref(instance, discard)
        registry[identity] = (instance_ref, mapping_proxy_type(dict(data)))
        return instance

    def registered_decision_data(registry, expected_type, instance, code):
        if exact_type(instance) is not expected_type:
            raise TypeError(code)
        entry = registry.get(identity_of(instance))
        if entry is None or entry[0]() is not instance:
            raise TypeError(code)
        return entry[1]

    class ValidatedA1ManagerDecision:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args, **kwargs):
            raise TypeError("validated_manager_decision_factory_required")

        @property
        def status(self):
            return registered_decision_data(
                manager_decision_registry,
                manager_decision_type,
                self,
                "validated_manager_decision_not_registered",
            )["status"]

        @property
        def next_state(self):
            return registered_decision_data(
                manager_decision_registry,
                manager_decision_type,
                self,
                "validated_manager_decision_not_registered",
            )["next_state"]

        @property
        def manager_a1_passed(self):
            return registered_decision_data(
                manager_decision_registry,
                manager_decision_type,
                self,
                "validated_manager_decision_not_registered",
            )["manager_a1_passed"]

        @property
        def a2_unlocked(self):
            return registered_decision_data(
                manager_decision_registry,
                manager_decision_type,
                self,
                "validated_manager_decision_not_registered",
            )["a2_unlocked"]

        @property
        def validated_at_utc(self):
            return registered_decision_data(
                manager_decision_registry,
                manager_decision_type,
                self,
                "validated_manager_decision_not_registered",
            )["validated_at_utc"]

        @property
        def report_id(self):
            return registered_decision_data(
                manager_decision_registry,
                manager_decision_type,
                self,
                "validated_manager_decision_not_registered",
            )["report_id"]

        @property
        def gate_receipt_id(self):
            return registered_decision_data(
                manager_decision_registry,
                manager_decision_type,
                self,
                "validated_manager_decision_not_registered",
            )["gate_receipt_id"]

        @property
        def report_sha256(self):
            return registered_decision_data(
                manager_decision_registry,
                manager_decision_type,
                self,
                "validated_manager_decision_not_registered",
            )["report_sha256"]

        @property
        def gate_receipt_sha256(self):
            return registered_decision_data(
                manager_decision_registry,
                manager_decision_type,
                self,
                "validated_manager_decision_not_registered",
            )["gate_receipt_sha256"]

    manager_decision_type = ValidatedA1ManagerDecision

    class SyntheticA1GateDecision:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args, **kwargs):
            raise TypeError("synthetic_gate_decision_factory_required")

        @property
        def status(self):
            return registered_decision_data(
                synthetic_decision_registry,
                synthetic_decision_type,
                self,
                "synthetic_gate_decision_not_registered",
            )["status"]

        @property
        def next_state(self):
            return registered_decision_data(
                synthetic_decision_registry,
                synthetic_decision_type,
                self,
                "synthetic_gate_decision_not_registered",
            )["next_state"]

        @property
        def manager_a1_passed(self):
            return registered_decision_data(
                synthetic_decision_registry,
                synthetic_decision_type,
                self,
                "synthetic_gate_decision_not_registered",
            )["manager_a1_passed"]

        @property
        def a2_unlocked(self):
            return registered_decision_data(
                synthetic_decision_registry,
                synthetic_decision_type,
                self,
                "synthetic_gate_decision_not_registered",
            )["a2_unlocked"]

        @property
        def validated_at_utc(self):
            return registered_decision_data(
                synthetic_decision_registry,
                synthetic_decision_type,
                self,
                "synthetic_gate_decision_not_registered",
            )["validated_at_utc"]

        @property
        def report_id(self):
            return registered_decision_data(
                synthetic_decision_registry,
                synthetic_decision_type,
                self,
                "synthetic_gate_decision_not_registered",
            )["report_id"]

        @property
        def gate_receipt_id(self):
            return registered_decision_data(
                synthetic_decision_registry,
                synthetic_decision_type,
                self,
                "synthetic_gate_decision_not_registered",
            )["gate_receipt_id"]

        @property
        def bundle_id(self):
            return registered_decision_data(
                synthetic_decision_registry,
                synthetic_decision_type,
                self,
                "synthetic_gate_decision_not_registered",
            )["bundle_id"]

    synthetic_decision_type = SyntheticA1GateDecision

    class A1ProductionGateResult:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args, **kwargs):
            raise TypeError("production_gate_result_factory_required")

        @property
        def report(self):
            return parse_result_snapshot(self)[1]

        @property
        def receipt(self):
            return parse_result_snapshot(self)[2]

        @property
        def bundle(self):
            return parse_result_snapshot(self)[3]

        @property
        def decision(self):
            return parse_result_snapshot(self)[4]

        @property
        def return_code(self):
            return parse_result_snapshot(self)[0]["return_code"]

    result_type = A1ProductionGateResult

    def parse_result_snapshot(instance):
        snapshot = registered_decision_data(
            result_registry,
            result_type,
            instance,
            "production_gate_result_not_registered",
        )
        report = report_type.from_bytes(snapshot["report_bytes"])
        receipt = receipt_type.from_bytes(snapshot["receipt_bytes"])
        bundle = bundle_type.from_bytes(snapshot["bundle_bytes"])
        report, receipt, bundle = parse_provenance_components(
            report,
            receipt,
            bundle,
        )
        current_artifacts = {
            "report_id": report.data["report_id"],
            "report_sha256": report.sha256(),
            "gate_receipt_id": receipt.data["gate_receipt_id"],
            "gate_receipt_sha256": receipt.sha256(),
            "bundle_id": bundle.data["bundle_id"],
            "bundle_sha256": bundle.sha256(),
        }
        if (
            report.canonical_bytes() != snapshot["report_bytes"]
            or receipt.canonical_bytes() != snapshot["receipt_bytes"]
            or bundle.canonical_bytes() != snapshot["bundle_bytes"]
            or current_artifacts != dict(snapshot["artifact_provenance"])
        ):
            raise TypeError("production_gate_result_snapshot_invalid")
        manager_authority = snapshot["manager_authority"]
        decision = snapshot["decision"]
        decision_registry = (
            manager_decision_registry
            if manager_authority
            else synthetic_decision_registry
        )
        _, _, _, decision_provenance = validate_registered_provenance(
            decision_registry,
            decision,
            report,
            receipt,
            bundle,
            manager_authority,
        )
        if dict(decision_provenance) != dict(snapshot["decision_provenance"]):
            raise TypeError("production_gate_result_decision_provenance_invalid")
        if snapshot["return_code"] != 0:
            raise TypeError("gate_result_binding_invalid")
        return snapshot, report, receipt, bundle, decision

    def read_exact_files(validation):
        if exact_type(validation) is not root_validation_type:
            raise error_type("gate_root_validation_type_invalid")
        staging = path_type(validation.staging_root)
        try:
            entries = list(staging.iterdir())
        except OSError as exc:
            raise error_type("gate_evidence_root_invalid") from exc
        if {item.name for item in entries} != set(evidence_files) or len(entries) != len(evidence_files):
            raise error_type("gate_evidence_exact_inventory_invalid")
        result = {}
        for name in evidence_files:
            item = staging / name
            try:
                before = item.lstat()
            except OSError as exc:
                raise error_type("gate_evidence_file_invalid") from exc
            if (
                not is_regular(before.st_mode)
                or before.st_size < 1
                or before.st_size > max_file_bytes[name]
            ):
                raise error_type("gate_evidence_file_invalid")
            try:
                raw = item.read_bytes()
                after = item.lstat()
            except OSError as exc:
                raise error_type("gate_evidence_file_invalid") from exc
            if (
                not is_regular(after.st_mode)
                or before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or len(raw) != after.st_size
            ):
                raise error_type("gate_evidence_file_race_invalid")
            result[name] = raw
        return result

    def invariant_bindings(validation):
        rows = []
        try:
            for role in roles:
                binding = validation.root_bindings[role]
                row = dict(exact_map(
                    {key: binding[key] for key in root_invariant_keys},
                    root_invariant_keys,
                    "gate_root_invariant_invalid",
                ))
                if row["role"] != role:
                    raise error_type("gate_root_invariant_invalid")
                check_hex(row["normalized_path_sha256"], "gate_root_invariant_invalid")
                check_hex(row["marker_sha256"], "gate_root_invariant_invalid")
                rows.append(row)
        except (KeyError, TypeError) as exc:
            raise error_type("gate_root_invariant_invalid") from exc
        return rows

    def validate_evidence(validation, evaluated_utc):
        evaluation = parse_utc(evaluated_utc)
        files = read_exact_files(validation)
        phase_value = parse_json(files[evidence_files[0]])
        if exact_type(phase_value) is not list:
            raise error_type("gate_phase_inventory_invalid")
        phases = [phase_from_dict(item) for item in phase_value]
        normalized_phases = [item.to_dict() for item in phases]
        observation = observation_from_bytes(files[evidence_files[1]])
        receipt = receipt_from_bytes(files[evidence_files[2]])
        marker = commit_from_bytes(files[evidence_files[3]])
        if normalized_phases != observation.data["phase_events"] or files[evidence_files[0]] != canon(normalized_phases):
            raise error_type("gate_phase_observation_replay_invalid")
        observation_validate_against(observation, validation.plan, validation.controls)
        receipt_validate_against(receipt, validation.plan, validation.controls, observation)
        commit_validate_against(
            marker,
            observation,
            receipt,
            files[evidence_files[0]],
            files[evidence_files[1]],
            files[evidence_files[2]],
        )
        if observation.data["decision"] != "pass" or observation.data["failure_codes"] != []:
            raise error_type("gate_observation_not_passed")
        if (
            receipt.data["status"] != "a1_passed_awaiting_independent_gate"
            or receipt.data["next_state"] != "awaiting_independent_a1_gate_validation"
            or receipt.data["failure_codes"] != []
        ):
            raise error_type("gate_executor_receipt_not_eligible")
        if any(
            receipt.data[key] is not False
            for key in (
                "a2_unlocked", "provider_invoked", "project_data_transferred",
                "compatibility_run_occurred", "h1_allowed", "formal_quality_allowed",
                "manager_a1_passed",
            )
        ):
            raise error_type("gate_executor_receipt_authority_invalid")
        actual_invariants = invariant_bindings(validation)
        if observation.data["root_invariants"] != actual_invariants:
            raise error_type("gate_root_identity_invalid")
        disposition = validation.plan.data["no_action_plan"]["approved_disposition"]
        approval = parse_utc(disposition["approval_timestamp_utc"])
        expiry = parse_utc(disposition["authorization_expiry_utc"])
        started = parse_utc(observation.data["execution_started_utc"])
        completed = parse_utc(observation.data["execution_completed_utc"])
        if not approval <= started <= completed <= evaluation <= expiry:
            raise error_type("gate_authorization_window_invalid")
        return {
            "validation": validation,
            "files": files,
            "observation": observation,
            "receipt": receipt,
            "marker": marker,
            "root_invariants": actual_invariants,
            "root_invariants_sha256": digest(canon(actual_invariants)),
            "approval": approval,
            "expiry": expiry,
            "evaluation": evaluation,
        }

    def build_gate_report(evidence, validation_mode, evaluated_utc):
        validation = evidence["validation"]
        observation = evidence["observation"]
        receipt = evidence["receipt"]
        marker = evidence["marker"]
        statuses = {
            "production_linux": "production_evidence_replayed_record_only",
            "synthetic_test_only": "synthetic_evidence_replayed_not_manager_consumable",
        }
        if validation_mode not in statuses:
            raise error_type("gate_report_mode_invalid")
        root = {
            "schema_version": report_schema,
            "validation_mode": validation_mode,
            "status": statuses[validation_mode],
            "plan_id": validation.plan.plan_id,
            "plan_sha256": validation.plan.sha256(),
            "control_id": validation.controls.data["control_id"],
            "control_sha256": validation.controls.sha256(),
            "observation_id": observation.data["observation_id"],
            "observation_sha256": observation.sha256(),
            "receipt_id": receipt.data["receipt_id"],
            "receipt_sha256": receipt.sha256(),
            "commit_id": marker.data["commit_id"],
            "commit_sha256": marker.sha256(),
            "action_time_git_sha": validation.plan.data["action_time_git_sha"],
            "worker_authority": worker_authority,
            "worker_version": worker_version,
            "payload_tree_sha256": marker.data["tree_sha256"],
            "root_invariants_sha256": evidence["root_invariants_sha256"],
            "gate_evaluated_utc": evaluated_utc,
            "authority_state": "record_only_not_manager_authority",
            "manager_a1_passed": False,
            "a2_unlocked": False,
            "failure_codes": [],
        }
        root["report_id"] = artifact_id("real-a1-production-gate-report-", root)
        return validate_report_data(root)

    def build_gate_receipt(report):
        parsed = parse_report(report)
        production = parsed.data["validation_mode"] == "production_linux"
        root = {
            "schema_version": receipt_schema,
            "report_id": parsed.data["report_id"],
            "report_sha256": parsed.sha256(),
            "plan_id": parsed.data["plan_id"],
            "control_id": parsed.data["control_id"],
            "observation_id": parsed.data["observation_id"],
            "receipt_id": parsed.data["receipt_id"],
            "commit_id": parsed.data["commit_id"],
            "status": (
                "production_bundle_recorded_not_manager_authority"
                if production
                else "synthetic_not_manager_consumable"
            ),
            "next_state": "requires_owning_bundle_replay" if production else "blocked_synthetic_test",
            "manager_a1_passed": False,
            "a2_unlocked": False,
            "provider_invoked": False,
            "project_data_transferred": False,
            "h1_allowed": False,
            "formal_quality_allowed": False,
        }
        root["gate_receipt_id"] = artifact_id("real-a1-production-gate-receipt-", root)
        return validate_receipt_data(root)

    def build_bundle(report, receipt):
        parsed_report = parse_report(report)
        parsed_receipt = parse_gate_receipt(receipt)
        parsed_receipt.validate_against(parsed_report)
        root = {
            "schema_version": bundle_schema,
            "report": parsed_report.to_dict(),
            "receipt": parsed_receipt.to_dict(),
        }
        root["bundle_id"] = artifact_id("real-a1-production-gate-bundle-", root)
        return validate_bundle_data(root)

    def build_bundle_for_validation(validation, validation_mode, evaluated_utc):
        evidence = validate_evidence(validation, evaluated_utc)
        report = report_type(build_gate_report(evidence, validation_mode, evaluated_utc))
        receipt = receipt_type(build_gate_receipt(report))
        bundle = A1ProductionGateBundle(build_bundle(report, receipt))
        return report, receipt, bundle

    def parse_provenance_components(report, receipt, bundle):
        parsed_report = parse_report(report)
        parsed_receipt = parse_gate_receipt(receipt)
        parsed_receipt.validate_against(parsed_report)
        if exact_type(bundle) is not bundle_type:
            raise TypeError("gate_bundle_type_invalid")
        parsed_bundle = bundle_type.from_dict(bundle.data)
        if (
            parsed_bundle.report().to_dict() != parsed_report.to_dict()
            or parsed_bundle.receipt().to_dict() != parsed_receipt.to_dict()
        ):
            raise TypeError("gate_bundle_nested_binding_invalid")
        return parsed_report, parsed_receipt, parsed_bundle

    def validate_registered_provenance(
        registry,
        decision,
        report,
        receipt,
        bundle,
        manager_required,
    ):
        code = (
            "validated_manager_decision_not_registered"
            if manager_required
            else "synthetic_gate_decision_not_registered"
        )
        expected_decision_type = manager_decision_type if manager_required else synthetic_decision_type
        provenance = registered_decision_data(
            registry,
            expected_decision_type,
            decision,
            code,
        )
        parsed_report, parsed_receipt, parsed_bundle = parse_provenance_components(
            report,
            receipt,
            bundle,
        )
        if manager_required:
            expected = {
                "validation_mode": "production_linux",
                "status": "validated_manager_a1_passed",
                "next_state": "a2_unlocked_not_executed",
                "manager_a1_passed": True,
                "a2_unlocked": True,
            }
        else:
            expected = {
                "validation_mode": "synthetic_test_only",
                "status": "synthetic_validated_not_manager_consumable",
                "next_state": "blocked_synthetic_test",
                "manager_a1_passed": False,
                "a2_unlocked": False,
            }
        if (
            any(provenance[key] != value for key, value in expected.items())
            or provenance["report_id"] != parsed_report.data["report_id"]
            or provenance["report_sha256"] != parsed_report.sha256()
            or provenance["gate_receipt_id"] != parsed_receipt.data["gate_receipt_id"]
            or provenance["gate_receipt_sha256"] != parsed_receipt.sha256()
            or provenance["bundle_id"] != parsed_bundle.data["bundle_id"]
            or provenance["bundle_sha256"] != parsed_bundle.sha256()
        ):
            raise TypeError(
                "manager_decision_bundle_binding_invalid"
                if manager_required
                else "synthetic_decision_bundle_binding_invalid"
            )
        return parsed_report, parsed_receipt, parsed_bundle, provenance

    def register_decision(manager_authority, data):
        if manager_authority:
            instance = object.__new__(manager_decision_type)
            return register_identity(
                manager_decision_registry,
                manager_decision_type,
                instance,
                data,
            )
        instance = object.__new__(synthetic_decision_type)
        return register_identity(
            synthetic_decision_registry,
            synthetic_decision_type,
            instance,
            data,
        )

    def register_result_snapshot(
        report,
        receipt,
        bundle,
        decision,
        return_code,
        manager_authority,
    ):
        expected_type = (
            manager_decision_type if manager_authority else synthetic_decision_type
        )
        if exact_type(decision) is not expected_type:
            raise TypeError(
                "validated_manager_decision_required"
                if manager_authority
                else "synthetic_gate_decision_required"
            )
        decision_registry = (
            manager_decision_registry
            if manager_authority
            else synthetic_decision_registry
        )
        parsed_report, parsed_receipt, parsed_bundle, provenance = (
            validate_registered_provenance(
                decision_registry,
                decision,
                report,
                receipt,
                bundle,
                manager_authority,
            )
        )
        if return_code != 0:
            raise TypeError("gate_result_binding_invalid")
        report_bytes = parsed_report.canonical_bytes()
        receipt_bytes = parsed_receipt.canonical_bytes()
        bundle_bytes = parsed_bundle.canonical_bytes()
        artifact_provenance = mapping_proxy_type(
            {
                "report_id": parsed_report.data["report_id"],
                "report_sha256": parsed_report.sha256(),
                "gate_receipt_id": parsed_receipt.data["gate_receipt_id"],
                "gate_receipt_sha256": parsed_receipt.sha256(),
                "bundle_id": parsed_bundle.data["bundle_id"],
                "bundle_sha256": parsed_bundle.sha256(),
            }
        )
        result = object.__new__(result_type)
        return register_identity(
            result_registry,
            result_type,
            result,
            {
                "report_bytes": report_bytes,
                "receipt_bytes": receipt_bytes,
                "bundle_bytes": bundle_bytes,
                "artifact_provenance": artifact_provenance,
                "decision": decision,
                "decision_provenance": mapping_proxy_type(dict(provenance)),
                "manager_authority": manager_authority,
                "return_code": return_code,
            },
        )

    def make_production_result(report, receipt, bundle, decision, return_code):
        return register_result_snapshot(
            report,
            receipt,
            bundle,
            decision,
            return_code,
            True,
        )

    def make_synthetic_result_for_tests(
        report,
        receipt,
        bundle,
        decision,
        return_code=0,
    ):
        return register_result_snapshot(
            report,
            receipt,
            bundle,
            decision,
            return_code,
            False,
        )

    def replay_bundle_with_validation(
        report_bytes,
        receipt_bytes,
        validation,
        validated_at_utc,
        manager_authority,
    ):
        report = report_type.from_bytes(report_bytes)
        receipt = receipt_type.from_bytes(receipt_bytes)
        receipt.validate_against(report)
        evidence = validate_evidence(validation, report.data["gate_evaluated_utc"])
        expected_report = build_gate_report(
            evidence,
            report.data["validation_mode"],
            report.data["gate_evaluated_utc"],
        )
        if report.to_dict() != expected_report:
            raise error_type("gate_report_replay_invalid")
        expected_receipt = build_gate_receipt(report)
        if receipt.to_dict() != expected_receipt:
            raise error_type("gate_receipt_replay_invalid")
        validated_at = parse_utc(validated_at_utc)
        if not evidence["evaluation"] <= validated_at <= evidence["expiry"]:
            raise error_type("gate_authorization_window_invalid")
        production_record = report.data["validation_mode"] == "production_linux"
        if manager_authority != production_record:
            raise error_type(
                "gate_production_record_required"
                if manager_authority
                else "gate_synthetic_record_required"
            )
        manager_passed = manager_authority
        bundle = bundle_type(build_bundle(report, receipt))
        data = {
            "validation_mode": report.data["validation_mode"],
            "status": (
                "validated_manager_a1_passed"
                if manager_passed
                else "synthetic_validated_not_manager_consumable"
            ),
            "next_state": (
                "a2_unlocked_not_executed"
                if manager_passed
                else "blocked_synthetic_test"
            ),
            "manager_a1_passed": manager_passed,
            "a2_unlocked": manager_passed,
            "provider_invoked": False,
            "project_data_transferred": False,
            "h1_allowed": False,
            "formal_quality_allowed": False,
            "validated_at_utc": validated_at_utc,
            "report_id": report.data["report_id"],
            "report_sha256": report.sha256(),
            "gate_receipt_id": receipt.data["gate_receipt_id"],
            "gate_receipt_sha256": receipt.sha256(),
            "bundle_id": bundle.data["bundle_id"],
            "bundle_sha256": bundle.sha256(),
        }
        return register_decision(manager_authority, data)

    def create_public_bundle(plan_path, controls_path, package_root, model_root, evidence_root):
        if platform != "linux":
            raise error_type("real_a1_production_gate_linux_required")
        validation = committed_validator(
            plan_path,
            controls_path,
            package_root,
            model_root,
            evidence_root,
        )
        evaluated_utc = utc_now()
        return build_bundle_for_validation(validation, "production_linux", evaluated_utc)

    def validate_production_gate_bundle_bytes(
        report_bytes,
        receipt_bytes,
        plan_path,
        controls_path,
        package_root,
        model_root,
        evidence_root,
    ):
        if platform != "linux":
            raise error_type("real_a1_production_gate_linux_required")
        validation = committed_validator(
            plan_path,
            controls_path,
            package_root,
            model_root,
            evidence_root,
        )
        return replay_bundle_with_validation(
            report_bytes,
            receipt_bytes,
            validation,
            utc_now(),
            True,
        )

    def run_public(plan_path, controls_path, package_root, model_root, evidence_root):
        report, receipt, bundle = create_public_bundle(
            plan_path,
            controls_path,
            package_root,
            model_root,
            evidence_root,
        )
        decision = validate_production_gate_bundle_bytes(
            report.canonical_bytes(),
            receipt.canonical_bytes(),
            plan_path,
            controls_path,
            package_root,
            model_root,
            evidence_root,
        )
        return make_production_result(report, receipt, bundle, decision, 0)

    def build_synthetic_bundle_for_tests(validation, evaluated_utc):
        return build_bundle_for_validation(validation, "synthetic_test_only", evaluated_utc)

    def validate_synthetic_bundle_for_tests(
        report_bytes,
        receipt_bytes,
        validation,
        validated_at_utc,
    ):
        return replay_bundle_with_validation(
            report_bytes,
            receipt_bytes,
            validation,
            validated_at_utc,
            False,
        )

    def validate_synthetic_decision_provenance_for_tests(
        decision,
        report,
        receipt,
        bundle,
    ):
        if exact_type(decision) is not synthetic_decision_type:
            raise TypeError("synthetic_gate_decision_required")
        validate_registered_provenance(
            synthetic_decision_registry,
            decision,
            report,
            receipt,
            bundle,
            False,
        )
        return True

    def decision_registry_counts_for_tests():
        return {
            "manager": len(manager_decision_registry),
            "synthetic": len(synthetic_decision_registry),
            "results": len(result_registry),
        }

    return {
        "A1ProductionGateReport": A1ProductionGateReport,
        "A1ProductionGateReceipt": A1ProductionGateReceipt,
        "A1ProductionGateBundle": A1ProductionGateBundle,
        "ValidatedA1ManagerDecision": ValidatedA1ManagerDecision,
        "SyntheticA1GateDecision": SyntheticA1GateDecision,
        "A1ProductionGateResult": A1ProductionGateResult,
        "validate_report_data": validate_report_data,
        "validate_receipt_data": validate_receipt_data,
        "validate_bundle_data": validate_bundle_data,
        "validate_evidence": validate_evidence,
        "run_public": run_public,
        "validate_bundle_public": validate_production_gate_bundle_bytes,
        "build_synthetic_bundle_for_tests": build_synthetic_bundle_for_tests,
        "validate_synthetic_bundle_for_tests": validate_synthetic_bundle_for_tests,
        "validate_synthetic_decision_provenance_for_tests": validate_synthetic_decision_provenance_for_tests,
        "make_synthetic_result_for_tests": make_synthetic_result_for_tests,
        "decision_registry_counts_for_tests": decision_registry_counts_for_tests,
        "canon": canon,
        "digest": digest,
        "parse_json": parse_json,
    }


_GATE_AUTHORITIES = _build_gate_authorities()
A1ProductionGateReport = _GATE_AUTHORITIES["A1ProductionGateReport"]
A1ProductionGateReceipt = _GATE_AUTHORITIES["A1ProductionGateReceipt"]
A1ProductionGateBundle = _GATE_AUTHORITIES["A1ProductionGateBundle"]
_SyntheticA1GateDecision = _GATE_AUTHORITIES["SyntheticA1GateDecision"]
ValidatedA1ManagerDecision = _GATE_AUTHORITIES["ValidatedA1ManagerDecision"]
A1ProductionGateResult = _GATE_AUTHORITIES["A1ProductionGateResult"]
run_a1_production_gate = _GATE_AUTHORITIES["run_public"]
validate_production_gate_bundle_bytes = _GATE_AUTHORITIES["validate_bundle_public"]
_build_synthetic_gate_bundle_for_tests = _GATE_AUTHORITIES["build_synthetic_bundle_for_tests"]
_validate_synthetic_gate_bundle_for_tests = _GATE_AUTHORITIES["validate_synthetic_bundle_for_tests"]
_validate_synthetic_decision_provenance_for_tests = _GATE_AUTHORITIES["validate_synthetic_decision_provenance_for_tests"]
_make_synthetic_gate_result_for_tests = _GATE_AUTHORITIES["make_synthetic_result_for_tests"]
_decision_registry_counts_for_tests = _GATE_AUTHORITIES["decision_registry_counts_for_tests"]
_validate_committed_evidence_for_tests = _GATE_AUTHORITIES["validate_evidence"]
