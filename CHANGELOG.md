# Changelog

## Unreleased (0.2.0)

- Structured label correction: field schemas (`enum` with optional `depends_on`
  hierarchy and `allow_custom`, `number` with range/step/multipliers, `text`) on
  `hil.init(fields=)`, `hil.set_fields`, `review(fields=)`, `submit(fields=)`,
  `PUT /v1/projects/{project}/fields` and `hil-eval fields`.
- Review UI correction form: chips with filter, hierarchical choices, number
  steppers, per-field highlight/reset, one-action confirm, keyboard shortcuts
  (`1`–`9`, arrows, `Enter`, `u`) and a phone-friendly layout.
- `ReviewRequest.fields`; reviews store `fields` (full corrected dict) and
  `field_diff` (`{name: {from, to}}`) and merge values into `corrected_output`.
  Items expose `fields`, `proposed_fields` and `external_id`.
- All export formats include `external_id`, `fields` and `field_diff`.
- Path-reference assets: `path_assets=` selectors and `asset_paths` in
  `POST /v1/items` record absolute paths without reading files;
  `serve --asset-root` (or `HIL_ASSET_ROOTS`) streams them via
  `GET /v1/items/{id}/assets/{index}` with Range support, default deny, traversal
  and symlink-escape protection. Assets report `kind`, `source_path`,
  `filename` and `availability`; `AssetResponse.sha256` is null for path assets.
- `hil-eval import FILE.jsonl` bulk-enqueues `hil.submit`-compatible records,
  idempotently (`external_id`/`--id-field`, else content hash), with counts.
- `Item.external_id`; SQLite schema version 3 (adds `projects.fields`).

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
