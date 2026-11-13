"""Grading one final answer against the gold.

Bare-number golds (52 of the 150) are graded by deterministic rules; the others go to the LLM
judge. Labels:

- correct / incorrect: an answer was given and checked;
- refusal: the agent said the information needed was not available;
- no_answer: the run ended without a valid answer (cut off, out of budget, or not matching the
  answer schema). It counts against accuracy but is reported separately.

Infrastructure failures (API errors, timeouts, a different served model) are never graded: the
runner keeps them out of the scored rows so plumbing problems are not counted as model mistakes.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel

from filing_agent.agent.answer import FinalAnswer
from filing_agent.agent.trace import INFRA_FAILURES
from filing_agent.data.financebench import Question
from filing_agent.eval.gold import GoldNumber, Kind, numeric_gold
from filing_agent.eval.judge import JudgeResult
from filing_agent.eval.numbers import SCALE_FACTOR, Scale, numbers_in_text

GradeLabel = Literal["correct", "incorrect", "refusal", "no_answer"]
GradeMethod = Literal["numeric", "judge", "status", "pending_judge"]
LENIENT_RELATIVE = 0.01
NEAR_MISS_RELATIVE = 0.05


class NumericCheck(BaseModel):
    gold_value: float
    gold_kind: Kind
    gold_scale: Scale | None
    predicted: float | None  # in the gold's terms: its money scale, percent points, or raw
    value_source: Literal["structured", "text", "none"]
    error: float | None
    tolerance: float
    within_tolerance: bool  # the headline rule
    strict: bool  # equal after rounding to the gold's written decimals
    lenient: bool  # within 1%
    near_miss: bool  # within 5% but outside tolerance
    scale_error: bool  # off by a factor of 1,000 or 1,000,000
    sign_error: bool


class Grade(BaseModel):
    question_id: str
    rep: int = 1
    label: GradeLabel | None  # None until the judge has graded it
    method: GradeMethod
    numeric: NumericCheck | None = None
    judge: JudgeResult | None = None
    needs_review: bool = False  # the judge's verdict and the figure check disagree
    note: str | None = None


def _in_gold_terms(
    gold: GoldNumber, value: float, unit: str | None, scale: str | None
) -> list[float]:
    """The predicted value expressed like the gold, with every reading a careful grader accepts."""
    if gold.kind == "money":
        gold_factor = SCALE_FACTOR[gold.scale or "millions"]
        pred_factor = SCALE_FACTOR.get(scale or "", gold_factor)
        return [value * pred_factor / gold_factor]
    if gold.kind == "percent":
        return [value] if unit == "percent" else [value, value * 100]  # 0.654 means 65.4%
    return [value, value / 100] if unit == "percent" else [value]  # 1.37% means 0.0137


def _predicted_values(gold: GoldNumber, answer: FinalAnswer) -> tuple[list[float], str]:
    if answer.value is not None:
        return _in_gold_terms(gold, answer.value, answer.unit, answer.scale), "structured"
    for number in numbers_in_text(answer.answer):  # fallback: the first number in the prose
        unit = "percent" if number.is_percent else None
        return _in_gold_terms(gold, number.value, unit, number.scale), "text"
    return [], "none"


def _round_half_up(value: float, decimals: int) -> float:
    return float(Decimal(repr(value)).quantize(Decimal(1).scaleb(-decimals), ROUND_HALF_UP))


def check_numeric(gold: GoldNumber, answer: FinalAnswer) -> NumericCheck:
    values, source = _predicted_values(gold, answer)
    tolerance = gold.tolerance
    if not values:
        return NumericCheck(
            gold_value=gold.value,
            gold_kind=gold.kind,
            gold_scale=gold.scale,
            value_source="none",
            tolerance=tolerance,
            predicted=None,
            error=None,
            within_tolerance=False,
            strict=False,
            lenient=False,
            near_miss=False,
            scale_error=False,
            sign_error=False,
        )
    eps = 1e-9 * max(1.0, abs(gold.value))
    best = min(values, key=lambda v: abs(v - gold.value))
    error = abs(best - gold.value)
    within = error <= tolerance + eps
    magnitude = abs(gold.value)
    return NumericCheck(
        gold_value=gold.value,
        gold_kind=gold.kind,
        gold_scale=gold.scale,
        value_source="structured" if source == "structured" else "text",
        tolerance=tolerance,
        predicted=best,
        error=error,
        within_tolerance=within,
        strict=any(abs(_round_half_up(v, gold.decimals) - gold.value) <= eps for v in values),
        lenient=error <= max(tolerance, LENIENT_RELATIVE * magnitude) + eps,
        near_miss=not within and error <= NEAR_MISS_RELATIVE * magnitude + eps,
        scale_error=not within
        and any(
            abs(v * f - gold.value) <= tolerance + eps
            for v in values
            for f in (1e3, 1e-3, 1e6, 1e-6)
        ),
        sign_error=not within
        and gold.value != 0
        and any(abs(-v - gold.value) <= tolerance + eps for v in values),
    )


def grade_answer(
    question: Question,
    run_status: str,
    answer: FinalAnswer | None,
    overrides: dict[str, dict[str, object]] | None = None,
) -> Grade:
    """Grade what deterministic rules can; mark the rest `pending_judge`."""
    if run_status in INFRA_FAILURES:
        raise ValueError(f"{question.id}: infrastructure failures ({run_status}) are not graded")
    if run_status != "answered" or answer is None:
        return Grade(question_id=question.id, label="no_answer", method="status", note=run_status)
    if answer.answer_status == "insufficient_information":
        return Grade(question_id=question.id, label="refusal", method="status")
    gold = numeric_gold(question, overrides)
    if gold is None:
        return Grade(question_id=question.id, label=None, method="pending_judge")
    check = check_numeric(gold, answer)
    return Grade(
        question_id=question.id,
        label="correct" if check.within_tolerance else "incorrect",
        method="numeric",
        numeric=check,
    )
