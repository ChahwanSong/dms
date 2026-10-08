import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CircleAlert, LoaderCircle, TriangleAlert } from "lucide-react";
import { useRequest, useRequestJobs, useCancelJob, useCancelRequest } from "./useJobs";
import { Card } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { REQUEST_TERMINAL_STATES, isTerminal } from "../../lib/jobState";
import { kstStamp } from "../../lib/datetime";
import { absSummary, pathSummary } from "../../lib/storagePaths";
import { useStorageBackends, useStorageRoots } from "../storages/useUserStorages";
import type { DataJob } from "../../lib/types";
import { StageNavProvider } from "./stageNav";
import { OutcomeCard } from "./OutcomeCard";
import { JobCard } from "./JobCard";
import { ActivitySection } from "./RequestActivity";
import { deriveJobStages, stageAnnouncement, stageSnapshot, type StageSnapshot } from "./jobStages";
import { deriveOutcome } from "./requestOutcome";
import { kstClock } from "./format";
import { FOCUS_RING, SMALL_BTN } from "./ui";

// 요청 상세(/jobs/:requestId) -- 2026-10-08 재설계(사용자 요청: preflight(preview)와 execution 의 출력·로그를
// 분리해서 보이게 + 결과 화면 개선). 위에서 아래로: 머리말 → (재조회 실패 띠) → 결과 배너 → 데이터 작업(잡 카드:
// ① 사전 점검·미리보기 / 작업 컨펌 / ② 실행) → 활동(전이 이력·진단 이벤트). 이 파일은 조립만 한다 -- 판정은
// jobStages·requestOutcome(순수), 그리기는 각 컴포넌트 파일.
//
// 폴링(useJobs): 요청 3초(비종단 동안), 잡 2초(요청 비종단 또는 비종단 잡이 있을 때 -- 잡 0개여도 돈다). 종단이면
// 전부 멈춘다(e2e E6: 종단 뒤 /jobs 요청 0건). 잡 키를 무효화하는 새 코드는 넣지 않는다.

const ERROR_BOX = "flex flex-wrap items-start gap-2 rounded-card border border-bad/30 bg-badbg px-4 py-3 text-sm text-bad";

function LoadingView({ requestId }: { requestId: string }) {
  // 실제 높이와 비슷한 자리표시(배너 + KPI 4칸 + 잡 카드)로 데이터 도착 때 화면이 밀리지 않게(CLS).
  return (
    <section className="max-w-6xl space-y-5" aria-busy="true">
      <div>
        <h1 className="text-2xl font-bold">요청 상세</h1>
        <p className="mt-1 font-mono text-xs text-ink/70 [overflow-wrap:anywhere]">{requestId}</p>
      </div>
      <p className="sr-only" role="status">불러오는 중…</p>
      <Card>
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="mt-3 h-4 w-2/3" />
        <Skeleton className="mt-2 h-4 w-1/3" />
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-16" />)}
        </div>
      </Card>
      <Card>
        <Skeleton className="h-5 w-1/3" />
        <Skeleton className="mt-4 h-24" />
        <Skeleton className="mt-3 h-24" />
      </Card>
    </section>
  );
}

function RetryButton({ onClick }: { onClick: () => void }) {
  return <button type="button" className={SMALL_BTN} onClick={onClick}>다시 시도</button>;
}

export function RequestDetail() {
  const { requestId = "" } = useParams();
  const req = useRequest(requestId);
  const reqTerminal = req.data ? REQUEST_TERMINAL_STATES.has(req.data.state) : undefined;
  const jobs = useRequestJobs(requestId, true, { requestTerminal: reqTerminal });
  const cancel = useCancelJob(requestId);
  const cancelRequest = useCancelRequest(requestId);
  // 스토리지 뿌리 맵(관리자 응답에만 managed_root 가 실린다 — 비관리자는 빈 맵)
  const roots = useStorageRoots();
  // 이름 → backend_type(같은 쿼리 -- 요청 추가 없음). 보조 그룹 주의문용.
  const backends = useStorageBackends();

  // 잡 목록 방어적 정규화(배열이 아니면 모름 취급) + 단계 모델. 잡 조회 실패면 null(모름) -- 0개와 다르다.
  const jobList: DataJob[] | null = jobs.isError && !jobs.data ? null
    : Array.isArray(jobs.data) ? jobs.data : jobs.data === undefined ? null : [];
  const models = useMemo(() => (jobList ?? []).map(deriveJobStages), [jobList]);

  // 화면 낭독: 구획 상태가 **바뀔 때만** 한 줄 알린다(첫 로드는 조용히). 이전 값은 ref 에.
  const prevSnaps = useRef<Record<string, StageSnapshot> | null>(null);
  const [announce, setAnnounce] = useState("");
  useEffect(() => {
    if (!jobList) return;
    const next: Record<string, StageSnapshot> = {};
    let msg: string | null = null;
    jobList.forEach((j, i) => {
      const snap = stageSnapshot(models[i]);
      next[j.job_id] = snap;
      if (msg === null && prevSnaps.current !== null) {
        const m = stageAnnouncement(prevSnaps.current[j.job_id], snap, models[i].flow);
        if (m !== null) msg = jobList.length > 1 ? `작업 ${i + 1} ${m}` : m;
      }
    });
    prevSnaps.current = next;
    if (msg !== null) setAnnounce(msg);
  }, [jobList, models]);

  if (req.isLoading || jobs.isLoading) return <LoadingView requestId={requestId} />;

  if (req.isError && !req.data) {
    return (
      <section className="max-w-6xl space-y-5">
        <div>
          <h1 className="text-2xl font-bold">요청 상세</h1>
          <p className="mt-1 font-mono text-xs text-ink/70 [overflow-wrap:anywhere]">{requestId}</p>
        </div>
        <div role="alert" className={ERROR_BOX}>
          <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <p className="min-w-0 flex-1">{(req.error as Error).message}</p>
          <RetryButton onClick={() => { void req.refetch(); void jobs.refetch(); }} />
        </div>
      </section>
    );
  }

  const data = req.data;
  if (!data) return null;
  const abs = absSummary(data.operation, data.payload, roots);
  const target = pathSummary(data.operation, data.payload);
  const live = !REQUEST_TERMINAL_STATES.has(data.state) || (jobList ?? []).some((j) => !isTerminal(j.state));
  // 폴링 중 한 번 실패했다고 화면 전체를 오류로 바꾸지 않는다 -- 보던 화면은 남기고 "언제 기준인지" 만 말한다.
  const refetchErr = req.isRefetchError || jobs.isRefetchError;
  const staleAt = Math.min(...[req.dataUpdatedAt, jobs.dataUpdatedAt].filter((t) => t > 0));
  const outcome = deriveOutcome(data, jobList, models);
  const batchChild = Boolean(data.batch_id);

  return (
    <StageNavProvider>
      <section className="max-w-6xl space-y-5">
        <div className="flex flex-wrap items-start gap-x-4 gap-y-1">
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-bold">{`${data.operation} 요청`}</h1>
            <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs text-ink/70">
              <span className="select-all font-mono [overflow-wrap:anywhere]">{data.request_id}</span>
              <span aria-hidden>·</span><span>{`요청자 ${data.requester_id}`}</span>
              <span aria-hidden>·</span><span>{`제출 ${kstStamp(data.created_at)}`}</span>
              <span aria-hidden>·</span><span>{`우선순위 ${data.priority}`}</span>
              {data.batch_id && (<>
                <span aria-hidden>·</span>
                <Link className={`text-accent underline ${FOCUS_RING}`} to={`/admin/batches/${data.batch_id}`}>
                  {`배치 ${data.batch_id.slice(0, 8)}`}
                </Link>
              </>)}
            </div>
          </div>
          {live && (
            <span className="inline-flex items-center gap-1.5 pt-1 text-xs text-ink/70">
              <span className="relative inline-flex h-2 w-2" aria-hidden>
                <span className="absolute inline-flex h-full w-full rounded-full bg-busy/60 motion-safe:animate-ping" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-busy" />
              </span>
              자동 갱신 중
            </span>
          )}
        </div>
        {refetchErr && (
          <div role="status" className="flex flex-wrap items-center gap-2 rounded-lg border border-attn/30 bg-attnbg/60 px-3 py-2 text-xs text-ink">
            <TriangleAlert className="h-4 w-4 shrink-0 text-attn" aria-hidden />
            <p className="min-w-0 flex-1 break-keep">
              {`자동 갱신에 실패했습니다 — ${Number.isFinite(staleAt) ? kstClock(new Date(staleAt).toISOString()) : "—"} 기준 화면입니다.`}
            </p>
            <RetryButton onClick={() => { void req.refetch(); void jobs.refetch(); }} />
          </div>
        )}
        <OutcomeCard req={data} jobs={jobList} models={models} abs={abs} target={target} cancelRequest={cancelRequest} />
        <section aria-labelledby="jobs-h" className="space-y-3">
          <h2 id="jobs-h" className="flex items-baseline gap-2 text-sm font-semibold">
            데이터 작업
            {jobList !== null && jobList.length > 0 && (
              <span className="text-xs font-normal text-ink/70 tabular-nums">{`${jobList.length}개`}</span>
            )}
          </h2>
          {jobList === null ? (
            // 잡 조회 실패를 삼키면 "잡이 0개" 와 구별되지 않는다 -- 실패를 이 자리에서 말한다.
            <div role="alert" className={ERROR_BOX}>
              <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <p className="min-w-0 flex-1">{(jobs.error as Error).message}</p>
              <RetryButton onClick={() => { void jobs.refetch(); }} />
            </div>
          ) : jobList.length === 0 ? (
            <div className="flex items-center justify-center gap-2 rounded-card border border-dashed border-line px-4 py-6 text-center text-sm text-ink/70">
              {!REQUEST_TERMINAL_STATES.has(data.state) ? (<>
                <LoaderCircle className="h-4 w-4 shrink-0 motion-safe:animate-spin" aria-hidden />
                <span className="break-keep">작업을 계획하는 중입니다 — 실행 노드가 정해지면 단계가 여기에 나타납니다.</span>
              </>) : (
                <span className="break-keep">만들어진 작업이 없습니다 — 계획 단계에서 끝나 로그·출력이 없습니다.</span>
              )}
            </div>
          ) : (
            jobList.map((j, i) => (
              <JobCard key={j.job_id} job={j} model={models[i]} backends={backends} cancel={cancel}
                       events={data.events} jobCount={jobList.length} refIso={data.created_at}
                       batchChild={batchChild} confirmOnGate={outcome.confirmOwner === "gate"} />
            ))
          )}
        </section>
        <ActivitySection req={data} />
        <p className="sr-only" role="status" aria-live="polite">{announce}</p>
      </section>
    </StageNavProvider>
  );
}
