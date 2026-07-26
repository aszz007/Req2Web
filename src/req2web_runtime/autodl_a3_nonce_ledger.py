"""Dedicated no-action durable nonce ledger for future Req2Web action-time issuance.

The ledger is local control state only.  It records non-authorizing intent state
and deliberately stops at ``closeout_required``.  It never signs, invokes an
endpoint, starts a process, or marks a capability as executable.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType
import weakref

import req2web_runtime.autodl_a3_action_time_issuer as _issuer


class DurableNonceLedgerError(ValueError):
    """Raised for an unsafe local nonce-ledger condition."""


def _build_authorities():
    bool_type = bool
    bytes_type = bytes
    dict_type = dict
    int_type = int
    list_type = list
    str_type = str
    type_fn = type
    len_fn = len
    set_type = set
    tuple_fn = tuple
    id_fn = id
    object_new = object.__new__
    weakref_ref = weakref.ref
    path_type = type(Path("."))
    module_file = __file__
    os_lstat = os.lstat
    os_abspath = os.path.abspath
    os_replace = os.replace
    os_open = os.open
    os_close = os.close
    os_fsync = os.fsync
    os_write = os.write
    os_name = os.name
    sys_platform = sys.platform
    directory_flag = getattr(os, "O_DIRECTORY", None)
    flags_exclusive = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags_directory_read = os.O_RDONLY | directory_flag if type(directory_flag) is int else None
    stat_symlink = 0o120000
    json_loads = json.loads
    json_dumps = json.dumps
    json_error = json.JSONDecodeError
    sha256_fn = hashlib.sha256
    datetime_type = datetime
    timezone_utc = timezone.utc
    mapping_proxy_type = MappingProxyType
    error_type = DurableNonceLedgerError
    type_error = TypeError
    value_error = ValueError
    temporary_directory = tempfile.TemporaryDirectory

    schema = "req2web.runtime.durable_action_time_nonce_ledger.v1"
    root_marker_schema = "req2web.runtime.durable_action_time_nonce_root.v1"
    protocol_status = "durable_nonce_readiness_no_action"
    root_name = "req2web-action-time-nonce-ledger"
    marker_name = ".req2web_nonce_ledger_root_v1.json"
    ledger_name = "nonce-ledger.json"
    lock_name = ".nonce-ledger.lock"
    lock_contents = b"req2web-no-action-ledger-lock"
    states = (
        "reserved", "issued", "delivered", "accepted_remote", "started",
        "terminal_success", "terminal_failure", "closeout_required",
    )
    terminal_states = ("terminal_success", "terminal_failure", "closeout_required")
    row_keys = (
        "nonce_id", "nonce_sha256", "issuance_id", "issuance_sha256",
        "expires_at_utc", "state", "transition_index", "history",
    )
    history_keys = ("state", "transition_index")
    ledger_keys = (
        "schema_version", "ledger_id", "protocol_status", "root_marker_sha256",
        "entries", "permit_issued", "signature_verified",
        "destructive_action_authorized", "external_action_executed",
        "cleanup_complete", "manager_consumable", "next_run_allowed",
        "a2_unlocked", "h1_allowed", "formal_quality_allowed",
    )
    marker_keys = ("schema_version", "root_marker_id", "normalized_root_sha256")
    ledger_registry = {}

    intent_type = _issuer.ActionTimeIssuanceIntent
    intent_parse_trusted = _issuer._parse_action_time_issuance_intent_for_trust
    intent_canonical_trusted = _issuer._action_time_issuance_intent_canonical_for_trust
    intent_to_dict_trusted = _issuer._action_time_issuance_intent_to_dict_for_trust
    intent_sha_trusted = _issuer._action_time_issuance_intent_sha256_for_trust
    intent_ledger_eligible_trusted = _issuer._action_time_issuance_intent_ledger_eligible_for_trust

    def canonical(value):
        try:
            return json_dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (type_error, value_error) as exc:
            raise error_type("durable_nonce_ledger_canonical_invalid") from exc

    def digest(raw):
        if type_fn(raw) is not bytes_type:
            raise error_type("durable_nonce_ledger_digest_bytes_required")
        return sha256_fn(raw).hexdigest()

    def parse_json(raw, code):
        if type_fn(raw) is not bytes_type:
            raise error_type(code)
        try:
            return json_loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json_error) as exc:
            raise error_type(code) from exc

    def exact_map(value, keys, code):
        if type_fn(value) is not dict_type or len_fn(value) != len_fn(keys) or set_type(value.keys()) != set_type(keys):
            raise error_type(code)
        return value

    def require_text(value, code):
        if type_fn(value) is not str_type or not value:
            raise error_type(code)
        return value

    def require_hash(value, code):
        if type_fn(value) is not str_type or len_fn(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise error_type(code)
        return value

    def require_flags(data, code):
        for key in (
            "permit_issued", "signature_verified", "destructive_action_authorized",
            "external_action_executed", "cleanup_complete", "manager_consumable",
            "next_run_allowed", "a2_unlocked", "h1_allowed", "formal_quality_allowed",
        ):
            if type_fn(data[key]) is not bool_type or data[key] is not False:
                raise error_type(code)

    def utc_now():
        return datetime_type.now(timezone_utc)

    def parse_utc(value, code):
        require_text(value, code)
        if not value.endswith("Z"):
            raise error_type(code)
        try:
            parsed = datetime_type.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise error_type(code) from exc
        if parsed.tzinfo != timezone_utc:
            raise error_type(code)
        return parsed

    def normalized_root_hash(root):
        return digest(str(root).encode("utf-8"))

    def lstat_required(path, code):
        try:
            return os_lstat(path)
        except FileNotFoundError as exc:
            raise error_type(code) from exc
        except OSError as exc:
            raise error_type(code) from exc

    def lstat_optional(path, code):
        try:
            return os_lstat(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise error_type(code) from exc

    def is_symlink(stat_value):
        return stat_value.st_mode & 0o170000 == stat_symlink

    def require_directory(path, code):
        stat_value = lstat_required(path, code)
        if is_symlink(stat_value) or stat_value.st_mode & 0o170000 != 0o040000:
            raise error_type(code)
        return stat_value

    def require_regular_file(path, code):
        stat_value = lstat_required(path, code)
        if is_symlink(stat_value) or stat_value.st_mode & 0o170000 != 0o100000:
            raise error_type(code)
        return stat_value

    def file_identity(stat_value):
        return (
            stat_value.st_dev,
            stat_value.st_ino,
            stat_value.st_size,
            stat_value.st_ctime_ns,
        )

    def validate_ancestors(root, code):
        current = root
        while True:
            require_directory(current, code)
            if current == current.parent:
                return
            current = current.parent

    def normalized_dedicated_path(root):
        if type_fn(root) is not path_type:
            raise error_type("durable_nonce_ledger_root_type_invalid")
        root = path_type(os_abspath(str(root)))
        if root.name != root_name:
            raise error_type("durable_nonce_ledger_root_name_invalid")
        validate_ancestors(root.parent, "durable_nonce_ledger_root_parent_symlink_invalid")
        return root

    def production_commit_barrier_supported():
        return (
            os_name == "posix"
            and sys_platform.startswith("linux")
            and type_fn(directory_flag) is int_type
            and type_fn(flags_directory_read) is int_type
        )

    def sync_directory(directory, code):
        if not production_commit_barrier_supported():
            raise error_type("durable_nonce_ledger_commit_barrier_unsupported_closeout_required")
        require_directory(directory, code)
        descriptor = None
        try:
            descriptor = os_open(str(directory), flags_directory_read)
        except OSError as exc:
            raise error_type(code) from exc
        try:
            os_fsync(descriptor)
        except OSError as exc:
            raise error_type(code) from exc
        finally:
            if descriptor is not None:
                try:
                    os_close(descriptor)
                except OSError as exc:
                    raise error_type(code) from exc

    def require_production_commit_barrier(root):
        root = normalized_dedicated_path(root)
        if not production_commit_barrier_supported():
            raise error_type("durable_nonce_ledger_commit_barrier_unsupported_closeout_required")
        # Verify the parent directory barrier before creating the production root.
        sync_directory(root.parent, "durable_nonce_ledger_commit_barrier_parent_unavailable_closeout_required")
        return root

    def normalized_dedicated_root(root, *, create, production=False):
        root = normalized_dedicated_path(root)
        stat_value = lstat_optional(root, "durable_nonce_ledger_root_lstat_invalid")
        if stat_value is None:
            if not create:
                raise error_type("durable_nonce_ledger_root_missing_closeout_required")
            try:
                root.mkdir()
            except OSError as exc:
                raise error_type("durable_nonce_ledger_root_create_failed_closeout_required") from exc
            if production:
                sync_directory(root.parent, "durable_nonce_ledger_commit_barrier_root_create_closeout_required")
        else:
            if is_symlink(stat_value) or stat_value.st_mode & 0o170000 != 0o040000:
                raise error_type("durable_nonce_ledger_root_not_directory")
        validate_ancestors(root, "durable_nonce_ledger_root_or_ancestor_drift_closeout_required")
        return root

    def root_children(root, code):
        try:
            return {child.name for child in root.iterdir()}
        except OSError as exc:
            raise error_type(code) from exc

    def marker_data(root):
        body = {
            "schema_version": root_marker_schema,
            "root_marker_id": "pending",
            "normalized_root_sha256": normalized_root_hash(root),
        }
        identity_body = dict_type(body)
        identity_body.pop("root_marker_id")
        body["root_marker_id"] = "nonce-ledger-root-" + digest(canonical(identity_body))[:20]
        return body

    def marker_bytes(root):
        return canonical(marker_data(root))

    def write_all(descriptor, raw, code, write_fn=None):
        if type_fn(raw) is not bytes_type:
            raise error_type(code)
        writer = os_write if write_fn is None else write_fn
        offset = 0
        while offset < len_fn(raw):
            try:
                written = writer(descriptor, raw[offset:])
            except Exception as exc:
                raise error_type(code) from exc
            if (
                type_fn(written) is not int_type
                or written <= 0
                or written > len_fn(raw) - offset
            ):
                raise error_type(code)
            offset += written
        return offset

    def atomic_write(path, raw, *, production=False):
        if type_fn(raw) is not bytes_type:
            raise error_type("durable_nonce_ledger_write_bytes_invalid")
        require_directory(path.parent, "durable_nonce_ledger_parent_symlink_invalid")
        temporary = path.parent / ("." + path.name + ".write")
        if lstat_optional(temporary, "durable_nonce_ledger_ambiguous_temporary_state") is not None:
            raise error_type("durable_nonce_ledger_ambiguous_temporary_state")
        descriptor = None
        try:
            # O_EXCL makes the creation step authoritative: an existing ordinary
            # file or symlink is never truncated, reused, or followed.
            descriptor = os_open(str(temporary), flags_exclusive, 0o666)
        except FileExistsError as exc:
            raise error_type("durable_nonce_ledger_ambiguous_temporary_state") from exc
        except OSError as exc:
            raise error_type("durable_nonce_ledger_atomic_write_failed_closeout_required") from exc
        try:
            write_all(descriptor, raw, "durable_nonce_ledger_write_all_incomplete_closeout_required")
            os_fsync(descriptor)
        except OSError as exc:
            raise error_type(
                "durable_nonce_ledger_commit_barrier_temp_fsync_closeout_required"
                if production else "durable_nonce_ledger_atomic_write_failed_closeout_required"
            ) from exc
        finally:
            if descriptor is not None:
                try:
                    os_close(descriptor)
                except OSError as exc:
                    raise error_type(
                        "durable_nonce_ledger_commit_barrier_temp_close_closeout_required"
                        if production else "durable_nonce_ledger_atomic_write_failed_closeout_required"
                    ) from exc
        try:
            os_replace(str(temporary), str(path))
        except OSError as exc:
            raise error_type(
                "durable_nonce_ledger_commit_barrier_replace_closeout_required"
                if production else "durable_nonce_ledger_atomic_write_failed_closeout_required"
            ) from exc
        if production:
            sync_directory(path.parent, "durable_nonce_ledger_commit_barrier_directory_sync_closeout_required")

    def read_marker(root, expected_marker_hash):
        marker_path = root / marker_name
        require_regular_file(marker_path, "durable_nonce_ledger_marker_symlink_invalid")
        try:
            raw = marker_path.read_bytes()
        except OSError as exc:
            raise error_type("durable_nonce_ledger_marker_invalid") from exc
        expected = marker_bytes(root)
        if raw != expected or digest(raw) != expected_marker_hash:
            raise error_type("durable_nonce_ledger_marker_or_containment_drift_closeout_required")
        marker = parse_json(raw, "durable_nonce_ledger_marker_invalid")
        if marker != marker_data(root):
            raise error_type("durable_nonce_ledger_marker_or_containment_drift_closeout_required")
        return raw

    def initialize_root(root, *, production=False):
        root = normalized_dedicated_root(root, create=True, production=production)
        marker_path = root / marker_name
        ledger_path = root / ledger_name
        marker_stat = lstat_optional(marker_path, "durable_nonce_ledger_marker_invalid")
        if marker_stat is not None:
            if is_symlink(marker_stat) or marker_stat.st_mode & 0o170000 != 0o100000:
                raise error_type("durable_nonce_ledger_marker_symlink_invalid")
            marker_raw = marker_path.read_bytes()
            expected = marker_bytes(root)
            if marker_raw != expected:
                raise error_type("durable_nonce_ledger_marker_or_containment_drift_closeout_required")
            if lstat_optional(ledger_path, "durable_nonce_ledger_ledger_missing_closeout_required") is None:
                raise error_type("durable_nonce_ledger_ledger_missing_closeout_required")
            allowed = {marker_name, ledger_name, lock_name}
            if not root_children(root, "durable_nonce_ledger_root_contents_invalid") <= allowed:
                raise error_type("durable_nonce_ledger_root_contents_invalid")
            return digest(marker_raw), False
        if root_children(root, "durable_nonce_ledger_root_contents_invalid"):
            raise error_type("durable_nonce_ledger_unmarked_root_not_empty")
        marker_raw = marker_bytes(root)
        atomic_write(marker_path, marker_raw, production=production)
        return digest(marker_raw), True

    def read_lock_binding(lock, code):
        stat_before = require_regular_file(lock, code)
        try:
            raw = lock.read_bytes()
        except OSError as exc:
            raise error_type(code) from exc
        stat_after = require_regular_file(lock, code)
        identity = file_identity(stat_after)
        if file_identity(stat_before) != identity or raw != lock_contents:
            raise error_type("durable_nonce_ledger_lock_ownership_drift_closeout_required")
        return identity, digest(raw)

    def validate_live_root(root, marker_hash, *, allow_lock, expected_lock_binding=None):
        root = normalized_dedicated_root(root, create=False)
        allowed = {marker_name, ledger_name, lock_name}
        if not root_children(root, "durable_nonce_ledger_root_contents_invalid") <= allowed:
            raise error_type("durable_nonce_ledger_root_contents_invalid")
        read_marker(root, marker_hash)
        ledger_path = root / ledger_name
        ledger_stat_before = require_regular_file(ledger_path, "durable_nonce_ledger_ledger_missing_or_not_regular_closeout_required")
        lock_path = root / lock_name
        lock_stat = lstat_optional(lock_path, "durable_nonce_ledger_lock_or_ambiguous_state_closeout_required")
        if allow_lock:
            if expected_lock_binding is None or lock_stat is None:
                raise error_type("durable_nonce_ledger_lock_ownership_drift_closeout_required")
            if read_lock_binding(lock_path, "durable_nonce_ledger_lock_ownership_drift_closeout_required") != expected_lock_binding:
                raise error_type("durable_nonce_ledger_lock_ownership_drift_closeout_required")
        elif lock_stat is not None:
            raise error_type("durable_nonce_ledger_lock_or_ambiguous_state_closeout_required")
        try:
            raw = ledger_path.read_bytes()
        except OSError as exc:
            raise error_type("durable_nonce_ledger_read_failed_closeout_required") from exc
        ledger_stat_after = require_regular_file(ledger_path, "durable_nonce_ledger_ledger_missing_or_not_regular_closeout_required")
        if file_identity(ledger_stat_before) != file_identity(ledger_stat_after):
            raise error_type("durable_nonce_ledger_file_drift_closeout_required")
        read_marker(root, marker_hash)
        validate_ancestors(root, "durable_nonce_ledger_root_or_ancestor_drift_closeout_required")
        return raw, file_identity(ledger_stat_after)

    def initial_ledger(marker_hash):
        root = {
            "schema_version": schema, "ledger_id": "pending", "protocol_status": protocol_status,
            "root_marker_sha256": marker_hash, "entries": [],
            "permit_issued": False, "signature_verified": False,
            "destructive_action_authorized": False, "external_action_executed": False,
            "cleanup_complete": False, "manager_consumable": False,
            "next_run_allowed": False, "a2_unlocked": False, "h1_allowed": False,
            "formal_quality_allowed": False,
        }
        identity_root = dict_type(root)
        identity_root.pop("ledger_id")
        identity_root.pop("entries")
        root["ledger_id"] = "durable-nonce-ledger-" + digest(canonical(identity_root))[:20]
        return root

    transition_authority = mapping_proxy_type({
        "reserved": ("issued", "closeout_required"),
        "issued": ("delivered", "closeout_required"),
        "delivered": ("accepted_remote", "closeout_required"),
        "accepted_remote": ("started", "closeout_required"),
        "started": ("terminal_success", "terminal_failure", "closeout_required"),
        "terminal_success": ("closeout_required",),
        "terminal_failure": ("closeout_required",),
        "closeout_required": (),
    })

    def transition_allowed(current, next_state):
        return next_state in transition_authority[current]

    def validate_history(value, expected_state, expected_index):
        if type_fn(value) is not list_type or not value or expected_index != len_fn(value) - 1:
            raise error_type("durable_nonce_ledger_history_invalid")
        first = exact_map(value[0], history_keys, "durable_nonce_ledger_history_invalid")
        if first["state"] != "reserved" or type_fn(first["transition_index"]) is not int_type or first["transition_index"] != 0:
            raise error_type("durable_nonce_ledger_history_invalid")
        previous_state = first["state"]
        for index, item in enumerate(value[1:], start=1):
            row = exact_map(item, history_keys, "durable_nonce_ledger_history_invalid")
            if (
                row["state"] not in states
                or type_fn(row["transition_index"]) is not int_type
                or row["transition_index"] != index
                or not transition_allowed(previous_state, row["state"])
            ):
                raise error_type("durable_nonce_ledger_history_invalid")
            previous_state = row["state"]
        if previous_state != expected_state:
            raise error_type("durable_nonce_ledger_history_invalid")

    def validate_ledger(data, marker_hash):
        data = exact_map(data, ledger_keys, "durable_nonce_ledger_exact_keys_invalid")
        if data["schema_version"] != schema or data["protocol_status"] != protocol_status:
            raise error_type("durable_nonce_ledger_schema_or_status_invalid")
        require_text(data["ledger_id"], "durable_nonce_ledger_id_invalid")
        if data["root_marker_sha256"] != marker_hash:
            raise error_type("durable_nonce_ledger_marker_binding_invalid")
        require_flags(data, "durable_nonce_ledger_public_authority_forbidden")
        if type_fn(data["entries"]) is not list_type:
            raise error_type("durable_nonce_ledger_entries_invalid")
        seen = set_type()
        for index, row in enumerate(data["entries"]):
            row = exact_map(row, row_keys, "durable_nonce_ledger_row_invalid")
            require_text(row["nonce_id"], "durable_nonce_ledger_row_invalid")
            require_hash(row["nonce_sha256"], "durable_nonce_ledger_row_invalid")
            require_text(row["issuance_id"], "durable_nonce_ledger_row_invalid")
            require_hash(row["issuance_sha256"], "durable_nonce_ledger_row_invalid")
            parse_utc(row["expires_at_utc"], "durable_nonce_ledger_row_invalid")
            if row["state"] not in states or type_fn(row["transition_index"]) is not int_type or row["transition_index"] < 0:
                raise error_type("durable_nonce_ledger_row_invalid")
            validate_history(row["history"], row["state"], row["transition_index"])
            identity = (row["nonce_id"], row["nonce_sha256"])
            if identity in seen:
                raise error_type("durable_nonce_ledger_nonce_replay_or_duplicate")
            seen.add(identity)
        identity_root = dict_type(data)
        identity_root.pop("ledger_id")
        identity_root.pop("entries")
        expected = "durable-nonce-ledger-" + digest(canonical(identity_root))[:20]
        if data["ledger_id"] != expected:
            raise error_type("durable_nonce_ledger_identity_invalid")
        return data

    def load_ledger(
        root,
        marker_hash,
        *,
        expected_file_identity=None,
        expected_ledger_sha256=None,
        allow_lock=False,
        expected_lock_binding=None,
    ):
        raw, current_identity = validate_live_root(
            root,
            marker_hash,
            allow_lock=allow_lock,
            expected_lock_binding=expected_lock_binding,
        )
        current_sha256 = digest(raw)
        if expected_file_identity is not None and current_identity != expected_file_identity:
            raise error_type("durable_nonce_ledger_file_drift_closeout_required")
        if expected_ledger_sha256 is not None and current_sha256 != expected_ledger_sha256:
            raise error_type("durable_nonce_ledger_file_drift_closeout_required")
        return (
            validate_ledger(parse_json(raw, "durable_nonce_ledger_corrupt_closeout_required"), marker_hash),
            raw,
            current_identity,
        )

    def acquire_lock(root, *, production=False):
        lock = root / lock_name
        existing = lstat_optional(lock, "durable_nonce_ledger_lock_or_ambiguous_state_closeout_required")
        if existing is not None:
            raise error_type("durable_nonce_ledger_lock_or_ambiguous_state_closeout_required")
        descriptor = None
        try:
            descriptor = os_open(str(lock), flags_exclusive, 0o666)
            write_all(descriptor, lock_contents, "durable_nonce_ledger_write_all_incomplete_closeout_required")
            os_fsync(descriptor)
        except OSError as exc:
            raise error_type(
                "durable_nonce_ledger_commit_barrier_lock_create_closeout_required"
                if production else "durable_nonce_ledger_lock_or_ambiguous_state_closeout_required"
            ) from exc
        finally:
            if descriptor is not None:
                try:
                    os_close(descriptor)
                except OSError as exc:
                    raise error_type(
                        "durable_nonce_ledger_commit_barrier_lock_close_closeout_required"
                        if production else "durable_nonce_ledger_lock_or_ambiguous_state_closeout_required"
                    ) from exc
        if production:
            sync_directory(root, "durable_nonce_ledger_commit_barrier_lock_create_directory_sync_closeout_required")
        return lock, read_lock_binding(lock, "durable_nonce_ledger_lock_ownership_drift_closeout_required")

    def release_lock(lock, expected_lock_binding, *, production=False):
        try:
            if read_lock_binding(lock, "durable_nonce_ledger_lock_ownership_drift_closeout_required") != expected_lock_binding:
                raise error_type("durable_nonce_ledger_lock_ownership_drift_closeout_required")
            lock.unlink()
        except OSError as exc:
            raise error_type("durable_nonce_ledger_lock_release_failed_closeout_required") from exc
        if production:
            sync_directory(lock.parent, "durable_nonce_ledger_commit_barrier_lock_release_directory_sync_closeout_required")

    class DurableNonceLedger:
        __slots__ = ("__weakref__",)
        def __new__(cls, *args, **kwargs):
            raise type_error("durable_nonce_ledger_factory_required")
        def snapshot(self):
            root, marker_hash, file_binding, ledger_sha256, _production = registered_entry(self)
            data, _raw, _identity = load_ledger(
                root,
                marker_hash,
                expected_file_identity=file_binding,
                expected_ledger_sha256=ledger_sha256,
                allow_lock=False,
            )
            return mapping_proxy_type(dict_type(data))

    ledger_type = DurableNonceLedger

    def registered_entry(instance):
        if type_fn(instance) is not ledger_type:
            raise type_error("durable_nonce_ledger_exact_type_required")
        entry = ledger_registry.get(id_fn(instance))
        if entry is None or entry[0]() is not instance:
            raise type_error("durable_nonce_ledger_not_registered")
        return entry[1], entry[2], entry[3], entry[4], entry[5]

    def update_registered_binding(instance, root, marker_hash, raw, file_binding):
        identity = id_fn(instance)
        entry = ledger_registry.get(identity)
        if entry is None or entry[0]() is not instance or entry[1] != root or entry[2] != marker_hash:
            raise type_error("durable_nonce_ledger_not_registered")
        ledger_registry[identity] = (entry[0], root, marker_hash, file_binding, digest(raw), entry[5])

    def create_ledger(root, *, production=False):
        if production:
            root = require_production_commit_barrier(root)
        marker_hash, marker_created = initialize_root(root, production=production)
        root = normalized_dedicated_root(root, create=False)
        if marker_created:
            atomic_write(root / ledger_name, canonical(initial_ledger(marker_hash)), production=production)
        data, raw, file_binding = load_ledger(root, marker_hash, allow_lock=False)
        instance = object_new(ledger_type)
        identity = id_fn(instance)
        def discard(stored_ref, identity_key=identity):
            current = ledger_registry.get(identity_key)
            if current is not None and current[0] is stored_ref:
                ledger_registry.pop(identity_key, None)
        ref = weakref_ref(instance, discard)
        ledger_registry[identity] = (ref, root, marker_hash, file_binding, digest(raw), production)
        return instance

    def production_root():
        return path_type(module_file).absolute().parents[2] / root_name

    def open_production_ledger_no_action():
        return create_ledger(production_root(), production=True)

    def _intent_data(intent):
        if type_fn(intent) is not intent_type:
            raise type_error("durable_nonce_ledger_intent_exact_type_required")
        intent_ledger_eligible_trusted(intent)
        parsed = intent_parse_trusted(intent_canonical_trusted(intent))
        data = intent_to_dict_trusted(parsed)
        if (
            data["permit_issued"] or data["signature_verified"]
            or data["destructive_action_authorized"] or data["external_action_executed"]
            or data["a2_unlocked"] or data["h1_allowed"] or data["formal_quality_allowed"]
        ):
            raise error_type("durable_nonce_ledger_intent_public_authority_forbidden")
        return data, intent_sha_trusted(parsed)

    def write_ledger(instance, root, marker_hash, state, lock_binding, production):
        validated = validate_ledger(state, marker_hash)
        _old_data, _old_raw, _old_identity = load_ledger(
            root,
            marker_hash,
            expected_file_identity=registered_entry(instance)[2],
            expected_ledger_sha256=registered_entry(instance)[3],
            allow_lock=True,
            expected_lock_binding=lock_binding,
        )
        raw = canonical(validated)
        atomic_write(root / ledger_name, raw, production=production)
        _new_data, persisted_raw, persisted_identity = load_ledger(
            root,
            marker_hash,
            allow_lock=True,
            expected_lock_binding=lock_binding,
        )
        if persisted_raw != raw:
            raise error_type("durable_nonce_ledger_atomic_write_failed_closeout_required")
        update_registered_binding(instance, root, marker_hash, persisted_raw, persisted_identity)
        return validated

    def reserve(ledger, intent):
        root, marker_hash, _file_binding, _ledger_sha256, production = registered_entry(ledger)
        data, intent_sha = _intent_data(intent)
        # Live validation before lock acquisition and again while holding the lock.
        load_ledger(root, marker_hash, expected_file_identity=_file_binding, expected_ledger_sha256=_ledger_sha256, allow_lock=False)
        lock, lock_binding = acquire_lock(root, production=production)
        try:
            state, _raw, _identity = load_ledger(
                root,
                marker_hash,
                expected_file_identity=registered_entry(ledger)[2],
                expected_ledger_sha256=registered_entry(ledger)[3],
                allow_lock=True,
                expected_lock_binding=lock_binding,
            )
            state = dict_type(state)
            now = utc_now()
            expires = parse_utc(data["nonce"]["expires_at_utc"], "durable_nonce_ledger_nonce_time_invalid")
            if expires <= now:
                raise error_type("durable_nonce_ledger_nonce_expired_closeout_required")
            identity = (data["nonce"]["nonce_id"], data["nonce"]["nonce_sha256"])
            if any((row["nonce_id"], row["nonce_sha256"]) == identity for row in state["entries"]):
                raise error_type("durable_nonce_ledger_nonce_replay_or_duplicate")
            state["entries"].append({
                "nonce_id": identity[0], "nonce_sha256": identity[1],
                "issuance_id": data["issuance_id"], "issuance_sha256": intent_sha,
                "expires_at_utc": data["nonce"]["expires_at_utc"], "state": "reserved",
                "transition_index": 0, "history": [{"state": "reserved", "transition_index": 0}],
            })
            write_ledger(ledger, root, marker_hash, state, lock_binding, production)
        finally:
            release_lock(lock, lock_binding, production=production)
        return ledger.snapshot()

    def transition(ledger, nonce_id, nonce_sha256, next_state):
        root, marker_hash, _file_binding, _ledger_sha256, production = registered_entry(ledger)
        require_text(nonce_id, "durable_nonce_ledger_nonce_identity_invalid")
        require_hash(nonce_sha256, "durable_nonce_ledger_nonce_identity_invalid")
        if next_state not in states:
            raise error_type("durable_nonce_ledger_transition_invalid")
        load_ledger(root, marker_hash, expected_file_identity=_file_binding, expected_ledger_sha256=_ledger_sha256, allow_lock=False)
        lock, lock_binding = acquire_lock(root, production=production)
        try:
            state, _raw, _identity = load_ledger(
                root,
                marker_hash,
                expected_file_identity=registered_entry(ledger)[2],
                expected_ledger_sha256=registered_entry(ledger)[3],
                allow_lock=True,
                expected_lock_binding=lock_binding,
            )
            state = dict_type(state)
            matches = [row for row in state["entries"] if row["nonce_id"] == nonce_id and row["nonce_sha256"] == nonce_sha256]
            if len_fn(matches) != 1:
                raise error_type("durable_nonce_ledger_nonce_not_found_or_ambiguous")
            row = matches[0]
            now = utc_now()
            expires = parse_utc(row["expires_at_utc"], "durable_nonce_ledger_nonce_time_invalid")
            if expires <= now and row["state"] not in terminal_states:
                row["state"] = "closeout_required"
                row["transition_index"] += 1
                row["history"].append({"state": "closeout_required", "transition_index": row["transition_index"]})
                write_ledger(ledger, root, marker_hash, state, lock_binding, production)
                raise error_type("durable_nonce_ledger_nonce_expired_closeout_required")
            if not transition_allowed(row["state"], next_state):
                raise error_type("durable_nonce_ledger_transition_invalid")
            row["state"] = next_state
            row["transition_index"] += 1
            row["history"].append({"state": next_state, "transition_index": row["transition_index"]})
            write_ledger(ledger, root, marker_hash, state, lock_binding, production)
        finally:
            release_lock(lock, lock_binding, production=production)
        return ledger.snapshot()

    def create_test_ledger(root):
        # Test-only helper; its caller must supply a dedicated final directory name.
        return create_ledger(path_type(root))

    def acquire_test_lock(ledger):
        root, marker_hash, file_binding, ledger_sha256, _production = registered_entry(ledger)
        load_ledger(
            root,
            marker_hash,
            expected_file_identity=file_binding,
            expected_ledger_sha256=ledger_sha256,
            allow_lock=False,
        )
        _lock, lock_binding = acquire_lock(root, production=False)
        return lock_binding

    def validate_test_lock_binding(ledger, lock_binding):
        root, marker_hash, file_binding, ledger_sha256, _production = registered_entry(ledger)
        load_ledger(
            root,
            marker_hash,
            expected_file_identity=file_binding,
            expected_ledger_sha256=ledger_sha256,
            allow_lock=True,
            expected_lock_binding=lock_binding,
        )

    def release_test_lock(ledger, lock_binding):
        root, _marker_hash, _file_binding, _ledger_sha256, _production = registered_entry(ledger)
        release_lock(root / lock_name, lock_binding, production=False)

    def exercise_write_all_for_tests(raw, scripted_results):
        if type_fn(scripted_results) is not tuple_fn:
            raise type_error("durable_nonce_ledger_test_script_tuple_required")
        index = 0
        def scripted_write(_descriptor, _remaining):
            nonlocal index
            if index >= len_fn(scripted_results):
                raise OSError("script_exhausted")
            result = scripted_results[index]
            index += 1
            if result == "raise":
                raise OSError("scripted_write_failure")
            return result
        return write_all(
            0,
            raw,
            "durable_nonce_ledger_write_all_incomplete_closeout_required",
            write_fn=scripted_write,
        )

    return {
        "DurableNonceLedger": DurableNonceLedger,
        "open_durable_action_time_nonce_ledger_no_action": open_production_ledger_no_action,
        "reserve_action_time_nonce_no_action": reserve,
        "transition_action_time_nonce_no_action": transition,
        "_create_durable_nonce_ledger_for_tests": create_test_ledger,
        "_acquire_durable_nonce_ledger_lock_for_tests": acquire_test_lock,
        "_validate_durable_nonce_ledger_lock_binding_for_tests": validate_test_lock_binding,
        "_release_durable_nonce_ledger_lock_for_tests": release_test_lock,
        "_exercise_durable_nonce_ledger_write_all_for_tests": exercise_write_all_for_tests,
        "DURABLE_NONCE_LEDGER_STATES": states,
    }


_AUTHORITIES = _build_authorities()
globals().update(_AUTHORITIES)

__all__ = [
    "DurableNonceLedgerError", "DurableNonceLedger",
    "open_durable_action_time_nonce_ledger_no_action",
    "reserve_action_time_nonce_no_action", "transition_action_time_nonce_no_action",
    "DURABLE_NONCE_LEDGER_STATES",
]
