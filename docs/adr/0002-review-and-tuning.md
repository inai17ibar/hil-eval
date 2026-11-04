# ADR 0002: Review recording and threshold auto-tuning (v0)

## Status

Accepted (Milestone 2).

## Context

Milestone 1 shipped `@hil.review`/`hil.submit` plus a `Store` protocol with
just enough surface for writing items (`save_item`, `list_items`). There was
no way to record a human's verdict on a queued item, and no `threshold`
auto-tuning (F6, requirements.md §8).

Milestone 2 adds:

1. A `Review` record (one human verdict per reviewer action) and a
   `ThresholdHistory` record (an audit trail of threshold changes).
2. `Store` operations to read/delete single items, filter/count by
   confidence range and status, and atomically apply a review to an item.
3. `hil_eval.tuning`: a v0 recommended-threshold algorithm per
   requirements.md §8, and `apply_tuning()` to persist it.
4. A migration path from M1-shaped databases (`PRAGMA user_version` 0 -> 1).

This is a contract change (new types, new `Store` methods), hence this ADR
per AGENTS.md.

## Decision

### Types

`Review(id, item_id, reviewer, action, corrected_output=None,
judge_correct=None, note=None, created_at)` and
`ThresholdHistory(id, project, old, new, n_samples, est_review_rate,
est_miss_rate, created_at)` are plain `kw_only` dataclasses (matching the
zero-deps, stdlib-dataclass style of `types.py`). `kw_only=True` lets the
field order match requirements.md §9 exactly (`corrected_output` before
`created_at`) without fighting dataclass's "no required field after a
default field" rule.

`ReviewAction` mirrors `Status`/`RoutedBy`'s `str, Enum` style. Review ->
item-status mapping is fixed: `approve -> approved`, `edit -> corrected`,
`reject -> rejected`, `skip -> skipped`.

Note: the HTTP contract's `Item.reviews` field (populated only on
`GET /v1/items/{id}`) is **not** added to the `Item` dataclass. `Item` stays
exactly as M1 defined it; the server composes `reviews` into the JSON
response by calling `store.list_reviews(item_id)` separately. Putting
`reviews` on `Item` would mean `save_item`'s `asdict(item)` either has to
special-case excluding it or starts persisting review data twice (once in
`reviews`, once embedded in every `items.data` snapshot going stale the
moment a new review is added).

### Store protocol additions

`get_item`, `delete_item` (cascades to that item's reviews, returns whether
anything was deleted), `list_projects`, `count_items` (same filters as
`list_items`, for pagination totals), `save_review`, `list_reviews`,
`list_reviewed` (latest review per item, for tuning), `add_threshold_history`,
`list_threshold_history`.

`list_items`/`count_items` gained `min_confidence`/`max_confidence` and a
`status` parameter that now also accepts a list (`GET /v1/items?status=` may
repeat per the HTTP contract). `list_items` also gained `order: "asc"|"desc"`
(default `"asc"`, preserving M1 behavior/tests) because the HTTP contract
requires "newest first" for `GET /v1/items`, and that has to be a real
`ORDER BY ... LIMIT ... OFFSET` at the SQL layer to paginate correctly
(reversing an already-paginated page is not equivalent).

`save_review` is atomic: insert into `reviews` and patch the item's status
**and its cached `data` JSON blob** happen in one `sqlite3` transaction
(single `with db:` block in `_run`). Raises `ItemNotFoundError` (new,
exported from `hil_eval.store`) if the item doesn't exist, so the server can
map it to a 404 — writing a review for a nonexistent item is a genuine
caller error, not something to fail-open on (unlike `@hil.review`'s own
recording path).

*Pitfall we hit and fixed*: `items.data` is a frozen JSON snapshot of the
`Item` at insert time (same as M1). Updating only the `items.status` SQL
column after a review (and not the `status` key inside `data`) would make
`get_item`/`list_items` keep reporting `status=pending` forever, since they
rehydrate `Item` from the `data` column, not from the `status` column (the
latter exists purely for the `WHERE status=?` index). `save_review` now
patches both.

### Migration (M1 -> M2 schema)

`SCHEMA_VERSION = 1`, tracked via `PRAGMA user_version`. `_init_schema` runs
`CREATE TABLE IF NOT EXISTS` for every table (so a fresh M2 database is
correct on first run) and then, if `user_version < 1`: adds the `confidence`
column to `items` if missing, and backfills it from each row's `data` JSON
(not `json_extract` — stdlib `sqlite3` is not guaranteed to be built with the
JSON1 extension, and AGENTS.md requires zero required dependencies / stdlib
only). Backfill runs inside the same transaction as the rest of
`_init_schema`'s `_run` call, so a crash mid-migration can't leave
`user_version=1` with a half-backfilled table.

New installs never go through the backfill loop (the column already exists
from `CREATE TABLE`), but still get `user_version` bumped to 1 so a later
migration to v2 doesn't try to re-run v1's steps.

### Threshold tuning v0 (`hil_eval/tuning.py`)

`recommend_threshold(store, project, target_miss_rate=None)` is pure
(read-only); `apply_tuning(...)` calls it and, only if the recommendation
actually differs from the current threshold, records a `ThresholdHistory`
row and calls `update_threshold`. A no-op recommendation (e.g.
`insufficient_data`) writes nothing — an unbroken history of `old == new`
rows would be noise, not an audit trail.

**Sample selection and IPW weight** (`_collect_samples`): for each item's
*latest* review (`store.list_reviewed`), excluding `routed_by=manual`,
`confidence is None`, and `action=skip`:

- `judge_correct = review.judge_correct`, or if absent, inferred from the
  action: `approve -> True`, `edit`/`reject -> False` (requirements.md §8
  step 1; `skip` is already excluded, so no inference is needed for it).
- weight is **1.0 if `confidence < threshold`**, else **`1/audit_rate`**
  (only if `routed_by == audit`; otherwise the sample is dropped — see Edge
  cases).

This is keyed off *confidence vs. threshold*, not off `routed_by`, even
though the task brief phrased it as "`routed_by=audit -> 1/audit_rate`".
Walking through `@hil.review`'s own routing logic
(`architecture.md` "Routing decision"): the audit coin flip happens
*before* the confidence check, so a call can be `routed_by=audit` while
still being below threshold. Every below-threshold call is persisted either
way (as `low_conf` if not audited, as `audit` if it was) — i.e. the
below-threshold slice is **never** subsampled, so it should always get
weight 1 regardless of which of the two labels it happened to land under.
Only above-threshold calls are subsampled (only audit-selected ones are
stored at all), so only those need the `1/audit_rate` blow-up. Weighting by
label instead of by confidence-vs-threshold would overweight the
"audited-and-also-below-threshold" slice, inflating its influence on `M(t)`
by `1/audit_rate` for samples that were not actually undersampled.

**Candidate scan**: `t` in `0.00, 0.01, ..., 1.00` (101 points, integer
`step/100` to dodge float-accumulation drift from repeatedly adding `0.01`).
For each `t`: `R(t)` = weighted fraction with `confidence < t`; for the
`confidence >= t` slice, `M(t)` = weighted miss rate
(`judge_correct=False`), and its 95% Wilson upper bound using Kish's
effective sample size `n_eff = (Σw)² / Σw²` over that slice (accounts for
the variance inflation IPW weighting introduces — a handful of
heavily-upweighted audit samples should produce a *wider*, not narrower, CI
than their raw count suggests).

Pick the smallest `t` with `upper(M(t)) <= target_miss_rate`
(minimizes review load, per requirements.md §8 step 3), then clamp the
result to `[current - 0.1, current + 0.1] ∩ [0, 1]` (step 5, "急変防止").
`reason` is `"ok"` if the clamp didn't change anything, `"clamped"` if it
did — this is reported so a caller/UI can tell "we're already at the ideal
point" apart from "we're still correcting toward it over several tuning
runs."

`n_samples < 30` (requirements.md §8 step 4) short-circuits to
`reason="insufficient_data"`, `recommended = current`, before the scan even
runs — deliberately checked on the *raw* unweighted sample count, not
`n_eff` (which varies per `t`); 30 raw human reviews is the bar for
"enough data exists to tune at all," independent of how uncertain any one
candidate `t` turns out to be.

If no `t` in `[0, 1]` satisfies the target (see Edge cases), move the
threshold toward 1.0 by the max allowed step:
`recommended = min(1.0, current + 0.1)`, `reason="no_threshold_meets_target"`.

## Edge cases considered

- **`audit_rate == 0`**: `1/audit_rate` would divide by zero. In practice
  `audit_rate=0` means the random audit draw (`Random().random() < 0`) is
  always `False`, so no `routed_by=audit` items can exist — but tests (and
  future code) can still construct that combination directly via the store.
  `_collect_samples` guards `routed_by == audit and audit_rate > 0`; if that
  fails the sample is dropped rather than raising.
- **Stale `low_conf` sample above the current threshold**: if the project's
  threshold was lowered after an item was routed `low_conf` under the old
  (higher) threshold, that item's `confidence` can be `>= current threshold`
  by the time tuning runs. Its original selection probability (1, under the
  old threshold) no longer matches what the current threshold implies, so
  there's no valid IPW weight for it under *this run's* model — it's
  dropped. This is a known v0 limitation: we don't store "threshold at
  collection time" per item, so a rapidly-changing threshold slightly
  shrinks the usable sample set rather than being modeled exactly in v1
  isotonic calibration could track this precisely.
- **Empty "above threshold" slice** (`above_weight == 0` for some
  candidate `t`, e.g. `t` above every observed confidence): defined as
  `M(t) = 0`, `upper = 0` — if a threshold auto-approves nothing, there is
  no miss risk by construction, so the constraint is trivially satisfied.
  This also means a single same-valued cluster of confidences (e.g. a test
  fixture using one fixed confidence for every sample) will trivially
  "pass" at any `t` just past that value even with few samples — real
  traffic has a continuous confidence distribution so this mostly matters
  for synthetic data/tests, but it's a real possible outcome, not a bug.
- **No `t` satisfies anywhere in `[0, 1]`**: possible when a high-weight
  wrong sample sits at `confidence = 1.0` (the audited-and-wrong case) —
  it never leaves the "above threshold" slice no matter how high `t` goes,
  and its weight dominates that slice as `t -> 1` (fewer correct samples
  dilute it). Handled by moving toward 1.0 by the max step rather than
  raising or silently keeping `current` — half progress toward "review
  everything" is safer than no progress, and `reason` makes the shortfall
  visible instead of looking like a successful tune.
- **`judge_correct` explicitly set alongside `action=approve`/`edit`**:
  explicit `judge_correct` always wins over the action-based inference —
  a human can approve an output while explicitly noting the judge's
  *reasoning* was wrong-but-harmless, and that should count as a miss for
  tuning purposes even though the action was "approve."
- **Multiple reviews on one item**: `list_reviewed` keeps only the latest
  (by `created_at`, then insertion order as a tiebreak) — an item that was
  rejected and later corrected-and-approved on a second pass should count
  by its final verdict, not an earlier one.

## Consequences

- `Item` and `save_item` are unchanged; the only new persisted artifacts are
  `reviews` and `threshold_history` rows plus the `items.confidence` column.
- Tuning is read-heavy (`list_reviewed` loads every reviewed item for a
  project into memory) and scans 101 candidate thresholds in Python, O(101 ×
  n_reviewed). Fine for v0/expected review-queue volumes; would need
  streaming/SQL aggregation if project review history grows into the
  hundreds of thousands.
- v1 candidates noted in requirements.md §8 (isotonic calibration, per-tag
  thresholds) are out of scope here; this ADR's weighting scheme is
  explicitly "v0."
