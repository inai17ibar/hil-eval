"""v0.2 `hil-eval import`: bulk, idempotent JSONL enqueue."""

import json

from typer.testing import CliRunner

from hil_eval.cli import app
from hil_eval.importer import import_jsonl
from hil_eval.store import SQLiteStore
from hil_eval.types import AssetKind, RoutedBy

SCHEMA = [{"name": "family", "choices": ["drums", "keys"]}, {"name": "bpm", "kind": "number"}]


def record(file_id, family="drums", **extra):
    return {
        "project": "soundai-labels",
        "name": "label_review",
        "file_id": file_id,
        "input": {"audio": f"/Volumes/Extreme SSD/Splice/{file_id}.wav"},
        "output": {"family": family, "bpm": 100},
        "options": ["correct", "unsure"],
        "confidence": 0.15,
        "tags": {"reason": "conflict", "n": 3},
        **extra,
    }


def write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n", encoding="utf-8")
    return path


def test_import_is_idempotent_by_id_field(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    source = write(tmp_path / "in.jsonl", [record("a"), record("b")])
    result = import_jsonl(store, source, id_field="file_id", path_assets=["input.audio"])
    assert (result.imported, result.duplicates, result.failed) == (2, 0, 0)
    assert result.ignored_keys == {}
    items = store.list_items("soundai-labels")
    assert {i.external_id for i in items} == {"a", "b"}
    item = items[0]
    assert item.fn_name == "label_review" and item.options == ["correct", "unsure"]
    assert item.confidence == 0.15 and item.routed_by == RoutedBy.manual
    assert item.tags == {"reason": "conflict", "n": "3"}
    assert item.assets[0].kind == AssetKind.path
    # Changed labels for the same file id are still duplicates.
    write(source, [record("a", family="keys"), record("b"), record("c")])
    again = import_jsonl(store, source, id_field="file_id", path_assets=["input.audio"])
    assert (again.imported, again.duplicates) == (1, 2)
    assert len(store.list_items("soundai-labels")) == 3


def test_import_without_ids_dedupes_by_content(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    rows = [record("a"), record("a"), record("b")]
    source = write(tmp_path / "in.jsonl", rows)
    first = import_jsonl(store, source)
    assert (first.imported, first.duplicates) == (2, 1)
    assert first.ignored_keys == {"file_id": 3}
    assert import_jsonl(store, source).imported == 0
    # the same content with extra path selectors is a different import
    assert import_jsonl(store, source, path_assets=["input.audio"]).imported == 2


def test_import_reports_bad_lines_and_dry_run(tmp_path):
    store = SQLiteStore(tmp_path / "h.db")
    rows = [
        "not json",
        json.dumps([1]),
        json.dumps({"input": 1, "output": 2}),
        json.dumps({"project": "p", "input": 1}),
        json.dumps({"project": "p", "input": 1, "output": 2, "confidence": 2}),
        json.dumps({"project": "p", "input": 1, "output": 2, "tags": [1]}),
        json.dumps({"project": "p", "input": 1, "output": 2, "fields": [{"name": ""}]}),
        json.dumps({"project": "p", "input": 1, "output": 2, "external_id": 5}),
        json.dumps({"project": "p", "input": 1, "output": 2, "assets": 5}),
        json.dumps({"project": "p", "input": 1, "output": 2, "fields": SCHEMA}),
    ]
    source = tmp_path / "in.jsonl"
    source.write_text("\n".join(rows), encoding="utf-8")
    dry = import_jsonl(store, source, dry_run=True)
    assert (dry.imported, dry.failed) == (1, 9)
    assert store.list_items("p") == []
    assert dry.errors[0].startswith("line 1:")
    result = import_jsonl(store, source)
    assert result.imported == 1
    [item] = store.list_items("p")
    assert item.fields[0]["name"] == "family"
    missing = import_jsonl(store, write(tmp_path / "m.jsonl", [record("x")]), id_field="nope")
    assert missing.failed == 1 and "nope" in missing.errors[0]


def test_cli_import_counts_fields_and_exit_codes(tmp_path):
    db = str(tmp_path / "h.db")
    rows = [{k: v for k, v in record(i).items() if k != "project"} for i in ("a", "b")]
    source = write(tmp_path / "in.jsonl", rows)
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(SCHEMA))
    runner = CliRunner()
    args = ["--db", db, "import", str(source), "--project", "labels", "--id-field", "file_id"]
    result = runner.invoke(app, [*args, "--path-assets", "input.audio", "--fields", str(schema)])
    assert result.exit_code == 0, result.output
    assert "imported 2, skipped 0 duplicate(s), failed 0 (of 2 records)" in result.output
    store = SQLiteStore(db)
    assert store.get_project("labels").fields[1]["name"] == "bpm"
    assert store.list_items("labels")[0].assets[0].kind == AssetKind.path
    result = runner.invoke(app, args)
    assert "imported 0, skipped 2 duplicate(s)" in result.output
    result = runner.invoke(app, ["--db", db, "import", str(source)])
    assert result.exit_code == 1
    assert "failed 2" in result.output and "Ignored unknown keys: file_id (2)" in result.output
    no_project = runner.invoke(app, ["--db", db, "import", str(source), "--fields", str(schema)])
    assert no_project.exit_code != 0
    dry = runner.invoke(app, [*args[:-2], "--dry-run", "--id-field", "x"])
    assert "would import 0" in dry.output


def test_cli_fields_command(tmp_path):
    db = str(tmp_path / "h.db")
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(SCHEMA))
    runner = CliRunner()
    result = runner.invoke(app, ["--db", db, "fields", "--project", "p", "--set", str(schema)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["name"] == "family"
    shown = runner.invoke(app, ["--db", db, "fields", "--project", "p"])
    assert json.loads(shown.output)[1]["kind"] == "number"
    cleared = runner.invoke(app, ["--db", db, "fields", "--project", "p", "--clear"])
    assert json.loads(cleared.output) is None
    schema.write_text(json.dumps([{"name": "a", "kind": "?"}]))
    bad = runner.invoke(app, ["--db", db, "fields", "--project", "p", "--set", str(schema)])
    assert bad.exit_code != 0
    unknown = runner.invoke(app, ["--db", db, "fields", "--project", "zzz"])
    assert unknown.exit_code != 0
