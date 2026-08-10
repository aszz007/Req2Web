from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, runtime_checkable

from .index import Bm25Index, TfidfIndex


SearchResult = dict[str, Any]


@runtime_checkable
class Retriever(Protocol):
    """Backend-neutral retrieval contract used by the Agent chain."""

    def search(
        self,
        query: str,
        top_k: int = 5,
        roles: Iterable[str] | None = None,
    ) -> list[SearchResult]: ...

    def search_by_role(
        self, query: str, top_k: int = 2
    ) -> dict[str, list[SearchResult]]: ...


@dataclass(frozen=True)
class RetrieverConfig:
    index_dir: Path
    backend: str = "tfidf"
    options: Mapping[str, Any] = field(default_factory=dict)


class TfidfRetriever:
    """Adapter exposing the existing TF-IDF index through ``Retriever``."""

    def __init__(self, index: TfidfIndex) -> None:
        self._index = index

    @classmethod
    def load(cls, index_dir: Path) -> "TfidfRetriever":
        return cls(TfidfIndex.load(index_dir))

    def search(
        self,
        query: str,
        top_k: int = 5,
        roles: Iterable[str] | None = None,
    ) -> list[SearchResult]:
        return self._index.search(query=query, top_k=top_k, roles=roles)

    def search_by_role(
        self, query: str, top_k: int = 2
    ) -> dict[str, list[SearchResult]]:
        return self._index.search_by_role(query=query, top_k=top_k)


class Bm25Retriever:
    """Deterministic local BM25 adapter over the shared corpus documents."""

    def __init__(self, index: Bm25Index) -> None:
        self._index = index

    @classmethod
    def load(
        cls,
        index_dir: Path,
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> "Bm25Retriever":
        return cls(Bm25Index.load(index_dir, k1=k1, b=b))

    def search(
        self,
        query: str,
        top_k: int = 5,
        roles: Iterable[str] | None = None,
    ) -> list[SearchResult]:
        return self._index.search(query=query, top_k=top_k, roles=roles)

    def search_by_role(
        self,
        query: str,
        top_k: int = 2,
    ) -> dict[str, list[SearchResult]]:
        return self._index.search_by_role(query=query, top_k=top_k)


class RrfRetriever:
    """Reciprocal-rank fusion over the local BM25 and TF-IDF rankings."""

    def __init__(
        self,
        *,
        bm25: Bm25Retriever,
        tfidf: TfidfRetriever,
        rrf_k: int = 60,
        source_depth: int = 20,
    ) -> None:
        if isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or rrf_k < 1:
            raise ValueError("RRF k must be a positive integer")
        if (
            isinstance(source_depth, bool)
            or not isinstance(source_depth, int)
            or source_depth < 1
        ):
            raise ValueError("RRF source depth must be a positive integer")
        self._bm25 = bm25
        self._tfidf = tfidf
        self.rrf_k = rrf_k
        self.source_depth = source_depth

    @classmethod
    def load(
        cls,
        index_dir: Path,
        *,
        rrf_k: int = 60,
        source_depth: int = 20,
    ) -> "RrfRetriever":
        return cls(
            bm25=Bm25Retriever.load(index_dir),
            tfidf=TfidfRetriever.load(index_dir),
            rrf_k=rrf_k,
            source_depth=source_depth,
        )

    @staticmethod
    def _projection_without_score(result: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in result.items() if key != "score"}

    def search(
        self,
        query: str,
        top_k: int = 5,
        roles: Iterable[str] | None = None,
    ) -> list[SearchResult]:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        depth = max(top_k, self.source_depth)
        rankings = (
            self._bm25.search(query=query, top_k=depth, roles=roles),
            self._tfidf.search(query=query, top_k=depth, roles=roles),
        )
        scores: dict[str, float] = {}
        views: dict[str, dict[str, Any]] = {}
        for ranking in rankings:
            for rank, result in enumerate(ranking, start=1):
                doc_id = str(result["doc_id"])
                view = self._projection_without_score(result)
                prior = views.setdefault(doc_id, view)
                if prior != view:
                    raise ValueError(
                        "RRF source rankings disagree on the shared result projection"
                    )
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (
                    self.rrf_k + rank
                )
        ranked_ids = sorted(scores, key=lambda doc_id: (-scores[doc_id], doc_id))
        return [
            {"score": round(scores[doc_id], 6), **views[doc_id]}
            for doc_id in ranked_ids[:top_k]
        ]

    def search_by_role(
        self,
        query: str,
        top_k: int = 2,
    ) -> dict[str, list[SearchResult]]:
        from .corpus import ROLE_ORDER

        return {
            role: self.search(query=query, top_k=top_k, roles=(role,))
            for role in ROLE_ORDER
        }


RetrieverFactory = Callable[[RetrieverConfig], Retriever]


class RetrieverRegistry:
    """Small registry that keeps future retrieval backends out of Agent code."""

    def __init__(self) -> None:
        self._factories: dict[str, RetrieverFactory] = {}

    def register(
        self, name: str, factory: RetrieverFactory, *, replace: bool = False
    ) -> None:
        normalized = name.strip().casefold()
        if not normalized:
            raise ValueError("retriever backend name must not be empty")
        if normalized in self._factories and not replace:
            raise ValueError(f"retriever backend is already registered: {normalized}")
        self._factories[normalized] = factory

    def create(self, config: RetrieverConfig) -> Retriever:
        name = config.backend.strip().casefold()
        try:
            factory = self._factories[name]
        except KeyError as exc:
            available = ", ".join(sorted(self._factories)) or "none"
            raise ValueError(
                f"unregistered retriever backend {config.backend!r}; available: {available}"
            ) from exc
        retriever = factory(config)
        if not isinstance(retriever, Retriever):
            raise TypeError(
                f"retriever backend {name!r} does not implement the Retriever protocol"
            )
        return retriever

    def available_backends(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


def _create_tfidf_retriever(config: RetrieverConfig) -> Retriever:
    return TfidfRetriever.load(config.index_dir)


def _create_bm25_retriever(config: RetrieverConfig) -> Retriever:
    unknown = set(config.options) - {"k1", "b"}
    if unknown:
        raise ValueError(f"unsupported BM25 options: {sorted(unknown)}")
    k1 = config.options.get("k1", 1.5)
    b = config.options.get("b", 0.75)
    if isinstance(k1, bool) or not isinstance(k1, (int, float)):
        raise ValueError("BM25 k1 must be numeric")
    if isinstance(b, bool) or not isinstance(b, (int, float)):
        raise ValueError("BM25 b must be numeric")
    return Bm25Retriever.load(config.index_dir, k1=float(k1), b=float(b))


def _create_rrf_retriever(config: RetrieverConfig) -> Retriever:
    unknown = set(config.options) - {"rrf_k", "source_depth"}
    if unknown:
        raise ValueError(f"unsupported RRF options: {sorted(unknown)}")
    rrf_k = config.options.get("rrf_k", 60)
    source_depth = config.options.get("source_depth", 20)
    return RrfRetriever.load(
        config.index_dir,
        rrf_k=rrf_k,
        source_depth=source_depth,
    )


DEFAULT_RETRIEVER_REGISTRY = RetrieverRegistry()
DEFAULT_RETRIEVER_REGISTRY.register("tfidf", _create_tfidf_retriever)
DEFAULT_RETRIEVER_REGISTRY.register("bm25", _create_bm25_retriever)
DEFAULT_RETRIEVER_REGISTRY.register("rrf", _create_rrf_retriever)


def create_retriever(
    config: RetrieverConfig,
    registry: RetrieverRegistry = DEFAULT_RETRIEVER_REGISTRY,
) -> Retriever:
    return registry.create(config)
