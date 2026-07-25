"""Authority-neutral A3 finalizer readiness contract.

The public finalizer only replays recorded artifacts structurally.  It cannot
terminate processes, remove files, operate AutoDL, use a browser or SSH, revoke
credentials, claim physical erasure, create manager authority, or declare
cleanup complete.  A future action-time manager boundary remains deferred.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import re
import weakref
from types import MappingProxyType

from .autodl_a3_closeout import (
    A3CloseoutEvidenceBundle,
    A3CloseoutReadinessReceipt,
    validate_a3_closeout_bundle_bytes,
)
from .autodl_a3_remote_closeout import (
    A3RemoteCloseoutPlan,
    A3RemoteCloseoutEnvelope,
    A3RemoteCloseoutReceipt,
    A3RemoteCloseoutCommit,
    validate_a3_remote_closeout_envelope_bytes,
)


FINALIZER_REPORT_SCHEMA = "req2web.runtime.a3_finalizer_report.v1"
FINALIZER_RECEIPT_SCHEMA = "req2web.runtime.a3_finalizer_receipt.v1"
FINALIZER_ENVELOPE_SCHEMA = "req2web.runtime.a3_finalizer_envelope.v1"


class A3FinalizerError(ValueError):
    pass


def _build_finalizer_authorities():
    exact_type, bytes_type, str_type, bool_type, int_type = type, bytes, str, bool, int
    dict_type, list_type, tuple_type, mapping_type = dict, list, tuple, Mapping
    sort_values, length_of, enumerate_values, all_value, any_value = sorted, len, enumerate, all, any
    unicode_error = UnicodeDecodeError
    object_new = object.__new__
    object_setattr, error_type, value_error, type_error = object.__setattr__, A3FinalizerError, ValueError, TypeError
    dumps, loads, sha256_ctor, fullmatch = json.dumps, json.loads, hashlib.sha256, re.fullmatch
    datetime_type, utc = datetime, timezone.utc
    weakref_ref, identity_of, mapping_proxy_type = weakref.ref, id, MappingProxyType
    policy_bundle_validator = validate_a3_closeout_bundle_bytes
    remote_envelope_validator = validate_a3_remote_closeout_envelope_bytes
    remote_plan_from_bytes = A3RemoteCloseoutPlan.from_bytes
    remote_receipt_from_bytes = A3RemoteCloseoutReceipt.from_bytes
    remote_envelope_from_bytes = A3RemoteCloseoutEnvelope.from_bytes
    report_schema, receipt_schema, envelope_schema = FINALIZER_REPORT_SCHEMA, FINALIZER_RECEIPT_SCHEMA, FINALIZER_ENVELOPE_SCHEMA
    report_keys = ("schema_version", "report_id", "status", "plan_id", "plan_sha256", "remote_receipt_id", "remote_receipt_sha256", "remote_commit_id", "remote_commit_sha256", "foundation_bundle_id", "foundation_bundle_sha256", "foundation_receipt_id", "foundation_receipt_sha256", "release_evidence_id", "release_evidence_sha256", "revocation_evidence_id", "revocation_evidence_sha256", "manager_consumable", "cleanup_complete", "next_run_allowed", "authorization_invalidated", "physical_erasure_claimed", "failure_codes")
    receipt_keys = ("schema_version", "receipt_id", "report_id", "report_sha256", "status", "manager_consumable", "cleanup_complete", "next_run_allowed", "authorization_invalidated", "physical_erasure_claimed")
    envelope_keys = ("schema_version", "report", "receipt")
    result_registry = {}

    def canonical(value):
        return dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def digest(raw):
        return sha256_ctor(raw).hexdigest()

    def identity(prefix, value, field):
        root = {key: item for key, item in value.items() if key != field}
        return prefix + digest(canonical(root))[:20]

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise error_type("duplicate_json_key")
            result[key] = value
        return result

    def reject_constant(_):
        raise error_type("nonfinite_json_invalid")

    def parse_bytes(raw):
        if exact_type(raw) is not bytes_type:
            raise error_type("canonical_bytes_required")
        try:
            data = loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)
        except (unicode_error, value_error, type_error) as exc:
            raise error_type("canonical_bytes_invalid") from exc
        if exact_type(data) is not dict_type:
            raise error_type("canonical_object_invalid")
        return data

    def exact_map(value, keys, code):
        if exact_type(value) is not dict_type or tuple_type(sort_values(value)) != tuple_type(sort_values(keys)):
            raise error_type(code)
        return dict_type(value)

    def exact_list(value, code):
        if exact_type(value) is not list_type:
            raise error_type(code)
        return list_type(value)

    def req_id(value, code):
        if exact_type(value) is not str_type or fullmatch(r"[a-z0-9][a-z0-9._:-]{2,255}", value) is None:
            raise error_type(code)
        return value

    def req_hash(value, code):
        if exact_type(value) is not str_type or fullmatch(r"[0-9a-f]{64}", value) is None or value == "0" * 64:
            raise error_type(code)
        return value

    def req_bool(value, expected, code):
        if exact_type(value) is not bool_type or value is not expected:
            raise error_type(code)
        return value

    def check_identity(data, field, prefix, code):
        req_id(data[field], code)
        if data[field] != identity(prefix, data, field):
            raise error_type(code)

    class Artifact:
        __slots__ = ("_raw",)
        _validator = None

        def __init__(self, value):
            object_setattr(self, "_raw", canonical(self._validator(value)))

        @classmethod
        def from_dict(cls, value):
            return cls(value)

        @classmethod
        def from_bytes(cls, raw):
            data = cls._validator(parse_bytes(raw))
            if canonical(data) != raw:
                raise error_type("artifact_bytes_not_canonical")
            obj = object.__new__(cls)
            object_setattr(obj, "_raw", raw)
            return obj

        @property
        def data(self):
            return self._validator(parse_bytes(self._raw))

        def validate(self):
            self.data

        def to_dict(self):
            return dict_type(self.data)

        def canonical_bytes(self):
            self.data
            return self._raw

        def sha256(self):
            return digest(self.canonical_bytes())

    def validate_report(value):
        data = exact_map(value, report_keys, "finalizer_report_exact_keys_invalid")
        if data["schema_version"] != report_schema or data["status"] not in ("a3_structural_replay_complete", "a3_structural_replay_incomplete"):
            raise error_type("finalizer_report_schema_or_status_invalid")
        for key in ("plan_id", "remote_receipt_id", "remote_commit_id", "foundation_bundle_id", "foundation_receipt_id", "release_evidence_id", "revocation_evidence_id"):
            req_id(data[key], "finalizer_report_id_invalid")
        for key in ("plan_sha256", "remote_receipt_sha256", "remote_commit_sha256", "foundation_bundle_sha256", "foundation_receipt_sha256", "release_evidence_sha256", "revocation_evidence_sha256"):
            req_hash(data[key], "finalizer_report_hash_invalid")
        if exact_type(data["failure_codes"]) is not list_type or data["failure_codes"] not in ([], ["structural_replay_incomplete"]):
            raise error_type("finalizer_report_failure_codes_invalid")
        structurally_complete = data["status"] == "a3_structural_replay_complete"
        if (data["failure_codes"] == []) != structurally_complete:
            raise error_type("finalizer_report_status_binding_invalid")
        # Every public structural replay is permanently non-authorizing.
        req_bool(data["manager_consumable"], False, "finalizer_report_authority_invalid")
        req_bool(data["cleanup_complete"], False, "finalizer_report_authority_invalid")
        req_bool(data["next_run_allowed"], False, "finalizer_report_next_run_invalid")
        req_bool(data["authorization_invalidated"], True, "finalizer_report_invalidation_invalid")
        req_bool(data["physical_erasure_claimed"], False, "finalizer_report_erasure_invalid")
        check_identity(data, "report_id", "autodl-a3-finalizer-report-", "finalizer_report_identity_invalid")
        return data

    class A3FinalizerReport(Artifact):
        _validator = staticmethod(validate_report)

    def validate_receipt(value):
        data = exact_map(value, receipt_keys, "finalizer_receipt_exact_keys_invalid")
        if data["schema_version"] != receipt_schema or data["status"] not in ("a3_structural_replay_complete", "a3_structural_replay_incomplete"):
            raise error_type("finalizer_receipt_schema_or_status_invalid")
        req_id(data["report_id"], "finalizer_receipt_report_id_invalid")
        req_hash(data["report_sha256"], "finalizer_receipt_report_hash_invalid")
        for field, expected in (("manager_consumable", False), ("cleanup_complete", False), ("next_run_allowed", False), ("authorization_invalidated", True), ("physical_erasure_claimed", False)):
            req_bool(data[field], expected, "finalizer_receipt_authority_invalid")
        check_identity(data, "receipt_id", "autodl-a3-finalizer-receipt-", "finalizer_receipt_identity_invalid")
        return data

    class A3FinalizerReceipt(Artifact):
        _validator = staticmethod(validate_receipt)


    def parse_plan(value):
        if exact_type(value) is not A3RemoteCloseoutPlan:
            raise error_type("finalizer_plan_type_invalid")
        return A3RemoteCloseoutPlan.from_bytes(value.canonical_bytes())

    def _aggregate_inventory(plan):
        rows, total_files, total_bytes = [], 0, 0
        for root in plan.data["root_contracts"]:
            rows.append({"role": root["role"], "inventory_tree_sha256": root["inventory_tree_sha256"]})
            total_files += root["file_count"]
            total_bytes += root["total_bytes"]
        return digest(canonical(rows)), total_files, total_bytes

    def _replay_common(plan_bytes, remote_envelope_bytes, remote_event_bytes, foundation_bundle_bytes, foundation_receipt_bytes):
        plan, remote_receipt, remote_commit = remote_envelope_validator(plan_bytes, remote_envelope_bytes, remote_event_bytes)
        bundle, readiness = policy_bundle_validator(foundation_bundle_bytes, foundation_receipt_bytes)
        policy, trigger = bundle.data["policy"], bundle.data["trigger"]
        if (plan.data["a3_policy_id"], plan.data["a3_policy_sha256"], plan.data["a3_trigger_id"], plan.data["a3_trigger_sha256"]) != (policy["policy_id"], digest(canonical(policy)), trigger["trigger_id"], digest(canonical(trigger))):
            raise error_type("finalizer_foundation_plan_binding_invalid")
        if (trigger["a1_plan_id"], trigger["a1_plan_sha256"]) != (plan.data["a1_plan_id"], plan.data["a1_plan_sha256"]):
            raise error_type("finalizer_trigger_a1_binding_invalid")
        if policy["single_use"] is not True or policy["retry_allowed"] is not False or policy["second_provision_allowed"] is not False:
            raise error_type("finalizer_policy_single_use_invalid")
        evidence = bundle.data["evidence"]
        if length_of(evidence) != 4:
            raise error_type("finalizer_evidence_count_invalid")
        process, deletion, release, revocation = evidence
        if (process["source_class"], deletion["source_class"], release["source_class"], revocation["source_class"]) != ("ssh_remote_observer", "ssh_remote_observer", "autodl_browser_control_plane_operator", "local_operator_attestation"):
            raise error_type("finalizer_source_class_invalid")
        return plan, remote_receipt, remote_commit, bundle, readiness, process, deletion, release, revocation

    def _parse_utc(text):
        return datetime_type.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=utc)

    def _evidence_complete(plan, remote_receipt, bundle, process, deletion, release, revocation, now_value):
        deadline = _parse_utc(bundle.data["trigger"]["remote_deadline_utc"])
        start, end = _parse_utc(remote_receipt.data["execution_started_utc"]), _parse_utc(remote_receipt.data["execution_completed_utc"])
        observed = [_parse_utc(item["observed_at_utc"]) for item in (process, deletion, release, revocation)]
        if not start <= end <= observed[1] <= observed[2] <= observed[3] <= deadline or now_value > deadline:
            return False
        aggregate_hash, total_files, total_bytes = _aggregate_inventory(plan)
        process_result = remote_receipt.data["process_result"]
        if remote_receipt.data["status"] != "remote_cleanup_observed" or tuple_type(process_result[key] for key in ("post_process_count", "post_pgid_count", "post_cgroup_count", "post_gpu_process_count", "post_listener_count")) != (0, 0, 0, 0, 0):
            return False
        if (process["status"], process["pid_sha256"], process["pgid_sha256"], process["cgroup_sha256"], process["post_process_count"], process["post_gpu_process_count"], process["post_listener_count"]) != ("process_termination_observed", plan.data["process_identity"]["pid_sha256"], plan.data["process_identity"]["pgid_sha256"], plan.data["process_identity"]["cgroup_sha256"], 0, 0, 0):
            return False
        if (deletion["status"], deletion["pre_inventory_sha256"], deletion["pre_file_count"], deletion["pre_total_bytes"], deletion["post_residual_file_count"], deletion["post_residual_total_bytes"]) != ("project_side_deletion_observed", aggregate_hash, total_files, total_bytes, 0, 0):
            return False
        if any_value(row["status"] != "deleted_exact" or row["post_residual_file_count"] != 0 or row["post_residual_total_bytes"] != 0 for row in remote_receipt.data["root_results"]):
            return False
        if (release["status"], release["instance_id_sha256"], release["control_plane_state"], release["storage_state"], release["billing_state"]) != ("released", plan.data["instance_id_sha256"], "released", "released", "not_running"):
            return False
        if (revocation["status"], revocation["credential_fingerprint_sha256"], revocation["remote_access_state"], revocation["local_ephemeral_key_state"], revocation["instance_release_state"], revocation["instance_access_state"]) != ("access_revocation_observed", plan.data["credential_fingerprint_sha256"], "removed", "removed", "released", "inaccessible"):
            return False
        return True

    def _build_report(plan, remote_receipt, remote_commit, bundle, readiness, release, revocation, complete):
        status = "a3_structural_replay_complete" if complete else "a3_structural_replay_incomplete"
        root = {"schema_version": report_schema, "report_id": "pending", "status": status, "plan_id": plan.data["plan_id"], "plan_sha256": plan.sha256(), "remote_receipt_id": remote_receipt.data["receipt_id"], "remote_receipt_sha256": remote_receipt.sha256(), "remote_commit_id": remote_commit.data["commit_id"], "remote_commit_sha256": remote_commit.sha256(), "foundation_bundle_id": bundle.data["bundle_id"], "foundation_bundle_sha256": bundle.sha256(), "foundation_receipt_id": readiness.data["receipt_id"], "foundation_receipt_sha256": readiness.sha256(), "release_evidence_id": release["evidence_id"], "release_evidence_sha256": digest(canonical(release)), "revocation_evidence_id": revocation["evidence_id"], "revocation_evidence_sha256": digest(canonical(revocation)), "manager_consumable": False, "cleanup_complete": False, "next_run_allowed": False, "authorization_invalidated": True, "physical_erasure_claimed": False, "failure_codes": [] if complete else ["structural_replay_incomplete"]}
        root["report_id"] = identity("autodl-a3-finalizer-report-", root, "report_id")
        return A3FinalizerReport(root)

    def _build_receipt(report):
        root = {"schema_version": receipt_schema, "receipt_id": "pending", "report_id": report.data["report_id"], "report_sha256": report.sha256(), "status": report.data["status"], "manager_consumable": False, "cleanup_complete": False, "next_run_allowed": False, "authorization_invalidated": True, "physical_erasure_claimed": False}
        root["receipt_id"] = identity("autodl-a3-finalizer-receipt-", root, "receipt_id")
        return A3FinalizerReceipt(root)

    def validate_a3_finalizer_envelope_bytes(envelope_bytes):
        data = exact_map(parse_bytes(envelope_bytes), envelope_keys, "finalizer_envelope_exact_keys_invalid")
        if data["schema_version"] != envelope_schema:
            raise error_type("finalizer_envelope_schema_invalid")
        report, receipt = A3FinalizerReport.from_dict(data["report"]), A3FinalizerReceipt.from_dict(data["receipt"])
        if (receipt.data["report_id"], receipt.data["report_sha256"], receipt.data["status"]) != (report.data["report_id"], report.sha256(), report.data["status"]):
            raise error_type("finalizer_envelope_binding_invalid")
        return report, receipt


    def _register(registry, instance, data):
        key = identity_of(instance)
        def cleanup(ref):
            current = registry.get(key)
            if current is not None and current[0] is ref:
                registry.pop(key, None)
        ref = weakref_ref(instance, cleanup)
        registry[key] = (ref, mapping_proxy_type(dict_type(data)))
        return instance

    def _registered(registry, instance, expected_type, code):
        if exact_type(instance) is not expected_type:
            raise type_error(code)
        current = registry.get(identity_of(instance))
        if current is None or current[0]() is not instance:
            raise error_type(code + "_not_registered")
        return dict_type(current[1])

    class A3FinalizerResult:
        __slots__ = ("__weakref__",)
        def __init__(self):
            raise type_error("a3_finalizer_result_factory_required")
        def _data(self):
            return _registered(result_registry, self, A3FinalizerResult, "a3_finalizer_result")
        @property
        def report(self):
            return A3FinalizerReport.from_bytes(self._data()["report_bytes"])
        @property
        def receipt(self):
            return A3FinalizerReceipt.from_bytes(self._data()["receipt_bytes"])

    def _make_result(report, receipt):
        obj = object_new(A3FinalizerResult)
        return _register(result_registry, obj, {"report_bytes": report.canonical_bytes(), "receipt_bytes": receipt.canonical_bytes()})

    def _finalize(plan_bytes, remote_envelope_bytes, remote_event_bytes, foundation_bundle_bytes, foundation_receipt_bytes, now_value):
        plan, remote_receipt, remote_commit, bundle, readiness, process, deletion, release, revocation = _replay_common(plan_bytes, remote_envelope_bytes, remote_event_bytes, foundation_bundle_bytes, foundation_receipt_bytes)
        structurally_complete = _evidence_complete(plan, remote_receipt, bundle, process, deletion, release, revocation, now_value)
        report = _build_report(plan, remote_receipt, remote_commit, bundle, readiness, release, revocation, structurally_complete)
        receipt = _build_receipt(report)
        return _make_result(report, receipt)

    def finalize_a3_closeout(plan_bytes, remote_envelope_bytes, remote_event_bytes, foundation_bundle_bytes, foundation_receipt_bytes):
        return _finalize(plan_bytes, remote_envelope_bytes, remote_event_bytes, foundation_bundle_bytes, foundation_receipt_bytes, datetime_type.now(utc))

    def _finalize_a3_closeout_for_tests(plan_bytes, remote_envelope_bytes, remote_event_bytes, foundation_bundle_bytes, foundation_receipt_bytes, now_utc):
        return _finalize(plan_bytes, remote_envelope_bytes, remote_event_bytes, foundation_bundle_bytes, foundation_receipt_bytes, _parse_utc(now_utc))

    def finalizer_envelope_from_result(result):
        if exact_type(result) is not A3FinalizerResult:
            raise type_error("a3_finalizer_result_required")
        return canonical({"schema_version": envelope_schema, "report": result.report.to_dict(), "receipt": result.receipt.to_dict()})

    return {
        "A3FinalizerReport": A3FinalizerReport,
        "A3FinalizerReceipt": A3FinalizerReceipt,
        "A3FinalizerResult": A3FinalizerResult,
        "finalize_a3_closeout": finalize_a3_closeout,
        "validate_a3_finalizer_envelope_bytes": validate_a3_finalizer_envelope_bytes,
        "finalizer_envelope_from_result": finalizer_envelope_from_result,
        "_finalize_a3_closeout_for_tests": _finalize_a3_closeout_for_tests,
    }


_AUTHORITIES = _build_finalizer_authorities()
globals().update(_AUTHORITIES)

__all__ = [
    "A3FinalizerError", "A3FinalizerReport", "A3FinalizerReceipt",
    "A3FinalizerResult",
    "finalize_a3_closeout", "validate_a3_finalizer_envelope_bytes",
    "finalizer_envelope_from_result",
]
