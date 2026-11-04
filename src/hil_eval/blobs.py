from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import tempfile
from pathlib import Path

from .types import AssetRef, AssetRole


def put_blob(directory: Path, sha256: str, data: bytes) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / sha256
    if not path.exists():
        fd, temp = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return str(path)


_IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")


def _is_image_bytes(data) -> bool:
    return data.startswith(_IMAGE_MAGIC) or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


def is_image(value) -> bool:
    """Implicit image detection used for values not explicitly listed in ``images``.

    Plain strings are never treated as images (they may be ordinary text or URLs);
    only PIL images, bytes with a known image signature, and ``Path`` objects that
    point to an existing image file qualify.
    """
    if isinstance(value, (bytes, bytearray)):
        return _is_image_bytes(bytes(value))
    if isinstance(value, Path):
        return value.is_file() and (mimetypes.guess_type(str(value))[0] or "").startswith("image/")
    return any(c.__module__.startswith("PIL.") for c in type(value).__mro__)


def store_image(image, store=None, *, role=AssetRole.input) -> AssetRef:
    if store is None:
        from .context import get_client

        client = get_client()
        if client is None:
            raise RuntimeError("hil.init() is required before storing images")
        store = client.store
    width = height = None
    if isinstance(image, (bytes, bytearray)):
        data = bytes(image)
        mime = "application/octet-stream"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif data.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif data.startswith((b"GIF87a", b"GIF89a")):
            mime = "image/gif"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            mime = "image/webp"
    elif isinstance(image, (str, Path)):
        if str(image).startswith(("http://", "https://")):
            raise NotImplementedError("Image URLs are not supported in Milestone 1")
        data = Path(image).read_bytes()
        mime = mimetypes.guess_type(str(image))[0] or "application/octet-stream"
    else:
        from PIL import Image

        if not isinstance(image, Image.Image):
            raise TypeError("Expected image bytes, path, or PIL.Image.Image")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        data = stream.getvalue()
        width, height = image.size
        mime = "image/png"
    digest = hashlib.sha256(data).hexdigest()
    return AssetRef(digest, role, mime, store.put_blob(digest, data, mime), width, height)
