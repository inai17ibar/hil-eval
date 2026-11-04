import hashlib
import hmac
import io
import json
import urllib.error
import urllib.request
from http.client import HTTPResponse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from hil_eval.server import create_app
from hil_eval.store import SQLiteStore
from hil_eval.types import ProjectConfig
from hil_eval.webhooks import WebhookDispatcher


@pytest.fixture
def receiver(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(({k.lower(): v for k, v in self.headers.items()}, body))
            self.send_response(self.server.response_status)
            self.end_headers()

        def log_message(self, *args):
            pass

    # Exercise the real HTTPServer request handler using in-memory sockets. This
    # keeps the test fully offline, including in sandboxes without loopback.
    server = HTTPServer.__new__(HTTPServer)
    server.response_status = 200

    class MemorySocket:
        def __init__(self, data):
            self.input = io.BytesIO(data)
            self.output = io.BytesIO()

        def makefile(self, mode, *args):
            return self.input if "r" in mode else self.output

        def sendall(self, data):
            self.output.write(data)

    def transport(request, **kwargs):
        body = request.data
        headers = dict(request.header_items())
        headers["Content-Length"] = str(len(body))
        wire = b"POST / HTTP/1.1\r\n"
        wire += "".join(f"{k}: {v}\r\n" for k, v in headers.items()).encode()
        wire += b"\r\n" + body
        connection = MemorySocket(wire)
        Handler(connection, ("127.0.0.1", 0), server)
        response = HTTPResponse(MemorySocket(connection.output.getvalue()))
        response.begin()
        if response.status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, response.status, "error", response.headers, response
            )
        return response

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    yield server, received, "http://receiver.test"


def test_api_events_signed(tmp_path, receiver):
    server, received, url = receiver
    store = SQLiteStore(tmp_path / "db.sqlite")
    application = create_app(store, webhook_url=url, webhook_secret="secret")
    with TestClient(application) as client:
        result = client.post("/v1/items", json=dict(project="p", input=1, output=2))
        assert result.status_code == 201
        i = result.json()
        assert (
            client.post(f"/v1/items/{i['id']}/review", json=dict(action="approve")).status_code
            == 200
        )
        application.state.webhook_dispatcher._queue.join()
    assert len(received) == 2
    assert [h["x-hileval-event"] for h, _ in received] == ["item.created", "review.created"]
    for headers, body in received:
        expected = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        assert headers["x-hileval-signature"] == expected
    assert json.loads(received[0][1])["id"] == i["id"]


def test_retries_drop_and_project_override(receiver, caplog):
    server, received, url = receiver
    server.response_status = 500
    dispatcher = WebhookDispatcher("http://unused", "default")
    dispatcher.backoff = (0, 0, 0)
    dispatcher.start()
    try:
        dispatcher.enqueue(
            "item.created", ProjectConfig("p", None, 0.05, 0.02, url, "override"), {"id": "x"}
        )
        dispatcher._queue.join()
    finally:
        dispatcher.stop()
    assert len(received) == 4  # initial attempt + three retries
    assert "dropped" in caplog.text
    headers, body = received[0]
    assert (
        headers["x-hileval-signature"]
        == "sha256=" + hmac.new(b"override", body, hashlib.sha256).hexdigest()
    )


def test_enqueue_failure_and_no_url(caplog):
    dispatcher = WebhookDispatcher()
    dispatcher.enqueue("event", None, {})
    assert dispatcher._queue.empty()
    dispatcher.default_url = "http://unused"
    dispatcher.enqueue("event", None, {"bad": object()})
    assert "enqueue failed" in caplog.text
    assert dispatcher._queue.empty()
