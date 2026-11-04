from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from .blobs import is_asset, store_image
from .store.sqlite import SQLiteStore
from .types import AssetRole, Item, Judgment, RoutedBy, Status, validate_unit

logger = logging.getLogger("hil_eval")
_default_client = None
_warned_missing = False


def get_client():
    global _warned_missing
    if _default_client is None and not _warned_missing:
        logger.warning("hil.init() has not been called; recording is disabled")
        _warned_missing = True
    return _default_client


def sanitize(value):
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        logger.warning("Could not serialize %s; storing repr", type(value).__qualname__)
        try:
            representation = repr(value)
        except Exception:
            representation = "<unrepresentable>"
        return {"__repr__": representation, "__type__": type(value).__qualname__}


class Client:
    def __init__(self, project, store):
        self.project = project
        self.store = store

    def config(self, project=None):
        name = project or self.project
        return self.store.get_project(name) or self.store.ensure_project(
            name, threshold=None, target_miss_rate=None, audit_rate=None
        )

    def record(
        self,
        input,
        output,
        *,
        judgment=None,
        routed_by=RoutedBy.manual,
        project=None,
        fn_name=None,
        tags=None,
        trace_id=None,
        judge_error=None,
        judge_label=None,
        images=(),
        options: list[str] | None = None,
    ):
        if options is not None and (
            not isinstance(options, list) or any(not isinstance(value, str) for value in options)
        ):
            raise ValueError("options must be a list of strings or None")
        project = project or self.project
        self.config(project)
        assets = []

        def store_asset(value, role):
            try:
                asset = store_image(value, self.store, role=role)
            except Exception as exc:  # keep the item; record why the asset is missing
                logger.warning("Could not store media asset: %s", exc)
                return {"$asset_error": f"{type(exc).__name__}: {exc}"}
            assets.append(asset)
            return {"$asset": asset.sha256}

        def capture(value, role):
            if is_asset(value):
                return store_asset(value, role)
            if isinstance(value, dict):
                return {k: capture(v, role) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [capture(v, role) for v in value]
            return value

        # Circular containers are handled by the JSON fallback, too.
        def prepare(value, role):
            try:
                return sanitize(capture(value, role))
            except RecursionError:
                return sanitize(value)

        item = Item(
            id=uuid4().hex,
            project=project,
            fn_name=fn_name,
            input=prepare(
                {
                    k: (store_asset(v, AssetRole.input) if k in images else v)
                    for k, v in input.items()
                }
                if images and isinstance(input, dict)
                else input,
                AssetRole.input,
            ),
            output=prepare(output, AssetRole.output),
            confidence=judgment.confidence if judgment else None,
            judge_label=judgment.label if judgment else judge_label,
            judge_reason=judgment.reason if judgment else judge_error,
            judge_metadata=sanitize(judgment.metadata) if judgment else {},
            routed_by=routed_by,
            status=Status.pending,
            tags=sanitize(tags or {}),
            trace_id=trace_id,
            created_at=datetime.now(timezone.utc),
            assets=assets,
            options=list(options) if options is not None else None,
        )
        return self.store.save_item(item)


def init(
    project,
    store="sqlite:///.hil/hil.db",
    *,
    threshold=None,
    target_miss_rate=None,
    audit_rate=None,
):
    global _default_client
    if not store.startswith("sqlite:///"):
        raise ValueError("Milestone 1 supports only sqlite:/// URLs")
    validate_unit("threshold", threshold, allow_none=True)
    validate_unit("target_miss_rate", target_miss_rate, allow_none=True)
    validate_unit("audit_rate", audit_rate, allow_none=True)
    backend = SQLiteStore(store[len("sqlite:///") :])
    backend.ensure_project(
        project, threshold=threshold, target_miss_rate=target_miss_rate, audit_rate=audit_rate
    )
    _default_client = Client(project, backend)


def submit(
    input,
    output,
    *,
    confidence=None,
    judge_label=None,
    judge_reason=None,
    tags=None,
    project=None,
    trace_id=None,
    options: list[str] | None = None,
):
    """Queue an item manually, optionally with choice labels for review."""
    client = get_client()
    if client is None:
        return None
    try:
        judgment = (
            Judgment(confidence, judge_label, judge_reason) if confidence is not None else None
        )
        return client.record(
            input,
            output,
            judgment=judgment,
            tags=tags,
            project=project,
            trace_id=trace_id,
            judge_error=judge_reason,
            judge_label=judge_label,
            options=options,
        )
    except Exception:
        logger.exception("Failed to submit review item")
        return None


def list_items(status=None, project=None):
    client = get_client()
    if client is None:
        return []
    return client.store.list_items(
        project or client.project, status=status, now=datetime.now(timezone.utc)
    )
