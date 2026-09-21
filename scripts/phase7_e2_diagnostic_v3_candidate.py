"""Build and validate the local-only Phase 7 E2 v3 protocol candidate.

This candidate is derived from the frozen v2 packet bytes after inspecting only
the completed development split.  It never runs a model and never changes the
historical v2 preparation or returned development result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SCHEMA = "req2web.phase7.e2_public.v3"
GOLD_SCHEMA = "req2web.phase7.e2_gold.v3"
PREPARATION_SCHEMA = "req2web.phase7.e2_preparation.v3"
BUDGETS = {
    "reads": 5,
    "input_tokens": 12000,
    "output_tokens": 2048,
    "provider_turns": 6,
    "session_seconds": 120,
    "measured_sessions": 40,
    "development_sessions": 4,
}
RAW_ARTIFACTS = {
    "requirements.json",
    "specification.json",
    "handoff.json",
    "render_bindings.json",
    "runtime.json",
    "versions.json",
}
FINAL_STATUSES = {"fault", "no_fault", "unknown", "unsupported_input"}

COMMON_PROMPT = (
    "You are an isolated experiment diagnostic agent. Diagnose only from the "
    "listed JSON artifacts. The hard limits are five artifact reads, six provider "
    "turns, 12000 cumulative input tokens, 2048 output tokens per reply, and 120 "
    "seconds excluding model load. First read readme.json. After at most five "
    "reads, the sixth provider turn must be a final answer. If trace_index.json "
    "is listed, it is a verdict-free navigation aid: after observing an entity ID "
    "in a raw artifact, read only /entities/<escaped-ID>; never read the index root. "
    "Report the smallest set of independent first faulty locations, not every "
    "downstream symptom. A no-progress sequence originates at its first advance "
    "event whose after_state equals before_state. Reply with exactly one JSON "
    "object. Read action: {\"action\":\"read\",\"artifact\":\"name.json\","
    "\"pointer\":\"\"}. Final action: {\"action\":\"final\",\"answer\":{"
    "\"status\":\"fault\",\"origin_candidates\":[{\"artifact\":\"name.json\","
    "\"pointer\":\"/exact/RFC6901/pointer\"}],\"affected_use_cases\":[\"UC-01\"],"
    "\"evidence_edges\":[{\"from\":{\"artifact\":\"a.json\",\"pointer\":\"/x\"},"
    "\"to\":{\"artifact\":\"b.json\",\"pointer\":\"/y\"}}],"
    "\"uncertainty\":\"concise\"}}. Status is exactly fault, no_fault, unknown, "
    "or unsupported_input. The host executes reads; never invent a tool response."
)

README = {
    "scope": "Bounded experiment projections from native deterministic Req2Web artifacts plus local tool fixtures; not a browser or model execution trace.",
    "task": "Diagnose inconsistencies and incomplete local tool execution. Report all independent first faulty locations and cite raw source endpoints.",
    "answer_locations": "Each origin uses artifact plus an exact RFC6901 pointer. Use /events/N/error for an explicit exception, the first /events/N/after_state that fails to advance for no-progress, /revision for stale handoff, and the exact mismatched relation leaf for cross-artifact faults.",
    "minimal_origin_rule": "Report the smallest independent origin set. Do not enumerate later repeated symptoms when an earlier event already explains the sequence.",
    "trace_index": "If trace_index.json is listed, query only /entities/<escaped-ID> after observing that ID in a raw artifact. The index contains source occurrences only, no verdict, expected label, fault family, or gold endpoint.",
    "authority": {
        "handoff.json": "Downstream links must preserve specification component-to-section-to-use-case ownership.",
        "render_bindings.json": "Labels copied from PageSpec must retain the specification value; they are not browser-observed text.",
        "runtime.json": "Actual local test-call order and outcomes. advance must increase state; read_status may repeat without changing state.",
        "specification.json": "Structural reference, not proof of business satisfaction.",
        "versions.json": "All active downstream views must use the release operator's active revision.",
    },
}


class CandidateError(ValueError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CandidateError(f"{path.name} must contain one JSON object")
    return value


def write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical(value))
        stream.flush()


def pointer(value: Any, location: str) -> Any:
    if location == "":
        return value
    if not isinstance(location, str) or not location.startswith("/"):
        raise CandidateError("invalid RFC6901 pointer")
    current = value
    for token in location[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not token.isdigit() or int(token) >= len(current):
                raise CandidateError("pointer does not resolve")
            current = current[int(token)]
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise CandidateError("pointer does not resolve")
    return current


def walk(value: Any, location: str = ""):
    if isinstance(value, dict):
        for key, child in sorted(value.items()):
            escaped = key.replace("~", "~0").replace("/", "~1")
            yield from walk(child, f"{location}/{escaped}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{location}/{index}")
    else:
        yield location, value


def make_index(files: Mapping[str, Any]) -> dict[str, Any]:
    """Build a verdict-free exact occurrence index over named source IDs."""
    identifiers: set[str] = set()
    for name in sorted(RAW_ARTIFACTS & set(files)):
        for location, value in walk(files[name]):
            if isinstance(value, str) and (
                location.endswith("_id")
                or location.endswith("/entity")
                or location.endswith("/use_case_ids/0")
            ):
                identifiers.add(value)
    entities: dict[str, list[dict[str, str]]] = {}
    for entity in sorted(identifiers):
        rows = []
        for name in sorted(RAW_ARTIFACTS & set(files)):
            digest = sha(canonical(files[name]))
            for location, scalar in walk(files[name]):
                if scalar == entity:
                    rows.append({"artifact": name, "pointer": location, "sha256": digest})
        entities[entity] = rows
    return {
        "kind": "experiment_only_source_occurrence_index_v3",
        "access": "lookup_only_by_exact_entity_pointer",
        "entities": entities,
        "meaning": "Each row cites an existing equal scalar. Shared values are navigation aids, not causal claims or correctness verdicts.",
    }


def _safe_packet_file(root: Path, packet_id: str, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or len(relative.parts) != 1 or relative.name != name:
        raise CandidateError("unsafe packet filename")
    path = root / "public" / "packets" / packet_id / name
    if not path.is_file() or path.is_symlink():
        raise CandidateError("missing or unsafe packet file")
    return path


def _transform_gold(source_gold: Mapping[str, Any]) -> dict[str, Any]:
    packets = []
    for source in source_gold.get("packets", []):
        row = json.loads(json.dumps(source))
        origins = []
        for origin in row.get("origins", []):
            if set(origin) != {"artifact", "entity", "field"}:
                raise CandidateError("legacy origin schema drifted")
            if not origin["field"].startswith("/"):
                raise CandidateError("legacy origin lacks an exact pointer")
            origins.append({"artifact": origin["artifact"], "pointer": origin["field"]})
        row["origins"] = origins
        packets.append(row)
    return {"schema_version": GOLD_SCHEMA, "packets": packets}


def prepare(source: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise CandidateError("candidate output already exists")
    source = source.resolve(strict=True)
    legacy_manifest_path = source / "public" / "manifest.json"
    legacy_gold_path = source / "private" / "gold.json"
    legacy_receipt_path = source / "preparation.json"
    legacy_manifest = read(legacy_manifest_path)
    legacy_gold = read(legacy_gold_path)
    legacy_receipt = read(legacy_receipt_path)
    if legacy_manifest.get("schema_version") != "req2web.phase7.e2_public.v1":
        raise CandidateError("source is not the frozen v2 public preparation")
    if sha(legacy_manifest_path.read_bytes()) != legacy_receipt.get("public_manifest_sha256") or sha(legacy_gold_path.read_bytes()) != legacy_receipt.get("gold_sha256"):
        raise CandidateError("source preparation identity drifted")
    packets = []
    for packet in legacy_manifest.get("packets", []):
        packet_id = packet["packet_id"]
        files: dict[str, Any] = {}
        source_rows = {row["path"]: row for row in packet["files"]}
        for name, identity in source_rows.items():
            path = _safe_packet_file(source, packet_id, name)
            raw = path.read_bytes()
            if len(raw) != identity["bytes"] or sha(raw) != identity["sha256"]:
                raise CandidateError("source packet identity drifted")
            if name not in {"readme.json", "trace_index.json"}:
                files[name] = json.loads(raw)
        files["readme.json"] = README
        if "trace_index.json" in source_rows:
            files["trace_index.json"] = make_index(files)
        rows = []
        for name, value in sorted(files.items()):
            raw = canonical(value)
            write_new(output / "public" / "packets" / packet_id / name, value)
            rows.append({"path": name, "sha256": sha(raw), "bytes": len(raw)})
        packets.append({"packet_id": packet_id, "files": rows})
    manifest = {
        "schema_version": PUBLIC_SCHEMA,
        "budgets": BUDGETS,
        "protocol": {
            "prompt_sha256": sha(COMMON_PROMPT.encode("utf-8")),
            "origin_schema": {"artifact": "JSON basename", "pointer": "exact RFC6901 pointer"},
            "trace_index_root_read_allowed": False,
            "fifth_read_requires_next_turn_final": True,
        },
        "packets": packets,
        "schedule": legacy_manifest["schedule"],
    }
    gold = _transform_gold(legacy_gold)
    write_new(output / "public" / "manifest.json", manifest)
    write_new(output / "private" / "gold.json", gold)
    receipt = {
        "schema_version": PREPARATION_SCHEMA,
        "status": "local_protocol_candidate_not_execution_authority",
        "revision_basis": "development_split_only_no_measured_sessions",
        "predecessor": {
            "preparation_sha256": sha(legacy_receipt_path.read_bytes()),
            "public_manifest_sha256": sha(legacy_manifest_path.read_bytes()),
            "gold_sha256": sha(legacy_gold_path.read_bytes()),
        },
        "public_manifest_sha256": sha(canonical(manifest)),
        "gold_sha256": sha(canonical(gold)),
        "source_sha256": sha(Path(__file__).read_bytes()),
        "historical_result_mutated": False,
        "model_loaded": False,
        "gpu_used": False,
        "development_sessions_executed": 0,
        "measured_sessions_executed": 0,
    }
    write_new(output / "preparation.json", receipt)
    result = validate(output)
    return {**result, "output": str(output)}


def _packet_files(root: Path, packet: Mapping[str, Any]) -> dict[str, Any]:
    packet_id = packet["packet_id"]
    expected = {row["path"] for row in packet["files"]}
    base = root / "public" / "packets" / packet_id
    if {path.name for path in base.iterdir() if path.is_file()} != expected:
        raise CandidateError("packet inventory drifted")
    files = {}
    for row in packet["files"]:
        path = _safe_packet_file(root, packet_id, row["path"])
        raw = path.read_bytes()
        if len(raw) != row["bytes"] or sha(raw) != row["sha256"]:
            raise CandidateError("packet file identity drifted")
        files[row["path"]] = json.loads(raw)
    return files


def validate_public(public_root: Path, expected_manifest_sha256: str | None = None) -> dict[str, Any]:
    """Validate only model-visible bytes; private gold need not be uploaded."""
    public_root = public_root.resolve(strict=True)
    manifest_path = public_root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise CandidateError("public manifest is missing or unsafe")
    raw = manifest_path.read_bytes()
    if expected_manifest_sha256 is not None and sha(raw) != expected_manifest_sha256:
        raise CandidateError("public manifest SHA-256 mismatch")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != PUBLIC_SCHEMA or manifest.get("budgets") != BUDGETS or len(manifest.get("packets", [])) != 22:
        raise CandidateError("public schema, budget, or packet count drifted")
    if manifest.get("protocol", {}).get("prompt_sha256") != sha(COMMON_PROMPT.encode("utf-8")):
        raise CandidateError("public prompt identity drifted")
    root = public_root.parent
    arms_by_packet: dict[str, set[str]] = {}
    for split, count in (("development", 4), ("measured", 40)):
        schedule = manifest["schedule"][split]
        if len(schedule) != count or len({row["session_id"] for row in schedule}) != count:
            raise CandidateError("public schedule drifted")
        for row in schedule:
            arms_by_packet.setdefault(row["packet_id"], set()).add(row["arm"])
    if any(arms != {"A", "B"} for arms in arms_by_packet.values()):
        raise CandidateError("public schedule is not paired")
    for packet in manifest["packets"]:
        files = _packet_files(root, packet)
        has_index = "trace_index.json" in files
        if has_index != ("B" in arms_by_packet[packet["packet_id"]]):
            raise CandidateError("trace index presence does not match paired B arm")
        if files.get("readme.json") != README:
            raise CandidateError("public readme drifted")
        if has_index and files["trace_index.json"] != make_index({key: value for key, value in files.items() if key != "trace_index.json"}):
            raise CandidateError("public trace index is not an exact source projection")
    return manifest


def validate(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    receipt = read(root / "preparation.json")
    manifest_path = root / "public" / "manifest.json"
    gold_path = root / "private" / "gold.json"
    manifest = read(manifest_path)
    gold = read(gold_path)
    if receipt.get("schema_version") != PREPARATION_SCHEMA or receipt.get("status") != "local_protocol_candidate_not_execution_authority":
        raise CandidateError("candidate receipt schema/status drifted")
    if sha(manifest_path.read_bytes()) != receipt.get("public_manifest_sha256") or sha(gold_path.read_bytes()) != receipt.get("gold_sha256"):
        raise CandidateError("candidate manifest or gold identity drifted")
    validate_public(root / "public", receipt["public_manifest_sha256"])
    arms_by_packet: dict[str, set[str]] = {}
    for split, count in (("development", 4), ("measured", 40)):
        schedule = manifest["schedule"][split]
        if len(schedule) != count or len({row["session_id"] for row in schedule}) != count:
            raise CandidateError("candidate schedule drifted")
        for row in schedule:
            arms_by_packet.setdefault(row["packet_id"], set()).add(row["arm"])
    if any(arms != {"A", "B"} for arms in arms_by_packet.values()):
        raise CandidateError("candidate schedule is not paired")
    for packet in manifest["packets"]:
        files = _packet_files(root, packet)
        has_index = "trace_index.json" in files
        if has_index != ("B" in arms_by_packet[packet["packet_id"]]):
            raise CandidateError("trace index presence does not match paired B arm")
        if files.get("readme.json") != README:
            raise CandidateError("candidate readme drifted")
        if has_index and files["trace_index.json"] != make_index({key: value for key, value in files.items() if key != "trace_index.json"}):
            raise CandidateError("candidate trace index is not an exact source projection")
    gold_by_packet = {row["packet_id"]: row for row in gold.get("packets", [])}
    if gold.get("schema_version") != GOLD_SCHEMA or set(gold_by_packet) != {row["packet_id"] for row in manifest["packets"]}:
        raise CandidateError("candidate gold inventory drifted")
    for packet_id, row in gold_by_packet.items():
        files = _packet_files(root, next(packet for packet in manifest["packets"] if packet["packet_id"] == packet_id))
        for origin in row["origins"]:
            if set(origin) != {"artifact", "pointer"} or origin["artifact"] not in files or origin["artifact"] == "trace_index.json":
                raise CandidateError("candidate origin schema is invalid")
            pointer(files[origin["artifact"]], origin["pointer"])
    return {
        "status": "validated_local_protocol_candidate_no_execution",
        "packet_count": 22,
        "development_sessions": 4,
        "measured_sessions": 40,
        "reads_per_session": BUDGETS["reads"],
        "provider_turns_per_session": BUDGETS["provider_turns"],
    }


def validate_action(action: Any, *, inventory: set[str], arm: str, reads_used: int, turn: int) -> dict[str, Any]:
    """Validate one model action for the future runtime without executing it."""
    if not isinstance(action, dict) or action.get("action") not in {"read", "final"}:
        raise CandidateError("action must be read or final")
    if action["action"] == "read":
        if set(action) != {"action", "artifact", "pointer"} or not all(isinstance(action[key], str) for key in ("artifact", "pointer")):
            raise CandidateError("read action schema is invalid")
        if reads_used >= BUDGETS["reads"] or turn >= BUDGETS["provider_turns"]:
            raise CandidateError("read budget exhausted; final answer required")
        if action["artifact"] not in inventory or (arm == "A" and action["artifact"] == "trace_index.json"):
            raise CandidateError("artifact is not visible to this arm")
        if action["artifact"] == "trace_index.json" and (not action["pointer"] or not action["pointer"].startswith("/entities/")):
            raise CandidateError("trace index requires an exact entity lookup pointer")
        return dict(action)
    if set(action) != {"action", "answer"} or not isinstance(action["answer"], dict):
        raise CandidateError("final action schema is invalid")
    answer = action["answer"]
    if set(answer) != {"status", "origin_candidates", "affected_use_cases", "evidence_edges", "uncertainty"}:
        raise CandidateError("final answer keys are invalid")
    if answer["status"] not in FINAL_STATUSES or not isinstance(answer["uncertainty"], str):
        raise CandidateError("final status or uncertainty is invalid")
    if not isinstance(answer["origin_candidates"], list) or any(
        not isinstance(row, dict) or set(row) != {"artifact", "pointer"}
        or not all(isinstance(value, str) for value in row.values())
        or not row["pointer"].startswith("/")
        for row in answer["origin_candidates"]
    ):
        raise CandidateError("origin candidates must use exact artifact/pointer pairs")
    if not isinstance(answer["affected_use_cases"], list) or not all(isinstance(value, str) for value in answer["affected_use_cases"]):
        raise CandidateError("affected use cases are invalid")
    if not isinstance(answer["evidence_edges"], list):
        raise CandidateError("evidence edges are invalid")
    for edge in answer["evidence_edges"]:
        if not isinstance(edge, dict) or set(edge) != {"from", "to"}:
            raise CandidateError("evidence edge schema is invalid")
        for endpoint in edge.values():
            if not isinstance(endpoint, dict) or set(endpoint) != {"artifact", "pointer"} or not all(isinstance(value, str) for value in endpoint.values()):
                raise CandidateError("evidence endpoint schema is invalid")
            if not endpoint["pointer"].startswith("/"):
                raise CandidateError("evidence endpoint pointer must be RFC6901")
            if endpoint["artifact"] == "trace_index.json":
                raise CandidateError("final evidence must cite raw sources, not the index")
    return dict(action)


def _pr(predicted: set[bytes], expected: set[bytes]) -> dict[str, Any]:
    hit = len(predicted & expected)
    return {
        "precision": hit / len(predicted) if predicted else None,
        "recall": hit / len(expected) if expected else None,
        "true_positive": hit,
        "predicted": len(predicted),
        "expected": len(expected),
    }


def score_answer(answer: Any, gold: Mapping[str, Any], files: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_action(
        {"action": "final", "answer": answer}, inventory=set(files), arm="A",
        reads_used=0, turn=1,
    )["answer"]
    expected_origins = {canonical(row) for row in gold["origins"]}
    predicted_origins = {canonical(row) for row in validated["origin_candidates"]}
    for origin in validated["origin_candidates"]:
        if origin["artifact"] not in files or origin["artifact"] == "trace_index.json":
            raise CandidateError("origin must cite a visible raw artifact")
        pointer(files[origin["artifact"]], origin["pointer"])
    groups = [{canonical(edge) for edge in group} for group in gold["evidence_groups"]]
    allowed_edges = set().union(*groups) if groups else set()
    predicted_edges: set[bytes] = set()
    resolvable = True
    for edge in validated["evidence_edges"]:
        for endpoint in edge.values():
            try:
                if endpoint["artifact"] not in files or endpoint["artifact"] == "trace_index.json":
                    raise CandidateError("evidence must cite raw artifacts")
                pointer(files[endpoint["artifact"]], endpoint["pointer"])
            except (CandidateError, KeyError, IndexError, TypeError):
                resolvable = False
        predicted_edges.add(canonical(edge))
    hit_groups = sum(bool(group & predicted_edges) for group in groups)
    complete = (
        validated["status"] == gold["status"]
        and predicted_origins == expected_origins
        and hit_groups == len(groups)
        and not predicted_edges - allowed_edges
        and resolvable
    )
    if gold["status"] == "no_fault":
        complete = complete and not validated["affected_use_cases"]
    return {
        "origin": _pr(predicted_origins, expected_origins),
        "evidence": {
            "precision": len(predicted_edges & allowed_edges) / len(predicted_edges) if predicted_edges else None,
            "recall": hit_groups / len(groups) if groups else None,
            "supported_groups": hit_groups,
            "expected_groups": len(groups),
            "raw_endpoints_resolvable": resolvable,
        },
        "use_cases": _pr(
            {canonical(value) for value in validated["affected_use_cases"]},
            {canonical(value) for value in gold["affected_use_cases"]},
        ),
        "complete_supported_diagnosis": complete,
        "clean_false_alarm": gold["status"] == "no_fault" and (
            validated["status"] == "fault" or bool(predicted_origins)
        ),
        "status": validated["status"],
    }


def _verify_raw_answer(row: Mapping[str, Any], raw_root: Path) -> None:
    refs = row.get("raw_refs", [])
    if not isinstance(refs, list) or not refs:
        raise CandidateError("answer has no raw response")
    final = None
    resolved_root = raw_root.resolve(strict=True)
    for ref in refs:
        relative = PurePosixPath(ref["path"])
        if relative.is_absolute() or not relative.parts or relative.parts[0] != row["session_id"] or ".." in relative.parts or "\\" in ref["path"]:
            raise CandidateError("unsafe or misbound raw response path")
        path = raw_root.joinpath(*relative.parts).resolve(strict=True)
        if resolved_root not in path.parents or not path.is_file() or path.is_symlink():
            raise CandidateError("unsafe raw response file")
        raw = path.read_bytes()
        if sha(raw) != ref["sha256"] or len(raw) != ref["bytes"]:
            raise CandidateError("raw response identity mismatch")
        if path.suffix == ".raw":
            final = json.loads(raw)
    if not isinstance(final, dict) or final.get("action") != "final" or final.get("answer") != row.get("answer"):
        raise CandidateError("summary answer differs from preserved final raw response")


def score(root: Path, results_path: Path, output: Path, raw_root: Path | None = None) -> dict[str, Any]:
    validate(root)
    manifest_path = root / "public" / "manifest.json"
    manifest = read(manifest_path)
    results = read(results_path)
    if results.get("schema_version") != "req2web.phase7.e2_runtime_result.v3":
        raise CandidateError("unexpected v3 runtime result schema")
    if results.get("public_manifest_sha256") != sha(manifest_path.read_bytes()):
        raise CandidateError("result/preparation identity mismatch")
    split = results.get("split")
    if split not in {"development", "measured"}:
        raise CandidateError("invalid result split")
    scheduled = manifest["schedule"][split]
    observed_rows = results.get("sessions")
    if not isinstance(observed_rows, list) or len({row.get("session_id") for row in observed_rows if isinstance(row, dict)}) != len(observed_rows):
        raise CandidateError("runtime session inventory is invalid")
    observed = {row["session_id"]: row for row in observed_rows}
    if set(observed) != {row["session_id"] for row in scheduled}:
        raise CandidateError("all scheduled sessions must be accounted for")
    gold = {row["packet_id"]: row for row in read(root / "private" / "gold.json")["packets"]}
    packet_by_id = {row["packet_id"]: row for row in manifest["packets"]}
    rows = []
    paired: dict[str, dict[str, bool]] = {}
    source_root = raw_root or results_path.parent
    for schedule in scheduled:
        runtime_row = observed[schedule["session_id"]]
        if runtime_row.get("packet_id") != schedule["packet_id"] or runtime_row.get("arm") != schedule["arm"]:
            raise CandidateError("runtime session binding drifted")
        expected = gold[schedule["packet_id"]]
        files = _packet_files(root, packet_by_id[schedule["packet_id"]])
        try:
            if runtime_row.get("status") not in FINAL_STATUSES:
                raise CandidateError("non-completed runtime result")
            _verify_raw_answer(runtime_row, source_root)
            scored = score_answer(runtime_row.get("answer"), expected, files)
        except (CandidateError, KeyError, OSError, ValueError, TypeError) as exc:
            runtime_status = runtime_row.get("status", "missing_status")
            scored = {
                "complete_supported_diagnosis": False,
                "status": "invalid_answer" if runtime_status in FINAL_STATUSES else runtime_status,
                "runtime_status": runtime_status,
                "scoring_error": str(exc),
            }
        rows.append({**schedule, "case_id": expected["case_id"], "family": expected["family"], **scored})
        paired.setdefault(schedule["packet_id"], {})[schedule["arm"]] = scored["complete_supported_diagnosis"]
    counts = {"both": 0, "only_A": 0, "only_B": 0, "neither": 0}
    for arms in paired.values():
        bucket = "both" if arms["A"] and arms["B"] else "only_A" if arms["A"] else "only_B" if arms["B"] else "neither"
        counts[bucket] += 1
    report = {
        "schema_version": "req2web.phase7.e2_diagnostic_score.v3",
        "split": split,
        "rows": rows,
        "paired": counts,
        "result_sha256": sha(results_path.read_bytes()),
        "public_manifest_sha256": results["public_manifest_sha256"],
        "scope": "development remains separate; failures retained; no human-usability or causal-root claim",
    }
    write_new(output, report)
    return {"status": "scored", "split": split, "sessions": len(rows), "paired": counts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--source", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("--root", type=Path, required=True)
    score_parser = commands.add_parser("score")
    score_parser.add_argument("--root", type=Path, required=True)
    score_parser.add_argument("--results", type=Path, required=True)
    score_parser.add_argument("--output", type=Path, required=True)
    score_parser.add_argument("--raw-root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.source, args.output)
        elif args.command == "validate":
            result = validate(args.root)
        else:
            result = score(args.root, args.results, args.output, args.raw_root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__, "message": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
