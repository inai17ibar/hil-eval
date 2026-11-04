import { AssetPreview } from "./AssetPreview";
import { describe, it, expect, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ReviewControls } from "./ReviewControls";
import { JsonForm, emptyValue } from "./JsonForm";
import { Diff, wordDiff } from "./Diff";
import type { Json } from "./api";

describe("JsonForm", () => {
  it("infers nested fields and repeatable rows with typed controls", () => {
    const html = renderToStaticMarkup(
      <JsonForm
        value={{
          name: "Alice",
          count: 2,
          ok: true,
          rows: ["a"],
          missing: null,
        }}
        onChange={() => {}}
      />,
    );
    expect(html).toContain('value="Alice"');
    expect(html).toContain('type="number"');
    expect(html).toContain('type="checkbox"');
    expect(html).toContain("Add rows row");
    expect(html).toContain("Remove rows 1");
    expect(html).toContain("JSON value");
  });
  it("preserves primitive types in new row templates", () => {
    expect(
      emptyValue({ name: "x", n: 1, ok: true, rows: [1], absent: null }),
    ).toEqual({ name: "", n: 0, ok: false, rows: [], absent: null });
  });
  it("updates object fields and array rows without changing siblings", () => {
    let result: Json = null;
    const form = JsonForm({
      value: { x: "old", y: 3 },
      onChange: (v) => {
        result = v;
      },
    });
    const children = form.props.children;
    children[1][0].props.onChange("new");
    expect(result).toEqual({ x: "new", y: 3 });
    const rows = JsonForm({
      value: ["a", "b"],
      onChange: (v) => {
        result = v;
      },
    });
    rows.props.children[1][0].props.children[0].props.onChange("c");
    expect(result).toEqual(["c", "b"]);
    rows.props.children[1][0].props.children[1].props.onClick();
    expect(result).toEqual(["b"]);
    rows.props.children[2].props.onClick();
    expect(result).toEqual(["a", "b", ""]);
  });
});
describe("Diff", () => {
  it("reconstructs both sides, including whitespace and empty strings", () => {
    for (const [before, after] of [
      ["one two", "one three"],
      ["", "new"],
      ["old", ""],
      ["a\n b", "a  c"],
      ["same", "same"],
    ]) {
      const parts = wordDiff(before, after);
      expect(
        parts
          .filter((p) => p.kind !== "add")
          .map((p) => p.text)
          .join(""),
      ).toBe(before);
      expect(
        parts
          .filter((p) => p.kind !== "remove")
          .map((p) => p.text)
          .join(""),
      ).toBe(after);
    }
  });
  it("renders semantic insertions and deletions and bounds large diffs", () => {
    const html = renderToStaticMarkup(<Diff before="old" after="new" />);
    expect(html).toContain("<del>old</del>");
    expect(html).toContain("<ins>new</ins>");
    expect(wordDiff("x ".repeat(1000), "y ".repeat(1000))).toHaveLength(2);
  });
});

describe("review regression controls", () => {
  it("disables review actions for superseded items", () => {
    const html = renderToStaticMarkup(
      <ReviewControls status="superseded" disabled={false}>
        <button>Approve</button>
      </ReviewControls>,
    );
    expect(html).toContain('disabled=""');
    expect(
      renderToStaticMarkup(
        <ReviewControls status="pending" disabled={false}>
          <button>Approve</button>
        </ReviewControls>,
      ),
    ).not.toContain("disabled");
  });
  it.each(['"answer"', "42", "true", "[1]", '{"answer":1}'])(
    "replaces null with JSON %s",
    (text) => {
      let result: Json = null;
      const form = JsonForm({
        value: null,
        onChange: (next) => {
          result = next;
        },
      });
      form.props.children[1].props.onChange({
        target: { value: text, setCustomValidity: () => {} },
      });
      expect(result).toEqual(JSON.parse(text));
    },
  );
  it("rejects invalid JSON without changing the correction", () => {
    let result: Json = null;
    let error = "";
    const form = JsonForm({
      value: null,
      onChange: (next) => {
        result = next;
      },
    });
    form.props.children[1].props.onChange({
      target: {
        value: "{",
        setCustomValidity: (value: string) => {
          error = value;
        },
      },
    });
    expect(result).toBeNull();
    expect(error).toBe("Enter valid JSON");
  });
});

vi.mock("./api", () => ({ imageUrl: (sha: string) => `/v1/blobs/${sha}` }));

describe("audio assets", () => {
  it("renders a playable audio control with metadata preload", () => {
    const html = renderToStaticMarkup(
      <AssetPreview
        asset={{
          sha256: "audio-hash",
          mime: "audio/wav",
          role: "input",
          order: 0,
          video_ts: 0.25,
          bbox: null,
          width: null,
          height: null,
          url: "/v1/blobs/audio-hash",
        }}
      />,
    );
    expect(html).toContain("<audio");
    expect(html).toContain('controls=""');
    expect(html).toContain('preload="metadata"');
    expect(html).toContain('src="/v1/blobs/audio-hash"');
  });
});
