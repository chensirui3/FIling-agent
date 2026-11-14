"""Batch mode for experiments without tools: every question in one Message Batch, half price.

A config with no tools needs exactly one request per question (closed book, long context,
classic RAG), so the whole run fits the Batches API, which bills every token at 50%. Agent
configs cannot use it: a batch request is single-shot and cannot answer tool calls.

How a batch run stays identical to a live one:

- each request is the one `Agent` would send (`Agent.request_params`), minus `cache_control`:
  a single-shot prompt is never read back, so caching it would only add the write premium;
- each result is replayed through `Agent.run` with a client that returns the stored message,
  so status handling, answer parsing, traces and result rows come from the same code;
- cost is recorded from the returned token counts at half the list price.

The batch id and its job map are saved in the run directory before waiting, so a run that is
interrupted (or started with `--no-wait`) collects its results on `--resume`. Latency is not
measured: a replayed answer has no meaningful wall-clock time.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from filing_agent.agent.loop import Agent, user_text
from filing_agent.data.financebench import Question
from filing_agent.eval.runner import RESULTS_FILE, read_jsonl

BATCH_FILE = "batch.json"
BATCH_DISCOUNT = 0.5
Job = tuple[str, int]  # (question id, rep)


class BatchState(BaseModel):
    batch_id: str
    submitted_at: str
    jobs: dict[str, Job]  # custom id -> job; question ids can exceed the 64-character limit
    collected: bool = False


class BatchItemError(RuntimeError):
    """One request of the batch did not produce a message (never graded, retried on resume)."""


def load_state(run_dir: Path) -> BatchState | None:
    path = run_dir / BATCH_FILE
    return (
        BatchState.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None
    )


def save_state(run_dir: Path, state: BatchState) -> None:
    (run_dir / BATCH_FILE).write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")


def pending_jobs(
    questions: Sequence[Question], reps: int, run_dir: Path
) -> list[tuple[Question, int]]:
    done = {(r["question_id"], r["rep"]) for r in read_jsonl(run_dir / RESULTS_FILE)}
    return [
        (q, rep)
        for q in sorted(questions, key=lambda q: q.id)
        for rep in range(1, reps + 1)
        if (q.id, rep) not in done
    ]


def batch_params(agent: Agent, question: str, context: str | None) -> dict[str, Any]:
    if agent.tool_definitions:
        raise ValueError("batch mode needs an experiment without tools: a batch cannot run them")
    if agent.config.fallbacks:
        raise ValueError("batch requests cannot use refusal fallbacks")
    params = agent.request_params([{"role": "user", "content": user_text(question, context)}])
    params.pop("cache_control", None)
    return params


def submit(
    client: Any, run_dir: Path, requests: Sequence[tuple[Job, dict[str, Any]]]
) -> BatchState:
    jobs = {f"job-{i:05d}": job for i, (job, _) in enumerate(requests)}
    batch = client.messages.batches.create(
        requests=[
            {"custom_id": custom_id, "params": params}
            for custom_id, (_, params) in zip(jobs, requests, strict=True)
        ]
    )
    state = BatchState(
        batch_id=batch.id,
        submitted_at=datetime.now(UTC).isoformat(timespec="seconds"),
        jobs=jobs,
    )
    save_state(run_dir, state)
    return state


def wait_until_ended(
    client: Any,
    batch_id: str,
    *,
    poll_s: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    on_poll: Callable[[Any], None] | None = None,
) -> None:
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if on_poll:
            on_poll(batch)
        if batch.processing_status == "ended":
            return
        sleep(poll_s)


def collect(client: Any, state: BatchState) -> dict[Job, Any]:
    """job -> its result entry (`.type` is succeeded, errored, canceled or expired)."""
    results: dict[Job, Any] = {}
    for entry in client.messages.batches.results(state.batch_id):
        job = state.jobs.get(entry.custom_id)
        if job is not None:  # results arrive in any order; key by id, never by position
            results[tuple(job)] = entry.result  # type: ignore[index]
    return results


def message_for(results: dict[Job, Any], question_id: str, rep: int) -> Any:
    result = results.get((question_id, rep))
    if result is None:
        raise BatchItemError("no result for this question in the batch")
    if result.type != "succeeded":
        detail = getattr(result, "error", None)
        raise BatchItemError(f"batch request {result.type}" + (f": {detail}" if detail else ""))
    return result.message


class ReplayClient:
    """A client whose only request returns a message a batch already produced."""

    def __init__(self, message: Any) -> None:
        self._message: Any | None = message
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **_params: Any) -> Any:
        if self._message is None:
            raise RuntimeError("a batch answer was asked for twice; batch runs are single-shot")
        message, self._message = self._message, None
        return message
