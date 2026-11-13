from __future__ import annotations

from pathlib import Path

import pytest

from filing_agent.corpus import parse as parse_module
from filing_agent.corpus.parse import (
    ParsedDoc,
    detect_statement,
    detect_units,
    has_split_numbers,
    load_parsed,
    parse_corpus,
    parse_pdf,
)
from tests.factories import make_pdf

CASH_FLOW_PAGE = [
    "Table of Contents",
    "Acme Corp and Subsidiaries",
    "Consolidated Statement of Cash Flows",
    "(Millions) 2018 2017 2016",
    "Purchases of property, plant and equipment (PP&E) (1,577) (1,373) (1,420)",
]
BALANCE_SHEET_PAGE = [
    "Consolidated Balance Sheets",
    "(In thousands, except share data)",
    "Total assets 36,500 37,987",
]


@pytest.mark.parametrize(
    ("line", "scale"),
    [
        ("(Millions)", "millions"),
        ("(In millions, except per share amounts)", "millions"),
        ("(Dollars in thousands)", "thousands"),
        ("In billions, except per share data", "billions"),
        ("$ in millions", "millions"),
        ("Purchases of PP&E (1,577) (1,373)", None),
        ("We returned $5 billion to shareholders in 2018.", None),
    ],
)
def test_detect_units(line: str, scale: str | None) -> None:
    assert detect_units(f"Header\n{line}\nRow 1 2 3")[0] == scale


@pytest.mark.parametrize(
    ("text", "statement"),
    [
        ("\n".join(CASH_FLOW_PAGE), "cash_flow_statement"),
        ("Consolidated Statements of Comprehensive Income\n(Millions)", "comprehensive_income"),
        ("CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS\n(Unaudited)", "income_statement"),
        ("Consolidated Statement of Financial Position", "balance_sheet"),
        ("Consolidated Statements of Changes in Shareholders' Equity", "equity_statement"),
        (
            "Index to Financial Statements\nConsolidated Statement of Income 52\n"
            "Consolidated Balance Sheet 54\nConsolidated Statement of Cash Flows 55",
            None,
        ),
        ("Notes to Consolidated Financial Statements\nNote 1. Significant policies", None),
    ],
)
def test_detect_statement(text: str, statement: str | None) -> None:
    assert detect_statement(text) == statement


def test_parse_pdf_reads_pages_with_zero_based_numbers_and_tags(tmp_path: Path) -> None:
    pdf = tmp_path / "ACME_2018_10K.pdf"
    pdf.write_bytes(make_pdf([["Acme Annual Report 2018"], CASH_FLOW_PAGE, BALANCE_SHEET_PAGE]))
    doc = parse_pdf(pdf)
    assert doc.doc_name == "ACME_2018_10K"
    assert [p.page for p in doc.pages] == [0, 1, 2]
    cash_flow = doc.pages[1]
    assert "(1,577)" in cash_flow.text
    assert "(1,577)" in cash_flow.layout
    assert cash_flow.units == "millions"
    assert cash_flow.statement == "cash_flow_statement"
    assert doc.pages[2].units == "thousands"
    assert doc.pages[2].statement == "balance_sheet"
    assert doc.pages[0].statement is None
    assert doc.empty_pages == 0


@pytest.mark.parametrize(
    ("text", "split"),
    [
        ("Cost of products sold 8,212 7 ,919 3.7", True),
        ("Sales to customers $ 2 4,020 100.0", True),
        ("Sales to customers $24,020 100.0", False),
        ("Purchases of PP&E (1,577) (1,373) (1,420)", False),
        ("Note 7 ,2019 was a good year", False),
    ],
)
def test_has_split_numbers(text: str, split: bool) -> None:
    assert has_split_numbers(text) is split


SPLIT_PAGE = ["Condensed Consolidated Statement of Earnings", "Sales to customers $ 2 4,020 100.0"]


def test_split_numbers_fall_back_to_pdfium_when_it_reads_them_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "JJ_2023Q2_EARNINGS.pdf"
    pdf.write_bytes(make_pdf([["Cover"], SPLIT_PAGE]))
    clean = "Condensed Consolidated Statement of Earnings\nSales to customers $24,020 100.0"
    monkeypatch.setattr(parse_module, "_pdfium_text", lambda _doc, _index: clean)
    doc = parse_pdf(pdf)
    assert doc.pages[0].extractor == "pdfplumber"
    assert doc.pages[1].extractor == "pypdfium2"
    assert doc.pages[1].text == clean
    assert doc.pages[1].statement == "income_statement"


def test_fallback_is_skipped_when_pdfium_text_is_no_better(tmp_path: Path) -> None:
    pdf = tmp_path / "JJ_2023Q2_EARNINGS.pdf"
    pdf.write_bytes(make_pdf([SPLIT_PAGE]))  # the stray space is real text here
    page = parse_pdf(pdf).pages[0]
    assert page.extractor == "pdfplumber"
    assert "$ 2 4,020" in page.text


def test_files_pdfminer_cannot_open_are_read_with_pdfium(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "DAMAGED_2023_8K.pdf"
    pdf.write_bytes(make_pdf([["Cover"], CASH_FLOW_PAGE]))

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise ValueError("No /Root object! - Is this really a PDF?")

    monkeypatch.setattr(parse_module.pdfplumber, "open", refuse)
    doc = parse_pdf(pdf)
    assert [p.extractor for p in doc.pages] == ["pypdfium2", "pypdfium2"]
    assert "(1,577)" in doc.pages[1].text
    assert doc.pages[1].statement == "cash_flow_statement"
    assert doc.pages[1].units == "millions"


def _write_corpus(tmp_path: Path) -> dict[str, Path]:
    files = {}
    for name, pages in {"A_2020_10K": [CASH_FLOW_PAGE], "B_2021_10K": [BALANCE_SHEET_PAGE]}.items():
        path = tmp_path / f"{name}.pdf"
        path.write_bytes(make_pdf(pages))
        files[name] = path
    return files


def test_parse_corpus_caches_outputs(tmp_path: Path) -> None:
    pdfs = _write_corpus(tmp_path)
    out = tmp_path / "parsed"
    first = parse_corpus(pdfs, out, workers=1)
    second = parse_corpus(pdfs, out, workers=1)
    forced = parse_corpus(pdfs, out, workers=1, force=True)
    assert [o.status for o in first] == ["parsed", "parsed"]
    assert [o.status for o in second] == ["cached", "cached"]
    assert [o.status for o in forced] == ["parsed", "parsed"]
    doc = load_parsed(out, "A_2020_10K")
    assert isinstance(doc, ParsedDoc)
    assert doc.pages[0].units == "millions"


def test_parse_corpus_in_worker_processes(tmp_path: Path) -> None:
    outcomes = parse_corpus(_write_corpus(tmp_path), tmp_path / "parsed", workers=2)
    assert [(o.doc_name, o.status, o.n_pages) for o in outcomes] == [
        ("A_2020_10K", "parsed", 1),
        ("B_2021_10K", "parsed", 1),
    ]


def test_unreadable_pdf_is_reported_not_raised(tmp_path: Path) -> None:
    bad = tmp_path / "BROKEN.pdf"
    bad.write_bytes(b"not a pdf")
    [outcome] = parse_corpus({"BROKEN": bad}, tmp_path / "parsed", workers=1)
    assert outcome.status == "failed"
    assert outcome.error
