"""`fa index ...` commands: build a search index over parsed filings, and query it."""

from __future__ import annotations

import time
from enum import StrEnum
from typing import cast

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from filing_agent.config import get_settings
from filing_agent.corpus.chunk import DocInfo
from filing_agent.corpus.index import (
    DEFAULT_EMBEDDING_MODEL,
    FastEmbedder,
    FilingIndex,
    Filters,
    IndexSpec,
    Mode,
)
from filing_agent.corpus.parse import load_parse_failures, load_parsed, parsed_path
from filing_agent.data import financebench as fb
from filing_agent.fresh.filings import FreshManifest

index_app = typer.Typer(
    help="Search index over parsed filings: build and query.", no_args_is_help=True
)
console = Console()


class CorpusChoice(StrEnum):
    all = "all"
    needed = "needed"


class ModeChoice(StrEnum):
    bm25 = "bm25"
    dense = "dense"
    hybrid = "hybrid"


def doc_infos(choice: CorpusChoice) -> dict[str, DocInfo]:
    """Filings with FinanceBench metadata (optionally only those the questions use)."""
    root = get_settings().financebench_dir
    documents = fb.load_documents(root)
    names = set(documents)
    if choice is CorpusChoice.needed:
        names &= {q.doc_name for q in fb.load_questions(root)}
    return {
        n: DocInfo(doc_name=n, company=d.company, doc_type=d.doc_type, doc_period=d.doc_period)
        for n, d in documents.items()
        if n in names
    }


def open_index(name: str) -> FilingIndex:
    settings = get_settings()
    index = FilingIndex.load(settings.index_dir / name)
    if index.embeddings is not None and index.spec.embedding_model:
        index.embedder = FastEmbedder(index.spec.embedding_model, settings.models_dir)
    return index


@index_app.command("build")
def build_command(
    name: str = typer.Option("contextual", help="Index name (a directory under data/index/)."),
    headers: bool = typer.Option(
        True,
        "--headers/--no-headers",
        help="Prefix each chunk with company, filing, period, statement and units line.",
    ),
    dense: bool = typer.Option(
        False,
        "--dense/--no-dense",
        help="Also embed chunks for dense and hybrid search (bge-small on CPU: ~10 chunks/s).",
    ),
    corpus: CorpusChoice = typer.Option(
        CorpusChoice.all, help="All 360 filings with metadata, or only the 84 the questions use."
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh/--no-fresh",
        help="Also index the post-cutoff filings pinned by `fa fresh select`.",
    ),
) -> None:
    """Chunk the parsed filings and build BM25 (and optionally dense) search over them."""
    settings = get_settings()
    infos = doc_infos(corpus)
    if fresh:
        infos |= FreshManifest.load(settings.fresh_manifest_file).doc_infos()
    missing = [n for n in infos if not parsed_path(settings.parsed_dir, n).is_file()]
    failures = load_parse_failures(settings.parsed_dir)
    never_parsed = [n for n in missing if n not in failures]
    if never_parsed:
        console.print(
            f"[red]{len(never_parsed)} filings are not parsed[/] (e.g. {never_parsed[0]}). "
            "Run `fa data parse --docs all` (and `fa fresh fetch` for fresh filings) first."
        )
        raise typer.Exit(code=1)
    if unreadable := [n for n in missing if n in failures]:
        console.print(
            f"[yellow]Skipping {len(unreadable)} filings no extractor can read:[/] "
            + ", ".join(unreadable)
        )
        infos = {n: info for n, info in infos.items() if n not in unreadable}

    spec = IndexSpec(
        name=name, with_headers=headers, embedding_model=DEFAULT_EMBEDDING_MODEL if dense else None
    )
    embedder = FastEmbedder(DEFAULT_EMBEDDING_MODEL, settings.models_dir) if dense else None
    started = time.perf_counter()
    with Progress(
        TextColumn("[bold]Embedding"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("embed", total=None, visible=dense)
        index = FilingIndex.build(
            spec,
            infos,
            (load_parsed(settings.parsed_dir, n) for n in sorted(infos)),
            embedder=embedder,
            on_chunked=lambda n: progress.update(task, total=n),
            on_progress=lambda n: progress.advance(task, n),
        )
    out_dir = settings.index_dir / name
    index.save(out_dir)
    dims = "none" if index.embeddings is None else str(index.embeddings.shape[1])
    console.print(
        f"Index [bold]{name}[/]: {len(index.docs)} filings, {len(index.chunks)} chunks, "
        f"headers {'on' if headers else 'off'}, embedding dims {dims}, "
        f"{time.perf_counter() - started:.0f}s -> {out_dir}"
    )


@index_app.command("search")
def search_command(
    query: str = typer.Argument(..., help="Search text."),
    name: str = typer.Option("contextual", "--index", help="Index name."),
    company: list[str] | None = typer.Option(None, help="Only these companies (repeatable)."),
    doc: list[str] | None = typer.Option(None, help="Only these filings (repeatable)."),
    year: list[int] | None = typer.Option(None, help="Only these fiscal periods (repeatable)."),
    mode: ModeChoice = typer.Option(
        ModeChoice.bm25, help="Ranking method (dense/hybrid need an index built with --dense)."
    ),
    k: int = typer.Option(5, "-k", "--top-k", min=1, max=50, help="Pages to return."),
) -> None:
    """Query an index from the command line (useful for spot checks)."""
    index = open_index(name)
    filters = Filters(
        companies=frozenset(company) if company else None,
        doc_names=frozenset(doc) if doc else None,
        periods=frozenset(year) if year else None,
    )
    hits = index.search(query, mode=cast(Mode, mode.value), top_k=k, filters=filters)
    table = Table(title=f"{mode.value} search in {name}", title_justify="left")
    for header in ("#", "filing", "page", "score", "snippet"):
        table.add_column(header)
    for rank, hit in enumerate(hits, start=1):
        snippet = " ".join(hit.text.split())[:110]
        table.add_row(str(rank), hit.doc_name, str(hit.page), f"{hit.score:.4f}", snippet)
    console.print(table)
