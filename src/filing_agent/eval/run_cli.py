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
from filing_agent.eval import batch as bm
from filing_agent.eval.context import build_context
from filing_agent.eval.datasets import load_dataset, split_path
from filing_agent.eval.experiment import ExperimentConfig, load_experiment
from filing_agent.eval.runner import CONFIG_FILE, Execute, run_experiment
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
    batch: bool = typer.Option(
        False, "--batch", help="Use the Batch API: half price, configs without tools only."
    ),
    wait: bool = typer.Option(
        True, "--wait/--no-wait", help="With --batch: wait for results, or collect on --resume."
    ),
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
    if batch:
        if exp.tools:
            console.print("[red]--batch needs a config without tools; a batch cannot run them.[/]")
            raise typer.Exit(code=1)
        low, high = low * bm.BATCH_DISCOUNT, high * bm.BATCH_DISCOUNT
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

    def agent_for(question: fb.Question, api: object, cost_factor: float = 1.0) -> Agent:
        context_tools = ToolContext(
            registry=registry,
            parsed_dir=settings.parsed_dir,
            index_loader=lambda: _require(index),
            sec_loader=(lambda: _require(sec)) if sec is not None else None,
            allowed_docs=frozenset({question.doc_name}) if exp.corpus == "gold_filing" else None,
        )
        return Agent(
            api,
            build_tools(context_tools, exp.tools),
            exp.agent_config(),
            cost_factor=cost_factor,
        )

    def context_for(question: fb.Question) -> str | None:
        return build_context(
            exp.context, question, load_doc=load_doc, index=index, k=exp.retrieved_k
        )

    def run_live(question: fb.Question, _rep: int) -> AgentRun:
        return agent_for(question, client).run(question.question, context=context_for(question))

    execute: Execute = run_live
    state: bm.BatchState | None = None
    if batch:
        state = bm.load_state(run_dir)
        if state is None or state.collected:
            jobs = bm.pending_jobs(questions, reps, run_dir)
            if not jobs:
                console.print("Nothing left to run.")
                return
            if high * len(jobs) > cap:  # a submitted batch cannot be stopped by the cap
                console.print(
                    f"[red]The rough upper cost, ${high * len(jobs):.2f}, is over the cap "
                    f"(${cap:.2f}). Raise --max-cost to submit this batch.[/]"
                )
                raise typer.Exit(code=1)
            requests = [
                ((q.id, r), bm.batch_params(agent_for(q, client), q.question, context_for(q)))
                for q, r in jobs
            ]
            state = bm.submit(client, run_dir, requests)
            console.print(f"Submitted batch {state.batch_id} with {len(requests)} requests.")
        if not wait:
            console.print(
                f"Collect the results later with: fa eval run {config} --batch --resume {run_id}"
            )
            return
        console.print(f"Waiting for batch {state.batch_id} (safe to interrupt and --resume).")
        bm.wait_until_ended(
            client,
            state.batch_id,
            on_poll=lambda b: console.print(
                f"[dim]{b.processing_status}: {b.request_counts.processing} processing, "
                f"{b.request_counts.succeeded} succeeded, {b.request_counts.errored} errored[/]"
            ),
        )
        answers = bm.collect(client, state)
        in_batch = {question_id for question_id, _ in state.jobs.values()}
        questions = [q for q in questions if q.id in in_batch]
        runs = len(state.jobs)

        def run_replayed(question: fb.Question, rep: int) -> AgentRun:
            replay = bm.ReplayClient(bm.message_for(answers, question.id, rep))
            agent = agent_for(question, replay, cost_factor=bm.BATCH_DISCOUNT)
            return agent.run(question.question, context=context_for(question))

        execute = run_replayed

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
            concurrency=1 if batch else concurrency,
            max_cost=None if batch else cap,
            on_done=on_done,
        )

    if state is not None:
        bm.save_state(run_dir, state.model_copy(update={"collected": True}))
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
