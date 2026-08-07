"""Deterministic PageSpec/DOM binding for Stage 3 M1-03a.

This module binds already-frozen AcceptancePlan obligations to a validated candidate
PageSpec and the offline bundle produced by DeterministicPageRenderer. It does not
launch a browser or decide runtime outcomes. Candidate PageSpec fields provide only
binding targets: every obligation continues to originate from AcceptancePlan.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any, Mapping

from req2web_generation.renderer import (
    RENDER_MANIFEST_SCHEMA_VERSION,
    SUPPORTED_COMPONENT_TYPES,
    RenderResult,
)
from req2web_generation.schema import PAGE_SPEC_SCHEMA_VERSION, PageSpec

from .acceptance_plan import ACCEPTANCE_PLAN_SCHEMA_VERSION, AcceptancePlan, AcceptanceCriterion
from .requirement_view import INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION, RequirementView


ACCEPTANCE_BINDING_SCHEMA_VERSION = "req2web.acceptance.binding.v2"
EXECUTABLE_STEP_PLAN_SCHEMA_VERSION = "req2web.acceptance.step_plan.v2"

_TERMINAL_STATUSES = {"fail", "not_supported"}
_TERMINAL_STAGES = {"page_spec_binding", "render_binding", "capability_boundary"}
_SUPPORTED_ACTION_KINDS = {
    "load_page",
    "assert_element_exists",
    "trigger_interaction",
    "assert_state",
    "assert_feedback",
}
_SUPPORTED_RECOVERY_SEMANTICS = {"retry_recovery", "permission_recovery"}
_MANIFEST_FILE_NAMES = ("index.html", "styles.css", "app.js")
_PAGE_DATA_PREFIX = "const PAGE_DATA = Object.freeze("


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_sha256(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return text


def _mapping_tuple(value: Mapping[str, str], field_name: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{field_name} must be a non-empty mapping")
    items = tuple(sorted(
        (_require_text(key, f"{field_name} key"), _require_text(item, f"{field_name}[{key}]"))
        for key, item in value.items()
    ))
    if len({key for key, _ in items}) != len(items):
        raise ValueError(f"{field_name} keys must be unique")
    return items


def _mapping_dict(value: tuple[tuple[str, str], ...], field_name: str) -> dict[str, str]:
    if not isinstance(value, tuple) or not value:
        raise ValueError(f"{field_name} must be a non-empty tuple")
    result: dict[str, str] = {}
    for index, item in enumerate(value):
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(f"{field_name}[{index}] must be a key/value tuple")
        key = _require_text(item[0], f"{field_name}[{index}].key")
        item_value = _require_text(item[1], f"{field_name}[{index}].value")
        if key in result:
            raise ValueError(f"{field_name} keys must be unique")
        result[key] = item_value
    if tuple(sorted(result.items())) != value:
        raise ValueError(f"{field_name} must use canonical key order")
    return result


def _attribute_selector(attribute: str, value: str) -> str:
    return f"[{_require_text(attribute, 'selector attribute')}={json.dumps(_require_text(value, 'selector value'), ensure_ascii=False)}]"


def _binding_identifier(
    *,
    view_sha256: str,
    plan_sha256: str,
    page_spec_sha256: str,
    manifest_sha256: str,
    criterion_id: str,
    disposition: str,
    terminal_status: str | None,
    terminal_stage: str | None,
    reason_code: str | None,
    target_refs: tuple[tuple[str, str], ...],
) -> str:
    material = {
        "criterion_id": criterion_id,
        "disposition": disposition,
        "manifest_sha256": manifest_sha256,
        "reason_code": reason_code,
        "schema_version": ACCEPTANCE_BINDING_SCHEMA_VERSION,
        "source_acceptance_plan_sha256": plan_sha256,
        "source_page_spec_sha256": page_spec_sha256,
        "source_requirement_view_sha256": view_sha256,
        "target_refs": _mapping_dict(target_refs, "target_refs"),
        "terminal_stage": terminal_stage,
        "terminal_status": terminal_status,
    }
    return "acceptance-binding-" + sha256(_canonical_json_bytes(material)).hexdigest()


def _step_identifier(
    *,
    binding_id: str,
    criterion_id: str,
    ordinal: int,
    action_kind: str,
    target_id: str,
    selector: str,
    expected_payload: tuple[tuple[str, str], ...],
    source: str,
) -> str:
    material = {
        "action_kind": action_kind,
        "binding_id": binding_id,
        "criterion_id": criterion_id,
        "expected_payload": _mapping_dict(expected_payload, "expected_payload"),
        "ordinal": ordinal,
        "schema_version": EXECUTABLE_STEP_PLAN_SCHEMA_VERSION,
        "selector": selector,
        "source": source,
        "target_id": target_id,
    }
    return "acceptance-step-" + sha256(_canonical_json_bytes(material)).hexdigest()


@dataclass(frozen=True)
class ExecutableStep:
    step_id: str
    binding_id: str
    criterion_id: str
    ordinal: int
    action_kind: str
    target_id: str
    selector: str
    expected_payload: tuple[tuple[str, str], ...]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_kind": self.action_kind,
            "binding_id": self.binding_id,
            "criterion_id": self.criterion_id,
            "expected_payload": _mapping_dict(self.expected_payload, "expected_payload"),
            "ordinal": self.ordinal,
            "selector": self.selector,
            "source": self.source,
            "step_id": self.step_id,
            "target_id": self.target_id,
        }


@dataclass(frozen=True)
class CriterionBinding:
    binding_id: str
    criterion_id: str
    disposition: str
    target_refs: tuple[tuple[str, str], ...]
    step_ids: tuple[str, ...]
    terminal_status: str | None = None
    terminal_stage: str | None = None
    reason_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "binding_id": self.binding_id,
            "criterion_id": self.criterion_id,
            "disposition": self.disposition,
            "step_ids": list(self.step_ids),
            "target_refs": _mapping_dict(self.target_refs, "target_refs"),
        }
        if self.disposition == "terminal":
            result.update({
                "reason_code": self.reason_code,
                "terminal_stage": self.terminal_stage,
                "terminal_status": self.terminal_status,
            })
        return result


@dataclass(frozen=True)
class AcceptanceBindingPlan:
    schema_version: str
    step_plan_schema_version: str
    source_requirement_view_schema_version: str
    source_requirement_view_sha256: str
    source_acceptance_plan_schema_version: str
    source_acceptance_plan_sha256: str
    source_page_spec_schema_version: str
    source_page_spec_sha256: str
    page_id: str
    observed_render_manifest_sha256: str
    bindings: tuple[CriterionBinding, ...]
    steps: tuple[ExecutableStep, ...]

    def validate(self) -> None:
        if self.schema_version != ACCEPTANCE_BINDING_SCHEMA_VERSION:
            raise ValueError(f"unsupported acceptance binding schema: {self.schema_version}")
        if self.step_plan_schema_version != EXECUTABLE_STEP_PLAN_SCHEMA_VERSION:
            raise ValueError(f"unsupported executable step-plan schema: {self.step_plan_schema_version}")
        if self.source_requirement_view_schema_version != INTERNAL_REQUIREMENT_VIEW_SCHEMA_VERSION:
            raise ValueError("binding plan has an unsupported RequirementView schema")
        if self.source_acceptance_plan_schema_version != ACCEPTANCE_PLAN_SCHEMA_VERSION:
            raise ValueError("binding plan has an unsupported AcceptancePlan schema")
        if self.source_page_spec_schema_version != PAGE_SPEC_SCHEMA_VERSION:
            raise ValueError("binding plan has an unsupported PageSpec schema")
        for field_name in (
            "source_requirement_view_sha256",
            "source_acceptance_plan_sha256",
            "source_page_spec_sha256",
            "observed_render_manifest_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        _require_text(self.page_id, "page_id")
        if not isinstance(self.bindings, tuple) or not self.bindings:
            raise ValueError("bindings must be a non-empty tuple")
        if not isinstance(self.steps, tuple):
            raise ValueError("steps must be a tuple")

        binding_ids: set[str] = set()
        criterion_ids: set[str] = set()
        flattened_step_ids: list[str] = []
        for binding in self.bindings:
            if not isinstance(binding, CriterionBinding):
                raise ValueError("bindings must contain CriterionBinding instances")
            _require_text(binding.binding_id, "binding.binding_id")
            _require_text(binding.criterion_id, "binding.criterion_id")
            if binding.binding_id in binding_ids or binding.criterion_id in criterion_ids:
                raise ValueError("bindings must use unique binding_id and criterion_id values")
            binding_ids.add(binding.binding_id)
            criterion_ids.add(binding.criterion_id)
            target_refs = _mapping_dict(binding.target_refs, "target_refs")
            if binding.disposition == "bound":
                if binding.terminal_status is not None or binding.terminal_stage is not None or binding.reason_code is not None:
                    raise ValueError("bound bindings must not contain a terminal result")
                if not binding.step_ids:
                    raise ValueError("bound bindings must reference executable steps")
            elif binding.disposition == "terminal":
                if binding.terminal_status not in _TERMINAL_STATUSES:
                    raise ValueError("terminal bindings may only use fail or not_supported")
                if binding.terminal_stage not in _TERMINAL_STAGES or not binding.reason_code:
                    raise ValueError("terminal binding stage/reason is invalid")
                if binding.step_ids:
                    raise ValueError("terminal bindings must not contain executable steps")
                if binding.terminal_status == "not_supported" and binding.terminal_stage != "capability_boundary":
                    raise ValueError("not_supported may only be emitted at capability_boundary")
                if binding.terminal_status == "fail" and binding.terminal_stage not in {"page_spec_binding", "render_binding"}:
                    raise ValueError("fail may only be emitted by PageSpec or render binding")
            else:
                raise ValueError("binding disposition must be bound or terminal")
            expected_binding_id = _binding_identifier(
                view_sha256=self.source_requirement_view_sha256,
                plan_sha256=self.source_acceptance_plan_sha256,
                page_spec_sha256=self.source_page_spec_sha256,
                manifest_sha256=self.observed_render_manifest_sha256,
                criterion_id=binding.criterion_id,
                disposition=binding.disposition,
                terminal_status=binding.terminal_status,
                terminal_stage=binding.terminal_stage,
                reason_code=binding.reason_code,
                target_refs=tuple(target_refs.items()),
            )
            if binding.binding_id != expected_binding_id:
                raise ValueError("binding_id does not match immutable binding material")
            if len(binding.step_ids) != len(set(binding.step_ids)):
                raise ValueError("binding step_ids must be unique")
            flattened_step_ids.extend(binding.step_ids)

        step_ids: set[str] = set()
        for ordinal, step in enumerate(self.steps):
            if not isinstance(step, ExecutableStep):
                raise ValueError("steps must contain ExecutableStep instances")
            if step.ordinal != ordinal or step.action_kind not in _SUPPORTED_ACTION_KINDS:
                raise ValueError("steps must use continuous ordinals and supported action kinds")
            for field_name in ("step_id", "binding_id", "criterion_id", "target_id", "selector", "source"):
                _require_text(getattr(step, field_name), f"step.{field_name}")
            payload = _mapping_dict(step.expected_payload, "expected_payload")
            expected_step_id = _step_identifier(
                binding_id=step.binding_id,
                criterion_id=step.criterion_id,
                ordinal=step.ordinal,
                action_kind=step.action_kind,
                target_id=step.target_id,
                selector=step.selector,
                expected_payload=tuple(payload.items()),
                source=step.source,
            )
            if step.step_id != expected_step_id or step.step_id in step_ids:
                raise ValueError("step_id is invalid or duplicate")
            step_ids.add(step.step_id)
        if tuple(flattened_step_ids) != tuple(step.step_id for step in self.steps):
            raise ValueError("steps must follow binding order and binding step_ids")
        if any(step.binding_id not in binding_ids or step.criterion_id not in criterion_ids for step in self.steps):
            raise ValueError("step references an unknown binding or criterion")

    def validate_against(
        self,
        requirement_view: RequirementView,
        acceptance_plan: AcceptancePlan,
        page_spec: PageSpec,
        render_result: RenderResult,
    ) -> None:
        self.validate()
        expected = _build_acceptance_binding(requirement_view, acceptance_plan, page_spec, render_result)
        if self != expected:
            raise ValueError("AcceptanceBindingPlan does not exactly match its source inputs")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "bindings": [binding.to_dict() for binding in self.bindings],
            "observed_render_manifest_sha256": self.observed_render_manifest_sha256,
            "page_id": self.page_id,
            "schema_version": self.schema_version,
            "source_acceptance_plan_schema_version": self.source_acceptance_plan_schema_version,
            "source_acceptance_plan_sha256": self.source_acceptance_plan_sha256,
            "source_page_spec_schema_version": self.source_page_spec_schema_version,
            "source_page_spec_sha256": self.source_page_spec_sha256,
            "source_requirement_view_schema_version": self.source_requirement_view_schema_version,
            "source_requirement_view_sha256": self.source_requirement_view_sha256,
            "step_plan_schema_version": self.step_plan_schema_version,
            "steps": [step.to_dict() for step in self.steps],
        }

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_dict())

    def sha256(self) -> str:
        return sha256(self.canonical_json_bytes()).hexdigest()


@dataclass(frozen=True)
class _DOMSection:
    section_id: str
    tag: str
    use_case_ids: tuple[str, ...]


@dataclass(frozen=True)
class _DOMComponent:
    component_id: str
    tag: str
    id_attribute: str | None
    section_id: str | None
    component_type: str | None
    renderer_kind: str | None


@dataclass(frozen=True)
class _FeedbackTarget:
    target_id: str
    selector: str
    target_ref_key: str
    target_ref_value: str

    @property
    def component_id(self) -> str:
        return self.target_id


class _RenderedHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_schema_values: list[str | None] = []
        self.body_attributes: list[dict[str, str]] = []
        self.sections: list[_DOMSection] = []
        self.components: list[_DOMComponent] = []
        self.interaction_trigger_values: set[str] = set()
        self.interaction_form_values: set[str] = set()
        self.feedback_component_ids: set[str] = set()
        self.page_state_region_count = 0
        self.page_state_message_count = 0
        self._section_stack: list[tuple[str, str]] = []
        self._component_stack: list[tuple[str, str]] = []
        self._page_state_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value for key, value in attrs if value is not None}
        if tag == "html":
            self.html_schema_values.append(values.get("data-page-spec-schema"))
        if tag == "body":
            self.body_attributes.append(values)
        if values.get("id") == "page-state":
            self.page_state_region_count += 1
            self._page_state_stack.append(tag)
        if "state-message" in values.get("class", "").split() and self._page_state_stack:
            self.page_state_message_count += 1
        section_id = values.get("data-section-id")
        if section_id is not None:
            self.sections.append(_DOMSection(section_id, tag, tuple(item for item in values.get("data-use-case-ids", "").split(" ") if item)))
            self._section_stack.append((tag, section_id))
        component_id = values.get("data-component-id")
        if component_id is not None:
            self.components.append(_DOMComponent(
                component_id, tag, values.get("id"), self._section_stack[-1][1] if self._section_stack else None,
                values.get("data-component-type"), values.get("data-renderer-kind"),
            ))
            self._component_stack.append((tag, component_id))
        if values.get("data-interaction-trigger") is not None:
            self.interaction_trigger_values.add(values["data-interaction-trigger"])
        if values.get("data-interaction-form") is not None:
            self.interaction_form_values.add(values["data-interaction-form"])
        if "component-feedback" in values.get("class", "").split() and self._component_stack:
            self.feedback_component_ids.add(self._component_stack[-1][1])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._component_stack and self._component_stack[-1][0] == tag:
            self._component_stack.pop()
        if self._section_stack and self._section_stack[-1][0] == tag:
            self._section_stack.pop()
        if self._page_state_stack and self._page_state_stack[-1] == tag:
            self._page_state_stack.pop()


@dataclass(frozen=True)
class _RenderInspection:
    observed_manifest_sha256: str
    errors: tuple[str, ...]
    dom: _RenderedHTMLParser | None
    runtime_data: Mapping[str, Any] | None


def _sentinel_manifest_sha256(render_result: RenderResult) -> str:
    return sha256(_canonical_json_bytes({"render_manifest": str(getattr(render_result, "render_manifest", "")), "status": "unavailable"})).hexdigest()


def _read_utf8(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as error:
        return None, f"cannot_read:{path.name}:{error.__class__.__name__}"


def _extract_page_data(script: str) -> tuple[Mapping[str, Any] | None, str | None]:
    offset = script.find(_PAGE_DATA_PREFIX)
    if offset < 0:
        return None, "missing_page_data"
    start = offset + len(_PAGE_DATA_PREFIX)
    try:
        parsed, end = json.JSONDecoder().raw_decode(script[start:])
    except json.JSONDecodeError:
        return None, "invalid_page_data"
    if not isinstance(parsed, dict) or not script[start + end:].lstrip().startswith(");"):
        return None, "invalid_page_data_boundary"
    return parsed, None


def _inspect_render(page_spec: PageSpec, render_result: RenderResult) -> _RenderInspection:
    if not isinstance(render_result, RenderResult):
        raise TypeError("render_result must be a RenderResult")
    errors: list[str] = []
    if not isinstance(render_result.output_dir, Path):
        return _RenderInspection(_sentinel_manifest_sha256(render_result), ("render_result_output_dir_not_path",), None, None)
    output_dir = render_result.output_dir
    expected_paths = {
        "index.html": render_result.index_html,
        "styles.css": render_result.styles_css,
        "app.js": render_result.app_js,
        "render_manifest.json": render_result.render_manifest,
    }
    for name, path in expected_paths.items():
        if not isinstance(path, Path) or path != output_dir / name:
            errors.append(f"render_result_path_mismatch:{name}")
    if render_result.page_id != page_spec.page_id:
        errors.append("render_result_page_id_mismatch")
    try:
        manifest_bytes = render_result.render_manifest.read_bytes()
        manifest_sha256 = sha256(manifest_bytes).hexdigest()
    except OSError as error:
        manifest_bytes = None
        manifest_sha256 = _sentinel_manifest_sha256(render_result)
        errors.append(f"cannot_read:render_manifest.json:{error.__class__.__name__}")
    manifest: Mapping[str, Any] | None = None
    if manifest_bytes is not None:
        try:
            parsed = json.loads(manifest_bytes.decode("utf-8"))
            manifest = parsed if isinstance(parsed, dict) else None
            if manifest is None:
                errors.append("render_manifest_not_object")
        except (UnicodeDecodeError, json.JSONDecodeError):
            errors.append("render_manifest_invalid_json")
    if manifest is not None:
        if set(manifest) != {"files", "page_id", "page_spec_schema_version", "schema_version"}:
            errors.append("render_manifest_keys_mismatch")
        if manifest.get("schema_version") != RENDER_MANIFEST_SCHEMA_VERSION:
            errors.append("render_manifest_schema_mismatch")
        if manifest.get("page_spec_schema_version") != PAGE_SPEC_SCHEMA_VERSION:
            errors.append("render_manifest_page_spec_schema_mismatch")
        if manifest.get("page_id") != page_spec.page_id:
            errors.append("render_manifest_page_id_mismatch")
        files = manifest.get("files")
        if not isinstance(files, list) or len(files) != len(_MANIFEST_FILE_NAMES):
            errors.append("render_manifest_files_invalid")
        else:
            declared: list[tuple[str, str]] = []
            names_valid = True
            for entry in files:
                if not isinstance(entry, dict) or set(entry) != {"name", "sha256"}:
                    errors.append("render_manifest_file_entry_invalid")
                    names_valid = False
                    continue
                name = entry.get("name")
                digest = entry.get("sha256")
                if not isinstance(name, str) or not isinstance(digest, str):
                    errors.append("render_manifest_file_entry_invalid")
                    names_valid = False
                    continue
                if Path(name).is_absolute() or "/" in name or "\\" in name or name in {".", ".."}:
                    errors.append("render_manifest_unsafe_file_name")
                    names_valid = False
                    continue
                if name not in _MANIFEST_FILE_NAMES:
                    errors.append("render_manifest_undeclared_file_name")
                    names_valid = False
                    continue
                if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                    errors.append(f"render_manifest_file_hash_invalid:{name}")
                    names_valid = False
                    continue
                declared.append((name, digest))
            if not names_valid or tuple(name for name, _ in declared) != _MANIFEST_FILE_NAMES:
                errors.append("render_manifest_file_order_mismatch")
            else:
                for name, digest in declared:
                    try:
                        actual = sha256((output_dir / name).read_bytes()).hexdigest()
                    except OSError as error:
                        errors.append(f"cannot_read:{name}:{error.__class__.__name__}")
                        continue
                    if actual != digest:
                        errors.append(f"render_manifest_hash_mismatch:{name}")
    html, html_error = _read_utf8(render_result.index_html)
    dom: _RenderedHTMLParser | None = None
    if html_error:
        errors.append(html_error)
    elif html is not None:
        dom = _RenderedHTMLParser()
        try:
            dom.feed(html)
            dom.close()
        except Exception as error:
            errors.append(f"index_html_parse_error:{error.__class__.__name__}")
            dom = None
    if dom is not None:
        if dom.html_schema_values != [PAGE_SPEC_SCHEMA_VERSION]:
            errors.append("html_page_spec_schema_mismatch")
        if len(dom.body_attributes) != 1:
            errors.append("html_body_count_invalid")
        else:
            body = dom.body_attributes[0]
            if body.get("data-page-id") != page_spec.page_id:
                errors.append("html_body_page_id_mismatch")
            if body.get("data-target-device") != page_spec.target_device:
                errors.append("html_body_target_device_mismatch")
    script, script_error = _read_utf8(render_result.app_js)
    runtime: Mapping[str, Any] | None = None
    if script_error:
        errors.append(script_error)
    elif script is not None:
        runtime, runtime_error = _extract_page_data(script)
        if runtime_error:
            errors.append(runtime_error)
    if runtime is not None:
        expected_sections = {item.component_id: item.section_id for item in page_spec.components}
        expected_initial = next(
            (
                item.state_id
                for item in page_spec.states
                if item.name == "initial"
            ),
            page_spec.states[0].state_id,
        )
        expected_states = [{"description": item.description, "name": item.name, "state_id": item.state_id, "visible_component_ids": item.visible_component_ids} for item in page_spec.states]
        expected_interactions = [{"action": item.action, "interaction_id": item.interaction_id, "source_state_id": item.source_state_id, "target_state_id": item.target_state_id, "trigger_component_id": item.trigger_component_id, "user_feedback": item.user_feedback} for item in page_spec.interactions]
        if runtime.get("page_id") != page_spec.page_id:
            errors.append("runtime_page_id_mismatch")
        if runtime.get("component_sections") != expected_sections:
            errors.append("runtime_component_sections_mismatch")
        if runtime.get("initial_state_id") != expected_initial:
            errors.append("runtime_initial_state_mismatch")
        if runtime.get("states") != expected_states:
            errors.append("runtime_states_mismatch")
        if runtime.get("interactions") != expected_interactions:
            errors.append("runtime_interactions_mismatch")
    return _RenderInspection(manifest_sha256, tuple(dict.fromkeys(errors)), dom, runtime)


class _PageSpecBindingError(ValueError):
    pass


class _RenderBindingError(ValueError):
    pass


def _maps(page_spec: PageSpec) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {item.use_case_id: item for item in page_spec.traceability.use_cases},
        {item.component_id: item for item in page_spec.components},
        {item.interaction_id: item for item in page_spec.interactions},
        {item.state_id: item for item in page_spec.states},
    )


def _capability_reason(criterion: AcceptanceCriterion) -> str | None:
    if criterion.source_kind == "use_case":
        return None
    if criterion.source_kind == "validation_signal" and criterion.semantic_kind in _SUPPORTED_RECOVERY_SEMANTICS:
        return None
    if criterion.source_kind == "validation_signal":
        return "validation_semantic_not_supported_in_m1_03a"
    if criterion.source_kind == "constraint":
        return "constraint_semantics_not_supported_in_m1_03a"
    if criterion.source_kind == "target_device":
        return "target_device_semantics_not_supported_in_m1_03a"
    return "requirement_semantics_not_supported_in_m1_03a"


def _runtime_maps(inspection: _RenderInspection) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]] | None:
    if inspection.runtime_data is None:
        return None
    states = inspection.runtime_data.get("states")
    interactions = inspection.runtime_data.get("interactions")
    if not isinstance(states, list) or not isinstance(interactions, list):
        return None
    try:
        state_map = {item["state_id"]: item for item in states if isinstance(item, dict)}
        interaction_map = {item["interaction_id"]: item for item in interactions if isinstance(item, dict)}
    except (KeyError, TypeError):
        return None
    if len(state_map) != len(states) or len(interaction_map) != len(interactions):
        return None
    return state_map, interaction_map


def _component_dom_error(component: Any, inspection: _RenderInspection) -> str | None:
    if inspection.dom is None:
        return "render_dom_unavailable"
    matches = [item for item in inspection.dom.components if item.component_id == component.component_id]
    if len(matches) != 1:
        return f"dom_component_count_mismatch:{component.component_id}"
    item = matches[0]
    expected_renderer_kind = component.component_type if component.component_type in SUPPORTED_COMPONENT_TYPES else "fallback"
    if item.tag != "article" or item.id_attribute != component.component_id:
        return f"dom_component_stable_id_mismatch:{component.component_id}"
    if item.section_id != component.section_id or item.component_type != component.component_type or item.renderer_kind != expected_renderer_kind:
        return f"dom_component_contract_mismatch:{component.component_id}"
    return None


def _section_dom_error(section_id: str, use_case_id: str, inspection: _RenderInspection) -> str | None:
    if inspection.dom is None:
        return "render_dom_unavailable"
    matches = [item for item in inspection.dom.sections if item.section_id == section_id]
    if len(matches) != 1 or matches[0].tag != "section":
        return f"dom_section_count_mismatch:{section_id}"
    if use_case_id not in matches[0].use_case_ids:
        return f"dom_section_use_case_mismatch:{section_id}"
    return None


def _state_runtime_error(state: Any, inspection: _RenderInspection) -> str | None:
    maps = _runtime_maps(inspection)
    if maps is None:
        return "runtime_data_unavailable"
    actual = maps[0].get(state.state_id)
    expected = {"description": state.description, "name": state.name, "state_id": state.state_id, "visible_component_ids": state.visible_component_ids}
    return None if actual == expected else f"runtime_state_mismatch:{state.state_id}"


def _interaction_runtime_error(interaction: Any, inspection: _RenderInspection) -> str | None:
    maps = _runtime_maps(inspection)
    if maps is None:
        return "runtime_data_unavailable"
    actual = maps[1].get(interaction.interaction_id)
    expected = {"action": interaction.action, "interaction_id": interaction.interaction_id, "source_state_id": interaction.source_state_id, "target_state_id": interaction.target_state_id, "trigger_component_id": interaction.trigger_component_id, "user_feedback": interaction.user_feedback}
    return None if actual == expected else f"runtime_interaction_mismatch:{interaction.interaction_id}"


def _interaction_selector(interaction: Any, inspection: _RenderInspection) -> str:
    if inspection.dom is None:
        raise _RenderBindingError("render_dom_unavailable")
    component_id = interaction.trigger_component_id
    if component_id in inspection.dom.interaction_form_values:
        return "form" + _attribute_selector("data-interaction-form", component_id)
    if component_id in inspection.dom.interaction_trigger_values:
        return _attribute_selector("data-interaction-trigger", component_id)
    raise _RenderBindingError(f"dom_interaction_trigger_missing:{interaction.interaction_id}")


def _feedback_target_for_trigger(
    trigger_component_id: str,
    components_by_id: Mapping[str, Any],
    inspection: _RenderInspection,
) -> _FeedbackTarget:
    """Resolve the feedback assertion target without claiming inline validation."""

    if inspection.dom is None:
        raise _RenderBindingError("render_dom_unavailable")
    trigger_matches = [
        item for item in inspection.dom.components
        if item.component_id == trigger_component_id
    ]
    if len(trigger_matches) != 1:
        raise _RenderBindingError(
            f"dom_trigger_component_count_mismatch:{trigger_component_id}"
        )
    trigger_section_id = trigger_matches[0].section_id
    local_targets = [
        item for item in inspection.dom.components
        if item.section_id == trigger_section_id
        and item.component_id in inspection.dom.feedback_component_ids
    ]
    if len(local_targets) > 1:
        raise _RenderBindingError(
            f"dom_feedback_target_ambiguous:{trigger_component_id}"
        )
    if local_targets:
        chosen = local_targets[0]
        component = components_by_id.get(chosen.component_id)
        if component is None or component.component_type != "status_panel":
            raise _RenderBindingError(
                f"dom_feedback_target_contract_mismatch:{chosen.component_id}"
            )
        if error := _component_dom_error(component, inspection):
            raise _RenderBindingError(error)
        return _FeedbackTarget(
            target_id=component.component_id,
            selector=_attribute_selector("data-component-id", component.component_id)
            + " .component-feedback",
            target_ref_key="feedback_component_id",
            target_ref_value=component.component_id,
        )
    if (
        inspection.dom.page_state_region_count != 1
        or inspection.dom.page_state_message_count != 1
    ):
        raise _RenderBindingError("dom_global_feedback_target_missing")
    return _FeedbackTarget(
        target_id="page-state",
        selector="#page-state .state-message",
        target_ref_key="feedback_target_id",
        target_ref_value="page-state.state-message",
    )


def _find_deterministic_path(
    initial_state_id: str,
    target_state_id: str,
    interactions: list[Any],
    *,
    final_interaction_ids: set[str],
) -> list[Any] | None:
    queue: list[tuple[str, list[Any], frozenset[str]]] = [
        (initial_state_id, [], frozenset())
    ]
    while queue:
        state_id, path, used_interaction_ids = queue.pop(0)
        for interaction in interactions:
            if interaction.source_state_id != state_id:
                continue
            if interaction.interaction_id in used_interaction_ids:
                continue
            next_path = [*path, interaction]
            if (
                interaction.target_state_id == target_state_id
                and interaction.interaction_id in final_interaction_ids
            ):
                return next_path
            queue.append(
                (
                    interaction.target_state_id,
                    next_path,
                    frozenset({*used_interaction_ids, interaction.interaction_id}),
                )
            )
    return None


def _is_error_or_recovery_acceptance(check: Any, states_by_id: Mapping[str, Any]) -> bool:
    state = states_by_id.get(check.state_id)
    state_name = state.name.casefold() if state is not None else ""
    check_id = check.check_id.casefold()
    return (
        state_name == "error"
        or "error" in check_id
        or "recovery" in check_id
    )


def _blueprint(action: str, target: str, selector: str, expected: Mapping[str, str], source: str) -> tuple[str, str, str, tuple[tuple[str, str], ...], str]:
    return action, target, selector, _mapping_tuple(expected, "expected_payload"), source


def _use_case_binding(criterion: AcceptanceCriterion, page_spec: PageSpec, inspection: _RenderInspection) -> tuple[dict[str, str], list[tuple[str, str, str, tuple[tuple[str, str], ...], str]]]:
    payload = _mapping_dict(criterion.expected_payload, "expected_payload")
    use_case_id = payload["use_case_id"]
    expected_outcome = payload["expected_outcome"]
    trace_by_id, components_by_id, interactions_by_id, states_by_id = _maps(page_spec)
    trace = trace_by_id.get(use_case_id)
    if trace is None:
        raise _PageSpecBindingError(f"missing_use_case_trace:{use_case_id}")
    use_case_order = [item.use_case_id for item in page_spec.use_cases]
    if use_case_id not in use_case_order:
        raise _PageSpecBindingError(f"missing_use_case_order:{use_case_id}")
    current_index = use_case_order.index(use_case_id)
    allowed_interaction_ids: list[str] = []
    for ordered_use_case_id in use_case_order[: current_index + 1]:
        ordered_trace = trace_by_id.get(ordered_use_case_id)
        if ordered_trace is None:
            raise _PageSpecBindingError(f"missing_use_case_trace:{ordered_use_case_id}")
        for interaction_id in ordered_trace.interaction_ids:
            if interaction_id not in interactions_by_id:
                raise _PageSpecBindingError(
                    f"missing_or_unrelated_trace_interaction:{interaction_id}"
                )
            if interaction_id not in allowed_interaction_ids:
                allowed_interaction_ids.append(interaction_id)
    current_trace_interaction_ids = set(trace.interaction_ids)
    allowed_interactions = [
        interactions_by_id[interaction_id]
        for interaction_id in allowed_interaction_ids
    ]
    checks = [
        item for item in page_spec.acceptance_checks
        if use_case_id in item.use_case_ids
        and item.state_id in states_by_id
        and not _is_error_or_recovery_acceptance(item, states_by_id)
    ]
    if not checks:
        raise _PageSpecBindingError(f"missing_use_case_acceptance_target:{use_case_id}")
    initial_state = next(
        (item for item in page_spec.states if item.name == "initial"),
        page_spec.states[0],
    )
    candidates: list[tuple[Any, list[Any]]] = []
    for check in checks:
        path = _find_deterministic_path(
            initial_state.state_id,
            check.state_id,
            allowed_interactions,
            final_interaction_ids=current_trace_interaction_ids,
        )
        if path is not None:
            candidates.append((check, path))
    if not candidates:
        raise _PageSpecBindingError(f"unreachable_use_case_acceptance_target:{use_case_id}")
    if len(candidates) > 1:
        raise _PageSpecBindingError(f"ambiguous_use_case_acceptance_target:{use_case_id}")
    acceptance, path = candidates[0]
    if not path or not trace.section_ids or not trace.component_ids:
        raise _PageSpecBindingError(f"incomplete_use_case_target:{use_case_id}")
    final_interaction = path[-1]
    for section_id in trace.section_ids:
        if error := _section_dom_error(section_id, use_case_id, inspection):
            raise _RenderBindingError(error)
    for component_id in trace.component_ids:
        component = components_by_id.get(component_id)
        if component is None:
            raise _PageSpecBindingError(f"missing_trace_component:{component_id}")
        if error := _component_dom_error(component, inspection):
            raise _RenderBindingError(error)
    feedback_target = _feedback_target_for_trigger(
        final_interaction.trigger_component_id,
        components_by_id,
        inspection,
    )
    for state in [
        initial_state,
        *(states_by_id[item.target_state_id] for item in path),
        states_by_id[acceptance.state_id],
    ]:
        if error := _state_runtime_error(state, inspection):
            raise _RenderBindingError(error)
    for interaction in path:
        if error := _interaction_runtime_error(interaction, inspection):
            raise _RenderBindingError(error)
        _interaction_selector(interaction, inspection)
    refs: dict[str, str] = {
        "acceptance_check_id": acceptance.check_id,
        "acceptance_state_id": acceptance.state_id,
        "feedback_interaction_id": final_interaction.interaction_id,
        feedback_target.target_ref_key: feedback_target.target_ref_value,
        "semantic_expected_outcome_sha256": sha256(
            expected_outcome.encode("utf-8")
        ).hexdigest(),
        "use_case_id": use_case_id,
    }
    refs.update({f"section_id:{index}": value for index, value in enumerate(sorted(trace.section_ids))})
    refs.update({f"component_id:{index}": value for index, value in enumerate(sorted(trace.component_ids))})
    refs.update({f"interaction_id:{index}": item.interaction_id for index, item in enumerate(path)})
    blueprints = [
        _blueprint(
            "load_page", page_spec.page_id,
            "body" + _attribute_selector("data-page-id", page_spec.page_id),
            {"page_id": page_spec.page_id},
            "renderer.index_html.body",
        )
    ]
    blueprints.extend(
        _blueprint(
            "assert_element_exists", section_id,
            "section" + _attribute_selector("data-section-id", section_id),
            {"stable_id": section_id, "use_case_id": use_case_id},
            "page_spec.traceability.use_cases.section_ids",
        )
        for section_id in sorted(trace.section_ids)
    )
    blueprints.extend(
        _blueprint(
            "assert_element_exists", component_id,
            _attribute_selector("data-component-id", component_id),
            {"stable_id": component_id},
            "page_spec.traceability.use_cases.component_ids",
        )
        for component_id in sorted(trace.component_ids)
    )
    for interaction in path:
        blueprints.append(
            _blueprint(
                "trigger_interaction", interaction.interaction_id,
                _interaction_selector(interaction, inspection),
                {
                    "action": interaction.action,
                    "source_state_id": interaction.source_state_id,
                    "target_state_id": interaction.target_state_id,
                },
                "page_spec.traceability.use_cases.interaction_ids",
            )
        )
        blueprints.append(
            _blueprint(
                "assert_state", interaction.target_state_id,
                "#page-state" + _attribute_selector("data-state-id", interaction.target_state_id),
                {"state_id": interaction.target_state_id},
                "page_spec.states",
            )
        )
    blueprints.append(
        _blueprint(
            "assert_feedback", feedback_target.target_id,
            feedback_target.selector,
            {"feedback": final_interaction.user_feedback},
            "page_spec.interactions.user_feedback",
        )
    )
    return refs, blueprints


def _semantic_words(*values: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", " ".join(values).casefold()))


def _has_any_semantic_word(values: tuple[str, ...], expected: set[str]) -> bool:
    return bool(_semantic_words(*values) & expected)


def _semantic_recovery_path(
    page_spec: PageSpec,
    *,
    scenario_token: str,
) -> tuple[Any, Any, Any, list[Any]] | None:
    initial_state = page_spec.states[0]
    error_state_words = {
        "denied",
        "error",
        "failed",
        "failure",
        "invalid",
        "recovery",
        "retry",
        "validation",
    }
    entry_words = {
        "input": {"error", "failed", "failure", "invalid", "validation"},
        "permission": {
            "access",
            "authorization",
            "denied",
            "forbidden",
            "permission",
            "unauthorized",
        },
    }[scenario_token]
    recovery_words = {
        "continue",
        "correct",
        "corrected",
        "recover",
        "recovered",
        "recovery",
        "resubmit",
        "retry",
        "success",
        "successful",
        "valid",
    }
    error_states = [
        state
        for state in page_spec.states
        if state.state_id != initial_state.state_id
        and _has_any_semantic_word(
            (state.name, state.description),
            error_state_words,
        )
    ]
    candidates: list[tuple[Any, Any, Any, list[Any]]] = []
    for error_state in error_states:
        entries = [
            interaction
            for interaction in page_spec.interactions
            if interaction.source_state_id != error_state.state_id
            and interaction.target_state_id == error_state.state_id
            and _has_any_semantic_word(
                (interaction.action, interaction.user_feedback),
                entry_words,
            )
        ]
        recoveries = [
            interaction
            for interaction in page_spec.interactions
            if interaction.source_state_id == error_state.state_id
            and interaction.target_state_id != error_state.state_id
            and _has_any_semantic_word(
                (interaction.action, interaction.user_feedback),
                recovery_words,
            )
        ]
        for entry in entries:
            entry_path = _find_deterministic_path(
                initial_state.state_id,
                error_state.state_id,
                page_spec.interactions,
                final_interaction_ids={entry.interaction_id},
            )
            if entry_path is None:
                continue
            for recovery in recoveries:
                candidates.append(
                    (error_state, entry, recovery, entry_path)
                )
    if len(candidates) != 1:
        return None
    return candidates[0]


def _recovery_binding(criterion: AcceptanceCriterion, page_spec: PageSpec, inspection: _RenderInspection) -> tuple[dict[str, str], list[tuple[str, str, str, tuple[tuple[str, str], ...], str]]]:
    payload = _mapping_dict(criterion.expected_payload, "expected_payload")
    scenario_contract = {
        "retry_recovery": ("input_error", "input"),
        "permission_recovery": ("auth_access", "permission"),
    }
    expected_source_value, scenario_token = scenario_contract[criterion.semantic_kind]
    if payload.get("source_value") != expected_source_value:
        raise _PageSpecBindingError(
            f"recovery_signal_source_value_mismatch:{criterion.source_id}"
        )
    _, components_by_id, _, states_by_id = _maps(page_spec)
    initial_state = page_spec.states[0]
    error_state = next(
        (item for item in page_spec.states if item.name == "error"),
        None,
    )
    error_suffix = f"-error-{scenario_token}"
    recovery_suffix = f"-recovery-{scenario_token}"
    legacy_entries = sorted(
        (
            item for item in page_spec.interactions
            if error_state is not None
            if item.source_state_id == initial_state.state_id
            and item.target_state_id == error_state.state_id
            and item.interaction_id.endswith(error_suffix)
            and item.trigger_component_id.endswith(error_suffix)
        ),
        key=lambda item: item.interaction_id,
    )
    legacy_recoveries = sorted(
        (
            item for item in page_spec.interactions
            if error_state is not None
            if item.source_state_id == error_state.state_id
            and item.target_state_id != error_state.state_id
            and item.interaction_id.endswith(recovery_suffix)
            and item.trigger_component_id.endswith(recovery_suffix)
        ),
        key=lambda item: item.interaction_id,
    )
    if len(legacy_entries) == 1 and len(legacy_recoveries) == 1:
        entry = legacy_entries[0]
        recovery = legacy_recoveries[0]
        entry_path = [entry]
    else:
        semantic_path = _semantic_recovery_path(
            page_spec,
            scenario_token=scenario_token,
        )
        if semantic_path is None:
            if error_state is None:
                raise _PageSpecBindingError("missing_initial_or_error_state")
            if not legacy_entries:
                raise _PageSpecBindingError(
                    f"missing_recovery_fixture_error_entry:{scenario_token}"
                )
            raise _PageSpecBindingError(
                f"missing_recovery_fixture_recovery:{scenario_token}"
            )
        error_state, entry, recovery, entry_path = semantic_path
    target_state = states_by_id.get(recovery.target_state_id)
    error_trigger = components_by_id.get(entry.trigger_component_id)
    recovery_trigger = components_by_id.get(recovery.trigger_component_id)
    if target_state is None or error_trigger is None or recovery_trigger is None:
        raise _PageSpecBindingError("missing_recovery_target_or_trigger")
    entry_source_state = states_by_id.get(entry.source_state_id)
    if entry_source_state is None:
        raise _PageSpecBindingError("missing_recovery_entry_source_state")
    if error_trigger.component_id not in entry_source_state.visible_component_ids:
        raise _PageSpecBindingError(
            "error_entry_trigger_not_visible_in_source_state"
        )
    if recovery_trigger.component_id not in error_state.visible_component_ids:
        raise _PageSpecBindingError("recovery_trigger_not_visible_in_error_state")
    for component in (error_trigger, recovery_trigger):
        if error := _component_dom_error(component, inspection):
            raise _RenderBindingError(error)
    entry_feedback = _feedback_target_for_trigger(
        error_trigger.component_id, components_by_id, inspection
    )
    recovery_feedback = _feedback_target_for_trigger(
        recovery_trigger.component_id, components_by_id, inspection
    )
    runtime_states = [
        initial_state,
        *(states_by_id[item.target_state_id] for item in entry_path),
        target_state,
    ]
    seen_state_ids: set[str] = set()
    for state in runtime_states:
        if state.state_id in seen_state_ids:
            continue
        seen_state_ids.add(state.state_id)
        if error := _state_runtime_error(state, inspection):
            raise _RenderBindingError(error)
    for interaction in (*entry_path, recovery):
        if error := _interaction_runtime_error(interaction, inspection):
            raise _RenderBindingError(error)
        _interaction_selector(interaction, inspection)
    refs = {
        "entry_feedback_component_id": entry_feedback.component_id,
        "error_entry_interaction_id": entry.interaction_id,
        "error_state_id": error_state.state_id,
        "recovery_feedback_component_id": recovery_feedback.component_id,
        "recovery_interaction_id": recovery.interaction_id,
        "recovery_scenario_token": scenario_token,
        "recovery_target_state_id": target_state.state_id,
        "validation_signal_id": criterion.source_id,
    }
    entry_feedback_selector = entry_feedback.selector
    recovery_feedback_selector = recovery_feedback.selector
    blueprints = [
        _blueprint("load_page", page_spec.page_id, "body" + _attribute_selector("data-page-id", page_spec.page_id), {"page_id": page_spec.page_id}, "renderer.index_html.body"),
    ]
    for interaction in entry_path:
        trigger = components_by_id[interaction.trigger_component_id]
        blueprints.extend(
            (
                _blueprint(
                    "assert_element_exists",
                    trigger.component_id,
                    _attribute_selector(
                        "data-component-id",
                        trigger.component_id,
                    ),
                    {"stable_id": trigger.component_id},
                    "page_spec.interactions.recovery_entry_path.trigger_component_id",
                ),
                _blueprint(
                    "trigger_interaction",
                    interaction.interaction_id,
                    _interaction_selector(interaction, inspection),
                    {
                        "action": interaction.action,
                        "source_state_id": interaction.source_state_id,
                        "target_state_id": interaction.target_state_id,
                    },
                    "page_spec.interactions.recovery_entry_path",
                ),
                _blueprint(
                    "assert_state",
                    interaction.target_state_id,
                    "#page-state"
                    + _attribute_selector(
                        "data-state-id",
                        interaction.target_state_id,
                    ),
                    {"state_id": interaction.target_state_id},
                    "page_spec.states.recovery_entry_path",
                ),
            )
        )
    blueprints.extend(
        (
            _blueprint("assert_feedback", entry_feedback.target_id, entry_feedback_selector, {"feedback": entry.user_feedback}, "page_spec.interactions.error_entry.user_feedback"),
            _blueprint("assert_element_exists", recovery_trigger.component_id, _attribute_selector("data-component-id", recovery_trigger.component_id), {"stable_id": recovery_trigger.component_id}, "page_spec.interactions.recovery.trigger_component_id"),
            _blueprint("trigger_interaction", recovery.interaction_id, _interaction_selector(recovery, inspection), {"action": recovery.action, "source_state_id": recovery.source_state_id, "target_state_id": recovery.target_state_id}, "page_spec.interactions.recovery"),
            _blueprint("assert_state", target_state.state_id, "#page-state" + _attribute_selector("data-state-id", target_state.state_id), {"state_id": target_state.state_id}, "page_spec.states.recovery_target"),
            _blueprint("assert_feedback", recovery_feedback.target_id, recovery_feedback_selector, {"feedback": recovery.user_feedback}, "page_spec.interactions.recovery.user_feedback"),
        )
    )
    return refs, blueprints


def _terminal(
    criterion: AcceptanceCriterion, view_sha: str, plan_sha: str, spec_sha: str, manifest_sha: str,
    status: str, stage: str, reason: str, refs: Mapping[str, str] | None = None,
) -> CriterionBinding:
    target_refs = _mapping_tuple(refs or {"criterion_source_id": criterion.source_id}, "target_refs")
    return CriterionBinding(
        binding_id=_binding_identifier(view_sha256=view_sha, plan_sha256=plan_sha, page_spec_sha256=spec_sha, manifest_sha256=manifest_sha, criterion_id=criterion.criterion_id, disposition="terminal", terminal_status=status, terminal_stage=stage, reason_code=reason, target_refs=target_refs),
        criterion_id=criterion.criterion_id, disposition="terminal", target_refs=target_refs, step_ids=(), terminal_status=status, terminal_stage=stage, reason_code=reason,
    )


def _bound(
    criterion: AcceptanceCriterion, view_sha: str, plan_sha: str, spec_sha: str, manifest_sha: str,
    refs: Mapping[str, str], blueprints: list[tuple[str, str, str, tuple[tuple[str, str], ...], str]], start: int,
) -> tuple[CriterionBinding, tuple[ExecutableStep, ...]]:
    target_refs = _mapping_tuple(refs, "target_refs")
    binding_id = _binding_identifier(view_sha256=view_sha, plan_sha256=plan_sha, page_spec_sha256=spec_sha, manifest_sha256=manifest_sha, criterion_id=criterion.criterion_id, disposition="bound", terminal_status=None, terminal_stage=None, reason_code=None, target_refs=target_refs)
    steps = tuple(ExecutableStep(
        step_id=_step_identifier(binding_id=binding_id, criterion_id=criterion.criterion_id, ordinal=start + offset, action_kind=action, target_id=target, selector=selector, expected_payload=payload, source=source),
        binding_id=binding_id, criterion_id=criterion.criterion_id, ordinal=start + offset, action_kind=action, target_id=target, selector=selector, expected_payload=payload, source=source,
    ) for offset, (action, target, selector, payload, source) in enumerate(blueprints))
    return CriterionBinding(binding_id, criterion.criterion_id, "bound", target_refs, tuple(item.step_id for item in steps)), steps


def _build_acceptance_binding(requirement_view: RequirementView, acceptance_plan: AcceptancePlan, page_spec: PageSpec, render_result: RenderResult) -> AcceptanceBindingPlan:
    if not isinstance(requirement_view, RequirementView):
        raise TypeError("requirement_view must be a RequirementView")
    if not isinstance(acceptance_plan, AcceptancePlan):
        raise TypeError("acceptance_plan must be an AcceptancePlan")
    if not isinstance(page_spec, PageSpec):
        raise TypeError("page_spec must be a PageSpec")
    requirement_view.validate()
    acceptance_plan.validate_against(requirement_view)
    page_spec.validate()
    view_sha = requirement_view.sha256()
    plan_sha = acceptance_plan.sha256()
    spec_sha = sha256(_canonical_json_bytes(page_spec.to_dict())).hexdigest()
    inspection = _inspect_render(page_spec, render_result)
    bindings: list[CriterionBinding] = []
    steps: list[ExecutableStep] = []
    for criterion in acceptance_plan.criteria:
        if reason := _capability_reason(criterion):
            bindings.append(_terminal(criterion, view_sha, plan_sha, spec_sha, inspection.observed_manifest_sha256, "not_supported", "capability_boundary", reason))
            continue
        if inspection.errors:
            bindings.append(_terminal(criterion, view_sha, plan_sha, spec_sha, inspection.observed_manifest_sha256, "fail", "render_binding", inspection.errors[0]))
            continue
        try:
            refs, blueprints = _use_case_binding(criterion, page_spec, inspection) if criterion.source_kind == "use_case" else _recovery_binding(criterion, page_spec, inspection)
        except _PageSpecBindingError as error:
            bindings.append(_terminal(criterion, view_sha, plan_sha, spec_sha, inspection.observed_manifest_sha256, "fail", "page_spec_binding", str(error)))
        except _RenderBindingError as error:
            bindings.append(_terminal(criterion, view_sha, plan_sha, spec_sha, inspection.observed_manifest_sha256, "fail", "render_binding", str(error)))
        else:
            binding, generated = _bound(criterion, view_sha, plan_sha, spec_sha, inspection.observed_manifest_sha256, refs, blueprints, len(steps))
            bindings.append(binding)
            steps.extend(generated)
    result = AcceptanceBindingPlan(
        schema_version=ACCEPTANCE_BINDING_SCHEMA_VERSION,
        step_plan_schema_version=EXECUTABLE_STEP_PLAN_SCHEMA_VERSION,
        source_requirement_view_schema_version=requirement_view.schema_version,
        source_requirement_view_sha256=view_sha,
        source_acceptance_plan_schema_version=acceptance_plan.schema_version,
        source_acceptance_plan_sha256=plan_sha,
        source_page_spec_schema_version=page_spec.schema_version,
        source_page_spec_sha256=spec_sha,
        page_id=page_spec.page_id,
        observed_render_manifest_sha256=inspection.observed_manifest_sha256,
        bindings=tuple(bindings),
        steps=tuple(steps),
    )
    result.validate()
    return result


def compile_acceptance_binding(requirement_view: RequirementView, acceptance_plan: AcceptancePlan, page_spec: PageSpec, render_result: RenderResult) -> AcceptanceBindingPlan:
    """Compile immutable PageSpec/DOM bindings and static executable steps.

    Supported target omissions become fail/page_spec_binding; manifest, DOM, runtime,
    or stable-ID inconsistencies become fail/render_binding. M1-03a never emits
    runtime pass or unknown: browser execution is deferred to M1-03b.
    """
    return _build_acceptance_binding(requirement_view, acceptance_plan, page_spec, render_result)
