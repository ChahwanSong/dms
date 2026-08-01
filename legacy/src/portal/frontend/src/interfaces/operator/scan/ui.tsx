import type { ReactNode } from "react";
import type { ScanBatch } from "../../../api";

// Definition grid is shared across the operator console.
export { SpecGrid, type KV } from "../../../components/SpecGrid";

// Scheduling priority -> chip tone (Low is the quiet default).
export function priorityTone(p?: string): string {
  if (p === "High") return "tone-high";
  if (p === "Mid") return "tone-mid";
  return "tone-low";
}

// Concise option chips for a scan batch (collapsed list row): priority,
// non-default parallelism, and any of the four scan options that are set. Scan is
// read-only, so there is nothing destructive to flag.
export function OptionChips({ batch }: { batch: ScanBatch }) {
  const opts = (batch.options || {}) as Record<string, unknown>;
  const chips: ReactNode[] = [
    <span key="prio" className={`chip ${priorityTone(batch.priority)}`}>
      {batch.priority ?? "Low"}
    </span>,
  ];
  if (batch.node_count != null)
    chips.push(
      <span key="nc" className="chip">
        노드 {batch.node_count}
      </span>,
    );
  if (opts.summary_only === true)
    chips.push(
      <span key="so" className="chip">
        summary_only
      </span>,
    );
  if (typeof opts.max_depth === "number")
    chips.push(
      <span key="md" className="chip">
        depth <b>{opts.max_depth}</b>
      </span>,
    );
  if (opts.follow_symlinks === true)
    chips.push(
      <span key="fs" className="chip">
        follow_symlinks
      </span>,
    );
  if (opts.one_file_system === true)
    chips.push(
      <span key="ofs" className="chip">
        one_file_system
      </span>,
    );
  return <span className="opt-chips">{chips}</span>;
}

// Human-friendly entries of the scan options object (for expanded views).
export function optionEntries(
  options?: Record<string, unknown> | null,
): { k: string; v: string }[] {
  const o = options || {};
  return Object.entries(o).map(([k, v]) => ({ k, v: fmtOptVal(v) }));
}

function fmtOptVal(v: unknown): string {
  if (typeof v === "boolean") return v ? "켜짐" : "꺼짐";
  if (typeof v === "number") return v.toLocaleString();
  return String(v);
}
