import type { ReactNode } from "react";
import type { BackupBatch } from "../../../api";

// Scheduling priority -> chip tone (Low is the quiet default).
export function priorityTone(p?: string): string {
  if (p === "High") return "tone-high";
  if (p === "Mid") return "tone-mid";
  return "tone-low";
}

// Concise, important option chips for a batch (collapsed list row). Only the
// things an operator must see at a glance: priority, destructive --delete,
// non-default parallelism, and ownership overrides (chmod/chown).
export function OptionChips({ batch }: { batch: BackupBatch }) {
  const opts = (batch.options || {}) as Record<string, unknown>;
  const chips: ReactNode[] = [
    <span key="prio" className={`chip ${priorityTone(batch.priority)}`}>
      {batch.priority ?? "Low"}
    </span>,
  ];
  if (batch.delete_enabled)
    chips.push(
      <span key="del" className="chip tone-danger">
        --delete
      </span>,
    );
  if (batch.node_count != null)
    chips.push(
      <span key="nc" className="chip">
        노드 {batch.node_count}
      </span>,
    );
  if (typeof opts.chown === "string" && opts.chown.trim())
    chips.push(
      <span key="chown" className="chip tone-warn">
        chown
      </span>,
    );
  if (typeof opts.chmod === "string" && opts.chmod.trim())
    chips.push(
      <span key="chmod" className="chip">
        chmod
      </span>,
    );
  return <span className="opt-chips">{chips}</span>;
}

export interface KV {
  label: string;
  value: ReactNode;
  mono?: boolean;
  tone?: string;
  span?: boolean; // span the full grid width (e.g. long paths / notes)
}

// Aligned definition grid: eyebrow label above a value. Replaces run-on
// "·"-separated metadata sentences with something scannable.
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

// Human-friendly entries of the sync options object (for expanded views).
export function optionEntries(
  options?: Record<string, unknown> | null,
): { k: string; v: string }[] {
  const o = options || {};
  return Object.entries(o)
    .filter(([k]) => k !== "delete") // shown via the --delete chip
    .map(([k, v]) => ({ k, v: fmtOptVal(k, v) }));
}

function fmtOptVal(k: string, v: unknown): string {
  if (typeof v === "boolean") return v ? "켜짐" : "꺼짐";
  if (k === "bufsize" && typeof v === "number") {
    return v >= 1024 * 1024 ? `${(v / (1024 * 1024)).toFixed(0)} MB` : `${v.toLocaleString()} B`;
  }
  if (typeof v === "number") return v.toLocaleString();
  return String(v);
}
