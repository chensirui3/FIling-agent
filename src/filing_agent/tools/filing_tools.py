"""The agent's tools: company lookup, filing list, search, page reading, XBRL facts, calculator."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from filing_agent.config import Settings, get_settings
from filing_agent.corpus.chunk import DOC_TYPE_LABELS, STATEMENT_LABELS, DocInfo
from filing_agent.corpus.index import FilingIndex, Filters, Mode
from filing_agent.corpus.live import LiveError, LiveLibrary, fetch_live
from filing_agent.corpus.parse import ParsedDoc, load_parsed
from filing_agent.fresh.filings import company_keys
from filing_agent.sec.companies import CompanyRegistry, UnknownCompanyError
from filing_agent.sec.http import CacheMiss, SecConfigError, SecHttpError
from filing_agent.sec.service import SecService
from filing_agent.sec.xbrl import UnknownConceptError, XbrlFact
from filing_agent.tools.base import Tool, ToolError, ToolRegistry
from filing_agent.tools.calculator import CalculatorError, calculate

DocType = Literal["10k", "10q", "8k", "earnings", "10k_annualreport"]

TEXT_TOOLS = ("resolve_company", "list_filings", "search_filings", "read_filing_page")
XBRL_TOOLS = ("xbrl_find_concepts", "xbrl_get_facts")
ALL_TOOLS = (*TEXT_TOOLS, *XBRL_TOOLS, "calculate")
# Opt-in: downloads filings from SEC EDGAR. Never part of an evaluation config.
LIVE_TOOLS = ("fetch_filing",)

_SEC_ERRORS = (UnknownCompanyError, UnknownConceptError, CacheMiss, SecConfigError, SecHttpError)


@dataclass
class ToolContext:
    """Shared, lazily loaded resources. `allowed_docs` restricts which filings tools can see."""

    registry: CompanyRegistry
    parsed_dir: Path
    index_loader: Callable[[], FilingIndex]
    sec_loader: Callable[[], SecService] | None = None
    allowed_docs: frozenset[str] | None = None
    search_mode: Mode = "bm25"
    live: LiveLibrary | None = None
    _index: FilingIndex | None = field(default=None, repr=False)
    _sec: SecService | None = field(default=None, repr=False)
    _pages: OrderedDict[str, ParsedDoc] = field(default_factory=OrderedDict, repr=False)
    _page_counts: dict[str, int] | None = field(default=None, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        index_name: str = "contextual",
        xbrl: bool = True,
        allowed_docs: Iterable[str] | None = None,
        sec_offline: bool | None = None,
        live: bool = False,
    ) -> ToolContext:
        s = settings or get_settings()
        return cls(
            registry=CompanyRegistry.load(s.companies_file),
            parsed_dir=s.parsed_dir,
            index_loader=lambda: FilingIndex.load(s.index_dir / index_name),
            sec_loader=(lambda: SecService.from_settings(s, offline=sec_offline)) if xbrl else None,
            allowed_docs=frozenset(allowed_docs) if allowed_docs is not None else None,
            live=LiveLibrary(s.data_dir / "live") if live else None,
        )

    @property
    def index(self) -> FilingIndex:
        if self._index is None:
            self._index = self.index_loader()
        return self._index

    @property
    def sec(self) -> SecService:
        if self.sec_loader is None:
            raise ToolError("XBRL data is not available in this setting.")
        if self._sec is None:
            self._sec = self.sec_loader()
        return self._sec

    def docs(self) -> dict[str, DocInfo]:
        docs = self.index.docs
        if self.live is not None:
            docs = {**self.live.docs(), **docs}
        if self.allowed_docs is None:
            return docs
        return {name: info for name, info in docs.items() if name in self.allowed_docs}

    def page_count(self, doc_name: str) -> int:
        if self.live is not None and doc_name in self.live.filings:
            return self.live.page_count(doc_name)
        if self._page_counts is None:
            counts: dict[str, int] = {}
            for chunk in self.index.chunks:
                counts[chunk.doc_name] = max(counts.get(chunk.doc_name, 0), chunk.page + 1)
            self._page_counts = counts
        return self._page_counts.get(doc_name, 0)

    def parsed(self, doc_name: str) -> ParsedDoc:
        if doc_name not in self.docs():
            raise ToolError(
                f"No filing named {doc_name!r} is available. Use list_filings to see doc_names."
            )
        cached = self._pages.get(doc_name)
        if cached is not None:
            self._pages.move_to_end(doc_name)
            return cached
        if self.live is not None and doc_name in self.live.filings:
            doc = self.live.parsed(doc_name)
        else:
            doc = load_parsed(self.parsed_dir, doc_name)
        self._pages[doc_name] = doc
        while len(self._pages) > 4:
            self._pages.popitem(last=False)
        return doc


# --------------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------------


class ResolveCompanyInput(BaseModel):
    query: str = Field(description="Company name, ticker or CIK, e.g. '3M', 'MMM', 'J&J'.")


class ListFilingsInput(BaseModel):
    company: str = Field(description="Company name, ticker or CIK.")
    doc_type: DocType | None = Field(None, description="Only this filing type.")
    fiscal_year: int | None = Field(None, description="Only filings for this fiscal year.")


class SearchFilingsInput(BaseModel):
    query: str = Field(description="What to find, in the filing's own words.")
    company: str | None = Field(None, description="Only this company's filings (recommended).")
    doc_name: str | None = Field(None, description="Only this filing (a doc_name).")
    doc_type: DocType | None = Field(None, description="Only this filing type.")
    fiscal_year: int | None = Field(None, description="Only filings for this fiscal year.")
    top_k: int | None = Field(None, ge=1, le=15, description="Pages to return (default 8).")


class ReadFilingPageInput(BaseModel):
    doc_name: str = Field(description="Filing id, e.g. 3M_2018_10K.")
    page: int = Field(ge=0, description="Zero-based page number, as search results give it.")
    include_neighbors: bool | None = Field(
        None, description="Also return the previous and next pages (tables often continue)."
    )


class XbrlFindConceptsInput(BaseModel):
    company: str = Field(description="Company name, ticker or CIK.")
    query: str = Field(description="Words describing the line item, e.g. 'revenue'.")
    top_k: int | None = Field(None, ge=1, le=25, description="Concepts to return (default 10).")


class XbrlGetFactsInput(BaseModel):
    company: str = Field(description="Company name, ticker or CIK.")
    concept: str = Field(description="Concept from xbrl_find_concepts, e.g. us-gaap:Revenues.")
    fiscal_year: int | None = Field(None, description="The company's own fiscal-year label.")
    period: Literal["annual", "quarter", "ytd", "instant", "any"] | None = Field(
        None, description="annual, quarter, ytd (year to date), instant (balance sheet) or any."
    )
    policy: Literal["first_reported", "latest"] | None = Field(
        None, description="Value as first reported (default) or the latest restatement."
    )


class FetchFilingInput(BaseModel):
    company: str = Field(description="Company name, ticker or CIK.")
    form: Literal["10-K", "10-Q"] = Field(description="Annual (10-K) or quarterly (10-Q).")
    period_ending: str | None = Field(
        None,
        description="When the reported period ends, as YYYY or YYYY-MM (2024, 2024-09). "
        "Omit for the latest filing of this form.",
    )


class Variable(BaseModel):
    name: str = Field(description="Descriptive identifier, e.g. revenue_2018.")
    value: float = Field(description="The number, without commas or units.")


class CalculateInput(BaseModel):
    expression: str = Field(description="Arithmetic over the variable names.")
    variables: list[Variable] = Field(description="Every input number, each with a name.")


# --------------------------------------------------------------------------------------------
# Implementations
# --------------------------------------------------------------------------------------------


def _resolve_company(ctx: ToolContext, args: ResolveCompanyInput) -> dict[str, Any]:
    company = ctx.registry.resolve(args.query)
    out: dict[str, Any] = {
        "name": company.name,
        "cik": company.cik,
        "tickers": company.tickers,
        "filings_available": sum(1 for d in ctx.docs().values() if d.company == company.name),
    }
    if company.note:
        out["note"] = company.note
    if ctx.sec_loader is not None:
        with suppress(CacheMiss, SecConfigError, SecHttpError):  # optional detail
            out["fiscal_year_end"] = ctx.sec.submissions(company).fiscal_year_end
    return out


def _list_filings(ctx: ToolContext, args: ListFilingsInput) -> dict[str, Any]:
    company = ctx.registry.resolve(args.company)
    owned = [d for d in ctx.docs().values() if d.company == company.name]
    matching = [
        d
        for d in owned
        if (args.doc_type is None or d.doc_type == args.doc_type)
        and (args.fiscal_year is None or d.doc_period == args.fiscal_year)
    ]
    if not matching:
        years = sorted({d.doc_period for d in owned})
        raise ToolError(
            f"No {company.name} filings match those filters. "
            f"Available fiscal years: {years or 'none'}."
        )
    matching.sort(key=lambda d: (-d.doc_period, d.doc_name))
    return {
        "company": company.name,
        "filings": [
            {
                "doc_name": d.doc_name,
                "type": DOC_TYPE_LABELS.get(d.doc_type, d.doc_type),
                "fiscal_year": d.doc_period,
                "pages": ctx.page_count(d.doc_name),
            }
            for d in matching
        ],
    }


def _snippet(text: str, limit: int = 700) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[:limit] + " ..."


def _search_filings(ctx: ToolContext, args: SearchFilingsInput) -> dict[str, Any]:
    companies = frozenset({ctx.registry.resolve(args.company).name}) if args.company else None
    available = ctx.docs()
    if args.doc_name and args.doc_name not in available:
        raise ToolError(f"No filing named {args.doc_name!r}. Use list_filings to see doc_names.")
    doc_names: set[str] | None = {args.doc_name} if args.doc_name else None
    if ctx.allowed_docs is not None:
        doc_names = (doc_names or set(ctx.allowed_docs)) & set(ctx.allowed_docs)
    filters = Filters(
        companies=companies,
        doc_names=frozenset(doc_names) if doc_names is not None else None,
        doc_types=frozenset({args.doc_type}) if args.doc_type else None,
        periods=frozenset({args.fiscal_year}) if args.fiscal_year is not None else None,
    )
    top_k = args.top_k or 8
    hits = ctx.index.search(args.query, mode=ctx.search_mode, top_k=top_k, filters=filters)
    if ctx.live is not None:  # scores of the two indexes are only roughly comparable
        hits += ctx.live.search(args.query, top_k=top_k, filters=filters)
        hits = sorted(hits, key=lambda h: -h.score)[:top_k]
    if not hits:
        return {"results": [], "note": "No matching pages. Try other words or fewer filters."}
    return {
        "results": [
            {
                "doc_name": h.doc_name,
                "page": h.page,
                "header": h.header,
                "snippet": _snippet(h.text),
            }
            for h in hits
        ]
    }


def _read_filing_page(ctx: ToolContext, args: ReadFilingPageInput) -> dict[str, Any]:
    doc = ctx.parsed(args.doc_name)
    if args.page >= doc.n_pages:
        raise ToolError(f"{args.doc_name} has pages 0 to {doc.n_pages - 1}.")
    wanted = [args.page]
    if args.include_neighbors:
        wanted = [p for p in (args.page - 1, args.page, args.page + 1) if 0 <= p < doc.n_pages]
    pages = []
    for number in wanted:
        page = doc.pages[number]
        pages.append(
            {
                "page": number,
                "statement": STATEMENT_LABELS.get(page.statement or ""),
                "units": page.units_line,
                "text": page.layout or page.text or "(this page has no text layer)",
            }
        )
    return {"doc_name": args.doc_name, "pages_in_filing": doc.n_pages, "pages": pages}


def _fetch_filing(ctx: ToolContext, args: FetchFilingInput) -> dict[str, Any]:
    if ctx.live is None:
        raise ToolError("Fetching filings from EDGAR is not enabled in this setting.")
    company = ctx.registry.resolve(args.company)
    names: dict[str, list[str]] = {}
    for info in ctx.index.docs.values():
        names.setdefault(info.company, []).append(info.doc_name)
    try:
        filing, status = fetch_live(
            ctx.sec.client,
            company,
            ctx.live,
            form=args.form,
            period_ending=args.period_ending,
            keys=company_keys(names),
            known_docs=ctx.index.docs.keys(),
        )
    except LiveError as exc:
        raise ToolError(str(exc)) from exc
    ctx._page_counts = None
    out: dict[str, Any] = {
        "status": status,
        "doc_name": filing.doc_name,
        "company": filing.company,
        "type": filing.form,
        "fiscal_year": filing.fiscal_year,
        "fiscal_period": filing.fiscal_period,
        "period_ending": filing.report_date.isoformat() if filing.report_date else None,
        "filed": filing.filing_date.isoformat(),
        "accession": filing.accession,
        "pages": ctx.page_count(filing.doc_name),
    }
    if filing.fiscal_note:
        out["note"] = filing.fiscal_note
    return out


def _find_concepts(ctx: ToolContext, args: XbrlFindConceptsInput) -> dict[str, Any]:
    company = ctx.registry.resolve(args.company)
    matches = ctx.sec.find_concepts(company.name, args.query, args.top_k or 10)
    if not matches:
        raise ToolError(f"No {company.name} XBRL concept matches {args.query!r}; try other words.")
    return {
        "company": company.name,
        "concepts": [m.model_dump(exclude={"score"}, exclude_none=True) for m in matches],
    }


def display_value(value: float, unit: str) -> str:
    """A human-readable scale next to the raw XBRL value: 1577000000 USD -> 1,577 million USD."""

    def millions(v: float) -> str:
        return f"{v / 1e6:,.3f}".rstrip("0").rstrip(".") + " million"

    if unit in ("USD", "shares") and abs(value) >= 100_000:
        return f"{millions(value)} {unit}"
    if unit == "USD/shares":
        return f"{value:g} USD per share"
    return f"{value:,.6g} {unit}"


def _fact_row(fact: XbrlFact) -> dict[str, Any]:
    value: float | int = int(fact.value) if fact.value.is_integer() else fact.value
    row: dict[str, Any] = {
        "fiscal_year": fact.fiscal_year,
        "fiscal_period": fact.fiscal_period,
        "period_type": fact.period_type,
        "start": fact.start.isoformat() if fact.start else None,
        "end": fact.end.isoformat(),
        "value": value,
        "unit": fact.unit,
        "display": display_value(fact.value, fact.unit),
        "form": fact.form,
        "filed": fact.filed.isoformat(),
        "accession": fact.accession,
    }
    if fact.fiscal_label_inferred:
        row["fiscal_label_inferred"] = True
    if fact.restated:
        row["restated"] = True
        row["reported_values"] = [
            {"value": v.value, "form": v.form, "filed": v.filed.isoformat()}
            for v in fact.reported_values
        ]
    return row


MAX_FACT_ROWS = 24


def _get_facts(ctx: ToolContext, args: XbrlGetFactsInput) -> dict[str, Any]:
    company = ctx.registry.resolve(args.company)
    facts = ctx.sec.get_facts(
        company.name,
        args.concept,
        fiscal_year=args.fiscal_year,
        period=args.period or "any",
        policy=args.policy or "first_reported",
    )
    if not facts:
        raise ToolError(
            f"{company.name} reports {args.concept} but no values match those filters; "
            "try period='any' or another fiscal_year."
        )
    out: dict[str, Any] = {
        "company": company.name,
        "concept": facts[0].concept,
        "label": facts[0].label,
        "values": [_fact_row(f) for f in facts[:MAX_FACT_ROWS]],
    }
    if len(facts) > MAX_FACT_ROWS:
        out["more_values"] = len(facts) - MAX_FACT_ROWS
    return out


def _calculate(args: CalculateInput) -> dict[str, Any]:
    result = calculate(args.expression, [(v.name, v.value) for v in args.variables])
    return result.model_dump()


# --------------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------------

DESCRIPTIONS = {
    "resolve_company": (
        "Look up a company by name, ticker or CIK. Returns the canonical name the other tools "
        "use, its SEC CIK and tickers, how many filings are available, and (when known) the "
        "fiscal year end as MMDD, e.g. '0131' for a year ending January 31."
    ),
    "list_filings": (
        "List the filings available to search and read for a company: doc_name, type (10-K, "
        "10-Q, 8-K, earnings release), fiscal year and page count. Use it to pick the filing "
        "that matches the question's period before searching."
    ),
    "search_filings": (
        "Keyword search (BM25) over filing pages; returns the best-matching pages with a snippet "
        "each. Always filter by company, and by fiscal_year or doc_name once you know them: "
        "unfiltered search across all companies rarely finds the right page. Use the filing's "
        "own wording ('purchases of property, plant and equipment', not 'capex'). Results are "
        "pages; read them with read_filing_page."
    ),
    "read_filing_page": (
        "Read one page of a filing in full, with table columns aligned, plus the page's "
        "statement type and units line (e.g. 'In millions, except per share data') when "
        "detected. Read the full page before taking a number from a table: check the units and "
        "which column is which period. include_neighbors adds the pages before and after."
    ),
    "xbrl_find_concepts": (
        "Find the XBRL concepts (tags) a company reports that match a description, e.g. "
        "'payments to acquire property plant and equipment' or 'revenue'. Returns concept names, "
        "labels, units and the years covered. Use it before xbrl_get_facts: tag names differ "
        "between companies and years."
    ),
    "xbrl_get_facts": (
        "Reported values of one XBRL concept from the company's 10-K and 10-Q filings (SEC API). "
        "Each value has its exact period (start, end), period type (annual, quarter, ytd, "
        "instant), the company's own fiscal-year label, the filing it came from, and a restated "
        "flag. Values are in whole units ('display' shows the scale: 1577000000 USD is 1,577 "
        "million USD). Filter by fiscal_year and period. Returns values as first reported unless "
        "policy is 'latest'."
    ),
    "fetch_filing": (
        "Download a 10-K or 10-Q from SEC EDGAR and make it available to list_filings, "
        "search_filings and read_filing_page. Use it when list_filings does not show the "
        "filing you need. Give the form and when the reported period ends (YYYY, or YYYY-MM "
        "for a quarter); omit the period for the latest filing. Returns the new doc_name, "
        "its fiscal year and period, and its page count. Works for filings with inline "
        "XBRL, roughly fiscal 2019 onward."
    ),
    "calculate": (
        "Evaluate arithmetic exactly. Use it for every computed figure. Give each input number "
        "a name in variables and use the names in the expression, e.g. expression "
        "'(capex_2018 / revenue_2018) * 100' with variables capex_2018=1577 and "
        "revenue_2018=32765. Supports + - * / **, parentheses, abs, round(x, digits) (half-up), "
        "min, max, sum, avg and sqrt."
    ),
}


def build_tools(ctx: ToolContext, names: Iterable[str] = ALL_TOOLS) -> ToolRegistry:
    company_errors = (UnknownCompanyError,)
    tools = {
        "resolve_company": Tool(
            "resolve_company",
            DESCRIPTIONS["resolve_company"],
            ResolveCompanyInput,
            lambda a: _resolve_company(ctx, a),
            2_000,
            company_errors,
        ),
        "list_filings": Tool(
            "list_filings",
            DESCRIPTIONS["list_filings"],
            ListFilingsInput,
            lambda a: _list_filings(ctx, a),
            4_000,
            company_errors,
        ),
        "search_filings": Tool(
            "search_filings",
            DESCRIPTIONS["search_filings"],
            SearchFilingsInput,
            lambda a: _search_filings(ctx, a),
            9_000,
            company_errors,
        ),
        "read_filing_page": Tool(
            "read_filing_page",
            DESCRIPTIONS["read_filing_page"],
            ReadFilingPageInput,
            lambda a: _read_filing_page(ctx, a),
            24_000,
            (FileNotFoundError,),
        ),
        "xbrl_find_concepts": Tool(
            "xbrl_find_concepts",
            DESCRIPTIONS["xbrl_find_concepts"],
            XbrlFindConceptsInput,
            lambda a: _find_concepts(ctx, a),
            6_000,
            _SEC_ERRORS,
        ),
        "xbrl_get_facts": Tool(
            "xbrl_get_facts",
            DESCRIPTIONS["xbrl_get_facts"],
            XbrlGetFactsInput,
            lambda a: _get_facts(ctx, a),
            9_000,
            _SEC_ERRORS,
        ),
        "fetch_filing": Tool(
            "fetch_filing",
            DESCRIPTIONS["fetch_filing"],
            FetchFilingInput,
            lambda a: _fetch_filing(ctx, a),
            2_000,
            _SEC_ERRORS,
        ),
        "calculate": Tool(
            "calculate",
            DESCRIPTIONS["calculate"],
            CalculateInput,
            _calculate,
            2_000,
            (CalculatorError,),
        ),
    }
    return ToolRegistry(tools[name] for name in names)
