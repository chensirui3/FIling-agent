from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from anthropic.types import Message

from filing_agent.agent.answer import FinalAnswer
from filing_agent.data.financebench import Question
from filing_agent.eval.grade_run import apply_judge, grade_rows
from filing_agent.eval.grading import Grade
from filing_agent.eval.judge import (
    JUDGE_SCHEMA,
    Judge,
    JudgeError,
    JudgeOutput,
    check_figures,
    figures_match,
    judge_prompt,
)
from filing_agent.eval.numbers import numbers_in_text
from filing_agent.eval.runner import ResultRow

JUDGE_MODEL = "claude-sonnet-5-5"
QUESTION = Question.model_validate(
    {
        "id": "q_liquidity",
        "company": "3M",
        "doc_name": "3M_2023Q2_10Q",
        "question_type": "domain-relevant",
        "question": "Does 3M have a reasonably healthy liquidity profile based on its quick ratio?",
        "answer": "No. The quick ratio for 3M was 0.96 by Jun'23 close.",
        "justification": "Quick ratio = (cash + receivables) / current liabilities.",
        "evidence": [],
    }
)
NUMERIC = Question.model_validate(
    {
        "id": "q_capex",
        "company": "3M",
        "doc_name": "3M_2018_10K",
        "question_type": "metrics-generated",
        "question": "What is the FY2018 capital expenditure (in USD millions) for 3M?",
        "answer": "$1577.00",
        "evidence": [],
    }
)


def _answer(text: str = "No; the quick ratio was 0.96.", value: float | None = 0.96) -> FinalAnswer:
    return FinalAnswer.model_validate(
        {
            "answer_status": "answered",
            "answer": text,
            "value": value,
            "unit": "ratio",
            "scale": None,
            "period": "Q2 FY2023",
            "calculation": None,
            "sources": [],
            "confidence": "high",
        }
    )


def _verdict(
    verdict: str = "correct",
    ref: list[str] | None = None,
    resp: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "reference_figures": ref if ref is not None else ["0.96"],
        "response_figures": resp if resp is not None else ["0.96"],
        "conclusion_matches": True,
        "contradicts_reference": False,
        "verdict": verdict,
        "rationale": "Same conclusion and figure.",
    } | extra


def _message(payload: dict[str, Any] | str, *, model: str = JUDGE_MODEL, stop: str = "end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return Message.model_validate(
        {
            "id": "msg_j",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 300,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
    )


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params: Any) -> Any:
        self.requests.append(params)
        return self.responses.pop(0)


def test_judge_request_and_result(tmp_path: Path) -> None:
    client = FakeClient([_message(_verdict())])
    judge = Judge(client, JUDGE_MODEL, cache_dir=tmp_path)
    result = judge.grade(QUESTION, _answer())
    assert result.output.verdict == "correct"
    assert result.figure_check.all_matched and result.figure_check.reference_figures == 1
    assert result.cost_usd == pytest.approx((1200 * 2.0 + 300 * 10.0) / 1e6)
    [request] = client.requests
    assert request["model"] == JUDGE_MODEL
    assert request["output_config"]["format"]["schema"] == JUDGE_SCHEMA
    prompt = request["messages"][0]["content"]
    assert "<reference_answer>" in prompt and "Stated figure: 0.96" in prompt
    # Cached: a second grade of the same answer makes no request.
    assert judge.grade(QUESTION, _answer()) == result and len(client.requests) == 1


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_message(_verdict(), model="claude-opus-5-5"), "served by"),
        (_message(_verdict(), stop="max_tokens"), "stopped with max_tokens"),
        (_message("not json"), "did not match"),
    ],
)
def test_judge_failures_raise(response: Any, message: str) -> None:
    with pytest.raises(JudgeError, match=message):
        Judge(FakeClient([response]), JUDGE_MODEL).grade(QUESTION, _answer())


@pytest.mark.parametrize(
    ("reference", "response", "match"),
    [
        ("0.96", "0.96", True),
        ("0.96", "0.97", False),
        ("$1,577 million", "$1.58 billion", True),  # coarser response, equal up to rounding
        ("$1,577 million", "$1.6 billion", True),  # 1.577 rounds to 1.6 at one decimal
        ("$1,577 million", "$1.7 billion", False),
        ("65.4%", "0.654", True),
        ("5.1%", "5.1%", True),
    ],
)
def test_figures_match(reference: str, response: str, match: bool) -> None:
    ref, resp = numbers_in_text(reference)[0], numbers_in_text(response)[0]
    assert figures_match(ref, resp) is match


def _row(question: Question, answer: FinalAnswer | None, status: str = "answered") -> ResultRow:
    return ResultRow(
        question_id=question.id,
        rep=1,
        status=status,
        answer=answer,
        final_text=None,
        error=None,
        cost_usd=0.2,
        tokens={},
        model_calls=1,
        tool_calls=3,
        tool_errors=0,
        internal_tool_errors=0,
        served_models=["claude-opus-5-5"],
        wall_s=12.0,
        started_at="2026-11-09T00:00:00+00:00",
    )


PENDING = Grade(question_id="q_liquidity", label=None, method="pending_judge")


@pytest.mark.parametrize(
    ("verdict", "label", "review"),
    [
        (_verdict(), "correct", False),
        (_verdict(resp=["0.91"]), "correct", True),  # judge says correct, figures disagree
        (
            _verdict("incorrect"),
            "incorrect",
            True,
        ),  # judge says wrong, figures and conclusion agree
        (_verdict("incorrect", resp=["0.91"], conclusion_matches=False), "incorrect", False),
    ],
)
def test_apply_judge_flags_disagreements(verdict: dict[str, Any], label: str, review: bool) -> None:
    judge = Judge(FakeClient([_message(verdict)]), JUDGE_MODEL)
    grade = apply_judge(PENDING, QUESTION, _row(QUESTION, _answer()), judge)
    assert (grade.label, grade.method, grade.needs_review) == (label, "judge", review)


def test_failed_judgment_stays_pending() -> None:
    judge = Judge(FakeClient([_message("garbage")]), JUDGE_MODEL)
    grade = apply_judge(PENDING, QUESTION, _row(QUESTION, _answer()), judge)
    assert grade.label is None and grade.note and "judge failed" in grade.note


def test_grade_rows_routes_numeric_prose_and_statuses() -> None:
    questions = {q.id: q for q in (QUESTION, NUMERIC)}
    capex = FinalAnswer.model_validate(
        _answer("Capex was $1,577 million.", 1577).model_dump()
        | {"unit": "USD", "scale": "millions"}
    )
    rows = [_row(QUESTION, _answer()), _row(NUMERIC, capex)]
    judged = grade_rows(
        rows, questions, judge=Judge(FakeClient([_message(_verdict())]), JUDGE_MODEL)
    )
    assert [(g.question_id, g.method, g.label) for g in judged] == [
        ("q_capex", "numeric", "correct"),
        ("q_liquidity", "judge", "correct"),
    ]
    unjudged = grade_rows(rows, questions, judge=None)
    assert [g.label for g in unjudged] == ["correct", None]
    truncated = grade_rows([_row(QUESTION, None, "truncated")], questions, judge=None)
    assert truncated[0].label == "no_answer"


def test_formulas_are_not_figures() -> None:
    # Seen live (Verizon quick ratio): the judge listed the justification's formula as a figure.
    output = JudgeOutput.model_validate(
        _verdict(
            ref=["0.54", "(37857 - 2388 - 8358) / 50171", "No"],
            resp=["0.54", "27,111 / 50,171", "No"],
        )
    )
    check = check_figures(output)
    assert (check.reference_figures, check.matched, check.unmatched) == (1, 1, [])
    assert check_figures(
        JudgeOutput.model_validate(_verdict(ref=["-0.02"], resp=["-0.02"]))
    ).all_matched


def test_prompt_marks_the_reference_and_response() -> None:
    prompt = judge_prompt(QUESTION, _answer())
    assert (
        prompt.index("<question>") < prompt.index("<reference_answer>") < prompt.index("<response>")
    )
