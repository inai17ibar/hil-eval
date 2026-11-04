from __future__ import annotations

import hmac
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator, model_validator

from ..blobs import sniff_mime
from ..store import ItemNotFoundError
from ..types import (
    AssetRef,
    Item,
    ProjectConfig,
    Review,
    ReviewAction,
    RoutedBy,
    Status,
    validate_unit,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _serialize_asset(asset: AssetRef) -> dict:
    return {
        "sha256": asset.sha256,
        "role": asset.role.value,
        "mime": asset.mime,
        "width": asset.width,
        "height": asset.height,
        "url": f"/v1/blobs/{asset.sha256}",
    }


def _serialize_item(item: Item, *, reviews: list[Review] | None = None) -> dict:
    data = {
        "id": item.id,
        "project": item.project,
        "fn_name": item.fn_name,
        "input": item.input,
        "output": item.output,
        "confidence": item.confidence,
        "judge_label": item.judge_label,
        "judge_reason": item.judge_reason,
        "judge_metadata": item.judge_metadata,
        "routed_by": item.routed_by.value,
        "status": item.status.value,
        "tags": item.tags,
        "trace_id": item.trace_id,
        "created_at": item.created_at.isoformat(),
        "assets": [_serialize_asset(a) for a in item.assets],
    }
    if reviews is not None:
        data["reviews"] = [_serialize_review(r) for r in reviews]
    return data


def _serialize_review(review: Review) -> dict:
    return {
        "id": review.id,
        "item_id": review.item_id,
        "reviewer": review.reviewer,
        "action": review.action.value,
        "corrected_output": review.corrected_output,
        "judge_correct": review.judge_correct,
        "note": review.note,
        "created_at": review.created_at.isoformat(),
    }


def _serialize_project(project: ProjectConfig) -> dict:
    return {
        "name": project.name,
        "threshold": project.threshold,
        "target_miss_rate": project.target_miss_rate,
        "audit_rate": project.audit_rate,
    }


class CreateItemRequest(BaseModel):
    project: str
    input: Any
    output: Any
    confidence: float | None = None
    judge_label: str | None = None
    judge_reason: str | None = None
    tags: dict[str, str] | None = None
    trace_id: str | None = None
    fn_name: str | None = None

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, value):
        return validate_unit("confidence", value, allow_none=True)


class ReviewRequest(BaseModel):
    action: ReviewAction
    corrected_output: Any = None
    judge_correct: bool | None = None
    reviewer: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check_corrected_output(self):
        if self.action == ReviewAction.edit and self.corrected_output is None:
            raise ValueError("corrected_output is required when action is 'edit'")
        return self


def create_app(store, api_key: str | None = None, static_dir: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="hil-eval")

    def _key_matches(provided: str | None) -> bool:
        return (
            provided is not None and api_key is not None and hmac.compare_digest(provided, api_key)
        )

    async def require_api_key(x_api_key: str | None = Header(default=None)):
        if api_key is not None and not _key_matches(x_api_key):
            raise HTTPException(status_code=401, detail="missing or invalid API key")

    async def require_api_key_header_or_query(
        x_api_key: str | None = Header(default=None),
        api_key_q: str | None = Query(default=None, alias="api_key"),
    ):
        if api_key is not None and not (_key_matches(x_api_key) or _key_matches(api_key_q)):
            raise HTTPException(status_code=401, detail="missing or invalid API key")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/v1/projects", dependencies=[Depends(require_api_key)])
    def list_projects():
        return [_serialize_project(p) for p in store.list_projects()]

    @app.get("/v1/items", dependencies=[Depends(require_api_key)])
    def list_items(
        project: str,
        status: Annotated[list[Status] | None, Query()] = None,
        min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
        max_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
        limit: Annotated[int, Query(ge=1)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        items = store.list_items(
            project,
            status=status,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            limit=limit,
            offset=offset,
            order="desc",
        )
        total = store.count_items(
            project, status=status, min_confidence=min_confidence, max_confidence=max_confidence
        )
        return {"items": [_serialize_item(i) for i in items], "total": total}

    @app.get("/v1/items/{item_id}", dependencies=[Depends(require_api_key)])
    def get_item(item_id: str):
        item = store.get_item(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        return _serialize_item(item, reviews=store.list_reviews(item_id))

    @app.post("/v1/items", status_code=201, dependencies=[Depends(require_api_key)])
    def create_item(body: CreateItemRequest):
        store.ensure_project(body.project, threshold=None, target_miss_rate=None, audit_rate=None)
        item = Item(
            id=uuid4().hex,
            project=body.project,
            fn_name=body.fn_name,
            input=body.input,
            output=body.output,
            confidence=body.confidence,
            judge_label=body.judge_label,
            judge_reason=body.judge_reason,
            judge_metadata={},
            routed_by=RoutedBy.manual,
            status=Status.pending,
            tags=body.tags or {},
            trace_id=body.trace_id,
            created_at=datetime.now(timezone.utc),
            assets=[],
        )
        return _serialize_item(store.save_item(item))

    @app.delete("/v1/items/{item_id}", status_code=204, dependencies=[Depends(require_api_key)])
    def delete_item(item_id: str):
        if not store.delete_item(item_id):
            raise HTTPException(status_code=404, detail="item not found")
        return Response(status_code=204)

    @app.post("/v1/items/{item_id}/review", dependencies=[Depends(require_api_key)])
    def review_item(item_id: str, body: ReviewRequest):
        review = Review(
            id=uuid4().hex,
            item_id=item_id,
            reviewer=body.reviewer,
            action=body.action,
            corrected_output=body.corrected_output,
            judge_correct=body.judge_correct,
            note=body.note,
            created_at=datetime.now(timezone.utc),
        )
        try:
            store.save_review(review)
        except ItemNotFoundError as exc:
            raise HTTPException(status_code=404, detail="item not found") from exc
        return _serialize_item(store.get_item(item_id))

    @app.get("/v1/blobs/{sha256}", dependencies=[Depends(require_api_key_header_or_query)])
    def get_blob(sha256: str):
        if not _SHA256_RE.fullmatch(sha256):
            raise HTTPException(status_code=404, detail="blob not found")
        path = store.get_blob_path(sha256)
        if path is None:
            raise HTTPException(status_code=404, detail="blob not found")
        with path.open("rb") as f:
            header = f.read(16)
        return FileResponse(path, media_type=sniff_mime(header))

    _mount_static(app, static_dir)
    return app


def _mount_static(app: FastAPI, static_dir: str | Path | None) -> None:
    default_root = Path(__file__).parent.parent / "static"
    static_root = Path(static_dir) if static_dir is not None else default_root
    index_file = static_root / "index.html"

    if not index_file.is_file():

        @app.get("/{full_path:path}")
        def ui_not_built(full_path: str):
            return HTMLResponse(
                "<html><body><h1>hil-eval UI not built</h1>"
                "<p>Run the UI build (vite build) or pass --static-dir.</p></body></html>"
            )

        return

    assets_dir = static_root / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    resolved_root = static_root.resolve()

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = (static_root / full_path).resolve()
        if full_path and candidate.is_relative_to(resolved_root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)
