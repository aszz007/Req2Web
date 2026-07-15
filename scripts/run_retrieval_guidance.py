from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent import AgentContextBundle, UseCase  # noqa: E402
from req2web_generation import RetrievalGuidanceBuilder  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build deterministic req2web.retrieval.guidance.v1 from an Agent context JSON."
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
