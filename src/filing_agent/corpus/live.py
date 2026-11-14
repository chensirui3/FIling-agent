"""Filings fetched from SEC EDGAR on demand, searchable next to the built index.

The built index covers a fixed corpus. `fetch_live` adds any 10-K or 10-Q of a registered
company: it finds the filing in the company's EDGAR filing list, downloads the primary
document, parses it like the fresh benchmark's filings and stores it in a small library:

- `manifest.json`: one pinned record per filing (accession, URL, SHA-256, fiscal period);
- `parsed/`: the parsed pages;
- a keyword index over the library's own filings, rebuilt in memory when one is added.

The main index is never modified, so evaluation runs are unaffected. Search results from the
two indexes are merged by BM25 score, which is only roughly comparable across them.

Limits: the filing must carry inline-XBRL cover facts (fiscal year and period), which periodic
reports have had since about 2019-2021, and the company must be in the registry.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from filing_agent.corpus.chunk import DocInfo
from filing_agent.corpus.index import FilingIndex, Filters, IndexSpec, PageHit
from filing_agent.corpus.parse import ParsedDoc, load_parsed
from filing_agent.fresh.filings import FreshFiling, fetch_filing, pin_filing
from filing_agent.sec.companies import Company
from filing_agent.sec.http import SecClient, submissions_page_url, submissions_url
from filing_agent.sec.submissions import Filing, older_page_names, parse_submissions

Form = Literal["10-K", "10-Q"]
MANIFEST_FILE = "manifest.json"
PARSED_DIR = "parsed"
_PERIOD = re.compile(r"^(?:19|20)\d{2}(?:-(?:0[1-9]|1[0-2]))?$")


class LiveError(LookupError):
    """The requested filing cannot be fetched; the message says what to try instead."""


class LiveManifest(BaseModel):
    filings: list[FreshFiling] = []


class LiveLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        path = root / MANIFEST_FILE
        manifest = (
            LiveManifest.model_validate_json(path.read_text(encoding="utf-8"))
            if path.is_file()
            else LiveManifest()
        )
        self.filings: dict[str, FreshFiling] = {f.doc_name: f for f in manifest.filings}
        self._index: FilingIndex | None = None

    @property
    def parsed_dir(self) -> Path:
        return self.root / PARSED_DIR

    def docs(self) -> dict[str, DocInfo]:
        with self._lock:
            return {name: f.doc_info() for name, f in self.filings.items()}

    def parsed(self, doc_name: str) -> ParsedDoc:
        return load_parsed(self.parsed_dir, doc_name)

    def add(self, filing: FreshFiling) -> None:
        """Record a filing whose parsed pages are already in `parsed_dir`."""
        with self._lock:
            self.filings[filing.doc_name] = filing
            self.root.mkdir(parents=True, exist_ok=True)
            manifest = LiveManifest(filings=sorted(self.filings.values(), key=lambda f: f.doc_name))
            (self.root / MANIFEST_FILE).write_text(
                manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            self._index = None

    def index(self) -> FilingIndex | None:
        with self._lock:
            if self._index is None and self.filings:
                docs = self.docs()
                self._index = FilingIndex.build(
                    IndexSpec(name="live", embedding_model=None),
                    docs,
                    (self.parsed(name) for name in docs),
                )
            return self._index

    def page_count(self, doc_name: str) -> int:
        return self.parsed(doc_name).n_pages if doc_name in self.filings else 0

    def search(self, query: str, *, top_k: int, filters: Filters) -> list[PageHit]:
        index = self.index()
        return (
            [] if index is None else index.search(query, mode="bm25", top_k=top_k, filters=filters)
        )


def _matches(filing: Filing, form: Form, period_ending: str | None) -> bool:
    if filing.form != form:  # exact: amendments (10-K/A) are left out
        return False
    if period_ending is None:
        return True
    return filing.report_date is not None and filing.report_date.isoformat().startswith(
        period_ending
    )


def find_filing(filings: Collection[Filing], form: Form, period_ending: str | None) -> Filing:
    """The one filing of `form` whose period ends in `period_ending` (YYYY or YYYY-MM), or the
    latest when no period is given."""
    if period_ending is not None and not _PERIOD.match(period_ending):
        raise LiveError("period_ending must look like 2024 or 2024-09.")
    matching = sorted(
        (f for f in filings if _matches(f, form, period_ending)),
        key=lambda f: (f.report_date or date.min, f.filing_date),
        reverse=True,
    )
    if not matching:
        ends = sorted(
            {f.report_date.isoformat() for f in filings if f.form == form and f.report_date},
            reverse=True,
        )[:8]
        raise LiveError(
            f"No {form} with a period ending in {period_ending or 'any period'} was found. "
            f"Recent {form} period ends: {', '.join(ends) or 'none'}."
        )
    if period_ending is None:
        return matching[0]
    ends = sorted({f.report_date.isoformat() for f in matching if f.report_date})
    if len(ends) > 1:
        raise LiveError(
            f"Several {form} filings have a period ending in {period_ending}: "
            f"{', '.join(ends)}. Give period_ending as YYYY-MM."
        )
    return matching[0]


def _filing_list(client: SecClient, company: Company, *, older: bool) -> list[Filing]:
    url = submissions_url(company.cik)
    data = client.get_json(url, refresh=not client.offline)  # new filings appear daily
    pages = (
        [client.get_json(submissions_page_url(n)) for n in older_page_names(data)] if older else []
    )
    return parse_submissions(data, pages).filings


def fetch_live(
    client: SecClient,
    company: Company,
    library: LiveLibrary,
    *,
    form: Form,
    period_ending: str | None,
    keys: Mapping[str, str],
    known_docs: Collection[str] = (),
) -> tuple[FreshFiling, Literal["fetched", "already_available"]]:
    """Find, download, parse and record one filing. `known_docs` are doc_names the built index
    already holds, which are reported instead of fetched twice."""
    try:
        chosen = find_filing(_filing_list(client, company, older=False), form, period_ending)
    except LiveError:
        if period_ending is None:
            raise
        chosen = find_filing(_filing_list(client, company, older=True), form, period_ending)
    outcome = pin_filing(client, company, chosen, keys)
    filing = outcome.filing
    if filing is None:
        raise LiveError(
            f"{company.name}'s {form} for the period ending {chosen.report_date} cannot be "
            f"added: {outcome.reason} Filings before about 2019 lack these cover facts."
        )
    if filing.doc_name in known_docs or filing.doc_name in library.filings:
        return filing, "already_available"
    fetch_filing(client, filing, library.parsed_dir)
    library.add(filing)
    return filing, "fetched"
