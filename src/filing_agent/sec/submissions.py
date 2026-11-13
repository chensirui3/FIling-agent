"""Filing history from the SEC submissions API."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from pydantic import BaseModel

ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


class Filing(BaseModel):
    cik: int
    accession: str
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str
    is_xbrl: bool

    @property
    def url(self) -> str:
        return f"{ARCHIVES}/{self.cik}/{self.accession.replace('-', '')}/{self.primary_document}"


class Submissions(BaseModel):
    cik: int
    name: str
    tickers: list[str]
    fiscal_year_end: str | None  # MMDD, e.g. "1231" or "0131"
    former_names: list[str]
    filings: list[Filing]


def _optional_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _columnar(cik: int, block: Mapping[str, Sequence[Any]]) -> list[Filing]:
    n = len(block.get("accessionNumber", []))
    xbrl = block.get("isXBRL") or [0] * n
    report = block.get("reportDate") or [""] * n
    return [
        Filing(
            cik=cik,
            accession=block["accessionNumber"][i],
            form=block["form"][i],
            filing_date=date.fromisoformat(block["filingDate"][i]),
            report_date=_optional_date(report[i]),
            primary_document=block["primaryDocument"][i],
            is_xbrl=bool(xbrl[i]),
        )
        for i in range(n)
    ]


def parse_submissions(
    data: Mapping[str, Any], older_pages: Iterable[Mapping[str, Any]] = ()
) -> Submissions:
    cik = int(data["cik"])
    filings = _columnar(cik, data["filings"]["recent"])
    for page in older_pages:
        filings += _columnar(cik, page)
    filings.sort(key=lambda f: (f.filing_date, f.accession), reverse=True)
    return Submissions(
        cik=cik,
        name=data.get("name", ""),
        tickers=list(data.get("tickers") or []),
        fiscal_year_end=data.get("fiscalYearEnd"),
        former_names=[n.get("name", "") for n in data.get("formerNames") or []],
        filings=filings,
    )


def older_page_names(data: Mapping[str, Any]) -> list[str]:
    return [f["name"] for f in data.get("filings", {}).get("files", [])]


def select_filings(
    submissions: Submissions,
    *,
    forms: Iterable[str] | None = None,
    year: int | None = None,
    limit: int | None = None,
) -> list[Filing]:
    """Filter by form (exact, e.g. "10-K") and by the year of the period the filing covers."""
    wanted = {f.upper() for f in forms} if forms else None
    out = [
        f
        for f in submissions.filings
        if (wanted is None or f.form.upper() in wanted)
        and (year is None or (f.report_date or f.filing_date).year == year)
    ]
    return out[:limit] if limit else out
