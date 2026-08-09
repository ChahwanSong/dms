import { useRequests } from "../jobs/useJobs";
import { useNodes } from "./useDashboard";
import { useInfraMetrics, useJobMetrics } from "./useMetrics";
import { MetricTile } from "../../components/ui/MetricTile";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import type { PillVariant } from "../../lib/jobState";
import type { StateCount } from "../../lib/types";

// KPI는 잡 상태의 집합 합산이다. 옛 스텁의 요청 목록 즉석 계산은 페이지네이션
// 상한(50건)에 걸려 총계가 거짓이 됐다 -- 백엔드 GROUP BY 집계로 바꾼다(설계 §4.1).
// 집합은 domain.DataJobState 기준: 비종단 중 Pending만 "대기", 나머지가 "실행 중".
const RUNNING_STATES = new Set(
  ["Preflight", "PreviewRunning", "ConfirmPending", "Executing", "Running"]);
const FAILED_STATES = new Set(["Failed", "TimedOut"]);

export function kpiFromStates(byState: StateCount[]) {
  const sum = (pred: (s: string) => boolean) =>
    byState.filter((r) => pred(r.state)).reduce((a, r) => a + r.count, 0);
  return {
    running: sum((s) => RUNNING_STATES.has(s)),
    pending: sum((s) => s === "Pending"),
    succeeded: sum((s) => s === "Succeeded"),
    failed: sum((s) => FAILED_STATES.has(s)),
  };
}

// 판정 배지: 릴리스 화면(releasePillVariant)과 같은 이유로 공용 pillVariant를
// 고치지 않는다 -- applied/progressing은 공용 매핑이 모르는 어휘다.
const VERDICT_VARIANT: Record<string, PillVariant> = {
  applied: "ok", progressing: "busy", failed: "bad",
};

export function Dashboard() {
  const reqs = useRequests();
  const nodes = useNodes();
  const jobsQ = useJobMetrics(24);
  const infraQ = useInfraMetrics();
  // 방어적 정규화 -- 배열 아닌 페이로드 하나가 화면을 죽이면 안 된다
  const byState = Array.isArray(jobsQ.data?.by_state) ? jobsQ.data.by_state : [];
  const kpi = kpiFromStates(byState);
  const components = Array.isArray(infraQ.data?.components)
    ? infraQ.data.components : [];
  const rs = Array.isArray(reqs.data) ? reqs.data : [];
  return (
    <section className="space-y-5">
      <h1 className="text-lg font-semibold">대시보드</h1>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricTile label="실행 중" value={kpi.running} />
        <MetricTile label="대기" value={kpi.pending} />
        <MetricTile label="성공(24h)" value={kpi.succeeded} />
        <MetricTile label="실패(24h)" value={kpi.failed} />
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <Card>
          <h2 className="font-medium mb-3">컴포넌트</h2>
          <ul className="space-y-2 text-sm">
            {components.map((c) => (
              <li key={c.component} className="flex items-center gap-2">
                <span className="shrink-0">{c.component}</span>
                <span className="text-muted text-xs truncate grow">
                  {c.image ?? "—"}
                </span>
                <span className="text-xs tabular-nums shrink-0">
                  {`${c.ready ?? "—"}/${c.desired ?? "—"}`}
                </span>
                <StatusPill state={c.verdict ?? "unknown"}
                            variant={c.verdict ? VERDICT_VARIANT[c.verdict] : "neutral"} />
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <h2 className="font-medium mb-3">최근 작업</h2>
          <ul className="space-y-2 text-sm">
            {rs.slice(0, 6).map((r) => (
              <li key={r.request_id} className="flex items-center justify-between">
                <span>{r.request_id} · {r.operation}</span>
                <StatusPill state={r.state} />
              </li>
            ))}
          </ul>
        </Card>
      </div>
      {/* 노드 상태 카드는 Task 7의 시계열 섹션(NodeMetricsSection)이 대체한다 --
          그때까지 신선도 목록을 유지해 화면 공백을 만들지 않는다 */}
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
    </section>
  );
}
