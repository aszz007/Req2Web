from __future__ import annotations

import gzip
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .corpus import ROLE_ORDER
from .schema import validate_document


INDEX_SCHEMA_VERSION = "req2web.rag.tfidf.v1"
ASCII_TOKEN = re.compile(r"[a-z0-9][a-z0-9_+.-]{1,}")
CJK_RUN = re.compile(r"[\u3400-\u9fff]+")

QUERY_EXPANSIONS = {
    "登录": "login sign in authentication auth account",
    "注册": "register sign up account",
    "搜索": "search query results",
    "筛选": "filter sort results",
    "商品": "product commerce shopping store",
    "购物车": "cart basket checkout commerce",
    "结算": "checkout payment order",
    "支付": "payment checkout order",
    "列表": "list feed catalog results",
    "详情": "detail profile content",
    "表单": "form input field submit",
    "移动": "mobile app responsive",
    "响应式": "responsive desktop tablet mobile webpage",
    "点击": "tap click gesture interaction",
    "滑动": "swipe scroll gesture interaction",
    "流程": "flow workflow interaction state steps",
    "异常": "error edge case failure validation",
    "错误": "error invalid failure validation",
    "权限": "permission access auth validation",
    "验收": "acceptance validation test expected behavior",
}


def tokenize(text: str) -> list[str]:
    normalized = text.lower()
    tokens = ASCII_TOKEN.findall(normalized)
    for run in CJK_RUN.findall(normalized):
        tokens.append(run)
        tokens.extend(run[index : index + 2] for index in range(max(0, len(run) - 1)))
        if len(run) <= 8:
            tokens.extend(run)
    return tokens


def expand_query(query: str) -> str:
    additions = [expansion for phrase, expansion in QUERY_EXPANSIONS.items() if phrase in query]
    return " ".join([query, *additions])


def _document_text(document: dict[str, Any]) -> str:
    weighted = [
        document["title"],
        document["title"],
        document["summary"],
        document["summary"],
        document["content"],
        " ".join(document["tags"]),
    ]
    return "\n".join(weighted)


def build_tfidf_index(
    documents: list[dict[str, Any]], max_features: int = 30_000
) -> dict[str, Any]:
    if not documents:
        raise ValueError("cannot build an index without documents")
    term_counts: list[Counter[str]] = []
    document_frequency: Counter[str] = Counter()
    for document in documents:
        validate_document(document)
        counts = Counter(tokenize(_document_text(document)))
        term_counts.append(counts)
        document_frequency.update(counts.keys())

    ranked_terms = sorted(
        document_frequency,
        key=lambda term: (-document_frequency[term], term),
    )[:max_features]
    vocabulary = set(ranked_terms)
    document_count = len(documents)
    idf = {
        term: math.log((document_count + 1) / (document_frequency[term] + 1)) + 1.0
        for term in ranked_terms
    }
    postings: dict[str, list[list[float | int]]] = defaultdict(list)
    for document_index, counts in enumerate(term_counts):
        weights = {
            term: (1.0 + math.log(count)) * idf[term]
            for term, count in counts.items()
            if term in vocabulary
        }
        norm = math.sqrt(sum(weight * weight for weight in weights.values())) or 1.0
        for term, weight in weights.items():
            postings[term].append([document_index, round(weight / norm, 8)])

    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "index_type": "sparse_tfidf_cosine",
        "document_count": document_count,
        "document_ids": [document["doc_id"] for document in documents],
        "idf": {term: round(idf[term], 8) for term in ranked_terms},
        "postings": dict(postings),
    }


def write_index(path: Path, index: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    temporary.write_bytes(gzip.compress(payload, mtime=0))
    temporary.replace(path)


def _load_documents(path: Path) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid document JSON at {path}:{line_number}") from exc
            validate_document(document)
            documents.append(document)
    return documents


class TfidfIndex:
    def __init__(self, documents: list[dict[str, Any]], index: dict[str, Any]) -> None:
        if index.get("schema_version") != INDEX_SCHEMA_VERSION:
            raise ValueError(f"unsupported index schema: {index.get('schema_version')}")
        if len(documents) != index.get("document_count"):
            raise ValueError("document and index counts do not match")
        ids = [document["doc_id"] for document in documents]
        if ids != index.get("document_ids"):
            raise ValueError("document order does not match the index")
        self.documents = documents
        self.index = index

    @classmethod
    def load(cls, directory: Path) -> "TfidfIndex":
        documents = _load_documents(directory / "documents.jsonl")
        with gzip.open(directory / "tfidf_index.json.gz", "rt", encoding="utf-8") as stream:
            index = json.load(stream)
        return cls(documents, index)

    def search(
        self,
        query: str,
        top_k: int = 5,
        roles: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be empty")
        allowed_roles = set(roles) if roles is not None else None
        query_counts = Counter(tokenize(expand_query(query)))
        idf = self.index["idf"]
        query_weights = {
            term: (1.0 + math.log(count)) * idf[term]
            for term, count in query_counts.items()
            if term in idf
        }
        norm = math.sqrt(sum(weight * weight for weight in query_weights.values())) or 1.0
        scores: defaultdict[int, float] = defaultdict(float)
        for term, weight in query_weights.items():
            query_weight = weight / norm
            for document_index, document_weight in self.index["postings"].get(term, []):
                document = self.documents[document_index]
                if allowed_roles is None or document["role"] in allowed_roles:
                    scores[document_index] += query_weight * document_weight

        ranked = sorted(scores.items(), key=lambda item: (-item[1], self.documents[item[0]]["doc_id"]))
        results: list[dict[str, Any]] = []
        for document_index, score in ranked[:top_k]:
            document = self.documents[document_index]
            results.append(
                {
                    "score": round(score, 6),
                    "doc_id": document["doc_id"],
                    "role": document["role"],
                    "dataset": document["dataset"],
                    "subset": document["subset"],
                    "sample_id": document["sample_id"],
                    "title": document["title"],
                    "summary": document["summary"],
                    "references": document["references"],
                }
            )
        return results

    def search_by_role(self, query: str, top_k: int = 2) -> dict[str, list[dict[str, Any]]]:
        return {role: self.search(query, top_k=top_k, roles=[role]) for role in ROLE_ORDER}
