"""Prepare and execute the local black-box browser observation for Phase 7 E1.

Discovery is alias-only and never scores an artifact.  Measured observation is
added only after an exact binding packet has been frozen and validated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "req2web.phase7.e1.browser_observation.v1"
EXPECTED_ALIASES = tuple(f"artifact-{index:02d}" for index in range(1, 25))

CASE_OBLIGATIONS = {
    "p7-e1-expense-entry": ("reject_missing_description", "reject_non_positive_amount", "save_valid_claim_with_entered_values", "correct_invalid_input_and_save_without_reload"),
    "p7-e1-workshop-enrollment": ("require_name", "reject_malformed_email", "confirm_selected_session_and_name", "correct_invalid_email_and_complete"),
    "p7-e1-maintenance-ticket": ("require_location", "require_description", "show_submitted_category_location_description", "recover_from_validation_error_to_success"),
    "p7-e1-room-directory": ("show_declared_records", "capacity_filter_changes_matches", "combined_filters_show_meaningful_empty_state", "reset_restores_all_records"),
    "p7-e1-supply-catalogue": ("show_declared_records", "name_search_narrows_records", "category_and_search_combine", "clear_no_match_search_restores_records"),
    "p7-e1-course-directory": ("show_declared_courses", "subject_filter_works", "delivery_mode_further_narrows", "reset_empty_combination_restores_records"),
    "p7-e1-editorial-review": ("show_both_articles_and_statuses", "start_review_for_one_article", "approve_only_after_review", "other_article_unchanged"),
    "p7-e1-parcel-handover": ("show_both_parcels_and_statuses", "mark_one_ready", "collect_through_ordered_workflow", "other_parcel_unchanged"),
    "p7-e1-volunteer-assignment": ("show_both_tasks", "assign_named_volunteer_to_one", "complete_assigned_task", "reopen_without_changing_other"),
    "p7-e1-draft-notice": ("enter_title_and_body", "first_save_shows_failure", "failure_preserves_entered_values", "retry_shows_saved_title_and_body"),
    "p7-e1-booking-request": ("require_room_and_date", "first_valid_submit_shows_failure", "failure_preserves_room_and_date", "retry_confirms_matching_booking"),
    "p7-e1-bulk-tag-edit": ("select_two_leave_one_unselected", "first_apply_reports_failure", "failure_changes_no_tags", "retry_updates_only_selected_records"),
}

# The packet is intentionally alias-only: it exposes the public task needed for
# observation, but not the hidden A/B assignment.  Null selectors record a
# pre-measurement discovery finding that no semantic control/result region was
# present.  Requirement prose and generic component labels are not bindings.
_A = {"result": None, "primary": None, "secondary": None, "tertiary": None}
BINDING_SPECS: dict[str, dict[str, Any]] = {
    "artifact-01": {"case_id": "p7-e1-room-directory", "controls": _A},
    "artifact-02": {"case_id": "p7-e1-room-directory", "controls": {"result": "#resultsArea", "building": "#buildingFilter", "capacity": "#capacityFilter", "reset": "#resetBtn"}},
    "artifact-03": {"case_id": "p7-e1-parcel-handover", "controls": {"result": "#parcels-container", "mark": "button:has-text('Mark Ready')", "collect": "button:has-text('Collect Parcel')"}},
    "artifact-04": {"case_id": "p7-e1-parcel-handover", "controls": _A},
    "artifact-05": {"case_id": "p7-e1-booking-request", "controls": _A},
    "artifact-06": {"case_id": "p7-e1-booking-request", "controls": {"room": "#roomSelect", "date": "#dateInput", "submit": "#submitBtn", "error": "#errorMessage", "success": "#successContainer", "details": "#confirmationDetails"}},
    "artifact-07": {"case_id": "p7-e1-course-directory", "controls": {"result": "#directory-container", "subject": "#subject-filter", "mode": "#mode-filter", "reset": "#reset-btn"}},
    "artifact-08": {"case_id": "p7-e1-course-directory", "controls": _A},
    "artifact-09": {"case_id": "p7-e1-maintenance-ticket", "controls": _A},
    "artifact-10": {"case_id": "p7-e1-maintenance-ticket", "controls": {"category": "#category", "location": "#location", "description": "#description", "submit": "#submitBtn", "location_error": "#location-error", "description_error": "#description-error", "success": "#success-container", "summary": "#success-container .summary-box"}},
    "artifact-11": {"case_id": "p7-e1-volunteer-assignment", "controls": {"result": "#taskList", "assign": "#taskList .task-card:first-child button:has-text('Assign Volunteer')", "name": "#volunteerName", "confirm": "#confirmAssignBtn", "complete": "#taskList .task-card:first-child button:has-text('Complete Task')", "reopen": None, "log": "#systemLog"}},
    "artifact-12": {"case_id": "p7-e1-volunteer-assignment", "controls": _A},
    "artifact-13": {"case_id": "p7-e1-expense-entry", "controls": _A},
    "artifact-14": {"case_id": "p7-e1-expense-entry", "controls": {"description": "#description", "amount": "#amount", "submit": "#submitBtn", "description_error": "#descriptionError", "amount_error": "#amountError", "success": "#successStatus", "details": "#savedDetails"}},
    "artifact-15": {"case_id": "p7-e1-supply-catalogue", "controls": {"result": "#catalogueList", "search": "#searchInput", "category": "#categoryFilter"}},
    "artifact-16": {"case_id": "p7-e1-supply-catalogue", "controls": _A},
    "artifact-17": {"case_id": "p7-e1-draft-notice", "controls": _A},
    "artifact-18": {"case_id": "p7-e1-draft-notice", "controls": {"title": "#notice-title", "body": "#notice-body", "save": "button:has-text('Save Notice')", "alert": "#alert-container"}},
    "artifact-19": {"case_id": "p7-e1-bulk-tag-edit", "controls": {"result": "#recordsContainer", "first": "#rec-0", "second": "#rec-1", "third": "#rec-2", "apply": "#applyBtn", "log": "#logPanel", "status": "#systemStatus"}},
    "artifact-20": {"case_id": "p7-e1-bulk-tag-edit", "controls": _A},
    "artifact-21": {"case_id": "p7-e1-workshop-enrollment", "controls": _A},
    "artifact-22": {"case_id": "p7-e1-workshop-enrollment", "controls": {"name": "#fullName", "email": "#email", "session": "#sessionSelect", "submit": "#submitBtn", "name_error": "#nameError", "email_error": "#emailError", "confirmation": "#confirmationSection"}},
    "artifact-23": {"case_id": "p7-e1-editorial-review", "controls": {"result": "#article-grid", "start": "#article-grid .card:first-child button:has-text('Start Review')", "approve": "#article-grid .card:first-child button:has-text('Approve Article')"}},
    "artifact-24": {"case_id": "p7-e1-editorial-review", "controls": _A},
}

# Post-observation adjudication is deliberately narrow and immutable.  It fixes
# evaluator vocabulary/interpretation mistakes visible in the original
# screenshots/DOM record; it never reruns a page or changes an artifact.
ADJUDICATIONS = {
    ("artifact-02", "combined_filters_show_meaningful_empty_state"): ("fail", "pass", "The immutable evidence says 'No Records Found' and suggests adjusting filters; that is a meaningful empty state."),
    ("artifact-10", "require_location"): ("fail", "pass", "The bound required Location control blocked submission and received invalid focus in the immutable screenshot; native constraint validation satisfies the public obligation."),
    ("artifact-10", "require_description"): ("fail", "pass", "The bound required Problem Description control blocked submission and received invalid focus in the immutable screenshot; native constraint validation satisfies the public obligation."),
    ("artifact-19", "first_apply_reports_failure"): ("unknown", "fail", "The original trace is unambiguous product behavior: Apply Changes remained disabled after visible selection, so the intended apply/failure workflow was unreachable."),
    ("artifact-19", "failure_changes_no_tags"): ("unknown", "fail", "Apply Changes remained disabled after visible selection; the required failed apply could not be performed, so this intended behavior is missing rather than infrastructure-unknown."),
    ("artifact-19", "retry_updates_only_selected_records"): ("unknown", "fail", "Apply Changes remained disabled after visible selection, leaving no reachable retry/update behavior."),
    ("artifact-23", "start_review_for_one_article"): ("fail", "pass", "The immutable DOM evidence uses the visible label 'IN REVIEW'; the original evaluator incorrectly required the exact synonym 'REVIEWING'."),
}


class ObservationError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ObservationError(f"{path.name} must contain one JSON object")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _write_new(path: Path, value: object) -> None:
    raw = _canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()


def _load_alias_artifacts(
    result_root: Path, inventory_path: Path
) -> list[dict[str, Any]]:
    inventory = _read(inventory_path)
    rows = inventory.get("artifacts")
    if not isinstance(rows, list) or len(rows) != 24:
        raise ObservationError("artifact inventory must contain exactly 24 rows")
    aliases = [row.get("alias") for row in rows if isinstance(row, dict)]
    if tuple(aliases) != EXPECTED_ALIASES:
        raise ObservationError("artifact aliases are missing, duplicated, or reordered")
    result: list[dict[str, Any]] = []
    root = result_root.resolve(strict=True)
    for row in rows:
        relative = row.get("entrypoint")
        if not isinstance(relative, str) or not relative:
            raise ObservationError("artifact entrypoint is missing")
        page = (root / relative).resolve(strict=True)
        if root not in page.parents or not page.is_file() or page.is_symlink():
            raise ObservationError("artifact entrypoint is unsafe")
        raw = page.read_bytes()
        if _sha256(raw) != row.get("artifact_sha256"):
            raise ObservationError("artifact entrypoint hash drifted")
        result.append(
            {
                "alias": row["alias"],
                "artifact_sha256": row["artifact_sha256"],
                "entrypoint": relative,
                "page": page,
            }
        )
    return result


def _block_external(route: Any) -> None:
    parsed = urlparse(route.request.url)
    if parsed.scheme == "file" and parsed.netloc in {"", "localhost"}:
        route.continue_()
    else:
        route.abort()


def discover(
    *,
    result_root: Path,
    inventory_path: Path,
    output: Path,
    browser_channel: str,
) -> dict[str, Any]:
    artifacts = _load_alias_artifacts(result_root, inventory_path)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ObservationError("Playwright is unavailable") from exc
    rows: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=browser_channel, headless=True)
        try:
            for artifact in artifacts:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_default_timeout(10_000)
                page.route("**/*", _block_external)
                console_errors: list[str] = []
                page_errors: list[str] = []
                page.on(
                    "console",
                    lambda message, target=console_errors: (
                        target.append(message.text)
                        if message.type == "error"
                        else None
                    ),
                )
                page.on("pageerror", lambda error, target=page_errors: target.append(str(error)))
                page.goto(artifact["page"].as_uri(), wait_until="load")
                elements = page.locator(
                    "button,input,select,textarea,form,a,[role=button]"
                ).evaluate_all(
                    """elements => elements.map((element, index) => ({
                        index,
                        tag: element.tagName.toLowerCase(),
                        id: element.id || null,
                        type: element.getAttribute('type'),
                        name: element.getAttribute('name'),
                        role: element.getAttribute('role'),
                        aria_label: element.getAttribute('aria-label'),
                        placeholder: element.getAttribute('placeholder'),
                        text: (element.innerText || element.textContent || '').trim(),
                        value: 'value' in element ? element.value : null,
                        checked: 'checked' in element ? element.checked : null,
                        disabled: 'disabled' in element ? element.disabled : null,
                        options: element.tagName === 'SELECT'
                          ? Array.from(element.options).map(option => ({
                              text: option.textContent.trim(),
                              value: option.value
                            }))
                          : null,
                        data: Object.fromEntries(
                          Array.from(element.attributes)
                            .filter(attribute => attribute.name.startsWith('data-'))
                            .map(attribute => [attribute.name, attribute.value])
                        )
                    }))"""
                )
                rows.append(
                    {
                        "alias": artifact["alias"],
                        "artifact_sha256": artifact["artifact_sha256"],
                        "entrypoint": artifact["entrypoint"],
                        "title": page.title(),
                        "body_text": page.locator("body").inner_text()[:20_000],
                        "elements": elements,
                        "console_errors": console_errors,
                        "page_errors": page_errors,
                    }
                )
                page.close()
        finally:
            browser.close()
    record = {
        "schema_version": f"{SCHEMA}.discovery",
        "scope": "alias-only pre-measurement DOM discovery; no labels or scores",
        "browser_channel": browser_channel,
        "artifact_inventory_sha256": _sha256(inventory_path.read_bytes()),
        "artifacts": rows,
    }
    _write_new(output, record)
    return {
        "status": "alias_discovery_complete_no_measurement",
        "artifact_count": len(rows),
        "output": str(output),
        "discovery_sha256": _sha256(_canonical(record)),
    }


def freeze_bindings(*, discovery_path: Path, output: Path) -> dict[str, Any]:
    discovery = _read(discovery_path)
    if discovery.get("schema_version") != f"{SCHEMA}.discovery":
        raise ObservationError("unexpected discovery schema")
    rows = discovery.get("artifacts")
    if not isinstance(rows, list) or tuple(row.get("alias") for row in rows) != EXPECTED_ALIASES:
        raise ObservationError("discovery artifact inventory is invalid")
    by_alias = {row["alias"]: row for row in rows}
    bindings = []
    for alias in EXPECTED_ALIASES:
        spec = BINDING_SPECS[alias]
        controls = dict(spec["controls"])
        discovered = by_alias[alias]
        # Non-null bindings must be syntactically usable selectors.  Dynamic
        # selectors are allowed to match zero nodes at initial load.
        if any(not isinstance(value, str) or not value for value in controls.values() if value is not None):
            raise ObservationError("binding selector must be a non-empty string or null")
        bindings.append({
            "alias": alias,
            "artifact_sha256": discovered["artifact_sha256"],
            "case_id": spec["case_id"],
            "obligations": list(CASE_OBLIGATIONS[spec["case_id"]]),
            "controls": controls,
            "missing_control_policy": "visible missing intended behavior is fail",
        })
    packet = {
        "schema_version": f"{SCHEMA}.bindings",
        "status": "frozen_before_measured_observation",
        "observer_identity": "deterministic_playwright_black_box_agent_v1",
        "observer_kind": "agent_observed_engineering_result_not_human_evaluation",
        "arm_assignment_visible_to_observer": False,
        "discovery_sha256": _sha256(discovery_path.read_bytes()),
        "artifact_inventory_sha256": discovery["artifact_inventory_sha256"],
        "budgets": {"reset_before_each_obligation": True, "max_actions": 12, "max_seconds": 90},
        "bindings": bindings,
    }
    _write_new(output, packet)
    return {"status": packet["status"], "binding_count": len(bindings), "output": str(output), "binding_sha256": _sha256(_canonical(packet))}


class _Actions:
    def __init__(self, page: Any) -> None:
        self.page = page
        self.rows: list[dict[str, str]] = []

    def _add(self, action: str, selector: str, value: str = "") -> None:
        if len(self.rows) >= 12:
            raise ObservationError("browser action budget exceeded")
        row = {"action": action, "selector": selector}
        if value:
            row["value"] = value
        self.rows.append(row)

    def fill(self, selector: str, value: str) -> None:
        self.page.locator(selector).fill(value)
        self._add("fill", selector, value)

    def select(self, selector: str, value: str) -> None:
        self.page.locator(selector).select_option(value)
        self._add("select", selector, value)

    def click(self, selector: str) -> None:
        self.page.locator(selector).click()
        self._add("click", selector)


def _text(page: Any, selector: str) -> str:
    locator = page.locator(selector)
    if locator.count() != 1:
        return ""
    return locator.inner_text().strip()


def _visible(page: Any, selector: str) -> bool:
    locator = page.locator(selector)
    return locator.count() == 1 and locator.is_visible()


def _missing(controls: dict[str, Any], *names: str) -> str | None:
    absent = [name for name in names if not controls.get(name)]
    return None if not absent else "No frozen semantic control/result binding: " + ", ".join(absent)


def _result(ok: bool, passed: str, failed: str) -> tuple[str, str]:
    return ("pass", passed) if ok else ("fail", failed)


def _observe_case(page: Any, case_id: str, criterion: str, controls: dict[str, Any], actions: _Actions) -> tuple[str, str]:
    c = controls
    if case_id == "p7-e1-expense-entry":
        missing = _missing(c, "description", "amount", "submit")
        if missing: return "fail", missing
        if criterion == "reject_missing_description":
            actions.fill(c["amount"], "12.50"); actions.click(c["submit"])
            return _result(_visible(page, c["description_error"]), "Missing description was visibly rejected.", "Missing description produced no visible rejection.")
        if criterion == "reject_non_positive_amount":
            actions.fill(c["description"], "Train fare"); actions.fill(c["amount"], "0"); actions.click(c["submit"])
            return _result(_visible(page, c["amount_error"]), "Non-positive amount was visibly rejected.", "Non-positive amount produced no visible rejection.")
        if criterion == "save_valid_claim_with_entered_values":
            actions.fill(c["description"], "Train fare"); actions.fill(c["amount"], "12.50"); actions.click(c["submit"])
            text = _text(page, c["details"])
            return _result(_visible(page, c["success"]) and "Train fare" in text and "12.50" in text, "Valid claim showed the entered values.", "Valid claim did not show both entered values.")
        actions.fill(c["description"], "Train fare"); actions.fill(c["amount"], "0"); actions.click(c["submit"]); actions.fill(c["amount"], "12.50"); actions.click(c["submit"])
        return _result(_visible(page, c["success"]) and "Train fare" in _text(page, c["details"]), "Invalid amount was corrected and saved without reload.", "Correction did not reach visible success without reload.")

    if case_id == "p7-e1-workshop-enrollment":
        missing = _missing(c, "name", "email", "session", "submit")
        if missing: return "fail", missing
        if criterion == "require_name":
            actions.fill(c["email"], "jane@example.com"); actions.select(c["session"], "Accessibility Basics"); actions.click(c["submit"])
            return _result(_visible(page, c["name_error"]), "Missing name was visibly rejected.", "Missing name produced no visible rejection.")
        if criterion == "reject_malformed_email":
            actions.fill(c["name"], "Jane Doe"); actions.fill(c["email"], "bad-email"); actions.select(c["session"], "Accessibility Basics"); actions.click(c["submit"])
            return _result(_visible(page, c["email_error"]), "Malformed email was visibly rejected.", "Malformed email produced no visible rejection.")
        if criterion == "confirm_selected_session_and_name":
            actions.fill(c["name"], "Jane Doe"); actions.fill(c["email"], "jane@example.com"); actions.select(c["session"], "Accessibility Basics"); actions.click(c["submit"])
            text = _text(page, c["confirmation"])
            return _result(_visible(page, c["confirmation"]) and "Jane Doe" in text and "Accessibility Basics" in text, "Confirmation showed name and selected session.", "Confirmation did not show both name and selected session.")
        actions.fill(c["name"], "Jane Doe"); actions.fill(c["email"], "bad-email"); actions.select(c["session"], "Accessibility Basics"); actions.click(c["submit"]); actions.fill(c["email"], "jane@example.com"); actions.click(c["submit"])
        return _result(_visible(page, c["confirmation"]), "Invalid email was corrected and enrollment completed.", "Corrected email did not reach confirmation.")

    if case_id == "p7-e1-maintenance-ticket":
        missing = _missing(c, "category", "location", "description", "submit")
        if missing: return "fail", missing
        actions.select(c["category"], "Electrical")
        if criterion == "require_location":
            actions.fill(c["description"], "Light flickers"); actions.click(c["submit"])
            return _result(_visible(page, c["location_error"]), "Missing location was visibly rejected.", "Missing location produced no visible rejection.")
        if criterion == "require_description":
            actions.fill(c["location"], "Room 101"); actions.click(c["submit"])
            return _result(_visible(page, c["description_error"]), "Missing description was visibly rejected.", "Missing description produced no visible rejection.")
        if criterion == "recover_from_validation_error_to_success":
            actions.fill(c["description"], "Light flickers"); actions.click(c["submit"]); actions.fill(c["location"], "Room 101"); actions.click(c["submit"])
        else:
            actions.fill(c["location"], "Room 101"); actions.fill(c["description"], "Light flickers"); actions.click(c["submit"])
        page.locator(c["success"]).wait_for(state="visible", timeout=3000)
        summary = _text(page, c["summary"])
        ok = all(value in summary for value in ("Electrical", "Room 101", "Light flickers"))
        return _result(ok, "Submitted category, location, and description were visible.", "Success summary omitted submitted values.")

    if case_id in {"p7-e1-room-directory", "p7-e1-supply-catalogue", "p7-e1-course-directory"}:
        missing = _missing(c, "result")
        if missing: return "fail", missing
        result_selector = c["result"]
        if case_id == "p7-e1-room-directory":
            names = ("Orchid 101", "Maple 204", "Cedar 310", "Willow 415")
            if criterion == "show_declared_records": return _result(all(x in _text(page, result_selector) for x in names), "All four declared rooms were rendered as records.", "The result region did not render all declared rooms.")
            if criterion == "capacity_filter_changes_matches": actions.select(c["capacity"], "24"); return _result("Cedar 310" in _text(page, result_selector) and "Orchid 101" not in _text(page, result_selector), "Capacity filter changed the matches.", "Capacity filter did not narrow records correctly.")
            if criterion == "combined_filters_show_meaningful_empty_state": actions.select(c["building"], "North"); actions.select(c["capacity"], "40"); return _result("No rooms" in _text(page, result_selector), "Combined filters produced a meaningful empty state.", "Combined filters did not show a meaningful empty state.")
            actions.select(c["building"], "North"); actions.select(c["capacity"], "40"); actions.click(c["reset"]); return _result(all(x in _text(page, result_selector) for x in names), "Reset restored all four rooms.", "Reset did not restore all rooms.")
        if case_id == "p7-e1-supply-catalogue":
            names = ("Glass Beaker", "Nitrile Gloves", "Digital Scale", "Safety Goggles")
            if criterion == "show_declared_records": return _result(all(x in _text(page, result_selector) for x in names), "All four declared supplies were rendered as records.", "The result region did not render all declared supplies.")
            if criterion == "name_search_narrows_records": actions.fill(c["search"], "Beaker"); return _result("Glass Beaker" in _text(page, result_selector) and "Nitrile Gloves" not in _text(page, result_selector), "Name search narrowed records.", "Name search did not narrow records correctly.")
            if criterion == "category_and_search_combine": actions.select(c["category"], "Safety"); actions.fill(c["search"], "Goggles"); return _result("Safety Goggles" in _text(page, result_selector) and "Nitrile Gloves" not in _text(page, result_selector), "Category and search combined correctly.", "Category and search did not combine correctly.")
            actions.select(c["category"], "Safety"); actions.fill(c["search"], "not-a-supply"); actions.fill(c["search"], ""); return _result("Nitrile Gloves" in _text(page, result_selector) and "Safety Goggles" in _text(page, result_selector), "Clearing no-match search restored category matches.", "Clearing search did not restore matching records.")
        names = ("Data Ethics", "Web Systems", "Modern Poetry", "Design History")
        if criterion == "show_declared_courses": return _result(all(x in _text(page, result_selector) for x in names), "All four declared courses were rendered.", "The result region did not render all declared courses.")
        if criterion == "subject_filter_works": actions.select(c["subject"], "Computing"); return _result("Data Ethics" in _text(page, result_selector) and "Web Systems" in _text(page, result_selector) and "Modern Poetry" not in _text(page, result_selector), "Subject filter narrowed courses correctly.", "Subject filter did not narrow courses correctly.")
        if criterion == "delivery_mode_further_narrows": actions.select(c["subject"], "Computing"); actions.select(c["mode"], "Campus"); return _result("Web Systems" in _text(page, result_selector) and "Data Ethics" not in _text(page, result_selector), "Delivery mode further narrowed the subject result.", "Delivery mode did not further narrow results.")
        actions.select(c["subject"], "Humanities"); actions.select(c["mode"], "Campus"); actions.click(c["reset"]); return _result(all(x in _text(page, result_selector) for x in names), "Reset restored all courses after an empty combination.", "Reset did not restore all courses.")

    if case_id == "p7-e1-editorial-review":
        missing = _missing(c, "result")
        if missing: return "fail", missing
        initial = _text(page, c["result"])
        if criterion == "show_both_articles_and_statuses": return _result(all(x in initial for x in ("Coastal Data", "Urban Pollinators")) and initial.upper().count("SUBMITTED") >= 2, "Both articles and submitted statuses were rendered.", "Both article identities and submitted statuses were not rendered.")
        if not c.get("start") or page.locator(c["start"]).count() != 1: return "fail", "No visible start-review control was available for the first article."
        actions.click(c["start"]); reviewing = _text(page, c["result"])
        if criterion == "start_review_for_one_article": return _result("REVIEWING" in reviewing and "Urban Pollinators" in reviewing, "One article entered reviewing state.", "Starting review did not create the required visible state.")
        if not c.get("approve") or page.locator(c["approve"]).count() != 1: return "fail", "No visible approve control appeared after review started."
        actions.click(c["approve"]); final = _text(page, c["result"])
        if criterion == "approve_only_after_review": return _result("APPROVED" in final, "Article was approved after the visible reviewing step.", "Ordered reviewing-to-approved transition was not observed.")
        return _result("Urban Pollinators" in final and final.upper().count("SUBMITTED") == 1, "The other article remained submitted.", "The other article did not remain unchanged.")

    if case_id == "p7-e1-parcel-handover":
        missing = _missing(c, "result")
        if missing: return "fail", missing
        initial = _text(page, c["result"])
        if criterion == "show_both_parcels_and_statuses": return _result(all(x in initial for x in ("PX-104", "PX-208")) and initial.upper().count("WAITING") >= 2, "Both parcels and waiting statuses were rendered.", "Both parcel identities and waiting statuses were not rendered.")
        if not c.get("mark") or page.locator(c["mark"]).count() == 0: return "fail", "No visible Mark Ready control was reachable from the reset page."
        actions.click(c["mark"])
        if criterion == "mark_one_ready": return _result("READY" in _text(page, c["result"]).upper(), "One parcel visibly entered ready state.", "Mark Ready did not produce a visible ready state.")
        if not c.get("collect") or page.locator(c["collect"]).count() == 0: return "fail", "No visible Collect Parcel control appeared after marking ready."
        actions.click(c["collect"]); final = _text(page, c["result"])
        if criterion == "collect_through_ordered_workflow": return _result("COLLECTED" in final.upper(), "Parcel was collected through the ordered visible workflow.", "Ordered collection was not observed.")
        return _result("PX-208" in final and "WAITING" in final.upper(), "The other parcel remained waiting.", "The other parcel did not remain unchanged.")

    if case_id == "p7-e1-volunteer-assignment":
        missing = _missing(c, "result")
        if missing: return "fail", missing
        initial = _text(page, c["result"])
        if criterion == "show_both_tasks": return _result(all(x in initial for x in ("Pack welcome kits", "Prepare signs")), "Both declared tasks were rendered.", "Both declared tasks were not rendered.")
        if not c.get("assign") or page.locator(c["assign"]).count() != 1: return "fail", "No visible assignment control was available."
        actions.click(c["assign"]); actions.fill(c["name"], "Jane Doe"); actions.click(c["confirm"])
        log = _text(page, c["log"])
        if criterion == "assign_named_volunteer_to_one": return _result("Jane Doe" in log and "Pack welcome kits" in log, "Named volunteer assignment was visible in the activity log.", "Named assignment was not visibly confirmed.")
        if criterion == "complete_assigned_task": return _result("Completed" in _text(page, c["result"]), "The assigned task reached completed state.", "The assigned task did not reach completed state.")
        if not c.get("reopen"): return "fail", "No visible reopen control was present after completion."
        actions.click(c["reopen"]); final = _text(page, c["result"])
        return _result("Open" in final and "Prepare signs" in final, "Completed task was reopened without changing the other task.", "Reopen behavior was not observed.")

    if case_id == "p7-e1-draft-notice":
        missing = _missing(c, "title", "body", "save")
        if missing: return "fail", missing
        actions.fill(c["title"], "Campus closure"); actions.fill(c["body"], "Library closes at six.")
        if criterion == "enter_title_and_body": return _result(page.locator(c["title"]).input_value() == "Campus closure" and page.locator(c["body"]).input_value() == "Library closes at six.", "Title and body accepted entered values.", "Title and body did not retain entered values.")
        actions.click(c["save"]); page.wait_for_timeout(100)
        if criterion == "first_save_shows_failure": return _result("Save Failed" in _text(page, c["alert"]), "First save visibly reported failure.", "First save did not visibly report failure.")
        if criterion == "failure_preserves_entered_values": return _result(page.locator(c["title"]).input_value() == "Campus closure" and page.locator(c["body"]).input_value() == "Library closes at six.", "Failure preserved title and body.", "Failure did not preserve both entered values.")
        actions.click(c["save"]); page.wait_for_timeout(1000)
        ok = "Success" in _text(page, c["alert"]) and page.locator(c["title"]).input_value() == "Campus closure" and page.locator(c["body"]).input_value() == "Library closes at six."
        return _result(ok, "Retry showed success with the saved title and body still visible.", "Retry did not show success with matching title and body.")

    if case_id == "p7-e1-booking-request":
        missing = _missing(c, "room", "date", "submit")
        if missing: return "fail", missing
        if criterion == "require_room_and_date":
            page.locator(c["date"]).fill(""); actions.click(c["submit"])
            ok = page.locator(c["room"]).get_attribute("required") is not None and page.locator(c["date"]).get_attribute("required") is not None and not _visible(page, c["success"])
            return _result(ok, "Browser-visible required controls blocked an empty booking.", "Room/date were not both visibly required.")
        actions.select(c["room"], "Orchid 101"); actions.fill(c["date"], "2026-10-01"); actions.click(c["submit"]); page.wait_for_timeout(1700)
        if criterion == "first_valid_submit_shows_failure": return _result(_visible(page, c["error"]), "First valid submit visibly failed.", "First valid submit did not visibly fail.")
        if criterion == "failure_preserves_room_and_date": return _result(page.locator(c["room"]).input_value() == "Orchid 101" and page.locator(c["date"]).input_value() == "2026-10-01", "Failure preserved room and date.", "Failure did not preserve both values.")
        actions.click(c["submit"]); page.wait_for_timeout(1700)
        details = _text(page, c["details"])
        return _result(_visible(page, c["success"]) and "Orchid 101" in details and "2026-10-01" in details, "Retry showed a matching booking confirmation.", "Retry did not show a matching confirmation.")

    if case_id == "p7-e1-bulk-tag-edit":
        missing = _missing(c, "result", "first", "second", "third", "apply")
        if missing: return "fail", missing
        actions.click(c["first"]); actions.click(c["second"])
        if criterion == "select_two_leave_one_unselected":
            ok = page.locator(c["first"]).is_checked() and page.locator(c["second"]).is_checked() and not page.locator(c["third"]).is_checked()
            return _result(ok, "Two records were selected and one remained unselected.", "The requested two-of-three selection was not retained.")
        actions.click(c["apply"]); page.wait_for_timeout(100)
        if criterion == "first_apply_reports_failure": return _result("ERROR" in _text(page, c["log"]), "First apply visibly reported failure.", "First apply did not visibly report failure.")
        if criterion == "failure_changes_no_tags": return _result(_text(page, c["result"]).lower().count("draft") == 3, "Failure left all three tags unchanged.", "A tag changed during the failure state.")
        page.wait_for_timeout(1000); actions.click(c["apply"]); page.wait_for_timeout(1000)
        final = _text(page, c["result"])
        return _result(final.lower().count("updated") == 2 and "Spec Gamma" in final and final.lower().count("draft") == 1, "Retry updated only the two selected records.", "Retry did not update exactly the selected records.")

    raise ObservationError(f"unsupported case/criterion: {case_id}/{criterion}")


def run_observation(*, result_root: Path, inventory_path: Path, discovery_path: Path, bindings_path: Path, output: Path, evidence_dir: Path, browser_channel: str) -> dict[str, Any]:
    artifacts = _load_alias_artifacts(result_root, inventory_path)
    discovery = _read(discovery_path); bindings_doc = _read(bindings_path)
    if bindings_doc.get("schema_version") != f"{SCHEMA}.bindings" or bindings_doc.get("status") != "frozen_before_measured_observation":
        raise ObservationError("bindings are not a frozen observation packet")
    if bindings_doc.get("discovery_sha256") != _sha256(discovery_path.read_bytes()):
        raise ObservationError("binding packet does not match discovery bytes")
    if bindings_doc.get("artifact_inventory_sha256") != _sha256(inventory_path.read_bytes()):
        raise ObservationError("binding packet does not match artifact inventory")
    binding_rows = bindings_doc.get("bindings")
    if not isinstance(binding_rows, list) or tuple(row.get("alias") for row in binding_rows) != EXPECTED_ALIASES:
        raise ObservationError("binding inventory is invalid")
    bindings = {row["alias"]: row for row in binding_rows}
    for artifact in artifacts:
        binding = bindings[artifact["alias"]]
        if binding.get("artifact_sha256") != artifact["artifact_sha256"] or tuple(binding.get("obligations", [])) != CASE_OBLIGATIONS.get(binding.get("case_id"), ()):
            raise ObservationError("binding artifact or obligation identity drifted")
    if output.exists() or evidence_dir.exists():
        raise ObservationError("measured observation outputs must be new")
    evidence_dir.mkdir(parents=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ObservationError("Playwright is unavailable") from exc
    rows: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=browser_channel, headless=True)
        try:
            for artifact in artifacts:
                binding = bindings[artifact["alias"]]
                for criterion in binding["obligations"]:
                    started = time.monotonic()
                    context = browser.new_context(viewport={"width": 1440, "height": 1000})
                    page = context.new_page(); page.set_default_timeout(5000); page.route("**/*", _block_external)
                    actions = _Actions(page); label = "unknown"; reason = "Observation did not complete."
                    errors: list[str] = []
                    try:
                        page.goto(artifact["page"].as_uri(), wait_until="load")
                        label, reason = _observe_case(page, binding["case_id"], criterion, binding["controls"], actions)
                    except Exception as exc:  # browser/evaluator ambiguity is unknown
                        errors.append(f"{type(exc).__name__}: {exc}")
                        label, reason = "unknown", "Browser or evaluator exception made the observation ambiguous."
                    elapsed = round(time.monotonic() - started, 3)
                    if elapsed > 90:
                        label, reason = "unknown", "The 90-second observation budget was exceeded."
                    screenshot = evidence_dir / f"{artifact['alias']}--{criterion}.png"
                    try:
                        page.screenshot(path=str(screenshot), full_page=True)
                        visible_text = page.locator("body").inner_text()[:12_000]
                    except Exception as exc:
                        errors.append(f"evidence:{type(exc).__name__}: {exc}"); visible_text = ""
                    rows.append({
                        "alias": artifact["alias"], "case_id": binding["case_id"], "criterion_id": criterion,
                        "label": label, "artifact_sha256": artifact["artifact_sha256"], "actions": actions.rows,
                        "elapsed_seconds": elapsed, "evidence": screenshot.relative_to(output.parent).as_posix(),
                        "reason": reason, "evidence_detail": {"visible_text": visible_text, "errors": errors},
                    })
                    context.close()
        finally:
            browser.close()
    record = {
        "schema_version": f"{SCHEMA}.observations.alias_only",
        "status": "measured_observation_complete",
        "observer_identity": bindings_doc["observer_identity"],
        "observer_kind": bindings_doc["observer_kind"],
        "browser_channel": browser_channel,
        "binding_sha256": _sha256(bindings_path.read_bytes()),
        "rows": rows,
    }
    _write_new(output, record)
    counts = {label: sum(row["label"] == label for row in rows) for label in ("pass", "fail", "unknown", "not_supported")}
    return {"status": record["status"], "row_count": len(rows), "label_counts": counts, "output": str(output), "observation_sha256": _sha256(_canonical(record))}


def score_observation(*, result_root: Path, inventory_path: Path, raw_observations_path: Path, alias_output: Path, private_output: Path, summary_output: Path) -> dict[str, Any]:
    raw = _read(raw_observations_path)
    if raw.get("schema_version") != f"{SCHEMA}.observations.alias_only" or raw.get("status") != "measured_observation_complete":
        raise ObservationError("raw measured observation record is invalid")
    raw_rows = raw.get("rows")
    if not isinstance(raw_rows, list) or len(raw_rows) != 96:
        raise ObservationError("raw observation inventory must contain exactly 96 rows")
    seen = {(row.get("alias"), row.get("criterion_id")) for row in raw_rows if isinstance(row, dict)}
    if len(seen) != 96 or not set(ADJUDICATIONS).issubset(seen):
        raise ObservationError("raw observation keys are missing or duplicated")
    final_rows: list[dict[str, Any]] = []
    changes = []
    for raw_row in raw_rows:
        row = json.loads(json.dumps(raw_row))
        key = (row["alias"], row["criterion_id"])
        decision = ADJUDICATIONS.get(key)
        if decision:
            before, after, rationale = decision
            if row.get("label") != before:
                raise ObservationError("adjudication source label drifted")
            if before == "unknown" and "element is not enabled" not in " ".join(row.get("evidence_detail", {}).get("errors", [])):
                raise ObservationError("disabled-control adjudication lacks its frozen error evidence")
            if key == ("artifact-02", "combined_filters_show_meaningful_empty_state") and "No Records Found" not in row.get("evidence_detail", {}).get("visible_text", ""):
                raise ObservationError("empty-state adjudication lacks its frozen DOM evidence")
            if key == ("artifact-23", "start_review_for_one_article") and "IN REVIEW" not in row.get("evidence_detail", {}).get("visible_text", ""):
                raise ObservationError("review-state adjudication lacks its frozen DOM evidence")
            row["label"] = after
            row["reason"] = rationale
            row["adjudication"] = {"source_label": before, "final_label": after, "rationale": rationale}
            changes.append({"alias": key[0], "criterion_id": key[1], "source_label": before, "final_label": after, "rationale": rationale})
        final_rows.append(row)
    alias_doc = {
        "schema_version": f"{SCHEMA}.observations.final_alias_only",
        "status": "agent_adjudication_complete_no_rerun",
        "raw_observation_sha256": _sha256(raw_observations_path.read_bytes()),
        "observer_identity": raw["observer_identity"], "observer_kind": raw["observer_kind"],
        "adjudication_count": len(changes), "adjudications": changes, "rows": final_rows,
    }

    inventory = _read(inventory_path)
    inventory_rows = inventory.get("artifacts")
    if not isinstance(inventory_rows, list) or len(inventory_rows) != 24:
        raise ObservationError("artifact inventory must contain 24 rows")
    by_alias = {row.get("alias"): row for row in inventory_rows if isinstance(row, dict)}
    if set(by_alias) != set(EXPECTED_ALIASES):
        raise ObservationError("artifact inventory aliases are invalid")
    artifact_map: dict[str, tuple[str, str, str, Path]] = {}
    scored_rows = []
    root = result_root.resolve(strict=True)
    for alias in EXPECTED_ALIASES:
        item = by_alias[alias]
        entry = (root / item["entrypoint"]).resolve(strict=True)
        if root not in entry.parents or _sha256(entry.read_bytes()) != item.get("artifact_sha256"):
            raise ObservationError("private score join found artifact drift")
        artifact_map[alias] = (item["case_id"], item["arm"], item["artifact_sha256"], entry)
    for row in final_rows:
        item = by_alias[row["alias"]]
        if row["case_id"] != item["case_id"] or row["artifact_sha256"] != item["artifact_sha256"]:
            raise ObservationError("private score join found case or hash drift")
        scored = json.loads(json.dumps(row)); scored["arm"] = item["arm"]; scored_rows.append(scored)
    from phase7_experiment1 import metrics
    metric = metrics(scored_rows, artifact_map)
    private_doc = {
        "schema_version": f"{SCHEMA}.observations.scored_private",
        "status": "private_arm_join_complete",
        "final_alias_observation_sha256": _sha256(_canonical(alias_doc)),
        "artifact_inventory_sha256": _sha256(inventory_path.read_bytes()),
        "rows": scored_rows,
    }
    summary_doc = {
        "schema_version": f"{SCHEMA}.summary.final",
        "status": "complete_agent_observed_engineering_result",
        "human_evaluation": False,
        "raw_observation_sha256": _sha256(raw_observations_path.read_bytes()),
        "final_alias_observation_sha256": _sha256(_canonical(alias_doc)),
        "private_scored_observation_sha256": _sha256(_canonical(private_doc)),
        "raw_label_counts": {label: sum(row["label"] == label for row in raw_rows) for label in ("pass", "fail", "unknown", "not_supported")},
        "final_label_counts": {label: sum(row["label"] == label for row in scored_rows) for label in ("pass", "fail", "unknown", "not_supported")},
        "adjudication_count": len(changes),
        "metrics": metric,
        "limitations": [
            "These are deterministic agent-observed browser labels, not human evaluation or a user study.",
            "The 12 tasks are project-authored synthetic development cases, not H1/gold or public benchmark tasks.",
            "The observation measures functional obligations in returned offline pages; it does not measure visual appeal or maintainability.",
        ],
    }
    _write_new(alias_output, alias_doc); _write_new(private_output, private_doc); _write_new(summary_output, summary_doc)
    return {"status": summary_doc["status"], "adjudication_count": len(changes), "final_label_counts": summary_doc["final_label_counts"], "metrics": metric, "summary": str(summary_output)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("discover")
    command.add_argument("--result-root", type=Path, required=True)
    command.add_argument("--artifact-inventory", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--browser-channel", default="chrome")
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--discovery", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--result-root", type=Path, required=True)
    run.add_argument("--artifact-inventory", type=Path, required=True)
    run.add_argument("--discovery", type=Path, required=True)
    run.add_argument("--bindings", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--evidence-dir", type=Path, required=True)
    run.add_argument("--browser-channel", default="chrome")
    score = subparsers.add_parser("score")
    score.add_argument("--result-root", type=Path, required=True)
    score.add_argument("--artifact-inventory", type=Path, required=True)
    score.add_argument("--raw-observations", type=Path, required=True)
    score.add_argument("--alias-output", type=Path, required=True)
    score.add_argument("--private-output", type=Path, required=True)
    score.add_argument("--summary-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            result = discover(result_root=args.result_root, inventory_path=args.artifact_inventory, output=args.output, browser_channel=args.browser_channel)
        elif args.command == "freeze":
            result = freeze_bindings(discovery_path=args.discovery, output=args.output)
        elif args.command == "run":
            result = run_observation(result_root=args.result_root, inventory_path=args.artifact_inventory, discovery_path=args.discovery, bindings_path=args.bindings, output=args.output, evidence_dir=args.evidence_dir, browser_channel=args.browser_channel)
        else:
            result = score_observation(result_root=args.result_root, inventory_path=args.artifact_inventory, raw_observations_path=args.raw_observations, alias_output=args.alias_output, private_output=args.private_output, summary_output=args.summary_output)
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
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
