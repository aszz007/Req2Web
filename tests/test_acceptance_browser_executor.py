from __future__ import annotations

import copy
from dataclasses import replace
import os
from pathlib import Path
import shutil
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_acceptance import (
    BrowserSafetyError,
    LazyPlaywrightBrowserBackend,
    compile_acceptance_binding,
    compile_acceptance_plan,
    execute_acceptance_binding_plan,
    project_requirement_view,
)
from req2web_agent import UseCase
from req2web_generation import DeterministicPageRenderer, PageSpecBuilder
from test_acceptance_binding import make_bundle


class FakeBrowserBackend:
    """Controlled in-memory backend; it never launches a browser or uses a network."""

    def __init__(
        self,
        binding_plan,
        *,
        missing_selectors: set[str] | None = None,
        raise_on_navigate: bool = False,
        fail_trigger_selectors: set[str] | None = None,
        trigger_evidence: dict[str, str] | None = None,
    ) -> None:
        self.missing_selectors = missing_selectors or set()
        self.raise_on_navigate = raise_on_navigate
        self.fail_trigger_selectors = fail_trigger_selectors or set()
        self.trigger_evidence = trigger_evidence
        self.calls: list[tuple[str, str]] = []
        self.closed = False
        self.current_state = "initial"
        self.current_feedback = ""
        self.group_index = -1
        self.trigger_index = 0
        steps_by_id = {step.step_id: step for step in binding_plan.steps}
        self.groups: list[list[tuple[str, str, str]]] = []
        for binding in binding_plan.bindings:
            if binding.disposition != "bound":
                continue
            steps = [steps_by_id[step_id] for step_id in binding.step_ids]
            group: list[tuple[str, str, str]] = []
            for index, step in enumerate(steps):
                if step.action_kind != "trigger_interaction":
                    continue
                feedback = next(
                    dict(candidate.expected_payload)["feedback"]
                    for candidate in steps[index + 1 :]
                    if candidate.action_kind == "assert_feedback"
                )
                group.append((
                    step.selector,
                    dict(step.expected_payload)["target_state_id"],
                    feedback,
                ))
            self.groups.append(group)

    def navigate(self, page_url: str, timeout_ms: int) -> dict[str, str]:
        self.calls.append(("navigate", page_url))
        if self.raise_on_navigate:
            raise TimeoutError("controlled navigation timeout")
        self.group_index += 1
        self.trigger_index = 0
        self.current_state = "initial"
        self.current_feedback = ""
        return {"navigation": "completed"}

    def element_exists(self, selector: str, timeout_ms: int) -> bool:
        self.calls.append(("element_exists", selector))
        return selector not in self.missing_selectors

    def trigger(self, selector: str, timeout_ms: int) -> dict[str, str]:
        self.calls.append(("trigger", selector))
        if selector in self.fail_trigger_selectors:
            return {"triggered": "false", "mechanism": "click"}
        expected_selector, target_state, feedback = self.groups[self.group_index][self.trigger_index]
        if selector != expected_selector:
            raise AssertionError("frozen trigger order was not preserved")
        self.trigger_index += 1
        self.current_state = target_state
        self.current_feedback = feedback
        if self.trigger_evidence is not None:
            return dict(self.trigger_evidence)
        return {"triggered": "true", "mechanism": "click"}

    def read_state(self, timeout_ms: int) -> dict[str, str]:
        self.calls.append(("read_state", "#page-state"))
        return {"state_id": self.current_state}

    def read_text(self, selector: str, timeout_ms: int) -> str:
        self.calls.append(("read_text", selector))
        return self.current_feedback

    def close(self) -> None:
        self.closed = True


class RecordingBackend:
    """Fails if a rejected page address reaches any backend operation."""

    def __init__(self) -> None:
        self.calls = 0

    def _called(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("external address must be rejected before backend execution")

    navigate = _called
    element_exists = _called
    trigger = _called
    read_state = _called
    read_text = _called
    close = _called


def make_branch_bundle():
    bundle = make_bundle(
        original_requirement=(
            "Create a mobile profile form, local image upload, and product search page "
            "with invalid input recovery."
        )
    )
    bundle.requirement_summary = "Form submission, local media input, and search interaction."
    bundle.task_type = "mixed_interaction"
    bundle.use_cases = [
        UseCase(
            use_case_id="use-case-form",
            title="Complete profile form",
            actor="member",
            goal="fill in the profile form",
            expected_outcome="profile saved",
        ),
        UseCase(
            use_case_id="use-case-media",
            title="Upload local image",
            actor="member",
            goal="upload an image",
            expected_outcome="image accepted",
        ),
        UseCase(
            use_case_id="use-case-search",
            title="Search products",
            actor="member",
            goal="search products",
            expected_outcome="result list shown",
        ),
    ]
    return bundle


class TriggerLocator:
    def __init__(self, page, selector: str, count: int, tag_name: str, input_type: str = "") -> None:
        self.page = page
        self.selector = selector
        self._count = count
        self.tag_name = tag_name
        self.input_type = input_type

    def count(self) -> int:
        self.page.operations.append(("count", self.selector))
        return self._count

    def evaluate(self, script: str):
        if "tag_name" in script:
            self.page.operations.append(("descriptor", self.selector))
            return {"tag_name": self.tag_name, "input_type": self.input_type}
        if "requestSubmit" in script:
            self.page.operations.append(("request_submit", self.selector))
            return None
        raise AssertionError("unexpected DOM evaluation")

    def set_input_files(self, fixture: dict[str, object]) -> None:
        self.page.operations.append(("set_input_files", self.selector))
        self.page.file_fixture = fixture

    def click(self, timeout: int) -> None:
        self.page.operations.append(("click", self.selector))


class TriggerPage:
    def __init__(self, locator_specs: dict[str, tuple[int, str, str]]) -> None:
        self.operations: list[tuple[str, str]] = []
        self.file_fixture: dict[str, object] | None = None
        self.locators = {
            selector: TriggerLocator(self, selector, count, tag_name, input_type)
            for selector, (count, tag_name, input_type) in locator_specs.items()
        }

    def locator(self, selector: str) -> TriggerLocator:
        self.operations.append(("locator", selector))
        return self.locators[selector]


class CleanupResource:
    def __init__(self, method: str, *, raises: bool = False) -> None:
        self.method = method
        self.raises = raises
        self.calls = 0

    def close(self) -> None:
        self.calls += 1
        if self.raises:
            raise RuntimeError("controlled browser close failure")

    def stop(self) -> None:
        self.calls += 1
        if self.raises:
            raise RuntimeError("controlled Playwright stop failure")


class LazyPlaywrightBackendBranchTest(unittest.TestCase):
    def make_backend(self, locator_specs: dict[str, tuple[int, str, str]]):
        backend = LazyPlaywrightBrowserBackend()
        page = TriggerPage(locator_specs)
        backend._page = page
        return backend, page

    def test_form_branch_uses_request_submit(self) -> None:
        selector = 'form[data-interaction-form="profile"]'
        backend, page = self.make_backend({selector: (1, "form", "")})

        self.assertEqual(backend.trigger(selector, 321), {"triggered": "true", "mechanism": "form_request_submit"})
        self.assertEqual(page.operations, [
            ("locator", selector),
            ("count", selector),
            ("descriptor", selector),
            ("request_submit", selector),
        ])

    def test_file_input_branch_uses_memory_fixture_and_change_path(self) -> None:
        selector = '[data-interaction-trigger="upload"]'
        file_selector = 'input[type=file]' + selector
        backend, page = self.make_backend({
            selector: (2, "button", ""),
            file_selector: (1, "input", "file"),
        })

        self.assertEqual(backend.trigger(selector, 321), {"triggered": "true", "mechanism": "file_change"})
        self.assertEqual(page.operations, [
            ("locator", selector),
            ("count", selector),
            ("locator", file_selector),
            ("count", file_selector),
            ("descriptor", file_selector),
            ("set_input_files", file_selector),
        ])
        self.assertEqual(page.file_fixture, {
            "name": "req2web-m1-fixture.txt",
            "mimeType": "text/plain",
            "buffer": b"req2web",
        })

    def test_ordinary_branch_uses_click(self) -> None:
        selector = '[data-interaction-trigger="search"]'
        backend, page = self.make_backend({selector: (1, "button", "")})

        self.assertEqual(backend.trigger(selector, 321), {"triggered": "true", "mechanism": "click"})
        self.assertEqual(page.operations, [
            ("locator", selector),
            ("count", selector),
            ("descriptor", selector),
            ("click", selector),
        ])

    def test_route_blocks_external_and_allows_only_local_requests(self) -> None:
        class FakeRequest:
            def __init__(self, url: str) -> None:
                self.url = url

        class FakeRoute:
            def __init__(self, url: str) -> None:
                self.request = FakeRequest(url)
                self.action = ""

            def continue_(self) -> None:
                self.action = "continue"

            def abort(self) -> None:
                self.action = "abort"

        expectations = {
            "https://example.test/asset.js": "abort",
            "http://127.0.0.1:8765/asset.js": "continue",
            "https://localhost:9443/asset.js": "continue",
            "file:///C:/tmp/asset.js": "continue",
        }
        for url, expected in expectations.items():
            with self.subTest(url=url):
                route = FakeRoute(url)
                LazyPlaywrightBrowserBackend._block_external_request(route)
                self.assertEqual(route.action, expected)

    def test_lazy_cleanup_attempts_both_resources_and_clears_handles(self) -> None:
        backend = LazyPlaywrightBrowserBackend()
        browser = CleanupResource("close", raises=True)
        playwright = CleanupResource("stop")
        backend._browser = browser
        backend._playwright = playwright
        backend._page = object()

        backend.close()

        self.assertEqual(browser.calls, 1)
        self.assertEqual(playwright.calls, 1)
        self.assertIsNone(backend._browser)
        self.assertIsNone(backend._playwright)
        self.assertIsNone(backend._page)


class AcceptanceBrowserExecutorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_acceptance_browser_executor" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.bundle = make_bundle()
        self.view = project_requirement_view(self.bundle)
        self.plan = compile_acceptance_plan(self.view)
        self.spec = PageSpecBuilder().build(self.bundle)
        self.renderer = DeterministicPageRenderer()
        self.render = self.renderer.render(self.spec, self.root / "page")
        self.binding = compile_acceptance_binding(self.view, self.plan, self.spec, self.render)
        self.page_url = (self.root / "page" / "index.html").resolve().as_uri()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        if self.root.parent.exists() and not any(self.root.parent.iterdir()):
            self.root.parent.rmdir()

    def run_fake(self, **kwargs):
        backend = FakeBrowserBackend(self.binding, **kwargs)
        report = execute_acceptance_binding_plan(
            self.binding,
            self.page_url,
            backend=backend,
            clock=lambda: 0.0,
        )
        return report, backend

    def test_all_bound_steps_pass_with_frozen_identity_and_order(self) -> None:
        report, backend = self.run_fake()

        report.validate_against(self.binding)
        self.assertTrue(backend.closed)
        self.assertEqual(
            [step.step_id for step in report.steps],
            [step.step_id for step in self.binding.steps],
        )
        self.assertEqual(
            [step.ordinal for step in report.steps],
            list(range(len(self.binding.steps))),
        )
        for frozen, runtime in zip(self.binding.steps, report.steps):
            self.assertEqual(runtime.target_id, frozen.target_id)
            self.assertEqual(runtime.selector, frozen.selector)
            self.assertEqual(runtime.source, frozen.source)
            self.assertEqual(runtime.to_dict()["target_id"], frozen.target_id)
            self.assertEqual(runtime.to_dict()["selector"], frozen.selector)
            self.assertEqual(runtime.to_dict()["source"], frozen.source)
        for binding, result in zip(self.binding.bindings, report.criteria):
            self.assertEqual(result.criterion_id, binding.criterion_id)
            if binding.disposition == "bound":
                self.assertEqual(result.status, "pass")
            else:
                self.assertEqual(result.status, binding.terminal_status)

    def test_assertion_mismatch_fails_and_retains_not_run_steps(self) -> None:
        first_bound = next(item for item in self.binding.bindings if item.disposition == "bound")
        failing_step = next(
            step
            for step in self.binding.steps
            if step.binding_id == first_bound.binding_id
            and step.action_kind == "assert_element_exists"
        )
        report, _ = self.run_fake(missing_selectors={failing_step.selector})

        result = next(item for item in report.criteria if item.binding_id == first_bound.binding_id)
        steps = [item for item in report.steps if item.binding_id == first_bound.binding_id]
        self.assertEqual(result.status, "fail")
        self.assertEqual(steps[0].status, "pass")
        self.assertEqual(steps[1].status, "fail")
        self.assertTrue(all(item.status == "skipped" for item in steps[2:]))
        self.assertEqual(dict(steps[2].actual_payload)["execution"], "not_run")
        self.assertEqual(dict(steps[2].evidence)["reason"], "not_run_after_fail")

    def test_timeout_or_runtime_exception_maps_to_unknown(self) -> None:
        report, backend = self.run_fake(raise_on_navigate=True)

        self.assertTrue(backend.closed)
        for binding in self.binding.bindings:
            result = next(item for item in report.criteria if item.binding_id == binding.binding_id)
            if binding.disposition == "bound":
                steps = [item for item in report.steps if item.binding_id == binding.binding_id]
                self.assertEqual(result.status, "unknown")
                self.assertEqual(steps[0].status, "unknown")
                self.assertTrue(all(item.status == "skipped" for item in steps[1:]))
                self.assertEqual(dict(steps[0].actual_payload)["exception_type"], "TimeoutError")
            else:
                self.assertEqual(result.status, binding.terminal_status)

    def test_terminal_fail_and_not_supported_are_inherited_unchanged(self) -> None:
        altered = copy.deepcopy(self.spec)
        trace = next(item for item in altered.traceability.use_cases if item.use_case_id == "use-case-search")
        trace.interaction_ids = [
            item for item in trace.interaction_ids if "recovery" in item
        ]
        altered.validate()
        render = self.renderer.render(altered, self.root / "terminal-page")
        binding = compile_acceptance_binding(self.view, self.plan, altered, render)
        backend = FakeBrowserBackend(binding)
        report = execute_acceptance_binding_plan(
            binding,
            (self.root / "terminal-page" / "index.html").resolve().as_uri(),
            backend=backend,
            clock=lambda: 0.0,
        )

        terminal_bindings = [item for item in binding.bindings if item.disposition == "terminal"]
        self.assertTrue(any(item.terminal_status == "fail" for item in terminal_bindings))
        self.assertTrue(any(item.terminal_status == "not_supported" for item in terminal_bindings))
        for frozen in terminal_bindings:
            result = next(item for item in report.criteria if item.binding_id == frozen.binding_id)
            self.assertEqual(result.status, frozen.terminal_status)
            self.assertEqual(result.terminal_stage, frozen.terminal_stage)
            self.assertEqual(result.reason_code, frozen.reason_code)
            self.assertFalse(result.step_ids)

    def test_external_network_is_blocked_before_backend_execution(self) -> None:
        backend = RecordingBackend()
        with self.assertRaises(BrowserSafetyError):
            execute_acceptance_binding_plan(
                self.binding,
                "https://example.test/page.html",
                backend=backend,
            )
        self.assertEqual(backend.calls, 0)

    def test_canonical_hash_is_stable_and_does_not_mutate_binding_input(self) -> None:
        original = copy.deepcopy(self.binding)
        first, _ = self.run_fake()
        second, _ = self.run_fake()

        self.assertEqual(self.binding, original)
        self.assertEqual(first.canonical_json_bytes(), second.canonical_json_bytes())
        self.assertEqual(first.sha256(), second.sha256())

    def test_each_supported_trigger_mechanism_passes_bound_steps_and_criteria(self) -> None:
        for mechanism in ("click", "form_request_submit", "file_change"):
            with self.subTest(mechanism=mechanism):
                report, _ = self.run_fake(
                    trigger_evidence={"triggered": "true", "mechanism": mechanism}
                )
                trigger_steps = [
                    step for step in report.steps if step.action_kind == "trigger_interaction"
                ]
                self.assertTrue(trigger_steps)
                self.assertTrue(all(step.status == "pass" for step in trigger_steps))
                self.assertTrue(all(
                    result.status == "pass"
                    for binding, result in zip(self.binding.bindings, report.criteria)
                    if binding.disposition == "bound"
                ))
                self.assertTrue(all(
                    dict(step.actual_payload)["mechanism"] == mechanism
                    for step in trigger_steps
                ))

    def test_false_trigger_evidence_fails_and_missing_mechanism_is_unknown(self) -> None:
        failed, _ = self.run_fake(
            trigger_evidence={"triggered": "false", "mechanism": "click"}
        )
        failed_trigger = next(
            step for step in failed.steps if step.action_kind == "trigger_interaction"
        )
        failed_result = next(
            result for result in failed.criteria if result.binding_id == failed_trigger.binding_id
        )
        self.assertEqual(failed_trigger.status, "fail")
        self.assertEqual(failed_result.status, "fail")

        unknown, _ = self.run_fake(trigger_evidence={"triggered": "true"})
        unknown_trigger = next(
            step for step in unknown.steps if step.action_kind == "trigger_interaction"
        )
        unknown_result = next(
            result for result in unknown.criteria if result.binding_id == unknown_trigger.binding_id
        )
        self.assertEqual(unknown_trigger.status, "unknown")
        self.assertEqual(unknown_result.status, "unknown")
        self.assertEqual(dict(unknown_trigger.actual_payload)["exception_type"], "BrowserEvidenceUnavailable")

    def test_cleanup_failure_does_not_erase_completed_criterion_report(self) -> None:
        class ClosingFailureBackend(FakeBrowserBackend):
            def close(self) -> None:
                super().close()
                raise RuntimeError("controlled cleanup failure")

        backend = ClosingFailureBackend(self.binding)
        report = execute_acceptance_binding_plan(
            self.binding,
            self.page_url,
            backend=backend,
            clock=lambda: 0.0,
        )

        self.assertTrue(backend.closed)
        report.validate_against(self.binding)
        self.assertTrue(any(item.status == "pass" for item in report.criteria))

    def test_intrinsic_validation_rejects_runtime_ordinal_and_criterion_step_order(self) -> None:
        report, _ = self.run_fake()
        bad_ordinal = replace(report, steps=(replace(report.steps[0], ordinal=99), *report.steps[1:]))
        with self.assertRaises(ValueError):
            bad_ordinal.validate()
        criterion = next(item for item in report.criteria if len(item.step_ids) > 1)
        bad_criterion = replace(criterion, step_ids=tuple(reversed(criterion.step_ids)))
        bad_order = replace(
            report,
            criteria=tuple(bad_criterion if item.binding_id == criterion.binding_id else item for item in report.criteria),
        )
        with self.assertRaises(ValueError):
            bad_order.validate()

    def test_report_validation_rejects_frozen_step_rewrite(self) -> None:
        report, _ = self.run_fake()
        forged_step = replace(report.steps[0], ordinal=99)
        forged = replace(report, steps=(forged_step, *report.steps[1:]))
        with self.assertRaises(ValueError):
            forged.validate_against(self.binding)
        for field_name, forged_value in (
            ("target_id", "forged-target"),
            ("selector", "[data-forged-target]"),
            ("source", "forged.source"),
        ):
            with self.subTest(field_name=field_name):
                replaced = replace(report.steps[0], **{field_name: forged_value})
                forged = replace(report, steps=(replaced, *report.steps[1:]))
                with self.assertRaises(ValueError):
                    forged.validate_against(self.binding)


@unittest.skipUnless(
    os.environ.get("REQ2WEB_RUN_BROWSER_INTEGRATION") == "1",
    "requires an approved local Playwright and Chromium installation",
)
class AcceptanceBrowserExecutorIntegrationTest(unittest.TestCase):
    """Opt-in local fixture only; it never navigates to an external service."""

    def test_rendered_file_fixture_passes_with_lazy_playwright(self) -> None:
        root = ROOT / "tests" / ".tmp_acceptance_browser_integration"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        try:
            bundle = make_branch_bundle()
            view = project_requirement_view(bundle)
            plan = compile_acceptance_plan(view)
            spec = PageSpecBuilder().build(bundle)
            render = DeterministicPageRenderer().render(spec, root / "page")
            binding = compile_acceptance_binding(view, plan, spec, render)
            component_types = {item.component_id: item.component_type for item in spec.components}
            interaction_components = {
                item.interaction_id: component_types[item.trigger_component_id]
                for item in spec.interactions
            }
            selectors = {
                step.target_id: step.selector
                for step in binding.steps
                if step.action_kind == "trigger_interaction"
            }
            self.assertTrue(any(
                interaction_components[interaction_id] == "form"
                and selectors[interaction_id].startswith("form[")
                for interaction_id in selectors
            ))
            self.assertTrue(any(
                interaction_components[interaction_id] == "media_input"
                and selectors[interaction_id].startswith("[data-interaction-trigger=")
                for interaction_id in selectors
            ))
            self.assertTrue(any(
                interaction_components[interaction_id] == "search_input"
                and selectors[interaction_id].startswith("[data-interaction-trigger=")
                for interaction_id in selectors
            ))
            report = execute_acceptance_binding_plan(
                binding,
                (root / "page" / "index.html").resolve().as_uri(),
                backend=LazyPlaywrightBrowserBackend(
                    channel=os.environ.get("REQ2WEB_PLAYWRIGHT_CHANNEL") or None,
                ),
            )
            report.validate_against(binding)
            self.assertTrue(all(
                result.status == "pass"
                for frozen, result in zip(binding.bindings, report.criteria)
                if frozen.disposition == "bound"
            ))
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
