from __future__ import annotations

import json
from pathlib import Path

from filing_agent.config import PROJECT_ROOT
from filing_agent.eval.chart import DEFAULT_OUT, LADDER, Bar, ladder_bars, ladder_svg


def _run(root: Path, run_id: str, name: str, split: str, correct: int, n: int) -> None:
    run = root / run_id
    run.mkdir(parents=True)
    config = {"experiment": {"name": name}, "split": split}
    metrics = {"graded": n, "pending_judge": 0, "labels": {"correct": correct}}
    (run / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (run / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")


def test_bars_take_the_largest_dev_run_in_ladder_order(tmp_path: Path) -> None:
    _run(tmp_path, "1-smoke", "b4_full_agent", "dev", 4, 4)
    _run(tmp_path, "2-dev", "b4_full_agent", "dev", 48, 50)
    _run(tmp_path, "3-test", "b4_full_agent", "test", 85, 100)
    _run(tmp_path, "4-dev", "b0_closed_book", "dev", 40, 50)
    _run(tmp_path, "5-dev", "b4_effort_low", "dev", 46, 50)  # not part of the ladder
    bars = ladder_bars(tmp_path)
    assert [(b.experiment, b.correct, b.n) for b in bars] == [
        ("b0_closed_book", 40, 50),
        ("b4_full_agent", 48, 50),
    ]
    assert bars[1].text == "96% (86–99%)"  # bounds rounded outward


def test_svg_draws_each_bar_to_scale() -> None:
    bars = [
        Bar("b0_closed_book", "Closed <book>", 25, 50),
        Bar("b4_full_agent", "Full agent", 50, 50),
    ]
    svg = ladder_svg(bars, "Title & more")
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "Closed &lt;book&gt;" in svg and "Title &amp; more" in svg
    assert 'width="170.0"' in svg and 'width="340.0"' in svg  # 50% and 100% of the plot
    assert "50% (36–64%)" in svg and "100% (92–100%)" in svg


def test_committed_chart_matches_the_saved_runs() -> None:
    bars = ladder_bars(PROJECT_ROOT / "results" / "runs")
    assert [b.experiment for b in bars] == list(LADDER)
    title = f"Baseline ladder on FinanceBench dev ({bars[0].n} questions, Claude Opus 5.5)"
    assert DEFAULT_OUT.read_text(encoding="utf-8") == ladder_svg(bars, title)
