"""The agent loop: Claude with the filing tools, under explicit budgets, fully traced.

Each turn sends the whole conversation (append-only, so thinking blocks stay valid and the
cached prefix keeps growing), runs every requested tool, and returns all results in one user
message. The final reply must be JSON matching the answer schema. When the turn or tool-call
budget runs out, the last request disables tools and asks for an answer with what was found.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import anthropic
from pydantic import BaseModel

from filing_agent.agent.answer import AnswerParseError, answer_format, parse_answer
from filing_agent.agent.pricing import cost_usd
from filing_agent.agent.prompts import PROMPT_VERSION, system_prompt
from filing_agent.agent.trace import AgentRun, ModelCall, RunStatus, ToolCall
from filing_agent.tools.base import ToolRegistry

Effort = Literal["low", "medium", "high", "xhigh", "max"]
FALLBACK_BETA = "server-side-fallback-2026-07-01"
BUDGET_NOTE = (
    "You have reached the tool budget for this question. Do not call more tools; give your "
    "final answer now, based on what you have found."
)


@dataclass(frozen=True)
class AgentConfig:
    model: str = "claude-opus-5-5"
    effort: Effort = "medium"
    max_turns: int = 16
    max_tool_calls: int = 40
    max_tokens: int = 16_000
    wall_clock_s: float = 300.0
    # Server-side refusal fallback may answer with another model. Interactive use turns it on;
    # eval runs keep it off, so every scored answer comes from the model under test.
    fallbacks: bool = False
    prompt_version: str = PROMPT_VERSION


EventHandler = Callable[[BaseModel], None]


def user_text(question: str, context: str | None = None) -> str:
    """The first user message: any context first, the question last."""
    return question if context is None else f"{context}\n\n{question}"


def _dump(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    return dict(block)


class Agent:
    def __init__(
        self,
        client: Any,
        tools: ToolRegistry,
        config: AgentConfig | None = None,
        *,
        on_event: EventHandler | None = None,
        clock: Callable[[], float] = time.monotonic,
        cost_factor: float = 1.0,
    ) -> None:
        self.client = client
        self.tools = tools
        self.config = config or AgentConfig()
        self.on_event = on_event
        self.clock = clock
        self.cost_factor = cost_factor  # 0.5 for answers that came back through a batch
        self.system = system_prompt(tools.names, self.config.prompt_version)
        self.tool_definitions = tools.definitions()

    # ------------------------------------------------------------------ one request

    def request_params(
        self, messages: list[dict[str, Any]], *, tools_off: bool = False
    ) -> dict[str, Any]:
        """The Messages API request for one turn (also what a batch submits)."""
        params: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": self.system,
            "messages": messages,
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": self.config.effort, "format": answer_format()},
            "cache_control": {"type": "ephemeral"},
        }
        if self.tool_definitions:
            params["tools"] = self.tool_definitions
            if tools_off:
                params["tool_choice"] = {"type": "none"}
        return params

    def _create(self, messages: list[dict[str, Any]], *, tools_off: bool) -> Any:
        params = self.request_params(messages, tools_off=tools_off)
        if self.config.fallbacks:
            return self.client.beta.messages.create(
                **params, betas=[FALLBACK_BETA], fallbacks="default"
            )
        return self.client.messages.create(**params)

    def _record(self, turn: int, response: Any, latency_s: float) -> ModelCall:
        usage = response.usage
        read = getattr(usage, "cache_read_input_tokens", None) or 0
        write = getattr(usage, "cache_creation_input_tokens", None) or 0
        details = getattr(response, "stop_details", None)
        cost = cost_usd(
            response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=read,
            cache_write_tokens=write,
        )
        call = ModelCall(
            turn=turn,
            response_id=getattr(response, "id", None),
            model_requested=self.config.model,
            model_served=response.model,
            stop_reason=response.stop_reason,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=read,
            cache_write_tokens=write,
            cost_usd=None if cost is None else cost * self.cost_factor,
            latency_ms=round(latency_s * 1000, 1),
            stop_details=_dump(details) if details is not None else None,
        )
        if self.on_event:
            self.on_event(call)
        return call

    def _run_tools(
        self, turn: int, content: list[Any], records: list[ToolCall]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for block in content:
            if block.type != "tool_use":
                continue
            raw_input = dict(block.input) if isinstance(block.input, dict) else {}
            outcome = self.tools.run(block.name, raw_input)
            record = ToolCall(
                turn=turn,
                tool_use_id=block.id,
                name=block.name,
                input=raw_input,
                output=outcome.content,
                is_error=outcome.is_error,
                internal_error=outcome.internal_error,
                truncated=outcome.truncated,
                latency_ms=outcome.latency_ms,
            )
            records.append(record)
            if self.on_event:
                self.on_event(record)
            result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": outcome.content,
            }
            if outcome.is_error:
                result["is_error"] = True
            results.append(result)
        return results

    # ------------------------------------------------------------------ the loop

    def run(self, question: str, *, context: str | None = None) -> AgentRun:
        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        started = self.clock()
        first = user_text(question, context)
        messages: list[dict[str, Any]] = [{"role": "user", "content": first}]
        transcript: list[dict[str, Any]] = [{"role": "user", "content": first}]
        model_calls: list[ModelCall] = []
        tool_calls: list[ToolCall] = []
        status: RunStatus = "budget_exhausted"
        answer = None
        final_text: str | None = None
        error: str | None = None

        for turn in range(1, self.config.max_turns + 1):
            if self.clock() - started > self.config.wall_clock_s:
                status, error = "timeout", f"wall clock exceeded {self.config.wall_clock_s:.0f}s"
                break
            last = turn == self.config.max_turns or len(tool_calls) >= self.config.max_tool_calls
            tools_off = last and bool(self.tool_definitions)
            if tools_off and turn > 1:
                note = {"type": "text", "text": BUDGET_NOTE}
                if messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], list):
                    messages[-1]["content"].append(note)  # after the tool results
                    transcript[-1]["content"].append(note)
                else:
                    messages.append({"role": "user", "content": [note]})
                    transcript.append({"role": "user", "content": [note]})

            requested_at = self.clock()
            try:
                response = self._create(messages, tools_off=tools_off)
            except anthropic.APIError as exc:
                status, error = "api_error", f"{type(exc).__name__}: {exc}"
                break
            except TypeError as exc:  # the SDK's way of saying no credentials are configured
                if "authentication" not in str(exc).lower():
                    raise
                status, error = "api_error", f"no Claude API credentials: {exc}"
                break
            call = self._record(turn, response, self.clock() - requested_at)
            model_calls.append(call)
            content = list(response.content)
            messages.append({"role": "assistant", "content": content})
            transcript.append({"role": "assistant", "content": [_dump(b) for b in content]})

            if not self.config.fallbacks and call.model_served != self.config.model:
                status = "model_mismatch"
                error = f"requested {self.config.model}, served {call.model_served}"
                break
            stop = response.stop_reason
            if stop == "tool_use":
                if tools_off:
                    break  # budget_exhausted: still asking for tools on the final turn
                results = self._run_tools(turn, content, tool_calls)
                messages.append({"role": "user", "content": results})
                transcript.append({"role": "user", "content": [dict(r) for r in results]})
                continue
            if stop == "pause_turn":
                continue
            if stop == "max_tokens":
                status = "truncated"
                break
            if stop == "refusal":
                status, error = "refusal", str(call.stop_details or "refused")
                break
            final_text = "".join(b.text for b in content if b.type == "text")
            try:
                answer = parse_answer(final_text)
                status = "answered"
            except AnswerParseError as exc:
                status, error = "invalid_answer", str(exc)
            break

        return AgentRun(
            question=question,
            config=asdict(self.config),
            status=status,
            answer=answer,
            final_text=final_text,
            error=error,
            model_calls=model_calls,
            tool_calls=tool_calls,
            transcript=transcript,
            started_at=started_at,
            wall_s=round(self.clock() - started, 2),
        )
