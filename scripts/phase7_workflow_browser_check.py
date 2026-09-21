"""Model-free real-renderer component check, not an experiment result."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from tests.test_page_renderer import build_spec
from req2web_generation import DeterministicPageRenderer


def check(output: Path):
    if output.exists():
        raise ValueError("Refusing an existing diagnostic output")
    spec = build_spec("Create an offline workflow with a required entry and a return action.")
    form = spec.components[0]
    back = spec.components[2]
    form.component_type = "form"
    form.label = "Required entry"
    back.label = "Return to entry"
    forward, reverse = spec.interactions
    reverse.source_state_id = "state-success"
    reverse.target_state_id = "state-initial"
    reverse.action = "Return to the initial state"
    reverse.user_feedback = "Ready for another entry"
    for state in spec.states:
        state.visible_component_ids = [c.component_id for c in spec.components]
    rendered = DeterministicPageRenderer().render(spec, output / "page")
    from playwright.sync_api import sync_playwright
    errors = []
    checks = []
    with sync_playwright() as browser_api:
        browser = browser_api.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(rendered.index_html.resolve().as_uri())
            back_button = page.get_by_role("button", name="Return to entry", exact=True)
            assert back_button.is_disabled()
            checks.append("return_disabled_in_initial_state")
            page.get_by_role("button", name="Submit", exact=True).click()
            assert page.locator("body").get_attribute("data-state-id") == "state-initial"
            assert page.get_by_label("Required entry", exact=True).get_attribute("aria-invalid") == "true"
            checks.append("empty_required_input_does_not_advance")
            page.get_by_label("Required entry", exact=True).fill("Example entry")
            page.get_by_role("button", name="Submit", exact=True).click()
            assert page.locator("body").get_attribute("data-state-id") == "state-success"
            assert page.get_by_role("button", name="Submit", exact=True).is_disabled()
            checks.append("valid_input_advances_and_old_action_disables")
            back_button.click()
            assert page.locator("body").get_attribute("data-state-id") == "state-initial"
            assert page.get_by_role("button", name="Submit", exact=True).is_enabled()
            assert page.locator(f"#{form.component_id} .action-feedback").inner_text() == ""
            checks.append("return_resets_stale_action_feedback")
            # Component API robustness test, never used by the experiment observer.
            page.evaluate("id => window.Req2WebRenderer.transition(id)", reverse.interaction_id)
            assert page.locator("body").get_attribute("data-state-id") == "state-initial"
            checks.append("out_of_state_transition_api_does_not_dispatch")
            page.screenshot(path=str(output / "after-return.png"), full_page=True)
            assert not errors, errors
            receipt = {"scope": "synthetic_component_browser_check_not_subject_run", "checks": checks,
                       "page_errors": errors, "browser": browser.version, "model_calls": 0}
        finally:
            browser.close()
    with (output / "receipt.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.output), indent=2))
