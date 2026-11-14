# ADR 0004: Structured label fields, path-reference assets and import

Status: accepted (v0.2)

Context: catalog labelling (soundai-labels) needs per-attribute corrections
(family/leaf/type/bpm/key) that a consumer can turn into `id -> overrides`, and the
audio lives only on one machine and may not be copied.

Field schemas are JSON-compatible lists (`hil_eval.fields.normalize_fields`
validates them). They live on the item (`Item.fields`) or the project
(`ProjectConfig.fields`, new `projects.fields` column, schema version 3); the
item's schema wins. Proposed values are read from the output at each field's
dotted `path`. A review may carry `fields`; the server (not the client) computes
`Review.fields` (all values) and `Review.field_diff` (changed only), validates
only changed values (plus children of a changed parent) so that out-of-schema
proposals can be confirmed, and writes the values into `corrected_output`.
`edit` without changes is stored as `approve`; `approve` with changes is
rejected. No new `ReviewAction` was added, so status mapping, tuning and old
clients are unchanged. Store protocol gains `set_project_fields`.

Path-reference assets are `AssetRef(kind="path", sha256=None, path=<abs path>)`
with `{"$asset_path": path}` markers. They are never read when recorded. The
server resolves them per request against `asset_roots` (default empty = deny):
the normalized path and its `realpath` must both lie under a root (`commonpath`),
which blocks `..` traversal and symlink escape. Only paths recorded on an item are
reachable (`/v1/items/{id}/assets/{index}`), never arbitrary paths. Anyone who
can create items can still point at any file inside an allowed root, so roots
should be as narrow as practical and non-loopback servers need an API key.

`hil-eval import` uses `Item.external_id` (or `--id-field`) for idempotency and
falls back to a content hash stored as `Item.import_key` (not exposed over HTTP).
Duplicates are skipped, never updated.
