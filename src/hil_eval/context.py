from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .blobs import is_asset, path_asset, store_asset
from .fields import normalize_fields
from .selectors import apply_path, parse_selectors
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


class _Marked:
    """A value explicitly selected as an asset (plain strings included)."""

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class _PathMarked:
    """A value selected as a path-reference asset (recorded, never read)."""

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


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
        assets=(),
        path_assets=(),
        fields=None,
        external_id=None,
        import_key=None,
    ):
        if external_id is not None and (not isinstance(external_id, str) or not external_id):
            raise ValueError("external_id must be a non-empty string or None")
        fields = normalize_fields(fields)
        if options is not None and (
            not isinstance(options, list) or any(not isinstance(value, str) for value in options)
        ):
            raise ValueError("options must be a list of strings or None")
        project = project or self.project
        self.config(project)
        selectors = parse_selectors(images, assets)
        path_selectors = parse_selectors(path_assets)
        stored = []
        counters = {}

        def keep(value, role, by_path=False):
            try:
                order = counters.get(role, 0)
                if by_path:
                    asset = path_asset(value, role=role, order=order)
                else:
                    asset = store_asset(value, self.store, role=role, order=order)
            except Exception as exc:  # keep the item; record why the asset is missing
                logger.warning("Could not store media asset: %s", exc)
                return {"$asset_error": f"{type(exc).__name__}: {exc}"}
            counters[role] = order + 1
            stored.append(asset)
            if by_path:
                return {"$asset_path": asset.path, "filename": asset.filename}
            marker = {"$asset": asset.sha256}
            if isinstance(value, (str, Path)):
                marker["filename"] = Path(value).name
            return marker

        # Explicitly selected values are wrapped first, then stored in document
        # order together with implicitly detected media, so `order` follows
        # list position (e.g. search rank).
        def capture(value, role):
            if isinstance(value, _PathMarked):
                return keep(value.value, role, by_path=True)
            if isinstance(value, _Marked):
                return keep(value.value, role)
            if is_asset(value):
                return keep(value, role)
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

        def mark(value):  # idempotent, so overlapping selectors are harmless
            return value if isinstance(value, (_Marked, _PathMarked)) else _Marked(value)

        def mark_path(value):  # path references win over blob selection
            if isinstance(value, _PathMarked):
                return value
            return _PathMarked(value.value if isinstance(value, _Marked) else value)

        for group, marker in ((selectors, mark), (path_selectors, mark_path)):
            for root, path in group:
                if root == "output":
                    output = apply_path(output, path, marker)
                elif root == "input":
                    input = apply_path(input, path, marker)
                elif isinstance(input, dict) and root in input:  # top-level input key shorthand
                    input = {**input, root: apply_path(input[root], path, marker)}

        item = Item(
            id=uuid4().hex,
            project=project,
            fn_name=fn_name,
            input=prepare(input, AssetRole.input),
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
            assets=stored,
            options=list(options) if options is not None else None,
            fields=fields,
            external_id=external_id,
            import_key=import_key,
        )
        return self.store.save_item(item)


def init(
    project,
    store="sqlite:///.hil/hil.db",
    *,
    threshold=None,
    target_miss_rate=None,
    audit_rate=None,
    fields=None,
):
    """Configure the default client. ``fields`` sets the project's label field schema."""
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
    if fields is not None:
        backend.set_project_fields(project, normalize_fields(fields))
    _default_client = Client(project, backend)


def set_fields(fields, *, project=None):
    """Set (or clear with ``None``) a project's structured label field schema."""
    client = get_client()
    if client is None:
        raise RuntimeError("Call hil.init() before setting fields")
    name = project or client.project
    client.config(name)
    client.store.set_project_fields(name, normalize_fields(fields))


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
    assets=None,
    images=None,
    name=None,
    path_assets=None,
    fields=None,
    external_id=None,
):
    """Queue an item manually, optionally with choice labels for review.

    path_assets: selectors like ``assets`` whose str/Path values are recorded as
        absolute path references only (no read, hash or copy). The serving machine
        plays them when the path is under ``serve --asset-root``.
    fields: structured label field schema for this item (overrides the project's).
    external_id: your stable id for the item (e.g. a file id); exported as-is.

    assets/images: selectors rooted at ``input`` or ``output`` (or a top-level
    input key), e.g. ``["output.results[*].path"]``; selected strings/Paths/bytes
    are stored as media assets. ``images`` is an alias kept for compatibility.
    name: optional ``fn_name`` shown in the UI and exports.
    """
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
            assets=[assets] if isinstance(assets, str) else tuple(assets or ()),
            images=[images] if isinstance(images, str) else tuple(images or ()),
            path_assets=[path_assets] if isinstance(path_assets, str) else tuple(path_assets or ()),
            fields=fields,
            external_id=external_id,
            fn_name=name,
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
