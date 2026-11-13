from __future__ import annotations

import json
from itertools import chain, repeat
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Message
from anthropic.types.beta import BetaMessage
from pydantic import BaseModel

from filing_agent.agent.answer import ANSWER_SCHEMA, AnswerParseError, parse_answer
from filing_agent.agent.loop import BUDGET_NOTE, FALLBACK_BETA, Agent, AgentConfig
from filing_agent.agent.pricing import cost_usd
from filing_agent.agent.prompts import system_prompt
from filing_agent.tools.base import Tool, ToolError, ToolRegistry
from filing_agent.tools.filing_tools import CalculateInput, _calculate

MODEL = "claude-opus-5-5"
ANSWER = {
    "answer_status": "answered",
    "answer": "3M's FY2018 capital expenditure was $1,577 million.",
    "value": 1577,
    "unit": "USD",
    "scale": "millions",
    "period": "FY2018",
    "calculation": None,
    "sources": [{"doc_name": "3M_2018_10K", "page": 59, "xbrl_concept": None, "accession": None}],
    "confidence": "high",
}
USAGE = {
    "input_tokens": 1000,
    "output_tokens": 200,
    "cache_read_input_tokens": 3000,
    "cache_creation_input_tokens": 500,
}


def _message(content: list[dict[str, Any]], stop: str, *, model: str = MODEL, beta: bool = False):
    data = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": USAGE,
    }
    return (BetaMessage if beta else Message).model_validate(data)


def thinking() -> dict[str, Any]:
    return {"type": "thinking", "thinking": "Need the cash flow statement.", "signature": "sig"}


def tool_use(tool_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": payload}


def answer(payload: dict[str, Any] = ANSWER) -> dict[str, Any]:
    return {"type": "text", "text": json.dumps(payload)}


CALC = {"expression": "a / b", "variables": [{"name": "a", "value": 1}, {"name": "b", "value": 8}]}


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=lambda o: o.model_dump(mode="json")))


class FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **params: Any) -> Any:
        self.requests.append(_jsonable(params))  # snapshot: the loop keeps appending to messages
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, responses: list[Any], beta_responses: list[Any] | None = None) -> None:
        self.messages = FakeMessages(responses)
        self.beta = SimpleNamespace(messages=FakeMessages(beta_responses or []))


class LookupInput(BaseModel):
    key: str


def _lookup(args: LookupInput) -> dict[str, str]:
    if args.key == "missing":
        raise ToolError("No such key.")
    return {"key": args.key, "value": "found"}


def _tools() -> ToolRegistry:
    return ToolRegistry(
        [
            Tool("lookup", "Look something up.", LookupInput, _lookup),
            Tool("calculate", "Arithmetic.", CalculateInput, _calculate),
        ]
    )


def _agent(client: FakeClient, **config: Any) -> Agent:
    return Agent(client, _tools(), AgentConfig(**config))


def test_answers_after_a_tool_call() -> None:
    client = FakeClient(
        [
            _message([thinking(), tool_use("toolu_1", "calculate", CALC)], "tool_use"),
            _message([answer()], "end_turn"),
        ]
    )
    run = _agent(client).run("What was 3M's FY2018 capex?")
    assert run.status == "answered"
    assert run.answer is not None and run.answer.value == 1577
    [call] = run.tool_calls
    assert (call.name, call.input, call.is_error) == ("calculate", CALC, False)
    assert '"result": 0.125' in call.output

    first, second = client.messages.requests
    assert first["model"] == MODEL
    assert first["thinking"]["type"] == "adaptive"
    assert first["output_config"]["effort"] == "medium"
    assert first["output_config"]["format"] == {"type": "json_schema", "schema": ANSWER_SCHEMA}
    assert first["cache_control"] == {"type": "ephemeral"}
    assert [t["name"] for t in first["tools"]] == ["lookup", "calculate"]
    assert all(t["strict"] for t in first["tools"])
    assert "tool_choice" not in first
    # Second request: question, the assistant turn unchanged (thinking included), then results.
    user, assistant, results = second["messages"]
    assert user == {"role": "user", "content": "What was 3M's FY2018 capex?"}
    assert [b["type"] for b in assistant["content"]] == ["thinking", "tool_use"]
    assert results["content"][0]["tool_use_id"] == "toolu_1"
    assert "is_error" not in results["content"][0]

    expected = cost_usd(
        MODEL, input_tokens=1000, output_tokens=200, cache_read_tokens=3000, cache_write_tokens=500
    )
    assert expected is not None
    assert run.cost_usd == pytest.approx(2 * expected)
    assert run.tokens == {"input": 2000, "output": 400, "cache_read": 6000, "cache_write": 1000}
    assert run.transcript[1]["content"][0]["type"] == "thinking"


def test_parallel_tool_calls_come_back_in_one_message() -> None:
    client = FakeClient(
        [
            _message(
                [tool_use("t1", "lookup", {"key": "a"}), tool_use("t2", "lookup", {"key": "b"})],
                "tool_use",
            ),
            _message([answer()], "end_turn"),
        ]
    )
    run = _agent(client).run("q")
    results = client.messages.requests[1]["messages"][-1]
    assert results["role"] == "user"
    assert [r["tool_use_id"] for r in results["content"]] == ["t1", "t2"]
    assert [c.turn for c in run.tool_calls] == [1, 1]


def test_tool_errors_are_flagged_for_the_model() -> None:
    client = FakeClient(
        [
            _message([tool_use("t1", "lookup", {"key": "missing"})], "tool_use"),
            _message([answer()], "end_turn"),
        ]
    )
    run = _agent(client).run("q")
    result = client.messages.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and result["content"] == "No such key."
    assert run.tool_calls[0].is_error and not run.tool_calls[0].internal_error


def test_tool_budget_forces_a_final_answer() -> None:
    client = FakeClient(
        [
            _message([tool_use("t1", "lookup", {"key": "a"})], "tool_use"),
            _message([answer()], "end_turn"),
        ]
    )
    run = _agent(client, max_tool_calls=1).run("q")
    final = client.messages.requests[1]
    assert final["tool_choice"] == {"type": "none"}
    assert final["messages"][-1]["content"][-1] == {"type": "text", "text": BUDGET_NOTE}
    assert run.status == "answered"


def test_still_calling_tools_on_the_last_turn_is_budget_exhausted() -> None:
    client = FakeClient(
        [
            _message([tool_use("t1", "lookup", {"key": "a"})], "tool_use"),
            _message([tool_use("t2", "lookup", {"key": "b"})], "tool_use"),
        ]
    )
    assert _agent(client, max_turns=2).run("q").status == "budget_exhausted"


@pytest.mark.parametrize(("stop", "status"), [("max_tokens", "truncated"), ("refusal", "refusal")])
def test_stop_reasons_map_to_statuses(stop: str, status: str) -> None:
    client = FakeClient([_message([{"type": "text", "text": "{"}], stop)])
    assert _agent(client).run("q").status == status


def test_a_different_served_model_fails_loudly() -> None:
    client = FakeClient([_message([answer()], "end_turn", model="claude-sonnet-5-5")])
    run = _agent(client).run("q")
    assert run.status == "model_mismatch" and "served claude-sonnet-5-5" in (run.error or "")


def test_non_json_final_text_is_an_invalid_answer() -> None:
    client = FakeClient([_message([{"type": "text", "text": "About $1.6 billion."}], "end_turn")])
    run = _agent(client).run("q")
    assert run.status == "invalid_answer" and run.final_text == "About $1.6 billion."


def test_api_errors_are_infrastructure_failures() -> None:
    error = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    run = _agent(FakeClient([error])).run("q")
    assert run.status == "api_error" and "APIConnectionError" in (run.error or "")


def test_missing_credentials_are_reported_as_such() -> None:
    error = TypeError("Could not resolve authentication method. Expected one of api_key ...")
    run = _agent(FakeClient([error])).run("q")
    assert run.status == "api_error" and "credentials" in (run.error or "")
    with pytest.raises(TypeError, match="unrelated"):
        _agent(FakeClient([TypeError("unrelated bug")])).run("q")


def test_fallbacks_use_the_beta_endpoint_and_allow_another_served_model() -> None:
    client = FakeClient(
        [], beta_responses=[_message([answer()], "end_turn", model="claude-opus-4-8", beta=True)]
    )
    run = _agent(client, fallbacks=True).run("q")
    assert run.status == "answered"
    [request] = client.beta.messages.requests
    assert request["betas"] == [FALLBACK_BETA] and request["fallbacks"] == "default"
    assert run.model_calls[0].model_served == "claude-opus-4-8"


def test_wall_clock_ceiling() -> None:
    # start, turn-1 check, request sent, response received, turn-2 check (over budget), end
    ticks = chain([0, 0, 0, 100, 400], repeat(400))
    client = FakeClient([_message([tool_use("t1", "lookup", {"key": "a"})], "tool_use")])
    agent = Agent(client, _tools(), AgentConfig(wall_clock_s=300), clock=lambda: next(ticks))
    run = agent.run("q")
    assert run.status == "timeout" and len(run.model_calls) == 1


def test_system_prompt_adapts_to_the_tools() -> None:
    assert "no tools" in system_prompt([])
    text_only = system_prompt(
        ["resolve_company", "search_filings", "read_filing_page", "calculate"]
    )
    assert "search_filings" in text_only and "xbrl_get_facts" not in text_only
    assert "xbrl_get_facts" in system_prompt(["xbrl_get_facts", "calculate"])
    with pytest.raises(ValueError, match="Unknown prompt version"):
        system_prompt([], version="v0")


def test_answer_schema_and_parsing() -> None:
    assert ANSWER_SCHEMA["additionalProperties"] is False
    assert set(ANSWER_SCHEMA["required"]) == {"answer_status", "answer", "sources", "confidence"}
    assert parse_answer(json.dumps(ANSWER)).sources[0].page == 59
    minimal = {"answer_status": "answered", "answer": "Yes.", "sources": [], "confidence": "low"}
    assert parse_answer(json.dumps(minimal)).value is None  # optional fields may be omitted
    with pytest.raises(AnswerParseError):
        parse_answer(json.dumps({"answer": "x"}))


def test_cost_uses_recorded_tokens() -> None:
    assert cost_usd(MODEL, input_tokens=1_000_000, output_tokens=0) == pytest.approx(4.0)
    assert cost_usd(
        MODEL, input_tokens=0, output_tokens=1_000_000, cache_read_tokens=1_000_000
    ) == (pytest.approx(20.2))
    sonnet_reads = cost_usd(
        "claude-sonnet-5-5", input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000
    )
    assert sonnet_reads == pytest.approx(0.10)  # 0.05x of input, as on Opus 5.5
    assert cost_usd("unknown-model", input_tokens=1, output_tokens=1) is None


def test_prompt_v2_adds_only_the_label_check() -> None:
    tools = ["list_filings", "search_filings", "read_filing_page", "xbrl_get_facts", "calculate"]
    v1, v2 = system_prompt(tools, "v1"), system_prompt(tools, "v2")
    assert "labels can be wrong" in v2 and "labels can be wrong" not in v1
    assert "or with xbrl_get_facts" in v2
    assert v2.count("\n- ") == v1.count("\n- ") + 1  # exactly one step more
    assert "or with xbrl_get_facts" not in system_prompt(["search_filings"], "v2")
    assert system_prompt([], "v2") == system_prompt([], "v1")  # closed book is unchanged
