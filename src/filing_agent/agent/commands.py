"""`fa ask`: run the agent on one question and show what it did."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast

import anthropic
import typer
from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel

from filing_agent.agent.client import CREDENTIALS_HINT, make_client
from filing_agent.agent.loop import Agent, AgentConfig, Effort
from filing_agent.agent.trace import AgentRun, ModelCall, ToolCall
from filing_agent.config import get_settings
from filing_agent.tools.filing_tools import (
    ALL_TOOLS,
    LIVE_TOOLS,
    TEXT_TOOLS,
    ToolContext,
    build_tools,
)

console = Console()


class EffortChoice(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    xhigh = "xhigh"
    max = "max"


def _short(value: object, limit: int = 90) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _print_event(event: BaseModel) -> None:
    if isinstance(event, ToolCall):
        args = {k: v for k, v in event.input.items() if v is not None}
        status = "[red]error[/]" if event.is_error else "ok"
        console.print(
            f"  [cyan]{event.name}[/] {_short(args)} -> {status} ({event.latency_ms:.0f} ms)"
        )
    elif isinstance(event, ModelCall):
        cost = f"${event.cost_usd:.4f}" if event.cost_usd is not None else "?"
        console.print(
            f"[dim]turn {event.turn}: {event.input_tokens:,} in, {event.cache_read_tokens:,} "
            f"cached, {event.output_tokens:,} out, {event.latency_ms / 1000:.1f}s, {cost}, "
            f"stop={event.stop_reason}[/]"
        )


def _summary(run: AgentRun) -> str:
    tokens = run.tokens
    cost = f"${run.cost_usd:.4f}" if run.cost_usd is not None else "unknown"
    return (
        f"status {run.status} · {len(run.model_calls)} model calls · {len(run.tool_calls)} tool "
        f"calls · {tokens['input'] + tokens['cache_read'] + tokens['cache_write']:,} input tokens "
        f"({tokens['cache_read']:,} cached) · {tokens['output']:,} output · {cost} · {run.wall_s}s"
    )


def ask_command(
    question: str = typer.Argument(..., help='The question, e.g. "What was 3M\'s FY2018 capex?"'),
    model: str | None = typer.Option(None, help="Model id (default: FA_AGENT_MODEL)."),
    effort: EffortChoice = typer.Option(EffortChoice.medium, help="Thinking effort."),
    xbrl: bool = typer.Option(True, "--xbrl/--no-xbrl", help="Give the agent the XBRL tools."),
    doc: list[str] | None = typer.Option(None, help="Restrict the agent to these filings."),
    live: bool = typer.Option(
        False, "--live", help="Let the agent download any 10-K or 10-Q from EDGAR."
    ),
    max_turns: int = typer.Option(16, min=1, help="Model turns before a forced answer."),
    fallbacks: bool = typer.Option(
        True,
        "--fallbacks/--no-fallbacks",
        help="Server-side refusal fallback (eval runs always turn it off).",
    ),
    save: bool = typer.Option(True, "--save/--no-save", help="Save the full trace as JSON."),
) -> None:
    """Answer one question with the agent, printing each tool call as it happens."""
    settings = get_settings()
    try:
        client = make_client(settings)
    except anthropic.AnthropicError:
        console.print(f"[red]{CREDENTIALS_HINT}[/]")
        raise typer.Exit(code=1) from None
    names = ALL_TOOLS if xbrl else (*TEXT_TOOLS, "calculate")
    if live:
        names = (*names, *LIVE_TOOLS)
    context = ToolContext.from_settings(
        settings,
        xbrl=xbrl or live,
        allowed_docs=doc,
        live=live,
        sec_offline=False if live else None,
    )
    tools = build_tools(context, names)
    config = AgentConfig(
        model=model or settings.agent_model,
        effort=cast(Effort, effort.value),
        max_turns=max_turns,
        fallbacks=fallbacks,
    )
    console.print(f"[bold]{config.model}[/] (effort {config.effort}) with {', '.join(names)}")
    run = Agent(client, tools, config, on_event=_print_event).run(question)

    if run.answer is not None:
        a = run.answer
        figure = (
            f"{a.value:g} {a.scale or ''} {a.unit or ''}".strip() if a.value is not None else "-"
        )
        body = (
            f"{a.answer}\n\n[dim]figure:[/] {figure}   [dim]period:[/] {a.period or '-'}   "
            f"[dim]confidence:[/] {a.confidence}"
            + (f"\n[dim]calculation:[/] {a.calculation}" if a.calculation else "")
        )
        console.print(Panel(body, title=a.answer_status, title_align="left"))
    else:
        console.print(Panel(run.error or run.final_text or "(no answer)", title=run.status))
        if run.error and "auth" in run.error.lower():
            console.print(f"[red]{CREDENTIALS_HINT}[/]")
    console.print(_summary(run))

    if save:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:48]
        path = settings.results_dir / "ask" / f"{stamp}-{slug}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(run.model_dump_json(indent=1), encoding="utf-8")
        console.print(f"[dim]trace: {path}[/]")
    if run.status != "answered":
        raise typer.Exit(code=1)
