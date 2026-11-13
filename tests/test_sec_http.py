from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from filing_agent.sec.http import (
    CacheMiss,
    RateLimiter,
    SecClient,
    SecConfigError,
    SecHttpError,
    cache_path,
    check_user_agent,
    companyfacts_url,
)

AGENT = "Jane Doe jane@contoso.test"
URL = companyfacts_url(66740)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


def _client(tmp_path: Path, handler, *, offline: bool = False, agent: str | None = AGENT):
    fake = FakeTime()
    client = SecClient(
        agent,
        tmp_path,
        offline=offline,
        transport=httpx.MockTransport(handler),
        limiter=RateLimiter(8.0, clock=fake.clock, sleep=fake.sleep),
        sleep=fake.sleep,
        backoff_s=0.5,
    )
    return client, fake


@pytest.mark.parametrize("agent", [None, "", "filing-agent", "jane@contoso.test"])
def test_user_agent_must_name_a_contact(agent: str | None) -> None:
    with pytest.raises(SecConfigError, match="SEC_USER_AGENT"):
        check_user_agent(agent)
    assert check_user_agent(f" {AGENT} ") == AGENT


def test_the_example_placeholder_is_rejected() -> None:
    with pytest.raises(SecConfigError, match="placeholder"):
        check_user_agent("Your Name your.email@example.com")


def test_cache_path_mirrors_the_url(tmp_path: Path) -> None:
    assert cache_path(tmp_path, URL) == tmp_path.joinpath(
        "data.sec.gov", "api", "xbrl", "companyfacts", "CIK0000066740.json"
    )


def test_responses_are_cached_and_carry_the_user_agent(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, json={"cik": 66740})

    client, _ = _client(tmp_path, handler)
    assert client.get_json(URL) == {"cik": 66740}
    assert client.get_json(URL) == {"cik": 66740}
    assert seen == [AGENT]
    assert json.loads(cache_path(tmp_path, URL).read_text(encoding="utf-8")) == {"cik": 66740}


def test_offline_mode_reads_the_cache_and_never_needs_a_user_agent(tmp_path: Path) -> None:
    path = cache_path(tmp_path, URL)
    path.parent.mkdir(parents=True)
    path.write_text('{"cached": true}', encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("offline mode made a request")

    client, _ = _client(tmp_path, handler, offline=True, agent=None)
    assert client.get_json(URL) == {"cached": True}
    with pytest.raises(CacheMiss, match="fa sec warm"):
        client.get_json(companyfacts_url(1))


def test_live_request_without_user_agent_fails_clearly(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, lambda r: httpx.Response(200, json={}), agent=None)
    with pytest.raises(SecConfigError):
        client.get_json(URL)


def test_transient_errors_retry_with_backoff_and_retry_after(tmp_path: Path) -> None:
    responses = iter(
        [
            httpx.Response(503),
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    client, fake = _client(tmp_path, lambda r: next(responses))
    assert client.get_json(URL) == {"ok": 1}
    assert client.requests_made == 3
    backoff, retry_after = [s for s in fake.sleeps if s not in (0.125,)][:2]
    assert 0.5 <= backoff <= 0.75  # jittered 0.5s first backoff
    assert retry_after == 2.0


def test_forbidden_is_not_retried(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, lambda r: httpx.Response(403))
    with pytest.raises(SecHttpError, match="User-Agent"):
        client.get_json(URL)
    assert client.requests_made == 1


def test_rate_limiter_spaces_requests() -> None:
    fake = FakeTime()
    limiter = RateLimiter(8.0, clock=fake.clock, sleep=fake.sleep)
    for _ in range(3):
        limiter.wait()
    assert fake.sleeps == [0.125, 0.125]
    assert fake.now == pytest.approx(0.25)
