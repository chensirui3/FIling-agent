from __future__ import annotations

import pytest

from filing_agent.data.financebench import Document, Question
from filing_agent.eval.grading import Grade, NumericCheck
from filing_agent.eval.report import compare_grades, compute_metrics
from filing_agent.eval.runner import ResultRow


def _question(qid: str, qtype: str) -> Question:
    return Question.model_validate(
        {
            "id": qid,
            "company": "Acme",
            "doc_name": "ACME_2018_10K",
            "question_type": qtype,
            "question": "q",
            "answer": "a",
            "evidence": [],
        }
    )


QUESTIONS = {
    "q1": _question("q1", "metrics-generated"),
    "q2": _question("q2", "domain-relevant"),
    "q3": _question("q3", "novel-generated"),
}
DOCUMENTS = {
    "ACME_2018_10K": Document(
        doc_name="ACME_2018_10K", company="Acme", doc_type="10k", doc_period=2018
    )
}


def _row(qid: str, rep: int, cost: float = 0.25, wall: float = 10.0) -> ResultRow:
    return ResultRow(
        question_id=qid,
        rep=rep,
        status="answered",
        answer=None,
        final_text=None,
        error=None,
        cost_usd=cost,
        tokens={},
        model_calls=2,
        tool_calls=4,
        tool_errors=1,
        internal_tool_errors=0,
        served_models=["claude-opus-5-5"],
        wall_s=wall,
        started_at="2026-11-09T00:00:00+00:00",
    )


def _check(strict: bool, within: bool, lenient: bool) -> NumericCheck:
    return NumericCheck(
        gold_value=1.0,
        gold_kind="number",
        gold_scale=None,
        predicted=1.0,
        value_source="structured",
        error=0.0,
        tolerance=0.01,
        within_tolerance=within,
        strict=strict,
        lenient=lenient,
        near_miss=False,
        scale_error=False,
        sign_error=False,
    )


def _grade(qid: str, rep: int, label: str | None, method: str = "judge", **extra: object) -> Grade:
    return Grade.model_validate(
        {"question_id": qid, "rep": rep, "label": label, "method": method} | extra
    )


def test_metrics_from_grades() -> None:
    rows = [_row(q, r, wall=10.0 * r) for q in QUESTIONS for r in (1, 2)]
    grades = [
        _grade("q1", 1, "correct", "numeric", numeric=_check(True, True, True)),
        _grade("q1", 2, "incorrect", "numeric", numeric=_check(False, False, True)),
        _grade("q2", 1, "correct"),
        _grade("q2", 2, "correct", needs_review=True),
        _grade("q3", 1, "refusal", "status"),
        _grade("q3", 2, None, "pending_judge"),
    ]
    m = compute_metrics(
        "run1", "b4_full_agent", rows, grades, [{"failure": "api_error"}], QUESTIONS, DOCUMENTS
    )
    assert (m.questions, m.rows, m.graded, m.pending_judge) == (3, 6, 5, 1)
    assert m.labels == {"correct": 3, "incorrect": 1, "refusal": 1, "no_answer": 0}
    assert m.accuracy == pytest.approx(3 / 5)
    assert m.accuracy_ci95 is not None and 0 <= m.accuracy_ci95[0] <= m.accuracy_ci95[1] <= 1
    assert m.accuracy_per_rep == {1: pytest.approx(2 / 3), 2: pytest.approx(1 / 2)}
    assert m.refusal_rate == pytest.approx(1 / 5)
    assert m.by_question_type["domain-relevant"].accuracy == 1.0
    assert m.by_doc_type["10k"].n == 5
    assert m.numeric_sensitivity == {"n": 2.0, "strict": 0.5, "default": 0.5, "lenient": 1.0}
    assert m.needs_review == 1 and m.infra_failures == 1
    assert m.cost_usd["agent_total"] == pytest.approx(1.5)
    assert m.wall_s["mean"] == pytest.approx(15.0)
    assert m.tool_calls == {"mean": 4.0, "errors": 6.0, "internal_errors": 0.0}


def test_compare_runs_pairs_questions() -> None:
    ids = [f"q{i}" for i in range(20)]
    before = [_grade(q, 1, "correct" if i < 10 else "incorrect") for i, q in enumerate(ids)]
    # After: questions 10-17 fixed, question 0 broken, plus one question only in this run.
    after = [_grade(q, 1, "correct" if 1 <= i < 18 else "incorrect") for i, q in enumerate(ids)]
    after.append(_grade("extra", 1, "correct"))
    c = compare_grades("a", before, "b", after)
    assert c.questions == 20
    assert (c.accuracy_a, c.accuracy_b) == (0.5, 0.85)
    assert c.delta == pytest.approx(0.35)
    assert c.delta_ci95[0] > 0
    assert c.fixed == [f"q{i}" for i in range(10, 18)] and c.broken == ["q0"]
    assert c.mcnemar_p < 0.05
    with pytest.raises(ValueError, match="share no graded"):
        compare_grades("a", before, "c", [_grade("other", 1, "correct")])
