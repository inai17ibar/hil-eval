# Changelog

## 0.1.1

- Add SDK choice review via `review(options=...)` (static or callable) and `submit(options=...)`.

- Add WAV, MP3, FLAC, Ogg, and AIFF audio asset detection, decorator capture,
  and base64 API uploads with filename fallback.
- Preview audio with playback controls and optional timestamp offsets; serve
  audio blobs with detected MIME types and HTTP Range support.
- Include ordered audio bytes in HF Parquet exports.
- Add `assets=` selectors to `review` and `submit` for values inside arguments and
  outputs (`"output.results[*].path"`); string paths become assets. `images=` stays
  as an alias.
- Asset `order` now follows document/list position per role instead of always 0.
- Stored `$asset` markers carry the source `filename` for path assets.
- JSONL/eval-set exports expand `$asset` markers with sha256, mime, filename, blob
  path and blob URL.
- Add `hil.export_to(path, format=...)` for all CLI formats; the `[hf]` extra is
  checked up front with an install hint (`hil.export(fmt="hf")` points to it).
- Add `review(name=...)` to override `fn_name`, and `review(route="all")` to queue
  every call.
