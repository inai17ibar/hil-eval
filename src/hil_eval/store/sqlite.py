from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from ..blobs import put_blob
from ..config import DEFAULT_AUDIT_RATE, DEFAULT_TARGET_MISS_RATE
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
    ThresholdHistory,
    validate_unit,
)
from .base import ItemNotFoundError

_ACTION_TO_STATUS = {
    ReviewAction.approve: Status.approved,
    ReviewAction.edit: Status.corrected,
    ReviewAction.choose: Status.corrected,
    ReviewAction.reject: Status.rejected,
    ReviewAction.skip: Status.skipped,
}

_T = TypeVar("_T")

SCHEMA_VERSION = 2


def _item_from_data(data: dict) -> Item:
    data = dict(data)
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    if data.get("expires_at") is not None:
        data["expires_at"] = datetime.fromisoformat(data["expires_at"])
    data["status"] = Status(data["status"])
    data["routed_by"] = RoutedBy(data["routed_by"])
    data["assets"] = [AssetRef(**{**a, "role": AssetRole(a["role"])}) for a in data["assets"]]
    return Item(**data)


def _review_from_data(data: dict) -> Review:
    data = dict(data)
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    data["action"] = ReviewAction(data["action"])
    return Review(**data)


def _threshold_history_from_data(data: dict) -> ThresholdHistory:
    data = dict(data)
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    return ThresholdHistory(**data)


class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.blob_dir = self.path.parent / "blobs"
        self.blob_dir.mkdir(exist_ok=True)
        self._run(self._init_schema)

    def _init_schema(self, db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                name TEXT PRIMARY KEY, threshold REAL,
                target_miss_rate REAL NOT NULL, audit_rate REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY, project TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, data TEXT NOT NULL, confidence REAL);
            CREATE INDEX IF NOT EXISTS items_project_status ON items(project, status);
            CREATE TABLE IF NOT EXISTS reviews (
                id TEXT PRIMARY KEY, item_id TEXT NOT NULL,
                created_at TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS reviews_item ON reviews(item_id);
            CREATE TABLE IF NOT EXISTS threshold_history (
                id TEXT PRIMARY KEY, project TEXT NOT NULL,
                created_at TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS threshold_history_project ON threshold_history(project);
        """)
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            if version < 1:
                self._migrate_to_v1(db)
            self._migrate_to_v2(db)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _migrate_to_v2(self, db):
        db.execute(
            "CREATE TABLE IF NOT EXISTS reason_codes (project TEXT, code TEXT, "
            "label TEXT, polarity TEXT, PRIMARY KEY(project, code))"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS tag_thresholds (project TEXT, tag_key "
            "TEXT, tag_value TEXT, threshold REAL, PRIMARY KEY(project, tag_key, tag_value))"
        )
        columns = [r[1] for r in db.execute("PRAGMA table_info(projects)")]
        for name in ("webhook_url", "webhook_secret"):
            if name not in columns:
                db.execute(f"ALTER TABLE projects ADD COLUMN {name} TEXT")

    def _migrate_to_v1(self, db):
        # M1 databases predate the `confidence` column on `items` (added to support
        # min_confidence/max_confidence filtering without a JSON scan per query).
        columns = [row[1] for row in db.execute("PRAGMA table_info(items)").fetchall()]
        if "confidence" not in columns:
            db.execute("ALTER TABLE items ADD COLUMN confidence REAL")
        rows = db.execute("SELECT id, data FROM items WHERE confidence IS NULL").fetchall()
        updates = []
        for item_id, data in rows:
            confidence = json.loads(data).get("confidence")
            if confidence is not None:
                updates.append((confidence, item_id))
        if updates:
            db.executemany("UPDATE items SET confidence=? WHERE id=?", updates)

    def _run(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        for delay in (0.02, 0.06, 0.15, None):
            try:
                db = sqlite3.connect(self.path)
                try:
                    db.execute("PRAGMA busy_timeout=2000")
                    db.execute("PRAGMA journal_mode=WAL")
                    with db:
                        return operation(db)
                finally:
                    db.close()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or delay is None:
                    raise
                time.sleep(delay)
        raise AssertionError("retry loop exhausted")

    def ensure_project(
        self,
        name,
        *,
        threshold=None,
        target_miss_rate=None,
        audit_rate=None,
        webhook_url=None,
        webhook_secret=None,
    ):
        for key, value in (
            ("threshold", threshold),
            ("target_miss_rate", target_miss_rate),
            ("audit_rate", audit_rate),
        ):
            validate_unit(key, value, allow_none=True)

        def write(db):
            db.execute(
                """INSERT INTO projects (name, threshold, target_miss_rate, audit_rate)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                threshold=COALESCE(?, threshold),
                target_miss_rate=COALESCE(?, target_miss_rate),
                audit_rate=COALESCE(?, audit_rate)""",
                (
                    name,
                    threshold,
                    DEFAULT_TARGET_MISS_RATE if target_miss_rate is None else target_miss_rate,
                    DEFAULT_AUDIT_RATE if audit_rate is None else audit_rate,
                    threshold,
                    target_miss_rate,
                    audit_rate,
                ),
            )
            for key, value in (("webhook_url", webhook_url), ("webhook_secret", webhook_secret)):
                if value is not None:
                    db.execute(f"UPDATE projects SET {key}=? WHERE name=?", (value, name))
            return ProjectConfig(
                *db.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
            )

        return self._run(write)

    def get_project(self, name):
        row = self._run(
            lambda db: db.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
        )
        return ProjectConfig(*row) if row else None

    def list_projects(self):
        rows = self._run(lambda db: db.execute("SELECT * FROM projects ORDER BY name").fetchall())
        return [ProjectConfig(*row) for row in rows]

    def update_threshold(self, project, threshold):
        validate_unit("threshold", threshold)
        self._run(
            lambda db: db.execute(
                "UPDATE projects SET threshold=? WHERE name=?", (threshold, project)
            )
        )

    def save_item(self, item):
        data = asdict(item)
        data["created_at"] = item.created_at.isoformat()
        data["expires_at"] = item.expires_at.isoformat() if item.expires_at else None
        self._run(
            lambda db: db.execute(
                "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    item.project,
                    item.status.value,
                    data["created_at"],
                    json.dumps(data),
                    item.confidence,
                ),
            )
        )
        return item

    def get_item(self, item_id):
        row = self._run(
            lambda db: db.execute("SELECT data FROM items WHERE id=?", (item_id,)).fetchone()
        )
        return _item_from_data(json.loads(row[0])) if row else None

    def delete_item(self, item_id):
        def write(db):
            db.execute("DELETE FROM reviews WHERE item_id=?", (item_id,))
            cursor = db.execute("DELETE FROM items WHERE id=?", (item_id,))
            return cursor.rowcount > 0

        return self._run(write)

    @staticmethod
    def _item_filters(status, min_confidence, max_confidence):
        clauses = []
        params = []
        if status is not None:
            statuses = [status] if isinstance(status, Status) else list(status)
            clauses.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(s.value for s in statuses)
        if min_confidence is not None:
            clauses.append("confidence >= ?")
            params.append(min_confidence)
        if max_confidence is not None:
            clauses.append("confidence <= ?")
            params.append(max_confidence)
        return clauses, params

    def list_items(
        self,
        project,
        *,
        status=None,
        min_confidence=None,
        max_confidence=None,
        limit=100,
        offset=0,
        order="asc",
        exclude_expired=True,
        now=None,
    ):
        if order not in ("asc", "desc"):
            raise ValueError(f"order must be 'asc' or 'desc', got {order!r}")
        clauses, params = self._item_filters(status, min_confidence, max_confidence)
        if project is not None:
            clauses.insert(0, "project=?")
            params = [project, *params]
        query = "SELECT data FROM items"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        direction = "ASC" if order == "asc" else "DESC"
        query += f" ORDER BY created_at {direction}, id {direction}"
        rows = self._run(lambda db: db.execute(query, params).fetchall())
        items = [_item_from_data(json.loads(row[0])) for row in rows]
        if exclude_expired:
            if now is None and any(i.expires_at and i.status == Status.pending for i in items):
                raise ValueError("now is required to filter expiring items")
            items = [
                i
                for i in items
                if i.status != Status.pending
                or i.expires_at is None
                or (now is not None and i.expires_at > now)
            ]
        return items[offset : offset + limit]

    def count_items(
        self,
        project,
        *,
        status=None,
        min_confidence=None,
        max_confidence=None,
        exclude_expired=True,
        now=None,
    ):
        return len(
            self.list_items(
                project,
                status=status,
                min_confidence=min_confidence,
                max_confidence=max_confidence,
                exclude_expired=exclude_expired,
                now=now,
                limit=2**63 - 1,
            )
        )

    def register_reason_code(self, entry: ReasonCode):
        self._run(
            lambda db: db.execute(
                "INSERT INTO reason_codes VALUES (?, ?, ?, ?) ON CONFLICT(project, "
                "code) DO UPDATE SET label=excluded.label, polarity=excluded.polarity",
                (entry.project, entry.code, entry.label, entry.polarity),
            )
        )

    def list_reason_codes(self, project):
        rows = self._run(
            lambda db: db.execute(
                "SELECT * FROM reason_codes WHERE project=? ORDER BY code", (project,)
            ).fetchall()
        )
        return [ReasonCode(project=r[0], code=r[1], label=r[2], polarity=r[3]) for r in rows]

    def set_tag_threshold(self, entry: TagThreshold):
        validate_unit("threshold", entry.threshold)
        self._run(
            lambda db: db.execute(
                "INSERT INTO tag_thresholds VALUES (?, ?, ?, ?) ON CONFLICT(project, "
                "tag_key, tag_value) DO UPDATE SET threshold=excluded.threshold",
                (entry.project, entry.tag_key, entry.tag_value, entry.threshold),
            )
        )

    def get_tag_threshold(self, project, tag_key, tag_value):
        row = self._run(
            lambda db: db.execute(
                "SELECT threshold FROM tag_thresholds WHERE project=? AND tag_key=? "
                "AND tag_value=?",
                (project, tag_key, tag_value),
            ).fetchone()
        )
        return row[0] if row else None

    def list_tag_thresholds(self, project):
        rows = self._run(
            lambda db: db.execute(
                "SELECT * FROM tag_thresholds WHERE project=? ORDER BY tag_key, tag_value",
                (project,),
            ).fetchall()
        )
        return [
            TagThreshold(project=r[0], tag_key=r[1], tag_value=r[2], threshold=r[3]) for r in rows
        ]

    def supersede_items(self, project, *, trace_id=None, tag=None):
        if trace_id is None and tag is None:
            raise ValueError("trace_id or tag is required")

        def write(db):
            count = 0
            for item_id, raw in db.execute(
                "SELECT id, data FROM items WHERE project=? AND status='pending'", (project,)
            ).fetchall():
                data = json.loads(raw)
                if (trace_id is not None and data.get("trace_id") == trace_id) or (
                    tag is not None and data["tags"].get(tag[0]) == tag[1]
                ):
                    data["status"] = "superseded"
                    db.execute(
                        "UPDATE items SET status='superseded', data=? WHERE id=?",
                        (json.dumps(data), item_id),
                    )
                    count += 1
            return count

        return self._run(write)

    def save_review(self, review):
        data = asdict(review)
        data["created_at"] = review.created_at.isoformat()
        data["action"] = review.action.value
        status = _ACTION_TO_STATUS[review.action]

        def write(db):
            row = db.execute("SELECT data FROM items WHERE id=?", (review.item_id,)).fetchone()
            if row is None:
                raise ItemNotFoundError(review.item_id)
            item_data = json.loads(row[0])
            item_data["status"] = status.value
            db.execute(
                "INSERT INTO reviews (id, item_id, created_at, data) VALUES (?, ?, ?, ?)",
                (review.id, review.item_id, data["created_at"], json.dumps(data)),
            )
            db.execute(
                "UPDATE items SET status=?, data=? WHERE id=?",
                (status.value, json.dumps(item_data), review.item_id),
            )

        self._run(write)
        return review

    def list_reviews(self, item_id):
        rows = self._run(
            lambda db: db.execute(
                "SELECT data FROM reviews WHERE item_id=? ORDER BY created_at, rowid",
                (item_id,),
            ).fetchall()
        )
        return [_review_from_data(json.loads(row[0])) for row in rows]

    def list_reviewed(self, project):
        rows = self._run(
            lambda db: db.execute(
                """SELECT i.data, r.data FROM items i
                   JOIN reviews r ON r.item_id = i.id
                   WHERE i.project = ?
                   ORDER BY r.created_at, r.rowid""",
                (project,),
            ).fetchall()
        )
        latest: dict[str, tuple[str, str]] = {}
        for item_data, review_data in rows:
            item_id = json.loads(item_data)["id"]
            latest[item_id] = (item_data, review_data)  # ascending order -> last write wins
        return [
            (_item_from_data(json.loads(item_data)), _review_from_data(json.loads(review_data)))
            for item_data, review_data in latest.values()
        ]

    def add_threshold_history(self, entry):
        data = asdict(entry)
        data["created_at"] = entry.created_at.isoformat()
        self._run(
            lambda db: db.execute(
                "INSERT INTO threshold_history (id, project, created_at, data) VALUES (?, ?, ?, ?)",
                (entry.id, entry.project, data["created_at"], json.dumps(data)),
            )
        )
        return entry

    def apply_threshold_change(self, entry):
        """Insert a history entry and set the project threshold in one transaction."""
        validate_unit("threshold", entry.new)
        data = asdict(entry)
        data["created_at"] = entry.created_at.isoformat()

        def write(db):
            cur = db.execute(
                "UPDATE projects SET threshold=? WHERE name=?", (entry.new, entry.project)
            )
            if cur.rowcount != 1:
                raise KeyError(f"unknown project {entry.project!r}")
            db.execute(
                "INSERT INTO threshold_history (id, project, created_at, data) VALUES (?, ?, ?, ?)",
                (entry.id, entry.project, data["created_at"], json.dumps(data)),
            )

        self._run(write)
        return entry

    def list_threshold_history(self, project):
        rows = self._run(
            lambda db: db.execute(
                "SELECT data FROM threshold_history WHERE project=? ORDER BY created_at, rowid",
                (project,),
            ).fetchall()
        )
        return [_threshold_history_from_data(json.loads(row[0])) for row in rows]

    def put_blob(self, sha256, data, mime):
        return put_blob(self.blob_dir, sha256, data)

    def get_blob_path(self, sha256):
        path = self.blob_dir / sha256
        return path if path.is_file() else None
