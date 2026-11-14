"""v0.2 path-reference assets: record without reading, serve under allowed roots only."""

import os

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import hil_eval as hil
from hil_eval import context
from hil_eval.blobs import path_asset, resolve_path_asset
from hil_eval.cli import app as cli
from hil_eval.exporters import export_hf, export_items
from hil_eval.server import create_app
from hil_eval.store import SQLiteStore
from hil_eval.types import AssetKind

WAV = b"RIFF\x24\x00\x00\x00WAVEdata" + bytes(range(64))


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "db" / "hil.db")
    monkeypatch.setattr(context, "_default_client", context.Client("p", store))
    return store


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Extreme SSD" / "Splice"
    (root / "Pack").mkdir(parents=True)
    (root / "Pack" / "kick 01.wav").write_bytes(WAV)
    return root


def test_path_asset_never_reads_the_file(store, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError("path assets must not be read")

    monkeypatch.setattr("pathlib.Path.read_bytes", forbid)
    item = hil.submit(
        {"audio": "/Volumes/Extreme SSD/Splice/missing kick.wav", "n": 1},
        {"family": "drums"},
        path_assets="input.audio",
    )
    [asset] = item.assets
    assert asset.kind == AssetKind.path and asset.sha256 is None
    assert asset.path == "/Volumes/Extreme SSD/Splice/missing kick.wav"
    assert asset.mime == "audio/wav" and asset.filename == "missing kick.wav"
    assert item.input["audio"] == {
        "$asset_path": "/Volumes/Extreme SSD/Splice/missing kick.wav",
        "filename": "missing kick.wav",
    }
    assert not list(store.blob_dir.iterdir())
    stored = store.get_item(item.id)
    assert stored.assets[0].kind == AssetKind.path


def test_path_selector_wins_over_blob_selector_and_keeps_order(store, library):
    real = str(library / "Pack" / "kick 01.wav")
    item = hil.submit(
        {"a": real, "b": real},
        "out",
        assets=["input.a", "input.b"],
        path_assets=["input.b"],
    )
    kinds = sorted((a.order, a.kind.value) for a in item.assets)
    assert kinds == [(0, "blob"), (1, "path")]


def test_decorator_path_assets_and_relative_paths(store, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    @hil.review(judge=lambda a, o: 0.0, audit_rate=0, path_assets=["sound"])
    def label(sound):
        return {"family": "keys"}

    label("rel/clip.flac")
    [item] = store.list_items("p")
    assert item.assets[0].path == str(tmp_path / "rel" / "clip.flac")
    assert item.assets[0].mime == "audio/flac"
    with pytest.raises(ValueError):
        hil.review(judge=lambda a, o: 1, path_assets=["nope"])(lambda sound: 1)


@pytest.mark.parametrize("value", [b"bytes", 3, "", "a\x00b", "https://x/y.wav"])
def test_path_asset_rejects_non_paths(value):
    with pytest.raises((TypeError, ValueError, NotImplementedError)):
        path_asset(value)


def test_resolve_denies_by_default_and_blocks_escapes(tmp_path, library):
    target = str(library / "Pack" / "kick 01.wav")
    assert resolve_path_asset(target, []) == ("not_allowed", None)
    assert resolve_path_asset(target, [str(library)])[0] == "available"
    assert resolve_path_asset(str(library / "Pack" / "gone.wav"), [str(library)]) == (
        "missing",
        None,
    )
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    traversal = str(library) + "/Pack/../../../secret.txt"
    assert resolve_path_asset(traversal, [str(library)]) == ("not_allowed", None)
    os.symlink(secret, library / "Pack" / "link.wav")
    assert resolve_path_asset(str(library / "Pack" / "link.wav"), [str(library)])[0] == (
        "not_allowed"
    )
    # a sibling directory sharing the root's prefix is not inside the root
    sibling = tmp_path / "Extreme SSD" / "Splice-other"
    sibling.mkdir()
    (sibling / "x.wav").write_bytes(WAV)
    assert resolve_path_asset(str(sibling / "x.wav"), [str(library)])[0] == "not_allowed"
    assert resolve_path_asset("relative.wav", [str(library)])[0] == "not_allowed"
    assert resolve_path_asset(str(library / "Pack"), [str(library)])[0] == "missing"
    # a symlinked root itself is fine
    alias = tmp_path / "alias"
    os.symlink(library, alias)
    assert resolve_path_asset(str(alias / "Pack" / "kick 01.wav"), [str(alias)])[0] == ("available")


def item_with_paths(store, *paths):
    return hil.submit({"files": list(paths)}, {"family": "drums"}, path_assets="input.files[*]")


def test_server_streams_allowed_paths_with_range(store, library):
    real = str(library / "Pack" / "kick 01.wav")
    item = item_with_paths(store, real, str(library / "Pack" / "missing.wav"), "/etc/hostname")
    client = TestClient(create_app(store, asset_roots=[str(library)]))
    data = client.get(f"/v1/items/{item.id}").json()
    assets = data["assets"]
    assert [a["availability"] for a in assets] == ["available", "missing", "not_allowed"]
    assert assets[0]["kind"] == "path" and assets[0]["sha256"] is None
    assert assets[0]["source_path"] == real and assets[0]["filename"] == "kick 01.wav"
    assert assets[0]["url"] == f"/v1/items/{item.id}/assets/0"
    full = client.get(assets[0]["url"])
    assert full.status_code == 200 and full.content == WAV
    assert full.headers["content-type"].startswith("audio/wav")
    partial = client.get(assets[0]["url"], headers={"Range": "bytes=0-3"})
    assert partial.status_code == 206 and partial.content == b"RIFF"
    assert partial.headers["content-range"] == f"bytes 0-3/{len(WAV)}"
    missing = client.get(assets[1]["url"])
    assert missing.status_code == 404 and "not found on this machine" in missing.text
    assert client.get(assets[2]["url"]).status_code == 403
    assert client.get(f"/v1/items/{item.id}/assets/9").status_code == 404
    assert client.get(f"/v1/items/{item.id}/assets/-1").status_code == 404
    assert client.get("/v1/items/nope/assets/0").status_code == 404


def test_server_denies_path_assets_without_roots(store, library):
    item = item_with_paths(store, str(library / "Pack" / "kick 01.wav"))
    client = TestClient(create_app(store))
    [asset] = client.get(f"/v1/items/{item.id}").json()["assets"]
    assert asset["availability"] == "not_allowed"
    response = client.get(asset["url"])
    assert response.status_code == 403 and "--asset-root" in response.text


def test_item_asset_endpoint_requires_api_key_and_serves_blobs(store, library):
    item = hil.submit({"a": str(library / "Pack" / "kick 01.wav")}, "o", assets="input.a")
    client = TestClient(create_app(store, api_key="k"))
    url = f"/v1/items/{item.id}/assets/0"
    assert client.get(url).status_code == 401
    assert client.get(url + "?api_key=k").content == WAV
    detail = client.get(f"/v1/items/{item.id}", headers={"X-API-Key": "k"}).json()
    assert detail["assets"][0]["kind"] == "blob"
    assert detail["assets"][0]["availability"] == "available"
    assert detail["assets"][0]["url"].startswith("/v1/blobs/")


def test_http_create_with_asset_paths(store, library):
    client = TestClient(create_app(store, asset_roots=[str(library)]))
    created = client.post(
        "/v1/items",
        json={
            "project": "p",
            "input": "x",
            "output": "y",
            "asset_paths": [
                {"path": str(library / "Pack" / "kick 01.wav")},
                {"path": "/Volumes/X/a.aif", "role": "output", "mime": "audio/aiff"},
            ],
        },
    )
    assert created.status_code == 201
    assets = created.json()["assets"]
    assert [a["availability"] for a in assets] == ["available", "not_allowed"]
    assert assets[1]["mime"] == "audio/aiff" and assets[1]["role"] == "output"
    bad = client.post(
        "/v1/items", json={"project": "p", "input": 1, "output": 1, "asset_paths": [{"path": ""}]}
    )
    assert bad.status_code == 422


def test_exports_keep_path_references(store, library, tmp_path):
    path = "/Volumes/Extreme SSD/Splice/a.wav"
    item = hil.submit({"audio": path}, {"family": "drums"}, path_assets="input.audio")
    TestClient(create_app(store)).post(f"/v1/items/{item.id}/review", json={"action": "approve"})
    [record] = export_items(store, "p")
    assert record["input"]["audio"] == {
        "$asset_path": path,
        "kind": "path",
        "sha256": None,
        "mime": "audio/wav",
        "filename": "a.wav",
        "path": path,
        "url": None,
    }
    [evalset] = export_items(store, "p", fmt="eval-set")
    assert evalset["input"]["audio"]["kind"] == "path"
    [ft] = export_items(store, "p", fmt="openai-ft")
    assert "$asset_path" not in ft["messages"][0]["content"][0]["text"]
    out = tmp_path / "x.parquet"
    assert export_hf(store, "p", out) == 1  # does not try to read the missing file
    assert pq.read_table(out).to_pylist()[0]["audio"] == []


def test_cli_serve_passes_asset_roots(monkeypatch, tmp_path, library):
    captured = {}

    def fake_run(application, host, port):
        captured["app"] = application

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    import hil_eval.server.app as server_app

    original = server_app.create_app

    def spy(*args, **kwargs):
        captured["roots"] = kwargs.get("asset_roots")
        return original(*args, **kwargs)

    monkeypatch.setattr(server_app, "create_app", spy)
    result = CliRunner().invoke(
        cli,
        ["--db", str(tmp_path / "c.db"), "serve", "--asset-root", str(library)],
    )
    assert result.exit_code == 0, result.output
    assert captured["roots"] == [str(library)]
    result = CliRunner().invoke(
        cli,
        ["--db", str(tmp_path / "c.db"), "serve"],
        env={"HIL_ASSET_ROOTS": f"{library}{os.pathsep}{tmp_path / 'nowhere'}"},
    )
    assert result.exit_code == 0, result.output
    assert captured["roots"] == [str(library), str(tmp_path / "nowhere")]
    assert "not a directory" in result.output
