"""The README's "See it work" section is a saved trace from the hard-tier run: keep them in step."""

from __future__ import annotations

from filing_agent.agent.trace import AgentRun
from filing_agent.config import PROJECT_ROOT
from filing_agent.eval.grading import grade_answer
from filing_agent.fresh.benchmark import load_questions

DEMO = PROJECT_ROOT / "docs" / "demo" / "fresh_hard_ADOBE_2026Q3_10Q_history_revenue.json"


def _run() -> AgentRun:
    return AgentRun.model_validate_json(DEMO.read_text(encoding="utf-8"))


def _squash(text: str) -> str:
    return " ".join(text.split())


def test_demo_trace_is_graded_correct() -> None:
    run = _run()
    questions = load_questions(PROJECT_ROOT / "configs" / "fresh" / "questions_hard.jsonl")
    question = next(q for q in questions if q.id == DEMO.stem)
    assert run.question == question.question
    assert grade_answer(question, run.status, run.answer).label == "correct"


def test_readme_shows_the_trace_as_recorded() -> None:
    run = _run()
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## See it work", 1)[1].split("\n## ", 1)[0]
    assert run.answer is not None
    assert run.question in section and run.answer.answer in section
    notes = [
        block["thinking"]
        for message in run.transcript
        if isinstance(message["content"], list)
        for block in message["content"]
        if block.get("type") == "thinking"
    ]
    assert notes and all(_squash(note) in _squash(section) for note in notes)
    shown = [
        line.split()[1].split("(")[0] for line in section.splitlines() if line.startswith("tool ")
    ]
    assert shown == [call.name for call in run.tool_calls]
