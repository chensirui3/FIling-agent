"""`fa fresh ...` commands: pick post-cutoff filings, then download and parse them."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date, datetime

import typer
from rich.console import Console
from rich.table import Table

from filing_agent.agent.client import CREDENTIALS_HINT, credentials_available, make_client
from filing_agent.config import Settings, get_settings
from filing_agent.corpus.html_parse import HtmlFiling, parse_html
from filing_agent.corpus.parse import load_parsed
from filing_agent.data import financebench as fb
from filing_agent.data.financebench import Question
from filing_agent.data.splits import load_split
from filing_agent.fresh.benchmark import (
    apply_review,
    company_split,
    load_reviews,
    review_sample,
    review_sheet,
    save_questions,
    save_split,
)
from filing_agent.fresh.drafting import DEFAULT_EFFORT as DRAFT_EFFORT
from filing_agent.fresh.drafting import DEFAULT_MODEL as DRAFT_MODEL
from filing_agent.fresh.drafting import (
    TextDraft,
    choose_candidate,
    draft_all,
    failures,
    load_drafts,
    save_drafts,
    text_question,
)
from filing_agent.fresh.filings import (
    FetchOutcome,
    FreshFiling,
    FreshManifest,
    IntegrityError,
    SelectOutcome,
    company_keys,
    fetch_filing,
    select_filings,
)
from filing_agent.fresh.hard import KINDS as HARD_KINDS
from filing_agent.fresh.hard import hard_questions
from filing_agent.fresh.questions import DEFAULT_SEED, numeric_questions
from filing_agent.sec.companies import CompanyRegistry
from filing_agent.sec.http import (
    CacheMiss,
    SecClient,
    SecConfigError,
    SecHttpError,
    companyfacts_url,
)

fresh_app = typer.Typer(
    help="Fresh benchmark: filings filed after the model's training cutoff.",
    no_args_is_help=True,
)
console = Console()

DEFAULT_FILED_AFTER = "2026-08-01"
HARD_SPLIT_NAME = "fresh_hard_v1"
_SEC_ERRORS = (SecConfigError, SecHttpError, CacheMiss)


def _client(settings: Settings) -> SecClient:
    return SecClient(settings.sec_user_agent, settings.sec_cache_dir, offline=settings.sec_offline)


def _financebench_keys(settings: Settings) -> dict[str, str]:
    """File-name prefixes FinanceBench uses for each company, so fresh names match its style."""
    try:
        documents = fb.load_documents(settings.financebench_dir)
    except FileNotFoundError:
        return {}
    names: dict[str, list[str]] = {}
    for doc_name, doc in documents.items():
        names.setdefault(doc.company, []).append(doc_name)
    return company_keys(names)


@fresh_app.command("select")
def select_command(
    filed_after: datetime = typer.Option(
        DEFAULT_FILED_AFTER,
        formats=["%Y-%m-%d"],
        help="Earliest filing date; set it after the agent model's training cutoff.",
    ),
    as_of: datetime | None = typer.Option(
        None, formats=["%Y-%m-%d"], help="Latest filing date considered (default: today)."
    ),
    refresh: bool = typer.Option(
        True, "--refresh/--cached", help="Re-download each company's filing list first."
    ),
) -> None:
    """Pick each company's latest 10-K/10-Q in the window and pin it in the fresh manifest."""
    settings = get_settings()
    registry = CompanyRegistry.load(settings.companies_file)
    window_end = as_of.date() if as_of else date.today()

    def report(outcome: SelectOutcome) -> None:
        if outcome.filing is not None:
            f = outcome.filing
            console.print(f"  {f.doc_name:<34} {f.form:<5} filed {f.filing_date}")
        else:
            console.print(f"  [yellow]{outcome.company}: skipped ({outcome.reason})[/]")

    with _client(settings) as client:
        try:
            manifest = select_filings(
                client,
                registry.companies,
                _financebench_keys(settings),
                filed_after=filed_after.date(),
                as_of=window_end,
                refresh=refresh,
                on_done=report,
            )
        except _SEC_ERRORS as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(code=1) from None
    manifest.save(settings.fresh_manifest_file)
    forms = {form: sum(1 for f in manifest.filings if f.form == form) for form in ("10-K", "10-Q")}
    console.print(
        f"{len(manifest.filings)} filings ({forms['10-K']} 10-K, {forms['10-Q']} 10-Q), "
        f"{len(manifest.skipped)} companies skipped -> {settings.fresh_manifest_file}"
    )


@fresh_app.command("fetch")
def fetch_command(
    force: bool = typer.Option(False, help="Re-parse filings whose parsed copy is current."),
) -> None:
    """Download (or read from cache), verify and parse every filing in the fresh manifest."""
    settings = get_settings()
    try:
        manifest = FreshManifest.load(settings.fresh_manifest_file)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from None
    outcomes: list[FetchOutcome] = []
    with _client(settings) as client:
        for filing in manifest.filings:
            try:
                outcomes.append(fetch_filing(client, filing, settings.parsed_dir, force=force))
            except (IntegrityError, *_SEC_ERRORS) as exc:
                console.print(f"[red]{filing.doc_name}: {exc}[/]")
                raise typer.Exit(code=1) from None
    table = Table(title="Fresh filings", title_justify="left")
    for header in ("filing", "status", "pages", "statement pages", "tagged figures"):
        table.add_column(header, justify="left" if header in ("filing", "status") else "right")
    for o in outcomes:
        facts = "–" if o.facts < 0 else str(o.facts)
        table.add_row(o.doc_name, o.status, str(o.n_pages), str(o.statements), facts)
    console.print(table)
    thin = [o.doc_name for o in outcomes if o.statements < 3]
    if thin:
        console.print(
            f"[yellow]{len(thin)} filings have fewer than 3 statement pages tagged:[/] "
            + ", ".join(thin)
        )
    console.print(f"{len(outcomes)} filings -> {settings.parsed_dir}")


def _load_filing(
    client: SecClient, filing: FreshFiling
) -> tuple[FreshFiling, HtmlFiling, dict | None]:
    data = client.get_bytes(filing.url)
    if hashlib.sha256(data).hexdigest() != filing.sha256:
        raise IntegrityError(f"{filing.doc_name}: the cached document does not match the manifest")
    try:
        companyfacts = client.get_json(companyfacts_url(filing.cik))
    except CacheMiss:
        companyfacts = None
    return filing, parse_html(data), companyfacts


@fresh_app.command("questions")
def questions_command(
    seed: int = typer.Option(DEFAULT_SEED, help="Seed for choosing metrics per filing."),
    review: int = typer.Option(10, help="Questions of each kind (numeric, text) to review."),
) -> None:
    """Build the question set: numeric questions plus the drafted text questions, with review
    verdicts applied, split by company, and a review sheet.

    Reads only cached data (filings, SEC XBRL API responses, saved drafts); run `fa fresh
    fetch` first, and `fa fresh draft-text` for the text questions.
    """
    settings = get_settings()
    manifest = FreshManifest.load(settings.fresh_manifest_file)
    with SecClient(settings.sec_user_agent, settings.sec_cache_dir, offline=True) as client:
        try:
            loaded = [_load_filing(client, f) for f in manifest.filings]
        except (IntegrityError, CacheMiss) as exc:
            console.print(f"[red]{exc}[/] Run `fa fresh fetch` first.")
            raise typer.Exit(code=1) from None
    outcomes = numeric_questions(loaded, seed=seed)
    numeric = [q for o in outcomes for q in o.chosen]
    text, text_skipped = _text_questions(settings, manifest.filings)
    reviews = load_reviews(settings.fresh_review_file)
    reviewed = [apply_review(q, reviews.get(q.id)) for q in [*numeric, *text]]
    questions = [q for q in reviewed if q is not None]
    rejected = len(reviewed) - len(questions)
    save_questions(settings.fresh_questions_file, questions)

    dev_companies = load_split(settings.splits_dir / "financebench_v1.json").dev_companies
    split = company_split(questions, dev_companies)
    split_path = settings.splits_dir / "fresh_v1.json"
    save_split(split, questions, split_path)

    sheet_path = settings.results_dir / "fresh" / "review_sheet.md"
    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    is_text = [(q.meta or {}).get("kind") == "text" for q in questions]
    sample = review_sample(
        [q for q, t in zip(questions, is_text, strict=True) if not t], review, seed
    ) + review_sample([q for q, t in zip(questions, is_text, strict=True) if t], review, seed)
    filings = {f.doc_name: f for f in manifest.filings}
    sheet_path.write_text(review_sheet(sample, filings, split) + "\n", encoding="utf-8")

    table = Table(title="Fresh questions", title_justify="left")
    for header in ("kind", "questions", "XBRL API verified", "reviewed"):
        table.add_column(header, justify="left" if header == "kind" else "right")
    for kind in ("extraction", "derived", "text"):
        qs = [q for q in questions if (q.meta or {}).get("kind") == kind]
        verified = sum(1 for q in qs if (q.meta or {}).get("xbrl_api") == "verified")
        changed = sum(1 for q in qs if (q.meta or {}).get("review"))
        table.add_row(kind, str(len(qs)), "–" if kind == "text" else str(verified), str(changed))
    console.print(table)
    mismatched = {o.doc_name: o.mismatches for o in outcomes if o.mismatches}
    if mismatched:
        console.print(f"[yellow]Candidates dropped (figure differs from the API):[/] {mismatched}")
    short = [o.doc_name for o in outcomes if len(o.chosen) < 2]
    if short:
        console.print(f"[yellow]Filings with fewer than 2 numeric questions:[/] {short}")
    if text_skipped:
        console.print(f"[yellow]Filings without a text question:[/] {text_skipped}")
    console.print(
        f"{len(questions)} questions ({rejected} rejected in review) -> "
        f"{settings.fresh_questions_file}\n"
        f"split {split.name}: dev {len(split.dev)} ({len(split.dev_companies)} companies), "
        f"test {len(split.test)} ({len(split.test_companies)} companies) -> {split_path}\n"
        f"review sheet ({len(sample)} questions) -> {sheet_path}"
    )


def _text_questions(
    settings: Settings, filings: list[FreshFiling]
) -> tuple[list[Question], dict[str, str]]:
    """The drafted text question of each filing, re-checked with the current rules."""
    drafts = load_drafts(settings.fresh_text_drafts_file)
    reviews = load_reviews(settings.fresh_review_file)
    questions: list[Question] = []
    skipped: dict[str, str] = {}
    for filing in filings:
        draft = drafts.get(filing.doc_name)
        if draft is None or draft.error or draft.page < 0:
            skipped[filing.doc_name] = draft.error if draft and draft.error else "not drafted"
            continue
        page_text = load_parsed(settings.parsed_dir, filing.doc_name).pages[draft.page].text
        number = reviews.get(f"fresh_{filing.doc_name}_text", {}).get("candidate")
        chosen = choose_candidate(draft, page_text, filing, number)
        if chosen is None:
            skipped[filing.doc_name] = "no candidate passes the checks"
            continue
        questions.append(text_question(filing, draft, *chosen, page_text))
    return questions, skipped


@fresh_app.command("draft-text")
def draft_text_command(
    model: str = typer.Option(DRAFT_MODEL, help="Model that drafts the questions."),
    effort: str = typer.Option(DRAFT_EFFORT, help="Effort for the drafting model."),
    max_cost: float = typer.Option(3.0, help="Stop sending requests after this many USD."),
    redo: bool = typer.Option(False, help="Draft again for filings that already have drafts."),
    doc: list[str] | None = typer.Option(None, help="Only these filings (repeatable)."),
) -> None:
    """Draft one text question per fresh filing with Claude (costs money; resumable).

    Drafts are saved to the fresh directory, so `fa fresh questions` rebuilds the set without
    calling the API again. Filings with a usable draft are skipped unless --redo is given.
    """
    settings = get_settings()
    if not credentials_available(settings):
        console.print(f"[red]{CREDENTIALS_HINT}[/]")
        raise typer.Exit(code=1)
    manifest = FreshManifest.load(settings.fresh_manifest_file)
    existing = load_drafts(settings.fresh_text_drafts_file)
    todo = [
        f
        for f in manifest.filings
        if (not doc or f.doc_name in doc)
        and (redo or f.doc_name not in existing or existing[f.doc_name].error)
    ]
    work = [(f, load_parsed(settings.parsed_dir, f.doc_name)) for f in todo]
    console.print(f"Drafting for {len(work)} filings with {model} (effort {effort}).")

    def report(draft: TextDraft) -> None:
        chosen = draft.chosen()
        status = f"candidate {chosen[0]} ok" if chosen else "[yellow]no usable candidate[/]"
        if draft.error:
            status = f"[red]{draft.error}[/]"
        console.print(
            f"  {draft.doc_name:<32} page {draft.page:>3}  {status}  ${draft.cost_usd or 0:.3f}"
        )

    drafts = draft_all(
        make_client(settings),
        work,
        model=model,
        effort=effort,
        max_cost_usd=max_cost,
        on_done=report,
    )
    existing.update({d.doc_name: d for d in drafts})
    save_drafts(settings.fresh_text_drafts_file, existing.values())
    spent = sum(d.cost_usd or 0.0 for d in drafts)
    usable = sum(1 for d in existing.values() if d.chosen() is not None)
    console.print(
        f"{len(drafts)} drafted (${spent:.2f}); {usable}/{len(manifest.filings)} filings have a "
        f"usable question -> {settings.fresh_text_drafts_file}"
    )
    if problems := failures(existing.values()):
        console.print(f"[yellow]Without a usable question:[/] {problems}")


@fresh_app.command("hard-questions")
def hard_questions_command(
    seed: int = typer.Option(DEFAULT_SEED, help="Seed for choosing questions per filing."),
    per_filing: int = typer.Option(2, min=1, max=3, help="Questions per filing (one per kind)."),
    review: int = typer.Option(12, help="Questions on the review sheet."),
) -> None:
    """Build the harder tier: segment, product and region figures; changes against two years
    earlier from the SEC XBRL API; and standard ratios with no formula given.

    Reads only cached data; run `fa fresh fetch` first.
    """
    settings = get_settings()
    manifest = FreshManifest.load(settings.fresh_manifest_file)
    with SecClient(settings.sec_user_agent, settings.sec_cache_dir, offline=True) as client:
        try:
            loaded = [_load_filing(client, f) for f in manifest.filings]
        except (IntegrityError, CacheMiss) as exc:
            console.print(f"[red]{exc}[/] Run `fa fresh fetch` first.")
            raise typer.Exit(code=1) from None
    outcomes = hard_questions(loaded, seed=seed, per_filing=per_filing)
    questions = [q for o in outcomes for q in o.chosen]
    save_questions(settings.fresh_hard_questions_file, questions)
    dev_companies = load_split(settings.splits_dir / "financebench_v1.json").dev_companies
    split = company_split(questions, dev_companies, name=HARD_SPLIT_NAME)
    split_path = settings.splits_dir / f"{HARD_SPLIT_NAME}.json"
    save_split(split, questions, split_path)
    sheet_path = settings.results_dir / "fresh" / "review_sheet_hard.md"
    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    sample = review_sample(questions, review, seed)
    filings = {f.doc_name: f for f in manifest.filings}
    sheet_path.write_text(review_sheet(sample, filings, split) + "\n", encoding="utf-8")

    table = Table(title="Fresh questions, hard tier", title_justify="left")
    for header in ("kind", "questions", "XBRL API verified", "not in API"):
        table.add_column(header, justify="left" if header == "kind" else "right")
    for kind in HARD_KINDS:
        qs = [q for q in questions if (q.meta or {}).get("kind") == kind]
        api = Counter((q.meta or {}).get("xbrl_api") for q in qs)
        table.add_row(kind, str(len(qs)), str(api["verified"]), str(api["not_in_api"]))
    console.print(table)
    if mismatched := {o.doc_name: o.mismatches for o in outcomes if o.mismatches}:
        console.print(f"[yellow]Candidates dropped (figure differs from the API):[/] {mismatched}")
    console.print(
        f"{len(questions)} questions -> {settings.fresh_hard_questions_file}\n"
        f"split {split.name}: dev {len(split.dev)} ({len(split.dev_companies)} companies), "
        f"test {len(split.test)} ({len(split.test_companies)} companies) -> {split_path}\n"
        f"review sheet ({len(sample)} questions) -> {sheet_path}"
    )
