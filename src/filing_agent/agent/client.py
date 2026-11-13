"""The Claude API client, configured from settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import anthropic

from filing_agent.config import Settings, get_settings

CREDENTIALS_HINT = (
    "No Claude API credentials found. Run `ant auth login`, or set ANTHROPIC_API_KEY in .env."
)


def _key(settings: Settings) -> str | None:
    if settings.anthropic_api_key is None:
        return None
    return settings.anthropic_api_key.get_secret_value().strip() or None


def credentials_available(settings: Settings | None = None) -> bool:
    """A cheap check before a paid run: a key in .env or the environment, or an `ant` profile."""
    settings = settings or get_settings()
    if _key(settings):
        return True
    if any(
        os.environ.get(v)
        for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")
    ):
        return True
    return (Path.home() / ".config" / "anthropic").is_dir()


def make_client(
    settings: Settings | None = None, *, max_retries: int = 4, timeout: float = 600.0
) -> anthropic.Anthropic:
    """Uses ANTHROPIC_API_KEY from .env when set; otherwise the SDK's own resolution
    (environment variable, then an `ant auth login` profile)."""
    settings = settings or get_settings()
    kwargs: dict[str, Any] = {"max_retries": max_retries, "timeout": timeout}
    if key := _key(settings):
        kwargs["api_key"] = key
    return anthropic.Anthropic(**kwargs)
