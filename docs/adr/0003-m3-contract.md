# ADR 0003: M3 review, assets, configuration and delivery contracts

Status: accepted

M3 adds context assets and ordered image/video references (`order=0`,
`video_ts=None`, `bbox=None`, with API validation of [x,y,w,h]). WebM and MP4
are sniffed by signature. Items gain optional options and expires_at; reviews gain
choice, reason_codes (an independent empty list), and reason_text. choose maps to
corrected; superseded is a terminal queue status. Existing JSON records load using
defaults. Options and choice are first-class fields because choice review is a
queryable interaction mode on which UI and exports branch.

ReasonCode(project, code, label, polarity) and TagThreshold(project, tag_key,
tag_value, threshold) are keyword-only, project-scoped records in separate tables.
Store adds register_reason_code/list_reason_codes, set/get/list_tag_thresholds and
supersede_items. list_items/count_items exclude expired pending records by default;
callers supply the comparison time with now (required when filtering expiring rows).
Filtering happens in Python, before pagination, avoiding a JSON1 dependency.
Superseding matches trace or tag, within the project, and only pending records.

Schema version 2 uses PRAGMA user_version, creates vocabulary/config tables and
adds nullable project webhook_url/webhook_secret columns. Item/review additions
remain in JSON; migration preserves old records. Explicit ensure_project webhook
settings override stored settings; omitted settings preserve them.

Tag tuning filters reviewed samples and writes tag thresholds; project threshold
is the fallback. ThresholdHistory remains project-only: tag tuning results are
printed but are not persisted in history in v0 (known gap).

Webhooks use an asynchronous daemon queue, signed JSON and bounded retries;
delivery errors log and never change successful writes. Built-in judges use only
stdlib HTTP and return Judgment; errors propagate into the decorator's existing
judge-error recording guard, preserving application results and exceptions.
Parquet export lazily imports optional pyarrow; required runtime dependencies
remain empty. HTTP/CLI/export errors remain explicit.

Parquet normally preserves inferred native Arrow values. Columns containing
heterogeneous JSON or empty structs fall back to JSON strings (Parquet cannot
represent these directly). The `hil_eval.json_columns` schema metadata lists
those columns so readers can decode them without loss. Images are list<binary>.
