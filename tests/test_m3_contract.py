import base64
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from hil_eval.blobs import _is_video_bytes, sniff_mime, store_asset
from hil_eval.cli import app
from hil_eval.server import create_app
from hil_eval.store import SQLiteStore
from hil_eval.types import Item, Review, ReviewAction, RoutedBy, Status, TagThreshold

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(tmp_path / "db.sqlite")


def item(store, **kwargs):
    values = dict(
        id=uuid4().hex,
        project="p",
        fn_name=None,
        input={"x": 1},
        output="a",
        confidence=0.2,
        judge_label=None,
        judge_reason=None,
        judge_metadata={},
        routed_by=RoutedBy.low_conf,
        status=Status.pending,
        tags={},
        trace_id=None,
        created_at=NOW,
    )
    values.update(kwargs)
    return store.save_item(Item(**values))


def review(store, i, **kwargs):
    values = dict(
        id=uuid4().hex, item_id=i.id, reviewer=None, action=ReviewAction.approve, created_at=NOW
    )
    values.update(kwargs)
    return store.save_review(Review(**values))


@pytest.mark.parametrize(
    "data,mime", [(b"\x1aE\xdf\xa3video", "video/webm"), (b"\x00\x00\x00\x18ftypisom", "video/mp4")]
)
def test_video_roundtrip(store, data, mime):
    assert _is_video_bytes(data)
    assert sniff_mime(data) == mime
    asset = store_asset(data, store, video_ts=1.5, bbox=[0, 1, 2, 3], order=2)
    saved = item(store, assets=[asset], options=["a", "b"])
    assert store.get_item(saved.id) == saved
    client = TestClient(create_app(store))
    response = client.post(
        "/v1/items",
        json=dict(
            project="p",
            input=1,
            output=2,
            assets=[
                dict(
                    data_base64=base64.b64encode(data).decode(),
                    role="context",
                    video_ts=2,
                    bbox=[0, 0, 2, 2],
                )
            ],
        ),
    )
    assert response.status_code == 201
    upload = response.json()["assets"][0]
    assert upload["mime"] == mime and upload["order"] == 0
    assert upload["role"] == "context" and upload["video_ts"] == 2
    downloaded = client.get(upload["url"])
    assert downloaded.content == data
    assert downloaded.headers["content-type"] == mime


@pytest.mark.parametrize(
    "asset",
    [
        dict(data_base64="!"),
        dict(data_base64="", bbox=[1, 2]),
        dict(data_base64="", bbox=[0, 0, -1, 2]),
        dict(data_base64="", video_ts=-1),
    ],
)
def test_invalid_assets(store, asset):
    client = TestClient(create_app(store))
    assert (
        client.post(
            "/v1/items", json=dict(project="p", input=1, output=2, assets=[asset])
        ).status_code
        == 422
    )


def test_choice_and_reason_codes(store):
    client = TestClient(create_app(store))
    i = item(store, options=[{"a": 1}, 0, None])
    endpoint = f"/v1/items/{i.id}/review"
    for body in [dict(action="choose"), dict(action="choose", choice="bad")]:
        assert client.post(endpoint, json=body).status_code == 422
    assert (
        client.post(
            "/v1/projects/p/reason-codes",
            json=dict(code="quality", label="Quality", polarity="negative"),
        ).status_code
        == 200
    )
    assert client.get("/v1/projects/p/reason-codes").json()[0]["code"] == "quality"
    assert (
        client.post(
            endpoint, json=dict(action="choose", choice=0, reason_codes=["bad"])
        ).status_code
        == 422
    )
    for choice in [0, None, {"a": 1}]:
        assert (
            client.post(
                endpoint,
                json=dict(
                    action="choose", choice=choice, reason_codes=["quality"], reason_text="why"
                ),
            ).status_code
            == 200
        )
    got = store.list_reviews(i.id)[-1]
    assert got.choice == {"a": 1} and got.reason_text == "why"
    assert store.get_item(i.id).status == Status.corrected
    other = item(store, project="other")
    assert (
        client.post(
            f"/v1/items/{other.id}/review",
            json=dict(action="choose", choice=False, reason_codes=["free"]),
        ).status_code
        == 200
    )


def test_expiry_before_pagination_and_counts(store):
    expired = item(store, expires_at=NOW)
    future = item(store, expires_at=NOW + timedelta(days=1))
    reviewed = item(store, expires_at=NOW - timedelta(days=1))
    review(store, reviewed)
    assert store.count_items("p", now=NOW) == 2
    assert store.list_items("p", now=NOW, limit=1, offset=1)[0].id in {future.id, reviewed.id}
    assert store.count_items("p", exclude_expired=False) == 3
    assert store.get_item(expired.id) is not None
    client = TestClient(create_app(store))
    assert client.get("/v1/items").json()["total"] == 1
    assert client.get("/v1/items?include_expired=true").json()["total"] == 3


def test_supersede_project_status_and_or_selectors(store):
    a = item(store, trace_id="trace")
    b = item(store, tags={"model": "a"})
    c = item(store, trace_id="trace")
    review(store, c)
    d = item(store, project="other", trace_id="trace")
    client = TestClient(create_app(store))
    assert client.post("/v1/items/supersede", json=dict(project="p")).status_code == 422
    assert client.post(
        "/v1/items/supersede",
        json=dict(project="p", trace_id="trace", tag_key="model", tag_value="a"),
    ).json() == {"superseded": 2}
    assert store.get_item(a.id).status == store.get_item(b.id).status == Status.superseded
    assert store.get_item(c.id).status == Status.approved
    assert store.get_item(d.id).status == Status.pending
    assert store.supersede_items("p", trace_id="trace") == 0


@pytest.mark.parametrize("version", [0, 1])
def test_old_database_migrates(tmp_path, version):
    path = tmp_path / "old.db"
    # Build the pre-M3 schema independently, with a genuinely old JSON payload.
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE projects (name TEXT PRIMARY KEY, threshold REAL, "
            "target_miss_rate REAL, audit_rate REAL); "
            "CREATE TABLE items (id TEXT PRIMARY KEY, project TEXT, status TEXT, "
            "created_at TEXT, data TEXT" + (", confidence REAL" if version else "") + ");"
        )
        old = dict(
            id="old",
            project="p",
            fn_name=None,
            input=1,
            output=2,
            confidence=0.2,
            judge_label=None,
            judge_reason=None,
            judge_metadata={},
            routed_by="manual",
            status="pending",
            tags={},
            trace_id=None,
            created_at=NOW.isoformat(),
            assets=[],
        )
        values = ["old", "p", "pending", NOW.isoformat(), json.dumps(old)]
        if version:
            values.append(0.2)
        db.execute("INSERT INTO items VALUES (" + ",".join("?" for _ in values) + ")", values)
        db.execute(f"PRAGMA user_version={version}")
    migrated = SQLiteStore(path)
    assert migrated.get_item("old").options is None
    assert migrated.get_item("old").expires_at is None
    assert migrated.list_reason_codes("p") == []
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert SQLiteStore(path).get_item("old").confidence == 0.2


def test_tag_tuning_cli_and_fallback(store):
    store.ensure_project("p", threshold=0.8)
    for model in ["a", "b"]:
        for _ in range(40):
            review(store, item(store, tags={"model": model}))
    result = CliRunner().invoke(
        app, ["--db", str(store.path), "tune", "--project", "p", "--by-tag", "model", "--apply"]
    )
    assert result.exit_code == 0, result.output
    assert "model=a" in result.output and "model=b" in result.output
    assert len(store.list_tag_thresholds("p")) == 2
    assert store.get_project("p").threshold == 0.8
    assert store.list_threshold_history("p") == []
    client = TestClient(create_app(store))
    assert client.get("/v1/projects/p/threshold?tag=model:a").json()["threshold"] == pytest.approx(
        0.7
    )
    assert client.get("/v1/projects/p/threshold?tag=model:missing").json() == {"threshold": 0.8}
    assert client.get("/v1/projects/p/threshold?tag=bad").status_code == 422
    assert (
        client.put(
            "/v1/projects/p/threshold", json=dict(tag_key="model", tag_value="a", threshold=0.4)
        ).status_code
        == 200
    )
    assert store.get_tag_threshold("p", "model", "a") == 0.4
    with pytest.raises(ValueError):
        TagThreshold(project="p", tag_key="x", tag_value="y", threshold=float("nan"))


def test_project_webhook_settings_preserved(store):
    first = store.ensure_project("p", webhook_url="http://local", webhook_secret="secret")
    assert store.ensure_project("p") == first
    assert store.get_project("p") == first
