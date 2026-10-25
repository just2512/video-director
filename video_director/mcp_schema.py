"""Small, dependency-free JSON Schema validator for MCP tool argument contracts.

It implements the schema keywords used by Video Director's advertised tool
catalogue. Keep this deliberately limited and explicit rather than silently
accepting constraints the runtime does not understand.
"""
from __future__ import annotations

import math
import re
from typing import Any


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        # Finiteness is validated separately so callers receive a precise
        # numeric-bound error instead of a misleading type mismatch.
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_json_schema(value: Any, schema: dict[str, Any], path: str = "arguments") -> str | None:
    """Return the first contract violation, or None when the value matches schema."""
    if not isinstance(schema, dict):
        return None

    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected] if expected else []
    if expected_types and not any(_matches_type(value, kind) for kind in expected_types):
        return f"{path} must be of type {' or '.join(str(kind) for kind in expected_types)}"

    if "enum" in schema and value not in schema["enum"]:
        return f"{path} must be one of {schema['enum']!r}"

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            return f"{path} must contain at least {minimum} characters"
        if isinstance(maximum, int) and len(value) > maximum:
            return f"{path} must contain at most {maximum} characters"
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            return f"{path} does not match the required pattern"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Integers are always finite. Do not coerce an arbitrary-size int to
        # float here: that can overflow before the schema's range check runs.
        if isinstance(value, float) and not math.isfinite(value):
            return f"{path} must be finite"
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            return f"{path} must be at least {minimum}"
        if isinstance(maximum, (int, float)) and value > maximum:
            return f"{path} must be at most {maximum}"

    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            missing = [key for key in required if key not in value]
            if missing:
                return f"{path} is missing required field(s): {', '.join(map(str, missing))}"

        properties = schema.get("properties", {})
        properties = properties if isinstance(properties, dict) else {}
        extras = [key for key in value if key not in properties]
        additional = schema.get("additionalProperties", True)
        if additional is False and extras:
            return f"{path} contains undeclared field(s): {', '.join(sorted(map(str, extras)))}"
        if isinstance(additional, dict):
            for key in extras:
                error = validate_json_schema(value[key], additional, f"{path}.{key}")
                if error:
                    return error

        for key, child_schema in properties.items():
            if key not in value:
                continue
            error = validate_json_schema(value[key], child_schema, f"{path}.{key}")
            if error:
                return error

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and isinstance(maximum, int):
            if len(value) < minimum or len(value) > maximum:
                return f"{path} must contain {minimum}–{maximum} items"
        else:
            if isinstance(minimum, int) and len(value) < minimum:
                return f"{path} must contain at least {minimum} items"
            if isinstance(maximum, int) and len(value) > maximum:
                return f"{path} must contain at most {maximum} items"
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                error = validate_json_schema(item, item_schema, f"{path}[{index}]")
                if error:
                    return error

    for keyword in ("anyOf", "oneOf"):
        branches = schema.get(keyword)
        if not isinstance(branches, list):
            continue
        matches = sum(
            validate_json_schema(value, branch, path) is None
            for branch in branches
            if isinstance(branch, dict)
        )
        if keyword == "anyOf" and matches == 0:
            return f"{path} must match at least one allowed schema"
        if keyword == "oneOf" and matches != 1:
            return f"{path} must match exactly one allowed schema"

    return None
