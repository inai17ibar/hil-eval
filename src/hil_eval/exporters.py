"""Portable exports of the latest human review of each item."""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .store.base import Store
from .types import AssetRole, ReviewAction, Status


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _without_assets(value: Any) -> Any:
    if _asset_ref(value):
        return None
    if isinstance(value, dict):
        return {k: _without_assets(v) for k, v in value.items() if not _asset_ref(v)}
    if isinstance(value, list):
        return [_without_assets(v) for v in value if not _asset_ref(v)]
    return value


def _asset_ref(value: Any) -> bool:
    return isinstance(value, dict) and "$asset" in value


def export_items(
    store: Store,
    project: str,
    *,
    fmt: str = "jsonl",
    statuses: Iterable[Status | str] = (Status.approved, Status.corrected),
    include_images: bool = True,
) -> Iterator[dict[str, Any]]:
    """Export selected statuses; missing image blobs raise rather than lose data."""
    if fmt not in {"jsonl", "openai-ft", "eval-set"}:
        raise ValueError(f"unknown export format: {fmt!r}")
    selected = {Status(s) for s in statuses}
    for item, review in store.list_reviewed(project):
        if item.status not in selected or (fmt == "openai-ft" and item.status == Status.rejected):
            continue
        expected = review.corrected_output if item.status == Status.corrected else item.output
        if review.action == ReviewAction.choose:
            expected = review.choice
        metadata = {
            "id": item.id,
            "project": item.project,
            "confidence": item.confidence,
            "judge_label": item.judge_label,
            "judge_correct": review.judge_correct,
            "tags": item.tags,
            "status": item.status.value,
            "reason_codes": review.reason_codes,
            "reason_text": review.reason_text,
            "choice": review.choice,
        }
        if fmt == "jsonl":
            yield {
                **metadata,
                "fn_name": item.fn_name,
                "input": item.input,
                "output": item.output,
                "corrected_output": expected,
                "judge_reason": item.judge_reason,
                "created_at": item.created_at.isoformat(),
            }
        elif fmt == "eval-set":
            yield {"input": item.input, "expected": expected, "metadata": metadata}
        else:
            content: list[dict[str, Any]] = [
                {"type": "text", "text": _text(_without_assets(item.input))}
            ]
            if include_images:
                for asset in item.assets:
                    if asset.role != AssetRole.input or not asset.mime.startswith("image/"):
                        continue
                    path = store.get_blob_path(asset.sha256)
                    if path is None:
                        raise FileNotFoundError(f"Missing image blob: {asset.sha256}")
                    data = base64.b64encode(path.read_bytes()).decode("ascii")
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{asset.mime};base64,{data}"},
                        }
                    )
            yield {
                "metadata": metadata,
                "messages": [
                    {"role": "user", "content": content},
                    {"role": "assistant", "content": _text(expected)},
                ],
            }


def write_jsonl(records: Iterable[dict[str, Any]], path: str | Path = "-") -> None:
    """Write UTF-8 JSONL, leaving stdout open when path is '-'."""
    if str(path) == "-":
        for record in records:
            print(json.dumps(record, ensure_ascii=False), file=sys.stdout)
    else:
        with Path(path).open("w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def export_hf(
    store: Store,
    project: str,
    path: str | Path,
    *,
    statuses=(Status.approved, Status.corrected),
    include_images=True,
) -> None:
    """Write reviewed JSONL-shaped records and image bytes as Parquet."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ImportError("Parquet export requires pyarrow; pip install hil-eval[hf]") from exc
    rows = list(export_items(store, project, statuses=statuses, include_images=include_images))
    images = []
    for row in rows:
        item = store.get_item(row["id"])
        blobs = []
        if include_images and item is not None:
            for asset in sorted(item.assets, key=lambda a: a.order):
                if not asset.mime.startswith("image/"):
                    continue
                blob = store.get_blob_path(asset.sha256)
                if blob is None:
                    raise FileNotFoundError(f"Missing image blob: {asset.sha256}")
                blobs.append(blob.read_bytes())
        images.append(blobs)

    # Native Arrow columns preserve ordinary JSON values. Empty objects and
    # mixed JSON types cannot be represented by Parquet; only those columns
    # fall back to JSON text, recorded in schema metadata for lossless decoding.
    def has_empty_struct(dtype):
        if pa.types.is_struct(dtype):
            return not len(dtype) or any(has_empty_struct(f.type) for f in dtype)
        if pa.types.is_list(dtype):
            return has_empty_struct(dtype.value_type)
        return False

    columns = {}
    json_columns = []
    names = (
        list(rows[0])
        if rows
        else [
            "id",
            "project",
            "confidence",
            "judge_label",
            "judge_correct",
            "tags",
            "status",
            "reason_codes",
            "reason_text",
            "choice",
            "fn_name",
            "input",
            "output",
            "corrected_output",
            "judge_reason",
            "created_at",
        ]
    )
    for name in names:
        values = [r[name] for r in rows]
        try:
            column = pa.array(values)
            if column.to_pylist() != values:
                raise ValueError("Arrow conversion changes JSON values")
            if has_empty_struct(column.type):
                raise ValueError("Parquet does not support empty structs")
        except (pa.ArrowInvalid, pa.ArrowTypeError, ValueError, OverflowError):
            column = pa.array([json.dumps(v, ensure_ascii=False) for v in values], type=pa.string())
            json_columns.append(name)
        if name == "reason_codes":
            column = pa.array(values, type=pa.list_(pa.string()))
        columns[name] = column
    table = pa.table(columns)
    if json_columns:
        table = table.replace_schema_metadata(
            {b"hil_eval.json_columns": json.dumps(json_columns).encode()}
        )
    table = table.append_column("images", pa.array(images, type=pa.list_(pa.binary())))
    pq.write_table(table, path)
