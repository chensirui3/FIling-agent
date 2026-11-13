"""Search over filing chunks: BM25, dense embeddings, and their reciprocal-rank fusion.

Results are collapsed to one hit per (filing, page), the unit FinanceBench labels evidence with.
Metadata filters (company, filing, filing type, fiscal period) are applied before ranking, so a
filtered search ranks only the allowed filings.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import bm25s
import numpy as np
from pydantic import BaseModel

from filing_agent.corpus.chunk import MAX_WORDS, OVERLAP_WORDS, Chunk, DocInfo, chunk_documents
from filing_agent.corpus.parse import PARSER_VERSION, ParsedDoc

INDEX_VERSION = "1"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
Mode = Literal["bm25", "dense", "hybrid"]
MODES: tuple[Mode, ...] = ("bm25", "dense", "hybrid")
RRF_K = 60
CANDIDATE_CHUNKS = 300  # chunks per mode considered before collapsing to pages

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "might",
        "of",
        "on",
        "or",
        "our",
        "should",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "whether",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)
_TOKEN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
_DIGIT_COMMA = re.compile(r"(?<=\d),(?=\d{3})")


def bm25_tokens(text: str) -> list[str]:
    """Lower-cased words and numbers ("1,577" -> "1577"), minus common stopwords."""
    normalized = _DIGIT_COMMA.sub("", text.lower())
    return [t for t in _TOKEN.findall(normalized) if t not in STOPWORDS]


class Embedder(Protocol):
    def embed_passages(
        self, texts: Sequence[str], on_progress: Callable[[int], None] | None = None
    ) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedder:
    """Local ONNX sentence embeddings via fastembed (CPU-friendly)."""

    def __init__(self, model_name: str, cache_dir: Path, batch_size: int = 64) -> None:
        from fastembed import TextEmbedding  # imported lazily: loading onnxruntime is slow

        self.model_name = model_name
        self.batch_size = batch_size
        self._model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))

    def embed_passages(
        self, texts: Sequence[str], on_progress: Callable[[int], None] | None = None
    ) -> np.ndarray:
        vectors: list[np.ndarray] = []
        for start in range(0, len(texts), 1024):
            block = list(texts[start : start + 1024])
            vectors.extend(self._model.passage_embed(block, batch_size=self.batch_size))
            if on_progress:
                on_progress(len(block))
        return _normalize(np.vstack(vectors).astype(np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        vector = next(iter(self._model.query_embed(text)))
        return _normalize(np.asarray(vector, dtype=np.float32)[None, :])[0]


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)


class IndexSpec(BaseModel):
    name: str
    with_headers: bool = True
    embedding_model: str | None = DEFAULT_EMBEDDING_MODEL
    max_words: int = MAX_WORDS
    overlap_words: int = OVERLAP_WORDS


class PageHit(BaseModel):
    doc_name: str
    page: int
    score: float
    chunk_id: int
    text: str
    header: str


@dataclass(frozen=True)
class Filters:
    companies: frozenset[str] | None = None
    doc_names: frozenset[str] | None = None
    doc_types: frozenset[str] | None = None
    periods: frozenset[int] | None = None

    def allows(self, doc: DocInfo) -> bool:
        return (
            (self.companies is None or doc.company in self.companies)
            and (self.doc_names is None or doc.doc_name in self.doc_names)
            and (self.doc_types is None or doc.doc_type in self.doc_types)
            and (self.periods is None or doc.doc_period in self.periods)
        )

    @property
    def empty(self) -> bool:
        return all(
            v is None for v in (self.companies, self.doc_names, self.doc_types, self.periods)
        )


class FilingIndex:
    def __init__(
        self,
        spec: IndexSpec,
        docs: Mapping[str, DocInfo],
        chunks: list[Chunk],
        bm25: bm25s.BM25,
        embeddings: np.ndarray | None,
        embedder: Embedder | None = None,
    ) -> None:
        self.spec = spec
        self.docs = dict(sorted(docs.items()))
        self.chunks = chunks
        self.bm25 = bm25
        self.embeddings = embeddings
        self.embedder = embedder
        self._doc_names = list(self.docs)
        doc_ids = {name: i for i, name in enumerate(self._doc_names)}
        self._chunk_doc = np.array([doc_ids[c.doc_name] for c in chunks], dtype=np.int32)

    # ---------------------------------------------------------------- build / persist

    @classmethod
    def build(
        cls,
        spec: IndexSpec,
        docs: Mapping[str, DocInfo],
        parsed: Iterable[ParsedDoc],
        *,
        embedder: Embedder | None = None,
        on_chunked: Callable[[int], None] | None = None,
        on_progress: Callable[[int], None] | None = None,
    ) -> FilingIndex:
        pairs = ((docs[p.doc_name], p) for p in parsed if p.doc_name in docs)
        chunks = chunk_documents(pairs, max_words=spec.max_words, overlap_words=spec.overlap_words)
        if on_chunked:
            on_chunked(len(chunks))
        indexed_docs = {name: docs[name] for name in sorted({c.doc_name for c in chunks})}
        texts = [c.indexed_text(spec.with_headers) for c in chunks]
        retriever = bm25s.BM25()
        retriever.index([bm25_tokens(t) for t in texts], show_progress=False)
        embeddings = None
        if spec.embedding_model:
            if embedder is None:
                raise ValueError(f"Index {spec.name!r} needs an embedder for dense search.")
            embeddings = embedder.embed_passages(texts, on_progress=on_progress)
        return cls(spec, indexed_docs, chunks, retriever, embeddings, embedder)

    def save(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "spec": self.spec.model_dump(),
            "index_version": INDEX_VERSION,
            "parser_version": PARSER_VERSION,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "n_docs": len(self.docs),
            "n_chunks": len(self.chunks),
            "embedding_dim": None if self.embeddings is None else int(self.embeddings.shape[1]),
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        (out_dir / "docs.json").write_text(
            json.dumps([d.model_dump() for d in self.docs.values()], indent=1) + "\n",
            encoding="utf-8",
        )
        with (out_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
            for chunk in self.chunks:
                handle.write(chunk.model_dump_json() + "\n")
        self.bm25.save(str(out_dir / "bm25"))
        if self.embeddings is not None:
            # float32 so a loaded index ranks exactly like the one that was built
            np.save(out_dir / "embeddings.npy", self.embeddings.astype(np.float32))

    @classmethod
    def load(cls, out_dir: Path, *, embedder: Embedder | None = None) -> FilingIndex:
        meta_path = out_dir / "meta.json"
        if not meta_path.is_file():
            raise FileNotFoundError(f"{out_dir} has no index. Run `fa index build` first.")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        spec = IndexSpec.model_validate(meta["spec"])
        docs = {
            d["doc_name"]: DocInfo.model_validate(d)
            for d in json.loads((out_dir / "docs.json").read_text(encoding="utf-8"))
        }
        with (out_dir / "chunks.jsonl").open(encoding="utf-8") as handle:
            chunks = [Chunk.model_validate_json(line) for line in handle if line.strip()]
        bm25 = bm25s.BM25.load(str(out_dir / "bm25"))
        emb_path = out_dir / "embeddings.npy"
        embeddings = np.load(emb_path).astype(np.float32) if emb_path.is_file() else None
        return cls(spec, docs, chunks, bm25, embeddings, embedder)

    # ---------------------------------------------------------------- search

    @property
    def has_dense(self) -> bool:
        return self.embeddings is not None and self.embedder is not None

    def _mask(self, filters: Filters) -> np.ndarray | None:
        if filters.empty:
            return None
        allowed = [i for i, name in enumerate(self._doc_names) if filters.allows(self.docs[name])]
        return np.isin(self._chunk_doc, np.array(allowed, dtype=np.int32))

    def _bm25_scores(self, query: str) -> np.ndarray:
        tokens = bm25_tokens(query)
        if not tokens:
            return np.zeros(len(self.chunks), dtype=np.float32)
        return np.asarray(self.bm25.get_scores(tokens), dtype=np.float32)

    def _dense_scores(self, query: str) -> np.ndarray:
        assert self.embeddings is not None and self.embedder is not None
        return self.embeddings @ self.embedder.embed_query(query)

    def _ranked_pages(
        self, scores: np.ndarray, mask: np.ndarray | None, limit: int, *, positive_only: bool
    ) -> list[tuple[int, float]]:
        """Best chunk per page, pages ordered by score (ties broken by chunk id)."""
        scores = scores.astype(np.float64, copy=True)
        if mask is not None:
            scores[~mask] = -np.inf
        if positive_only:
            scores[scores <= 0] = -np.inf
        k = min(CANDIDATE_CHUNKS, len(scores))
        if k == 0:
            return []
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.lexsort((top, -scores[top]))]
        ranked: list[tuple[int, float]] = []
        seen: set[tuple[str, int]] = set()
        for chunk_id in top:
            score = float(scores[chunk_id])
            if not np.isfinite(score):
                break
            chunk = self.chunks[int(chunk_id)]
            key = (chunk.doc_name, chunk.page)
            if key in seen:
                continue
            seen.add(key)
            ranked.append((int(chunk_id), score))
            if len(ranked) >= limit:
                break
        return ranked

    def search(
        self,
        query: str,
        *,
        mode: Mode = "hybrid",
        top_k: int = 8,
        filters: Filters | None = None,
    ) -> list[PageHit]:
        filters = filters or Filters()
        mask = self._mask(filters)
        if mode in ("dense", "hybrid") and not self.has_dense:
            if mode == "dense":
                raise ValueError(f"Index {self.spec.name!r} has no dense embeddings loaded.")
            mode = "bm25"

        if mode == "bm25":
            ranked = self._ranked_pages(self._bm25_scores(query), mask, top_k, positive_only=True)
        elif mode == "dense":
            ranked = self._ranked_pages(self._dense_scores(query), mask, top_k, positive_only=False)
        else:
            rankings = [
                self._ranked_pages(
                    self._bm25_scores(query), mask, CANDIDATE_CHUNKS, positive_only=True
                ),
                self._ranked_pages(
                    self._dense_scores(query), mask, CANDIDATE_CHUNKS, positive_only=False
                ),
            ]
            fused: dict[tuple[str, int], list[float]] = {}  # page -> [score, best chunk id]
            for ranking in rankings:
                for rank, (chunk_id, _) in enumerate(ranking):
                    chunk = self.chunks[chunk_id]
                    entry = fused.setdefault((chunk.doc_name, chunk.page), [0.0, float(chunk_id)])
                    entry[0] += 1.0 / (RRF_K + rank + 1)
            ordered = sorted(fused.items(), key=lambda kv: (-kv[1][0], kv[0]))[:top_k]
            ranked = [(int(entry[1]), entry[0]) for _, entry in ordered]

        hits: list[PageHit] = []
        for chunk_id, score in ranked:
            chunk = self.chunks[chunk_id]
            hits.append(
                PageHit(
                    doc_name=chunk.doc_name,
                    page=chunk.page,
                    score=round(score, 6),
                    chunk_id=chunk_id,
                    text=chunk.text,
                    header=chunk.header,
                )
            )
        return hits
