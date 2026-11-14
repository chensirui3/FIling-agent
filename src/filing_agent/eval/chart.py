"""`fa eval chart`: the dev baseline ladder as an SVG, drawn from the saved run metrics.

The chart is plain SVG text with no plotting dependency. It reads only files that are in git
(`config.json` and `metrics.json` of each run), so a clean clone redraws it exactly.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from html import escape
from pathlib import Path

import typer
from rich.console import Console

from filing_agent.config import PROJECT_ROOT, get_settings
from filing_agent.eval.stats import wilson_interval

console = Console()
DEFAULT_OUT = PROJECT_ROOT / "docs" / "figures" / "dev_ladder.svg"
MAIN = "b4_full_agent"
# Experiment -> label, in the order drawn: least help at the top, the oracle ceiling last.
LADDER: dict[str, str] = {
    "b0_closed_book": "Closed book (question only)",
    "b3r_classic_rag": "Classic RAG (one search, one call)",
    "b3_shared_text": "Text tools, all filings",
    "b2_single_filing": "Text tools, the right filing given",
    "b5_no_calculator": "Full agent without the calculator",
    MAIN: "Full agent",
    "b1_oracle_evidence": "Evidence pages given (ceiling)",
}


@dataclass(frozen=True)
class Bar:
    experiment: str
    label: str
    correct: int
    n: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.n

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.correct, self.n)

    @property
    def text(self) -> str:
        """Accuracy with its interval, bounds rounded outward as in the results tables."""
        low, high = self.interval
        return (
            f"{self.accuracy:.0%} ({math.floor(low * 100 + 1e-9)}–{math.ceil(high * 100 - 1e-9)}%)"
        )


def ladder_bars(runs_dir: Path, split: str = "dev") -> list[Bar]:
    """One bar per ladder experiment: its largest graded run on `split` (the latest on a tie)."""
    best: dict[str, tuple[int, str, int]] = {}  # experiment -> (rows, run_id, correct)
    for run in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        config_file, metrics_file = run / "config.json", run / "metrics.json"
        if not (config_file.is_file() and metrics_file.is_file()):
            continue
        config = json.loads(config_file.read_text(encoding="utf-8"))
        metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
        name = config["experiment"]["name"]
        if name not in LADDER or config.get("split") != split or metrics["pending_judge"]:
            continue
        candidate = (int(metrics["graded"]), run.name, int(metrics["labels"]["correct"]))
        if name not in best or candidate[:2] >= best[name][:2]:
            best[name] = candidate
    return [
        Bar(name, label, best[name][2], best[name][0])
        for name, label in LADDER.items()
        if name in best
    ]


def ladder_svg(bars: list[Bar], title: str) -> str:
    width, label_w, plot_w, row_h, top = 600, 262, 340, 34, 62
    left = label_w + 12
    height = top + row_h * len(bars) + 34

    def x(share: float) -> float:
        return round(left + plot_w * share, 1)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="{escape(title)}" '
        'font-family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif" font-size="13">',
        f'<rect width="{width}" height="{height}" rx="8" fill="#ffffff"/>',
        '<text x="16" y="26" font-size="15" font-weight="600" fill="#1f2328">'
        f"{escape(title)}</text>",
        '<text x="16" y="45" fill="#59636e">Accuracy with 95% Wilson intervals; '
        "one run per configuration</text>",
    ]
    for tick in (0, 25, 50, 75, 100):
        out.append(
            f'<line x1="{x(tick / 100)}" y1="{top - 4}" x2="{x(tick / 100)}" '
            f'y2="{height - 30}" stroke="#d1d9e0" stroke-width="1"/>'
            f'<text x="{x(tick / 100)}" y="{height - 12}" text-anchor="middle" '
            f'fill="#59636e">{tick}%</text>'
        )
    for i, bar in enumerate(bars):
        y = top + row_h * i
        mid = y + row_h / 2 - 3
        main = bar.experiment == MAIN
        low, high = bar.interval
        weight = ' font-weight="600"' if main else ""
        out += [
            f'<text x="{label_w}" y="{mid + 4}" text-anchor="end" fill="#1f2328"{weight}>'
            f"{escape(bar.label)}</text>",
            f'<rect x="{left}" y="{y + 4}" width="{round(plot_w * bar.accuracy, 1)}" '
            f'height="{row_h - 14}" rx="2" fill="{"#0969da" if main else "#8cb4e2"}"/>',
            f'<path d="M{x(low)} {mid}H{x(high)}M{x(low)} {mid - 5}v10M{x(high)} {mid - 5}v10" '
            'stroke="#1f2328" stroke-width="1.5" fill="none"/>',
            f'<text x="{left + plot_w + 14}" y="{mid + 4}" fill="#1f2328"{weight}>'
            f"{bar.text}</text>",
        ]
    out.append("</svg>")
    return "\n".join(out) + "\n"


def chart_command(
    out: Path = typer.Option(DEFAULT_OUT, help="Where to write the SVG."),
) -> None:
    """Draw the dev baseline ladder from the saved runs."""
    bars = ladder_bars(get_settings().results_dir / "runs")
    if not bars:
        console.print("[red]No graded dev runs of the baseline configs found.[/]")
        raise typer.Exit(code=1)
    n = bars[0].n
    title = f"Baseline ladder on FinanceBench dev ({n} questions, Claude Opus 5.5)"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(ladder_svg(bars, title), encoding="utf-8", newline="\n")
    for bar in bars:
        console.print(f"  {bar.label:38s} {bar.text}")
    console.print(f"[dim]wrote {out}[/]")
