"""Check that each gold evidence passage sits on the page it is labeled with.

Retrieval metrics compare retrieved pages with `evidence_page_num`, so the labels have to agree
with our page numbering before those metrics mean anything. For every evidence item we score each
page of the filing by weighted token containment (how much of the passage's vocabulary, with
multi-digit numbers weighted 3x, appears on the page) and compare the best page with the label.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel

from filing_agent.corpus.parse import ParsedDoc
from filing_agent.data.financebench import Question

Status = Literal["on_page", "off_by_one", "elsewhere", "not_found", "missing_doc"]
STATUSES: tuple[Status, ...] = ("on_page", "off_by_one", "elsewhere", "not_found", "missing_doc")

FOUND_THRESHOLD = 0.6  # best page must contain this share of the passage's weighted tokens
TIE_TOLERANCE = 0.02  # the labeled page counts as best if within this of the top score
MIN_SNIPPET_TOKENS = 5  # shorter passages fall back to the full-page text

_TOKEN = re.compile(r"[a-z]+|\d+(?:[.,]\d+)*")


def tokens(text: str) -> set[str]:
    """Words of 3+ letters and numbers (commas removed), lower-cased."""
    out: set[str] = set()
    for token in _TOKEN.findall(text.lower()):
        if token[0].isdigit():
            out.add(token.replace(",", ""))
        elif len(token) >= 3:
            out.add(token)
    return out


def _weight(token: str) -> int:
    return 3 if token[0].isdigit() and len(token) >= 3 else 1


def containment(passage: set[str], page: set[str]) -> float:
    total = sum(_weight(t) for t in passage)
    if not total:
        return 0.0
    return sum(_weight(t) for t in passage if t in page) / total


class AlignmentRecord(BaseModel):
    question_id: str
    doc_name: str
    evidence_index: int
    labeled_page: int
    best_page: int | None
    labeled_score: float
    best_score: float
    status: Status

    @property
    def offset(self) -> int | None:
        return None if self.best_page is None else self.best_page - self.labeled_page


def _classify(labeled: int, best: int, labeled_score: float, best_score: float) -> Status:
    if best_score < FOUND_THRESHOLD:
        return "not_found"
    if labeled_score >= best_score - TIE_TOLERANCE:
        return "on_page"
    if abs(best - labeled) == 1:
        return "off_by_one"
    return "elsewhere"


def align_evidence(
    questions: Sequence[Question], load_doc: Callable[[str], ParsedDoc | None]
) -> list[AlignmentRecord]:
    page_tokens: dict[str, list[set[str]] | None] = {}
    records: list[AlignmentRecord] = []
    for question in questions:
        for index, evidence in enumerate(question.evidence):
            if evidence.doc_name not in page_tokens:
                doc = load_doc(evidence.doc_name)
                page_tokens[evidence.doc_name] = (
                    None if doc is None else [tokens(p.text) for p in doc.pages]
                )
            pages = page_tokens[evidence.doc_name]
            labeled = evidence.page
            if not pages:
                records.append(
                    AlignmentRecord(
                        question_id=question.id,
                        doc_name=evidence.doc_name,
                        evidence_index=index,
                        labeled_page=labeled,
                        best_page=None,
                        labeled_score=0.0,
                        best_score=0.0,
                        status="missing_doc",
                    )
                )
                continue
            passage = tokens(evidence.text)
            if len(passage) < MIN_SNIPPET_TOKENS:
                passage = tokens(evidence.full_page_text) or passage
            scores = [containment(passage, page) for page in pages]
            best = max(range(len(scores)), key=lambda i: (scores[i], -abs(i - labeled)))
            labeled_score = scores[labeled] if 0 <= labeled < len(scores) else 0.0
            records.append(
                AlignmentRecord(
                    question_id=question.id,
                    doc_name=evidence.doc_name,
                    evidence_index=index,
                    labeled_page=labeled,
                    best_page=best,
                    labeled_score=round(labeled_score, 3),
                    best_score=round(scores[best], 3),
                    status=_classify(labeled, best, labeled_score, scores[best]),
                )
            )
    return records


def summarize_alignment(records: Sequence[AlignmentRecord]) -> dict[str, object]:
    counts = Counter(r.status for r in records)
    offsets = Counter(r.offset for r in records if r.status in ("off_by_one", "elsewhere"))
    total = len(records)
    return {
        "evidence_items": total,
        "status": {s: counts.get(s, 0) for s in STATUSES},
        "on_page_rate": round(counts.get("on_page", 0) / total, 4) if total else 0.0,
        "offsets": dict(sorted(offsets.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))),
    }
