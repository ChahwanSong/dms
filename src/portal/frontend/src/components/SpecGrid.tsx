import type { ReactNode } from "react";

export interface KV {
  label: string;
  value: ReactNode;
  mono?: boolean;
  tone?: string;
  span?: boolean; // span the full grid width (e.g. long paths / notes)
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
          <dd className={[it.mono ? "mono" : "", it.tone || ""].join(" ").trim()}>{it.value}</dd>
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
