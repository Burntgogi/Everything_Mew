"""Immutable tool specifications shared by lite discovery and validation."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal, Mapping


ArgumentType = Literal["string", "integer", "boolean"]


@dataclass(frozen=True, slots=True)
class PropertySpec:
    kind: ArgumentType
    enum: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.kind}
        if self.enum:
            schema["enum"] = list(self.enum)
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        return schema


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    properties: Mapping[str, PropertySpec]
    required: tuple[str, ...] = ()

    def to_mcp_definition(self) -> dict[str, Any]:
        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": {name: spec.to_schema() for name, spec in self.properties.items()},
            "additionalProperties": False,
        }
        if self.required:
            input_schema["required"] = list(self.required)
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": input_schema,
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        }


def _properties(**specs: PropertySpec) -> Mapping[str, PropertySpec]:
    return MappingProxyType(dict(specs))


VALID_SORTS: Final = ("name", "path", "size", "date_modified")

TOOL_SPECS: Final = (
    ToolSpec(
        name="everything_status",
        description="Report whether Everything and the selected read-only backend are available.",
        properties=_properties(),
    ),
    ToolSpec(
        name="everything_count",
        description="Count matching Everything results inside a host-allowed scope.",
        properties=_properties(
            query=PropertySpec("string"),
            scope=PropertySpec("string"),
        ),
        required=("query",),
    ),
    ToolSpec(
        name="everything_search",
        description="Return path-first candidates inside a host-allowed scope; metadata requires host opt-in.",
        properties=_properties(
            query=PropertySpec("string"),
            scope=PropertySpec("string"),
            limit=PropertySpec("integer", minimum=1, maximum=100),
            sort=PropertySpec("string", enum=VALID_SORTS),
            metadata=PropertySpec("boolean"),
        ),
        required=("query",),
    ),
    ToolSpec(
        name="everything_syntax_help",
        description="Return concise Everything query syntax help.",
        properties=_properties(topic=PropertySpec("string")),
    ),
)

TOOL_SPEC_BY_NAME: Final[Mapping[str, ToolSpec]] = MappingProxyType({spec.name: spec for spec in TOOL_SPECS})


def tool_definitions() -> list[dict[str, Any]]:
    """Return JSON-serializable copies generated from the immutable specs."""
    return [spec.to_mcp_definition() for spec in TOOL_SPECS]
