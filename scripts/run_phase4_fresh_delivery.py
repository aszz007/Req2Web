"""Run the no-model Phase 4 fresh-integrated downstream delivery bridge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from req2web_orchestration.model_route import (  # noqa: E402
    SCRIPTED_ACCEPTANCE_FAIL_KEY,
    SCRIPTED_ACCEPTANCE_PASS_KEY,
    SCRIPTED_ACCEPTANCE_UNKNOWN_KEY,
)
from req2web_orchestration.phase4_graph import (  # noqa: E402
    phase4_create_portable_authority_state,
    synthetic_commerce_b_input,
)
from req2web_runtime.phase4_fresh_delivery import (  # noqa: E402
    Phase4FreshDeliveryError,
    build_phase4_graph_bound_delivery_materials,
    run_phase4_fresh_delivery,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Revalidate a Phase 4 fresh-integrated assembled artifact and run "
            "local no-model downstream delivery."
        )
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    parser.add_argument(
        "--material-root",
        type=Path,
        required=True,
        help="New dedicated root for graph-bound G0, D17, and delivery materials.",
    )
    parser.add_argument(
        "--case-id",
        default="path3-commerce-checkout",
        help="Synthetic Phase 4 case identity bound to context and G0.",
    )
    parser.add_argument(
        "--request-id",
        default="p4-02a-synthetic-request-001",
        help="Synthetic Phase 4 request identity bound to the graph state.",
    )
    parser.add_argument(
        "--acceptance-key",
        choices=(
            SCRIPTED_ACCEPTANCE_PASS_KEY,
            SCRIPTED_ACCEPTANCE_FAIL_KEY,
            SCRIPTED_ACCEPTANCE_UNKNOWN_KEY,
        ),
        default=SCRIPTED_ACCEPTANCE_PASS_KEY,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        b_input = synthetic_commerce_b_input(
            case_id=args.case_id,
            request_id=args.request_id,
        )
        state = phase4_create_portable_authority_state(b_input)
        materials = build_phase4_graph_bound_delivery_materials(
            graph_state=state,
            material_root=args.material_root.resolve(strict=False),
        )
        live = materials.live
        receipt = run_phase4_fresh_delivery(
            source_root=args.source_root.resolve(strict=True),
            delivery_root=args.delivery_root,
            context=live["context"],
            guidance=live["guidance"],
            manifest=live["manifest"],
            selected=live["selected"],
            local_request=live["local_request"],
            pre_invocation_audit=live["pre_invocation_audit"],
            local_qwen_preparation=live["local_qwen_preparation"],
            package=live["package"],
            frozen_g0_reference=live["frozen_g0_reference"],
            fallback_record=live["fallback_record"],
            fallback_snapshot_dir=live["fallback_snapshot_dir"],
            scripted_acceptance_fixture=args.acceptance_key,
        )
    except (OSError, KeyError, TypeError, ValueError, Phase4FreshDeliveryError) as exc:
        print(f"phase4 fresh delivery failed closed: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "receipt_id": receipt.receipt_id,
                "status": receipt.downstream["status"],
                "delivery_success": receipt.success_accounting["delivery_success"],
                "raw_model_contract_success": receipt.success_accounting[
                    "raw_model_contract_success"
                ],
                "normalized_node_contract_success": receipt.success_accounting[
                    "normalized_node_contract_success"
                ],
                "model_success": receipt.success_accounting["model_success"],
                "repair": receipt.downstream["one_repair"],
                "fallback": receipt.downstream["same_case_g0_fallback"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if receipt.success_accounting["delivery_success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
