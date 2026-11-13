"""Offline retrieval eval: does search rank a gold evidence page in the top k?

The query is the question text. A hit is any page in the question's own filing that holds one of
its evidence passages: the labeled page, or another page in that filing that contains the passage
equally well (repeated tables). Pages from other filings never count, even if they repeat the
figure, which makes this a strict, page-level measure.

Settings narrow the search space step by step, from all filings to the gold filing alone, so the
gaps show how much is lost to picking the wrong company, year, filing or page.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from filing_agent.corpus.index import Embedder, FilingIndex, Filters, Mode, PageHit
from filing_agent.corpus.parse import ParsedDoc
from filing_agent.data.financebench import Document, Question
from filing_agent.data.verify import FOUND_THRESHOLD, TIE_TOLERANCE, containment, tokens

KS = (1, 3, 5, 10, 20)
Setting = Literal["all_filings", "same_company", "company_and_year", "gold_filing"]
SETTINGS: tuple[Setting, ...] = ("all_filings", "same_company", "company_and_year", "gold_filing")
SETTING_LABELS: dict[Setting, str] = {
    "all_filings": "all filings",
    "same_company": "same company",
    "company_and_year": "company + fiscal year",
    "gold_filing": "gold filing only",
}


@dataclass(frozen=True)
class Case:
    question_id: str
    query: str
    doc_name: str
    company: str
    doc_type: str
    period: int
    question_type: str
    evidence_pages: tuple[frozenset[int], ...]  # one set of acceptable pages per evidence item


def acceptable_pages(question: Question, doc: ParsedDoc) -> tuple[frozenset[int], ...]:
    page_tokens = [tokens(p.text) for p in doc.pages]
    sets: list[frozenset[int]] = []
    for evidence in question.evidence:
        passage = tokens(evidence.text) or tokens(evidence.full_page_text)
        labeled = containment(passage, page_tokens[evidence.page])
        pages = {evidence.page}
        for index, page in enumerate(page_tokens):
            score = containment(passage, page)
            if score >= FOUND_THRESHOLD and score >= labeled - TIE_TOLERANCE:
                pages.add(index)
        sets.append(frozenset(pages))
    return tuple(sets)


def build_cases(
    questions: Iterable[Question],
    documents: Mapping[str, Document],
    load_doc: Callable[[str], ParsedDoc],
) -> list[Case]:
    cases: list[Case] = []
    for q in questions:
        doc = documents[q.doc_name]
        cases.append(
            Case(
                question_id=q.id,
                query=q.question,
                doc_name=q.doc_name,
                company=q.company,
                doc_type=doc.doc_type,
                period=doc.doc_period,
                question_type=q.question_type,
                evidence_pages=acceptable_pages(q, load_doc(q.doc_name)),
            )
        )
    return cases


def filters_for(case: Case, setting: Setting) -> Filters:
    if setting == "same_company":
        return Filters(companies=frozenset({case.company}))
    if setting == "company_and_year":
        return Filters(companies=frozenset({case.company}), periods=frozenset({case.period}))
    if setting == "gold_filing":
        return Filters(doc_names=frozenset({case.doc_name}))
    return Filters()


def evidence_ranks(hits: Sequence[PageHit], case: Case) -> list[int | None]:
    """1-based rank of the first hit for each evidence item (None if not retrieved)."""
    ranks: list[int | None] = []
    for pages in case.evidence_pages:
        rank = next(
            (
                i
                for i, h in enumerate(hits, start=1)
                if h.doc_name == case.doc_name and h.page in pages
            ),
            None,
        )
        ranks.append(rank)
    return ranks


class CachingEmbedder:
    """Embeds each distinct query once; the same question is searched in many settings."""

    def __init__(self, inner: Embedder) -> None:
        self.inner = inner
        self._cache: dict[str, np.ndarray] = {}

    def embed_passages(
        self, texts: Sequence[str], on_progress: Callable[[int], None] | None = None
    ) -> np.ndarray:
        return self.inner.embed_passages(texts, on_progress)

    def embed_query(self, text: str) -> np.ndarray:
        if text not in self._cache:
            self._cache[text] = self.inner.embed_query(text)
        return self._cache[text]


def run(
    index: FilingIndex,
    cases: Sequence[Case],
    *,
    settings: Sequence[Setting] = SETTINGS,
    modes: Sequence[Mode] = ("bm25", "dense", "hybrid"),
    max_k: int = max(KS),
    on_search: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """One row per (setting, mode, case) with the rank of each evidence item."""
    rows: list[dict[str, Any]] = []
    for setting in settings:
        for mode in modes:
            for case in cases:
                hits = index.search(
                    case.query, mode=mode, top_k=max_k, filters=filters_for(case, setting)
                )
                rows.append(
                    {
                        "index": index.spec.name,
                        "setting": setting,
                        "mode": mode,
                        "question_id": case.question_id,
                        "question_type": case.question_type,
                        "doc_type": case.doc_type,
                        "ranks": evidence_ranks(hits, case),
                    }
                )
                if on_search:
                    on_search()
    return rows


def aggregate(rows: Sequence[Mapping[str, Any]], ks: Sequence[int] = KS) -> dict[str, float]:
    """hit@k: any evidence page in the top k. full@k: every evidence item found in the top k."""
    if not rows:
        return {}
    out: dict[str, float] = {"n": float(len(rows))}
    for k in ks:
        out[f"hit@{k}"] = float(
            np.mean([any(r is not None and r <= k for r in row["ranks"]) for row in rows])
        )
        out[f"full@{k}"] = float(
            np.mean([all(r is not None and r <= k for r in row["ranks"]) for row in rows])
        )
    first = [min((r for r in row["ranks"] if r is not None), default=None) for row in rows]
    out["mrr"] = float(np.mean([0.0 if r is None else 1.0 / r for r in first]))
    return out


def summarize(
    rows: Sequence[Mapping[str, Any]], groups: Mapping[str, set[str]] | None = None
) -> list[dict[str, Any]]:
    """Metrics per (index, setting, mode), overall and for each named question-id group."""
    keys = sorted(
        {(r["index"], r["setting"], r["mode"]) for r in rows},
        key=lambda k: (k[0], SETTINGS.index(k[1]), k[2]),
    )
    summary: list[dict[str, Any]] = []
    for index_name, setting, mode in keys:
        subset = [
            r for r in rows if (r["index"], r["setting"], r["mode"]) == (index_name, setting, mode)
        ]
        parts: dict[str, Sequence[Mapping[str, Any]]] = {"all": subset}
        for name, ids in (groups or {}).items():
            parts[name] = [r for r in subset if r["question_id"] in ids]
        for part, part_rows in parts.items():
            summary.append(
                {"index": index_name, "setting": setting, "mode": mode, "part": part}
                | aggregate(part_rows)
            )
    return summary
