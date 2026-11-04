# Milestone 3 implementation plan

Scope: H1/H3–H10 from `/tmp/ux-arch.md` §6, split into 6 phases, each a self-contained
agent task. 1→4 are sequential (contract before consumers); 5 depends on 1+2+3 (UI reads
the new fields/API/export shapes); 6 depends on everything. Current state:
`src/hil_eval/{types,blobs,cli}.py`, `store/{base,sqlite}.py`, `server/app.py`,
`exporters.py`, `tuning.py`, `ui/src/{api.ts,main.tsx}`, `.github/workflows/ci.yml`. Follow
`docs/architecture.md`/`docs/adr/0002-review-and-tuning.md` style: stdlib dataclasses, zero
required deps, fail-open only inside `@review`'s recording path, never for HTTP/CLI.

## ADR `docs/adr/0003-m3-contract.md` (write first, in phase 1)

- New `AssetRole.context`; `AssetRef` gains `order: int`, `video_ts: float | None`,
  `bbox: list[float] | None`; mime sniffing extended to `video/webm`/`video/mp4`.
- New `ReviewAction.choose`; `Item` gains `options: list[Any] | None`; `Review` gains
  `choice: Any`, `reason_codes: list[str]`, `reason_text: str | None`.
- New `Status.superseded`; `Item` gains `expires_at: datetime | None`.
- New `ReasonCode(project, code, label, polarity)` and `TagThreshold(project, tag_key,
  tag_value, threshold)` — project-scoped vocab/config, not `Item`/`Review` fields, so they
  get their own tables.
- `Store` grows: `register_reason_code`, `list_reason_codes`, `set_tag_threshold`,
  `get_tag_threshold`, `list_tag_thresholds`, `supersede_items`; `list_items`/`count_items`
  gain `exclude_expired: bool = True`.
- Rationale: `options`/`choice` live outside the `input`/`corrected_output` JSON blobs
  because choice review is a distinct, queryable interaction mode that UI/export must branch
  on, not incidental payload shape.
- Migration: `SCHEMA_VERSION = 2`, same `PRAGMA user_version` gate as `0002`.

## Phase 1 — Contract, store, migration (H1, H3, H4, H5, H7, H8)

**Files**: `types.py`, `store/base.py`, `store/sqlite.py`, `server/app.py`, `cli.py`,
`tuning.py`, `blobs.py`, `docs/adr/0003-m3-contract.md`.

**Types**: `AssetRole` +`context`. `AssetRef` +`order: int = 0`, `video_ts: float | None =
None`, `bbox: list[float] | None = None` (`[x,y,w,h]`, validated at the API boundary).
`ReviewAction` +`choose`. `Review` +`choice: Any = None`, `reason_codes: list[str] =
field(default_factory=list)`, `reason_text: str | None = None`. `Item` +`options: list[Any]
| None = None`, `expires_at: datetime | None = None`. `Status` +`superseded`;
`_ACTION_TO_STATUS[choose] = Status.corrected`. New `kw_only` dataclasses `ReasonCode(project,
code, label, polarity)`, `TagThreshold(project, tag_key, tag_value, threshold)`. All new
fields need defaults — old DB rows must deserialize into the new dataclasses unchanged.

**Blobs**: `_is_video_bytes` (WebM EBML magic `\x1aE\xdf\xa3`, MP4 `data[4:8]==b"ftyp"`);
`sniff_mime` branches for `video/webm`/`video/mp4`. Add `store_asset(data, role, *,
video_ts=None, bbox=None, order=0)` as the general (image-or-video) path; `store_image`
becomes a thin wrapper over it.

**Store + SQLite**: `list_items`/`count_items` +`exclude_expired: bool = True` → clause
`(expires_at IS NULL OR expires_at > :now OR status != 'pending')`, `now` passed by the
caller (never `datetime.now()` inside the store). `supersede_items(project, *,
trace_id=None, tag=None) -> int`: updates pending items matching trace_id or (loaded then
Python-filtered, no JSON1 dependency) tag key/value to `status=superseded`, returns count.
`register_reason_code`/`list_reason_codes` → table `reason_codes(project, code, label,
polarity, PRIMARY KEY(project, code))`. `set_tag_threshold`/`get_tag_threshold`/
`list_tag_thresholds` → table `tag_thresholds(project, tag_key, tag_value, threshold,
PRIMARY KEY(project, tag_key, tag_value))`. `_migrate_to_v2`: `CREATE TABLE IF NOT EXISTS`
for both (no `items` column changes — new fields live in the existing `data` JSON blob).
Bump `SCHEMA_VERSION = 2`.

**HTTP API**: `POST /v1/items` — `CreateItemRequest` +`options`, +`expires_at`, +`assets:
list[AssetUpload] | None` where `AssetUpload = {data_base64, mime?, role="input",
filename?, video_ts?, bbox?}`; decode, sniff mime if omitted, `put_blob`, `order` = list
index. `ReviewRequest` +`choice`, +`reason_codes: list[str] = []`, +`reason_text`;
validator: `action==choose` requires `choice` (and `choice in item.options` if non-empty);
`reason_codes` checked against `list_reason_codes(project)` only if that project has any
registered. `GET /v1/items` +`include_expired: bool = False`. New `POST
/v1/items/supersede` body `{project, trace_id?, tag_key?, tag_value?}` → `{"superseded":
n}`. New `GET`/`POST /v1/projects/{project}/reason-codes` (`{code, label?, polarity?}`).
New `GET /v1/projects/{project}/threshold?tag=key:value` → `{"threshold": ...}`; `PUT` body
`{tag_key, tag_value, threshold}`.

**CLI + tuning**: `tune --by-tag KEY` enumerates distinct tag values from
`store.list_reviewed(project)`, calls `recommend_threshold(..., tag=(KEY, value))` per
value. `recommend_threshold`/`_collect_samples`/`apply_tuning` gain optional `tag:
tuple[str,str] | None` (filters samples by `item.tags.get(tag[0])==tag[1]`); `apply_tuning`
calls `set_tag_threshold` instead of `update_threshold` when `tag` is set.
`ThresholdHistory` unchanged — tag-scoped runs print to the CLI but aren't persisted to
history in v0 (note as a known gap in the ADR, not silently dropped).

**Acceptance tests** (`tests/test_m3_contract.py`, extend `test_store_m2.py`/`test_api.py`):
- Round-trip an `Item`/`AssetRef` with `video/webm` + `video_ts`/`bbox` through
  `save_item`/`get_item`; `POST /v1/items` with a base64 video asset downloads correctly.
- A saved pre-M3 fixture DB migrates in place via `SCHEMA_VERSION` bump; old items load.
- `action=choose` with valid/invalid `choice` → 200/422; unknown `reason_codes` → 422.
- Expired pending item excluded by default, included with `include_expired=true`.
- `POST /v1/items/supersede` flips only matching pending items.
- `tune --by-tag model` sets per-value thresholds; `GET .../threshold?tag=` returns them,
  falling back to the project threshold when unset.

## Phase 2 — Webhooks and built-in judges (H6)

**Files**: `webhooks.py` (new), `server/app.py`, `cli.py`,
`judges/{__init__,llm,rule,logprob}.py` (new package).

`ProjectConfig` +`webhook_url`, +`webhook_secret` (per-project override of a CLI-wide
default; migration adds the columns). `webhooks.WebhookDispatcher(default_url=None,
default_secret=None)`: `queue.Queue` + one daemon thread; `.enqueue(event, project,
payload)` resolves URL/secret, POSTs JSON via `urllib.request` with
`X-HilEval-Signature: sha256=<hmac_sha256(secret, body).hexdigest()>` and
`X-HilEval-Event`. Retries on non-2xx/`URLError` with backoff `(1, 5, 15)`s, then drops +
logs ERROR (fail-open — never block the request thread). `.start()`/`.stop()` hooked into
`create_app`'s startup/shutdown. `create_item`/`review_item` enqueue `item.created`/
`review.created` after their DB write succeeds. `cli.py serve` gets
`--webhook-url`/`--webhook-secret` (envvar `HIL_WEBHOOK_URL`/`_SECRET`).

`judges/llm.py`: `llm(model, rubric, *, provider="openai"|"anthropic", api_key=None,
api_base=None, provider_fn=None)` — real providers POST via `urllib.request` to the
OpenAI/Anthropic chat endpoints, prompting for strict JSON `{confidence,label,reason}`
(invalid JSON → judge error). `provider_fn: Callable[[prompt],str]` injects a canned
response for tests with no network/keys. `judges/rule.py`: `rule(predicate, *,
on_true=1.0, on_false=0.0, label=None)`. `judges/logprob.py`: `logprob(extractor:
Callable|str, *, transform="sigmoid"|"identity")`.

**Acceptance tests** (`tests/test_webhooks.py`, `tests/test_judges.py`):
- In-process `http.server.HTTPServer`: valid HMAC on `item.created`/`review.created`; a
  500-returning receiver retried 3x (zero-delay backoff in tests) then dropped.
- `judges.llm(provider_fn=...)` returns a `Judgment` with no socket access (monkeypatched).
- `judges.rule`/`judges.logprob`: boundary/invalid inputs → judge error, not a raise.

## Phase 3 — Exports: HF/Parquet + H9 fields

**Files**: `exporters.py`, `pyproject.toml`, `cli.py`.

Add `[project.optional-dependencies] hf = ["pyarrow>=14"]`. `export_items`: every format's
metadata (and the `jsonl` record) gains `reason_codes`, `reason_text`, `choice` from the
latest `Review`. New `export_hf(store, project, path, *, statuses=..., include_images=True)`:
lazy `import pyarrow`, raise `ImportError` with an install hint if missing; builds one
`pa.Table` (same rows as `jsonl` plus `images: list<binary>`), writes via
`pq.write_table`. It's a separate function, not another `export_items` branch — Parquet
needs the whole table up front, unlike the streaming formats. `cli.py export --format hf`
requires a real `--output` path (stdout unsupported, raises `BadParameter`).

**Acceptance tests** (extend `tests/test_exporters.py`):
- All three JSON-ish formats include `reason_codes`/`reason_text`/`choice` for a
  `action=choose` item.
- `export_hf` round-trips via `pyarrow.parquet.read_table` (row count, columns, exact image
  bytes).
- `--format hf` without `pyarrow` fails with the install hint.

## Phase 4 — OpenAPI spec + generated TS client

**Files**: `openapi.yaml` (repo root), `cli.py`, `clients/ts/` (new package).

`cli.py openapi [--output openapi.yaml]`: `create_app(...).openapi()` dumped via
`yaml.safe_dump` (`pyyaml` added to the `server` extra). `tests/test_openapi.py`:
regenerate to a temp file, diff against the checked-in `openapi.yaml`. `clients/ts/`:
hand-written (OpenAPI is the shape source of truth, not a codegen input), mirrors
`ui/src/api.ts`'s typed-fetch style via `new HilEvalClient({baseUrl, apiKey})`. Package
`@hil-eval/client`: `package.json` (`scripts: {build: "tsc", test: "node --test
dist/*.test.js"}`), `tsconfig.json`, `src/index.ts` covering every M1–M3 endpoint
(`listItems`, `getItem`, `createItem` incl. base64 assets, `reviewItem` incl. `choose`,
`supersedeItems`, reason-code CRUD, `getTagThreshold`), `src/index.test.ts` using
`node:test` + `node:assert` with `globalThis.fetch` mocked.

**Acceptance tests**:
- `test_openapi.py` (above).
- `clients/ts`: `tsc` builds clean; `npm test` asserts URL/method/headers/body per method
  against mocked `fetch`.

## Phase 5 — UI: forms, diff, choice, reason codes, deep links, video

**Files**: `ui/src/main.tsx`, `ui/src/api.ts`, new `ui/src/{JsonForm,Diff}.tsx`,
`ui/e2e/*.spec.ts`, `ui/playwright.config.ts` (new), `ui/package.json`.

`api.ts`: extend `Item`/`Review`/`ReviewBody` with `options`, `choice`, `reason_codes`,
`reason_text`, `expires_at`; add `api.reasonCodes(project)`. `JsonForm.tsx`: schema
inference from `item.output`'s shape (object → field per key, array → repeatable rows),
replacing the raw-JSON textarea for structured outputs (string/number outputs keep the
textarea). `Diff.tsx`: small LCS-based word diff between `item.output` and the draft (no
new dependency — bounded, well-understood algorithm). `main.tsx`: `item.options` non-empty
→ render choice buttons (`submit("choose")`); reason-code multi-select from
`api.reasonCodes`; video assets (`mime.startsWith("video/")`) render `<video controls>`,
seek to `video_ts` on load; `bbox` overlays a positioned border on image assets. Deep links
(H10): `history.pushState` on selection change; on mount, parse `/items/:id` from
`location.pathname` and preselect (the existing SPA fallback in `_mount_static` already
serves `index.html` for that path). `ui/e2e/review.spec.ts`: `playwright.config.ts`
`webServer` runs `hil-eval serve --db <seeded tmp>`. Scenarios: approve; choose from
options; edit via `JsonForm` and confirm the diff view; direct navigation to `/items/<id>`.

**Acceptance tests**: unit tests for `JsonForm`/`Diff`; `npx playwright test` suite above.

## Phase 6 — CI, release workflow, docs

**Files**: `.github/workflows/publish.yml` (new), `.github/workflows/ci.yml`,
`docs/plan.md`, `README.md`.

`publish.yml`: `on: workflow_dispatch` + `push: tags: ["v*"]`; `environment: testpypi`;
`permissions: id-token: write`; steps checkout → `uv build` →
`pypa/gh-action-pypi-publish` (OIDC trusted publishing, no token). Authored, not invoked.
`ci.yml`: add `clients-ts` job (`npm ci && npm run build && npm test` in `clients/ts/`) and
`e2e` job (`npm ci`, install Playwright browsers, `npx playwright test` in `ui/`). Docs:
note in `docs/plan.md` that this expanded M3 supersedes the original 3-week-plan M3
milestone row, cross-referencing `docs/adr/0003-m3-contract.md`; add `hf` extras and
webhook/choice-review examples to `README.md`.

**Acceptance tests**: `publish.yml` parses clean (`actionlint` in the `lint` job, no live
run); `ci.yml` green end-to-end once phases 1–5 are merged.
