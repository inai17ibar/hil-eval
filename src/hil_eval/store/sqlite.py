from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from ..blobs import put_blob
from ..config import DEFAULT_AUDIT_RATE, DEFAULT_TARGET_MISS_RATE
from ..types import AssetRef, AssetRole, Item, ProjectConfig, RoutedBy, Status


class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.blob_dir = self.path.parent / "blobs"
        self.blob_dir.mkdir(exist_ok=True)
        self._run(
            lambda db: db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                name TEXT PRIMARY KEY, threshold REAL,
                target_miss_rate REAL NOT NULL, audit_rate REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY, project TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS items_project_status ON items(project, status);
        """)
        )

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

    def update_threshold(self, project, threshold):
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
                "INSERT INTO items VALUES (?, ?, ?, ?, ?)",
                (item.id, item.project, item.status.value, data["created_at"], json.dumps(data)),
            )
        )
        return item

    def list_items(self, project, *, status=None, limit=100, offset=0):
        query = "SELECT data FROM items WHERE project=?"
        params = [project]
        if status is not None:
            query += " AND status=?"
            params.append(Status(status).value)
        query += " ORDER BY created_at, id LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        rows = self._run(lambda db: db.execute(query, params).fetchall())
        items = []
        for row in rows:
            data = json.loads(row[0])
            data["created_at"] = datetime.fromisoformat(data["created_at"])
            data["status"] = Status(data["status"])
            data["routed_by"] = RoutedBy(data["routed_by"])
            data["assets"] = [
                AssetRef(**{**a, "role": AssetRole(a["role"])}) for a in data["assets"]
            ]
            items.append(Item(**data))
        return items

    def put_blob(self, sha256, data, mime):
        return put_blob(self.blob_dir, sha256, data)

    def get_blob_path(self, sha256):
        path = self.blob_dir / sha256
        return path if path.is_file() else None
