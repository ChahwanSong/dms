import { useEffect, useMemo, useState } from "react";
import { operatorApi, type AttentionItem } from "../../../api";
import Section from "./Section";

const SEVERITIES = ["ERROR", "WARN", "INFO"] as const;
// per-item badge color reuses the san-* palette
const SEV_CLASS: Record<string, string> = {
  ERROR: "san-failed", WARN: "san-degraded", INFO: "san-unknown",
};
const CAT_LABEL: Record<string, string> = {
  live: "현재 조치 필요",
  history: "과거 작업 이력 (종료된 작업·결과)",
};

// compact one-line detail: every field except the ones already shown as chrome
function detailText(r: AttentionItem): string {
  const omit = new Set(["issue_type", "severity", "category"]);
  return JSON.stringify(
    Object.fromEntries(Object.entries(r).filter(([k]) => !omit.has(k))),
  ).slice(0, 160);
}

function Group({ title, items }: { title: string; items: AttentionItem[] }) {
  if (items.length === 0) return null;
  return (
    <>
      <h4 className="dash-sub">{title} <span className="muted small">({items.length})</span></h4>
      <ul className="dash-attention">
        {items.map((r, i) => (
          <li key={i}>
            <span className={`san ${SEV_CLASS[r.severity || "WARN"]}`}>{r.issue_type}</span>
            <span className="mono small"> {detailText(r)}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

export default function AttentionPanel() {
  const [rows, setRows] = useState<AttentionItem[]>([]);
  // INFO (e.g. soft-deleted, awaiting manual cleanup) is hidden by default
  const [active, setActive] = useState<Set<string>>(new Set(["ERROR", "WARN"]));
  useEffect(() => {
    operatorApi.dashboard.attention().then(setRows).catch(() => setRows([]));
  }, []);

  const counts = useMemo(() => {
    const c: Record<string, number> = { ERROR: 0, WARN: 0, INFO: 0 };
    rows.forEach((r) => { const s = r.severity || "WARN"; if (s in c) c[s] += 1; });
    return c;
  }, [rows]);

  const filtered = rows.filter((r) => active.has(r.severity || "WARN"));
  const live = filtered.filter((r) => r.category !== "history");
  const history = filtered.filter((r) => r.category === "history");

  const toggle = (s: string) =>
    setActive((prev) => {
      const n = new Set(prev);
      if (n.has(s)) n.delete(s); else n.add(s);
      return n;
    });

  const badge = <span className={filtered.length ? "err-num" : "muted small"}>
    ({filtered.length}/{rows.length})
  </span>;

  return (
    <Section title="조치 필요" badge={badge}>
      <div className="attn-filters">
        {SEVERITIES.map((s) => (
          <button
            key={s}
            className={`attn-chip sev-${s} ${active.has(s) ? "on" : ""}`}
            onClick={() => toggle(s)}
            title={active.has(s) ? `${s} 숨기기` : `${s} 보기`}
          >
            {s} <b>{counts[s]}</b>
          </button>
        ))}
      </div>
      {rows.length === 0 ? <p className="muted">없음</p> :
        filtered.length === 0 ? <p className="muted">선택한 심각도에 해당하는 항목이 없습니다.</p> : (
          <>
            <Group title={CAT_LABEL.live} items={live} />
            <Group title={CAT_LABEL.history} items={history} />
          </>
        )}
    </Section>
  );
}
