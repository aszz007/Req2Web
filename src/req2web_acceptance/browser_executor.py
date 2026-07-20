"""Deterministic browser execution for frozen Stage 3 M1 acceptance bindings.

This module consumes only an already-frozen AcceptanceBindingPlan. It never
derives obligations, changes bindings, invokes retrieval, or reads model/evaluator
artifacts. Unit tests inject a controlled backend; the Playwright backend is lazy.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse

from .binding import (
    ACCEPTANCE_BINDING_SCHEMA_VERSION,
    EXECUTABLE_STEP_PLAN_SCHEMA_VERSION,
    AcceptanceBindingPlan,
    CriterionBinding,
    ExecutableStep,
)


BROWSER_EXECUTION_SCHEMA_VERSION = "req2web.acceptance.browser_execution.v1"

_CRITERION_STATUSES = {"pass", "fail", "unknown", "not_supported"}
_STEP_STATUSES = {"pass", "fail", "unknown", "skipped"}
_BOUND_STATUSES = {"pass", "fail", "unknown"}
_ACTIONS = {
    "load_page",
    "assert_element_exists",
    "trigger_interaction",
    "assert_state",
    "assert_feedback",
}
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class BrowserExecutorError(RuntimeError):
    """Base class for controlled browser-backend failures."""


class BrowserBackendUnavailable(BrowserExecutorError):
    """Raised when the optional Playwright backend cannot be imported."""


class BrowserSafetyError(ValueError):
    """Raised before execution when a page address is outside the local boundary."""


class BrowserEvidenceUnavailable(BrowserExecutorError):
    """Raised when a backend cannot provide evidence required by a frozen step."""


class BrowserBackend(Protocol):
    """Minimal injectable backend contract for the frozen browser step-plan."""

    def navigate(self, page_url: str, timeout_ms: int) -> Mapping[str, str]: ...
    def element_exists(self, selector: str, timeout_ms: int) -> bool: ...
    def trigger(self, selector: str, timeout_ms: int) -> Mapping[str, str]: ...
    def read_state(self, timeout_ms: int) -> Mapping[str, str]: ...
    def read_text(self, selector: str, timeout_ms: int) -> str | None: ...
    def close(self) -> None: ...


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _sha(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return text


def _pairs(value: Mapping[str, str], field_name: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    result = tuple(sorted((_text(key, f"{field_name} key"), _text(item, f"{field_name}[{key}]")) for key, item in value.items()))
    if len({key for key, _ in result}) != len(result):
        raise ValueError(f"{field_name} keys must be unique")
    return result


def _pair_dict(value: tuple[tuple[str, str], ...], field_name: str) -> dict[str, str]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    result: dict[str, str] = {}
    for index, item in enumerate(value):
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(f"{field_name}[{index}] must be a key/value tuple")
        key = _text(item[0], f"{field_name}[{index}].key")
        data = _text(item[1], f"{field_name}[{index}].value")
        if key in result:
            raise ValueError(f"{field_name} keys must be unique")
        result[key] = data
    if tuple(sorted(result.items())) != value:
        raise ValueError(f"{field_name} must use canonical key order")
    return result


def _backend_map(value: object, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise BrowserEvidenceUnavailable(f"{field_name} did not return a mapping")
    try:
        return _pair_dict(_pairs(value, field_name), field_name)
    except ValueError as error:
        raise BrowserEvidenceUnavailable(str(error)) from error


def _duration_ms(clock: Callable[[], float], started_at: float) -> int:
    return max(0, int((clock() - started_at) * 1000))


def _page_identity(plan: AcceptanceBindingPlan) -> str:
    return "page-runtime-identity-" + sha256(_canonical_json_bytes({
        "observed_render_manifest_sha256": plan.observed_render_manifest_sha256,
        "page_id": plan.page_id,
        "source_page_spec_sha256": plan.source_page_spec_sha256,
    })).hexdigest()


def validate_local_page_url(page_url: str) -> str:
    """Allow file pages and only explicitly ported loopback HTTP(S) addresses."""

    text = _text(page_url, "page_url")
    parsed = urlparse(text)
    if parsed.scheme == "file" and parsed.netloc in {"", "localhost"} and parsed.path:
        return text
    if parsed.scheme in {"http", "https"} and parsed.hostname in _LOCAL_HOSTS and parsed.port is not None:
        return text
    raise BrowserSafetyError(
        "page_url must be a file:// address or an explicitly ported loopback HTTP(S) address"
    )


@dataclass(frozen=True)
class RuntimeStepEvidence:
    """Auditable result for one unchanged M1-03a ExecutableStep."""

    step_id: str
    binding_id: str
    criterion_id: str
    ordinal: int
    action_kind: str
    target_id: str
    selector: str
    source: str
    status: str
    expected_payload: tuple[tuple[str, str], ...]
    actual_payload: tuple[tuple[str, str], ...]
    evidence: tuple[tuple[str, str], ...]
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_kind": self.action_kind,
            "actual_payload": _pair_dict(self.actual_payload, "actual_payload"),
            "binding_id": self.binding_id,
            "criterion_id": self.criterion_id,
            "duration_ms": self.duration_ms,
            "evidence": _pair_dict(self.evidence, "evidence"),
            "expected_payload": _pair_dict(self.expected_payload, "expected_payload"),
            "ordinal": self.ordinal,
            "selector": self.selector,
            "source": self.source,
            "status": self.status,
            "step_id": self.step_id,
            "target_id": self.target_id,
        }


@dataclass(frozen=True)
class CriterionRuntimeResult:
    """Final criterion state; terminal results are copied exactly from binding."""

    criterion_id: str
    binding_id: str
    status: str
    step_ids: tuple[str, ...]
    evidence: tuple[tuple[str, str], ...]
    terminal_stage: str | None = None
    reason_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "binding_id": self.binding_id,
            "criterion_id": self.criterion_id,
            "evidence": _pair_dict(self.evidence, "criterion_evidence"),
            "status": self.status,
            "step_ids": list(self.step_ids),
        }
        if self.terminal_stage is not None:
            result["terminal_stage"] = self.terminal_stage
            result["reason_code"] = self.reason_code
        return result


@dataclass(frozen=True)
class BrowserExecutionReport:
    """Canonical runtime evidence bound to exactly one AcceptanceBindingPlan."""

    schema_version: str
    source_binding_schema_version: str
    source_step_plan_schema_version: str
    source_binding_plan_sha256: str
    page_id: str
    page_identity: str
    source_page_spec_sha256: str
    observed_render_manifest_sha256: str
    page_url_sha256: str
    criteria: tuple[CriterionRuntimeResult, ...]
    steps: tuple[RuntimeStepEvidence, ...]

    def validate(self) -> None:
        if self.schema_version != BROWSER_EXECUTION_SCHEMA_VERSION:
            raise ValueError(f"unsupported browser execution schema: {self.schema_version}")
        if self.source_binding_schema_version != ACCEPTANCE_BINDING_SCHEMA_VERSION:
            raise ValueError("browser report has an unsupported binding schema")
        if self.source_step_plan_schema_version != EXECUTABLE_STEP_PLAN_SCHEMA_VERSION:
            raise ValueError("browser report has an unsupported step-plan schema")
        for name in ("source_binding_plan_sha256", "source_page_spec_sha256", "observed_render_manifest_sha256", "page_url_sha256"):
            _sha(getattr(self, name), name)
        _text(self.page_id, "page_id")
        _text(self.page_identity, "page_identity")
        if not isinstance(self.criteria, tuple) or not isinstance(self.steps, tuple):
            raise ValueError("browser report criteria and steps must be tuples")
        if [step.ordinal for step in self.steps] != list(range(len(self.steps))):
            raise ValueError("browser report runtime ordinals must be continuous and ordered")
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("browser report runtime step IDs must be unique")
        if len({item.criterion_id for item in self.criteria}) != len(self.criteria):
            raise ValueError("browser report criterion IDs must be unique")
        if len({item.binding_id for item in self.criteria}) != len(self.criteria):
            raise ValueError("browser report binding IDs must be unique")
        for step in self.steps:
            if not isinstance(step, RuntimeStepEvidence) or step.status not in _STEP_STATUSES:
                raise ValueError("browser report has an invalid runtime step")
            if step.action_kind not in _ACTIONS or not isinstance(step.duration_ms, int) or step.duration_ms < 0:
                raise ValueError("browser report runtime step action or duration is invalid")
            for name in ("step_id", "binding_id", "criterion_id", "target_id", "selector", "source"):
                _text(getattr(step, name), f"runtime_step.{name}")
            _pair_dict(step.expected_payload, "runtime_step.expected_payload")
            _pair_dict(step.actual_payload, "runtime_step.actual_payload")
            _pair_dict(step.evidence, "runtime_step.evidence")
        for criterion in self.criteria:
            if not isinstance(criterion, CriterionRuntimeResult) or criterion.status not in _CRITERION_STATUSES:
                raise ValueError("browser report has an invalid criterion result")
            _text(criterion.criterion_id, "criterion.criterion_id")
            _text(criterion.binding_id, "criterion.binding_id")
            _pair_dict(criterion.evidence, "criterion_evidence")
            if len(criterion.step_ids) != len(set(criterion.step_ids)):
                raise ValueError("browser report criterion step IDs must be unique")
        flattened_step_ids = [step_id for criterion in self.criteria for step_id in criterion.step_ids]
        if flattened_step_ids != [step.step_id for step in self.steps]:
            raise ValueError("browser report criterion step IDs must preserve runtime step order")

    def validate_against(self, plan: AcceptanceBindingPlan) -> None:
        if not isinstance(plan, AcceptanceBindingPlan):
            raise TypeError("plan must be an AcceptanceBindingPlan")
        plan.validate()
        self.validate()
        if (
            self.source_binding_plan_sha256 != plan.sha256()
            or self.page_id != plan.page_id
            or self.page_identity != _page_identity(plan)
            or self.source_page_spec_sha256 != plan.source_page_spec_sha256
            or self.observed_render_manifest_sha256 != plan.observed_render_manifest_sha256
        ):
            raise ValueError("browser report identity does not match the supplied binding plan")
        if len(self.criteria) != len(plan.bindings) or len(self.steps) != len(plan.steps):
            raise ValueError("browser report must retain every frozen binding and step")
        by_step = {item.step_id: item for item in self.steps}
        if len(by_step) != len(self.steps):
            raise ValueError("browser report runtime step IDs must be unique")
        for frozen, actual in zip(plan.steps, self.steps):
            if (
                actual.step_id != frozen.step_id
                or actual.binding_id != frozen.binding_id
                or actual.criterion_id != frozen.criterion_id
                or actual.ordinal != frozen.ordinal
                or actual.action_kind != frozen.action_kind
                or actual.target_id != frozen.target_id
                or actual.selector != frozen.selector
                or actual.source != frozen.source
                or actual.expected_payload != frozen.expected_payload
            ):
                raise ValueError("browser report rewrites a frozen executable step")
        for binding, result in zip(plan.bindings, self.criteria):
            if result.criterion_id != binding.criterion_id or result.binding_id != binding.binding_id or result.step_ids != binding.step_ids:
                raise ValueError("browser report criterion identity does not match frozen binding")
            if binding.disposition == "terminal":
                if result.status != binding.terminal_status or result.terminal_stage != binding.terminal_stage or result.reason_code != binding.reason_code:
                    raise ValueError("terminal binding result must be inherited unchanged")
                continue
            if result.status not in _BOUND_STATUSES or result.terminal_stage is not None or result.reason_code is not None:
                raise ValueError("bound criterion has an invalid runtime result")
            statuses = [by_step[step_id].status for step_id in binding.step_ids]
            if result.status == "pass":
                if any(status != "pass" for status in statuses):
                    raise ValueError("passing criterion needs pass evidence for every step")
            else:
                first_non_pass = next((status for status in statuses if status != "pass"), None)
                if first_non_pass != result.status:
                    raise ValueError("criterion status must equal the first non-pass step")
                if any(status not in {"pass", result.status, "skipped"} for status in statuses):
                    raise ValueError("trailing browser steps must be explicit skips")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "criteria": [item.to_dict() for item in self.criteria],
            "observed_render_manifest_sha256": self.observed_render_manifest_sha256,
            "page_id": self.page_id,
            "page_identity": self.page_identity,
            "page_url_sha256": self.page_url_sha256,
            "schema_version": self.schema_version,
            "source_binding_plan_sha256": self.source_binding_plan_sha256,
            "source_binding_schema_version": self.source_binding_schema_version,
            "source_page_spec_sha256": self.source_page_spec_sha256,
            "source_step_plan_schema_version": self.source_step_plan_schema_version,
            "steps": [item.to_dict() for item in self.steps],
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


class LazyPlaywrightBrowserBackend:
    """Optional sync Playwright backend loaded only when actual execution starts."""

    def __init__(self, *, channel: str | None = None) -> None:
        if channel is not None:
            _text(channel, "channel")
        self._channel = channel
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._page: Any | None = None

    def _ensure_page(self, timeout_ms: int) -> Any:
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise BrowserBackendUnavailable(
                "Playwright is unavailable; inject a controlled backend for unit tests or install an approved browser runtime for integration execution"
            ) from error
        self._playwright = sync_playwright().start()
        try:
            launch_options = {} if self._channel is None else {"channel": self._channel}
            self._browser = self._playwright.chromium.launch(**launch_options)
            self._page = self._browser.new_page()
            self._page.set_default_timeout(timeout_ms)
            self._page.route("**/*", self._block_external_request)
        except Exception:
            self.close()
            raise
        return self._page

    @staticmethod
    def _block_external_request(route: Any) -> None:
        parsed = urlparse(route.request.url)
        local_http = parsed.scheme in {"http", "https"} and parsed.hostname in _LOCAL_HOSTS and parsed.port is not None
        local_file = parsed.scheme == "file" and parsed.netloc in {"", "localhost"}
        if local_http or local_file:
            route.continue_()
        else:
            route.abort()

    def navigate(self, page_url: str, timeout_ms: int) -> Mapping[str, str]:
        validate_local_page_url(page_url)
        self._ensure_page(timeout_ms).goto(page_url, wait_until="load", timeout=timeout_ms)
        return {"navigation": "completed"}

    def element_exists(self, selector: str, timeout_ms: int) -> bool:
        return self._ensure_page(timeout_ms).locator(selector).count() > 0

    @staticmethod
    def _element_descriptor(locator: Any) -> tuple[str, str] | None:
        if locator.count() != 1:
            return None
        descriptor = locator.evaluate(
            "(element) => ({tag_name: element.tagName.toLowerCase(), input_type: (element.getAttribute('type') || '').toLowerCase()})"
        )
        if not isinstance(descriptor, Mapping):
            raise BrowserEvidenceUnavailable("DOM descriptor must be a mapping")
        tag_name = descriptor.get("tag_name")
        input_type = descriptor.get("input_type")
        if not isinstance(tag_name, str) or not isinstance(input_type, str):
            raise BrowserEvidenceUnavailable("DOM descriptor fields must be strings")
        return tag_name, input_type

    def trigger(self, selector: str, timeout_ms: int) -> Mapping[str, str]:
        page = self._ensure_page(timeout_ms)
        locator = page.locator(selector)
        descriptor = self._element_descriptor(locator)
        if descriptor == ("form", ""):
            locator.evaluate("(form) => form.requestSubmit()")
            return {"triggered": "true", "mechanism": "form_request_submit"}
        if descriptor == ("input", "file"):
            locator.set_input_files({
                "name": "req2web-m1-fixture.txt",
                "mimeType": "text/plain",
                "buffer": b"req2web",
            })
            return {"triggered": "true", "mechanism": "file_change"}
        if descriptor is None and selector.startswith("[data-interaction-trigger="):
            file_locator = page.locator("input[type=file]" + selector)
            if self._element_descriptor(file_locator) == ("input", "file"):
                file_locator.set_input_files({
                    "name": "req2web-m1-fixture.txt",
                    "mimeType": "text/plain",
                    "buffer": b"req2web",
                })
                return {"triggered": "true", "mechanism": "file_change"}
        if descriptor is None:
            raise BrowserEvidenceUnavailable("frozen interaction selector is not a unique supported DOM target")
        locator.click(timeout=timeout_ms)
        return {"triggered": "true", "mechanism": "click"}

    def read_state(self, timeout_ms: int) -> Mapping[str, str]:
        value = self._ensure_page(timeout_ms).locator("#page-state").get_attribute("data-state-id", timeout=timeout_ms)
        if value is None:
            raise BrowserEvidenceUnavailable("#page-state has no data-state-id")
        return {"state_id": value}

    def read_text(self, selector: str, timeout_ms: int) -> str | None:
        return self._ensure_page(timeout_ms).locator(selector).text_content(timeout=timeout_ms)

    def close(self) -> None:
        """Attempt both cleanup operations without changing completed D04 evidence."""

        try:
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
        finally:
            try:
                if self._playwright is not None:
                    try:
                        self._playwright.stop()
                    except Exception:
                        pass
            finally:
                self._page = None
                self._browser = None
                self._playwright = None


def _runtime_step(step: ExecutableStep, status: str, actual: Mapping[str, str], evidence: Mapping[str, str], duration_ms: int) -> RuntimeStepEvidence:
    return RuntimeStepEvidence(
        step_id=step.step_id,
        binding_id=step.binding_id,
        criterion_id=step.criterion_id,
        ordinal=step.ordinal,
        action_kind=step.action_kind,
        target_id=step.target_id,
        selector=step.selector,
        source=step.source,
        status=status,
        expected_payload=step.expected_payload,
        actual_payload=_pairs(actual, "actual_payload"),
        evidence=_pairs(evidence, "evidence"),
        duration_ms=duration_ms,
    )


def _skipped_step(step: ExecutableStep, reason: str) -> RuntimeStepEvidence:
    return _runtime_step(step, "skipped", {"execution": "not_run"}, {"reason": reason}, 0)


def _run_step(backend: BrowserBackend, step: ExecutableStep, page_url: str, timeout_ms: int) -> tuple[str, dict[str, str], dict[str, str]]:
    expected = dict(step.expected_payload)
    if step.action_kind == "load_page":
        navigation = _backend_map(backend.navigate(page_url, timeout_ms), "navigate")
        exists = backend.element_exists(step.selector, timeout_ms)
        if not isinstance(exists, bool):
            raise BrowserEvidenceUnavailable("element_exists must return bool")
        actual = {**navigation, "selector_matched": str(exists).lower()}
        return ("pass" if exists else "fail"), actual, {"selector": step.selector}
    if step.action_kind == "assert_element_exists":
        exists = backend.element_exists(step.selector, timeout_ms)
        if not isinstance(exists, bool):
            raise BrowserEvidenceUnavailable("element_exists must return bool")
        return ("pass" if exists else "fail"), {"element_exists": str(exists).lower()}, {"selector": step.selector}
    if step.action_kind == "trigger_interaction":
        actual = _backend_map(backend.trigger(step.selector, timeout_ms), "trigger")
        if "triggered" not in actual or "mechanism" not in actual:
            raise BrowserEvidenceUnavailable("trigger evidence must include triggered and mechanism")
        if actual["triggered"] == "false":
            return "fail", actual, {"selector": step.selector}
        if actual["triggered"] != "true":
            raise BrowserEvidenceUnavailable("triggered must be true or false")
        if actual["mechanism"] not in {"click", "form_request_submit", "file_change"}:
            raise BrowserEvidenceUnavailable("trigger mechanism is not supported")
        return "pass", actual, {"selector": step.selector}
    if step.action_kind == "assert_state":
        exists = backend.element_exists(step.selector, timeout_ms)
        if not isinstance(exists, bool):
            raise BrowserEvidenceUnavailable("element_exists must return bool")
        state = _backend_map(backend.read_state(timeout_ms), "read_state")
        actual = {**state, "selector_matched": str(exists).lower()}
        return ("pass" if exists and state.get("state_id") == expected["state_id"] else "fail"), actual, {"selector": step.selector}
    if step.action_kind == "assert_feedback":
        feedback = backend.read_text(step.selector, timeout_ms)
        if feedback is None or not isinstance(feedback, str):
            raise BrowserEvidenceUnavailable("read_text did not return feedback evidence")
        return ("pass" if feedback == expected["feedback"] else "fail"), {"feedback": feedback}, {"selector": step.selector}
    raise BrowserEvidenceUnavailable(f"unsupported frozen action kind: {step.action_kind}")


def _terminal_result(binding: CriterionBinding) -> CriterionRuntimeResult:
    return CriterionRuntimeResult(
        criterion_id=binding.criterion_id,
        binding_id=binding.binding_id,
        status=binding.terminal_status or "unknown",
        step_ids=binding.step_ids,
        evidence=_pairs({"origin": "acceptance_binding_plan", "reason_code": binding.reason_code or "missing_terminal_reason"}, "criterion_evidence"),
        terminal_stage=binding.terminal_stage,
        reason_code=binding.reason_code,
    )


def execute_acceptance_binding_plan(
    binding_plan: AcceptanceBindingPlan,
    page_url: str,
    *,
    backend: BrowserBackend | None = None,
    timeout_ms: int = 5_000,
    clock: Callable[[], float] = time.monotonic,
) -> BrowserExecutionReport:
    """Execute only frozen bound steps and retain runtime evidence for every step.

    Binding fail and not_supported records are copied unchanged. A real assertion
    mismatch is fail; a backend timeout, interruption, unavailable browser, or
    missing evidence is unknown. Remaining steps are recorded as skipped after the
    first non-pass outcome for that criterion.
    """

    if not isinstance(binding_plan, AcceptanceBindingPlan):
        raise TypeError("binding_plan must be an AcceptanceBindingPlan")
    binding_plan.validate()
    if not isinstance(timeout_ms, int) or timeout_ms <= 0:
        raise ValueError("timeout_ms must be a positive integer")
    if not callable(clock):
        raise TypeError("clock must be callable")
    local_url = validate_local_page_url(page_url)
    by_id = {step.step_id: step for step in binding_plan.steps}
    runtime_steps: list[RuntimeStepEvidence] = []
    runtime_criteria: list[CriterionRuntimeResult] = []
    active_backend = backend
    backend_used = False
    try:
        for binding in binding_plan.bindings:
            if binding.disposition == "terminal":
                runtime_criteria.append(_terminal_result(binding))
                continue
            criterion_status = "pass"
            halted = False
            for step_id in binding.step_ids:
                step = by_id[step_id]
                if halted:
                    runtime_steps.append(_skipped_step(step, f"not_run_after_{criterion_status}"))
                    continue
                if active_backend is None:
                    active_backend = LazyPlaywrightBrowserBackend()
                backend_used = True
                started_at = clock()
                try:
                    status, actual, evidence = _run_step(active_backend, step, local_url, timeout_ms)
                except Exception as error:
                    status = "unknown"
                    actual = {"exception_type": type(error).__name__}
                    evidence = {"reason": "browser_runtime_interrupted"}
                runtime_steps.append(_runtime_step(step, status, actual, evidence, _duration_ms(clock, started_at)))
                if status != "pass":
                    criterion_status = status
                    halted = True
            runtime_criteria.append(CriterionRuntimeResult(
                criterion_id=binding.criterion_id,
                binding_id=binding.binding_id,
                status=criterion_status,
                step_ids=binding.step_ids,
                evidence=_pairs({"origin": "browser_executor", "result": criterion_status}, "criterion_evidence"),
            ))
    finally:
        if backend_used and active_backend is not None:
            try:
                active_backend.close()
            except Exception:
                # Cleanup happens after criterion execution and cannot overwrite its report.
                pass
    report = BrowserExecutionReport(
        schema_version=BROWSER_EXECUTION_SCHEMA_VERSION,
        source_binding_schema_version=binding_plan.schema_version,
        source_step_plan_schema_version=binding_plan.step_plan_schema_version,
        source_binding_plan_sha256=binding_plan.sha256(),
        page_id=binding_plan.page_id,
        page_identity=_page_identity(binding_plan),
        source_page_spec_sha256=binding_plan.source_page_spec_sha256,
        observed_render_manifest_sha256=binding_plan.observed_render_manifest_sha256,
        page_url_sha256=sha256(local_url.encode("utf-8")).hexdigest(),
        criteria=tuple(runtime_criteria),
        steps=tuple(runtime_steps),
    )
    report.validate_against(binding_plan)
    return report
