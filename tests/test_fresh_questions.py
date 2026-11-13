from __future__ import annotations

import random
from datetime import date
from pathlib import Path

import pytest

from filing_agent.corpus.html_parse import parse_html
from filing_agent.fresh.benchmark import (
    apply_review,
    company_split,
    load_reviews,
    review_sample,
    review_sheet,
    split_summary,
)
from filing_agent.fresh.filings import FreshFiling
from filing_agent.fresh.questions import (
    Period,
    api_index,
    api_status,
    candidate_drafts,
    clean_label,
    filing_periods,
    numeric_questions,
    possessive,
    rounding_decimals,
    statement_figures,
)

ACCESSION = "0000000001-26-000001"
CONTEXTS: dict[str, tuple[str | None, str]] = {  # id: (start, end)
    "q": ("2026-05-01", "2026-07-31"),
    "pq": ("2025-05-01", "2025-07-31"),
    "ytd": ("2026-02-01", "2026-07-31"),
    "i": (None, "2026-07-31"),
}


def _context(cid: str, start: str | None, end: str) -> str:
    period = (
        f"<xbrli:instant>{end}</xbrli:instant>"
        if start is None
        else f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>"
    )
    return (
        f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="x">1'
        f"</xbrli:identifier></xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>"
    )


def _row(label: str, *cells: tuple[str, str, str]) -> str:
    """cells: (concept, context, printed text)."""
    tds = "".join(
        f'<td>$</td><td><ix:nonFraction name="us-gaap:{concept}" contextRef="{ctx}" '
        f'unitRef="{"usdPerShare" if "PerShare" in concept else "usd"}" decimals="-6" '
        f'scale="{0 if "PerShare" in concept else 6}">{text}</ix:nonFraction></td>'
        for concept, ctx, text in cells
    )
    return f"<tr><td>{label}</td>{tds}</tr>"


def statements_html() -> bytes:
    contexts = "".join(_context(cid, *span) for cid, span in CONTEXTS.items())
    units = (
        '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
        '<xbrli:unit id="usdPerShare"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>'
        "iso4217:USD</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator>"
        "<xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide>"
        "</xbrli:unit>"
    )
    header = (
        f'<div style="display:none"><ix:header><ix:resources>{contexts}{units}'
        "</ix:resources></ix:header></div>"
    )
    brk = '<hr style="page-break-after:always"/>'
    income = (
        "<p>Condensed Consolidated Statements of Income</p><table>"
        "<tr><td>(Amounts in millions, except per share data)</td><td>2026</td></tr>"
        + _row("Total revenues", ("Revenues", "q", "1,000"), ("Revenues", "pq", "800"))
        + _row("Total revenues (six months)", ("Revenues", "ytd", "1,900"))
        + _row("Cost of sales", ("CostOfRevenue", "q", "600"))
        + _row("Operating income", ("OperatingIncomeLoss", "q", "150"))
        + _row("Net income attributable to Acme (1)", ("NetIncomeLoss", "q", "100"))
        + _row("Diluted", ("EarningsPerShareDiluted", "q", "1.25"))
        + "</table>"
    )
    balance = (
        "<p>Condensed Consolidated Balance Sheets</p><table>"
        "<tr><td>(Amounts in millions)</td><td>July 31, 2026</td></tr>"
        + _row("Total current assets", ("AssetsCurrent", "i", "500"))
        + _row("Total current liabilities", ("LiabilitiesCurrent", "i", "400"))
        + _row("Total assets", ("Assets", "i", "2,000"))
        + _row("Total liabilities", ("Liabilities", "i", "1,500"))
        + "</table>"
    )
    cash_flow = (
        "<p>Condensed Consolidated Statements of Cash Flows</p><table>"
        "<tr><td>(Amounts in millions)</td><td>Six months</td></tr>"
        + _row("Net cash provided by operating activities", (
            "NetCashProvidedByUsedInOperatingActivities", "ytd", "300"))
        + _row("Payments for property and equipment", (
            "PaymentsToAcquirePropertyPlantAndEquipment", "ytd", "120"))
        + "</table>"
    )  # fmt: skip
    body = f"<p>Cover</p>{brk}{income}{brk}{balance}{brk}{cash_flow}"
    return f"<html><body>{header}{body}</body></html>".encode()


FILING = FreshFiling(
    doc_name="ACME_2027Q2_10Q",
    company="Acme",
    cik=1,
    form="10-Q",
    accession=ACCESSION,
    filing_date=date(2026, 9, 1),
    report_date=date(2026, 7, 31),
    fiscal_year=2027,
    fiscal_period="Q2",
    url="https://www.sec.gov/x.htm",
    size=1,
    sha256="0" * 64,
)


def _companyfacts(*, revenue_factor: float = 1.0) -> dict:
    """API responses for the filing; `revenue_factor` != 1 makes every revenue figure disagree."""

    def entry(start: str | None, end: str, value: float) -> dict:
        e = {"end": end, "val": value, "accn": ACCESSION}
        return e | ({"start": start} if start else {})

    q, i, ytd = CONTEXTS["q"], CONTEXTS["i"], CONTEXTS["ytd"]
    usd = {
        "Revenues": [
            entry(*q, 1_000e6 * revenue_factor),
            entry(*CONTEXTS["pq"], 800e6 * revenue_factor),
            entry(*ytd, 1_900e6 * revenue_factor),
        ],
        "CostOfRevenue": [entry(*q, 600e6)],
        "OperatingIncomeLoss": [entry(*q, 150e6)],
        "NetIncomeLoss": [entry(*q, 100e6)],
        "AssetsCurrent": [entry(*i, 500e6)],
        "LiabilitiesCurrent": [entry(*i, 400e6)],
        "Assets": [entry(*i, 2_000e6)],
        "Liabilities": [entry(*i, 1_500e6)],
        "NetCashProvidedByUsedInOperatingActivities": [entry(*ytd, 300e6)],
        "PaymentsToAcquirePropertyPlantAndEquipment": [entry(*ytd, 120e6)],
    }
    facts = {name: {"units": {"USD": entries}} for name, entries in usd.items()}
    facts["EarningsPerShareDiluted"] = {"units": {"USD/shares": [entry(*q, 1.25)]}}
    return {"facts": {"us-gaap": facts}}


def _drafts(seed: int = 0) -> dict[str, object]:
    figures = statement_figures(parse_html(statements_html()))
    return {d.key: d for d in candidate_drafts(FILING, figures, random.Random(seed))}


def test_statement_figures_carry_row_label_unit_and_statement() -> None:
    figures = statement_figures(parse_html(statements_html()))
    revenue = next(f for f in figures if f.name == "Revenues" and f.start == date(2026, 5, 1))
    assert (revenue.label, revenue.statement, revenue.unit) == (
        "Total revenues",
        "income_statement",
        "USD",
    )
    assert revenue.value == 1_000e6 and revenue.scaled == 1_000 and revenue.page == 1
    eps = next(f for f in figures if f.name == "EarningsPerShareDiluted")
    assert eps.unit == "USD/shares" and eps.value == 1.25
    net = next(f for f in figures if f.name == "NetIncomeLoss")
    assert net.label == "Net income attributable to Acme"  # footnote marker dropped

    periods = filing_periods(figures, date(2026, 7, 31))
    assert periods["quarter"].start == date(2026, 5, 1)
    assert periods["ytd"].start == date(2026, 2, 1)
    assert periods["instant"].start is None


def test_extraction_questions_name_the_period_and_units() -> None:
    drafts = _drafts()
    revenue = drafts["revenue"]
    assert revenue.answer == "$1000"  # type: ignore[attr-defined]
    assert revenue.question == (  # type: ignore[attr-defined]
        "What was Acme's total revenue in the three months ended July 31, 2026? "
        "Answer in USD millions."
    )
    assert drafts["diluted_eps"].answer == "1.25"  # type: ignore[attr-defined]
    assert drafts["diluted_eps"].question.endswith("Answer in USD per share.")  # type: ignore[attr-defined]
    assert drafts["capex"].question.startswith(  # type: ignore[attr-defined]
        "How much did Acme spend on capital expenditures"
    )
    assert "in the six months ended July 31, 2026" in drafts["capex"].question  # type: ignore[attr-defined]
    assert drafts["total_assets"].question.startswith(  # type: ignore[attr-defined]
        "What were Acme's total assets as of July 31, 2026?"
    )


def test_derived_questions_compute_from_the_printed_figures() -> None:
    drafts = _drafts()
    answers = {key: d.answer for key, d in drafts.items()}  # type: ignore[attr-defined]
    assert answers["operating_margin"] == "15.0%"
    assert answers["net_margin"] == "10.0%"
    assert answers["gross_margin_from_cost"] == "40.0%"
    assert answers["revenue_growth"] == "25.0%"
    assert answers["current_ratio"] == "1.25"
    assert answers["liabilities_to_assets"] == "75.0%"
    assert answers["working_capital"] == "$100"
    assert answers["free_cash_flow"] == "$180"
    question = drafts["operating_margin"].question  # type: ignore[attr-defined]
    assert '"Operating income" divided by "Total revenues"' in question
    assert question.endswith("Answer in percent, rounded to one decimal place.")


def test_numbers_on_a_rounding_boundary_get_one_more_decimal() -> None:
    assert rounding_decimals(24.45, 1) == 2
    assert rounding_decimals(24.448, 1) == 2
    assert rounding_decimals(24.4, 1) == 1
    assert rounding_decimals(1.125, 2) == 3


def test_period_phrases_and_names() -> None:
    def phrase(start: date) -> str:
        return Period("quarter", start, date(2026, 9, 5)).phrase()

    assert phrase(date(2026, 6, 14)) == "in the 12 weeks ended September 5, 2026"  # 84 days
    assert phrase(date(2026, 6, 7)) == "in the three months ended September 5, 2026"  # 13 weeks
    assert phrase(date(2025, 12, 28)) == "in the 36 weeks ended September 5, 2026"
    assert Period("instant", None, date(2026, 9, 5)).phrase() == "as of September 5, 2026"
    assert possessive("Walmart") == "Walmart's"
    assert possessive("General Mills") == "General Mills'"
    assert possessive("American Express") == "American Express's"
    assert possessive("McDonald's") == "McDonald's"
    assert clean_label("Sales to customers (Note 9)") == "Sales to customers"
    assert clean_label("Total revenues:") == "Total revenues"


def test_api_cross_check() -> None:
    figures = statement_figures(parse_html(statements_html()))
    revenue = [f for f in figures if f.name == "Revenues" and f.start == date(2026, 5, 1)]
    assert api_status(revenue, api_index(_companyfacts(), ACCESSION)) == "verified"
    wrong = api_index(_companyfacts(revenue_factor=0.999), ACCESSION)
    assert api_status(revenue, wrong) == "mismatch"
    assert api_status(revenue, api_index(_companyfacts(), "other-accession")) == "not_in_api"
    assert api_status(revenue, api_index(None, ACCESSION)) == "not_in_api"


def test_numeric_questions_pick_one_of_each_kind_with_evidence() -> None:
    (outcome,) = numeric_questions([(FILING, parse_html(statements_html()), _companyfacts())])
    kinds = [q.meta["kind"] for q in outcome.chosen]  # type: ignore[index]
    assert kinds == ["extraction", "derived"]
    first, second = outcome.chosen
    assert first.meta["family"] == "income"  # type: ignore[index]  # filing 0: income first
    assert second.meta["family"] == "balance"  # type: ignore[index]
    for question in outcome.chosen:
        assert question.id.startswith("fresh_ACME_2027Q2_10Q_")
        assert question.meta["xbrl_api"] == "verified"  # type: ignore[index]
        assert question.evidence and all(e.full_page_text for e in question.evidence)
        assert all(e.text in e.full_page_text for e in question.evidence)


def test_mismatched_figures_never_become_questions() -> None:
    (outcome,) = numeric_questions(
        [(FILING, parse_html(statements_html()), _companyfacts(revenue_factor=0.999))]
    )
    assert "revenue" in outcome.mismatches
    for question in outcome.chosen:
        assert all(
            c["concept"] != "us-gaap:Revenues"
            for c in question.meta["components"]  # type: ignore[index]
        )


def test_company_split_and_review_sample() -> None:
    (outcome,) = numeric_questions([(FILING, parse_html(statements_html()), _companyfacts())])
    questions = outcome.chosen
    split = company_split(questions, ["Acme"])
    assert split.dev == tuple(q.id for q in questions) and split.test == ()
    assert split_summary(split, questions)["dev"]["kind"] == {"derived": 1, "extraction": 1}
    sample = review_sample(questions, 5, seed=1)
    assert len(sample) == 1  # one question per company


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_choice_is_deterministic(seed: int) -> None:
    def run() -> list[str]:
        (outcome,) = numeric_questions(
            [(FILING, parse_html(statements_html()), _companyfacts())], seed=seed
        )
        return [q.id for q in outcome.chosen]

    assert run() == run()


def test_reviews_reject_fix_or_annotate(tmp_path: Path) -> None:
    path = tmp_path / "review.yaml"
    path.write_text(
        "reviews:\n"
        "  q-reject: {verdict: reject, reviewer: me, note: wrong period}\n"
        "  q-fix: {verdict: fix, question: 'Better wording?', reviewer: me}\n"
        "  q-ok: {verdict: accept, reviewer: me, note: checked the page}\n",
        encoding="utf-8",
    )
    reviews = load_reviews(path)
    (outcome,) = numeric_questions([(FILING, parse_html(statements_html()), _companyfacts())])
    question = outcome.chosen[0]
    assert apply_review(question, None) is question
    assert apply_review(question, reviews["q-reject"]) is None
    fixed = apply_review(question, reviews["q-fix"])
    assert fixed is not None and fixed.question == "Better wording?"
    assert fixed.answer == question.answer and fixed.meta["review"]["verdict"] == "fix"  # type: ignore[index]
    noted = apply_review(question, reviews["q-ok"])
    assert noted is not None and noted.meta["review"]["note"] == "checked the page"  # type: ignore[index]
    assert load_reviews(tmp_path / "missing.yaml") == {}


def test_review_sheet_shows_rows_for_numeric_items() -> None:
    (outcome,) = numeric_questions([(FILING, parse_html(statements_html()), _companyfacts())])
    split = company_split(outcome.chosen, ["Acme"])
    sheet = review_sheet(outcome.chosen, {FILING.doc_name: FILING}, split)
    assert sheet.count("## ") == 2 and "Rows in our parse" in sheet
    assert "(dev)" in sheet and FILING.url in sheet
