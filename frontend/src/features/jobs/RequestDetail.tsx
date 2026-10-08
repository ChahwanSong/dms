import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CircleAlert, FileX, LoaderCircle, TriangleAlert } from "lucide-react";
import { requestGone, useRequest, useRequestJobs, useCancelJob, useCancelRequest } from "./useJobs";
import { reasonText } from "../../lib/api";
import { Card } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { REQUEST_TERMINAL_STATES, isTerminal } from "../../lib/jobState";
import { kstStamp } from "../../lib/datetime";
import { useStorageBackends, useStorageRoots } from "../storages/useUserStorages";
import type { DataJob } from "../../lib/types";
import { StageNavProvider } from "./stageNav";
import { OutcomeCard } from "./OutcomeCard";
import { deriveRequestSpec } from "./requestSpec";
import { RequestSpecCard, RootBadge } from "./RequestSpecCard";
import { JobCard } from "./JobCard";
import { ActivitySection } from "./RequestActivity";
import { deriveJobStages, stageAnnouncement, stageSnapshot, type StageSnapshot } from "./stageModel";
import { deriveOutcome } from "./requestOutcome";
import { kstClock } from "./format";
import { FOCUS_RING, LINK_BTN, SMALL_BTN } from "./ui";

// 요청 상세(/jobs/:requestId) -- 2026-10-08 재설계(사용자 요청: preflight(preview)와 execution 의 출력·로그를
// 분리해서 보이게 + 결과 화면 개선). 위에서 아래로: 머리말(+ root 배지) → (재조회 실패 띠) → 결과 배너 → 요청 내용
// (대상·옵션 / 실행 권한·자원 -- 같은 날 "옵션·root 실행 여부가 전부 빠져 있다" 요청) → 데이터 작업(잡 카드:
// ① 사전 점검·미리보기 / 작업 컨펌 / ② 실행) → 활동(전이 이력·진단 이벤트). 이 파일은 조립만 한다 -- 판정은
// stageModel·requestOutcome·requestSpec(순수), 그리기는 각 컴포넌트 파일.
//
// 폴링(useJobs): 요청 3초(비종단 동안), 잡 2초(요청 비종단 또는 비종단 잡이 있을 때 -- 잡 0개여도 돈다). 종단이면
// 전부 멈춘다(e2e E6: 종단 뒤 /jobs 요청 0건). 잡 키를 무효화하는 새 코드는 넣지 않는다.

const ERROR_BOX = "flex flex-wrap items-start gap-2 rounded-card border border-bad/30 bg-badbg px-4 py-3 text-sm text-bad";

function LoadingView({ requestId }: { requestId: string }) {
  // 실제 높이와 비슷한 자리표시(배너 + KPI 4칸 + 요청 내용 + 잡 카드)로 데이터 도착 때 화면이 밀리지 않게(CLS).
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
        <Skeleton className="h-4 w-24" />
        {/* 카드와 같은 중단점(xl) -- 1024–1279 에서 골격만 2열이면 도착 때 높이가 뛴다. */}
        <div className="mt-3 grid gap-3 xl:grid-cols-2"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
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

// 「없는 요청」(404 -- 삭제됐거나 볼 수 없는 요청, 2026-10-08 작업 삭제). 오류가 아니라 **상태**라 붉은 경보 대신
// 중립 상자이고, 「다시 시도」를 두지 않는다(지워진 요청은 다시 읽어도 404 다 -- 그 버튼이 첫 404 에도 나오던 결함).
// 문구는 사유 코드 request_not_found 의 것 하나다 -- 남의 요청 404 와 같은 문구라 존재 오라클이 없다.
function NotFoundView({ requestId }: { requestId: string }) {
  return (
    <section className="max-w-6xl space-y-5">
      <div>
        <h1 className="text-2xl font-bold">요청 상세</h1>
        <p className="mt-1 font-mono text-xs text-ink/70 [overflow-wrap:anywhere]">{requestId}</p>
      </div>
      <div role="status" className="flex flex-wrap items-center gap-3 rounded-card border border-line bg-surface px-4 py-4 text-sm text-ink">
        <FileX className="h-5 w-5 shrink-0 text-ink/70" aria-hidden />
        <p className="min-w-0 flex-1 break-keep">{reasonText("request_not_found")}</p>
        <Link className={LINK_BTN} to="/jobs">전체 작업으로</Link>
      </div>
    </section>
  );
}

export function RequestDetail() {
  const { requestId = "" } = useParams();
  const req = useRequest(requestId);
  // 404 = 삭제됐거나 볼 수 없는 요청. **데이터 유무와 관계없이**(캐시된 화면을 보다가 재조회가 404 여도) 「없는
  // 요청」 화면이다 -- 낡은 화면에 「자동 갱신 실패」 띠만 뜨면 지워진 요청을 살아 있는 것처럼 보인다. 그동안 잡
  // 조회는 끈다(지워진 요청의 잡은 404 뿐이고, 비종단 잡 캐시가 있으면 2초 폴링이 계속 404 를 두드린다). 요청 쿼리
  // 쪽 폴링·포커스 재조회도 404 뒤에 멈춘다(useRequest).
  const notFound = requestGone(req.error);
  const reqTerminal = req.data ? REQUEST_TERMINAL_STATES.has(req.data.state) : undefined;
  const jobs = useRequestJobs(requestId, !notFound, { requestTerminal: reqTerminal });
  const cancel = useCancelJob(requestId);
  const cancelRequest = useCancelRequest(requestId);
  // 스토리지 뿌리 맵. managed_root 는 역할 무관(2026-09-29~). 비관리자 응답엔 관리자 전용 스토리지가, 모두의 응답엔
  // 비활성 스토리지가 빠져 그 경로는 모름(절대경로 행 생략)
  const roots = useStorageRoots();
  // 이름 → backend_type(같은 쿼리 -- 요청 추가 없음). 「요청 내용」의 보조 그룹 주의문용.
  const backends = useStorageBackends();

  // 잡 목록 방어적 정규화(배열이 아니면 모름 취급) + 단계 모델. 잡 조회 실패면 null(모름) -- 0개와 다르다.
  const jobList: DataJob[] | null = jobs.isError && !jobs.data ? null
    : Array.isArray(jobs.data) ? jobs.data : jobs.data === undefined ? null : [];
  // 요청 이벤트도 모델에 넘긴다(제출 보류 판정) -- 잡 카드(JobStages)가 같은 입력으로 같은 모델을 만든다.
  // requestTerminal 은 이벤트와 **같은 응답**의 요청 상태다(관문 이벤트 부재를 믿는 조건 -- stageModel ALSO_POD_MARKER).
  const events = req.data?.events;
  const evTerminal = reqTerminal === true;
  const models = useMemo(() => (jobList ?? []).map((j) => deriveJobStages(j, events, { requestTerminal: evTerminal })),
    [jobList, events, evTerminal]);
  // 잡 조회 실패를 한 번이라도 봤으면 그 뒤의 재조회(「다시 시도」)는 첫 로딩이 아니다 -- TanStack v5 는 데이터 없는
  // 쿼리의 재조회마다 status 를 pending 으로 되돌려 isLoading 이 다시 참이 되는데, 그때 화면 전체를 골격으로 바꾸면
  // 배너·오류 상자·「다시 시도」(포커스)가 통째로 사라졌다 나타난다(리뷰 V1). 재조회 중엔 error 가 null 이 되므로
  // 마지막 오류 문구를 기억해 상자에 남긴다(훅 순서가 바뀌지 않게 조기 return 앞에서).
  const lastJobsErr = useRef<string | null>(null);
  if (jobs.error) lastJobsErr.current = (jobs.error as Error).message;
  const jobsErrSeen = jobs.errorUpdateCount > 0;
  // 상자에 보일 오류 문구. null 이면 이 인스턴스는 실패 문구를 모른다(아래 잡 구획이 경보 대신 골격을 그린다).
  const jobsErrMsg = jobs.error ? (jobs.error as Error).message : lastJobsErr.current;

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

  if (notFound) return <NotFoundView requestId={requestId} />;
  if (req.isLoading || (jobs.isLoading && !jobsErrSeen)) return <LoadingView requestId={requestId} />;

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
  // 「요청 내용」 카드와 머리말 root 배지가 읽는 한 모델(훅이 아니다 -- 조기 return 뒤에 둬도 된다).
  const spec = deriveRequestSpec(data, jobList, roots, backends);
  const live = !REQUEST_TERMINAL_STATES.has(data.state) || (jobList ?? []).some((j) => !isTerminal(j.state));
  // 폴링 중 한 번 실패했다고 화면 전체를 오류로 바꾸지 않는다 -- 보던 화면은 남기고 "언제 기준인지" 만 말한다.
  const refetchErr = req.isRefetchError || jobs.isRefetchError;
  const staleAt = Math.min(...[req.dataUpdatedAt, jobs.dataUpdatedAt].filter((t) => t > 0));
  // 잡을 모르는데 실패 문구도 없으면 아래 잡 구획은 중립 골격(재조회 중)이다 -- 배너도 같은 이야기를 하게 넘긴다.
  const jobsLoading = jobList === null && jobsErrMsg === null;
  const outcome = deriveOutcome(data, jobList, models, { jobsLoading });
  const batchChild = Boolean(data.batch_id);

  return (
    <StageNavProvider>
      <section className="max-w-6xl space-y-5">
        <div className="flex flex-wrap items-start gap-x-4 gap-y-1">
          <div className="min-w-0 flex-1">
            {/* 배지는 h1 **밖**(h1 이름 정확 일치 계약 「sync 요청」). root 일 때만 -- 비 root 는 정상 경우라 요소를 늘리지 않는다
                (비 root 는 카드의 「실행 권한」이 「root 아님」으로 말한다). 본문의 첫 줄이라 카드(배너·KPI 아래)보다 먼저
                보인다. 단 375 에서는 관리자 사이드바 블록(약 1,060px) 다음이라 첫 화면 안은 아니다 -- 모바일 사이드바 접기는
                BACKLOG(셸 범위). */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <h1 className="text-2xl font-bold">{`${data.operation} 요청`}</h1>
              {spec.badge !== null && <RootBadge kind={spec.badge} />}
            </div>
            <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs text-ink/70">
              <span className="select-all font-mono [overflow-wrap:anywhere]">{data.request_id}</span>
              <span aria-hidden>·</span><span>{`요청자 ${data.requester_id}`}</span>
              <span aria-hidden>·</span><span>{`제출 ${kstStamp(data.created_at)}`}</span>
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
        <OutcomeCard req={data} jobs={jobList} models={models} cancelRequest={cancelRequest} jobsLoading={jobsLoading} />
        <RequestSpecCard spec={spec} />
        <section aria-labelledby="jobs-h" className="space-y-3">
          <h2 id="jobs-h" className="flex items-baseline gap-2 text-sm font-semibold">
            데이터 작업
            {/* 개수는 여럿일 때만 -- 「1개」는 정보가 없다(planner 는 요청당 잡을 하나 만든다). */}
            {jobList !== null && jobList.length > 1 && (
              <span className="text-xs font-normal text-ink/70 tabular-nums">{`${jobList.length}개`}</span>
            )}
          </h2>
          {jobList === null && jobsErrMsg !== null ? (
            // 잡 조회 실패를 삼키면 "잡이 0개" 와 구별되지 않는다 -- 실패를 이 자리에서 말한다.
            <div role="alert" aria-busy={jobs.isFetching} className={ERROR_BOX}>
              <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <p className="min-w-0 flex-1">{jobsErrMsg}</p>
              <RetryButton onClick={() => { void jobs.refetch(); }} />
            </div>
          ) : jobList === null ? (
            // 오류를 본 적은 있지만 이 화면 인스턴스는 그 문구를 모른다(캐시에 실패가 남은 채 다시 들어와 재조회 중 --
            // 재조회가 error 를 비운다). 오류가 아니라 로딩이다: 붉은 경보(role=alert)로 "불러오는 중" 을 알리지 않고 잡
            // 카드 자리에 중립 골격을 둔다(리뷰 N2). 재조회가 또 실패하면 위 상자가 그 문구로 뜬다.
            <div aria-busy="true">
              <p className="sr-only" role="status">작업 정보를 불러오는 중…</p>
              <Card>
                <Skeleton className="h-5 w-1/3" />
                <Skeleton className="mt-4 h-24" />
                <Skeleton className="mt-3 h-24" />
              </Card>
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
              <JobCard key={j.job_id} job={j} model={models[i]} cancel={cancel}
                       events={data.events} requestTerminal={evTerminal} jobCount={jobList.length} refIso={data.created_at}
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
