from __future__ import annotations

from datetime import date

import pytest

from filing_agent.corpus.html_parse import (
    FALLBACK_PAGE_WORDS,
    InlineFact,
    html_statement,
    merge_cells,
    parse_html,
    parse_html_filing,
)
from tests.html_fixtures import FILING


def test_merge_cells_rejoins_currency_signs_parentheses_and_percent() -> None:
    assert merge_cells(["Net sales", "$", "186,100", "$", "175,750"]) == [
        "Net sales",
        "$186,100",
        "$175,750",
    ]
    assert merge_cells(["Interest", "(", "92", ")"]) == ["Interest", "(92)"]
    assert merge_cells(["Rate", "12.5", "%"]) == ["Rate", "12.5%"]
    assert merge_cells(["Loss", "$", "(", "5", ")"]) == ["Loss", "$(5)"]
    assert merge_cells([")", "x"]) == [")", "x"]  # nothing to attach to


def test_pages_follow_page_breaks_and_tables_become_rows() -> None:
    filing = parse_html(FILING.encode())
    assert len(filing.pages) == 3
    assert filing.pages[0] == "QUARTERLY REPORT\nWalmart Inc."
    assert filing.pages[1].splitlines() == [
        "Condensed Consolidated Statements of Income",
        "(Amounts in millions, except per share data) | 2026",
        "Net sales | $186,100",
        "Interest income | (92)",
        "Gross margin | 24.5%",
        "Other | —",
    ]
    assert filing.pages[2] == "Net sales were $186.1 billion."
    # the hidden inline-XBRL header, <title> and <style> never reach the text
    assert all("2027" not in page and "margin: 0" not in page for page in filing.pages)


def test_inline_pieces_join_like_a_browser_renders_them() -> None:
    html = (
        b"<html><body><p><span>CONDENSED CONSOLIDA</span><span>TED BALANCE SHEETS</span></p>"
        b"<p>Revenue rose <b>12</b>% to\n  $4.1 billion</p></body></html>"
    )
    assert parse_html(html).pages == [
        "CONDENSED CONSOLIDATED BALANCE SHEETS\nRevenue rose 12% to $4.1 billion"
    ]


def test_fixed_layout_page_boxes_are_pages() -> None:
    box = '<div style="position:relative; height: 792pt"><div>{}</div></div>'
    body = box.format("page one") + box.format("page two")
    assert parse_html(f"<html><body>{body}</body></html>".encode()).pages == [
        "page one",
        "page two",
    ]


def test_html_statement_titles_beyond_the_pdf_forms() -> None:
    assert html_statement("PART I\nItem 1\nINCOME STATEMENTS\n(In millions)") == "income_statement"
    assert html_statement("CASH FLOWS STATEMENTS") == "cash_flow_statement"
    assert html_statement("COMPREHENSIVE INCOME STATEMENTS") == "comprehensive_income"
    assert html_statement("STOCKHOLDERS' EQUITY STATEMENTS") == "equity_statement"
    jnj = "Johnson & Johnson and subsidiaries consolidated statements of earnings"
    assert html_statement(f"Table of Contents\n{jnj}") == "income_statement"
    note = (
        "Recent Accounting Guidance\nIncome Statement – Disaggregation of Income Statement Expenses"
    )
    assert html_statement(note) is None


def test_inline_facts_carry_page_value_and_context() -> None:
    filing = parse_html(FILING.encode())
    by_concept = {(f.concept, f.page): f for f in filing.facts}
    revenue = by_concept[("us-gaap:Revenues", 1)]
    assert revenue.value() == 186_100_000_000
    assert revenue.decimals == "-6" and revenue.unit == "usd"
    assert by_concept[("us-gaap:Revenues", 2)].value() == pytest.approx(186_100_000_000)
    assert by_concept[("us-gaap:InvestmentIncomeInterest", 1)].value() == -92_000_000
    assert by_concept[("x:Margin", 1)].value() == pytest.approx(0.245)
    assert by_concept[("x:Other", 1)].value() == 0

    duration = filing.contexts["c-1"]
    assert (duration.start, duration.end, duration.dimensions) == (
        date(2026, 5, 1),
        date(2026, 7, 31),
        (),
    )
    instant = filing.contexts["c-2"]
    assert instant.start is None and instant.end == date(2026, 7, 31)
    assert instant.dimensions == (
        ("us-gaap:StatementBusinessSegmentsAxis", "wmt:WalmartUSSegmentMember"),
    )
    assert filing.dei["DocumentFiscalYearFocus"] == "2027"
    assert filing.dei["DocumentFiscalPeriodFocus"] == "Q2"


def test_unreadable_figures_have_no_value() -> None:
    fact = InlineFact("x:Count", "c-1", None, None, 0, False, "ixt-sec:numwordsen", "two", 0)
    assert fact.value() is None


def test_parsed_doc_tags_statement_and_units_per_cell() -> None:
    doc = parse_html_filing(FILING.encode(), "WALMART_2027Q2_10Q")
    assert doc.n_pages == 3 and doc.source_size == len(FILING.encode())
    statement = doc.pages[1]
    assert statement.statement == "income_statement"
    assert statement.units == "millions"
    assert statement.units_line == "(Amounts in millions, except per share data)"
    assert {p.extractor for p in doc.pages} == {"html"}
    assert doc.parser_version.startswith("html-")


def test_filings_without_page_breaks_are_cut_by_length() -> None:
    paragraph = " ".join(["word"] * 100)
    paragraphs = [f"<p>{paragraph}</p>" for _ in range(30)]
    paragraphs[20] = (
        f'<p>{paragraph} <ix:nonFraction name="x:A" contextRef="c" scale="0">7</ix:nonFraction></p>'
    )
    filing = parse_html(f"<html><body>{''.join(paragraphs)}</body></html>".encode())
    per_page = -(-FALLBACK_PAGE_WORDS // 100)  # 7 paragraphs reach 700 words
    assert len(filing.pages) == -(-30 // per_page)
    assert [f.page for f in filing.facts] == [20 // per_page]
