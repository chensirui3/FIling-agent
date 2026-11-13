"""`fa mcp serve`: run the tools as an MCP server (stdio by default, or streamable HTTP)."""

from __future__ import annotations

from enum import StrEnum

import anyio
import typer

from filing_agent.config import get_settings
from filing_agent.mcp_server import build_server, serve_stdio
from filing_agent.tools.filing_tools import ToolContext, build_tools

mcp_app = typer.Typer(help="Serve the agent's tools over MCP.", no_args_is_help=True)


class Transport(StrEnum):
    stdio = "stdio"
    http = "http"


@mcp_app.command("serve")
def serve_command(
    transport: Transport = typer.Option(Transport.stdio, help="stdio or streamable HTTP."),
    index: str = typer.Option(
        "contextual_fresh", help="Search index (FinanceBench + fresh filings by default)."
    ),
    xbrl: bool = typer.Option(True, "--xbrl/--no-xbrl", help="Include the SEC XBRL tools."),
    sec_offline: bool | None = typer.Option(
        None,
        "--sec-offline/--sec-online",
        help="Answer SEC requests only from the cache (default: FA_SEC_OFFLINE).",
    ),
    host: str = typer.Option("127.0.0.1", help="HTTP host."),
    port: int = typer.Option(8000, help="HTTP port."),
) -> None:
    """Serve the seven research tools over MCP. With stdio, stdout carries the protocol only."""
    settings = get_settings()
    context = ToolContext.from_settings(
        settings, index_name=index, xbrl=xbrl, sec_offline=sec_offline
    )
    server = build_server(build_tools(context))
    if transport is Transport.stdio:
        anyio.run(serve_stdio, server)
        return
    import uvicorn  # installed with the MCP SDK; only the HTTP transport needs it

    uvicorn.run(server.streamable_http_app(host=host), host=host, port=port, log_level="warning")
