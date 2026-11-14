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
Rule for actions and options (v0.2, revised): when a review carries
`fields`, the requested action among approve/edit/choose is not trusted; the
server derives it from the diff -- `approve` (status approved) when nothing
changed, `edit` (status corrected, values merged into `corrected_output`) when
something changed or a `corrected_output` was supplied. `reject`/`skip` cannot
carry fields. `choice` is an orthogonal verdict flag: it must be one of the item's
options (when it has any) and is stored verbatim next to `fields`/`field_diff`,
so "not sure, but I corrected it like this" is one review (choice `unsure`,
action `edit`). hil-eval never interprets option values: verdicts such as
`correct` or `wrong_instrument` are derivable from `field_diff` (empty means the
proposal was confirmed; the keys name the wrong attributes), so for structured
items projects should keep only flags the diff cannot express (e.g. `unsure`).
They are still accepted if present -- the simplest rule is "no option is
special" -- and consumers decide how to treat contradictory combinations.
Without `fields`, choice reviews behave as in v0.1 (action `choose`, exported
expected value = the choice). Fields always win over an explicitly supplied
`corrected_output`, and outputs that cannot hold the values (non-objects) get 422.

Exports keep both: jsonl/eval-set carry `choice`, `fields`, `field_diff` and the
field-merged `corrected_output`/`expected`; openai-ft trains on the field-merged
output and keeps `choice` in `metadata` (flagged samples are included; filter on
`metadata.choice` if you do not want to train on them); hf has `choice`,
`fields`, `field_diff` columns.

Store protocol gains `set_project_fields`.

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
