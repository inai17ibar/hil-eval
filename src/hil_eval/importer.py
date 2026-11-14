"""Bulk-enqueue ``hil.submit``-compatible JSONL (``hil-eval import``)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import Client
from .fields import FieldError, normalize_fields
from .selectors import parse_selectors
from .types import Judgment, RoutedBy

#: Keys understood per JSONL record (the ``hil.submit`` keyword arguments).
RECORD_KEYS = frozenset(
    {
        "project",
        "name",
        "fn_name",
        "input",
        "output",
        "confidence",
        "judge_label",
        "judge_reason",
        "tags",
        "trace_id",
        "options",
        "assets",
        "images",
        "path_assets",
        "fields",
        "external_id",
    }
)


@dataclass
class ImportResult:
    imported: int = 0
    duplicates: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    ignored_keys: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.imported + self.duplicates + self.failed


def _selectors(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ValueError("selectors must be a string or a list of strings")


def _lookup(record: dict, dotted: str) -> Any:
    value: Any = record
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def import_jsonl(
    store,
    path: str | Path,
    *,
    project: str | None = None,
    id_field: str | None = None,
    path_assets: list[str] | None = None,
    dry_run: bool = False,
) -> ImportResult:
    """Enqueue every record of a JSONL file; return counts.

    Idempotent: a record whose ``external_id`` (or ``id_field`` value) already
    exists in its project, or whose content hash was imported before, is counted
    as a duplicate and skipped. ``project`` is the default for records without one.
    ``path_assets`` selectors are added to every record (path references, no reads).
    """
    result = ImportResult()
    seen: dict[str, tuple[set[str], set[str]]] = {}

    def known(name: str) -> tuple[set[str], set[str]]:
        if name not in seen:
            items = store.list_items(name, exclude_expired=False, limit=2**63 - 1)
            seen[name] = (
                {i.external_id for i in items if i.external_id},
                {i.import_key for i in items if i.import_key},
            )
        return seen[name]

    with Path(path).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("record must be a JSON object")
                for key in set(record) - RECORD_KEYS - ({id_field} if id_field else set()):
                    result.ignored_keys[key] = result.ignored_keys.get(key, 0) + 1
                name = record.get("project") or project
                if not isinstance(name, str) or not name:
                    raise ValueError("no project (set it in the record or pass --project)")
                if "input" not in record or "output" not in record:
                    raise ValueError("record needs 'input' and 'output'")
                external_id = record.get("external_id")
                if id_field is not None:
                    value = _lookup(record, id_field)
                    if value is None:
                        raise ValueError(f"missing id field {id_field!r}")
                    external_id = str(value)
                if external_id is not None and (
                    not isinstance(external_id, str) or not external_id
                ):
                    raise ValueError("external_id must be a non-empty string")
                selectors = _selectors(record.get("path_assets")) + list(path_assets or [])
                key = hashlib.sha256(
                    json.dumps(
                        {**record, "project": name, "path_assets": selectors},
                        sort_keys=True,
                        ensure_ascii=False,
                        default=str,
                    ).encode()
                ).hexdigest()
                ids, keys = known(name)
                if (external_id is not None and external_id in ids) or key in keys:
                    result.duplicates += 1
                    continue
                confidence = record.get("confidence")
                judgment = (
                    Judgment(confidence, record.get("judge_label"), record.get("judge_reason"))
                    if confidence is not None
                    else None
                )
                tags = record.get("tags")
                if tags is not None and not isinstance(tags, dict):
                    raise ValueError("tags must be an object")
                # Validate everything up front so --dry-run reports the same failures.
                normalize_fields(record.get("fields"))
                assets = _selectors(record.get("assets"))
                images = _selectors(record.get("images"))
                parse_selectors(assets, images, selectors)
                options = record.get("options")
                if options is not None and (
                    not isinstance(options, list) or any(not isinstance(o, str) for o in options)
                ):
                    raise ValueError("options must be a list of strings")
                if not dry_run:
                    Client(name, store).record(
                        record["input"],
                        record["output"],
                        judgment=judgment,
                        routed_by=RoutedBy.manual,
                        fn_name=record.get("name") or record.get("fn_name"),
                        tags={
                            k: v if isinstance(v, str) else json.dumps(v) for k, v in tags.items()
                        }
                        if tags
                        else None,
                        trace_id=record.get("trace_id"),
                        judge_error=record.get("judge_reason"),
                        judge_label=record.get("judge_label"),
                        options=options,
                        assets=assets,
                        images=images,
                        path_assets=selectors,
                        fields=record.get("fields"),
                        external_id=external_id,
                        import_key=key,
                    )
                if external_id is not None:
                    ids.add(external_id)
                keys.add(key)
                result.imported += 1
            except (ValueError, TypeError, FieldError) as exc:
                result.failed += 1
                result.errors.append(f"line {number}: {exc}")
    return result
