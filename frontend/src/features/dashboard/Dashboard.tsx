import { useRequests } from "../jobs/useJobs";
import { useNodes } from "./useDashboard";
import { isTerminal } from "../../lib/jobState";
import { MetricTile } from "../../components/ui/MetricTile";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";

export function Dashboard() {
  const reqs = useRequests(); const nodes = useNodes();
  const rs = reqs.data ?? [];
  const running = rs.filter((r) => !isTerminal(r.state)).length;
  const pending = rs.filter((r) => r.state === "Pending").length;
  const ok = rs.filter((r) => r.state === "Succeeded").length;
  const failed = rs.filter((r) => r.state === "Failed").length;
  return (
    <section className="space-y-5">
      <h1 className="text-lg font-semibold">대시보드</h1>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricTile label="실행 중" value={running} />
        <MetricTile label="대기" value={pending} />
        <MetricTile label="성공" value={ok} />
        <MetricTile label="실패" value={failed} />
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <Card>
          <h2 className="font-medium mb-3">노드 상태</h2>
          <ul className="space-y-2 text-sm">
            {(nodes.data ?? []).map((n) => (
              <li key={n.node_name} className="flex items-center justify-between">
                <span>{n.node_name}</span>
                <StatusPill state={n.fresh ? "Succeeded" : "Failed"} />
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <h2 className="font-medium mb-3">최근 작업</h2>
          <ul className="space-y-2 text-sm">
            {rs.slice(0, 6).map((r) => (
              <li key={r.request_id} className="flex items-center justify-between">
                <span>{r.request_id} · {r.operation}</span><StatusPill state={r.state} />
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </section>
  );
}
