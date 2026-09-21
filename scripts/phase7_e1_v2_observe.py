"""Independent Playwright observer for the frozen Phase 7 E1 v2 workflows.

The observer consumes only public requirements plus an immutable artifact
manifest.  It never reads PageSpec, F4, model output, or generator metadata.
Every obligation starts in a fresh browser context and has its own evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from phase7_e1_v2_cases import (  # noqa: E402
    cases,
    frozen_input_identity,
)


SCHEMA = "req2web.phase7.e1_v2.observer.v4"
ARMS = ("A", "B", "C")
REPLAY_CASE_IDS = (
    "e1v2-exhibition-approval",
    "e1v2-report-export",
    "e1v2-publishing-wizard",
)
MAX_ACTIONS = 16
MAX_SECONDS = 90

_CRITERIA = {
    "ordered_decision": (
        "correct_initial_state", "enter_review", "approve_after_review",
        "block_pre_review_approval", "reject_from_review", "reset_after_rejection_without_stale_success",
    ),
    "failure_recovery": (
        "ready_state", "first_attempt_fails", "no_success_on_failure",
        "retry_reaches_success", "retry_unavailable_before_failure", "terminal_repeat_does_not_restart_or_duplicate",
    ),
    "cancel_restart": (
        "start_step", "advance_to_second_step", "back_returns_to_first",
        "cancel_resets", "restart_has_no_stale_completion", "confirmation_only_after_all_mandatory_steps",
    ),
    "conditional_branch": (
        "initial_branch_choice", "first_branch_path", "second_branch_path",
        "no_shortcut_to_terminal", "alternative_actions_unavailable_on_current_branch", "reset_permits_other_branch",
    ),
}

# These execution details are evaluator-owned.  They are deliberately absent
# from the generator-visible case module and prompts.
_ORDER_CHECKPOINT = {
    "e1v2-exhibition-approval": "Complete checklist",
    "e1v2-equipment-release": "Record calibration",
    "e1v2-translation-signoff": "Verify glossary",
}
_RECOVERY_PREPARATION = {
    "e1v2-mock-archive-transfer": "Prepare transfer",
    "e1v2-report-export": "Select export",
    "e1v2-configuration-deployment": "Run precheck",
}
_WIZARD_STEP_COUNT = {
    "e1v2-dev-setup-walkthrough": 3,
    "e1v2-onboarding-walkthrough": 3,
    "e1v2-publishing-wizard": 4,
    "e1v2-inspection-checklist": 5,
}
_BRANCH_CHECKPOINT = {
    "e1v2-basic-extended-review": ("first", "Complete evidence check"),
    "e1v2-local-remote-handoff": ("second", "Confirm connection mode"),
    "e1v2-standard-exception-approval": ("second", "Record exception reason"),
}


def evaluator_checks() -> dict[str, tuple[str, ...]]:
    """Return private evaluator criteria; generators must not import this file."""
    return {row["case_id"]: _CRITERIA[row["family"]] for row in cases()}


def obligations_for_case(case_id: str) -> tuple[str, ...]:
    try:
        family = next(row["family"] for row in cases() if row["case_id"] == case_id)
    except StopIteration as exc:
        raise KeyError(case_id) from exc
    return _CRITERIA[family]


class ObservationError(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def observer_identity() -> str:
    public_contract = {
        "schema": SCHEMA,
        "input_identity": frozen_input_identity(),
        "checks": evaluator_checks(),
        "case_execution_parameters": {
            "ordered_checkpoints": _ORDER_CHECKPOINT,
            "recovery_preparations": _RECOVERY_PREPARATION,
            "wizard_step_counts": _WIZARD_STEP_COUNT,
            "branch_checkpoints": _BRANCH_CHECKPOINT,
        },
        "arms": ARMS,
        "replay_case_ids": REPLAY_CASE_IDS,
        "limits": {"max_actions": MAX_ACTIONS, "max_seconds": MAX_SECONDS},
        "selector_policy": "visible exact role/name and exact visible state text only",
        "implementation_sha256": _sha256(Path(__file__).read_bytes()),
    }
    return _sha256(_canonical(public_contract))


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ObservationError(f"{path.name} must contain one JSON object")
    return value


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical(value)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()


def _safe_file(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ObservationError("artifact paths must be non-empty root-relative POSIX paths")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ObservationError("artifact path is not a safe root-relative path")
    candidate = root.joinpath(*pure.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ObservationError(f"artifact file is missing: {relative}") from exc
    if root != resolved and root not in resolved.parents:
        raise ObservationError("artifact path escapes the result root")
    if candidate.is_symlink() or not resolved.is_file():
        raise ObservationError("artifact path must name a regular non-symlink file")
    return resolved


@dataclass(frozen=True)
class Delivery:
    case_id: str
    arm: str
    available: bool
    entrypoint: Path | None
    entrypoint_relative: str | None
    files: tuple[dict[str, str], ...]
    reason: str | None
    runtime_status: str | None
    exit_code: int | None
    started_calls: int | None
    terminal_product_failure: bool


def load_artifact_manifest(result_root: Path, manifest_path: Path) -> dict[tuple[str, str], Delivery]:
    """Validate every declared asset byte and return delivery availability.

    A row whose assets are missing or have drifted is retained as an explicit
    unavailable delivery so it can score zero. Structural ambiguity (unknown
    case/arm, duplicate key, or unsafe path) fails the manifest closed.
    """
    root = result_root.resolve(strict=True)
    manifest = _read_json(manifest_path)
    rows = manifest.get("rows")
    if not isinstance(rows, list):
        raise ObservationError("artifact manifest rows must be a list")
    known_cases = {row["case_id"] for row in cases()}
    result: dict[tuple[str, str], Delivery] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ObservationError("artifact manifest row must be an object")
        case_id, arm = row.get("case_id"), row.get("arm")
        if case_id not in known_cases or arm not in ARMS:
            raise ObservationError("artifact row has unknown case_id or arm")
        key = (case_id, arm)
        if key in result:
            raise ObservationError("artifact manifest contains a duplicate case/arm row")
        entrypoint = row.get("entrypoint")
        files = row.get("files")
        runtime_status = row.get("status") if isinstance(row.get("status"), str) else None
        exit_code = row.get("exit_code") if type(row.get("exit_code")) is int else None
        started_calls = row.get("started_calls") if type(row.get("started_calls")) is int else None
        terminal_product_failure = (
            (runtime_status, exit_code) in {
                ("failed_or_interrupted", 2),
                ("failed_closed_consistency", 0),
            }
            and started_calls is not None
            and started_calls > 0
        )
        if not isinstance(entrypoint, str) or not isinstance(files, list) or not files:
            result[key] = Delivery(
                case_id, arm, False, None, None, (), "missing entrypoint or asset inventory",
                runtime_status, exit_code, started_calls, terminal_product_failure,
            )
            continue
        seen: set[str] = set()
        normalized: list[dict[str, str]] = []
        failure: str | None = None
        entry_path: Path | None = None
        for file_row in files:
            if not isinstance(file_row, dict) or set(file_row) != {"path", "sha256"}:
                failure = "invalid asset inventory row"
                break
            relative, expected = file_row.get("path"), file_row.get("sha256")
            if not isinstance(relative, str) or relative in seen:
                failure = "duplicate or invalid asset path"
                break
            seen.add(relative)
            if not isinstance(expected, str) or len(expected) != 71 or not expected.startswith("sha256:"):
                failure = "invalid asset sha256"
                break
            try:
                resolved = _safe_file(root, relative)
            except ObservationError as exc:
                # Unsafe traversal is a manifest-level error; an ordinary
                # missing file is an unavailable delivery.
                if "safe root-relative" in str(exc) or "escapes" in str(exc) or "symlink" in str(exc):
                    raise
                failure = str(exc)
                break
            if _sha256(resolved.read_bytes()) != expected:
                failure = f"asset hash mismatch: {relative}"
                break
            normalized.append({"path": relative, "sha256": expected})
            if relative == entrypoint:
                entry_path = resolved
        if entrypoint not in seen:
            failure = "entrypoint is not included in the immutable asset inventory"
        if entry_path is not None and entry_path.suffix.lower() not in {".html", ".htm"}:
            failure = "entrypoint is not an HTML file"
        result[key] = Delivery(
            case_id, arm, failure is None and entry_path is not None,
            entry_path if failure is None else None,
            entrypoint if failure is None else None,
            tuple(normalized), failure, runtime_status, exit_code, started_calls,
            terminal_product_failure,
        )
    return result


def _block_external(route: Any) -> None:
    parsed = urlparse(route.request.url)
    if parsed.scheme in {"file", "data", "blob", "about"} and parsed.netloc in {"", "localhost"}:
        route.continue_()
    else:
        route.abort()


class Actions:
    def __init__(self, page: Any) -> None:
        self.page = page
        self.rows: list[dict[str, str]] = []

    def click(self, label: str) -> None:
        if len(self.rows) >= MAX_ACTIONS:
            raise ObservationError("visible action budget exceeded")
        self.page.get_by_role("button", name=label, exact=True).click()
        self.rows.append({"action": "click", "visible_name": label})


def _button(page: Any, label: str) -> Any:
    return page.get_by_role("button", name=label, exact=True)


def _available(page: Any, label: str) -> bool:
    control = _button(page, label)
    return control.count() == 1 and control.is_visible() and control.is_enabled()


def _unavailable(page: Any, label: str) -> bool:
    control = _button(page, label)
    return control.count() == 0 or not control.is_visible() or not control.is_enabled()


def _state_count(page: Any, text: str) -> int:
    locator = page.get_by_text(text, exact=True)
    return sum(1 for index in range(locator.count()) if locator.nth(index).is_visible())


def _state(page: Any, text: str) -> bool:
    return _state_count(page, text) >= 1


def _result(ok: bool, passed: str, failed: str) -> tuple[str, str]:
    return ("pass", passed) if ok else ("fail", failed)


def _ordered(case: dict[str, Any], page: Any, criterion: str, actions: Actions) -> tuple[str, str]:
    if criterion == "correct_initial_state":
        return _result(_state(page, "Pending decision") and not _state(page, "Approved"), "Initial pending state is visible.", "Initial pending state is not uniquely demonstrated.")
    if criterion == "block_pre_review_approval":
        blocked = _unavailable(page, "Approve")
        if not blocked:
            actions.click("Approve")
            blocked = _state(page, "Pending decision") and not _state(page, "Approved")
        if blocked and _available(page, "Start review"):
            actions.click("Start review")
            blocked = blocked and _state(page, "In review")
        return _result(blocked, "Pre-review approval was blocked and the allowed review path worked.", "Approval was not blocked before review or the allowed path did not work.")
    if not _available(page, "Start review"):
        return "fail", "Visible Start review action is missing or disabled."
    actions.click("Start review")
    if criterion == "enter_review":
        return _result(_state(page, "In review"), "Start review entered review.", "Start review did not enter review.")
    if criterion == "approve_after_review":
        checkpoint = _ORDER_CHECKPOINT.get(case["case_id"])
        if checkpoint is not None:
            if not _available(page, checkpoint):
                return "fail", f"Required checkpoint {checkpoint} is missing in review."
            if not _unavailable(page, "Approve"):
                return "fail", "Approve became available before the required checkpoint."
            actions.click(checkpoint)
        if not _available(page, "Approve"):
            return "fail", "Approve is unavailable in review."
        actions.click("Approve")
        return _result(_state(page, "Approved"), "Approval succeeded after review.", "Approval did not reach the approved state.")
    if not _available(page, "Reject"):
        return "fail", "Reject is unavailable in review."
    actions.click("Reject")
    if criterion == "reject_from_review":
        return _result(_state(page, "Rejected"), "Rejection succeeded from review.", "Rejection did not reach the rejected state.")
    if not _state(page, "Rejected") or not _available(page, "Reset"):
        return "fail", "The rejection prerequisite or Reset action is missing."
    actions.click("Reset")
    return _result(_state(page, "Pending decision") and not _state(page, "Approved"), "Reset cleared rejection without stale success.", "Reset did not restore pending or left stale success.")


def _recovery(case: dict[str, Any], page: Any, criterion: str, actions: Actions) -> tuple[str, str]:
    if criterion == "ready_state":
        return _result(_state(page, "Ready"), "Ready state is visible.", "Ready state is missing.")
    if criterion == "retry_unavailable_before_failure":
        blocked = _unavailable(page, "Retry")
        preparation = _RECOVERY_PREPARATION.get(case["case_id"])
        if blocked and preparation is not None:
            if not _available(page, preparation):
                return "fail", f"Required preparation action {preparation} is missing."
            actions.click(preparation)
        if blocked and _available(page, "Attempt"):
            actions.click("Attempt")
            blocked = blocked and _state(page, "Attempt failed")
        return _result(blocked, "Retry was unavailable before failure and the failure path worked.", "Retry was prematurely available or failure path was inoperable.")
    preparation = _RECOVERY_PREPARATION.get(case["case_id"])
    if preparation is not None:
        if not _available(page, preparation):
            return "fail", f"Required preparation action {preparation} is missing."
        actions.click(preparation)
    if not _available(page, "Attempt"):
        return "fail", "Visible Attempt action is missing or disabled."
    actions.click("Attempt")
    if criterion == "first_attempt_fails":
        return _result(_state(page, "Attempt failed"), "First attempt visibly failed.", "First attempt did not visibly fail.")
    if criterion == "no_success_on_failure":
        return _result(_state(page, "Attempt failed") and not _state(page, "Completed"), "Failure occurred without a success state.", "Failure prerequisite was absent or success appeared during failure.")
    if not _state(page, "Attempt failed") or not _available(page, "Retry"):
        return "fail", "The failure prerequisite or Retry action is missing."
    actions.click("Retry")
    if criterion == "retry_reaches_success":
        return _result(_state(page, "Completed"), "Retry reached completion.", "Retry did not reach completion.")
    before = _state_count(page, "Completed")
    stable = _unavailable(page, "Repeat")
    if not stable:
        actions.click("Repeat")
        stable = _state(page, "Completed") and _state_count(page, "Completed") == before
    return _result(stable, "Terminal repeat was unavailable or left one unchanged completion.", "Terminal repeat restarted or duplicated completion.")


def _advance_to_final_step(
    case: dict[str, Any], page: Any, actions: Actions, *, current_step: int = 1,
) -> bool:
    for step in range(current_step + 1, _WIZARD_STEP_COUNT[case["case_id"]] + 1):
        if not _available(page, "Next"):
            return False
        actions.click("Next")
        if not _state(page, f"Step {step}"):
            return False
    return True


def _wizard(case: dict[str, Any], page: Any, criterion: str, actions: Actions) -> tuple[str, str]:
    if criterion == "start_step":
        if not _state(page, "Not started") or not _available(page, "Start"):
            return "fail", "Initial state or Start action is missing."
        actions.click("Start")
        return _result(_state(page, "Step 1"), "Start opened the first step.", "Start did not open the first step.")
    if criterion == "confirmation_only_after_all_mandatory_steps":
        blocked = _unavailable(page, "Confirm")
        if not blocked:
            actions.click("Confirm")
            blocked = not _state(page, "Confirmed")
        if not _available(page, "Start"):
            return "fail", "Start is missing."
        actions.click("Start")
        if not _advance_to_final_step(case, page, actions) or not _available(page, "Confirm"):
            return "fail", "All mandatory steps did not expose confirmation."
        actions.click("Confirm")
        return _result(blocked and _state(page, "Confirmed"), "Confirmation was blocked early and allowed only after all steps.", "Confirmation gating did not match the required sequence.")
    if not _available(page, "Start"):
        return "fail", "Visible Start action is missing or disabled."
    actions.click("Start")
    if not _available(page, "Next"):
        return "fail", "Next is unavailable at the first step."
    actions.click("Next")
    if criterion == "advance_to_second_step":
        return _result(_state(page, "Step 2"), "Next advanced to the second step.", "Next did not reach the second step.")
    if criterion == "back_returns_to_first":
        if not _available(page, "Back"):
            return "fail", "Back is unavailable at the second step."
        actions.click("Back")
        return _result(_state(page, "Step 1"), "Back returned to the first step.", "Back did not return to the first step.")
    if criterion == "cancel_resets":
        if not _available(page, "Cancel"):
            return "fail", "Cancel is unavailable during the walkthrough."
        actions.click("Cancel")
        return _result(_state(page, "Not started") and not _state(page, "Confirmed"), "Cancel reset the walkthrough.", "Cancel did not reset the walkthrough.")
    if not _advance_to_final_step(case, page, actions, current_step=2) or not _available(page, "Confirm"):
        return "fail", "The final mandatory step or Confirm action is missing."
    actions.click("Confirm")
    if not _state(page, "Confirmed") or not _available(page, "Restart"):
        return "fail", "Confirmation prerequisite or Restart action is missing."
    actions.click("Restart")
    return _result(_state(page, "Step 1") and not _state(page, "Confirmed"), "Restart opened a clean first step.", "Restart retained stale completion or missed the first step.")


def _branch(case: dict[str, Any], page: Any, criterion: str, actions: Actions) -> tuple[str, str]:
    if criterion == "initial_branch_choice":
        ok = _state(page, "Choose a path") and _available(page, "Choose first path") and _available(page, "Choose second path")
        return _result(ok, "Initial branch choice exposes both paths.", "Initial branch choice or one path action is missing.")
    if criterion == "no_shortcut_to_terminal":
        blocked = _unavailable(page, "Continue first path") and _unavailable(page, "Continue second path")
        if blocked and _available(page, "Choose first path"):
            actions.click("Choose first path")
            blocked = blocked and _state(page, "First path active")
        return _result(blocked, "No terminal shortcut existed before choosing a path.", "A terminal shortcut existed or the legal path did not work.")
    choice = "Choose second path" if criterion == "second_branch_path" else "Choose first path"
    active = "Second path active" if criterion == "second_branch_path" else "First path active"
    advance = "Continue second path" if criterion == "second_branch_path" else "Continue first path"
    if not _available(page, choice):
        return "fail", f"Visible {choice} action is missing or disabled."
    actions.click(choice)
    if criterion == "alternative_actions_unavailable_on_current_branch":
        blocked = _unavailable(page, "Choose second path") and _unavailable(page, "Continue second path")
        checkpoint = _BRANCH_CHECKPOINT.get(case["case_id"])
        if blocked and checkpoint is not None and checkpoint[0] == "first":
            if not _available(page, checkpoint[1]):
                return "fail", "The required first-branch checkpoint is missing."
            blocked = blocked and _unavailable(page, "Continue first path")
            actions.click(checkpoint[1])
        if blocked and _available(page, "Continue first path"):
            actions.click("Continue first path")
            blocked = blocked and _state(page, "Completed")
        return _result(blocked, "Alternative actions were unavailable while the chosen branch remained operable.", "Alternative actions leaked across branches or the chosen path failed.")
    if criterion == "reset_permits_other_branch":
        if not _state(page, active) or not _available(page, "Reset"):
            return "fail", "Active first path or Reset is missing."
        actions.click("Reset")
        if not _state(page, "Choose a path") or not _available(page, "Choose second path"):
            return "fail", "Reset did not restore the branch choice."
        actions.click("Choose second path")
        return _result(_state(page, "Second path active"), "Reset permitted the other branch.", "The other branch remained unavailable after reset.")
    if not _state(page, active):
        return "fail", f"{active} is missing."
    checkpoint = _BRANCH_CHECKPOINT.get(case["case_id"])
    branch = "second" if criterion == "second_branch_path" else "first"
    if checkpoint is not None and checkpoint[0] == branch:
        if not _unavailable(page, advance) or not _available(page, checkpoint[1]):
            return "fail", f"{advance} was not gated by {checkpoint[1]}."
        actions.click(checkpoint[1])
    if not _available(page, advance):
        return "fail", f"{advance} is unavailable after required preparation."
    actions.click(advance)
    return _result(_state(page, "Completed"), "Chosen branch reached completion.", "Chosen branch did not reach completion.")


_FAMILY_OBSERVERS: dict[str, Callable[[dict[str, Any], Any, str, Actions], tuple[str, str]]] = {
    "ordered_decision": _ordered,
    "failure_recovery": _recovery,
    "cancel_restart": _wizard,
    "conditional_branch": _branch,
}


def _missing_rows(
    case: dict[str, Any], arm: str, run_kind: str, reason: str,
    delivery: Delivery | None,
) -> list[dict[str, Any]]:
    return [{
        "case_id": case["case_id"], "split": case["split"], "family": case["family"], "arm": arm,
        "run_kind": run_kind, "criterion_id": criterion, "label": "fail",
        "delivery_available": False, "reason": reason, "actions": [], "action_count": 0,
        "elapsed_seconds": 0.0, "before_visible_text": "", "after_visible_text": "",
        "console_errors": [], "page_errors": [], "evidence": None,
        "delivery_terminal_status": None if delivery is None else delivery.runtime_status,
        "delivery_exit_code": None if delivery is None else delivery.exit_code,
        "delivery_started_calls": None if delivery is None else delivery.started_calls,
        "delivery_terminal_product_failure": False if delivery is None else delivery.terminal_product_failure,
    } for criterion in obligations_for_case(case["case_id"])]


def observe(
    *, result_root: Path, manifest_path: Path, output: Path, evidence_dir: Path,
    split: str = "measured", browser_channel: str = "chrome", include_replays: bool = True,
) -> dict[str, Any]:
    if split not in {"development", "measured"}:
        raise ObservationError("split must be development or measured")
    if output.exists() or evidence_dir.exists():
        raise ObservationError("observation outputs must be new")
    deliveries = load_artifact_manifest(result_root, manifest_path)
    selected = [row for row in cases() if row["split"] == split]
    evidence_dir.mkdir(parents=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ObservationError("Playwright is unavailable") from exc
    rows: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=browser_channel, headless=True)
        try:
            schedule: list[tuple[dict[str, Any], str, str]] = [
                (case, arm, "primary") for case in selected for arm in ARMS
            ]
            if split == "measured" and include_replays:
                by_id = {case["case_id"]: case for case in selected}
                schedule.extend((by_id[case_id], arm, "supplementary_replay") for case_id in REPLAY_CASE_IDS for arm in ARMS)
            for case, arm, run_kind in schedule:
                delivery = deliveries.get((case["case_id"], arm))
                if delivery is None or not delivery.available or delivery.entrypoint is None:
                    reason = "missing delivery" if delivery is None else f"invalid delivery: {delivery.reason}"
                    rows.extend(_missing_rows(case, arm, run_kind, reason, delivery))
                    continue
                for criterion in obligations_for_case(case["case_id"]):
                    started = time.monotonic()
                    context = browser.new_context(viewport={"width": 1440, "height": 1000})
                    page = context.new_page()
                    page.set_default_timeout(5000)
                    page.route("**/*", _block_external)
                    console_errors: list[str] = []
                    page_errors: list[str] = []
                    page.on("console", lambda msg, target=console_errors: target.append(msg.text) if msg.type == "error" else None)
                    page.on("pageerror", lambda err, target=page_errors: target.append(str(err)))
                    actions = Actions(page)
                    stem = f"{run_kind}--{case['case_id']}--{arm}--{criterion}"
                    before_path = evidence_dir / f"{stem}--before.png"
                    after_path = evidence_dir / f"{stem}--after.png"
                    before_text = after_text = ""
                    label, reason = "unknown", "Browser observation did not complete."
                    try:
                        page.goto(delivery.entrypoint.as_uri(), wait_until="load")
                        before_text = page.locator("body").inner_text()[:12_000]
                        page.screenshot(path=str(before_path), full_page=True)
                        label, reason = _FAMILY_OBSERVERS[case["family"]](case, page, criterion, actions)
                        after_text = page.locator("body").inner_text()[:12_000]
                        page.screenshot(path=str(after_path), full_page=True)
                    except ObservationError:
                        raise
                    except Exception as exc:
                        page_errors.append(f"observer:{type(exc).__name__}: {exc}")
                        label, reason = "unknown", "Browser or evaluator exception made the observation ambiguous."
                        try:
                            after_text = page.locator("body").inner_text()[:12_000]
                            page.screenshot(path=str(after_path), full_page=True)
                        except Exception as evidence_exc:
                            page_errors.append(f"evidence:{type(evidence_exc).__name__}: {evidence_exc}")
                    elapsed = round(time.monotonic() - started, 3)
                    if elapsed > MAX_SECONDS:
                        label, reason = "unknown", "The 90-second observation budget was exceeded."
                    rows.append({
                        "case_id": case["case_id"], "split": split, "family": case["family"], "arm": arm,
                        "run_kind": run_kind, "criterion_id": criterion, "label": label,
                        "delivery_available": True, "reason": reason, "actions": actions.rows,
                        "action_count": len(actions.rows), "elapsed_seconds": elapsed,
                        "before_visible_text": before_text, "after_visible_text": after_text,
                        "console_errors": console_errors, "page_errors": page_errors,
                        "delivery_terminal_status": delivery.runtime_status,
                        "delivery_exit_code": delivery.exit_code,
                        "delivery_started_calls": delivery.started_calls,
                        "delivery_terminal_product_failure": delivery.terminal_product_failure,
                        "evidence": {
                            "before": before_path.relative_to(output.parent).as_posix(),
                            "after": after_path.relative_to(output.parent).as_posix(),
                        },
                    })
                    context.close()
        finally:
            browser.close()
    primary = [row for row in rows if row["run_kind"] == "primary"]
    repeats = [row for row in rows if row["run_kind"] == "supplementary_replay"]
    record = {
        "schema_version": SCHEMA, "status": "observation_complete", "split": split,
        "input_identity": frozen_input_identity(), "observer_identity": observer_identity(),
        "browser_channel": browser_channel, "artifact_manifest_sha256": _sha256(manifest_path.read_bytes()),
        "limits": {"fresh_context_per_obligation": True, "max_actions": MAX_ACTIONS, "max_seconds": MAX_SECONDS},
        "primary": {"row_count": len(primary), "label_counts": _counts(primary), "rows": primary},
        "supplementary_replay": {"excluded_from_primary": True, "row_count": len(repeats), "label_counts": _counts(repeats), "rows": repeats},
    }
    _write_new(output, record)
    return {
        "status": record["status"], "split": split,
        "primary_row_count": len(primary), "primary_label_counts": _counts(primary),
        "supplementary_row_count": len(repeats), "supplementary_label_counts": _counts(repeats),
        "output": str(output), "observation_sha256": _sha256(_canonical(record)),
    }


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {label: sum(row["label"] == label for row in rows) for label in ("pass", "fail", "unknown")}


def build_development_gate(
    *, observation: Path, artifact_manifest: Path, freeze: Path, output: Path,
) -> dict[str, Any]:
    """Bind an operable development observation and enforce readiness quality."""
    if output.exists():
        raise ObservationError("development gate output must be new")
    root = output.resolve().parent
    record = _read_json(observation)
    rows = record.get("primary", {}).get("rows") if isinstance(record.get("primary"), dict) else None
    expected_pairs = {
        (case["case_id"], arm)
        for case in cases() if case["split"] == "development"
        for arm in ARMS
    }
    actual_pairs = {
        (row.get("case_id"), row.get("arm"))
        for row in rows or [] if isinstance(row, dict)
    }
    expected_row_keys = {
        (case["case_id"], arm, criterion)
        for case in cases() if case["split"] == "development"
        for arm in ARMS
        for criterion in obligations_for_case(case["case_id"])
    }
    actual_row_keys = {
        (row.get("case_id"), row.get("arm"), row.get("criterion_id"))
        for row in rows or [] if isinstance(row, dict)
    }
    if (
        record.get("status") != "observation_complete"
        or record.get("split") != "development"
        or record.get("input_identity") != frozen_input_identity()
        or record.get("observer_identity") != observer_identity()
        or not isinstance(rows, list)
        or len(rows) != 72
        or actual_pairs != expected_pairs
        or actual_row_keys != expected_row_keys
        or len(actual_row_keys) != len(rows)
        or any(row.get("label") not in {"pass", "fail"} for row in rows)
    ):
        raise ObservationError("development observation is incomplete or inoperable")
    if record.get("artifact_manifest_sha256") != _sha256(artifact_manifest.read_bytes()):
        raise ObservationError("development artifact manifest binding drifted")
    manifest_value = _read_json(artifact_manifest)
    if (
        manifest_value.get("split") != "development"
        or manifest_value.get("freeze_sha256") != _sha256(freeze.read_bytes())
        or type(manifest_value.get("started_calls")) is not int
        or not 0 <= manifest_value["started_calls"] <= 24
        or manifest_value.get("stop_reason") is not None
        or not isinstance(manifest_value.get("rows"), list)
        or len(manifest_value["rows"]) != 12
    ):
        raise ObservationError("development runtime inventory is invalid")

    manifest_by_pair: dict[tuple[object, object], dict[str, Any]] = {}
    for manifest_row in manifest_value["rows"]:
        if not isinstance(manifest_row, dict):
            raise ObservationError("development runtime row is invalid")
        pair = (manifest_row.get("case_id"), manifest_row.get("arm"))
        if pair in manifest_by_pair:
            raise ObservationError("development runtime has duplicate case-arm row")
        manifest_by_pair[pair] = manifest_row
    if set(manifest_by_pair) != expected_pairs:
        raise ObservationError("development runtime case-arm inventory is incomplete")

    a_rows = [row for row in rows if row.get("arm") == "A"]
    a_case_pass_counts = {
        case["case_id"]: sum(
            row.get("label") == "pass"
            for row in a_rows
            if row.get("case_id") == case["case_id"]
        )
        for case in cases()
        if case["split"] == "development"
    }
    quality_policy = {
        "arm": "A",
        "minimum_total_passed_obligations": 20,
        "minimum_passed_obligations_per_case": 4,
        "required_available_deliveries": 4,
        "minimum_completed_model_deliveries": 3,
        "unknown_rows_allowed": 0,
    }
    a_available_pairs = {
        (row["case_id"], row["arm"])
        for row in a_rows
        if row.get("delivery_available") is True
    }
    a_manifest_rows = [
        row for (case_id, arm), row in manifest_by_pair.items() if arm == "A"
    ]
    quality_observed = {
        "total_passed_obligations": sum(a_case_pass_counts.values()),
        "passed_obligations_per_case": a_case_pass_counts,
        "available_deliveries": len(a_available_pairs),
        "completed_model_deliveries": sum(
            row.get("status") == "completed_model_delivery"
            for row in a_manifest_rows
        ),
        "unknown_rows": sum(row.get("label") == "unknown" for row in a_rows),
    }
    quality_passed = (
        quality_observed["total_passed_obligations"]
        >= quality_policy["minimum_total_passed_obligations"]
        and all(
            count >= quality_policy["minimum_passed_obligations_per_case"]
            for count in a_case_pass_counts.values()
        )
        and quality_observed["available_deliveries"]
        == quality_policy["required_available_deliveries"]
        and quality_observed["completed_model_deliveries"]
        >= quality_policy["minimum_completed_model_deliveries"]
        and quality_observed["unknown_rows"]
        == quality_policy["unknown_rows_allowed"]
    )
    if not quality_passed:
        raise ObservationError(
            "development quality gate failed; measured generation is not eligible"
        )

    unavailable_pairs = {
        (row["case_id"], row["arm"])
        for row in rows if row.get("delivery_available") is False
    }
    if unavailable_pairs == expected_pairs:
        raise ObservationError("development observation has no renderable delivery")
    for pair in unavailable_pairs:
        pair_rows = [row for row in rows if (row["case_id"], row["arm"]) == pair]
        manifest_row = manifest_by_pair[pair]
        if (
            len(pair_rows) != 6
            or any(row.get("label") != "fail" for row in pair_rows)
            or any(row.get("evidence") is not None for row in pair_rows)
            or any(row.get("delivery_terminal_product_failure") is not True for row in pair_rows)
            or (manifest_row.get("status"), manifest_row.get("exit_code")) not in {
                ("failed_or_interrupted", 2),
                ("failed_closed_consistency", 0),
            }
            or type(manifest_row.get("started_calls")) is not int
            or manifest_row["started_calls"] <= 0
        ):
            raise ObservationError("unavailable delivery is not a definitive terminal product failure")

    bound_paths = [freeze.resolve(strict=True), artifact_manifest.resolve(strict=True), observation.resolve(strict=True)]
    for row in rows:
        if row.get("delivery_available") is False:
            continue
        evidence = row.get("evidence")
        if not isinstance(evidence, dict) or set(evidence) != {"before", "after"}:
            raise ObservationError("development observation evidence is incomplete")
        for relative in evidence.values():
            bound_paths.append((observation.parent / str(relative)).resolve(strict=True))
    unique_paths = sorted(set(bound_paths))
    files = []
    for path in unique_paths:
        if path.is_symlink() or not path.is_file() or (path != root and root not in path.parents):
            raise ObservationError("development gate evidence must be regular files below the gate root")
        files.append({"path": path.relative_to(root).as_posix(), "sha256": _sha256(path.read_bytes())})
    value = {
        "schema_version": f"{SCHEMA}.development_gate.v1",
        "status": "development_gate_passed",
        "freeze_sha256": _sha256(freeze.read_bytes()),
        "input_identity": frozen_input_identity(),
        "observer_identity": observer_identity(),
        "actual_rendering_capability": True,
        "usable_protocol_output": True,
        "observation_operable": True,
        "development_rows": len(expected_pairs),
        "development_available_deliveries": len(expected_pairs) - len(unavailable_pairs),
        "development_terminal_product_failures": len(unavailable_pairs),
        "development_started_calls": manifest_value["started_calls"],
        "freeze_path": freeze.resolve().relative_to(root).as_posix(),
        "development_runtime_manifest_path": artifact_manifest.resolve().relative_to(root).as_posix(),
        "observation_path": observation.resolve().relative_to(root).as_posix(),
        "quality_pass_required": True,
        "quality_policy": quality_policy,
        "quality_observed": quality_observed,
        "quality_passed": True,
        "files": files,
    }
    _write_new(output, value)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--artifact-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "measured"), default="measured")
    parser.add_argument("--browser-channel", default="chrome")
    parser.add_argument("--no-replays", action="store_true")
    parser.add_argument("--development-gate-receipt", type=Path)
    parser.add_argument("--freeze", type=Path)
    args = parser.parse_args(argv)
    try:
        result = observe(
            result_root=args.result_root, manifest_path=args.artifact_manifest,
            output=args.output, evidence_dir=args.evidence_dir, split=args.split,
            browser_channel=args.browser_channel, include_replays=not args.no_replays,
        )
        if (args.development_gate_receipt is None) != (args.freeze is None):
            raise ObservationError("development gate receipt and freeze must be supplied together")
        if args.development_gate_receipt is not None:
            if args.split != "development":
                raise ObservationError("only the development split may create a gate receipt")
            gate = build_development_gate(
                observation=args.output,
                artifact_manifest=args.artifact_manifest,
                freeze=args.freeze,
                output=args.development_gate_receipt,
            )
            result["development_gate"] = str(args.development_gate_receipt)
            result["development_gate_sha256"] = _sha256(_canonical(gate))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed_closed", "error_type": type(exc).__name__, "message": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
