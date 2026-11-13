"""Selecting, pinning and parsing the fresh filings.

Selection rule: for each company, its latest 10-K or 10-Q (amendments excluded) filed in
[filed_after, as_of]. The manifest pins each filing's accession number and the SHA-256 of its
primary document, so the corpus can be rebuilt byte for byte from the SEC archive.

Fresh filings follow FinanceBench's naming: WALMART_2027Q2_10Q, APPLE_2026_10K. The fiscal year
and quarter come from the filing's own cover facts (dei:DocumentFiscalYearFocus/PeriodFocus).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from filing_agent.corpus.chunk import DocInfo
from filing_agent.corpus.html_parse import HTML_PARSER_VERSION, parse_html, parsed_doc_from_html
from filing_agent.corpus.parse import ParsedDoc, parsed_path
from filing_agent.sec.companies import Company
from filing_agent.sec.http import SecClient, submissions_url
from filing_agent.sec.submissions import Filing, parse_submissions

PERIODIC_FORMS = ("10-K", "10-Q")
MANIFEST_VERSION = 1


class FreshFiling(BaseModel):
    doc_name: str
    company: str
    cik: int
    form: str  # "10-K" or "10-Q"
    accession: str
    filing_date: date
    report_date: date | None
    fiscal_year: int
    fiscal_period: str  # "Q1".."Q3" or "FY"
    url: str
    size: int
    sha256: str
    fiscal_note: str | None = None  # set when the filing's text overrides its cover facts

    @property
    def doc_type(self) -> str:
        return "10k" if self.form == "10-K" else "10q"

    def doc_info(self) -> DocInfo:
        return DocInfo(
            doc_name=self.doc_name,
            company=self.company,
            doc_type=self.doc_type,
            doc_period=self.fiscal_year,
        )


class FreshManifest(BaseModel):
    version: int = MANIFEST_VERSION
    rule: str
    filed_after: date
    as_of: date
    filings: list[FreshFiling]
    skipped: dict[str, str] = {}  # company -> reason

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> FreshManifest:
        if not path.is_file():
            raise FileNotFoundError(f"{path} not found. Run `fa fresh select` first.")
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def doc_infos(self) -> dict[str, DocInfo]:
        return {f.doc_name: f.doc_info() for f in self.filings}


def selection_rule(filed_after: date, as_of: date) -> str:
    return (
        f"Per company, the latest {' or '.join(PERIODIC_FORMS)} (no amendments) "
        f"filed from {filed_after.isoformat()} to {as_of.isoformat()}."
    )


def latest_periodic(filings: Iterable[Filing], filed_after: date, as_of: date) -> Filing | None:
    candidates = [
        f for f in filings if f.form in PERIODIC_FORMS and filed_after <= f.filing_date <= as_of
    ]
    return max(candidates, key=lambda f: (f.filing_date, f.accession), default=None)


# --------------------------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------------------------

_KEY = re.compile(r"^(?P<key>.+?)_(?:19|20)\d{2}")


def company_keys(doc_names: Mapping[str, Iterable[str]]) -> dict[str, str]:
    """Company -> its FinanceBench file-name prefix ("Johnson & Johnson" -> "JOHNSON_JOHNSON")."""
    keys: dict[str, str] = {}
    for company, names in doc_names.items():
        prefixes = Counter(m.group("key") for n in names if (m := _KEY.match(n)))
        if prefixes:
            keys[company] = prefixes.most_common(1)[0][0]
    return keys


def fallback_key(company: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", company.upper())


def fresh_doc_name(key: str, form: str, fiscal_year: int, fiscal_period: str) -> str:
    if form == "10-K":
        return f"{key}_{fiscal_year}_10K"
    return f"{key}_{fiscal_year}{fiscal_period}_10Q"


def fiscal_focus(dei: Mapping[str, str]) -> tuple[int, str]:
    """(fiscal year, "Q1".."Q3"/"FY") from a filing's cover facts."""
    year = re.search(r"(?:19|20)\d{2}", dei.get("DocumentFiscalYearFocus", ""))
    period = re.search(r"Q[1-4]|FY", dei.get("DocumentFiscalPeriodFocus", "").upper())
    if not year or not period:
        raise ValueError(
            "The filing does not state its fiscal year and period "
            "(dei:DocumentFiscalYearFocus / dei:DocumentFiscalPeriodFocus)."
        )
    return int(year.group()), period.group()


_ORDINALS = {"Q1": "first", "Q2": "second", "Q3": "third"}


def fiscal_year_in_text(pages: Iterable[str], period: str, cover_year: int) -> int | None:
    """The fiscal year the filing's own words give this quarter, if it disagrees with the cover.

    Cover facts can be wrong: Oracle's 10-Q for the quarter ended August 31, 2026 tags fiscal
    year 2026, while its text compares "the first quarter of fiscal 2027" (58 mentions) with
    "the first quarter of fiscal 2026" (36). A 10-Q compares its quarter with the same quarter
    a year earlier, so only a year named together with the year before it counts as current.

    A year named alone is not enough. Amcor's quarter ended September 30, 2026 opens a
    transition period after a change of fiscal year end; its text says "first quarter of
    fiscal year 2026" only for the comparison quarter.
    """
    ordinal = _ORDINALS.get(period)
    if ordinal is None:
        return None
    pattern = re.compile(
        rf"\b{ordinal} quarter of fiscal (?:year )?((?:19|20)\d\d)\b", re.IGNORECASE
    )
    counts = Counter(int(m.group(1)) for page in pages for m in pattern.finditer(page))
    paired = [year for year in counts if year - 1 in counts and abs(year - cover_year) <= 1]
    if not paired:
        return None
    current = max(paired, key=lambda year: (counts[year], year))
    return current if current != cover_year else None


# --------------------------------------------------------------------------------------------
# Select and fetch
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectOutcome:
    company: str
    filing: FreshFiling | None = None
    reason: str | None = None


def select_filings(
    client: SecClient,
    companies: Iterable[Company],
    keys: Mapping[str, str],
    *,
    filed_after: date,
    as_of: date,
    refresh: bool = True,
    on_done: Callable[[SelectOutcome], None] | None = None,
) -> FreshManifest:
    """Pick each company's filing, download its primary document and pin it in a manifest."""
    filings: list[FreshFiling] = []
    skipped: dict[str, str] = {}
    for company in companies:
        outcome = _select_one(client, company, keys, filed_after, as_of, refresh)
        if outcome.filing is not None:
            filings.append(outcome.filing)
        else:
            skipped[company.name] = outcome.reason or "unknown"
        if on_done:
            on_done(outcome)
    return FreshManifest(
        rule=selection_rule(filed_after, as_of),
        filed_after=filed_after,
        as_of=as_of,
        filings=sorted(filings, key=lambda f: f.doc_name),
        skipped=dict(sorted(skipped.items())),
    )


def _select_one(
    client: SecClient,
    company: Company,
    keys: Mapping[str, str],
    filed_after: date,
    as_of: date,
    refresh: bool,
) -> SelectOutcome:
    submissions = parse_submissions(client.get_json(submissions_url(company.cik), refresh=refresh))
    filing = latest_periodic(submissions.filings, filed_after, as_of)
    if filing is None:
        return SelectOutcome(company.name, reason="no 10-K or 10-Q filed in the window")
    data = client.get_bytes(filing.url)
    html = parse_html(data)
    try:
        fiscal_year, fiscal_period = fiscal_focus(html.dei)
    except ValueError as exc:
        return SelectOutcome(company.name, reason=f"{filing.accession}: {exc}")
    note = None
    if (from_text := fiscal_year_in_text(html.pages, fiscal_period, fiscal_year)) is not None:
        note = (
            f"Cover facts give fiscal {fiscal_year} {fiscal_period}; the filing's text calls "
            f"this period the {_ORDINALS[fiscal_period]} quarter of fiscal {from_text}."
        )
        fiscal_year = from_text
    key = keys.get(company.name) or fallback_key(company.name)
    return SelectOutcome(
        company.name,
        FreshFiling(
            doc_name=fresh_doc_name(key, filing.form, fiscal_year, fiscal_period),
            company=company.name,
            cik=company.cik,
            form=filing.form,
            accession=filing.accession,
            filing_date=filing.filing_date,
            report_date=filing.report_date,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            url=filing.url,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            fiscal_note=note,
        ),
    )


class IntegrityError(RuntimeError):
    """A downloaded document does not match the hash pinned in the manifest."""


@dataclass(frozen=True)
class FetchOutcome:
    doc_name: str
    status: Literal["parsed", "cached"]
    n_pages: int
    statements: int  # pages tagged as a primary statement
    facts: int  # inline-XBRL figures located on a page


def _is_current(path: Path, filing: FreshFiling) -> ParsedDoc | None:
    if not path.is_file():
        return None
    try:
        doc = ParsedDoc.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if doc.parser_version != HTML_PARSER_VERSION or doc.source_size != filing.size:
        return None
    return doc


def fetch_filing(
    client: SecClient, filing: FreshFiling, parsed_dir: Path, *, force: bool = False
) -> FetchOutcome:
    """Download (or read from cache), verify and parse one fresh filing."""
    out = parsed_path(parsed_dir, filing.doc_name)
    cached = None if force else _is_current(out, filing)
    if cached is not None:
        statements = sum(1 for p in cached.pages if p.statement)
        return FetchOutcome(filing.doc_name, "cached", cached.n_pages, statements, -1)
    data = client.get_bytes(filing.url)
    digest = hashlib.sha256(data).hexdigest()
    if digest != filing.sha256:
        raise IntegrityError(
            f"{filing.doc_name}: SHA-256 {digest[:12]}… does not match the manifest "
            f"({filing.sha256[:12]}…). The cached copy at the SEC URL has changed."
        )
    html = parse_html(data)
    doc = parsed_doc_from_html(html, filing.doc_name, len(data))
    parsed_dir.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".part")
    partial.write_text(doc.model_dump_json(), encoding="utf-8")
    os.replace(partial, out)
    statements = sum(1 for p in doc.pages if p.statement)
    return FetchOutcome(filing.doc_name, "parsed", doc.n_pages, statements, len(html.facts))
