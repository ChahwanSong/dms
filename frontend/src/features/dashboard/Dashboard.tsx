import { useInfraMetrics, useJobMetrics } from "./useMetrics";
import { NodeMetricsSection } from "./NodeMetricsSection";
import { JobStatsSection } from "./JobStatsSection";
import { QueueSection } from "./QueueSection";
import { MetricTile } from "../../components/ui/MetricTile";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import type { PillVariant } from "../../lib/jobState";
import type { InfraComponent, StateCount } from "../../lib/types";

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
  const jobsQ = useJobMetrics(24);
  const infraQ = useInfraMetrics();
  // 방어적 정규화 -- 배열 아닌 페이로드 하나가 화면을 죽이면 안 된다
  const byState = Array.isArray(jobsQ.data?.by_state) ? jobsQ.data.by_state : [];
  const kpi = kpiFromStates(byState);
  const components = Array.isArray(infraQ.data?.components)
    ? infraQ.data.components : [];
  const jobImage = infraQ.data?.job_image;
  // 드리프트 = live(워크로드 파드템플릿)와 동봉 매니페스트가 "둘 다 있고" 다르다.
  // 어느 한쪽이 null 이면 비교하지 않는다 -- 추측 금지(설계 §4).
  const drifted = (c: InfraComponent) =>
    c.image != null && c.manifest_image != null && c.image !== c.manifest_image;
  return (
    <section className="space-y-5">
      <h1 className="text-2xl font-bold">대시보드</h1>
      {/* 5타일: md 폭에서 5열은 타일당 ~130px -- MetricTile(라벨 한 줄 + 숫자)이
          충분히 읽힌다(실측). 좁은 폭은 2열 그대로. */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <MetricTile label="실행 중" value={kpi.running} />
        <MetricTile label="대기" value={kpi.pending} />
        <MetricTile label="성공(24h)" value={kpi.succeeded} />
        <MetricTile label="실패(24h)" value={kpi.failed} />
        {/* 계획 거부는 data_jobs 가 생기기 전의 종단이라 위 4타일(전부 by_state
            = data_jobs 집계) 밖이다 -- results 집계 필드가 유일한 원천. 0 은
            0 으로 표기한다(null≠0 -- 거부 없음은 정상값). */}
        <MetricTile label="계획 거부(24h)" value={jobsQ.data?.plan_rejected ?? 0} />
      </div>
      {/* grid 에 홀로 남은 컴포넌트 카드는 md 반폭이 어색해 전폭으로 편다. */}
      <Card>
        <h2 className="font-medium mb-3">컴포넌트</h2>
        <ul className="space-y-2 text-sm">
          {components.map((c) => (
            <li key={c.component} className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="shrink-0">{c.component}</span>
                <span className="text-muted text-xs truncate grow">
                  {c.image ?? "—"}
                </span>
                {/* neutral 이다: 드리프트는 포탈 롤아웃 직후 반드시 생기는 정상
                    상태이고, 같은 행의 bad 는 이미 verdict=failed 를 뜻한다.
                    빨강을 겹치면 정상 롤아웃마다 "고장" 으로 오독돼 알람 피로가
                    구조적으로 생긴다. 긴급함은 아래 문장(미래형 + 되돌림 결과)이
                    진다 -- 배지는 눈을 그 문장으로 끌기만 하면 된다. */}
                {drifted(c) && <StatusPill state="드리프트" variant="neutral" />}
                <span className="text-xs tabular-nums shrink-0">
                  {`${c.ready ?? "—"}/${c.desired ?? "—"}`}
                </span>
                <StatusPill state={c.verdict ?? "unknown"}
                            variant={c.verdict ? VERDICT_VARIANT[c.verdict] : "neutral"} />
              </div>
              {/* 롤아웃이 성공(applied)해도 뜨는 줄이다 -- 고장이 아니라 "매니페스트가
                  아직 옛 태그"라는 뜻이며, 문장은 그 결과(다음 apply의 되돌림)를
                  미래형으로 말한다. 한 개의 템플릿 리터럴 = 한 개의 텍스트 노드. */}
              {drifted(c) && (
                <p className="text-xs text-bad">
                  {`매니페스트 ${c.manifest_image} — 다음 kubectl apply가 이 태그로 되돌립니다`}
                </p>
              )}
            </li>
          ))}
        </ul>
        {jobImage?.live && jobImage?.manifest && jobImage.live !== jobImage.manifest && (
          <p className="mt-3 text-xs text-bad">
            {/* source=db(릴리스의 job-image 오버라이드)면 apply 는 되돌리지 못한다
                -- 같은 문구를 쓰면 거짓 경고가 된다(슬라이스 35). */}
            {jobImage.source === "db"
              ? `잡 이미지 ${jobImage.live} · 매니페스트 ${jobImage.manifest} — 릴리스 오버라이드가 우선이라 kubectl apply 로 되돌아가지 않습니다`
              : `잡 이미지 ${jobImage.live} · 매니페스트 ${jobImage.manifest} — 다음 kubectl apply가 매니페스트 값으로 되돌립니다`}
          </p>
        )}
      </Card>
      <NodeMetricsSection />
      {/* 설계 §3: 「잡 통계」 앞 자립형 카드 -- 잡 통계와 달리 DB 가 아니라
          라이브 PodGroup 을 본다 */}
      <QueueSection />
      <JobStatsSection />
      {/* 「최근 작업」 카드는 제거됐다(2026-08-23 사용자 결정): 작업 메뉴의
          「전체 작업」 화면(요청자 열·필터·무한 스크롤)과 중복이었다 -- 대시보드는
          개요(KPI·컴포넌트·큐·통계)만 남긴다. */}
    </section>
  );
}
