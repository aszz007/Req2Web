from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Mapping, Sequence


class A3CloseoutError(ValueError):
    """Raised when an A3 structural evidence artifact is invalid."""


def _build_contract() -> dict[str, object]:
    error_type = A3CloseoutError
    dumps = json.dumps
    loads = json.loads
    sha256_fn = hashlib.sha256
    fullmatch = re.fullmatch
    datetime_type = datetime
    timedelta_type = timedelta
    utc = timezone.utc
    frozen_setattr = object.__setattr__
    exact_type = type
    length_of = len
    dict_type = dict
    list_type = list
    tuple_type = tuple
    frozen_set_type = frozenset
    sort_values = sorted
    set_type = set
    zip_values = zip
    any_value = any
    enumerate_values = enumerate
    bytes_type = bytes
    bool_type = bool
    int_type = int
    str_type = str
    type_error = TypeError
    value_error = ValueError
    unicode_decode_error = UnicodeDecodeError
    key_error = KeyError

    schemas = {
        "policy": "req2web.runtime.autodl_a3_policy.v1",
        "trigger": "req2web.runtime.autodl_a3_trigger.v1",
        "process": "req2web.runtime.autodl_a3_process_evidence.v1",
        "deletion": "req2web.runtime.autodl_a3_deletion_evidence.v1",
        "release": "req2web.runtime.autodl_a3_release_evidence.v1",
        "revocation": "req2web.runtime.autodl_a3_revocation_evidence.v1",
        "bundle": "req2web.runtime.autodl_a3_evidence_bundle.v1",
        "receipt": "req2web.runtime.autodl_a3_readiness_receipt.v1",
    }
    categories = (
        "process_termination",
        "project_side_deletion",
        "instance_release",
        "access_revocation",
    )
    cleanup_mode = "delete_work_cache_release_instance_revoke_access"
    remote_deadline_minutes = 120
    local_archive_retention_days = 30
    absent_hash = "0" * 64

    common_evidence_keys = (
        "schema_version", "evidence_id", "policy_id", "policy_sha256",
        "trigger_id", "trigger_sha256", "category", "source_class",
        "source_artifact_id", "source_artifact_sha256", "observed_at_utc", "status",
    )
    keys = {
        "policy": (
            "schema_version", "policy_id", "evidence_categories",
            "remote_deadline_minutes", "cleanup_mode", "local_archive_retention_days",
            "single_use", "physical_erasure_claimed", "new_payload_allowed",
            "inference_allowed", "retry_allowed", "repair_allowed", "second_provision_allowed",
        ),
        "trigger": (
            "schema_version", "trigger_id", "policy_id", "policy_sha256",
            "a1_plan_id", "a1_plan_sha256", "a1_execution_receipt_id",
            "a1_execution_receipt_sha256", "triggered_at_utc", "remote_deadline_utc",
            "single_use", "external_action_executed", "cleanup_complete",
            "physical_erasure_claimed",
        ),
        "process": common_evidence_keys + (
            "term_sent", "grace_seconds", "kill_sent", "pid_sha256", "pgid_sha256",
            "cgroup_sha256", "post_process_count", "post_gpu_process_count",
            "post_listener_count", "physical_erasure_claimed",
        ),
        "deletion": common_evidence_keys + (
            "pre_inventory_sha256", "pre_file_count", "pre_total_bytes", "action_identity",
            "post_residual_file_count", "post_residual_total_bytes", "physical_erasure_claimed",
        ),
        "release": common_evidence_keys + (
            "instance_id_sha256", "release_request_sha256", "control_plane_state",
            "storage_state", "billing_state", "physical_erasure_claimed",
        ),
        "revocation": common_evidence_keys + (
            "credential_fingerprint_sha256", "remote_access_state",
            "local_ephemeral_key_state", "instance_release_state", "instance_access_state",
            "physical_erasure_claimed",
        ),
        "inventory_row": ("category", "evidence_id", "bytes", "sha256"),
        "bundle": (
            "schema_version", "bundle_id", "policy", "trigger", "evidence_order",
            "evidence", "inventory", "tree_sha256", "external_action_authorized",
            "cleanup_complete", "manager_consumable", "physical_erasure_claimed",
        ),
        "receipt": (
            "schema_version", "receipt_id", "bundle_id", "bundle_sha256", "status",
            "failure_codes", "structural_evidence_ready", "manager_consumable",
            "external_action_authorized", "cleanup_complete", "next_run_allowed",
            "a2_unlocked", "h1_allowed", "formal_quality_allowed", "physical_erasure_claimed",
        ),
    }
    keysets = {name: frozen_set_type(value) for name, value in keys.items()}

    def canonical(value: object) -> bytes:
        try:
            return dumps(
                value, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        except (type_error, value_error) as exc:
            raise error_type("canonical_json_invalid") from exc

    def digest(raw: bytes) -> str:
        return sha256_fn(raw).hexdigest()

    def exact_map(value: object, name: str, code: str) -> dict[str, object]:
        if exact_type(value) is not dict_type or length_of(value) != length_of(keys[name]) or frozen_set_type(value) != keysets[name]:
            raise error_type(code)
        return dict_type(value)

    def exact_list(value: object, code: str) -> list[object]:
        if exact_type(value) is not list_type:
            raise error_type(code)
        return list_type(value)

    def parse_bytes(raw: object) -> dict[str, object]:
        if exact_type(raw) is not bytes_type or raw.startswith(b"\xef\xbb\xbf"):
            raise error_type("canonical_bytes_invalid")
        try:
            value = loads(raw.decode("utf-8"))
        except (unicode_decode_error, value_error) as exc:
            raise error_type("canonical_bytes_invalid") from exc
        if exact_type(value) is not dict_type or canonical(value) != raw:
            raise error_type("canonical_bytes_invalid")
        return value

    def req_bool(value: object, expected: bool, code: str) -> None:
        if exact_type(value) is not bool_type or value is not expected:
            raise error_type(code)

    def req_int(value: object, code: str, minimum: int = 0) -> int:
        if exact_type(value) is not int_type or value < minimum:
            raise error_type(code)
        return value

    def req_text(value: object, code: str) -> str:
        if exact_type(value) is not str_type or not value or length_of(value) > 256:
            raise error_type(code)
        return value

    def req_id(value: object, code: str) -> str:
        text = req_text(value, code)
        if fullmatch(r"[a-z0-9][a-z0-9._:-]{2,255}", text) is None:
            raise error_type(code)
        return text

    def req_hash(value: object, code: str, allow_absent: bool = False) -> str:
        if exact_type(value) is not str_type or fullmatch(r"[0-9a-f]{64}", value) is None:
            raise error_type(code)
        if value == absent_hash and not allow_absent:
            raise error_type(code)
        return value

    def parse_time(value: object, code: str) -> datetime:
        if exact_type(value) is not str_type or fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is None:
            raise error_type(code)
        try:
            return datetime_type.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=utc)
        except value_error as exc:
            raise error_type(code) from exc

    def format_time(value: datetime) -> str:
        return value.astimezone(utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def identify(prefix: str, data: dict[str, object], key: str) -> dict[str, object]:
        root = {name: item for name, item in data.items() if name != key}
        data[key] = prefix + digest(canonical(root))[:20]
        return data

    def req_identity(data: dict[str, object], key: str, prefix: str, code: str) -> None:
        req_id(data[key], code)
        root = {name: item for name, item in data.items() if name != key}
        if data[key] != prefix + digest(canonical(root))[:20]:
            raise error_type(code)

    def artifact_type(name: str, validator):
        captured_validator = validator

        @dataclass(frozen=True)
        class Artifact:
            data: Mapping[str, object]

            def __post_init__(self) -> None:
                frozen_setattr(self, "data", captured_validator(self.data))

            @classmethod
            def from_dict(cls, value: object):
                return cls(captured_validator(value))

            @classmethod
            def from_bytes(cls, raw: object):
                return cls(captured_validator(parse_bytes(raw)))

            def validate(self) -> None:
                captured_validator(self.data)

            def to_dict(self) -> dict[str, object]:
                return captured_validator(self.data)

            def canonical_bytes(self) -> bytes:
                return canonical(captured_validator(self.data))

            def sha256(self) -> str:
                return digest(self.canonical_bytes())

        Artifact.__name__ = name
        Artifact.__qualname__ = name
        return Artifact

    def validate_policy(value: object) -> dict[str, object]:
        data = exact_map(value, "policy", "policy_exact_keys_invalid")
        if data["schema_version"] != schemas["policy"] or data["evidence_categories"] != list_type(categories):
            raise error_type("policy_schema_or_categories_invalid")
        if data["remote_deadline_minutes"] != remote_deadline_minutes:
            raise error_type("policy_remote_deadline_invalid")
        if data["cleanup_mode"] != cleanup_mode or data["local_archive_retention_days"] != local_archive_retention_days:
            raise error_type("policy_cleanup_invalid")
        req_bool(data["single_use"], True, "policy_single_use_invalid")
        for field in (
            "physical_erasure_claimed", "new_payload_allowed", "inference_allowed",
            "retry_allowed", "repair_allowed", "second_provision_allowed",
        ):
            req_bool(data[field], False, f"policy_{field}_invalid")
        req_identity(data, "policy_id", "autodl-a3-policy-", "policy_identity_invalid")
        return data

    Policy = artifact_type("A3CloseoutPolicy", validate_policy)
    policy_from_dict = Policy.from_dict

    def create_policy():
        root = {
            "schema_version": schemas["policy"], "policy_id": "pending",
            "evidence_categories": list_type(categories),
            "remote_deadline_minutes": remote_deadline_minutes,
            "cleanup_mode": cleanup_mode,
            "local_archive_retention_days": local_archive_retention_days,
            "single_use": True, "physical_erasure_claimed": False,
            "new_payload_allowed": False, "inference_allowed": False,
            "retry_allowed": False, "repair_allowed": False, "second_provision_allowed": False,
        }
        return Policy(validate_policy(identify("autodl-a3-policy-", root, "policy_id")))

    def parse_policy(value: object):
        if exact_type(value) is not Policy:
            raise error_type("policy_type_invalid")
        return policy_from_dict(value.data)

    def validate_trigger(value: object) -> dict[str, object]:
        data = exact_map(value, "trigger", "trigger_exact_keys_invalid")
        if data["schema_version"] != schemas["trigger"]:
            raise error_type("trigger_schema_invalid")
        req_id(data["policy_id"], "trigger_policy_id_invalid")
        req_hash(data["policy_sha256"], "trigger_policy_hash_invalid")
        req_id(data["a1_plan_id"], "trigger_plan_id_invalid")
        req_hash(data["a1_plan_sha256"], "trigger_plan_hash_invalid")
        req_id(data["a1_execution_receipt_id"], "trigger_receipt_id_invalid")
        req_hash(data["a1_execution_receipt_sha256"], "trigger_receipt_hash_invalid")
        start = parse_time(data["triggered_at_utc"], "trigger_time_invalid")
        deadline = parse_time(data["remote_deadline_utc"], "trigger_deadline_invalid")
        if deadline != start + timedelta_type(minutes=remote_deadline_minutes):
            raise error_type("trigger_deadline_invalid")
        req_bool(data["single_use"], True, "trigger_single_use_invalid")
        for field in ("external_action_executed", "cleanup_complete", "physical_erasure_claimed"):
            req_bool(data[field], False, f"trigger_{field}_invalid")
        req_identity(data, "trigger_id", "autodl-a3-trigger-", "trigger_identity_invalid")
        return data

    Trigger = artifact_type("A3CloseoutTrigger", validate_trigger)
    trigger_from_dict = Trigger.from_dict

    def create_trigger(policy, a1_plan_id: str, a1_plan_sha256: str,
                       a1_execution_receipt_id: str, a1_execution_receipt_sha256: str,
                       triggered_at_utc: str):
        parsed_policy = parse_policy(policy)
        start = parse_time(triggered_at_utc, "trigger_time_invalid")
        root = {
            "schema_version": schemas["trigger"], "trigger_id": "pending",
            "policy_id": parsed_policy.data["policy_id"], "policy_sha256": parsed_policy.sha256(),
            "a1_plan_id": req_id(a1_plan_id, "trigger_plan_id_invalid"),
            "a1_plan_sha256": req_hash(a1_plan_sha256, "trigger_plan_hash_invalid"),
            "a1_execution_receipt_id": req_id(a1_execution_receipt_id, "trigger_receipt_id_invalid"),
            "a1_execution_receipt_sha256": req_hash(a1_execution_receipt_sha256, "trigger_receipt_hash_invalid"),
            "triggered_at_utc": format_time(start),
            "remote_deadline_utc": format_time(start + timedelta_type(minutes=remote_deadline_minutes)),
            "single_use": True, "external_action_executed": False,
            "cleanup_complete": False, "physical_erasure_claimed": False,
        }
        return Trigger(validate_trigger(identify("autodl-a3-trigger-", root, "trigger_id")))

    def parse_trigger(value: object):
        if exact_type(value) is not Trigger:
            raise error_type("trigger_type_invalid")
        return trigger_from_dict(value.data)

    def evidence_base(schema: str, category: str, source_class: str, policy, trigger,
                      source_artifact_id: str, source_artifact_sha256: str,
                      observed_at_utc: str, status: str) -> dict[str, object]:
        parsed_policy = parse_policy(policy)
        parsed_trigger = parse_trigger(trigger)
        if (parsed_trigger.data["policy_id"], parsed_trigger.data["policy_sha256"]) != (parsed_policy.data["policy_id"], parsed_policy.sha256()):
            raise error_type("evidence_policy_trigger_binding_invalid")
        return {
            "schema_version": schema, "evidence_id": "pending",
            "policy_id": parsed_policy.data["policy_id"], "policy_sha256": parsed_policy.sha256(),
            "trigger_id": parsed_trigger.data["trigger_id"], "trigger_sha256": parsed_trigger.sha256(),
            "category": category, "source_class": source_class,
            "source_artifact_id": req_id(source_artifact_id, f"{category}_source_artifact_id_invalid"),
            "source_artifact_sha256": req_hash(source_artifact_sha256, f"{category}_source_artifact_hash_invalid", status == "not_executed"),
            "observed_at_utc": format_time(parse_time(observed_at_utc, f"{category}_time_invalid")),
            "status": status,
        }

    def validate_common(data: dict[str, object], schema: str, category: str, source_class: str) -> None:
        if data["schema_version"] != schema or data["category"] != category:
            raise error_type(f"{category}_schema_or_category_invalid")
        if data["source_class"] != source_class:
            raise error_type(f"{category}_source_invalid")
        req_id(data["policy_id"], f"{category}_policy_id_invalid")
        req_hash(data["policy_sha256"], f"{category}_policy_hash_invalid")
        req_id(data["trigger_id"], f"{category}_trigger_id_invalid")
        req_hash(data["trigger_sha256"], f"{category}_trigger_hash_invalid")
        req_id(data["source_artifact_id"], f"{category}_source_artifact_id_invalid")
        req_hash(data["source_artifact_sha256"], f"{category}_source_artifact_hash_invalid", data["status"] == "not_executed")
        parse_time(data["observed_at_utc"], f"{category}_time_invalid")

    def validate_process(value: object) -> dict[str, object]:
        data = exact_map(value, "process", "process_exact_keys_invalid")
        validate_common(data, schemas["process"], categories[0], "ssh_remote_observer")
        if data["status"] not in ("process_termination_observed", "process_termination_incomplete", "not_executed"):
            raise error_type("process_status_invalid")
        for field in ("term_sent", "kill_sent"):
            if exact_type(data[field]) is not bool_type:
                raise error_type(f"process_{field}_invalid")
        req_int(data["grace_seconds"], "process_grace_invalid")
        for field in ("pid_sha256", "pgid_sha256", "cgroup_sha256"):
            req_hash(data[field], f"process_{field}_invalid", data["status"] == "not_executed")
        counts = tuple_type(req_int(data[field], f"process_{field}_invalid") for field in ("post_process_count", "post_gpu_process_count", "post_listener_count"))
        req_bool(data["physical_erasure_claimed"], False, "process_physical_erasure_invalid")
        if data["status"] == "process_termination_observed" and (data["term_sent"] is not True or counts != (0, 0, 0)):
            raise error_type("process_observed_state_invalid")
        if data["status"] == "not_executed" and (data["term_sent"] or data["kill_sent"] or data["grace_seconds"] or counts != (0, 0, 0)):
            raise error_type("process_not_executed_state_invalid")
        req_identity(data, "evidence_id", "autodl-a3-process-", "process_identity_invalid")
        return data

    Process = artifact_type("A3ProcessTerminationEvidence", validate_process)

    def create_process_evidence(
        policy, trigger, *, source_artifact_id: str, source_artifact_sha256: str,
        observed_at_utc: str, term_sent: bool, grace_seconds: int, kill_sent: bool,
        pid_sha256: str, pgid_sha256: str, cgroup_sha256: str,
        post_process_count: int, post_gpu_process_count: int, post_listener_count: int,
    ):
        if source_artifact_sha256 == absent_hash:
            status = "not_executed"
        elif term_sent and (post_process_count, post_gpu_process_count, post_listener_count) == (0, 0, 0):
            status = "process_termination_observed"
        else:
            status = "process_termination_incomplete"
        root = evidence_base(
            schemas["process"], categories[0], "ssh_remote_observer", policy, trigger,
            source_artifact_id, source_artifact_sha256, observed_at_utc, status,
        )
        root.update({
            "term_sent": term_sent, "grace_seconds": grace_seconds, "kill_sent": kill_sent,
            "pid_sha256": pid_sha256, "pgid_sha256": pgid_sha256, "cgroup_sha256": cgroup_sha256,
            "post_process_count": post_process_count,
            "post_gpu_process_count": post_gpu_process_count,
            "post_listener_count": post_listener_count,
        })
        root["physical_erasure_claimed"] = False
        return Process(validate_process(identify("autodl-a3-process-", root, "evidence_id")))

    def validate_deletion(value: object) -> dict[str, object]:
        data = exact_map(value, "deletion", "deletion_exact_keys_invalid")
        validate_common(data, schemas["deletion"], categories[1], "ssh_remote_observer")
        if data["status"] not in ("project_side_deletion_observed", "project_side_deletion_incomplete", "not_executed"):
            raise error_type("deletion_status_invalid")
        req_hash(data["pre_inventory_sha256"], "deletion_inventory_hash_invalid", data["status"] == "not_executed")
        pre = (req_int(data["pre_file_count"], "deletion_pre_count_invalid"), req_int(data["pre_total_bytes"], "deletion_pre_bytes_invalid"))
        if data["action_identity"] != "project_side_delete_work_cache_v1":
            raise error_type("deletion_action_identity_invalid")
        residual = (req_int(data["post_residual_file_count"], "deletion_residual_count_invalid"), req_int(data["post_residual_total_bytes"], "deletion_residual_bytes_invalid"))
        req_bool(data["physical_erasure_claimed"], False, "deletion_physical_erasure_invalid")
        if data["status"] == "project_side_deletion_observed" and residual != (0, 0):
            raise error_type("deletion_observed_state_invalid")
        if data["status"] == "not_executed" and (pre != (0, 0) or residual != (0, 0)):
            raise error_type("deletion_not_executed_state_invalid")
        req_identity(data, "evidence_id", "autodl-a3-deletion-", "deletion_identity_invalid")
        return data

    Deletion = artifact_type("A3ProjectSideDeletionEvidence", validate_deletion)

    def create_deletion_evidence(
        policy, trigger, *, source_artifact_id: str, source_artifact_sha256: str,
        observed_at_utc: str, pre_inventory_sha256: str, pre_file_count: int,
        pre_total_bytes: int, post_residual_file_count: int, post_residual_total_bytes: int,
    ):
        if source_artifact_sha256 == absent_hash:
            status = "not_executed"
        elif (post_residual_file_count, post_residual_total_bytes) == (0, 0):
            status = "project_side_deletion_observed"
        else:
            status = "project_side_deletion_incomplete"
        root = evidence_base(
            schemas["deletion"], categories[1], "ssh_remote_observer", policy, trigger,
            source_artifact_id, source_artifact_sha256, observed_at_utc, status,
        )
        root.update({
            "pre_inventory_sha256": pre_inventory_sha256, "pre_file_count": pre_file_count,
            "pre_total_bytes": pre_total_bytes,
            "post_residual_file_count": post_residual_file_count,
            "post_residual_total_bytes": post_residual_total_bytes,
        })
        root["action_identity"] = "project_side_delete_work_cache_v1"
        root["physical_erasure_claimed"] = False
        return Deletion(validate_deletion(identify("autodl-a3-deletion-", root, "evidence_id")))

    release_state_tuples = {
        "released": ("released", "released", "not_running"),
        "stopped": ("stopped", "retained", "stopped"),
        "requested": ("requested", "unknown", "unknown"),
        "unknown": ("unknown", "unknown", "unknown"),
        "not_executed": ("not_executed", "not_executed", "not_executed"),
    }
    release_status_by_tuple = {value: status for status, value in release_state_tuples.items()}

    def validate_release(value: object) -> dict[str, object]:
        data = exact_map(value, "release", "release_exact_keys_invalid")
        validate_common(data, schemas["release"], categories[2], "autodl_browser_control_plane_operator")
        states = (data["control_plane_state"], data["storage_state"], data["billing_state"])
        if data["status"] not in release_state_tuples or states != release_state_tuples[data["status"]]:
            raise error_type("release_state_tuple_invalid")
        req_hash(data["instance_id_sha256"], "release_instance_hash_invalid", data["status"] == "not_executed")
        req_hash(data["release_request_sha256"], "release_request_hash_invalid", data["status"] in ("unknown", "not_executed"))
        req_bool(data["physical_erasure_claimed"], False, "release_physical_erasure_invalid")
        req_identity(data, "evidence_id", "autodl-a3-release-", "release_identity_invalid")
        return data

    Release = artifact_type("A3InstanceReleaseEvidence", validate_release)

    def create_release_evidence(
        policy, trigger, *, source_artifact_id: str, source_artifact_sha256: str,
        observed_at_utc: str, instance_id_sha256: str, release_request_sha256: str,
        control_plane_state: str, storage_state: str, billing_state: str,
    ):
        states = (control_plane_state, storage_state, billing_state)
        try:
            status = release_status_by_tuple[states]
        except key_error as exc:
            raise error_type("release_state_tuple_invalid") from exc
        if source_artifact_sha256 == absent_hash and status != "not_executed":
            raise error_type("release_not_executed_state_invalid")
        if source_artifact_sha256 != absent_hash and status == "not_executed":
            raise error_type("release_source_state_invalid")
        root = evidence_base(
            schemas["release"], categories[2], "autodl_browser_control_plane_operator",
            policy, trigger, source_artifact_id, source_artifact_sha256,
            observed_at_utc, status,
        )
        root.update({
            "instance_id_sha256": instance_id_sha256,
            "release_request_sha256": release_request_sha256,
            "control_plane_state": control_plane_state,
            "storage_state": storage_state,
            "billing_state": billing_state,
        })
        root["physical_erasure_claimed"] = False
        return Release(validate_release(identify("autodl-a3-release-", root, "evidence_id")))

    def validate_revocation(value: object) -> dict[str, object]:
        data = exact_map(value, "revocation", "revocation_exact_keys_invalid")
        validate_common(data, schemas["revocation"], categories[3], "local_operator_attestation")
        if data["status"] not in ("access_revocation_observed", "access_revocation_incomplete", "not_executed"):
            raise error_type("revocation_status_invalid")
        req_hash(data["credential_fingerprint_sha256"], "revocation_fingerprint_invalid", data["status"] == "not_executed")
        allowed = {
            "remote_access_state": ("removed", "present", "unknown", "not_executed"),
            "local_ephemeral_key_state": ("removed", "present", "unknown", "not_executed"),
            "instance_release_state": ("released", "stopped", "requested", "unknown", "not_executed"),
            "instance_access_state": ("inaccessible", "accessible", "unknown", "not_executed"),
        }
        for field, values in allowed.items():
            if data[field] not in values:
                raise error_type(f"revocation_{field}_invalid")
        req_bool(data["physical_erasure_claimed"], False, "revocation_physical_erasure_invalid")
        states = (data["remote_access_state"], data["local_ephemeral_key_state"], data["instance_release_state"], data["instance_access_state"])
        if data["status"] == "access_revocation_observed" and states != ("removed", "removed", "released", "inaccessible"):
            raise error_type("revocation_observed_state_invalid")
        if data["status"] == "not_executed" and states != ("not_executed",) * 4:
            raise error_type("revocation_not_executed_state_invalid")
        req_identity(data, "evidence_id", "autodl-a3-revocation-", "revocation_identity_invalid")
        return data

    Revocation = artifact_type("A3AccessRevocationEvidence", validate_revocation)

    def create_revocation_evidence(
        policy, trigger, *, source_artifact_id: str, source_artifact_sha256: str,
        observed_at_utc: str, credential_fingerprint_sha256: str,
        remote_access_state: str, local_ephemeral_key_state: str,
        instance_release_state: str, instance_access_state: str,
    ):
        states = (
            remote_access_state, local_ephemeral_key_state,
            instance_release_state, instance_access_state,
        )
        if source_artifact_sha256 == absent_hash:
            status = "not_executed"
        elif states == ("removed", "removed", "released", "inaccessible"):
            status = "access_revocation_observed"
        else:
            status = "access_revocation_incomplete"
        root = evidence_base(
            schemas["revocation"], categories[3], "local_operator_attestation",
            policy, trigger, source_artifact_id, source_artifact_sha256,
            observed_at_utc, status,
        )
        root.update({
            "credential_fingerprint_sha256": credential_fingerprint_sha256,
            "remote_access_state": remote_access_state,
            "local_ephemeral_key_state": local_ephemeral_key_state,
            "instance_release_state": instance_release_state,
            "instance_access_state": instance_access_state,
        })
        root["physical_erasure_claimed"] = False
        return Revocation(validate_revocation(identify("autodl-a3-revocation-", root, "evidence_id")))

    evidence_types = (Process, Deletion, Release, Revocation)
    evidence_parsers = (Process.from_dict, Deletion.from_dict, Release.from_dict, Revocation.from_dict)

    def parse_evidence_objects(values: object) -> tuple[object, ...]:
        if exact_type(values) not in (list_type, tuple_type) or length_of(values) != 4:
            raise error_type("bundle_evidence_count_invalid")
        result = []
        for index, (value, expected_type, parser) in enumerate_values(zip_values(values, evidence_types, evidence_parsers)):
            if exact_type(value) is not expected_type:
                raise error_type("bundle_evidence_type_invalid")
            parsed = parser(value.data)
            if parsed.data["category"] != categories[index]:
                raise error_type("bundle_evidence_order_invalid")
            result.append(parsed)
        return tuple_type(result)

    def parse_evidence_dicts(values: object) -> tuple[object, ...]:
        rows = exact_list(values, "bundle_evidence_invalid")
        if length_of(rows) != 4:
            raise error_type("bundle_evidence_count_invalid")
        result = tuple_type(parser(row) for parser, row in zip_values(evidence_parsers, rows))
        if tuple_type(item.data["category"] for item in result) != categories:
            raise error_type("bundle_evidence_order_invalid")
        return result

    def validate_bundle(value: object) -> dict[str, object]:
        data = exact_map(value, "bundle", "bundle_exact_keys_invalid")
        if data["schema_version"] != schemas["bundle"] or data["evidence_order"] != list_type(categories):
            raise error_type("bundle_schema_or_order_invalid")
        policy = policy_from_dict(data["policy"])
        trigger = trigger_from_dict(data["trigger"])
        if (trigger.data["policy_id"], trigger.data["policy_sha256"]) != (policy.data["policy_id"], policy.sha256()):
            raise error_type("bundle_policy_trigger_binding_invalid")
        evidence = parse_evidence_dicts(data["evidence"])
        start = parse_time(trigger.data["triggered_at_utc"], "bundle_trigger_time_invalid")
        deadline = parse_time(trigger.data["remote_deadline_utc"], "bundle_deadline_invalid")
        previous = start
        for item in evidence:
            if (item.data["policy_id"], item.data["policy_sha256"], item.data["trigger_id"], item.data["trigger_sha256"]) != (policy.data["policy_id"], policy.sha256(), trigger.data["trigger_id"], trigger.sha256()):
                raise error_type("bundle_evidence_binding_invalid")
            current = parse_time(item.data["observed_at_utc"], "bundle_evidence_time_invalid")
            if current < previous:
                raise error_type("bundle_evidence_time_order_invalid")
            if current > deadline:
                raise error_type("bundle_evidence_after_deadline")
            previous = current
        expected_inventory = []
        for item in evidence:
            raw = item.canonical_bytes()
            expected_inventory.append({"category": item.data["category"], "evidence_id": item.data["evidence_id"], "bytes": length_of(raw), "sha256": digest(raw)})
        inventory = exact_list(data["inventory"], "bundle_inventory_invalid")
        parsed_inventory = []
        for row in inventory:
            parsed = exact_map(row, "inventory_row", "bundle_inventory_row_invalid")
            req_text(parsed["category"], "bundle_inventory_category_invalid")
            req_id(parsed["evidence_id"], "bundle_inventory_id_invalid")
            req_int(parsed["bytes"], "bundle_inventory_bytes_invalid", 1)
            req_hash(parsed["sha256"], "bundle_inventory_hash_invalid")
            parsed_inventory.append(parsed)
        if parsed_inventory != expected_inventory or data["tree_sha256"] != digest(canonical(expected_inventory)):
            raise error_type("bundle_inventory_binding_invalid")
        for field in ("external_action_authorized", "cleanup_complete", "manager_consumable", "physical_erasure_claimed"):
            req_bool(data[field], False, f"bundle_{field}_invalid")
        req_identity(data, "bundle_id", "autodl-a3-bundle-", "bundle_identity_invalid")
        return data

    Bundle = artifact_type("A3CloseoutEvidenceBundle", validate_bundle)
    bundle_from_dict = Bundle.from_dict

    def create_bundle(policy, trigger, evidence: Sequence[object]):
        parsed_policy = parse_policy(policy)
        parsed_trigger = parse_trigger(trigger)
        parsed_evidence = parse_evidence_objects(evidence)
        inventory = []
        for item in parsed_evidence:
            raw = item.canonical_bytes()
            inventory.append({"category": item.data["category"], "evidence_id": item.data["evidence_id"], "bytes": length_of(raw), "sha256": digest(raw)})
        root = {
            "schema_version": schemas["bundle"], "bundle_id": "pending",
            "policy": parsed_policy.to_dict(), "trigger": parsed_trigger.to_dict(),
            "evidence_order": list_type(categories), "evidence": [item.to_dict() for item in parsed_evidence],
            "inventory": inventory, "tree_sha256": digest(canonical(inventory)),
            "external_action_authorized": False, "cleanup_complete": False,
            "manager_consumable": False, "physical_erasure_claimed": False,
        }
        return Bundle(validate_bundle(identify("autodl-a3-bundle-", root, "bundle_id")))

    def parse_bundle(value: object):
        if exact_type(value) is not Bundle:
            raise error_type("bundle_type_invalid")
        return bundle_from_dict(value.data)

    complete_statuses = (
        "process_termination_observed", "project_side_deletion_observed",
        "released", "access_revocation_observed",
    )

    def readiness_state(bundle) -> tuple[str, list[str], bool]:
        parsed = parse_bundle(bundle)
        statuses = tuple_type(item.data["status"] for item in parse_evidence_dicts(parsed.data["evidence"]))
        if statuses == ("not_executed",) * 4:
            return "a3_not_executed", ["a3_not_executed"], False
        if statuses == complete_statuses:
            return "a3_structural_evidence_ready_not_manager_consumable", [], True
        failures = sort_values(f"{category}_incomplete" for category, actual, expected in zip_values(categories, statuses, complete_statuses) if actual != expected)
        return "a3_evidence_incomplete", failures, False

    def validate_receipt(value: object) -> dict[str, object]:
        data = exact_map(value, "receipt", "readiness_receipt_exact_keys_invalid")
        if data["schema_version"] != schemas["receipt"]:
            raise error_type("readiness_receipt_schema_invalid")
        req_id(data["bundle_id"], "readiness_receipt_bundle_id_invalid")
        req_hash(data["bundle_sha256"], "readiness_receipt_bundle_hash_invalid")
        if data["status"] not in ("a3_structural_evidence_ready_not_manager_consumable", "a3_evidence_incomplete", "a3_not_executed"):
            raise error_type("readiness_receipt_status_invalid")
        codes = exact_list(data["failure_codes"], "readiness_receipt_failure_codes_invalid")
        incomplete_codes = tuple_type(f"{category}_incomplete" for category in categories)
        allowed_codes = frozen_set_type(("a3_not_executed",) + incomplete_codes)
        if (
            any_value(exact_type(code) is not str_type or code not in allowed_codes for code in codes)
            or codes != sort_values(set_type(codes))
        ):
            raise error_type("readiness_receipt_failure_codes_invalid")
        ready = data["structural_evidence_ready"]
        if exact_type(ready) is not bool_type:
            raise error_type("readiness_receipt_structural_flag_invalid")
        status = data["status"]
        if status == "a3_structural_evidence_ready_not_manager_consumable":
            if ready is not True or codes:
                raise error_type("readiness_receipt_status_consistency_invalid")
        elif status == "a3_not_executed":
            if ready is not False or codes != ["a3_not_executed"]:
                raise error_type("readiness_receipt_status_consistency_invalid")
        elif ready is not False or not codes or "a3_not_executed" in codes:
            raise error_type("readiness_receipt_status_consistency_invalid")
        for field in ("manager_consumable", "external_action_authorized", "cleanup_complete", "next_run_allowed", "a2_unlocked", "h1_allowed", "formal_quality_allowed", "physical_erasure_claimed"):
            req_bool(data[field], False, f"readiness_receipt_{field}_invalid")
        req_identity(data, "receipt_id", "autodl-a3-readiness-", "readiness_receipt_identity_invalid")
        return data

    Receipt = artifact_type("A3CloseoutReadinessReceipt", validate_receipt)
    receipt_from_bytes = Receipt.from_bytes

    def evaluate_readiness(bundle):
        parsed_bundle = parse_bundle(bundle)
        status, failures, ready = readiness_state(parsed_bundle)
        root = {
            "schema_version": schemas["receipt"], "receipt_id": "pending",
            "bundle_id": parsed_bundle.data["bundle_id"], "bundle_sha256": parsed_bundle.sha256(),
            "status": status, "failure_codes": failures, "structural_evidence_ready": ready,
            "manager_consumable": False, "external_action_authorized": False,
            "cleanup_complete": False, "next_run_allowed": False, "a2_unlocked": False,
            "h1_allowed": False, "formal_quality_allowed": False, "physical_erasure_claimed": False,
        }
        return Receipt(validate_receipt(identify("autodl-a3-readiness-", root, "receipt_id")))

    def validate_receipt_against(receipt, bundle) -> None:
        if exact_type(receipt) is not Receipt:
            raise error_type("readiness_receipt_type_invalid")
        parsed_receipt = Receipt.from_dict(receipt.data)
        expected = evaluate_readiness(parse_bundle(bundle))
        if parsed_receipt.canonical_bytes() != expected.canonical_bytes():
            raise error_type("readiness_receipt_bundle_binding_invalid")

    def validate_bundle_bytes(bundle_bytes: object, receipt_bytes: object):
        bundle = Bundle.from_bytes(bundle_bytes)
        receipt = receipt_from_bytes(receipt_bytes)
        validate_receipt_against(receipt, bundle)
        return bundle, receipt

    return {
        "A3CloseoutPolicy": Policy,
        "A3CloseoutTrigger": Trigger,
        "A3ProcessTerminationEvidence": Process,
        "A3ProjectSideDeletionEvidence": Deletion,
        "A3InstanceReleaseEvidence": Release,
        "A3AccessRevocationEvidence": Revocation,
        "A3CloseoutEvidenceBundle": Bundle,
        "A3CloseoutReadinessReceipt": Receipt,
        "create_a3_closeout_policy": create_policy,
        "create_a3_closeout_trigger": create_trigger,
        "create_a3_process_termination_evidence": create_process_evidence,
        "create_a3_project_side_deletion_evidence": create_deletion_evidence,
        "create_a3_instance_release_evidence": create_release_evidence,
        "create_a3_access_revocation_evidence": create_revocation_evidence,
        "create_a3_closeout_evidence_bundle": create_bundle,
        "evaluate_a3_closeout_readiness": evaluate_readiness,
        "validate_a3_closeout_readiness_against": validate_receipt_against,
        "validate_a3_closeout_bundle_bytes": validate_bundle_bytes,
        "A3_EVIDENCE_CATEGORIES": categories,
        "A3_CLEANUP_MODE": cleanup_mode,
        "A3_REMOTE_DEADLINE_MINUTES": remote_deadline_minutes,
        "A3_LOCAL_ARCHIVE_RETENTION_DAYS": local_archive_retention_days,
        "A3_ABSENT_HASH": absent_hash,
    }


globals().update(_build_contract())

__all__ = [
    "A3CloseoutError", "A3CloseoutPolicy", "A3CloseoutTrigger",
    "A3ProcessTerminationEvidence", "A3ProjectSideDeletionEvidence",
    "A3InstanceReleaseEvidence", "A3AccessRevocationEvidence",
    "A3CloseoutEvidenceBundle", "A3CloseoutReadinessReceipt",
    "create_a3_closeout_policy", "create_a3_closeout_trigger",
    "create_a3_process_termination_evidence", "create_a3_project_side_deletion_evidence",
    "create_a3_instance_release_evidence", "create_a3_access_revocation_evidence",
    "create_a3_closeout_evidence_bundle", "evaluate_a3_closeout_readiness",
    "validate_a3_closeout_readiness_against", "validate_a3_closeout_bundle_bytes",
    "A3_EVIDENCE_CATEGORIES", "A3_CLEANUP_MODE", "A3_REMOTE_DEADLINE_MINUTES",
    "A3_LOCAL_ARCHIVE_RETENTION_DAYS",
]
