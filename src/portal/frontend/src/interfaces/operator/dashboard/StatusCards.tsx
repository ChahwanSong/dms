import { type ReactNode } from "react";
import { type DashboardSummary } from "../../../api";

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="dash-card">
      <div className="dash-card-title">{title}</div>
      <div className="dash-card-body">{children}</div>
    </div>
  );
}

export default function StatusCards({ summary }: { summary: DashboardSummary | null }) {
  const cs = summary?.control_state.data;
  const ws = summary?.work_summary.data;
  const dj = summary?.data_jobs.data;
  const nd = summary?.nodes.data;
  const schedOk = cs && !cs.maintenance_mode && !cs.drain_mode && !cs.scheduling_blocked;
  return (
    <div className="dash-cards">
      <Card title="스케줄러">
        <div className={`san ${schedOk ? "san-ready" : "san-degraded"}`}>
          {cs ? (schedOk ? "정상" : "차단/점검") : "—"}
        </div>
        <ul className="dash-kv">
          <li>maintenance <b>{cs ? String(cs.maintenance_mode) : "—"}</b></li>
          <li>drain <b>{cs ? String(cs.drain_mode) : "—"}</b></li>
          <li>scheduling <b>{cs ? (cs.scheduling_blocked ? "차단" : "허용") : "—"}</b></li>
        </ul>
      </Card>
      <Card title="큐 / 작업">
        <ul className="dash-kv">
          <li>활성 plan <b>{ws?.plans.total_active ?? "—"}</b></li>
          <li>활성 run <b>{ws?.runs.total_active ?? "—"}</b></li>
          <li>lease 임박 <b className="err-num">{ws?.runs.lease_expiring_soon ?? "—"}</b></li>
          <li>stale/recovery <b className="err-num">{ws?.runs.stale_or_recovery ?? "—"}</b></li>
          <li>조치 필요 <b className="err-num">{ws?.requests.action_required ?? "—"}</b></li>
        </ul>
      </Card>
      <Card title="노드">
        <ul className="dash-kv">
          <li>Fresh <b className="ok-num">{nd?.fresh ?? "—"}</b></li>
          <li>Stale <b className="err-num">{nd?.stale ?? "—"}</b></li>
          {nd && Object.entries(nd.by_role).map(([role, c]) => (
            <li key={role}>{role} <b>{c.fresh}/{c.fresh + c.stale}</b></li>
          ))}
        </ul>
      </Card>
      <Card title="데이터 잡">
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
