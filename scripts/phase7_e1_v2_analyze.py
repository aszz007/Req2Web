"""Aggregate E1 v2 browser evidence without collapsing distinct claims."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import random
import statistics
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from phase7_e1_v2_cases import cases, frozen_input_identity
from phase7_e1_v2_observe import ARMS, REPLAY_CASE_IDS, observer_identity, obligations_for_case


SCHEMA = "req2web.phase7.e1_v2.analysis.v1"
STUDY_LABEL = "Phase 7 E1 v2"
INFERENCE_STATUS = "exploratory_because_cases_informed_v19_repair"


class AnalysisError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AnalysisError(f"{path.name} must contain one object")
    return value


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(probability * len(ordered)))]


def _paired_bootstrap(case_scores: dict[str, dict[str, int]], left: str, right: str) -> dict[str, Any]:
    case_ids = sorted(case_scores)
    differences = [case_scores[case_id][left] - case_scores[case_id][right] for case_id in case_ids]
    rng = random.Random(20260917)
    samples = [
        statistics.mean(rng.choice(differences) for _ in differences)
        for _ in range(10_000)
    ]
    observed_mean = statistics.mean(differences)
    sign_flip_means = [
        statistics.mean(
            difference * sign
            for difference, sign in zip(differences, signs, strict=True)
        )
        for signs in itertools.product((-1, 1), repeat=len(differences))
    ]
    exact_two_sided_p = sum(
        abs(value) >= abs(observed_mean) - 1e-12
        for value in sign_flip_means
    ) / len(sign_flip_means)
    return {
        "contrast": f"{left}-{right}",
        "unit": "passed_obligations_per_case_out_of_6",
        "case_differences": [
            {"case_id": case_id, "difference": case_scores[case_id][left] - case_scores[case_id][right]}
            for case_id in case_ids
        ],
        "mean_difference": observed_mean,
        "descriptive_case_bootstrap_95_interval": [_percentile(samples, 0.025), _percentile(samples, 0.975)],
        "case_win_tie_loss": {
            "left_wins": sum(value > 0 for value in differences),
            "ties": sum(value == 0 for value in differences),
            "right_wins": sum(value < 0 for value in differences),
        },
        "exact_paired_sign_flip_two_sided_p": exact_two_sided_p,
        "exact_sign_flip_assignments": len(sign_flip_means),
        "bootstrap_seed": 20260917,
        "bootstrap_samples": 10_000,
        "generalization_claimed": False,
    }


def analyze(*, observation_path: Path, runtime_manifest_path: Path) -> dict[str, Any]:
    observation = _read(observation_path)
    runtime = _read(runtime_manifest_path)
    if (
        observation.get("status") != "observation_complete"
        or observation.get("split") != "measured"
        or observation.get("input_identity") != frozen_input_identity()
        or observation.get("observer_identity") != observer_identity()
        or observation.get("artifact_manifest_sha256") != _sha(runtime_manifest_path.read_bytes())
        or runtime.get("split") != "measured"
        or runtime.get("automatic_retry_count") != 0
    ):
        raise AnalysisError("measured observation or runtime binding is invalid")
    primary = observation.get("primary", {}).get("rows")
    replay = observation.get("supplementary_replay", {}).get("rows")
    runtime_rows = runtime.get("rows")
    measured_cases = [row for row in cases() if row["split"] == "measured"]
    case_count = len(measured_cases)
    obligation_count = 6
    expected_primary_count = case_count * len(ARMS) * obligation_count
    expected_replay_count = len(REPLAY_CASE_IDS) * len(ARMS) * obligation_count
    if not isinstance(primary, list) or len(primary) != expected_primary_count or not isinstance(replay, list) or len(replay) != expected_replay_count:
        raise AnalysisError("measured browser row inventory is incomplete")
    if not isinstance(runtime_rows, list) or len(runtime_rows) != case_count * len(ARMS):
        raise AnalysisError("measured runtime row inventory is incomplete")

    runtime_by_key = {(row["case_id"], row["arm"]): row for row in runtime_rows}
    if len(runtime_by_key) != case_count * len(ARMS):
        raise AnalysisError("measured runtime contains duplicate case/arm rows")
    expected_primary_keys = {
        (case["case_id"], arm, criterion)
        for case in measured_cases
        for arm in ARMS
        for criterion in obligations_for_case(case["case_id"])
    }
    primary_keys = {(row.get("case_id"), row.get("arm"), row.get("criterion_id")) for row in primary}
    if primary_keys != expected_primary_keys or len(primary_keys) != len(primary):
        raise AnalysisError("measured primary observation keys are incomplete or duplicated")
    case_scores: dict[str, dict[str, int]] = {
        case["case_id"]: {arm: 0 for arm in ARMS} for case in measured_cases
    }
    arms: dict[str, Any] = {}
    for arm in ARMS:
        selected = [row for row in primary if row.get("arm") == arm]
        arm_total = case_count * obligation_count
        if len(selected) != arm_total:
            raise AnalysisError(f"arm {arm} does not have {arm_total} primary observations")
        counts = {label: sum(row.get("label") == label for row in selected) for label in ("pass", "fail", "unknown")}
        for row in selected:
            if row.get("case_id") not in case_scores or row.get("label") not in counts:
                raise AnalysisError("primary observation identity or label is invalid")
            case_scores[row["case_id"]][arm] += int(row["label"] == "pass")
        per_case = [
            {"case_id": case["case_id"], "family": case["family"], "passed": case_scores[case["case_id"]][arm], "total": obligation_count}
            for case in measured_cases
        ]
        family = {}
        for family_name in sorted({case["family"] for case in measured_cases}):
            rows = [row for row in selected if row.get("family") == family_name]
            family[family_name] = {
                "pass": sum(row.get("label") == "pass" for row in rows),
                "fail": sum(row.get("label") == "fail" for row in rows),
                "unknown": sum(row.get("label") == "unknown" for row in rows),
                "total": len(rows),
            }
        native = [runtime_by_key[(case["case_id"], arm)] for case in measured_cases]
        claimed = [row for row in native if arm == "A" and row.get("status") == "completed_model_delivery"]
        claimed_failures = [
            row for row in claimed
            if case_scores[row["case_id"]][arm] < obligation_count
        ]
        resources = {
            "started_calls": sum(int(row.get("started_calls", 0)) for row in native),
            "elapsed_seconds": round(sum(float(row.get("elapsed_seconds", 0.0)) for row in native), 3),
            "input_tokens": sum(int(row.get("metrics", {}).get("input_tokens", 0)) for row in native),
            "output_tokens": sum(int(row.get("metrics", {}).get("output_tokens", 0)) for row in native),
        }
        arms[arm] = {
            "primary": {
                **counts,
                "total": arm_total,
                "coverage_lower_bound": counts["pass"] / arm_total,
                "coverage_upper_bound": (counts["pass"] + counts["unknown"]) / arm_total,
                "all_six_cases": sum(row["passed"] == obligation_count for row in per_case),
                "case_count": case_count,
                "per_case": per_case,
                "per_family": family,
            },
            "native_status_counts": {
                status: sum(row.get("status") == status for row in native)
                for status in sorted({str(row.get("status")) for row in native})
            },
            "delivery_calibration": {
                "native_success_claim_policy": "completed_model_delivery_only" if arm == "A" else "no_native_behavior_success_claim",
                "claimed_success_cases": len(claimed),
                "claimed_success_with_any_independent_failure_or_unknown": len(claimed_failures),
                "unconditional_all_six_case_yield": sum(row["passed"] == obligation_count for row in per_case) / case_count,
            },
            "resources": resources,
        }

    primary_by_key = {
        (row["case_id"], row["arm"], row["criterion_id"]): row for row in primary
    }
    replay_checks = []
    for row in replay:
        key = (row.get("case_id"), row.get("arm"), row.get("criterion_id"))
        original = primary_by_key.get(key)
        if original is None or row.get("case_id") not in REPLAY_CASE_IDS:
            raise AnalysisError("supplementary replay binding is invalid")
        replay_checks.append({
            "case_id": row["case_id"], "arm": row["arm"], "criterion_id": row["criterion_id"],
            "same_label": row.get("label") == original.get("label"),
            "same_visible_outcome": row.get("after_visible_text") == original.get("after_visible_text"),
        })
    return {
        "schema_version": SCHEMA,
        "status": "analysis_complete",
        "input_identity": frozen_input_identity(),
        "observer_identity": observer_identity(),
        "observation_sha256": _sha(observation_path.read_bytes()),
        "runtime_manifest_sha256": _sha(runtime_manifest_path.read_bytes()),
        "primary_unit": "case-level paired authored workflow; obligations are not independent subjects",
        "arms": arms,
        "paired_descriptive": [
            _paired_bootstrap(case_scores, "A", "B"),
            _paired_bootstrap(case_scores, "A", "C"),
        ],
        "inference_policy": {
            "primary_contrast": "A-B",
            "case_is_the_independent_unit": True,
            "obligations_are_repeated_measures_not_independent_samples": True,
            "alpha": 0.05,
            "test": "exact paired two-sided sign-flip test on per-case passed-obligation differences",
            "status": INFERENCE_STATUS,
        },
        "supplementary_replay": {
            "excluded_from_primary": True,
            "row_count": len(replay_checks),
            "same_label_count": sum(row["same_label"] for row in replay_checks),
            "same_visible_outcome_count": sum(row["same_visible_outcome"] for row in replay_checks),
            "rows": replay_checks,
        },
        "claim_limits": [
            "repair-informed regression comparison, not a preregistered confirmatory benchmark",
            "small authored synthetic workflow population with four family clusters",
            "A uses four model calls while B and C use one; this is not an equal-compute causal ablation",
            "browser behavior does not establish visual quality, retrieval benefit, backend correctness, or user productivity",
        ],
    }


def _markdown(result: dict[str, Any]) -> str:
    lines = [f"# {STUDY_LABEL} measured summary", "", "Primary behavior coverage:", "", "| Arm | Pass | Fail | Unknown | All-six cases |", "| --- | ---: | ---: | ---: | ---: |"]
    for arm in ARMS:
        row = result["arms"][arm]["primary"]
        lines.append(f"| {arm} | {row['pass']}/{row['total']} | {row['fail']} | {row['unknown']} | {row['all_six_cases']}/{row['case_count']} |")
    lines.extend(["", "Paired case-level differences:", ""])
    for row in result["paired_descriptive"]:
        interval = row["descriptive_case_bootstrap_95_interval"]
        lines.append(f"- {row['contrast']}: mean {row['mean_difference']:.3f} passed obligations per case; descriptive 95% case bootstrap [{interval[0]:.3f}, {interval[1]:.3f}]; exact paired sign-flip p={row['exact_paired_sign_flip_two_sided_p']:.4f}.")
    lines.extend(["", "These results are descriptive for the frozen authored cases. They do not establish general superiority or an equal-compute architectural effect.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    try:
        result = analyze(observation_path=args.observation, runtime_manifest_path=args.runtime_manifest)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if args.markdown is not None:
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            with args.markdown.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(_markdown(result))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__, "message": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": result["status"], "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
