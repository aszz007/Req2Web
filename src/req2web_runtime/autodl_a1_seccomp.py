"""Linux-only seccomp-process-offline primitives for a future AutoDL A1 attempt.

Importing this module performs no privileged action, model load, network access,
process launch, repository transfer, or AutoDL action. Installing a filter is
an explicit, irreversible operation intended only for a dedicated child process
owned by a later reviewed launcher/observer slice.
"""
from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import stat
import sys
import weakref

SECCOMP_POLICY_SCHEMA = "req2web.runtime.seccomp_process_offline_policy.v1"
SECCOMP_FILTER_SCHEMA = "req2web.runtime.seccomp_process_offline_filter.v1"
SECCOMP_INSTALLATION_SCHEMA = "req2web.runtime.seccomp_process_offline_installation_observation.v1"
SECCOMP_PROBE_SCHEMA = "req2web.runtime.seccomp_process_offline_negative_probe_observation.v1"
SECCOMP_PROFILE = "seccomp_process_offline_v1"
SECCOMP_LIBRARY_SONAME = "libseccomp.so.2"
SECCOMP_ARCHITECTURE = "x86_64"

_SCMP_ACT_ALLOW = 0x7FFF0000
_SCMP_ACT_KILL_PROCESS = 0x80000000
_SCMP_ACT_ERRNO_EPERM = 0x00050000 | errno.EPERM
_SCMP_FLTATR_ACT_BADARCH = 2
_SCMP_FLTATR_CTL_TSYNC = 4
_PR_SET_NO_NEW_PRIVS = 38
_PR_GET_NO_NEW_PRIVS = 39
_SECCOMP_MODE_FILTER = 2
_AUDIT_ARCH_X86_64 = 0xC000003E
_X32_SYSCALL_BIT = 0x40000000
_X86_64_GETPID_SYSCALL = 39

_DENIED_SYSCALLS = (
    "socket", "socketpair", "connect", "bind", "listen", "accept", "accept4",
    "sendto", "sendmsg", "sendmmsg", "recvfrom", "recvmsg", "recvmmsg",
    "shutdown", "getsockname", "getpeername", "setsockopt", "getsockopt",
    "io_uring_setup", "io_uring_enter", "io_uring_register", "bpf", "ptrace",
    "process_vm_readv", "process_vm_writev", "pidfd_getfd", "seccomp",
    "unshare", "setns",
)
_NEGATIVE_PROBES = (
    "socket_create", "dns_lookup", "connect_syscall", "bind_syscall",
    "listen_syscall", "socket_fd_inventory", "endpoint_observation",
)


class SeccompProcessOfflineError(ValueError):
    """Raised when the seccomp-process-offline contract cannot be trusted."""


def _validate_load_candidate_identity(
    *,
    expected_bpf: object,
    actual_bpf: object,
    compiled_data: object,
    policy_id: object,
    policy_sha256: object,
    x32_status: object,
    library_version: object,
    library_soname: object,
    _exact_type=type,
    _bytes_type=bytes,
    _dict_type=dict,
    _sha256_factory=hashlib.sha256,
    _error_type=SeccompProcessOfflineError,
) -> None:
    """Fail closed unless the exact exported load context matches its artifact."""
    if _exact_type(expected_bpf) is not _bytes_type or _exact_type(actual_bpf) is not _bytes_type:
        raise _error_type("seccomp_filter_bpf_type_invalid")
    if _exact_type(compiled_data) is not _dict_type:
        raise _error_type("seccomp_filter_data_invalid")
    if actual_bpf != expected_bpf:
        raise _error_type("seccomp_final_context_bpf_mismatch")
    if compiled_data.get("policy_id") != policy_id or compiled_data.get("policy_sha256") != policy_sha256:
        raise _error_type("seccomp_final_context_policy_mismatch")
    if compiled_data.get("bpf_length") != len(actual_bpf):
        raise _error_type("seccomp_final_context_bpf_length_mismatch")
    if compiled_data.get("bpf_sha256") != _sha256_factory(actual_bpf).hexdigest():
        raise _error_type("seccomp_final_context_bpf_sha256_mismatch")
    if compiled_data.get("x32_probe_status") != x32_status:
        raise _error_type("seccomp_final_context_x32_mismatch")
    if compiled_data.get("libseccomp_version") != library_version:
        raise _error_type("seccomp_final_context_library_version_mismatch")
    if compiled_data.get("compiler_identity") != f"libseccomp:{library_version}:{library_soname}":
        raise _error_type("seccomp_final_context_compiler_identity_mismatch")


def _build_authorities():
    canonical_json = json.dumps
    parse_json = json.loads
    json_decode_error = json.JSONDecodeError
    sha256_factory = hashlib.sha256
    exact_type = type
    dict_type = dict
    list_type = list
    tuple_type = tuple
    str_type = str
    int_type = int
    bool_type = bool
    bytes_type = bytes
    unicode_decode_error = UnicodeDecodeError
    object_new = object.__new__
    weak_registry_type = weakref.WeakKeyDictionary
    error_type = SeccompProcessOfflineError
    schema_policy = SECCOMP_POLICY_SCHEMA
    schema_filter = SECCOMP_FILTER_SCHEMA
    schema_installation = SECCOMP_INSTALLATION_SCHEMA
    schema_probe = SECCOMP_PROBE_SCHEMA
    profile = SECCOMP_PROFILE
    library_soname = SECCOMP_LIBRARY_SONAME
    architecture = SECCOMP_ARCHITECTURE
    denied_syscalls = tuple(_DENIED_SYSCALLS)
    negative_probes = tuple(_NEGATIVE_PROBES)
    platform_system = platform.system
    platform_machine = platform.machine
    cdll = ctypes.CDLL
    c_void_p = ctypes.c_void_p
    c_uint32 = ctypes.c_uint32
    c_uint = ctypes.c_uint
    c_int = ctypes.c_int
    c_long = ctypes.c_long
    c_ulong = ctypes.c_ulong
    c_char_p = ctypes.c_char_p
    pointer = ctypes.POINTER
    structure = ctypes.Structure
    set_errno = ctypes.set_errno
    get_errno = ctypes.get_errno
    os_pipe = os.pipe
    os_read = os.read
    os_close = os.close
    os_readlink = os.readlink
    os_listdir = os.listdir
    os_lstat = os.lstat
    path_type = Path
    getpid = os.getpid
    socket_module = socket
    stat_islink = stat.S_ISLNK
    datetime_type = datetime
    timezone_utc = timezone.utc
    sys_platform = sys.platform
    eperm = errno.EPERM
    enosys = errno.ENOSYS
    re_fullmatch = re.fullmatch
    validate_load_candidate_identity = _validate_load_candidate_identity
    scmp_act_allow = _SCMP_ACT_ALLOW
    scmp_act_kill_process = _SCMP_ACT_KILL_PROCESS
    scmp_act_errno_eperm = _SCMP_ACT_ERRNO_EPERM
    scmp_attr_badarch = _SCMP_FLTATR_ACT_BADARCH
    scmp_attr_tsync = _SCMP_FLTATR_CTL_TSYNC
    pr_set_no_new_privs = _PR_SET_NO_NEW_PRIVS
    pr_get_no_new_privs = _PR_GET_NO_NEW_PRIVS
    seccomp_mode_filter = _SECCOMP_MODE_FILTER
    audit_arch_x86_64 = _AUDIT_ARCH_X86_64
    x32_syscall_bit = _X32_SYSCALL_BIT
    x86_64_getpid_syscall = _X86_64_GETPID_SYSCALL

    policy_keys = (
        "schema_version", "policy_id", "profile", "architecture",
        "libseccomp_soname", "default_action", "deny_action",
        "bad_arch_action", "x32_abi_policy", "required_no_new_privs",
        "required_tsync", "installation_thread_policy", "denied_syscalls", "fd_policy", "negative_probes",
        "unsupported_platform_behavior",
    )
    filter_keys = (
        "schema_version", "filter_id", "policy_id", "policy_sha256",
        "architecture", "native_arch_token", "x32_probe_status",
        "libseccomp_soname", "libseccomp_version", "bpf_length", "bpf_sha256",
        "compiler_identity",
    )
    installation_keys = (
        "schema_version", "installation_id", "policy_id", "policy_sha256",
        "filter_id", "filter_sha256", "pid", "observed_at_utc",
        "no_new_privs", "seccomp_mode", "thread_ids", "socket_fd_count",
        "record_scope", "requires_live_recheck", "manager_consumable", "a1_passed",
    )
    probe_keys = (
        "schema_version", "probe_id", "installation_id", "policy_id",
        "policy_sha256", "observed_at_utc", "results", "socket_fd_count",
        "record_scope", "requires_live_recheck", "manager_consumable", "a1_passed",
    )

    def canonical(value: object) -> bytes:
        return canonical_json(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode("utf-8")

    def digest(raw: bytes) -> str:
        return sha256_factory(raw).hexdigest()

    def identify(prefix: str, data: dict[str, object], key: str) -> str:
        root = dict_type(data)
        root.pop(key, None)
        return prefix + digest(canonical(root))[:20]

    def require_hash(value: object, code: str) -> str:
        if exact_type(value) is not str_type or re_fullmatch(r"[0-9a-f]{64}", value) is None:
            raise error_type(code)
        return value

    def require_id(value: object, prefix: str, code: str) -> str:
        if exact_type(value) is not str_type or not value.startswith(prefix) or re_fullmatch(r"[a-z0-9_.-]+", value) is None:
            raise error_type(code)
        return value

    def exact_map(value: object, keys: tuple[str, ...], code: str) -> dict[str, object]:
        if exact_type(value) is not dict_type or tuple_type(sorted(value)) != tuple_type(sorted(keys)):
            raise error_type(code)
        return dict_type(value)

    def exact_string_list(value: object, expected: tuple[str, ...], code: str) -> list[str]:
        if exact_type(value) is not list_type or tuple_type(value) != expected:
            raise error_type(code)
        if any(exact_type(item) is not str_type for item in value):
            raise error_type(code)
        return list_type(value)

    def validate_policy_data(value: object) -> dict[str, object]:
        data = exact_map(value, policy_keys, "seccomp_policy_keys_invalid")
        if data["schema_version"] != schema_policy:
            raise error_type("seccomp_policy_schema_invalid")
        if data["profile"] != profile or data["architecture"] != architecture:
            raise error_type("seccomp_policy_profile_invalid")
        if data["libseccomp_soname"] != library_soname:
            raise error_type("seccomp_policy_library_invalid")
        if (data["default_action"], data["deny_action"], data["bad_arch_action"]) != ("allow", "errno:EPERM", "kill_process"):
            raise error_type("seccomp_policy_action_invalid")
        if data["x32_abi_policy"] != "require_preload_ENOSYS_probe":
            raise error_type("seccomp_policy_x32_invalid")
        if data["required_no_new_privs"] is not True or data["required_tsync"] is not True:
            raise error_type("seccomp_policy_installation_requirement_invalid")
        if data["installation_thread_policy"] != "single_thread_before_model_load":
            raise error_type("seccomp_policy_thread_installation_invalid")
        exact_string_list(data["denied_syscalls"], denied_syscalls, "seccomp_policy_syscalls_invalid")
        if data["fd_policy"] != "reject_all_socket_fds" or data["unsupported_platform_behavior"] != "fail_closed":
            raise error_type("seccomp_policy_fd_or_platform_invalid")
        exact_string_list(data["negative_probes"], negative_probes, "seccomp_policy_probes_invalid")
        require_id(data["policy_id"], "seccomp-policy-", "seccomp_policy_id_invalid")
        if data["policy_id"] != identify("seccomp-policy-", data, "policy_id"):
            raise error_type("seccomp_policy_identity_invalid")
        return data

    def parse_canonical(raw: object, validator, code: str) -> dict[str, object]:
        if exact_type(raw) is not bytes_type:
            raise error_type(code)
        try:
            decoded = parse_json(raw.decode("utf-8"))
        except (unicode_decode_error, json_decode_error) as exc:
            raise error_type(code) from exc
        parsed = validator(decoded)
        if canonical(parsed) != raw:
            raise error_type(code + "_not_canonical")
        return parsed

    def validate_filter_data(value: object) -> dict[str, object]:
        data = exact_map(value, filter_keys, "seccomp_filter_keys_invalid")
        if data["schema_version"] != schema_filter or data["architecture"] != architecture:
            raise error_type("seccomp_filter_schema_or_arch_invalid")
        if data["native_arch_token"] != audit_arch_x86_64 or data["x32_probe_status"] != "ENOSYS":
            raise error_type("seccomp_filter_arch_probe_invalid")
        if data["libseccomp_soname"] != library_soname:
            raise error_type("seccomp_filter_library_invalid")
        require_id(data["filter_id"], "seccomp-filter-", "seccomp_filter_id_invalid")
        require_id(data["policy_id"], "seccomp-policy-", "seccomp_filter_policy_id_invalid")
        require_hash(data["policy_sha256"], "seccomp_filter_policy_sha_invalid")
        require_hash(data["bpf_sha256"], "seccomp_filter_bpf_sha_invalid")
        if exact_type(data["bpf_length"]) is not int_type or isinstance(data["bpf_length"], bool_type) or data["bpf_length"] <= 0 or data["bpf_length"] % 8 != 0:
            raise error_type("seccomp_filter_bpf_length_invalid")
        if exact_type(data["libseccomp_version"]) is not str_type or re_fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", data["libseccomp_version"]) is None:
            raise error_type("seccomp_filter_library_version_invalid")
        if data["compiler_identity"] != f"libseccomp:{data['libseccomp_version']}:{library_soname}":
            raise error_type("seccomp_filter_compiler_invalid")
        if data["filter_id"] != identify("seccomp-filter-", data, "filter_id"):
            raise error_type("seccomp_filter_identity_invalid")
        return data

    def validate_installation_data(value: object) -> dict[str, object]:
        data = exact_map(value, installation_keys, "seccomp_installation_keys_invalid")
        if data["schema_version"] != schema_installation:
            raise error_type("seccomp_installation_schema_invalid")
        require_id(data["installation_id"], "seccomp-installation-observation-", "seccomp_installation_id_invalid")
        require_id(data["policy_id"], "seccomp-policy-", "seccomp_installation_policy_invalid")
        require_id(data["filter_id"], "seccomp-filter-", "seccomp_installation_filter_invalid")
        require_hash(data["policy_sha256"], "seccomp_installation_hash_invalid")
        require_hash(data["filter_sha256"], "seccomp_installation_hash_invalid")
        if exact_type(data["pid"]) is not int_type or isinstance(data["pid"], bool_type) or data["pid"] <= 0:
            raise error_type("seccomp_installation_pid_invalid")
        if exact_type(data["observed_at_utc"]) is not str_type or re_fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", data["observed_at_utc"]) is None:
            raise error_type("seccomp_installation_time_invalid")
        if data["no_new_privs"] is not True or data["seccomp_mode"] != seccomp_mode_filter:
            raise error_type("seccomp_installation_state_invalid")
        tids = data["thread_ids"]
        if exact_type(tids) is not list_type or not tids or any(exact_type(item) is not int_type or item <= 0 for item in tids):
            raise error_type("seccomp_installation_threads_invalid")
        if tuple_type(tids) != tuple_type(sorted(tids)) or len(set(tids)) != len(tids):
            raise error_type("seccomp_installation_threads_invalid")
        if (
            data["socket_fd_count"] != 0
            or data["record_scope"] != "replay_only_non_authoritative"
            or data["requires_live_recheck"] is not True
            or data["manager_consumable"] is not False
            or data["a1_passed"] is not False
        ):
            raise error_type("seccomp_installation_claim_invalid")
        if data["installation_id"] != identify("seccomp-installation-observation-", data, "installation_id"):
            raise error_type("seccomp_installation_identity_invalid")
        return data

    def validate_probe_data(value: object) -> dict[str, object]:
        data = exact_map(value, probe_keys, "seccomp_probe_keys_invalid")
        if data["schema_version"] != schema_probe:
            raise error_type("seccomp_probe_schema_invalid")
        require_id(data["probe_id"], "seccomp-probe-observation-", "seccomp_probe_id_invalid")
        require_id(data["installation_id"], "seccomp-installation-observation-", "seccomp_probe_installation_invalid")
        require_id(data["policy_id"], "seccomp-policy-", "seccomp_probe_policy_invalid")
        require_hash(data["policy_sha256"], "seccomp_probe_policy_sha_invalid")
        if exact_type(data["observed_at_utc"]) is not str_type or re_fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", data["observed_at_utc"]) is None:
            raise error_type("seccomp_probe_time_invalid")
        rows = data["results"]
        if exact_type(rows) is not list_type or len(rows) != len(negative_probes):
            raise error_type("seccomp_probe_results_invalid")
        names = []
        for row in rows:
            item = exact_map(row, ("name", "status", "detail"), "seccomp_probe_row_invalid")
            if any(exact_type(item[key]) is not str_type for key in ("name", "status", "detail")):
                raise error_type("seccomp_probe_row_invalid")
            expected_status = "inconclusive_self_probe" if item["name"] == "dns_lookup" else "blocked"
            if item["status"] != expected_status:
                raise error_type("seccomp_probe_row_invalid")
            names.append(item["name"])
        if tuple_type(names) != negative_probes:
            raise error_type("seccomp_probe_order_invalid")
        if (
            data["socket_fd_count"] != 0
            or data["record_scope"] != "replay_only_non_authoritative"
            or data["requires_live_recheck"] is not True
            or data["manager_consumable"] is not False
            or data["a1_passed"] is not False
        ):
            raise error_type("seccomp_probe_claim_invalid")
        if data["probe_id"] != identify("seccomp-probe-observation-", data, "probe_id"):
            raise error_type("seccomp_probe_identity_invalid")
        return data

    policy_registry = weak_registry_type()
    filter_registry = weak_registry_type()
    installation_registry = weak_registry_type()
    probe_registry = weak_registry_type()

    def registered_type(name: str, registry, validator, parse_code: str):
        class RegisteredArtifact:
            __slots__ = ("__weakref__",)

            def __new__(cls, *args, **kwargs):
                raise error_type(parse_code + "_direct_constructor_invalid")

            @property
            def data(self) -> dict[str, object]:
                raw = registry.get(self)
                if raw is None:
                    raise error_type(parse_code + "_not_registered")
                return validator(parse_json(raw.decode("utf-8")))

            def to_dict(self) -> dict[str, object]:
                return dict_type(self.data)

            def canonical_bytes(self) -> bytes:
                raw = registry.get(self)
                if raw is None or canonical(self.data) != raw:
                    raise error_type(parse_code + "_registry_drift")
                return raw

            def sha256(self) -> str:
                return digest(self.canonical_bytes())

        RegisteredArtifact.__name__ = name
        return RegisteredArtifact

    SeccompProcessOfflinePolicy = registered_type("SeccompProcessOfflinePolicy", policy_registry, validate_policy_data, "seccomp_policy")
    SeccompFilterArtifact = registered_type("SeccompFilterArtifact", filter_registry, validate_filter_data, "seccomp_filter")
    SeccompInstallationObservationRecord = registered_type("SeccompInstallationObservationRecord", installation_registry, validate_installation_data, "seccomp_installation")
    SeccompNegativeProbeObservationRecord = registered_type("SeccompNegativeProbeObservationRecord", probe_registry, validate_probe_data, "seccomp_probe")

    def register(registry, cls, validator, data: dict[str, object]):
        parsed = validator(data)
        raw = canonical(parsed)
        obj = object_new(cls)
        registry[obj] = raw
        return obj

    def parse_registered(raw: object, registry, cls, validator, code: str):
        parsed = parse_canonical(raw, validator, code)
        return register(registry, cls, validator, parsed)

    def policy_from_object(value: object):
        if exact_type(value) is not SeccompProcessOfflinePolicy:
            raise error_type("seccomp_policy_object_invalid")
        return parse_registered(value.canonical_bytes(), policy_registry, SeccompProcessOfflinePolicy, validate_policy_data, "seccomp_policy_bytes_invalid")

    def filter_from_object(value: object):
        if exact_type(value) is not SeccompFilterArtifact:
            raise error_type("seccomp_filter_object_invalid")
        return parse_registered(value.canonical_bytes(), filter_registry, SeccompFilterArtifact, validate_filter_data, "seccomp_filter_bytes_invalid")


    def utc_now() -> str:
        return datetime_type.now(timezone_utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def ensure_supported_platform() -> None:
        if sys_platform != "linux" or platform_system() != "Linux" or platform_machine().lower() not in ("x86_64", "amd64"):
            raise error_type("seccomp_profile_platform_unsupported")

    def new_default_policy():
        data: dict[str, object] = {
            "schema_version": schema_policy,
            "policy_id": "pending",
            "profile": profile,
            "architecture": architecture,
            "libseccomp_soname": library_soname,
            "default_action": "allow",
            "deny_action": "errno:EPERM",
            "bad_arch_action": "kill_process",
            "x32_abi_policy": "require_preload_ENOSYS_probe",
            "required_no_new_privs": True,
            "required_tsync": True,
            "installation_thread_policy": "single_thread_before_model_load",
            "denied_syscalls": list_type(denied_syscalls),
            "fd_policy": "reject_all_socket_fds",
            "negative_probes": list_type(negative_probes),
            "unsupported_platform_behavior": "fail_closed",
        }
        data["policy_id"] = identify("seccomp-policy-", data, "policy_id")
        return register(policy_registry, SeccompProcessOfflinePolicy, validate_policy_data, data)

    def parse_policy_bytes(raw: object):
        return parse_registered(raw, policy_registry, SeccompProcessOfflinePolicy,
                                validate_policy_data, "seccomp_policy_bytes_invalid")

    def parse_filter_bytes(raw: object):
        return parse_registered(raw, filter_registry, SeccompFilterArtifact,
                                validate_filter_data, "seccomp_filter_bytes_invalid")

    def parse_installation_observation_bytes(raw: object):
        return parse_registered(raw, installation_registry, SeccompInstallationObservationRecord,
                                validate_installation_data, "seccomp_installation_bytes_invalid")

    def parse_negative_probe_observation_bytes(raw: object):
        return parse_registered(raw, probe_registry, SeccompNegativeProbeObservationRecord,
                                validate_probe_data, "seccomp_probe_bytes_invalid")

    def _library_version(library) -> str:
        class ScmpVersion(structure):
            _fields_ = (("major", c_uint), ("minor", c_uint), ("micro", c_uint))

        library.seccomp_version.argtypes = []
        library.seccomp_version.restype = pointer(ScmpVersion)
        version = library.seccomp_version()
        if not version:
            raise error_type("seccomp_library_version_unavailable")
        return f"{version.contents.major}.{version.contents.minor}.{version.contents.micro}"

    def _load_library():
        ensure_supported_platform()
        try:
            library = cdll(library_soname, use_errno=True)
        except OSError as exc:
            raise error_type("seccomp_library_unavailable") from exc
        library.seccomp_init.argtypes = [c_uint32]
        library.seccomp_init.restype = c_void_p
        library.seccomp_release.argtypes = [c_void_p]
        library.seccomp_release.restype = None
        library.seccomp_attr_set.argtypes = [c_void_p, c_int, c_uint32]
        library.seccomp_attr_set.restype = c_int
        library.seccomp_rule_add.argtypes = [c_void_p, c_uint32, c_int, c_uint]
        library.seccomp_rule_add.restype = c_int
        library.seccomp_syscall_resolve_name.argtypes = [c_char_p]
        library.seccomp_syscall_resolve_name.restype = c_int
        library.seccomp_export_bpf.argtypes = [c_void_p, c_int]
        library.seccomp_export_bpf.restype = c_int
        library.seccomp_load.argtypes = [c_void_p]
        library.seccomp_load.restype = c_int
        library.seccomp_arch_native.argtypes = []
        library.seccomp_arch_native.restype = c_uint32
        return library

    def _require_x32_unavailable() -> str:
        libc = cdll(None, use_errno=True)
        set_errno(0)
        value = libc.syscall(c_long(x32_syscall_bit | x86_64_getpid_syscall))
        call_errno = get_errno()
        if value != -1 or call_errno != enosys:
            raise error_type("seccomp_x32_abi_available_or_unverifiable")
        return "ENOSYS"

    def _build_context(library):
        if library.seccomp_arch_native() != audit_arch_x86_64:
            raise error_type("seccomp_native_arch_unexpected")
        context = library.seccomp_init(scmp_act_allow)
        if not context:
            raise error_type("seccomp_context_init_failed")
        try:
            if library.seccomp_attr_set(context, scmp_attr_badarch, scmp_act_kill_process) != 0:
                raise error_type("seccomp_badarch_policy_failed")
            if library.seccomp_attr_set(context, scmp_attr_tsync, 1) != 0:
                raise error_type("seccomp_tsync_policy_failed")
            for syscall_name in denied_syscalls:
                number = library.seccomp_syscall_resolve_name(syscall_name.encode("ascii"))
                if number < 0:
                    raise error_type("seccomp_syscall_unavailable:" + syscall_name)
                if library.seccomp_rule_add(context, scmp_act_errno_eperm, number, 0) != 0:
                    raise error_type("seccomp_rule_install_failed:" + syscall_name)
            return context
        except Exception:
            library.seccomp_release(context)
            raise

    def _export_bpf(library, context) -> bytes:
        read_fd, write_fd = os_pipe()
        try:
            if library.seccomp_export_bpf(context, write_fd) != 0:
                raise error_type("seccomp_bpf_export_failed")
        finally:
            os_close(write_fd)
        try:
            chunks = []
            while True:
                chunk = os_read(read_fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
        finally:
            os_close(read_fd)
        if not raw:
            raise error_type("seccomp_bpf_empty")
        return raw

    def compile_filter(policy_object: object):
        policy = policy_from_object(policy_object)
        policy_data = policy.data
        ensure_supported_platform()
        x32_status = _require_x32_unavailable()
        library = _load_library()
        context = _build_context(library)
        try:
            bpf = _export_bpf(library, context)
            version = _library_version(library)
        finally:
            library.seccomp_release(context)
        data: dict[str, object] = {
            "schema_version": schema_filter,
            "filter_id": "pending",
            "policy_id": policy_data["policy_id"],
            "policy_sha256": policy.sha256(),
            "architecture": architecture,
            "native_arch_token": audit_arch_x86_64,
            "x32_probe_status": x32_status,
            "libseccomp_soname": library_soname,
            "libseccomp_version": version,
            "bpf_length": len(bpf),
            "bpf_sha256": digest(bpf),
            "compiler_identity": f"libseccomp:{version}:{library_soname}",
        }
        data["filter_id"] = identify("seccomp-filter-", data, "filter_id")
        return register(filter_registry, SeccompFilterArtifact, validate_filter_data, data), bpf

    def _status_values() -> dict[str, str]:
        text = path_type("/proc/self/status").read_text(encoding="utf-8")
        values: dict[str, str] = {}
        for line in text.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                values[key] = value.strip()
        return values

    def _thread_ids() -> list[int]:
        root = path_type("/proc/self/task")
        identifiers = []
        for child in root.iterdir():
            if not child.name.isdigit():
                raise error_type("seccomp_thread_identifier_invalid")
            identifiers.append(int_type(child.name))
        if not identifiers:
            raise error_type("seccomp_thread_inventory_empty")
        return sorted(identifiers)

    def _socket_fds() -> list[int]:
        found = []
        directory = "/proc/self/fd"
        for item in os_listdir(directory):
            if not item.isdigit():
                continue
            location = directory + "/" + item
            try:
                link = os_readlink(location)
            except FileNotFoundError:
                continue
            if link.startswith("socket:["):
                found.append(int_type(item))
        return sorted(found)

    def _verify_runtime_observation() -> tuple[list[int], int, int]:
        status = _status_values()
        try:
            no_new_privs = int_type(status["NoNewPrivs"])
            seccomp_mode = int_type(status["Seccomp"])
        except (KeyError, ValueError) as exc:
            raise error_type("seccomp_status_missing") from exc
        if no_new_privs != 1 or seccomp_mode != seccomp_mode_filter:
            raise error_type("seccomp_status_not_enforced")
        fds = _socket_fds()
        if fds:
            raise error_type("seccomp_inherited_socket_fd_detected")
        return _thread_ids(), no_new_privs, seccomp_mode

    def install_filter(policy_object: object, filter_object: object, expected_bpf: object):
        policy = policy_from_object(policy_object)
        compiled = filter_from_object(filter_object)
        if exact_type(expected_bpf) is not bytes_type:
            raise error_type("seccomp_filter_bpf_type_invalid")
        policy_data = policy.data
        compiled_data = compiled.data
        ensure_supported_platform()
        x32_status = _require_x32_unavailable()
        library = _load_library()
        library_version = _library_version(library)
        context = _build_context(library)
        try:
            # Export and verify the exact same context that will be loaded.
            actual_bpf = _export_bpf(library, context)
            validate_load_candidate_identity(
                expected_bpf=expected_bpf,
                actual_bpf=actual_bpf,
                compiled_data=compiled_data,
                policy_id=policy_data["policy_id"],
                policy_sha256=policy.sha256(),
                x32_status=x32_status,
                library_version=library_version,
                library_soname=library_soname,
            )
            preinstall_threads = _thread_ids()
            if len(preinstall_threads) != 1:
                raise error_type("seccomp_preinstall_single_thread_required")
            if _socket_fds():
                raise error_type("seccomp_preexisting_socket_fd_detected")
            libc = cdll(None, use_errno=True)
            if libc.prctl(c_int(pr_set_no_new_privs), c_ulong(1), c_ulong(0), c_ulong(0), c_ulong(0)) != 0:
                raise error_type("seccomp_no_new_privs_install_failed")
            if libc.prctl(c_int(pr_get_no_new_privs), c_ulong(0), c_ulong(0), c_ulong(0), c_ulong(0)) != 1:
                raise error_type("seccomp_no_new_privs_unverified")
            if library.seccomp_load(context) != 0:
                raise error_type("seccomp_load_failed")
        finally:
            library.seccomp_release(context)
        thread_ids, no_new_privs, seccomp_mode = _verify_runtime_observation()
        data: dict[str, object] = {
            "schema_version": schema_installation,
            "installation_id": "pending",
            "policy_id": policy.data["policy_id"],
            "policy_sha256": policy.sha256(),
            "filter_id": compiled.data["filter_id"],
            "filter_sha256": compiled.sha256(),
            "pid": getpid(),
            "observed_at_utc": utc_now(),
            "no_new_privs": no_new_privs == 1,
            "seccomp_mode": seccomp_mode,
            "thread_ids": thread_ids,
            "socket_fd_count": 0,
            "record_scope": "replay_only_non_authoritative",
            "requires_live_recheck": True,
            "manager_consumable": False,
            "a1_passed": False,
        }
        data["installation_id"] = identify("seccomp-installation-observation-", data, "installation_id")
        return register(installation_registry, SeccompInstallationObservationRecord, validate_installation_data, data)

    def _blocked_socket_create() -> str:
        try:
            candidate = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
        except OSError as exc:
            if exc.errno == eperm:
                return "EPERM"
            raise error_type("seccomp_socket_probe_wrong_errno") from exc
        else:
            candidate.close()
            raise error_type("seccomp_socket_probe_not_blocked")

    def _dns_lookup_self_probe() -> str:
        try:
            socket_module.getaddrinfo("req2web-offline.invalid", 443, type=socket_module.SOCK_STREAM)
        except OSError as exc:
            # Resolver failures can be NXDOMAIN, NSS behavior, or a socket
            # denial. This local record deliberately does not claim causation.
            return f"{exact_type(exc).__name__}:{getattr(exc, 'errno', None)}"
        raise error_type("seccomp_dns_probe_unexpected_success")

    def _blocked_syscall(number: int, label: str) -> str:
        libc = cdll(None, use_errno=True)
        set_errno(0)
        result = libc.syscall(c_long(number), c_long(-1), c_void_p(), c_ulong(0))
        if result != -1 or get_errno() != eperm:
            raise error_type("seccomp_" + label + "_probe_not_blocked")
        return "EPERM"

    def run_negative_probes(policy_object: object, installation_object: object):
        if exact_type(installation_object) is not SeccompInstallationObservationRecord:
            raise error_type("seccomp_installation_object_invalid")
        policy = policy_from_object(policy_object)
        installation = parse_installation_observation_bytes(installation_object.canonical_bytes())
        if installation.data["policy_id"] != policy.data["policy_id"] or installation.data["policy_sha256"] != policy.sha256():
            raise error_type("seccomp_probe_installation_policy_mismatch")
        if installation.data["pid"] != getpid():
            raise error_type("seccomp_probe_installation_pid_mismatch")
        live_threads, live_nnp, live_mode = _verify_runtime_observation()
        if (installation.data["thread_ids"], installation.data["no_new_privs"], installation.data["seccomp_mode"]) != (live_threads, live_nnp == 1, live_mode):
            raise error_type("seccomp_probe_installation_live_state_mismatch")
        details = (
            ("socket_create", "blocked", _blocked_socket_create()),
            ("dns_lookup", "inconclusive_self_probe", _dns_lookup_self_probe()),
            ("connect_syscall", "blocked", _blocked_syscall(42, "connect")),
            ("bind_syscall", "blocked", _blocked_syscall(49, "bind")),
            ("listen_syscall", "blocked", _blocked_syscall(50, "listen")),
            ("socket_fd_inventory", "blocked", "zero"),
            ("endpoint_observation", "blocked", "no_process_owned_socket_endpoint"),
        )
        rows = [{"name": name, "status": status, "detail": detail} for name, status, detail in details]
        data: dict[str, object] = {
            "schema_version": schema_probe,
            "probe_id": "pending",
            "installation_id": installation.data["installation_id"],
            "policy_id": policy.data["policy_id"],
            "policy_sha256": policy.sha256(),
            "observed_at_utc": utc_now(),
            "results": rows,
            "socket_fd_count": 0,
            "record_scope": "replay_only_non_authoritative",
            "requires_live_recheck": True,
            "manager_consumable": False,
            "a1_passed": False,
        }
        data["probe_id"] = identify("seccomp-probe-observation-", data, "probe_id")
        return register(probe_registry, SeccompNegativeProbeObservationRecord, validate_probe_data, data)

    return {
        "SeccompProcessOfflinePolicy": SeccompProcessOfflinePolicy,
        "SeccompFilterArtifact": SeccompFilterArtifact,
        "SeccompInstallationObservationRecord": SeccompInstallationObservationRecord,
        "SeccompNegativeProbeObservationRecord": SeccompNegativeProbeObservationRecord,
        "create_default_policy": new_default_policy,
        "parse_policy_bytes": parse_policy_bytes,
        "parse_filter_bytes": parse_filter_bytes,
        "parse_installation_observation_bytes": parse_installation_observation_bytes,
        "parse_negative_probe_observation_bytes": parse_negative_probe_observation_bytes,
        "compile_filter": compile_filter,
        "install_filter": install_filter,
        "run_negative_probes": run_negative_probes,
        "supported_platform": ensure_supported_platform,
    }


_AUTHORITIES = _build_authorities()

SeccompProcessOfflinePolicy = _AUTHORITIES["SeccompProcessOfflinePolicy"]
SeccompFilterArtifact = _AUTHORITIES["SeccompFilterArtifact"]
SeccompInstallationObservationRecord = _AUTHORITIES["SeccompInstallationObservationRecord"]
SeccompNegativeProbeObservationRecord = _AUTHORITIES["SeccompNegativeProbeObservationRecord"]
create_default_policy = _AUTHORITIES["create_default_policy"]
parse_policy_bytes = _AUTHORITIES["parse_policy_bytes"]
parse_filter_bytes = _AUTHORITIES["parse_filter_bytes"]
parse_installation_observation_bytes = _AUTHORITIES["parse_installation_observation_bytes"]
parse_negative_probe_observation_bytes = _AUTHORITIES["parse_negative_probe_observation_bytes"]
compile_filter = _AUTHORITIES["compile_filter"]
install_filter = _AUTHORITIES["install_filter"]
run_negative_probes = _AUTHORITIES["run_negative_probes"]
require_supported_platform = _AUTHORITIES["supported_platform"]

__all__ = (
    "SECCOMP_POLICY_SCHEMA",
    "SECCOMP_FILTER_SCHEMA",
    "SECCOMP_INSTALLATION_SCHEMA",
    "SECCOMP_PROBE_SCHEMA",
    "SECCOMP_PROFILE",
    "SeccompProcessOfflineError",
    "SeccompProcessOfflinePolicy",
    "SeccompFilterArtifact",
    "SeccompInstallationObservationRecord",
    "SeccompNegativeProbeObservationRecord",
    "create_default_policy",
    "parse_policy_bytes",
    "parse_filter_bytes",
    "parse_installation_observation_bytes",
    "parse_negative_probe_observation_bytes",
    "compile_filter",
    "install_filter",
    "run_negative_probes",
    "require_supported_platform",
)
