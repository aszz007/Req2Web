"""Real-A1 executor core candidate. Importing this module never executes A1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
from types import MappingProxyType
from typing import Mapping

from .autodl_a1_linux_worker import validate_preparation_roots
from .autodl_a1_operational import (
    A1ModelInventory,
    A1OperationalControls,
    A1OperationalPlan,
    RealA1OperationalError,
)

PHASE_EVENT_SCHEMA = "req2web.runtime.real_a1_phase_event.v1"
PRODUCTION_OBSERVATION_SCHEMA = "req2web.runtime.real_a1_production_observation.v1"
EXECUTION_RECEIPT_SCHEMA = "req2web.runtime.real_a1_execution_receipt.v1"
EVIDENCE_COMMIT_SCHEMA = "req2web.runtime.real_a1_evidence_commit.v1"
CHANNEL_SCHEMA = "req2web.runtime.real_a1_probe_channel.v1"
WORKER_AUTHORITY = "req2web.runtime.autodl_a1_probe_worker.v1"
WORKER_VERSION = "1"
FIXED_PROMPT = "REQ2WEB_A1_SENTINEL_7f9c. Return exactly: A1 operational probe acknowledged."
EXPECTED_SUFFIX = "A1 operational probe acknowledged."
PHASE_EVENT_FILENAME = "phase_events.json"
OBSERVATION_FILENAME = "production_observation.json"
RECEIPT_FILENAME = "execution_receipt.json"
EVIDENCE_COMMIT_FILENAME = "evidence_commit.json"
_PAYLOAD_ARTIFACT_FILES = (PHASE_EVENT_FILENAME, OBSERVATION_FILENAME, RECEIPT_FILENAME)
_ALLOWED_ARTIFACT_FILES = (*_PAYLOAD_ARTIFACT_FILES, EVIDENCE_COMMIT_FILENAME)
_CHANNEL_READ_CHUNK_BYTES = 4096
_MAX_CHANNEL_EVENT_LINE_BYTES = 65536
_MAX_CHANNEL_BUFFER_BYTES = 131072

_PHASE_KEYS = ("schema_version", "event_id", "plan_id", "control_id", "sequence", "phase", "transition", "parent_elapsed_ms", "channel_sha256", "channel_length", "status", "failure_code")
_OBSERVATION_KEYS = ("schema_version", "observation_id", "plan_id", "plan_sha256", "control_id", "control_sha256", "package_manifest_sha256", "model_inventory_sha256", "action_time_git_sha", "worker_authority", "worker_version", "execution_started_utc", "execution_completed_utc", "root_invariants", "phase_events", "phase_inventory_sha256", "runtime", "gpu", "resources", "model", "processor", "probe", "network", "root_contract", "decision", "failure_codes")
_ROOT_INVARIANT_KEYS = ("role", "normalized_path_sha256", "marker_id", "marker_sha256", "expected_state")
_RECEIPT_KEYS = ("schema_version", "receipt_id", "plan_id", "plan_sha256", "control_id", "control_sha256", "observation_id", "observation_sha256", "status", "next_state", "failure_codes", "a2_unlocked", "provider_invoked", "project_data_transferred", "compatibility_run_occurred", "h1_allowed", "formal_quality_allowed", "manager_a1_passed")
_COMMIT_KEYS = ("schema_version", "commit_id", "plan_id", "plan_sha256", "control_id", "control_sha256", "observation_id", "observation_sha256", "receipt_id", "receipt_sha256", "action_time_git_sha", "worker_authority", "worker_version", "files", "tree_sha256")
_COMMIT_ROW_KEYS = ("relative_path", "bytes", "sha256")
_CHANNEL_KEYS = ("schema_version", "sequence", "phase", "transition", "payload")
_PHASE_ORDER = (("setup", "started"), ("setup", "completed"), ("load", "started"), ("load", "completed"), ("probe", "started"), ("probe", "completed"))
_FAILURE_CODES = (
    "channel_invalid", "phase_sequence_invalid", "setup_timeout", "load_timeout", "probe_timeout",
    "child_failed", "child_exit_invalid", "production_payload_invalid", "runtime_invalid", "gpu_invalid", "resource_invalid",
    "device_invalid", "model_invalid", "processor_invalid", "probe_invalid", "network_invalid",
    "netns_invalid", "listener_invalid", "dns_invalid", "egress_invalid", "load_oom",
)


class RealA1ExecutorError(ValueError):
    pass


@dataclass(frozen=True)
class RealA1ExecutionResult:
    observation: object | None
    receipt: object | None
    return_code: int
    cleanup_required: bool
    evidence_staging_root: str | None


def _build_executor_authorities():
    phase_event_schema = PHASE_EVENT_SCHEMA
    production_observation_schema = PRODUCTION_OBSERVATION_SCHEMA
    execution_receipt_schema = EXECUTION_RECEIPT_SCHEMA
    evidence_commit_schema = EVIDENCE_COMMIT_SCHEMA
    channel_schema = CHANNEL_SCHEMA
    worker_authority = WORKER_AUTHORITY
    worker_version = WORKER_VERSION
    fixed_prompt = FIXED_PROMPT
    expected_suffix = EXPECTED_SUFFIX
    dumps = json.dumps
    loads = json.loads
    sha256_ctor = hashlib.sha256
    mapping_type = Mapping
    error_type = RealA1ExecutorError
    plan_type = A1OperationalPlan
    plan_from_dict = A1OperationalPlan.from_dict
    controls_type = A1OperationalControls
    controls_from_dict = A1OperationalControls.from_dict
    inventory_from_dict = A1ModelInventory.from_dict
    production_inventory = A1ModelInventory.production()
    phase_keys = tuple(_PHASE_KEYS)
    observation_keys = tuple(_OBSERVATION_KEYS)
    receipt_keys = tuple(_RECEIPT_KEYS)
    commit_keys = tuple(_COMMIT_KEYS)
    commit_row_keys = tuple(_COMMIT_ROW_KEYS)
    payload_artifact_files = tuple(_PAYLOAD_ARTIFACT_FILES)
    channel_keys = tuple(_CHANNEL_KEYS)
    phase_order = tuple(_PHASE_ORDER)
    failure_codes = tuple(_FAILURE_CODES) + ("authorization_window_invalid",)
    root_invariant_keys = tuple(_ROOT_INVARIANT_KEYS)
    datetime_type = datetime
    utc_timezone = timezone.utc
    utc_format = "%Y-%m-%dT%H:%M:%SZ"

    def utc_now_real():
        return datetime_type.now(utc_timezone).strftime(utc_format)

    def parse_utc(value):
        return datetime_type.strptime(value, utc_format).replace(tzinfo=utc_timezone)
    fixed_prompt_hash = sha256_ctor(fixed_prompt.encode("utf-8")).hexdigest()
    expected_suffix_hash = sha256_ctor(expected_suffix.encode("utf-8")).hexdigest()

    def canon(value):
        return dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def digest(value):
        return sha256_ctor(value).hexdigest()

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
        if type(raw) is not bytes or raw.startswith(b"\xef\xbb\xbf"):
            raise error_type("canonical_bytes_invalid")
        try:
            value = loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)
        except Exception as exc:
            raise error_type("json_invalid") from exc
        if canon(value) != raw:
            raise error_type("noncanonical_json")
        return value

    def exact_map(value, keys, code):
        if not isinstance(value, mapping_type) or set(value) != set(keys):
            raise error_type(code)
        return value

    def check_hex(value, code):
        if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise error_type(code)
        return value

    def parse_plan(value):
        if type(value) is not plan_type:
            raise error_type("executor_plan_type_invalid")
        parsed = plan_from_dict(value.data)
        if parsed.data["model_inventory"]["inventory_mode"] != "production_pinned":
            raise error_type("executor_inventory_mode_invalid")
        return parsed

    def parse_controls(value):
        if type(value) is not controls_type:
            raise error_type("executor_controls_type_invalid")
        return controls_from_dict(value.data)

    def validate_phase_data(value):
        data = dict(exact_map(value, phase_keys, "phase_event_exact_keys_invalid"))
        if data["schema_version"] != phase_event_schema or type(data["sequence"]) is not int or data["sequence"] < 1 or data["phase"] not in ("setup", "load", "probe") or data["transition"] not in ("started", "completed", "failed") or type(data["parent_elapsed_ms"]) is not int or data["parent_elapsed_ms"] < 0 or type(data["channel_length"]) is not int or data["channel_length"] < 1 or data["status"] not in ("observed", "failed"):
            raise error_type("phase_event_boundary_invalid")
        check_hex(data["channel_sha256"], "phase_event_channel_hash_invalid")
        if data["status"] == "observed" and data["failure_code"] is not None:
            raise error_type("phase_event_status_invalid")
        if data["status"] == "failed" and data["failure_code"] not in failure_codes:
            raise error_type("phase_event_status_invalid")
        root = {key: data[key] for key in phase_keys if key != "event_id"}
        if data["event_id"] != artifact_id("real-a1-phase-event-", root):
            raise error_type("phase_event_identity_invalid")
        return data

    @dataclass(frozen=True)
    class A1ProductionPhaseEvent:
        data: Mapping[str, object]

        @classmethod
        def from_dict(cls, value):
            return cls(validate_phase_data(value))

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_phase_data(parse_json(raw)))

        def validate(self):
            validate_phase_data(self.data)

        def to_dict(self):
            return dict(validate_phase_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_phase_data(self.data)))

        def sha256(self):
            return digest(canon(dict(validate_phase_data(self.data))))

    phase_from_dict = A1ProductionPhaseEvent.from_dict

    def failure_measurements(plan):
        inventory = inventory_from_dict(plan.data["model_inventory"]) if plan is not None else production_inventory
        return {
            "runtime": {"python": "not_observed", "torch": "not_observed", "transformers": "not_observed", "cuda": "not_observed"},
            "gpu": {"index": -1, "uuid": "not_observed", "name": "not_observed", "vram_mib": 0, "driver": "not_observed", "cuda_device_count": 0, "model_device": "not_observed", "bf16": False},
            "resources": {"ram_gib": 0, "free_disk_gib": 0, "cuda_peak_allocated_bytes": 0, "cuda_peak_reserved_bytes": 0, "host_rss_bytes": 0, "host_peak_rss_bytes": 0, "setup_ms": 0, "load_ms": 0, "probe_ms": 0},
            "model": {"repository": inventory.data["repository"], "revision": inventory.data["revision"], "inventory_sha256": inventory.sha256(), "inventory_tree_sha256": inventory.data["tree_sha256"], "quantization": "none", "cpu_offload": False, "device_map": "none", "load_status": "not_observed"},
            "processor": {"keys": [], "multimedia_keys": [], "text_only": False},
            "probe": {"prompt_sha256": fixed_prompt_hash, "prompt_length": len(fixed_prompt), "expected_suffix_sha256": expected_suffix_hash, "expected_suffix_length": len(expected_suffix), "output_sha256": "0" * 64, "output_length": 0, "generated_token_count": 0, "suffix_match": False, "prompt_sentinel_absent_from_output": False},
            "network": {"parent_netns_id": "not_observed", "child_netns_id": "not_observed", "netns_distinct": False, "dns_status": "not_observed", "egress_status": "not_observed", "listener_count": -1, "http_endpoint_observed": True},
        }

    def validate_success_payload(payload, plan=None):
        expected_keys = ("runtime", "gpu", "resources", "model", "processor", "probe", "network")
        data = dict(exact_map(payload, expected_keys, "production_payload_keys_invalid"))
        runtime = dict(exact_map(data["runtime"], ("python", "torch", "transformers", "cuda"), "runtime_keys_invalid"))
        if runtime != {"python": "3.11", "torch": "2.7.1+cu128", "transformers": "5.14.1", "cuda": "12.8"}:
            raise error_type("runtime_invalid")
        gpu = dict(exact_map(data["gpu"], ("index", "uuid", "name", "vram_mib", "driver", "cuda_device_count", "model_device", "bf16"), "gpu_keys_invalid"))
        if gpu["index"] != 0 or type(gpu["uuid"]) is not str or not gpu["uuid"] or gpu["name"] != "NVIDIA GeForce RTX 5090" or type(gpu["vram_mib"]) is not int or gpu["vram_mib"] < 30000 or type(gpu["driver"]) is not str or not gpu["driver"] or gpu["cuda_device_count"] != 1 or gpu["bf16"] is not True:
            raise error_type("gpu_invalid")
        if gpu["model_device"] != "cuda:0":
            raise error_type("device_invalid")
        resources = dict(exact_map(data["resources"], ("ram_gib", "free_disk_gib", "cuda_peak_allocated_bytes", "cuda_peak_reserved_bytes", "host_rss_bytes", "host_peak_rss_bytes", "setup_ms", "load_ms", "probe_ms"), "resource_keys_invalid"))
        if any(type(resources[key]) is not int or resources[key] < 0 for key in resources) or resources["ram_gib"] < 60 or resources["free_disk_gib"] < 80:
            raise error_type("resource_invalid")
        inventory = inventory_from_dict(plan.data["model_inventory"]) if plan is not None else production_inventory
        model = dict(exact_map(data["model"], ("repository", "revision", "inventory_sha256", "inventory_tree_sha256", "quantization", "cpu_offload", "device_map", "load_status"), "model_keys_invalid"))
        if model != {"repository": inventory.data["repository"], "revision": inventory.data["revision"], "inventory_sha256": inventory.sha256(), "inventory_tree_sha256": inventory.data["tree_sha256"], "quantization": "none", "cpu_offload": False, "device_map": "none", "load_status": "loaded"}:
            raise error_type("model_invalid")
        processor = dict(exact_map(data["processor"], ("keys", "multimedia_keys", "text_only"), "processor_keys_invalid"))
        if processor != {"keys": ["attention_mask", "input_ids"], "multimedia_keys": [], "text_only": True}:
            raise error_type("processor_invalid")
        probe = dict(exact_map(data["probe"], ("prompt_sha256", "prompt_length", "expected_suffix_sha256", "expected_suffix_length", "output_sha256", "output_length", "generated_token_count", "suffix_match", "prompt_sentinel_absent_from_output"), "probe_keys_invalid"))
        if probe["prompt_sha256"] != fixed_prompt_hash or probe["prompt_length"] != len(fixed_prompt) or probe["expected_suffix_sha256"] != expected_suffix_hash or probe["expected_suffix_length"] != len(expected_suffix) or probe["output_sha256"] != expected_suffix_hash or probe["output_length"] != len(expected_suffix) or type(probe["generated_token_count"]) is not int or not 1 <= probe["generated_token_count"] <= 8 or probe["suffix_match"] is not True or probe["prompt_sentinel_absent_from_output"] is not True:
            raise error_type("probe_invalid")
        network = dict(exact_map(data["network"], ("parent_netns_id", "child_netns_id", "netns_distinct", "dns_status", "egress_status", "listener_count", "http_endpoint_observed"), "network_keys_invalid"))
        if type(network["parent_netns_id"]) is not str or type(network["child_netns_id"]) is not str or not network["parent_netns_id"] or not network["child_netns_id"] or network["parent_netns_id"] == network["child_netns_id"] or network["netns_distinct"] is not True:
            raise error_type("netns_invalid")
        if network["dns_status"] != "blocked":
            raise error_type("dns_invalid")
        if network["egress_status"] != "blocked":
            raise error_type("egress_invalid")
        if network["listener_count"] != 0 or network["http_endpoint_observed"] is not False:
            raise error_type("listener_invalid")
        return data

    def validate_observation_data(value):
        data = dict(exact_map(value, observation_keys, "production_observation_exact_keys_invalid"))
        if data["schema_version"] != production_observation_schema or data["worker_authority"] != worker_authority or data["worker_version"] != worker_version or data["decision"] not in ("pass", "fail") or type(data["failure_codes"]) is not list or any(code not in failure_codes for code in data["failure_codes"]):
            raise error_type("production_observation_boundary_invalid")
        for key in ("plan_sha256", "control_sha256", "package_manifest_sha256", "model_inventory_sha256", "phase_inventory_sha256"):
            check_hex(data[key], "production_observation_hash_invalid")
        if type(data["action_time_git_sha"]) is not str or len(data["action_time_git_sha"]) != 40:
            raise error_type("production_observation_action_sha_invalid")
        try:
            started = parse_utc(data["execution_started_utc"])
            completed = parse_utc(data["execution_completed_utc"])
        except Exception as exc:
            raise error_type("production_observation_time_invalid") from exc
        if started > completed:
            raise error_type("production_observation_time_invalid")
        invariants = data["root_invariants"]
        if type(invariants) is not list or [item.get("role") for item in invariants if isinstance(item, mapping_type)] != ["control", "package", "model", "evidence"]:
            raise error_type("production_observation_root_invariants_invalid")
        for item in invariants:
            row = dict(exact_map(item, root_invariant_keys, "production_observation_root_invariant_invalid"))
            check_hex(row["normalized_path_sha256"], "production_observation_root_invariant_invalid")
            check_hex(row["marker_sha256"], "production_observation_root_invariant_invalid")
            if type(row["marker_id"]) is not str or type(row["expected_state"]) is not str:
                raise error_type("production_observation_root_invariant_invalid")
        events = data["phase_events"]
        if type(events) is not list:
            raise error_type("phase_event_inventory_invalid")
        parsed_events = [phase_from_dict(event) for event in events]
        if data["phase_inventory_sha256"] != digest(canon([event.to_dict() for event in parsed_events])):
            raise error_type("phase_event_inventory_invalid")
        if len(parsed_events) > len(phase_order):
            raise error_type("phase_event_inventory_invalid")
        previous_elapsed = -1
        saw_failed_transition = False
        for index, event in enumerate(parsed_events, 1):
            event_data = event.to_dict()
            expected_phase, expected_transition = phase_order[index - 1]
            if event_data["plan_id"] != data["plan_id"] or event_data["control_id"] != data["control_id"] or event_data["sequence"] != index or event_data["parent_elapsed_ms"] < previous_elapsed:
                raise error_type("phase_event_inventory_invalid")
            previous_elapsed = event_data["parent_elapsed_ms"]
            actual = (event_data["phase"], event_data["transition"])
            if event_data["transition"] == "failed":
                if saw_failed_transition or index != len(parsed_events) or event_data["phase"] != expected_phase or event_data["status"] != "failed":
                    raise error_type("phase_event_inventory_invalid")
                saw_failed_transition = True
            elif actual != (expected_phase, expected_transition) or event_data["status"] != "observed":
                raise error_type("phase_event_inventory_invalid")
        if data["decision"] == "pass" and (len(parsed_events) != len(phase_order) or saw_failed_transition):
            raise error_type("phase_event_inventory_invalid")
        root_contract = dict(exact_map(data["root_contract"], ("control_marker_sha256", "package_marker_sha256", "model_marker_sha256", "evidence_marker_sha256", "argv_sha256", "environment_sha256", "project_payload_contract"), "root_contract_invalid"))
        for key in root_contract:
            if key.endswith("sha256"):
                check_hex(root_contract[key], "root_contract_hash_invalid")
        if root_contract["project_payload_contract"] != "fixed_roots_argv_env_prompt_only_not_global_host_claim":
            raise error_type("root_contract_invalid")
        expected_failure = data["decision"] == "fail"
        if expected_failure != bool(data["failure_codes"]):
            raise error_type("production_observation_decision_invalid")
        measurement_keys = ("runtime", "gpu", "resources", "model", "processor", "probe", "network")
        if data["decision"] == "pass":
            validate_success_payload({key: data[key] for key in measurement_keys})
        elif {key: data[key] for key in measurement_keys} != failure_measurements(None):
            raise error_type("production_failure_measurements_invalid")
        root = {key: data[key] for key in observation_keys if key != "observation_id"}
        if data["observation_id"] != artifact_id("real-a1-production-observation-", root):
            raise error_type("production_observation_identity_invalid")
        return data

    @dataclass(frozen=True)
    class A1ProductionObservation:
        data: Mapping[str, object]

        @classmethod
        def from_dict(cls, value):
            return cls(validate_observation_data(value))

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_observation_data(parse_json(raw)))

        def validate(self):
            validate_observation_data(self.data)

        def to_dict(self):
            return dict(validate_observation_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_observation_data(self.data)))

        def sha256(self):
            return digest(canon(dict(validate_observation_data(self.data))))

        def validate_against(self, plan, controls):
            parsed_plan = parse_plan(plan)
            parsed_controls = parse_controls(controls)
            validated = validate_observation_data(self.data)
            if validated["decision"] == "pass":
                validate_success_payload({key: validated[key] for key in ("runtime", "gpu", "resources", "model", "processor", "probe", "network")}, parsed_plan)
            if validated["plan_id"] != parsed_plan.plan_id or validated["plan_sha256"] != parsed_plan.sha256() or validated["control_id"] != parsed_controls.data["control_id"] or validated["control_sha256"] != parsed_controls.sha256() or validated["package_manifest_sha256"] != parsed_plan.data["package_manifest_sha256"] or validated["model_inventory_sha256"] != parsed_plan.data["model_inventory_sha256"] or validated["action_time_git_sha"] != parsed_plan.data["action_time_git_sha"]:
                raise error_type("production_observation_binding_invalid")
            if validated["decision"] == "pass":
                disposition = parsed_plan.data["no_action_plan"]["approved_disposition"]
                approval = parse_utc(disposition["approval_timestamp_utc"])
                expiry = parse_utc(disposition["authorization_expiry_utc"])
                started = parse_utc(validated["execution_started_utc"])
                completed = parse_utc(validated["execution_completed_utc"])
                if not approval <= started <= completed <= expiry:
                    raise error_type("production_observation_authorization_window_invalid")

    observation_type = A1ProductionObservation
    observation_from_dict = A1ProductionObservation.from_dict

    def parse_observation(value):
        if type(value) is not observation_type:
            raise error_type("production_observation_type_invalid")
        return observation_from_dict(value.data)

    def validate_receipt_data(value):
        data = dict(exact_map(value, receipt_keys, "execution_receipt_exact_keys_invalid"))
        if data["schema_version"] != execution_receipt_schema or data["status"] not in ("a1_passed_awaiting_independent_gate", "a1_failed_cleanup_required") or data["next_state"] not in ("awaiting_independent_a1_gate_validation", "a3_cleanup_release_revoke_required") or type(data["failure_codes"]) is not list or any(code not in failure_codes for code in data["failure_codes"]):
            raise error_type("execution_receipt_boundary_invalid")
        false_keys = ("a2_unlocked", "provider_invoked", "project_data_transferred", "compatibility_run_occurred", "h1_allowed", "formal_quality_allowed", "manager_a1_passed")
        if any(data[key] is not False for key in false_keys):
            raise error_type("execution_receipt_unlock_invalid")
        passed = data["status"] == "a1_passed_awaiting_independent_gate"
        if passed != (data["next_state"] == "awaiting_independent_a1_gate_validation") or passed == bool(data["failure_codes"]):
            raise error_type("execution_receipt_status_invalid")
        for key in ("plan_sha256", "control_sha256", "observation_sha256"):
            check_hex(data[key], "execution_receipt_hash_invalid")
        root = {key: data[key] for key in receipt_keys if key != "receipt_id"}
        if data["receipt_id"] != artifact_id("real-a1-execution-receipt-", root):
            raise error_type("execution_receipt_identity_invalid")
        return data

    @dataclass(frozen=True)
    class A1ExecutionReceipt:
        data: Mapping[str, object]

        @classmethod
        def from_dict(cls, value):
            return cls(validate_receipt_data(value))

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
            return digest(canon(dict(validate_receipt_data(self.data))))

        def validate_against(self, plan, controls, observation):
            parsed_plan = parse_plan(plan)
            parsed_controls = parse_controls(controls)
            parsed_observation = parse_observation(observation)
            parsed_observation.validate_against(parsed_plan, parsed_controls)
            expected = build_receipt(parsed_plan, parsed_controls, parsed_observation)
            if validate_receipt_data(self.data) != expected:
                raise error_type("execution_receipt_replay_invalid")

    receipt_type = A1ExecutionReceipt
    receipt_from_dict = A1ExecutionReceipt.from_dict

    def parse_receipt(value):
        if type(value) is not receipt_type:
            raise error_type("execution_receipt_type_invalid")
        return receipt_from_dict(value.data)

    def validate_commit_data(value):
        data = dict(exact_map(value, commit_keys, "evidence_commit_exact_keys_invalid"))
        if data["schema_version"] != evidence_commit_schema or data["worker_authority"] != worker_authority or data["worker_version"] != worker_version:
            raise error_type("evidence_commit_boundary_invalid")
        for key in ("plan_sha256", "control_sha256", "observation_sha256", "receipt_sha256", "tree_sha256"):
            check_hex(data[key], "evidence_commit_hash_invalid")
        if type(data["action_time_git_sha"]) is not str or len(data["action_time_git_sha"]) != 40 or any(char not in "0123456789abcdef" for char in data["action_time_git_sha"]):
            raise error_type("evidence_commit_action_sha_invalid")
        rows = data["files"]
        if type(rows) is not list or len(rows) != len(payload_artifact_files):
            raise error_type("evidence_commit_files_invalid")
        normalized = []
        for index, row in enumerate(rows):
            row_data = dict(exact_map(row, commit_row_keys, "evidence_commit_row_invalid"))
            if row_data["relative_path"] != payload_artifact_files[index] or type(row_data["bytes"]) is not int or type(row_data["bytes"]) is bool or row_data["bytes"] < 1:
                raise error_type("evidence_commit_row_invalid")
            check_hex(row_data["sha256"], "evidence_commit_row_hash_invalid")
            normalized.append(row_data)
        if data["tree_sha256"] != digest(canon(normalized)):
            raise error_type("evidence_commit_tree_invalid")
        for key, prefix in (("plan_id", "real-a1-plan-"), ("control_id", "real-a1-control-"), ("observation_id", "real-a1-production-observation-"), ("receipt_id", "real-a1-execution-receipt-")):
            if type(data[key]) is not str or not data[key].startswith(prefix):
                raise error_type("evidence_commit_binding_invalid")
        root = {key: data[key] for key in commit_keys if key != "commit_id"}
        if data["commit_id"] != artifact_id("real-a1-evidence-commit-", root):
            raise error_type("evidence_commit_identity_invalid")
        return data

    def build_commit_marker(observation, receipt, payload_bytes):
        parsed_observation = parse_observation(observation)
        parsed_receipt = parse_receipt(receipt)
        payloads = dict(exact_map(payload_bytes, payload_artifact_files, "evidence_commit_payload_inventory_invalid"))
        phase_bytes = payloads[payload_artifact_files[0]]
        observation_bytes = payloads[payload_artifact_files[1]]
        receipt_bytes = payloads[payload_artifact_files[2]]
        if observation_bytes != parsed_observation.canonical_bytes() or receipt_bytes != parsed_receipt.canonical_bytes() or phase_bytes != canon(parsed_observation.data["phase_events"]):
            raise error_type("evidence_commit_payload_replay_invalid")
        if parsed_receipt.data["plan_id"] != parsed_observation.data["plan_id"] or parsed_receipt.data["plan_sha256"] != parsed_observation.data["plan_sha256"] or parsed_receipt.data["control_id"] != parsed_observation.data["control_id"] or parsed_receipt.data["control_sha256"] != parsed_observation.data["control_sha256"] or parsed_receipt.data["observation_id"] != parsed_observation.data["observation_id"] or parsed_receipt.data["observation_sha256"] != parsed_observation.sha256():
            raise error_type("evidence_commit_nested_binding_invalid")
        rows = [{"relative_path": name, "bytes": len(payloads[name]), "sha256": digest(payloads[name])} for name in payload_artifact_files]
        root = {"schema_version": evidence_commit_schema, "plan_id": parsed_observation.data["plan_id"], "plan_sha256": parsed_observation.data["plan_sha256"], "control_id": parsed_observation.data["control_id"], "control_sha256": parsed_observation.data["control_sha256"], "observation_id": parsed_observation.data["observation_id"], "observation_sha256": parsed_observation.sha256(), "receipt_id": parsed_receipt.data["receipt_id"], "receipt_sha256": parsed_receipt.sha256(), "action_time_git_sha": parsed_observation.data["action_time_git_sha"], "worker_authority": worker_authority, "worker_version": worker_version, "files": rows, "tree_sha256": digest(canon(rows))}
        root["commit_id"] = artifact_id("real-a1-evidence-commit-", root)
        return validate_commit_data(root)

    @dataclass(frozen=True)
    class A1EvidenceCommitMarker:
        data: Mapping[str, object]

        @classmethod
        def from_dict(cls, value):
            return cls(validate_commit_data(value))

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_commit_data(parse_json(raw)))

        def validate(self):
            validate_commit_data(self.data)

        def to_dict(self):
            return dict(validate_commit_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_commit_data(self.data)))

        def sha256(self):
            return digest(canon(dict(validate_commit_data(self.data))))

        def validate_against(self, observation, receipt, phase_events_bytes, observation_bytes, receipt_bytes):
            parsed_observation = parse_observation(observation)
            parsed_receipt = parse_receipt(receipt)
            expected = build_commit_marker(parsed_observation, parsed_receipt, {payload_artifact_files[0]: phase_events_bytes, payload_artifact_files[1]: observation_bytes, payload_artifact_files[2]: receipt_bytes})
            if validate_commit_data(self.data) != expected:
                raise error_type("evidence_commit_replay_invalid")

    def build_observation(plan, controls, events, measurements, decision, failure_list, root_contract):
        parsed_plan = parse_plan(plan)
        parsed_controls = parse_controls(controls)
        parsed_events = [phase_from_dict(event.to_dict() if isinstance(event, A1ProductionPhaseEvent) else event) for event in events]
        completed_utc = root_contract.completed_utc or root_contract.utc_now()
        root = {"schema_version": production_observation_schema, "plan_id": parsed_plan.plan_id, "plan_sha256": parsed_plan.sha256(), "control_id": parsed_controls.data["control_id"], "control_sha256": parsed_controls.sha256(), "package_manifest_sha256": parsed_plan.data["package_manifest_sha256"], "model_inventory_sha256": parsed_plan.data["model_inventory_sha256"], "action_time_git_sha": parsed_plan.data["action_time_git_sha"], "worker_authority": worker_authority, "worker_version": worker_version, "execution_started_utc": root_contract.started_utc, "execution_completed_utc": completed_utc, "root_invariants": root_contract.root_invariants, "phase_events": [event.to_dict() for event in parsed_events], "phase_inventory_sha256": digest(canon([event.to_dict() for event in parsed_events])), **measurements, "root_contract": root_contract, "decision": decision, "failure_codes": list(failure_list)}
        root["observation_id"] = artifact_id("real-a1-production-observation-", root)
        return validate_observation_data(root)

    def build_receipt(plan, controls, observation):
        parsed_plan = parse_plan(plan)
        parsed_controls = parse_controls(controls)
        parsed_observation = parse_observation(observation)
        parsed_observation.validate_against(parsed_plan, parsed_controls)
        passed = parsed_observation.data["decision"] == "pass"
        root = {"schema_version": execution_receipt_schema, "plan_id": parsed_plan.plan_id, "plan_sha256": parsed_plan.sha256(), "control_id": parsed_controls.data["control_id"], "control_sha256": parsed_controls.sha256(), "observation_id": parsed_observation.data["observation_id"], "observation_sha256": parsed_observation.sha256(), "status": "a1_passed_awaiting_independent_gate" if passed else "a1_failed_cleanup_required", "next_state": "awaiting_independent_a1_gate_validation" if passed else "a3_cleanup_release_revoke_required", "failure_codes": list(parsed_observation.data["failure_codes"]), "a2_unlocked": False, "provider_invoked": False, "project_data_transferred": False, "compatibility_run_occurred": False, "h1_allowed": False, "formal_quality_allowed": False, "manager_a1_passed": False}
        root["receipt_id"] = artifact_id("real-a1-execution-receipt-", root)
        return validate_receipt_data(root)

    def make_phase_event(plan, controls, sequence, phase, transition, elapsed_ms, raw, status="observed", failure_code=None):
        root = {"schema_version": phase_event_schema, "plan_id": plan.plan_id, "control_id": controls.data["control_id"], "sequence": sequence, "phase": phase, "transition": transition, "parent_elapsed_ms": elapsed_ms, "channel_sha256": digest(raw), "channel_length": len(raw), "status": status, "failure_code": failure_code}
        root["event_id"] = artifact_id("real-a1-phase-event-", root)
        return A1ProductionPhaseEvent(validate_phase_data(root))

    def parse_channel(raw):
        data = dict(exact_map(parse_json(raw), channel_keys, "channel_exact_keys_invalid"))
        if data["schema_version"] != channel_schema or type(data["sequence"]) is not int or data["sequence"] < 1 or data["phase"] not in ("setup", "load", "probe") or data["transition"] not in ("started", "completed", "failed") or type(data["payload"]) is not dict:
            raise error_type("channel_invalid")
        return data

    class RootContractState(dict):
        pass

    def root_contract(controls, argv, environment, root_bindings=None, _utc_now=utc_now_real):
        markers = {item["role"]: item for item in controls.data["root_markers"]}
        if root_bindings is None:
            invariants = [{"role": role, "normalized_path_sha256": digest(("synthetic-test-root:" + role).encode("utf-8")), "marker_id": markers[role]["marker_id"], "marker_sha256": markers[role]["marker_sha256"], "expected_state": markers[role]["expected_state"]} for role in ("control", "package", "model", "evidence")]
        else:
            invariants = [{key: root_bindings[role][key] for key in root_invariant_keys} for role in ("control", "package", "model", "evidence")]
        result = RootContractState({"control_marker_sha256": markers["control"]["marker_sha256"], "package_marker_sha256": markers["package"]["marker_sha256"], "model_marker_sha256": markers["model"]["marker_sha256"], "evidence_marker_sha256": markers["evidence"]["marker_sha256"], "argv_sha256": digest(canon(argv)), "environment_sha256": digest(canon(environment)), "project_payload_contract": "fixed_roots_argv_env_prompt_only_not_global_host_claim"})
        result.started_utc = _utc_now()
        result.completed_utc = None
        result.utc_now = _utc_now
        result.root_invariants = invariants
        return result


    def success_payload_for_tests(plan, parent_netns="net:[100]", child_netns="net:[200]"):
        parsed_plan = parse_plan(plan)
        inventory = inventory_from_dict(parsed_plan.data["model_inventory"])
        return {"runtime": {"python": "3.11", "torch": "2.7.1+cu128", "transformers": "5.14.1", "cuda": "12.8"}, "gpu": {"index": 0, "uuid": "GPU-test-uuid", "name": "NVIDIA GeForce RTX 5090", "vram_mib": 32768, "driver": "test-driver", "cuda_device_count": 1, "model_device": "cuda:0", "bf16": True}, "resources": {"ram_gib": 64, "free_disk_gib": 96, "cuda_peak_allocated_bytes": 1024, "cuda_peak_reserved_bytes": 2048, "host_rss_bytes": 4096, "host_peak_rss_bytes": 8192, "setup_ms": 10, "load_ms": 20, "probe_ms": 5}, "model": {"repository": inventory.data["repository"], "revision": inventory.data["revision"], "inventory_sha256": inventory.sha256(), "inventory_tree_sha256": inventory.data["tree_sha256"], "quantization": "none", "cpu_offload": False, "device_map": "none", "load_status": "loaded"}, "processor": {"keys": ["attention_mask", "input_ids"], "multimedia_keys": [], "text_only": True}, "probe": {"prompt_sha256": fixed_prompt_hash, "prompt_length": len(fixed_prompt), "expected_suffix_sha256": expected_suffix_hash, "expected_suffix_length": len(expected_suffix), "output_sha256": expected_suffix_hash, "output_length": len(expected_suffix), "generated_token_count": 4, "suffix_match": True, "prompt_sentinel_absent_from_output": True}, "network": {"parent_netns_id": parent_netns, "child_netns_id": child_netns, "netns_distinct": parent_netns != child_netns, "dns_status": "blocked", "egress_status": "blocked", "listener_count": 0, "http_endpoint_observed": False}}

    def failure_observation(plan, controls, events, code, contract):
        measurements = failure_measurements(plan)
        return A1ProductionObservation(build_observation(plan, controls, events, measurements, "fail", [code], contract))

    def cancel_adapter(adapter, grace_ms):
        adapter.terminate()
        if not adapter.wait(grace_ms):
            adapter.kill()
            adapter.wait(grace_ms)

    def monitor_adapter(plan, controls, adapter, contract):
        parsed_plan = parse_plan(plan)
        parsed_controls = parse_controls(controls)
        events = []
        disposition = parsed_plan.data["no_action_plan"]["approved_disposition"]
        approval = parse_utc(disposition["approval_timestamp_utc"])
        expiry = parse_utc(disposition["authorization_expiry_utc"])
        started = parse_utc(contract.started_utc)
        grace = parsed_plan.data["limits"]["cancel_grace_seconds"] * 1000
        if not approval <= started <= expiry:
            cancel_adapter(adapter, grace)
            observation = failure_observation(parsed_plan, parsed_controls, events, "authorization_window_invalid", contract)
            receipt = A1ExecutionReceipt(build_receipt(parsed_plan, parsed_controls, observation))
            return observation, receipt
        phase_started = {"setup": 0, "load": None, "probe": None}
        limits = {"setup": parsed_plan.data["limits"]["setup_seconds"] * 1000, "load": parsed_plan.data["limits"]["load_seconds"] * 1000, "probe": parsed_plan.data["limits"]["probe_seconds"] * 1000}
        final_payload = None
        for index, expected in enumerate(phase_order, 1):
            phase, transition = expected
            if transition == "started" and phase != "setup":
                phase_started[phase] = adapter.elapsed_ms()
            start = phase_started[phase] or 0
            remaining = max(0, limits[phase] - (adapter.elapsed_ms() - start))
            try:
                raw = adapter.read_event(remaining)
            except error_type:
                cancel_adapter(adapter, grace)
                observation = failure_observation(parsed_plan, parsed_controls, events, "channel_invalid", contract)
                receipt = A1ExecutionReceipt(build_receipt(parsed_plan, parsed_controls, observation))
                return observation, receipt
            if raw is None:
                code = phase + "_timeout"
                cancel_adapter(adapter, grace)
                observation = failure_observation(parsed_plan, parsed_controls, events, code, contract)
                receipt = A1ExecutionReceipt(build_receipt(parsed_plan, parsed_controls, observation))
                return observation, receipt
            try:
                channel = parse_channel(raw)
            except error_type:
                cancel_adapter(adapter, grace)
                observation = failure_observation(parsed_plan, parsed_controls, events, "channel_invalid", contract)
                receipt = A1ExecutionReceipt(build_receipt(parsed_plan, parsed_controls, observation))
                return observation, receipt
            actual = (channel["phase"], channel["transition"])
            if channel["sequence"] != index or (actual != expected and actual != (phase, "failed")):
                cancel_adapter(adapter, grace)
                observation = failure_observation(parsed_plan, parsed_controls, events, "phase_sequence_invalid", contract)
                receipt = A1ExecutionReceipt(build_receipt(parsed_plan, parsed_controls, observation))
                return observation, receipt
            if channel["transition"] == "failed":
                failure_code = channel["payload"].get("failure_code", "child_failed")
                if failure_code not in failure_codes:
                    failure_code = "child_failed"
                events.append(make_phase_event(parsed_plan, parsed_controls, index, phase, "failed", adapter.elapsed_ms(), raw, "failed", failure_code))
                cancel_adapter(adapter, grace)
                observation = failure_observation(parsed_plan, parsed_controls, events, failure_code, contract)
                receipt = A1ExecutionReceipt(build_receipt(parsed_plan, parsed_controls, observation))
                return observation, receipt
            events.append(make_phase_event(parsed_plan, parsed_controls, index, phase, transition, adapter.elapsed_ms(), raw))
            if actual == ("probe", "completed"):
                final_payload = channel["payload"]
        if not adapter.wait(grace):
            cancel_adapter(adapter, grace)
            observation = failure_observation(parsed_plan, parsed_controls, events, "child_exit_invalid", contract)
            receipt = A1ExecutionReceipt(build_receipt(parsed_plan, parsed_controls, observation))
            return observation, receipt
        if adapter.return_code() != 0:
            observation = failure_observation(parsed_plan, parsed_controls, events, "child_exit_invalid", contract)
            receipt = A1ExecutionReceipt(build_receipt(parsed_plan, parsed_controls, observation))
            return observation, receipt
        if not adapter.channel_clean():
            observation = failure_observation(parsed_plan, parsed_controls, events, "channel_invalid", contract)
            receipt = A1ExecutionReceipt(build_receipt(parsed_plan, parsed_controls, observation))
            return observation, receipt
        contract.completed_utc = contract.utc_now()
        completed = parse_utc(contract.completed_utc)
        if not approval <= started <= completed <= expiry:
            observation = failure_observation(parsed_plan, parsed_controls, events, "authorization_window_invalid", contract)
            receipt = A1ExecutionReceipt(build_receipt(parsed_plan, parsed_controls, observation))
            return observation, receipt
        try:
            measurements = validate_success_payload(final_payload, parsed_plan)
            observation = A1ProductionObservation(build_observation(parsed_plan, parsed_controls, events, measurements, "pass", [], contract))
        except error_type as exc:
            code = str(exc) if str(exc) in failure_codes else "production_payload_invalid"
            observation = failure_observation(parsed_plan, parsed_controls, events, code, contract)
        receipt = A1ExecutionReceipt(build_receipt(parsed_plan, parsed_controls, observation))
        return observation, receipt

    class ScriptedAdapter:
        def __init__(self, script, survive_terminate=False):
            self.script = [dict(item) for item in script]
            self.index = 0
            self.now = 0
            self.terminated = 0
            self.killed = 0
            self.survive_terminate = survive_terminate

        def elapsed_ms(self):
            return self.now

        def read_event(self, timeout_ms):
            if self.index >= len(self.script):
                self.now += timeout_ms
                return None
            item = self.script[self.index]
            delay = item["delay_ms"]
            if delay > timeout_ms:
                self.now += timeout_ms
                item["delay_ms"] = delay - timeout_ms
                return None
            self.now += delay
            self.index += 1
            return item["raw"]

        def terminate(self):
            self.terminated += 1

        def wait(self, _grace_ms):
            return not self.survive_terminate or self.killed > 0

        def kill(self):
            self.killed += 1

        def return_code(self):
            return 0 if self.index >= len(self.script) else None

        def channel_clean(self):
            return True

    def scripted_run_for_tests(plan, controls, script, survive_terminate=False, utc_values=None, root_bindings=None):
        adapter = ScriptedAdapter(script, survive_terminate)
        values = iter(utc_values or ("2026-07-25T00:00:00Z", "2026-07-25T00:00:01Z", "2026-07-25T00:00:02Z"))
        last = [None]
        def test_utc_now():
            try:
                last[0] = next(values)
            except StopIteration:
                pass
            return last[0]
        contract = root_contract(controls, ["private-scripted-test"], {"mode": "private-scripted-test"}, root_bindings, _utc_now=test_utc_now)
        observation, receipt = monitor_adapter(plan, controls, adapter, contract)
        return observation, receipt, {"terminate": adapter.terminated, "kill": adapter.killed, "elapsed_ms": adapter.elapsed_ms()}

    def channel_bytes_for_tests(sequence, phase, transition, payload=None):
        return canon({"schema_version": channel_schema, "sequence": sequence, "phase": phase, "transition": transition, "payload": payload or {}})

    return {"A1ProductionPhaseEvent": A1ProductionPhaseEvent, "A1ProductionObservation": A1ProductionObservation, "A1ExecutionReceipt": A1ExecutionReceipt, "A1EvidenceCommitMarker": A1EvidenceCommitMarker, "validate_phase_data": validate_phase_data, "validate_observation_data": validate_observation_data, "validate_receipt_data": validate_receipt_data, "validate_commit_data": validate_commit_data, "build_commit_marker": build_commit_marker, "build_observation": build_observation, "build_receipt": build_receipt, "failure_observation": failure_observation, "monitor_adapter": monitor_adapter, "scripted_run_for_tests": scripted_run_for_tests, "channel_bytes_for_tests": channel_bytes_for_tests, "success_payload_for_tests": success_payload_for_tests, "canon": canon, "digest": digest, "parse_json": parse_json, "root_contract": root_contract, "parse_plan": parse_plan, "parse_controls": parse_controls}


_EXECUTOR_AUTHORITIES = _build_executor_authorities()
A1ProductionPhaseEvent = _EXECUTOR_AUTHORITIES["A1ProductionPhaseEvent"]
A1ProductionObservation = _EXECUTOR_AUTHORITIES["A1ProductionObservation"]
A1ExecutionReceipt = _EXECUTOR_AUTHORITIES["A1ExecutionReceipt"]
A1EvidenceCommitMarker = _EXECUTOR_AUTHORITIES["A1EvidenceCommitMarker"]
_validate_phase_event_data = _EXECUTOR_AUTHORITIES["validate_phase_data"]
_validate_production_observation_data = _EXECUTOR_AUTHORITIES["validate_observation_data"]
_validate_execution_receipt_data = _EXECUTOR_AUTHORITIES["validate_receipt_data"]
_validate_evidence_commit_data = _EXECUTOR_AUTHORITIES["validate_commit_data"]
_build_evidence_commit_marker = _EXECUTOR_AUTHORITIES["build_commit_marker"]
_run_scripted_executor_for_tests = _EXECUTOR_AUTHORITIES["scripted_run_for_tests"]
_channel_bytes_for_tests = _EXECUTOR_AUTHORITIES["channel_bytes_for_tests"]
_success_payload_for_tests = _EXECUTOR_AUTHORITIES["success_payload_for_tests"]
_captured_canon = _EXECUTOR_AUTHORITIES["canon"]
_captured_sha = _EXECUTOR_AUTHORITIES["digest"]
_captured_json = _EXECUTOR_AUTHORITIES["parse_json"]


class _PopenPhaseAdapter:
    _monotonic = staticmethod(time.monotonic)
    _selector_type = selectors.DefaultSelector
    _event_read = selectors.EVENT_READ
    _timeout_error = subprocess.TimeoutExpired
    _read = staticmethod(os.read)
    _set_blocking = staticmethod(os.set_blocking)
    _killpg = staticmethod(getattr(os, "killpg", None))
    _sigterm = signal.SIGTERM
    _sigkill = getattr(signal, "SIGKILL", 9)
    _error_type = RealA1ExecutorError
    _read_chunk_bytes = _CHANNEL_READ_CHUNK_BYTES
    _max_event_line_bytes = _MAX_CHANNEL_EVENT_LINE_BYTES
    _max_buffer_bytes = _MAX_CHANNEL_BUFFER_BYTES

    def __init__(self, process):
        self.process = process
        self.started = self._monotonic()
        self.selector = self._selector_type()
        self.fd = process.stdout.fileno()
        self._set_blocking(self.fd, False)
        self.selector.register(self.fd, self._event_read)
        self._buffer = bytearray()
        self._channel_invalid = False
        self._eof = False

    def elapsed_ms(self):
        return int((self._monotonic() - self.started) * 1000)

    def _fail_channel(self):
        self._channel_invalid = True
        raise self._error_type("channel_invalid")

    def _append_chunk(self, chunk):
        if len(self._buffer) + len(chunk) > self._max_buffer_bytes:
            self._fail_channel()
        self._buffer.extend(chunk)
        newline = self._buffer.find(b"\n")
        if newline < 0 and len(self._buffer) > self._max_event_line_bytes:
            self._fail_channel()
        if newline > self._max_event_line_bytes:
            self._fail_channel()

    def _take_complete_line(self):
        newline = self._buffer.find(b"\n")
        if newline < 0:
            return None
        if newline == 0 or newline > self._max_event_line_bytes:
            self._fail_channel()
        line = bytes(self._buffer[:newline])
        del self._buffer[: newline + 1]
        try:
            line.decode("utf-8")
        except UnicodeDecodeError:
            self._fail_channel()
        return line

    def read_event(self, timeout_ms):
        line = self._take_complete_line()
        if line is not None:
            return line
        deadline = self._monotonic() + max(0, timeout_ms) / 1000
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return None
            ready = self.selector.select(remaining)
            if not ready:
                if self._monotonic() >= deadline:
                    return None
                continue
            try:
                chunk = self._read(self.fd, self._read_chunk_bytes)
            except BlockingIOError:
                continue
            except OSError:
                self._fail_channel()
            if not chunk:
                self._eof = True
                self._fail_channel()
            self._append_chunk(chunk)
            line = self._take_complete_line()
            if line is not None:
                return line

    def _drain_after_exit(self):
        while True:
            try:
                chunk = self._read(self.fd, self._read_chunk_bytes)
            except BlockingIOError:
                break
            except OSError:
                self._channel_invalid = True
                break
            if not chunk:
                self._eof = True
                break
            try:
                self._append_chunk(chunk)
            except self._error_type:
                break
        if self._buffer:
            self._channel_invalid = True

    def terminate(self):
        if self._killpg is None:
            self.process.terminate()
        else:
            self._killpg(self.process.pid, self._sigterm)

    def wait(self, grace_ms):
        try:
            self.process.wait(timeout=grace_ms / 1000)
        except self._timeout_error:
            return False
        self._drain_after_exit()
        return True

    def kill(self):
        if self._killpg is None:
            self.process.kill()
        else:
            self._killpg(self.process.pid, self._sigkill)

    def return_code(self):
        return self.process.poll()

    def channel_clean(self):
        return not self._channel_invalid and not self._buffer


class _PrivateIncrementalSelector:
    def __init__(self, chunks, silent_after_chunks):
        self.chunks = chunks
        self.silent_after_chunks = silent_after_chunks

    def select(self, timeout):
        if self.chunks:
            return ((None, None),)
        if timeout > 0:
            time.sleep(timeout)
        return () if self.silent_after_chunks else ((None, None),)


class _PrivateIncrementalProcess:
    def __init__(self):
        self.pid = 0

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


def _incremental_adapter_for_tests(chunks, silent_after_chunks=False):
    scripted_chunks = list(chunks)
    adapter = _PopenPhaseAdapter.__new__(_PopenPhaseAdapter)
    adapter.process = _PrivateIncrementalProcess()
    adapter.started = time.monotonic()
    adapter.selector = _PrivateIncrementalSelector(scripted_chunks, silent_after_chunks)
    adapter.fd = -1
    adapter._buffer = bytearray()
    adapter._channel_invalid = False
    adapter._eof = False

    def scripted_read(_fd, _size):
        if scripted_chunks:
            return scripted_chunks.pop(0)
        if silent_after_chunks:
            raise BlockingIOError
        return b""

    adapter._read = scripted_read
    adapter._killpg = None
    return adapter


def _safe_empty_directory(
    path,
    _path_type=Path,
    _is_symlink_mode=stat.S_ISLNK,
    _is_directory_mode=stat.S_ISDIR,
    _mkdir=os.mkdir,
    _platform=sys.platform,
    _error_type=RealA1ExecutorError,
):
    root = _path_type(path)
    if root.exists():
        item_stat = root.lstat()
        if _is_symlink_mode(item_stat.st_mode) or not _is_directory_mode(item_stat.st_mode) or any(root.iterdir()):
            raise _error_type("executor_evidence_role_not_empty")
        return root.resolve(strict=True)
    parent = root.parent.resolve(strict=True)
    if parent.is_symlink() or not parent.is_dir():
        raise _error_type("executor_evidence_parent_invalid")
    _mkdir(root, 0o700 if _platform == "linux" else 0o777)
    return root.resolve(strict=True)


def _prepare_evidence_staging_root(
    evidence_root,
    _safe_empty=_safe_empty_directory,
    _mkdir=os.mkdir,
    _platform=sys.platform,
    _error_type=RealA1ExecutorError,
):
    role_root = _safe_empty(evidence_root)
    staging = role_root / "real-a1-executor-v1"
    if staging.exists() or staging.is_symlink():
        raise _error_type("executor_evidence_staging_exists")
    _mkdir(staging, 0o700 if _platform == "linux" else 0o777)
    if any(staging.iterdir()):
        raise _error_type("executor_evidence_staging_not_empty")
    return staging


def _write_exclusive(
    path,
    content,
    _open=os.open,
    _fdopen=os.fdopen,
    _fsync=os.fsync,
    _close=os.close,
    _write_only=os.O_WRONLY,
    _create=os.O_CREAT,
    _exclusive=os.O_EXCL,
    _no_follow=getattr(os, "O_NOFOLLOW", 0),
):
    flags = _write_only | _create | _exclusive | _no_follow
    descriptor = _open(path, flags, 0o600)
    try:
        with _fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            _fsync(stream.fileno())
    finally:
        _close(descriptor)


def _write_executor_artifacts(
    staging,
    observation,
    receipt,
    _path_type=Path,
    _observation_type=A1ProductionObservation,
    _receipt_type=A1ExecutionReceipt,
    _commit_type=A1EvidenceCommitMarker,
    _build_commit=_build_evidence_commit_marker,
    _canon=_captured_canon,
    _phase_filename=PHASE_EVENT_FILENAME,
    _observation_filename=OBSERVATION_FILENAME,
    _receipt_filename=RECEIPT_FILENAME,
    _commit_filename=EVIDENCE_COMMIT_FILENAME,
    _payload_files=_PAYLOAD_ARTIFACT_FILES,
    _allowed_files=_ALLOWED_ARTIFACT_FILES,
    _write=_write_exclusive,
    _error_type=RealA1ExecutorError,
):
    staging = _path_type(staging)
    existing = {item.name for item in staging.iterdir()}
    if existing:
        raise _error_type("executor_evidence_extra_or_existing")
    parsed_observation = _observation_type.from_dict(observation.data)
    parsed_receipt = _receipt_type.from_dict(receipt.data)
    events = parsed_observation.data["phase_events"]
    payloads = {
        _phase_filename: _canon(events),
        _observation_filename: parsed_observation.canonical_bytes(),
        _receipt_filename: parsed_receipt.canonical_bytes(),
    }
    if tuple(payloads) != _payload_files:
        raise _error_type("executor_payload_artifact_inventory_invalid")
    commit_marker = _commit_type(_build_commit(parsed_observation, parsed_receipt, payloads))
    published = {**payloads, _commit_filename: commit_marker.canonical_bytes()}
    if tuple(published) != _allowed_files:
        raise _error_type("executor_artifact_inventory_invalid")
    for name, content in published.items():
        _write(staging / name, content)


def _publish_executor_result(
    staging,
    observation,
    receipt,
    _write_artifacts=_write_executor_artifacts,
    _result_type=RealA1ExecutionResult,
    _error_type=Exception,
    _stringify=str,
):
    try:
        _write_artifacts(staging, observation, receipt)
    except _error_type:
        return _result_type(None, None, 4, True, _stringify(staging))
    decision = observation.data["decision"]
    return _result_type(observation, receipt, 0 if decision == "pass" else 5, decision != "pass", _stringify(staging))


def _build_production_runner():
    platform = sys.platform
    executable = sys.executable
    operational_validate = validate_preparation_roots
    monitor = _EXECUTOR_AUTHORITIES["monitor_adapter"]
    root_contract_fn = _EXECUTOR_AUTHORITIES["root_contract"]
    failure_observation_fn = _EXECUTOR_AUTHORITIES["failure_observation"]
    build_receipt_fn = _EXECUTOR_AUTHORITIES["build_receipt"]
    receipt_type = _EXECUTOR_AUTHORITIES["A1ExecutionReceipt"]
    executor_error = RealA1ExecutorError
    operational_error = RealA1OperationalError
    os_error = OSError
    exception_type = Exception
    stringify = str
    result_type = RealA1ExecutionResult
    prepare_staging = _prepare_evidence_staging_root
    publish_result = _publish_executor_result
    adapter_type = _PopenPhaseAdapter
    popen = subprocess.Popen
    devnull = subprocess.DEVNULL
    pipe = subprocess.PIPE
    readlink = os.readlink
    unshare = "/usr/bin/unshare"
    worker_module = "req2web_runtime.autodl_a1_probe_worker"
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "TOKENIZERS_PARALLELISM": "false", "CUDA_VISIBLE_DEVICES": "0", "PYTHONNOUSERSITE": "1"}

    def execute_real_a1(plan_path, controls_path, package_root, model_root, evidence_root, execute_real_a1):
        if execute_real_a1 is not True:
            raise executor_error("explicit_execute_real_a1_required")
        if platform != "linux":
            raise executor_error("real_a1_executor_linux_required")
        try:
            preparation = operational_validate(plan_path, controls_path, package_root, model_root, evidence_root)
        except (operational_error, os_error):
            return result_type(None, None, 2, True, None)
        try:
            staging = prepare_staging(evidence_root)
        except (executor_error, os_error):
            return result_type(None, None, 3, True, None)
        adapter = None
        parent_netns = "not_observed"
        command = [unshare, "--net", "--fork", "--mount-proc", executable, "-m", worker_module, "--plan", stringify(plan_path), "--controls", stringify(controls_path), "--package-root", stringify(package_root), "--model-root", stringify(model_root), "--parent-netns-id", parent_netns]
        contract = None
        try:
            parent_netns = readlink("/proc/self/ns/net")
            command[-1] = parent_netns
            contract = root_contract_fn(preparation.controls, command, environment, preparation.root_bindings)
            process = popen(command, stdin=devnull, stdout=pipe, stderr=devnull, cwd="/", env=environment, shell=False, start_new_session=True)
            adapter = adapter_type(process)
            observation, receipt = monitor(preparation.plan, preparation.controls, adapter, contract)
        except exception_type:
            if contract is None:
                contract = root_contract_fn(preparation.controls, command, environment, preparation.root_bindings)
            if adapter is not None:
                adapter.terminate()
                if not adapter.wait(30000):
                    adapter.kill()
                    adapter.wait(30000)
            observation = failure_observation_fn(preparation.plan, preparation.controls, [], "child_failed", contract)
            receipt = receipt_type(build_receipt_fn(preparation.plan, preparation.controls, observation))
        return publish_result(staging, observation, receipt)

    return execute_real_a1


execute_real_a1 = _build_production_runner()
