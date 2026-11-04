"""Start an isolated review server for browser tests; remove its DB on exit."""

import io
import wave
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from hil_eval.blobs import store_asset
from hil_eval.server.app import create_app
from hil_eval.store.sqlite import SQLiteStore
from hil_eval.types import Item, RoutedBy, Status

with TemporaryDirectory(prefix="hil-eval-e2e-") as directory:
    store = SQLiteStore(Path(directory) / "review.db")
    store.ensure_project("seed", threshold=None, target_miss_rate=None, audit_rate=None)
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8000)
    store.save_item(
        Item(
            assets=[store_asset(audio.getvalue(), store, video_ts=0.25)],
            id="seed-example",
            project="seed",
            fn_name="Example",
            input="Example input",
            output={"answer": "Review me"},
            confidence=None,
            judge_label=None,
            judge_reason=None,
            judge_metadata={},
            routed_by=RoutedBy.manual,
            status=Status.pending,
            tags={},
            trace_id=None,
            created_at=datetime.now(timezone.utc),
        )
    )
    uvicorn.run(create_app(store), host="127.0.0.1", port=8799)
