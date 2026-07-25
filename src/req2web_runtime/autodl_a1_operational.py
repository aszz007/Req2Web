"""Local-only Real-A1 operational contracts with no execution side effects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from types import MappingProxyType
from typing import Mapping

from .autodl_a1_preflight import (
    AutoDLA1DeploymentPackageManifest,
    AutoDLA1PreflightError,
    AutoDLA1PreflightPlan,
)

PLAN_SCHEMA = "req2web.runtime.real_a1_plan.v1"
CONTROL_SCHEMA = "req2web.runtime.real_a1_controls.v1"
MODEL_INVENTORY_SCHEMA = "req2web.runtime.real_a1_model_inventory.v1"
ROOT_MARKER_SCHEMA = "req2web.runtime.real_a1_root_marker.v1"
OBS_SCHEMA = "req2web.runtime.real_a1_observation.v1"
RECEIPT_SCHEMA = "req2web.runtime.real_a1_receipt.v1"
GATE_SCHEMA = "req2web.runtime.real_a1_gate.v1"
INTENT_SCHEMA = "req2web.runtime.real_a1_closeout_intent.v1"
CLOSEOUT_SCHEMA = "req2web.runtime.real_a1_closeout_receipt.v1"
MODEL_REPOSITORY = "Qwen/Qwen3.5-9B"
MODEL_REVISION = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
MODEL_BYTES = 19329393661
MODEL_FILE_COUNT = 16
BENIGN_PROMPT = "Return exactly: A1 operational probe acknowledged."

_OFFICIAL_MODEL_ROWS = (
    (".gitattributes", 1570, "34448b82c17d60fec9b65b1f093c115ddbaadc04beb1b0140b6bfed2e012a930"),
    ("LICENSE", 11544, "bbedc3fda3305820b977265f01b8619d87570a6739de3a5582c3464840f1e57a"),
    ("README.md", 77643, "c5f5a8c2dddab69cfbf05279235aa5fddb137939a06539c4c7637aa900fef6d0"),
    ("chat_template.jinja", 7756, "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715"),
    ("config.json", 3126, "d0883072e01861ed0b2d47be3c16c36a8e81c224c7ffaa310c6558fb3f932b05"),
    ("merges.txt", 3353259, "a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d"),
    ("model.safetensors-00001-of-00004.safetensors", 5276436216, "db6f444b43d318c92f360a13a25561a6a65b10c0631b8ed305a426dbaa6c380e"),
    ("model.safetensors-00002-of-00004.safetensors", 5335161512, "31c7d7e2dd5d207840b31cc59083c8f4c4718959149e0358c0364052bb9a0330"),
    ("model.safetensors-00003-of-00004.safetensors", 5368717440, "7ec36ba3a4176a44c3c0876ad80c56a2f70c84bf008d82e9501df642f17dadec"),
    ("model.safetensors-00004-of-00004.safetensors", 3325995712, "b62b0c4cd7e44edee103ee8f4fe225f246d5e768e07bfd5f25b63a8aa1fdd0c6"),
    ("model.safetensors.index.json", 79657, "26d3539b516be613f39563617cb9d33b3f83d401298125be392c80cefb8f7fe5"),
    ("preprocessor_config.json", 390, "27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516"),
    ("tokenizer.json", 12807982, "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42"),
    ("tokenizer_config.json", 16710, "316230d6a809701f4db5ea8f8fc862bc3a6f3229c937c174e674ff3ca0a64ac8"),
    ("video_preprocessor_config.json", 385, "7768af27c1fafa9cc9011c1dc20067e03f8915e03b63504550e11d5066986d13"),
    ("vocab.json", 6722759, "ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003"),
)

_PLAN_KEYS = ("schema_version", "plan_id", "no_action_plan_id", "no_action_plan_sha256", "no_action_plan", "action_time_git_sha", "package_manifest_sha256", "model_inventory", "model_inventory_sha256", "attempt_index", "provider", "gpu", "runtime", "model", "decode", "limits", "network", "a2_auto_unlock_allowed", "h1_allowed", "formal_quality_allowed")
_CONTROL_KEYS = ("schema_version", "control_id", "plan_id", "observed_at_utc", "provider", "instance_id_hash", "price_cny_per_hour", "ssh_host_fingerprint_sha256", "evidence_hashes", "review_state", "root_markers")
_MODEL_INVENTORY_KEYS = ("schema_version", "inventory_id", "inventory_mode", "repository", "revision", "file_count", "total_bytes", "files", "tree_sha256")
_MODEL_ROW_KEYS = ("relative_path", "bytes", "sha256")
_ROOT_MARKER_KEYS = ("schema_version", "marker_id", "marker_sha256", "role", "plan_id", "expected_state")
_OBSERVATION_KEYS = ("schema_version", "observation_id", "plan_id", "source", "authority_id", "observed_at_utc", "hardware", "runtime", "model", "load", "network", "probe", "timing", "decision", "failure_codes")
_RECEIPT_KEYS = ("schema_version", "receipt_id", "plan_id", "plan_sha256", "control_id", "control_sha256", "observation", "status", "next_state", "a2_unlocked", "provider_invoked", "project_data_transferred", "compatibility_run_occurred", "h1_allowed", "formal_quality_allowed")
_GATE_KEYS = ("schema_version", "gate_id", "plan_id", "receipt_id", "status", "manager_consumable", "failure_codes")
_INTENT_KEYS = ("schema_version", "intent_id", "plan_id", "receipt_id", "project_side_deletion_required", "process_termination_required", "instance_release_required", "credential_revocation_required", "physical_erasure_claimed")
_CLOSEOUT_KEYS = ("schema_version", "closeout_id", "intent_id", "status", "project_side_deleted", "process_terminated", "instance_release_evidence", "credential_revocation_evidence", "physical_erasure_claimed")
_KEYS = {"plan": _PLAN_KEYS, "control": _CONTROL_KEYS, "model_inventory": _MODEL_INVENTORY_KEYS, "model_row": _MODEL_ROW_KEYS, "root_marker": _ROOT_MARKER_KEYS, "obs": _OBSERVATION_KEYS, "receipt": _RECEIPT_KEYS, "gate": _GATE_KEYS, "intent": _INTENT_KEYS, "closeout": _CLOSEOUT_KEYS}


class RealA1OperationalError(ValueError):
    pass


def _build_operational_authorities():
    model_repository = MODEL_REPOSITORY
    model_revision = MODEL_REVISION
    model_bytes = MODEL_BYTES
    model_file_count = MODEL_FILE_COUNT
    benign_prompt = BENIGN_PROMPT
    dumps = json.dumps
    loads = json.loads
    sha256_ctor = hashlib.sha256
    mapping_type = Mapping
    path_type = Path
    pure_path_type = PurePosixPath
    parse_time = datetime.strptime
    utc = timezone.utc
    hex40 = re.compile(r"[0-9a-f]{40}")
    hex64 = re.compile(r"[0-9a-f]{64}")
    time_pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
    error_type = RealA1OperationalError
    preflight_error_type = AutoDLA1PreflightError
    base_plan_type = AutoDLA1PreflightPlan
    base_plan_from_dict = AutoDLA1PreflightPlan.from_dict
    package_manifest_type = AutoDLA1DeploymentPackageManifest
    package_manifest_from_dict = AutoDLA1DeploymentPackageManifest.from_dict
    package_validate_root = AutoDLA1DeploymentPackageManifest.validate_against_root
    executable = sys.executable
    worker_module = "req2web_runtime.autodl_a1_linux_worker"
    schemas = MappingProxyType({"plan": PLAN_SCHEMA, "control": CONTROL_SCHEMA, "model_inventory": MODEL_INVENTORY_SCHEMA, "root_marker": ROOT_MARKER_SCHEMA, "obs": OBS_SCHEMA, "receipt": RECEIPT_SCHEMA, "gate": GATE_SCHEMA, "intent": INTENT_SCHEMA, "closeout": CLOSEOUT_SCHEMA})
    keys = MappingProxyType({name: tuple(value) for name, value in _KEYS.items()})
    official_rows = tuple((path, size, digest) for path, size, digest in _OFFICIAL_MODEL_ROWS)
    root_states = MappingProxyType({"control": "existing_exact_control_inputs", "package": "existing_exact_package_manifest", "model": "existing_exact_model_inventory", "evidence": "dedicated_new_or_empty_no_write"})
    root_roles = ("control", "package", "model", "evidence")
    allowlist = ("pypi.org:443", "files.pythonhosted.org:443", "download.pytorch.org:443", "huggingface.co:443", "cdn-lfs.huggingface.co:443", "cas-bridge.xethub.hf.co:443", "transfer.xethub.hf.co:443")

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
        if type(raw) is not bytes:
            raise error_type("bytes_required")
        if raw.startswith(b"\xef\xbb\xbf"):
            raise error_type("utf8_bom_rejected")
        try:
            value = loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)
        except Exception as exc:
            raise error_type("json_invalid") from exc
        if canon(value) != raw:
            raise error_type("noncanonical_json")
        return value

    def exact_map(value, expected_keys, code):
        if not isinstance(value, mapping_type) or set(value) != set(expected_keys):
            raise error_type(code)
        return value

    def check_hex(value, code, length=64):
        pattern = hex40 if length == 40 else hex64 if length == 64 else None
        if type(value) is not str or pattern is None or pattern.fullmatch(value) is None:
            raise error_type(code)
        return value

    def check_time(value, code):
        if type(value) is not str or time_pattern.fullmatch(value) is None:
            raise error_type(code)
        try:
            parse_time(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=utc)
        except ValueError as exc:
            raise error_type(code) from exc
        return value

    def payload_free(value):
        prohibited = ("requirement", "agentcontext", "retrieval", "pagespec", "h1", "gold", "rico", "reference", "payload", "image", "video")
        if any(token in str(value).lower() for token in prohibited):
            raise error_type("project_payload_carrier_rejected")
        return value

    def parse_base_plan(value):
        if type(value) is not base_plan_type:
            raise error_type("no_action_plan_type_invalid")
        try:
            return base_plan_from_dict(value.data)
        except preflight_error_type as exc:
            raise error_type("no_action_plan_invalid") from exc

    def parse_base_plan_dict(value):
        try:
            return base_plan_from_dict(value)
        except preflight_error_type as exc:
            raise error_type("plan_no_action_binding_invalid") from exc

    def valid_relative_path(value, code):
        if type(value) is not str or not value or "\\" in value:
            raise error_type(code)
        pure = pure_path_type(value)
        if pure.is_absolute() or pure.as_posix() != value or any(part in ("", ".", "..") for part in pure.parts):
            raise error_type(code)
        return value

    def hash_file(path):
        hasher = sha256_ctor()
        with path.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                hasher.update(block)
        return hasher.hexdigest()

    def scan_exact_regular_files(root, code):
        root_path = path_type(root)
        try:
            root_stat = root_path.lstat()
        except OSError as exc:
            raise error_type(code) from exc
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            raise error_type(code)
        resolved = root_path.resolve(strict=True)
        actual = {}
        pending = [resolved]
        while pending:
            current = pending.pop()
            for item in current.iterdir():
                item_stat = item.lstat()
                if stat.S_ISLNK(item_stat.st_mode):
                    raise error_type(code)
                if stat.S_ISDIR(item_stat.st_mode):
                    pending.append(item)
                elif stat.S_ISREG(item_stat.st_mode):
                    actual[item.relative_to(resolved).as_posix()] = item
                else:
                    raise error_type(code)
        return resolved, actual

    def validate_inventory_data(value, allow_synthetic=False):
        data = dict(exact_map(value, keys["model_inventory"], "model_inventory_exact_keys_invalid"))
        rows = data["files"]
        if type(rows) is not list or not rows:
            raise error_type("model_inventory_files_invalid")
        normalized = []
        paths = []
        for row in rows:
            row_data = dict(exact_map(row, keys["model_row"], "model_inventory_row_invalid"))
            valid_relative_path(row_data["relative_path"], "model_inventory_path_invalid")
            if type(row_data["bytes"]) is not int or type(row_data["bytes"]) is bool or row_data["bytes"] < 0:
                raise error_type("model_inventory_bytes_invalid")
            check_hex(row_data["sha256"], "model_inventory_hash_invalid")
            normalized.append(row_data)
            paths.append(row_data["relative_path"])
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise error_type("model_inventory_files_invalid")
        if data["schema_version"] != schemas["model_inventory"] or type(data["file_count"]) is not int or data["file_count"] != len(normalized) or type(data["total_bytes"]) is not int or data["total_bytes"] != sum(row["bytes"] for row in normalized):
            raise error_type("model_inventory_summary_invalid")
        check_hex(data["revision"], "model_inventory_revision_invalid", 40)
        expected_tree = digest(canon(normalized))
        if data["tree_sha256"] != expected_tree:
            raise error_type("model_inventory_tree_invalid")
        mode = data["inventory_mode"]
        if mode == "production_pinned":
            expected_rows = [{"relative_path": path, "bytes": size, "sha256": row_digest} for path, size, row_digest in official_rows]
            if data["repository"] != model_repository or data["revision"] != model_revision or data["file_count"] != model_file_count or data["total_bytes"] != model_bytes or normalized != expected_rows:
                raise error_type("model_inventory_production_contract_invalid")
        elif mode == "synthetic_test" and allow_synthetic:
            if type(data["repository"]) is not str or not data["repository"].startswith("synthetic/"):
                raise error_type("model_inventory_synthetic_contract_invalid")
        else:
            raise error_type("model_inventory_mode_invalid")
        root = {key: data[key] for key in keys["model_inventory"] if key != "inventory_id"}
        if data["inventory_id"] != artifact_id("real-a1-model-inventory-", root):
            raise error_type("model_inventory_identity_invalid")
        return data

    def build_synthetic_inventory_for_tests(rows):
        normalized = [dict(exact_map(row, keys["model_row"], "model_inventory_row_invalid")) for row in rows]
        root = {"schema_version": schemas["model_inventory"], "inventory_mode": "synthetic_test", "repository": "synthetic/private-test-only", "revision": "0" * 40, "file_count": len(normalized), "total_bytes": sum(row["bytes"] for row in normalized), "files": normalized, "tree_sha256": digest(canon(normalized))}
        root["inventory_id"] = artifact_id("real-a1-model-inventory-", root)
        return validate_inventory_data(root, allow_synthetic=True)

    @dataclass(frozen=True)
    class A1ModelInventory:
        data: Mapping[str, object]

        @classmethod
        def production(cls):
            rows = [{"relative_path": path, "bytes": size, "sha256": row_digest} for path, size, row_digest in official_rows]
            root = {"schema_version": schemas["model_inventory"], "inventory_mode": "production_pinned", "repository": model_repository, "revision": model_revision, "file_count": model_file_count, "total_bytes": model_bytes, "files": rows, "tree_sha256": digest(canon(rows))}
            root["inventory_id"] = artifact_id("real-a1-model-inventory-", root)
            return cls(validate_inventory_data(root))

        @classmethod
        def from_dict(cls, value):
            return cls(validate_inventory_data(value))

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_inventory_data(parse_json(raw)))

        def validate(self):
            validate_inventory_data(self.data)

        def to_dict(self):
            return dict(validate_inventory_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_inventory_data(self.data)))

        def sha256(self):
            return digest(canon(dict(validate_inventory_data(self.data))))

        def validate_against_root(self, root):
            data = validate_inventory_data(self.data)
            _, actual = scan_exact_regular_files(root, "model_inventory_root_invalid")
            expected = {row["relative_path"]: row for row in data["files"]}
            if set(actual) != set(expected):
                raise error_type("model_inventory_regular_file_set_invalid")
            for relative_path, row in expected.items():
                item = actual[relative_path]
                if item.stat().st_size != row["bytes"] or hash_file(item) != row["sha256"]:
                    raise error_type("model_inventory_identity_drift")

    inventory_type = A1ModelInventory
    inventory_from_dict = A1ModelInventory.from_dict

    def parse_inventory(value):
        if type(value) is not inventory_type:
            raise error_type("model_inventory_type_invalid")
        return inventory_from_dict(value.data)

    def plan_fixed(inventory):
        return {
            "gpu": {"count": 1, "name": "NVIDIA GeForce RTX 5090", "min_vram_mib": 30000, "min_ram_gib": 60, "min_free_disk_gib": 80},
            "runtime": {"python": "3.11", "torch": "2.7.1+cu128", "transformers": "5.14.1", "cuda": "12.8", "precision": "bf16", "quantization": "none", "cpu_offload": False},
            "model": {"repository": inventory.data["repository"], "revision": inventory.data["revision"], "file_count": inventory.data["file_count"], "total_bytes": inventory.data["total_bytes"], "inventory_tree_sha256": inventory.data["tree_sha256"], "max_download_bytes": 22548578304, "max_managed_bytes": 65 * 1024**3},
            "decode": {"text_only": True, "thinking": False, "do_sample": False, "seed": 20260724, "max_total_tokens": 16384, "probe_max_new_tokens": 8, "parallel_generation": 1},
            "limits": {"setup_seconds": 2700, "load_seconds": 720, "probe_seconds": 120, "cancel_grace_seconds": 30, "max_price_cny_per_hour": 2.88},
            "network": {"acquisition_allowlist": list(allowlist), "offline_allowlist": []},
        }

    def validate_plan_data(value):
        data = dict(exact_map(value, keys["plan"], "plan_exact_keys_invalid"))
        base = parse_base_plan_dict(data["no_action_plan"])
        inventory = inventory_from_dict(data["model_inventory"])
        if inventory.data["inventory_mode"] != "production_pinned":
            raise error_type("plan_model_inventory_mode_invalid")
        fixed = {"schema_version": schemas["plan"], "attempt_index": 1, "provider": "AutoDL", **plan_fixed(inventory), "a2_auto_unlock_allowed": False, "h1_allowed": False, "formal_quality_allowed": False}
        if any(data[key] != expected or type(data[key]) is not type(expected) for key, expected in fixed.items()):
            raise error_type("plan_fixed_boundary_invalid")
        disposition = base.data["approved_disposition"]
        if data["no_action_plan_id"] != base.plan_id or data["no_action_plan_sha256"] != base.sha256() or data["action_time_git_sha"] != disposition["action_time_git_sha"] or data["package_manifest_sha256"] != disposition["deployment_package_manifest_sha256"] or data["model_inventory_sha256"] != inventory.sha256():
            raise error_type("plan_nested_binding_invalid")
        check_hex(data["no_action_plan_sha256"], "plan_no_action_hash_invalid")
        check_hex(data["package_manifest_sha256"], "plan_package_hash_invalid")
        check_hex(data["model_inventory_sha256"], "plan_model_inventory_hash_invalid")
        check_hex(data["action_time_git_sha"], "plan_action_sha_invalid", 40)
        root = {key: data[key] for key in keys["plan"] if key != "plan_id"}
        if data["plan_id"] != artifact_id("real-a1-plan-", root):
            raise error_type("plan_identity_invalid")
        return data

    @dataclass(frozen=True)
    class A1OperationalPlan:
        data: Mapping[str, object]

        @classmethod
        def create(cls, no_action_plan, model_inventory):
            base = parse_base_plan(no_action_plan)
            inventory = parse_inventory(model_inventory)
            if inventory.data["inventory_mode"] != "production_pinned":
                raise error_type("plan_model_inventory_mode_invalid")
            disposition = base.data["approved_disposition"]
            manifest = base.data["deployment_package_manifest"]
            root = {"schema_version": schemas["plan"], "no_action_plan_id": base.plan_id, "no_action_plan_sha256": base.sha256(), "no_action_plan": base.to_dict(), "action_time_git_sha": disposition["action_time_git_sha"], "package_manifest_sha256": disposition["deployment_package_manifest_sha256"], "model_inventory": inventory.to_dict(), "model_inventory_sha256": inventory.sha256(), "attempt_index": 1, "provider": "AutoDL", **plan_fixed(inventory), "a2_auto_unlock_allowed": False, "h1_allowed": False, "formal_quality_allowed": False}
            if manifest["source_commit_sha"] != root["action_time_git_sha"]:
                raise error_type("action_time_package_binding_invalid")
            root["plan_id"] = artifact_id("real-a1-plan-", root)
            return cls(validate_plan_data(root))

        @classmethod
        def from_dict(cls, value):
            return cls(validate_plan_data(value))

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_plan_data(parse_json(raw)))

        def validate(self):
            validate_plan_data(self.data)

        @property
        def plan_id(self):
            return validate_plan_data(self.data)["plan_id"]

        def to_dict(self):
            return dict(validate_plan_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_plan_data(self.data)))

        def sha256(self):
            return digest(canon(dict(validate_plan_data(self.data))))

    plan_type = A1OperationalPlan
    plan_from_dict = A1OperationalPlan.from_dict

    def parse_plan(value):
        if type(value) is not plan_type:
            raise error_type("plan_type_invalid")
        return plan_from_dict(value.data)

    def validate_marker_data(value):
        data = dict(exact_map(value, keys["root_marker"], "root_marker_exact_keys_invalid"))
        role = data["role"]
        if data["schema_version"] != schemas["root_marker"] or role not in root_roles or data["expected_state"] != root_states[role] or type(data["plan_id"]) is not str or not data["plan_id"]:
            raise error_type("root_marker_boundary_invalid")
        base = {"schema_version": data["schema_version"], "role": role, "plan_id": data["plan_id"], "expected_state": data["expected_state"]}
        expected_hash = digest(canon(base))
        if data["marker_sha256"] != expected_hash or data["marker_id"] != "real-a1-root-marker-" + role + "-" + expected_hash[:20]:
            raise error_type("root_marker_identity_invalid")
        return data

    @dataclass(frozen=True)
    class A1RootMarker:
        data: Mapping[str, object]

        @classmethod
        def create(cls, plan_id, role):
            if role not in root_roles:
                raise error_type("root_marker_role_invalid")
            base = {"schema_version": schemas["root_marker"], "role": role, "plan_id": plan_id, "expected_state": root_states[role]}
            marker_hash = digest(canon(base))
            root = {"schema_version": schemas["root_marker"], "marker_id": "real-a1-root-marker-" + role + "-" + marker_hash[:20], "marker_sha256": marker_hash, "role": role, "plan_id": plan_id, "expected_state": root_states[role]}
            return cls(validate_marker_data(root))

        @classmethod
        def from_dict(cls, value):
            return cls(validate_marker_data(value))

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_marker_data(parse_json(raw)))

        def validate(self):
            validate_marker_data(self.data)

        def to_dict(self):
            return dict(validate_marker_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_marker_data(self.data)))

        def sha256(self):
            return digest(canon(dict(validate_marker_data(self.data))))

    marker_type = A1RootMarker
    marker_from_dict = A1RootMarker.from_dict

    def validate_control_data(value):
        data = dict(exact_map(value, keys["control"], "control_exact_keys_invalid"))
        check_time(data["observed_at_utc"], "control_time_invalid")
        check_hex(data["instance_id_hash"], "control_instance_invalid")
        check_hex(data["ssh_host_fingerprint_sha256"], "control_ssh_invalid")
        if data["schema_version"] != schemas["control"] or data["provider"] != "AutoDL" or data["review_state"] != "awaiting_independent_control_plane_validation" or type(data["price_cny_per_hour"]) not in (int, float) or type(data["price_cny_per_hour"]) is bool or not 0 <= data["price_cny_per_hour"] <= 2.88 or type(data["evidence_hashes"]) is not list or len(data["evidence_hashes"]) != 4:
            raise error_type("control_boundary_invalid")
        for evidence_hash in data["evidence_hashes"]:
            check_hex(evidence_hash, "control_evidence_invalid")
        markers = data["root_markers"]
        if type(markers) is not list or len(markers) != len(root_roles):
            raise error_type("control_root_markers_invalid")
        parsed_markers = [marker_from_dict(item) for item in markers]
        if tuple(marker.data["role"] for marker in parsed_markers) != root_roles or any(marker.data["plan_id"] != data["plan_id"] for marker in parsed_markers):
            raise error_type("control_root_markers_invalid")
        root = {key: data[key] for key in keys["control"] if key != "control_id"}
        if data["control_id"] != artifact_id("real-a1-control-", root):
            raise error_type("control_identity_invalid")
        return data

    @dataclass(frozen=True)
    class A1OperationalControls:
        data: Mapping[str, object]

        @classmethod
        def create(cls, plan, instance_id_hash, price, ssh_hash, at, evidence_hashes):
            parsed_plan = parse_plan(plan)
            markers = [A1RootMarker.create(parsed_plan.plan_id, role).to_dict() for role in root_roles]
            root = {"schema_version": schemas["control"], "plan_id": parsed_plan.plan_id, "observed_at_utc": at, "provider": "AutoDL", "instance_id_hash": instance_id_hash, "price_cny_per_hour": price, "ssh_host_fingerprint_sha256": ssh_hash, "evidence_hashes": evidence_hashes, "review_state": "awaiting_independent_control_plane_validation", "root_markers": markers}
            root["control_id"] = artifact_id("real-a1-control-", root)
            return cls(validate_control_data(root))

        @classmethod
        def from_dict(cls, value):
            return cls(validate_control_data(value))

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_control_data(parse_json(raw)))

        def validate(self):
            validate_control_data(self.data)

        def to_dict(self):
            return dict(validate_control_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_control_data(self.data)))

        def sha256(self):
            return digest(canon(dict(validate_control_data(self.data))))

    control_type = A1OperationalControls
    control_from_dict = A1OperationalControls.from_dict

    def parse_control(value):
        if type(value) is not control_type:
            raise error_type("control_type_invalid")
        return control_from_dict(value.data)

    def observation_fixed(inventory):
        return {
            "hardware": {"gpu_count": 1, "gpu_name": "NVIDIA GeForce RTX 5090", "vram_mib": 32768, "ram_gib": 64, "free_disk_gib": 96, "peak_vram_mib": 31000, "peak_rss_mib": 4096},
            "runtime": {"python": "3.11", "torch": "2.7.1+cu128", "transformers": "5.14.1", "cuda": "12.8"},
            "model": {"repository": inventory.data["repository"], "revision": inventory.data["revision"], "file_count": inventory.data["file_count"], "total_bytes": inventory.data["total_bytes"], "inventory_tree_sha256": inventory.data["tree_sha256"], "quantization": "none", "cpu_offload": False},
            "load": {"status": "loaded", "processor_keys": ["input_ids", "attention_mask"]},
            "network": {"namespace_isolation": "not_implemented", "dns_observation": "not_implemented", "egress_observation": "not_implemented", "listener_observation": "not_implemented", "endpoint_observation": "not_implemented"},
            "probe": {"prompt_sha256": digest(benign_prompt.encode("utf-8")), "prompt_length": len(benign_prompt), "generated_suffix_sha256": digest(b"A1 operational probe acknowledged."), "generated_suffix_length": 34, "generated_token_count": 4, "status": "passed"},
            "timing": {"setup_seconds": 1, "load_seconds": 1, "probe_seconds": 1},
        }

    def validate_observation_data(value):
        data = dict(exact_map(value, keys["obs"], "observation_exact_keys_invalid"))
        check_time(data["observed_at_utc"], "observation_time_invalid")
        source = data["source"]
        authority = "req2web.a1.synthetic_dry_run.v1" if source == "synthetic_dry_run" else "req2web.a1.linux_observer.v1" if source == "production_linux_observer" else None
        if authority is None or data["authority_id"] != authority or data["schema_version"] != schemas["obs"]:
            raise error_type("observation_authority_invalid")
        if type(data["model"]) is not dict:
            raise error_type("observation_measurement_invalid")
        model = dict(exact_map(data["model"], ("repository", "revision", "file_count", "total_bytes", "inventory_tree_sha256", "quantization", "cpu_offload"), "observation_model_keys_invalid"))
        fixed = {
            "hardware": observation_fixed_stub["hardware"],
            "runtime": observation_fixed_stub["runtime"],
            "load": observation_fixed_stub["load"],
            "network": observation_fixed_stub["network"],
            "probe": observation_fixed_stub["probe"],
            "timing": observation_fixed_stub["timing"],
        }
        if model["quantization"] != "none" or model["cpu_offload"] is not False or any(data[key] != expected for key, expected in fixed.items()) or (data["decision"], data["failure_codes"]) not in (("pass", []), ("fail", ["synthetic_failure"])):
            raise error_type("observation_measurement_invalid")
        check_hex(model["revision"], "observation_model_revision_invalid", 40)
        check_hex(model["inventory_tree_sha256"], "observation_model_tree_invalid")
        if type(model["repository"]) is not str or not model["repository"] or type(model["file_count"]) is not int or type(model["file_count"]) is bool or model["file_count"] < 1 or type(model["total_bytes"]) is not int or type(model["total_bytes"]) is bool or model["total_bytes"] < 0:
            raise error_type("observation_measurement_invalid")
        root = {key: data[key] for key in keys["obs"] if key != "observation_id"}
        if data["observation_id"] != artifact_id("real-a1-observation-", root):
            raise error_type("observation_identity_invalid")
        return data

    observation_fixed_stub = {
        "hardware": {"gpu_count": 1, "gpu_name": "NVIDIA GeForce RTX 5090", "vram_mib": 32768, "ram_gib": 64, "free_disk_gib": 96, "peak_vram_mib": 31000, "peak_rss_mib": 4096},
        "runtime": {"python": "3.11", "torch": "2.7.1+cu128", "transformers": "5.14.1", "cuda": "12.8"},
        "load": {"status": "loaded", "processor_keys": ["input_ids", "attention_mask"]},
        "network": {"namespace_isolation": "not_implemented", "dns_observation": "not_implemented", "egress_observation": "not_implemented", "listener_observation": "not_implemented", "endpoint_observation": "not_implemented"},
        "probe": {"prompt_sha256": digest(benign_prompt.encode("utf-8")), "prompt_length": len(benign_prompt), "generated_suffix_sha256": digest(b"A1 operational probe acknowledged."), "generated_suffix_length": 34, "generated_token_count": 4, "status": "passed"},
        "timing": {"setup_seconds": 1, "load_seconds": 1, "probe_seconds": 1},
    }

    @dataclass(frozen=True)
    class A1Observation:
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

    observation_type = A1Observation
    observation_from_dict = A1Observation.from_dict

    def parse_observation(value):
        if type(value) is not observation_type:
            raise error_type("observation_type_invalid")
        return observation_from_dict(value.data)

    def build_synthetic_observation(plan, at, decision, failure_codes):
        parsed_plan = parse_plan(plan)
        inventory = inventory_from_dict(parsed_plan.data["model_inventory"])
        root = {"schema_version": schemas["obs"], "plan_id": parsed_plan.plan_id, "source": "synthetic_dry_run", "authority_id": "req2web.a1.synthetic_dry_run.v1", "observed_at_utc": at, **observation_fixed(inventory), "decision": decision, "failure_codes": failure_codes}
        root["observation_id"] = artifact_id("real-a1-observation-", root)
        return validate_observation_data(root)

    class SyntheticA1DryRunAuthority:
        @staticmethod
        def observe(plan, at="2026-07-25T00:00:00Z"):
            return A1Observation(build_synthetic_observation(plan, at, "pass", []))

        @staticmethod
        def fail(plan, at="2026-07-25T00:00:00Z"):
            return A1Observation(build_synthetic_observation(plan, at, "fail", ["synthetic_failure"]))

    def validate_receipt_data(value):
        data = dict(exact_map(value, keys["receipt"], "receipt_exact_keys_invalid"))
        observation = observation_from_dict(data["observation"])
        check_hex(data["plan_sha256"], "receipt_plan_hash_invalid")
        check_hex(data["control_sha256"], "receipt_control_hash_invalid")
        passed = observation.data["decision"] == "pass"
        expected_status = "a1_passed_awaiting_independent_gate" if passed else "a1_failed_cleanup_required"
        expected_state = "awaiting_independent_a1_gate_validation" if passed else "a3_cleanup_release_revoke_required"
        false_flags = ("a2_unlocked", "provider_invoked", "project_data_transferred", "compatibility_run_occurred", "h1_allowed", "formal_quality_allowed")
        if data["schema_version"] != schemas["receipt"] or observation.data["plan_id"] != data["plan_id"] or data["status"] != expected_status or data["next_state"] != expected_state or any(data[key] is not False for key in false_flags):
            raise error_type("receipt_boundary_invalid")
        root = {key: data[key] for key in keys["receipt"] if key != "receipt_id"}
        if data["receipt_id"] != artifact_id("real-a1-receipt-", root):
            raise error_type("receipt_identity_invalid")
        return data

    def build_receipt(plan, controls, observation):
        parsed_plan = parse_plan(plan)
        parsed_controls = parse_control(controls)
        parsed_observation = parse_observation(observation)
        if parsed_controls.data["plan_id"] != parsed_plan.plan_id or parsed_observation.data["plan_id"] != parsed_plan.plan_id:
            raise error_type("receipt_plan_binding_invalid")
        plan_model = parsed_plan.data["model"]
        expected_observation_model = {"repository": plan_model["repository"], "revision": plan_model["revision"], "file_count": plan_model["file_count"], "total_bytes": plan_model["total_bytes"], "inventory_tree_sha256": plan_model["inventory_tree_sha256"], "quantization": "none", "cpu_offload": False}
        if parsed_observation.data["model"] != expected_observation_model:
            raise error_type("receipt_plan_model_binding_invalid")
        passed = parsed_observation.data["decision"] == "pass"
        root = {"schema_version": schemas["receipt"], "plan_id": parsed_plan.plan_id, "plan_sha256": parsed_plan.sha256(), "control_id": parsed_controls.data["control_id"], "control_sha256": parsed_controls.sha256(), "observation": parsed_observation.to_dict(), "status": "a1_passed_awaiting_independent_gate" if passed else "a1_failed_cleanup_required", "next_state": "awaiting_independent_a1_gate_validation" if passed else "a3_cleanup_release_revoke_required", "a2_unlocked": False, "provider_invoked": False, "project_data_transferred": False, "compatibility_run_occurred": False, "h1_allowed": False, "formal_quality_allowed": False}
        root["receipt_id"] = artifact_id("real-a1-receipt-", root)
        return validate_receipt_data(root)

    @dataclass(frozen=True)
    class A1OperationalReceipt:
        data: Mapping[str, object]

        @classmethod
        def create(cls, plan, controls, observation):
            return cls(build_receipt(plan, controls, observation))

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
            if validate_receipt_data(self.data) != build_receipt(plan, controls, observation):
                raise error_type("receipt_replay_invalid")

    receipt_type = A1OperationalReceipt
    receipt_from_dict = A1OperationalReceipt.from_dict

    def parse_receipt(value, code="gate_receipt_type_invalid"):
        if type(value) is not receipt_type:
            raise error_type(code)
        return receipt_from_dict(value.data)

    def validate_gate_data(value):
        data = dict(exact_map(value, keys["gate"], "gate_exact_keys_invalid"))
        allowed = {"synthetic_pass_not_manager_consumable": (False, []), "a1_gate_failed": (False, ["production_authority_not_available_local"]), "a1_failed_cleanup_required": (False, ["evidence_invalid"])}
        if data["schema_version"] != schemas["gate"] or data["status"] not in allowed or (data["manager_consumable"], data["failure_codes"]) != allowed[data["status"]]:
            raise error_type("gate_invalid")
        root = {key: data[key] for key in keys["gate"] if key != "gate_id"}
        if data["gate_id"] != artifact_id("real-a1-gate-", root):
            raise error_type("gate_identity_invalid")
        return data

    @dataclass(frozen=True)
    class A1IndependentGate:
        data: Mapping[str, object]

        @classmethod
        def evaluate(cls, plan, controls, receipt):
            parsed_plan = parse_plan(plan)
            parsed_controls = parse_control(controls)
            parsed_receipt = parse_receipt(receipt)
            observation = observation_from_dict(parsed_receipt.data["observation"])
            parsed_receipt.validate_against(parsed_plan, parsed_controls, observation)
            status = "synthetic_pass_not_manager_consumable" if observation.data["source"] == "synthetic_dry_run" and observation.data["decision"] == "pass" else "a1_gate_failed"
            failures = [] if status == "synthetic_pass_not_manager_consumable" else ["production_authority_not_available_local"]
            root = {"schema_version": schemas["gate"], "plan_id": parsed_plan.plan_id, "receipt_id": parsed_receipt.data["receipt_id"], "status": status, "manager_consumable": False, "failure_codes": failures}
            root["gate_id"] = artifact_id("real-a1-gate-", root)
            return cls(validate_gate_data(root))

        @classmethod
        def from_dict(cls, value):
            return cls(validate_gate_data(value))

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_gate_data(parse_json(raw)))

        def validate(self):
            validate_gate_data(self.data)

        def to_dict(self):
            return dict(validate_gate_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_gate_data(self.data)))

        def sha256(self):
            return digest(canon(dict(validate_gate_data(self.data))))

    def validate_intent_data(value):
        data = dict(exact_map(value, keys["intent"], "intent_exact_keys_invalid"))
        required = (data["project_side_deletion_required"], data["process_termination_required"], data["instance_release_required"], data["credential_revocation_required"], data["physical_erasure_claimed"])
        if data["schema_version"] != schemas["intent"] or required != (True, True, True, True, False):
            raise error_type("intent_invalid")
        root = {key: data[key] for key in keys["intent"] if key != "intent_id"}
        if data["intent_id"] != artifact_id("real-a1-intent-", root):
            raise error_type("intent_identity_invalid")
        return data

    @dataclass(frozen=True)
    class A1CloseoutIntent:
        data: Mapping[str, object]

        @classmethod
        def create(cls, plan, receipt):
            parsed_plan = parse_plan(plan)
            parsed_receipt = parse_receipt(receipt, "closeout_receipt_type_invalid")
            if parsed_receipt.data["plan_id"] != parsed_plan.plan_id:
                raise error_type("closeout_plan_binding_invalid")
            root = {"schema_version": schemas["intent"], "plan_id": parsed_plan.plan_id, "receipt_id": parsed_receipt.data["receipt_id"], "project_side_deletion_required": True, "process_termination_required": True, "instance_release_required": True, "credential_revocation_required": True, "physical_erasure_claimed": False}
            root["intent_id"] = artifact_id("real-a1-intent-", root)
            return cls(validate_intent_data(root))

        @classmethod
        def from_dict(cls, value):
            return cls(validate_intent_data(value))

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_intent_data(parse_json(raw)))

        def validate(self):
            validate_intent_data(self.data)

        def to_dict(self):
            return dict(validate_intent_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_intent_data(self.data)))

        def sha256(self):
            return digest(canon(dict(validate_intent_data(self.data))))

    intent_type = A1CloseoutIntent
    intent_from_dict = A1CloseoutIntent.from_dict

    def parse_intent(value):
        if type(value) is not intent_type:
            raise error_type("intent_type_invalid")
        return intent_from_dict(value.data)

    def validate_closeout_data(value):
        data = dict(exact_map(value, keys["closeout"], "closeout_exact_keys_invalid"))
        evidence = (data["project_side_deleted"], data["process_terminated"], data["instance_release_evidence"], data["credential_revocation_evidence"])
        if data["schema_version"] != schemas["closeout"] or data["status"] not in ("cleanup_not_executed", "cleanup_incomplete") or evidence != (False, False, False, False) or data["physical_erasure_claimed"] is not False:
            raise error_type("closeout_invalid")
        root = {key: data[key] for key in keys["closeout"] if key != "closeout_id"}
        if data["closeout_id"] != artifact_id("real-a1-closeout-", root):
            raise error_type("closeout_identity_invalid")
        return data

    def build_closeout(intent, status):
        parsed_intent = parse_intent(intent)
        root = {"schema_version": schemas["closeout"], "intent_id": parsed_intent.data["intent_id"], "status": status, "project_side_deleted": False, "process_terminated": False, "instance_release_evidence": False, "credential_revocation_evidence": False, "physical_erasure_claimed": False}
        root["closeout_id"] = artifact_id("real-a1-closeout-", root)
        return validate_closeout_data(root)

    @dataclass(frozen=True)
    class A1CloseoutReceipt:
        data: Mapping[str, object]

        @classmethod
        def create(cls, intent):
            return cls(build_closeout(intent, "cleanup_not_executed"))

        @classmethod
        def incomplete(cls, intent):
            return cls(build_closeout(intent, "cleanup_incomplete"))

        @classmethod
        def from_dict(cls, value):
            return cls(validate_closeout_data(value))

        @classmethod
        def from_bytes(cls, raw):
            return cls(validate_closeout_data(parse_json(raw)))

        def validate(self):
            validate_closeout_data(self.data)

        def to_dict(self):
            return dict(validate_closeout_data(self.data))

        def canonical_bytes(self):
            return canon(dict(validate_closeout_data(self.data)))

        def sha256(self):
            return digest(canon(dict(validate_closeout_data(self.data))))

    def build_real_a1_worker_command(plan_path, controls_path, package_root, model_root, evidence_root):
        for value in (plan_path, controls_path, package_root, model_root, evidence_root):
            payload_free(value)
        return [executable, "-m", worker_module, "--plan", str(plan_path), "--controls", str(controls_path), "--package-root", str(package_root), "--model-root", str(model_root), "--evidence-root", str(evidence_root)]

    def run_real_a1_operational(plan_path, controls_path, package_root, model_root, evidence_root):
        for value in (plan_path, controls_path, package_root, model_root, evidence_root):
            payload_free(value)
        raise error_type("real_a1_executor_not_ready")

    def validate_a1_model_inventory_bytes(raw):
        return A1ModelInventory.from_bytes(raw)

    def validate_a1_operational_plan_bytes(raw):
        return A1OperationalPlan.from_bytes(raw)

    def validate_a1_operational_controls_bytes(raw):
        return A1OperationalControls.from_bytes(raw)

    def validate_a1_operational_receipt_bytes(raw, plan, controls, observation):
        receipt = A1OperationalReceipt.from_bytes(raw)
        receipt.validate_against(plan, controls, observation)
        return receipt

    return {
        "A1ModelInventory": A1ModelInventory,
        "A1RootMarker": A1RootMarker,
        "A1OperationalPlan": A1OperationalPlan,
        "A1OperationalControls": A1OperationalControls,
        "A1Observation": A1Observation,
        "SyntheticA1DryRunAuthority": SyntheticA1DryRunAuthority,
        "A1OperationalReceipt": A1OperationalReceipt,
        "A1IndependentGate": A1IndependentGate,
        "A1CloseoutIntent": A1CloseoutIntent,
        "A1CloseoutReceipt": A1CloseoutReceipt,
        "build_real_a1_worker_command": build_real_a1_worker_command,
        "run_real_a1_operational": run_real_a1_operational,
        "validate_a1_model_inventory_bytes": validate_a1_model_inventory_bytes,
        "validate_a1_operational_plan_bytes": validate_a1_operational_plan_bytes,
        "validate_a1_operational_controls_bytes": validate_a1_operational_controls_bytes,
        "validate_a1_operational_receipt_bytes": validate_a1_operational_receipt_bytes,
        "canon": canon,
        "digest": digest,
        "artifact_id": artifact_id,
        "parse_json": parse_json,
        "exact_map": exact_map,
        "check_hex": check_hex,
        "check_time": check_time,
        "payload_free": payload_free,
        "parse_base_plan": parse_base_plan,
        "parse_base_plan_dict": parse_base_plan_dict,
        "validate_inventory_data": validate_inventory_data,
        "build_synthetic_inventory_for_tests": build_synthetic_inventory_for_tests,
        "validate_plan_data": validate_plan_data,
        "validate_marker_data": validate_marker_data,
        "validate_control_data": validate_control_data,
        "validate_observation_data": validate_observation_data,
        "validate_receipt_data": validate_receipt_data,
        "validate_gate_data": validate_gate_data,
        "validate_intent_data": validate_intent_data,
        "validate_closeout_data": validate_closeout_data,
        "parse_inventory": parse_inventory,
        "parse_plan": parse_plan,
        "parse_control": parse_control,
        "parse_observation": parse_observation,
        "parse_receipt": parse_receipt,
        "parse_intent": parse_intent,
        "schemas": schemas,
        "keys": keys,
        "official_rows": official_rows,
        "root_states": root_states,
    }


_AUTHORITIES = _build_operational_authorities()
A1ModelInventory = _AUTHORITIES["A1ModelInventory"]
A1RootMarker = _AUTHORITIES["A1RootMarker"]
A1OperationalPlan = _AUTHORITIES["A1OperationalPlan"]
A1OperationalControls = _AUTHORITIES["A1OperationalControls"]
A1Observation = _AUTHORITIES["A1Observation"]
SyntheticA1DryRunAuthority = _AUTHORITIES["SyntheticA1DryRunAuthority"]
A1OperationalReceipt = _AUTHORITIES["A1OperationalReceipt"]
A1IndependentGate = _AUTHORITIES["A1IndependentGate"]
A1CloseoutIntent = _AUTHORITIES["A1CloseoutIntent"]
A1CloseoutReceipt = _AUTHORITIES["A1CloseoutReceipt"]
build_real_a1_worker_command = _AUTHORITIES["build_real_a1_worker_command"]
run_real_a1_operational = _AUTHORITIES["run_real_a1_operational"]
validate_a1_model_inventory_bytes = _AUTHORITIES["validate_a1_model_inventory_bytes"]
validate_a1_operational_plan_bytes = _AUTHORITIES["validate_a1_operational_plan_bytes"]
validate_a1_operational_controls_bytes = _AUTHORITIES["validate_a1_operational_controls_bytes"]
validate_a1_operational_receipt_bytes = _AUTHORITIES["validate_a1_operational_receipt_bytes"]

# Patchable names for adversarial rebinding tests. Public authorities retain the
# private closure values created above and never resolve these globals at call time.
_captured_canon = _AUTHORITIES["canon"]
_captured_sha = _AUTHORITIES["digest"]
_captured_id = _AUTHORITIES["artifact_id"]
_captured_json = _AUTHORITIES["parse_json"]
_captured_map = _AUTHORITIES["exact_map"]
_captured_hex = _AUTHORITIES["check_hex"]
_captured_time = _AUTHORITIES["check_time"]
_captured_datetime = datetime.strptime
_captured_payload_free = _AUTHORITIES["payload_free"]
_captured_base_plan = _AUTHORITIES["parse_base_plan"]
_captured_base_plan_dict = _AUTHORITIES["parse_base_plan_dict"]
_validate_model_inventory_data = _AUTHORITIES["validate_inventory_data"]
_build_synthetic_model_inventory_for_tests = _AUTHORITIES["build_synthetic_inventory_for_tests"]
_validate_plan_data = _AUTHORITIES["validate_plan_data"]
_validate_root_marker_data = _AUTHORITIES["validate_marker_data"]
_validate_control_data = _AUTHORITIES["validate_control_data"]
_validate_observation_data = _AUTHORITIES["validate_observation_data"]
_validate_receipt_data = _AUTHORITIES["validate_receipt_data"]
_validate_gate_data = _AUTHORITIES["validate_gate_data"]
_validate_intent_data = _AUTHORITIES["validate_intent_data"]
_validate_closeout_data = _AUTHORITIES["validate_closeout_data"]
_captured_inventory_parser = _AUTHORITIES["parse_inventory"]
_captured_plan_parser = _AUTHORITIES["parse_plan"]
_captured_control_parser = _AUTHORITIES["parse_control"]
_captured_observation_parser = _AUTHORITIES["parse_observation"]
_captured_receipt_parser = _AUTHORITIES["parse_receipt"]
_captured_intent_parser = _AUTHORITIES["parse_intent"]
_TRUST = MappingProxyType({"schemas": _AUTHORITIES["schemas"], "keys": _AUTHORITIES["keys"], "official_rows": _AUTHORITIES["official_rows"], "root_states": _AUTHORITIES["root_states"]})
