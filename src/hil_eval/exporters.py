"""Portable exports of the latest human review of each item."""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .store.base import Store
from .types import AssetKind, AssetRole, ReviewAction, Status


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
    return isinstance(value, dict) and ("$asset" in value or "$asset_path" in value)


HF_INSTALL_HINT = (
    "Parquet export (format 'hf') requires pyarrow, which ships with the [hf] extra: "
    "pip install 'hil-eval[hf]'  (uv: uv add 'hil-eval[hf]')"
)
FORMATS = ("jsonl", "openai-ft", "eval-set", "hf")


def require_pyarrow() -> None:
    """Fail fast with an install hint when the optional [hf] extra is missing."""
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
    except ImportError as exc:
        raise ImportError(HF_INSTALL_HINT) from exc


def _resolve_assets(value: Any, refs: dict[str, Any], store: Store) -> Any:
    """Expand ``{"$asset": sha}`` markers into self-describing references.

    Path-reference markers (``{"$asset_path": "/abs/file.wav"}``) stay path
    references: ``kind="path"`` with the recorded absolute ``path`` and no bytes.
    """
    if isinstance(value, dict):
        source = value.get("$asset_path")
        if isinstance(source, str):
            ref = refs.get(("path", source))
            return {
                **value,
                "kind": "path",
                "sha256": None,
                "mime": ref.mime if ref is not None else None,
                "filename": value.get("filename"),
                "path": source,
                "url": None,
            }
        sha = value.get("$asset")
        if isinstance(sha, str):
            ref = refs.get(sha)
            path = store.get_blob_path(sha)
            return {
                **value,
                "kind": "blob",
                "sha256": sha,
                "mime": ref.mime if ref is not None else None,
                "filename": value.get("filename"),
                "path": str(path) if path is not None else None,
                "url": f"/v1/blobs/{sha}",
            }
        return {k: _resolve_assets(v, refs, store) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_assets(v, refs, store) for v in value]
    return value


def export_items(
    store: Store,
    project: str,
    *,
    fmt: str = "jsonl",
    statuses: Iterable[Status | str] = (Status.approved, Status.corrected),
    include_images: bool = True,
) -> Iterator[dict[str, Any]]:
    """Export selected statuses; missing image blobs raise rather than lose data.

    jsonl and eval-set expand ``{"$asset": sha}`` markers with sha256, mime,
    filename, local blob path and the server's blob URL; path-reference assets
    are exported as ``kind="path"`` with their recorded path.
    Every format's metadata carries ``external_id``, the reviewed structured
    ``fields`` (full corrected dict) and ``field_diff`` (``{name: {from, to}}``).
    """
    if fmt == "hf":
        raise ValueError("format 'hf' writes Parquet; use hil.export_to(path, format='hf')")
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
            "external_id": item.external_id,
            "fields": review.fields,
            "field_diff": review.field_diff,
        }
        if fmt in ("jsonl", "eval-set"):
            refs = {
                (("path", a.path) if a.kind == AssetKind.path else a.sha256): a for a in item.assets
            }

            def resolve(value, refs=refs):
                return _resolve_assets(value, refs, store)

        if fmt == "jsonl":
            yield {
                **metadata,
                "fn_name": item.fn_name,
                "input": resolve(item.input),
                "output": resolve(item.output),
                "corrected_output": resolve(expected),
                "judge_reason": item.judge_reason,
                "created_at": item.created_at.isoformat(),
            }
        elif fmt == "eval-set":
            yield {
                "input": resolve(item.input),
                "expected": resolve(expected),
                "metadata": metadata,
            }
        else:
            content: list[dict[str, Any]] = [
                {"type": "text", "text": _text(_without_assets(item.input))}
            ]
            if include_images:
                for asset in item.assets:
                    if (
                        asset.kind == AssetKind.path
                        or asset.role != AssetRole.input
                        or not asset.mime.startswith("image/")
                    ):
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


def write_jsonl(records: Iterable[dict[str, Any]], path: str | Path = "-") -> int:
    """Write UTF-8 JSONL, leaving stdout open when path is '-'; return the record count."""
    count = 0
    if str(path) == "-":
        for record in records:
            print(json.dumps(record, ensure_ascii=False), file=sys.stdout)
            count += 1
    else:
        with Path(path).open("w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
    return count


def export_hf(
    store: Store,
    project: str,
    path: str | Path,
    *,
    statuses=(Status.approved, Status.corrected),
    include_images=True,
) -> int:
    """Write reviewed JSONL-shaped records and image/audio bytes as Parquet."""
    require_pyarrow()
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = list(export_items(store, project, statuses=statuses, include_images=include_images))
    images = []
    audio = []
    for row in rows:
        item = store.get_item(row["id"])
        blobs = []
        audio_blobs = []
        if include_images and item is not None:
            for asset in sorted(item.assets, key=lambda a: a.order):
                # Path references are exported as paths (inside input/output), never read.
                if asset.kind == AssetKind.path or not asset.mime.startswith(("image/", "audio/")):
                    continue
                blob = store.get_blob_path(asset.sha256)
                if blob is None:
                    raise FileNotFoundError(f"Missing media blob: {asset.sha256}")
                (audio_blobs if asset.mime.startswith("audio/") else blobs).append(
                    blob.read_bytes()
                )
        images.append(blobs)
        audio.append(audio_blobs)

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
            "external_id",
            "fields",
            "field_diff",
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
    table = table.append_column("audio", pa.array(audio, type=pa.list_(pa.binary())))
    pq.write_table(table, path)
    return len(rows)


def export_to(
    store: Store,
    project: str,
    path: str | Path,
    *,
    format: str = "jsonl",
    statuses: Iterable[Status | str] = (Status.approved, Status.corrected),
    include_images: bool = True,
) -> int:
    """Write an export file in any CLI format; return the number of records."""
    if format not in FORMATS:
        raise ValueError(f"unknown export format: {format!r}; choose one of {', '.join(FORMATS)}")
    if format == "hf":
        if str(path) == "-":
            raise ValueError("format 'hf' requires a real file path")
        return export_hf(store, project, path, statuses=statuses, include_images=include_images)
    return write_jsonl(
        export_items(store, project, fmt=format, statuses=statuses, include_images=include_images),
        path,
    )
