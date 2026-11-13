"""Grade every row of a run: deterministic rules first, then the judge for prose answers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic

from filing_agent.data.financebench import Question
from filing_agent.eval.grading import Grade, grade_answer
from filing_agent.eval.judge import Judge, JudgeError
from filing_agent.eval.runner import ResultRow, read_jsonl

GRADES_FILE = "grades.jsonl"


def apply_judge(grade: Grade, question: Question, row: ResultRow, judge: Judge) -> Grade:
    """Judge a pending grade. A failed judgment leaves it pending (retried on re-grade)."""
    assert row.answer is not None
    try:
        result = judge.grade(question, row.answer)
    except (JudgeError, anthropic.APIError) as exc:
        return grade.model_copy(update={"note": f"judge failed: {exc}"})
    verdict = result.output.verdict
    check = result.figure_check
    figures_say_right = (
        check.reference_figures > 0
        and check.all_matched
        and result.output.conclusion_matches
        and not result.output.contradicts_reference
    )
    needs_review = (verdict == "correct" and not check.all_matched) or (
        verdict == "incorrect" and figures_say_right
    )
    return grade.model_copy(
        update={"label": verdict, "method": "judge", "judge": result, "needs_review": needs_review}
    )


def grade_rows(
    rows: Sequence[ResultRow],
    questions: Mapping[str, Question],
    *,
    judge: Judge | None,
    overrides: dict[str, dict[str, object]] | None = None,
    concurrency: int = 4,
    on_done: Callable[[Grade], None] | None = None,
) -> list[Grade]:
    grades: dict[tuple[str, int], Grade] = {}
    pending: list[tuple[Grade, ResultRow]] = []
    for row in rows:
        question = questions[row.question_id]
        grade = grade_answer(question, row.status, row.answer, overrides)
        grade = grade.model_copy(update={"rep": row.rep})
        grades[(row.question_id, row.rep)] = grade
        if grade.method == "pending_judge" and judge is not None:
            pending.append((grade, row))
        elif on_done:
            on_done(grade)

    def judged(item: tuple[Grade, ResultRow]) -> Grade:
        grade, row = item
        result = apply_judge(grade, questions[row.question_id], row, judge)  # type: ignore[arg-type]
        if on_done:
            on_done(result)
        return result

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for result in pool.map(judged, pending):
            grades[(result.question_id, result.rep)] = result
    return [grades[key] for key in sorted(grades)]


def write_grades(run_dir: Path, grades: Sequence[Grade]) -> Path:
    path = run_dir / GRADES_FILE
    with path.open("w", encoding="utf-8") as handle:
        for grade in grades:
            handle.write(grade.model_dump_json() + "\n")
    return path


def load_grades(run_dir: Path) -> list[Grade]:
    return [Grade.model_validate(g) for g in read_jsonl(run_dir / GRADES_FILE)]
