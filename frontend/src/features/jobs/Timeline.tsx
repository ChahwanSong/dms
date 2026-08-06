import type { Transition } from "../../lib/types";
import { reasonText } from "../../lib/api";
export function Timeline({ transitions }: { transitions: Transition[] }) {
  if (!transitions.length) return <p className="text-muted text-sm">전이 이력이 없습니다</p>;
  return (
    <ol className="space-y-1 text-sm">
      {transitions.map((t, i) => (
        <li key={i} className="flex flex-wrap gap-2">
          <span className="text-muted tabular-nums">{t.at}</span>
          <span>{t.from_state ?? "—"} → {t.to_state}</span>
          {t.reason_code && <span className="text-bad">{reasonText(t.reason_code)}</span>}
          {t.actor && <span className="text-muted">({t.actor})</span>}
        </li>
      ))}
    </ol>
  );
}
