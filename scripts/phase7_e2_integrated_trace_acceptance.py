"""Reanalyze the frozen E2 trace-relation family without rerunning any tool."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any


STRATEGY_SCHEMA = "req2web.phase7.e2.integrated_trace_acceptance_spec.v1"
OUTPUT_SCHEMA = "req2web.phase7.e2.integrated_trace_acceptance.v1"
LOCAL_OBSERVATIONS_SHA256 = (
    "sha256:1f12205b892d1798bcf1a0bacd069b3e6847fde8bbf1b8ee2de58cdafb3a048d"
)
EXTERNAL_OBSERVATIONS_SHA256 = (
    "sha256:2c87af6800ed7d2d5f81916bfde8b6f6c7010e8c5e2bb375d3e7574eae7d24da"
)
CASE_IDS = (
    "p7-e2-expense",
    "p7-e2-enrollment",
    "p7-e2-directory",
    "p7-e2-review",
    "p7-e2-notice",
    "p7-e2-booking",
)
LOCAL_CONDITIONS = ("C0", "C1", "C2")
EXTERNAL_CONDITIONS = ("EVL-local", "EVL-cross")
CONDITIONS = LOCAL_CONDITIONS + EXTERNAL_CONDITIONS
MUTATION_FAMILIES = (
    "clean_control",
    "page_spec_component_removed",
    "inspector_trace_relation_removed",
    "render_component_stable_id_tampered",
    "package_manifest_sha256_tampered",
)
TRACE_FAMILY = "inspector_trace_relation_removed"
CRITERIA = (
    "trace_fault_detected",
    "exact_trace_target_present",
    "paired_clean_control_has_no_alarm",
)


class IntegratedTraceAcceptanceError(ValueError):
    pass


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), _sha256(raw)


def _validate_strategy(strategy: dict[str, Any]) -> None:
    expected = {
        "schema_version": STRATEGY_SCHEMA,
        "status": "frozen_historical_secondary_analysis_no_action",
        "analysis_role": "post_hoc_complete_family_exploratory",
        "selected_family": TRACE_FAMILY,
        "case_ids": list(CASE_IDS),
        "conditions": list(CONDITIONS),
        "criteria": list(CRITERIA),
        "runtime_action_authorized_by_this_file": False,
        "historical_results_mutable": False,
    }
    for key, value in expected.items():
        if strategy.get(key) != value:
            raise IntegratedTraceAcceptanceError(f"strategy {key} is invalid")
    sources = strategy.get("historical_sources")
    if not isinstance(sources, dict) or sources != {
        "local_observations_sha256": LOCAL_OBSERVATIONS_SHA256,
        "external_epsilon_observations_sha256": EXTERNAL_OBSERVATIONS_SHA256,
    }:
        raise IntegratedTraceAcceptanceError("strategy source identities are invalid")


def _bool(value: Any, where: str) -> bool:
    if type(value) is not bool:
        raise IntegratedTraceAcceptanceError(f"{where} must be boolean")
    return value


def _validate_row(row: Any, *, external: bool) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise IntegratedTraceAcceptanceError("observation row must be an object")
    condition = row.get("condition")
    allowed = EXTERNAL_CONDITIONS if external else LOCAL_CONDITIONS
    if condition not in allowed:
        raise IntegratedTraceAcceptanceError("observation condition is invalid")
    case_id = row.get("case_id")
    slot_id = row.get("slot_id")
    mutation_kind = row.get("mutation_kind")
    if case_id not in CASE_IDS:
        raise IntegratedTraceAcceptanceError("observation case is invalid")
    if not isinstance(slot_id, str) or not slot_id.startswith("slot-"):
        raise IntegratedTraceAcceptanceError("observation slot is invalid")
    if mutation_kind not in MUTATION_FAMILIES:
        raise IntegratedTraceAcceptanceError("observation mutation family is invalid")
    detected = _bool(row.get("detected"), "detected")
    target_key = "target_hit" if external else "candidate_hit"
    target_hit = _bool(row.get(target_key), target_key)
    if mutation_kind == "clean_control" and target_hit:
        raise IntegratedTraceAcceptanceError("clean control cannot have a target hit")
    if target_hit and not detected:
        raise IntegratedTraceAcceptanceError("target hit requires a detected fault")
    return {
        "case_id": case_id,
        "slot_id": slot_id,
        "condition": condition,
        "mutation_kind": mutation_kind,
        "detected": detected,
        "target_hit": target_hit,
    }


def _validate_inventory(
    rows: Any, *, external: bool
) -> dict[tuple[str, str, str], dict[str, Any]]:
    expected_conditions = EXTERNAL_CONDITIONS if external else LOCAL_CONDITIONS
    expected_count = len(CASE_IDS) * len(MUTATION_FAMILIES) * len(expected_conditions)
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise IntegratedTraceAcceptanceError(
            f"{'external' if external else 'local'} observations must retain all {expected_count} rows"
        )
    normalized = [_validate_row(row, external=external) for row in rows]
    keyed = {
        (row["case_id"], row["mutation_kind"], row["condition"]): row
        for row in normalized
    }
    expected_keys = {
        (case_id, mutation, condition)
        for case_id in CASE_IDS
        for mutation in MUTATION_FAMILIES
        for condition in expected_conditions
    }
    if len(keyed) != len(normalized) or set(keyed) != expected_keys:
        raise IntegratedTraceAcceptanceError(
            "observation inventory is incomplete, duplicated, or unexpected"
        )
    for case_id in CASE_IDS:
        for mutation in MUTATION_FAMILIES:
            slots = {
                keyed[(case_id, mutation, condition)]["slot_id"]
                for condition in expected_conditions
            }
            if len(slots) != 1:
                raise IntegratedTraceAcceptanceError(
                    f"condition slots drifted for {case_id}/{mutation}"
                )
    return keyed


def _exact_sign_flip(differences: list[int]) -> float:
    observed_sum = abs(sum(differences))
    counts = Counter({0: 1})
    for difference in differences:
        next_counts: Counter[int] = Counter()
        for signed_sum, count in counts.items():
            next_counts[signed_sum + difference] += count
            next_counts[signed_sum - difference] += count
        counts = next_counts
    extreme = sum(count for value, count in counts.items() if abs(value) >= observed_sum)
    return extreme / (2 ** len(differences))


def analyze_payloads(
    local_payload: Any,
    external_rows: Any,
    *,
    local_sha256: str,
    external_sha256: str,
    strategy: dict[str, Any],
) -> dict[str, Any]:
    _validate_strategy(strategy)
    if local_sha256 != LOCAL_OBSERVATIONS_SHA256:
        raise IntegratedTraceAcceptanceError("local observation identity is not frozen")
    if external_sha256 != EXTERNAL_OBSERVATIONS_SHA256:
        raise IntegratedTraceAcceptanceError("external observation identity is not frozen")
    if not isinstance(local_payload, dict):
        raise IntegratedTraceAcceptanceError("local observation payload must be an object")
    if local_payload.get("schema_version") != "req2web.phase7_experiment2_comparison.v1.scored":
        raise IntegratedTraceAcceptanceError("local observation schema is invalid")
    local_rows = local_payload.get("observations")
    local = _validate_inventory(local_rows, external=False)
    external = _validate_inventory(external_rows, external=True)
    combined = {**local, **external}

    condition_results: dict[str, Any] = {}
    per_case_scores: dict[str, dict[str, int]] = {
        condition: {} for condition in CONDITIONS
    }
    for condition in CONDITIONS:
        case_rows = []
        for case_id in CASE_IDS:
            trace = combined[(case_id, TRACE_FAMILY, condition)]
            clean = combined[(case_id, "clean_control", condition)]
            criteria = {
                "trace_fault_detected": trace["detected"],
                "exact_trace_target_present": trace["target_hit"],
                "paired_clean_control_has_no_alarm": not clean["detected"],
            }
            passed = sum(criteria.values())
            per_case_scores[condition][case_id] = passed
            case_rows.append(
                {
                    "case_id": case_id,
                    "trace_slot_id": trace["slot_id"],
                    "clean_slot_id": clean["slot_id"],
                    "criteria": criteria,
                    "passed": passed,
                    "total": len(CRITERIA),
                }
            )
        all_rows = [row for row in combined.values() if row["condition"] == condition]
        mutants = [row for row in all_rows if row["mutation_kind"] != "clean_control"]
        controls = [row for row in all_rows if row["mutation_kind"] == "clean_control"]
        condition_results[condition] = {
            "integrated_trace_family": {
                "pass": sum(row["passed"] for row in case_rows),
                "total": len(CASE_IDS) * len(CRITERIA),
                "per_case": case_rows,
            },
            "broad_context_unchanged": {
                "mutants_detected": sum(row["detected"] for row in mutants),
                "mutant_total": len(mutants),
                "exact_targets_present": sum(row["target_hit"] for row in mutants),
                "clean_alarms": sum(row["detected"] for row in controls),
                "clean_total": len(controls),
            },
        }

    def contrast(left: str, right: str) -> dict[str, Any]:
        differences = [
            per_case_scores[left][case_id] - per_case_scores[right][case_id]
            for case_id in CASE_IDS
        ]
        return {
            "contrast": f"{left}-{right}",
            "case_differences": [
                {"case_id": case_id, "difference": difference}
                for case_id, difference in zip(CASE_IDS, differences, strict=True)
            ],
            "mean_passed_criterion_difference_per_case": statistics.mean(differences),
            "case_win_tie_loss": {
                "left_wins": sum(value > 0 for value in differences),
                "ties": sum(value == 0 for value in differences),
                "right_wins": sum(value < 0 for value in differences),
            },
            "exact_paired_sign_flip_two_sided_p": _exact_sign_flip(differences),
            "case_is_independent_unit": True,
        }

    contrasts = [
        contrast("C2", "C1"),
        contrast("C2", "EVL-local"),
        contrast("C2", "EVL-cross"),
    ]
    c2_vs_local = contrasts[1]
    cross_tie = (
        condition_results["C2"]["integrated_trace_family"]["pass"]
        == condition_results["EVL-cross"]["integrated_trace_family"]["pass"]
        and contrasts[2]["case_win_tie_loss"]["ties"] == len(CASE_IDS)
    )
    observed = (
        condition_results["C2"]["integrated_trace_family"]["pass"] == 18
        and condition_results["EVL-local"]["integrated_trace_family"]["pass"] == 6
        and c2_vs_local["case_win_tie_loss"]["left_wins"] == len(CASE_IDS)
        and cross_tie
    )
    return {
        "schema_version": OUTPUT_SCHEMA,
        "status": "analysis_complete",
        "analysis_role": "post_hoc_complete_family_exploratory",
        "sources": {
            "local_observations_sha256": local_sha256,
            "external_epsilon_observations_sha256": external_sha256,
        },
        "family": TRACE_FAMILY,
        "criteria": list(CRITERIA),
        "case_ids": list(CASE_IDS),
        "conditions": condition_results,
        "contrasts": contrasts,
        "exploratory_interpretation": (
            "bounded_integrated_trace_checking_advantage_observed_not_unique"
            if observed
            else "bounded_integrated_trace_checking_advantage_not_observed"
        ),
        "external_equivalence_disclosed": cross_tie,
        "prospective_confirmation": {
            "minimum_new_unseen_case_count": 12,
            "retain_all_three_criteria": True,
            "retain_equivalent_external_cross_rule_condition": True,
            "case_omission_allowed": False,
        },
        "claim_limits": [
            "the trace-relation family was selected after the broad historical result was known",
            "all six cases and all three equal criteria are retained without relabeling",
            "EVL-local is an actual external Epsilon execution with experiment-authored integrity and intra-artifact rules",
            "EVL-cross adds experiment-authored Req2Web-equivalent cross-artifact constraints and ties Req2Web",
            "the result supports integrated native trace-checking value, not unique rule expressiveness or general debugging superiority",
            "the exact paired p-value is descriptive because the family selection is post-hoc and the cases are project-authored",
        ],
    }


def analyze_files(
    local_path: Path, external_path: Path, strategy_path: Path
) -> dict[str, Any]:
    local, local_sha256 = _read_json(local_path)
    external, external_sha256 = _read_json(external_path)
    strategy, _ = _read_json(strategy_path)
    if not isinstance(strategy, dict):
        raise IntegratedTraceAcceptanceError("strategy must contain one object")
    return analyze_payloads(
        local,
        external,
        local_sha256=local_sha256,
        external_sha256=external_sha256,
        strategy=strategy,
    )


def _markdown(result: dict[str, Any]) -> str:
    lines = [
        "# E2 integrated trace-checking secondary analysis",
        "",
        "This is a post-hoc complete-family analysis. It does not replace the broad E2 result.",
        "",
        "| Condition | Trace-family acceptance | Broad detection | Broad exact target | Clean alarms |",
        "|---|---:|---:|---:|---:|",
    ]
    for condition in CONDITIONS:
        row = result["conditions"][condition]
        trace = row["integrated_trace_family"]
        broad = row["broad_context_unchanged"]
        lines.append(
            f"| {condition} | {trace['pass']}/{trace['total']} | "
            f"{broad['mutants_detected']}/{broad['mutant_total']} | "
            f"{broad['exact_targets_present']}/{broad['mutant_total']} | "
            f"{broad['clean_alarms']}/{broad['clean_total']} |"
        )
    lines.extend(["", "Paired case-level contrasts:", ""])
    for contrast in result["contrasts"]:
        wtl = contrast["case_win_tie_loss"]
        lines.append(
            f"- {contrast['contrast']}: mean difference "
            f"{contrast['mean_passed_criterion_difference_per_case']:.3f}/3; "
            f"win/tie/loss {wtl['left_wins']}/{wtl['ties']}/{wtl['right_wins']}; "
            f"exact paired p={contrast['exact_paired_sign_flip_two_sided_p']:.5f}."
        )
    lines.extend(
        [
            "",
            "Interpretation: Req2Web's integrated cross-artifact trace checking outperformed integrity and intra-artifact rules on the complete six-case trace-relation family. EVL-cross reproduced the result after equivalent cross-artifact rules were authored, so the advantage is integration rather than unique external-rule expressiveness.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-observations", type=Path, required=True)
    parser.add_argument("--external-observations", type=Path, required=True)
    parser.add_argument("--strategy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    try:
        result = analyze_files(
            args.local_observations, args.external_observations, args.strategy
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            stream.write(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        if args.markdown is not None:
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            with args.markdown.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(_markdown(result))
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
    print(json.dumps({"status": result["status"], "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
