# Architecture — Milestone 1 core (designed with Claude Code)

## Package layout (src layout)
```
src/hil_eval/
  __init__.py       # public API: init, review, submit, Judgment, Item, Status, RoutedBy
  types.py          # Judgment, Item, AssetRef, ProjectConfig, enums (stdlib dataclasses, zero deps)
  config.py         # DEFAULT_THRESHOLD, DEFAULT_AUDIT_RATE, DEFAULT_TARGET_MISS_RATE
  context.py        # module-level default Client + Client class (multi-project support)
  decorator.py      # @review, sync+async wrapper, binding/serialization
  blobs.py          # content-addressed store: sha256, path/bytes/PIL.Image
  store/
    __init__.py
    base.py         # Store Protocol
    sqlite.py       # SQLiteStore (stdlib sqlite3 only)
```

## Types (`types.py`, stdlib `dataclasses`, no pydantic/SQLModel)
```python
class Status(str, Enum): pending = "pending"; approved = "approved"; corrected = "corrected"; rejected = "rejected"; skipped = "skipped"
class RoutedBy(str, Enum): low_conf = "low_conf"; audit = "audit"; manual = "manual"
class AssetRole(str, Enum): input = "input"; output = "output"

@dataclass(frozen=True)
class Judgment:
    confidence: float
    label: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    def __post_init__(self):
        c = self.confidence
        if not isinstance(c, (int, float)) or isinstance(c, bool) or math.isnan(c) or math.isinf(c) or not (0.0 <= c <= 1.0):
            raise ValueError(f"confidence must be a finite float in [0,1], got {c!r}")

@dataclass
class AssetRef:
    sha256: str; role: AssetRole; mime: str; path: str
    width: int | None = None; height: int | None = None

@dataclass
class Item:
    id: str              # uuid4 hex, assigned client-side (store-portable, exportable pre-insert)
    project: str
    fn_name: str | None
    input: Any; output: Any          # JSON-serializable (post-sanitization)
    confidence: float | None
    judge_label: str | None; judge_reason: str | None; judge_metadata: dict
    routed_by: RoutedBy; status: Status
    tags: dict[str, str]
    trace_id: str | None
    created_at: datetime             # UTC, set by caller (not inside store, for testability)
    assets: list[AssetRef] = field(default_factory=list)

@dataclass
class ProjectConfig:
    name: str; threshold: float | None; target_miss_rate: float; audit_rate: float
```

## Store protocol (`store/base.py`)
```python
class Store(Protocol):
    def ensure_project(self, name: str, *, threshold: float|None, target_miss_rate: float|None, audit_rate: float|None) -> ProjectConfig: ...
    def get_project(self, name: str) -> ProjectConfig | None: ...
    def update_threshold(self, project: str, threshold: float) -> None: ...
    def save_item(self, item: Item) -> Item: ...
    def list_items(self, project: str, *, status: Status|None=None, limit: int=100, offset: int=0) -> list[Item]: ...
    def put_blob(self, sha256: str, data: bytes, mime: str) -> str: ...   # returns path
    def get_blob_path(self, sha256: str) -> Path | None: ...
```
Minimal for M1: `Review`/`ThresholdHistory` tables deferred to M2 (no review UI yet); `save_review` not in protocol yet.

**SQLiteStore**: one short-lived `sqlite3.connect()` per operation (`check_same_thread=True`, default) rather than a shared long-lived connection — avoids cross-thread sharing bugs entirely. `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=2000` set on connect. Writes wrapped in a small retry loop (3 attempts, backoff 20/60/150ms) on `sqlite3.OperationalError: database is locked` — WAL mostly avoids this but defends against slow readers/AV scanners on Windows. Directory for db + `blobs/` created with `parents=True, exist_ok=True` on first use.

## Blob store (`blobs.py`)
`store_image(image: str | Path | bytes | "PIL.Image.Image") -> AssetRef`
- `bytes` → used as-is; mime sniffed from magic bytes (PNG/JPEG/GIF/WEBP header check, else `application/octet-stream`).
- `str | Path` → read file bytes; mime via `mimetypes.guess_type`.
- `PIL.Image.Image` → lazy `import PIL` only on this branch (optional dep, not required to install `hil_eval`); re-encoded to PNG bytes via `io.BytesIO`; width/height read from `.size`.
- sha256 of the final raw bytes; path = `<store_dir>/blobs/<sha256>` (flat, per F2.1). Write via tempfile + `os.replace` for atomicity; skip write if path already exists (content-addressed = naturally idempotent/dedup'd).
- URL input **out of scope for M1** (needs network fetch / extra dep) — raises `NotImplementedError` with a clear message rather than silently failing.

## Public API

```python
hil.init(project: str, store: str = "sqlite:///.hil/hil.db", *, threshold: float|None=None,
          target_miss_rate: float|None=None, audit_rate: float|None=None) -> None
```
Parses `store` URL (only `sqlite://` scheme in M1 — others raise `ValueError`). Sets the module-level default `Client`. Upserts the project row: any explicitly-passed (non-`None`) kwarg overwrites stored config; omitted kwargs preserve existing stored values on repeat calls, or fall back to defaults (`threshold=None`, `target_miss_rate=0.05`, `audit_rate=0.02`) on first creation.

```python
@hil.review(judge: Callable[..., Judgment|float], threshold: float|Literal["auto"]=0.8,
            audit_rate: float|None=None, inputs: list[str]|None=None,
            tags: dict[str,str]|None=None, project: str|None=None)
def fn(...): ...
```
Works on sync and async `def`. Resolution order: `project` kwarg → default client's project.

**Execution**: bind args via `inspect.signature(fn).bind(*a, **kw)`; call the real `fn` **outside any try/except** — the wrapped function's own exceptions always propagate unchanged. Only the *recording* path (judge call → routing → persistence) is wrapped in `try/except Exception`, logged via `logging.getLogger("hil_eval")`, and swallowed — this is "fail-open."

**Routing decision** (inside the guarded block):
1. `audited = Random(per-decoration-seed).random() < audit_rate` — decided *before* invoking judge, independent of its outcome.
2. Run judge; normalize return value to `Judgment` (see edge cases).
3. If judge failed/invalid **and not audited** → drop, nothing persisted (true fail-open, zero cost).
4. If judge failed/invalid **and audited** → persist with `confidence=None`, `judge_reason="judge_error: ..."`, `routed_by=audit` (still useful ground-truth for tune, excluded from confidence-bucketed stats).
5. Else: `routed_by = audit if audited else (low_conf if confidence < resolved_threshold else None)`. Only `low_conf`/`audit` items are persisted — high-confidence, non-sampled calls incur zero storage cost (matches F1.2/F1.5 intent: the store *is* the review queue, not a full trace log).

**Threshold resolution**: `"auto"` re-reads `store.get_project(project).threshold` on *every call* (not cached) so a `hil-eval tune --apply` mid-process takes effect immediately; falls back to `DEFAULT_THRESHOLD = 0.8` if the project has no stored threshold yet (logs once at INFO).

```python
hil.submit(input, output, *, confidence: float|None=None, judge_label=None, judge_reason=None,
           tags=None, project=None, trace_id=None) -> Item | None
```
Always persisted with `routed_by=manual` regardless of confidence/threshold (explicit call = explicit intent) — the only case it returns `None` is "not initialized" or an unrecoverable store error after retries, both logged.

**Async**: judge must be a plain sync callable in M1 (built-in async LLM-judges are M2 scope). For async `fn`, the actual DB write is offloaded via `asyncio.to_thread` so the blocking `sqlite3` call doesn't stall the event loop; sync `fn` writes inline.

## Edge cases & decisions (concise)

1. **Non-JSON output/input**: sanitize with `json.dumps(x, default=str)`; if that still throws, store `{"__repr__": repr(x), "__type__": type(x).__qualname__}` + log warning. Never raises into caller.
2. **Judge raises exception**: caught, treated as "judge error" (case 3/4 above). Original `fn` output still returned to caller untouched.
3. **Judge returns float**: wrapped as `Judgment(confidence=float, label=None, reason=None)`. Non-numeric/bool return → judge error.
4. **Judge returns `Judgment`**: used as-is; its own validation (range/NaN) runs in `__post_init__`, funneling into the same judge-error path.
5. **Confidence out of range or NaN/inf**: rejected, not clamped — clamping would silently corrupt the tune algorithm's IPW math; logged with offending value.
6. **Generators/async generators**: rejected at **decoration time** (`TypeError`), not at call time — streaming semantics are undefined for M1 and this is a programmer error, not a fail-open scenario.
7. **Methods**: `self`/`cls` auto-excluded from captured input by name if `inputs` not given; when `inputs` *is* given, only those names are recorded (recommended for methods to dodge unpicklable `self`).
8. **Missing `hil.init()`**: decorator/`submit` no-op the recording path with a one-time warning; wrapped function always still runs and returns normally.
9. **Concurrent writes**: no shared connection; SQLite WAL + `busy_timeout` + 3x backoff retry on `OperationalError`. No global lock in-process — correctness delegated to SQLite.
10. **Thread safety**: one connection per operation, never shared across threads; `audit_rate` RNG uses a private `random.Random` instance per decorated function (not the global `random` module) to avoid interfering with user's own seeding.
11. **`threshold="auto"` with no tuned value yet**: falls back to `DEFAULT_THRESHOLD=0.8`, logged once, never raises.
12. **Image args mixed into `inputs`**: values matching path/bytes/PIL types get blob-stored and replaced with `{"$asset": sha256}` in the JSON `input`; non-image values pass through `json.dumps` sanitization.
13. **`hil.init()` called twice with different project**: allowed — supports multi-project processes via `Client`; default client is last-`init`-wins, documented as "single default project" convention.
14. **Store write fails after retries (disk full, permissions)**: swallowed for `@review` (fail-open); `submit` returns `None` and logs at ERROR (explicit call, so caller may want to know, but never raises).

## Post-review decisions (Codex review of M1)
- `inputs` = whitelist of argument names to **store**; the judge always receives all bound arguments (minus `self`/`cls`). Unknown names raise `ValueError` at decoration time.
- `images` = argument names explicitly treated as images (path `str`/`Path`, bytes, PIL). Implicit detection elsewhere is limited to PIL images, bytes with a PNG/JPEG/GIF/WEBP signature, and existing image `Path` objects. Plain strings (incl. URLs, `"x.png"`) are never implicitly images.
- A failing asset (e.g. missing file) is recorded as `{"$asset_error": "..."}`; the item is still saved.
- `threshold`, `audit_rate`, `target_miss_rate` are validated (finite, non-bool, in [0, 1]) in `review`, `init`, and the store.
- The async wrapper guards the `asyncio.to_thread` offload so recording can never replace the function's result.
