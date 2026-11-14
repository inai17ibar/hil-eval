import type { FieldDefinition, Json } from "./api";

export type Values = Record<string, Json>;

/** Same semantics as the server: numbers compare by value, others by type+value. */
export function same(a: Json | undefined, b: Json | undefined): boolean {
  if (typeof a === "number" && typeof b === "number") return a === b;
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

export function changedFields(proposed: Values, values: Values): string[] {
  return Object.keys(values).filter((k) => !same(values[k], proposed[k]));
}

export function choicesFor(field: FieldDefinition, values: Values): string[] {
  const choices = field.choices ?? [];
  if (Array.isArray(choices)) return choices;
  const parent = field.depends_on ? values[field.depends_on] : null;
  if (parent === null || parent === undefined)
    return [...new Set(Object.values(choices).flat())].sort();
  return choices[String(parent)] ?? [];
}

/** Case-insensitive match; prefix matches sort first. */
export function filterChoices(choices: string[], query: string): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return choices;
  const hits = choices.filter((c) => c.toLowerCase().includes(q));
  return [
    ...hits.filter((c) => c.toLowerCase().startsWith(q)),
    ...hits.filter((c) => !c.toLowerCase().startsWith(q)),
  ];
}

export function validateField(
  field: FieldDefinition,
  value: Json,
  values: Values,
): string | null {
  if (value === null || value === undefined)
    return field.nullable === false ? "A value is required" : null;
  if (field.kind === "enum") {
    if (typeof value !== "string") return "Must be text";
    if (!field.allow_custom && !choicesFor(field, values).includes(value))
      return field.depends_on
        ? `Not a valid choice for this ${field.depends_on}`
        : "Not an allowed choice";
  } else if (field.kind === "number") {
    if (typeof value !== "number" || !Number.isFinite(value))
      return "Must be a number";
    if (field.integer && !Number.isInteger(value))
      return "Must be a whole number";
    if (field.min != null && value < field.min) return `Must be ≥ ${field.min}`;
    if (field.max != null && value > field.max) return `Must be ≤ ${field.max}`;
  } else if (typeof value !== "string") return "Must be text";
  return null;
}

/** Errors for changed fields (and children of a changed parent), like the server. */
export function fieldErrors(
  fields: FieldDefinition[],
  proposed: Values,
  values: Values,
): Record<string, string> {
  const changed = new Set(changedFields(proposed, values));
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const parentChanged = !!field.depends_on && changed.has(field.depends_on);
    const value = values[field.name] ?? null;
    if (changed.has(field.name) || (parentChanged && value !== null)) {
      const error = validateField(field, value, values);
      if (error) errors[field.name] = error;
    }
  }
  return errors;
}

/**
 * Set one value. Children whose current value is not in the changed parent's
 * branch are cleared (or snapped to its only choice), even with allow_custom,
 * so the reviewer sees what to fix.
 */
export function setValue(
  fields: FieldDefinition[],
  values: Values,
  name: string,
  value: Json,
): Values {
  const next: Values = { ...values, [name]: value };
  const changed = [name];
  for (const field of fields) {
    if (!field.depends_on || !changed.includes(field.depends_on)) continue;
    const current = next[field.name];
    const allowed = choicesFor(field, next);
    if (
      current !== null &&
      current !== undefined &&
      !allowed.includes(String(current))
    ) {
      next[field.name] = allowed.length === 1 ? allowed[0] : null;
      changed.push(field.name);
    }
  }
  return next;
}

export function display(value: Json | undefined): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}

/**
 * Reset one field to its proposed value. Dependents (transitively) that no longer
 * fit the restored parent go back to their proposed values too.
 */
export function resetField(
  fields: FieldDefinition[],
  values: Values,
  proposed: Values,
  name: string,
): Values {
  const next: Values = { ...values, [name]: proposed[name] ?? null };
  const touched = [name];
  for (const field of fields) {
    if (!field.depends_on || !touched.includes(field.depends_on)) continue;
    const current = next[field.name];
    if (
      current !== null &&
      current !== undefined &&
      !choicesFor(field, next).includes(String(current))
    ) {
      next[field.name] = proposed[field.name] ?? null;
      touched.push(field.name);
    }
  }
  return next;
}
