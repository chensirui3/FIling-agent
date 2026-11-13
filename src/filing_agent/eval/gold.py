"""Gold numbers for the bare-number FinanceBench answers, with their kind, scale and precision.

`configs/gold_overrides.yaml` corrects any gold the parser reads wrongly, after human review
(`fa eval gold-review` exports the sheet). An override never changes FinanceBench's answer
text, only how this repository reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml

from filing_agent.data.financebench import Question
from filing_agent.eval.numbers import Scale, parse_bare_number, question_scale

Kind = Literal["money", "percent", "number"]


def significant_decimals(value: float, written: int) -> int:
    """Decimals that carry information. FinanceBench prints metrics with two decimals whatever
    their precision: "$4.60" (billions) is PepsiCo's $4,625 million rounded to 4.6, so the
    trailing zero must not be read as precision."""
    exponent = Decimal(repr(abs(value))).normalize().as_tuple().exponent
    return min(written, max(0, -exponent)) if isinstance(exponent, int) else written


@dataclass(frozen=True)
class GoldNumber:
    question_id: str
    value: float  # in `scale` for money, in percent points for percents, raw otherwise
    kind: Kind
    scale: Scale | None
    decimals: int  # significant decimals: trailing zeros after the point are formatting
    scale_source: Literal["question", "answer", "assumed", "override", "none"]

    @property
    def tolerance(self) -> float:
        """Half a unit in the last significant decimal (the prediction rounds to the gold), or
        0.1% of the value for intermediate-rounding drift, whichever is wider."""
        return max(0.5 * 10.0**-self.decimals, 0.001 * abs(self.value))


def load_overrides(path: Path | None) -> dict[str, dict[str, object]]:
    if path is None or not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(data.get("overrides") or {})


def numeric_gold(
    question: Question, overrides: dict[str, dict[str, object]] | None = None
) -> GoldNumber | None:
    """The gold as a number, or None when the gold answer is not a bare number."""
    parsed = parse_bare_number(question.answer)
    if parsed is None:
        return None
    asked_scale = question_scale(question.question)
    if parsed.is_percent:
        kind: Kind = "percent"
        scale, source = None, "none"
    elif parsed.is_money or asked_scale is not None:
        kind = "money"
        if parsed.scale:
            scale, source = parsed.scale, "answer"
        elif asked_scale:
            scale, source = asked_scale, "question"
        else:
            scale, source = "millions", "assumed"  # FinanceBench's default for dollar amounts
    else:
        kind, scale, source = "number", None, "none"
    gold = GoldNumber(
        question_id=question.id,
        value=parsed.value,
        kind=kind,
        scale=scale,
        decimals=significant_decimals(parsed.value, parsed.decimals),
        scale_source=source,
    )
    override = (overrides or {}).get(question.id)
    if override:
        gold = GoldNumber(
            question_id=question.id,
            value=float(override.get("value", gold.value)),  # type: ignore[arg-type]
            kind=override.get("kind", gold.kind),  # type: ignore[arg-type]
            scale=override.get("scale", gold.scale),  # type: ignore[arg-type]
            decimals=int(override.get("decimals", gold.decimals)),  # type: ignore[call-overload]
            scale_source="override",
        )
    return gold
