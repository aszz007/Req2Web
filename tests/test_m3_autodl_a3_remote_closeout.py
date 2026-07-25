from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime.autodl_a3_closeout as foundation
import req2web_runtime.autodl_a3_remote_closeout as remote
from tests import test_m3_autodl_a1_production_gate as gate_fixtures


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def reidentify(value, field, prefix):
    value[field] = prefix + digest(canonical({key: item for key, item in value.items() if key != field}))[:20]


def h(text):
    return digest(text.encode("utf-8"))


def row(name, raw):
    return {"relative_path_sha256": h(name), "kind": "file", "bytes": len(raw), "sha256": digest(raw)}


def directory_row(name):
    return {"relative_path_sha256": h(name), "kind": "directory", "bytes": 0, "sha256": "0" * 64}


def root_contract(role, normalized_path, marker, rows):
    rows = sorted(rows, key=lambda item: item["relative_path_sha256"])
    files = [item for item in rows if item["kind"] == "file"]
    directories = [item for item in rows if item["kind"] == "directory"]
    return {
        "role": role,
        "normalized_path_sha256": remote._path_hash_for_tests(normalized_path),
        "marker_id": json.loads(marker.decode("utf-8"))["marker_id"],
        "marker_sha256": digest(marker),
        "inventory": rows,
        "inventory_tree_sha256": digest(canonical(rows)),
        "file_count": len(files),
        "directory_count": len(directories),
        "total_bytes": sum(item["bytes"] for item in files),
    }


def make_fixture():
    owner = gate_fixtures.A1ProductionGateTests("test_exact_committed_evidence_bundle_is_authority_neutral")
    owner.setUp()
    try:
        a1_plan, controls, gate_bundle = owner.fixture["plan"], owner.fixture["controls"], owner.bundle
        policy = foundation.create_a3_closeout_policy()
        trigger = foundation.create_a3_closeout_trigger(
            policy, a1_plan.plan_id, a1_plan.sha256(), owner.executor_receipt.data["receipt_id"],
            owner.executor_receipt.sha256(), "2026-07-25T00:00:00Z",
        )
        process_manifest = canonical({"schema_version": "req2web.runtime.a3_remote_process_manifest.v1", "pid": 4242, "pgid": 4242, "cgroup": "req2web-a3-synthetic", "gpu_processes": [4242], "listeners": []})
        process_identity = {
            "manifest_sha256": digest(process_manifest), "pid_sha256": h("4242"),
            "pgid_sha256": h("4242"), "cgroup_sha256": h("req2web-a3-synthetic"),
            "gpu_processes_sha256": digest(canonical([4242])), "listeners_sha256": digest(canonical([])),
        }
        contracts = []
        for role in remote.A3_REMOTE_ROOT_ROLES:
            marker = remote.build_a3_remote_root_marker(role, a1_plan.plan_id, gate_bundle.sha256())
            rows = [row(remote.A3_REMOTE_ROOT_MARKER_FILENAME, marker)]
            payload_name = remote.A3_REMOTE_PROCESS_MANIFEST_FILENAME if role == "work" else role + "-payload.bin"
            payload = process_manifest if role == "work" else (role + "-fixture").encode("utf-8")
            rows.append(row(payload_name, payload))
            contracts.append(root_contract(role, "/synthetic/" + role, marker, rows))
        evidence_marker = remote.build_a3_remote_root_marker(remote.A3_REMOTE_EVIDENCE_ROLE, a1_plan.plan_id, gate_bundle.sha256())
        evidence_contract = root_contract(remote.A3_REMOTE_EVIDENCE_ROLE, "/synthetic/evidence", evidence_marker, [row(remote.A3_REMOTE_ROOT_MARKER_FILENAME, evidence_marker)])
        plan = remote.create_a3_remote_closeout_plan(
            a1_plan.canonical_bytes(), controls.canonical_bytes(), gate_bundle.canonical_bytes(),
            policy.canonical_bytes(), trigger.canonical_bytes(), "f" * 64, contracts,
            evidence_contract, process_identity,
        )
        process_result = {"term_sent": True, "kill_sent": False, "grace_seconds": 30, **{key: process_identity[key] for key in ("pid_sha256", "pgid_sha256", "cgroup_sha256", "gpu_processes_sha256", "listeners_sha256")}, "post_process_count": 0, "post_pgid_count": 0, "post_cgroup_count": 0, "post_gpu_process_count": 0, "post_listener_count": 0}
        results = [{"role": contract["role"], "pre_inventory_tree_sha256": contract["inventory_tree_sha256"], "pre_file_count": contract["file_count"], "pre_total_bytes": contract["total_bytes"], "deleted_file_count": contract["file_count"], "deleted_directory_count": contract["directory_count"], "post_residual_file_count": 0, "post_residual_total_bytes": 0, "status": "deleted_exact"} for contract in contracts]
        event_bytes, remote_receipt, remote_commit, envelope = remote._scripted_remote_closeout_for_tests(plan, started_at_utc="2026-07-25T00:10:00Z", completed_at_utc="2026-07-25T00:20:00Z", process_result=process_result, root_results=results)
        aggregate_rows = [{"role": item["role"], "inventory_tree_sha256": item["inventory_tree_sha256"]} for item in contracts]
        process_evidence = foundation.create_a3_process_termination_evidence(policy, trigger, source_artifact_id="remote-process-observation", source_artifact_sha256=remote_receipt.sha256(), observed_at_utc="2026-07-25T00:20:00Z", term_sent=True, grace_seconds=30, kill_sent=False, pid_sha256=process_identity["pid_sha256"], pgid_sha256=process_identity["pgid_sha256"], cgroup_sha256=process_identity["cgroup_sha256"], post_process_count=0, post_gpu_process_count=0, post_listener_count=0)
        deletion_evidence = foundation.create_a3_project_side_deletion_evidence(policy, trigger, source_artifact_id="remote-deletion-observation", source_artifact_sha256=remote_receipt.sha256(), observed_at_utc="2026-07-25T00:30:00Z", pre_inventory_sha256=digest(canonical(aggregate_rows)), pre_file_count=sum(item["file_count"] for item in contracts), pre_total_bytes=sum(item["total_bytes"] for item in contracts), post_residual_file_count=0, post_residual_total_bytes=0)
        release_evidence = foundation.create_a3_instance_release_evidence(policy, trigger, source_artifact_id="browser-release-observation", source_artifact_sha256="a" * 64, observed_at_utc="2026-07-25T00:40:00Z", instance_id_sha256=plan.data["instance_id_sha256"], release_request_sha256="b" * 64, control_plane_state="released", storage_state="released", billing_state="not_running")
        revocation_evidence = foundation.create_a3_access_revocation_evidence(policy, trigger, source_artifact_id="operator-revocation-attestation", source_artifact_sha256="c" * 64, observed_at_utc="2026-07-25T00:50:00Z", credential_fingerprint_sha256=plan.data["credential_fingerprint_sha256"], remote_access_state="removed", local_ephemeral_key_state="removed", instance_release_state="released", instance_access_state="inaccessible")
        bundle = foundation.create_a3_closeout_evidence_bundle(policy, trigger, [process_evidence, deletion_evidence, release_evidence, revocation_evidence])
        return {"owner": owner, "plan": plan, "event_bytes": event_bytes, "remote_receipt": remote_receipt, "remote_commit": remote_commit, "remote_envelope": envelope, "foundation_bundle": bundle, "foundation_receipt": foundation.evaluate_a3_closeout_readiness(bundle), "contracts": contracts, "evidence_contract": evidence_contract, "process_identity": process_identity, "process_result": process_result, "root_results": results}
    except BaseException:
        owner.tearDown()
        raise


class A3RemoteCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.fixture = make_fixture()

    def tearDown(self):
        self.fixture["owner"].tearDown()

    def test_owning_plan_replay_and_payload_free_envelope(self):
        plan = remote.A3RemoteCloseoutPlan.from_bytes(self.fixture["plan"].canonical_bytes())
        replayed = remote.validate_a3_remote_closeout_envelope_bytes(plan.canonical_bytes(), self.fixture["remote_envelope"].canonical_bytes(), self.fixture["event_bytes"])
        self.assertEqual(replayed[1].data["status"], "remote_cleanup_observed")
        self.assertFalse(replayed[1].data["manager_consumable"])
        self.assertFalse(replayed[1].data["cleanup_complete"])
        self.assertEqual(plan.data["evidence_root_contract"]["role"], remote.A3_REMOTE_EVIDENCE_ROLE)

    def test_direct_parser_and_reidentified_bytes_remain_non_authorizing(self):
        forged = deepcopy(self.fixture["remote_receipt"].to_dict())
        forged["manager_consumable"] = True
        reidentify(forged, "receipt_id", "autodl-a3-remote-receipt-")
        with self.assertRaisesRegex(remote.A3RemoteCloseoutError, "remote_receipt_authority_invalid"):
            remote.A3RemoteCloseoutReceipt.from_dict(forged)
        forged_plan = deepcopy(self.fixture["plan"].to_dict())
        forged_plan["retry_allowed"] = True
        reidentify(forged_plan, "plan_id", "autodl-a3-remote-plan-")
        with self.assertRaisesRegex(remote.A3RemoteCloseoutError, "remote_plan_retry_invalid"):
            remote.A3RemoteCloseoutPlan(forged_plan)

    def test_public_execute_flag_rejects_before_paths_or_destructive_helpers(self):
        with self.assertRaisesRegex(remote.A3RemoteCloseoutError, "a3_destructive_execution_not_available"):
            remote.run_a3_remote_closeout("not-a-plan", "not-work", "not-cache", "not-log", "not-transfer", "not-evidence", True)
        source = inspect.getsource(remote.run_a3_remote_closeout)
        self.assertNotIn("_terminate_then_check(", source)
        self.assertNotIn("_delete_exact(", source)
        self.assertNotIn("_exclusive_write(", source)

    def test_staging_validates_evidence_marker_inventory_and_topology_without_writing(self):
        with tempfile.TemporaryDirectory(dir=str(Path(__file__).resolve().parents[1])) as temporary:
            base = Path(temporary)
            roots = {role: base / role for role in remote.A3_REMOTE_ROOT_ROLES}
            evidence = base / "evidence"
            for root in (*roots.values(), evidence):
                root.mkdir()
            contracts = []
            for role, root in roots.items():
                marker = remote.build_a3_remote_root_marker(role, self.fixture["plan"].data["a1_plan_id"], self.fixture["plan"].data["a1_gate_bundle_sha256"])
                (root / remote.A3_REMOTE_ROOT_MARKER_FILENAME).write_bytes(marker)
                payload_name = remote.A3_REMOTE_PROCESS_MANIFEST_FILENAME if role == "work" else role + "-payload.bin"
                payload = b"structural-only"
                (root / payload_name).write_bytes(payload)
                contracts.append(root_contract(role, root, marker, [row(remote.A3_REMOTE_ROOT_MARKER_FILENAME, marker), row(payload_name, payload)]))
            evidence_marker = remote.build_a3_remote_root_marker(remote.A3_REMOTE_EVIDENCE_ROLE, self.fixture["plan"].data["a1_plan_id"], self.fixture["plan"].data["a1_gate_bundle_sha256"])
            (evidence / remote.A3_REMOTE_ROOT_MARKER_FILENAME).write_bytes(evidence_marker)
            evidence_contract = root_contract(remote.A3_REMOTE_EVIDENCE_ROLE, evidence, evidence_marker, [row(remote.A3_REMOTE_ROOT_MARKER_FILENAME, evidence_marker)])
            # Use one policy/trigger pair to preserve the foundation binding.
            policy = foundation.create_a3_closeout_policy()
            trigger = foundation.create_a3_closeout_trigger(policy, self.fixture["plan"].data["a1_plan_id"], self.fixture["plan"].data["a1_plan_sha256"], self.fixture["owner"].executor_receipt.data["receipt_id"], self.fixture["owner"].executor_receipt.sha256(), "2026-07-25T00:00:00Z")
            plan = remote.create_a3_remote_closeout_plan(self.fixture["owner"].fixture["plan"].canonical_bytes(), self.fixture["owner"].fixture["controls"].canonical_bytes(), self.fixture["owner"].bundle.canonical_bytes(), policy.canonical_bytes(), trigger.canonical_bytes(), "f" * 64, contracts, evidence_contract, self.fixture["process_identity"])
            plan_path = base / "plan.json"
            plan_path.write_bytes(plan.canonical_bytes())
            staging = remote.run_a3_remote_closeout(plan_path, roots["work"], roots["cache"], roots["log"], roots["transfer"], evidence, False)
            self.assertEqual(staging.data["status"], "a3_structural_staging_validated")
            self.assertFalse(staging.data["cleanup_complete"])
            self.assertTrue(staging.data["authorization_invalidated"])

            nested = roots["work"] / "nested-evidence"
            nested.mkdir()
            nested_marker = remote.build_a3_remote_root_marker(remote.A3_REMOTE_EVIDENCE_ROLE, self.fixture["plan"].data["a1_plan_id"], self.fixture["plan"].data["a1_gate_bundle_sha256"])
            (nested / remote.A3_REMOTE_ROOT_MARKER_FILENAME).write_bytes(nested_marker)
            overlap_contracts = deepcopy(contracts)
            work_marker = (roots["work"] / remote.A3_REMOTE_ROOT_MARKER_FILENAME).read_bytes()
            overlap_contracts[0] = root_contract("work", roots["work"], work_marker, [
                row(remote.A3_REMOTE_ROOT_MARKER_FILENAME, work_marker),
                row(remote.A3_REMOTE_PROCESS_MANIFEST_FILENAME, b"structural-only"),
                directory_row("nested-evidence"),
                row("nested-evidence/" + remote.A3_REMOTE_ROOT_MARKER_FILENAME, nested_marker),
            ])
            overlap_evidence = root_contract(remote.A3_REMOTE_EVIDENCE_ROLE, nested, nested_marker, [row(remote.A3_REMOTE_ROOT_MARKER_FILENAME, nested_marker)])
            overlap_plan = remote.create_a3_remote_closeout_plan(self.fixture["owner"].fixture["plan"].canonical_bytes(), self.fixture["owner"].fixture["controls"].canonical_bytes(), self.fixture["owner"].bundle.canonical_bytes(), policy.canonical_bytes(), trigger.canonical_bytes(), "f" * 64, overlap_contracts, overlap_evidence, self.fixture["process_identity"])
            plan_path.write_bytes(overlap_plan.canonical_bytes())
            with self.assertRaisesRegex(remote.A3RemoteCloseoutError, "remote_root_topology_invalid"):
                remote.run_a3_remote_closeout(plan_path, roots["work"], roots["cache"], roots["log"], roots["transfer"], nested, False)
            (nested / remote.A3_REMOTE_ROOT_MARKER_FILENAME).unlink()
            nested.rmdir()

            plan_path.write_bytes(plan.canonical_bytes())
            (evidence / remote.A3_REMOTE_ROOT_MARKER_FILENAME).write_bytes(work_marker)
            with self.assertRaisesRegex(remote.A3RemoteCloseoutError, "root_marker_schema_or_role_invalid"):
                remote.run_a3_remote_closeout(plan_path, roots["work"], roots["cache"], roots["log"], roots["transfer"], evidence, False)
            (evidence / remote.A3_REMOTE_ROOT_MARKER_FILENAME).write_bytes(evidence_marker)
            (evidence / "unexpected.txt").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(remote.A3RemoteCloseoutError, "remote_evidence_root_inventory_invalid"):
                remote.run_a3_remote_closeout(plan_path, roots["work"], roots["cache"], roots["log"], roots["transfer"], evidence, False)

    def test_evidence_root_overlap_and_wrong_marker_fail_before_action(self):
        source = inspect.getsource(remote.run_a3_remote_closeout)
        self.assertIn("a3_destructive_execution_not_available", source)
        self.assertIn("_check_topology(roots + (evidence,))", inspect.getsource(remote))
        bad = deepcopy(self.fixture["plan"].to_dict())
        bad["evidence_root_contract"]["marker_sha256"] = "0" * 64
        reidentify(bad, "plan_id", "autodl-a3-remote-plan-")
        with self.assertRaisesRegex(remote.A3RemoteCloseoutError, "root_contract_hash_invalid"):
            remote.A3RemoteCloseoutPlan.from_dict(bad)

    def test_definition_time_capture_and_public_runner_shape(self):
        plan = self.fixture["plan"]
        with patch.multiple(remote, type=lambda _: (_ for _ in ()).throw(AssertionError("type")), len=lambda _: (_ for _ in ()).throw(AssertionError("len"))):
            self.assertEqual(plan.canonical_bytes(), self.fixture["plan"].canonical_bytes())
            self.assertEqual(plan.to_dict()["plan_id"], self.fixture["plan"].data["plan_id"])
        parameters = tuple(inspect.signature(remote.run_a3_remote_closeout).parameters)
        self.assertEqual(parameters, ("plan_path", "work_root", "cache_root", "log_root", "transfer_root", "evidence_root", "execute_a3_closeout"))
        self.assertNotIn("callback", inspect.getsource(remote.run_a3_remote_closeout))


if __name__ == "__main__":
    unittest.main()
