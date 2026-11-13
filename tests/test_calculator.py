from __future__ import annotations

import pytest

from filing_agent.tools.calculator import CalculatorError, calculate


def test_named_inputs_and_substitution() -> None:
    result = calculate("(capex / revenue) * 100", {"capex": 1577, "revenue": 32765})
    assert result.result == pytest.approx(4.8130627193651)
    assert result.substituted == "1577 / 32765 * 100"
    assert result.variables == {"capex": 1577.0, "revenue": 32765.0}


def test_decimal_arithmetic_is_exact() -> None:
    assert calculate("0.1 + 0.2").result_text == "0.3"
    assert calculate("a - b", [("a", 1577.5), ("b", 1373.25)]).result_text == "204.25"


@pytest.mark.parametrize(
    ("expression", "text"),
    [
        ("round(2.675, 2)", "2.68"),  # half-up, unlike Python's round()
        ("round(-2.5)", "-3"),
        ("avg(10, 20, 33)", "21"),
        ("max(1, 7, 3) - min(4, 2)", "5"),
        ("sum(1, 2, 3)", "6"),
        ("sqrt(16)", "4"),
        ("abs(-1.5)", "1.5"),
        ("(1.21 ** 0.5 - 1) * 100", "10"),  # CAGR-style fractional power
        ("-(3 - 5) * 2", "4"),
        ("1 / 3", "0.333333333333333"),
    ],
)
def test_supported_operations(expression: str, text: str) -> None:
    assert calculate(expression).result_text == text


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("__import__('os').system('echo hi')", "Only these functions"),
        ("revenue.real", "Attribute is not allowed"),
        ("(lambda: 1)()", "Only these functions"),
        ("x + 1", "Unknown name 'x'"),
        ("1 / 0", "Division by zero"),
        ("10 ** 1000", "too large"),
        ("2 ^ 3", r"Use \*\* for powers"),
        ("1,577 + 1", "Commas are not allowed"),
        ("5% * 3", "Write percentages as numbers"),
        ("1 +", "Not a valid arithmetic expression"),
        ("", "empty"),
        ("1 < 2", "Compare is not allowed"),
        ("(-8) ** 0.5", "fractional power"),
        ("round(1.5, 0.5)", "whole number"),
    ],
)
def test_rejected_expressions_explain_why(expression: str, message: str) -> None:
    with pytest.raises(CalculatorError, match=message):
        calculate(expression)


def test_variable_names_are_checked() -> None:
    with pytest.raises(CalculatorError, match="usable variable name"):
        calculate("a", [("2bad", 1.0)])
    with pytest.raises(CalculatorError, match="only once"):
        calculate("a", [("a", 1.0), ("a", 2.0)])
    with pytest.raises(CalculatorError, match="usable variable name"):
        calculate("round", [("round", 1.0)])


def test_long_expressions_are_rejected() -> None:
    with pytest.raises(CalculatorError, match="limited"):
        calculate("+".join(["1"] * 400))
