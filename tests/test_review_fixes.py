"""Regression tests for issues found in the Codex review of M1."""

import io
from unittest.mock import patch

import pytest
from PIL import Image

import hil_eval as hil
from hil_eval import context


@pytest.fixture(autouse=True)
def initialized(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "_default_client", None)
    hil.init("t", f"sqlite:///{tmp_path / 'db.sqlite'}", audit_rate=0)


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (2, 3)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.parametrize("text", ["missing.png", "https://example.com/a.jpg", b"plain bytes"])
def test_ordinary_values_are_not_treated_as_images(text):
    item = hil.submit({"q": text}, text, confidence=0.1)
    assert item is not None
    assert item.assets == []
    assert hil.list_items()[0].output == (text if isinstance(text, str) else str(text))


def test_explicit_image_path_missing_keeps_item(tmp_path):
    @hil.review(judge=lambda a, o: 0.1, images=["image"])
    def fn(image, text):
        return "ok"

    assert fn(str(tmp_path / "nope.png"), "hi") == "ok"
    [item] = hil.list_items()
    assert "$asset_error" in item.input["image"]
    assert item.input["text"] == "hi"


def test_explicit_image_path_string(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(png_bytes())

    @hil.review(judge=lambda a, o: 0.1, images=["image"])
    def fn(image):
        return "ok"

    fn(str(path))
    [item] = hil.list_items()
    assert item.input["image"] == {"$asset": item.assets[0].sha256}


def test_judge_sees_all_args_but_inputs_limits_storage():
    seen = {}

    def judge(args, out):
        seen.update(args)
        return 0.1

    @hil.review(judge=judge, inputs=["a"])
    def fn(a, b):
        return a + b

    fn(1, 2)
    assert seen == {"a": 1, "b": 2}
    assert hil.list_items()[0].input == {"a": 1}


def test_unknown_argument_names_rejected():
    with pytest.raises(ValueError):

        @hil.review(judge=lambda a, o: 0, inputs=["nope"])
        def fn(x):
            return x


@pytest.mark.parametrize("bad", [float("nan"), -0.1, 1.5, True, "x"])
def test_invalid_threshold_and_audit_rate(bad):
    with pytest.raises(ValueError):
        hil.review(judge=lambda a, o: 0, threshold=bad)
    with pytest.raises(ValueError):
        hil.review(judge=lambda a, o: 0, audit_rate=bad)
    with pytest.raises(ValueError):
        hil.init("t2", "sqlite:///:memory:", audit_rate=bad)


async def test_async_offload_failure_is_fail_open():
    @hil.review(judge=lambda a, o: 0)
    async def fn():
        return 42

    with patch("asyncio.to_thread", side_effect=RuntimeError("boom")):
        assert await fn() == 42
