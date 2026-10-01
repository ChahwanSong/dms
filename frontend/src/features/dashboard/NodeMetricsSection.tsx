import { useState, type ReactNode } from "react";
import { useNodeMetrics } from "./useMetrics";
import { useNodes } from "./useDashboard";
import { WindowSelect } from "./WindowSelect";
import { Card } from "../../components/ui/Card";
import { Sparkline, type SparklineDomain } from "../../components/ui/Sparkline";
import type { Node, NodeMetricPoint, NodeMetricSeries } from "../../lib/types";

// 에이전트 리포트는 스키마 검증 없이 저장된다 -- NodesList.tsx와 같은 방어 관용구
const asArray = <T,>(v: unknown): T[] => (Array.isArray(v) ? v : []);

// NodesList.tsx humanBytes 의 국소 사본(공용 모듈은 이르다는 그쪽 관례) -- 단
// 그쪽은 디스크 총량이라 MiB 이상만 다루고, 여기는 네트워크 대역폭이라 KiB/B
// 대가 흔해 KiB 단위를 더했다.
const BYTE_UNITS: [string, number][] = [
  ["TiB", 1024 ** 4], ["GiB", 1024 ** 3], ["MiB", 1024 ** 2], ["KiB", 1024],
];
function humanBytes(bytes: number): string {
  for (const [unit, size] of BYTE_UNITS) {
    if (bytes >= size) return `${(bytes / size).toFixed(1)} ${unit}`;
  }
  return `${Math.round(bytes)} B`;
}

const fmtPct = (v: number) => `${Math.round(v)}%`;
const fmtLoad = (v: number) => String(Math.round(v * 100) / 100);
const fmtBps = (v: number) => `${humanBytes(v)}/s`;

// 메모리·디스크 사용%의 자연 상한 -- 차트가 창과 무관한 절대 스케일로 그려진다
const PCT_DOMAIN: SparklineDomain = { min: 0, max: 100 };

// 리포트는 스키마 검증 없이 저장된다(위 asArray 와 같은 방어) -- cpu_count 는
// 양의 유한수일 때만 신뢰한다. 구형 에이전트 리포트엔 필드 자체가 없다: null
// (모름)로 두면 load 차트가 창 최대 폴백으로 그려진다. 0 이나 문자열 오염값을
// 거짓 상한으로 뭉개지 않는다.
function cpuCountOf(report: unknown): number | null {
  const os = (report as { os?: { cpu_count?: unknown } } | null | undefined)?.os;
  const v = os?.cpu_count;
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
}

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

function Metric({ title, values, label, fmt, domain, capLabel }: {
  title: ReactNode; values: (number | null)[]; label: string;
  fmt: (v: number) => string; domain?: SparklineDomain; capLabel?: string;
}) {
  // 현재값 = 마지막 유효점(마지막 점이 null 결측이어도 직전 실측이 "현재"다),
  // 최대값 = 창 내 유효점의 최대. 유효점이 없으면 병기도 "—" -- 0 으로 뭉개지
  // 않는다. 상대값 스케일에서 절대 수치가 사라지는 것을 이 병기가 보상한다.
  const valid = values.filter((v): v is number => v !== null && Number.isFinite(v));
  const cur = valid.length > 0 ? valid[valid.length - 1] : null;
  return (
    <div>
      <div className="flex items-center justify-between text-muted text-xs">
        <span>{title}</span>
        {/* 상한 라벨은 SVG 밖 캡션 -- preserveAspectRatio="none" 아래 SVG 텍스트는
            가로로 왜곡된다(Sparkline 주석 참조) */}
        {capLabel !== undefined && <span>{capLabel}</span>}
      </div>
      <Sparkline values={values} label={label} domain={domain} />
      <div className="text-muted text-xs">
        {cur === null ? "—" : `${fmt(cur)} · 최대 ${fmt(Math.max(...valid))}`}
      </div>
    </div>
  );
}

// 노드당 한 줄: Load(1분평균)·메모리·수신·송신(2026-10-01 사용자 요청 -- 펼침(load5/load15·스토리지 사용%·
// 마운트/도구/계정 요약)을 없앴다. 스토리지 사용%는 공유 FS 라 노드마다 같은 값이었고, 마운트·도구·계정 상세는
// 노드 화면(/admin/nodes)에 있다).
export function NodeMetricsSection() {
  const [windowH, setWindowH] = useState(24);
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
        // 코어 수는 사실상 불변이라 시계열이 아닌 최신 리포트에서 읽는다
        // (metrics_series.py 의 같은 취지 주석). load 는 코어 수를 넘을 수 있으므로
        // 상한이 아니라 기준선이다 -- Sparkline 이 스케일을 max(코어수, 창 최대)로
        // 늘려 초과를 자르지 않는다.
        const cpu = cpuCountOf(report);
        const loadDomain: SparklineDomain = { min: 0, max: cpu };
        const loadCap = cpu !== null ? `코어 ${cpu}` : undefined;
        return (
          <div key={n.node_name} className="border-t border-black/5 py-3">
            <div className="flex items-center justify-between">
              <span className="font-medium">{n.node_name}</span>
              <span className={`text-xs ${n.fresh ? "text-ok" : "text-bad"}`}>
                {n.fresh ? "정상" : "지연"} · {ageText(n.reported_at)}
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
              <Metric title="Load (1분평균)" label={`${n.node_name} Load (1분평균)`} fmt={fmtLoad}
                      domain={loadDomain} capLabel={loadCap}
                      values={pick(points, (p) => p.load1)} />
              <Metric title="메모리 사용%" label={`${n.node_name} 메모리`} fmt={fmtPct}
                      domain={PCT_DOMAIN} capLabel="상한 100%"
                      values={pick(points, (p) => p.mem_used_pct)} />
              {/* 네트워크는 링크 속도 데이터가 없어(범위 밖) 자연 상한이 없다 --
                  0 하한 + 창 최대 스케일에 수치 병기가 절대량을 말한다 */}
              <Metric title="수신 B/s" label={`${n.node_name} 수신`} fmt={fmtBps}
                      values={pick(points, (p) => p.net_rx_bps)} />
              <Metric title="송신 B/s" label={`${n.node_name} 송신`} fmt={fmtBps}
                      values={pick(points, (p) => p.net_tx_bps)} />
            </div>
          </div>
        );
      })}
    </Card>
  );
}
