import { useId, useState, type ReactNode } from "react";
import { ChevronDown, History } from "lucide-react";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { isTerminal } from "../../lib/jobState";
import { ApiError, reasonText } from "../../lib/api";
import { toolSummary } from "../../lib/jobTool";
import type { DataJob } from "../../lib/types";
import type { useCancelJob } from "./useJobs";
import { Timeline } from "./Timeline";
import { JobStages } from "./JobStages";
import { normTransitions, type JobStagesModel } from "./stageModel";
import { FOCUS_RING } from "./ui";

// 실행 도구 라벨(잡 카드 헤더). **배지가 아니라 중립 텍스트**인 이유: 이 카드의
// 배지 자리는 StatusPill 하나뿐이고 그 색은 상태 판정(ok/bad/busy) 계약이다 —
// 도구에 배지를 하나 더 달면 색이 없는 판정을 만들고(어떤 도구가 "좋은" 도구인가?)
// 두 배지가 서로 상태처럼 읽힌다. 식별자 옆의 작은 중립 라벨로 붙인다 -- 글자색은 ink/70(흰 카드 4.94:1, AA).
// 예전 관례였던 text-muted(#888, 3.54:1)는 장식 전용이다(스펙 §9, 리뷰 3차: 도구 이름은 의미 있는 글자다).
// 모름(계획 전)이면 아무것도 그리지 않는다 — "—" 도 거짓 표시다(null≠0).
export function ToolLabel({ job }: { job: DataJob }) {
  const text = toolSummary(job);
  if (text === null) return null;
  return <span className="text-xs text-ink/70">{text}</span>;
}

// 잡 자신의 상태 전이 목록은 기본으로 접는다 -- 단계 구획이 같은 사실(언제 어느 단계였나)을 이미 말하고, 펼친
// 원문은 진단용이다. 제목(heading)이 아니라 버튼인 이유: 페이지의 「전이 이력」 heading 은 요청 Timeline 하나뿐
// 이어야 한다(e2e 04 getByRole("heading",{name:"전이 이력"}) 은 부분 일치라 비슷한 heading 이 하나만 있어도 깨진다).
// trailing = 버튼 옆에 붙는 잡별 사실(아티팩트 경로) -- 좁은 폭에선 버튼 아래 줄로 접힌다.
function JobTransitionsDisclosure({ job, trailing = null }: { job: DataJob; trailing?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const n = normTransitions(job.transitions).length;
  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <button type="button" aria-expanded={open} aria-controls={id} disabled={n === 0}
                onClick={() => setOpen((v) => !v)}
                className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2 text-xs text-ink/70 hover:bg-panel hover:text-ink disabled:opacity-50 disabled:hover:bg-transparent ${FOCUS_RING}`}>
          <History className="h-3.5 w-3.5" aria-hidden />
          {`상태 전이 ${n}건`}
          <ChevronDown aria-hidden className={`h-3.5 w-3.5 motion-safe:transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
        {trailing}
      </div>
      {/* 0건이면 Timeline 을 그리지 않는다 -- 「전이 이력이 없습니다」가 요청 Timeline 의 것과 겹치면 안 된다. */}
      <div id={id} hidden={!open}>
        {n > 0 && <div className="mt-2 pl-2"><Timeline transitions={job.transitions} /></div>}
      </div>
    </div>
  );
}

export function JobCard({ job, model, cancel, events, requestTerminal = false, jobCount, refIso, batchChild,
                          confirmOnGate }: {
  job: DataJob; model: JobStagesModel;
  cancel: ReturnType<typeof useCancelJob>;
  events: unknown; requestTerminal?: boolean; jobCount: number; refIso?: string | null; batchChild: boolean;
  confirmOnGate: boolean;
}) {
  // 컨펌 대기는 「취소」 대신 컨펌 흐름이 다음 행동이다(종전 규칙 그대로). TimedOut 이 종단이 되면서(2026-10-08)
  // 시간 초과 잡에 남던 「취소」도 사라진다.
  const canCancel = job.state !== "ConfirmPending" && !isTerminal(job.state);
  return (
    <Card>
      {/* 헤더 = 식별자 · (잡 ≥2일 때만 실행 도구) · 상태 pill · 취소. 실행 도구·신원·보조 그룹은 위 「요청 내용」 카드가
          말한다(잡이 하나면 같은 사실을 두 번 그리지 않는다 -- 여럿이면 잡마다 도구가 다를 수 있어 여기에 남긴다).
          32자 hex job_id 는 좁은 폭에서 어디서든 줄을 바꾼다(e2e L1).
          job_id 는 이 span 한 곳에만 그린다(e2e E6 getByText(job_id, exact) 가 정확히 1개). 잡 Card 와 job_id 사이
          조상에 bg-surface 를 쓰지 않는다(RequestDetail.test 가 closest(".bg-surface") 로 카드를 찾는다). */}
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <h3 className="min-w-0 text-sm font-semibold">
            <span className="sr-only">작업 </span>
            <span className="select-all font-mono [overflow-wrap:anywhere]">{job.job_id}</span>
          </h3>
          {jobCount > 1 && <ToolLabel job={job} />}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <StatusPill state={job.state} />
          {canCancel && (
            <Button variant="ghost" disabled={cancel.isPending}
                    onClick={() => cancel.mutate(job.job_id)}>취소</Button>
          )}
        </div>
      </div>
      {/* useCancelJob 은 요청 단위 훅 하나를 모든 잡 카드가 공유한다 -- variables(마지막 mutate 인자 = jobId)로
          한정하지 않으면 한 잡의 취소 실패가 모든 카드에 도배된다. */}
      {canCancel && cancel.isError && cancel.variables === job.job_id && (
        <p className="text-bad text-sm mt-1">{(cancel.error as ApiError).message}</p>
      )}
      {/* 사유는 실패 단계 행이 싣는다. 실패 지점이 없는데 사유가 있으면(비종단·성공에 실린 경우) 여기서 말한다 --
          받은 사실을 조용히 버리지 않는다. */}
      {!model.failure && job.reason_code && (
        <p className="text-bad text-sm mt-1">{reasonText(job.reason_code)}</p>
      )}
      {!model.known && (
        <p className="mt-2 text-sm text-ink/70 break-keep">{`알 수 없는 상태 ${job.state} — 단계 표시는 대략적입니다.`}</p>
      )}
      <JobStages job={job} events={events} requestTerminal={requestTerminal} jobCount={jobCount} refIso={refIso}
                 batchChild={batchChild} confirmOnGate={confirmOnGate} />
      {/* 아티팩트 경로는 잡별 사실이라 잡 카드 맨 아래 줄에(스킴 없는 경로가 실 값이다 -- stepper). px-2 = 버튼의 안쪽
          여백 -- 좁은 폭에서 버튼 아래로 접혀도 버튼 아이콘과 같은 세로선에서 시작한다. sm 이상은 왼쪽 구분선 -- 같은
          크기·색의 글자가 버튼 옆에 붙으면 버튼의 일부로 읽힌다. */}
      <JobTransitionsDisclosure job={job} trailing={job.artifact_uri
        ? <p className="min-w-0 px-2 text-xs text-ink/70 break-all sm:border-l sm:border-line sm:pl-3">{`아티팩트 ${job.artifact_uri}`}</p>
        : null} />
    </Card>
  );
}
