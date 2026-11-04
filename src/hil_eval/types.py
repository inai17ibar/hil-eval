from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Status(str, Enum):
    pending = "pending"
    approved = "approved"
    corrected = "corrected"
    rejected = "rejected"
    skipped = "skipped"
    superseded = "superseded"


class RoutedBy(str, Enum):
    low_conf = "low_conf"
    audit = "audit"
    manual = "manual"


class AssetRole(str, Enum):
    input = "input"
    output = "output"
    context = "context"


class ReviewAction(str, Enum):
    approve = "approve"
    edit = "edit"
    reject = "reject"
    skip = "skip"
    choose = "choose"


def validate_unit(name: str, value: Any, *, allow_none: bool = False) -> float | None:
    """Validate a finite, non-boolean number in [0, 1] (reject, never clamp)."""
    if value is None and allow_none:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{name} must be a finite number in [0, 1], got {value!r}")
    return float(value)


@dataclass(frozen=True)
class Judgment:
    confidence: float
    label: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        validate_unit("confidence", self.confidence)


@dataclass
class AssetRef:
    sha256: str
    role: AssetRole
    mime: str
    path: str
    width: int | None = None
    height: int | None = None
    order: int = 0
    video_ts: float | None = None
    bbox: list[float] | None = None


@dataclass
class Item:
    id: str
    project: str
    fn_name: str | None
    input: Any
    output: Any
    confidence: float | None
    judge_label: str | None
    judge_reason: str | None
    judge_metadata: dict
    routed_by: RoutedBy
    status: Status
    tags: dict[str, str]
    trace_id: str | None
    created_at: datetime
    assets: list[AssetRef] = field(default_factory=list)
    options: list[Any] | None = None
    expires_at: datetime | None = None


@dataclass
class ProjectConfig:
    name: str
    threshold: float | None
    target_miss_rate: float
    audit_rate: float
    webhook_url: str | None = None
    webhook_secret: str | None = None


@dataclass(kw_only=True)
class Review:
    id: str
    item_id: str
    reviewer: str | None
    action: ReviewAction
    corrected_output: Any = None
    choice: Any = None
    reason_codes: list[str] = field(default_factory=list)
    reason_text: str | None = None
    judge_correct: bool | None = None
    note: str | None = None
    created_at: datetime


@dataclass(kw_only=True)
class ThresholdHistory:
    id: str
    project: str
    old: float | None
    new: float
    n_samples: int
    est_review_rate: float | None
    est_miss_rate: float | None
    created_at: datetime


@dataclass(kw_only=True)
class ReasonCode:
    project: str
    code: str
    label: str | None = None
    polarity: str | None = None


@dataclass(kw_only=True)
class TagThreshold:
    project: str
    tag_key: str
    tag_value: str
    threshold: float

    def __post_init__(self):
        validate_unit("threshold", self.threshold)
