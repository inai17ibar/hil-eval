import base64
import io
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from PIL import Image

import hil_eval as hil
from hil_eval.context import get_client
from hil_eval.exporters import export_items, write_jsonl
from hil_eval.types import Review, ReviewAction, Status


@pytest.fixture
def reviewed(tmp_path):
    hil.init("export", store=f"sqlite:///{tmp_path / 'db.sqlite'}")
    image = Image.new("RGB", (2, 2), "red")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    raw = stream.getvalue()
    item = hil.submit({"prompt": "Describe", "nested": [{"image": raw}]}, {"answer": "old"})
    store = get_client().store
    now = datetime.now(timezone.utc)
    store.save_review(
        Review(
            id=uuid4().hex,
            item_id=item.id,
            reviewer=None,
            action=ReviewAction.approve,
            created_at=now,
        )
    )
    store.save_review(
        Review(
            id=uuid4().hex,
            item_id=item.id,
            reviewer=None,
            action=ReviewAction.edit,
            corrected_output={"answer": "new"},
            judge_correct=False,
            created_at=now + timedelta(seconds=1),
        )
    )
    return store, item, raw


def test_jsonl_latest_and_python_api(reviewed, tmp_path, capsys):
    store, item, _ = reviewed
    records = list(export_items(store, "export"))
    assert len(records) == 1
    record = records[0]
    assert record["id"] == item.id
    assert record["corrected_output"] == {"answer": "new"}
    assert record["output"] == {"answer": "old"}
    assert record["judge_correct"] is False
    assert record["status"] == "corrected"
    assert list(hil.export()) == records
    path = tmp_path / "records.jsonl"
    write_jsonl(iter(records), path)
    assert json.loads(path.read_text()) == record
    write_jsonl(records, "-")
    assert json.loads(capsys.readouterr().out) == record


def test_openai_images_and_eval_set(reviewed):
    store, _, raw = reviewed
    messages = list(export_items(store, "export", fmt="openai-ft"))[0]["messages"]
    content = messages[0]["content"]
    assert "$asset" not in content[0]["text"]
    assert json.loads(content[0]["text"])["prompt"] == "Describe"
    assert content[1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(raw).decode()
    )
    assert json.loads(messages[1]["content"]) == {"answer": "new"}
    no_images = list(export_items(store, "export", fmt="openai-ft", include_images=False))
    assert len(no_images[0]["messages"][0]["content"]) == 1
    record = list(export_items(store, "export", fmt="eval-set"))[0]
    assert record["expected"] == {"answer": "new"}
    assert record["metadata"]["judge_correct"] is False


def test_statuses_and_rejected_exclusion(reviewed):
    store, item, _ = reviewed
    for action, status in [
        (ReviewAction.approve, Status.approved),
        (ReviewAction.reject, Status.rejected),
        (ReviewAction.skip, Status.skipped),
    ]:
        store.save_review(
            Review(
                id=uuid4().hex,
                item_id=item.id,
                reviewer=None,
                action=action,
                created_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        record = list(export_items(store, "export", statuses=[status]))[0]
        assert record["status"] == status.value
        if status == Status.approved:
            assert record["corrected_output"] == item.output
        if status == Status.rejected:
            assert not list(export_items(store, "export", fmt="openai-ft", statuses=[status]))
    assert not list(export_items(store, "export", statuses=[]))
    with pytest.raises(ValueError):
        list(export_items(store, "export", fmt="unknown"))


def test_missing_image_is_reported(reviewed):
    store, item, _ = reviewed
    store.get_blob_path(item.assets[0].sha256).unlink()
    with pytest.raises(FileNotFoundError):
        list(export_items(store, "export", fmt="openai-ft"))
    assert list(export_items(store, "export", fmt="openai-ft", include_images=False))


def test_root_image_reference_is_removed(tmp_path):
    hil.init("root-image", store=f"sqlite:///{tmp_path / 'hil.db'}")
    image = Image.new("RGB", (2, 2), "blue")
    item = hil.submit(image, "blue square")
    store = get_client().store
    store.save_review(
        Review(
            id=uuid4().hex,
            item_id=item.id,
            reviewer=None,
            action=ReviewAction.approve,
            created_at=datetime.now(timezone.utc),
        )
    )
    record = list(hil.export(fmt="openai-ft"))[0]
    assert "$asset" not in record["messages"][0]["content"][0]["text"]
    assert record["messages"][1]["content"] == "blue square"
