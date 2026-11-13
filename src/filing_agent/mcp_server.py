"""The agent's tools as an MCP server, for Claude Code, Claude Desktop or any MCP client.

The server lists the exact definitions the agent sees (names, descriptions, strict JSON
schemas) and runs every call through the same `ToolRegistry`. A tool therefore behaves the same
for the agent and for an MCP client, and `tests/test_mcp_server.py` checks this.

Transports: stdio (what desktop clients launch) and streamable HTTP. Every tool only reads:
parsed filings, the search index, the SEC cache, or the SEC API when online.
"""

from __future__ import annotations

from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from filing_agent import __version__
from filing_agent.tools.base import ToolRegistry

SERVER_NAME = "filing-agent"
INSTRUCTIONS = (
    "Research tools for SEC filings of 40 US public companies: FinanceBench's filings "
    "(2015-2023) and each company's latest 10-K or 10-Q filed from August to November 2026. "
    "Resolve the company, list its filings, search and read filing pages, query SEC XBRL "
    "facts, and compute with named inputs. Cite the filing and page for every figure."
)


def mcp_tools(registry: ToolRegistry) -> list[types.Tool]:
    return [
        types.Tool(
            name=definition["name"],
            description=definition["description"],
            input_schema=definition["input_schema"],
            annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False),
        )
        for definition in registry.definitions()
    ]


def build_server(registry: ToolRegistry) -> Server[Any]:
    """A low-level MCP server over the registry. Calls run in a worker thread, because the
    tools are blocking (index search, file reads, SEC requests)."""
    tools = mcp_tools(registry)

    async def list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        outcome = await anyio.to_thread.run_sync(
            registry.run, params.name, dict(params.arguments or {})
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=outcome.content)],
            is_error=outcome.is_error,
        )

    return Server(
        SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve_stdio(server: Server[Any]) -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
