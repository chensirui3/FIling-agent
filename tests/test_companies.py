from __future__ import annotations

import json

import pytest

from filing_agent.config import PROJECT_ROOT
from filing_agent.sec.commands import names_agree
from filing_agent.sec.companies import CompanyRegistry, UnknownCompanyError, normalize_name

REGISTRY_FILE = PROJECT_ROOT / "configs" / "companies.yaml"
QUESTIONS_FILE = PROJECT_ROOT / "data/raw/financebench/data/financebench_open_source.jsonl"


@pytest.fixture(scope="module")
def registry() -> CompanyRegistry:
    return CompanyRegistry.load(REGISTRY_FILE)


def test_registry_has_forty_unique_companies(registry: CompanyRegistry) -> None:
    assert len(registry.companies) == 40
    assert len({c.cik for c in registry.companies}) == 40
    assert all(registry.resolve(c.name) is c for c in registry.companies)


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("3M", "3M"),
        ("mmm", "3M"),
        ("3M Company", "3M"),
        ("66740", "3M"),
        ("J&J", "Johnson & Johnson"),
        ("Johnson and Johnson", "Johnson & Johnson"),
        ("JPMorgan Chase & Co.", "JPMorgan"),
        ("Square", "Block"),
        ("SQ", "Block"),
        ("Coca Cola", "Coca-Cola"),
        ("The Coca-Cola Company", "Coca-Cola"),
        ("Amazon.com, Inc.", "Amazon"),
        ("McDonald's", "McDonalds"),
        ("Lockheed", "Lockheed Martin"),
    ],
)
def test_resolve_names_tickers_aliases_and_ciks(
    registry: CompanyRegistry, query: str, name: str
) -> None:
    assert registry.resolve(query).name == name


def test_unknown_company_raises_with_suggestions(registry: CompanyRegistry) -> None:
    with pytest.raises(UnknownCompanyError, match="Tesla"):
        registry.resolve("Tesla")
    with pytest.raises(UnknownCompanyError) as excinfo:
        registry.resolve("American Airlines")  # two registry names start with "American"
    assert "American Express" in excinfo.value.suggestions


def test_cik10_is_zero_padded(registry: CompanyRegistry) -> None:
    assert registry.resolve("3M").cik10 == "0000066740"


def test_normalize_name() -> None:
    assert normalize_name("PG&E Corporation") == "pg and e"
    assert normalize_name("JPMORGAN CHASE & CO") == "jpmorgan chase and"
    assert normalize_name("Corning Inc /NY") == "corning ny"


@pytest.mark.parametrize(
    ("ours", "sec", "ok"),
    [
        (["JPMorgan"], ["JPMORGAN CHASE & CO"], True),
        (["Walmart", "Wal-Mart Stores"], ["Walmart Inc.", "WAL MART STORES INC"], True),
        (["AMD", "Advanced Micro Devices"], ["ADVANCED MICRO DEVICES INC"], True),
        (["Corning"], ["CORNING INC /NY"], True),
        (["Intel"], ["INTELLIGENT SYSTEMS CORP"], False),
    ],
)
def test_names_agree(ours: list[str], sec: list[str], ok: bool) -> None:
    assert names_agree(ours, sec) is ok


@pytest.mark.skipif(not QUESTIONS_FILE.is_file(), reason="FinanceBench not downloaded")
def test_every_financebench_company_resolves(registry: CompanyRegistry) -> None:
    lines = QUESTIONS_FILE.read_text(encoding="utf-8").splitlines()
    names = {json.loads(line)["company"] for line in lines if line.strip()}
    assert {registry.resolve(n).name for n in names} == names
