from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from filing_agent.corpus.html_parse import parse_html
from filing_agent.fresh.hard import (
    DEFINITIONS,
    PastValue,
    breakdown_draft,
    breakdowns,
    definition_draft,
    definition_parts,
    hard_questions,
    history_draft,
    is_transition,
    member_label,
    past_value,
    past_words,
    period_words,
    printed_label,
)
from filing_agent.fresh.questions import Figure, Period, filing_periods, statement_figures
from tests.test_fresh_questions import FILING, _companyfacts, statements_html

QUARTER = Period("quarter", date(2026, 5, 1), date(2026, 7, 31))
SPEC = {s.key: s for s in DEFINITIONS}


def _figure(name: str, value: float, statement: str = "income_statement", **kw: Any) -> Figure:
    fields: dict[str, Any] = {
        "concept": f"us-gaap:{name}",
        "unit": "USD",
        "value": value,
        "scale": 6,
        "displayed": f"{value / 1e6:,.0f}",
        "start": QUARTER.start,
        "end": QUARTER.end,
        "page": 1,
        "statement": statement,
        "row": f"{name} | {value / 1e6:,.0f}",
    }
    return Figure(**(fields | kw))


def test_member_labels_resolve_to_the_printed_name() -> None:
    assert member_label("wmt:WalmartInternationalSegmentMember") == "Walmart International"
    assert member_label("srt:EuropeMiddleEastAndAfricaMember") == "Europe Middle East and Africa"
    page = "Net sales by product\niPhone | $49,000\nINNOVATIVE MEDICINE | 16,757"
    assert printed_label(page, member_label("aapl:IPhoneMember")) == "iPhone"
    assert printed_label(page, "Innovative Medicine") == "Innovative Medicine"
    assert printed_label("Revenue in the U.S. rose 4%.", "US") == "U.S."
    assert printed_label("Revenue by segment", "Widgets") is None


def test_periods_are_named_in_fiscal_terms() -> None:
    assert period_words(FILING, QUARTER, transition=False) == (
        "in",
        "the second quarter of fiscal 2027",
    )
    ytd = Period("ytd", date(2026, 2, 1), date(2026, 7, 31))
    assert period_words(FILING, ytd, False) == ("in", "the first half of fiscal 2027")
    instant = Period("instant", None, date(2026, 7, 31))
    assert period_words(FILING, instant, False) == (
        "at",
        "the end of the second quarter of fiscal 2027",
    )
    annual = FILING.model_copy(update={"form": "10-K", "fiscal_period": "FY"})
    year = Period("year", date(2025, 8, 1), date(2026, 7, 31))
    assert period_words(annual, year, False) == ("in", "fiscal 2027")
    # inside a fiscal-year transition the period keeps its dates
    assert period_words(FILING, QUARTER, True) == ("in", "the three months ended July 31, 2026")


def test_transition_needs_repeated_mentions_beyond_the_cover() -> None:
    cover = "For the transition period from ___ to ___"
    html = parse_html(f"<html><body><p>{cover}</p></body></html>".encode())
    assert not is_transition(html)
    body = " ".join(["the Transition Period"] * 5)
    brk = '<hr style="page-break-after:always"/>'
    html = parse_html(f"<html><body><p>{cover}</p>{brk}<p>{body}</p></body></html>".encode())
    assert is_transition(html)


def _entry(start: str, end: str, value: float, filed: str, **kw: object) -> dict:
    return {"start": start, "end": end, "val": value, "filed": filed, "accn": filed} | kw


def _facts(*entries: dict) -> dict:
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": list(entries)}}}}}


def test_past_value_is_first_reported_and_refuses_restatements() -> None:
    revenue = _figure("Revenues", 1_000e6)
    original = _entry(
        "2024-05-01", "2024-07-31", 800e6, "2024-09-01", fy=2025, fp="Q2", form="10-Q"
    )
    comparative = _entry("2024-05-01", "2024-07-31", 800e6, "2025-09-01", fy=2026, fp="Q2")
    past = past_value(_facts(comparative, original), revenue)
    assert past is not None and past.filed == "2024-09-01" and past.value == 800e6
    restated = comparative | {"val": 790e6}
    assert past_value(_facts(original, restated), revenue) is None  # which value is the gold?
    six_months = _entry("2024-02-01", "2024-07-31", 1_500e6, "2024-09-01")
    assert past_value(_facts(six_months), revenue) is None  # same end, different duration
    assert past_words(past, FILING, "quarter") == "the second quarter of fiscal 2025"
    relabeled = PastValue(800e6, date(2024, 5, 1), date(2024, 7, 31), 2025, "Q3", "10-Q", "", "")
    assert past_words(relabeled, FILING, "quarter") == "the three months ended July 31, 2024"


def test_history_question_reads_from_two_years_earlier() -> None:
    revenue = _figure("Revenues", 1_000e6, row="Total revenues | 1,000")
    past = PastValue(800e6, date(2024, 5, 1), date(2024, 7, 31), 2025, "Q2", "10-Q", "x", "y")
    words = period_words(FILING, QUARTER, False)
    draft = history_draft("revenue", revenue, past, FILING, words)
    assert draft is not None and draft.answer == "25.0%"
    assert draft.question.startswith(
        "By what percentage did Acme's total revenue change from the second quarter of fiscal "
        "2025 to the second quarter of fiscal 2027?"
    )


def test_definitions_need_one_reading() -> None:
    period = QUARTER
    revenue, operating = _figure("Revenues", 1_000e6), _figure("OperatingIncomeLoss", 150e6)
    parts = definition_parts(SPEC["operating_margin"], [revenue, operating], period)
    assert parts == (operating, revenue)
    second_line = _figure("RevenueFromContractWithCustomerExcludingAssessedTax", 980e6)
    assert (
        definition_parts(SPEC["operating_margin"], [revenue, second_line, operating], period)
        is None
    )
    net = _figure("NetIncomeLoss", 100e6)
    consolidated = _figure("ProfitLoss", 110e6)
    assert definition_parts(SPEC["net_margin"], [revenue, net, consolidated], period) is None
    cfo = _figure("NetCashProvidedByUsedInOperatingActivities", 300e6, "cash_flow_statement")
    capex = _figure("PaymentsToAcquirePropertyPlantAndEquipment", 120e6, "cash_flow_statement")
    software = _figure("PaymentsToDevelopSoftware", 40e6, "cash_flow_statement")
    assert definition_parts(SPEC["free_cash_flow"], [cfo, capex], period) == (cfo, capex)
    assert definition_parts(SPEC["free_cash_flow"], [cfo, capex, software], period) is None


def test_definition_questions_state_no_formula() -> None:
    figures = statement_figures(parse_html(statements_html()))
    periods = filing_periods(figures, date(2026, 7, 31))
    parts = definition_parts(SPEC["operating_margin"], figures, periods["quarter"])
    assert parts is not None
    words = period_words(FILING, periods["quarter"], False)
    draft = definition_draft(SPEC["operating_margin"], parts, "Acme", words)
    assert draft is not None and draft.answer == "15.0%"
    assert draft.question == (
        "What was Acme's operating margin in the second quarter of fiscal 2027? "
        "Answer in percent, rounded to one decimal place."
    )


SEGMENT_HTML = """<html><body>
<div style="display:none"><ix:header><ix:resources>
<xbrli:context id="q"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>
</xbrli:entity><xbrli:period><xbrli:startDate>2026-05-01</xbrli:startDate>
<xbrli:endDate>2026-07-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="w"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>
<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis"
>acme:WidgetsSegmentMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>2026-05-01</xbrli:startDate>
<xbrli:endDate>2026-07-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="t"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>
<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis"
>us-gaap:IntersegmentEliminationMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>2026-05-01</xbrli:startDate>
<xbrli:endDate>2026-07-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
</ix:resources></ix:header></div>
<p>Segment information</p><table>
<tr><td>Widgets</td><td>$</td><td><ix:nonFraction name="us-gaap:Revenues" contextRef="w"
 unitRef="usd" decimals="-6" scale="6">400</ix:nonFraction></td></tr>
<tr><td>Eliminations</td><td><ix:nonFraction name="us-gaap:Revenues" contextRef="t"
 unitRef="usd" decimals="-6" scale="6">20</ix:nonFraction></td></tr>
<tr><td>Total</td><td><ix:nonFraction name="us-gaap:Revenues" contextRef="q"
 unitRef="usd" decimals="-6" scale="6">1,000</ix:nonFraction></td></tr>
</table></body></html>"""


def test_breakdowns_take_named_parts_and_skip_totals_and_eliminations() -> None:
    html = parse_html(SEGMENT_HTML.encode())
    (item,) = breakdowns(html, QUARTER, totals={1_000e6})
    assert (item.axis, item.measure, item.label, item.figure.value) == (
        "segment",
        "revenue",
        "Widgets",
        400e6,
    )
    draft = breakdown_draft(item, "Acme", period_words(FILING, QUARTER, False))
    assert draft is not None and draft.answer == "$400"
    assert draft.question == (
        "What revenue did Acme's Widgets segment report in the second quarter of fiscal 2027? "
        "Answer in USD millions."
    )


@pytest.mark.parametrize("per_filing", [1, 2])
def test_hard_questions_pick_distinct_kinds(per_filing: int) -> None:
    companyfacts = _companyfacts()
    companyfacts["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        _entry("2024-05-01", "2024-07-31", 800e6, "2024-09-01", fy=2025, fp="Q2", form="10-Q")
    )  # two years earlier: makes a history question possible next to the definitions
    (outcome,) = hard_questions(
        [(FILING, parse_html(statements_html()), companyfacts)], per_filing=per_filing
    )
    kinds = [q.meta["kind"] for q in outcome.chosen]  # type: ignore[index]
    assert len(kinds) == per_filing == len(set(kinds))
    assert all(q.id.startswith("fresh_hard_ACME_2027Q2_10Q_") for q in outcome.chosen)
    assert all(e.full_page_text for q in outcome.chosen for e in q.evidence)
