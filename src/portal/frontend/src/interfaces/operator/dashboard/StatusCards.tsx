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

// volcano-system component (pod) role → label for the compact card.
const VOL_ROLE: Record<string, string> = {
  scheduler: "스케줄러", controllers: "컨트롤러", admission: "admission",
};

export default function StatusCards({ summary }: { summary: DashboardSummary | null }) {
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
        <ul className="dash-kv">
          {/* 컨트롤 상태 */}
          <li>maintenance <b>{cs ? String(cs.maintenance_mode) : "—"}</b></li>
          <li>drain <b>{cs ? String(cs.drain_mode) : "—"}</b></li>
          <li>scheduling <b>{cs ? (cs.scheduling_blocked ? "차단" : "허용") : "—"}</b></li>
          {/* 큐 / 작업 */}
          <li className="dash-kv-sep">활성 plan <b>{ws?.plans.total_active ?? "—"}</b></li>
          <li>활성 run <b>{ws?.runs.total_active ?? "—"}</b></li>
          <li>lease 임박 <b className="err-num">{ws?.runs.lease_expiring_soon ?? "—"}</b></li>
          <li>stale/recovery <b className="err-num">{ws?.runs.stale_or_recovery ?? "—"}</b></li>
          <li>조치 필요 <b className="err-num">{ws?.requests.action_required ?? "—"}</b></li>
          {/* Volcano */}
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
