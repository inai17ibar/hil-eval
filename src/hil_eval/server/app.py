from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import math
import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from ..blobs import detect_mime, sniff_mime
from ..store import ItemNotFoundError, ItemSupersededError
from ..types import (
    AssetRef,
    AssetRole,
    Item,
    ProjectConfig,
    ReasonCode,
    Review,
    ReviewAction,
    RoutedBy,
    Status,
    TagThreshold,
    validate_unit,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _serialize_asset(asset: AssetRef) -> dict:
    return {
        "sha256": asset.sha256,
        "role": asset.role.value,
        "mime": asset.mime,
        "order": asset.order,
        "video_ts": asset.video_ts,
        "bbox": asset.bbox,
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
        "options": item.options,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
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
        "choice": review.choice,
        "reason_codes": review.reason_codes,
        "reason_text": review.reason_text,
        "created_at": review.created_at.isoformat(),
    }


def _serialize_project(project: ProjectConfig) -> dict:
    return {
        "name": project.name,
        "threshold": project.threshold,
        "target_miss_rate": project.target_miss_rate,
        "audit_rate": project.audit_rate,
    }


class AssetResponse(BaseModel):
    sha256: str
    role: AssetRole
    mime: str
    order: int
    video_ts: float | None
    bbox: tuple[float, float, float, float] | None
    width: int | None
    height: int | None
    url: str


class ReviewResponse(BaseModel):
    id: str
    item_id: str
    reviewer: str | None
    action: ReviewAction
    corrected_output: Any
    judge_correct: bool | None
    note: str | None
    choice: Any
    reason_codes: list[str]
    reason_text: str | None
    created_at: str


class ItemResponse(BaseModel):
    id: str
    project: str
    fn_name: str | None
    input: Any
    output: Any
    confidence: float | None
    judge_label: str | None
    judge_reason: str | None
    judge_metadata: dict[str, Any]
    routed_by: RoutedBy
    status: Status
    tags: dict[str, str]
    trace_id: str | None
    created_at: str
    assets: list[AssetResponse]
    options: list[Any] | None
    expires_at: str | None


class ItemDetailResponse(ItemResponse):
    reviews: list[ReviewResponse]


class QueueResponse(BaseModel):
    items: list[ItemResponse]
    total: int


class ProjectResponse(BaseModel):
    name: str
    threshold: float | None
    target_miss_rate: float
    audit_rate: float


class ReasonCodeResponse(BaseModel):
    project: str
    code: str
    label: str | None
    polarity: str | None


class ThresholdResponse(BaseModel):
    threshold: float | None


class SupersedeResponse(BaseModel):
    superseded: int


class HealthResponse(BaseModel):
    ok: bool


class AssetUpload(BaseModel):
    data_base64: str
    mime: str | None = None
    role: AssetRole = AssetRole.input
    filename: str | None = None
    video_ts: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    bbox: list[float] | None = None

    @field_validator("data_base64")
    @classmethod
    def _base64(cls, value):
        try:
            base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid base64 asset") from exc
        return value

    @field_validator("bbox")
    @classmethod
    def _bbox(cls, value):
        if value is not None and (
            len(value) != 4 or any(not math.isfinite(v) or v < 0 for v in value)
        ):
            raise ValueError("bbox must contain finite nonnegative [x,y,w,h]")
        return value


class SupersedeRequest(BaseModel):
    project: str
    trace_id: str | None = None
    tag_key: str | None = None
    tag_value: str | None = None

    @model_validator(mode="after")
    def _selector(self):
        if (self.tag_key is None) != (self.tag_value is None):
            raise ValueError("tag_key and tag_value must be supplied together")
        if self.trace_id is None and self.tag_key is None:
            raise ValueError("trace_id or tag is required")
        return self


class ReasonCodeRequest(BaseModel):
    code: str
    label: str | None = None
    polarity: str | None = None


class TagThresholdRequest(BaseModel):
    tag_key: str
    tag_value: str
    threshold: float

    @field_validator("threshold", mode="before")
    @classmethod
    def _threshold(cls, value):
        return validate_unit("threshold", value)


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
    options: list[Any] | None = None
    expires_at: datetime | None = None
    assets: list[AssetUpload] | None = None

    @field_validator("expires_at")
    @classmethod
    def _expiry(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must have a timezone")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _check_confidence(cls, value):
        return validate_unit("confidence", value, allow_none=True)


class ReviewRequest(BaseModel):
    action: ReviewAction
    choice: Any = None
    reason_codes: list[str] = Field(default_factory=list)
    reason_text: str | None = None
    corrected_output: Any = None
    judge_correct: bool | None = None
    reviewer: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check_corrected_output(self):
        if self.action == ReviewAction.edit and self.corrected_output is None:
            raise ValueError("corrected_output is required when action is 'edit'")
        if self.action == ReviewAction.choose and "choice" not in self.model_fields_set:
            raise ValueError("choice is required when action is choose")
        return self


def create_app(
    store,
    api_key: str | None = None,
    static_dir: str | Path | None = None,
    *,
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
) -> FastAPI:
    from ..webhooks import WebhookDispatcher

    dispatcher = WebhookDispatcher(webhook_url, webhook_secret)

    @asynccontextmanager
    async def lifespan(app):
        dispatcher.start()
        try:
            yield
        finally:
            dispatcher.stop()

    app = FastAPI(title="hil-eval", lifespan=lifespan)
    app.state.webhook_dispatcher = dispatcher

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

    def emit(event, project, payload):
        try:
            dispatcher.enqueue(event, store.get_project(project), payload)
        except Exception:
            logging.getLogger("hil_eval").exception("Webhook enqueue failed")

    @app.get("/healthz", response_model=HealthResponse)
    def healthz():
        return {"ok": True}

    @app.get(
        "/v1/projects",
        response_model=list[ProjectResponse],
        dependencies=[Depends(require_api_key)],
    )
    def list_projects():
        return [_serialize_project(p) for p in store.list_projects()]

    @app.get("/v1/items", response_model=QueueResponse, dependencies=[Depends(require_api_key)])
    def list_items(
        project: str | None = None,
        status: Annotated[list[Status] | None, Query()] = None,
        min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
        max_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
        limit: Annotated[int, Query(ge=1)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        include_expired: bool = False,
    ):
        now = datetime.now(timezone.utc)
        items = store.list_items(
            project,
            status=status,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            limit=limit,
            offset=offset,
            order="desc",
            exclude_expired=not include_expired,
            now=now,
        )
        total = store.count_items(
            project,
            status=status,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            exclude_expired=not include_expired,
            now=now,
        )
        return {"items": [_serialize_item(i) for i in items], "total": total}

    @app.post(
        "/v1/items/supersede",
        response_model=SupersedeResponse,
        dependencies=[Depends(require_api_key)],
    )
    def supersede(body: SupersedeRequest):
        tag = (body.tag_key, body.tag_value) if body.tag_key is not None else None
        return {"superseded": store.supersede_items(body.project, trace_id=body.trace_id, tag=tag)}

    @app.get(
        "/v1/projects/{project}/reason-codes",
        response_model=list[ReasonCodeResponse],
        dependencies=[Depends(require_api_key)],
    )
    def reason_codes(project: str):
        return [asdict(r) for r in store.list_reason_codes(project)]

    @app.post(
        "/v1/projects/{project}/reason-codes",
        response_model=ReasonCodeResponse,
        dependencies=[Depends(require_api_key)],
    )
    def register_reason(project: str, body: ReasonCodeRequest):
        entry = ReasonCode(project=project, **body.model_dump())
        store.register_reason_code(entry)
        return asdict(entry)

    @app.get(
        "/v1/projects/{project}/threshold",
        response_model=ThresholdResponse,
        dependencies=[Depends(require_api_key)],
    )
    def get_threshold(project: str, tag: str | None = None):
        config = store.get_project(project)
        if config is None:
            raise HTTPException(404, "project not found")
        threshold = None
        if tag is not None:
            if ":" not in tag:
                raise HTTPException(422, "tag must be key:value")
            threshold = store.get_tag_threshold(project, *tag.split(":", 1))
        return {"threshold": config.threshold if threshold is None else threshold}

    @app.put(
        "/v1/projects/{project}/threshold",
        response_model=ThresholdResponse,
        dependencies=[Depends(require_api_key)],
    )
    def set_threshold(project: str, body: TagThresholdRequest):
        store.set_tag_threshold(TagThreshold(project=project, **body.model_dump()))
        return {"threshold": body.threshold}

    @app.get(
        "/v1/items/{item_id}",
        response_model=ItemDetailResponse,
        dependencies=[Depends(require_api_key)],
    )
    def get_item(item_id: str):
        item = store.get_item(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        return _serialize_item(item, reviews=store.list_reviews(item_id))

    @app.post(
        "/v1/items",
        response_model=ItemResponse,
        status_code=201,
        dependencies=[Depends(require_api_key)],
    )
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
            options=body.options,
            expires_at=body.expires_at,
        )
        for order, upload in enumerate(body.assets or []):
            data = base64.b64decode(upload.data_base64, validate=True)
            digest = hashlib.sha256(data).hexdigest()
            mime = upload.mime or detect_mime(data, upload.filename)
            item.assets.append(
                AssetRef(
                    digest,
                    upload.role,
                    mime,
                    store.put_blob(digest, data, mime),
                    order=order,
                    video_ts=upload.video_ts,
                    bbox=upload.bbox,
                )
            )
        result = _serialize_item(store.save_item(item))
        emit("item.created", item.project, result)
        return result

    @app.delete("/v1/items/{item_id}", status_code=204, dependencies=[Depends(require_api_key)])
    def delete_item(item_id: str):
        if not store.delete_item(item_id):
            raise HTTPException(status_code=404, detail="item not found")
        return Response(status_code=204)

    @app.post(
        "/v1/items/{item_id}/review",
        response_model=ItemResponse,
        dependencies=[Depends(require_api_key)],
    )
    def review_item(item_id: str, body: ReviewRequest):
        item = store.get_item(item_id)
        if item is None:
            raise HTTPException(404, "item not found")
        if body.action == ReviewAction.choose and item.options and body.choice not in item.options:
            raise HTTPException(422, "choice must be one of item options")
        registered = {r.code for r in store.list_reason_codes(item.project)}
        if registered and set(body.reason_codes) - registered:
            raise HTTPException(422, "unknown reason code")
        review = Review(
            id=uuid4().hex,
            item_id=item_id,
            reviewer=body.reviewer,
            action=body.action,
            corrected_output=body.corrected_output,
            judge_correct=body.judge_correct,
            note=body.note,
            choice=body.choice,
            reason_codes=body.reason_codes,
            reason_text=body.reason_text,
            created_at=datetime.now(timezone.utc),
        )
        try:
            store.save_review(review)
        except ItemSupersededError as exc:
            raise HTTPException(409, "item is superseded") from exc
        except ItemNotFoundError as exc:
            raise HTTPException(status_code=404, detail="item not found") from exc
        emit("review.created", item.project, _serialize_review(review))
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

        @app.get("/{full_path:path}", include_in_schema=False)
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

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = (static_root / full_path).resolve()
        if full_path and candidate.is_relative_to(resolved_root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)
