from __future__ import annotations

from pathlib import Path

from filing_agent.agent.trace import ToolCall
from filing_agent.eval.xbrl_use import XbrlUse, evidence_route, summarize, to_json, xbrl_use
from tests.factories import question


def _call(name: str, *, is_error: bool = False) -> ToolCall:
    return ToolCall(
        turn=1,
        tool_use_id="t",
        name=name,
        input={},
        output="",
        is_error=is_error,
        internal_error=False,
        truncated=False,
        latency_ms=1.0,
    )


def test_route_counts_only_calls_that_returned_data() -> None:
    assert evidence_route([_call("xbrl_get_facts"), _call("calculate")]) == "xbrl"
    assert evidence_route([_call("list_filings"), _call("search_filings")]) == "text"
    assert evidence_route([_call("xbrl_get_facts"), _call("read_filing_page")]) == "both"
    failed = _call("xbrl_get_facts", is_error=True)
    assert evidence_route([failed, _call("search_filings")]) == "text"
    assert evidence_route([_call("xbrl_find_concepts"), _call("calculate")]) == "none"


def _row(qid: str, route: str, label: str, kind: str = "money", tried: bool = False) -> XbrlUse:
    return XbrlUse(qid, "run", kind, route, tried, label)  # type: ignore[arg-type]


def test_summary_is_a_lower_bound_from_correct_xbrl_only_answers() -> None:
    rows = [
        _row("q1", "xbrl", "correct"),
        _row("q2", "xbrl", "incorrect", kind="number"),
        _row("q3", "text", "correct", kind="number"),
        _row("q4", "text", "correct", tried=True),
    ]
    summary = summarize(rows)
    assert summary["correct_from_xbrl_alone"] == 1
    assert summary["answerable_lower_bound"] == 0.25
    assert summary["by_route"] == {"xbrl": 2, "text": 2, "both": 0, "none": 0}
    assert summary["text_route_without_trying_xbrl"] == 1
    assert summary["by_gold_kind"] == {
        "money": {"questions": 2, "correct_from_xbrl_alone": 1},
        "number": {"questions": 2, "correct_from_xbrl_alone": 0},
    }
    assert len(to_json(rows)["questions"]) == 4  # type: ignore[arg-type]


def test_missing_trace_is_reported(tmp_path: Path) -> None:
    grade = '{"question_id": "q1", "rep": 1, "label": "correct", "method": "numeric"}'
    (tmp_path / "grades.jsonl").write_text(grade + chr(10), encoding="utf-8")
    q = question("q1", "Acme", "ACME_2018_10K")
    q = q.model_copy(update={"question_type": "metrics-generated"})
    try:
        xbrl_use(tmp_path, {"q1": q})
    except FileNotFoundError as error:
        assert "traces are local" in str(error)
    else:
        raise AssertionError("expected FileNotFoundError")
