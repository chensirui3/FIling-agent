from __future__ import annotations

from itertools import pairwise

from filing_agent.corpus.chunk import DocInfo, chunk_documents, page_header, split_page
from filing_agent.corpus.parse import ParsedDoc, ParsedPage

INFO = DocInfo(doc_name="ACME_2018_10K", company="Acme", doc_type="10k", doc_period=2018)


def _doc(pages: list[str]) -> ParsedDoc:
    return ParsedDoc(
        doc_name=INFO.doc_name,
        parser_version="test",
        pdfplumber_version="test",
        source_size=1,
        n_pages=len(pages),
        seconds=0.0,
        pages=[
            ParsedPage(
                page=i,
                text=t,
                layout=t,
                statement="cash_flow_statement" if i else None,
                units="millions" if i else None,
                units_line="(Millions)" if i else None,
            )
            for i, t in enumerate(pages)
        ],
    )


def test_short_page_is_one_window() -> None:
    assert split_page("Line one here\n\nLine two here") == ["Line one here\nLine two here"]


def test_long_page_windows_respect_the_limit_and_overlap() -> None:
    lines = [f"row{i} " + " ".join(f"w{i}_{j}" for j in range(9)) for i in range(50)]  # 500 words
    windows = split_page("\n".join(lines), max_words=200, overlap_words=30)
    assert len(windows) >= 3
    assert all(len(w.split()) <= 200 for w in windows)
    for previous, current in pairwise(windows):
        assert current.splitlines()[0] in previous.splitlines()  # carried-over context
    covered = {line for w in windows for line in w.splitlines()}
    assert covered == set(lines)


def test_overlong_single_line_is_split() -> None:
    windows = split_page(" ".join(f"w{i}" for i in range(450)), max_words=200, overlap_words=0)
    assert [len(w.split()) for w in windows] == [200, 200, 50]


def test_page_header_names_company_filing_period_statement_and_units() -> None:
    page = _doc(["cover", "Purchases of PP&E (1,577)"]).pages[1]
    assert page_header(INFO, page) == (
        "Acme | 10-K 2018 | page 1 | cash flow statement | (Millions)"
    )


def test_chunks_are_page_bounded_and_numbered() -> None:
    chunks = chunk_documents([(INFO, _doc(["Acme annual report", "Cash flows (1,577)", ""]))])
    assert [(c.chunk_id, c.page, c.part) for c in chunks] == [(0, 0, 0), (1, 1, 0)]
    assert chunks[1].indexed_text(with_header=True).startswith("Acme | 10-K 2018 | page 1")
    assert chunks[1].indexed_text(with_header=False) == "Cash flows (1,577)"
