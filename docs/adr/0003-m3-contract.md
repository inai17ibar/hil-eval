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

Automatic routing re-reads thresholds on every call. Matching decorator tags take
precedence over the project threshold; when multiple tags match, the highest
threshold wins (the most conservative review policy), independent of tag order.
With no match, use the project threshold, then DEFAULT_THRESHOLD. An explicit
numeric decorator threshold overrides both tag and project configuration.

Review and supersede operations acquire a SQLite write transaction before reading
item state. save_review raises ItemSupersededError for terminal superseded items;
the HTTP boundary maps this conflict to 409 without inserting a review.

## v0.1.1 audio extension

Assets also accept audio paths/bytes and base64 uploads, without changing
AssetRef or the Store protocol. WAV (RIFF/WAVE), MP3 (ID3 or MPEG frame sync),
FLAC, Ogg, and AIFF (FORM/AIFF) use signature detection before filename fallback.
Implicit capture includes media bytes and existing media Path objects; string
paths still require the decorator's explicit images argument. Audio playback
reuses video_ts as an optional offset in seconds and the existing Range-capable
blob route. HF Parquet adds an audio list<binary> column alongside images;
include_images=False omits bytes from both columns. OpenAI image export behavior
is unchanged.
