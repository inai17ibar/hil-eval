import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  api,
  imageUrl,
  type Action,
  type Filters,
  type Item,
  type Json,
  type Project,
  type Queue,
} from "./api";
import "./style.css";
const pretty = (value: Json) => JSON.stringify(value, null, 2);
const confidence = (value: number | null) =>
  value === null ? "—" : `${(value * 100).toFixed(1)}%`;
function Value({ value }: { value: Json }) {
  if (value && typeof value === "object") {
    if (!Array.isArray(value) && typeof value.$asset === "string")
      return (
        <img
          className="asset"
          src={imageUrl(value.$asset)}
          alt={`Asset ${value.$asset}`}
          loading="lazy"
        />
      );
    const hasAsset = (v: Json): boolean =>
      !!v &&
      typeof v === "object" &&
      ("$asset" in v || Object.values(v).some(hasAsset));
    if (hasAsset(value))
      return (
        <div className="structured">
          {Object.entries(value).map(([key, child]) => (
            <div key={key}>
              <strong>{key}</strong>
              <Value value={child} />
            </div>
          ))}
        </div>
      );
  }
  return <pre>{typeof value === "string" ? value : pretty(value)}</pre>;
}
function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [filters, setFilters] = useState<Filters>({
    project: "",
    status: "pending",
    min: "",
    max: "",
  });
  const [offset, setOffset] = useState(0);
  const [queue, setQueue] = useState<Queue>({ items: [], total: 0 });
  const [selected, setSelected] = useState<string | null>(null);
  const [item, setItem] = useState<Item | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [judge, setJudge] = useState("");
  const [note, setNote] = useState("");
  const invalid =
    [filters.min, filters.max].some(
      (v) =>
        v !== "" &&
        (!Number.isFinite(Number(v)) || Number(v) < 0 || Number(v) > 1),
    ) ||
    (filters.min !== "" &&
      filters.max !== "" &&
      Number(filters.min) > Number(filters.max));
  useEffect(() => {
    const c = new AbortController();
    api
      .projects(c.signal)
      .then(setProjects)
      .catch((e) => {
        if (!c.signal.aborted) setError(String(e));
      });
    return () => c.abort();
  }, []);
  useEffect(() => {
    if (invalid) return;
    const c = new AbortController();
    setLoading(true);
    setError("");
    api
      .queue(filters, offset, c.signal)
      .then((q) => {
        if (offset > 0 && !q.items.length) {
          setOffset(Math.max(0, offset - 50));
          return;
        }
        setQueue(q);
        setSelected((id) =>
          q.items.some((i) => i.id === id) ? id : (q.items[0]?.id ?? null),
        );
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(String(e));
      })
      .finally(() => {
        if (!c.signal.aborted) setLoading(false);
      });
    return () => c.abort();
  }, [filters, offset, revision, invalid]);
  useEffect(() => {
    setItem(null);
    setEditing(false);
    setJudge("");
    setNote("");
    if (!selected) {
      setDetailLoading(false);
      return;
    }
    const c = new AbortController();
    setDetailLoading(true);
    api
      .item(selected, c.signal)
      .then(setItem)
      .catch((e) => {
        if (!c.signal.aborted) setError(String(e));
      })
      .finally(() => {
        if (!c.signal.aborted) setDetailLoading(false);
      });
    return () => c.abort();
  }, [selected, revision]);
  function change(key: keyof Filters, value: string) {
    setFilters((f) => ({ ...f, [key]: value }));
    setOffset(0);
  }
  function move(delta: number) {
    if (busy || loading) return;
    const index = queue.items.findIndex((i) => i.id === selected);
    const next = queue.items[index + delta];
    if (next) setSelected(next.id);
  }
  function edit() {
    if (item && !busy) {
      setDraft(pretty(item.output));
      setEditing(true);
    }
  }
  async function submit(action: Action) {
    if (!item || busy || loading || invalid) return;
    let corrected: Json = draft;
    if (action === "edit") {
      try {
        corrected = JSON.parse(draft) as Json;
      } catch {
        corrected = draft;
      }
    }
    setBusy(true);
    setError("");
    try {
      await api.review(item.id, {
        action,
        judge_correct: judge === "" ? null : judge === "yes",
        ...(note ? { note } : {}),
        ...(action === "edit" ? { corrected_output: corrected } : {}),
      });
      const index = queue.items.findIndex((i) => i.id === item.id);
      setSelected(
        queue.items[index + 1]?.id ?? queue.items[index - 1]?.id ?? null,
      );
      setEditing(false);
      setRevision((r) => r + 1);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (event.key === "Escape" && editing && !busy) {
        event.preventDefault();
        setEditing(false);
        return;
      }
      if (
        (event.ctrlKey || event.metaKey) &&
        event.key === "Enter" &&
        editing
      ) {
        event.preventDefault();
        void submit("edit");
        return;
      }
      if (
        event.target instanceof HTMLElement &&
        event.target.closest(
          'input, textarea, select, [contenteditable="true"]',
        )
      )
        return;
      if (
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        editing ||
        busy ||
        event.repeat
      )
        return;
      const actions: Record<string, () => void> = {
        j: () => move(1),
        k: () => move(-1),
        a: () => void submit("approve"),
        e: edit,
        r: () => void submit("reject"),
        s: () => void submit("skip"),
      };
      if (actions[event.key]) {
        event.preventDefault();
        actions[event.key]();
      }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  });
  return (
    <>
      <header>
        <div>
          <span className="brand">hil-eval</span>
          <span className="subtitle">Human review</span>
        </div>
        <span className="count">{queue.total} matching items</span>
      </header>
      {error && (
        <div className="error" role="alert">
          {error}
          <button onClick={() => setRevision((r) => r + 1)}>Retry</button>
        </div>
      )}
      <main>
        <aside>
          <div className="filters">
            <h1>Review queue</h1>
            <label>
              Project
              <select
                value={filters.project}
                disabled={busy}
                onChange={(e) => change("project", e.target.value)}
              >
                <option value="">All projects</option>
                {projects.map((p) => (
                  <option key={p.name}>{p.name}</option>
                ))}
              </select>
            </label>
            <label>
              Status
              <select
                value={filters.status}
                disabled={busy}
                onChange={(e) => change("status", e.target.value)}
              >
                <option value="">All statuses</option>
                {[
                  "pending",
                  "approved",
                  "corrected",
                  "rejected",
                  "skipped",
                ].map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
            <div className="range">
              {(["min", "max"] as const).map((key) => (
                <label key={key}>
                  {key === "min" ? "Min" : "Max"} confidence
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    placeholder={key === "min" ? "0" : "1"}
                    value={filters[key]}
                    disabled={busy}
                    onChange={(e) => change(key, e.target.value)}
                  />
                </label>
              ))}
            </div>
            {invalid && (
              <p role="alert">
                Use confidence values between 0 and 1, with min ≤ max.
              </p>
            )}
          </div>
          <nav aria-label="Review items" aria-busy={loading}>
            {loading ? (
              <p className="muted">Loading queue…</p>
            ) : queue.items.length ? (
              queue.items.map((i) => (
                <button
                  className={`queue-item ${selected === i.id ? "selected" : ""}`}
                  key={i.id}
                  disabled={busy || invalid}
                  onClick={() => setSelected(i.id)}
                  aria-current={selected === i.id ? "true" : undefined}
                >
                  <div>
                    <strong>{i.fn_name || "Unnamed function"}</strong>
                    <span className="score">{confidence(i.confidence)}</span>
                  </div>
                  <div>
                    <span className={`badge ${i.status}`}>{i.status}</span>
                    <span>{i.project}</span>
                  </div>
                  <time dateTime={i.created_at}>
                    {new Date(i.created_at).toLocaleString()}
                  </time>
                </button>
              ))
            ) : (
              <p className="muted">No items match these filters.</p>
            )}
          </nav>
          <div className="pagination">
            <button
              disabled={offset === 0 || busy || loading}
              onClick={() => setOffset((o) => o - 50)}
            >
              ← Previous
            </button>
            <span>
              {queue.total ? offset + 1 : 0}–{offset + queue.items.length}
            </span>
            <button
              disabled={offset + 50 >= queue.total || busy || loading}
              onClick={() => setOffset((o) => o + 50)}
            >
              Next →
            </button>
          </div>
        </aside>
        <article aria-busy={detailLoading}>
          {detailLoading ? (
            <p className="empty">Loading item…</p>
          ) : item ? (
            <>
              <div className="detail-heading">
                <div>
                  <p className="eyebrow">
                    {item.project} / {item.routed_by}
                  </p>
                  <h1>{item.fn_name || "Unnamed function"}</h1>
                  <p className="muted">
                    {new Date(item.created_at).toLocaleString()} · {item.id}
                  </p>
                </div>
                <span className={`badge ${item.status}`}>{item.status}</span>
              </div>
              <div className="metadata">
                <div>
                  <span>Confidence</span>
                  <strong>{confidence(item.confidence)}</strong>
                </div>
                <div>
                  <span>Judge label</span>
                  <strong>{item.judge_label ?? "—"}</strong>
                </div>
                <div>
                  <span>Routed by</span>
                  <strong>{item.routed_by}</strong>
                </div>
              </div>
              <section>
                <h2>Judge reason</h2>
                <p className="reason">
                  {item.judge_reason || "No reason provided."}
                </p>
              </section>
              <div className="tags">
                {item.tags.map((tag, index) => (
                  <span className="badge" key={`${tag}-${index}`}>
                    {tag}
                  </span>
                ))}
              </div>
              <section>
                <h2>Input</h2>
                <Value value={item.input} />
              </section>
              {item.assets.length > 0 && (
                <section>
                  <h2>Assets</h2>
                  <div className="assets">
                    {item.assets.map((asset, index) => (
                      <figure key={`${asset.sha256}-${index}`}>
                        <img
                          className="asset"
                          src={imageUrl(asset.sha256)}
                          alt={`${asset.role} asset`}
                          loading="lazy"
                        />
                        <figcaption>
                          {asset.role} · {asset.mime}
                        </figcaption>
                      </figure>
                    ))}
                  </div>
                </section>
              )}
              <section>
                <h2>Output</h2>
                <pre>{pretty(item.output)}</pre>
                <ValueImages value={item.output} />
              </section>
              <section className="review">
                <h2>Your review</h2>
                <fieldset disabled={busy || loading || invalid}>
                  <label>
                    Judge was right?
                    <select
                      value={judge}
                      onChange={(e) => setJudge(e.target.value)}
                    >
                      <option value="">Unset</option>
                      <option value="yes">Yes</option>
                      <option value="no">No</option>
                    </select>
                  </label>
                  <label>
                    Note <span className="muted">(optional)</span>
                    <textarea
                      rows={2}
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder="Add context for this review"
                    />
                  </label>
                  {editing && (
                    <div className="editor">
                      <label>
                        Corrected output
                        <textarea
                          autoFocus
                          rows={10}
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          spellCheck={false}
                        />
                      </label>
                      <p className="muted">
                        Valid JSON is saved as structured data. Other text is
                        saved as a string.
                      </p>
                      <div className="actions">
                        <button
                          className="primary"
                          onClick={() => void submit("edit")}
                        >
                          Save edit
                        </button>
                        <button onClick={() => setEditing(false)}>
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                  {!editing && (
                    <div className="actions">
                      <button
                        className="primary"
                        onClick={() => void submit("approve")}
                      >
                        Approve <kbd>a</kbd>
                      </button>
                      <button onClick={edit}>
                        Edit <kbd>e</kbd>
                      </button>
                      <button
                        className="danger"
                        onClick={() => void submit("reject")}
                      >
                        Reject <kbd>r</kbd>
                      </button>
                      <button onClick={() => void submit("skip")}>
                        Skip <kbd>s</kbd>
                      </button>
                    </div>
                  )}
                  {busy && <p role="status">Saving review…</p>}
                </fieldset>
              </section>
              <section>
                <h2>
                  Previous reviews{" "}
                  <span className="muted">({item.reviews?.length ?? 0})</span>
                </h2>
                {item.reviews?.length ? (
                  item.reviews.map((review) => (
                    <div className="past-review" key={review.id}>
                      <strong>{review.action}</strong> ·{" "}
                      {review.reviewer || "Anonymous"}
                      <time>
                        {new Date(review.created_at).toLocaleString()}
                      </time>
                      <p>
                        Judge was right:{" "}
                        {review.judge_correct === null
                          ? "Unset"
                          : review.judge_correct
                            ? "Yes"
                            : "No"}
                      </p>
                      {review.note && <p className="reason">{review.note}</p>}
                      {review.action === "edit" && (
                        <pre>{pretty(review.corrected_output)}</pre>
                      )}
                    </div>
                  ))
                ) : (
                  <p className="muted">No reviews yet.</p>
                )}
              </section>
            </>
          ) : (
            <p className="empty">Select an item to start reviewing.</p>
          )}
        </article>
      </main>
      <footer>
        <span>
          <kbd>j</kbd> / <kbd>k</kbd> next / previous
        </span>
        <span>
          <kbd>a</kbd> approve
        </span>
        <span>
          <kbd>e</kbd> edit
        </span>
        <span>
          <kbd>r</kbd> reject
        </span>
        <span>
          <kbd>s</kbd> skip
        </span>
        <span>
          <kbd>Ctrl / ⌘ Enter</kbd> save edit
        </span>
        <span>
          <kbd>Esc</kbd> cancel edit
        </span>
      </footer>
    </>
  );
}
function ValueImages({ value }: { value: Json }) {
  if (!value || typeof value !== "object") return null;
  if (!Array.isArray(value) && typeof value.$asset === "string")
    return (
      <img
        className="asset"
        src={imageUrl(value.$asset)}
        alt="Output asset"
        loading="lazy"
      />
    );
  return (
    <>
      {Object.entries(value).map(([key, child]) => (
        <ValueImages key={key} value={child} />
      ))}
    </>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
