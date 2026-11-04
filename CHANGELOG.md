# Changelog

## 0.1.1

- Add SDK choice review via `review(options=...)` (static or callable) and `submit(options=...)`.

- Add WAV, MP3, FLAC, Ogg, and AIFF audio asset detection, decorator capture,
  and base64 API uploads with filename fallback.
- Preview audio with playback controls and optional timestamp offsets; serve
  audio blobs with detected MIME types and HTTP Range support.
- Include ordered audio bytes in HF Parquet exports.
