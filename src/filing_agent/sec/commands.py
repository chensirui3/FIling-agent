"""`fa sec ...` commands: company lookup, XBRL concepts and facts, filings, cache warm-up."""

from __future__ import annotations

from enum import StrEnum
from typing import cast

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from filing_agent.sec.companies import CompanyRegistry, UnknownCompanyError, normalize_name
from filing_agent.sec.http import CacheMiss, SecConfigError, SecHttpError
from filing_agent.sec.service import SecService
from filing_agent.sec.submissions import select_filings
from filing_agent.sec.xbrl import PeriodFilter, Policy, UnknownConceptError

sec_app = typer.Typer(help="SEC EDGAR: companies, XBRL facts, filings.", no_args_is_help=True)
console = Console()


class PeriodChoice(StrEnum):
    any = "any"
    annual = "annual"
    quarter = "quarter"
    instant = "instant"
    ytd = "ytd"


class PolicyChoice(StrEnum):
    first_reported = "first_reported"
    latest = "latest"


def _service() -> SecService:
    return SecService.from_settings()


def _fail(exc: Exception) -> None:
    console.print(f"[red]{exc}[/]")
    raise typer.Exit(code=1)


@sec_app.command("company")
def company_command(query: str = typer.Argument(..., help="Name, ticker, alias or CIK.")) -> None:
    """Resolve a company to its SEC identity."""
    try:
        company = _service().company(query)
    except UnknownCompanyError as exc:
        _fail(exc)
        return
    console.print(
        f"[bold]{company.name}[/]  CIK {company.cik} ({company.cik10})  "
        f"tickers {', '.join(company.tickers) or '-'}"
        + (f"  [dim]{company.note}[/]" if company.note else "")
    )


@sec_app.command("concepts")
def concepts_command(
    company: str = typer.Argument(..., help="Company name, ticker or CIK."),
    query: str = typer.Argument(..., help="Words to look for in concept names and labels."),
    k: int = typer.Option(10, "-k", "--top-k", min=1, max=50, help="Concepts to show."),
) -> None:
    """Find the XBRL concepts a company reports that match a description."""
    try:
        matches = _service().find_concepts(company, query, k)
    except (UnknownCompanyError, SecConfigError, SecHttpError, CacheMiss) as exc:
        _fail(exc)
        return
    table = Table(title=f"XBRL concepts for {company!r} matching {query!r}", title_justify="left")
    for header in ("concept", "label", "units", "years", "facts"):
        table.add_column(header)
    for m in matches:
        years = f"{m.first_year}-{m.last_year}" if m.first_year else "-"
        table.add_row(m.concept, m.label or "", ", ".join(m.units), years, str(m.n_facts))
    console.print(table)


@sec_app.command("facts")
def facts_command(
    company: str = typer.Argument(..., help="Company name, ticker or CIK."),
    concept: str = typer.Argument(..., help="Concept, e.g. us-gaap:Revenues or Revenues."),
    year: int | None = typer.Option(None, help="Company fiscal year label, e.g. 2022."),
    period: PeriodChoice = typer.Option(PeriodChoice.any, help="Period length."),
    policy: PolicyChoice = typer.Option(
        PolicyChoice.first_reported, help="Value as first reported, or the latest restatement."
    ),
    limit: int = typer.Option(12, min=1, help="Rows to show."),
) -> None:
    """Show a concept's reported values with periods, fiscal labels and filings."""
    try:
        facts = _service().get_facts(
            company,
            concept,
            fiscal_year=year,
            period=cast(PeriodFilter, period.value),
            policy=cast(Policy, policy.value),
        )
    except (
        UnknownCompanyError,
        UnknownConceptError,
        SecConfigError,
        SecHttpError,
        CacheMiss,
    ) as exc:
        _fail(exc)
        return
    table = Table(title=f"{concept} for {company}", title_justify="left")
    for header in (
        "fiscal",
        "period",
        "start",
        "end",
        "value",
        "unit",
        "form",
        "filed",
        "restated",
    ):
        table.add_column(header)
    for f in facts[:limit]:
        table.add_row(
            f"{f.fiscal_year or '?'} {f.fiscal_period or ''}".strip(),
            f.period_type,
            str(f.start or ""),
            str(f.end),
            f"{f.value:,.0f}" if abs(f.value) >= 1000 else f"{f.value:g}",
            f.unit,
            f.form,
            str(f.filed),
            "yes" if f.restated else "",
        )
    console.print(table)


@sec_app.command("filings")
def filings_command(
    company: str = typer.Argument(..., help="Company name, ticker or CIK."),
    form: list[str] | None = typer.Option(None, help="Form types, e.g. --form 10-K (repeatable)."),
    year: int | None = typer.Option(None, help="Year of the period covered."),
    limit: int = typer.Option(15, min=1, help="Rows to show."),
) -> None:
    """List a company's filings from the SEC submissions API."""
    service = _service()
    try:
        submissions = service.submissions(service.company(company), include_older=year is not None)
    except (UnknownCompanyError, SecConfigError, SecHttpError, CacheMiss) as exc:
        _fail(exc)
        return
    table = Table(title=f"Filings: {submissions.name}", title_justify="left")
    for header in ("form", "period", "filed", "accession", "document"):
        table.add_column(header)
    for f in select_filings(submissions, forms=form, year=year, limit=limit):
        table.add_row(f.form, str(f.report_date or ""), str(f.filing_date), f.accession, f.url)
    console.print(table)


@sec_app.command("warm")
def warm_command(
    older: bool = typer.Option(False, "--older", help="Also fetch older filing-history pages."),
) -> None:
    """Fetch submissions and XBRL company facts for every registry company into the cache."""
    service = _service()
    companies = service.registry.companies
    try:
        with Progress(
            TextColumn("[bold]SEC"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("warm", total=len(companies))
            service.warm(companies, include_older=older, on_done=lambda _c: progress.advance(task))
    except (SecConfigError, SecHttpError) as exc:
        _fail(exc)
    console.print(
        f"{len(companies)} companies cached in {service.client.cache_dir} "
        f"({service.client.requests_made} requests made)"
    )


def names_agree(registry_names: list[str], sec_names: list[str]) -> bool:
    """True if every word of some registry name appears in some SEC (current or former) name."""
    sec_words = [set(normalize_name(n).split()) for n in sec_names if n]
    return any(
        words and any(words <= sec for sec in sec_words)
        for words in (set(normalize_name(n).split()) for n in registry_names)
    )


@sec_app.command("verify-companies")
def verify_companies_command() -> None:
    """Check every registry CIK against the SEC's own entity name (and former names)."""
    service = _service()
    registry: CompanyRegistry = service.registry
    table = Table(title="Registry check", title_justify="left")
    for header in ("company", "CIK", "SEC name", "fiscal year end", "ok"):
        table.add_column(header)
    problems = 0
    for company in registry.companies:
        try:
            subs = service.submissions(company)
        except (SecConfigError, SecHttpError, CacheMiss) as exc:
            _fail(exc)
            return
        ok = names_agree([company.name, *company.aliases], [subs.name, *subs.former_names])
        problems += not ok
        table.add_row(
            company.name,
            str(company.cik),
            subs.name,
            subs.fiscal_year_end or "",
            "yes" if ok else "[red]NO[/]",
        )
    console.print(table)
    if problems:
        console.print(f"[red]{problems} CIKs do not match their company names.[/]")
        raise typer.Exit(code=1)
    console.print("All CIKs match. Set `verified: true` in configs/companies.yaml.")
