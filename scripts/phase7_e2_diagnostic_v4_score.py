"""Fail-closed local scorer for returned Phase 7 E2 v4 results.

The semantic scoring authority remains the v3 complete-supported-diagnosis
implementation.  This adapter verifies the new raw shallow final action and
then supplies its deterministic canonical expansion to that unchanged scorer.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import phase7_e2_diagnostic_v3_candidate as v3
import phase7_e2_diagnostic_v4_protocol as protocol


RESULT_SCHEMA = "req2web.phase7.e2_runtime_result.v4"
SCORE_SCHEMA = "req2web.phase7.e2_diagnostic_score.v4"


class ScoreV4Error(ValueError):
    pass


def _verified_raw_files(row: dict[str, Any], raw_root: Path) -> list[tuple[int, Path, bytes]]:
    refs = row.get("raw_refs")
    if not isinstance(refs, list) or not refs:
        raise ScoreV4Error("answer has no raw response")
    resolved_root = raw_root.resolve(strict=True)
    verified: list[tuple[int, Path, bytes]] = []
    seen_turns: set[int] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            raise ScoreV4Error("raw reference must be an object")
        path_text = ref.get("path")
        turn = ref.get("turn")
        if type(path_text) is not str or type(turn) is not int or turn <= 0 or turn in seen_turns:
            raise ScoreV4Error("raw reference path or turn is invalid")
        relative = PurePosixPath(path_text)
        if (
            relative.is_absolute()
            or not relative.parts
            or relative.parts[0] != row["session_id"]
            or ".." in relative.parts
            or "\\" in path_text
        ):
            raise ScoreV4Error("unsafe or misbound raw response path")
        path = raw_root.joinpath(*relative.parts).resolve(strict=True)
        if resolved_root not in path.parents or not path.is_file() or path.is_symlink():
            raise ScoreV4Error("unsafe raw response file")
        raw = path.read_bytes()
        if v3.sha(raw) != ref.get("sha256") or len(raw) != ref.get("bytes"):
            raise ScoreV4Error("raw response identity mismatch")
        verified.append((turn, path, raw))
        seen_turns.add(turn)
    return sorted(verified)


def verify_raw_answer(
    row: dict[str, Any], raw_root: Path, *, inventory: set[str]
) -> dict[str, Any]:
    finals: list[tuple[int, dict[str, Any]]] = []
    verified = _verified_raw_files(row, raw_root)
    for turn, path, raw in verified:
        if path.suffix != ".raw":
            continue
        action = protocol.parse_model_object(raw)
        if action.get("action") == "final":
            finals.append((turn, protocol.final_to_canonical(action, inventory=inventory)))
    if len(finals) != 1 or finals[0][0] != max(turn for turn, _, _ in verified):
        raise ScoreV4Error("completed session must end with exactly one raw v4 final action")
    answer = finals[0][1]
    if answer != row.get("answer"):
        raise ScoreV4Error("summary answer differs from preserved v4 final raw response")
    return answer


def score(root: Path, results_path: Path, output: Path, raw_root: Path | None = None) -> dict[str, Any]:
    v3.validate(root)
    manifest_path = root / "public" / "manifest.json"
    manifest = v3.read(manifest_path)
    results = v3.read(results_path)
    if results.get("schema_version") != RESULT_SCHEMA:
        raise ScoreV4Error("unexpected v4 runtime result schema")
    if results.get("public_manifest_sha256") != v3.sha(manifest_path.read_bytes()):
        raise ScoreV4Error("result/preparation identity mismatch")
    split = results.get("split")
    if split not in {"development", "measured"}:
        raise ScoreV4Error("invalid result split")
    scheduled = manifest["schedule"][split]
    observed_rows = results.get("sessions")
    if (
        not isinstance(observed_rows, list)
        or len({row.get("session_id") for row in observed_rows if isinstance(row, dict)}) != len(observed_rows)
    ):
        raise ScoreV4Error("runtime session inventory is invalid")
    observed = {row["session_id"]: row for row in observed_rows}
    if set(observed) != {row["session_id"] for row in scheduled}:
        raise ScoreV4Error("all scheduled sessions must be accounted for")
    gold = {row["packet_id"]: row for row in v3.read(root / "private" / "gold.json")["packets"]}
    packet_by_id = {row["packet_id"]: row for row in manifest["packets"]}
    rows = []
    paired: dict[str, dict[str, bool]] = {}
    source_root = raw_root or results_path.parent
    for schedule in scheduled:
        runtime_row = observed[schedule["session_id"]]
        if runtime_row.get("packet_id") != schedule["packet_id"] or runtime_row.get("arm") != schedule["arm"]:
            raise ScoreV4Error("runtime session binding drifted")
        expected = gold[schedule["packet_id"]]
        files = v3._packet_files(root, packet_by_id[schedule["packet_id"]])
        inventory = set(files)
        if schedule["arm"] == "A":
            inventory.discard(protocol.TRACE_INDEX_ARTIFACT)
        try:
            if runtime_row.get("status") not in v3.FINAL_STATUSES:
                raise ScoreV4Error("non-completed runtime result")
            answer = verify_raw_answer(runtime_row, source_root, inventory=inventory)
            scored = v3.score_answer(answer, expected, files)
        except (ScoreV4Error, protocol.ProtocolV4Error, v3.CandidateError, KeyError, OSError, ValueError, TypeError) as exc:
            runtime_status = runtime_row.get("status", "missing_status")
            scored = {
                "complete_supported_diagnosis": False,
                "status": "invalid_answer" if runtime_status in v3.FINAL_STATUSES else runtime_status,
                "runtime_status": runtime_status,
                "scoring_error": str(exc),
            }
        rows.append(
            {
                **schedule,
                "case_id": expected["case_id"],
                "family": expected["family"],
                **scored,
            }
        )
        paired.setdefault(schedule["packet_id"], {})[schedule["arm"]] = scored[
            "complete_supported_diagnosis"
        ]
    counts = {"both": 0, "only_A": 0, "only_B": 0, "neither": 0}
    for arms in paired.values():
        bucket = (
            "both"
            if arms["A"] and arms["B"]
            else "only_A"
            if arms["A"]
            else "only_B"
            if arms["B"]
            else "neither"
        )
        counts[bucket] += 1
    report = {
        "schema_version": SCORE_SCHEMA,
        "split": split,
        "rows": rows,
        "paired": counts,
        "result_sha256": v3.sha(results_path.read_bytes()),
        "public_manifest_sha256": results["public_manifest_sha256"],
        "scope": "v4 wire verification plus unchanged v3 strict semantic scoring; failures retained",
    }
    v3.write_new(output, report)
    return {"status": "scored", "split": split, "sessions": len(rows), "paired": counts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path)
    args = parser.parse_args(argv)
    try:
        result = score(args.root, args.results, args.output, args.raw_root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__, "message": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
