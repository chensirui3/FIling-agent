"""Run an experiment over a question set with repetitions, recording everything.

Layout of a run directory (results/runs/<run_id>/):

- config.json: the experiment config, its hash, split, question ids, repetitions, versions
- results.jsonl: one row per (question, rep) that produced a gradable outcome
- errors.jsonl: infrastructure failures (API errors, timeouts, a different served model,
  exceptions in the harness). These are never graded, and a resumed run retries them.
- traces/<question>__r<rep>.json: the full agent trace (kept local, not committed)

Re-running with the same run directory resumes: completed (question, rep) pairs are skipped.
A cost cap stops scheduling new work once recorded spend reaches it (in-flight work finishes,
so the overshoot is at most one question per worker).
"""

from __future__ import annotations

import json
import threading
import traceback
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from filing_agent.agent.answer import FinalAnswer
from filing_agent.agent.trace import INFRA_FAILURES, AgentRun
from filing_agent.data.financebench import Question

CONFIG_FILE = "config.json"
RESULTS_FILE = "results.jsonl"
ERRORS_FILE = "errors.jsonl"
TRACES_DIR = "traces"


class ResultRow(BaseModel):
    question_id: str
    rep: int
    status: str
    answer: FinalAnswer | None
    final_text: str | None
    error: str | None
    cost_usd: float | None
    tokens: dict[str, int]
    model_calls: int
    tool_calls: int
    tool_errors: int
    internal_tool_errors: int
    served_models: list[str]
    wall_s: float
    started_at: str


class ErrorRow(BaseModel):
    question_id: str
    rep: int
    failure: str  # api_error, timeout, model_mismatch, or exception
    error: str | None
    cost_usd: float | None
    started_at: str


Execute = Callable[[Question, int], AgentRun]


@dataclass
class RunSummary:
    run_dir: Path
    planned: int
    skipped_done: int
    completed: int = 0
    infra_failures: int = 0
    cost_usd: float = 0.0
    stopped_by_cost_cap: bool = False
    statuses: Counter[str] = field(default_factory=Counter)


def trace_path(run_dir: Path, question_id: str, rep: int) -> Path:
    return run_dir / TRACES_DIR / f"{question_id}__r{rep}.json"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_results(run_dir: Path) -> list[ResultRow]:
    return [ResultRow.model_validate(r) for r in read_jsonl(run_dir / RESULTS_FILE)]


def _row(question: Question, rep: int, run: AgentRun) -> ResultRow:
    return ResultRow(
        question_id=question.id,
        rep=rep,
        status=run.status,
        answer=run.answer,
        final_text=run.final_text,
        error=run.error,
        cost_usd=run.cost_usd,
        tokens=run.tokens,
        model_calls=len(run.model_calls),
        tool_calls=len(run.tool_calls),
        tool_errors=sum(1 for c in run.tool_calls if c.is_error),
        internal_tool_errors=sum(1 for c in run.tool_calls if c.internal_error),
        served_models=sorted({c.model_served for c in run.model_calls}),
        wall_s=run.wall_s,
        started_at=run.started_at,
    )


def run_experiment(
    questions: Sequence[Question],
    *,
    reps: int,
    run_dir: Path,
    execute: Execute,
    concurrency: int = 4,
    max_cost: float | None = None,
    on_done: Callable[[str, int, str, float], None] | None = None,
) -> RunSummary:
    """Run `execute` for every (question, rep) not already recorded in `run_dir`."""
    (run_dir / TRACES_DIR).mkdir(parents=True, exist_ok=True)
    done = {(r["question_id"], r["rep"]) for r in read_jsonl(run_dir / RESULTS_FILE)}
    jobs = [
        (q, rep)
        for q in sorted(questions, key=lambda q: q.id)
        for rep in range(1, reps + 1)
        if (q.id, rep) not in done
    ]
    prior_cost = sum(
        row.get("cost_usd") or 0.0
        for name in (RESULTS_FILE, ERRORS_FILE)
        for row in read_jsonl(run_dir / name)
    )
    summary = RunSummary(
        run_dir=run_dir,
        planned=len(questions) * reps,
        skipped_done=len(done),
        cost_usd=prior_cost,
    )
    lock = threading.Lock()

    def append(name: str, row: BaseModel) -> None:
        with (run_dir / name).open("a", encoding="utf-8") as handle:
            handle.write(row.model_dump_json() + "\n")

    def task(question: Question, rep: int) -> None:
        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            run = execute(question, rep)
        except Exception as exc:  # a harness bug: recorded, never graded, retried on resume
            error = ErrorRow(
                question_id=question.id,
                rep=rep,
                failure="exception",
                error="".join(traceback.format_exception_only(exc)).strip(),
                cost_usd=None,
                started_at=started_at,
            )
            with lock:
                append(ERRORS_FILE, error)
                summary.infra_failures += 1
                summary.statuses["exception"] += 1
            if on_done:
                on_done(question.id, rep, "exception", 0.0)
            return
        trace_path(run_dir, question.id, rep).write_text(run.model_dump_json(), encoding="utf-8")
        cost = run.cost_usd or 0.0
        with lock:
            if run.status in INFRA_FAILURES:
                append(
                    ERRORS_FILE,
                    ErrorRow(
                        question_id=question.id,
                        rep=rep,
                        failure=run.status,
                        error=run.error,
                        cost_usd=run.cost_usd,
                        started_at=run.started_at,
                    ),
                )
                summary.infra_failures += 1
            else:
                append(RESULTS_FILE, _row(question, rep, run))
                summary.completed += 1
            summary.statuses[run.status] += 1
            summary.cost_usd += cost
        if on_done:
            on_done(question.id, rep, run.status, cost)

    pending: set[Future[None]] = set()
    remaining = iter(jobs)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        while True:
            while len(pending) < max(1, concurrency) and not summary.stopped_by_cost_cap:
                job = next(remaining, None)
                if job is None:
                    break
                with lock:
                    over_budget = max_cost is not None and summary.cost_usd >= max_cost
                if over_budget:  # only reported when work was actually left unstarted
                    summary.stopped_by_cost_cap = True
                    break
                pending.add(pool.submit(task, *job))
            if not pending:
                break
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                future.result()
    return summary
