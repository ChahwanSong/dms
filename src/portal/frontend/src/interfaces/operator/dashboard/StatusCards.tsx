import { type ReactNode } from "react";
import { type DashboardSummary } from "../../../api";

// Panel-consistent action-required counts (computed in Dashboard from the SAME
// /attention list the 조치 필요 sub-view uses — so the card matches the panel:
// dismissed excluded, preflight/cancel INFO hidden). live = 현재 조치 필요.
export interface AttentionCounts {
  live: number;
  liveErr: number;
  history: number;
  info: number;
}

function Card({ title, children, onClick, hint }: {
  title: string; children: ReactNode; onClick?: () => void; hint?: string;
}) {
  return (
    <div
      className={"dash-card" + (onClick ? " dash-card-link" : "")}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      title={onClick ? hint : undefined}
      onKeyDown={onClick ? (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); }
      } : undefined}
    >
      <div className="dash-card-title">
        {title}
        {onClick && <span className="dash-card-arrow" aria-hidden="true">→</span>}
      </div>
      <div className="dash-card-body">{children}</div>
    </div>
  );
}

// A control-state chip that reads as health at a glance: green when normal, amber/red
// (with a glyph) when the attention condition is true. `attention=null` → unknown.
function CtrlChip({
  label,
  attention,
  okText,
  badText,
  tone,
}: {
  label: string;
  attention: boolean | null;
  okText: string;
  badText: string;
  tone: "warn" | "bad";
}) {
  if (attention == null) return <span className="ctrl-chip ctrl-unknown">{label} —</span>;
  const cls = attention ? `ctrl-${tone}` : "ctrl-ok";
  const glyph = attention ? (tone === "bad" ? "✕" : "▲") : "●";
  return (
    <span className={`ctrl-chip ${cls}`} title={`${label}: ${attention ? badText : okText}`}>
      <span className="ctrl-glyph" aria-hidden="true">{glyph}</span>
      {label} {attention ? badText : okText}
    </span>
  );
}

// volcano-system component (pod) role → label for the compact card.
const VOL_ROLE: Record<string, string> = {
  scheduler: "스케줄러", controllers: "컨트롤러", admission: "admission",
};

export default function StatusCards({ summary, attention, onNavigate }: {
  summary: DashboardSummary | null;
  attention?: AttentionCounts | null;
  onNavigate?: (section: string) => void;
}) {
  const cs = summary?.control_state.data;
  const ws = summary?.work_summary.data;
  const dj = summary?.data_jobs.data;
  const nd = summary?.nodes.data;
  const ch = summary?.control_hosts.data;
  const vol = summary?.volcano.data;
  const schedOk = cs && !cs.maintenance_mode && !cs.drain_mode && !cs.scheduling_blocked;
  const volOk = vol && !vol.has_errors && vol.total > 0 && vol.ready === vol.total;
  return (
    <div className="dash-cards">
      <Card title="노드">
        <ul className="dash-kv">
          <li>Fresh <b className="ok-num">{nd?.fresh ?? "—"}</b></li>
          <li>Stale <b className="err-num">{nd?.stale ?? "—"}</b></li>
          {nd && Object.entries(nd.by_role).map(([role, c]) => (
            <li key={role}>{role} <b>{c.fresh}/{c.fresh + c.stale}</b></li>
          ))}
          {ch && ch.total > 0 && (
            <>
              <li className="dash-kv-sep">
                CSI 호스트 도달{" "}
                <b className={ch.reachable === ch.total ? "ok-num" : "err-num"}>
                  {ch.reachable}/{ch.total}
                </b>
              </li>
              <li>
                변경권한{" "}
                <b className={ch.can_mutate === ch.total ? "ok-num" : "err-num"}>
                  {ch.can_mutate}/{ch.total}
                </b>
              </li>
            </>
          )}
        </ul>
      </Card>
      <Card title="스케줄러">
        <div className={`san ${schedOk ? "san-ready" : "san-degraded"}`}>
          {cs ? (schedOk ? "정상" : "차단/점검") : "—"}
        </div>
        <div className="ctrl-chips">
          <CtrlChip label="점검" attention={cs ? cs.maintenance_mode : null} okText="꺼짐" badText="켜짐" tone="warn" />
          <CtrlChip label="드레인" attention={cs ? cs.drain_mode : null} okText="꺼짐" badText="켜짐" tone="warn" />
          <CtrlChip label="스케줄링" attention={cs ? cs.scheduling_blocked : null} okText="허용" badText="차단" tone="bad" />
        </div>
        <ul className="dash-kv">
          {/* Volcano (gang 스케줄러) */}
          <li className="dash-kv-sep">
            Volcano{" "}
            <b className={volOk ? "ok-num" : !vol || vol.total === 0 ? "" : "err-num"}>
              {!vol ? "—" : vol.total === 0 ? "없음" : volOk ? "정상" : "점검"}
            </b>
          </li>
          <li>큐 (open) <b>{vol ? `${vol.queues_open}/${vol.queues}` : "—"}</b></li>
          <li>활성 잡 <b>{vol ? `${vol.jobs_active}/${vol.jobs_total}` : "—"}</b></li>
          {vol && Object.entries(vol.components).map(([role, c]) => (
            <li key={role}>
              {VOL_ROLE[role] || role}{" "}
              <b className={c.ready === c.total ? "ok-num" : "err-num"}>{c.ready}/{c.total}</b>
            </li>
          ))}
        </ul>
      </Card>
      <Card
        title="작업 · 조치 필요"
        onClick={onNavigate ? () => onNavigate("dashboard-attention") : undefined}
        hint="‘조치 필요’ 상세 보기"
      >
        <ul className="dash-kv">
          <li>활성 plan <b>{ws?.plans.total_active ?? "—"}</b></li>
          <li>활성 run <b>{ws?.runs.total_active ?? "—"}</b></li>
          <li>lease 임박 <b className={(ws?.runs.lease_expiring_soon ?? 0) > 0 ? "err-num" : ""}>{ws?.runs.lease_expiring_soon ?? "—"}</b></li>
          <li>stale/recovery <b className={(ws?.runs.stale_or_recovery ?? 0) > 0 ? "err-num" : ""}>{ws?.runs.stale_or_recovery ?? "—"}</b></li>
          {/* 조치 필요 — matches the 조치 필요 패널(현재/긴급/과거), not the raw DMS count */}
          <li className="dash-kv-sep">
            현재 조치 필요{" "}
            <b className={attention ? (attention.live > 0 ? "err-num" : "ok-num") : ""}>
              {attention ? `${attention.live}건` : (ws?.requests.action_required ?? "—")}
            </b>
            {attention && attention.liveErr > 0 && (
              <span className="err-num"> · 긴급 {attention.liveErr}</span>
            )}
          </li>
          {attention && attention.history > 0 && (
            <li>과거 이력 <b>{attention.history}</b></li>
          )}
          {attention && attention.info > 0 && (
            <li>알림(INFO) <b className="muted">{attention.info}</b></li>
          )}
        </ul>
      </Card>
      <Card title="요청">
        <ul className="dash-kv">
          <li>실행 <b>{dj?.by_state?.Running ?? 0}</b></li>
          <li>대기 <b>{dj?.by_state?.Pending ?? 0}</b></li>
          <li>확인대기 <b>{dj?.by_state?.ConfirmPending ?? 0}</b></li>
          <li>실패 <b className="err-num">{dj?.by_state?.Failed ?? 0}</b></li>
          <li>진행중 합 <b>{dj?.active_total ?? "—"}</b></li>
        </ul>
      </Card>
    </div>
  );
}
