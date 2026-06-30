import { isValidElement, type ReactNode } from "react";

export interface KV {
  label: string;
  value: ReactNode;
  mono?: boolean;
  tone?: string;
  span?: boolean; // span the full grid width (e.g. long paths / notes)
}

// Defense in depth: several callers feed `value` straight from open
// `[k: string]: unknown` API records, so a field the contract types as a string
// can arrive as an object/array at runtime. Rendering a plain object as a React
// child throws (React #31) and — with no error boundary below the crash site —
// unmounts the whole tree. Coerce anything that isn't a valid child to text so a
// stray object degrades to readable JSON instead of a white screen.
function renderable(v: ReactNode): ReactNode {
  if (v != null && typeof v === "object" && !isValidElement(v) && !Array.isArray(v)) {
    try {
      return JSON.stringify(v);
    } catch {
      return String(v);
    }
  }
  return v;
}

// Aligned definition grid: an eyebrow label above its value. Replaces run-on
// "·"-separated metadata sentences with something scannable. Shared across the
// operator console (data backup, storage inventory).
export function SpecGrid({ items }: { items: KV[] }) {
  return (
    <dl className="spec-grid">
      {items.map((it, i) => (
        <div className={`spec-kv${it.span ? " span" : ""}`} key={i}>
          <dt>{it.label}</dt>
          <dd className={[it.mono ? "mono" : "", it.tone || ""].join(" ").trim()}>
            {renderable(it.value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

// Tri-state boolean as a colored pill: true→예(green), false→아니오(red),
// null/undefined→?(gray). Optional labels override the defaults.
export function BoolChip({
  value,
  yes = "예",
  no = "아니오",
  unknown = "?",
}: {
  value?: boolean | null;
  yes?: string;
  no?: string;
  unknown?: string;
}) {
  const cls = value == null ? "tone-unknown" : value ? "tone-ok" : "tone-danger";
  const label = value == null ? unknown : value ? yes : no;
  return <span className={`chip ${cls}`}>{label}</span>;
}
