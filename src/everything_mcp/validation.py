"""Dependency-free validation for lite MCP tool arguments."""

from __future__ import annotations

from typing import Any, Mapping

from .tool_specs import PropertySpec, ToolSpec


class ToolValidationError(ValueError):
    """Raised when MCP tool arguments do not match the advertised schema."""


def validate_tool_arguments(spec: ToolSpec, arguments: Mapping[str, Any]) -> dict[str, Any]:
    for required_name in spec.required:
        if required_name not in arguments:
            raise ToolValidationError(f"Missing required argument: {required_name}.")

    for name in arguments:
        if name not in spec.properties:
            raise ToolValidationError(f"Unexpected argument: {name}.")

    for name, value in arguments.items():
        _validate_property(name, value, spec.properties[name])

    return dict(arguments)


def _validate_property(name: str, value: Any, spec: PropertySpec) -> None:
    if spec.kind == "string":
        if not isinstance(value, str):
            raise ToolValidationError(f"{name} must be a string.")
        if spec.enum and value not in spec.enum:
            choices = ", ".join(spec.enum)
            raise ToolValidationError(f"{name} must be one of: {choices}.")
        return

    if spec.kind == "boolean":
        if type(value) is not bool:
            raise ToolValidationError(f"{name} must be a boolean.")
        return

    if type(value) is not int:
        raise ToolValidationError(f"{name} must be an integer.")
    if spec.minimum is not None and value < spec.minimum or spec.maximum is not None and value > spec.maximum:
        if spec.minimum is not None and spec.maximum is not None:
            raise ToolValidationError(f"{name} must be between {spec.minimum} and {spec.maximum}.")
        if spec.minimum is not None:
            raise ToolValidationError(f"{name} must be at least {spec.minimum}.")
        raise ToolValidationError(f"{name} must be at most {spec.maximum}.")
