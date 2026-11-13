"""API cost from recorded token usage (never estimated from text length)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float  # 5-minute TTL writes, 1.25x input


# Anthropic first-party rates (pricing table cached 2026-10-06). Haiku 5.5's rates apply to
# prompts up to 100K tokens; its cache rates follow the usual 0.1x / 1.25x of input.
PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
}


def cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Cost of one request, or None for a model without a known price."""
    price = PRICES.get(model)
    if price is None:
        return None
    total = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read_tokens * price.cache_read
        + cache_write_tokens * price.cache_write
    )
    return total / 1_000_000
