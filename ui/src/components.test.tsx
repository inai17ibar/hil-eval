import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
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
    expect(html).toContain('value="null"');
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
