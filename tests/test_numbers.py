from __future__ import annotations

import pytest

from filing_agent.eval.numbers import numbers_in_text, parse_bare_number, question_scale


@pytest.mark.parametrize(
    ("text", "value", "decimals", "money", "percent", "scale"),
    [
        ("$1577.00", 1577.0, 2, True, False, None),
        ("(1,234)", -1234.0, 0, False, False, None),
        ("-0.02", -0.02, 2, False, False, None),
        ("65.4%", 65.4, 1, False, True, None),
        ("24.26", 24.26, 2, False, False, None),
        ("$8.70 billion", 8.7, 2, True, False, "billions"),
        ("0", 0.0, 0, False, False, None),
    ],
)
def test_parse_bare_number(
    text: str, value: float, decimals: int, money: bool, percent: bool, scale: str | None
) -> None:
    parsed = parse_bare_number(text)
    assert parsed is not None
    assert (parsed.value, parsed.decimals, parsed.is_money, parsed.is_percent, parsed.scale) == (
        value,
        decimals,
        money,
        percent,
        scale,
    )


@pytest.mark.parametrize("text", ["Yes, 5%", "about 12", "N/A", ""])
def test_parse_bare_number_rejects_prose(text: str) -> None:
    assert parse_bare_number(text) is None


@pytest.mark.parametrize(
    ("question", "scale"),
    [
        ("What is the FY2018 capex (in USD millions) for 3M?", "millions"),
        ("How much did it pay? Answer in USD billions.", "billions"),
        ("How much does Pfizer expect to pay in USD million?", "millions"),
        ("What were sales in millions of dollars?", "millions"),
        ("Revenue (in thousands)?", "thousands"),
        ("What is the FY2019 fixed asset turnover ratio?", None),
    ],
)
def test_question_scale(question: str, scale: str | None) -> None:
    assert question_scale(question) == scale


def test_numbers_in_text_skip_years_and_keep_scale() -> None:
    [capex] = numbers_in_text("In FY2018, capex was $1,577 million.")
    assert (capex.value, capex.is_money, capex.scale) == (1577.0, True, "millions")
    growth = numbers_in_text("Revenue grew 30.8% in 2017 to $177.9 billion")
    assert [(n.value, n.is_percent, n.scale) for n in growth] == [
        (30.8, True, None),
        (177.9, False, "billions"),
    ]
    assert numbers_in_text("The quick ratio was 0.96 in Q2")[0].value == 0.96


@pytest.mark.parametrize("text", ["65.4%", "Margin was 65.4%.", "65.4%, up from 60%", "(65.4%)"])
def test_percent_sign_is_kept_at_the_end_and_before_punctuation(text: str) -> None:
    first = numbers_in_text(text)[0]
    assert (first.value, first.is_percent) == (65.4, True)


def test_parentheses_mean_negative_only_in_accounting_form() -> None:
    assert numbers_in_text("Net change (1,577)")[0].value == -1577.0
    aside = numbers_in_text("Capex was $1,577 million (4.8% of revenue)")
    assert [(n.value, n.is_percent) for n in aside] == [(1577.0, False), (4.8, True)]
