from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from req2web_runtime import autodl_action_time_authority as authority_module
from req2web_runtime import autodl_a3_handoff_protocol as protocol_module
from req2web_runtime import (
    autodl_a3_authenticated_executor_contract as executor_module,
)


def _build_main():
    parse_request = authority_module._parse_action_time_request_for_trust
    request_to_dict = authority_module._action_time_request_to_dict_for_trust
    request_sha256 = authority_module._action_time_request_sha256_for_trust
    parse_capsule = protocol_module._parse_a3_handoff_capsule_for_trust
    capsule_to_dict = protocol_module._a3_handoff_capsule_to_dict_for_trust
    capsule_sha256 = protocol_module._a3_handoff_capsule_sha256_for_trust
    parse_contract = (
        executor_module._parse_a3_authenticated_executor_contract_for_trust
    )
    contract_to_dict = (
        executor_module._a3_authenticated_executor_contract_to_dict_for_trust
    )
    contract_sha256 = (
        executor_module._a3_authenticated_executor_contract_sha256_for_trust
    )
    path_type = Path
    json_dumps = json.dumps

    def main() -> int:
        parser = argparse.ArgumentParser(
            description=(
                "Validate the no-action AutoDL A3 trusted-handoff protocol "
                "artifacts. This command never issues a permit or executes "
                "an external action."
            )
        )
        parser.add_argument("--request", required=True, type=path_type)
        parser.add_argument("--capsule", required=True, type=path_type)
        parser.add_argument("--executor-contract", required=True, type=path_type)
        args = parser.parse_args()

        request = parse_request(args.request.read_bytes())
        capsule = parse_capsule(args.capsule.read_bytes())
        contract = parse_contract(args.executor_contract.read_bytes())
        request_data = request_to_dict(request)
        capsule_data = capsule_to_dict(capsule)
        contract_data = contract_to_dict(contract)
        request_hash = request_sha256(request)
        capsule_hash = capsule_sha256(capsule)
        if (
            capsule_data["request_id"] != request_data["request_id"]
            or capsule_data["request_sha256"] != request_hash
            or contract_data["request_id"] != request_data["request_id"]
            or contract_data["request_sha256"] != request_hash
            or contract_data["capsule_id"] != capsule_data["capsule_id"]
            or contract_data["capsule_sha256"] != capsule_hash
        ):
            raise ValueError("a3_handoff_validator_cross_bundle_invalid")

        print(
            json_dumps(
                {
                    "status": (
                        "a3_trusted_handoff_structural_readiness_validated"
                    ),
                    "request_id": request_data["request_id"],
                    "request_sha256": request_hash,
                    "capsule_id": capsule_data["capsule_id"],
                    "capsule_sha256": capsule_hash,
                    "executor_contract_id": contract_data["contract_id"],
                    "executor_contract_sha256": contract_sha256(contract),
                    "permit_issued": False,
                    "signature_verified": False,
                    "destructive_action_authorized": False,
                    "external_action_executed": False,
                    "cleanup_complete": False,
                    "manager_consumable": False,
                    "next_run_allowed": False,
                    "a2_unlocked": False,
                    "h1_allowed": False,
                    "formal_quality_allowed": False,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0

    return main


main = _build_main()

if __name__ == "__main__":
    raise SystemExit(main())
