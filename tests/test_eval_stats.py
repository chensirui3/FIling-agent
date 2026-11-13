from __future__ import annotations

import pytest

from filing_agent.eval.stats import (
    majority,
    mcnemar_exact,
    noise_floor,
    paired_bootstrap,
    per_question_mean,
    wilson_interval,
)


def test_wilson_interval() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and high == pytest.approx(0.2775, abs=1e-3)
    low, high = wilson_interval(75, 100)
    assert (low, high) == (pytest.approx(0.6566, abs=1e-3), pytest.approx(0.8248, abs=1e-3))


def test_noise_floor() -> None:
    assert noise_floor(100, 3) == pytest.approx(0.0577, abs=1e-4)
    assert noise_floor(50, 2) == pytest.approx(0.1)


def test_paired_bootstrap() -> None:
    same = paired_bootstrap([1, 0, 1, 0], [1, 0, 1, 0])
    assert (same.delta, same.low, same.high) == (0.0, 0.0, 0.0)
    before = [1.0] * 50 + [0.0] * 50
    after = [1.0] * 100
    result = paired_bootstrap(before, after, seed=1)
    assert result.delta == pytest.approx(0.5)
    assert 0.39 < result.low < 0.5 < result.high < 0.61
    assert paired_bootstrap(before, after, seed=1) == result  # seeded
    with pytest.raises(ValueError, match="paired"):
        paired_bootstrap([1, 0], [1])


def test_mcnemar_exact() -> None:
    fixed_only = mcnemar_exact([False] * 10, [True] * 10)
    assert (fixed_only.fixed, fixed_only.broken) == (10, 0)
    assert fixed_only.p_value == pytest.approx(2 / 2**10)
    balanced = mcnemar_exact([True] * 5 + [False] * 5, [False] * 5 + [True] * 5)
    assert balanced.p_value == 1.0
    assert mcnemar_exact([True, False], [True, False]).p_value == 1.0


def test_per_question_mean_and_majority() -> None:
    scores = per_question_mean([("a", True), ("a", False), ("a", True), ("b", False), ("b", True)])
    assert scores == {"a": pytest.approx(2 / 3), "b": 0.5}
    assert majority(scores) == {"a": True, "b": False}  # ties count as not correct
