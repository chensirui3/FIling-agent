"""Safe, exact arithmetic for the agent.

Expressions are parsed with `ast` and evaluated against a whitelist (numbers, named variables,
+ - * / ** and a few functions) in Decimal, so 0.1 + 0.2 is exactly 0.3 and `round` is half-up as
in financial reporting. There is no `eval`. Inputs are named: an answer's trace then shows which
numbers went into which formula, which lets the error analysis tell a wrong input (table
reading) from a wrong formula or a wrong calculation.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Mapping, Sequence
from decimal import (
    ROUND_HALF_UP,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

from pydantic import BaseModel

MAX_EXPRESSION_CHARS = 500
MAX_ABS_EXPONENT = Decimal(100)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CalculatorError(ValueError):
    """A message for the model: what was wrong with the expression."""


class CalcResult(BaseModel):
    expression: str
    substituted: str  # the expression with every variable replaced by its value
    variables: dict[str, float]
    result: float
    result_text: str  # up to 15 significant digits, no exponent notation


def _round(value: Decimal, digits: Decimal = Decimal(0)) -> Decimal:
    if digits != digits.to_integral_value():
        raise CalculatorError("round(x, digits) needs a whole number of digits.")
    return value.quantize(Decimal(1).scaleb(-int(digits)), rounding=ROUND_HALF_UP)


def _avg(*values: Decimal) -> Decimal:
    if not values:
        raise CalculatorError("avg() needs at least one value.")
    return sum(values, Decimal(0)) / len(values)


def _sqrt(value: Decimal) -> Decimal:
    if value < 0:
        raise CalculatorError("sqrt() of a negative number.")
    return value.sqrt()


_FUNCTIONS: dict[str, Callable[..., Decimal]] = {
    "abs": lambda x: abs(x),
    "round": _round,
    "min": lambda *xs: min(xs),
    "max": lambda *xs: max(xs),
    "sum": lambda *xs: sum(xs, Decimal(0)),
    "avg": _avg,
    "sqrt": _sqrt,
}
_BINARY: dict[type[ast.operator], Callable[[Decimal, Decimal], Decimal]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}


def _decimal(value: float | int | Decimal) -> Decimal:
    if isinstance(value, bool):
        raise CalculatorError("Booleans are not numbers here.")
    # repr() keeps the shortest exact form of a float (1577.5 stays 1577.5, not 1577.4999...).
    number = value if isinstance(value, Decimal) else Decimal(repr(value))
    if not number.is_finite():
        raise CalculatorError("Only finite numbers are allowed.")
    return number


def _evaluate(node: ast.AST, variables: Mapping[str, Decimal]) -> Decimal:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return _decimal(node.value)
    if isinstance(node, ast.Name):
        if node.id not in variables:
            known = ", ".join(sorted(variables)) or "none"
            raise CalculatorError(f"Unknown name {node.id!r}. Variables given: {known}.")
        return variables[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd | ast.USub):
        operand = _evaluate(node.operand, variables)
        return -operand if isinstance(node.op, ast.USub) else operand
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left, variables)
        right = _evaluate(node.right, variables)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_ABS_EXPONENT:
                raise CalculatorError(f"Exponent {right} is too large (limit {MAX_ABS_EXPONENT}).")
            if left < 0 and right != right.to_integral_value():
                raise CalculatorError("A negative number cannot be raised to a fractional power.")
            return left**right
        if isinstance(node.op, ast.BitXor):
            raise CalculatorError("Use ** for powers (^ is not exponentiation).")
        operation = _BINARY.get(type(node.op))
        if operation is None:
            raise CalculatorError(f"Operator {type(node.op).__name__} is not supported.")
        return operation(left, right)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            allowed = ", ".join(_FUNCTIONS)
            raise CalculatorError(f"Only these functions are available: {allowed}.")
        if node.keywords:
            raise CalculatorError("Pass function arguments by position, e.g. round(x, 2).")
        args = [_evaluate(arg, variables) for arg in node.args]
        try:
            return _FUNCTIONS[node.func.id](*args)
        except TypeError as exc:
            raise CalculatorError(f"Wrong arguments for {node.func.id}(): {exc}") from None
    if isinstance(node, ast.Tuple):
        raise CalculatorError("Commas are not allowed inside numbers: write 1577, not 1,577.")
    raise CalculatorError(f"{type(node).__name__} is not allowed in an arithmetic expression.")


class _Substitute(ast.NodeTransformer):
    def __init__(self, values: Mapping[str, float | int]) -> None:
        self.values = values

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.values:
            return ast.copy_location(ast.Constant(self.values[node.id]), node)
        return node


def _format(value: Decimal) -> str:
    """15 significant digits; unlike floats, Decimal keeps trailing zeros, so drop them."""
    text = format(value, ".15g")
    mantissa, marker, exponent = text.partition("e" if "e" in text else "E")
    if "." in mantissa:
        mantissa = mantissa.rstrip("0").rstrip(".")
    text = mantissa + marker + exponent
    return "0" if text == "-0" else text


def _plain(value: Decimal) -> float | int:
    number = float(value)
    return int(number) if number.is_integer() and abs(number) < 2**53 else number


def calculate(
    expression: str, variables: Mapping[str, float] | Sequence[tuple[str, float]] = ()
) -> CalcResult:
    expression = expression.strip()
    if not expression:
        raise CalculatorError("The expression is empty.")
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise CalculatorError(f"Expressions are limited to {MAX_EXPRESSION_CHARS} characters.")
    if "%" in expression:
        raise CalculatorError("Write percentages as numbers: 5% is 0.05 (or 5 / 100).")
    pairs = list(variables.items()) if isinstance(variables, Mapping) else list(variables)
    for name, _ in pairs:
        if not _IDENTIFIER.match(name) or name in _FUNCTIONS:
            raise CalculatorError(f"{name!r} is not a usable variable name.")
    if len({name for name, _ in pairs}) != len(pairs):
        raise CalculatorError("Each variable name may appear only once.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"Not a valid arithmetic expression: {exc.msg}.") from None

    values = {name: _decimal(value) for name, value in pairs}
    with localcontext() as context:
        context.prec = 34
        context.traps[DivisionByZero] = True
        context.traps[InvalidOperation] = True
        context.traps[Overflow] = True
        try:
            result = _evaluate(tree.body, values)
        except (DivisionByZero, ZeroDivisionError):
            raise CalculatorError("Division by zero.") from None
        except (InvalidOperation, Overflow) as exc:
            raise CalculatorError(f"The result is undefined or too large ({exc}).") from None
    plain = {name: float(value) for name, value in pairs}
    shown = {name: _plain(number) for name, number in values.items()}
    substituted = ast.unparse(_Substitute(shown).visit(tree)) if shown else expression
    return CalcResult(
        expression=expression,
        substituted=substituted,
        variables=plain,
        result=float(result),
        result_text=_format(result),
    )
