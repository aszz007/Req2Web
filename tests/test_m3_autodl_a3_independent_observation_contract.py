from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import req2web_runtime.autodl_a3_independent_observation_contract as contract
import req2web_runtime.autodl_a3_finalizer as finalizer
from tests.test_m3_autodl_a3_remote_closeout import make_fixture


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def reidentify(value):
    body = {key: item for key, item in value.items() if key != "contract_id"}
    value["contract_id"] = "autodl-a3-independent-observation-" + digest(canonical(body))[:20]


class IndependentObservationContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = make_fixture()
        result = finalizer._finalize_a3_closeout_for_tests(
            self.fixture["plan"].canonical_bytes(),
            self.fixture["remote_envelope"].canonical_bytes(),
            self.fixture["event_bytes"],
            self.fixture["foundation_bundle"].canonical_bytes(),
            self.fixture["foundation_receipt"].canonical_bytes(),
            "2026-07-25T01:00:00Z",
        )
        self.finalizer_envelope = finalizer.finalizer_envelope_from_result(result)
        self.artifact = self.create()

    def tearDown(self):
        self.fixture["owner"].tearDown()

    def create(self):
        return contract.create_independent_observation_contract(
            self.fixture["foundation_bundle"].canonical_bytes(),
            self.fixture["foundation_receipt"].canonical_bytes(),
            self.fixture["plan"].canonical_bytes(),
            self.fixture["remote_envelope"].canonical_bytes(),
            self.fixture["event_bytes"],
            self.finalizer_envelope,
        )

    def validate(self, raw):
        return contract.validate_independent_observation_contract_bytes(
            raw,
            self.fixture["foundation_bundle"].canonical_bytes(),
            self.fixture["foundation_receipt"].canonical_bytes(),
            self.fixture["plan"].canonical_bytes(),
            self.fixture["remote_envelope"].canonical_bytes(),
            self.fixture["event_bytes"],
            self.finalizer_envelope,
        )

    def test_happy_declaration_is_canonical_registered_and_no_action(self):
        data = self.artifact.data
        self.assertEqual(data["status"], contract.INDEPENDENT_OBSERVATION_CONTRACT_STATUS)
        self.assertEqual(
            [row["category"] for row in data["requirements"]],
            [
                "process_group_listener",
                "root_residual",
                "instance_release",
                "credential_access_revocation",
            ],
        )
        self.assertEqual(
            [row["structural_source_role"] for row in data["requirements"]],
            ["remote_executor_receipt", "remote_executor_receipt", "local_receipt", "local_receipt"],
        )
        for row in data["requirements"]:
            self.assertFalse(row["self_certification_accepted"])
            self.assertFalse(row["observation_occurred"])
            self.assertEqual(row["status"], "independent_observer_required")
            self.assertNotEqual(
                row["structural_source_role"], row["required_independent_source_role"]
            )
        for field in (
            "manager_consumable",
            "cleanup_complete",
            "release_complete",
            "revocation_complete",
            "next_run_allowed",
            "a2_unlocked",
            "external_action_authorized",
            "external_action_executed",
            "physical_erasure_claimed",
        ):
            self.assertFalse(data[field])
        replay = self.validate(self.artifact.canonical_bytes())
        self.assertEqual(replay.sha256(), self.artifact.sha256())

    def test_each_structural_receipt_cannot_self_certify_its_requirement(self):
        for index in range(4):
            forged = deepcopy(self.artifact.to_dict())
            row = forged["requirements"][index]
            row["required_independent_source_role"] = row["structural_source_role"]
            reidentify(forged)
            with self.subTest(index=index), self.assertRaisesRegex(
                contract.A3IndependentObservationContractError,
                "source_separation_invalid",
            ):
                self.validate(canonical(forged))

    def test_forged_authority_occurrence_and_physical_erasure_are_rejected(self):
        for field in (
            "manager_consumable",
            "cleanup_complete",
            "release_complete",
            "revocation_complete",
            "next_run_allowed",
            "a2_unlocked",
            "external_action_authorized",
            "external_action_executed",
            "physical_erasure_claimed",
        ):
            forged = deepcopy(self.artifact.to_dict())
            forged[field] = True
            reidentify(forged)
            with self.subTest(field=field), self.assertRaisesRegex(
                contract.A3IndependentObservationContractError,
                "no_action_invariant_invalid",
            ):
                self.validate(canonical(forged))
        forged = deepcopy(self.artifact.to_dict())
        forged["requirements"][0]["observation_occurred"] = True
        reidentify(forged)
        with self.assertRaisesRegex(
            contract.A3IndependentObservationContractError,
            "occurrence_claim_invalid",
        ):
            self.validate(canonical(forged))

    def test_cross_chain_and_self_consistent_binding_drift_are_rejected(self):
        other = make_fixture()
        try:
            result = finalizer._finalize_a3_closeout_for_tests(
                other["plan"].canonical_bytes(),
                other["remote_envelope"].canonical_bytes(),
                other["event_bytes"],
                other["foundation_bundle"].canonical_bytes(),
                other["foundation_receipt"].canonical_bytes(),
                "2026-07-25T01:00:00Z",
            )
            other_finalizer = finalizer.finalizer_envelope_from_result(result)
            with self.assertRaisesRegex(
                contract.A3IndependentObservationContractError,
                "source_binding_invalid",
            ):
                contract.validate_independent_observation_contract_bytes(
                    self.artifact.canonical_bytes(),
                    other["foundation_bundle"].canonical_bytes(),
                    other["foundation_receipt"].canonical_bytes(),
                    other["plan"].canonical_bytes(),
                    other["remote_envelope"].canonical_bytes(),
                    other["event_bytes"],
                    other_finalizer,
                )
        finally:
            other["owner"].tearDown()

        forged = deepcopy(self.artifact.to_dict())
        forged["remote_receipt_sha256"] = "f" * 64
        reidentify(forged)
        with self.assertRaisesRegex(
            contract.A3IndependentObservationContractError,
            "source_binding_invalid",
        ):
            self.validate(canonical(forged))

    def test_direct_constructor_subclass_and_unregistered_object_are_rejected(self):
        with self.assertRaisesRegex(
            contract.A3IndependentObservationContractError,
            "direct_constructor_invalid",
        ):
            contract.IndependentObservationContract()

        class EqualContract(contract.IndependentObservationContract):
            pass

        with self.assertRaisesRegex(
            contract.A3IndependentObservationContractError,
            "direct_constructor_invalid",
        ):
            EqualContract()
        unregistered = object.__new__(contract.IndependentObservationContract)
        with self.assertRaisesRegex(
            contract.A3IndependentObservationContractError,
            "not_registered",
        ):
            _ = unregistered.canonical_bytes()

    def test_module_rebinding_cannot_replace_captured_owning_validators(self):
        bomb = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("rebound"))
        with patch.multiple(
            contract,
            validate_a3_closeout_bundle_bytes=bomb,
            validate_a3_remote_closeout_envelope_bytes=bomb,
            validate_a3_finalizer_envelope_bytes=bomb,
            hashlib=None,
            json=None,
        ):
            rebuilt = self.create()
            self.assertEqual(rebuilt.canonical_bytes(), self.artifact.canonical_bytes())
            replay = self.validate(self.artifact.canonical_bytes())
            self.assertFalse(replay.data["cleanup_complete"])

        forged = deepcopy(self.artifact.to_dict())
        forged["manager_consumable"] = True
        reidentify(forged)
        with patch.object(
            contract.IndependentObservationContract,
            "canonical_bytes",
            lambda _self: canonical(forged),
        ):
            with self.assertRaisesRegex(
                contract.A3IndependentObservationContractError,
                "no_action_invariant_invalid",
            ):
                self.validate(self.artifact.canonical_bytes())

    def test_public_surface_has_no_action_or_external_runtime_entrypoint(self):
        public_names = set(contract.__all__)
        self.assertEqual(
            public_names,
            {
                "A3IndependentObservationContractError",
                "INDEPENDENT_OBSERVATION_CONTRACT_SCHEMA",
                "INDEPENDENT_OBSERVATION_CONTRACT_STATUS",
                "IndependentObservationContract",
                "create_independent_observation_contract",
                "validate_independent_observation_contract_bytes",
            },
        )
        source = inspect.getsource(contract)
        for banned in (
            "import subprocess",
            "import socket",
            "paramiko",
            "requests.",
            "selenium",
            "playwright",
            "torch",
            "transformers",
            "os.remove",
            "shutil.rmtree",
            "terminate(",
            "kill(",
        ):
            self.assertNotIn(banned, source)
        parameters = tuple(
            inspect.signature(contract.create_independent_observation_contract).parameters
        )
        self.assertEqual(
            parameters,
            (
                "foundation_bundle_bytes",
                "foundation_receipt_bytes",
                "remote_plan_bytes",
                "remote_envelope_bytes",
                "remote_event_bytes",
                "finalizer_envelope_bytes",
            ),
        )


if __name__ == "__main__":
    unittest.main()