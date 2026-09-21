"""Local-only Phase 7 E2 v4 diagnostic protocol contract.

This module does not load a model or execute an experiment.  It defines a
strict, shallow model-output protocol for a future separately authorized run.
The v3 evidence, scorer, acceptance thresholds, public packets, and returned
results remain immutable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import phase7_e2_diagnostic_v3_candidate as v3


PROTOCOL_SCHEMA = "req2web.phase7.e2_protocol.v4"
PROTOCOL_REVISION = "req2web.phase7.e2_grounded_trace_action_flat_final.v4"
TRACE_INDEX_ARTIFACT = "trace_index.json"
TRACE_INDEX_KIND = "experiment_only_source_occurrence_index_v3"
ENDPOINT_SEPARATOR = "::"
EDGE_SEPARATOR = "=>"
BUDGETS = dict(v3.BUDGETS)
FINAL_STATUSES = set(v3.FINAL_STATUSES)


class ProtocolV4Error(ValueError):
    """A fail-closed v4 protocol violation."""


def _strict_object(raw: bytes | str) -> dict[str, Any]:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolV4Error("model response must be UTF-8 JSON text") from exc
    if not isinstance(raw, str):
        raise ProtocolV4Error("model response must be UTF-8 JSON text")

    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in rows:
            if key in value:
                raise ProtocolV4Error("duplicate JSON key")
            value[key] = item
        return value

    def invalid_constant(value: str) -> None:
        raise ProtocolV4Error(f"invalid JSON constant: {value}")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolV4Error("model response is not one strict JSON object") from exc
    if not isinstance(value, dict):
        raise ProtocolV4Error("model response must be one JSON object")
    return value


def parse_model_object(raw: bytes | str) -> dict[str, Any]:
    """Parse one raw model response without normalization or repair."""
    return _strict_object(raw)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ProtocolV4Error(f"{label} keys must be exactly {sorted(expected)}")


def _validate_pointer_syntax(value: str) -> None:
    if not value.startswith("/"):
        raise ProtocolV4Error("endpoint pointer must be a non-empty RFC6901 pointer")
    if re.search(r"~(?![01])", value):
        raise ProtocolV4Error("endpoint pointer contains an invalid RFC6901 escape")


def _decode_endpoint(value: Any, inventory: set[str]) -> dict[str, str]:
    if not isinstance(value, str) or value.count(ENDPOINT_SEPARATOR) != 1:
        raise ProtocolV4Error("endpoint must be artifact.json::/rfc6901/pointer")
    artifact, pointer = value.split(ENDPOINT_SEPARATOR, 1)
    if artifact not in inventory or artifact == TRACE_INDEX_ARTIFACT:
        raise ProtocolV4Error("final endpoint must cite one visible raw artifact")
    _validate_pointer_syntax(pointer)
    return {"artifact": artifact, "pointer": pointer}


def _decode_edge(value: Any, inventory: set[str]) -> dict[str, dict[str, str]]:
    if not isinstance(value, str) or value.count(EDGE_SEPARATOR) != 1:
        raise ProtocolV4Error("edge must be raw-endpoint=>raw-endpoint")
    source, target = value.split(EDGE_SEPARATOR, 1)
    return {
        "from": _decode_endpoint(source, inventory),
        "to": _decode_endpoint(target, inventory),
    }


def _require_unique_strings(values: Any, label: str) -> list[str]:
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ProtocolV4Error(f"{label} must be a list of strings")
    if len(values) != len(set(values)):
        raise ProtocolV4Error(f"{label} must not contain duplicates")
    return list(values)


def final_to_canonical(action: Mapping[str, Any], *, inventory: Iterable[str]) -> dict[str, Any]:
    """Convert the strict shallow wire form into the unchanged scoring form.

    This is a schema translation, not JSON repair: the supplied model object
    must already be valid, exact-key JSON and every endpoint must parse under
    the frozen delimiter grammar.
    """
    _exact_keys(
        action,
        {"action", "status", "origins", "use_cases", "edges", "uncertainty"},
        "v4 final action",
    )
    if action["action"] != "final" or action["status"] not in FINAL_STATUSES:
        raise ProtocolV4Error("invalid v4 final action or status")
    if not isinstance(action["uncertainty"], str):
        raise ProtocolV4Error("uncertainty must be a string")
    visible = set(inventory)
    origins = _require_unique_strings(action["origins"], "origins")
    use_cases = _require_unique_strings(action["use_cases"], "use_cases")
    edges = _require_unique_strings(action["edges"], "edges")
    answer = {
        "status": action["status"],
        "origin_candidates": [_decode_endpoint(value, visible) for value in origins],
        "affected_use_cases": use_cases,
        "evidence_edges": [_decode_edge(value, visible) for value in edges],
        "uncertainty": action["uncertainty"],
    }
    try:
        return v3.validate_action(
            {"action": "final", "answer": answer},
            inventory=visible,
            arm="A",
            reads_used=0,
            turn=1,
        )["answer"]
    except v3.CandidateError as exc:
        raise ProtocolV4Error(str(exc)) from exc


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)
    elif isinstance(value, str):
        yield value


def grounded_entities(raw_value: Any, trace_index: Mapping[str, Any]) -> set[str]:
    """Return index entity IDs already visible in a preceding raw read."""
    if trace_index.get("kind") != TRACE_INDEX_KIND or not isinstance(trace_index.get("entities"), dict):
        raise ProtocolV4Error("trace index shape is invalid")
    visible_scalars = set(_walk_strings(raw_value))
    return visible_scalars & set(trace_index["entities"])


def _escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def trace_lookup_to_read(
    action: Mapping[str, Any],
    *,
    arm: str,
    observed_entities: set[str],
    trace_index: Mapping[str, Any],
    reads_used: int,
    turn: int,
) -> dict[str, str]:
    """Resolve a grounded B-only entity action to one exact index read."""
    _exact_keys(action, {"action", "entity_id"}, "v4 trace lookup")
    if action["action"] != "trace_lookup" or arm != "B":
        raise ProtocolV4Error("trace_lookup is available only to arm B")
    entity_id = action["entity_id"]
    if not isinstance(entity_id, str) or not entity_id:
        raise ProtocolV4Error("trace_lookup entity_id must be a non-empty string")
    if reads_used >= BUDGETS["reads"] or turn >= BUDGETS["provider_turns"]:
        raise ProtocolV4Error("read budget exhausted; final answer required")
    if entity_id not in observed_entities:
        raise ProtocolV4Error("trace_lookup entity_id was not grounded in an earlier raw read")
    if trace_index.get("kind") != TRACE_INDEX_KIND or not isinstance(trace_index.get("entities"), dict):
        raise ProtocolV4Error("trace index shape is invalid")
    if entity_id not in trace_index["entities"]:
        raise ProtocolV4Error("grounded entity is absent from the trace index")
    return {
        "action": "read",
        "artifact": TRACE_INDEX_ARTIFACT,
        "pointer": f"/entities/{_escape_pointer_token(entity_id)}",
    }


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
    """Validate one already parsed v4 model action without executing it."""
    if arm not in {"A", "B"}:
        raise ProtocolV4Error("arm must be A or B")
    if not isinstance(action, Mapping) or action.get("action") not in {"read", "trace_lookup", "final"}:
        raise ProtocolV4Error("action must be read, trace_lookup, or final")
    visible = set(inventory)
    if action["action"] == "final":
        return {"kind": "final", "answer": final_to_canonical(action, inventory=visible)}
    if action["action"] == "trace_lookup":
        return {
            "kind": "trace_lookup",
            "model_action": dict(action),
            "read": trace_lookup_to_read(
                action,
                arm=arm,
                observed_entities=observed_entities,
                trace_index=trace_index,
                reads_used=reads_used,
                turn=turn,
            ),
        }
    _exact_keys(action, {"action", "artifact", "pointer"}, "v4 raw read")
    if not all(isinstance(action[key], str) for key in ("artifact", "pointer")):
        raise ProtocolV4Error("raw read fields must be strings")
    if reads_used >= BUDGETS["reads"] or turn >= BUDGETS["provider_turns"]:
        raise ProtocolV4Error("read budget exhausted; final answer required")
    if action["artifact"] == TRACE_INDEX_ARTIFACT:
        raise ProtocolV4Error("direct trace-index reads are forbidden; use trace_lookup")
    if action["artifact"] not in visible:
        raise ProtocolV4Error("artifact is not visible to this arm")
    if action["pointer"] != "":
        _validate_pointer_syntax(action["pointer"])
    return {"kind": "raw_read", "read": dict(action)}


def parse_and_validate(raw: bytes | str, **context: Any) -> dict[str, Any]:
    return validate_action(parse_model_object(raw), **context)


def self_check() -> dict[str, Any]:
    if BUDGETS != v3.BUDGETS:
        raise ProtocolV4Error("v4 budget drifted from v3")
    return {
        "status": "validated_local_protocol_only",
        "schema_version": PROTOCOL_SCHEMA,
        "protocol_revision": PROTOCOL_REVISION,
        "budgets": BUDGETS,
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
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__, "message": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
