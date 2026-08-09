import { useState } from "react";
import { useNodeMetrics } from "./useMetrics";
import { useNodes } from "./useDashboard";
import { WindowSelect } from "./WindowSelect";
import { Card } from "../../components/ui/Card";
import { Sparkline } from "../../components/ui/Sparkline";
import type { Node, NodeMetricPoint, NodeMetricSeries } from "../../lib/types";

// 에이전트 리포트는 스키마 검증 없이 저장된다 -- NodesList.tsx와 같은 방어 관용구
const asArray = <T,>(v: unknown): T[] => (Array.isArray(v) ? v : []);

function ageText(reportedAt: string): string {
  const ms = Date.now() - Date.parse(reportedAt);
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const m = Math.floor(ms / 60000);
  if (m < 1) return "방금";
  if (m < 60) return `${m}분 전`;
  return `${Math.floor(m / 60)}시간 전`;
}

function pick(points: NodeMetricPoint[], f: (p: NodeMetricPoint) => number | null) {
  return points.map(f);
}

// 증거 스냅샷(설계 §4.2): Ready n/전체 요약. 원본 표는 노드 화면(/admin/nodes)에
// 이미 있으므로 여기서는 비율만 -- 상세가 필요하면 그 화면으로 간다.
function readyCount(items: unknown): string {
  const arr = asArray<{ status?: string }>(items);
  return `${arr.filter((i) => i.status === "Ready").length}/${arr.length}`;
}

function Metric({ title, values, label }: {
  title: string; values: (number | null)[]; label: string;
}) {
  return (
    <div>
      <div className="text-muted text-xs">{title}</div>
      <Sparkline values={values} label={label} />
    </div>
  );
}

export function NodeMetricsSection() {
  const [windowH, setWindowH] = useState(24);
  const [open, setOpen] = useState<string | null>(null);
  const metricsQ = useNodeMetrics(windowH);
  const nodesQ = useNodes();
  const series = asArray<NodeMetricSeries>(metricsQ.data?.nodes);
  const reports = new Map(
    asArray<Node>(nodesQ.data).map((n) => [n.node_name, n.report] as const));
  return (
    <Card>
      <div className="flex items-center justify-between mb-2">
        <h2 className="font-medium">노드/리소스</h2>
        <WindowSelect value={windowH} onChange={setWindowH} />
      </div>
      {metricsQ.isLoading && <p className="text-muted text-sm">불러오는 중…</p>}
      {series.map((n) => {
        const points = asArray<NodeMetricPoint>(n.points);
        const report = reports.get(n.node_name);
        // 스토리지 이름은 포인트마다 다를 수 있다(스토리지 추가/제거) -- 합집합으로 그린다
        const diskNames = [...new Set(points.flatMap(
          (p) => asArray<{ storage_name: string }>(p.disks).map((d) => d.storage_name)))];
        return (
          <div key={n.node_name} className="border-t border-black/5 py-3">
            <div className="flex items-center justify-between">
              <button className="font-medium" onClick={() =>
                setOpen(open === n.node_name ? null : n.node_name)}>
                {n.node_name}
              </button>
              <span className={`text-xs ${n.fresh ? "text-ok" : "text-bad"}`}>
                {n.fresh ? "정상" : "지연"} · {ageText(n.reported_at)}
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
              <Metric title="load1" label={`${n.node_name} load1`}
                      values={pick(points, (p) => p.load1)} />
              <Metric title="메모리 사용%" label={`${n.node_name} 메모리`}
                      values={pick(points, (p) => p.mem_used_pct)} />
              <Metric title="수신 B/s" label={`${n.node_name} 수신`}
                      values={pick(points, (p) => p.net_rx_bps)} />
              <Metric title="송신 B/s" label={`${n.node_name} 송신`}
                      values={pick(points, (p) => p.net_tx_bps)} />
            </div>
            {open === n.node_name && (
              <div className="mt-3 space-y-3">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <Metric title="load5" label={`${n.node_name} load5`}
                          values={pick(points, (p) => p.load5)} />
                  <Metric title="load15" label={`${n.node_name} load15`}
                          values={pick(points, (p) => p.load15)} />
                  {diskNames.map((name) => (
                    <div key={name}>
                      <div className="text-muted text-xs"><span>{name}</span> 사용%</div>
                      <Sparkline label={`${n.node_name} ${name} 디스크`}
                                 values={points.map((p) =>
                                   asArray<{ storage_name: string; used_pct: number | null }>(p.disks)
                                     .find((d) => d.storage_name === name)?.used_pct ?? null)} />
                    </div>
                  ))}
                </div>
                {report != null && (
                  <p className="text-muted text-xs">
                    마운트 {readyCount((report as { mounts?: unknown }).mounts)} ·
                    도구 {readyCount((report as { tools?: unknown }).tools)} ·
                    계정 {readyCount((report as { identities?: unknown }).identities)}
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}
    </Card>
  );
}
