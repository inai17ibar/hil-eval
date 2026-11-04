import pytest

import hil_eval as hil
from hil_eval import context
from hil_eval.selectors import apply_path, parse_selector
from hil_eval.store import SQLiteStore

WAV = b"RIFF\x24\x00\x00\x00WAVEdata"


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "db")
    monkeypatch.setattr(context, "_default_client", context.Client("p", store))
    return store


def wavs(tmp_path, n=3):
    paths = []
    for i in range(n):
        path = tmp_path / f"s{i}.wav"
        path.write_bytes(WAV + bytes([i]))
        paths.append(str(path))
    return paths


@pytest.mark.parametrize(
    "text,expected",
    [
        ("audio", ("audio", ())),
        ("output.audio", ("output", ("audio",))),
        ("output[*].path", ("output", ("*", "path"))),
        ("output.results[*].path", ("output", ("results", "*", "path"))),
        ("output[0]", ("output", (0,))),
    ],
)
def test_parse_selector(text, expected):
    assert parse_selector(text) == expected


@pytest.mark.parametrize("text", ["", "a..b", "a.", "[*]", " a", "a[x]"])
def test_parse_selector_rejects(text):
    with pytest.raises(ValueError):
        parse_selector(text)


def test_apply_path_skips_missing():
    value = {"results": [{"path": "a"}, {"other": 1}]}
    assert apply_path(value, ("results", "*", "path"), str.upper) == {
        "results": [{"path": "A"}, {"other": 1}]
    }
    assert apply_path(value, ("nope", "*"), str.upper) == value


def test_output_assets_with_order_and_name(store, tmp_path):
    paths = wavs(tmp_path)

    def make():
        @hil.review(
            judge=lambda a, o: 0.1,
            audit_rate=0,
            assets=["output.results[*].path"],
            name="search_text",
        )
        def search(query):
            return {"results": [{"rank": i + 1, "path": p} for i, p in enumerate(paths)]}

        return search

    out = make()("kick")
    assert isinstance(out["results"][0]["path"], str)  # result unchanged
    [item] = hil.list_items()
    assert item.fn_name == "search_text"
    output_assets = sorted(item.assets, key=lambda a: a.order)
    assert [a.order for a in output_assets] == [0, 1, 2]
    for asset, row, path in zip(output_assets, item.output["results"], paths, strict=True):
        assert row["path"] == {"$asset": asset.sha256, "filename": path.rsplit("/", 1)[-1]}
        assert asset.mime == "audio/wav" and asset.role.value == "output"


def test_images_alias_and_list_output(store, tmp_path):
    paths = wavs(tmp_path, 2)

    @hil.review(judge=lambda a, o: 0.1, audit_rate=0, images=["sound"], assets=["output[*]"])
    def fn(sound):
        return paths

    fn(paths[0])
    [item] = hil.list_items()
    assert item.input["sound"]["$asset"] and all("$asset" in v for v in item.output)
    roles = [(a.role.value, a.order) for a in item.assets]
    assert roles == [("input", 0), ("output", 0), ("output", 1)]


def test_missing_asset_file_keeps_item(store, tmp_path):
    @hil.review(judge=lambda a, o: 0.1, audit_rate=0, assets=["output.path"])
    def fn():
        return {"path": str(tmp_path / "missing.wav")}

    fn()
    [item] = hil.list_items()
    assert "$asset_error" in item.output["path"]


def test_decorator_validation():
    with pytest.raises(ValueError, match="Unknown"):
        hil.review(judge=lambda a, o: 1, assets=["nope.x"])(lambda x: x)
    with pytest.raises(ValueError, match="ambiguous"):
        hil.review(judge=lambda a, o: 1, assets=["output"])(lambda output: output)
    with pytest.raises(ValueError, match="route"):
        hil.review(judge=lambda a, o: 1, route="some")
    with pytest.raises(ValueError):
        hil.review(judge=lambda a, o: 1, assets=["bad..sel"])


def test_route_all_queues_every_call(store):
    @hil.review(judge=lambda a, o: 1.0, threshold=0.5, route="all")
    def fn(x):
        return x

    for i in range(3):
        fn(i)
    items = hil.list_items()
    assert len(items) == 3 and {i.routed_by.value for i in items} == {"audit"}


def test_submit_assets_and_name(store, tmp_path):
    paths = wavs(tmp_path, 2)
    item = hil.submit(
        {"query": "kick"},
        [{"path": p} for p in paths],
        assets=["output[*].path"],
        name="manual",
    )
    assert item.fn_name == "manual"
    assert [a.order for a in item.assets] == [0, 1]


def test_single_string_selector(store, tmp_path):
    [path] = wavs(tmp_path, 1)

    @hil.review(judge=lambda a, o: 0.1, audit_rate=0, assets="output.path")
    def fn():
        return {"path": path}

    fn()
    [item] = hil.list_items()
    assert item.output["path"]["filename"] == "s0.wav"
