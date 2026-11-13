from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from filing_agent.agent.answer import ANSWER_SCHEMA
from filing_agent.sec.companies import Company, CompanyRegistry
from filing_agent.sec.http import SecClient, cache_path, companyfacts_url, submissions_url
from filing_agent.sec.service import SecService
from filing_agent.tools.base import Tool, ToolRegistry, count_unions, render, strict_schema
from filing_agent.tools.filing_tools import ALL_TOOLS, ToolContext, build_tools, display_value
from tests.search_fixtures import CORPUS, build_index, parsed
from tests.sec_fixtures import acme_companyfacts, acme_submissions

UNSUPPORTED = {"title", "default", "minimum", "maximum", "minLength", "maxLength", "pattern"}


@pytest.fixture
def registry() -> CompanyRegistry:
    return CompanyRegistry(
        [
            Company(name="Acme", cik=1, tickers=["ACME"]),
            Company(name="Beta", cik=2, tickers=["BETA"]),
        ]
    )


@pytest.fixture
def ctx(tmp_path: Path, registry: CompanyRegistry) -> ToolContext:
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    for name in CORPUS:
        (parsed_dir / f"{name}.json").write_text(parsed(name).model_dump_json(), encoding="utf-8")
    cache = tmp_path / "sec"
    for url, payload in [
        (companyfacts_url(1), acme_companyfacts()),
        (submissions_url(1), acme_submissions()),
    ]:
        path = cache_path(cache, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    index = build_index(dense=False)
    return ToolContext(
        registry=registry,
        parsed_dir=parsed_dir,
        index_loader=lambda: index,
        sec_loader=lambda: SecService(SecClient(None, cache, offline=True), registry),
    )


def _run(tools: ToolRegistry, name: str, payload: dict[str, Any]) -> Any:
    outcome = tools.run(name, payload)
    assert not outcome.is_error, outcome.content
    return json.loads(outcome.content)


def _walk(node: Any, in_name_map: bool = False):
    if isinstance(node, dict):
        if not in_name_map:
            yield node
        for key, value in node.items():
            yield from _walk(value, in_name_map=key in ("properties", "$defs") and not in_name_map)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def test_every_tool_definition_is_strict(ctx: ToolContext) -> None:
    definitions = build_tools(ctx).definitions()
    assert [d["name"] for d in definitions] == list(ALL_TOOLS)
    for definition in definitions:
        assert definition["strict"] is True and definition["description"]
        for node in _walk(definition["input_schema"]):
            assert not UNSUPPORTED & set(node), (definition["name"], node)
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) <= set(node["properties"])


def test_one_request_stays_under_the_api_union_limit(ctx: ToolContext) -> None:
    # The API rejects a request whose tool and output schemas hold more than 16 union-typed
    # parameters; optional fields are therefore omitted-when-unused, not nullable.
    schemas = [d["input_schema"] for d in build_tools(ctx).definitions()] + [ANSWER_SCHEMA]
    assert sum(count_unions(s) for s in schemas) <= 16


def test_strict_schema_keeps_fields_named_like_keywords() -> None:
    class Odd(BaseModel):
        title: str
        default: int | None = None

    schema = strict_schema(Odd)
    assert list(schema["properties"]) == ["title", "default"]
    assert schema["required"] == ["title"]
    assert schema["properties"]["default"]["type"] == "integer"  # optional, not nullable


def test_resolve_company_and_list_filings(ctx: ToolContext) -> None:
    tools = build_tools(ctx)
    company = _run(tools, "resolve_company", {"query": "acme"})
    assert company == {
        "name": "Acme",
        "cik": 1,
        "tickers": ["ACME"],
        "filings_available": 2,
        "fiscal_year_end": "1231",
    }
    filings = _run(
        tools, "list_filings", {"company": "ACME", "doc_type": None, "fiscal_year": None}
    )
    assert [f["doc_name"] for f in filings["filings"]] == ["ACME_2019_10K", "ACME_2018_10K"]
    assert filings["filings"][1] == {
        "doc_name": "ACME_2018_10K",
        "type": "10-K",
        "fiscal_year": 2018,
        "pages": 3,
    }
    missing = tools.run("list_filings", {"company": "Acme", "doc_type": "8k", "fiscal_year": None})
    assert missing.is_error and "Available fiscal years: [2018, 2019]" in missing.content
    unknown = tools.run("resolve_company", {"query": "Tesla"})
    assert unknown.is_error and not unknown.internal_error


def test_search_filters_and_reading_pages(ctx: ToolContext) -> None:
    tools = build_tools(ctx)
    query = {
        "query": "purchases of property plant and equipment",
        "doc_name": None,
        "doc_type": None,
        "top_k": None,
    }
    hits = _run(tools, "search_filings", query | {"company": "Acme", "fiscal_year": 2019})
    assert {r["doc_name"] for r in hits["results"]} == {"ACME_2019_10K"}
    top = _run(tools, "search_filings", query | {"company": "Acme", "fiscal_year": 2018})[
        "results"
    ][0]
    assert (top["doc_name"], top["page"]) == ("ACME_2018_10K", 1)
    assert top["header"].startswith("Acme | 10-K 2018 | page 1")

    page = _run(
        tools,
        "read_filing_page",
        {"doc_name": "ACME_2018_10K", "page": 1, "include_neighbors": None},
    )
    assert "(1,577)" in page["pages"][0]["text"]
    around = _run(
        tools,
        "read_filing_page",
        {"doc_name": "ACME_2018_10K", "page": 1, "include_neighbors": True},
    )
    assert [p["page"] for p in around["pages"]] == [0, 1, 2]
    beyond = tools.run(
        "read_filing_page", {"doc_name": "ACME_2018_10K", "page": 9, "include_neighbors": None}
    )
    assert beyond.is_error and "pages 0 to 2" in beyond.content
    nope = tools.run(
        "search_filings", query | {"company": None, "fiscal_year": None, "doc_name": "X_10K"}
    )
    assert nope.is_error and "list_filings" in nope.content


def test_allowed_docs_limit_what_tools_can_see(ctx: ToolContext) -> None:
    ctx.allowed_docs = frozenset({"ACME_2018_10K"})
    tools = build_tools(ctx)
    query = {
        "query": "purchases of property plant and equipment",
        "company": None,
        "doc_name": None,
        "doc_type": None,
        "fiscal_year": None,
        "top_k": None,
    }
    assert {r["doc_name"] for r in _run(tools, "search_filings", query)["results"]} == {
        "ACME_2018_10K"
    }
    other = tools.run(
        "read_filing_page", {"doc_name": "ACME_2019_10K", "page": 1, "include_neighbors": None}
    )
    assert other.is_error
    listed = _run(tools, "list_filings", {"company": "Acme", "doc_type": None, "fiscal_year": None})
    assert [f["doc_name"] for f in listed["filings"]] == ["ACME_2018_10K"]


def test_xbrl_tools(ctx: ToolContext) -> None:
    tools = build_tools(ctx)
    concepts = _run(
        tools,
        "xbrl_find_concepts",
        {
            "company": "Acme",
            "query": "payments to acquire property plant and equipment",
            "top_k": 3,
        },
    )
    assert (
        concepts["concepts"][0]["concept"] == "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
    )
    facts = _run(
        tools,
        "xbrl_get_facts",
        {
            "company": "Acme",
            "concept": concepts["concepts"][0]["concept"],
            "fiscal_year": 2018,
            "period": "annual",
            "policy": None,
        },
    )
    [row] = facts["values"]
    assert (row["value"], row["display"], row["fiscal_year"]) == (
        1577000000,
        "1,577 million USD",
        2018,
    )
    restated = _run(
        tools,
        "xbrl_get_facts",
        {
            "company": "Acme",
            "concept": "Revenues",
            "fiscal_year": 2018,
            "period": "annual",
            "policy": None,
        },
    )["values"][0]
    assert restated["restated"] and len(restated["reported_values"]) == 2
    uncached = tools.run(
        "xbrl_get_facts",
        {
            "company": "Beta",
            "concept": "Revenues",
            "fiscal_year": None,
            "period": None,
            "policy": None,
        },
    )
    assert uncached.is_error and not uncached.internal_error and "not cached" in uncached.content


def test_xbrl_tools_are_unavailable_without_sec_data(ctx: ToolContext) -> None:
    ctx.sec_loader = None
    outcome = build_tools(ctx).run(
        "xbrl_find_concepts", {"company": "Acme", "query": "revenue", "top_k": None}
    )
    assert outcome.is_error and "not available in this setting" in outcome.content


def test_calculate_tool(ctx: ToolContext) -> None:
    tools = build_tools(ctx)
    result = _run(
        tools,
        "calculate",
        {
            "expression": "a / b",
            "variables": [{"name": "a", "value": 1}, {"name": "b", "value": 8}],
        },
    )
    assert result["result"] == 0.125
    bad = tools.run("calculate", {"expression": "a / 0", "variables": [{"name": "a", "value": 1}]})
    assert bad.is_error and "Division by zero" in bad.content


def test_invalid_inputs_are_reported_to_the_model(ctx: ToolContext) -> None:
    tools = build_tools(ctx)
    missing = tools.run("search_filings", {"company": "Acme"})
    assert missing.is_error and "query" in missing.content and "Invalid input" in missing.content
    negative = tools.run(
        "read_filing_page", {"doc_name": "ACME_2018_10K", "page": -1, "include_neighbors": None}
    )
    assert negative.is_error and "page" in negative.content
    assert tools.run("no_such_tool", {}).is_error


def test_unexpected_exceptions_are_internal_errors() -> None:
    class Empty(BaseModel):
        pass

    def boom(_args: Empty) -> None:
        raise RuntimeError("index file is corrupt")

    outcome = Tool("boom", "fails", Empty, boom).run({})
    assert outcome.is_error and outcome.internal_error and "RuntimeError" in outcome.content


def test_render_truncates_with_a_note() -> None:
    text, truncated = render({"text": "x" * 500}, 100)
    assert truncated and text.startswith('{"text": "xxx') and "truncated" in text


@pytest.mark.parametrize(
    ("value", "unit", "shown"),
    [
        (1577000000.0, "USD", "1,577 million USD"),
        (1577500000.0, "USD", "1,577.5 million USD"),
        (575000000.0, "shares", "575 million shares"),
        (8.89, "USD/shares", "8.89 USD per share"),
        (0.21, "pure", "0.21 pure"),
    ],
)
def test_display_value(value: float, unit: str, shown: str) -> None:
    assert display_value(value, unit) == shown
