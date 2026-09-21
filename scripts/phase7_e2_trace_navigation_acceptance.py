"""Analyze the frozen E2 traceability evidence and assess future v3 paired runs.

The historical command is a read-only secondary analysis.  The assess command
accepts only the frozen v3 preparation and keeps every scheduled session in the
denominator.  It never launches a model or changes historical evidence.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any, Mapping


HISTORICAL_SCHEMA = "req2web.phase7.e2.trace_navigation_historical.v1"
ASSESSMENT_SCHEMA = "req2web.phase7.e2.trace_navigation_assessment.v1"
LOCAL_SUMMARY_SHA256 = "sha256:a2c96d242728a91c12d5598cb71e5537503f0872d03bbdfaaf8e9e70e96c179b"
EPSILON_SUMMARY_SHA256 = "sha256:b834ca225fcd58efc7362e9f2c89bae76fee320c068684e7ebb93d2f820e8e04"
PUBLIC_MANIFEST_SHA256 = "sha256:824f0d119f3ad5ac25e9579e4ae8eccd51a55845a790695001c3753ebec40574"
GOLD_SHA256 = "sha256:779f1e7bc054c0105689957256423179d37552a414c2a77ebdc1c500408087e5"
TRACE_FAMILIES = frozenset({"wrong_relation", "stale_revision", "two_defects"})
INDEX_ELIGIBLE_FAMILIES = frozenset({"wrong_relation", "two_defects"})
FINAL_STATUSES = frozenset({"fault", "no_fault", "unknown", "unsupported_input"})
EXPECTED_MEASURED_FAMILY_COUNTS = {
    "clean": 4,
    "explicit_error": 2,
    "no_progress": 2,
    "stale_revision": 4,
    "two_defects": 4,
    "wrong_relation": 4,
}
MIN_NET_ADDITIONAL_TRACE_PACKETS = 3
MIN_B_ONLY_TRACE_WINS = 3
MIN_GROUNDED_INDEX_LOOKUP_RATE = 0.5
MAX_B_CLEAN_FALSE_ALARMS = 0
MAX_INVALID_SESSIONS_PER_ARM = 2


class AcceptanceError(ValueError):
    pass


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _load_object(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise AcceptanceError(f"expected a JSON object: {path}")
    return value, raw


def _require_nonnegative_int(value: Any, where: str) -> int:
    if type(value) is not int or value < 0:
        raise AcceptanceError(f"{where} must be a nonnegative integer")
    return value


def _validate_condition(row: Any, *, condition: str, external: bool) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise AcceptanceError(f"missing historical condition {condition}")
    mutant_total = _require_nonnegative_int(row.get("mutant_total"), f"{condition}.mutant_total")
    clean_total_key = "clean_total" if external else "clean_control_total"
    clean_total = _require_nonnegative_int(row.get(clean_total_key), f"{condition}.{clean_total_key}")
    if mutant_total != 24 or clean_total != 6:
        raise AcceptanceError(f"{condition} must retain all 24 mutants and 6 controls")
    detected = _require_nonnegative_int(row.get("detected"), f"{condition}.detected")
    clean_key = "clean_alarms" if external else "clean_control_alarms"
    clean_alarms = _require_nonnegative_int(row.get(clean_key), f"{condition}.{clean_key}")
    target_key = "target_hit" if external else "candidate_hit"
    target_hit = _require_nonnegative_int(row.get(target_key), f"{condition}.{target_key}")
    singleton = _require_nonnegative_int(row.get("single_exact_target"), f"{condition}.single_exact_target")
    if detected > mutant_total or target_hit > mutant_total or singleton > mutant_total or clean_alarms > clean_total:
        raise AcceptanceError(f"historical counts exceed denominators for {condition}")
    per_mutation = row.get("per_mutation")
    expected_mutations = {
        "page_spec_component_removed",
        "inspector_trace_relation_removed",
        "render_component_stable_id_tampered",
        "package_manifest_sha256_tampered",
    }
    if not isinstance(per_mutation, dict) or set(per_mutation) != expected_mutations:
        raise AcceptanceError(f"historical mutation inventory drifted for {condition}")
    normalized = {}
    for name, counts in per_mutation.items():
        if not isinstance(counts, dict):
            raise AcceptanceError(f"invalid mutation row {condition}.{name}")
        total = counts.get("total", 6)
        if total != 6:
            raise AcceptanceError(f"mutation family must retain six rows: {condition}.{name}")
        normalized[name] = {
            "detected": _require_nonnegative_int(counts.get("detected"), f"{condition}.{name}.detected"),
            "target_hit": _require_nonnegative_int(
                counts.get("target_hit" if external else "single_exact_target"),
                f"{condition}.{name}.target",
            ),
        }
    return {
        "detected": detected,
        "mutant_total": mutant_total,
        "target_hit": target_hit,
        "single_exact_target": singleton,
        "clean_alarms": clean_alarms,
        "clean_total": clean_total,
        "per_mutation": normalized,
    }


def analyze_historical_payloads(
    local: dict[str, Any],
    external: dict[str, Any],
    *,
    local_sha256: str,
    external_sha256: str,
) -> dict[str, Any]:
    if local_sha256 != LOCAL_SUMMARY_SHA256 or external_sha256 != EPSILON_SUMMARY_SHA256:
        raise AcceptanceError("historical summary identity does not match the frozen evidence")
    if set(local) != {"C0", "C1", "C2"} or set(external) != {"EVL-local", "EVL-cross"}:
        raise AcceptanceError("historical condition inventory drifted")
    normalized_local = {
        name: _validate_condition(local[name], condition=name, external=False)
        for name in ("C0", "C1", "C2")
    }
    normalized_external = {
        name: _validate_condition(external[name], condition=name, external=True)
        for name in ("EVL-local", "EVL-cross")
    }
    c1 = normalized_local["C1"]
    c2 = normalized_local["C2"]
    evl_local = normalized_external["EVL-local"]
    evl_cross = normalized_external["EVL-cross"]
    trace_key = "inspector_trace_relation_removed"
    return {
        "schema_version": HISTORICAL_SCHEMA,
        "status": "analysis_complete",
        "analysis_role": "post_hoc_structural_context_not_new_execution",
        "sources": {
            "local_comparison_summary_sha256": local_sha256,
            "external_epsilon_summary_sha256": external_sha256,
        },
        "conditions": {**normalized_local, **normalized_external},
        "contrasts": {
            "req2web_c2_minus_local_reference_c1": {
                "additional_detected_mutants": c2["detected"] - c1["detected"],
                "additional_exact_targets_present": c2["target_hit"] - c1["target_hit"],
                "additional_trace_relation_defects_detected": (
                    c2["per_mutation"][trace_key]["detected"]
                    - c1["per_mutation"][trace_key]["detected"]
                ),
                "clean_alarm_difference": c2["clean_alarms"] - c1["clean_alarms"],
            },
            "external_cross_minus_external_local": {
                "additional_detected_mutants": evl_cross["detected"] - evl_local["detected"],
                "additional_exact_targets_present": evl_cross["target_hit"] - evl_local["target_hit"],
                "additional_trace_relation_defects_detected": (
                    evl_cross["per_mutation"][trace_key]["detected"]
                    - evl_local["per_mutation"][trace_key]["detected"]
                ),
                "clean_alarm_difference": evl_cross["clean_alarms"] - evl_local["clean_alarms"],
            },
            "req2web_c2_minus_external_cross": {
                "detected_mutant_difference": c2["detected"] - evl_cross["detected"],
                "exact_target_presence_difference": c2["target_hit"] - evl_cross["target_hit"],
                "clean_alarm_difference": c2["clean_alarms"] - evl_cross["clean_alarms"],
                "supplementary_singleton_difference_not_primary": (
                    c2["single_exact_target"] - evl_cross["single_exact_target"]
                ),
            },
        },
        "interpretation": "built_in_cross_artifact_trace_coverage_observed_external_equivalence_disclosed",
        "claim_limits": [
            "the 24 mutations are post-hoc project-authored artifact defects, not independent natural Agent failures",
            "Req2Web adds coverage over integrity and selected local-reference checks for missing cross-artifact relations",
            "an equivalently configured external EVL engine ties Req2Web on detection and exact target presence",
            "singleton candidate counts are output-specific and are not top-1 causal-root accuracy",
            "the prospective paired Agent experiment tests navigation assistance, not unique rule expressiveness",
        ],
    }


def analyze_historical_files(local_path: Path, external_path: Path) -> dict[str, Any]:
    local, local_raw = _load_object(local_path)
    external, external_raw = _load_object(external_path)
    return analyze_historical_payloads(
        local,
        external,
        local_sha256=_sha(local_raw),
        external_sha256=_sha(external_raw),
    )


def _pointer_unescape(value: str) -> str:
    if "~" in value:
        index = 0
        while index < len(value):
            if value[index] == "~" and (index + 1 == len(value) or value[index + 1] not in "01"):
                raise AcceptanceError("trace index pointer contains invalid RFC6901 escaping")
            index += 2 if value[index] == "~" else 1
    return value.replace("~1", "/").replace("~0", "~")


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)


def audit_tool_reads(records: list[dict[str, Any]]) -> dict[str, Any]:
    observed_strings: set[str] = set()
    index_reads = 0
    grounded_exact_index_reads = 0
    violations: list[str] = []
    previous_turn = 0
    for record in records:
        turn = record.get("turn")
        if type(turn) is not int or turn <= previous_turn:
            raise AcceptanceError("tool-read records must have unique increasing turn numbers")
        previous_turn = turn
        request = record.get("request")
        response = record.get("response")
        if not isinstance(request, dict) or not isinstance(response, dict):
            raise AcceptanceError("tool-read record must preserve request and response")
        artifact = request.get("artifact")
        pointer = request.get("pointer")
        if response.get("artifact") != artifact or response.get("pointer") != pointer:
            raise AcceptanceError("tool-read request/response binding drifted")
        if artifact == "trace_index.json":
            index_reads += 1
            prefix = "/entities/"
            if not isinstance(pointer, str) or not pointer.startswith(prefix):
                violations.append(f"turn_{turn}:non_exact_index_pointer")
                continue
            escaped = pointer[len(prefix):]
            if not escaped or "/" in escaped:
                violations.append(f"turn_{turn}:non_exact_index_pointer")
                continue
            entity = _pointer_unescape(escaped)
            if entity not in observed_strings:
                violations.append(f"turn_{turn}:index_entity_not_previously_observed")
                continue
            grounded_exact_index_reads += 1
        else:
            observed_strings.update(_strings(response.get("value")))
    return {
        "total_reads": len(records),
        "raw_artifact_reads": len(records) - index_reads,
        "index_reads": index_reads,
        "grounded_exact_index_reads": grounded_exact_index_reads,
        "violations": violations,
    }


def _session_tool_reads(raw_root: Path, session_id: str) -> list[dict[str, Any]]:
    root = raw_root.resolve(strict=True)
    session = (root / session_id).resolve(strict=True)
    if root not in session.parents or not session.is_dir() or session.is_symlink():
        raise AcceptanceError(f"unsafe or missing raw session directory: {session_id}")
    records = []
    for path in sorted(session.glob("turn_*.tool_read.json")):
        resolved = path.resolve(strict=True)
        if session not in resolved.parents or not resolved.is_file() or resolved.is_symlink():
            raise AcceptanceError(f"unsafe tool-read record: {path}")
        value, _ = _load_object(resolved)
        try:
            turn = int(path.name.split("_")[1].split(".")[0])
        except (IndexError, ValueError) as exc:
            raise AcceptanceError(f"invalid tool-read filename: {path.name}") from exc
        records.append({**value, "turn": turn})
    return records


def _mean(values: list[float | int]) -> float | None:
    return statistics.mean(values) if values else None


def assess_payloads(
    *,
    score: dict[str, Any],
    runtime: dict[str, Any],
    manifest: dict[str, Any],
    gold: dict[str, Any],
    split: str,
    runtime_sha256: str,
    manifest_sha256: str,
    gold_sha256: str,
    read_audits: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    if split not in {"development", "measured"}:
        raise AcceptanceError("split must be development or measured")
    if manifest_sha256 != PUBLIC_MANIFEST_SHA256 or gold_sha256 != GOLD_SHA256:
        raise AcceptanceError("assessment preparation identity is not the frozen v3 candidate")
    if manifest.get("schema_version") != "req2web.phase7.e2_public.v3":
        raise AcceptanceError("unexpected public manifest schema")
    if gold.get("schema_version") != "req2web.phase7.e2_gold.v3":
        raise AcceptanceError("unexpected gold schema")
    if runtime.get("schema_version") != "req2web.phase7.e2_runtime_result.v3" or runtime.get("split") != split:
        raise AcceptanceError("runtime result schema or split drifted")
    if score.get("schema_version") != "req2web.phase7.e2_diagnostic_score.v3" or score.get("split") != split:
        raise AcceptanceError("score schema or split drifted")
    bare_manifest_sha = manifest_sha256.removeprefix("sha256:")
    if runtime.get("public_manifest_sha256") != bare_manifest_sha or score.get("public_manifest_sha256") != bare_manifest_sha:
        raise AcceptanceError("runtime/score public manifest binding drifted")
    if score.get("result_sha256") != runtime_sha256.removeprefix("sha256:"):
        raise AcceptanceError("score does not bind the supplied runtime result")

    schedule = manifest.get("schedule", {}).get(split)
    runtime_rows = runtime.get("sessions")
    score_rows = score.get("rows")
    if not isinstance(schedule, list) or not isinstance(runtime_rows, list) or not isinstance(score_rows, list):
        raise AcceptanceError("schedule, runtime sessions, and score rows must be arrays")
    expected_count = 4 if split == "development" else 40
    if len(schedule) != expected_count or len(runtime_rows) != expected_count or len(score_rows) != expected_count:
        raise AcceptanceError(f"{split} must retain all {expected_count} scheduled sessions")

    def keyed(rows: list[dict[str, Any]], where: str) -> dict[str, dict[str, Any]]:
        if any(not isinstance(row, dict) or not isinstance(row.get("session_id"), str) for row in rows):
            raise AcceptanceError(f"invalid {where} row")
        result = {row["session_id"]: row for row in rows}
        if len(result) != len(rows):
            raise AcceptanceError(f"duplicate {where} session")
        return result

    scheduled = keyed(schedule, "schedule")
    observed = keyed(runtime_rows, "runtime")
    scored = keyed(score_rows, "score")
    if set(scheduled) != set(observed) or set(scheduled) != set(scored) or set(read_audits) != set(scheduled):
        raise AcceptanceError("scheduled/runtime/scored/read-audit session inventories differ")

    gold_rows = gold.get("packets")
    if not isinstance(gold_rows, list):
        raise AcceptanceError("gold packet inventory is invalid")
    split_gold = [row for row in gold_rows if isinstance(row, dict) and row.get("split") == split]
    expected_packets = 2 if split == "development" else 20
    gold_by_packet = {row.get("packet_id"): row for row in split_gold}
    if len(split_gold) != expected_packets or len(gold_by_packet) != expected_packets:
        raise AcceptanceError(f"{split} gold must retain all {expected_packets} packets")
    if split == "measured":
        family_counts = defaultdict(int)
        for row in split_gold:
            family_counts[row.get("family")] += 1
        if dict(sorted(family_counts.items())) != EXPECTED_MEASURED_FAMILY_COUNTS:
            raise AcceptanceError("measured family inventory drifted")

    enriched: list[dict[str, Any]] = []
    for session_id, schedule_row in scheduled.items():
        runtime_row = observed[session_id]
        score_row = scored[session_id]
        packet_id = schedule_row.get("packet_id")
        arm = schedule_row.get("arm")
        if packet_id not in gold_by_packet or arm not in {"A", "B"}:
            raise AcceptanceError("scheduled packet or arm is invalid")
        expected = gold_by_packet[packet_id]
        for row, where in ((runtime_row, "runtime"), (score_row, "score")):
            if row.get("packet_id") != packet_id or row.get("arm") != arm:
                raise AcceptanceError(f"{where} row binding drifted for {session_id}")
        if score_row.get("case_id") != expected.get("case_id") or score_row.get("family") != expected.get("family"):
            raise AcceptanceError(f"score/gold binding drifted for {session_id}")
        audit = read_audits[session_id]
        for key in ("total_reads", "raw_artifact_reads", "index_reads", "grounded_exact_index_reads"):
            _require_nonnegative_int(audit.get(key), f"{session_id}.{key}")
        if not isinstance(audit.get("violations"), list):
            raise AcceptanceError(f"{session_id}.violations must be a list")
        runtime_reads = _require_nonnegative_int(runtime_row.get("reads"), f"{session_id}.reads")
        runtime_status = runtime_row.get("status")
        invalid = runtime_status not in FINAL_STATUSES or score_row.get("status") == "invalid_answer" or "scoring_error" in score_row
        unattributed_read_attempts = runtime_reads - audit["total_reads"]
        if unattributed_read_attempts < 0 or unattributed_read_attempts > 1:
            raise AcceptanceError(f"runtime/tool-read count mismatch for {session_id}")
        if unattributed_read_attempts and not invalid:
            raise AcceptanceError(f"completed session lacks a tool-read receipt: {session_id}")
        complete = bool(score_row.get("complete_supported_diagnosis")) and not invalid
        origin_tp = score_row.get("origin", {}).get("true_positive", 0) if not invalid else 0
        evidence_tp = score_row.get("evidence", {}).get("supported_groups", 0) if not invalid else 0
        expected_origins = len(expected.get("origins", []))
        expected_groups = len(expected.get("evidence_groups", []))
        if not all(type(value) is int and value >= 0 for value in (origin_tp, evidence_tp)):
            raise AcceptanceError(f"invalid scored metric for {session_id}")
        if origin_tp > expected_origins or evidence_tp > expected_groups:
            raise AcceptanceError(f"scored metric exceeds gold for {session_id}")
        enriched.append({
            "session_id": session_id,
            "packet_id": packet_id,
            "case_id": expected["case_id"],
            "family": expected["family"],
            "gold_status": expected["status"],
            "arm": arm,
            "runtime_status": runtime_status,
            "invalid_or_noncompleted": invalid,
            "complete_supported_diagnosis": complete,
            "clean_false_alarm": bool(score_row.get("clean_false_alarm")) if not invalid else False,
            "clean_correct_no_fault": expected["status"] == "no_fault" and complete,
            "origin_true_positive": origin_tp,
            "origin_expected": expected_origins,
            "evidence_groups_supported": evidence_tp,
            "evidence_groups_expected": expected_groups,
            "reads": runtime_reads,
            "raw_artifact_reads": audit["raw_artifact_reads"],
            "index_reads": audit["index_reads"],
            "grounded_exact_index_reads": audit["grounded_exact_index_reads"],
            "unattributed_rejected_read_attempts": unattributed_read_attempts,
            "trace_lookup_protocol_violations": list(audit["violations"]),
            "provider_turns": _require_nonnegative_int(runtime_row.get("provider_turns"), f"{session_id}.provider_turns"),
            "input_tokens": _require_nonnegative_int(runtime_row.get("input_tokens"), f"{session_id}.input_tokens"),
            "elapsed_seconds": runtime_row.get("elapsed_seconds"),
        })

    summaries: dict[str, Any] = {}
    for arm in ("A", "B"):
        rows = [row for row in enriched if row["arm"] == arm]
        origin_expected = sum(row["origin_expected"] for row in rows)
        evidence_expected = sum(row["evidence_groups_expected"] for row in rows)
        elapsed = [row["elapsed_seconds"] for row in rows if type(row["elapsed_seconds"]) in {int, float} and row["elapsed_seconds"] >= 0]
        summaries[arm] = {
            "sessions": len(rows),
            "complete_supported_diagnoses": sum(row["complete_supported_diagnosis"] for row in rows),
            "invalid_or_noncompleted": sum(row["invalid_or_noncompleted"] for row in rows),
            "clean_false_alarms": sum(row["clean_false_alarm"] for row in rows),
            "clean_correct_no_fault": sum(row["clean_correct_no_fault"] for row in rows),
            "micro_exact_origin_recall": (sum(row["origin_true_positive"] for row in rows) / origin_expected if origin_expected else None),
            "micro_required_evidence_group_recall": (sum(row["evidence_groups_supported"] for row in rows) / evidence_expected if evidence_expected else None),
            "mean_total_reads": _mean([row["reads"] for row in rows]),
            "mean_raw_artifact_reads": _mean([row["raw_artifact_reads"] for row in rows]),
            "mean_index_reads": _mean([row["index_reads"] for row in rows]),
            "mean_provider_turns": _mean([row["provider_turns"] for row in rows]),
            "mean_input_tokens": _mean([row["input_tokens"] for row in rows]),
            "mean_elapsed_seconds": _mean(elapsed),
        }

    packet_pairs: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in enriched:
        packet_pairs[row["packet_id"]][row["arm"]] = row
    if any(set(pair) != {"A", "B"} for pair in packet_pairs.values()) or len(packet_pairs) != expected_packets:
        raise AcceptanceError("every packet must retain exactly one A/B pair")

    trace_pairs = [
        (packet_id, pair)
        for packet_id, pair in packet_pairs.items()
        if pair["A"]["family"] in TRACE_FAMILIES
    ]
    paired_counts = {"both": 0, "only_A": 0, "only_B": 0, "neither": 0}
    for _, pair in trace_pairs:
        a = pair["A"]["complete_supported_diagnosis"]
        b = pair["B"]["complete_supported_diagnosis"]
        paired_counts["both" if a and b else "only_A" if a else "only_B" if b else "neither"] += 1

    violations = [
        {"session_id": row["session_id"], "violations": row["trace_lookup_protocol_violations"]}
        for row in enriched if row["trace_lookup_protocol_violations"]
    ]
    eligible_b = [row for row in enriched if row["arm"] == "B" and row["family"] in INDEX_ELIGIBLE_FAMILIES]
    grounded_b = sum(row["grounded_exact_index_reads"] > 0 for row in eligible_b)
    grounded_rate = grounded_b / len(eligible_b) if eligible_b else None

    if split == "development":
        gate_checks = {
            "all_sessions_protocol_complete": all(not row["invalid_or_noncompleted"] for row in enriched),
            "no_trace_lookup_protocol_violation": not violations,
            "eligible_b_uses_grounded_exact_index": grounded_b >= 1,
        }
        return {
            "schema_version": ASSESSMENT_SCHEMA,
            "status": "assessment_complete",
            "split": split,
            "development_gate_passed": all(gate_checks.values()),
            "gate_checks": gate_checks,
            "arms": summaries,
            "rows": enriched,
            "measured_thresholds_evaluated": False,
            "claim_allowed": False,
            "next_action": "one measured batch may proceed only when the development gate passes; otherwise create a new protocol version rather than rerunning this batch",
        }

    if len(trace_pairs) != 12 or len(eligible_b) != 8:
        raise AcceptanceError("frozen measured trace population drifted")
    clean_a = summaries["A"]["clean_false_alarms"]
    clean_b = summaries["B"]["clean_false_alarms"]
    invalid_a = summaries["A"]["invalid_or_noncompleted"]
    invalid_b = summaries["B"]["invalid_or_noncompleted"]
    net_b = paired_counts["only_B"] - paired_counts["only_A"]
    checks = {
        "all_twenty_pairs_present": len(packet_pairs) == 20,
        "twelve_trace_pairs_present": len(trace_pairs) == 12,
        "b_has_at_least_three_net_additional_complete_trace_packets": net_b >= MIN_NET_ADDITIONAL_TRACE_PACKETS,
        "b_has_at_least_three_b_only_trace_wins": paired_counts["only_B"] >= MIN_B_ONLY_TRACE_WINS,
        "no_trace_lookup_protocol_violations": not violations,
        "grounded_index_lookup_rate_at_least_half": grounded_rate is not None and grounded_rate >= MIN_GROUNDED_INDEX_LOOKUP_RATE,
        "b_clean_false_alarms_do_not_exceed_a": clean_b <= clean_a,
        "b_has_zero_clean_false_alarms": clean_b <= MAX_B_CLEAN_FALSE_ALARMS,
        "b_invalid_or_noncompleted_do_not_exceed_a": invalid_b <= invalid_a,
        "both_arms_have_at_most_two_invalid_or_noncompleted_sessions": max(invalid_a, invalid_b) <= MAX_INVALID_SESSIONS_PER_ARM,
    }
    supported = all(checks.values())
    efficiency_checks = {
        "primary_acceptance_passed": supported,
        "all_read_attempts_have_tool_receipts": not any(row["unattributed_rejected_read_attempts"] for row in enriched),
        "b_mean_raw_artifact_reads_do_not_exceed_a": summaries["B"]["mean_raw_artifact_reads"] <= summaries["A"]["mean_raw_artifact_reads"],
        "b_mean_total_reads_at_most_one_above_a": summaries["B"]["mean_total_reads"] <= summaries["A"]["mean_total_reads"] + 1.0,
    }
    return {
        "schema_version": ASSESSMENT_SCHEMA,
        "status": "assessment_complete",
        "split": split,
        "sources": {
            "runtime_result_sha256": runtime_sha256,
            "public_manifest_sha256": manifest_sha256,
            "private_gold_sha256": gold_sha256,
        },
        "arms": summaries,
        "trace_population": {
            "families": sorted(TRACE_FAMILIES),
            "packet_pairs": len(trace_pairs),
            "paired_complete_supported_diagnosis": paired_counts,
            "net_additional_complete_trace_packets_for_b": net_b,
        },
        "manipulation_check": {
            "index_eligible_b_sessions": len(eligible_b),
            "sessions_with_grounded_exact_index_lookup": grounded_b,
            "grounded_exact_index_lookup_rate": grounded_rate,
            "protocol_violations": violations,
        },
        "acceptance_checks": checks,
        "interpretation": (
            "bounded_trace_navigation_advantage_observed"
            if supported else "bounded_trace_navigation_advantage_not_observed"
        ),
        "efficiency_checks": efficiency_checks,
        "efficiency_interpretation": (
            "bounded_raw_search_burden_advantage_observed"
            if all(efficiency_checks.values()) else "raw_search_burden_advantage_not_established"
        ),
        "rows": enriched,
        "claim_limits": [
            "this is a bounded paired study over four project-authored case clusters and does not establish statistical or general root-cause superiority",
            "unknown, unsupported, invalid, timed-out, and missing outcomes are retained as failures rather than removed",
            "the index is verdict-free and final evidence must cite raw artifacts, but the result still evaluates this specific model and protocol",
            "the historical external EVL comparison remains tied with Req2Web on static detection and exact target presence",
        ],
    }


def assess_files(
    *, preparation_root: Path, score_path: Path, runtime_path: Path, raw_root: Path
) -> dict[str, Any]:
    manifest_path = preparation_root / "public" / "manifest.json"
    gold_path = preparation_root / "private" / "gold.json"
    manifest, manifest_raw = _load_object(manifest_path)
    gold, gold_raw = _load_object(gold_path)
    score, _ = _load_object(score_path)
    runtime, runtime_raw = _load_object(runtime_path)
    split = runtime.get("split")
    schedule = manifest.get("schedule", {}).get(split)
    if not isinstance(schedule, list):
        raise AcceptanceError("runtime split is absent from the manifest schedule")
    read_audits = {}
    for row in schedule:
        session_id = row.get("session_id")
        if not isinstance(session_id, str):
            raise AcceptanceError("schedule session_id is invalid")
        read_audits[session_id] = audit_tool_reads(_session_tool_reads(raw_root, session_id))
    return assess_payloads(
        score=score,
        runtime=runtime,
        manifest=manifest,
        gold=gold,
        split=split,
        runtime_sha256=_sha(runtime_raw),
        manifest_sha256=_sha(manifest_raw),
        gold_sha256=_sha(gold_raw),
        read_audits=read_audits,
    )


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _historical_markdown(result: Mapping[str, Any]) -> str:
    conditions = result["conditions"]
    lines = [
        "# E2 traceability acceptance context",
        "",
        "This is a post-hoc analysis of preserved local evidence; no detector, model, or external tool was rerun.",
        "",
        "| Condition | Detected | Exact target present | Clean alarms |",
        "|---|---:|---:|---:|",
    ]
    for name in ("C1", "C2", "EVL-local", "EVL-cross"):
        row = conditions[name]
        lines.append(f"| {name} | {row['detected']}/24 | {row['target_hit']}/24 | {row['clean_alarms']}/6 |")
    contrast = result["contrasts"]["req2web_c2_minus_local_reference_c1"]
    lines.extend([
        "",
        f"Req2Web C2 adds {contrast['additional_detected_mutants']} detections and {contrast['additional_exact_targets_present']} exact target hits over C1; all {contrast['additional_trace_relation_defects_detected']} additional detections are missing cross-artifact Inspector relations.",
        "",
        "Epsilon with equivalent cross-artifact rules ties Req2Web on the primary detection and exact-target-presence metrics. The planned paired Agent study therefore tests source-grounded navigation assistance, not unique rule expressiveness.",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    historical = commands.add_parser("historical")
    historical.add_argument("--local-summary", type=Path, required=True)
    historical.add_argument("--external-summary", type=Path, required=True)
    historical.add_argument("--output", type=Path, required=True)
    historical.add_argument("--markdown", type=Path)
    assess = commands.add_parser("assess")
    assess.add_argument("--preparation-root", type=Path, required=True)
    assess.add_argument("--score", type=Path, required=True)
    assess.add_argument("--runtime", type=Path, required=True)
    assess.add_argument("--raw-root", type=Path, required=True)
    assess.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "historical":
            result = analyze_historical_files(args.local_summary, args.external_summary)
            _write_new(args.output, result)
            if args.markdown is not None:
                args.markdown.parent.mkdir(parents=True, exist_ok=True)
                with args.markdown.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(_historical_markdown(result))
        else:
            result = assess_files(
                preparation_root=args.preparation_root,
                score_path=args.score,
                runtime_path=args.runtime,
                raw_root=args.raw_root,
            )
            _write_new(args.output, result)
    except (OSError, UnicodeError, json.JSONDecodeError, AcceptanceError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__, "message": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": result["status"], "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
