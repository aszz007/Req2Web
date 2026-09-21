"""Frozen generator-visible case packet for Phase 7 E1 v2.

``cases()`` is the only case interface intended for generators.  It contains
no evaluator criteria, action plans, selectors, or expected verdicts.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


SCHEMA = "req2web.phase7.e1_v2.cases.v1"


def _case(case_id: str, split: str, family: str, requirement: str, *constraints: str) -> dict[str, Any]:
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
            *constraints,
        ],
    }


_CASES = [
    _case(
        "e1v2-dev-document-approval", "development", "ordered_decision",
        "Build a document-approval workflow for an internal policy draft. The initial visible state must be exactly 'Pending decision'. A visible 'Start review' button changes it to 'In review'. 'Approve' may reach 'Approved' only from review and must be absent or disabled before review. From review, 'Reject' changes the state to 'Rejected'. A visible 'Reset' after rejection returns to 'Pending decision' and must not leave 'Approved' visible. The prototype simulates review locally.",
        "The reviewed object is an internal policy draft.",
    ),
    _case(
        "e1v2-exhibition-approval", "measured", "ordered_decision",
        "Build an exhibition-approval workflow for a gallery proposal whose accessibility checklist is reviewed before opening. The initial visible state must be exactly 'Pending decision'. A visible 'Start review' button changes it to 'In review'. In review, 'Complete checklist' must be used before 'Approve' is enabled. 'Approve' may reach 'Approved' only after that checkpoint and must be absent or disabled before review. From review, 'Reject' changes the state to 'Rejected' without requiring the checkpoint. A visible 'Reset' after rejection returns to 'Pending decision' and must not leave 'Approved' visible. All decisions are simulated locally.",
        "The decision concerns an exhibition proposal and its accessibility checklist.",
    ),
    _case(
        "e1v2-equipment-release", "measured", "ordered_decision",
        "Build an equipment-release workflow for a calibrated field recorder awaiting a safety review. The initial visible state must be exactly 'Pending decision'. A visible 'Start review' button changes it to 'In review'. In review, 'Record calibration' must be used before 'Approve' is enabled. 'Approve' may reach 'Approved' only after that checkpoint and must be absent or disabled before review. From review, 'Reject' changes the state to 'Rejected' without requiring the checkpoint. A visible 'Reset' after rejection returns to 'Pending decision' and must not leave 'Approved' visible. No physical equipment or inventory system is controlled.",
        "The decision concerns release of a calibrated recorder after safety review.",
    ),
    _case(
        "e1v2-translation-signoff", "measured", "ordered_decision",
        "Build a translation sign-off workflow for a museum guide whose terminology must be checked against an approved glossary. The initial visible state must be exactly 'Pending decision'. A visible 'Start review' button changes it to 'In review'. In review, 'Verify glossary' must be used before 'Approve' is enabled. 'Approve' may reach 'Approved' only after that checkpoint and must be absent or disabled before review. From review, 'Reject' changes the state to 'Rejected' without requiring the checkpoint. A visible 'Reset' after rejection returns to 'Pending decision' and must not leave 'Approved' visible. Sign-off is an in-page simulation.",
        "The decision concerns terminology consistency in a translated museum guide.",
    ),
    _case(
        "e1v2-dev-sample-download", "development", "failure_recovery",
        "Build a simulated sample-download workflow. The initial visible state must be exactly 'Ready'. 'Retry' must be absent or disabled before a failure. The first visible 'Attempt' action must reach 'Attempt failed' and must not show 'Completed'. A visible 'Retry' then reaches 'Completed'. In the terminal state, 'Repeat' must be absent or disabled, or using it must leave one completion unchanged rather than restarting or duplicating it. No file or network transfer occurs.",
        "The operation represents downloading a harmless example file.",
    ),
    _case(
        "e1v2-mock-archive-transfer", "measured", "failure_recovery",
        "Build a simulated archive-transfer workflow for moving a mock exhibit package into local staging. The initial visible state must be exactly 'Ready'. 'Prepare transfer' keeps that state visible and enables 'Attempt'. 'Retry' must be absent or disabled before a failure. The first visible 'Attempt' action after preparation must reach 'Attempt failed' and must not show 'Completed'. A visible 'Retry' then reaches 'Completed'. In the terminal state, 'Repeat' must be absent or disabled, or using it must leave one completion unchanged rather than restarting or duplicating it. No archive is actually copied.",
        "The operation represents staging a mock exhibit archive, not a real transfer.",
    ),
    _case(
        "e1v2-report-export", "measured", "failure_recovery",
        "Build a simulated report-export workflow for an accessibility summary. The initial visible state must be exactly 'Ready'. 'Select export' keeps that state visible and enables 'Attempt'. 'Retry' must be absent or disabled before a failure. The first visible 'Attempt' action after selection must reach 'Attempt failed' and must not show 'Completed'. A visible 'Retry' then reaches 'Completed'. In the terminal state, 'Repeat' must be absent or disabled, or using it must leave one completion unchanged rather than restarting or duplicating it. No report is written to disk.",
        "The operation represents exporting an accessibility summary.",
    ),
    _case(
        "e1v2-configuration-deployment", "measured", "failure_recovery",
        "Build a simulated configuration-deployment workflow for a kiosk display profile. The initial visible state must be exactly 'Ready'. 'Run precheck' keeps that state visible and enables 'Attempt'. 'Retry' must be absent or disabled before a failure. The first visible 'Attempt' action after the precheck must reach 'Attempt failed' and must not show 'Completed'. A visible 'Retry' then reaches 'Completed'. In the terminal state, 'Repeat' must be absent or disabled, or using it must leave one completion unchanged rather than restarting or duplicating it. No device configuration is changed.",
        "The operation represents deploying a kiosk profile without controlling a real device.",
    ),
    _case(
        "e1v2-dev-setup-walkthrough", "development", "cancel_restart",
        "Build a three-step setup walkthrough. The initial visible state must be exactly 'Not started'. 'Start' opens 'Step 1'; 'Next' advances to 'Step 2' and then 'Step 3'; 'Back' from the second step returns to 'Step 1'. 'Cancel' from an active step resets to 'Not started'. 'Confirm' must be absent or disabled until all three steps have been reached and may then show 'Confirmed'. From that terminal state, 'Restart' opens 'Step 1' without leaving 'Confirmed' visible.",
        "The walkthrough describes local preferences and does not change system settings.",
    ),
    _case(
        "e1v2-onboarding-walkthrough", "measured", "cancel_restart",
        "Build a three-step volunteer-onboarding walkthrough covering welcome, safety notes, and contact confirmation. The initial visible state must be exactly 'Not started'. 'Start' opens 'Step 1'; 'Next' advances to 'Step 2' and then 'Step 3'; 'Back' from the second step returns to 'Step 1'. 'Cancel' from an active step resets to 'Not started'. 'Confirm' must be absent or disabled until all three steps have been reached and may then show 'Confirmed'. From that terminal state, 'Restart' opens 'Step 1' without leaving 'Confirmed' visible.",
        "The three steps represent welcome, safety notes, and contact confirmation.",
    ),
    _case(
        "e1v2-publishing-wizard", "measured", "cancel_restart",
        "Build a four-step publishing wizard covering metadata, audience, preview, and release confirmation for a draft bulletin. The initial visible state must be exactly 'Not started'. 'Start' opens 'Step 1'; successive 'Next' actions advance through 'Step 2', 'Step 3', and 'Step 4'; 'Back' from the second step returns to 'Step 1'. 'Cancel' from an active step resets to 'Not started'. 'Confirm' must be absent or disabled until all four steps have been reached and may then show 'Confirmed'. From that terminal state, 'Restart' opens 'Step 1' without leaving 'Confirmed' visible. Nothing is published.",
        "The four steps represent metadata, audience, preview, and release confirmation.",
    ),
    _case(
        "e1v2-inspection-checklist", "measured", "cancel_restart",
        "Build a five-step inspection checklist covering exterior, power, controls, accessibility, and final acknowledgment for a demo kiosk. The initial visible state must be exactly 'Not started'. 'Start' opens 'Step 1'; successive 'Next' actions advance through 'Step 2', 'Step 3', 'Step 4', and 'Step 5'; 'Back' from the second step returns to 'Step 1'. 'Cancel' from an active step resets to 'Not started'. 'Confirm' must be absent or disabled until all five steps have been reached and may then show 'Confirmed'. From that terminal state, 'Restart' opens 'Step 1' without leaving 'Confirmed' visible.",
        "The five steps represent exterior, power, controls, accessibility, and final acknowledgment.",
    ),
    _case(
        "e1v2-dev-short-long-walkthrough", "development", "conditional_branch",
        "Build a walkthrough with an explicit short-path or long-path choice. The initial visible state must be exactly 'Choose a path'. 'Choose first path' opens 'First path active', where only 'Continue first path' may reach 'Completed'. 'Choose second path' opens 'Second path active', where only 'Continue second path' may reach 'Completed'. No continue action may reach the terminal state before a path is chosen, and the alternative choice/action must be absent or disabled while a path is active. 'Reset' returns to 'Choose a path' and permits choosing the other path.",
        "The first path is a short walkthrough and the second is an extended walkthrough.",
    ),
    _case(
        "e1v2-basic-extended-review", "measured", "conditional_branch",
        "Build a proposal review with an explicit basic-review or extended-review choice. The initial visible state must be exactly 'Choose a path'. 'Choose first path' opens 'First path active'; 'Complete evidence check' is then required before 'Continue first path' may reach 'Completed'. 'Choose second path' opens 'Second path active', where 'Continue second path' may reach 'Completed'. No continue action may reach the terminal state before a path is chosen, and the alternative choice/action must be absent or disabled while a path is active. 'Reset' returns to 'Choose a path' and permits choosing the other path.",
        "The first path is basic review; the second adds an extended evidence check.",
    ),
    _case(
        "e1v2-local-remote-handoff", "measured", "conditional_branch",
        "Build a handoff simulation with an explicit local-handoff or remote-handoff choice. The initial visible state must be exactly 'Choose a path'. 'Choose first path' opens 'First path active', where 'Continue first path' may reach 'Completed'. 'Choose second path' opens 'Second path active'; 'Confirm connection mode' is then required before 'Continue second path' may reach 'Completed'. No continue action may reach the terminal state before a path is chosen, and the alternative choice/action must be absent or disabled while a path is active. 'Reset' returns to 'Choose a path' and permits choosing the other path. No connection is made.",
        "The first path simulates a local handoff; the second simulates a remote handoff.",
    ),
    _case(
        "e1v2-standard-exception-approval", "measured", "conditional_branch",
        "Build an approval simulation with an explicit standard-route or exception-route choice. The initial visible state must be exactly 'Choose a path'. 'Choose first path' opens 'First path active', where 'Continue first path' may reach 'Completed'. 'Choose second path' opens 'Second path active'; 'Record exception reason' is then required before 'Continue second path' may reach 'Completed'. No continue action may reach the terminal state before a path is chosen, and the alternative choice/action must be absent or disabled while a path is active. 'Reset' returns to 'Choose a path' and permits choosing the other path.",
        "The first path is standard approval; the second records an exception route.",
    ),
]


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def cases() -> list[dict[str, Any]]:
    """Return a defensive copy of the frozen generator-visible case packet."""
    return copy.deepcopy(_CASES)


def frozen_input_identity() -> str:
    """Return the hard-frozen identity of the exact generator-visible bytes."""
    actual = "sha256:" + hashlib.sha256(_canonical({"schema_version": SCHEMA, "cases": _CASES})).hexdigest()
    if actual != FROZEN_INPUT_SHA256:
        raise RuntimeError("E1 v2 public input packet drifted")
    return actual


# Updated only by an explicit successor-study input freeze.
FROZEN_INPUT_SHA256 = "sha256:cfa73f7d3a95a8c2706b3d191c795d6f7d32d89551bcee182a67d5169ed6dd48"


__all__ = ["SCHEMA", "cases", "frozen_input_identity"]
