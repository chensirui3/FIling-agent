"""Run metrics and paired comparisons, always recomputed from the per-row grades."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

import numpy as np
from pydantic import BaseModel

from filing_agent.data.financebench import Document, Question
from filing_agent.eval.grading import Grade
from filing_agent.eval.runner import ResultRow
from filing_agent.eval.stats import (
    bootstrap_ci,
    majority,
    mcnemar_exact,
    paired_bootstrap,
    per_question_mean,
    wilson_interval,
)

LABELS = ("correct", "incorrect", "refusal", "no_answer")


class GroupScore(BaseModel):
    n: int
    correct: int
    accuracy: float


class RunMetrics(BaseModel):
    run_id: str
    experiment: str
    questions: int
    rows: int
    graded: int
    pending_judge: int
    labels: dict[str, int]
    accuracy: float | None
    accuracy_ci95: tuple[float, float] | None
    accuracy_per_rep: dict[int, float]
    refusal_rate: float | None
    no_answer_rate: float | None
    by_question_type: dict[str, GroupScore]
    by_kind: dict[str, GroupScore] = {}  # extraction / derived / text (fresh set)
    by_doc_type: dict[str, GroupScore]
    by_method: dict[str, GroupScore]
    numeric_sensitivity: dict[str, float]
    needs_review: int
    infra_failures: int
    cost_usd: dict[str, float]
    wall_s: dict[str, float]
    tool_calls: dict[str, float]


def accuracy_interval(per_question: Sequence[float]) -> tuple[float, float]:
    """95% interval for accuracy. One answer per question: Wilson, which stays honest at 0% and
    100% (0 of 114 is 0-3.3%, not a zero-width interval). Several repetitions: a bootstrap over
    questions, which keeps each question's repetitions together."""
    if all(v in (0.0, 1.0) for v in per_question):
        return wilson_interval(int(sum(per_question)), len(per_question))
    return bootstrap_ci(per_question)


def _group(grades: Iterable[Grade], key: Callable[[Grade], str]) -> dict[str, GroupScore]:
    buckets: dict[str, list[bool]] = {}
    for grade in grades:
        buckets.setdefault(key(grade), []).append(grade.label == "correct")
    return {
        name: GroupScore(n=len(v), correct=sum(v), accuracy=sum(v) / len(v))
        for name, v in sorted(buckets.items())
    }


def compute_metrics(
    run_id: str,
    experiment: str,
    rows: Sequence[ResultRow],
    grades: Sequence[Grade],
    errors: Sequence[Mapping[str, Any]],
    questions: Mapping[str, Question],
    documents: Mapping[str, Document],
) -> RunMetrics:
    graded = [g for g in grades if g.label is not None]
    labels = Counter(g.label for g in graded)
    accuracy = labels["correct"] / len(graded) if graded else None
    per_question = per_question_mean([(g.question_id, g.label == "correct") for g in graded])
    by_rep: dict[int, list[bool]] = {}
    for grade in graded:
        by_rep.setdefault(grade.rep, []).append(grade.label == "correct")
    numeric = [g.numeric for g in graded if g.numeric is not None]

    def doc_type(grade: Grade) -> str:
        doc = documents.get(questions[grade.question_id].doc_name)
        return doc.doc_type if doc else "unknown"

    walls = np.asarray([r.wall_s for r in rows], dtype=float)
    return RunMetrics(
        run_id=run_id,
        experiment=experiment,
        questions=len({r.question_id for r in rows}),
        rows=len(rows),
        graded=len(graded),
        pending_judge=sum(1 for g in grades if g.label is None),
        labels={label: labels.get(label, 0) for label in LABELS},
        accuracy=accuracy,
        accuracy_ci95=accuracy_interval(list(per_question.values())) if per_question else None,
        accuracy_per_rep={rep: sum(v) / len(v) for rep, v in sorted(by_rep.items())},
        refusal_rate=labels["refusal"] / len(graded) if graded else None,
        no_answer_rate=labels["no_answer"] / len(graded) if graded else None,
        by_question_type=_group(graded, lambda g: questions[g.question_id].question_type),
        by_kind=_group(
            [g for g in graded if (questions[g.question_id].meta or {}).get("kind")],
            lambda g: str((questions[g.question_id].meta or {})["kind"]),
        ),
        by_doc_type=_group(graded, doc_type),
        by_method=_group(graded, lambda g: g.method),
        numeric_sensitivity={
            "n": float(len(numeric)),
            "strict": float(np.mean([c.strict for c in numeric])) if numeric else 0.0,
            "default": float(np.mean([c.within_tolerance for c in numeric])) if numeric else 0.0,
            "lenient": float(np.mean([c.lenient for c in numeric])) if numeric else 0.0,
        },
        needs_review=sum(1 for g in graded if g.needs_review),
        infra_failures=len(errors),
        cost_usd={
            "agent_total": float(sum(r.cost_usd or 0.0 for r in rows)),
            "agent_per_row": float(np.mean([r.cost_usd or 0.0 for r in rows])) if rows else 0.0,
            "judge_total": float(sum((g.judge.cost_usd or 0.0) for g in grades if g.judge)),
        },
        wall_s={
            "mean": float(walls.mean()) if walls.size else 0.0,
            "p90": float(np.percentile(walls, 90)) if walls.size else 0.0,
        },
        tool_calls={
            "mean": float(np.mean([r.tool_calls for r in rows])) if rows else 0.0,
            "errors": float(sum(r.tool_errors for r in rows)),
            "internal_errors": float(sum(r.internal_tool_errors for r in rows)),
        },
    )


class Comparison(BaseModel):
    run_a: str
    run_b: str
    questions: int
    accuracy_a: float
    accuracy_b: float
    delta: float
    delta_ci95: tuple[float, float]
    mcnemar_p: float
    fixed: list[str]  # wrong in A (majority of reps), right in B
    broken: list[str]  # right in A, wrong in B


def compare_grades(
    run_a: str, grades_a: Sequence[Grade], run_b: str, grades_b: Sequence[Grade]
) -> Comparison:
    """Paired on the questions graded in both runs (each question's reps averaged)."""
    a = per_question_mean([(g.question_id, g.label == "correct") for g in grades_a if g.label])
    b = per_question_mean([(g.question_id, g.label == "correct") for g in grades_b if g.label])
    common = sorted(set(a) & set(b))
    if not common:
        raise ValueError("the two runs share no graded questions")
    delta = paired_bootstrap([a[q] for q in common], [b[q] for q in common])
    right_a = majority({q: a[q] for q in common})
    right_b = majority({q: b[q] for q in common})
    test = mcnemar_exact([right_a[q] for q in common], [right_b[q] for q in common])
    return Comparison(
        run_a=run_a,
        run_b=run_b,
        questions=len(common),
        accuracy_a=float(np.mean([a[q] for q in common])),
        accuracy_b=float(np.mean([b[q] for q in common])),
        delta=delta.delta,
        delta_ci95=(delta.low, delta.high),
        mcnemar_p=test.p_value,
        fixed=[q for q in common if not right_a[q] and right_b[q]],
        broken=[q for q in common if right_a[q] and not right_b[q]],
    )
