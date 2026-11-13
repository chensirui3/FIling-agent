"""Reading numbers out of gold answers, questions and (as a fallback) answer text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Scale = Literal["units", "thousands", "millions", "billions"]
SCALE_FACTOR: dict[str, float] = {
    "units": 1.0,
    "thousands": 1e3,
    "millions": 1e6,
    "billions": 1e9,
}
_SCALE_WORD = {
    "thousand": "thousands",
    "k": "thousands",
    "million": "millions",
    "mn": "millions",
    "mm": "millions",
    "m": "millions",
    "billion": "billions",
    "bn": "billions",
    "b": "billions",
}


@dataclass(frozen=True)
class ParsedNumber:
    value: float
    decimals: int  # digits after the decimal point as written ("1577.00" -> 2)
    is_money: bool
    is_percent: bool
    scale: Scale | None  # stated next to the number ("$1.2 billion")


_BARE = re.compile(
    r"^\s*(?P<lead>[-−(])?\s*(?P<dollar>\$)?\s*(?P<lead2>[-−])?\s*"
    r"(?P<num>\d[\d,]*(?:\.\d+)?|\.\d+)\s*\)?\s*(?P<pct>%)?\s*"
    r"(?P<word>thousand|million|billion|times|x|days)?\.?\s*$",
    re.IGNORECASE,
)


def parse_bare_number(text: str) -> ParsedNumber | None:
    """Parse an answer that is only a number: "$1577.00", "(1,234)", "-0.02", "65.4%", "24.26"."""
    match = _BARE.match(text)
    if not match:
        return None
    digits = match.group("num").replace(",", "")
    value = float(digits)
    if match.group("lead") or match.group("lead2"):
        value = -value
    word = (match.group("word") or "").lower()
    return ParsedNumber(
        value=value,
        decimals=len(digits.split(".")[1]) if "." in digits else 0,
        is_money=bool(match.group("dollar")),
        is_percent=bool(match.group("pct")),
        scale=_SCALE_WORD.get(word),  # type: ignore[arg-type]
    )


_QUESTION_SCALE = re.compile(
    r"\bUSD\s+(?P<a>thousand|million|billion)s?\b"
    r"|\bin\s+(?P<b>thousand|million|billion)s?\s+of\s+(?:US\s?)?(?:dollars|USD)\b"
    r"|\(in\s+(?P<c>thousand|million|billion)s?\)",
    re.IGNORECASE,
)


def question_scale(question: str) -> Scale | None:
    """The money scale a question asks for: "(in USD millions)", "Answer in USD billions"."""
    match = _QUESTION_SCALE.search(question)
    if not match:
        return None
    word = next(g for g in match.groups() if g).lower()
    return _SCALE_WORD[word]  # type: ignore[return-value]


_IN_TEXT = re.compile(
    r"(?P<lead>[-−(])?\s*(?P<dollar>\$)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<close>\))?\s*"
    r"(?P<pct>%|percent\b)?\s*(?P<word>(?:thousand|million|billion|mn|mm|bn)\b)?",
    re.IGNORECASE,
)
_YEAR = re.compile(r"^(19[89]\d|20[0-4]\d)$")


def numbers_in_text(text: str) -> list[ParsedNumber]:
    """Numbers in free text, skipping bare years ("FY2018"), in order of appearance."""
    found: list[ParsedNumber] = []
    for match in _IN_TEXT.finditer(text):
        digits = match.group("num").replace(",", "")
        before = text[max(0, match.start() - 2) : match.start()]
        is_year = bool(_YEAR.match(digits)) and not (
            match.group("dollar") or match.group("pct") or match.group("word")
        )
        if is_year or before.endswith(("FY", "Q")):
            continue
        value = float(digits)
        lead = match.group("lead")
        # "(1,577)" is an accounting negative; "(4.8% of revenue)" is an aside.
        if lead in ("-", "−") or (lead == "(" and match.group("close")):
            value = -value
        word = (match.group("word") or "").lower()
        found.append(
            ParsedNumber(
                value=value,
                decimals=len(digits.split(".")[1]) if "." in digits else 0,
                is_money=bool(match.group("dollar")),
                is_percent=bool(match.group("pct")),
                scale=_SCALE_WORD.get(word),  # type: ignore[arg-type]
            )
        )
    return found
