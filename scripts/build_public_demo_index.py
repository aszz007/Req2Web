"""Build a separate synthetic portability index, never an evaluation corpus."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from req2web_rag.corpus import ROLE_ORDER, write_documents  # noqa: E402
from req2web_rag.index import build_tfidf_index, write_index  # noqa: E402
from req2web_rag.schema import validate_document  # noqa: E402


def build_demo_index(output_dir: Path) -> dict[str, object]:
    requested = Path(output_dir).absolute()
    if any(part.is_symlink() for part in (requested, *requested.parents)):
        raise ValueError("the demo output path must not contain symbolic links")
    output = requested.resolve()
    frozen = (ROOT / "data/processed/rag").resolve()
    if output == frozen or frozen in output.parents or output in frozen.parents:
        raise ValueError("the frozen retrieval corpus must not be overwritten")
    if output.exists():
        raise ValueError("choose a new demo output directory; existing data is preserved")
    source = ROOT / "fixtures/public_demo/documents.jsonl"
    documents = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line]
    for document in documents:
        validate_document(document)
        if (document["dataset"] != "req2web_synthetic_demo"
                or document["metadata"].get("third_party_material") is not False):
            raise ValueError("only the authored synthetic demo records are permitted")
    if Counter(row["role"] for row in documents) != Counter(ROLE_ORDER):
        raise ValueError("the demo must contain exactly one record per retrieval role")
    index = build_tfidf_index(documents)
    # Claim a new directory first. Never overwrite an existing directory on a
    # concurrent call; partial failures are retained for inspection, not deleted.
    output.mkdir(parents=True, exist_ok=False)
    write_documents(output / "documents.jsonl", documents)
    write_index(output / "tfidf_index.json.gz", index)
    manifest = {
        "schema_version": "req2web.public_demo.index.v1",
        "material_class": "project_authored_synthetic_demo",
        "evaluation_evidence": False,
        "document_count": len(documents),
        "roles": list(ROLE_ORDER),
        "source": "fixtures/public_demo/documents.jsonl",
        "index_type": index["index_type"],
    }
    (output / "index_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/public_demo_index")
    args = parser.parse_args(argv)
    try:
        result = build_demo_index(args.output_dir)
    except (OSError, ValueError) as exc:
        print(f"[REQ2WEB-PUBLIC-DEMO] failed closed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
