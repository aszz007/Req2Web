from __future__ import annotations

"""Deterministic, evidence-bounded guidance derived from Agent retrieval results.

This module deliberately works on the compact retrieval result records already
present in ``AgentContextBundle``.  It never opens the RAG corpus or an asset.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Iterable

from req2web_agent import AGENT_BUNDLE_SCHEMA_VERSION, AgentContextBundle
from req2web_rag.corpus import ROLE_ORDER


RETRIEVAL_GUIDANCE_SCHEMA_VERSION = "req2web.retrieval.guidance.v1"


@dataclass
class GuidanceSource:
    role: str
    doc_id: str
    source_fields: list[str]
    extraction_rule: str
    reference_uris: list[str]


@dataclass
class GuidanceItem:
    guidance_id: str
    category: str
    value: str
    source: GuidanceSource


@dataclass
class UseCaseGuidanceTrace:
    use_case_id: str
    guidance_ids: list[str]
    source_doc_ids: list[str]


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _unique(values: Iterable[str], field_name: str) -> list[str]:
    items = list(values)
    if len(items) != len(set(items)):
        raise ValueError(f"{field_name} must be unique")
    return items


@dataclass
class RetrievalGuidance:
    guidance_bundle_id: str
    target_device: str
    task_type: str
    requirement_guidance: list[GuidanceItem]
    ui_guidance: list[GuidanceItem]
    interaction_guidance: list[GuidanceItem]
    implementation_guidance: list[GuidanceItem]
    validation_guidance: list[GuidanceItem]
    use_case_traces: list[UseCaseGuidanceTrace]
    source_context_schema_version: str = AGENT_BUNDLE_SCHEMA_VERSION
    schema_version: str = RETRIEVAL_GUIDANCE_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != RETRIEVAL_GUIDANCE_SCHEMA_VERSION:
            raise ValueError(f"unsupported RetrievalGuidance schema: {self.schema_version}")
        if self.source_context_schema_version != AGENT_BUNDLE_SCHEMA_VERSION:
            raise ValueError("RetrievalGuidance must identify req2web.agent.context.v1")
        for name in ("guidance_bundle_id", "target_device", "task_type"):
            _require_text(getattr(self, name), name)

        groups = {
            "requirement": self.requirement_guidance,
            "ui_reference": self.ui_guidance,
            "interaction_flow": self.interaction_guidance,
            "implementation": self.implementation_guidance,
            "validation": self.validation_guidance,
        }
        all_items: list[GuidanceItem] = []
        for role in ROLE_ORDER:
            items = groups[role]
            if not items:
                raise ValueError(f"RetrievalGuidance requires non-empty {role} guidance")
            all_items.extend(items)
            for item in items:
                _require_text(item.guidance_id, "guidance_item.guidance_id")
                _require_text(item.category, "guidance_item.category")
                _require_text(item.value, "guidance_item.value")
                if item.source.role != role:
                    raise ValueError(f"guidance {item.guidance_id} has the wrong source role")
                _require_text(item.source.doc_id, "guidance_source.doc_id")
                _require_text(item.source.extraction_rule, "guidance_source.extraction_rule")
                if not item.source.source_fields:
                    raise ValueError("guidance_source.source_fields must not be empty")
                for field_name in item.source.source_fields:
                    _require_text(field_name, "guidance_source.source_field")
                _unique(item.source.source_fields, "guidance_source.source_fields")
                for uri in item.source.reference_uris:
                    _require_text(uri, "guidance_source.reference_uri")
                _unique(item.source.reference_uris, "guidance_source.reference_uris")
                if _is_absolute_path(item.value) or any(
                    _is_absolute_path(uri) for uri in item.source.reference_uris
                ):
                    raise ValueError("RetrievalGuidance must not contain absolute paths")

        guidance_ids = _unique(
            (item.guidance_id for item in all_items), "guidance_item IDs"
        )
        trace_ids = _unique(
            (item.use_case_id for item in self.use_case_traces), "use_case trace IDs"
        )
        if not trace_ids:
            raise ValueError("RetrievalGuidance must contain use case traces")
        known_guidance = set(guidance_ids)
        known_docs = {item.source.doc_id for item in all_items}
        covered_guidance: set[str] = set()
        for trace in self.use_case_traces:
            _require_text(trace.use_case_id, "use_case_trace.use_case_id")
            guidance = _unique(trace.guidance_ids, "use_case_trace.guidance_ids")
            documents = _unique(trace.source_doc_ids, "use_case_trace.source_doc_ids")
            if not guidance or not documents:
                raise ValueError("use_case traces must reference guidance and documents")
            missing_guidance = [item for item in guidance if item not in known_guidance]
            if missing_guidance:
                raise ValueError(f"use_case trace contains unknown guidance: {missing_guidance}")
            missing_docs = [item for item in documents if item not in known_docs]
            if missing_docs:
                raise ValueError(f"use_case trace contains unknown documents: {missing_docs}")
            trace_docs = {
                item.source.doc_id for item in all_items if item.guidance_id in guidance
            }
            if not set(documents).issubset(trace_docs):
                raise ValueError("use_case trace documents must be sources of its guidance")
            covered_guidance.update(guidance)
        if covered_guidance != known_guidance:
            raise ValueError("every guidance item must be traced by at least one use case")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


def _is_absolute_path(value: str) -> bool:
    return value.startswith(("/", "\\")) or (
        len(value) > 2 and value[1] == ":" and value[2] in {"/", "\\"}
    )


def _result_text(result: dict[str, Any]) -> str:
    # This is an explicit extraction rule, not a copy of RAG content.  The
    # controlled token map below only emits its fixed vocabulary.
    return " ".join(
        _require_text(result.get(name), f"retrieval result {name}")
        for name in ("title", "summary")
    ).casefold()


def _reference_uris(result: dict[str, Any], allowed_kinds: set[str]) -> list[str]:
    references = result.get("references", [])
    if not isinstance(references, list):
        raise ValueError("retrieval result references must be a list")
    uris: list[str] = []
    for reference in references:
        if not isinstance(reference, dict):
            raise ValueError("retrieval result reference must be an object")
        kind = reference.get("kind")
        uri = reference.get("uri")
        if kind in allowed_kinds:
            uris.append(_require_text(uri, "retrieval result reference uri"))
    return list(dict.fromkeys(uris))


_TOKEN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("search_input", ("搜索", "search")),
    ("filter_control", ("筛选", "filter")),
    ("result_list", ("结果", "result", "列表", "list")),
    ("cart_summary", ("购物车", "cart", "basket")),
    ("checkout_action", ("结算", "checkout", "支付", "payment")),
    ("media_input", ("拍摄", "上传", "照片", "图片", "camera", "photo", "upload")),
    ("analysis_result", ("识别", "分析", "recognition", "analysis")),
    ("location_picker", ("地图", "地址", "位置", "定位", "map", "address", "location")),
    ("metric_summary", ("仪表盘", "看板", "指标", "统计", "dashboard", "metric")),
    ("detail_view", ("详情", "detail")),
    ("empty_state", ("空状态", "无匹配", "empty", "no data")),
    ("permission_recovery", ("权限", "permission", "拒绝")),
    ("retry_recovery", ("重试", "retry", "错误", "error", "失败", "failure")),
    ("loading_completion", ("加载", "loading", "load")),
)


def _tokens(text: str) -> list[str]:
    return [value for value, keywords in _TOKEN_RULES if any(key in text for key in keywords)]


def _item(
    role: str,
    result: dict[str, Any],
    category: str,
    value: str,
    source_fields: list[str],
    extraction_rule: str,
    reference_uris: list[str] = (),
) -> GuidanceItem:
    doc_id = _require_text(result.get("doc_id"), "retrieval result doc_id")
    token = hashlib.sha256(
        f"{role}|{doc_id}|{category}|{value}|{extraction_rule}".encode("utf-8")
    ).hexdigest()[:12]
    return GuidanceItem(
        guidance_id=f"guidance-{role}-{token}",
        category=category,
        value=value,
        source=GuidanceSource(
            role=role,
            doc_id=doc_id,
            source_fields=source_fields,
            extraction_rule=extraction_rule,
            reference_uris=list(reference_uris),
        ),
    )


class RetrievalGuidanceBuilder:
    """Build ``req2web.retrieval.guidance.v1`` from a completed Agent bundle.

    The per-role adapters accept only the compact, already-retrieved result
    fields.  ``title`` and ``summary`` are passed through a finite token map;
    references are selected only by whitelisted reference kinds.  This makes
    source-field attribution auditable and prevents a hidden corpus read.
    """

    def build(self, context: AgentContextBundle) -> RetrievalGuidance:
        if not isinstance(context, AgentContextBundle):
            raise TypeError("RetrievalGuidanceBuilder input must be an AgentContextBundle")
        if context.schema_version != AGENT_BUNDLE_SCHEMA_VERSION:
            raise ValueError(f"unsupported Agent context schema: {context.schema_version}")
        context.validate()
        for field_name in ("target_device", "task_type"):
            _require_text(getattr(context, field_name), f"Agent context {field_name}")

        groups = {
            "requirement": self._requirement(context.retrieval_results["requirement"]),
            "ui_reference": self._ui(context.retrieval_results["ui_reference"]),
            "interaction_flow": self._flow(context.retrieval_results["interaction_flow"]),
            "implementation": self._implementation(context.retrieval_results["implementation"]),
            "validation": self._validation(context.retrieval_results["validation"]),
        }
        all_items = [item for role in ROLE_ORDER for item in groups[role]]
        traces = self._traces(context, all_items)
        payload = {
            "context": context.to_dict(),
            "guidance": [asdict(item) for item in all_items],
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:12]
        guidance = RetrievalGuidance(
            guidance_bundle_id=f"retrieval-guidance-{context.task_type}-{digest}",
            target_device=context.target_device,
            task_type=context.task_type,
            requirement_guidance=groups["requirement"],
            ui_guidance=groups["ui_reference"],
            interaction_guidance=groups["interaction_flow"],
            implementation_guidance=groups["implementation"],
            validation_guidance=groups["validation"],
            use_case_traces=traces,
        )
        guidance.validate()
        return guidance

    def _checked_results(self, role: str, results: object) -> list[dict[str, Any]]:
        if not isinstance(results, list) or not results:
            raise ValueError(f"RetrievalGuidance requires non-empty retrieval results for role: {role}")
        checked: list[dict[str, Any]] = []
        for result in results:
            if not isinstance(result, dict):
                raise ValueError(f"retrieval result for {role} must be an object")
            if result.get("role") != role:
                raise ValueError(f"retrieval result has the wrong role: {role}")
            _require_text(result.get("doc_id"), "retrieval result doc_id")
            _result_text(result)
            checked.append(result)
        doc_ids = [str(item["doc_id"]) for item in checked]
        _unique(doc_ids, f"retrieval result doc_ids for {role}")
        return checked

    def _requirement(self, results: object) -> list[GuidanceItem]:
        items: list[GuidanceItem] = []
        for result in self._checked_results("requirement", results):
            dataset = _require_text(result.get("dataset"), "requirement dataset")
            subset = _require_text(result.get("subset"), "requirement subset")
            items.append(_item("requirement", result, "similar_task_type", f"{dataset}:{subset}", ["dataset", "subset"], "requirement_source_adapter:v1:dataset_subset"))
            reference_kinds = self._reference_kinds(result, {"requirement", "workflow", "prototype", "resource_directory"})
            for kind in reference_kinds:
                items.append(_item("requirement", result, "business_boundary_hint", f"reference_kind:{kind}", ["references"], f"requirement_source_adapter:v1:reference_kind:{kind}"))
            for token in _tokens(_result_text(result)):
                items.append(_item("requirement", result, "reusable_constraint", token, ["title", "summary"], f"controlled_token_map:v1:{token}"))
        return items

    def _ui(self, results: object) -> list[GuidanceItem]:
        items: list[GuidanceItem] = []
        for result in self._checked_results("ui_reference", results):
            tokens = _tokens(_result_text(result))
            items.append(_item("ui_reference", result, "layout_hint", tokens[0] if tokens else "reference_screen", ["title", "summary"], f"controlled_token_map:v1:{tokens[0] if tokens else 'reference_screen'}"))
            for token in tokens[1:]:
                items.append(_item("ui_reference", result, "component_hint", token, ["title", "summary"], f"controlled_token_map:v1:{token}"))
            for uri in _reference_uris(result, {"screenshot", "semantic_image", "view_hierarchy", "semantic_annotation"}):
                items.append(_item("ui_reference", result, "ui_reference_uri", uri, ["references"], "ui_source_adapter:v1:whitelisted_uri", [uri]))
        return items

    def _flow(self, results: object) -> list[GuidanceItem]:
        items: list[GuidanceItem] = []
        for result in self._checked_results("interaction_flow", results):
            text = _result_text(result)
            if "mixed" in text:
                pattern = "tap_and_swipe"
            elif "swipe" in text or "滑动" in text:
                pattern = "swipe"
            else:
                pattern = "tap"
            items.append(_item("interaction_flow", result, "operation_pattern", pattern, ["title", "summary"], f"flow_source_adapter:v1:{pattern}"))
            step_uris = _reference_uris(result, {"step_screenshot", "step_hierarchy"})
            state_change = "multi_step_transition" if len(step_uris) > 1 else "single_step_transition"
            items.append(_item("interaction_flow", result, "state_change_hint", state_change, ["references"], f"flow_source_adapter:v1:{state_change}", step_uris[:2]))
        return items

    def _implementation(self, results: object) -> list[GuidanceItem]:
        items: list[GuidanceItem] = []
        for result in self._checked_results("implementation", results):
            tokens = _tokens(_result_text(result))
            for token in tokens or ["reference_structure"]:
                items.append(_item("implementation", result, "implementation_structure_hint", token, ["title", "summary"], f"controlled_token_map:v1:{token}"))
            kinds = self._reference_kinds(result, {"html", "target_html", "screenshot", "target_screenshot", "input_sketch", "prototype"})
            if kinds:
                items.append(_item("implementation", result, "resource_constraint", "+".join(kinds), ["references"], "implementation_source_adapter:v1:reference_kinds"))
        return items

    def _validation(self, results: object) -> list[GuidanceItem]:
        items: list[GuidanceItem] = []
        for result in self._checked_results("validation", results):
            tokens = _tokens(_result_text(result))
            items.append(_item("validation", result, "acceptance_condition", "regression_case", ["title", "summary"], "validation_source_adapter:v1:issue_or_case"))
            for token in tokens:
                category = "recovery_hint" if token in {"permission_recovery", "retry_recovery"} else "exception_scenario"
                items.append(_item("validation", result, category, token, ["title", "summary"], f"controlled_token_map:v1:{token}"))
            for uri in _reference_uris(result, {"issue", "pull_request"}):
                items.append(_item("validation", result, "validation_reference_uri", uri, ["references"], "validation_source_adapter:v1:whitelisted_uri", [uri]))
        return items

    @staticmethod
    def _reference_kinds(result: dict[str, Any], allowed: set[str]) -> list[str]:
        references = result.get("references", [])
        if not isinstance(references, list):
            raise ValueError("retrieval result references must be a list")
        kinds = []
        for reference in references:
            if not isinstance(reference, dict):
                raise ValueError("retrieval result reference must be an object")
            kind = reference.get("kind")
            if kind in allowed:
                kinds.append(str(kind))
        return list(dict.fromkeys(kinds))

    @staticmethod
    def _traces(context: AgentContextBundle, items: list[GuidanceItem]) -> list[UseCaseGuidanceTrace]:
        by_role: dict[str, list[GuidanceItem]] = {
            role: [item for item in items if item.source.role == role] for role in ROLE_ORDER
        }
        traces: list[UseCaseGuidanceTrace] = []
        for index, use_case in enumerate(context.use_cases):
            selected = [role_items[index % len(role_items)] for role_items in by_role.values()]
            # Each trace includes the selected guidance plus all related source
            # documents, making use-case evidence navigable without copying text.
            docs = list(dict.fromkeys(item.source.doc_id for item in selected))
            traces.append(UseCaseGuidanceTrace(use_case.use_case_id, [item.guidance_id for item in selected], docs))
        # Add every unassigned item to the first use case so validation can prove
        # the traceability relation is complete while keeping order deterministic.
        assigned = {guidance_id for trace in traces for guidance_id in trace.guidance_ids}
        for item in items:
            if item.guidance_id not in assigned:
                traces[0].guidance_ids.append(item.guidance_id)
                if item.source.doc_id not in traces[0].source_doc_ids:
                    traces[0].source_doc_ids.append(item.source.doc_id)
        return traces
