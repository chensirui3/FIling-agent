"""`fa eval grade`, `fa eval report` and `fa eval compare`."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from filing_agent.agent.client import CREDENTIALS_HINT, credentials_available, make_client
from filing_agent.config import get_settings
from filing_agent.eval.datasets import all_documents, all_questions
from filing_agent.eval.gold import load_overrides
from filing_agent.eval.grade_run import grade_rows, load_grades, write_grades
from filing_agent.eval.judge import Judge
from filing_agent.eval.report import Comparison, RunMetrics, compare_grades, compute_metrics
from filing_agent.eval.runner import CONFIG_FILE, ERRORS_FILE, load_results, read_jsonl

console = Console()


def _run_dir(run_id: str) -> Path:
    path = get_settings().results_dir / "runs" / run_id
    if not (path / CONFIG_FILE).is_file():
        console.print(f"[red]No run named {run_id} in {path.parent}.[/]")
        raise typer.Exit(code=1)
    return path


def grade_command(
    run_id: str = typer.Argument(..., help="Run to grade (a directory under results/runs/)."),
    judge: bool = typer.Option(
        True, "--judge/--no-judge", help="Use the LLM judge for prose answers."
    ),
    concurrency: int = typer.Option(4, min=1, max=16, help="Judge calls in parallel."),
) -> None:
    """Grade every answer in a run: numeric rules first, then the judge."""
    settings = get_settings()
    run_dir = _run_dir(run_id)
    rows = load_results(run_dir)
    questions = all_questions(settings)
    the_judge = None
    if judge:
        if not credentials_available(settings):
            console.print(f"[yellow]{CREDENTIALS_HINT} Prose answers stay pending.[/]")
        else:
            the_judge = Judge(
                make_client(settings),
                settings.judge_model,
                cache_dir=settings.data_dir / "cache" / "judge",
            )
    grades = grade_rows(
        rows,
        questions,
        judge=the_judge,
        overrides=load_overrides(settings.gold_overrides_file),
        concurrency=concurrency,
    )
    path = write_grades(run_dir, grades)
    labels: dict[str, int] = {}
    for grade in grades:
        key = grade.label or "pending (needs the judge)"
        labels[key] = labels.get(key, 0) + 1
    review = sum(1 for g in grades if g.needs_review)
    console.print(f"{len(grades)} answers graded -> {path}")
    console.print(", ".join(f"{k} {v}" for k, v in sorted(labels.items())))
    if review:
        console.print(
            f"[yellow]{review} judge verdicts disagree with the figure check; review them.[/]"
        )


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def report_command(
    run_id: str = typer.Argument(..., help="Run to report on (graded with `fa eval grade`)."),
) -> None:
    """Accuracy with a 95% interval, breakdowns, cost and latency for one run."""
    _print_metrics(_metrics_for(run_id))


def _metrics_for(run_id: str) -> RunMetrics:
    """Recompute a run's metrics from its grades and save them as metrics.json."""
    settings = get_settings()
    run_dir = _run_dir(run_id)
    config = json.loads((run_dir / CONFIG_FILE).read_text(encoding="utf-8"))
    grades = load_grades(run_dir)
    if not grades:
        console.print(f"[red]{run_id} has no grades yet. Run `fa eval grade {run_id}` first.[/]")
        raise typer.Exit(code=1)
    metrics = compute_metrics(
        run_id,
        config["experiment"]["name"],
        load_results(run_dir),
        grades,
        read_jsonl(run_dir / ERRORS_FILE),
        all_questions(settings),
        all_documents(settings),
    )
    (run_dir / "metrics.json").write_text(metrics.model_dump_json(indent=2), encoding="utf-8")
    return metrics


def _print_metrics(m: RunMetrics) -> None:
    ci = f" (95% CI {m.accuracy_ci95[0]:.1%}-{m.accuracy_ci95[1]:.1%})" if m.accuracy_ci95 else ""
    console.print(
        f"[bold]{m.experiment}[/] {m.run_id}: accuracy [bold]{_pct(m.accuracy)}[/]{ci} on "
        f"{m.graded} graded answers from {m.questions} questions"
    )
    console.print(
        "labels: "
        + ", ".join(f"{k} {v}" for k, v in m.labels.items())
        + f"; pending {m.pending_judge}; needs review {m.needs_review}; "
        f"infrastructure failures {m.infra_failures}"
    )
    if len(m.accuracy_per_rep) > 1:
        console.print(
            "per repetition: " + ", ".join(f"{_pct(v)}" for v in m.accuracy_per_rep.values())
        )
    for title, groups in (
        ("By question type", m.by_question_type),
        ("By kind (fresh set)", m.by_kind),
        ("By filing type", m.by_doc_type),
        ("By grading method", m.by_method),
    ):
        table = Table(title=title, title_justify="left")
        for header in ("group", "n", "correct", "accuracy"):
            table.add_column(header)
        for name, g in groups.items():
            table.add_row(name, str(g.n), str(g.correct), _pct(g.accuracy))
        console.print(table)
    n = int(m.numeric_sensitivity["n"])
    if n:
        s = m.numeric_sensitivity
        console.print(
            f"numeric answers (n={n}): strict {_pct(s['strict'])}, default {_pct(s['default'])}, "
            f"lenient {_pct(s['lenient'])}"
        )
    cost, wall, tools = m.cost_usd, m.wall_s, m.tool_calls
    console.print(
        f"cost: agent ${cost['agent_total']:.2f} (${cost['agent_per_row']:.3f}/answer), "
        f"judge ${cost['judge_total']:.2f}; latency mean {wall['mean']:.1f}s, "
        f"p90 {wall['p90']:.1f}s; tool calls/answer {tools['mean']:.1f}, "
        f"tool errors {int(tools['errors'])} (internal {int(tools['internal_errors'])})"
    )


def compare_command(
    run_a: str = typer.Argument(..., help="Baseline run."),
    run_b: str = typer.Argument(..., help="Changed run."),
) -> None:
    """Paired comparison on shared questions: change with 95% CI, McNemar p, fixed and broken."""
    settings = get_settings()
    result = compare_grades(
        run_a, load_grades(_run_dir(run_a)), run_b, load_grades(_run_dir(run_b))
    )
    out = settings.results_dir / "compare" / f"{run_a}__vs__{run_b}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    _print_comparison(result)
    console.print(f"-> {out}")


def _short_ids(ids: list[str]) -> str:
    return ", ".join(q.removeprefix("financebench_id_") for q in ids) or "-"


def _print_comparison(c: Comparison) -> None:
    low, high = c.delta_ci95
    console.print(
        f"{c.questions} shared questions: {_pct(c.accuracy_a)} -> {_pct(c.accuracy_b)}, "
        f"change [bold]{c.delta:+.1%}[/] (95% CI {low:+.1%} to {high:+.1%}), "
        f"McNemar p = {c.mcnemar_p:.3f}"
    )
    console.print(f"fixed {len(c.fixed)}: {_short_ids(c.fixed)}")
    console.print(f"broken {len(c.broken)}: {_short_ids(c.broken)}")


TABLE_COLUMNS = (
    "experiment",
    "n",
    "accuracy",
    "95% CI",
    "refused",
    "no answer",
    "review",
    "$/answer",
    "latency",
    "tool calls",
)


def _table_row(m: RunMetrics) -> list[str]:
    graded = max(m.graded, 1)
    ci = f"{m.accuracy_ci95[0]:.0%}–{m.accuracy_ci95[1]:.0%}" if m.accuracy_ci95 else "-"
    return [
        m.experiment,
        str(m.graded),
        _pct(m.accuracy),
        ci,
        f"{m.labels['refusal'] / graded:.0%}",
        f"{m.labels['no_answer'] / graded:.0%}",
        str(m.needs_review),
        f"${m.cost_usd['agent_per_row']:.3f}",
        f"{m.wall_s['mean']:.0f}s",
        f"{m.tool_calls['mean']:.1f}",
    ]


def table_command(
    run_ids: list[str] = typer.Argument(..., help="Graded runs to put side by side."),
    markdown: bool = typer.Option(False, "--markdown", help="Print a Markdown table for docs."),
) -> None:
    """Several runs side by side: accuracy with 95% CI, refusals, cost, latency, tool calls."""
    rows = [_table_row(_metrics_for(run_id)) for run_id in run_ids]
    if markdown:
        console.print("| " + " | ".join(TABLE_COLUMNS) + " |", soft_wrap=True)
        console.print("|" + "|".join("---" for _ in TABLE_COLUMNS) + "|", soft_wrap=True)
        for row in rows:
            console.print("| " + " | ".join(row) + " |", soft_wrap=True)
        return
    table = Table(title="Runs", title_justify="left")
    for header in TABLE_COLUMNS:
        table.add_column(header)
    for row in rows:
        table.add_row(*row)
    console.print(table)
