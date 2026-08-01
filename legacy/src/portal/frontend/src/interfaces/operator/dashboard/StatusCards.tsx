import { type DashboardSummary } from "../../../api";

// Panel-consistent action-required counts (computed in Dashboard from the SAME
// /attention list the 조치 필요 sub-view uses — so this matches the panel:
// dismissed excluded, preflight/cancel INFO hidden). live = 현재 조치 필요.
export interface AttentionCounts {
  live: number;
  liveErr: number;
  history: number;
  info: number;
}

// A control-state chip that reads as health at a glance: green when normal, amber/red
// (with a glyph) when the attention condition is true. `attention=null` → unknown.
function CtrlChip({ label, attention, okText, badText, tone }: {
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
      {label}{attention ? ` ${badText}` : ""}
    </span>
  );
}

// Detail card: the breakdowns the KPI row + section tables don't surface — control
// state, work-summary counts, request states, and 조치 필요 tallies.
export default function StatusCards({ summary, attention, onNavigate }: {
  summary: DashboardSummary | null;
  attention?: AttentionCounts | null;
  onNavigate?: (section: string) => void;
}) {
  const cs = summary?.control_state.data;
  const ws = summary?.work_summary.data;
  const dj = summary?.data_jobs.data;
  const bs = dj?.by_state || {};
  return (
    <section className="ui-card">
      <div className="ui-card-hd">
        <h3>제어 · 작업 · 요청</h3>
        <div className="ctrl-chips" style={{ margin: 0 }}>
          <CtrlChip label="점검" attention={cs ? cs.maintenance_mode : null} okText="꺼짐" badText="켜짐" tone="warn" />
          <CtrlChip label="드레인" attention={cs ? cs.drain_mode : null} okText="꺼짐" badText="켜짐" tone="warn" />
          <CtrlChip label="스케줄링" attention={cs ? cs.scheduling_blocked : null} okText="허용" badText="차단" tone="bad" />
        </div>
      </div>
      <div className="ui-card-bd">
        <div className="ui-card-div" />
        <div className="dash-detail">
          <div>
            <div className="card-eyebrow">작업</div>
            <ul className="dash-kv">
              <li>활성 plan <b>{ws?.plans.total_active ?? "—"}</b></li>
              <li>활성 run <b>{ws?.runs.total_active ?? "—"}</b></li>
              <li>lease 임박 <b className={(ws?.runs.lease_expiring_soon ?? 0) > 0 ? "err-num" : ""}>{ws?.runs.lease_expiring_soon ?? "—"}</b></li>
              <li>stale/recovery <b className={(ws?.runs.stale_or_recovery ?? 0) > 0 ? "err-num" : ""}>{ws?.runs.stale_or_recovery ?? "—"}</b></li>
            </ul>
          </div>
          <div>
            <div className="card-eyebrow">데이터 요청 (상태별)</div>
            <ul className="dash-kv">
              <li>실행 <b>{bs.Running ?? 0}</b></li>
              <li>대기 <b>{bs.Pending ?? 0}</b></li>
              <li>확인대기 <b>{bs.ConfirmPending ?? 0}</b></li>
              <li>실패 <b className={(bs.Failed ?? 0) > 0 ? "err-num" : ""}>{bs.Failed ?? 0}</b></li>
              <li>진행중 합 <b>{dj?.active_total ?? "—"}</b></li>
            </ul>
          </div>
          <div>
            <div className="card-eyebrow">조치 필요</div>
            <ul className="dash-kv">
              <li>현재{" "}
                <b className={attention ? (attention.live > 0 ? "err-num" : "ok-num") : ""}>
                  {attention ? `${attention.live}건` : (ws?.requests.action_required ?? "—")}
                </b>
              </li>
              {attention && attention.liveErr > 0 && <li>긴급 <b className="err-num">{attention.liveErr}</b></li>}
              {attention && attention.history > 0 && <li>과거 이력 <b>{attention.history}</b></li>}
              {attention && attention.info > 0 && <li>알림(INFO) <b className="muted">{attention.info}</b></li>}
            </ul>
            {onNavigate && (
              <button type="button" className="hd-link" style={{ marginTop: "0.5rem", background: "none", border: 0, cursor: "pointer", padding: 0 }}
                onClick={() => onNavigate("dashboard-attention")}>조치 필요 상세 →</button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
