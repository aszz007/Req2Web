"""V4 wrapper around the frozen E2 trace-navigation acceptance semantics."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import phase7_e2_diagnostic_v4_protocol as protocol
import phase7_e2_trace_navigation_acceptance as base


ASSESSMENT_SCHEMA = "req2web.phase7.e2.trace_navigation_assessment.v4"
RUNTIME_SCHEMA = "req2web.phase7.e2_runtime_result.v4"
SCORE_SCHEMA = "req2web.phase7.e2_diagnostic_score.v4"


def audit_tool_reads(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in records:
        request = record.get("request")
        model_action = record.get("model_action")
        if isinstance(request, dict) and request.get("artifact") == protocol.TRACE_INDEX_ARTIFACT:
            if not isinstance(model_action, dict) or set(model_action) != {"action", "entity_id"}:
                raise base.AcceptanceError("v4 index receipt must preserve the exact model trace_lookup action")
            if model_action.get("action") != "trace_lookup" or not isinstance(model_action.get("entity_id"), str):
                raise base.AcceptanceError("v4 index receipt has an invalid model trace_lookup action")
            expected_pointer = f"/entities/{model_action['entity_id'].replace('~', '~0').replace('/', '~1')}"
            if request.get("pointer") != expected_pointer:
                raise base.AcceptanceError("v4 derived index pointer does not bind the model entity_id")
        elif model_action is not None:
            raise base.AcceptanceError("v4 raw reads must not carry a synthetic model_action")
    return base.audit_tool_reads(records)


def assess_files(
    *, preparation_root: Path, score_path: Path, runtime_path: Path, raw_root: Path
) -> dict[str, Any]:
    manifest_path = preparation_root / "public" / "manifest.json"
    gold_path = preparation_root / "private" / "gold.json"
    manifest, manifest_raw = base._load_object(manifest_path)
    gold, gold_raw = base._load_object(gold_path)
    score, score_raw = base._load_object(score_path)
    runtime, runtime_raw = base._load_object(runtime_path)
    if runtime.get("schema_version") != RUNTIME_SCHEMA:
        raise base.AcceptanceError("unexpected v4 runtime schema")
    if score.get("schema_version") != SCORE_SCHEMA:
        raise base.AcceptanceError("unexpected v4 score schema")
    split = runtime.get("split")
    schedule = manifest.get("schedule", {}).get(split)
    if not isinstance(schedule, list):
        raise base.AcceptanceError("runtime split is absent from the manifest schedule")
    read_audits = {}
    for row in schedule:
        session_id = row.get("session_id")
        if not isinstance(session_id, str):
            raise base.AcceptanceError("schedule session_id is invalid")
        read_audits[session_id] = audit_tool_reads(base._session_tool_reads(raw_root, session_id))

    compatible_runtime = deepcopy(runtime)
    compatible_runtime["schema_version"] = "req2web.phase7.e2_runtime_result.v3"
    compatible_score = deepcopy(score)
    compatible_score["schema_version"] = "req2web.phase7.e2_diagnostic_score.v3"
    result = base.assess_payloads(
        score=compatible_score,
        runtime=compatible_runtime,
        manifest=manifest,
        gold=gold,
        split=split,
        runtime_sha256=base._sha(runtime_raw),
        manifest_sha256=base._sha(manifest_raw),
        gold_sha256=base._sha(gold_raw),
        read_audits=read_audits,
    )
    result["schema_version"] = ASSESSMENT_SCHEMA
    sources = result.setdefault("sources", {})
    sources.update(
        {
            "runtime_result_sha256": base._sha(runtime_raw),
            "score_sha256": base._sha(score_raw),
            "public_manifest_sha256": base._sha(manifest_raw),
            "private_gold_sha256": base._sha(gold_raw),
            "protocol_revision": protocol.PROTOCOL_REVISION,
        }
    )
    return result


def write_new(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preparation-root", type=Path, required=True)
    parser.add_argument("--score", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = assess_files(
            preparation_root=args.preparation_root,
            score_path=args.score,
            runtime_path=args.runtime,
            raw_root=args.raw_root,
        )
        write_new(args.output, result)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__, "message": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": "assessed", "split": result["split"], "development_gate_passed": result.get("development_gate_passed")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
