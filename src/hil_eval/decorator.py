from __future__ import annotations

import asyncio
import inspect
import random
from collections.abc import Callable
from functools import wraps
from typing import Any

from .config import DEFAULT_THRESHOLD
from .context import get_client, logger
from .fields import normalize_fields
from .selectors import parse_selectors
from .types import Judgment, RoutedBy, validate_unit


def _render(path):
    return "".join(f"[{p}]" if isinstance(p, int) or p == "*" else f".{p}" for p in path)


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
    assets: list[str] | None = None,
    name: str | None = None,
    route: str = "confidence",
    path_assets: list[str] | str | None = None,
    fields: list | None = None,
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
        Kept for compatibility; ``assets`` is the generic spelling.
    assets: selectors for media values in arguments or the return value, e.g.
        ``["audio", "output.audio", "output[*].path", "output.results[*].path"]``.
        The first segment is an argument name or ``output``; ``[*]`` maps over lists
        (or dict values) and ``[N]`` indexes. Selected strings/Paths/bytes become
        assets ordered by position; missing keys are ignored.
    name: ``fn_name`` recorded for items (default ``fn.__qualname__``).
    route: ``"confidence"`` (default) queues low-confidence plus audited calls;
        ``"all"`` queues every call (recorded as ``routed_by=manual``, so threshold
        tuning ignores them instead of mis-weighting them as audits).
    path_assets: selectors like ``assets`` whose str/Path values are recorded as
        absolute path references only (never read, hashed or copied); the review
        server streams them from ``serve --asset-root`` directories.
    fields: structured label field schema (list of dicts or ``hil.Field``) for the
        correction form; values are prefilled from the output (see ``hil.fields``).
    """
    if threshold != "auto":
        validate_unit("threshold", threshold)
    validate_unit("audit_rate", audit_rate, allow_none=True)
    if inspect.iscoroutinefunction(judge) or inspect.isasyncgenfunction(judge):
        raise TypeError("Milestone 1 requires a synchronous judge")
    if route not in ("confidence", "all"):
        raise ValueError(f"route must be 'confidence' or 'all', got {route!r}")
    if name is not None and (not isinstance(name, str) or not name):
        raise ValueError("name must be a non-empty string")
    selectors = parse_selectors(images, assets)
    path_selectors = parse_selectors(path_assets)
    fields = normalize_fields(fields)

    def decorate(fn):
        if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
            raise TypeError("Generators are not supported in Milestone 1")
        signature = inspect.signature(fn)
        roots = {root for root, _ in selectors + path_selectors}
        if "output" in roots and "output" in signature.parameters:
            raise ValueError(
                f"{fn.__qualname__} has an argument named 'output'; "
                "asset selectors rooted at 'output' would be ambiguous"
            )
        unknown = (set(inputs or ()) | (roots - {"output"})) - set(signature.parameters)
        if unknown:
            raise ValueError(f"Unknown argument name(s) for {fn.__qualname__}: {sorted(unknown)}")
        image_names = tuple(roots - {"output"})

        # Argument roots are made explicit (input.<arg>) so they cannot collide
        # with the record-level "input"/"output" roots.
        def explicit(group):
            return tuple(
                f"{root if root == 'output' else 'input.' + root}{_render(path)}"
                for root, path in group
            )

        asset_selectors = explicit(selectors)
        path_asset_selectors = explicit(path_selectors)
        fn_name = name or fn.__qualname__
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
                    if not audited and route != "all":
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
                # route="all" uses manual so IPW tuning does not reweight
                # these (selection probability 1) samples as audits.
                routed = (
                    RoutedBy.manual
                    if route == "all"
                    else RoutedBy.audit
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
                        fn_name=fn_name,
                        tags=tags,
                        judge_error=error,
                        assets=asset_selectors,
                        path_assets=path_asset_selectors,
                        fields=fields,
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
