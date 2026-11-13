"""The fresh question set on disk, its dev/test split and the human review sheet."""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from filing_agent.data.financebench import Question
from filing_agent.data.splits import Split
from filing_agent.fresh.filings import FreshFiling
from filing_agent.fresh.questions import display_name

SPLIT_NAME = "fresh_v1"
SPLIT_METHOD = "company-grouped; dev = the financebench_v1 dev companies"


def save_questions(path: Path, questions: Iterable[Question]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [q.model_dump_json(exclude_none=True) for q in questions]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_questions(path: Path) -> list[Question]:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. Run `fa fresh questions` first.")
    return [
        Question.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_reviews(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): dict(v or {}) for k, v in (data.get("reviews") or {}).items()}


def apply_review(question: Question, review: Mapping[str, Any] | None) -> Question | None:
    """None if rejected; otherwise the question with any fixed fields and the verdict in meta."""
    if not review:
        return question
    if review.get("verdict") == "reject":
        return None
    meta = dict(question.meta or {})
    meta["review"] = {k: review[k] for k in ("verdict", "reviewer", "note") if k in review}
    updates = {k: str(review[k]).strip() for k in ("question", "answer") if k in review}
    return question.model_copy(update={**updates, "meta": meta})


def company_split(
    questions: Sequence[Question], dev_companies: Iterable[str], name: str = SPLIT_NAME
) -> Split:
    """Same company partition as FinanceBench's split, so dev work never sees a test company."""
    dev_set = set(dev_companies)
    companies = sorted({q.company for q in questions})
    return Split(
        name=name,
        seed=0,
        dev=tuple(q.id for q in questions if q.company in dev_set),
        test=tuple(q.id for q in questions if q.company not in dev_set),
        dev_companies=tuple(c for c in companies if c in dev_set),
        test_companies=tuple(c for c in companies if c not in dev_set),
    )


def _kind(question: Question) -> str:
    return str((question.meta or {}).get("kind", question.question_type))


def split_summary(split: Split, questions: Sequence[Question]) -> dict[str, Any]:
    by_id = {q.id: q for q in questions}
    summary: dict[str, Any] = {}
    for part in ("dev", "test"):
        qs = [by_id[i] for i in split.ids(part)]
        summary[part] = {
            "questions": len(qs),
            "companies": len({q.company for q in qs}),
            "kind": dict(sorted(Counter(_kind(q) for q in qs).items())),
        }
    return summary


def save_split(split: Split, questions: Sequence[Question], path: Path) -> None:
    payload = {
        "name": split.name,
        "method": SPLIT_METHOD,
        "seed": split.seed,
        "summary": split_summary(split, questions),
        "dev_companies": list(split.dev_companies),
        "test_companies": list(split.test_companies),
        "dev": list(split.dev),
        "test": list(split.test),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def review_sample(questions: Sequence[Question], size: int, seed: int) -> list[Question]:
    """A seeded sample spread over question kinds and companies (one question per company)."""
    rng = random.Random(f"review:{seed}")
    by_kind: dict[str, list[Question]] = {}
    for question in questions:
        by_kind.setdefault(_kind(question), []).append(question)
    for pool in by_kind.values():
        rng.shuffle(pool)
    sample: list[Question] = []
    seen: set[str] = set()
    kinds = sorted(by_kind)
    while len(sample) < size and any(by_kind.values()):
        for kind in kinds:
            pool = by_kind[kind]
            while pool and pool[-1].company in seen:
                pool.pop()
            if pool and len(sample) < size:
                question = pool.pop()
                sample.append(question)
                seen.add(question.company)
        if all(not by_kind[k] for k in kinds):
            break
    return sample


def _numeric_item(question: Question) -> list[str]:
    rows = [f"  - `{e.text}` (page {e.page})" for e in question.evidence]
    return [
        f"- **How:** {question.justification}",
        "- **Rows in our parse:**",
        *rows,
        f"- **SEC XBRL API:** {(question.meta or {}).get('xbrl_api', 'n/a')}",
    ]


def _text_item(question: Question) -> list[str]:
    (evidence,) = question.evidence
    return [f"- **Quote (page {evidence.page}):** {evidence.text}"]


def review_sheet(
    sample: Sequence[Question], filings: Mapping[str, FreshFiling], split: Split
) -> str:
    dev = set(split.dev)
    lines = [
        "# Fresh benchmark: review sheet",
        "",
        "Numeric golds were computed from the filing's tagged (inline-XBRL) figures; *SEC XBRL "
        "API* says whether the SEC's API reports the same figures for the same filing and "
        "period. Text questions were drafted by Claude from one page and checked "
        "automatically (verbatim quote, figures on the page) and by a first-pass review.",
        "",
        "To check an item, open the filing, search for the row label or a phrase from the quote "
        "(Ctrl+F), and confirm the figure, the period and the answer. Reply with the numbers of "
        "any items that are wrong or ambiguous, and why.",
        "",
    ]
    for n, question in enumerate(sample, start=1):
        meta = question.meta or {}
        filing = filings[question.doc_name]
        kind = meta.get("kind", "")
        review = meta.get("review") or {}
        lines += [
            f"## {n}. {display_name(question.company)}: {kind}, {meta.get('metric', '')} "
            f"({'dev' if question.id in dev else 'test'})",
            "",
            f"- **Question:** {question.question}",
            f"- **Gold:** {question.answer}",
            *(_text_item(question) if kind == "text" else _numeric_item(question)),
            *([f"- **First-pass note:** {review['note']}"] if review.get("note") else []),
            f"- **Filing:** [{filing.form}, filed {filing.filing_date}]({filing.url})",
            f"- **ID:** `{question.id}`",
            "",
        ]
    return "\n".join(lines)
