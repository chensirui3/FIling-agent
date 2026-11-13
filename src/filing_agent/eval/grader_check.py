"""Known-good and known-bad answers, run through the numeric grader.

A grader is only trustworthy if it passes the gold answers, in every form a careful human grader
accepts, and fails plausible wrong ones. For every bare-number gold this builds synthetic answers
with known verdicts and checks the grader agrees with each.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from filing_agent.agent.answer import FinalAnswer, Unit
from filing_agent.data.financebench import Question
from filing_agent.eval.gold import GoldNumber, numeric_gold
from filing_agent.eval.grading import GradeLabel, grade_answer
from filing_agent.eval.numbers import Scale


def _answer(
    value: float | None,
    unit: Unit | None,
    scale: Scale | None,
    *,
    text: str = "Synthetic answer.",
    status: str = "answered",
) -> FinalAnswer:
    return FinalAnswer.model_validate(
        {
            "answer_status": status,
            "answer": text,
            "value": value,
            "unit": unit,
            "scale": scale,
            "period": None,
            "calculation": None,
            "sources": [],
            "confidence": "high",
        }
    )


def _unit(gold: GoldNumber) -> Unit:
    return "USD" if gold.kind == "money" else "percent" if gold.kind == "percent" else "ratio"


def _rescaled(gold: GoldNumber) -> FinalAnswer | None:
    if gold.kind != "money":
        return None
    if gold.scale == "billions":
        return _answer(gold.value * 1000, "USD", "millions")
    return _answer(gold.value / 1000, "USD", "billions")


def _in_prose(gold: GoldNumber) -> FinalAnswer:
    figure = f"{gold.value:,.{gold.decimals}f}"
    if gold.kind == "money":
        text = f"The figure is ${figure} {(gold.scale or 'millions')[:-1]}."
    elif gold.kind == "percent":
        text = f"The figure is {figure}%."
    else:
        text = f"The figure is {figure}."
    return _answer(None, None, None, text=text)


def _nonzero(
    make: Callable[[GoldNumber], FinalAnswer],
) -> Callable[[GoldNumber], FinalAnswer | None]:
    return lambda g: None if g.value == 0 else make(g)


@dataclass(frozen=True)
class Probe:
    name: str
    expect: GradeLabel
    make: Callable[[GoldNumber], FinalAnswer | None]  # None: not applicable to this gold


PROBES: tuple[Probe, ...] = (
    Probe("the gold itself", "correct", lambda g: _answer(g.value, _unit(g), g.scale)),
    Probe("same amount, other scale", "correct", _rescaled),
    Probe(
        "percent given as a fraction",
        "correct",
        lambda g: _answer(g.value / 100, "ratio", None) if g.kind == "percent" else None,
    ),
    Probe("figure only in the prose", "correct", _in_prose),
    Probe(
        "rounds to the gold",
        "correct",
        lambda g: _answer(g.value + 0.4 * 10.0**-g.decimals, _unit(g), g.scale),
    ),
    Probe(
        "too high, beyond rounding",
        "incorrect",
        # 10% high, or two units of the last decimal if 10% would still round to the gold
        _nonzero(
            lambda g: _answer(
                g.value + max(0.1 * abs(g.value), 2 * 10.0**-g.decimals), _unit(g), g.scale
            )
        ),
    ),
    Probe(
        "x1000 scale slip",
        "incorrect",
        _nonzero(lambda g: _answer(g.value * 1000, _unit(g), g.scale)),
    ),
    Probe("sign flipped", "incorrect", _nonzero(lambda g: _answer(-g.value, _unit(g), g.scale))),
    Probe(
        "says information is missing",
        "refusal",
        lambda g: _answer(None, None, None, status="insufficient_information"),
    ),
)


@dataclass
class ProbeResult:
    probe: Probe
    checked: int = 0
    failures: list[str] = field(default_factory=list)


def run_grader_check(
    questions: Sequence[Question], overrides: dict[str, dict[str, object]] | None = None
) -> list[ProbeResult]:
    results = [ProbeResult(p) for p in PROBES]
    for question in questions:
        gold = numeric_gold(question, overrides)
        if gold is None:
            continue
        for result in results:
            answer = result.probe.make(gold)
            if answer is None:
                continue
            result.checked += 1
            label = grade_answer(question, "answered", answer, overrides).label
            if label != result.probe.expect:
                result.failures.append(f"{question.id} -> {label}")
    return results
