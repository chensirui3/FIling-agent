from __future__ import annotations

from typing import Any

import pytest

from filing_agent.agent.answer import FinalAnswer
from filing_agent.data.financebench import Question
from filing_agent.eval.gold import numeric_gold, significant_decimals
from filing_agent.eval.grading import grade_answer


def _question(answer: str, question: str = "What is the metric?") -> Question:
    return Question(
        id="q1",
        company="Acme",
        doc_name="ACME_2018_10K",
        question_type="metrics-generated",
        question=question,
        answer=answer,
        evidence=[],
    )


def _answer(value: float | None, unit: Any = None, scale: Any = None, **extra: Any) -> FinalAnswer:
    data = {
        "answer_status": "answered",
        "answer": "Synthetic.",
        "value": value,
        "unit": unit,
        "scale": scale,
        "period": None,
        "calculation": None,
        "sources": [],
        "confidence": "high",
    }
    return FinalAnswer.model_validate(data | extra)


CAPEX = _question("$1577.00", "What is the FY2018 capital expenditure (in USD millions) for Acme?")


@pytest.mark.parametrize(
    ("answer", "label"),
    [
        (_answer(1577, "USD", "millions"), "correct"),
        (_answer(1.577, "USD", "billions"), "correct"),  # same amount, other scale
        (_answer(1_577_000_000, "USD", "units"), "correct"),
        (_answer(1578.5, "USD", "millions"), "correct"),  # within 0.1%
        (_answer(1590, "USD", "millions"), "incorrect"),
        (_answer(None, answer="Capex was $1,577 million in FY2018."), "correct"),  # from prose
    ],
)
def test_money_answers(answer: FinalAnswer, label: str) -> None:
    assert grade_answer(CAPEX, "answered", answer).label == label


def test_diagnostic_flags_on_wrong_money_answers() -> None:
    near = grade_answer(CAPEX, "answered", _answer(1600, "USD", "millions")).numeric
    assert near is not None and near.near_miss and not near.scale_error
    slip = grade_answer(CAPEX, "answered", _answer(1.577, "USD", "millions")).numeric
    assert slip is not None and slip.scale_error and not slip.near_miss
    flipped = grade_answer(CAPEX, "answered", _answer(-1577, "USD", "millions")).numeric
    assert flipped is not None and flipped.sign_error


@pytest.mark.parametrize(
    ("gold", "answer", "label"),
    [
        ("65.4%", _answer(65.4, "percent"), "correct"),
        ("65.4%", _answer(0.654, "ratio"), "correct"),
        ("65.4%", _answer(65.6, "percent"), "incorrect"),
        ("0.01", _answer(0.0137, "ratio"), "correct"),  # rounds to the gold
        ("0.01", _answer(1.37, "percent"), "correct"),
        ("0.01", _answer(0.016, "ratio"), "incorrect"),  # rounds to 0.02
        ("0.8", _answer(0.84, "ratio"), "correct"),
        ("0.8", _answer(0.88, "ratio"), "incorrect"),  # rounds to 0.9
        ("24.26", _answer(24.27, "times"), "correct"),  # intermediate-rounding drift
        ("24.26", _answer(24.4, "times"), "incorrect"),
    ],
)
def test_percent_and_ratio_answers(gold: str, answer: FinalAnswer, label: str) -> None:
    assert grade_answer(_question(gold), "answered", answer).label == label


def test_strict_and_lenient_variants() -> None:
    check = grade_answer(_question("24.26"), "answered", _answer(24.27, "times")).numeric
    assert check is not None and check.within_tolerance and not check.strict
    lenient = grade_answer(_question("24.26"), "answered", _answer(24.4, "times")).numeric
    assert lenient is not None and not lenient.within_tolerance and lenient.lenient


def test_statuses_refusals_and_judge_routing() -> None:
    refusal = _answer(None, answer_status="insufficient_information")
    assert grade_answer(CAPEX, "answered", refusal).label == "refusal"
    for status in ("truncated", "budget_exhausted", "invalid_answer", "refusal"):
        assert grade_answer(CAPEX, status, None).label == "no_answer"
    with pytest.raises(ValueError, match="not graded"):
        grade_answer(CAPEX, "api_error", None)
    prose = grade_answer(_question("Yes, the ratio improved."), "answered", _answer(1.2))
    assert (prose.label, prose.method) == (None, "pending_judge")


@pytest.mark.parametrize(
    ("written", "value", "decimals"),
    [
        ("4.60", 4.6, 1),
        ("1577.00", 1577.0, 0),
        ("24.26", 24.26, 2),
        ("-0.02", -0.02, 2),
        ("0", 0, 0),
    ],
)
def test_trailing_zeros_are_formatting_not_precision(
    written: str, value: float, decimals: int
) -> None:
    assert significant_decimals(value, len(written.split(".")[1]) if "." in written else 0) == (
        decimals
    )


def test_pepsico_capex_as_reported_matches_the_two_decimal_gold() -> None:
    # Gold "$4.60" (USD billions); PepsiCo reported $4,625 million for FY2021.
    capex = _question("$4.60", "What is the FY2021 capital expenditure (in USD billions)?")
    assert grade_answer(capex, "answered", _answer(4625, "USD", "millions")).label == "correct"
    assert grade_answer(capex, "answered", _answer(4.7, "USD", "billions")).label == "incorrect"


def test_gold_kinds_and_scale_sources() -> None:
    capex = numeric_gold(CAPEX)
    assert capex is not None and (capex.kind, capex.scale, capex.scale_source) == (
        "money",
        "millions",
        "question",
    )
    assumed = numeric_gold(_question("$382.00", "How much was spent on buybacks?"))
    assert assumed is not None and assumed.scale_source == "assumed"
    asked = numeric_gold(_question("77.78", "How much will it pay in USD million?"))
    assert asked is not None and asked.kind == "money"
    ratio = numeric_gold(_question("24.26"))
    assert ratio is not None and (ratio.kind, ratio.tolerance) == ("number", pytest.approx(0.02426))
    overridden = numeric_gold(_question("$8.70"), {"q1": {"scale": "billions"}})
    assert overridden is not None and (overridden.scale, overridden.scale_source) == (
        "billions",
        "override",
    )
