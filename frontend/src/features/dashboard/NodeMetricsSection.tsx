import { useState, type ReactNode } from "react";
import { RefreshCw } from "lucide-react";
import { loadErrorText, useNodeMetrics } from "./useMetrics";
import { useNodes } from "./useDashboard";
import { WindowClampNote, WindowSelect } from "./WindowSelect";
import { Card } from "../../components/ui/Card";
import { Sparkline, type SparklineDomain } from "../../components/ui/Sparkline";
import type { Node, NodeInfo, NodeMetricPoint, NodeMetricSeries } from "../../lib/types";
import { PlacementBadges } from "../nodes/NodePlacement";
import { kstStampEpoch } from "../../lib/datetime";

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

// 메모리·디스크·CPU 사용%의 자연 상한 -- 차트가 창과 무관한 절대 스케일로 그려진다
const PCT_DOMAIN: SparklineDomain = { min: 0, max: 100 };

// CPU 사용률·I/O 대기%(2026-10-16): 서버(metrics_series._pct)가 이미 0..100 만 통과시키지만 구 서버 응답엔 키
// 자체가 없다(undefined) -- 같은 범위 검사로 null(모름)로 접는다. 0 은 유휴 노드의 정상값이라 그대로 둔다.
const pctOf = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 100 ? v : null;

// 창 안의 마지막 유효값(Metric 의 "현재값"과 같은 규칙) -- CPU 칸 캡션의 I/O 대기%
function lastValid(values: (number | null)[]): number | null {
  for (let i = values.length - 1; i >= 0; i -= 1) {
    const v = values[i];
    if (v !== null) return v;
  }
  return null;
}

// 리포트는 스키마 검증 없이 저장된다(위 asArray 와 같은 방어) -- cpu_count 는
// 양의 유한수일 때만 신뢰한다. 구형 에이전트 리포트엔 필드 자체가 없다: null
// (모름)로 두면 load 차트가 창 최대 폴백으로 그려진다. 0 이나 문자열 오염값을
// 거짓 상한으로 뭉개지 않는다.
function cpuCountOf(report: unknown): number | null {
  const os = (report as { os?: { cpu_count?: unknown } } | null | undefined)?.os;
  const v = os?.cpu_count;
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
}

// 부하(load) 칸의 뜻 -- 칸 제목의 툴팁과 카드 아래 각주가 같은 문장을 쓴다(2026-10-16).
const LOAD_HELP = "부하(load) = 실행 중·실행 대기·I/O 대기 스레드 수의 평균이라 코어 수를 넘을 수 있으며 CPU 사용률이 아닙니다.";

function ageText(reportedAt: string, nowMs: number): string {
  const ms = nowMs - Date.parse(reportedAt);
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const m = Math.floor(ms / 60000);
  if (m < 1) return "방금";
  if (m < 60) return `${m}분 전`;
  return `${Math.floor(m / 60)}시간 전`;
}

function pick(points: NodeMetricPoint[], f: (p: NodeMetricPoint) => number | null) {
  return points.map(f);
}

// 점마다의 가로 위치 = 창(start~end) 안 시각 비율(2026-11-11). 순번으로 놓으면 보고가 끊긴 몇 시간이 한 칸으로 줄어
// 사라졌다(서버가 공백에 null 표지를 끼워 선도 끊는다 -- metrics_series.build_node_points). 창 경계나 어느 점의 시각을
// 모르면 undefined -- Sparkline 이 순번 배치로 돌아간다(모르는 시각을 지어내지 않는다).
export function timeFractions(points: NodeMetricPoint[], start: unknown, end: unknown): number[] | undefined {
  const s = typeof start === "string" ? Date.parse(start) : NaN;
  const e = typeof end === "string" ? Date.parse(end) : NaN;
  if (!Number.isFinite(s) || !Number.isFinite(e) || e <= s) return undefined;
  const xs = points.map((p) => (typeof p.at === "string" ? (Date.parse(p.at) - s) / (e - s) : NaN));
  return xs.every((x) => Number.isFinite(x)) ? xs : undefined;
}

function Metric({ title, values, label, fmt, domain, capLabel, xs }: {
  title: ReactNode; values: (number | null)[]; label: string;
  fmt: (v: number) => string; domain?: SparklineDomain; capLabel?: string; xs?: number[];
}) {
  // 현재값 = 마지막 유효점(마지막 점이 null 결측이어도 직전 실측이 "현재"다),
  // 최대값 = 창 내 유효점의 최대. 유효점이 없으면 병기도 "—" -- 0 으로 뭉개지
  // 않는다. 상대값 스케일에서 절대 수치가 사라지는 것을 이 병기가 보상한다.
  const valid = values.filter((v): v is number => v !== null && Number.isFinite(v));
  const cur = valid.length > 0 ? valid[valid.length - 1] : null;
  // role=group + 이름: 칸이 다섯이 되며(2026-10-16) "어느 칸의 —인가"를 화면 낭독기·테스트가 구분하게.
  // 머리줄 flex-wrap: md 의 5칸(칸당 ~80px)에서 제목·캡션이 서로를 짓눌러 글자 단위로 깨지지 않고 캡션이
  // 다음 줄로 내려가게 -- 노드당 한 줄(2026-10-01)은 격자 행 기준이라 칸 안 줄바꿈과 무관하다.
  return (
    <div role="group" aria-label={label}>
      <div className="flex flex-wrap items-center justify-between gap-x-1 text-muted text-xs">
        <span>{title}</span>
        {/* 상한 라벨은 SVG 밖 캡션 -- preserveAspectRatio="none" 아래 SVG 텍스트는
            가로로 왜곡된다(Sparkline 주석 참조) */}
        {capLabel !== undefined && <span>{capLabel}</span>}
      </div>
      <Sparkline values={values} label={label} domain={domain} xs={xs} />
      <div className="text-muted text-xs">
        {cur === null ? "—" : `${fmt(cur)} · 최대 ${fmt(Math.max(...valid))}`}
      </div>
    </div>
  );
}

// 노드당 한 줄: CPU 사용률·부하(load 1분)·메모리·수신·송신(2026-10-01 사용자 요청 -- 펼침(load5/load15·스토리지
// 사용%·마운트/도구/계정 요약)을 없앴다. 스토리지 사용%는 공유 FS 라 노드마다 같은 값이었고, 마운트·도구·계정 상세는
// 노드 화면(/admin/nodes)에 있다).
// CPU 사용률 칸(2026-10-16 사용자 보고 「대시보드의 cpu 사용량은 어떻게 집계하는거야? 코어수가 128이어도 200이
// 넘어가고 막 그러던데.」): 그때까지 CPU 처럼 읽히던 칸은 load1(실행·실행 대기·I/O 대기 스레드 수의 1분 평균)이라
// 코어 수를 정당하게 넘었다. 실제 사용률(/proc/stat 차분, 에이전트 계산)을 첫 칸에 두고 load 칸은 이름부터
// "부하"로 바꿔 사용률이 아님을 밝힌다. 5칸이라 md 이상은 5열(한 행 유지), 모바일은 2열로 쌓인다.
export function NodeMetricsSection() {
  const [windowH, setWindowH] = useState(24);
  const metricsQ = useNodeMetrics(windowH);
  const nodesQ = useNodes();
  const series = asArray<NodeMetricSeries>(metricsQ.data?.nodes);
  const reports = new Map(
    asArray<Node>(nodesQ.data).map((n) => [n.node_name, n.report] as const));
  // 배치 제외·cordon 배지(2026-10-02) -- 노드 목록 응답의 exclusion·report.k8s_node 를 그대로 쓴다.
  const nodeInfo = new Map(asArray<NodeInfo>(nodesQ.data).map((n) => [n.node_name, n] as const));
  // 신선도 나이의 「지금」 = 마지막 노드 목록 폴링 시각(2026-11-11). dataUpdatedAt 을 읽어야 내용이 같은 재조회(보고가 멈춘
  // 노드)에도 다시 그려져 나이가 자란다 -- Date.now() 만 쓰면 목록 data 가 바뀔 때까지 「지연 · 5분 전」이 굳는다.
  const nowMs = nodesQ.dataUpdatedAt > 0 ? nodesQ.dataUpdatedAt : Date.now();
  const winStart = metricsQ.data?.start;
  const winEnd = metricsQ.data?.end;
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <h2 className="font-medium">노드/리소스</h2>
        {/* 새로고침(2026-10-02 사용자 요청): 시계열은 기간 재조회 위주라 폴링이 없다(useMetrics) -- 지금 값을
            보고 싶을 때 누른다. 노드 목록(신선도·코어 수)도 함께 다시 읽는다. 마지막 갱신 시각을 옆에 둔다.
            안쪽 묶음도 flex-wrap + 글자 nowrap(2026-11-11): 390px 에서 한 줄 flex 가 줄어 「새로고/침」·「갱신/19:28:12」로
            음절 중간에서 꺾였다(NodesList 의 9fbef86 규약과 같은 처방). */}
        <div className="flex flex-wrap items-center justify-end gap-2">
          {metricsQ.dataUpdatedAt > 0 && (
            <span className="text-xs text-muted whitespace-nowrap" aria-label="마지막 갱신">
              {`갱신 ${kstStampEpoch(Math.floor(metricsQ.dataUpdatedAt / 1000)).slice(11, 19)}`}
            </span>
          )}
          <button type="button" aria-label="노드/리소스 새로고침" title="새로고침"
                  disabled={metricsQ.isFetching}
                  onClick={() => { void metricsQ.refetch(); void nodesQ.refetch(); }}
                  className="inline-flex items-center gap-1 whitespace-nowrap rounded-lg border border-line bg-panel px-2.5 py-1
                             text-xs text-muted hover:text-ink disabled:opacity-60">
            <RefreshCw className={`h-3.5 w-3.5 ${metricsQ.isFetching ? "animate-spin" : ""}`} aria-hidden />
            새로고침
          </button>
          <WindowSelect value={windowH} onChange={setWindowH} />
        </div>
      </div>
      {metricsQ.isLoading && <p className="text-muted text-sm">불러오는 중…</p>}
      {/* 첫 조회 실패·노드 0대(2026-11-11): 예전엔 둘 다 머리줄만 남은 빈 카드라 원인이 안 보였다. 노드 0대는 응답이 배열로
          왔을 때만 말한다 -- 배열이 아닌 페이로드는 「없음」이 아니라 모름이다. */}
      {metricsQ.isError && !metricsQ.data && (
        <p className="text-bad text-sm">{loadErrorText("노드 지표를", metricsQ.error)}</p>
      )}
      {Array.isArray(metricsQ.data?.nodes) && metricsQ.data.nodes.length === 0 && (
        <p className="text-muted text-sm break-keep">보고한 노드가 없습니다 — 에이전트(DaemonSet) 상태를 확인하세요.</p>
      )}
      {metricsQ.data && <WindowClampNote selected={windowH} served={metricsQ.data.window_hours} />}
      {series.map((n) => {
        const points = asArray<NodeMetricPoint>(n.points);
        const xs = timeFractions(points, winStart, winEnd);
        const report = reports.get(n.node_name);
        // 신선도·나이는 폴링되는(5초) 노드 목록 값으로 그린다(2026-11-11). 시계열 응답의 fresh·reported_at 은 처음 받은 순간의
        // 값이라(이 쿼리는 폴링하지 않는다) 정상 노드가 「정상 · 6분 전」으로 늙고, 멈춘 노드도 초록 「정상」으로 남았다 --
        // 같은 줄의 배치 제외·cordon 배지(노드 목록)와도 시점이 갈렸다. 노드 목록에 아직 없을 때만 시계열 값으로 돌아간다.
        const info = nodeInfo.get(n.node_name);
        const fresh = info?.fresh ?? n.fresh;
        const reportedAt = info?.reported_at ?? n.reported_at;
        // 코어 수는 사실상 불변이라 시계열이 아닌 최신 리포트에서 읽는다
        // (metrics_series.py 의 같은 취지 주석). load 는 코어 수를 넘을 수 있으므로
        // 상한이 아니라 기준선이다 -- Sparkline 이 스케일을 max(코어수, 창 최대)로
        // 늘려 초과를 자르지 않는다.
        const cpu = cpuCountOf(report);
        const loadDomain: SparklineDomain = { min: 0, max: cpu };
        const loadCap = cpu !== null ? `코어 ${cpu} 기준` : undefined;
        const iowait = lastValid(pick(points, (p) => pctOf(p.cpu_iowait_pct)));
        return (
          <div key={n.node_name} className="border-t border-black/5 py-3">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-2">
                <span className="font-medium">{n.node_name}</span>
                {info && <PlacementBadges node={info} />}
              </span>
              <span className={`text-xs ${fresh ? "text-ok" : "text-bad"}`}>
                {fresh ? "정상" : "지연"} · {ageText(reportedAt, nowMs)}
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-2">
              {/* 구형 에이전트(필드 없음)·재시작 후 첫 보고는 null -- 칸은 "—"(0% 아님). I/O 대기는 알 때만 캡션 */}
              <Metric title="CPU 사용률" label={`${n.node_name} CPU 사용률`} fmt={fmtPct}
                      domain={PCT_DOMAIN}
                      capLabel={iowait !== null ? `I/O 대기 ${fmtPct(iowait)}` : undefined}
                      values={pick(points, (p) => pctOf(p.cpu_used_pct))} xs={xs} />
              {/* load 는 코어 수를 넘을 수 있으므로 코어 수는 상한이 아니라 기준선이다(위 주석) */}
              <Metric title={<span title={LOAD_HELP}>부하(load 1분)</span>}
                      label={`${n.node_name} 부하(load 1분)`} fmt={fmtLoad}
                      domain={loadDomain} capLabel={loadCap}
                      values={pick(points, (p) => p.load1)} xs={xs} />
              <Metric title="메모리 사용%" label={`${n.node_name} 메모리`} fmt={fmtPct}
                      domain={PCT_DOMAIN} capLabel="상한 100%"
                      values={pick(points, (p) => p.mem_used_pct)} xs={xs} />
              {/* 네트워크는 링크 속도 데이터가 없어(범위 밖) 자연 상한이 없다 --
                  0 하한 + 창 최대 스케일에 수치 병기가 절대량을 말한다 */}
              <Metric title="수신 B/s" label={`${n.node_name} 수신`} fmt={fmtBps}
                      values={pick(points, (p) => p.net_rx_bps)} xs={xs} />
              <Metric title="송신 B/s" label={`${n.node_name} 송신`} fmt={fmtBps}
                      values={pick(points, (p) => p.net_tx_bps)} xs={xs} />
            </div>
          </div>
        );
      })}
      {series.length > 0 && (
        <p className="mt-2 text-xs text-muted">{`CPU 사용률 = 직전 보고 대비 CPU 가 일한 시간 비율(0–100%). ${LOAD_HELP}`}</p>
      )}
    </Card>
  );
}
