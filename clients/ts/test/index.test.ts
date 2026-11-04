import { test } from "node:test";
import assert from "node:assert/strict";
import { HilEvalClient } from "../src/index.js";

test("every endpoint serializes its request and returns its response", async () => {
  const original = globalThis.fetch;
  let last: { url: string; init: RequestInit };
  globalThis.fetch = async (url, init) => {
    last = { url: String(url), init: init! };
    if (init?.method === "DELETE") return new Response(null, { status: 204 });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  const c = new HilEvalClient({ baseUrl: "http://example.test/", apiKey: "secret" });
  async function check(call: () => Promise<unknown>, path: string, method = "GET", body?: unknown) {
    await call();
    assert.equal(last.url, "http://example.test" + path);
    assert.equal(last.init.method, method);
    assert.deepEqual(last.init.headers, { "X-API-Key": "secret", ...(body === undefined ? {} : { "Content-Type": "application/json" }) });
    assert.equal(last.init.body, body === undefined ? undefined : JSON.stringify(body));
  }
  try {
    await check(() => c.health(), "/healthz");
    await check(() => c.listProjects(), "/v1/projects");
    await check(() => c.listItems(), "/v1/items");
    await check(() => c.listItems({ project: "a b", status: ["pending", "superseded"], min_confidence: 0, max_confidence: 1, limit: 5, offset: 2, include_expired: true }), "/v1/items?project=a+b&status=pending&status=superseded&min_confidence=0&max_confidence=1&limit=5&offset=2&include_expired=true");
    await check(() => c.getItem("a/b"), "/v1/items/a%2Fb");
    const item = { project: "p", input: null, output: { x: 1 }, options: [null, "yes"], assets: [{ data_base64: "AAAA", role: "context" as const, video_ts: 2 }] };
    await check(() => c.createItem(item), "/v1/items", "POST", item);
    const review = { action: "choose" as const, choice: null, reason_codes: ["quality"], reason_text: "why" };
    await check(() => c.reviewItem("a/b", review), "/v1/items/a%2Fb/review", "POST", review);
    await check(() => c.deleteItem("id"), "/v1/items/id", "DELETE");
    const supersede = { project: "p", tag_key: "model", tag_value: "v1" };
    await check(() => c.supersedeItems(supersede), "/v1/items/supersede", "POST", supersede);
    await check(() => c.listReasonCodes("a/b"), "/v1/projects/a%2Fb/reason-codes");
    const reason = { code: "quality", label: "Quality", polarity: "negative" };
    await check(() => c.registerReasonCode("p", reason), "/v1/projects/p/reason-codes", "POST", reason);
    await check(() => c.getTagThreshold("p", "model:a b"), "/v1/projects/p/threshold?tag=model%3Aa+b");
    await check(() => c.getTagThreshold("p"), "/v1/projects/p/threshold");
    const threshold = { tag_key: "model", tag_value: "v1", threshold: 0.6 };
    await check(() => c.setTagThreshold("p", threshold), "/v1/projects/p/threshold", "PUT", threshold);
    assert.ok(await c.getBlob("abc") instanceof ArrayBuffer);
    assert.equal(last!.url, "http://example.test/v1/blobs/abc");
    globalThis.fetch = async () => new Response("denied", { status: 401 });
    await assert.rejects(c.getItem("id"), /401.*denied/);
    globalThis.fetch = async (_url, init) => {
      assert.deepEqual(init?.headers, {});
      return new Response('{"ok":true}');
    };
    assert.deepEqual(await new HilEvalClient({ baseUrl: "http://example.test" }).health(), { ok: true });
  } finally { globalThis.fetch = original; }
});
