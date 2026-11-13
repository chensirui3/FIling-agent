"""Page-bounded chunks for search, each with a contextual header.

Chunks never cross a page, so every hit maps to exactly one (filing, page): the unit FinanceBench
labels its evidence with. The header names the company, filing, period, statement and the page's
units line, because a window cut from the middle of a table otherwise loses its "(in millions)"
line and its statement title. Indexes can be built with or without the header, which lets the
retrieval eval measure what it is worth.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from filing_agent.corpus.parse import ParsedDoc, ParsedPage

MAX_WORDS = 200
OVERLAP_WORDS = 30

DOC_TYPE_LABELS = {
    "10k": "10-K",
    "10q": "10-Q",
    "8k": "8-K",
    "earnings": "earnings release",
    "10k_annualreport": "annual report",
}
STATEMENT_LABELS = {
    "balance_sheet": "balance sheet",
    "income_statement": "income statement",
    "comprehensive_income": "statement of comprehensive income",
    "cash_flow_statement": "cash flow statement",
    "equity_statement": "statement of equity",
}


class DocInfo(BaseModel):
    """The filing metadata search can filter on."""

    doc_name: str
    company: str
    doc_type: str
    doc_period: int


class Chunk(BaseModel):
    chunk_id: int
    doc_name: str
    page: int
    part: int
    text: str
    header: str

    def indexed_text(self, with_header: bool) -> str:
        return f"{self.header}\n{self.text}" if with_header and self.header else self.text


def page_header(info: DocInfo, page: ParsedPage) -> str:
    doc_label = DOC_TYPE_LABELS.get(info.doc_type, info.doc_type)
    parts = [info.company, f"{doc_label} {info.doc_period}", f"page {page.page}"]
    if page.statement:
        parts.append(STATEMENT_LABELS[page.statement])
    if page.units_line:
        parts.append(page.units_line)
    return " | ".join(parts)


def _split_long_line(line: str, max_words: int) -> list[str]:
    words = line.split()
    if len(words) <= max_words:
        return [line]
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]


def split_page(
    text: str, max_words: int = MAX_WORDS, overlap_words: int = OVERLAP_WORDS
) -> list[str]:
    """Pack whole lines into windows of at most `max_words`.

    Each new window starts with up to `overlap_words` of the previous window's trailing lines, so
    a table row or sentence that straddles a boundary is searchable from both sides.
    """
    lines = [
        piece
        for raw in text.splitlines()
        if raw.strip()
        for piece in _split_long_line(raw.strip(), max_words)
    ]
    windows: list[str] = []
    current: list[str] = []
    count = 0
    for line in lines:
        n_words = len(line.split())
        if current and count + n_words > max_words:
            windows.append("\n".join(current))
            carried: list[str] = []
            carried_words = 0
            for previous in reversed(current):
                w = len(previous.split())
                if carried_words + w > overlap_words:
                    break
                carried.insert(0, previous)
                carried_words += w
            current, count = carried, carried_words
        current.append(line)
        count += n_words
    if current:
        windows.append("\n".join(current))
    return windows


def chunk_documents(
    docs: Iterable[tuple[DocInfo, ParsedDoc]],
    *,
    max_words: int = MAX_WORDS,
    overlap_words: int = OVERLAP_WORDS,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for info, parsed in docs:
        for page in parsed.pages:
            header = page_header(info, page)
            for part, window in enumerate(split_page(page.text, max_words, overlap_words)):
                chunks.append(
                    Chunk(
                        chunk_id=len(chunks),
                        doc_name=info.doc_name,
                        page=page.page,
                        part=part,
                        text=window,
                        header=header,
                    )
                )
    return chunks
