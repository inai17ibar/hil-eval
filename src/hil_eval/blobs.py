from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import tempfile
from pathlib import Path

from .types import AssetKind, AssetRef, AssetRole


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


def _is_video_bytes(data: bytes) -> bool:
    return data.startswith(b"\x1aE\xdf\xa3") or data[4:8] == b"ftyp"


def sniff_mime(data: bytes) -> str:
    """Guess a media mime type from magic bytes; used where no filename/extension
    is available (e.g. the flat, content-addressed blob store)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if data.startswith(b"fLaC"):
        return "audio/flac"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data[:4] == b"FORM" and data[8:12] == b"AIFF":
        return "audio/aiff"
    return "application/octet-stream"


def detect_mime(data: bytes, filename: str | None = None) -> str:
    """Prefer signatures, falling back to the filename for unknown formats."""
    mime = sniff_mime(data)
    if mime != "application/octet-stream":
        return mime
    return (mimetypes.guess_type(filename)[0] if filename else None) or mime


def is_asset(value) -> bool:
    """Detect media bytes and existing media Paths, keeping plain strings as text."""
    if isinstance(value, (bytes, bytearray)):
        return sniff_mime(bytes(value)).startswith(("image/", "video/", "audio/"))
    if isinstance(value, Path):
        if not value.is_file():
            return False
        try:
            with value.open("rb") as stream:
                header = stream.read(16)
        except OSError:
            header = b""
        return detect_mime(header, str(value)).startswith(("image/", "video/", "audio/"))
    return is_image(value)


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


def store_asset(
    image, store=None, *, role=AssetRole.input, video_ts=None, bbox=None, order=0
) -> AssetRef:
    if store is None:
        from .context import get_client

        client = get_client()
        if client is None:
            raise RuntimeError("hil.init() is required before storing assets")
        store = client.store
    width = height = None
    if isinstance(image, (bytes, bytearray)):
        data = bytes(image)
        mime = sniff_mime(data)
    elif isinstance(image, (str, Path)):
        if str(image).startswith(("http://", "https://")):
            raise NotImplementedError("Media URLs are not supported in Milestone 1")
        data = Path(image).read_bytes()
        mime = detect_mime(data, str(image))
    else:
        if not any(c.__module__.startswith("PIL.") for c in type(image).__mro__):
            raise TypeError(
                f"Expected media bytes, path, or PIL.Image.Image, got {type(image).__name__}"
            )
        from PIL import Image

        if not isinstance(image, Image.Image):
            raise TypeError("Expected media bytes, path, or PIL.Image.Image")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        data = stream.getvalue()
        width, height = image.size
        mime = "image/png"
    digest = hashlib.sha256(data).hexdigest()
    return AssetRef(
        digest, role, mime, store.put_blob(digest, data, mime), width, height, order, video_ts, bbox
    )


def store_image(image, store=None, *, role=AssetRole.input) -> AssetRef:
    return store_asset(image, store, role=role)


_MIME_ALIASES = {
    "audio/x-wav": "audio/wav",
    "audio/x-aiff": "audio/aiff",
    "audio/x-flac": "audio/flac",
}


def _within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives / mixed absolute and relative
        return False


def resolve_path_asset(path: str, roots) -> tuple[str, Path | None]:
    """Resolve a recorded path-asset against allowed roots (default deny).

    Returns ``("available", real_path)``, ``("missing", None)`` when the path is
    allowed but no regular file exists on this machine, or ``("not_allowed", None)``
    when no root is configured or the path (lexically *or* after resolving symlinks)
    falls outside every root. Traversal (``..``) and symlink escapes are rejected.
    """
    if not roots or not isinstance(path, str) or "\x00" in path or not os.path.isabs(path):
        return "not_allowed", None
    lexical = os.path.normpath(path)
    real = os.path.realpath(lexical)
    for root in roots:
        root_lexical = os.path.normpath(os.path.abspath(os.path.expanduser(os.fspath(root))))
        root_real = os.path.realpath(root_lexical)
        if _within(lexical, root_lexical) and _within(real, root_real):
            return ("available", Path(real)) if os.path.isfile(real) else ("missing", None)
    return "not_allowed", None


def path_asset(value, *, role=AssetRole.input, order=0) -> AssetRef:
    """Reference a media file by absolute path without reading, hashing or copying it.

    The path need not exist on this machine; ``hil-eval serve --asset-root`` resolves
    it at request time on the machine that has the file.
    """
    if isinstance(value, (bytes, bytearray)) or not isinstance(value, (str, Path)):
        raise TypeError(f"Path assets need a str or Path, got {type(value).__name__}")
    text = os.fspath(value)
    if not text or "\x00" in text:
        raise ValueError("Path assets need a non-empty path without NUL bytes")
    if text.startswith(("http://", "https://")):
        raise NotImplementedError("Media URLs are not supported")
    absolute = os.path.abspath(os.path.expanduser(text))
    name = os.path.basename(absolute)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    mime = _MIME_ALIASES.get(mime, mime)
    return AssetRef(None, role, mime, absolute, order=order, kind=AssetKind.path, filename=name)
