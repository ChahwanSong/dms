import { type CSSProperties, type ReactNode } from "react";
import { type DashboardSummary } from "../../../api";
import { type AttentionCounts } from "./StatusCards";

// Headline stat cards (summary before detail). Deep breakdowns stay in the detail
// card + section tables below; these are the at-a-glance numbers.
const IC = {
  node: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2" /><path d="M8 21h8m-4-4v4" /></svg>
  ),
  bolt: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M13 2 3 14h7l-1 8 10-12h-7l1-8Z" /></svg>
  ),
  alert: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /></svg>
  ),
  activity: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2" /></svg>
  ),
  check: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 11.1V12a10 10 0 1 1-5.9-9.1M22 4 12 14.01l-3-3" /></svg>
  ),
};

const TONE: Record<string, { k: string; kb: string }> = {
  accent: { k: "var(--accent)", kb: "var(--accent-soft)" },
  teal: { k: "var(--teal)", kb: "rgba(45,212,191,0.14)" },
  warn: { k: "var(--warn)", kb: "var(--warn-soft)" },
  ok: { k: "var(--ok)", kb: "var(--ok-soft)" },
};

function Kpi({ tone, icon, children, label, delta, small, onClick, hint }: {
  tone: keyof typeof TONE;
  icon: ReactNode;
  children: ReactNode;
  label: string;
  delta?: { text: string; tone: "up" | "dn" | "flat" };
  small?: boolean;
  onClick?: () => void;
  hint?: string;
}) {
  const tv = TONE[tone];
  const style = { ["--k"]: tv.k, ["--kb"]: tv.kb } as CSSProperties;
  return (
    <div
      className={"kpi" + (onClick ? " kpi-link" : "")}
      style={style}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      title={onClick ? hint : undefined}
      onKeyDown={onClick ? (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); }
      } : undefined}
    >
      <div className="kpi-r1">
        <span className="kpi-icn">{icon}</span>
        {delta && <span className={`kpi-delta ${delta.tone}`}>{delta.text}</span>}
      </div>
      <div className={"kpi-num" + (small ? " sm" : "")}>{children}</div>
      <div className="kpi-lbl">{label}</div>
    </div>
  );
}

export default function KpiRow({ summary, attention, onNavigate }: {
  summary: DashboardSummary | null;
  attention?: AttentionCounts | null;
  onNavigate?: (section: string) => void;
}) {
  const ws = summary?.work_summary.data;
  const dj = summary?.data_jobs.data;
  const nd = summary?.nodes.data;
  const cs = summary?.control_state.data;
  const schedOk = cs && !cs.maintenance_mode && !cs.drain_mode && !cs.scheduling_blocked;
  const total = nd ? nd.fresh + nd.stale : null;
  const live = attention ? attention.live : ws?.requests.action_required;
  return (
    <div className="kpi-row">
      <Kpi
        tone="accent"
        icon={IC.node}
        label="워커 노드 온라인"
        delta={nd && nd.stale > 0 ? { text: `stale ${nd.stale}`, tone: "dn" } : undefined}
      >
        {nd ? nd.fresh : "—"}
        {total != null && <small>/ {total}</small>}
      </Kpi>
      <Kpi tone="teal" icon={IC.bolt} label="진행중 작업 (run)">
        {ws?.runs.total_active ?? "—"}
      </Kpi>
      <Kpi
        tone="warn"
        icon={IC.alert}
        label="조치 필요"
        onClick={onNavigate ? () => onNavigate("dashboard-attention") : undefined}
        hint="‘조치 필요’ 상세 보기"
        delta={attention && attention.liveErr > 0 ? { text: `긴급 ${attention.liveErr}`, tone: "dn" } : undefined}
      >
        {live ?? "—"}
      </Kpi>
      <Kpi tone="accent" icon={IC.activity} label="진행중 데이터잡">
        {dj?.active_total ?? "—"}
      </Kpi>
      <Kpi tone={schedOk ? "ok" : "warn"} icon={IC.check} label="제어 상태 · 스케줄러" small>
        {cs ? (schedOk ? "정상" : "차단/점검") : "—"}
      </Kpi>
    </div>
  );
}
