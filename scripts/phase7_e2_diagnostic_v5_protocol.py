"""Local-only Phase 7 E2 v5 diagnostic protocol contract.

V5 preserves the public packets, model, budgets, strict semantic scorer,
acceptance thresholds, raw-first custody, and zero-retry rule.  It makes the
trace-assisted treatment observable: after arm B sees an index-backed entity,
its next action must be exactly one grounded trace lookup, and a B final is not
legal until that lookup succeeds.  The final wire form uses parallel endpoint
arrays so the model never has to compose a delimiter-bearing edge string.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import phase7_e2_diagnostic_v3_candidate as v3
import phase7_e2_diagnostic_v4_protocol as v4


PROTOCOL_SCHEMA = "req2web.phase7.e2_protocol.v5"
PROTOCOL_REVISION = "req2web.phase7.e2_mandatory_grounded_trace_parallel_endpoints.v5"
TRACE_INDEX_ARTIFACT = v4.TRACE_INDEX_ARTIFACT
TRACE_INDEX_KIND = v4.TRACE_INDEX_KIND
BUDGETS = dict(v4.BUDGETS)
FINAL_STATUSES = set(v4.FINAL_STATUSES)
LOOKUP_USED_SENTINEL = "\x00req2web-v5-grounded-trace-used"


class ProtocolV5Error(v4.ProtocolV4Error):
    """A fail-closed v5 protocol violation."""


# The v4 runtime catches this public name when the versioned v5 adapter is
# installed.  Keeping the alias makes the inherited raw-first session loop
# classify v5 contract failures as invalid responses rather than backend faults.
ProtocolV4Error = ProtocolV5Error


def parse_model_object(raw: bytes | str) -> dict[str, Any]:
    try:
        return v4.parse_model_object(raw)
    except v4.ProtocolV4Error as exc:
        raise ProtocolV5Error(str(exc)) from exc


def grounded_entities(raw_value: Any, trace_index: Mapping[str, Any]) -> set[str]:
    try:
        return v4.grounded_entities(raw_value, trace_index)
    except v4.ProtocolV4Error as exc:
        raise ProtocolV5Error(str(exc)) from exc


def _string_list(value: Any, label: str, *, unique: bool) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProtocolV5Error(f"{label} must be a list of strings")
    if unique and len(value) != len(set(value)):
        raise ProtocolV5Error(f"{label} must not contain duplicates")
    return list(value)


def final_to_canonical(action: Mapping[str, Any], *, inventory: Iterable[str]) -> dict[str, Any]:
    expected = {
        "action",
        "status",
        "origins",
        "use_cases",
        "edge_from",
        "edge_to",
        "uncertainty",
    }
    try:
        v4._exact_keys(action, expected, "v5 final action")
    except v4.ProtocolV4Error as exc:
        raise ProtocolV5Error(str(exc)) from exc
    if action["action"] != "final" or action["status"] not in FINAL_STATUSES:
        raise ProtocolV5Error("invalid v5 final action or status")
    if not isinstance(action["uncertainty"], str):
        raise ProtocolV5Error("uncertainty must be a string")

    origins = _string_list(action["origins"], "origins", unique=True)
    use_cases = _string_list(action["use_cases"], "use_cases", unique=True)
    sources = _string_list(action["edge_from"], "edge_from", unique=False)
    targets = _string_list(action["edge_to"], "edge_to", unique=False)
    if len(sources) != len(targets):
        raise ProtocolV5Error("edge_from and edge_to must have equal lengths")
    if len(set(zip(sources, targets))) != len(sources):
        raise ProtocolV5Error("evidence edge pairs must not contain duplicates")

    visible = set(inventory)
    try:
        answer = {
            "status": action["status"],
            "origin_candidates": [v4._decode_endpoint(item, visible) for item in origins],
            "affected_use_cases": use_cases,
            "evidence_edges": [
                {
                    "from": v4._decode_endpoint(source, visible),
                    "to": v4._decode_endpoint(target, visible),
                }
                for source, target in zip(sources, targets)
            ],
            "uncertainty": action["uncertainty"],
        }
        return v3.validate_action(
            {"action": "final", "answer": answer},
            inventory=visible,
            arm="A",
            reads_used=0,
            turn=1,
        )["answer"]
    except (v4.ProtocolV4Error, v3.CandidateError) as exc:
        raise ProtocolV5Error(str(exc)) from exc


def _real_observed_entities(observed_entities: set[str]) -> set[str]:
    return observed_entities - {LOOKUP_USED_SENTINEL}


def validate_action(
    action: Mapping[str, Any],
    *,
    arm: str,
    inventory: Iterable[str],
    observed_entities: set[str],
    trace_index: Mapping[str, Any],
    reads_used: int,
    turn: int,
) -> dict[str, Any]:
    if arm not in {"A", "B"}:
        raise ProtocolV5Error("arm must be A or B")
    if not isinstance(action, Mapping) or action.get("action") not in {
        "read",
        "trace_lookup",
        "final",
    }:
        raise ProtocolV5Error("action must be read, trace_lookup, or final")

    lookup_used = LOOKUP_USED_SENTINEL in observed_entities
    grounded = _real_observed_entities(observed_entities)
    if arm == "B" and grounded and not lookup_used and action["action"] != "trace_lookup":
        raise ProtocolV5Error(
            "arm B must use one grounded trace_lookup immediately after an index-backed entity is observed"
        )

    if action["action"] == "final":
        if arm == "B" and not lookup_used:
            raise ProtocolV5Error("arm B final requires one successful grounded trace_lookup")
        return {"kind": "final", "answer": final_to_canonical(action, inventory=inventory)}

    if action["action"] == "trace_lookup":
        if arm != "B":
            raise ProtocolV5Error("trace_lookup is available only to arm B")
        if lookup_used:
            raise ProtocolV5Error("arm B permits exactly one trace_lookup")
        try:
            request = v4.trace_lookup_to_read(
                action,
                arm=arm,
                observed_entities=grounded,
                trace_index=trace_index,
                reads_used=reads_used,
                turn=turn,
            )
        except v4.ProtocolV4Error as exc:
            raise ProtocolV5Error(str(exc)) from exc
        observed_entities.add(LOOKUP_USED_SENTINEL)
        return {"kind": "trace_lookup", "model_action": dict(action), "read": request}

    try:
        return v4.validate_action(
            action,
            arm=arm,
            inventory=inventory,
            observed_entities=grounded,
            trace_index=trace_index,
            reads_used=reads_used,
            turn=turn,
        )
    except v4.ProtocolV4Error as exc:
        raise ProtocolV5Error(str(exc)) from exc


def parse_and_validate(raw: bytes | str, **context: Any) -> dict[str, Any]:
    return validate_action(parse_model_object(raw), **context)


def self_check() -> dict[str, Any]:
    if BUDGETS != v3.BUDGETS:
        raise ProtocolV5Error("v5 budget drifted from v3")
    return {
        "status": "validated_local_protocol_only",
        "schema_version": PROTOCOL_SCHEMA,
        "protocol_revision": PROTOCOL_REVISION,
        "budgets": BUDGETS,
        "b_lookup_policy": "exactly_one_after_first_grounded_index_entity_before_final",
        "final_edge_wire": "parallel_endpoint_arrays",
        "model_loaded": False,
        "run_occurred": False,
        "measured_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_subparsers(dest="command", required=True).add_parser("self-check")
    args = parser.parse_args(argv)
    try:
        result = self_check() if args.command == "self-check" else None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
