import { useState } from "react";
import { useJobMetrics } from "./useMetrics";
import { WindowSelect } from "./WindowSelect";
import { Card } from "../../components/ui/Card";
import { Table } from "../../components/ui/Table";
import { BarChart } from "../../components/ui/BarChart";
import { reasonText } from "../../lib/api";
import type { BreakdownRow, JobMetrics, StateCount } from "../../lib/types";

const asArray = <T,>(v: unknown): T[] => (Array.isArray(v) ? v : []);

// 종단 집합은 domain.TERMINAL_DATA_JOB_STATES와 동일한 6종. jobState.ts의
// TERMINAL_STATES는 요청 화면용 옛 집합이라 TimedOut이 빠져 있어 쓰지 않는다.
const TERMINAL = new Set(
  ["Succeeded", "Failed", "TimedOut", "Cancelled", "Rejected", "PreviewExpired"]);

export function successRate(byState: StateCount[]): string {
  const terminal = byState.filter((r) => TERMINAL.has(r.state))
    .reduce((a, r) => a + r.count, 0);
  if (terminal === 0) return "—";
  const ok = byState.find((r) => r.state === "Succeeded")?.count ?? 0;
  return `${Math.round((ok / terminal) * 100)}% (${ok}/${terminal})`;
}

// NodesList.tsx의 humanBytes와 같은 로직의 국소 사본 -- 그쪽은 export하지 않고,
// 이 표시 하나를 위해 공용 모듈을 만드는 것은 이르다.
const BYTE_UNITS: [string, number][] = [
  ["TiB", 1024 ** 4], ["GiB", 1024 ** 3], ["MiB", 1024 ** 2],
];
function humanBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes)) return "—";
  for (const [unit, size] of BYTE_UNITS) {
    if (bytes >= size) return `${(bytes / size).toFixed(1)} ${unit}`;
  }
  return `${bytes} B`;
}

// "2026-08-09T01" -> "01시", "2026-08-09" -> "08-09"
function bucketLabel(bucket: string, kind: "hour" | "day"): string {
  return kind === "hour" ? `${bucket.slice(11, 13)}시` : bucket.slice(5);
}

function Breakdown<R extends BreakdownRow>({ title, rows, nameOf }: {
  title: string;
  rows: R[];
  nameOf: (r: R) => string | null;
}) {
  if (rows.length === 0) return null;
  return (
    <div>
      <h3 className="font-medium mb-2 text-sm">{title}</h3>
      <Table>
        <thead>
          <tr className="text-muted">
            <th className="py-1">이름</th><th>총</th><th>성공</th><th>실패</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-black/5">
              <td className="py-1">{nameOf(r) ?? "—"}</td>
              <td>{r.count}</td><td>{r.succeeded}</td><td>{r.failed}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

export function JobStatsSection() {
  const [windowH, setWindowH] = useState(24);
  const q = useJobMetrics(windowH);
  const d = q.data;
  const byState = asArray<StateCount>(d?.by_state);
  const bucketKind = d?.bucket === "day" ? "day" : "hour";
  const throughput = asArray<{ bucket: string; count: number }>(d?.throughput)
    .map((b) => ({ label: bucketLabel(b.bucket, bucketKind), value: b.count }));
  const durations = asArray<{ bucket: string; count: number }>(d?.duration_histogram)
    .map((b) => ({ label: b.bucket, value: b.count }));
  const submitWaits = asArray<{ bucket: string; count: number }>(d?.submit_wait_histogram)
    .map((b) => ({ label: b.bucket, value: b.count }));
  const reasons = asArray<{ reason_code: string; count: number }>(d?.failure_reasons);
  return (
    <Card>
      <div className="flex items-center justify-between mb-2">
        <h2 className="font-medium">잡 통계</h2>
        <WindowSelect value={windowH} onChange={setWindowH} />
      </div>
      {q.isLoading && <p className="text-muted text-sm">불러오는 중…</p>}
      <p className="text-sm">성공률 {successRate(byState)}</p>
      <div className="grid md:grid-cols-3 gap-4 mt-3">
        <div>
          <h3 className="font-medium mb-2 text-sm">처리량</h3>
          <BarChart data={throughput} label="처리량" />
        </div>
        <div>
          <h3 className="font-medium mb-2 text-sm">수행시간 분포</h3>
          <BarChart data={durations} label="수행시간 분포" />
        </div>
        <div>
          <h3 className="font-medium mb-2 text-sm">제출 대기 분포</h3>
          <BarChart data={submitWaits} label="제출 대기 분포" />
          {/* 수행시간(created_at -> updated_at)은 이 대기를 포함한 전체 수명이다 --
              나란히 놓인 두 분포의 포함 관계를 화면에 명시한다(설계 §2.4). 제외
              건수(NULL)는 백필 공백 -- 숨기지 않는다(설계 §3). 한 개의 템플릿
              리터럴 = 한 개의 텍스트 노드(getByText 가 통으로 찾도록). */}
          <p className="text-muted text-xs mt-1">
            {`집계 ${d?.submit_wait_counted ?? 0}건 · 제외(기록 없음) ${d?.submit_wait_excluded ?? 0}건 — 수행시간 분포는 이 대기를 포함합니다`}
          </p>
        </div>
      </div>
      <div className="grid md:grid-cols-3 gap-4 mt-4">
        <Breakdown title="도구별" rows={asArray<JobMetrics["by_tool"][number]>(d?.by_tool)}
                   nameOf={(r) => r.tool} />
        <Breakdown title="스토리지별" rows={asArray<JobMetrics["by_storage"][number]>(d?.by_storage)}
                   nameOf={(r) => r.storage} />
        <Breakdown title="사용자별" rows={asArray<JobMetrics["by_requester"][number]>(d?.by_requester)}
                   nameOf={(r) => r.requester_id} />
      </div>
      {reasons.length > 0 && (
        <div className="mt-4">
          <h3 className="font-medium mb-2 text-sm">실패 사유 상위</h3>
          <Table>
            <thead>
              <tr className="text-muted"><th className="py-1">사유</th><th>건수</th></tr>
            </thead>
            <tbody>
              {reasons.map((r) => (
                <tr key={r.reason_code} className="border-t border-black/5">
                  <td className="py-1">{reasonText(r.reason_code)}</td>
                  <td>{r.count}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      <p className="text-muted text-sm mt-4">
        <span className="font-medium">처리 항목/바이트</span>{" "}
        {d?.files_total ?? "—"} / {humanBytes(d?.bytes_total ?? null)}
      </p>
    </Card>
  );
}
