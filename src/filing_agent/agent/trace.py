"""What one agent run leaves behind: every model call, every tool call, and the transcript."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from filing_agent.agent.answer import FinalAnswer

RunStatus = Literal[
    "answered",  # a valid final answer (which may still say insufficient_information)
    "invalid_answer",  # the final message did not match the answer schema
    "truncated",  # stopped at max_tokens
    "refusal",  # stopped by a safety classifier
    "budget_exhausted",  # still calling tools when the turn budget ran out
    "timeout",  # the wall-clock ceiling was reached
    "api_error",  # the API failed after retries (an infrastructure failure, never graded)
    "model_mismatch",  # a different model served the request than the one asked for
]
INFRA_FAILURES: frozenset[str] = frozenset({"api_error", "model_mismatch", "timeout"})


class ModelCall(BaseModel):
    turn: int
    response_id: str | None
    model_requested: str
    model_served: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    latency_ms: float
    stop_details: dict[str, Any] | None = None


class ToolCall(BaseModel):
    turn: int
    tool_use_id: str
    name: str
    input: dict[str, Any]
    output: str
    is_error: bool
    internal_error: bool
    truncated: bool
    latency_ms: float


class AgentRun(BaseModel):
    question: str
    config: dict[str, Any]
    status: RunStatus
    answer: FinalAnswer | None = None
    final_text: str | None = None
    error: str | None = None
    model_calls: list[ModelCall] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    started_at: str
    wall_s: float

    @property
    def cost_usd(self) -> float | None:
        costs = [c.cost_usd for c in self.model_calls]
        return None if any(c is None for c in costs) else float(sum(c for c in costs if c))

    @property
    def tokens(self) -> dict[str, int]:
        return {
            "input": sum(c.input_tokens for c in self.model_calls),
            "output": sum(c.output_tokens for c in self.model_calls),
            "cache_read": sum(c.cache_read_tokens for c in self.model_calls),
            "cache_write": sum(c.cache_write_tokens for c in self.model_calls),
        }
