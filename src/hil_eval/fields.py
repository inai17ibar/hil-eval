"""Structured label fields: schema validation, proposed values, diffs.

A field schema is a list of JSON-compatible dicts (or :class:`Field` objects)::

    [
        {"name": "family", "kind": "enum", "choices": ["drums", "keys"]},
        {"name": "leaf", "kind": "enum", "depends_on": "family",
         "choices": {"drums": ["kick", "snare"], "keys": ["piano"]}},
        {"name": "type", "kind": "enum", "choices": ["loop", "one_shot"]},
        {"name": "bpm", "kind": "number", "min": 40, "max": 240, "integer": True},
        {"name": "key", "kind": "text"},
    ]

``path`` (default: the field name) is a dotted path into the item output used to
prefill the proposed value. Reviewers submit a ``{name: value}`` dict; the review
stores the full corrected dict plus a per-field ``{"from", "to"}`` diff.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

KINDS = ("enum", "number", "text")
_MISSING = object()


@dataclass
class Field:
    """One structured label field; see the module docstring for the JSON form."""

    name: str
    kind: str = "enum"
    label: str | None = None
    path: str | None = None
    choices: list[str] | dict[str, list[str]] | None = None
    depends_on: str | None = None
    allow_custom: bool = False
    nullable: bool = True
    min: float | None = None
    max: float | None = None
    step: float | None = None
    integer: bool = False
    multipliers: list[float] | None = None
    suggestions: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


class FieldError(ValueError):
    """Invalid field schema or field values."""


def _number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _strings(values: Any, where: str) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise FieldError(f"{where} must be a list of strings")
    return list(values)


def normalize_fields(fields: Any) -> list[dict[str, Any]] | None:
    """Validate a schema and return it as a list of plain dicts (``None`` stays ``None``)."""
    if fields is None:
        return None
    if not isinstance(fields, (list, tuple)):
        raise FieldError("fields must be a list of field definitions")
    allowed = set(Field.__dataclass_fields__)
    result: list[dict[str, Any]] = []
    names: list[str] = []
    for index, raw in enumerate(fields):
        if isinstance(raw, Field):
            raw = raw.to_dict()
        if not isinstance(raw, dict):
            raise FieldError(f"fields[{index}] must be an object")
        unknown = set(raw) - allowed
        if unknown:
            raise FieldError(f"fields[{index}] has unknown key(s): {sorted(unknown)}")
        try:
            spec = Field(**raw)
        except TypeError as exc:
            raise FieldError(f"fields[{index}]: {exc}") from exc
        if not isinstance(spec.name, str) or not spec.name:
            raise FieldError(f"fields[{index}].name must be a non-empty string")
        if spec.name in names:
            raise FieldError(f"duplicate field name {spec.name!r}")
        if spec.kind not in KINDS:
            raise FieldError(f"field {spec.name!r}: kind must be one of {', '.join(KINDS)}")
        if spec.path is not None and (not isinstance(spec.path, str) or not spec.path):
            raise FieldError(f"field {spec.name!r}: path must be a non-empty string")
        for flag in ("allow_custom", "nullable", "integer"):
            if not isinstance(getattr(spec, flag), bool):
                raise FieldError(f"field {spec.name!r}: {flag} must be a boolean")
        if spec.kind == "enum":
            if spec.depends_on is not None:
                if spec.depends_on not in names:
                    raise FieldError(
                        f"field {spec.name!r}: depends_on must name an earlier enum field"
                    )
                parent = next(f for f in result if f["name"] == spec.depends_on)
                if parent["kind"] != "enum":
                    raise FieldError(f"field {spec.name!r}: depends_on must name an enum field")
                if not isinstance(spec.choices, dict):
                    raise FieldError(
                        f"field {spec.name!r}: choices must map parent values to lists"
                    )
                spec.choices = {
                    str(k): _strings(v, f"field {spec.name!r} choices[{k!r}]")
                    for k, v in spec.choices.items()
                }
            else:
                spec.choices = _strings(spec.choices, f"field {spec.name!r} choices")
            if not spec.choices and not spec.allow_custom:
                raise FieldError(f"field {spec.name!r}: enum needs choices or allow_custom")
        else:
            if spec.choices is not None or spec.depends_on is not None:
                raise FieldError(
                    f"field {spec.name!r}: choices/depends_on are only valid for enum fields"
                )
        if spec.kind == "number":
            for bound in ("min", "max", "step"):
                value = getattr(spec, bound)
                if value is not None and not _number(value):
                    raise FieldError(f"field {spec.name!r}: {bound} must be a finite number")
            if spec.min is not None and spec.max is not None and spec.min > spec.max:
                raise FieldError(f"field {spec.name!r}: min must be <= max")
            if spec.step is not None and spec.step <= 0:
                raise FieldError(f"field {spec.name!r}: step must be positive")
            if spec.multipliers is not None and (
                not isinstance(spec.multipliers, list)
                or any(not _number(m) or m <= 0 for m in spec.multipliers)
            ):
                raise FieldError(f"field {spec.name!r}: multipliers must be positive numbers")
        elif spec.multipliers is not None or spec.min is not None or spec.max is not None:
            raise FieldError(f"field {spec.name!r}: min/max/multipliers need kind 'number'")
        if spec.suggestions is not None:
            if spec.kind != "text":
                raise FieldError(f"field {spec.name!r}: suggestions need kind 'text'")
            spec.suggestions = _strings(spec.suggestions, f"field {spec.name!r} suggestions")
        if spec.label is not None and not isinstance(spec.label, str):
            raise FieldError(f"field {spec.name!r}: label must be a string")
        names.append(spec.name)
        result.append(spec.to_dict())
    return result


def _get(value: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def proposed_values(fields: list[dict[str, Any]] | None, output: Any) -> dict[str, Any] | None:
    """Read each field's proposed value from ``output`` (missing -> ``None``)."""
    if not fields:
        return None
    values = {}
    for spec in fields:
        found = _get(output, spec.get("path") or spec["name"])
        values[spec["name"]] = None if found is _MISSING else found
    return values


def allowed_choices(spec: dict[str, Any], values: dict[str, Any]) -> list[str]:
    choices = spec.get("choices") or []
    if isinstance(choices, dict):
        parent = values.get(spec["depends_on"])
        if parent is None:
            return sorted({c for group in choices.values() for c in group})
        return list(choices.get(str(parent), []))
    return list(choices)


def validate_field_value(spec: dict[str, Any], value: Any, values: dict[str, Any]) -> str | None:
    """Return an error message, or ``None`` when ``value`` is acceptable."""
    if value is None:
        return None if spec.get("nullable", True) else "a value is required"
    kind = spec["kind"]
    if kind == "enum":
        if not isinstance(value, str):
            return "must be a string"
        if not spec.get("allow_custom") and value not in allowed_choices(spec, values):
            if spec.get("depends_on"):
                return f"{value!r} is not a valid choice for {spec['depends_on']}"
            return f"{value!r} is not one of the allowed choices"
    elif kind == "number":
        if not _number(value):
            return "must be a finite number"
        if spec.get("integer") and float(value) != int(value):
            return "must be an integer"
        if spec.get("min") is not None and value < spec["min"]:
            return f"must be >= {spec['min']}"
        if spec.get("max") is not None and value > spec["max"]:
            return f"must be <= {spec['max']}"
    elif not isinstance(value, str):
        return "must be a string"
    return None


def _same(a: Any, b: Any) -> bool:
    if _number(a) and _number(b):
        return a == b
    return type(a) is type(b) and a == b


def field_diff(proposed: dict[str, Any], corrected: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``{name: {"from": proposed, "to": corrected}}`` for every changed field."""
    return {
        name: {"from": proposed.get(name), "to": value}
        for name, value in corrected.items()
        if not _same(value, proposed.get(name))
    }


def check_values(
    fields: list[dict[str, Any]], output: Any, values: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Validate submitted values; return (full corrected dict, per-field diff).

    Omitted fields keep their proposed value. Only changed fields are validated,
    so a reviewer can confirm a proposed value that falls outside the schema.
    Raises :class:`FieldError` with every problem found.
    """
    if not isinstance(values, dict):
        raise FieldError("fields must be an object of field name -> value")
    names = [spec["name"] for spec in fields]
    unknown = set(values) - set(names)
    if unknown:
        raise FieldError(f"unknown field(s): {sorted(unknown)}")
    proposed = proposed_values(fields, output) or {}
    corrected = {name: values.get(name, proposed.get(name)) for name in names}
    diff = field_diff(proposed, corrected)
    errors = []
    for spec in fields:
        name = spec["name"]
        # A changed parent re-validates its children against the new branch.
        parent_changed = spec.get("depends_on") in diff
        if name in diff or (parent_changed and corrected[name] is not None):
            error = validate_field_value(spec, corrected[name], corrected)
            if error:
                errors.append(f"{name}: {error}")
    if errors:
        raise FieldError("; ".join(errors))
    return corrected, diff


def apply_values(fields: list[dict[str, Any]], output: Any, values: dict[str, Any]) -> Any:
    """Return a copy of ``output`` with corrected values written at each field path."""
    if not isinstance(output, dict):
        return output
    import copy

    result = copy.deepcopy(output)
    for spec in fields:
        name = spec["name"]
        if name not in values:
            continue
        parts = (spec.get("path") or name).split(".")
        target = result
        for part in parts[:-1]:
            child = target.get(part)
            if not isinstance(child, dict):
                child = {}
                target[part] = child
            target = child
        target[parts[-1]] = values[name]
    return result
