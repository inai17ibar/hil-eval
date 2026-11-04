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


class RoutedBy(str, Enum):
    low_conf = "low_conf"
    audit = "audit"
    manual = "manual"


class AssetRole(str, Enum):
    input = "input"
    output = "output"


@dataclass(frozen=True)
class Judgment:
    confidence: float
    label: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        c = self.confidence
        if (
            not isinstance(c, (int, float))
            or isinstance(c, bool)
            or not math.isfinite(c)
            or not 0 <= c <= 1
        ):
            raise ValueError(f"confidence must be a finite float in [0,1], got {c!r}")


@dataclass
class AssetRef:
    sha256: str
    role: AssetRole
    mime: str
    path: str
    width: int | None = None
    height: int | None = None


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


@dataclass
class ProjectConfig:
    name: str
    threshold: float | None
    target_miss_rate: float
    audit_rate: float
