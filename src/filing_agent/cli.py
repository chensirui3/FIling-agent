"""Command-line interface: `fa <group> <command>`."""

from __future__ import annotations

import sys

import typer

from filing_agent import __version__
from filing_agent.agent.commands import ask_command
from filing_agent.corpus.commands import index_app
from filing_agent.data.commands import data_app
from filing_agent.eval.commands import eval_app
from filing_agent.fresh.commands import fresh_app
from filing_agent.mcp_commands import mcp_app
from filing_agent.sec.commands import sec_app
from filing_agent.tools.commands import tools_app

app = typer.Typer(
    help="Filing research agent: data, tools, agent runs and evaluation.",
    no_args_is_help=True,
    add_completion=False,
)
app.command("ask")(ask_command)
app.add_typer(data_app, name="data")
app.add_typer(index_app, name="index")
app.add_typer(sec_app, name="sec")
app.add_typer(tools_app, name="tools")
app.add_typer(eval_app, name="eval")
app.add_typer(fresh_app, name="fresh")
app.add_typer(mcp_app, name="mcp")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"filing-agent {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version."
    ),
) -> None:
    """Filing research agent."""


def _force_utf8_stdio() -> None:
    """Avoid UnicodeEncodeError when output is redirected on non-UTF-8 Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        reconfigure = getattr(stream, "reconfigure", None)
        if encoding != "utf8" and reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _force_utf8_stdio()
    app()


if __name__ == "__main__":
    main()
