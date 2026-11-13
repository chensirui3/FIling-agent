"""`fa eval ...` commands."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import cast

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from filing_agent.config import get_settings
from filing_agent.corpus.commands import open_index
from filing_agent.corpus.index import MODES, Mode
from filing_agent.corpus.parse import load_parsed
from filing_agent.data import financebench as fb
from filing_agent.data.splits import load_split
from filing_agent.eval import retrieval as rv
from filing_agent.eval.analysis_cli import (
    compare_command,
    grade_command,
    report_command,
    table_command,
)
from filing_agent.eval.gold import load_overrides, numeric_gold
from filing_agent.eval.grader_check import run_grader_check
from filing_agent.eval.run_cli import run_command

eval_app = typer.Typer(help="Evaluation runs and reports.", no_args_is_help=True)
eval_app.command("run")(run_command)
eval_app.command("grade")(grade_command)
eval_app.command("report")(report_command)
eval_app.command("compare")(compare_command)
eval_app.command("table")(table_command)
console = Console()


@eval_app.command("retrieval")
def retrieval_command(
    indexes: list[str] = typer.Option(
        ["plain", "contextual"], "--index", help="Indexes to compare."
    ),
    modes: str = typer.Option("bm25,dense,hybrid", help="Comma-separated ranking methods."),
    split_name: str = typer.Option("financebench_v1", "--split", help="Split for dev/test rows."),
) -> None:
    """Rank gold evidence pages with the question as the query, in four search settings."""
    settings = get_settings()
    root = settings.financebench_dir
    questions = fb.load_questions(root)
    documents = fb.load_documents(root)
    split = load_split(settings.splits_dir / f"{split_name}.json")
    mode_list: list[Mode] = [cast(Mode, m.strip()) for m in modes.split(",") if m.strip()]
    if unknown := [m for m in mode_list if m not in MODES]:
        raise typer.BadParameter(f"unknown modes: {unknown}")
    cases = rv.build_cases(questions, documents, lambda n: load_parsed(settings.parsed_dir, n))

    rows: list[dict] = []
    metas: list[dict] = []
    for name in indexes:
        index = open_index(name)
        if index.embedder is not None:
            index.embedder = rv.CachingEmbedder(index.embedder)
        usable: list[Mode] = [m for m in mode_list if m == "bm25" or index.embeddings is not None]
        metas.append({"name": name, **index.spec.model_dump(), "chunks": len(index.chunks)})
        with Progress(
            TextColumn(f"[bold]{name}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(name, total=len(rv.SETTINGS) * len(usable) * len(cases))
            rows += rv.run(index, cases, modes=usable, on_search=partial(progress.advance, task))

    summary = rv.summarize(rows, groups={"dev": set(split.dev), "test": set(split.test)})
    out = settings.results_dir / "retrieval" / f"{split_name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "split": split_name,
        "indexes": metas,
        "summary": summary,
        "rows": rows,
    }
    out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")

    table = Table(title="Gold-page retrieval, all 150 questions", title_justify="left")
    for header in (
        "index",
        "setting",
        "mode",
        "hit@1",
        "hit@5",
        "hit@10",
        "hit@20",
        "full@10",
        "MRR",
    ):
        table.add_column(header, justify="right" if header[0] in "hfM" else "left")
    for row in summary:
        if row["part"] != "all":
            continue
        table.add_row(
            row["index"],
            rv.SETTING_LABELS[row["setting"]],
            row["mode"],
            *(f"{row[key]:.0%}" for key in ("hit@1", "hit@5", "hit@10", "hit@20", "full@10")),
            f"{row['mrr']:.3f}",
        )
    console.print(table)
    console.print(f"Per-question ranks and dev/test breakdowns: {out}")


@eval_app.command("grader-check")
def grader_check_command() -> None:
    """Run known-good and known-bad answers through the numeric grader (no API calls)."""
    settings = get_settings()
    questions = fb.load_questions(settings.financebench_dir)
    results = run_grader_check(questions, load_overrides(settings.gold_overrides_file))
    table = Table(title="Numeric grader check (bare-number golds)", title_justify="left")
    for header in ("synthetic answer", "expected", "checked", "failures"):
        table.add_column(header)
    for result in results:
        failed = len(result.failures)
        table.add_row(
            result.probe.name,
            result.probe.expect,
            str(result.checked),
            f"[red]{failed}[/]" if failed else "0",
        )
    console.print(table)
    failures = [f for r in results for f in r.failures]
    for failure in failures[:15]:
        console.print(f"  [red]{failure}[/]")
    if failures:
        raise typer.Exit(code=1)
    console.print("The grader accepts every correct form and rejects every wrong one.")


@eval_app.command("gold-review")
def gold_review_command(
    out: Path | None = typer.Option(
        None, help="CSV path (default results/labels/gold_review.csv)."
    ),
) -> None:
    """Export how each bare-number gold answer is read (value, kind, scale) for human review."""
    settings = get_settings()
    questions = fb.load_questions(settings.financebench_dir)
    overrides = load_overrides(settings.gold_overrides_file)
    path = out or settings.results_dir / "labels" / "gold_review.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for question in questions:
        gold = numeric_gold(question, overrides)
        if gold is None:
            continue
        rows.append(
            {
                "question_id": question.id,
                "question": " ".join(question.question.split())[:240],
                "gold_answer": question.answer,
                "kind": gold.kind,
                "value": gold.value,
                "scale": gold.scale or "",
                "scale_source": gold.scale_source,
                "decimals": gold.decimals,
                "tolerance": round(gold.tolerance, 6),
                "reviewer_ok": "",
                "correction": "",
                "note": "",
            }
        )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:  # BOM: opens cleanly in Excel
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    kinds = Counter(r["kind"] for r in rows)
    assumed = [r["question_id"] for r in rows if r["scale_source"] == "assumed"]
    console.print(f"{len(rows)} bare-number golds -> {path}")
    console.print(f"kinds: {dict(kinds)}; scale assumed (check these first): {len(assumed)}")
    for qid in assumed:
        console.print(f"  [yellow]{qid}[/]")
