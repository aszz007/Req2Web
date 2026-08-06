from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import AgentContextBundle, UseCase  # noqa: E402
from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    GuidanceItem,
    GuidanceSource,
    RetrievalGuidance,
    RetrievalGuidedPageSpecBuilder,
    UseCaseGuidanceTrace,
)


ENTRYPOINT_ISOLATION_SCHEMA = "req2web.entrypoint.isolation.v1"
ENTRYPOINT_CLASSIFICATION = "replay_only"
ACTIVE_DEFAULT_ENTRY = False
ENTRYPOINT_SCOPE_NOTICE = (
    "Replay-only utility over saved context and guidance artifacts; it does not "
    "rerun the current complete flow from a raw requirement or act as the "
    "default entry."
)
ENTRYPOINT_HELP = (
    "Isolation: entrypoint_classification=replay_only; "
    "active_default_entry=false. "
    f"{ENTRYPOINT_SCOPE_NOTICE}"
)


def entrypoint_isolation_metadata() -> dict[str, object]:
    return {
        "schema": ENTRYPOINT_ISOLATION_SCHEMA,
        "event": "entrypoint_isolation",
        "entrypoint": "scripts/run_guided_page_spec.py",
        "entrypoint_classification": ENTRYPOINT_CLASSIFICATION,
        "active_default_entry": ACTIVE_DEFAULT_ENTRY,
        "scope_notice": ENTRYPOINT_SCOPE_NOTICE,
    }


def emit_entrypoint_isolation() -> None:
    sys.stderr.write(
        json.dumps(
            entrypoint_isolation_metadata(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic guided PageSpec v1 from existing Agent context "
            "and req2web.retrieval.guidance.v1 JSON."
        ),
        epilog=ENTRYPOINT_HELP,
    )
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--guidance", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--render-dir",
        type=Path,
        help="Optional static smoke output rendered by the unchanged PageSpec renderer",
    )
    return parser.parse_args()


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


def _load_context(path: Path) -> AgentContextBundle:
    payload = _read_json(path, "Agent context")
    try:
        payload["use_cases"] = [UseCase(**item) for item in payload["use_cases"]]
        return AgentContextBundle(**payload)
    except (KeyError, TypeError) as exc:
        raise ValueError("Agent context JSON does not match req2web.agent.context.v1") from exc


def _guidance_items(values: object) -> list[GuidanceItem]:
    if not isinstance(values, list):
        raise ValueError("RetrievalGuidance item group must be a list")
    items: list[GuidanceItem] = []
    try:
        for value in values:
            source = GuidanceSource(**value["source"])
            item = dict(value)
            item["source"] = source
            items.append(GuidanceItem(**item))
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid RetrievalGuidance item") from exc
    return items


def _load_guidance(path: Path) -> RetrievalGuidance:
    payload = _read_json(path, "RetrievalGuidance")
    try:
        for name in (
            "requirement_guidance",
            "ui_guidance",
            "interaction_guidance",
            "implementation_guidance",
            "validation_guidance",
        ):
            payload[name] = _guidance_items(payload[name])
        payload["use_case_traces"] = [
            UseCaseGuidanceTrace(**item) for item in payload["use_case_traces"]
        ]
        return RetrievalGuidance(**payload)
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "RetrievalGuidance JSON does not match req2web.retrieval.guidance.v1"
        ) from exc


def main() -> int:
    args = parse_args()
    emit_entrypoint_isolation()
    result = RetrievalGuidedPageSpecBuilder().build(
        _load_context(args.context),
        _load_guidance(args.guidance),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    page_spec_json = json.dumps(result.page_spec.to_dict(), ensure_ascii=False, indent=2) + "\n"
    result_json = json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n"
    (args.output_dir / "page_spec.json").write_text(page_spec_json, encoding="utf-8")
    (args.output_dir / "build_result.json").write_text(result_json, encoding="utf-8")
    if args.render_dir:
        DeterministicPageRenderer().render(result.page_spec, args.render_dir)
    print(result_json, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
