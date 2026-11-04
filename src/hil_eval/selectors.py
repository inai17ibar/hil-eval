"""Asset selectors: mark values inside inputs or outputs as media assets.

A selector is a dotted path whose first segment names a root (an argument name,
``input`` or ``output``); later segments are dict keys, ``[N]`` indexes or
``[*]`` wildcards, e.g. ``"audio"``, ``"output.audio"``, ``"output[*].path"``,
``"output.results[*].path"``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

_TOKEN = re.compile(r"\[(\*|-?\d+)\]|\.?([^.\[\]]+)")

Selector = tuple[str, tuple[Any, ...]]


def parse_selector(text: str) -> Selector:
    """Parse ``root.key[*].leaf`` into ``(root, (key, '*', leaf))``."""
    if not isinstance(text, str) or not text or text.strip() != text:
        raise ValueError(f"Invalid asset selector: {text!r}")
    tokens: list[Any] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None or (position == 0 and match.group(1) is not None):
            raise ValueError(f"Invalid asset selector: {text!r}")
        if match.group(1) is not None:
            tokens.append("*" if match.group(1) == "*" else int(match.group(1)))
        else:
            if position and not text[position] == ".":
                raise ValueError(f"Invalid asset selector: {text!r}")
            tokens.append(match.group(2))
        position = match.end()
    if text.endswith("."):
        raise ValueError(f"Invalid asset selector: {text!r}")
    return tokens[0], tuple(tokens[1:])


def parse_selectors(*groups: Iterable[str] | str | None) -> list[Selector]:
    selectors: list[Selector] = []
    for group in groups:
        if group is None:
            continue
        if isinstance(group, str):
            group = [group]
        for text in group:
            selector = parse_selector(text)
            if selector not in selectors:
                selectors.append(selector)
    return selectors


def apply_path(value: Any, path: tuple[Any, ...], fn: Callable[[Any], Any]) -> Any:
    """Return a copy of ``value`` with ``fn`` applied at ``path``; missing keys are skipped."""
    if not path:
        return fn(value)
    head, rest = path[0], path[1:]
    if head == "*":
        if isinstance(value, (list, tuple)):
            return [apply_path(v, rest, fn) for v in value]
        if isinstance(value, dict):
            return {k: apply_path(v, rest, fn) for k, v in value.items()}
        return value
    if isinstance(head, int):
        if isinstance(value, (list, tuple)) and -len(value) <= head < len(value):
            copy = list(value)
            copy[head] = apply_path(copy[head], rest, fn)
            return copy
        return value
    if isinstance(value, dict) and head in value:
        return {**value, head: apply_path(value[head], rest, fn)}
    return value
