# hil-eval

> Working name. Status: pre-alpha (Milestone 1 of the MVP).

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

Roadmap: review UI (React), HTTP API, JSONL / OpenAI fine-tuning export, automatic
threshold tuning. See `docs/` (Japanese).

## Development

```bash
uv sync && uv run pytest && uv run ruff check . && uv run ruff format --check .
```

## License

MIT
