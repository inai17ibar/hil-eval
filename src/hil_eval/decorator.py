from __future__ import annotations

import asyncio
import inspect
import random
from collections.abc import Callable
from functools import wraps
from typing import Any

from .config import DEFAULT_THRESHOLD
from .context import get_client, logger
from .types import Judgment, RoutedBy, validate_unit


def review(
    judge,
    threshold=0.8,
    audit_rate=None,
    inputs=None,
    images=None,
    tags=None,
    project=None,
    *,
    options: list[str]
    | Callable[[tuple[Any, ...], dict[str, Any], Any], list[str] | None]
    | None = None,
):
    """Decorate a function so its outputs are judged and low-confidence ones queued.

    judge: ``(arguments: dict, output) -> Judgment | float``; receives all bound
        arguments (``self``/``cls`` excluded).
    options: choice labels, or ``(args, kwargs, result) -> list[str] | None``.
        Resolved only for queued items; callback failures are logged (fail-open).
    inputs: optional whitelist of argument names to *store* (default: all).
    images: argument names whose values are media assets (path str/Path, bytes, PIL).
        Bytes with an image/video/audio signature, PIL images and existing media ``Path`` objects
        are detected automatically; plain strings are only treated as assets here.
    """
    if threshold != "auto":
        validate_unit("threshold", threshold)
    validate_unit("audit_rate", audit_rate, allow_none=True)
    if inspect.iscoroutinefunction(judge) or inspect.isasyncgenfunction(judge):
        raise TypeError("Milestone 1 requires a synchronous judge")

    def decorate(fn):
        if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
            raise TypeError("Generators are not supported in Milestone 1")
        signature = inspect.signature(fn)
        unknown = (set(inputs or ()) | set(images or ())) - set(signature.parameters)
        if unknown:
            raise ValueError(f"Unknown argument name(s) for {fn.__qualname__}: {sorted(unknown)}")
        image_names = tuple(images or ())
        rng = random.Random()
        warned_auto = False

        def record(args, kwargs, output):
            nonlocal warned_auto
            client = get_client()
            if client is None:
                return
            try:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                arguments = {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}
                captured = (
                    {k: v for k, v in arguments.items() if k in inputs or k in image_names}
                    if inputs is not None
                    else arguments
                )
                config = client.config(project)
                rate = config.audit_rate if audit_rate is None else audit_rate
                audited = rng.random() < rate
                error = None
                try:
                    judgment = judge(arguments, output)
                    if not isinstance(judgment, Judgment):
                        judgment = Judgment(judgment)
                except Exception as exc:
                    logger.exception("Review judge failed")
                    judgment = None
                    error = f"judge_error: {exc}"
                    if not audited:
                        return
                resolved = threshold
                if threshold == "auto":
                    matches = [
                        value
                        for key, tag_value in (tags or {}).items()
                        if (value := client.store.get_tag_threshold(config.name, key, tag_value))
                        is not None
                    ]
                    resolved = max(matches) if matches else config.threshold
                    if resolved is None:
                        resolved = DEFAULT_THRESHOLD
                        if not warned_auto:
                            logger.info("No stored threshold; using DEFAULT_THRESHOLD=0.8")
                            warned_auto = True
                routed = (
                    RoutedBy.audit
                    if audited
                    else (
                        RoutedBy.low_conf
                        if judgment is not None and judgment.confidence < resolved
                        else None
                    )
                )
                if routed is not None:
                    client.record(
                        captured,
                        output,
                        judgment=judgment,
                        routed_by=routed,
                        project=project,
                        fn_name=fn.__qualname__,
                        tags=tags,
                        judge_error=error,
                        images=image_names,
                        options=options(args, kwargs, output) if callable(options) else options,
                    )
            except Exception:
                logger.exception("Failed to record review item")

        if inspect.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args, **kwargs):
                output = await fn(*args, **kwargs)
                try:
                    await asyncio.to_thread(record, args, kwargs, output)
                except Exception:
                    logger.exception("Failed to record review item")
                return output

            return async_wrapper

        @wraps(fn)
        def wrapper(*args, **kwargs):
            output = fn(*args, **kwargs)
            record(args, kwargs, output)
            return output

        return wrapper

    return decorate
