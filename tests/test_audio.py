import base64

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from test_m3_contract import item, review

import hil_eval as hil
from hil_eval import context
from hil_eval.blobs import detect_mime, sniff_mime, store_asset
from hil_eval.exporters import export_hf
from hil_eval.server import create_app
from hil_eval.store import SQLiteStore

AUDIO = [
    (b"RIFF\x24\x00\x00\x00WAVEdata", "audio/wav", "wav"),
    (b"ID3audio", "audio/mpeg", "mp3"),
    (b"\xff\xfb\x90\x00audio", "audio/mpeg", "mp3"),
    (b"fLaCaudio", "audio/flac", "flac"),
    (b"OggSaudio", "audio/ogg", "ogg"),
    (b"FORM\x00\x00\x00\x00AIFFaudio", "audio/aiff", "aiff"),
]


@pytest.mark.parametrize("data,mime,extension", AUDIO)
def test_audio_end_to_end(tmp_path, monkeypatch, data, mime, extension):
    store = SQLiteStore(tmp_path / "db")
    monkeypatch.setattr(context, "_default_client", context.Client("p", store))
    path = tmp_path / f"sound.{extension}"
    path.write_bytes(data)
    assert sniff_mime(data) == mime
    assert detect_mime(b"unknown", str(path)).startswith("audio/")
    assert store_asset(path, store).mime == mime
    misleading = tmp_path / "sound.png"
    misleading.write_bytes(data)
    assert store_asset(misleading, store).mime == mime

    @hil.review(judge=lambda i, o: 0.1, audit_rate=0)
    def transcribe(sound):
        return "transcript"

    for value in (data, path):
        assert transcribe(value) == "transcript"
    for saved in hil.list_items():
        assert saved.assets[0].mime == mime
        assert saved.input["sound"] == {"$asset": saved.assets[0].sha256}

    @hil.review(judge=lambda i, o: 0.1, images=["sound"], audit_rate=0)
    def explicit(sound):
        return "explicit"

    assert explicit(str(path)) == "explicit"
    assert any(i.output == "explicit" and i.assets for i in hil.list_items())
    client = TestClient(create_app(store))
    response = client.post(
        "/v1/items",
        json={
            "project": "p",
            "input": "sound",
            "output": "text",
            "assets": [{"data_base64": base64.b64encode(data).decode(), "video_ts": 0.5}],
        },
    )
    assert response.status_code == 201
    asset = response.json()["assets"][0]
    assert asset["mime"] == mime
    assert asset["video_ts"] == 0.5
    blob = client.get(asset["url"])
    assert blob.status_code == 200
    assert blob.headers["content-type"] == mime
    assert blob.content == data
    partial = client.get(asset["url"], headers={"Range": "bytes=0-3"})
    assert partial.status_code == 206
    assert partial.content == data[:4]
    assert partial.headers["content-range"] == f"bytes 0-3/{len(data)}"


def test_audio_hf_export(tmp_path):
    store = SQLiteStore(tmp_path / "db")
    data = AUDIO[0][0]
    asset = store_asset(data, store)
    saved = item(store, assets=[asset])
    review(store, saved)
    path = tmp_path / "audio.parquet"
    export_hf(store, "p", path)
    row = pq.read_table(path).to_pylist()[0]
    assert row["audio"] == [data]
    assert row["images"] == []
    export_hf(store, "p", path, include_images=False)
    assert pq.read_table(path).to_pylist()[0]["audio"] == []
    store.get_blob_path(asset.sha256).unlink()
    with pytest.raises(FileNotFoundError):
        export_hf(store, "p", path)


@pytest.mark.parametrize("data", [b"", b"\xff", b"RIFF", b"RIFF1234WEBP", b"FORM1234"])
def test_non_audio_headers(data):
    assert not sniff_mime(data).startswith("audio/")
