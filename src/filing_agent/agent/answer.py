"""The agent's final answer: a strict JSON schema the model's last message must follow.

A structured answer makes grading deterministic where it can be: the key figure arrives as a
number with an explicit unit and scale, separate from the prose, and sources point to the pages
or XBRL facts it came from.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from filing_agent.tools.base import strict_schema

Unit = Literal[
    "USD", "percent", "ratio", "times", "days", "shares", "USD_per_share", "count", "other"
]
Scale = Literal["units", "thousands", "millions", "billions"]


class Source(BaseModel):
    """Optional fields are omitted when they do not apply (they are not nullable unions)."""

    doc_name: str | None = Field(None, description="Filing the figure came from, e.g. 3M_2018_10K.")
    page: int | None = Field(None, description="Zero-based page in that filing.")
    xbrl_concept: str | None = Field(None, description="XBRL concept, if from XBRL.")
    accession: str | None = Field(None, description="Accession number of that XBRL fact's filing.")


class FinalAnswer(BaseModel):
    answer_status: Literal["answered", "insufficient_information"]
    answer: str = Field(description="The direct answer in one to three sentences.")
    value: float | None = Field(
        None,
        description="The key figure in the given scale and unit, e.g. 1577 for $1,577 million.",
    )
    unit: Unit | None = Field(None, description="Unit of the key figure.")
    scale: Scale | None = Field(None, description="Scale of the key figure.")
    period: str | None = Field(None, description="Fiscal period, e.g. FY2018 or Q2 FY2023.")
    calculation: str | None = Field(None, description="Formula and inputs, if computed.")
    sources: list[Source]
    confidence: Literal["high", "medium", "low"]


ANSWER_SCHEMA: dict[str, Any] = strict_schema(FinalAnswer)


def answer_format() -> dict[str, Any]:
    return {"type": "json_schema", "schema": ANSWER_SCHEMA}


class AnswerParseError(ValueError):
    """The final message was not valid answer JSON."""


def parse_answer(text: str) -> FinalAnswer:
    try:
        return FinalAnswer.model_validate_json(text.strip())
    except ValidationError as exc:
        raise AnswerParseError(str(exc)) from None
