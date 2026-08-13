import { useState } from "react";
import { useJobMetrics } from "./useMetrics";
import { WindowSelect } from "./WindowSelect";
import { Card } from "../../components/ui/Card";
import { Table } from "../../components/ui/Table";
import { BarChart, r2 } from "../../components/ui/BarChart";
import { reasonText } from "../../lib/api";
import type { BreakdownRow, JobMetrics, SecondsSummary, StateCount } from "../../lib/types";

const asArray = <T,>(v: unknown): T[] => (Array.isArray(v) ? v : []);

// 종단 집합은 domain.TERMINAL_DATA_JOB_STATES와 동일한 6종. jobState.ts의
// TERMINAL_STATES는 요청 화면용 옛 집합이라 TimedOut이 빠져 있어 쓰지 않는다.
const TERMINAL = new Set(
  ["Succeeded", "Failed", "TimedOut", "Cancelled", "Rejected", "PreviewExpired"]);

// 종단 잡이 없으면 null -- 0%(전패)도 100%(전승)도 아닌 "표본 없음"이다. 비율로
// 뭉개면 화면이 거짓말한다(null ≠ 0 규약).
export function successParts(byState: StateCount[]): { ok: number; terminal: number } | null {
  const terminal = byState.filter((r) => TERMINAL.has(r.state))
    .reduce((a, r) => a + r.count, 0);
  if (terminal === 0) return null;
  const ok = byState.find((r) => r.state === "Succeeded")?.count ?? 0;
  return { ok, terminal };
}

// 문자열 계약("N% (ok/terminal)" 또는 "—")은 유지한다 -- 기존 소비자 호환.
export function successRate(byState: StateCount[]): string {
  const p = successParts(byState);
  if (p === null) return "—";
  return `${Math.round((p.ok / p.terminal) * 100)}% (${p.ok}/${p.terminal})`;
}

// 큰 % 숫자의 톤. 임계(80/50)는 배치 잡 운영 감각의 절단이다: 8할 이상이면
// 건강(ok), 절반 이상이면 주의(busy), 절반 미만은 문제(bad). 클래스명
// text-ok/text-busy/text-bad 는 DS 계약 -- 값(색)만 바꿀 수 있다.
export function rateTone(pct: number): "text-ok" | "text-busy" | "text-bad" {
  if (pct >= 80) return "text-ok";
  if (pct >= 50) return "text-busy";
  return "text-bad";
}

// 성공률 = 큰 % 숫자 + 성공/비성공 스택 미터(슬라이스 20 시절 텍스트 한 줄의
// 대체). 세그먼트 폭은 반올림 전 원비율(r2) -- 표기 47% 와 기하 46.51% 는 다른
// 정밀도를 가져도 된다. 세그먼트 사이 2px 틈은 표면색 구분자(테두리 금지).
function SuccessRateBar({ byState }: { byState: StateCount[] }) {
  const p = successParts(byState);
  if (p === null) return <p className="text-muted text-sm">성공률 — 종단 잡 없음</p>;
  const pct = Math.round((p.ok / p.terminal) * 100);
  const okPct = r2((p.ok / p.terminal) * 100);
  const failed = p.terminal - p.ok;
  return (
    <div className="max-w-md">
      <p className="flex items-baseline gap-2">
        <span className={`text-2xl font-semibold tabular-nums ${rateTone(pct)}`}>{pct}%</span>
        {/* "비성공"에는 Failed 외에 TimedOut·Cancelled·Rejected·PreviewExpired 가
            섞여 있어 "실패"라 쓰지 않는다 -- 분모 표기는 종단 총수. */}
        <span className="text-xs text-muted">성공률 · 성공 {p.ok} / 종단 {p.terminal}</span>
      </p>
      <div role="img" aria-label="성공률"
           className="mt-1 flex h-2 gap-0.5 overflow-hidden rounded-full">
        {p.ok > 0 &&
          <div className="bg-ok" style={{ width: `${okPct}%` }} title={`성공 ${p.ok}`} />}
        {failed > 0 &&
          <div className="bg-bad" style={{ width: `${r2(100 - okPct)}%` }}
               title={`비성공 ${failed}`} />}
      </div>
    </div>
  );
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

// humanBytes 와 같은 국소 헬퍼(공용 모듈은 이르다) -- 요약 수치(초)를 사람
// 단위로. 후행 .0 은 소음이라 떨군다("1.0h" 아닌 "1h").
const fmt1 = (n: number) => {
  const s = n.toFixed(1);
  return s.endsWith(".0") ? s.slice(0, -2) : s;
};
export function humanSeconds(s: number | null | undefined): string {
  // null/undefined = 표본 없음 -- 0("0s", 즉시 종료라는 정상값)과 뭉개지 않는다.
  if (s === null || s === undefined || !Number.isFinite(s)) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${fmt1(s / 60)}m`;
  if (s < 86400) return `${fmt1(s / 3600)}h`;
  return `${fmt1(s / 86400)}d`;
}

// 숫자 요약 한 행. summary 가 null(표본 없음)이면 세 칸 전부 "—" -- 0 요약으로
// 뭉개면 "즉시 끝났다"는 거짓말이 된다(null ≠ 0 규약).
function SummaryRow({ name, summary }: {
  name: string; summary: SecondsSummary | null | undefined;
}) {
  return (
    <tr className="border-t border-black/5">
      <td className="py-1">{name}</td>
      <td>{humanSeconds(summary?.mean_seconds ?? null)}</td>
      <td>{humanSeconds(summary?.p50_seconds ?? null)}</td>
      <td>{humanSeconds(summary?.p95_seconds ?? null)}</td>
    </tr>
  );
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
  const schedWaits = asArray<{ bucket: string; count: number }>(d?.sched_wait_histogram)
    .map((b) => ({ label: b.bucket, value: b.count }));
  const execRuntimes = asArray<{ bucket: string; count: number }>(d?.exec_runtime_histogram)
    .map((b) => ({ label: b.bucket, value: b.count }));
  const reasons = asArray<{ reason_code: string; count: number }>(d?.failure_reasons);
  const rejections = asArray<{ reason_code: string; count: number }>(d?.plan_rejection_reasons);
  return (
    <Card>
      <div className="flex items-center justify-between mb-2">
        <h2 className="font-medium">잡 통계</h2>
        <WindowSelect value={windowH} onChange={setWindowH} />
      </div>
      {q.isLoading && <p className="text-muted text-sm">불러오는 중…</p>}
      {/* 로딩 중(d 없음)은 "모름"이라 성공률 블록 자체를 내지 않는다 -- 착지 후
          종단 0건일 때만 "종단 잡 없음"(정상값)을 명시한다. */}
      {d && <SuccessRateBar byState={byState} />}
      {/* 2열 × 3행 배치(슬라이스 31): 위 행 처리량|전체 수명, 중간 행 두 "대기"
          분포(제출 대기 = created_at→첫 비-Pending DMS 픽업 지연, 스케줄
          대기(Volcano) = execution 제출→첫 RUNNING 관측 -- 다른 것을 잰다, 설계
          §2.2), 아래 행 실행시간|숫자 요약. 4열로 눌러 넣으면 md 폭에서 차트가
          읽히지 않아 2열 줄바꿈을 유지한다(슬라이스 20 결정). */}
      <div className="grid md:grid-cols-2 gap-4 mt-3">
        <div>
          <h3 className="font-medium mb-2 text-sm">처리량</h3>
          <BarChart data={throughput} label="처리량" />
        </div>
        <div>
          {/* 라벨 정직화(슬라이스 31): created_at→updated_at 는 실행이 아니라
              제출·확인(사람)·스케줄 대기까지 전부 합산한 수명이다 -- 「수행시간」
              이라 부르면 실행이 느려 보인다(확인 대기 며칠이 그대로 실린다).
              API 필드명(duration_histogram)은 소비자 호환으로 유지, 이름만 정직하게. */}
          <h3 className="font-medium mb-2 text-sm">전체 수명 분포</h3>
          <BarChart data={durations} label="전체 수명 분포" />
          <p className="text-muted text-xs mt-1">
            {`created_at→updated_at 전체 수명 — 제출·확인(사람)·스케줄 대기를 모두 포함합니다. 실행시간 분포가 순수 실행 구간입니다`}
          </p>
        </div>
        <div>
          <h3 className="font-medium mb-2 text-sm">제출 대기 분포</h3>
          <BarChart data={submitWaits} label="제출 대기 분포" />
          {/* 전체 수명(created_at -> updated_at)은 이 대기를 포함한다 -- 분포 간
              포함 관계를 화면에 명시한다(설계 §2.4). 제외 건수(NULL)는 백필 공백
              -- 숨기지 않는다(설계 §3). 한 개의 템플릿 리터럴 = 한 개의 텍스트
              노드(getByText 가 통으로 찾도록). */}
          <p className="text-muted text-xs mt-1">
            {`집계 ${d?.submit_wait_counted ?? 0}건 · 제외(기록 없음) ${d?.submit_wait_excluded ?? 0}건 — 전체 수명 분포는 이 대기를 포함합니다`}
          </p>
        </div>
        <div>
          <h3 className="font-medium mb-2 text-sm">스케줄 대기(Volcano) 분포</h3>
          <BarChart data={schedWaits} label="스케줄 대기(Volcano) 분포" />
          {/* 제출 대기와 같은 캡션 패턴(집계/제외 -- 설계 §3) + 근사 오차 명시
              (설계 §2.2: 스테퍼 틱 5s + vcjob status 지연이 더해진 근사이지
              PodGroup 이 보고하는 값이 아니다). 제외(기록 없음)에는 과거 잡(백필
              없음 §2.5)·Running 미도달·한 틱 완료·스텁 백엔드가 모두 들어간다 --
              도입 직후 "집계 0건 · 제외 N건"이 정상이며, 이 수가 보여야 화면이
              "데이터 없음"으로 거짓말하지 않는다(설계 §2.6). */}
          <p className="text-muted text-xs mt-1">
            {`집계 ${d?.sched_wait_counted ?? 0}건 · 제외(기록 없음) ${d?.sched_wait_excluded ?? 0}건 — 제출 대기(DMS 픽업 지연)와 달리 Volcano 큐 대기의 근사입니다(스테퍼 틱 5초 오차)`}
          </p>
        </div>
        <div>
          <h3 className="font-medium mb-2 text-sm">실행시간 분포</h3>
          <BarChart data={execRuntimes} label="실행시간 분포" />
          {/* 실행시간 = updated_at − exec_submitted_at − sched_wait 의 파생 계산
              (스키마 무변경, 슬라이스 31) -- 순수 실행 구간의 근사다. sched_wait
              캡션 관례 그대로 집계/제외를 명시한다: 제외에는 앵커 없는 과거 잡
              (슬라이스 20 이전)·첫 RUNNING 관측 전에 끝난 한 틱 완료·실행 미도달
              (preflight/preview 실패)이 들어간다 -- 도입 직후 "집계 0건 · 제외
              N건"이 정상이고, 이 수가 보여야 공백이 숨지 않는다. */}
          <p className="text-muted text-xs mt-1">
            {`집계 ${d?.exec_runtime_counted ?? 0}건 · 제외(앵커 없음·한 틱 완료·실행 미도달) ${d?.exec_runtime_excluded ?? 0}건 — 첫 RUNNING 관측→종단의 근사입니다(스테퍼 틱 오차 ±10초)`}
          </p>
        </div>
        <div>
          <h3 className="font-medium mb-2 text-sm">숫자 요약</h3>
          <Table>
            <thead>
              <tr className="text-muted">
                <th className="py-1">구간</th><th>평균</th><th>중앙값</th><th>p95</th>
              </tr>
            </thead>
            <tbody>
              <SummaryRow name="전체 수명" summary={d?.duration_summary} />
              <SummaryRow name="실행시간" summary={d?.exec_runtime_summary} />
            </tbody>
          </Table>
          {/* p50/p95 는 백엔드 nearest-rank 실측값(보간 없음) -- 표본 없음(null)
              은 위 SummaryRow 가 "—" 로 낸다(0 과 뭉개지 않는다). */}
          <p className="text-muted text-xs mt-1">
            각 분포와 같은 표본의 평균·중앙값·p95(실측값)입니다
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
      {/* 계획 거부 사유 상위(results 집계): 실패 사유 상위(잡 실패)와 별도 표다 --
          섞으면 라벨이 거짓말이 된다. 사유 한글화는 같은 reasonText 재사용. */}
      {rejections.length > 0 && (
        <div className="mt-4">
          <h3 className="font-medium mb-2 text-sm">계획 거부 사유 상위</h3>
          <Table>
            <thead>
              <tr className="text-muted"><th className="py-1">사유</th><th>건수</th></tr>
            </thead>
            <tbody>
              {rejections.map((r) => (
                <tr key={r.reason_code} className="border-t border-black/5">
                  <td className="py-1">{reasonText(r.reason_code)}</td>
                  <td>{r.count}</td>
                </tr>
              ))}
            </tbody>
          </Table>
          {/* 정직화: 계획 거부는 data_jobs 가 생기기 전의 종단이라 위의 모든
              분포·분해 표(전부 data_jobs 집계)에 안 잡힌다. 한 개의 템플릿
              리터럴 = 한 개의 텍스트 노드. */}
          <p className="text-muted text-xs mt-1">
            {`계획 거부 ${d?.plan_rejected ?? 0}건 — 계획 단계 거부는 잡이 되지 못해 잡 통계 밖입니다. 이 표가 그 구간입니다`}
          </p>
        </div>
      )}
      <p className="text-muted text-sm mt-4">
        <span className="font-medium">처리 항목/바이트</span>{" "}
        {d?.files_total ?? "—"} / {humanBytes(d?.bytes_total ?? null)}
      </p>
    </Card>
  );
}
