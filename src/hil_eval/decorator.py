from __future__ import annotations

import asyncio
import inspect
import random
from functools import wraps

from .config import DEFAULT_THRESHOLD
from .context import get_client, logger
from .types import Judgment, RoutedBy


def review(judge, threshold=0.8, audit_rate=None, inputs=None, tags=None, project=None):
    if inspect.iscoroutinefunction(judge) or inspect.isasyncgenfunction(judge):
        raise TypeError("Milestone 1 requires a synchronous judge")

    def decorate(fn):
        if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
            raise TypeError("Generators are not supported in Milestone 1")
        signature = inspect.signature(fn)
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
                captured = (
                    {k: v for k, v in bound.arguments.items() if k in inputs}
                    if inputs is not None
                    else {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}
                )
                config = client.config(project)
                rate = config.audit_rate if audit_rate is None else audit_rate
                audited = rng.random() < rate
                error = None
                try:
                    judgment = judge(captured, output)
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
                    resolved = config.threshold
                    if resolved is None:
                        resolved = DEFAULT_THRESHOLD
                        if not warned_auto:
                            logger.info("No stored threshold; using DEFAULT_THRESHOLD=0.8")
                            warned_auto = True
                routed = (
                    RoutedBy.audit
                    if audited
                    else (RoutedBy.low_conf if judgment.confidence < resolved else None)
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
                    )
            except Exception:
                logger.exception("Failed to record review item")

        if inspect.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args, **kwargs):
                output = await fn(*args, **kwargs)
                await asyncio.to_thread(record, args, kwargs, output)
                return output

            return async_wrapper

        @wraps(fn)
        def wrapper(*args, **kwargs):
            output = fn(*args, **kwargs)
            record(args, kwargs, output)
            return output

        return wrapper

    return decorate
