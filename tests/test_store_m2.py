import json
import sqlite3
from datetime import datetime, timezone

import pytest

from hil_eval.store import ItemNotFoundError, SQLiteStore
from hil_eval.types import (
    Item,
    Review,
    ReviewAction,
    RoutedBy,
    Status,
    ThresholdHistory,
)


def make_item(store, *, id, project="p", confidence=0.5, routed_by=RoutedBy.low_conf, **overrides):
    defaults = dict(
        id=id,
        project=project,
        fn_name="fn",
        input={"x": 1},
        output={"y": 2},
        confidence=confidence,
        judge_label=None,
        judge_reason=None,
        judge_metadata={},
        routed_by=routed_by,
        status=Status.pending,
        tags={},
        trace_id=None,
        created_at=datetime.now(timezone.utc),
        assets=[],
    )
    defaults.update(overrides)
    item = Item(**defaults)
    return store.save_item(item)


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(tmp_path / "db.sqlite")


def test_get_item_roundtrip(store):
    item = make_item(store, id="a")
    assert store.get_item("a") == item
    assert store.get_item("missing") is None


def test_delete_item(store):
    make_item(store, id="a")
    assert store.delete_item("a") is True
    assert store.get_item("a") is None
    assert store.delete_item("a") is False


def test_delete_item_cascades_reviews(store):
    make_item(store, id="a")
    store.save_review(
        Review(
            id="r1",
            item_id="a",
            reviewer="bob",
            action=ReviewAction.approve,
            created_at=datetime.now(timezone.utc),
        )
    )
    store.delete_item("a")
    assert store.list_reviews("a") == []


def test_list_items_confidence_filters(store):
    make_item(store, id="lo", confidence=0.1)
    make_item(store, id="mid", confidence=0.5)
    make_item(store, id="hi", confidence=0.9)
    make_item(store, id="none", confidence=None, routed_by=RoutedBy.manual)

    ids = {i.id for i in store.list_items("p", min_confidence=0.4, max_confidence=0.6)}
    assert ids == {"mid"}

    ids = {i.id for i in store.list_items("p", min_confidence=0.4)}
    assert ids == {"mid", "hi"}


def test_list_items_status_filter_accepts_list(store):
    make_item(store, id="a", confidence=0.1)
    make_item(store, id="b", confidence=0.1)
    store.save_review(
        Review(
            id="r1",
            item_id="a",
            reviewer=None,
            action=ReviewAction.approve,
            created_at=datetime.now(timezone.utc),
        )
    )
    store.save_review(
        Review(
            id="r2",
            item_id="b",
            reviewer=None,
            action=ReviewAction.reject,
            created_at=datetime.now(timezone.utc),
        )
    )
    ids = {i.id for i in store.list_items("p", status=[Status.approved, Status.rejected])}
    assert ids == {"a", "b"}


def test_list_items_order_and_count(store):
    make_item(store, id="a", confidence=0.1)
    make_item(store, id="b", confidence=0.2)
    assert [i.id for i in store.list_items("p", order="asc")] == ["a", "b"]
    assert [i.id for i in store.list_items("p", order="desc")] == ["b", "a"]
    assert store.count_items("p") == 2
    assert store.count_items("p", min_confidence=0.15) == 1
    with pytest.raises(ValueError):
        store.list_items("p", order="sideways")


def test_list_projects(store):
    store.ensure_project("a", threshold=None, target_miss_rate=None, audit_rate=None)
    store.ensure_project("b", threshold=0.5, target_miss_rate=None, audit_rate=None)
    assert [p.name for p in store.list_projects()] == ["a", "b"]


@pytest.mark.parametrize(
    "action,status",
    [
        (ReviewAction.approve, Status.approved),
        (ReviewAction.edit, Status.corrected),
        (ReviewAction.reject, Status.rejected),
        (ReviewAction.skip, Status.skipped),
    ],
)
def test_save_review_updates_item_status(store, action, status):
    make_item(store, id="a")
    review = Review(
        id="r1",
        item_id="a",
        reviewer="bob",
        action=action,
        corrected_output={"y": 3} if action == ReviewAction.edit else None,
        created_at=datetime.now(timezone.utc),
    )
    store.save_review(review)
    assert store.get_item("a").status == status
    assert store.list_reviews("a") == [review]


def test_save_review_missing_item_raises(store):
    review = Review(
        id="r1",
        item_id="missing",
        reviewer=None,
        action=ReviewAction.approve,
        created_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ItemNotFoundError):
        store.save_review(review)
    assert store.list_reviews("missing") == []


def test_save_review_is_atomic_on_failure(store):
    # No item exists; verify the review insert did not happen even though it
    # would otherwise have been a valid standalone insert (no partial writes).
    review = Review(
        id="r1",
        item_id="missing",
        reviewer=None,
        action=ReviewAction.approve,
        created_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ItemNotFoundError):
        store.save_review(review)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_list_reviews_multiple_in_order(store):
    make_item(store, id="a")
    r1 = Review(
        id="r1",
        item_id="a",
        reviewer=None,
        action=ReviewAction.reject,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    r2 = Review(
        id="r2",
        item_id="a",
        reviewer=None,
        action=ReviewAction.approve,
        created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    store.save_review(r1)
    store.save_review(r2)
    assert [r.id for r in store.list_reviews("a")] == ["r1", "r2"]


def test_list_reviewed_returns_latest_review_per_item(store):
    make_item(store, id="a", confidence=0.3)
    store.save_review(
        Review(
            id="r1",
            item_id="a",
            reviewer=None,
            action=ReviewAction.reject,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
    )
    store.save_review(
        Review(
            id="r2",
            item_id="a",
            reviewer=None,
            action=ReviewAction.approve,
            created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
    )
    [(item, review)] = store.list_reviewed("p")
    assert item.id == "a"
    assert review.id == "r2"


def test_list_reviewed_excludes_unreviewed_items(store):
    make_item(store, id="a")
    assert store.list_reviewed("p") == []


def test_threshold_history(store):
    store.ensure_project("p", threshold=0.8, target_miss_rate=None, audit_rate=None)
    entry = ThresholdHistory(
        id="h1",
        project="p",
        old=0.8,
        new=0.7,
        n_samples=42,
        est_review_rate=0.3,
        est_miss_rate=0.02,
        created_at=datetime.now(timezone.utc),
    )
    store.add_threshold_history(entry)
    assert store.list_threshold_history("p") == [entry]
    assert store.list_threshold_history("other") == []


def _m1_schema_db(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE projects (
                name TEXT PRIMARY KEY, threshold REAL,
                target_miss_rate REAL NOT NULL, audit_rate REAL NOT NULL);
            CREATE TABLE items (
                id TEXT PRIMARY KEY, project TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX items_project_status ON items(project, status);
        """)
        data = {
            "id": "legacy-1",
            "project": "p",
            "fn_name": "fn",
            "input": {"x": 1},
            "output": {"y": 2},
            "confidence": 0.42,
            "judge_label": None,
            "judge_reason": None,
            "judge_metadata": {},
            "routed_by": RoutedBy.low_conf.value,
            "status": Status.pending.value,
            "tags": {},
            "trace_id": None,
            "created_at": "2024-01-01T00:00:00+00:00",
            "assets": [],
        }
        db.execute(
            "INSERT INTO items VALUES (?, ?, ?, ?, ?)",
            ("legacy-1", "p", "pending", data["created_at"], json.dumps(data)),
        )
        db.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?)",
            ("p", 0.8, 0.05, 0.02),
        )
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0


def test_migrates_m1_database(tmp_path):
    path = tmp_path / "legacy.sqlite"
    _m1_schema_db(path)

    store = SQLiteStore(path)

    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {"reviews", "threshold_history"} <= tables

    # pre-existing item is still readable and its confidence column was backfilled
    item = store.get_item("legacy-1")
    assert item.confidence == 0.42
    assert item.input == {"x": 1}
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT confidence FROM items WHERE id=?", ("legacy-1",)).fetchone()
        assert row[0] == 0.42

    # the migrated store is fully functional for M2 features
    assert store.list_items("p", min_confidence=0.4) == [item]
    review = Review(
        id="r1",
        item_id="legacy-1",
        reviewer="bob",
        action=ReviewAction.approve,
        created_at=datetime.now(timezone.utc),
    )
    store.save_review(review)
    assert store.get_item("legacy-1").status == Status.approved


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "legacy.sqlite"
    _m1_schema_db(path)
    SQLiteStore(path)
    # re-opening an already-migrated db should not error or re-run the migration
    store = SQLiteStore(path)
    assert store.get_item("legacy-1").confidence == 0.42
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
