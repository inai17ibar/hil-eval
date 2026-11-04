export interface Part {
  kind: "same" | "add" | "remove";
  text: string;
}
/** Cap the quadratic table at 250k cells; large edits show whole replacements. */
export function wordDiff(before: string, after: string): Part[] {
  const a = before.match(/\s+|[^\s]+/g) ?? [];
  const b = after.match(/\s+|[^\s]+/g) ?? [];
  if ((a.length + 1) * (b.length + 1) > 250_000)
    return [
      { kind: "remove", text: before },
      { kind: "add", text: after },
    ];
  const table = Array.from(
    { length: a.length + 1 },
    () => new Uint32Array(b.length + 1),
  );
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      table[i][j] =
        a[i] === b[j]
          ? 1 + table[i + 1][j + 1]
          : Math.max(table[i + 1][j], table[i][j + 1]);
  const parts: Part[] = [];
  let i = 0,
    j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      parts.push({ kind: "same", text: a[i++] });
      j++;
    } else if (
      j < b.length &&
      (i === a.length || table[i][j + 1] > table[i + 1][j])
    )
      parts.push({ kind: "add", text: b[j++] });
    else parts.push({ kind: "remove", text: a[i++] });
  }
  return parts;
}
export function Diff({ before, after }: { before: string; after: string }) {
  return (
    <div aria-label="Output diff">
      <h3>Changes</h3>
      <pre>
        {wordDiff(before, after).map((p, i) =>
          p.kind === "add" ? (
            <ins key={i}>{p.text}</ins>
          ) : p.kind === "remove" ? (
            <del key={i}>{p.text}</del>
          ) : (
            <span key={i}>{p.text}</span>
          ),
        )}
      </pre>
    </div>
  );
}
