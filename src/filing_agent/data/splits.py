"""Dev/test split grouped by company, so no company's filings appear on both sides.

Grouping by company makes it structurally impossible for a fix tuned on dev to exploit
company-specific quirks that also appear in test. Within that constraint, a seeded random search
picks the company set whose dev questions best match the overall mix of question types and
filing types.
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from filing_agent.data.financebench import Document, Question
from filing_agent.data.stats import doc_type_of

Part = Literal["dev", "test"]
METHOD = "company-grouped; seeded random search matching question-type and filing-type mix"


@dataclass(frozen=True)
class Split:
    name: str
    seed: int
    dev: tuple[str, ...]
    test: tuple[str, ...]
    dev_companies: tuple[str, ...]
    test_companies: tuple[str, ...]

    def ids(self, part: Part) -> tuple[str, ...]:
        return self.dev if part == "dev" else self.test


def filing_group(question: Question, documents: Mapping[str, Document]) -> str:
    """Annual reports vs. everything else (10-Q, 8-K, earnings releases)."""
    return "10k" if doc_type_of(question, documents) in {"10k", "10k_annualreport"} else "other"


@dataclass(frozen=True)
class _CompanyProfile:
    n: int
    types: Counter[str]
    groups: Counter[str]


def make_company_split(
    questions: Sequence[Question],
    documents: Mapping[str, Document],
    *,
    dev_size: int = 50,
    seed: int = 13,
    trials: int = 20_000,
    name: str = "financebench_v1",
) -> Split:
    if not 0 < dev_size < len(questions):
        raise ValueError(f"dev_size must be between 1 and {len(questions) - 1}")

    by_company: dict[str, list[Question]] = defaultdict(list)
    for question in questions:
        by_company[question.company].append(question)
    companies = sorted(by_company)
    profiles = {
        c: _CompanyProfile(
            n=len(qs),
            types=Counter(q.question_type for q in qs),
            groups=Counter(filing_group(q, documents) for q in qs),
        )
        for c, qs in by_company.items()
    }

    fraction = dev_size / len(questions)
    type_target = {t: n * fraction for t, n in Counter(q.question_type for q in questions).items()}
    group_target = {
        g: n * fraction for g, n in Counter(filing_group(q, documents) for q in questions).items()
    }

    def score(chosen: Sequence[str]) -> float:
        n = sum(profiles[c].n for c in chosen)
        types: Counter[str] = Counter()
        groups: Counter[str] = Counter()
        for c in chosen:
            types.update(profiles[c].types)
            groups.update(profiles[c].groups)
        return (
            2.0 * abs(n - dev_size)
            + sum(abs(types[t] - target) for t, target in type_target.items())
            + sum(abs(groups[g] - target) for g, target in group_target.items())
        )

    rng = random.Random(seed)
    best_score = float("inf")
    best: tuple[str, ...] = ()
    for _ in range(trials):
        order = companies[:]
        rng.shuffle(order)
        chosen: list[str] = []
        count = 0
        for company in order:
            if count >= dev_size:
                break
            chosen.append(company)
            count += profiles[company].n
        # Also consider stopping one company earlier (undershooting instead of overshooting).
        for candidate in (chosen, chosen[:-1]):
            if not candidate:
                continue
            key = tuple(sorted(candidate))
            value = score(key)
            if value < best_score:
                best_score, best = value, key

    dev_set = set(best)
    test_companies = tuple(c for c in companies if c not in dev_set)
    return Split(
        name=name,
        seed=seed,
        dev=tuple(sorted(q.id for c in best for q in by_company[c])),
        test=tuple(sorted(q.id for c in test_companies for q in by_company[c])),
        dev_companies=best,
        test_companies=test_companies,
    )


def summarize(
    split: Split, questions: Sequence[Question], documents: Mapping[str, Document]
) -> dict[str, Any]:
    by_id = {q.id: q for q in questions}
    summary: dict[str, Any] = {}
    for part in ("dev", "test"):
        qs = [by_id[i] for i in split.ids(part)]
        summary[part] = {
            "questions": len(qs),
            "companies": len({q.company for q in qs}),
            "question_type": dict(sorted(Counter(q.question_type for q in qs).items())),
            "filing_group": dict(sorted(Counter(filing_group(q, documents) for q in qs).items())),
        }
    return summary


def save_split(split: Split, path: Path, summary: Mapping[str, Any], dev_size: int) -> None:
    payload = {
        "name": split.name,
        "method": METHOD,
        "seed": split.seed,
        "dev_size_target": dev_size,
        "summary": summary,
        "dev_companies": list(split.dev_companies),
        "test_companies": list(split.test_companies),
        "dev": list(split.dev),
        "test": list(split.test),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_split(path: Path) -> Split:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Split(
        name=payload["name"],
        seed=int(payload["seed"]),
        dev=tuple(payload["dev"]),
        test=tuple(payload["test"]),
        dev_companies=tuple(payload["dev_companies"]),
        test_companies=tuple(payload["test_companies"]),
    )
