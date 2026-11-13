"""A polite, cached HTTP client for SEC EDGAR.

SEC fair-access rules (https://www.sec.gov/os/accessing-edgar-data): at most 10 requests per
second, and every automated request must declare a User-Agent with a name and contact email.
This client stays at 8 requests per second, retries transient errors with jittered backoff
(honoring Retry-After), and caches every JSON response on disk under a path that mirrors the URL.

In offline mode a cache miss is an error. Eval runs use it, so tool outputs are identical
from run to run and no run can silently depend on live data.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

DATA_HOST = "https://data.sec.gov"
WWW_HOST = "https://www.sec.gov"
DEFAULT_MAX_RPS = 8.0
_RETRYABLE = {429, 500, 502, 503, 504}


class SecConfigError(RuntimeError):
    """The client cannot make live requests as configured."""


class SecHttpError(RuntimeError):
    def __init__(self, url: str, status: int, hint: str = "") -> None:
        super().__init__(f"SEC request failed ({status}) for {url}{'. ' + hint if hint else ''}")
        self.url = url
        self.status = status


class CacheMiss(LookupError):
    """Offline mode and the response is not cached."""


_PLACEHOLDER_HINTS = ("example.com", "your name", "your.email")


def check_user_agent(user_agent: str | None) -> str:
    if not user_agent or "@" not in user_agent or " " not in user_agent.strip():
        raise SecConfigError(
            "The SEC requires a User-Agent with a name and contact email for automated access. "
            'Set SEC_USER_AGENT in .env, e.g. SEC_USER_AGENT="Jane Doe jane@gmail.com".'
        )
    if any(hint in user_agent.lower() for hint in _PLACEHOLDER_HINTS):
        raise SecConfigError(
            "SEC_USER_AGENT still holds the example placeholder; the SEC needs a real contact. "
            "Put your own name and email in .env."
        )
    return user_agent.strip()


class RateLimiter:
    """Spaces calls at least 1/rate seconds apart (thread-safe)."""

    def __init__(
        self,
        rate: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval = 1.0 / rate
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            self._sleep(start - now)


def cache_path(cache_dir: Path, url: str, *, raw: bool = False) -> Path:
    """data.sec.gov/api/xbrl/companyfacts/CIK0000066740.json -> <cache>/data.sec.gov/api/...

    JSON responses always end in .json; raw files (`raw=True`, e.g. filing HTML) keep their name.
    """
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    name = segments.pop() if segments else "index"
    if parts.query:
        name += "-" + hashlib.sha1(parts.query.encode()).hexdigest()[:16]
    if not raw and not name.endswith(".json"):
        name += ".json"
    return cache_dir.joinpath(parts.netloc, *segments, name)


class SecClient:
    def __init__(
        self,
        user_agent: str | None,
        cache_dir: Path,
        *,
        offline: bool = False,
        max_rps: float = DEFAULT_MAX_RPS,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 4,
        backoff_s: float = 1.0,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache_dir = cache_dir
        self.offline = offline
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self._sleep = sleep
        self._limiter = limiter or RateLimiter(max_rps, sleep=sleep)
        self._user_agent = user_agent
        self._client: httpx.Client | None = None
        self._transport = transport
        self.requests_made = 0

    def _http(self) -> httpx.Client:
        if self._client is None:
            agent = check_user_agent(self._user_agent)
            self._client = httpx.Client(
                headers={"User-Agent": agent, "Accept-Encoding": "gzip, deflate"},
                timeout=httpx.Timeout(60.0, connect=15.0),
                transport=self._transport,
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> SecClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def is_cached(self, url: str) -> bool:
        return cache_path(self.cache_dir, url).is_file()

    def get_json(self, url: str, *, refresh: bool = False) -> Any:
        path = cache_path(self.cache_dir, url)
        if path.is_file() and not refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        if self.offline:
            raise CacheMiss(f"Offline mode: {url} is not cached. Run `fa sec warm` online first.")
        data = self._fetch(url).json()
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        partial.write_text(json.dumps(data), encoding="utf-8")
        os.replace(partial, path)
        return data

    def get_bytes(self, url: str, *, refresh: bool = False) -> bytes:
        """A raw file (e.g. a filing's HTML document), cached under its own name."""
        path = cache_path(self.cache_dir, url, raw=True)
        if path.is_file() and not refresh:
            return path.read_bytes()
        if self.offline:
            raise CacheMiss(
                f"Offline mode: {url} is not cached. Run `fa fresh fetch` online first."
            )
        data = self._fetch(url).content
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        partial.write_bytes(data)
        os.replace(partial, path)
        return data

    def _fetch(self, url: str) -> httpx.Response:
        client = self._http()
        for attempt in range(1, self.max_attempts + 1):
            self._limiter.wait()
            self.requests_made += 1
            try:
                response = client.get(url)
            except httpx.TransportError:
                if attempt == self.max_attempts:
                    raise
                delay = self._backoff(attempt)
            else:
                if response.status_code < 400:
                    return response
                if response.status_code == 403:
                    raise SecHttpError(
                        url, 403, "The SEC blocks requests without a valid User-Agent"
                    )
                if response.status_code not in _RETRYABLE or attempt == self.max_attempts:
                    raise SecHttpError(url, response.status_code)
                delay = _retry_after(response) or self._backoff(attempt)
            self._sleep(delay)
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int) -> float:
        return self.backoff_s * 2 ** (attempt - 1) * (1 + random.random() / 2)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def companyfacts_url(cik: int) -> str:
    return f"{DATA_HOST}/api/xbrl/companyfacts/CIK{cik:010d}.json"


def submissions_url(cik: int) -> str:
    return f"{DATA_HOST}/submissions/CIK{cik:010d}.json"


def submissions_page_url(name: str) -> str:
    return f"{DATA_HOST}/submissions/{name}"


def frames_url(concept: str, unit: str, period: str) -> str:
    taxonomy, _, name = concept.rpartition(":")
    return f"{DATA_HOST}/api/xbrl/frames/{taxonomy or 'us-gaap'}/{name}/{unit}/{period}.json"


TICKERS_URL = f"{WWW_HOST}/files/company_tickers.json"
