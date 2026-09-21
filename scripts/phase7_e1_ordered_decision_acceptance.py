"""Score the frozen E1 v18 ordered-decision family as a post-hoc secondary analysis."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import statistics
from typing import Any


SCHEMA = "req2web.phase7.e1.ordered_decision_acceptance.v1"
SOURCE_SCHEMA = "req2web.phase7.e1_v2.observer.v3"
SOURCE_OBSERVATION_SHA256 = (
    "sha256:476bcd226bc30d376c5cfb77146e6f53548312435efa925ba51d3a4a779481f2"
)
SOURCE_INPUT_IDENTITY = (
    "sha256:cfa73f7d3a95a8c2706b3d191c795d6f7d32d89551bcee182a67d5169ed6dd48"
)
SOURCE_OBSERVER_IDENTITY = (
    "sha256:df3de016c86653c3ad958d2acf153d65bf8d5951a06dc84de9e4eecb17d1e747"
)
SOURCE_RUNTIME_MANIFEST_SHA256 = (
    "sha256:059be05a037c0547c9abdce4bbcfa07b223ceaa001317a851a47a3358154728c"
)
ARMS = ("A", "B", "C")
ORDERED_CASE_IDS = (
    "e1v2-exhibition-approval",
    "e1v2-equipment-release",
    "e1v2-translation-signoff",
)
ORDERED_CRITERIA = (
    "correct_initial_state",
    "enter_review",
    "approve_after_review",
    "block_pre_review_approval",
    "reject_from_review",
    "reset_after_rejection_without_stale_success",
)


class AcceptanceError(ValueError):
    pass


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _exact_sign_flip(differences: list[int]) -> float:
    if not differences:
        raise AcceptanceError("at least one paired case difference is required")
    observed = abs(statistics.mean(differences))
    assignments = list(itertools.product((-1, 1), repeat=len(differences)))
    extreme = 0
    for signs in assignments:
        candidate = abs(
            statistics.mean(
                difference * sign
                for difference, sign in zip(differences, signs, strict=True)
            )
        )
        extreme += candidate >= observed - 1e-12
    return extreme / len(assignments)


def _score_ordered_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    expected_keys = {
        (case_id, arm, criterion_id)
        for case_id in ORDERED_CASE_IDS
        for arm in ARMS
        for criterion_id in ORDERED_CRITERIA
    }
    selected = [row for row in rows if row.get("family") == "ordered_decision"]
    keys = {
        (row.get("case_id"), row.get("arm"), row.get("criterion_id"))
        for row in selected
    }
    if len(selected) != len(expected_keys) or keys != expected_keys:
        raise AcceptanceError("ordered-decision row inventory is incomplete or duplicated")
    for row in selected:
        if row.get("label") not in {"pass", "fail", "unknown"}:
            raise AcceptanceError("ordered-decision row label is invalid")

    case_scores: dict[str, dict[str, int]] = {
        case_id: {arm: 0 for arm in ARMS} for case_id in ORDERED_CASE_IDS
    }
    arm_results: dict[str, Any] = {}
    for arm in ARMS:
        arm_rows = [row for row in selected if row["arm"] == arm]
        counts = {
            label: sum(row["label"] == label for row in arm_rows)
            for label in ("pass", "fail", "unknown")
        }
        for row in arm_rows:
            case_scores[row["case_id"]][arm] += int(row["label"] == "pass")
        arm_results[arm] = {
            **counts,
            "total": len(arm_rows),
            "coverage_lower_bound": counts["pass"] / len(arm_rows),
            "coverage_upper_bound": (
                counts["pass"] + counts["unknown"]
            ) / len(arm_rows),
            "per_case": [
                {
                    "case_id": case_id,
                    "passed": case_scores[case_id][arm],
                    "total": len(ORDERED_CRITERIA),
                }
                for case_id in ORDERED_CASE_IDS
            ],
        }

    def contrast(left: str, right: str) -> dict[str, Any]:
        differences = [
            case_scores[case_id][left] - case_scores[case_id][right]
            for case_id in ORDERED_CASE_IDS
        ]
        return {
            "contrast": f"{left}-{right}",
            "case_differences": [
                {"case_id": case_id, "difference": difference}
                for case_id, difference in zip(
                    ORDERED_CASE_IDS, differences, strict=True
                )
            ],
            "mean_passed_obligation_difference_per_case": statistics.mean(
                differences
            ),
            "case_win_tie_loss": {
                "left_wins": sum(value > 0 for value in differences),
                "ties": sum(value == 0 for value in differences),
                "right_wins": sum(value < 0 for value in differences),
            },
            "exact_paired_sign_flip_two_sided_p": _exact_sign_flip(differences),
            "case_is_the_independent_unit": True,
        }

    return {
        "arms": arm_results,
        "contrasts": [contrast("A", "B"), contrast("A", "C")],
    }


def analyze_payload(
    payload: dict[str, Any], *, source_observation_sha256: str
) -> dict[str, Any]:
    if source_observation_sha256 != SOURCE_OBSERVATION_SHA256:
        raise AcceptanceError("source observation bytes do not match the frozen v18 artifact")
    expected_top = {
        "schema_version": SOURCE_SCHEMA,
        "status": "observation_complete",
        "split": "measured",
        "input_identity": SOURCE_INPUT_IDENTITY,
        "observer_identity": SOURCE_OBSERVER_IDENTITY,
        "artifact_manifest_sha256": SOURCE_RUNTIME_MANIFEST_SHA256,
    }
    for key, expected in expected_top.items():
        if payload.get(key) != expected:
            raise AcceptanceError(f"source observation {key} binding is invalid")
    rows = payload.get("primary", {}).get("rows")
    if not isinstance(rows, list) or len(rows) != 216:
        raise AcceptanceError("source observation must retain all 216 primary rows")
    all_keys = {
        (row.get("case_id"), row.get("arm"), row.get("criterion_id"))
        for row in rows
    }
    if len(all_keys) != len(rows):
        raise AcceptanceError("source observation contains duplicate primary rows")

    overall = {}
    for arm in ARMS:
        arm_rows = [row for row in rows if row.get("arm") == arm]
        if len(arm_rows) != 72:
            raise AcceptanceError(f"arm {arm} must retain all 72 primary rows")
        overall[arm] = {
            "pass": sum(row.get("label") == "pass" for row in arm_rows),
            "fail": sum(row.get("label") == "fail" for row in arm_rows),
            "unknown": sum(row.get("label") == "unknown" for row in arm_rows),
            "total": len(arm_rows),
        }

    ordered = _score_ordered_rows(rows)
    a_b = next(row for row in ordered["contrasts"] if row["contrast"] == "A-B")
    exploratory_label = (
        "bounded_ordered_decision_advantage_observed_not_confirmed"
        if ordered["arms"]["A"]["pass"] > ordered["arms"]["B"]["pass"]
        and a_b["case_win_tie_loss"]["right_wins"] == 0
        else "ordered_decision_advantage_not_observed"
    )
    return {
        "schema_version": SCHEMA,
        "status": "analysis_complete",
        "analysis_role": "post_hoc_secondary_exploratory",
        "source": {
            "observation_sha256": source_observation_sha256,
            "input_identity": SOURCE_INPUT_IDENTITY,
            "observer_identity": SOURCE_OBSERVER_IDENTITY,
            "runtime_manifest_sha256": SOURCE_RUNTIME_MANIFEST_SHA256,
        },
        "historical_overall_reference_unchanged": overall,
        "ordered_decision": ordered,
        "exploratory_interpretation": exploratory_label,
        "prospective_confirmation_rule": {
            "population": "new unseen ordered-decision workflows with explicit review, prerequisite, alternative decision, and reset semantics",
            "minimum_case_count": 12,
            "primary_contrast": "A-B",
            "primary_metric": "six equally weighted browser obligations per case",
            "minimum_mean_passed_obligation_difference_per_case": 1.0,
            "maximum_exact_paired_sign_flip_two_sided_p": 0.05,
            "unknown_rows_allowed": 0,
            "missing_or_rejected_delivery_policy": "six behavioral failures, never a safety pass",
            "arm_c_policy": "secondary structured baseline; its failure does not invalidate a complete A-B comparison",
            "case_omission_allowed": False,
        },
        "claim_limits": [
            "the ordered-decision family was selected after inspecting the broader v18 result",
            "the historical three-case family is exploratory and underpowered",
            "all three cases and all six frozen obligations are retained without reweighting or relabeling",
            "the result supports at most an ordered-decision workflow advantage, not general web-generation superiority",
            "A used more model calls than B and C; this is a whole-system comparison, not an equal-compute causal ablation",
            "failure-recovery, cancel/restart, and broad visual-quality claims remain unsupported by this secondary analysis",
        ],
    }


def analyze_file(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise AcceptanceError("source observation must contain one object")
    return analyze_payload(payload, source_observation_sha256=_sha256(raw))


def _markdown(result: dict[str, Any]) -> str:
    ordered = result["ordered_decision"]
    lines = [
        "# E1 ordered-decision secondary acceptance analysis",
        "",
        "This is a post-hoc exploratory secondary analysis. The historical overall result is unchanged.",
        "",
        "| Arm | Ordered-decision pass | Fail | Unknown |",
        "|---|---:|---:|---:|",
    ]
    for arm in ARMS:
        row = ordered["arms"][arm]
        lines.append(
            f"| {arm} | {row['pass']}/{row['total']} | {row['fail']} | {row['unknown']} |"
        )
    lines.extend(["", "Case-level contrasts:", ""])
    for row in ordered["contrasts"]:
        wtl = row["case_win_tie_loss"]
        lines.append(
            f"- {row['contrast']}: mean difference {row['mean_passed_obligation_difference_per_case']:.3f}/6; "
            f"win/tie/loss {wtl['left_wins']}/{wtl['ties']}/{wtl['right_wins']}; "
            f"exact paired sign-flip p={row['exact_paired_sign_flip_two_sided_p']:.4f}."
        )
    lines.extend(
        [
            "",
            "Interpretation: the frozen v18 evidence shows a bounded ordered-decision advantage, but the three-case post-hoc subset is not confirmatory. A new unseen 12-case study is required for a stronger claim.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    try:
        result = analyze_file(args.observation)
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
