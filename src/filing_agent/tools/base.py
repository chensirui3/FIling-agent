"""Tool definitions shared by the agent loop and the MCP server.

Each tool has a name, a description, a Pydantic input model and a function. The input model
generates the JSON schema sent to Claude (`strict: true`), and also validates every call at run
time, including constraints that strict schemas cannot express (bounds, lengths).

Outputs go back to the model as compact JSON, capped per tool with an explicit truncation note.
Expected failures (unknown company, bad expression, uncached SEC data) come back as tool errors
the model can act on. Anything else is marked internal, so the error analysis can tell harness
bugs from model mistakes.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

# Keywords strict tool schemas do not support; Pydantic still enforces them on each call.
_UNSUPPORTED = {
    "title",
    "default",
    "examples",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
}


def _without_null(prop: dict[str, Any]) -> dict[str, Any]:
    """{"anyOf": [X, {"type": "null"}], ...} -> X (keeping the description)."""
    options = prop.get("anyOf")
    if not isinstance(options, list):
        return prop
    non_null = [option for option in options if option != {"type": "null"}]
    if len(non_null) != 1 or len(non_null) == len(options):
        return prop
    return {key: value for key, value in prop.items() if key != "anyOf"} | non_null[0]


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema for strict tool use and structured outputs.

    Objects are closed, unsupported constraints are dropped (Pydantic still enforces them), and
    optional fields are simply not required instead of required-but-nullable: the API caps a
    request at 16 union-typed parameters, and nullable fields count as unions.
    """
    schema = copy.deepcopy(model.model_json_schema())

    def clean(node: Any, *, name_map: bool = False) -> Any:
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        if name_map:  # field names -> schemas: keep every key, clean each schema
            return {key: clean(value) for key, value in node.items()}
        out = {
            key: clean(value, name_map=key in ("properties", "$defs"))
            for key, value in node.items()
            if key not in _UNSUPPORTED
        }
        if out.get("type") == "object" or "properties" in out:
            properties = out.setdefault("properties", {})
            required = [name for name in node.get("required", []) if name in properties]
            for name in properties:
                if name not in required:
                    properties[name] = _without_null(properties[name])
            out["required"] = required
            out["additionalProperties"] = False
        return out

    return clean(schema)


def count_unions(schema: Any) -> int:
    """Union-typed parameters (anyOf or a list of types), as the API counts them."""
    if isinstance(schema, list):
        return sum(count_unions(item) for item in schema)
    if not isinstance(schema, dict):
        return 0
    own = int("anyOf" in schema or isinstance(schema.get("type"), list))
    return own + sum(count_unions(value) for value in schema.values())


class ToolError(Exception):
    """An expected failure, reported to the model as a tool error."""


@dataclass(frozen=True)
class ToolOutcome:
    content: str  # what the model sees
    is_error: bool
    data: Any  # the structured result (for traces and error attribution)
    latency_ms: float
    internal_error: bool = False  # an unexpected exception: a harness bug, not a model mistake
    truncated: bool = False


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[Any], Any]
    max_chars: int = 6000
    expected_errors: tuple[type[Exception], ...] = ()

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": strict_schema(self.input_model),
            "strict": True,
        }

    def run(self, raw_input: Any) -> ToolOutcome:
        started = time.perf_counter()

        def outcome(content: str, *, error: bool, data: Any = None, **flags: bool) -> ToolOutcome:
            return ToolOutcome(
                content, error, data, round((time.perf_counter() - started) * 1000, 1), **flags
            )

        try:
            args = self.input_model.model_validate(raw_input)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors()
            )
            return outcome(f"Invalid input for {self.name}: {problems}", error=True)
        try:
            data = self.fn(args)
        except (ToolError, *self.expected_errors) as exc:
            return outcome(str(exc), error=True)
        except Exception as exc:  # surfaced as an internal error so it is never blamed on the model
            return outcome(
                f"Internal error in {self.name}: {type(exc).__name__}: {exc}",
                error=True,
                internal_error=True,
            )
        text, truncated = render(data, self.max_chars)
        return outcome(text, error=False, data=data, truncated=truncated)


def _jsonable(data: Any) -> Any:
    if isinstance(data, BaseModel):
        return data.model_dump(mode="json", exclude_none=True)
    if isinstance(data, list | tuple):
        return [_jsonable(item) for item in data]
    if isinstance(data, dict):
        return {key: _jsonable(value) for key, value in data.items()}
    return data


def render(data: Any, max_chars: int) -> tuple[str, bool]:
    text = data if isinstance(data, str) else json.dumps(_jsonable(data), ensure_ascii=False)
    if len(text) <= max_chars:
        return text, False
    note = f"\n[truncated: showing {max_chars} of {len(text)} characters; narrow the request]"
    return text[:max_chars] + note, True


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def __getitem__(self, name: str) -> Tool:
        return self._tools[name]

    def definitions(self) -> list[dict[str, Any]]:
        return [tool.definition() for tool in self._tools.values()]

    def subset(self, names: Iterable[str]) -> ToolRegistry:
        wanted = list(names)
        if unknown := [n for n in wanted if n not in self._tools]:
            raise KeyError(f"Unknown tools: {unknown}")
        return ToolRegistry(self._tools[n] for n in wanted)

    def run(self, name: str, raw_input: Any) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome(f"There is no tool named {name!r}.", True, None, 0.0)
        return tool.run(raw_input)
