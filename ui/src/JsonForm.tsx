import type { Json } from "./api";

/** Infer controls recursively from the value, preserving JSON primitive types. */
export function JsonForm({
  value,
  onChange,
  label = "Corrected output",
}: {
  value: Json;
  onChange: (value: Json) => void;
  label?: string;
}) {
  if (Array.isArray(value))
    return (
      <fieldset>
        <legend>{label}</legend>
        {value.map((entry, index) => (
          <div key={index}>
            <JsonForm
              label={`${label} ${index + 1}`}
              value={entry}
              onChange={(next) =>
                onChange(value.map((v, i) => (i === index ? next : v)))
              }
            />
            <button
              type="button"
              onClick={() => onChange(value.filter((_, i) => i !== index))}
            >
              Remove {label} {index + 1}
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => onChange([...value, emptyValue(value[0] ?? "")])}
        >
          Add {label} row
        </button>
      </fieldset>
    );
  if (value !== null && typeof value === "object")
    return (
      <fieldset>
        <legend>{label}</legend>
        {Object.entries(value).map(([key, child]) => (
          <JsonForm
            key={key}
            label={key}
            value={child}
            onChange={(next) => onChange({ ...value, [key]: next })}
          />
        ))}
      </fieldset>
    );
  if (typeof value === "boolean")
    return (
      <label>
        {label}
        <input
          type="checkbox"
          checked={value}
          onChange={(e) => onChange(e.target.checked)}
        />
      </label>
    );
  if (value === null)
    return (
      <label>
        {label}
        <select value="null" onChange={() => {}}>
          <option value="null">null</option>
        </select>
      </label>
    );
  return (
    <label>
      {label}
      <input
        type={typeof value === "number" ? "number" : "text"}
        step="any"
        value={value}
        onChange={(e) => {
          if (typeof value === "number") {
            if (
              e.target.value !== "" &&
              Number.isFinite(e.target.valueAsNumber)
            )
              onChange(e.target.valueAsNumber);
          } else onChange(e.target.value);
        }}
      />
    </label>
  );
}
export function emptyValue(value: Json): Json {
  if (Array.isArray(value)) return [];
  if (value !== null && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, emptyValue(v)]),
    );
  return typeof value === "number"
    ? 0
    : typeof value === "boolean"
      ? false
      : value === null
        ? null
        : "";
}
