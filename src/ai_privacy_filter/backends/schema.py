"""Tiny JSON-schema validator for backend replies.

The project keeps no JSON-schema dependency: :data:`ai_privacy_filter.triage.TRIAGE_SCHEMA`
uses a small, fixed keyword set. This validator implements exactly that subset so
a reply is checked against the same schema the CLI or API was asked to honour.

Supported keywords: ``type`` (a name or list of names), ``enum``, ``properties``,
``required``, ``additionalProperties`` (``false``), ``minLength``, ``maxLength``,
``items``, ``minItems`` and ``maxItems``. Unsupported keywords are ignored rather
than silently accepted as a promise; callers keep schemas inside this subset.

Error messages name the structural problem and a path only, never a model value
or a prompt fragment.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["validate"]


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _matches_type(value: Any, expected: str) -> bool:
    actual = _type_name(value)
    if expected == "number":
        return actual in {"integer", "number"}
    if expected == "integer":
        return actual == "integer"
    return actual == expected


def validate(payload: Any, schema: Mapping[str, Any]) -> str | None:
    """Return a structural error message, or ``None`` when ``payload`` matches."""
    return _check(payload, schema, "$")


def _check(value: Any, schema: Mapping[str, Any], path: str) -> str | None:
    expected = schema.get("type")
    if isinstance(expected, str):
        if not _matches_type(value, expected):
            return f"{path}: expected {expected}, got {_type_name(value)}"
    elif isinstance(expected, list) and expected:
        names = [name for name in expected if isinstance(name, str)]
        if names and not any(_matches_type(value, name) for name in names):
            return f"{path}: unexpected type {_type_name(value)}"

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return f"{path}: value is not one of the allowed values"

    if isinstance(value, str):
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(value) < minimum:
            return f"{path}: string is shorter than {minimum}"
        maximum = schema.get("maxLength")
        if isinstance(maximum, int) and len(value) > maximum:
            return f"{path}: string is longer than {maximum}"

    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                error = _check(item, items, f"{path}[{index}]")
                if error is not None:
                    return error
        minimum_items = schema.get("minItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            return f"{path}: fewer than {minimum_items} items"
        maximum_items = schema.get("maxItems")
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            return f"{path}: more than {maximum_items} items"

    if isinstance(value, Mapping):
        required = schema.get("required")
        if isinstance(required, Sequence) and not isinstance(required, str):
            for name in required:
                if isinstance(name, str) and name not in value:
                    return f"{path}: missing required property {name!r}"
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            for name, subschema in properties.items():
                if not isinstance(name, str) or name not in value:
                    continue
                if not isinstance(subschema, Mapping):
                    continue
                error = _check(value[name], subschema, f"{path}.{name}")
                if error is not None:
                    return error
            if schema.get("additionalProperties") is False:
                for name in value:
                    if name not in properties:
                        return f"{path}: unexpected property {name!r}"
    return None
