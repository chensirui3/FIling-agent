"""What goes in the prompt before the question, for baselines that are not tool-only.

The context comes first and the question last, which FinanceBench's authors found works better
for long inputs.
"""

from __future__ import annotations

from collections.abc import Callable

from filing_agent.corpus.index import FilingIndex
from filing_agent.corpus.parse import ParsedDoc
from filing_agent.data.financebench import Question
from filing_agent.eval.experiment import ContextKind


def oracle_evidence(question: Question, load_doc: Callable[[str], ParsedDoc]) -> str:
    """The gold evidence pages (column-aligned text), as the oracle setting provides them."""
    try:
        doc: ParsedDoc | None = load_doc(question.doc_name)
    except FileNotFoundError:
        doc = None
    parts = [f"Pages from {question.doc_name}, the filing this question is about:"]
    for page in sorted({e.page for e in question.evidence}):
        if doc is not None and page < doc.n_pages:
            text = doc.pages[page].layout or doc.pages[page].text
        else:  # fall back to FinanceBench's own extraction of the page
            text = next(e.full_page_text for e in question.evidence if e.page == page)
        parts.append(f"--- {question.doc_name}, page {page} ---\n{text}")
    return "\n\n".join(parts)


def retrieved(question: Question, index: FilingIndex, k: int) -> str:
    """Classic single-shot retrieval: the question as the query, top pages across all filings."""
    hits = index.search(question.question, mode="bm25", top_k=k)
    parts = ["Search results for the question (best-matching pages across all filings):"]
    parts += [f"--- {h.doc_name}, page {h.page} ---\n{h.header}\n{h.text}" for h in hits]
    return "\n\n".join(parts)


def full_filing(question: Question, load_doc: Callable[[str], ParsedDoc]) -> str:
    """The whole filing, page by page (compact text, to keep the token count down)."""
    doc = load_doc(question.doc_name)
    parts = [f"The full text of {question.doc_name}, the filing this question is about:"]
    parts += [f"--- page {p.page} ---\n{p.text}" for p in doc.pages if p.text.strip()]
    return "\n\n".join(parts)


def build_context(
    kind: ContextKind,
    question: Question,
    *,
    load_doc: Callable[[str], ParsedDoc],
    index: FilingIndex | None,
    k: int,
) -> str | None:
    if kind == "none":
        return None
    if kind == "oracle_evidence":
        return oracle_evidence(question, load_doc)
    if kind == "full_filing":
        return full_filing(question, load_doc)
    if index is None:
        raise ValueError("context 'retrieved' needs a search index")
    return retrieved(question, index, k)
