"""Parity: an MCP client sees and runs exactly the tools the agent sees and runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
from mcp import Client

from filing_agent.mcp_server import build_server
from filing_agent.sec.companies import CompanyRegistry
from filing_agent.tools.base import ToolRegistry
from filing_agent.tools.filing_tools import ALL_TOOLS, ToolContext, build_tools

CALC = {
    "expression": "capex / revenue * 100",
    "variables": [{"name": "capex", "value": 1577}, {"name": "revenue", "value": 32765}],
}


def _registry(tmp_path: Path) -> ToolRegistry:
    def no_index() -> Any:
        raise RuntimeError("the parity test never searches")

    context = ToolContext(registry=CompanyRegistry([]), parsed_dir=tmp_path, index_loader=no_index)
    return build_tools(context)


def test_mcp_clients_see_the_agents_exact_tool_definitions(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    async def listed() -> Any:
        async with Client(build_server(registry)) as client:
            return await client.list_tools()

    tools = anyio.run(listed).tools
    definitions = registry.definitions()
    assert [t.name for t in tools] == [d["name"] for d in definitions] == list(ALL_TOOLS)
    for tool, definition in zip(tools, definitions, strict=True):
        assert tool.description == definition["description"]
        assert tool.input_schema == definition["input_schema"]
        assert tool.annotations is not None and tool.annotations.read_only_hint is True


def test_mcp_calls_run_through_the_same_registry(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    async def calls() -> tuple[Any, Any]:
        async with Client(build_server(registry)) as client:
            good = await client.call_tool("calculate", CALC)
            bad = await client.call_tool("calculate", {"expression": "1 +", "variables": []})
            return good, bad

    good, bad = anyio.run(calls)
    assert good.is_error is False
    assert good.content[0].text == registry.run("calculate", CALC).content
    assert bad.is_error is True
    assert (
        bad.content[0].text
        == registry.run("calculate", {"expression": "1 +", "variables": []}).content
    )
