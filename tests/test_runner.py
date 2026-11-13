from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from filing_agent.agent.answer import FinalAnswer
from filing_agent.agent.trace import AgentRun, ModelCall
from filing_agent.config import PROJECT_ROOT
from filing_agent.data.financebench import Question
from filing_agent.eval.context import build_context
from filing_agent.eval.experiment import ExperimentConfig, load_experiment
from filing_agent.eval.runner import (
    ERRORS_FILE,
    RESULTS_FILE,
    load_results,
    read_jsonl,
    run_experiment,
    trace_path,
)
from tests.search_fixtures import build_index, parsed

ANSWER = FinalAnswer.model_validate(
    {
        "answer_status": "answered",
        "answer": "x",
        "value": 1.0,
        "unit": "ratio",
        "scale": None,
        "period": None,
        "calculation": None,
        "sources": [],
        "confidence": "high",
    }
)


def _question(qid: str, doc: str = "ACME_2018_10K", pages: tuple[int, ...] = (1,)) -> Question:
    return Question.model_validate(
        {
            "id": qid,
            "company": "Acme",
            "doc_name": doc,
            "question_type": "metrics-generated",
            "question": "What is Acme's FY2018 purchases of property plant and equipment?",
            "answer": "$1577.00",
            "evidence": [
                {"doc_name": doc, "page": p, "text": "PP&E", "full_page_text": f"FB page {p}"}
                for p in pages
            ],
        }
    )


def _run(status: str = "answered", cost: float = 0.5) -> AgentRun:
    call = ModelCall(
        turn=1,
        response_id="m",
        model_requested="claude-opus-5-5",
        model_served="claude-opus-5-5",
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd=cost,
        latency_ms=1.0,
    )
    return AgentRun(
        question="q",
        config={},
        status=status,  # type: ignore[arg-type]
        answer=ANSWER if status == "answered" else None,
        model_calls=[call],
        started_at="2026-11-09T00:00:00+00:00",
        wall_s=1.0,
    )


QUESTIONS = [_question(f"q{i}") for i in range(4)]


def test_results_errors_and_traces_are_written(tmp_path: Path) -> None:
    statuses = {"q0": "answered", "q1": "api_error", "q2": "truncated", "q3": "answered"}
    summary = run_experiment(
        QUESTIONS, reps=2, run_dir=tmp_path, execute=lambda q, _r: _run(statuses[q.id])
    )
    results = load_results(tmp_path)
    errors = read_jsonl(tmp_path / ERRORS_FILE)
    assert sorted((r.question_id, r.rep) for r in results) == [
        ("q0", 1),
        ("q0", 2),
        ("q2", 1),
        ("q2", 2),
        ("q3", 1),
        ("q3", 2),
    ]
    assert {(e["question_id"], e["failure"]) for e in errors} == {("q1", "api_error")}
    assert summary.completed == 6 and summary.infra_failures == 2
    assert summary.cost_usd == pytest.approx(4.0)
    assert trace_path(tmp_path, "q2", 2).is_file()
    assert results[0].answer is not None and results[0].served_models == ["claude-opus-5-5"]


def test_resume_skips_completed_and_retries_failures(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []
    first_try = {"q1"}

    def execute(question: Question, rep: int) -> AgentRun:
        calls.append((question.id, rep))
        return _run("timeout" if question.id in first_try else "answered")

    run_experiment(QUESTIONS, reps=1, run_dir=tmp_path, execute=execute)
    first_try.clear()
    calls.clear()
    summary = run_experiment(QUESTIONS, reps=1, run_dir=tmp_path, execute=execute)
    assert calls == [("q1", 1)]  # only the failed one runs again
    assert summary.skipped_done == 3 and summary.completed == 1
    assert len(load_results(tmp_path)) == 4


def test_exceptions_are_recorded_not_raised(tmp_path: Path) -> None:
    def execute(question: Question, _rep: int) -> AgentRun:
        if question.id == "q2":
            raise RuntimeError("index file is corrupt")
        return _run()

    summary = run_experiment(QUESTIONS, reps=1, run_dir=tmp_path, execute=execute)
    [error] = read_jsonl(tmp_path / ERRORS_FILE)
    assert error["failure"] == "exception" and "index file is corrupt" in error["error"]
    assert summary.completed == 3 and summary.statuses["exception"] == 1


def test_cost_cap_stops_scheduling_new_work(tmp_path: Path) -> None:
    summary = run_experiment(
        [_question(f"q{i}") for i in range(10)],
        reps=1,
        run_dir=tmp_path,
        execute=lambda _q, _r: _run(cost=1.0),
        concurrency=1,
        max_cost=3.0,
    )
    assert summary.stopped_by_cost_cap and summary.completed == 3
    # Resuming counts the spend already recorded, so the cap still holds.
    again = run_experiment(
        [_question(f"q{i}") for i in range(10)],
        reps=1,
        run_dir=tmp_path,
        execute=lambda _q, _r: _run(cost=1.0),
        concurrency=1,
        max_cost=3.0,
    )
    assert again.completed == 0 and again.stopped_by_cost_cap


def test_cost_cap_is_not_reported_when_nothing_was_left(tmp_path: Path) -> None:
    summary = run_experiment(
        QUESTIONS, reps=1, run_dir=tmp_path, execute=lambda _q, _r: _run(cost=1.0), max_cost=4.0
    )
    assert summary.completed == 4 and not summary.stopped_by_cost_cap


def test_runs_in_parallel(tmp_path: Path) -> None:
    active = 0
    peak = 0
    lock = threading.Lock()
    barrier = threading.Barrier(3, timeout=5)

    def execute(_question: Question, _rep: int) -> AgentRun:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait()  # proves three run at once
        with lock:
            active -= 1
        return _run()

    run_experiment(
        [_question(f"q{i}") for i in range(3)],
        reps=1,
        run_dir=tmp_path,
        execute=execute,
        concurrency=3,
    )
    assert peak == 3
    assert len(read_jsonl(tmp_path / RESULTS_FILE)) == 3


def test_every_ladder_config_loads_with_a_distinct_hash() -> None:
    configs = sorted((PROJECT_ROOT / "configs" / "experiments").glob("*.yaml"))
    loaded = [load_experiment(p) for p in configs]
    assert [c.name for c in loaded] == [p.stem for p in configs]
    assert len({c.config_hash() for c in loaded}) == len(loaded)
    assert all(not c.agent_config().fallbacks for c in loaded)
    full = next(c for c in loaded if c.name == "b4_full_agent")
    assert full.uses_xbrl and full.corpus == "all"


def test_experiment_config_validation() -> None:
    with pytest.raises(ValueError, match="unknown tools"):
        ExperimentConfig(name="x", description="d", tools=["web_search"])
    with pytest.raises(ValueError, match="Extra inputs"):
        ExperimentConfig.model_validate({"name": "x", "description": "d", "temperature": 0})
    shuffled = ExperimentConfig(name="x", description="d", tools=["calculate", "search_filings"])
    assert shuffled.tools == ["search_filings", "calculate"]  # canonical order
    assert shuffled.config_hash() != ExperimentConfig(name="x", description="d").config_hash()


def test_context_builders() -> None:
    question = _question("q", pages=(1, 2))
    docs = {"ACME_2018_10K": parsed("ACME_2018_10K")}
    oracle = build_context("oracle_evidence", question, load_doc=docs.__getitem__, index=None, k=3)
    assert oracle is not None and "page 1 ---" in oracle and "page 2 ---" in oracle
    assert "(1,577)" in oracle and "total assets" in oracle
    full = build_context("full_filing", question, load_doc=docs.__getitem__, index=None, k=3)
    assert full is not None and full.count("--- page") == 3
    retrieved = build_context(
        "retrieved", question, load_doc=docs.__getitem__, index=build_index(dense=False), k=2
    )
    assert retrieved is not None and retrieved.count("--- ") == 2
    assert build_context("none", question, load_doc=docs.__getitem__, index=None, k=3) is None


def test_oracle_falls_back_to_financebench_page_text() -> None:
    def missing(_name: str):
        raise FileNotFoundError

    text = build_context("oracle_evidence", _question("q"), load_doc=missing, index=None, k=1)
    assert text is not None and "FB page 1" in text
    assert json.dumps(text)  # plain string, safe to send
