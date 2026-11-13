from __future__ import annotations

from datetime import date

import pytest

from filing_agent.sec.xbrl import (
    CompanyFacts,
    UnknownConceptError,
    concept_words,
    find_concepts,
    get_facts,
    period_type,
)
from tests.sec_fixtures import CAPEX, TEN_K, M, acme_companyfacts, retailer_companyfacts


@pytest.fixture(scope="module")
def acme() -> CompanyFacts:
    return CompanyFacts.model_validate(acme_companyfacts())


@pytest.mark.parametrize(
    ("start", "end", "kind"),
    [
        (None, date(2018, 12, 31), "instant"),
        (date(2018, 1, 1), date(2018, 12, 31), "annual"),
        (date(2022, 1, 30), date(2023, 1, 28), "annual"),  # 52-week year
        (date(2019, 4, 1), date(2019, 6, 30), "quarter"),
        (date(2019, 1, 1), date(2019, 6, 30), "ytd"),
        (date(2019, 1, 1), date(2019, 9, 30), "ytd"),
        (date(2019, 1, 1), date(2019, 1, 31), "other"),
    ],
)
def test_period_type(start: date | None, end: date, kind: str) -> None:
    assert period_type(start, end) == kind


def test_comparative_periods_get_their_own_fiscal_year(acme: CompanyFacts) -> None:
    facts = get_facts(acme, "PaymentsToAcquirePropertyPlantAndEquipment", period="annual")
    assert [(f.fiscal_year, f.value / M) for f in facts] == [
        (2019, 1699),
        (2018, 1577),
        (2017, 1373),
        (2016, 1420),
        (2015, 1461),
    ]
    # FY2017 capex appears in the FY2017, FY2018 and FY2019 10-Ks (tagged fy=2017, 2018, 2019);
    # its label comes from the 10-K whose own period it is, and its value from the first report.
    fy2017 = next(f for f in facts if f.fiscal_year == 2017)
    assert fy2017.accession == TEN_K[2017][0]
    # FY2015 and FY2016 only ever appear as comparatives (ACME's first XBRL 10-K is FY2017),
    # so their labels are inferred from the year offset and flagged as such.
    assert [f.fiscal_label_inferred for f in facts] == [False, False, False, True, True]


def test_filter_by_fiscal_year_and_prefixed_concept(acme: CompanyFacts) -> None:
    concept = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
    [fact] = get_facts(acme, concept, fiscal_year=2018)
    assert fact.value == CAPEX[2018] * M
    assert (fact.start, fact.end, fact.fiscal_period) == (
        date(2018, 1, 1),
        date(2018, 12, 31),
        "FY",
    )
    assert fact.concept == "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
    assert not fact.restated


def test_restatement_is_flagged_and_policy_picks_the_value(acme: CompanyFacts) -> None:
    [first] = get_facts(acme, "Revenues", fiscal_year=2018, period="annual")
    [latest] = get_facts(acme, "Revenues", fiscal_year=2018, period="annual", policy="latest")
    assert (first.value, first.accession) == (32765 * M, TEN_K[2018][0])
    assert (latest.value, latest.accession) == (32700 * M, TEN_K[2019][0])
    assert first.restated and latest.restated
    assert [v.value for v in first.reported_values] == [32765 * M, 32700 * M]


def test_quarter_ytd_and_q4_labels(acme: CompanyFacts) -> None:
    quarters = get_facts(acme, "Revenues", period="quarter")
    labels = {(f.end, f.fiscal_year, f.fiscal_period): f.value / M for f in quarters}
    assert labels == {
        (date(2019, 12, 31), 2019, "Q4"): 7991,  # three months first reported in the 10-K
        (date(2019, 6, 30), 2019, "Q2"): 8171,
        (date(2019, 3, 31), 2019, "Q1"): 7863,
        (date(2018, 3, 31), 2018, "Q1"): 8278,  # comparative in the 2019 10-Q keeps 2018
    }
    [ytd] = get_facts(acme, "Revenues", period="ytd")
    assert (ytd.value / M, ytd.fiscal_year, ytd.fiscal_period) == (16034, 2019, "Q2")


def test_8k_values_are_ignored(acme: CompanyFacts) -> None:
    assert all(f.form.startswith("10-") for f in get_facts(acme, "Revenues"))
    assert 1.0 not in {f.value for f in get_facts(acme, "Revenues")}


def test_instants_are_labelled_by_the_report_that_first_had_them(acme: CompanyFacts) -> None:
    facts = get_facts(acme, "PropertyPlantAndEquipmentNet", period="instant")
    assert [(f.end, f.fiscal_year, f.fiscal_period) for f in facts] == [
        (date(2019, 3, 31), 2019, "Q1"),
        (date(2018, 12, 31), 2018, "FY"),
        (date(2017, 12, 31), 2017, "FY"),
    ]


def test_fiscal_labels_follow_each_companys_convention() -> None:
    # Same 52-week year (ending 2023-01-28); one company calls it fiscal 2022, the other 2023.
    shoe = CompanyFacts.model_validate(retailer_companyfacts(2, "SHOE LOCKER INC", 2022))
    box = CompanyFacts.model_validate(retailer_companyfacts(3, "BEST BOX INC", 2023))
    assert get_facts(shoe, "Revenues")[0].fiscal_year == 2022
    assert get_facts(box, "Revenues")[0].fiscal_year == 2023


def test_unknown_concept_suggests_close_matches(acme: CompanyFacts) -> None:
    with pytest.raises(UnknownConceptError) as excinfo:
        get_facts(acme, "PropertyPlantEquipmentGross")
    assert "us-gaap:PropertyPlantAndEquipmentNet" in excinfo.value.suggestions


def test_concept_words_split_camel_case() -> None:
    assert concept_words("PaymentsToAcquirePPE2019") == ["payments", "to", "acquire", "ppe", "2019"]


def test_find_concepts_ranks_name_and_label_matches(acme: CompanyFacts) -> None:
    matches = find_concepts(acme, "payments to acquire property, plant and equipment")
    assert matches[0].concept == "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
    assert matches[0].units == ["USD"]
    assert (matches[0].first_year, matches[0].last_year) == (2015, 2019)
    assert find_concepts(acme, "revenue")[0].concept == "us-gaap:Revenues"
    assert find_concepts(acme, "zzz unrelated") == []
