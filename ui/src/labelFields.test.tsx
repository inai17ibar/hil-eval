import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { FieldDefinition } from "./api";
import { LabelForm, SEARCH_THRESHOLD } from "./LabelForm";
import {
  changedFields,
  choicesFor,
  fieldErrors,
  filterChoices,
  resetField,
  same,
  setValue,
} from "./labelFields";

const FIELDS: FieldDefinition[] = [
  { name: "family", kind: "enum", choices: ["drums", "keys", "bass"] },
  {
    name: "leaf",
    kind: "enum",
    depends_on: "family",
    choices: { drums: ["kick", "snare"], keys: ["piano"], bass: ["808"] },
  },
  {
    name: "type",
    kind: "enum",
    choices: ["loop", "one_shot"],
    nullable: false,
  },
  {
    name: "bpm",
    kind: "number",
    min: 40,
    max: 240,
    integer: true,
    multipliers: [0.5, 2],
  },
  { name: "key", kind: "text", suggestions: ["C major"] },
];
const PROPOSED = {
  family: "drums",
  leaf: "kick",
  type: "loop",
  bpm: 100,
  key: null,
};

describe("label field logic", () => {
  it("compares like the server", () => {
    expect(same(100, 100.0)).toBe(true);
    expect(same(true, 1)).toBe(false);
    expect(same(null, undefined)).toBe(true);
    expect(changedFields(PROPOSED, { ...PROPOSED, bpm: 50 })).toEqual(["bpm"]);
  });
  it("filters hierarchical choices by the parent value", () => {
    expect(choicesFor(FIELDS[1], { family: "keys" })).toEqual(["piano"]);
    expect(choicesFor(FIELDS[1], { family: null })).toEqual([
      "808",
      "kick",
      "piano",
      "snare",
    ]);
    expect(choicesFor(FIELDS[1], { family: "nope" })).toEqual([]);
  });
  it("ranks prefix matches first when filtering", () => {
    expect(filterChoices(["snare", "kick", "rimshot", "shaker"], "s")).toEqual([
      "snare",
      "shaker",
      "rimshot",
    ]);
    expect(filterChoices(["a"], " ")).toEqual(["a"]);
  });
  it("clears or snaps children when the parent changes", () => {
    expect(setValue(FIELDS, PROPOSED, "family", "keys").leaf).toBe("piano");
    const toBass = setValue(
      [
        FIELDS[0],
        { ...FIELDS[1], choices: { drums: ["kick"], bass: ["808", "sub"] } },
      ],
      PROPOSED,
      "family",
      "bass",
    );
    expect(toBass.leaf).toBeNull();
    expect(setValue(FIELDS, PROPOSED, "family", "drums").leaf).toBe("kick");
    const open = [FIELDS[0], { ...FIELDS[1], allow_custom: true }];
    expect(setValue(open, PROPOSED, "family", "bass").leaf).toBe("808");
  });
  it("resets a parent and restores dependents that no longer fit", () => {
    const edited = { ...PROPOSED, family: "keys", leaf: "piano", bpm: 50 };
    const reset = resetField(FIELDS, edited, PROPOSED, "family");
    expect(reset).toEqual({ ...PROPOSED, bpm: 50 });
    const kept = resetField(
      FIELDS,
      { ...PROPOSED, leaf: "snare" },
      PROPOSED,
      "family",
    );
    expect(kept.leaf).toBe("snare");
  });
  it("validates only changed fields", () => {
    expect(
      fieldErrors(
        FIELDS,
        { ...PROPOSED, leaf: "full" },
        { ...PROPOSED, leaf: "full" },
      ),
    ).toEqual({});
    const errors = fieldErrors(FIELDS, PROPOSED, {
      ...PROPOSED,
      bpm: 300,
      type: null,
      key: 3 as unknown as string,
    });
    expect(Object.keys(errors).sort()).toEqual(["bpm", "key", "type"]);
    expect(
      fieldErrors(FIELDS, PROPOSED, { ...PROPOSED, bpm: 99.5 }).bpm,
    ).toMatch(/whole/);
    expect(
      fieldErrors(FIELDS, PROPOSED, {
        ...PROPOSED,
        bpm: "x" as unknown as number,
      }).bpm,
    ).toMatch(/number/);
  });
});

describe("LabelForm", () => {
  it("prefills chips and highlights changed rows with the original value", () => {
    const html = renderToStaticMarkup(
      <LabelForm
        fields={FIELDS}
        proposed={PROPOSED}
        values={{ ...PROPOSED, family: "keys", leaf: "piano" }}
        onChange={() => {}}
      />,
    );
    expect(html.match(/label-row changed/g)).toHaveLength(2);
    expect(html).toContain('<del title="Proposed">drums</del>');
    expect(html).toContain('<ins title="Corrected">keys</ins>');
    expect(html).toContain('aria-label="Reset family"');
    expect(html).toMatch(/aria-checked="true"[^>]*>keys</);
    expect(html).toContain("chip proposed"); // the original stays visible
    expect(html).toContain("×0.5");
    expect(html).toContain('inputMode="numeric"');
    expect(html).toContain("<datalist");
    expect(html).toContain("as proposed");
    expect(html).toContain('data-field-index="1"');
  });
  it("shows a filter box for long or open choice lists and flags out-of-schema values", () => {
    const many = Array.from(
      { length: SEARCH_THRESHOLD + 1 },
      (_, i) => `c${i}`,
    );
    const html = renderToStaticMarkup(
      <LabelForm
        fields={[
          { name: "inst", kind: "enum", choices: many },
          { name: "leaf", kind: "enum", choices: ["a"] },
        ]}
        proposed={{ inst: "c1", leaf: "full" }}
        values={{ inst: "c1", leaf: "full" }}
        onChange={() => {}}
      />,
    );
    expect(html.match(/type="search"/g)).toHaveLength(1);
    expect(html).toContain("chip selected proposed outside");
    expect(html).toContain("— none");
  });
});
