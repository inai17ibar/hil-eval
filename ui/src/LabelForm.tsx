import { useState } from "react";
import type { FieldDefinition, Json } from "./api";
import {
  changedFields,
  choicesFor,
  display,
  fieldErrors,
  filterChoices,
  same,
  setValue,
  type Values,
} from "./labelFields";

/** Enum fields with more choices than this get a filter box above the chips. */
export const SEARCH_THRESHOLD = 8;

export interface LabelFormProps {
  fields: FieldDefinition[];
  proposed: Values;
  values: Values;
  onChange: (values: Values) => void;
}

/**
 * Structured label correction: every field is prefilled with the proposed value,
 * changed rows are highlighted with the original struck through, and each row
 * can be reset on its own. Keyboard: 1–9 jump to a field, ←/→ move between
 * chips, typing filters long choice lists and Enter picks the first match.
 */
export function LabelForm({
  fields,
  proposed,
  values,
  onChange,
}: LabelFormProps) {
  const errors = fieldErrors(fields, proposed, values);
  const set = (name: string, value: Json) =>
    onChange(setValue(fields, values, name, value));
  return (
    <div className="label-form" role="group" aria-label="Label fields">
      {fields.map((field, index) => {
        const value = values[field.name] ?? null;
        const original = proposed[field.name] ?? null;
        const changed = !same(value, original);
        const id = `field-${field.name}`;
        return (
          <div
            key={field.name}
            className={`label-row${changed ? " changed" : ""}${errors[field.name] ? " invalid" : ""}`}
            data-field-index={index + 1}
          >
            <div className="label-head">
              <span className="label-name" id={`${id}-label`}>
                {index < 9 && <kbd className="hotkey">{index + 1}</kbd>}
                {field.label ?? field.name}
              </span>
              {changed ? (
                <span className="label-change" aria-live="polite">
                  <del title="Proposed">{display(original)}</del>
                  <span aria-hidden="true"> → </span>
                  <ins title="Corrected">{display(value)}</ins>
                  <button
                    type="button"
                    className="reset"
                    aria-label={`Reset ${field.label ?? field.name}`}
                    onClick={() =>
                      onChange({ ...values, [field.name]: original })
                    }
                  >
                    ↺
                  </button>
                </span>
              ) : (
                <span className="label-same">as proposed</span>
              )}
            </div>
            {field.kind === "enum" ? (
              <EnumInput
                field={field}
                values={values}
                value={value}
                original={original}
                labelledBy={`${id}-label`}
                onPick={(v) => set(field.name, v)}
              />
            ) : field.kind === "number" ? (
              <NumberInput
                field={field}
                value={value}
                labelledBy={`${id}-label`}
                onValue={(v) => set(field.name, v)}
              />
            ) : (
              <TextInput
                field={field}
                value={value}
                labelledBy={`${id}-label`}
                onValue={(v) => set(field.name, v)}
              />
            )}
            {errors[field.name] && (
              <p className="field-error" role="alert">
                {errors[field.name]}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function EnumInput({
  field,
  values,
  value,
  original,
  labelledBy,
  onPick,
}: {
  field: FieldDefinition;
  values: Values;
  value: Json;
  original: Json;
  labelledBy: string;
  onPick: (value: Json) => void;
}) {
  const [query, setQuery] = useState("");
  const allowed = choicesFor(field, values);
  const searchable = allowed.length > SEARCH_THRESHOLD || !!field.allow_custom;
  const shown = filterChoices(allowed, query);
  // Keep the current value (and, for flat lists, the proposed one) visible even
  // when it is outside the schema; a re-parented child only shows its new branch.
  const extras = (field.depends_on ? [value] : [value, original]).filter(
    (v, i, all): v is string =>
      typeof v === "string" && !allowed.includes(v) && all.indexOf(v) === i,
  );
  const chips: (string | null)[] = [
    ...extras.filter(
      (v) => !query || v.toLowerCase().includes(query.toLowerCase()),
    ),
    ...shown,
    ...(field.nullable !== false && !query ? [null] : []),
  ];
  const custom =
    field.allow_custom && query.trim() && !allowed.includes(query.trim())
      ? query.trim()
      : null;
  function pick(next: Json) {
    onPick(next);
    setQuery("");
  }
  return (
    <div className="enum-input">
      {searchable && (
        <input
          type="search"
          className="chip-filter"
          aria-label={`Filter ${field.label ?? field.name}`}
          placeholder={
            field.allow_custom ? "Filter or type a new value…" : "Filter…"
          }
          value={query}
          enterKeyHint="done"
          autoComplete="off"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) {
              e.preventDefault();
              e.stopPropagation();
              if (shown.length) pick(shown[0]);
              else if (custom) pick(custom);
            } else if (e.key === "Escape") {
              e.stopPropagation();
              if (query) setQuery("");
              else e.currentTarget.blur();
            }
          }}
        />
      )}
      <div className="chips" role="radiogroup" aria-labelledby={labelledBy}>
        {chips.map((choice, i) => {
          const selected = same(choice, value);
          const outside = choice !== null && !allowed.includes(choice);
          return (
            <button
              type="button"
              role="radio"
              aria-checked={selected}
              tabIndex={
                selected || (i === 0 && !chips.some((c) => same(c, value)))
                  ? 0
                  : -1
              }
              key={choice ?? "\u0000null"}
              className={`chip${selected ? " selected" : ""}${same(choice, original) ? " proposed" : ""}${outside ? " outside" : ""}`}
              title={outside ? "Not in the field schema" : undefined}
              onClick={() => pick(choice)}
              onKeyDown={(e) => {
                const delta = {
                  ArrowRight: 1,
                  ArrowDown: 1,
                  ArrowLeft: -1,
                  ArrowUp: -1,
                }[e.key];
                if (!delta) return;
                e.preventDefault();
                e.stopPropagation();
                const next = chips[(i + delta + chips.length) % chips.length];
                pick(next);
                const group = e.currentTarget.parentElement;
                requestAnimationFrame(() =>
                  (
                    group?.querySelector(
                      '[aria-checked="true"]',
                    ) as HTMLElement | null
                  )?.focus(),
                );
              }}
            >
              {choice ?? "— none"}
            </button>
          );
        })}
        {custom && (
          <button
            type="button"
            className="chip custom"
            onClick={() => pick(custom)}
          >
            Use “{custom}”
          </button>
        )}
        {!chips.length && !custom && (
          <span className="muted">No matching choices</span>
        )}
      </div>
    </div>
  );
}

function NumberInput({
  field,
  value,
  labelledBy,
  onValue,
}: {
  field: FieldDefinition;
  value: Json;
  labelledBy: string;
  onValue: (value: Json) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const current = typeof value === "number" ? value : null;
  const step = field.step ?? 1;
  const round = (n: number) =>
    field.integer ? Math.round(n) : Math.round(n * 1e6) / 1e6;
  return (
    <div className="number-input">
      <button
        type="button"
        aria-label={`Decrease ${field.label ?? field.name}`}
        disabled={current === null}
        onClick={() => current !== null && onValue(round(current - step))}
      >
        −
      </button>
      <input
        type="text"
        inputMode={field.integer ? "numeric" : "decimal"}
        aria-labelledby={labelledBy}
        value={text ?? (current === null ? "" : String(current))}
        placeholder="—"
        onChange={(e) => {
          setText(e.target.value);
          const raw = e.target.value.trim();
          if (raw === "") onValue(null);
          else if (Number.isFinite(Number(raw))) onValue(Number(raw));
          else onValue(raw); // keeps the typo visible and flags "Must be a number"
        }}
        onBlur={() => setText(null)}
        onKeyDown={(e) => {
          if (e.key === "Escape") e.currentTarget.blur();
        }}
      />
      <button
        type="button"
        aria-label={`Increase ${field.label ?? field.name}`}
        disabled={current === null}
        onClick={() => current !== null && onValue(round(current + step))}
      >
        +
      </button>
      {(field.multipliers ?? []).map((m) => (
        <button
          type="button"
          key={m}
          className="chip"
          disabled={current === null}
          onClick={() => current !== null && onValue(round(current * m))}
        >
          ×{m}
        </button>
      ))}
    </div>
  );
}

function TextInput({
  field,
  value,
  labelledBy,
  onValue,
}: {
  field: FieldDefinition;
  value: Json;
  labelledBy: string;
  onValue: (value: Json) => void;
}) {
  const list = field.suggestions?.length ? `suggest-${field.name}` : undefined;
  return (
    <div className="text-input">
      <input
        type="text"
        aria-labelledby={labelledBy}
        list={list}
        value={typeof value === "string" ? value : ""}
        placeholder="—"
        autoComplete="off"
        onChange={(e) =>
          onValue(
            e.target.value === "" && field.nullable !== false
              ? null
              : e.target.value,
          )
        }
        onKeyDown={(e) => {
          if (e.key === "Escape") e.currentTarget.blur();
        }}
      />
      {list && (
        <datalist id={list}>
          {field.suggestions!.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
      )}
    </div>
  );
}

/** Focus the first control of field N (1-based), as used by the 1–9 shortcuts. */
export function focusField(index: number): boolean {
  const row = document.querySelector(`[data-field-index="${index}"]`);
  const target = row?.querySelector<HTMLElement>(
    'input, [role="radio"][tabindex="0"], [role="radio"]',
  );
  if (!target) return false;
  row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  target.focus();
  return true;
}

export function summary(proposed: Values, values: Values): string[] {
  return changedFields(proposed, values);
}
