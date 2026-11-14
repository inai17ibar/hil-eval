"""v0.2 structured label fields: schema, review API, exports."""

import json

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

import hil_eval as hil
from hil_eval import context
from hil_eval.exporters import export_hf, export_items
from hil_eval.fields import (
    FieldError,
    apply_values,
    check_values,
    field_diff,
    normalize_fields,
    proposed_values,
)
from hil_eval.server import create_app
from hil_eval.store import SQLiteStore

SCHEMA = [
    {"name": "family", "choices": ["drums", "keys", "bass"]},
    {
        "name": "leaf",
        "depends_on": "family",
        "choices": {"drums": ["kick", "snare"], "keys": ["piano"], "bass": ["808"]},
    },
    {"name": "type", "choices": ["loop", "one_shot"], "nullable": False},
    {"name": "bpm", "kind": "number", "min": 40, "max": 240, "integer": True},
    {"name": "key", "kind": "text", "path": "meta.key"},
]
OUTPUT = {
    "family": "drums",
    "leaf": "kick",
    "type": "loop",
    "bpm": 100,
    "meta": {"key": "D minor"},
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "db")
    monkeypatch.setattr(context, "_default_client", context.Client("p", store))
    return store


def test_normalize_accepts_dicts_and_field_objects():
    fields = normalize_fields([hil.Field("family", choices=["a"]), {"name": "n", "kind": "number"}])
    assert fields[0]["name"] == "family" and fields[0]["kind"] == "enum"
    assert fields[1]["kind"] == "number"
    assert normalize_fields(None) is None


@pytest.mark.parametrize(
    "bad",
    [
        "nope",
        [{"name": ""}],
        [{"name": "a", "choices": ["x"]}, {"name": "a", "choices": ["y"]}],
        [{"name": "a", "kind": "date"}],
        [{"name": "a", "choices": "x"}],
        [{"name": "a"}],
        [{"name": "leaf", "depends_on": "family", "choices": {"x": ["y"]}}],
        [{"name": "f", "choices": ["x"]}, {"name": "l", "depends_on": "f", "choices": ["y"]}],
        [{"name": "n", "kind": "number", "min": 5, "max": 1}],
        [{"name": "n", "kind": "number", "min": float("nan")}],
        [{"name": "t", "kind": "text", "choices": ["x"]}],
        [{"name": "a", "choices": ["x"], "bogus": 1}],
        [{"name": "a", "choices": ["x"], "min": 1}],
        [{"name": "n", "kind": "number", "multipliers": [0]}],
        [{"name": "a", "kind": "text"}, {"name": "b", "kind": "text", "path": "a"}],
        [
            {"name": "m", "kind": "text", "path": "meta"},
            {"name": "k", "kind": "text", "path": "meta.k"},
        ],
        [{"name": "a", "kind": "text", "path": "x..y"}],
    ],
)
def test_normalize_rejects_invalid_schemas(bad):
    with pytest.raises(FieldError):
        normalize_fields(bad)


def test_proposed_values_follow_paths_and_missing_is_none():
    fields = normalize_fields(SCHEMA)
    assert proposed_values(fields, OUTPUT) == {
        "family": "drums",
        "leaf": "kick",
        "type": "loop",
        "bpm": 100,
        "key": "D minor",
    }
    assert proposed_values(fields, "text")["family"] is None
    assert proposed_values([], OUTPUT) is None


def test_check_values_diffs_only_changes_and_validates_them():
    fields = normalize_fields(SCHEMA)
    corrected, diff = check_values(fields, OUTPUT, {})
    assert diff == {} and corrected["bpm"] == 100
    corrected, diff = check_values(fields, OUTPUT, {"family": "keys", "leaf": "piano", "bpm": 50})
    assert diff == {
        "family": {"from": "drums", "to": "keys"},
        "leaf": {"from": "kick", "to": "piano"},
        "bpm": {"from": 100, "to": 50},
    }
    assert corrected["key"] == "D minor"
    # 100.0 == 100 is not a change; True is not 1
    assert check_values(fields, OUTPUT, {"bpm": 100.0})[1] == {}
    for values, message in [
        ({"family": "guitar"}, "family"),
        ({"family": "keys"}, "leaf"),  # parent changed: child must fit the new branch
        ({"leaf": "piano"}, "leaf"),
        ({"bpm": 300}, "<= 240"),
        ({"bpm": 99.5}, "integer"),
        ({"bpm": "fast"}, "number"),
        ({"type": None}, "required"),
        ({"key": 3}, "string"),
        ({"nope": 1}, "unknown"),
    ]:
        with pytest.raises(FieldError, match=message):
            check_values(fields, OUTPUT, values)
    with pytest.raises(FieldError):
        check_values(fields, OUTPUT, ["family"])


def test_unchanged_out_of_schema_values_can_be_confirmed():
    fields = normalize_fields(SCHEMA)
    odd = {**OUTPUT, "leaf": "full"}
    assert check_values(fields, odd, {})[1] == {}
    custom = normalize_fields([{"name": "leaf", "choices": ["a"], "allow_custom": True}])
    assert check_values(custom, {"leaf": "a"}, {"leaf": "zz"})[1] == {
        "leaf": {"from": "a", "to": "zz"}
    }


def test_apply_values_writes_nested_paths_without_mutating():
    fields = normalize_fields(SCHEMA)
    updated = apply_values(fields, OUTPUT, {"key": "C major", "bpm": 120})
    assert updated["meta"]["key"] == "C major" and updated["bpm"] == 120
    assert OUTPUT["meta"]["key"] == "D minor"
    with pytest.raises(FieldError, match="object"):
        apply_values(fields, "text", {"bpm": 1})
    assert apply_values([{"name": "x", "path": "a.b"}], {}, {"x": 1}) == {"a": {"b": 1}}
    with pytest.raises(FieldError, match="non-object"):
        apply_values([{"name": "x", "path": "a.b"}], {"a": "keep"}, {"x": 1})
    assert field_diff({"a": True}, {"a": 1}) == {"a": {"from": True, "to": 1}}


def test_project_item_and_decorator_schemas(store):
    hil.set_fields(SCHEMA[:1])
    assert store.get_project("p").fields[0]["name"] == "family"
    item = hil.submit("in", OUTPUT, fields=SCHEMA)
    assert [f["name"] for f in item.fields] == [f["name"] for f in SCHEMA]
    assert hil.submit("in", OUTPUT, fields=[{"name": ""}]) is None  # fail-open

    @hil.review(judge=lambda a, o: 0.1, audit_rate=0, fields=SCHEMA[:2])
    def label(path):
        return OUTPUT

    label("x.wav")
    [queued] = [i for i in store.list_items("p") if i.fn_name and "label" in i.fn_name]
    assert [f["name"] for f in queued.fields] == ["family", "leaf"]
    with pytest.raises(FieldError):
        hil.review(judge=lambda a, o: 1, fields=[{"name": "x", "kind": "?"}])
    hil.set_fields(None)
    assert store.get_project("p").fields is None


def test_init_sets_project_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "_default_client", None)
    hil.init("labels", f"sqlite:///{tmp_path}/h.db", fields=SCHEMA)
    assert context.get_client().store.get_project("labels").fields[1]["depends_on"] == "family"


def make_client(store):
    return TestClient(create_app(store))


def test_api_review_with_fields_end_to_end(store):
    client = make_client(store)
    assert client.put("/v1/projects/p/fields", json={"fields": SCHEMA}).status_code == 200
    assert client.get("/v1/projects/p/fields").json()["fields"][4]["path"] == "meta.key"
    assert client.get("/v1/projects/missing/fields").status_code == 404
    bad = client.put("/v1/projects/p/fields", json={"fields": [{"name": "a", "kind": "x"}]})
    assert bad.status_code == 422
    assert client.get("/v1/projects").json()[0]["fields"][0]["name"] == "family"

    created = client.post(
        "/v1/items", json={"project": "p", "input": "a", "output": OUTPUT, "external_id": "f-1"}
    ).json()
    assert created["fields"][0]["name"] == "family"
    assert created["proposed_fields"]["key"] == "D minor"
    assert created["external_id"] == "f-1"

    url = f"/v1/items/{created['id']}/review"
    assert client.post(url, json={"action": "approve", "fields": {"bpm": 50}}).status_code == 422
    assert client.post(url, json={"action": "edit", "fields": {"bpm": 999}}).status_code == 422
    assert client.post(url, json={"action": "reject", "fields": {}}).status_code == 422
    response = client.post(
        url, json={"action": "edit", "fields": {"bpm": 50, "key": "C major"}, "note": "half"}
    )
    assert response.status_code == 200 and response.json()["status"] == "corrected"
    [review] = client.get(f"/v1/items/{created['id']}").json()["reviews"]
    assert review["field_diff"] == {
        "bpm": {"from": 100, "to": 50},
        "key": {"from": "D minor", "to": "C major"},
    }
    assert review["fields"] == {
        "family": "drums",
        "leaf": "kick",
        "type": "loop",
        "bpm": 50,
        "key": "C major",
    }
    assert review["corrected_output"]["meta"]["key"] == "C major"
    assert review["corrected_output"]["bpm"] == 50

    [record] = export_items(store, "p")
    assert record["external_id"] == "f-1"
    assert record["field_diff"]["bpm"] == {"from": 100, "to": 50}
    assert record["corrected_output"]["bpm"] == 50
    [evalset] = export_items(store, "p", fmt="eval-set")
    assert evalset["metadata"]["fields"]["bpm"] == 50
    [ft] = export_items(store, "p", fmt="openai-ft")
    assert ft["metadata"]["field_diff"]["key"]["to"] == "C major"


def test_edit_without_changes_is_approve_and_choose_only_confirms_fields(store):
    client = make_client(store)
    item = client.post(
        "/v1/items",
        json={
            "project": "p",
            "input": "a",
            "output": OUTPUT,
            "fields": SCHEMA,
            "options": ["correct", "unsure"],
        },
    ).json()
    url = f"/v1/items/{item['id']}/review"
    assert client.post(url, json={"action": "edit", "fields": {}}).json()["status"] == "approved"
    changed = client.post(
        url, json={"action": "choose", "choice": "unsure", "fields": {"family": None}}
    )
    assert changed.status_code == 422  # a choice cannot carry corrections
    chosen = client.post(url, json={"action": "choose", "choice": "unsure", "fields": {}})
    assert chosen.status_code == 200
    review = client.get(f"/v1/items/{item['id']}").json()["reviews"][-1]
    assert review["choice"] == "unsure"
    assert review["field_diff"] == {} and review["fields"]["family"] == "drums"


def test_fields_rejected_without_schema_and_legacy_edit_unchanged(store):
    client = make_client(store)
    item = client.post("/v1/items", json={"project": "p", "input": "a", "output": {"x": 1}}).json()
    url = f"/v1/items/{item['id']}/review"
    assert client.post(url, json={"action": "edit", "fields": {"x": 2}}).status_code == 422
    assert client.post(url, json={"action": "edit"}).status_code == 422
    ok = client.post(url, json={"action": "edit", "corrected_output": {"x": 2}})
    assert ok.status_code == 200
    review = client.get(f"/v1/items/{item['id']}").json()["reviews"][0]
    assert review["fields"] is None and review["field_diff"] is None
    bad = client.post(
        "/v1/items", json={"project": "p", "input": 1, "output": 1, "fields": [{"name": ""}]}
    )
    assert bad.status_code == 422


def test_hf_export_has_field_columns(store, tmp_path):
    client = make_client(store)
    item = client.post(
        "/v1/items", json={"project": "p", "input": "a", "output": OUTPUT, "fields": SCHEMA}
    ).json()
    client.post(f"/v1/items/{item['id']}/review", json={"action": "edit", "fields": {"bpm": 90}})
    path = tmp_path / "out.parquet"
    assert export_hf(store, "p", path) == 1
    table = pq.read_table(path)
    meta = json.loads(table.schema.metadata.get(b"hil_eval.json_columns", b"[]"))
    row = table.to_pylist()[0]
    diff = json.loads(row["field_diff"]) if "field_diff" in meta else row["field_diff"]
    assert diff["bpm"]["to"] == 90
    empty = tmp_path / "empty.parquet"
    assert export_hf(store, "nobody", empty) == 0
    assert {"fields", "field_diff", "external_id"} <= set(pq.read_table(empty).column_names)


def test_fields_win_over_supplied_output_and_bad_shapes_are_rejected(store):
    client = make_client(store)
    item = client.post(
        "/v1/items", json={"project": "p", "input": "a", "output": OUTPUT, "fields": SCHEMA}
    ).json()
    url = f"/v1/items/{item['id']}/review"
    body = {"action": "edit", "fields": {"bpm": 50}, "corrected_output": {"bpm": 100, "x": 1}}
    assert client.post(url, json=body).status_code == 200
    review = client.get(f"/v1/items/{item['id']}").json()["reviews"][-1]
    assert review["corrected_output"]["bpm"] == 50 and review["corrected_output"]["x"] == 1
    assert review["corrected_output"]["family"] == "drums"
    for output in ["plain text", [1, 2], None, {"meta": "flat"}]:
        scalar = client.post(
            "/v1/items", json={"project": "p", "input": "a", "output": output, "fields": SCHEMA}
        ).json()
        url = f"/v1/items/{scalar['id']}/review"
        # confirming is fine; a correction that cannot be written back is rejected
        assert client.post(url, json={"action": "edit", "fields": {}}).status_code == 200
        bad = client.post(url, json={"action": "edit", "fields": {"key": "C major"}})
        assert bad.status_code == 422, output
