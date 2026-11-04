"""Portable exports of the latest human review of each item."""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .store.base import Store
from .types import AssetRole, Status


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
        metadata = {
            "id": item.id,
            "project": item.project,
            "confidence": item.confidence,
            "judge_label": item.judge_label,
            "judge_correct": review.judge_correct,
            "tags": item.tags,
            "status": item.status.value,
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
                "messages": [
                    {"role": "user", "content": content},
                    {"role": "assistant", "content": _text(expected)},
                ]
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
