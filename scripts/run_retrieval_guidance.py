from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import AgentContextBundle, UseCase  # noqa: E402
from req2web_generation import RetrievalGuidanceBuilder  # noqa: E402


ENTRYPOINT_ISOLATION_SCHEMA = "req2web.entrypoint.isolation.v1"
ENTRYPOINT_CLASSIFICATION = "replay_only"
ACTIVE_DEFAULT_ENTRY = False
ENTRYPOINT_SCOPE_NOTICE = (
    "Replay-only utility over a saved Agent context; it does not rerun the "
    "current complete flow from a raw requirement or act as the default entry."
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
        "entrypoint": "scripts/run_retrieval_guidance.py",
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
        description="Build deterministic req2web.retrieval.guidance.v1 from an Agent context JSON.",
        epilog=ENTRYPOINT_HELP,
    )
    parser.add_argument("--context", required=True, type=Path, help="Existing AgentContextBundle JSON")
    parser.add_argument("--output", type=Path, help="Optional JSON output path; otherwise print to stdout")
    return parser.parse_args()


def _load_context(path: Path) -> AgentContextBundle:
    try:
        # PowerShell commonly writes UTF-8 JSON with a BOM; accepting it keeps
        # the context-only CLI usable without changing the JSON contract.
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Agent context JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Agent context JSON must be an object")
    try:
        payload["use_cases"] = [UseCase(**item) for item in payload["use_cases"]]
        return AgentContextBundle(**payload)
    except (KeyError, TypeError) as exc:
        raise ValueError("Agent context JSON does not match req2web.agent.context.v1") from exc


def main() -> int:
    args = parse_args()
    emit_entrypoint_isolation()
    guidance = RetrievalGuidanceBuilder().build(_load_context(args.context))
    rendered = json.dumps(guidance.to_dict(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
