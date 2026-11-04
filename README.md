# hil-eval

> Working name. Status: pre-alpha.

Add **human-in-the-loop review** to AI outputs in a few lines: a judge scores every
output, and only low-confidence items (plus a small random audit sample) land in a
local review queue backed by SQLite. Images are stored content-addressed by SHA-256.

## Install (from source)

```bash
uv sync            # or: pip install -e .   (add [image] for Pillow support)
```

## Example

```python
import hil_eval as hil

hil.init(project="demo", store="sqlite:///.hil/hil.db")


def judge(inputs, output):
    return hil.Judgment(confidence=0.4 if "?" in output else 0.95, reason="hedged answer")


@hil.review(judge=judge, threshold=0.8, audit_rate=0.0)
def answer(question: str) -> str:
    return "Maybe 42?" if "life" in question else "Paris"


answer("capital of France")  # high confidence -> not queued
answer("meaning of life")  # low confidence  -> queued for review
print([(i.input, i.output, i.routed_by.value) for i in hil.list_items()])
```

The decorated function's return value is always passed through unchanged; judge or
storage failures are logged and never break your app (fail-open). Async functions work too.

- `threshold="auto"` uses the project's stored threshold (falls back to 0.8).
- `images=["image"]` marks arguments as images (paths, bytes, PIL) stored by SHA-256.
- `audit_rate` routes a random fraction of high-confidence items for unbiased tuning.
- `hil.submit(input=..., output=..., confidence=...)` enqueues an item manually.

See `docs/` for the design and roadmap (Japanese).

## Development

```bash
uv sync && uv run pytest && uv run ruff check . && uv run ruff format --check .
```

## License

MIT

## Review UI and HTTP API

```bash
pip install 'hil-eval[server,image]'
python examples/demo_review.py --db .hil/hil.db
hil-eval serve --db .hil/hil.db
# Open http://127.0.0.1:8765, or use hil-eval ui to open a browser automatically.
```

Approve, edit, reject, or skip queued outputs in the UI. `serve --no-ui` runs
only the API. Use `--api-key` (or `HIL_API_KEY`) when binding a non-loopback host.
The global `--db` option (or `HIL_DB`) selects the database for every command:
`hil-eval --db .hil/hil.db projects`. Published wheels include the UI; source
checkouts need `cd ui && npm ci && npm run build` first.

## Export reviewed data

```bash
hil-eval export --project demo --format jsonl -o reviews.jsonl
hil-eval export --project demo --format openai-ft --status corrected -o train.jsonl
hil-eval export --project demo --format eval-set --no-images -o eval.jsonl
```

Exports default to approved and corrected items using each item's latest review.
OpenAI fine-tuning exports embed input images as data URLs; `--no-images` omits
those images. `-o -` writes to stdout. In Python, `hil.export(fmt="eval-set")`
returns an iterator of records using the project and store from `hil.init()`.

## Tune thresholds

```bash
hil-eval tune --project demo --target-miss-rate 0.05
hil-eval tune --project demo --apply
hil-eval history --project demo
```

Tuning reports the current and recommended thresholds, sample count, estimated
review/miss rates, and the miss-rate upper bound. At least 30 usable reviews are
required. `--apply` saves the recommendation; `@hil.review(threshold="auto", ...)`
picks up the saved threshold on subsequent calls.
