"""`fa data ...` commands: download, stats, parse, verify, split."""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import cast

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from filing_agent.config import get_settings
from filing_agent.corpus.parse import (
    ParsedDoc,
    default_workers,
    load_parsed,
    parse_corpus,
    record_parse_failures,
)
from filing_agent.data import financebench as fb
from filing_agent.data.splits import make_company_split, save_split, summarize
from filing_agent.data.stats import compute_stats
from filing_agent.data.verify import align_evidence, summarize_alignment

data_app = typer.Typer(help="FinanceBench data: download, inspect and split.", no_args_is_help=True)
console = Console()


class PdfChoice(StrEnum):
    none = "none"
    needed = "needed"
    all = "all"


class DocChoice(StrEnum):
    needed = "needed"
    all = "all"


def _counts_table[K](title: str, counts: Mapping[K, int], key_header: str) -> Table:
    table = Table(title=title, title_justify="left")
    table.add_column(key_header)
    table.add_column("count", justify="right")
    for key, value in counts.items():
        table.add_row(str(key), str(value))
    return table


@data_app.command("download")
def download_command(
    pdfs: PdfChoice = typer.Option(
        PdfChoice.needed,
        help="Filing PDFs to fetch: none, the ones the 150 questions use, or the full corpus.",
    ),
    workers: int = typer.Option(6, min=1, max=16, help="Parallel PDF downloads."),
) -> None:
    """Download FinanceBench at a pinned commit, verifying every file's git blob SHA."""
    settings = get_settings()
    dest = settings.financebench_dir
    with Progress(
        TextColumn("[bold]PDFs"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("pdfs", total=None)
        report = fb.download(
            dest,
            pdfs=cast(fb.PdfSelection, pdfs.value),
            github_token=settings.github_token,
            workers=workers,
            on_listing=lambda n: progress.update(task, total=n),
            on_file=lambda _path, _downloaded: progress.advance(task),
        )
    console.print(f"FinanceBench @ {report.commit[:7]} -> {dest}")
    console.print(
        f"{len(report.downloaded)} downloaded, {len(report.already_current)} already up to date"
    )


@data_app.command("stats")
def stats_command() -> None:
    """Summarize the question set and the document corpus."""
    root = get_settings().financebench_dir
    questions = fb.load_questions(root)
    documents = fb.load_documents(root)
    s = compute_stats(questions, documents)
    console.print(
        f"[bold]{s.n_questions}[/] questions over [bold]{s.n_docs}[/] filings from "
        f"[bold]{s.n_companies}[/] companies (corpus: {s.corpus_docs} filings, "
        f"{s.corpus_companies} companies)"
    )
    console.print(
        f"{s.evidence_items} gold evidence items; {s.multi_page_questions} questions have "
        f"evidence on more than one page"
    )
    for doc_name, rows in fb.duplicate_documents(root).items():
        used = any(q.doc_name == doc_name for q in questions)
        console.print(
            f"[yellow]Note:[/] {doc_name} appears {rows} times in the metadata; the first row is "
            f"used ({'referenced' if used else 'not referenced'} by the questions)."
        )
    console.print(_counts_table("Question types", s.question_types, "type"))
    console.print(_counts_table("Filing type (by question)", s.doc_types, "doc_type"))
    console.print(_counts_table("Fiscal period (by question)", s.periods, "doc_period"))
    console.print(_counts_table("Gold answer shape", s.answer_kinds, "kind"))


@data_app.command("parse")
def parse_command(
    docs: DocChoice = typer.Option(
        DocChoice.needed, help="Parse the filings the questions use, or every downloaded PDF."
    ),
    workers: int = typer.Option(default_workers(), min=1, help="Parallel worker processes."),
    force: bool = typer.Option(False, "--force", help="Re-parse even if outputs are current."),
) -> None:
    """Parse filing PDFs into per-page text (plain and column-aligned) with page tags."""
    settings = get_settings()
    root = settings.financebench_dir
    if docs is DocChoice.needed:
        names = sorted({q.doc_name for q in fb.load_questions(root)})
        pdf_files = {name: fb.pdf_path(root, name) for name in names}
    else:
        pdf_files = {p.stem: p for p in sorted((root / fb.PDF_DIR).glob("*.pdf"))}
    missing = [name for name, path in pdf_files.items() if not path.is_file()]
    if missing or not pdf_files:
        console.print(f"[red]Missing PDFs ({len(missing)}).[/] Run `fa data download` first.")
        raise typer.Exit(code=1)

    with Progress(
        TextColumn("[bold]Parsing"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("parse", total=len(pdf_files))
        outcomes = parse_corpus(
            pdf_files,
            settings.parsed_dir,
            workers=workers,
            force=force,
            on_done=lambda _outcome: progress.advance(task),
        )
    record_parse_failures(settings.parsed_dir, outcomes)
    by_status = {s: [o for o in outcomes if o.status == s] for s in ("parsed", "cached", "failed")}
    pages = sum(o.n_pages for o in outcomes)
    empty = sum(o.empty_pages for o in outcomes)
    console.print(
        f"{len(by_status['parsed'])} parsed, {len(by_status['cached'])} up to date, "
        f"{len(by_status['failed'])} failed; {pages} pages ({empty} with no text layer)"
    )
    for outcome in by_status["failed"]:
        console.print(f"[red]failed[/] {outcome.doc_name}: {outcome.error}")
    for outcome in outcomes:
        if outcome.n_pages and outcome.empty_pages / outcome.n_pages > 0.2:
            console.print(
                f"[yellow]{outcome.doc_name}[/]: {outcome.empty_pages}/{outcome.n_pages} pages "
                "have no text (scanned images?)"
            )
    if by_status["failed"]:
        raise typer.Exit(code=1)


@data_app.command("verify")
def verify_command(
    show: int = typer.Option(30, help="How many off-page evidence items to list."),
) -> None:
    """Check that every gold evidence passage is on its labeled (zero-indexed) page."""
    settings = get_settings()
    questions = fb.load_questions(settings.financebench_dir)

    def load(doc_name: str) -> ParsedDoc | None:
        try:
            return load_parsed(settings.parsed_dir, doc_name)
        except FileNotFoundError:
            return None

    records = align_evidence(questions, load)
    summary = summarize_alignment(records)
    report: Path = settings.parsed_dir / "alignment_report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps({"summary": summary, "records": [r.model_dump() for r in records]}, indent=2)
        + "\n",
        encoding="utf-8",
    )

    status_counts = cast(dict[str, int], summary["status"])
    console.print(_counts_table("Evidence alignment", status_counts, "status"))
    console.print(
        f"on-page rate: [bold]{cast(float, summary['on_page_rate']):.1%}[/] "
        f"of {summary['evidence_items']} evidence items; offsets of misaligned items: "
        f"{summary['offsets'] or 'none'}"
    )
    off = [r for r in records if r.status != "on_page"]
    if off:
        table = Table(title="Not on the labeled page", title_justify="left")
        for header in (
            "question",
            "doc",
            "labeled",
            "best",
            "labeled score",
            "best score",
            "status",
        ):
            table.add_column(header)
        for r in off[:show]:
            table.add_row(
                r.question_id.removeprefix("financebench_id_"),
                r.doc_name,
                str(r.labeled_page),
                "-" if r.best_page is None else str(r.best_page),
                f"{r.labeled_score:.2f}",
                f"{r.best_score:.2f}",
                r.status,
            )
        console.print(table)
    console.print(f"Report: {report}")


@data_app.command("split")
def split_command(
    dev_size: int = typer.Option(50, help="Target number of dev questions."),
    seed: int = typer.Option(13, help="Seed for the company search."),
    name: str = typer.Option("financebench_v1", help="Split name (file name under configs/)."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing split file."),
) -> None:
    """Create a company-grouped dev/test split and save it under configs/splits/."""
    settings = get_settings()
    path = settings.splits_dir / f"{name}.json"
    if path.exists() and not force:
        console.print(f"[red]{path} already exists.[/] Splits must stay fixed; use --force.")
        raise typer.Exit(code=1)
    root = settings.financebench_dir
    questions = fb.load_questions(root)
    documents = fb.load_documents(root)
    split = make_company_split(questions, documents, dev_size=dev_size, seed=seed, name=name)
    summary = summarize(split, questions, documents)
    save_split(split, path, summary, dev_size)

    table = Table(title=f"Split {name} (seed {seed})", title_justify="left")
    for header in ("part", "questions", "companies", "question types", "filings (10k/other)"):
        table.add_column(header)
    for part in ("dev", "test"):
        p = summary[part]
        types = ", ".join(f"{k.split('-')[0]} {v}" for k, v in p["question_type"].items())
        groups = f"{p['filing_group'].get('10k', 0)}/{p['filing_group'].get('other', 0)}"
        table.add_row(part, str(p["questions"]), str(p["companies"]), types, groups)
    console.print(table)
    console.print(f"Saved {path}")
