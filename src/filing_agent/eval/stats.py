"""Uncertainty for small evals: Wilson intervals, paired bootstrap, exact McNemar, noise floor.

With 150 questions (100 on test), a single run's accuracy carries a 95% interval of roughly
plus or minus 8 to 10 points, so a change is judged on the same questions before and after:
paired, over repeated runs.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

Z_95 = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """95% interval for a proportion; well behaved near 0 and 1, unlike the normal one."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def noise_floor(n_questions: int, reps: int = 1) -> float:
    """Rough half-width of a paired 95% interval on a pass rate: 1 / sqrt(n * reps)."""
    return 1 / math.sqrt(n_questions * reps)


def bootstrap_ci(
    values: Sequence[float], *, n_boot: int = 10_000, seed: int = 0, confidence: float = 0.95
) -> tuple[float, float]:
    """Percentile interval for a mean, resampling questions (each value is one question)."""
    data = np.asarray(values, dtype=float)
    if data.size == 0:
        raise ValueError("no values")
    rng = np.random.default_rng(seed)
    means = data[rng.integers(0, data.size, size=(n_boot, data.size))].mean(axis=1)
    tail = (1 - confidence) / 2 * 100
    low, high = np.percentile(means, [tail, 100 - tail])
    return (float(low), float(high))


@dataclass(frozen=True)
class PairedDelta:
    delta: float  # mean(after) - mean(before)
    low: float
    high: float
    n: int


def paired_bootstrap(
    before: Sequence[float],
    after: Sequence[float],
    *,
    n_boot: int = 10_000,
    seed: int = 0,
    confidence: float = 0.95,
) -> PairedDelta:
    """Resample questions (keeping each question's before/after pair) to bound the change."""
    a = np.asarray(before, dtype=float)
    b = np.asarray(after, dtype=float)
    if a.shape != b.shape or a.size == 0:
        raise ValueError("before and after must be non-empty and paired by question")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, a.size, size=(n_boot, a.size))
    deltas = b[idx].mean(axis=1) - a[idx].mean(axis=1)
    tail = (1 - confidence) / 2 * 100
    low, high = np.percentile(deltas, [tail, 100 - tail])
    return PairedDelta(float(b.mean() - a.mean()), float(low), float(high), int(a.size))


@dataclass(frozen=True)
class McNemar:
    fixed: int  # wrong before, right after
    broken: int  # right before, wrong after
    p_value: float


def mcnemar_exact(before: Sequence[bool], after: Sequence[bool]) -> McNemar:
    """Exact two-sided McNemar test: a binomial test on the questions that changed."""
    if len(before) != len(after):
        raise ValueError("before and after must be paired by question")
    fixed = sum(1 for x, y in zip(before, after, strict=True) if not x and y)
    broken = sum(1 for x, y in zip(before, after, strict=True) if x and not y)
    n = fixed + broken
    if n == 0:
        return McNemar(fixed, broken, 1.0)
    k = min(fixed, broken)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return McNemar(fixed, broken, min(1.0, 2 * tail))


def per_question_mean(rows: Sequence[tuple[str, bool]]) -> dict[str, float]:
    """Share of repetitions answered correctly, per question id."""
    totals: dict[str, list[int]] = {}
    for qid, correct in rows:
        totals.setdefault(qid, []).append(int(correct))
    return {qid: sum(v) / len(v) for qid, v in sorted(totals.items())}


def majority(scores: Mapping[str, float]) -> dict[str, bool]:
    """Correct in more than half of the repetitions (ties count as not correct)."""
    return {qid: score > 0.5 for qid, score in scores.items()}
