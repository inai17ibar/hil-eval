import json

import pyarrow.parquet as pq
import pytest
from test_m3_contract import review
from test_v011_assets import WAV, wavs
from test_v011_assets import store as store  # noqa: F401  (fixture)

import hil_eval as hil
from hil_eval import exporters


def test_jsonl_export_resolves_assets_and_export_to(store, tmp_path):
    paths = wavs(tmp_path, 2)
    item = hil.submit(
        {"q": "kick"}, {"results": [{"path": p} for p in paths]}, assets=["output.results[*].path"]
    )
    review(store, item)
    [record] = list(exporters.export_items(store, "p"))
    ref = record["output"]["results"][1]["path"]
    asset = sorted(item.assets, key=lambda a: a.order)[1]
    assert ref == {
        "$asset": asset.sha256,
        "sha256": asset.sha256,
        "filename": "s1.wav",
        "mime": "audio/wav",
        "path": str(store.get_blob_path(asset.sha256)),
        "url": f"/v1/blobs/{asset.sha256}",
    }

    target = tmp_path / "out.jsonl"
    assert hil.export_to(target) == 1
    assert json.loads(target.read_text())["id"] == item.id
    assert hil.export_to(tmp_path / "eval.jsonl", format="eval-set") == 1
    parquet = tmp_path / "out.parquet"
    assert hil.export_to(parquet, format="hf") == 1
    assert pq.read_table(parquet).column("audio").to_pylist()[0] == [
        WAV + b"\x00",
        WAV + b"\x01",
    ]
    with pytest.raises(ValueError, match="unknown export format"):
        hil.export_to(target, format="csv")
    with pytest.raises(ValueError, match="export_to"):
        list(exporters.export_items(store, "p", fmt="hf"))


def test_export_to_hf_missing_pyarrow(store, tmp_path, monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *args, **kwargs):
        if name.startswith("pyarrow"):
            raise ImportError("no pyarrow")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(ImportError, match=r"hil-eval\[hf\]"):
        hil.export_to(tmp_path / "x.parquet", format="hf")
    assert not (tmp_path / "x.parquet").exists()
