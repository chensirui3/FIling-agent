from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from filing_agent.agent.loop import Agent, AgentConfig
from filing_agent.agent.pricing import cost_usd
from filing_agent.agent.trace import AgentRun
from filing_agent.data.financebench import Question
from filing_agent.eval import batch as bm
from filing_agent.eval.runner import ERRORS_FILE, load_results, read_jsonl, run_experiment
from filing_agent.tools.base import Tool, ToolRegistry
from filing_agent.tools.filing_tools import CalculateInput, _calculate
from tests.factories import question
from tests.test_agent import MODEL, USAGE, _message, answer

QUESTIONS = [question(f"q{i}", "Acme", "ACME_2018_10K") for i in range(3)]


class FakeBatches:
    """The three Batches API calls the run uses, with results returned out of order."""

    def __init__(self, results: dict[str, Any]) -> None:
        self.results_by_id = results
        self.created: list[dict[str, Any]] = []
        self.polls = 0

    def create(self, *, requests: list[dict[str, Any]]) -> Any:
        self.created = requests
        return SimpleNamespace(id="batch_1", processing_status="in_progress")

    def retrieve(self, batch_id: str) -> Any:
        assert batch_id == "batch_1"
        self.polls += 1
        return SimpleNamespace(processing_status="ended" if self.polls > 1 else "in_progress")

    def results(self, batch_id: str) -> Any:
        assert batch_id == "batch_1"
        for custom_id in reversed(list(self.results_by_id)):
            yield SimpleNamespace(custom_id=custom_id, result=self.results_by_id[custom_id])


def _client(results: dict[str, Any]) -> Any:
    batches = FakeBatches(results)
    return SimpleNamespace(messages=SimpleNamespace(batches=batches)), batches


def _agent(client: Any, cost_factor: float = 1.0) -> Agent:
    return Agent(client, ToolRegistry([]), AgentConfig(model=MODEL), cost_factor=cost_factor)


def test_batch_request_is_the_live_request_without_caching() -> None:
    agent = _agent(None)
    params = bm.batch_params(agent, "What was capex?", "Page text.")
    live = agent.request_params([{"role": "user", "content": "Page text.\n\nWhat was capex?"}])
    assert "cache_control" in live and "cache_control" not in params
    assert {k: v for k, v in live.items() if k != "cache_control"} == params
    assert "tools" not in params

    with_tools = Agent(
        None, ToolRegistry([Tool("calculate", "Arithmetic.", CalculateInput, _calculate)])
    )
    with pytest.raises(ValueError, match="without tools"):
        bm.batch_params(with_tools, "q", None)


def test_batch_run_records_answers_at_half_price_and_failures_apart(tmp_path: Path) -> None:
    succeeded = SimpleNamespace(type="succeeded", message=_message([answer()], "end_turn"))
    errored = SimpleNamespace(type="errored", error="overloaded_error")
    client, batches = _client({"job-00000": succeeded, "job-00001": errored})  # q2 has no result

    jobs = bm.pending_jobs(QUESTIONS, 1, tmp_path)
    assert [(q.id, rep) for q, rep in jobs] == [("q0", 1), ("q1", 1), ("q2", 1)]
    requests = [((q.id, rep), bm.batch_params(_agent(client), q.question, None)) for q, rep in jobs]
    state = bm.submit(client, tmp_path, requests)
    assert [r["custom_id"] for r in batches.created] == ["job-00000", "job-00001", "job-00002"]
    assert batches.created[0]["params"]["messages"][0]["content"] == QUESTIONS[0].question
    assert bm.load_state(tmp_path) == state and not state.collected  # saved before waiting

    naps: list[float] = []
    bm.wait_until_ended(client, state.batch_id, poll_s=7, sleep=naps.append)
    assert naps == [7] and batches.polls == 2
    answers = bm.collect(client, state)

    def execute(q: Question, rep: int) -> AgentRun:
        replay = bm.ReplayClient(bm.message_for(answers, q.id, rep))
        return _agent(replay, cost_factor=bm.BATCH_DISCOUNT).run(q.question)

    summary = run_experiment(QUESTIONS, reps=1, run_dir=tmp_path, execute=execute, concurrency=1)
    (row,) = load_results(tmp_path)
    full_price = cost_usd(
        MODEL,
        input_tokens=USAGE["input_tokens"],
        output_tokens=USAGE["output_tokens"],
        cache_read_tokens=USAGE["cache_read_input_tokens"],
        cache_write_tokens=USAGE["cache_creation_input_tokens"],
    )
    assert full_price is not None
    assert row.question_id == "q0" and row.status == "answered" and row.answer is not None
    assert row.cost_usd == pytest.approx(full_price / 2)
    errors = {e["question_id"]: e["error"] for e in read_jsonl(tmp_path / ERRORS_FILE)}
    assert "batch request errored: overloaded_error" in errors["q1"]
    assert "no result for this question" in errors["q2"]
    assert summary.completed == 1 and summary.infra_failures == 2
    # a resumed run would batch only what is still missing
    assert [(q.id, rep) for q, rep in bm.pending_jobs(QUESTIONS, 1, tmp_path)] == [
        ("q1", 1),
        ("q2", 1),
    ]


def test_replay_client_answers_once() -> None:
    replay = bm.ReplayClient("message")
    assert replay.messages.create(model=MODEL) == "message"
    with pytest.raises(RuntimeError, match="single-shot"):
        replay.messages.create(model=MODEL)
