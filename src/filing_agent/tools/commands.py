"""`fa tools ...`: list the agent's tools, or run one by hand."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from filing_agent.tools.filing_tools import ToolContext, build_tools

tools_app = typer.Typer(
    help="The agent's tools: list them, or run one by hand.", no_args_is_help=True
)
console = Console()


@tools_app.command("list")
def list_command() -> None:
    """Show each tool's name, inputs and description."""
    table = Table(title="Agent tools", title_justify="left", show_lines=True)
    for header in ("tool", "inputs", "description"):
        table.add_column(header)
    for definition in build_tools(ToolContext.from_settings()).definitions():
        inputs = ", ".join(definition["input_schema"]["properties"])
        table.add_row(definition["name"], inputs, definition["description"])
    console.print(table)


@tools_app.command("run")
def run_command(
    name: str = typer.Argument(..., help="Tool name, e.g. search_filings."),
    payload: str = typer.Argument("{}", help='Tool input as JSON, e.g. \'{"query": "3M"}\'.'),
    xbrl: bool = typer.Option(True, "--xbrl/--no-xbrl", help="Make SEC XBRL data available."),
) -> None:
    """Run one tool exactly as the agent would and print what the model would see."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        console.print(f"[red]The input is not valid JSON:[/] {exc}")
        raise typer.Exit(code=2) from None
    outcome = build_tools(ToolContext.from_settings(xbrl=xbrl)).run(name, data)
    status = "internal error" if outcome.internal_error else "error" if outcome.is_error else "ok"
    note = " (truncated)" if outcome.truncated else ""
    console.print(f"[bold]{name}[/]: {status} in {outcome.latency_ms:.0f} ms{note}")
    try:
        console.print_json(outcome.content)
    except (json.JSONDecodeError, TypeError):
        console.print(outcome.content)
    if outcome.is_error:
        raise typer.Exit(code=1)
