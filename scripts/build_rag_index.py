from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from build_vision2web_webpage_rag import build_webpage_rag  # noqa: E402
from req2web_rag.corpus import build_unified_documents, write_documents  # noqa: E402
from req2web_rag.index import build_tfidf_index, write_index  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Req2Web unified corpus and local TF-IDF index.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data/processed/rag",
        help="Directory for documents.jsonl and tfidf_index.json.gz",
    )
    parser.add_argument("--max-features", type=int, default=30_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    processed = ROOT / "data/processed"
    output_dir = args.output_dir.resolve()
    webpage_path, webpage_records = build_webpage_rag(ROOT)
    documents = build_unified_documents(processed)
    document_path = output_dir / "documents.jsonl"
    index_path = output_dir / "tfidf_index.json.gz"
    write_documents(document_path, documents)
    index = build_tfidf_index(documents, max_features=args.max_features)
    write_index(index_path, index)

    summary = {
        "schema_version": "req2web.rag.build.v1",
        "document_count": len(documents),
        "role_counts": dict(sorted(Counter(doc["role"] for doc in documents).items())),
        "dataset_counts": dict(sorted(Counter(doc["dataset"] for doc in documents).items())),
        "vision2web_webpage_source": webpage_path.name,
        "vision2web_webpage_records": len(webpage_records),
        "index_type": index["index_type"],
        "vocabulary_size": len(index["idf"]),
        "documents": document_path.name,
        "index": index_path.name,
    }
    manifest_path = output_dir / "index_manifest.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
