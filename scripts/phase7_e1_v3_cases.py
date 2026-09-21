"""Frozen public cases for the Phase 7 E1 v3 repair-informed holdout.

The generator-facing packet contains only public requirements and constraints.
Evaluator criteria, action scripts, and expected verdicts remain in the local
observer and are excluded from the server payload.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


SCHEMA = "req2web.phase7.e1_v3.cases.v1"


def _case(
    case_id: str,
    split: str,
    family: str,
    requirement: str,
    context: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "split": split,
        "family": family,
        "requirement": requirement,
        "target_device": "desktop",
        "task_type": "workflow_prototype",
        "constraints": [
            "Produce a self-contained offline prototype with no backend or network access.",
            "Expose every required action and state through visible page controls and text.",
            context,
        ],
    }


def _ordered(case_id: str, split: str, subject: str, checkpoint: str, context: str) -> dict[str, Any]:
    return _case(
        case_id,
        split,
        "ordered_decision",
        f"Build a {subject} approval workflow. The initial visible state must be exactly 'Pending decision'. A visible 'Start review' button changes it to 'In review'. In review, '{checkpoint}' must be used before 'Approve' is enabled. 'Approve' may reach 'Approved' only after that checkpoint and must be absent or disabled before review. From review, 'Reject' changes the state to 'Rejected' without requiring the checkpoint. A visible 'Reset' after rejection returns to 'Pending decision' and must not leave 'Approved' visible. All decisions are simulated locally.",
        context,
    )


def _recovery(case_id: str, split: str, subject: str, preparation: str, context: str) -> dict[str, Any]:
    return _case(
        case_id,
        split,
        "failure_recovery",
        f"Build a simulated {subject} workflow. The initial visible state must be exactly 'Ready'. '{preparation}' keeps that state visible and enables 'Attempt'. 'Retry' must be absent or disabled before a failure. The first visible 'Attempt' action after preparation must reach 'Attempt failed' and must not show 'Completed'. A visible 'Retry' then reaches 'Completed'. In the terminal state, 'Repeat' must be absent or disabled, or using it must leave one completion unchanged rather than restarting or duplicating it. No external operation occurs.",
        context,
    )


def _wizard(case_id: str, split: str, subject: str, steps: list[str], context: str) -> dict[str, Any]:
    final_step = len(steps)
    progression = ", ".join(steps)
    return _case(
        case_id,
        split,
        "cancel_restart",
        f"Build a {final_step}-step {subject} walkthrough covering {progression}. The initial visible state must be exactly 'Not started'. 'Start' opens 'Step 1'; successive 'Next' actions advance through every numbered step up to 'Step {final_step}'; 'Back' from the second step returns to 'Step 1'. 'Cancel' from an active step resets to 'Not started'. 'Confirm' must be absent or disabled until all {final_step} steps have been reached and may then show 'Confirmed'. From that terminal state, 'Restart' opens 'Step 1' without leaving 'Confirmed' visible.",
        context,
    )


def _branch(
    case_id: str,
    split: str,
    subject: str,
    checkpoint_branch: str | None,
    checkpoint: str | None,
    context: str,
) -> dict[str, Any]:
    first = "'Choose first path' opens 'First path active'"
    second = "'Choose second path' opens 'Second path active'"
    if checkpoint_branch == "first":
        first += f"; '{checkpoint}' is then required before 'Continue first path' may reach 'Completed'"
        second += ", where 'Continue second path' may reach 'Completed'"
    elif checkpoint_branch == "second":
        first += ", where 'Continue first path' may reach 'Completed'"
        second += f"; '{checkpoint}' is then required before 'Continue second path' may reach 'Completed'"
    else:
        first += ", where 'Continue first path' may reach 'Completed'"
        second += ", where 'Continue second path' may reach 'Completed'"
    return _case(
        case_id,
        split,
        "conditional_branch",
        f"Build a {subject} workflow with an explicit two-path choice. The initial visible state must be exactly 'Choose a path'. {first}. {second}. No continue action may reach the terminal state before a path is chosen, and the alternative choice/action must be absent or disabled while a path is active. 'Reset' returns to 'Choose a path' and permits choosing the other path.",
        context,
    )


_CASES = [
    _ordered("e1v3-dev-permit-approval", "development", "temporary exhibit permit", "Verify attachments", "The permit and attachments are synthetic."),
    _recovery("e1v3-dev-dataset-export", "development", "sample dataset export", "Choose subset", "The dataset and export are simulated."),
    _wizard("e1v3-dev-tour-walkthrough", "development", "guided-tour setup", ["welcome", "route", "confirmation"], "The tour setup changes no external schedule."),
    _branch("e1v3-dev-route-review", "development", "route review", "second", "Record rationale", "Both routes are local simulations."),
    _ordered("e1v3-public-program-approval", "measured", "public program", "Complete accessibility audit", "The decision concerns a proposed public program."),
    _ordered("e1v3-equipment-loan-release", "measured", "equipment loan", "Verify return condition", "The equipment loan is simulated and controls no inventory."),
    _ordered("e1v3-subtitle-signoff", "measured", "subtitle sign-off", "Check terminology", "The sign-off concerns a synthetic subtitle draft."),
    _recovery("e1v3-catalog-export", "measured", "catalog export", "Select records", "No catalog file is written."),
    _recovery("e1v3-media-package-transfer", "measured", "media package transfer", "Prepare package", "No media package is copied."),
    _recovery("e1v3-kiosk-profile-apply", "measured", "kiosk profile application", "Run validation", "No device profile is changed."),
    _wizard("e1v3-visitor-intake", "measured", "visitor intake", ["welcome", "access needs", "contact confirmation"], "The intake stores no personal data."),
    _wizard("e1v3-exhibit-publishing", "measured", "exhibit publishing", ["metadata", "audience", "preview", "release confirmation"], "Nothing is published."),
    _wizard("e1v3-safety-inspection", "measured", "safety inspection", ["exterior", "power", "controls", "accessibility", "acknowledgment"], "The inspection controls no physical equipment."),
    _branch("e1v3-standard-expanded-review", "measured", "standard or expanded review", "first", "Complete evidence check", "Both review paths are simulations."),
    _branch("e1v3-local-remote-preview", "measured", "local or remote preview", "second", "Confirm connection mode", "No remote connection is made."),
    _branch("e1v3-regular-exception-publish", "measured", "regular or exception publication", "second", "Record exception reason", "Nothing is published and the exception is synthetic."),
]


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def cases() -> list[dict[str, Any]]:
    return copy.deepcopy(_CASES)


def frozen_input_identity() -> str:
    actual = "sha256:" + hashlib.sha256(
        _canonical({"schema_version": SCHEMA, "cases": _CASES})
    ).hexdigest()
    if actual != FROZEN_INPUT_SHA256:
        raise RuntimeError("E1 v3 public input packet drifted")
    return actual


FROZEN_INPUT_SHA256 = "sha256:18da8f091d98cdde8ae74d4b0fcb376c033da0039e80721ecdbcd7d27f832f56"


__all__ = ["SCHEMA", "cases", "frozen_input_identity"]
