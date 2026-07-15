from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import AgentContextBundle, UseCase  # noqa: E402
from req2web_generation import (  # noqa: E402
    AcceptanceCheck,
    AffectedPageSpecField,
    ComponentSpec,
    ConstraintSpec,
    DeterministicPageRenderer,
    EvidenceReference,
    GuidanceDecision,
    GuidanceItem,
    GuidanceSource,
    GuidedPageSpecBuildResult,
    InteractionSpec,
    LayoutSpec,
    MinimalConsistencyChecker,
    PageSpec,
    PageSpecBuilder,
    PageState,
    PageUseCase,
    RetrievalGuidance,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
    SectionSpec,
    TraceabilitySpec,
    UseCaseGuidanceTrace,
    UseCaseTrace,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a deterministic Req2Web retrieval influence and role-ablation report.")
    parser.add_argument("--context", required=True, type=Path, help="Existing req2web.agent.context.v1 JSON")
    parser.add_argument("--guidance", required=True, type=Path, help="Existing req2web.retrieval.guidance.v1 JSON")
    parser.add_argument("--output-dir", required=True, type=Path, help="Ignored output directory for all controlled artifacts")
    return parser.parse_args()


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} JSON must be an object")
    return value


def _load_context(path: Path) -> AgentContextBundle:
    payload = _read_json(path, "Agent context")
    try:
        payload["use_cases"] = [UseCase(**item) for item in payload["use_cases"]]
        return AgentContextBundle(**payload)
    except (KeyError, TypeError) as exc:
        raise ValueError("Agent context JSON does not match req2web.agent.context.v1") from exc


def _items(values: object) -> list[GuidanceItem]:
    if not isinstance(values, list):
        raise ValueError("RetrievalGuidance item group must be a list")
    try:
        return [GuidanceItem(**{**item, "source": GuidanceSource(**item["source"])}) for item in values]
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid RetrievalGuidance item") from exc


def _load_guidance(path: Path) -> RetrievalGuidance:
    payload = _read_json(path, "RetrievalGuidance")
    try:
        for name in ("requirement_guidance", "ui_guidance", "interaction_guidance", "implementation_guidance", "validation_guidance"):
            payload[name] = _items(payload[name])
        payload["use_case_traces"] = [UseCaseGuidanceTrace(**item) for item in payload["use_case_traces"]]
        return RetrievalGuidance(**payload)
    except (KeyError, TypeError) as exc:
        raise ValueError("RetrievalGuidance JSON does not match req2web.retrieval.guidance.v1") from exc


def _write_json(path: Path, value: object) -> bytes:
    payload = value.to_dict() if hasattr(value, "to_dict") else value
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return encoded


def main() -> int:
    args = parse_args()
    context = _load_context(args.context)
    guidance = _load_guidance(args.guidance)
    baseline = PageSpecBuilder().build(context)
    builder = RetrievalGuidedPageSpecBuilder()
    full = builder.build(context, guidance)
    ablations = {role: builder.build(context, guidance, disabled_roles=(role,)) for role in ROLE_ORDER}

    _write_json(args.output_dir / "baseline" / "page_spec.json", baseline)
    _write_json(args.output_dir / "full" / "page_spec.json", full.page_spec)
    _write_json(args.output_dir / "full" / "build_result.json", full)
    for role, result in ablations.items():
        _write_json(args.output_dir / "ablations" / role / "page_spec.json", result.page_spec)
        _write_json(args.output_dir / "ablations" / role / "build_result.json", result)
    rendered = DeterministicPageRenderer().render(full.page_spec, args.output_dir / "full" / "rendered")
    consistency = MinimalConsistencyChecker().check(full.page_spec, rendered)
    _write_json(args.output_dir / "full" / "consistency_report.json", consistency)
    report = RetrievalInfluenceChecker().check(context, guidance, baseline, full, ablations, rendered)
    report_bytes = _write_json(args.output_dir / "retrieval_influence_report.json", report)
    digest = hashlib.sha256(report_bytes).hexdigest()
    (args.output_dir / "retrieval_influence_report.sha256").write_text(f"{digest}  retrieval_influence_report.json\n", encoding="utf-8")
    print(json.dumps({"report_id": report.report_id, "passed": report.passed, "sha256": digest}, ensure_ascii=False, sort_keys=True))
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
