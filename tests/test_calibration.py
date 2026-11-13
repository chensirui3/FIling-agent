from __future__ import annotations

import pytest

from filing_agent.agent.answer import FinalAnswer
from filing_agent.eval.calibration import (
    CalibrationItem,
    agreement,
    cohen_kappa,
    read_verdicts,
    sheet,
    stratified_sample,
)
from tests.factories import question


def _graded(n_correct: int, n_incorrect: int) -> list[CalibrationItem]:
    items = [CalibrationItem("", "run", f"q{i}", 1, "correct") for i in range(n_correct)]
    items += [CalibrationItem("", "run", f"x{i}", 1, "incorrect") for i in range(n_incorrect)]
    return items


def test_sample_is_stratified_shuffled_and_renumbered() -> None:
    sample = stratified_sample(_graded(20, 5), n_correct=6, n_incorrect=3, seed=1)
    labels = [s.judge_label for s in sample]
    assert labels.count("correct") == 6 and labels.count("incorrect") == 3
    assert [s.item for s in sample] == [f"C{i:02d}" for i in range(1, 10)]
    assert labels != sorted(labels)  # verdicts are mixed, not in blocks
    assert sample == stratified_sample(_graded(20, 5), n_correct=6, n_incorrect=3, seed=1)
    small = stratified_sample(_graded(2, 1), n_correct=6, n_incorrect=3, seed=1)
    assert len(small) == 3  # never more than there are


def test_sheet_is_blind_to_the_judge() -> None:
    item = CalibrationItem("C01", "run", "q1", 1, "SECRET-VERDICT")
    answer = FinalAnswer.model_validate(
        {
            "answer_status": "answered",
            "answer": "Capex was $1,577 million.",
            "sources": [],
            "confidence": "high",
        }
    )
    text = sheet(
        [item],
        {"q1": question("q1", "Acme", "ACME_2018_10K")},
        {("run", "q1", 1): answer},
    )
    assert "SECRET-VERDICT" not in text
    assert "## C01" in text and "Capex was $1,577 million." in text
    assert sum(line.startswith("**Your verdict:**") for line in text.splitlines()) == 1


def test_read_verdicts_accepts_loose_spellings() -> None:
    text = "\n".join(
        [
            "## C01",
            "**Your verdict:** correct",
            "## C02",
            "**Your verdict:** **Incorrect**",
            "## C03",
            "**Your verdict:** c",
            "## C04",
            "**Your verdict:** unsure",
            "## C05",
            "**Your verdict:** ",
            "## C06",
            "**Your verdict:** maybe",
        ]
    )
    assert read_verdicts(text) == {
        "C01": "correct",
        "C02": "incorrect",
        "C03": "correct",
        "C04": "unsure",
        "C05": None,
        "C06": None,
    }


def test_agreement_by_stratum_weighted_and_kappa() -> None:
    items = [CalibrationItem(f"C{i}", "run", f"q{i}", 1, "correct") for i in range(1, 5)]
    items += [CalibrationItem(f"C{i}", "run", f"q{i}", 1, "incorrect") for i in (5, 6)]
    human = {"C1": "correct", "C2": "correct", "C3": "incorrect", "C4": "correct"}
    human |= {"C5": "incorrect", "C6": "correct"}
    result = agreement(items, human, {"correct": 90, "incorrect": 10})  # type: ignore[arg-type]
    assert result.agree_when_judge_correct == (3, 4)
    assert result.agree_when_judge_incorrect == (1, 2)
    assert result.raw_agreement == pytest.approx(4 / 6)
    assert result.weighted_agreement == pytest.approx(0.75 * 0.9 + 0.5 * 0.1)
    assert result.kappa == pytest.approx(
        cohen_kappa([(i.judge_label, human[i.item]) for i in items])
    )
    assert cohen_kappa([("correct", "correct"), ("incorrect", "incorrect")]) == 1.0


def test_unsure_and_missing_are_left_out() -> None:
    items = [
        CalibrationItem("C1", "run", "q1", 1, "correct"),
        CalibrationItem("C2", "run", "q2", 1, "correct"),
        CalibrationItem("C3", "run", "q3", 1, "correct"),
    ]
    result = agreement(items, {"C1": "correct", "C2": "unsure"}, {"correct": 10})
    assert (result.labeled, result.unsure, result.missing) == (1, 1, 1)
    assert result.agree_when_judge_correct == (1, 1)
