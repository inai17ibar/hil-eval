import pytest
from fastapi.testclient import TestClient

from hil_eval.server import create_app
from hil_eval.store import SQLiteStore

PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(tmp_path / "db.sqlite")


@pytest.fixture
def client(store):
    return TestClient(create_app(store))


def create_item(client, **overrides):
    body = {"project": "p", "input": {"x": 1}, "output": {"y": 2}, "confidence": 0.5}
    body.update(overrides)
    return client.post("/v1/items", json=body)


def test_healthz_is_always_open(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_create_and_get_item(client):
    resp = create_item(client)
    assert resp.status_code == 201
    item = resp.json()
    assert item["project"] == "p"
    assert item["routed_by"] == "manual"
    assert item["status"] == "pending"
    assert item["input"] == {"x": 1}
    assert "reviews" not in item

    got = client.get(f"/v1/items/{item['id']}")
    assert got.status_code == 200
    assert got.json()["reviews"] == []


def test_get_item_404(client):
    assert client.get("/v1/items/missing").status_code == 404


def test_create_item_rejects_out_of_range_confidence(client):
    resp = create_item(client, confidence=1.5)
    assert resp.status_code == 422


def test_create_item_registers_project(client):
    create_item(client, project="brand-new")
    names = {p["name"] for p in client.get("/v1/projects").json()}
    assert "brand-new" in names


def test_delete_item(client):
    item = create_item(client).json()
    assert client.delete(f"/v1/items/{item['id']}").status_code == 204
    assert client.get(f"/v1/items/{item['id']}").status_code == 404
    assert client.delete(f"/v1/items/{item['id']}").status_code == 404


def test_list_items_filters_and_total(client):
    create_item(client, confidence=0.1)
    create_item(client, confidence=0.9)
    create_item(client, project="other", confidence=0.1)

    resp = client.get("/v1/items", params={"project": "p"})
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2

    resp = client.get("/v1/items", params={"project": "p", "min_confidence": 0.5})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["confidence"] == 0.9

    resp = client.get("/v1/items", params={"project": "p", "limit": 1, "offset": 0})
    assert len(resp.json()["items"]) == 1
    assert resp.json()["total"] == 2


def test_list_items_newest_first(client):
    a = create_item(client, confidence=0.1).json()
    b = create_item(client, confidence=0.1).json()
    ids = [i["id"] for i in client.get("/v1/items", params={"project": "p"}).json()["items"]]
    assert ids == [b["id"], a["id"]]


def test_list_items_status_may_repeat(client):
    a = create_item(client).json()
    b = create_item(client).json()
    c = create_item(client).json()
    client.post(f"/v1/items/{a['id']}/review", json={"action": "approve"})
    client.post(f"/v1/items/{b['id']}/review", json={"action": "reject"})

    resp = client.get(
        "/v1/items",
        params=[("project", "p"), ("status", "approved"), ("status", "rejected")],
    )
    ids = {i["id"] for i in resp.json()["items"]}
    assert ids == {a["id"], b["id"]}
    assert c["id"] not in ids


def test_create_item_rejects_invalid_confidence_range(client):
    resp = client.get("/v1/items", params={"project": "p", "min_confidence": 2.0})
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "action,status,needs_corrected",
    [
        ("approve", "approved", False),
        ("edit", "corrected", True),
        ("reject", "rejected", False),
        ("skip", "skipped", False),
    ],
)
def test_review_updates_status(client, action, status, needs_corrected):
    item = create_item(client).json()
    body = {"action": action}
    if needs_corrected:
        body["corrected_output"] = {"y": 3}
    resp = client.post(f"/v1/items/{item['id']}/review", json=body)
    assert resp.status_code == 200
    assert resp.json()["status"] == status
    assert "reviews" not in resp.json()


def test_review_edit_without_corrected_output_is_422(client):
    item = create_item(client).json()
    resp = client.post(f"/v1/items/{item['id']}/review", json={"action": "edit"})
    assert resp.status_code == 422


def test_review_missing_item_is_404(client):
    resp = client.post("/v1/items/missing/review", json={"action": "approve"})
    assert resp.status_code == 404


def test_review_records_full_history_on_get(client):
    item = create_item(client).json()
    client.post(
        f"/v1/items/{item['id']}/review",
        json={"action": "reject", "reviewer": "bob", "note": "nope"},
    )
    got = client.get(f"/v1/items/{item['id']}").json()
    assert len(got["reviews"]) == 1
    assert got["reviews"][0]["reviewer"] == "bob"
    assert got["reviews"][0]["action"] == "reject"
    assert got["reviews"][0]["note"] == "nope"


def test_blob_round_trip(store, client):
    sha256 = "a" * 64
    store.put_blob(sha256, PNG_HEADER, "image/png")

    resp = client.get(f"/v1/blobs/{sha256}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == PNG_HEADER


def test_blob_not_found(client):
    assert client.get(f"/v1/blobs/{'a' * 64}").status_code == 404


@pytest.mark.parametrize(
    "sha256",
    [
        "not-hex-" + "a" * 56,
        "a" * 63,
        "a" * 65,
        "%2e%2e",  # percent-encoded ".." as a single path segment
    ],
)
def test_blob_rejects_malformed_sha256(client, sha256):
    # a literal ".." (or "../") in the URL gets collapsed by HTTP client-side
    # normalization before it's ever sent, so it can't reach our route at
    # all; percent-encoding is the realistic bypass of that normalization,
    # and it's what actually exercises the sha256 regex guard below.
    resp = client.get(f"/v1/blobs/{sha256}")
    assert resp.status_code == 404


def test_blob_cannot_escape_blob_dir(store, client, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("sensitive")
    # even if an attacker could get a traversal-shaped sha256 past routing,
    # the store itself only ever looks inside blob_dir for an exact filename.
    assert store.get_blob_path("..") is None


class TestApiKeyAuth:
    @pytest.fixture
    def client(self, store):
        return TestClient(create_app(store, api_key="s3cret"))

    def test_v1_routes_require_key(self, client):
        assert client.get("/v1/projects").status_code == 401

    def test_v1_routes_accept_correct_header(self, client):
        resp = client.get("/v1/projects", headers={"X-API-Key": "s3cret"})
        assert resp.status_code == 200

    def test_v1_routes_reject_wrong_key(self, client):
        resp = client.get("/v1/projects", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

    def test_healthz_stays_open(self, client):
        assert client.get("/healthz").status_code == 200

    def test_blob_accepts_query_api_key(self, store, client):
        sha256 = "b" * 64
        store.put_blob(sha256, PNG_HEADER, "image/png")
        assert client.get(f"/v1/blobs/{sha256}").status_code == 401
        assert client.get(f"/v1/blobs/{sha256}?api_key=s3cret").status_code == 200
        assert client.get(f"/v1/blobs/{sha256}?api_key=wrong").status_code == 401
        resp = client.get(f"/v1/blobs/{sha256}", headers={"X-API-Key": "s3cret"})
        assert resp.status_code == 200


def test_static_ui_not_built_message(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "not built" in resp.text.lower()
    assert client.get("/some/deep/route").status_code == 200


def test_static_ui_served_when_built(store, tmp_path):
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<html>index</html>")
    (static_dir / "assets" / "app.js").write_text("console.log('hi')")
    (static_dir / "favicon.ico").write_bytes(b"icon")

    app = create_app(store, static_dir=static_dir)
    client = TestClient(app)

    assert client.get("/").text == "<html>index</html>"
    assert client.get("/some/client/route").text == "<html>index</html>"
    assert client.get("/assets/app.js").text == "console.log('hi')"
    assert client.get("/favicon.ico").content == b"icon"


def test_static_ui_fallback_blocks_path_traversal(store, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("sensitive")
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>index</html>")

    app = create_app(store, static_dir=static_dir)
    client = TestClient(app)

    resp = client.get("/..%2fsecret.txt")
    assert resp.status_code == 200
    assert resp.text == "<html>index</html>"
