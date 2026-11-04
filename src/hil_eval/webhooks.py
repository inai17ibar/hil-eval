"""Best-effort signed webhook delivery using only the standard library."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from typing import Any

from .types import ProjectConfig

_LOG = logging.getLogger("hil_eval")


class WebhookDispatcher:
    def __init__(self, default_url=None, default_secret=None, *, max_queue_size: int = 1000):
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be positive")
        self.default_url = default_url
        self.default_secret = default_secret
        self.backoff = (1, 5, 15)
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_queue_size)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="hil-eval-webhooks")
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=1)

    def enqueue(self, event: str, project: ProjectConfig | None, payload: Any):
        try:
            url = (project.webhook_url if project else None) or self.default_url
            secret = project.webhook_secret if project else None
            if secret is None:
                secret = self.default_secret
            if url:
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                self._queue.put_nowait((url, secret, event, body))
        except queue.Full:
            _LOG.warning("Webhook event dropped: queue full")
        except Exception:
            _LOG.exception("Webhook enqueue failed")

    def _run(self):
        while not self._stop.is_set():
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._deliver(*job)
            finally:
                self._queue.task_done()

    def _deliver(self, url, secret, event, body):
        headers = {"Content-Type": "application/json", "X-HilEval-Event": event}
        if secret is not None:
            signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-HilEval-Signature"] = f"sha256={signature}"
        for attempt in range(len(self.backoff) + 1):
            if self._stop.is_set():
                return
            try:
                request = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(request, timeout=5) as response:
                    if not 200 <= response.status < 300:
                        raise urllib.error.URLError(f"HTTP {response.status}")
                return
            except Exception:
                if attempt == len(self.backoff):
                    _LOG.exception("Webhook delivery dropped after retries: %s", event)
                    return
                if self._stop.wait(self.backoff[attempt]):
                    return
