from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from filing_agent.agent.prompts import system_prompt
from filing_agent.corpus.index import Filters
from filing_agent.corpus.live import LiveError, LiveLibrary, find_filing
from filing_agent.sec.companies import CompanyRegistry
from filing_agent.sec.service import SecService
from filing_agent.sec.submissions import Filing
from filing_agent.tools.filing_tools import ALL_TOOLS, LIVE_TOOLS, ToolContext, build_tools
from tests.search_fixtures import build_index
from tests.test_fresh import WALMART, _client


def _filing(form: str, report: str, accession: str = "a") -> Filing:
    return Filing(
        cik=1,
        accession=accession,
        form=form,
        filing_date=date.fromisoformat(report),
        report_date=date.fromisoformat(report),
        primary_document="x.htm",
        is_xbrl=True,
    )


FILINGS = [
    _filing("10-K", "2024-05-31", "k24"),
    _filing("10-K/A", "2024-05-31", "amended"),
    _filing("10-K", "2023-05-31", "k23"),
    _filing("10-Q", "2024-02-29", "q-feb"),
    _filing("10-Q", "2024-08-31", "q-aug"),
]


def test_find_filing_by_form_and_period_end() -> None:
    assert find_filing(FILINGS, "10-K", None).accession == "k24"  # the latest; no amendments
    assert find_filing(FILINGS, "10-K", "2023").accession == "k23"
    assert find_filing(FILINGS, "10-Q", "2024-08").accession == "q-aug"
    with pytest.raises(LiveError, match=r"2024-02-29, 2024-08-31\. Give period_ending as YYYY-MM"):
        find_filing(FILINGS, "10-Q", "2024")
    with pytest.raises(LiveError, match="Recent 10-K period ends: 2024-05-31, 2023-05-31"):
        find_filing(FILINGS, "10-K", "2019")
    with pytest.raises(LiveError, match="must look like"):
        find_filing(FILINGS, "10-K", "May 2024")


def _run(tools: Any, name: str, payload: dict[str, Any], *, error: bool = False) -> Any:
    outcome = tools.run(name, payload)
    assert outcome.is_error == error, outcome.content
    return outcome.content if error else json.loads(outcome.content)


def _context(tmp_path: Path, *, live: bool = True) -> ToolContext:
    registry = CompanyRegistry([WALMART])
    index = build_index(dense=False)
    return ToolContext(
        registry=registry,
        parsed_dir=tmp_path / "parsed",
        index_loader=lambda: index,
        sec_loader=lambda: SecService(_client(tmp_path / "cache"), registry),
        live=LiveLibrary(tmp_path / "live") if live else None,
    )


def test_fetched_filing_is_listed_searched_and_read(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    tools = build_tools(ctx, (*ALL_TOOLS, *LIVE_TOOLS))
    assert "No Walmart filings" in _run(tools, "list_filings", {"company": "Walmart"}, error=True)

    request = {"company": "WMT", "form": "10-Q", "period_ending": "2026-07"}
    fetched = _run(tools, "fetch_filing", request)
    assert fetched["status"] == "fetched" and fetched["doc_name"] == "WALMART_2027Q2_10Q"
    assert (fetched["fiscal_year"], fetched["fiscal_period"]) == (2027, "Q2")
    assert fetched["period_ending"] == "2026-07-31" and fetched["pages"] >= 1

    listed = _run(tools, "list_filings", {"company": "Walmart", "fiscal_year": 2027})
    assert [f["doc_name"] for f in listed["filings"]] == ["WALMART_2027Q2_10Q"]
    assert listed["filings"][0]["pages"] == fetched["pages"]
    hits = _run(tools, "search_filings", {"query": "net sales", "company": "Walmart"})["results"]
    assert hits and hits[0]["doc_name"] == "WALMART_2027Q2_10Q"
    page = _run(
        tools, "read_filing_page", {"doc_name": hits[0]["doc_name"], "page": hits[0]["page"]}
    )
    assert "Net sales" in page["pages"][0]["text"]

    assert _run(tools, "fetch_filing", request)["status"] == "already_available"
    missing = {"company": "Walmart", "form": "10-K", "period_ending": "2020"}
    assert "No 10-K with a period ending in 2020" in _run(
        tools, "fetch_filing", missing, error=True
    )

    # the library survives a restart, and the built index is untouched
    reopened = LiveLibrary(tmp_path / "live")
    assert list(reopened.docs()) == ["WALMART_2027Q2_10Q"]
    assert (
        reopened.search("net sales", top_k=3, filters=Filters())[0].doc_name == "WALMART_2027Q2_10Q"
    )
    assert "WALMART_2027Q2_10Q" not in ctx.index.docs


def test_fetching_is_opt_in(tmp_path: Path) -> None:
    assert "fetch_filing" not in ALL_TOOLS
    tools = build_tools(_context(tmp_path, live=False), (*ALL_TOOLS, *LIVE_TOOLS))
    request = {"company": "Walmart", "form": "10-Q", "period_ending": None}
    assert "not enabled" in _run(tools, "fetch_filing", request, error=True)
    assert "fetch_filing" not in system_prompt(ALL_TOOLS)
    assert "fetch_filing" in system_prompt((*ALL_TOOLS, *LIVE_TOOLS))
