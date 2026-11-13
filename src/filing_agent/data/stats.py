"""Summary statistics for the FinanceBench sample."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from filing_agent.data.financebench import QUESTION_TYPES, Document, Question

AnswerKind = Literal["number", "yes/no + rationale", "text with figures", "text only"]

_YES_NO = re.compile(r"^\s*(yes|no)\b", re.IGNORECASE)
_BARE_NUMBER = re.compile(
    r"[-−$\s(]*[\d,]+(\.\d+)?\s*%?\)?\s*(million|billion|thousand|times|x|days)?\.?",
    re.IGNORECASE,
)
_DIGIT = re.compile(r"\d")


def answer_kind(answer: str) -> AnswerKind:
    """Coarse shape of a gold answer. Bare numbers can be graded by numeric rules alone."""
    text = answer.strip()
    if _YES_NO.match(text):
        return "yes/no + rationale"
    if _BARE_NUMBER.fullmatch(text):
        return "number"
    if _DIGIT.search(text):
        return "text with figures"
    return "text only"


@dataclass(frozen=True)
class DatasetStats:
    n_questions: int
    n_companies: int
    n_docs: int
    corpus_docs: int
    corpus_companies: int
    question_types: dict[str, int]
    doc_types: dict[str, int]
    periods: dict[int, int]
    evidence_items: int
    evidence_per_question: dict[int, int]
    multi_page_questions: int
    answer_kinds: dict[str, int]
    answer_kinds_by_type: dict[str, dict[str, int]]


def doc_type_of(question: Question, documents: Mapping[str, Document]) -> str:
    doc = documents.get(question.doc_name)
    return doc.doc_type if doc else "unknown"


def compute_stats(questions: Sequence[Question], documents: Mapping[str, Document]) -> DatasetStats:
    periods = Counter(
        documents[q.doc_name].doc_period for q in questions if q.doc_name in documents
    )
    by_type: dict[str, dict[str, int]] = {}
    for qtype in QUESTION_TYPES:
        kinds = Counter(answer_kind(q.answer) for q in questions if q.question_type == qtype)
        by_type[qtype] = dict(kinds.most_common())
    return DatasetStats(
        n_questions=len(questions),
        n_companies=len({q.company for q in questions}),
        n_docs=len({q.doc_name for q in questions}),
        corpus_docs=len(documents),
        corpus_companies=len({d.company for d in documents.values()}),
        question_types=dict(Counter(q.question_type for q in questions).most_common()),
        doc_types=dict(Counter(doc_type_of(q, documents) for q in questions).most_common()),
        periods=dict(sorted(periods.items())),
        evidence_items=sum(len(q.evidence) for q in questions),
        evidence_per_question=dict(sorted(Counter(len(q.evidence) for q in questions).items())),
        multi_page_questions=sum(1 for q in questions if len(q.evidence_pages) > 1),
        answer_kinds=dict(Counter(answer_kind(q.answer) for q in questions).most_common()),
        answer_kinds_by_type=by_type,
    )
