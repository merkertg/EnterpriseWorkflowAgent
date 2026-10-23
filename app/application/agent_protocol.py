"""The model-provider port and strict tool-argument validation.

The shapes follow a tool-use loop: a model turn either requests tool calls
(each with an ID, a tool name and JSON arguments) or gives a final answer, or
the provider refuses. Every call result goes back with the call's ID and an
error flag. A later adapter for a real model maps these shapes to its API;
Stage 07 ships only deterministic providers that never use the network.

Everything a model returns is an untrusted proposal: arguments are checked
against the tool's schema here before any tool runs.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

type JsonObject = Mapping[str, Any]


class ArgumentError(Exception):
    """Arguments do not match a tool or answer schema."""

    code = "invalid_arguments"

    def __init__(self, message: str) -> None:
        """Keep a short reason without echoing the submitted values."""
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool offered to the model: name, purpose and strict JSON Schema."""

    name: str
    description: str
    input_schema: JsonObject


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One requested call; the ID links it to its result."""

    call_id: str
    name: str
    arguments: JsonObject


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What the runner returns for one call; errors carry a code only."""

    call_id: str
    content: JsonObject
    is_error: bool = False


class TurnKind(StrEnum):
    """What a model turn contains."""

    TOOL_CALLS = "tool_calls"
    FINAL = "final"
    REFUSAL = "refusal"


@dataclass(frozen=True, slots=True)
class ModelTurn:
    """One provider answer: tool calls, a final answer or a refusal."""

    kind: TurnKind
    tool_calls: tuple[ToolCall, ...] = ()
    answer: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class UserMessage:
    """The task given to the model; document content arrives only via tools."""

    text: str


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """A previous model turn, replayed so the model sees its own calls."""

    turn: ModelTurn


@dataclass(frozen=True, slots=True)
class ToolResultsMessage:
    """All results for one turn's calls, returned together."""

    results: tuple[ToolResult, ...]


type Message = UserMessage | AssistantMessage | ToolResultsMessage


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Everything a provider sees: fixed instructions, tools and the history."""

    system: str
    tools: tuple[ToolSpec, ...]
    messages: tuple[Message, ...]
    answer_schema: JsonObject = field(default_factory=dict)


class ModelProvider(Protocol):
    """A model behind a port; it proposes, the application decides."""

    @property
    def name(self) -> str:
        """A short label recorded in traces, such as demo-rules/1."""
        ...

    def complete(self, request: ModelRequest) -> ModelTurn:
        """Return the next turn; may raise on transport or format errors."""
        ...


def validate_against_schema(schema: JsonObject, value: object) -> None:
    """Check a value against the strict JSON Schema subset used by tools.

    Supported: object (properties, required, additionalProperties false),
    string (minLength, maxLength, enum, pattern), integer (minimum, maximum),
    boolean and array (items, maxItems). Booleans are never integers, and an
    object schema must forbid extra keys, so a typo cannot pass silently.
    """
    _check(schema, value, "$")


def _check(schema: JsonObject, value: object, path: str) -> None:
    """Validate one node; error messages name the path, never the value."""
    kind = schema.get("type")
    if kind == "object":
        _check_object(schema, value, path)
    elif kind == "string":
        _check_string(schema, value, path)
    elif kind == "integer":
        if type(value) is not int:
            raise ArgumentError(f"{path} must be an integer.")
        if "minimum" in schema and value < schema["minimum"]:
            raise ArgumentError(f"{path} is below the minimum.")
        if "maximum" in schema and value > schema["maximum"]:
            raise ArgumentError(f"{path} is above the maximum.")
    elif kind == "boolean":
        if type(value) is not bool:
            raise ArgumentError(f"{path} must be a boolean.")
    elif kind == "array":
        if not isinstance(value, Sequence) or isinstance(value, str | bytes):
            raise ArgumentError(f"{path} must be an array.")
        if len(value) > schema.get("maxItems", 50):
            raise ArgumentError(f"{path} has too many items.")
        for index, item in enumerate(value):
            _check(schema["items"], item, f"{path}[{index}]")
    else:
        raise ArgumentError(f"{path} uses an unsupported schema type.")


def _check_object(schema: JsonObject, value: object, path: str) -> None:
    """Require a mapping with string keys, every required key and no others."""
    if schema.get("additionalProperties") is not False:
        raise ArgumentError(f"{path} schema must forbid extra keys.")
    if not isinstance(value, Mapping) or any(type(k) is not str for k in value):
        raise ArgumentError(f"{path} must be an object.")
    properties: JsonObject = schema.get("properties", {})
    extra = sorted(set(value) - set(properties))
    if extra:
        raise ArgumentError(f"{path} has unknown keys: {', '.join(extra)[:120]}.")
    missing = [key for key in schema.get("required", ()) if key not in value]
    if missing:
        raise ArgumentError(f"{path} lacks required keys: {', '.join(missing)}.")
    for key, item in value.items():
        _check(properties[key], item, f"{path}.{key}")


def _check_string(schema: JsonObject, value: object, path: str) -> None:
    """Check length, allowed values and the full-match pattern."""
    if type(value) is not str:
        raise ArgumentError(f"{path} must be a string.")
    if len(value) < schema.get("minLength", 0):
        raise ArgumentError(f"{path} is too short.")
    if len(value) > schema.get("maxLength", 200):
        raise ArgumentError(f"{path} is too long.")
    if "enum" in schema and value not in schema["enum"]:
        raise ArgumentError(f"{path} is not an allowed value.")
    if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
        raise ArgumentError(f"{path} has an invalid format.")
