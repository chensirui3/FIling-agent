"""Judge calibration: a person grades a blind sample of judge-graded answers.

The LLM judge grades every prose answer, which is about 60% of FinanceBench plus the fresh text
questions. Calibration checks the judge against a human on a stratified random sample:

- mostly answers the judge called correct, where a lenient judge would inflate scores;
- some it called incorrect, where a harsh one would deflate them.

The sheet shows what the judge saw (question, answer key, justification, response) and never
the judge's verdict. Items are shuffled and carry neutral ids. Agreement is reported per
stratum and, weighted by how common each verdict is, overall; with Cohen's kappa.

The sheet quotes FinanceBench questions and answers (CC-BY-NC-4.0), so it stays out of git;
the verdicts are saved separately without that text.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from filing_agent.agent.answer import FinalAnswer
from filing_agent.data.financebench import Question
from filing_agent.eval.judge import format_response
from filing_agent.eval.stats import wilson_interval

Verdict = Literal["correct", "incorrect", "unsure"]
SHEET_FILE = "judge_sheet.md"
SAMPLE_FILE = "sample.json"
LABELS_FILE = "labels.json"
AGREEMENT_FILE = "agreement.json"

RUBRIC = """\
Grade each response against the answer key, as the judge was asked to. Treat the answer key as
correct, even where you would have answered differently.

- **correct:** the same answer as the key to what the question asks. Key figures agree up to
  rounding, and an equivalent amount in another unit counts ($1.58 billion for $1,577 million).
  Different wording, a different valid line of reasoning, or extra correct detail is fine. For
  qualitative questions, a different but reasonable answer is correct if it does not
  contradict the key.
- **incorrect:** a different conclusion, key figures off beyond rounding, the asked-for thing
  left out, or reasoning or figures that contradict the key even when the conclusion matches.
- **unsure:** you cannot tell. Use it sparingly; unsure items are left out of the agreement.

Write `correct`, `incorrect` or `unsure` after **Your verdict:** for every item, save the file,
and tell Claude it is done."""


@dataclass(frozen=True)
class CalibrationItem:
    item: str  # neutral id shown on the sheet, e.g. "C07"
    run_id: str
    question_id: str
    rep: int
    judge_label: str  # correct or incorrect


def stratified_sample(
    graded: Sequence[CalibrationItem], n_correct: int, n_incorrect: int, seed: int
) -> list[CalibrationItem]:
    """`n_correct` and `n_incorrect` items drawn at random from each judge verdict, shuffled
    together and renumbered so their order and ids reveal nothing."""
    rng = random.Random(f"calibration:{seed}")
    by_label: dict[str, list[CalibrationItem]] = {"correct": [], "incorrect": []}
    for item in graded:
        if item.judge_label in by_label:
            by_label[item.judge_label].append(item)
    chosen = rng.sample(by_label["correct"], min(n_correct, len(by_label["correct"])))
    chosen += rng.sample(by_label["incorrect"], min(n_incorrect, len(by_label["incorrect"])))
    rng.shuffle(chosen)
    return [
        CalibrationItem(f"C{i:02d}", c.run_id, c.question_id, c.rep, c.judge_label)
        for i, c in enumerate(chosen, start=1)
    ]


def sheet(
    items: Sequence[CalibrationItem],
    questions: Mapping[str, Question],
    answers: Mapping[tuple[str, str, int], FinalAnswer],
) -> str:
    lines = ["# Judge calibration sheet", "", RUBRIC, ""]
    for item in items:
        question = questions[item.question_id]
        answer = answers[(item.run_id, item.question_id, item.rep)]
        response = format_response(answer).strip().replace("\n", "\n> ")
        lines += [
            f"## {item.item}",
            "",
            f"**Question:** {' '.join(question.question.split())}",
            "",
            f"**Answer key:** {' '.join(question.answer.split())}",
        ]
        if question.justification:
            lines += ["", f"**Key's justification:** {' '.join(question.justification.split())}"]
        lines += ["", "**Response:**", f"> {response}", "", "**Your verdict:** ", ""]
    return "\n".join(lines)


_ITEM = re.compile(r"^## (C\d+)\s*$")
_VERDICT = re.compile(r"^\*\*Your verdict:\*\*\s*(.*)$")


def read_verdicts(text: str) -> dict[str, Verdict | None]:
    """item -> verdict written on the sheet (None where nothing usable was written)."""
    verdicts: dict[str, Verdict | None] = {}
    current: str | None = None
    for line in text.splitlines():
        if match := _ITEM.match(line.strip()):
            current = match.group(1)
        elif (match := _VERDICT.match(line.strip())) and current:
            word = match.group(1).strip().strip("*`_ ").lower()
            verdicts[current] = (
                "correct"
                if word.startswith("c")
                else "incorrect"
                if word.startswith("i")
                else "unsure"
                if word.startswith("u")
                else None
            )
    return verdicts


@dataclass(frozen=True)
class Agreement:
    labeled: int  # items with a correct or incorrect human verdict
    unsure: int
    missing: int
    agree_when_judge_correct: tuple[int, int]  # (agree, n)
    agree_when_judge_incorrect: tuple[int, int]
    raw_agreement: float | None  # on the sample
    weighted_agreement: float | None  # strata weighted by how often the judge gives each verdict
    kappa: float | None
    judge_correct_ci95: tuple[float, float] | None  # Wilson interval for the leniency stratum


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(first[k] * second[k] for k in set(first) | set(second)) / n**2
    return None if expected == 1 else (observed - expected) / (1 - expected)


def agreement(
    items: Sequence[CalibrationItem],
    verdicts: Mapping[str, Verdict | None],
    population: Mapping[str, int],
) -> Agreement:
    pairs = [
        (item.judge_label, verdict)
        for item in items
        if (verdict := verdicts.get(item.item)) in ("correct", "incorrect")
    ]

    def stratum(label: str) -> tuple[int, int]:
        rows = [human for judge, human in pairs if judge == label]
        return sum(h == label for h in rows), len(rows)

    correct, incorrect = stratum("correct"), stratum("incorrect")
    rates = {
        label: a / n for label, (a, n) in (("correct", correct), ("incorrect", incorrect)) if n
    }
    total = sum(population.get(label, 0) for label in rates)
    weighted = (
        sum(rate * population.get(label, 0) for label, rate in rates.items()) / total
        if total
        else None
    )
    return Agreement(
        labeled=len(pairs),
        unsure=sum(1 for item in items if verdicts.get(item.item) == "unsure"),
        missing=sum(1 for item in items if verdicts.get(item.item) is None),
        agree_when_judge_correct=correct,
        agree_when_judge_incorrect=incorrect,
        raw_agreement=sum(a == b for a, b in pairs) / len(pairs) if pairs else None,
        weighted_agreement=weighted,
        kappa=cohen_kappa(pairs),
        judge_correct_ci95=wilson_interval(*correct) if correct[1] else None,
    )


def save_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def items_to_json(items: Sequence[CalibrationItem]) -> list[dict[str, object]]:
    return [asdict(item) for item in items]


def items_from_json(rows: Sequence[Mapping[str, object]]) -> list[CalibrationItem]:
    return [
        CalibrationItem(
            item=str(r["item"]),
            run_id=str(r["run_id"]),
            question_id=str(r["question_id"]),
            rep=int(str(r["rep"])),
            judge_label=str(r["judge_label"]),
        )
        for r in rows
    ]
