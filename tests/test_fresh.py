from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import httpx
import pytest

from filing_agent.corpus.parse import load_parsed
from filing_agent.fresh.filings import (
    FreshManifest,
    IntegrityError,
    company_keys,
    fallback_key,
    fetch_filing,
    fiscal_focus,
    fiscal_year_in_text,
    fresh_doc_name,
    latest_periodic,
    select_filings,
)
from filing_agent.sec.companies import Company
from filing_agent.sec.http import RateLimiter, SecClient, submissions_url
from filing_agent.sec.submissions import Filing
from tests.html_fixtures import FILING

AGENT = "Jane Doe jane@contoso.test"
WALMART = Company(name="Walmart", cik=104169, tickers=["WMT"])
COSTCO = Company(name="Costco", cik=909832, tickers=["COST"])
HTML = FILING.encode()
FILED_AFTER, AS_OF = date(2026, 8, 1), date(2026, 11, 12)


def _filing(form: str, filed: date, accession: str) -> Filing:
    return Filing(
        cik=1,
        accession=accession,
        form=form,
        filing_date=filed,
        report_date=None,
        primary_document="x.htm",
        is_xbrl=True,
    )


def _submissions(cik: int, rows: list[tuple[str, str, str, str, str]]) -> dict:
    columns = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
    recent: dict[str, list] = {name: [row[i] for row in rows] for i, name in enumerate(columns)}
    recent["isXBRL"] = [1] * len(rows)
    return {
        "cik": str(cik),
        "name": "",
        "tickers": [],
        "fiscalYearEnd": "0131",
        "formerNames": [],
        "filings": {"recent": recent, "files": []},
    }


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == submissions_url(WALMART.cik):
        rows = [
            ("0000104169-26-000154", "10-Q", "2026-09-04", "2026-07-31", "wmt-20260731.htm"),
            ("0000104169-26-000101", "10-Q", "2026-06-06", "2026-04-30", "wmt-20260430.htm"),
        ]
        return httpx.Response(200, json=_submissions(WALMART.cik, rows))
    if url == submissions_url(COSTCO.cik):
        rows = [("0000909832-26-000020", "10-Q", "2026-07-02", "2026-05-10", "cost-q3.htm")]
        return httpx.Response(200, json=_submissions(COSTCO.cik, rows))
    if url.endswith("/104169/000010416926000154/wmt-20260731.htm"):
        return httpx.Response(200, content=HTML)
    return httpx.Response(404)


def _client(cache: Path, *, offline: bool = False) -> SecClient:
    return SecClient(
        AGENT,
        cache,
        offline=offline,
        transport=httpx.MockTransport(_handler),
        limiter=RateLimiter(1000.0, sleep=lambda _s: None),
    )


def test_latest_periodic_takes_the_newest_10k_or_10q_inside_the_window() -> None:
    filings = [
        _filing("10-Q", date(2026, 7, 30), "before-window"),
        _filing("10-Q", date(2026, 8, 20), "in-window"),
        _filing("10-K/A", date(2026, 9, 1), "amendment"),
        _filing("8-K", date(2026, 10, 1), "current-report"),
        _filing("10-Q", date(2026, 11, 20), "after-as-of"),
    ]
    assert latest_periodic(filings, FILED_AFTER, AS_OF).accession == "in-window"  # type: ignore[union-attr]
    assert latest_periodic(filings[:1], FILED_AFTER, AS_OF) is None


def test_names_follow_financebench_style() -> None:
    keys = company_keys(
        {
            "Johnson & Johnson": ["JOHNSON_JOHNSON_2022_10K", "JOHNSON_JOHNSON_2023Q2_EARNINGS"],
            "3M": ["3M_2018_10K"],
            "Unnamed": ["no-year-in-name"],
        }
    )
    assert keys == {"Johnson & Johnson": "JOHNSON_JOHNSON", "3M": "3M"}
    assert fallback_key("Johnson & Johnson") == "JOHNSONJOHNSON"
    assert fresh_doc_name("WALMART", "10-Q", 2027, "Q2") == "WALMART_2027Q2_10Q"
    assert fresh_doc_name("APPLE", "10-K", 2026, "FY") == "APPLE_2026_10K"


def test_fiscal_focus_reads_the_cover_facts() -> None:
    dei = {"DocumentFiscalYearFocus": "2027", "DocumentFiscalPeriodFocus": "q2"}
    assert fiscal_focus(dei) == (2027, "Q2")
    with pytest.raises(ValueError, match="fiscal year"):
        fiscal_focus({"DocumentFiscalYearFocus": "2027"})


def test_filing_text_overrides_a_wrong_cover_fiscal_year() -> None:
    oracle = [
        "Total revenues for the first quarter of fiscal 2027 rose ...",
        "compared with the first quarter of fiscal 2026. In the first quarter of fiscal 2027 ...",
    ]
    assert fiscal_year_in_text(oracle, "Q1", 2026) == 2027
    assert fiscal_year_in_text(oracle, "Q1", 2027) is None  # cover agrees with the text
    assert fiscal_year_in_text(["third quarter of 2026"], "Q3", 2026) is None  # no fiscal phrase
    assert fiscal_year_in_text(oracle, "FY", 2026) is None  # annual reports keep the cover
    # a year named alone is only the comparison quarter (Amcor's transition period)
    amcor = ["costs in the first quarter of fiscal year 2026 and the Transition Period"]
    assert fiscal_year_in_text(amcor, "Q1", 2027) is None
    # a forward-looking mention of next year's quarter does not outvote the current one
    nike = ["first quarter of fiscal 2027"] * 3 + [
        "first quarter of fiscal 2026",
        "we expect the first quarter of fiscal 2028 to ...",
    ]
    assert fiscal_year_in_text(nike, "Q1", 2027) is None


def test_select_pins_filings_and_fetch_parses_them(tmp_path: Path) -> None:
    cache, parsed_dir = tmp_path / "cache", tmp_path / "parsed"
    with _client(cache) as client:
        manifest = select_filings(
            client, [WALMART, COSTCO], {"Walmart": "WALMART"}, filed_after=FILED_AFTER, as_of=AS_OF
        )
    assert manifest.skipped == {"Costco": "no 10-K or 10-Q filed in the window"}
    (filing,) = manifest.filings
    assert filing.doc_name == "WALMART_2027Q2_10Q"
    assert (filing.fiscal_year, filing.fiscal_period, filing.form) == (2027, "Q2", "10-Q")
    assert filing.sha256 == hashlib.sha256(HTML).hexdigest() and filing.size == len(HTML)
    assert filing.doc_info().doc_type == "10q" and filing.doc_info().doc_period == 2027

    path = tmp_path / "filings.json"
    manifest.save(path)
    assert FreshManifest.load(path) == manifest

    # fetch reads the document cached by select: it works offline
    with _client(cache, offline=True) as offline:
        outcome = fetch_filing(offline, filing, parsed_dir)
        assert (outcome.status, outcome.n_pages, outcome.statements, outcome.facts) == (
            "parsed",
            3,
            1,
            5,
        )
        assert fetch_filing(offline, filing, parsed_dir).status == "cached"
        doc = load_parsed(parsed_dir, filing.doc_name)
        assert doc.pages[1].statement == "income_statement"

        tampered = filing.model_copy(update={"sha256": "0" * 64})
        with pytest.raises(IntegrityError, match="does not match"):
            fetch_filing(offline, tampered, parsed_dir, force=True)
