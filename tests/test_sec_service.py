from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from filing_agent.cli import app
from filing_agent.config import get_settings
from filing_agent.sec import commands as sec_commands
from filing_agent.sec.companies import Company, CompanyRegistry
from filing_agent.sec.http import (
    SecClient,
    cache_path,
    companyfacts_url,
    submissions_page_url,
    submissions_url,
)
from filing_agent.sec.service import SecService
from filing_agent.sec.submissions import parse_submissions, select_filings
from tests.sec_fixtures import TEN_K, acme_companyfacts, acme_submissions, acme_submissions_page


def _seed_cache(cache_dir: Path) -> None:
    for url, payload in [
        (companyfacts_url(1), acme_companyfacts()),
        (submissions_url(1), acme_submissions()),
        (submissions_page_url("CIK0000000001-submissions-001.json"), acme_submissions_page()),
    ]:
        path = cache_path(cache_dir, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def service(tmp_path: Path) -> SecService:
    _seed_cache(tmp_path)
    registry = CompanyRegistry([Company(name="Acme", cik=1, tickers=["ACME"])])
    return SecService(SecClient(None, tmp_path, offline=True), registry)


def test_parse_submissions_with_older_pages() -> None:
    subs = parse_submissions(acme_submissions(), [acme_submissions_page()])
    assert (subs.name, subs.fiscal_year_end, subs.former_names) == (
        "ACME CORP",
        "1231",
        ["ACME MANUFACTURING CO"],
    )
    assert [f.filing_date.year for f in subs.filings] == [2020, 2019, 2019, 2019, 2018]
    tenk_2018 = select_filings(subs, forms=["10-k"], year=2018)
    assert [f.accession for f in tenk_2018] == [TEN_K[2018][0]]
    assert tenk_2018[0].url == (
        "https://www.sec.gov/Archives/edgar/data/1/000000000119000001/acme-10k2018.htm"
    )
    assert tenk_2018[0].report_date == date(2018, 12, 31)


def test_service_answers_offline_from_the_cache(service: SecService) -> None:
    [capex] = service.get_facts(
        "acme", "PaymentsToAcquirePropertyPlantAndEquipment", fiscal_year=2018
    )
    assert capex.value == 1577 * 1_000_000
    assert service.find_concepts("ACME", "revenues")[0].concept == "us-gaap:Revenues"
    subs = service.submissions(service.company("acme"), include_older=True)
    assert len(subs.filings) == 5


def test_service_keeps_a_bounded_number_of_companies(service: SecService) -> None:
    service.max_companies = 1
    service.company_facts(service.company("acme"))
    assert list(service._facts) == [1]


@pytest.fixture
def offline_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "companies.yaml").write_text(
        "companies:\n  - {name: Acme, cik: 1, tickers: [ACME]}\n", encoding="utf-8"
    )
    monkeypatch.setenv("FA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FA_CONFIGS_DIR", str(configs))
    monkeypatch.setenv("FA_SEC_OFFLINE", "1")
    _seed_cache(tmp_path / "data" / "cache" / "sec")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_cli_facts_and_concepts_offline(offline_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sec_commands, "console", Console(width=200))  # no wrapped table cells
    runner = CliRunner()
    facts = runner.invoke(
        app, ["sec", "facts", "acme", "Revenues", "--year", "2018", "--period", "annual"]
    )
    assert facts.exit_code == 0, facts.stdout
    assert "32,765,000,000" in facts.stdout
    assert "yes" in facts.stdout  # restated
    concepts = runner.invoke(app, ["sec", "concepts", "acme", "property plant equipment"])
    assert concepts.exit_code == 0, concepts.stdout
    assert "PropertyPlantAndEquipmentNet" in concepts.stdout
    missing = runner.invoke(app, ["sec", "facts", "acme", "NoSuchConcept"])
    assert missing.exit_code == 1
