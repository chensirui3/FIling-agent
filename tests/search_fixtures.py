"""A tiny two-company corpus and a deterministic hashing embedder for search tests."""

from __future__ import annotations

import zlib
from collections.abc import Callable, Sequence

import numpy as np

from filing_agent.corpus.chunk import DocInfo
from filing_agent.corpus.index import FilingIndex, IndexSpec, bm25_tokens
from filing_agent.corpus.parse import ParsedDoc, ParsedPage

CORPUS: dict[str, tuple[DocInfo, list[str]]] = {
    "ACME_2018_10K": (
        DocInfo(doc_name="ACME_2018_10K", company="Acme", doc_type="10k", doc_period=2018),
        [
            "Acme annual report fiscal 2018 cover",
            "Consolidated statement of cash flows purchases of property plant and equipment "
            "(1,577) (1,373) proceeds from sale of businesses 846",
            "Consolidated balance sheet total assets 36,500 total liabilities 26,652",
        ],
    ),
    "ACME_2019_10K": (
        DocInfo(doc_name="ACME_2019_10K", company="Acme", doc_type="10k", doc_period=2019),
        [
            "Acme annual report fiscal 2019 cover",
            "Consolidated statement of cash flows purchases of property plant and equipment "
            "(1,699) (1,577) proceeds from sale of businesses 236",
        ],
    ),
    "BETA_2018_10Q": (
        DocInfo(doc_name="BETA_2018_10Q", company="Beta", doc_type="10q", doc_period=2018),
        [
            "Beta quarterly report cash flows purchases of property plant and equipment (88)",
            "Beta segment revenue industrial 4,321 consumer 1,234",
        ],
    ),
}


def parsed(name: str) -> ParsedDoc:
    _, pages = CORPUS[name]
    return ParsedDoc(
        doc_name=name,
        parser_version="test",
        pdfplumber_version="test",
        source_size=1,
        n_pages=len(pages),
        seconds=0.0,
        pages=[ParsedPage(page=i, text=t, layout=t) for i, t in enumerate(pages)],
    )


class HashEmbedder:
    """Bag-of-words hashed into a small dense vector: deterministic and model-free."""

    def __init__(self, dim: int = 128) -> None:
        self.dim = dim

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dim, dtype=np.float32)
        for token in bm25_tokens(text):
            vector[zlib.crc32(token.encode()) % self.dim] += 1.0
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector

    def embed_passages(
        self, texts: Sequence[str], on_progress: Callable[[int], None] | None = None
    ) -> np.ndarray:
        if on_progress:
            on_progress(len(texts))
        return np.vstack([self._vector(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)


def build_index(*, dense: bool = True, headers: bool = True) -> FilingIndex:
    spec = IndexSpec(
        name="test", with_headers=headers, embedding_model="hash-test" if dense else None
    )
    docs = {name: info for name, (info, _) in CORPUS.items()}
    return FilingIndex.build(
        spec, docs, (parsed(n) for n in CORPUS), embedder=HashEmbedder() if dense else None
    )
