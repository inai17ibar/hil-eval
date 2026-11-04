from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from ..blobs import put_blob
from ..config import DEFAULT_AUDIT_RATE, DEFAULT_TARGET_MISS_RATE
from ..types import (
    AssetRef,
    AssetRole,
    Item,
    ProjectConfig,
    Review,
    ReviewAction,
    RoutedBy,
    Status,
    ThresholdHistory,
    validate_unit,
)
from .base import ItemNotFoundError

_ACTION_TO_STATUS = {
    ReviewAction.approve: Status.approved,
    ReviewAction.edit: Status.corrected,
    ReviewAction.reject: Status.rejected,
    ReviewAction.skip: Status.skipped,
}

SCHEMA_VERSION = 1


def _item_from_data(data: dict) -> Item:
    data = dict(data)
    data["created_at"] = datetime.fromisoformat(data["created_at"])
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
            self._migrate_to_v1(db)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

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

    def _run(self, operation):
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

    def ensure_project(self, name, *, threshold=None, target_miss_rate=None, audit_rate=None):
        for key, value in (
            ("threshold", threshold),
            ("target_miss_rate", target_miss_rate),
            ("audit_rate", audit_rate),
        ):
            validate_unit(key, value, allow_none=True)

        def write(db):
            db.execute(
                """INSERT INTO projects VALUES (?, ?, ?, ?)
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
    ):
        if order not in ("asc", "desc"):
            raise ValueError(f"order must be 'asc' or 'desc', got {order!r}")
        clauses, params = self._item_filters(status, min_confidence, max_confidence)
        query = "SELECT data FROM items WHERE project=?"
        params = [project, *params]
        for clause in clauses:
            query += f" AND {clause}"
        direction = "ASC" if order == "asc" else "DESC"
        query += f" ORDER BY created_at {direction}, id {direction} LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        rows = self._run(lambda db: db.execute(query, params).fetchall())
        return [_item_from_data(json.loads(row[0])) for row in rows]

    def count_items(self, project, *, status=None, min_confidence=None, max_confidence=None):
        clauses, params = self._item_filters(status, min_confidence, max_confidence)
        query = "SELECT COUNT(*) FROM items WHERE project=?"
        params = [project, *params]
        for clause in clauses:
            query += f" AND {clause}"
        row = self._run(lambda db: db.execute(query, params).fetchone())
        return row[0]

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
