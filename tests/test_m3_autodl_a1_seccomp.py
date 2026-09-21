from __future__ import annotations

from copy import deepcopy
import ctypes.util
import hashlib
import inspect
import json
from pathlib import Path
import os
import subprocess
import sys
import textwrap
import unittest

import req2web_runtime.autodl_a1_seccomp as module
from req2web_runtime.autodl_a1_seccomp import (
    SeccompFilterArtifact,
    SeccompInstallationObservationRecord,
    SeccompNegativeProbeObservationRecord,
    SeccompProcessOfflineError,
    SeccompProcessOfflinePolicy,
    create_default_policy,
    parse_filter_bytes,
    parse_installation_observation_bytes,
    parse_policy_bytes,
    parse_negative_probe_observation_bytes,
    require_supported_platform,
    run_negative_probes,
)


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def identify(prefix: str, value: dict[str, object], key: str) -> str:
    root = deepcopy(value)
    root.pop(key, None)
    return prefix + hashlib.sha256(canonical(root)).hexdigest()[:20]


def make_filter(policy: SeccompProcessOfflinePolicy) -> SeccompFilterArtifact:
    data: dict[str, object] = {
        "schema_version": module.SECCOMP_FILTER_SCHEMA,
        "filter_id": "pending",
        "policy_id": policy.data["policy_id"],
        "policy_sha256": policy.sha256(),
        "architecture": "x86_64",
        "native_arch_token": 0xC000003E,
        "x32_probe_status": "ENOSYS",
        "libseccomp_soname": "libseccomp.so.2",
        "libseccomp_version": "2.5.5",
        "bpf_length": 8,
        "bpf_sha256": hashlib.sha256(b"12345678").hexdigest(),
        "compiler_identity": "libseccomp:2.5.5:libseccomp.so.2",
    }
    data["filter_id"] = identify("seccomp-filter-", data, "filter_id")
    return parse_filter_bytes(canonical(data))


def make_installation(
    policy: SeccompProcessOfflinePolicy,
    filter_artifact: SeccompFilterArtifact,
) -> SeccompInstallationObservationRecord:
    data: dict[str, object] = {
        "schema_version": module.SECCOMP_INSTALLATION_SCHEMA,
        "installation_id": "pending",
        "policy_id": policy.data["policy_id"],
        "policy_sha256": policy.sha256(),
        "filter_id": filter_artifact.data["filter_id"],
        "filter_sha256": filter_artifact.sha256(),
        "pid": 1,
        "observed_at_utc": "2026-07-26T00:00:00Z",
        "no_new_privs": True,
        "seccomp_mode": 2,
        "thread_ids": [1],
        "socket_fd_count": 0,
        "record_scope": "replay_only_non_authoritative",
        "requires_live_recheck": True,
        "manager_consumable": False,
        "a1_passed": False,
    }
    data["installation_id"] = identify("seccomp-installation-observation-", data, "installation_id")
    return parse_installation_observation_bytes(canonical(data))


def make_probe(
    policy: SeccompProcessOfflinePolicy,
    installation: SeccompInstallationObservationRecord,
) -> SeccompNegativeProbeObservationRecord:
    details = {
        "socket_create": "EPERM",
        "dns_lookup": "gaierror:-3",
        "connect_syscall": "EPERM",
        "bind_syscall": "EPERM",
        "listen_syscall": "EPERM",
        "socket_fd_inventory": "zero",
        "endpoint_observation": "no_process_owned_socket_endpoint",
    }
    data: dict[str, object] = {
        "schema_version": module.SECCOMP_PROBE_SCHEMA,
        "probe_id": "pending",
        "installation_id": installation.data["installation_id"],
        "policy_id": policy.data["policy_id"],
        "policy_sha256": policy.sha256(),
        "observed_at_utc": "2026-07-26T00:00:01Z",
        "results": [
            {
                "name": name,
                "status": "inconclusive_self_probe" if name == "dns_lookup" else "blocked",
                "detail": details[name],
            }
            for name in policy.data["negative_probes"]
        ],
        "socket_fd_count": 0,
        "record_scope": "replay_only_non_authoritative",
        "requires_live_recheck": True,
        "manager_consumable": False,
        "a1_passed": False,
    }
    data["probe_id"] = identify("seccomp-probe-observation-", data, "probe_id")
    return parse_negative_probe_observation_bytes(canonical(data))


class SeccompPolicyContractTests(unittest.TestCase):
    def rejected(self, fn) -> None:
        with self.assertRaises(SeccompProcessOfflineError):
            fn()

    def test_default_policy_is_canonical_and_frozen(self) -> None:
        policy = create_default_policy()
        replay = parse_policy_bytes(policy.canonical_bytes())
        self.assertEqual(replay.canonical_bytes(), policy.canonical_bytes())
        self.assertEqual(policy.data["profile"], "seccomp_process_offline_v1")
        self.assertEqual(policy.data["architecture"], "x86_64")
        self.assertEqual(policy.data["x32_abi_policy"], "require_preload_ENOSYS_probe")
        self.assertEqual(policy.data["fd_policy"], "reject_all_socket_fds")
        self.assertEqual(policy.data["installation_thread_policy"], "single_thread_before_model_load")
        self.assertIn("socket", policy.data["denied_syscalls"])
        self.assertIn("io_uring_enter", policy.data["denied_syscalls"])
        self.assertIn("pidfd_getfd", policy.data["denied_syscalls"])
        self.assertIn("endpoint_observation", policy.data["negative_probes"])

    def test_direct_constructor_and_unregistered_object_are_rejected(self) -> None:
        for artifact_type in (
            SeccompProcessOfflinePolicy,
            SeccompFilterArtifact,
            SeccompInstallationObservationRecord,
            SeccompNegativeProbeObservationRecord,
        ):
            self.rejected(artifact_type)
            forged = object.__new__(artifact_type)
            self.rejected(lambda forged=forged: forged.canonical_bytes())

    def test_policy_tamper_and_noncanonical_bytes_fail_closed(self) -> None:
        policy = create_default_policy()
        base = policy.to_dict()
        variants = []
        changed_profile = deepcopy(base)
        changed_profile["profile"] = "netns"
        variants.append(changed_profile)
        reordered = deepcopy(base)
        reordered["denied_syscalls"] = list(reversed(reordered["denied_syscalls"]))
        variants.append(reordered)
        widened = deepcopy(base)
        widened["fd_policy"] = "allow_unix_sockets"
        variants.append(widened)
        manager_claim = deepcopy(base)
        manager_claim["unsupported_platform_behavior"] = "fallback"
        variants.append(manager_claim)
        for value in variants:
            value["policy_id"] = identify("seccomp-policy-", value, "policy_id")
            self.rejected(lambda value=value: parse_policy_bytes(canonical(value)))
        self.rejected(lambda: parse_policy_bytes(policy.canonical_bytes() + b"\n"))
        self.rejected(lambda: parse_policy_bytes(bytearray(policy.canonical_bytes())))

    def test_definition_time_authority_survives_module_rebinding(self) -> None:
        expected = create_default_policy().canonical_bytes()
        saved = (
            module.SECCOMP_PROFILE,
            module.SECCOMP_ARCHITECTURE,
            module.SECCOMP_LIBRARY_SONAME,
            module._DENIED_SYSCALLS,
            module._NEGATIVE_PROBES,
            module._AUTHORITIES,
        )
        try:
            module.SECCOMP_PROFILE = "forged"
            module.SECCOMP_ARCHITECTURE = "forged"
            module.SECCOMP_LIBRARY_SONAME = "forged.so"
            module._DENIED_SYSCALLS = ("read",)
            module._NEGATIVE_PROBES = ("none",)
            module._AUTHORITIES = {}
            self.assertEqual(create_default_policy().canonical_bytes(), expected)
            self.assertEqual(parse_policy_bytes(expected).canonical_bytes(), expected)
        finally:
            (
                module.SECCOMP_PROFILE,
                module.SECCOMP_ARCHITECTURE,
                module.SECCOMP_LIBRARY_SONAME,
                module._DENIED_SYSCALLS,
                module._NEGATIVE_PROBES,
                module._AUTHORITIES,
            ) = saved

    def test_filter_installation_and_probe_artifacts_are_strict_no_action_records(self) -> None:
        policy = create_default_policy()
        filter_artifact = make_filter(policy)
        installation = make_installation(policy, filter_artifact)
        probe = make_probe(policy, installation)
        self.assertEqual(parse_filter_bytes(filter_artifact.canonical_bytes()).sha256(), filter_artifact.sha256())
        self.assertEqual(parse_installation_observation_bytes(installation.canonical_bytes()).sha256(), installation.sha256())
        self.assertEqual(parse_negative_probe_observation_bytes(probe.canonical_bytes()).sha256(), probe.sha256())
        self.assertFalse(installation.data["manager_consumable"])
        self.assertFalse(installation.data["a1_passed"])
        self.assertFalse(probe.data["manager_consumable"])
        self.assertFalse(probe.data["a1_passed"])
        self.rejected(lambda: run_negative_probes(policy, installation))

    def test_artifact_tamper_fails_closed(self) -> None:
        policy = create_default_policy()
        filter_artifact = make_filter(policy)
        installation = make_installation(policy, filter_artifact)
        probe = make_probe(policy, installation)
        changes = []
        bad_filter = filter_artifact.to_dict()
        bad_filter["bpf_length"] = 7
        changes.append((bad_filter, parse_filter_bytes, "filter_id", "seccomp-filter-"))
        bad_install = installation.to_dict()
        bad_install["manager_consumable"] = True
        changes.append((bad_install, parse_installation_observation_bytes, "installation_id", "seccomp-installation-observation-"))
        bad_probe = probe.to_dict()
        bad_probe["results"][0]["status"] = "pass"
        changes.append((bad_probe, parse_negative_probe_observation_bytes, "probe_id", "seccomp-probe-observation-"))
        for value, parser, key, prefix in changes:
            value[key] = identify(prefix, value, key)
            self.rejected(lambda value=value, parser=parser: parser(canonical(value)))

    def test_final_load_context_identity_is_verified_before_seccomp_load(self) -> None:
        policy = create_default_policy()
        filter_artifact = make_filter(policy)
        compiled_data = filter_artifact.to_dict()
        expected_bpf = b"12345678"
        module._validate_load_candidate_identity(
            expected_bpf=expected_bpf,
            actual_bpf=expected_bpf,
            compiled_data=compiled_data,
            policy_id=policy.data["policy_id"],
            policy_sha256=policy.sha256(),
            x32_status="ENOSYS",
            library_version="2.5.5",
            library_soname="libseccomp.so.2",
        )

        variants = []
        variants.append({"actual_bpf": b"87654321"})
        wrong_hash = deepcopy(compiled_data)
        wrong_hash["bpf_sha256"] = "0" * 64
        variants.append({"compiled_data": wrong_hash})
        variants.append({"policy_sha256": "0" * 64})
        variants.append({"x32_status": "available"})
        variants.append({"library_version": "9.9.9"})
        base = {
            "expected_bpf": expected_bpf,
            "actual_bpf": expected_bpf,
            "compiled_data": compiled_data,
            "policy_id": policy.data["policy_id"],
            "policy_sha256": policy.sha256(),
            "x32_status": "ENOSYS",
            "library_version": "2.5.5",
            "library_soname": "libseccomp.so.2",
        }
        for changes in variants:
            candidate = dict(base)
            candidate.update(changes)
            self.rejected(lambda candidate=candidate: module._validate_load_candidate_identity(**candidate))

        source = inspect.getsource(module.install_filter)
        export_index = source.index("actual_bpf = _export_bpf(library, context)")
        verify_index = source.index("validate_load_candidate_identity(")
        load_index = source.index("library.seccomp_load(context)")
        self.assertLess(export_index, verify_index)
        self.assertLess(verify_index, load_index)
        self.assertNotIn("compile_filter(policy)", source)

    def test_public_actions_reject_fake_objects_before_platform_or_runtime_use(self) -> None:
        class Fake:
            def canonical_bytes(self) -> bytes:
                return create_default_policy().canonical_bytes()

        self.rejected(lambda: module.compile_filter(Fake()))
        self.rejected(lambda: module.install_filter(Fake(), Fake(), b"12345678"))
        self.rejected(lambda: run_negative_probes(create_default_policy(), Fake()))

    @unittest.skipIf(sys.platform == "linux", "non-Linux fail-closed check")
    def test_non_linux_compile_and_platform_gate_fail_closed(self) -> None:
        self.rejected(require_supported_platform)
        self.rejected(lambda: module.compile_filter(create_default_policy()))

    @unittest.skipUnless(
        sys.platform == "linux" and ctypes.util.find_library("seccomp"),
        "Linux libseccomp integration unavailable",
    )
    def test_linux_filter_install_and_negative_probes_in_dedicated_child(self) -> None:
        script = textwrap.dedent(
            """
            import json
            import req2web_runtime.autodl_a1_seccomp as m

            policy = m.create_default_policy()
            filter_artifact, bpf = m.compile_filter(policy)
            installation = m.install_filter(policy, filter_artifact, bpf)
            observation = m.run_negative_probes(policy, installation)
            print(json.dumps({
                "profile": policy.data["profile"],
                "bpf_length": filter_artifact.data["bpf_length"],
                "no_new_privs": installation.data["no_new_privs"],
                "seccomp_mode": installation.data["seccomp_mode"],
                "socket_fd_count": observation.data["socket_fd_count"],
                "statuses": {
                    row["name"]: row["status"]
                    for row in observation.data["results"]
                },
                "manager_consumable": observation.data["manager_consumable"],
                "a1_passed": observation.data["a1_passed"],
            }, sort_keys=True))
            """
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        completed = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["profile"], "seccomp_process_offline_v1")
        self.assertGreater(payload["bpf_length"], 0)
        self.assertTrue(payload["no_new_privs"])
        self.assertEqual(payload["seccomp_mode"], 2)
        self.assertEqual(payload["socket_fd_count"], 0)
        self.assertEqual(payload["statuses"]["dns_lookup"], "inconclusive_self_probe")
        self.assertEqual(
            {status for name, status in payload["statuses"].items() if name != "dns_lookup"},
            {"blocked"},
        )
        self.assertFalse(payload["manager_consumable"])
        self.assertFalse(payload["a1_passed"])

    def test_public_action_signatures_expose_no_trust_override(self) -> None:
        for action in (
            create_default_policy,
            parse_policy_bytes,
            parse_filter_bytes,
            parse_installation_observation_bytes,
            parse_negative_probe_observation_bytes,
            module.compile_filter,
            module.install_filter,
            run_negative_probes,
            require_supported_platform,
        ):
            parameters = inspect.signature(action).parameters
            self.assertNotIn("_authority", parameters)
            self.assertNotIn("validator", parameters)
            self.assertNotIn("callback", parameters)

    def test_source_import_is_no_action(self) -> None:
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "subprocess.Popen(",
            "requests.",
            "paramiko",
            "AutoModelForImageTextToText",
            "from_pretrained(",
            "unshare --net",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
