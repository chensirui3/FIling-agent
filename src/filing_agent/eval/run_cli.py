"""`fa eval run`: run an experiment config over the dev or test questions."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from filing_agent.agent.client import CREDENTIALS_HINT, credentials_available, make_client
from filing_agent.agent.loop import Agent
from filing_agent.agent.prompts import system_prompt
from filing_agent.agent.trace import AgentRun
from filing_agent.config import PROJECT_ROOT, get_settings
from filing_agent.corpus.index import FilingIndex
from filing_agent.corpus.parse import ParsedDoc, load_parsed
from filing_agent.data import financebench as fb
from filing_agent.data.splits import load_split
from filing_agent.eval.context import build_context
from filing_agent.eval.datasets import load_dataset, split_path
from filing_agent.eval.experiment import ExperimentConfig, load_experiment
from filing_agent.eval.runner import CONFIG_FILE, run_experiment
from filing_agent.sec.companies import CompanyRegistry
from filing_agent.sec.http import companyfacts_url
from filing_agent.sec.service import SecService
from filing_agent.tools.filing_tools import TEXT_TOOLS, ToolContext, build_tools

console = Console()


class SplitChoice(StrEnum):
    dev = "dev"
    test = "test"
    all = "all"


def rough_cost_per_question(exp: ExperimentConfig) -> tuple[float, float]:
    """Planning range only. Agent figures are from the first measured runs (Opus 5.5, effort
    medium: $0.04-0.06 per dev question, with prompt caching); recorded cost always wins."""
    relative = {"claude-opus-5-5": 1.0, "claude-sonnet-5-5": 0.5, "claude-haiku-5-5": 0.025}
    scale = relative.get(exp.model, 1.0)
    if exp.context == "full_filing":
        low, high = 0.40, 0.90  # input-dominated: the whole filing is sent
    elif not exp.tools:
        low, high = 0.005, 0.03
    elif exp.context == "oracle_evidence":
        low, high = 0.01, 0.05
    else:
        low, high = 0.03, 0.15
    return low * scale, high * scale


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return out.stdout.strip() or None


def select_questions(
    questions: list[fb.Question],
    split_file: Path,
    part: SplitChoice,
    ids: str | None,
    limit: int | None,
) -> list[fb.Question]:
    if ids:
        wanted = {i.strip() for i in ids.split(",") if i.strip()}
        chosen = [
            q
            for q in questions
            if q.id in wanted or q.id.removeprefix("financebench_id_") in wanted
        ]
    elif part is SplitChoice.all:
        chosen = list(questions)
    else:
        split = load_split(split_file)
        keep = set(split.ids("dev" if part is SplitChoice.dev else "test"))
        chosen = [q for q in questions if q.id in keep]
    return chosen[:limit] if limit else chosen


def run_command(
    config: Path = typer.Argument(
        ..., help="Experiment YAML, e.g. configs/experiments/b3_shared_text.yaml"
    ),
    split: SplitChoice = typer.Option(SplitChoice.dev, help="Which questions: dev, test or all."),
    reps: int = typer.Option(1, min=1, help="Repetitions per question."),
    ids: str | None = typer.Option(None, help="Comma-separated question ids instead of a split."),
    limit: int | None = typer.Option(None, min=1, help="Only the first N questions."),
    concurrency: int = typer.Option(4, min=1, max=16, help="Questions run in parallel."),
    max_cost: float | None = typer.Option(
        None, help="USD cap for this run (default FA_MAX_COST_USD)."
    ),
    resume: str | None = typer.Option(None, help="Run id to resume (skips completed questions)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan and cost range only."),
) -> None:
    """Run an experiment: every selected question, `reps` times, with full traces."""
    settings = get_settings()
    exp = load_experiment(config)
    questions = select_questions(
        load_dataset(exp.dataset, settings),
        split_path(exp.dataset, settings),
        split,
        ids,
        limit,
    )
    if not questions:
        console.print("[red]No questions selected.[/]")
        raise typer.Exit(code=1)
    cap = settings.max_cost_usd if max_cost is None else max_cost
    low, high = rough_cost_per_question(exp)
    runs = len(questions) * reps
    console.print(
        f"[bold]{exp.name}[/] ({exp.config_hash()}) on {exp.dataset}: "
        f"{len(questions)} questions x {reps} reps = "
        f"{runs} runs on {exp.model}, effort {exp.effort}\n"
        f"tools: {', '.join(exp.tools) or 'none'} · corpus: {exp.corpus} · context: {exp.context}\n"
        f"rough cost ${low * runs:.2f}-${high * runs:.2f} (cap ${cap:.2f})"
    )
    if dry_run:
        return
    if not credentials_available(settings):
        console.print(f"[red]{CREDENTIALS_HINT}[/]")
        raise typer.Exit(code=1)

    needs_index = bool(set(exp.tools) & set(TEXT_TOOLS)) or exp.context == "retrieved"
    index = FilingIndex.load(settings.index_dir / exp.index) if needs_index else None
    registry = CompanyRegistry.load(settings.companies_file)
    sec: SecService | None = None
    if exp.uses_xbrl:
        sec = SecService.from_settings(settings, offline=exp.sec_offline)
        uncached = sorted(
            {
                q.company
                for q in questions
                if not sec.client.is_cached(companyfacts_url(registry.resolve(q.company).cik))
            }
        )
        if exp.sec_offline and uncached:
            console.print(
                f"[red]XBRL data is not cached for {len(uncached)} companies[/] "
                f"({', '.join(uncached[:5])}...). Run `fa sec warm` first (needs SEC_USER_AGENT)."
            )
            raise typer.Exit(code=1)

    run_id = resume or f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{exp.name}-{exp.config_hash()}"
    run_dir = settings.results_dir / "runs" / run_id
    config_path = run_dir / CONFIG_FILE
    if config_path.is_file():
        recorded = json.loads(config_path.read_text(encoding="utf-8"))
        if recorded["config_hash"] != exp.config_hash():
            console.print(f"[red]{run_id} was run with a different config; start a new run.[/]")
            raise typer.Exit(code=1)
    else:
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt = system_prompt(exp.tools, exp.prompt_version)
        record = {
            "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "experiment": exp.model_dump(mode="json"),
            "config_hash": exp.config_hash(),
            "dataset": exp.dataset,
            "split": split.value if not ids else "ids",
            "question_ids": [q.id for q in questions],
            "reps": reps,
            "max_cost_usd": cap,
            "git_commit": _git_commit(),
            "versions": {
                "filing-agent": version("filing-agent"),
                "anthropic": version("anthropic"),
            },
            "system_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        }
        config_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    client = make_client(settings)

    def load_doc(name: str) -> ParsedDoc:
        return load_parsed(settings.parsed_dir, name)

    def execute(question: fb.Question, _rep: int) -> AgentRun:
        context_tools = ToolContext(
            registry=registry,
            parsed_dir=settings.parsed_dir,
            index_loader=lambda: _require(index),
            sec_loader=(lambda: _require(sec)) if sec is not None else None,
            allowed_docs=frozenset({question.doc_name}) if exp.corpus == "gold_filing" else None,
        )
        agent = Agent(client, build_tools(context_tools, exp.tools), exp.agent_config())
        context = build_context(
            exp.context, question, load_doc=load_doc, index=index, k=exp.retrieved_k
        )
        return agent.run(question.question, context=context)

    with Progress(
        TextColumn("[bold]Running"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("{task.fields[cost]}"),
        console=console,
    ) as progress:
        task = progress.add_task("run", total=runs, cost="$0.00")
        spent = [0.0]

        def on_done(_qid: str, _rep: int, _status: str, cost: float) -> None:
            spent[0] += cost
            progress.update(task, advance=1, cost=f"${spent[0]:.2f}")

        summary = run_experiment(
            questions,
            reps=reps,
            run_dir=run_dir,
            execute=execute,
            concurrency=concurrency,
            max_cost=cap,
            on_done=on_done,
        )

    table = Table(title=f"Run {run_id}", title_justify="left")
    table.add_column("status")
    table.add_column("count", justify="right")
    for status, count in summary.statuses.most_common():
        table.add_row(status, str(count))
    console.print(table)
    not_run = summary.planned - summary.skipped_done - summary.completed - summary.infra_failures
    console.print(
        f"{summary.completed} recorded, {summary.infra_failures} infrastructure failures "
        f"(retried on --resume {run_id}), {max(0, not_run)} not run"
        + (" (cost cap reached)" if summary.stopped_by_cost_cap else "")
        + f"; total spend ${summary.cost_usd:.2f}\n-> {run_dir}"
    )


def _require[T](value: T | None) -> T:
    if value is None:
        raise RuntimeError("a resource this experiment needs was not loaded")
    return value
