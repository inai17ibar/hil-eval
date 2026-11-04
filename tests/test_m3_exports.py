import builtins
import json

import pyarrow.parquet as pq
import pytest
from test_m3_contract import item, review
from typer.testing import CliRunner

from hil_eval.blobs import store_image
from hil_eval.cli import app
from hil_eval.exporters import export_hf, export_items
from hil_eval.store import SQLiteStore
from hil_eval.types import ReviewAction


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(tmp_path / "db.sqlite")


@pytest.mark.parametrize("fmt", ["jsonl", "eval-set", "openai-ft"])
def test_choice_metadata_and_expected(store, fmt):
    i = item(store, options=["a", "b"])
    review(
        store,
        i,
        action=ReviewAction.choose,
        choice="b",
        reason_codes=["quality"],
        reason_text="better",
    )
    (row,) = export_items(store, "p", fmt=fmt)
    metadata = row if fmt == "jsonl" else row["metadata"]
    assert metadata["reason_codes"] == ["quality"]
    assert metadata["reason_text"] == "better"
    assert metadata["choice"] == "b"
    if fmt == "jsonl":
        assert row["corrected_output"] == "b"
    elif fmt == "eval-set":
        assert row["expected"] == "b"
    else:
        assert row["messages"][1]["content"] == "b"
    review(store, i, action=ReviewAction.reject, reason_text="latest")
    latest = list(export_items(store, "p", fmt=fmt, statuses=["rejected"]))
    if fmt == "openai-ft":
        assert latest == []
    else:
        (row,) = latest
        assert (row if fmt == "jsonl" else row["metadata"])["reason_text"] == "latest"


def test_parquet_images_and_json_columns(store, tmp_path):
    data = b"\x89PNG\r\n\x1a\nexact-image-bytes"
    asset = store_image(data, store)
    i = item(store, assets=[asset], options=[{"answer": 2}])
    review(store, i, action=ReviewAction.choose, choice={"answer": 2}, reason_codes=["fix"])
    path = tmp_path / "out.parquet"
    export_hf(store, "p", path)
    table = pq.read_table(path)
    assert table.num_rows == 1
    (row,) = table.to_pylist()
    assert row["images"] == [data]
    assert row["reason_codes"] == ["fix"]
    assert row["choice"] == {"answer": 2}
    assert row["input"] == {"x": 1}
    export_hf(store, "p", path, include_images=False)
    assert pq.read_table(path).to_pylist()[0]["images"] == []
    store.get_blob_path(asset.sha256).unlink()
    with pytest.raises(FileNotFoundError):
        export_hf(store, "p", path)


def test_empty_parquet_and_cli(store, tmp_path):
    path = tmp_path / "empty.parquet"
    result = CliRunner().invoke(
        app,
        [
            "--db",
            str(store.path),
            "export",
            "--project",
            "p",
            "--format",
            "hf",
            "--output",
            str(path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert pq.read_table(path).num_rows == 0
    assert "images" in pq.read_table(path).column_names
    result = CliRunner().invoke(
        app, ["--db", str(store.path), "export", "--project", "p", "--format", "hf"]
    )
    assert result.exit_code != 0 and "--output" in result.output


def test_missing_pyarrow_install_hint(store, tmp_path, monkeypatch):
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "pyarrow":
            raise ImportError("missing")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    with pytest.raises(ImportError, match=r"hil-eval\[hf\]"):
        export_hf(store, "p", tmp_path / "x.parquet")
    result = CliRunner().invoke(
        app,
        [
            "--db",
            str(store.path),
            "export",
            "--project",
            "p",
            "--format",
            "hf",
            "--output",
            str(tmp_path / "x.parquet"),
        ],
    )
    assert result.exit_code != 0 and "hil-eval[hf]" in result.output


def test_parquet_mixed_json_fallback_is_documented(store, tmp_path):
    review(store, item(store, input=1, output={}))
    review(store, item(store, input={"nested": []}, output={}))
    path = tmp_path / "mixed.parquet"
    export_hf(store, "p", path)
    table = pq.read_table(path)
    encoded = json.loads(table.schema.metadata[b"hil_eval.json_columns"])
    assert "input" in encoded and "output" in encoded
    rows = table.to_pylist()
    assert [json.loads(row["input"]) for row in rows] == [1, {"nested": []}]
    assert [json.loads(row["output"]) for row in rows] == [{}, {}]


def test_parquet_preserves_absent_keys_nested_and_top_level(store, tmp_path):
    values = [{"a": 1, "nested": [{"x": 1}]}, {"b": 2, "nested": [{"y": 2}]}]
    for value in values:
        review(store, item(store, input=value))
    path = tmp_path / "keys.parquet"
    export_hf(store, "p", path)
    table = pq.read_table(path)
    assert "input" in json.loads(table.schema.metadata[b"hil_eval.json_columns"])
    assert [json.loads(row["input"]) for row in table.to_pylist()] == values
