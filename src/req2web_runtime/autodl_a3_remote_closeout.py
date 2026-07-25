"""Fail-closed payload-free readiness contract for future AutoDL A3 remote closeout.

No SSH, browser, AutoDL, model, network, process, deletion, release, or
credential action occurs on import.  The public runner is Linux-only and
requires an explicit boolean; tests use only the private scripted seam.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

from .autodl_a1_operational import A1OperationalControls, A1OperationalPlan
from .autodl_a1_production_gate import A1ProductionGateBundle
from .autodl_a3_closeout import A3CloseoutPolicy, A3CloseoutTrigger, A3_REMOTE_DEADLINE_MINUTES


REMOTE_PLAN_SCHEMA = "req2web.runtime.a3_remote_closeout_plan.v1"
REMOTE_RECEIPT_SCHEMA = "req2web.runtime.a3_remote_closeout_receipt.v1"
REMOTE_COMMIT_SCHEMA = "req2web.runtime.a3_remote_closeout_commit.v1"
REMOTE_ENVELOPE_SCHEMA = "req2web.runtime.a3_remote_closeout_envelope.v1"
REMOTE_STAGING_SCHEMA = "req2web.runtime.a3_remote_closeout_staging.v1"
REMOTE_ROOT_MARKER_SCHEMA = "req2web.runtime.a3_remote_root_marker.v1"
REMOTE_PROCESS_SCHEMA = "req2web.runtime.a3_remote_process_manifest.v1"

A3_REMOTE_ROOT_ROLES = ("work", "cache", "log", "transfer")
A3_REMOTE_EVIDENCE_ROLE = "evidence"
A3_REMOTE_CLEANUP_MODE = "delete_work_cache_release_instance_revoke_access"
A3_REMOTE_ROOT_MARKER_FILENAME = ".req2web-a3-root-marker.json"
A3_REMOTE_PROCESS_MANIFEST_FILENAME = ".req2web-a3-process-identity.json"
A3_REMOTE_EVENT_FILENAME = "remote_closeout_events.json"
A3_REMOTE_RECEIPT_FILENAME = "remote_closeout_receipt.json"
A3_REMOTE_COMMIT_FILENAME = "remote_closeout_commit.json"
A3_REMOTE_ABSENT_HASH = "0" * 64


class A3RemoteCloseoutError(ValueError):
    pass


def _build_authorities():
    # All validation authority is captured at definition time. Public APIs expose
    # no caller-supplied command, backend, callback, observer, platform, or clock.
    exact_type, bytes_type, str_type, int_type, bool_type = type, bytes, str, int, bool
    dict_type, list_type, tuple_type, mapping_type = dict, list, tuple, Mapping
    sort_values, length_of, enumerate_values, all_value, any_value = sorted, len, enumerate, all, any
    next_value, range_values, sum_values, string_type = next, range, sum, str
    os_error, key_error, lookup_error, permission_error, unicode_error = OSError, KeyError, ProcessLookupError, PermissionError, UnicodeDecodeError
    object_setattr, error_type, value_error, type_error = object.__setattr__, A3RemoteCloseoutError, ValueError, TypeError
    dumps, loads, sha256_ctor, fullmatch = json.dumps, json.loads, hashlib.sha256, re.fullmatch
    datetime_type, timedelta_type, utc = datetime, timedelta, timezone.utc
    getattr_fn = getattr
    path_type, normcase, lstat, scandir = Path, os.path.normcase, os.lstat, os.scandir
    is_dir, is_file, is_link = stat.S_ISDIR, stat.S_ISREG, stat.S_ISLNK
    a1_plan_from_bytes, controls_from_bytes, gate_from_bytes = A1OperationalPlan.from_bytes, A1OperationalControls.from_bytes, A1ProductionGateBundle.from_bytes
    policy_from_bytes, trigger_from_bytes = A3CloseoutPolicy.from_bytes, A3CloseoutTrigger.from_bytes
    schemas = MappingProxyType({"plan": REMOTE_PLAN_SCHEMA, "receipt": REMOTE_RECEIPT_SCHEMA, "commit": REMOTE_COMMIT_SCHEMA, "envelope": REMOTE_ENVELOPE_SCHEMA, "staging": REMOTE_STAGING_SCHEMA, "marker": REMOTE_ROOT_MARKER_SCHEMA, "process": REMOTE_PROCESS_SCHEMA})
    roles, evidence_role, absent, deadline_minutes, cleanup_mode = tuple_type(A3_REMOTE_ROOT_ROLES), A3_REMOTE_EVIDENCE_ROLE, A3_REMOTE_ABSENT_HASH, A3_REMOTE_DEADLINE_MINUTES, A3_REMOTE_CLEANUP_MODE
    marker_name, process_name, event_name, receipt_name, commit_name = A3_REMOTE_ROOT_MARKER_FILENAME, A3_REMOTE_PROCESS_MANIFEST_FILENAME, A3_REMOTE_EVENT_FILENAME, A3_REMOTE_RECEIPT_FILENAME, A3_REMOTE_COMMIT_FILENAME
    plan_keys = ("schema_version", "plan_id", "a1_plan_id", "a1_plan_sha256", "a1_controls_id", "a1_controls_sha256", "a1_gate_bundle_id", "a1_gate_bundle_sha256", "a1_gate_report_id", "a1_gate_report_sha256", "a3_policy_id", "a3_policy_sha256", "a3_trigger_id", "a3_trigger_sha256", "instance_id_sha256", "ssh_host_fingerprint_sha256", "credential_fingerprint_sha256", "cleanup_mode", "remote_deadline_utc", "single_use", "retry_allowed", "second_action_allowed", "root_contracts", "evidence_root_contract", "process_identity")
    root_keys = ("role", "normalized_path_sha256", "marker_id", "marker_sha256", "inventory", "inventory_tree_sha256", "file_count", "directory_count", "total_bytes")
    inventory_keys = ("relative_path_sha256", "kind", "bytes", "sha256")
    process_keys = ("manifest_sha256", "pid_sha256", "pgid_sha256", "cgroup_sha256", "gpu_processes_sha256", "listeners_sha256")
    receipt_keys = ("schema_version", "receipt_id", "plan_id", "plan_sha256", "status", "execution_started_utc", "execution_completed_utc", "process_result", "root_results", "event_sha256", "event_bytes", "manager_consumable", "cleanup_complete", "next_run_allowed", "authorization_invalidated", "physical_erasure_claimed")
    process_result_keys = ("term_sent", "kill_sent", "grace_seconds", "pid_sha256", "pgid_sha256", "cgroup_sha256", "gpu_processes_sha256", "listeners_sha256", "post_process_count", "post_pgid_count", "post_cgroup_count", "post_gpu_process_count", "post_listener_count")
    result_keys = ("role", "pre_inventory_tree_sha256", "pre_file_count", "pre_total_bytes", "deleted_file_count", "deleted_directory_count", "post_residual_file_count", "post_residual_total_bytes", "status")
    commit_keys = ("schema_version", "commit_id", "plan_id", "plan_sha256", "receipt_id", "receipt_sha256", "files", "tree_sha256")
    event_keys = ("relative_name", "bytes", "sha256")
    envelope_keys = ("schema_version", "receipt", "commit")
    staging_keys = ("schema_version", "staging_id", "status", "plan_id", "plan_sha256", "evidence_root_marker_id", "evidence_root_marker_sha256", "manager_consumable", "cleanup_complete", "next_run_allowed", "authorization_invalidated", "physical_erasure_claimed")

    def canonical(value):
        return dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def digest(raw):
        return sha256_ctor(raw).hexdigest()

    def identity(prefix, value, field):
        root = {key: item for key, item in value.items() if key != field}
        return prefix + digest(canonical(root))[:20]

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise error_type("duplicate_json_key")
            out[key] = value
        return out

    def reject_constant(_):
        raise error_type("nonfinite_json_invalid")

    def parse_bytes(raw):
        if exact_type(raw) is not bytes_type:
            raise error_type("canonical_bytes_required")
        try:
            value = loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)
        except (unicode_error, value_error, type_error) as exc:
            raise error_type("canonical_bytes_invalid") from exc
        if exact_type(value) is not dict_type:
            raise error_type("canonical_object_invalid")
        return value

    def exact_map(value, keys, code):
        if exact_type(value) is not dict_type or tuple_type(sort_values(value)) != tuple_type(sort_values(keys)):
            raise error_type(code)
        return dict_type(value)

    def exact_list(value, code):
        if exact_type(value) is not list_type:
            raise error_type(code)
        return list_type(value)

    def req_text(value, code, maximum=256):
        if exact_type(value) is not str_type or not value or length_of(value) > maximum:
            raise error_type(code)
        return value

    def req_id(value, code):
        text = req_text(value, code)
        if fullmatch(r"[a-z0-9][a-z0-9._:-]{2,255}", text) is None:
            raise error_type(code)
        return text

    def req_hash(value, code, allow_absent=False):
        if exact_type(value) is not str_type or fullmatch(r"[0-9a-f]{64}", value) is None or (value == absent and not allow_absent):
            raise error_type(code)
        return value

    def req_int(value, code):
        if exact_type(value) is not int_type or value < 0:
            raise error_type(code)
        return value

    def req_bool(value, expected, code):
        if exact_type(value) is not bool_type or value is not expected:
            raise error_type(code)
        return value

    def parse_time(value, code):
        if exact_type(value) is not str_type or fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is None:
            raise error_type(code)
        try:
            return datetime_type.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=utc)
        except value_error as exc:
            raise error_type(code) from exc

    def format_time(value):
        return value.astimezone(utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def path_hash(path):
        return digest(normcase(string_type(path_type(path).resolve(strict=False))).encode("utf-8"))

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
            parsed = cls._validator(parse_bytes(raw))
            if canonical(parsed) != raw:
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

    def validate_inventory(value, code):
        rows = exact_list(value, code)
        if not rows:
            raise error_type(code)
        parsed = []
        for row in rows:
            item = exact_map(row, inventory_keys, code)
            req_hash(item["relative_path_sha256"], code)
            if item["kind"] not in ("file", "directory"):
                raise error_type(code)
            req_int(item["bytes"], code)
            req_hash(item["sha256"], code, item["kind"] == "directory")
            if item["kind"] == "directory" and (item["bytes"], item["sha256"]) != (0, absent):
                raise error_type(code)
            parsed.append(item)
        if [row["relative_path_sha256"] for row in parsed] != sort_values(row["relative_path_sha256"] for row in parsed) or length_of({row["relative_path_sha256"] for row in parsed}) != length_of(parsed):
            raise error_type(code)
        return tuple_type(parsed)

    def validate_root(row, index=None, expected_role=None):
        data = exact_map(row, root_keys, "root_contract_exact_keys_invalid")
        role = roles[index] if expected_role is None else expected_role
        if data["role"] != role:
            raise error_type("root_contract_order_invalid")
        for key in ("normalized_path_sha256", "marker_sha256", "inventory_tree_sha256"):
            req_hash(data[key], "root_contract_hash_invalid")
        req_id(data["marker_id"], "root_contract_marker_invalid")
        inventory = validate_inventory(data["inventory"], "root_contract_inventory_invalid")
        if data["inventory_tree_sha256"] != digest(canonical(list_type(inventory))):
            raise error_type("root_contract_tree_invalid")
        files = [item for item in inventory if item["kind"] == "file"]
        dirs = [item for item in inventory if item["kind"] == "directory"]
        if (data["file_count"], data["directory_count"], data["total_bytes"]) != (length_of(files), length_of(dirs), sum_values(item["bytes"] for item in files)):
            raise error_type("root_contract_counts_invalid")
        for key in ("file_count", "directory_count", "total_bytes"):
            req_int(data[key], "root_contract_counts_invalid")
        return data

    def validate_roots(value):
        rows = exact_list(value, "root_contracts_invalid")
        if length_of(rows) != length_of(roles):
            raise error_type("root_contracts_invalid")
        return tuple_type(validate_root(row, index) for index, row in enumerate_values(rows))

    def validate_evidence_root(value):
        return validate_root(value, expected_role=evidence_role)

    def validate_process_identity(value):
        data = exact_map(value, process_keys, "process_identity_exact_keys_invalid")
        for key in process_keys:
            req_hash(data[key], "process_identity_hash_invalid")
        return data


    def validate_plan(value):
        data = exact_map(value, plan_keys, "remote_plan_exact_keys_invalid")
        if data["schema_version"] != schemas["plan"] or data["cleanup_mode"] != cleanup_mode:
            raise error_type("remote_plan_schema_or_mode_invalid")
        for key in ("a1_plan_id", "a1_controls_id", "a1_gate_bundle_id", "a1_gate_report_id", "a3_policy_id", "a3_trigger_id"):
            req_id(data[key], "remote_plan_id_invalid")
        for key in ("a1_plan_sha256", "a1_controls_sha256", "a1_gate_bundle_sha256", "a1_gate_report_sha256", "a3_policy_sha256", "a3_trigger_sha256", "instance_id_sha256", "ssh_host_fingerprint_sha256", "credential_fingerprint_sha256"):
            req_hash(data[key], "remote_plan_hash_invalid")
        parse_time(data["remote_deadline_utc"], "remote_plan_deadline_invalid")
        req_bool(data["single_use"], True, "remote_plan_single_use_invalid")
        req_bool(data["retry_allowed"], False, "remote_plan_retry_invalid")
        req_bool(data["second_action_allowed"], False, "remote_plan_second_action_invalid")
        validate_roots(data["root_contracts"])
        validate_evidence_root(data["evidence_root_contract"])
        validate_process_identity(data["process_identity"])
        check_identity(data, "plan_id", "autodl-a3-remote-plan-", "remote_plan_identity_invalid")
        return data

    class A3RemoteCloseoutPlan(Artifact):
        _validator = staticmethod(validate_plan)

    def parse_plan(value):
        if exact_type(value) is not A3RemoteCloseoutPlan:
            raise error_type("remote_plan_type_invalid")
        return A3RemoteCloseoutPlan.from_bytes(value.canonical_bytes())

    def build_a3_remote_root_marker(role, a1_plan_id, a1_gate_bundle_sha256):
        if role not in roles + (evidence_role,):
            raise error_type("root_marker_role_invalid")
        root = {"schema_version": schemas["marker"], "marker_id": "pending", "role": role, "a1_plan_id": req_id(a1_plan_id, "root_marker_plan_invalid"), "a1_gate_bundle_sha256": req_hash(a1_gate_bundle_sha256, "root_marker_gate_invalid")}
        root["marker_id"] = identity("autodl-a3-root-marker-", root, "marker_id")
        return canonical(root)

    def validate_root_marker(raw, role, plan):
        data = exact_map(parse_bytes(raw), ("schema_version", "marker_id", "role", "a1_plan_id", "a1_gate_bundle_sha256"), "root_marker_exact_keys_invalid")
        if data["schema_version"] != schemas["marker"] or data["role"] != role:
            raise error_type("root_marker_schema_or_role_invalid")
        req_id(data["marker_id"], "root_marker_id_invalid")
        req_id(data["a1_plan_id"], "root_marker_plan_invalid")
        req_hash(data["a1_gate_bundle_sha256"], "root_marker_gate_invalid")
        if data["marker_id"] != identity("autodl-a3-root-marker-", data, "marker_id"):
            raise error_type("root_marker_identity_invalid")
        contract = plan.data["evidence_root_contract"] if role == evidence_role else next_value(item for item in plan.data["root_contracts"] if item["role"] == role)
        if (data["marker_id"], digest(raw), data["a1_plan_id"], data["a1_gate_bundle_sha256"]) != (contract["marker_id"], contract["marker_sha256"], plan.data["a1_plan_id"], plan.data["a1_gate_bundle_sha256"]):
            raise error_type("root_marker_plan_binding_invalid")
        return data

    def create_plan(a1_plan_bytes, controls_bytes, gate_bundle_bytes, policy_bytes, trigger_bytes, credential_fingerprint_sha256, root_contracts, evidence_root_contract, process_identity):
        a1_plan = a1_plan_from_bytes(a1_plan_bytes)
        controls = controls_from_bytes(controls_bytes)
        gate_bundle = gate_from_bytes(gate_bundle_bytes)
        gate_report = gate_bundle.report()
        policy = policy_from_bytes(policy_bytes)
        trigger = trigger_from_bytes(trigger_bytes)
        if controls.data["plan_id"] != a1_plan.plan_id:
            raise error_type("remote_plan_controls_binding_invalid")
        if (gate_report.data["plan_id"], gate_report.data["plan_sha256"], gate_report.data["control_id"], gate_report.data["control_sha256"]) != (a1_plan.plan_id, a1_plan.sha256(), controls.data["control_id"], controls.sha256()):
            raise error_type("remote_plan_gate_binding_invalid")
        if (trigger.data["policy_id"], trigger.data["policy_sha256"], trigger.data["a1_plan_id"], trigger.data["a1_plan_sha256"]) != (policy.data["policy_id"], policy.sha256(), a1_plan.plan_id, a1_plan.sha256()):
            raise error_type("remote_plan_foundation_binding_invalid")
        if policy.data["cleanup_mode"] != cleanup_mode or policy.data["remote_deadline_minutes"] != deadline_minutes:
            raise error_type("remote_plan_policy_invalid")
        root = {
            "schema_version": schemas["plan"], "plan_id": "pending",
            "a1_plan_id": a1_plan.plan_id, "a1_plan_sha256": a1_plan.sha256(),
            "a1_controls_id": controls.data["control_id"], "a1_controls_sha256": controls.sha256(),
            "a1_gate_bundle_id": gate_bundle.data["bundle_id"], "a1_gate_bundle_sha256": gate_bundle.sha256(),
            "a1_gate_report_id": gate_report.data["report_id"], "a1_gate_report_sha256": gate_report.sha256(),
            "a3_policy_id": policy.data["policy_id"], "a3_policy_sha256": policy.sha256(),
            "a3_trigger_id": trigger.data["trigger_id"], "a3_trigger_sha256": trigger.sha256(),
            "instance_id_sha256": controls.data["instance_id_hash"],
            "ssh_host_fingerprint_sha256": controls.data["ssh_host_fingerprint_sha256"],
            "credential_fingerprint_sha256": req_hash(credential_fingerprint_sha256, "remote_plan_credential_invalid"),
            "cleanup_mode": cleanup_mode, "remote_deadline_utc": trigger.data["remote_deadline_utc"],
            "single_use": True, "retry_allowed": False, "second_action_allowed": False,
            "root_contracts": [dict_type(item) for item in validate_roots(root_contracts)],
            "evidence_root_contract": dict_type(validate_evidence_root(evidence_root_contract)),
            "process_identity": dict_type(validate_process_identity(process_identity)),
        }
        root["plan_id"] = identity("autodl-a3-remote-plan-", root, "plan_id")
        return A3RemoteCloseoutPlan(root)

    def validate_process_result(value, plan):
        data = exact_map(value, process_result_keys, "remote_receipt_process_exact_keys_invalid")
        for key in ("term_sent", "kill_sent"):
            if exact_type(data[key]) is not bool_type:
                raise error_type("remote_receipt_process_boolean_invalid")
        req_int(data["grace_seconds"], "remote_receipt_process_grace_invalid")
        for key in ("pid_sha256", "pgid_sha256", "cgroup_sha256", "gpu_processes_sha256", "listeners_sha256"):
            if data[key] != plan.data["process_identity"][key]:
                raise error_type("remote_receipt_process_binding_invalid")
            req_hash(data[key], "remote_receipt_process_hash_invalid")
        for key in ("post_process_count", "post_pgid_count", "post_cgroup_count", "post_gpu_process_count", "post_listener_count"):
            req_int(data[key], "remote_receipt_process_count_invalid")
        return data

    def validate_results(value, plan):
        rows = exact_list(value, "remote_receipt_root_results_invalid")
        if length_of(rows) != length_of(roles):
            raise error_type("remote_receipt_root_results_invalid")
        out = []
        for index, row in enumerate_values(rows):
            data = exact_map(row, result_keys, "remote_receipt_root_result_exact_keys_invalid")
            contract = plan.data["root_contracts"][index]
            if (data["role"], data["pre_inventory_tree_sha256"], data["pre_file_count"], data["pre_total_bytes"]) != (roles[index], contract["inventory_tree_sha256"], contract["file_count"], contract["total_bytes"]):
                raise error_type("remote_receipt_root_result_binding_invalid")
            for key in ("pre_file_count", "pre_total_bytes", "deleted_file_count", "deleted_directory_count", "post_residual_file_count", "post_residual_total_bytes"):
                req_int(data[key], "remote_receipt_root_result_count_invalid")
            if data["status"] not in ("deleted_exact", "incomplete"):
                raise error_type("remote_receipt_root_result_status_invalid")
            if data["status"] == "deleted_exact" and (data["deleted_file_count"], data["deleted_directory_count"], data["post_residual_file_count"], data["post_residual_total_bytes"]) != (contract["file_count"], contract["directory_count"], 0, 0):
                raise error_type("remote_receipt_root_result_complete_invalid")
            out.append(data)
        return tuple_type(out)

    def result_status(process, root_results):
        counts = tuple_type(process[name] for name in ("post_process_count", "post_pgid_count", "post_cgroup_count", "post_gpu_process_count", "post_listener_count"))
        return "remote_cleanup_observed" if process["term_sent"] is True and counts == (0, 0, 0, 0, 0) and all_value(row["status"] == "deleted_exact" for row in root_results) else "remote_cleanup_incomplete"

    def validate_receipt(value):
        data = exact_map(value, receipt_keys, "remote_receipt_exact_keys_invalid")
        if data["schema_version"] != schemas["receipt"] or data["status"] not in ("remote_cleanup_observed", "remote_cleanup_incomplete"):
            raise error_type("remote_receipt_schema_or_status_invalid")
        req_id(data["plan_id"], "remote_receipt_plan_id_invalid")
        req_hash(data["plan_sha256"], "remote_receipt_plan_hash_invalid")
        start, completed = parse_time(data["execution_started_utc"], "remote_receipt_time_invalid"), parse_time(data["execution_completed_utc"], "remote_receipt_time_invalid")
        if completed < start:
            raise error_type("remote_receipt_time_order_invalid")
        req_hash(data["event_sha256"], "remote_receipt_event_invalid")
        req_int(data["event_bytes"], "remote_receipt_event_invalid")
        for field, expected in (("manager_consumable", False), ("cleanup_complete", False), ("next_run_allowed", False), ("authorization_invalidated", True), ("physical_erasure_claimed", False)):
            req_bool(data[field], expected, "remote_receipt_authority_invalid")
        check_identity(data, "receipt_id", "autodl-a3-remote-receipt-", "remote_receipt_identity_invalid")
        return data

    class A3RemoteCloseoutReceipt(Artifact):
        _validator = staticmethod(validate_receipt)

    def parse_receipt(value):
        if exact_type(value) is not A3RemoteCloseoutReceipt:
            raise error_type("remote_receipt_type_invalid")
        return A3RemoteCloseoutReceipt.from_bytes(value.canonical_bytes())

    def build_receipt(plan, started_at_utc, completed_at_utc, process_result, root_results, event_bytes):
        parsed_plan = parse_plan(plan)
        start, completed = parse_time(started_at_utc, "remote_receipt_time_invalid"), parse_time(completed_at_utc, "remote_receipt_time_invalid")
        if not start <= completed <= parse_time(parsed_plan.data["remote_deadline_utc"], "remote_receipt_deadline_invalid"):
            raise error_type("remote_receipt_deadline_invalid")
        process, results = validate_process_result(process_result, parsed_plan), validate_results(root_results, parsed_plan)
        if exact_type(event_bytes) is not bytes_type:
            raise error_type("remote_receipt_event_invalid")
        root = {"schema_version": schemas["receipt"], "receipt_id": "pending", "plan_id": parsed_plan.data["plan_id"], "plan_sha256": parsed_plan.sha256(), "status": result_status(process, results), "execution_started_utc": format_time(start), "execution_completed_utc": format_time(completed), "process_result": dict_type(process), "root_results": [dict_type(row) for row in results], "event_sha256": digest(event_bytes), "event_bytes": length_of(event_bytes), "manager_consumable": False, "cleanup_complete": False, "next_run_allowed": False, "authorization_invalidated": True, "physical_erasure_claimed": False}
        root["receipt_id"] = identity("autodl-a3-remote-receipt-", root, "receipt_id")
        receipt = A3RemoteCloseoutReceipt(root)
        validate_receipt_against(receipt, parsed_plan, event_bytes)
        return receipt

    def validate_receipt_against(receipt, plan, event_bytes):
        parsed_plan, parsed = parse_plan(plan), parse_receipt(receipt)
        data = parsed.data
        if (data["plan_id"], data["plan_sha256"]) != (parsed_plan.data["plan_id"], parsed_plan.sha256()):
            raise error_type("remote_receipt_plan_binding_invalid")
        start, completed = parse_time(data["execution_started_utc"], "remote_receipt_time_invalid"), parse_time(data["execution_completed_utc"], "remote_receipt_time_invalid")
        if not start <= completed <= parse_time(parsed_plan.data["remote_deadline_utc"], "remote_receipt_deadline_invalid"):
            raise error_type("remote_receipt_deadline_invalid")
        process, results = validate_process_result(data["process_result"], parsed_plan), validate_results(data["root_results"], parsed_plan)
        if exact_type(event_bytes) is not bytes_type or (data["event_sha256"], data["event_bytes"], data["status"]) != (digest(event_bytes), length_of(event_bytes), result_status(process, results)):
            raise error_type("remote_receipt_replay_invalid")


    def validate_commit(value):
        data = exact_map(value, commit_keys, "remote_commit_exact_keys_invalid")
        if data["schema_version"] != schemas["commit"]:
            raise error_type("remote_commit_schema_invalid")
        for key in ("plan_id", "receipt_id"):
            req_id(data[key], "remote_commit_id_invalid")
        for key in ("plan_sha256", "receipt_sha256", "tree_sha256"):
            req_hash(data[key], "remote_commit_hash_invalid")
        files = exact_list(data["files"], "remote_commit_files_invalid")
        if [row.get("relative_name") if exact_type(row) is dict_type else None for row in files] != [event_name, receipt_name]:
            raise error_type("remote_commit_files_invalid")
        parsed = []
        for row in files:
            item = exact_map(row, event_keys, "remote_commit_file_invalid")
            req_text(item["relative_name"], "remote_commit_file_invalid")
            req_int(item["bytes"], "remote_commit_file_invalid")
            req_hash(item["sha256"], "remote_commit_file_invalid")
            parsed.append(item)
        if data["tree_sha256"] != digest(canonical(parsed)):
            raise error_type("remote_commit_tree_invalid")
        check_identity(data, "commit_id", "autodl-a3-remote-commit-", "remote_commit_identity_invalid")
        return data

    class A3RemoteCloseoutCommit(Artifact):
        _validator = staticmethod(validate_commit)

    def build_commit(plan, receipt, event_bytes):
        parsed_plan, parsed_receipt = parse_plan(plan), parse_receipt(receipt)
        validate_receipt_against(parsed_receipt, parsed_plan, event_bytes)
        files = [
            {"relative_name": event_name, "bytes": length_of(event_bytes), "sha256": digest(event_bytes)},
            {"relative_name": receipt_name, "bytes": length_of(parsed_receipt.canonical_bytes()), "sha256": parsed_receipt.sha256()},
        ]
        root = {"schema_version": schemas["commit"], "commit_id": "pending", "plan_id": parsed_plan.data["plan_id"], "plan_sha256": parsed_plan.sha256(), "receipt_id": parsed_receipt.data["receipt_id"], "receipt_sha256": parsed_receipt.sha256(), "files": files, "tree_sha256": digest(canonical(files))}
        root["commit_id"] = identity("autodl-a3-remote-commit-", root, "commit_id")
        return A3RemoteCloseoutCommit(root)

    def validate_commit_against(commit, plan, receipt, event_bytes):
        if exact_type(commit) is not A3RemoteCloseoutCommit:
            raise error_type("remote_commit_type_invalid")
        expected = build_commit(plan, receipt, event_bytes)
        if commit.canonical_bytes() != expected.canonical_bytes():
            raise error_type("remote_commit_binding_invalid")

    def validate_envelope(value):
        data = exact_map(value, envelope_keys, "remote_envelope_exact_keys_invalid")
        if data["schema_version"] != schemas["envelope"]:
            raise error_type("remote_envelope_schema_invalid")
        receipt = A3RemoteCloseoutReceipt.from_dict(data["receipt"])
        commit = A3RemoteCloseoutCommit.from_dict(data["commit"])
        if (commit.data["receipt_id"], commit.data["receipt_sha256"]) != (receipt.data["receipt_id"], receipt.sha256()):
            raise error_type("remote_envelope_receipt_binding_invalid")
        return data

    class A3RemoteCloseoutEnvelope(Artifact):
        _validator = staticmethod(validate_envelope)

    def build_envelope(plan, receipt, event_bytes):
        parsed_plan, parsed_receipt = parse_plan(plan), parse_receipt(receipt)
        commit = build_commit(parsed_plan, parsed_receipt, event_bytes)
        return A3RemoteCloseoutEnvelope({"schema_version": schemas["envelope"], "receipt": parsed_receipt.to_dict(), "commit": commit.to_dict()})

    def validate_a3_remote_closeout_envelope_bytes(plan_bytes, envelope_bytes, event_bytes):
        plan = A3RemoteCloseoutPlan.from_bytes(plan_bytes)
        envelope = A3RemoteCloseoutEnvelope.from_bytes(envelope_bytes)
        receipt = A3RemoteCloseoutReceipt.from_dict(envelope.data["receipt"])
        commit = A3RemoteCloseoutCommit.from_dict(envelope.data["commit"])
        validate_receipt_against(receipt, plan, event_bytes)
        validate_commit_against(commit, plan, receipt, event_bytes)
        return plan, receipt, commit


    # Public staging validates only local structural bindings.  No public API
    # has destructive A3 authority; a future action-time executor is deferred.
    def _safe_root(path, expected_hash):
        candidate = path_type(path)
        try:
            info = lstat(candidate)
        except os_error as exc:
            raise error_type("remote_root_missing") from exc
        if is_link(info.st_mode) or not is_dir(info.st_mode) or path_hash(candidate) != expected_hash:
            raise error_type("remote_root_identity_invalid")
        return candidate.resolve(strict=True)

    def _check_topology(roots):
        if length_of({string_type(root) for root in roots}) != length_of(roots):
            raise error_type("remote_root_topology_invalid")
        for index, left in enumerate_values(roots):
            for right in roots[index + 1:]:
                for parent, child in ((left, right), (right, left)):
                    try:
                        child.relative_to(parent)
                    except value_error:
                        continue
                    raise error_type("remote_root_topology_invalid")

    def _scan_root(root):
        rows, locations, stack = [], {}, [root]
        while stack:
            directory = stack.pop()
            try:
                entries = list_type(scandir(directory))
            except os_error as exc:
                raise error_type("remote_root_scan_failed") from exc
            for entry in entries:
                target = path_type(entry.path)
                try:
                    info = lstat(target)
                    relative = target.relative_to(root).as_posix()
                except (os_error, value_error) as exc:
                    raise error_type("remote_root_escape_invalid") from exc
                if not relative or is_link(info.st_mode) or not (is_dir(info.st_mode) or is_file(info.st_mode)):
                    raise error_type("remote_root_nonregular_invalid")
                key = digest(relative.encode("utf-8"))
                if is_file(info.st_mode):
                    raw = target.read_bytes()
                    row = {"relative_path_sha256": key, "kind": "file", "bytes": length_of(raw), "sha256": digest(raw)}
                else:
                    row = {"relative_path_sha256": key, "kind": "directory", "bytes": 0, "sha256": absent}
                    stack.append(target)
                rows.append(row)
                locations[key] = target
        return sort_values(rows, key=lambda row: row["relative_path_sha256"]), locations

    def _live_roots(plan, paths):
        contracts = plan.data["root_contracts"]
        roots = tuple_type(_safe_root(path, contracts[index]["normalized_path_sha256"]) for index, path in enumerate_values(paths))
        scanned = []
        for index, root in enumerate_values(roots):
            contract = contracts[index]
            try:
                marker_raw = (root / marker_name).read_bytes()
            except os_error as exc:
                raise error_type("remote_root_marker_missing") from exc
            validate_root_marker(marker_raw, roles[index], plan)
            rows, locations = _scan_root(root)
            if rows != contract["inventory"]:
                raise error_type("remote_root_inventory_invalid")
            scanned.append((rows, locations))
        return roots, tuple_type(scanned)

    def _live_evidence_root(plan, path):
        contract = plan.data["evidence_root_contract"]
        root = _safe_root(path, contract["normalized_path_sha256"])
        try:
            marker_raw = (root / marker_name).read_bytes()
        except os_error as exc:
            raise error_type("remote_evidence_root_marker_missing") from exc
        validate_root_marker(marker_raw, evidence_role, plan)
        rows, _locations = _scan_root(root)
        if rows != contract["inventory"]:
            raise error_type("remote_evidence_root_inventory_invalid")
        return root

    def _validate_staging_roots(plan, paths, evidence_path):
        roots, scanned = _live_roots(plan, paths)
        evidence = _live_evidence_root(plan, evidence_path)
        _check_topology(roots + (evidence,))
        return roots, scanned, evidence

    # No process, GPU, listener, deletion, or evidence-write helper is present in
    # this candidate.  A future destructive executor is intentionally deferred.

    def validate_staging_record(value):
        data = exact_map(value, staging_keys, "remote_staging_exact_keys_invalid")
        if data["schema_version"] != schemas["staging"] or data["status"] != "a3_structural_staging_validated":
            raise error_type("remote_staging_schema_or_status_invalid")
        req_id(data["plan_id"], "remote_staging_plan_id_invalid")
        for key in ("plan_sha256", "evidence_root_marker_sha256"):
            req_hash(data[key], "remote_staging_hash_invalid")
        req_id(data["evidence_root_marker_id"], "remote_staging_marker_id_invalid")
        for field, expected in (("manager_consumable", False), ("cleanup_complete", False), ("next_run_allowed", False), ("authorization_invalidated", True), ("physical_erasure_claimed", False)):
            req_bool(data[field], expected, "remote_staging_authority_invalid")
        check_identity(data, "staging_id", "autodl-a3-remote-staging-", "remote_staging_identity_invalid")
        return data

    class A3RemoteCloseoutStaging(Artifact):
        _validator = staticmethod(validate_staging_record)

    def run_a3_remote_closeout(plan_path, work_root, cache_root, log_root, transfer_root, evidence_root, execute_a3_closeout):
        if execute_a3_closeout is True:
            # A caller flag cannot establish destructive authority.  Reject before
            # any process, delete, or evidence-write helper is reached.
            raise error_type("a3_destructive_execution_not_available")
        if exact_type(execute_a3_closeout) is not bool_type or execute_a3_closeout is not False:
            raise error_type("a3_structural_staging_flag_invalid")
        plan = A3RemoteCloseoutPlan.from_bytes(path_type(plan_path).read_bytes())
        _validate_staging_roots(plan, (work_root, cache_root, log_root, transfer_root), evidence_root)
        contract = plan.data["evidence_root_contract"]
        root = {"schema_version": schemas["staging"], "staging_id": "pending", "status": "a3_structural_staging_validated", "plan_id": plan.data["plan_id"], "plan_sha256": plan.sha256(), "evidence_root_marker_id": contract["marker_id"], "evidence_root_marker_sha256": contract["marker_sha256"], "manager_consumable": False, "cleanup_complete": False, "next_run_allowed": False, "authorization_invalidated": True, "physical_erasure_claimed": False}
        root["staging_id"] = identity("autodl-a3-remote-staging-", root, "staging_id")
        return A3RemoteCloseoutStaging(root)

    # Private synthetic seam: no OS process, filesystem, network, browser, SSH,
    # credential, delete, release, or revocation action is reachable here.
    def _scripted_remote_closeout_for_tests(plan, *, started_at_utc, completed_at_utc, process_result, root_results):
        parsed = parse_plan(plan)
        event_bytes = canonical({"schema_version": "req2web.runtime.a3_remote_events.v1", "plan_id": parsed.data["plan_id"], "plan_sha256": parsed.sha256(), "scripted": True})
        receipt = build_receipt(parsed, started_at_utc, completed_at_utc, process_result, root_results, event_bytes)
        commit = build_commit(parsed, receipt, event_bytes)
        return event_bytes, receipt, commit, A3RemoteCloseoutEnvelope({"schema_version": schemas["envelope"], "receipt": receipt.to_dict(), "commit": commit.to_dict()})


    return {
        "A3RemoteCloseoutPlan": A3RemoteCloseoutPlan,
        "A3RemoteCloseoutReceipt": A3RemoteCloseoutReceipt,
        "A3RemoteCloseoutCommit": A3RemoteCloseoutCommit,
        "A3RemoteCloseoutEnvelope": A3RemoteCloseoutEnvelope,
        "A3RemoteCloseoutStaging": A3RemoteCloseoutStaging,
        "create_a3_remote_closeout_plan": create_plan,
        "build_a3_remote_root_marker": build_a3_remote_root_marker,
        "validate_a3_remote_closeout_envelope_bytes": validate_a3_remote_closeout_envelope_bytes,
        "validate_a3_remote_receipt_against": validate_receipt_against,
        "run_a3_remote_closeout": run_a3_remote_closeout,
        "_scripted_remote_closeout_for_tests": _scripted_remote_closeout_for_tests,
        "_canonical_for_tests": canonical,
        "_digest_for_tests": digest,
        "_path_hash_for_tests": path_hash,
        "A3_REMOTE_ROOT_ROLES": roles,
        "A3_REMOTE_EVIDENCE_ROLE": evidence_role,
        "A3_REMOTE_CLEANUP_MODE": cleanup_mode,
        "A3_REMOTE_ROOT_MARKER_FILENAME": marker_name,
        "A3_REMOTE_PROCESS_MANIFEST_FILENAME": process_name,
        "A3_REMOTE_ABSENT_HASH": absent,
    }


_AUTHORITIES = _build_authorities()
globals().update(_AUTHORITIES)

__all__ = [
    "A3RemoteCloseoutError", "A3RemoteCloseoutPlan", "A3RemoteCloseoutReceipt",
    "A3RemoteCloseoutCommit", "A3RemoteCloseoutEnvelope", "A3RemoteCloseoutStaging",
    "create_a3_remote_closeout_plan", "build_a3_remote_root_marker",
    "validate_a3_remote_closeout_envelope_bytes", "validate_a3_remote_receipt_against",
    "run_a3_remote_closeout", "A3_REMOTE_ROOT_ROLES", "A3_REMOTE_EVIDENCE_ROLE", "A3_REMOTE_CLEANUP_MODE",
    "A3_REMOTE_ROOT_MARKER_FILENAME", "A3_REMOTE_PROCESS_MANIFEST_FILENAME",
    "A3_REMOTE_ABSENT_HASH",
]
