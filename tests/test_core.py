import asyncio
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

import hil_eval as hil
from hil_eval import context
from hil_eval.blobs import store_image
from hil_eval.store.sqlite import SQLiteStore


@pytest.fixture(autouse=True)
def initialized(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "_default_client", None)
    monkeypatch.setattr(context, "_warned_missing", False)
    hil.init("test", f"sqlite:///{tmp_path / 'db.sqlite'}", audit_rate=0)
    return context.get_client()


@pytest.mark.parametrize(
    "confidence,audit,route",
    [
        (0.3, 0, hil.RoutedBy.low_conf),
        (0.99, 1, hil.RoutedBy.audit),
        (0.3, 1, hil.RoutedBy.audit),
        (0.8, 0, None),
        (0.99, 0, None),
    ],
)
def test_routing(confidence, audit, route):
    @hil.review(judge=lambda i, o: confidence, audit_rate=audit)
    def fn(x):
        return x + 1

    assert fn(4) == 5
    items = hil.list_items()
    assert len(items) == (route is not None)
    if route:
        assert items[0].routed_by == route
        assert items[0].input == {"x": 4}
        assert items[0].output == 5
        assert items[0].confidence == confidence
        assert items[0].created_at.utcoffset().total_seconds() == 0


def test_auto_threshold(initialized, caplog):
    caplog.set_level("INFO", logger="hil_eval")
    fn = hil.review(judge=lambda i, o: 0.7, threshold="auto")(lambda: "ok")
    fn()
    fn()
    assert sum("No stored threshold" in r.message for r in caplog.records) == 1
    initialized.store.update_threshold("test", 0.6)
    fn()
    assert len(hil.list_items()) == 2
    initialized.store.update_threshold("test", 0.9)
    fn()
    assert len(hil.list_items()) == 3


@pytest.mark.parametrize("value", [-1, 2, float("nan"), float("inf"), True, "0.5", None])
def test_invalid_confidence(value):
    with pytest.raises(ValueError):
        hil.Judgment(value)
    fn = hil.review(judge=lambda i, o: value)(lambda: 42)
    assert fn() == 42
    assert hil.list_items() == []


@pytest.mark.parametrize("audit", [0, 1])
def test_judge_exception(audit):
    def judge(i, o):
        raise RuntimeError("judge broke")

    fn = hil.review(judge=judge, audit_rate=audit)(lambda: 42)
    assert fn() == 42
    items = hil.list_items()
    assert len(items) == audit
    if audit:
        assert items[0].confidence is None
        assert items[0].judge_reason == "judge_error: judge broke"
        assert items[0].routed_by == hil.RoutedBy.audit


def test_fn_exception():
    error = ValueError("original")

    @hil.review(judge=lambda i, o: pytest.fail("judge called"))
    def fn():
        raise error

    with pytest.raises(ValueError) as exc:
        fn()
    assert exc.value is error


async def test_async(initialized, monkeypatch):
    import threading

    main_thread = threading.get_ident()
    original = initialized.store.save_item

    def save(item):
        assert threading.get_ident() != main_thread
        return original(item)

    monkeypatch.setattr(initialized.store, "save_item", save)

    @hil.review(judge=lambda i, o: 0.1)
    async def fn(x):
        await asyncio.sleep(0)
        return x

    assert await fn(12) == 12
    assert len(hil.list_items()) == 1


def test_generators_rejected():
    def gen():
        yield 1

    async def agen():
        yield 1

    for fn in (gen, agen):
        with pytest.raises(TypeError):
            hil.review(judge=lambda i, o: 0)(fn)


def test_methods_and_selected_inputs():
    class Example:
        @hil.review(judge=lambda i, o: 0)
        def run(self, x, y=3):
            return x + y

        @classmethod
        @hil.review(judge=lambda i, o: 0, inputs=["x"])
        def selected(cls, x, secret):
            return x

    assert Example().run(2) == 5
    Example.selected(4, "hidden")
    assert [i.input for i in hil.list_items()] == [{"x": 2, "y": 3}, {"x": 4}]


def test_missing_init(monkeypatch, caplog):
    monkeypatch.setattr(context, "_default_client", None)
    fn = hil.review(judge=lambda i, o: pytest.fail("judge called"))(lambda: 3)
    assert fn() == fn() == 3
    assert hil.submit(1, 2) is None
    assert hil.list_items() == []
    assert len(caplog.records) == 1


def test_non_json():
    class Value:
        def __str__(self):
            return "custom"

    value = Value()
    fn = hil.review(judge=lambda i, o: 0)(lambda: value)
    assert fn() is value
    assert hil.list_items()[0].output == "custom"
    circular = []
    circular.append(circular)
    assert hil.submit(None, circular).output["__type__"] == "list"


def test_images(initialized, tmp_path):
    image = Image.new("RGB", (3, 4), "red")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    data = stream.getvalue()
    path = tmp_path / "image.png"
    path.write_bytes(data)
    refs = [store_image(v) for v in (data, path, str(path), image)]
    assert len({r.sha256 for r in refs}) == 1
    assert refs[-1].width == 3 and refs[-1].height == 4
    assert refs[0].mime == "image/png"
    assert Path(refs[0].path).parent == tmp_path / "blobs"
    assert len(list((tmp_path / "blobs").iterdir())) == 1
    fn = hil.review(judge=lambda i, o: 0)(lambda image: "caption")
    fn(image)
    item = hil.list_items()[0]
    assert item.input == {"image": {"$asset": refs[0].sha256}}
    assert item.assets[0].width == 3
    assert initialized.store.get_blob_path(refs[0].sha256).read_bytes() == data
    with pytest.raises(NotImplementedError, match="URLs"):
        store_image("https://example.com/image.png")


def test_submit_and_projects(initialized):
    item = hil.submit({"x": 1}, "out", confidence=1, tags={"model": "v1"}, trace_id="trace")
    assert item.routed_by == hil.RoutedBy.manual
    assert item.trace_id == "trace"
    assert hil.submit(None, None).confidence is None
    hil.submit(1, 2, project="other")
    assert len(hil.list_items(status=hil.Status.pending)) == 2
    assert len(hil.list_items(project="other")) == 1
    assert hil.list_items(status=hil.Status.approved) == []
    assert initialized.store.list_items("test", limit=1, offset=1)[0].confidence is None


def test_config_preservation(tmp_path):
    url = f"sqlite:///{tmp_path / 'config.db'}"
    hil.init("a", url, threshold=0.6, audit_rate=0.3, target_miss_rate=0.1)
    hil.init("a", url)
    config = context.get_client().store.get_project("a")
    assert (config.threshold, config.audit_rate, config.target_miss_rate) == (0.6, 0.3, 0.1)
    hil.init("a", url, audit_rate=0)
    assert context.get_client().store.get_project("a").audit_rate == 0
    hil.init("b", url)
    assert hil.submit(1, 2).project == "b"
    with pytest.raises(ValueError):
        hil.init("a", "postgres://localhost/db")


def test_concurrent_writes(initialized):
    with ThreadPoolExecutor(max_workers=12) as pool:
        items = list(pool.map(lambda n: hil.submit(n, n), range(80)))
    assert all(items)
    assert len({item.id for item in hil.list_items()}) == 80
    reopened = SQLiteStore(initialized.store.path)
    assert len(reopened.list_items("test")) == 80


def test_store_failure(initialized, monkeypatch):
    def fail(item):
        raise OSError("disk full")

    monkeypatch.setattr(initialized.store, "save_item", fail)
    fn = hil.review(judge=lambda i, o: 0)(lambda: "ok")
    assert fn() == "ok"
    assert hil.submit(1, 2) is None


def test_judgment_metadata():
    fn = hil.review(
        judge=lambda i, o: hil.Judgment(
            0.1, label="uncertain", reason="ambiguous", metadata={"source": "rule"}
        )
    )(lambda: "output")
    fn()
    item = hil.list_items()[0]
    assert item.judge_label == "uncertain"
    assert item.judge_reason == "ambiguous"
    assert item.judge_metadata == {"source": "rule"}
    assert hil.submit(1, 2, judge_label="manual").judge_label == "manual"


@pytest.mark.parametrize(
    "data,mime",
    [
        (b"\xff\xd8\xffabc", "image/jpeg"),
        (b"GIF89aabc", "image/gif"),
        (b"RIFF1234WEBPabc", "image/webp"),
        (b"unknown", "application/octet-stream"),
    ],
)
def test_blob_magic(data, mime):
    assert store_image(data).mime == mime


def test_sync_judge_required():
    async def judge(i, o):
        return 0.1

    with pytest.raises(TypeError, match="synchronous"):
        hil.review(judge=judge)


def test_write_retries(initialized, monkeypatch):
    import sqlite3

    from hil_eval.store import sqlite

    attempts = []
    delays = []

    def operation(db):
        attempts.append(1)
        if len(attempts) < 4:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    monkeypatch.setattr(sqlite.time, "sleep", delays.append)
    assert initialized.store._run(operation) == "ok"
    assert delays == [0.02, 0.06, 0.15]


def test_pagination_and_blob_missing(initialized):
    assert initialized.store.get_blob_path("missing") is None
    assert initialized.store.get_project("missing") is None
    for x in range(4):
        hil.submit(x, x)
    assert [i.input for i in initialized.store.list_items("test", limit=2, offset=1)] == [1, 2]


def test_recording_assets_and_input_selection(tmp_path):
    image = Image.new("RGB", (2, 2))
    path = tmp_path / "input.png"
    image.save(path)
    data = path.read_bytes()

    @hil.review(judge=lambda i, o: 0, inputs=["image"], tags={"kind": "image"})
    def fn(image, secret):
        return data

    assert fn(path, "exclude") is data
    item = hil.list_items()[0]
    assert set(item.input) == {"image"}
    assert item.input["image"] == item.output
    assert {a.role.value for a in item.assets} == {"input", "output"}
    assert len({a.sha256 for a in item.assets}) == 1
    assert item.tags == {"kind": "image"}


def test_retry_exhaustion(initialized, monkeypatch):
    import sqlite3

    from hil_eval.store import sqlite

    attempts = []

    def locked(db):
        attempts.append(1)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite.time, "sleep", lambda delay: None)
    with pytest.raises(sqlite3.OperationalError):
        initialized.store._run(locked)
    assert len(attempts) == 4
