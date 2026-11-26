import { Fragment } from "react";
import { Info, TriangleAlert } from "lucide-react";
import { HelpTip } from "../../components/ui/HelpTip";
import { useNow } from "../../lib/useNow";
import type { DataJob } from "../../lib/types";
import { useJobProgress, type ProgressPhase } from "./useArtifacts";
import { pctFmt, progressView } from "./progressModel";
import { STEP_COPY, type StepModel } from "./stageModel";

// sync 진행률 한 칸(2026-11-26) -- 요청 상세 잡 카드(JobStages StepRow)의 「진행 중」 실행·미리보기 행 머리 줄 바로 아래에만
// 마운트된다(조건은 progressModel.showsProgress). 행이 끝나면 언마운트되어 폴링이 멈추고 결과 카드(StageResults)가 자리를 잇는다.
// 판정·문구는 전부 progressModel(순수)이고 여기는 그리기만 한다.
//
// - aria-live 는 두지 않는다 -- 5초마다 읽어 주면 소음이다(필요하면 사용자가 다가가 읽는다).
// - 머리는 「진행 · {단계}」 -- 단계 배지·StatusPill 글자(「진행 중」 등, e2e 정확 일치 단언)를 단독으로 쓰지 않는다.
// - 막대는 근거가 있을 때만(percent !== null), 기준 문구를 늘 곁에 둔다(무엇의 % 인지 -- dsync 복사 % 는 바이트·배치 안이다).
// - 색은 기존 토큰(busy·attn·ink/70)과 번들 아이콘만(airgap).
export function JobProgress({ job, step, live = true }: { job: DataJob; step: StepModel; live?: boolean }) {
  const phase = step.id as ProgressPhase;
  const q = useJobProgress(job.job_id, phase, live);
  const now = useNow(5000, live);
  const v = progressView(q.data, q.error, job, step, now, q.dataUpdatedAt);
  const stepName = STEP_COPY[step.id];
  return (
    <div role="group" aria-label={`${stepName} 진행`}
         className="mt-2 min-w-0 rounded-md border border-busy/20 bg-busybg/40 px-2.5 py-2 text-xs">
      <p className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="font-medium text-ink break-keep">{v.title !== null ? `진행 · ${v.title}` : "진행"}</span>
        {v.fresh !== null && <span className="text-ink/70 tabular-nums">{v.fresh}</span>}
      </p>
      {v.percent !== null && (
        <div className="mt-1.5 flex min-w-0 items-center gap-2">
          <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={v.percent}
               aria-valuetext={pctFmt(v.percent)} aria-label={`${stepName} 진행률(${v.basis ?? ""})`}
               className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-line">
            <div className="h-full rounded-full bg-busy" style={{ width: `${v.percent}%` }} />
          </div>
          <span className="shrink-0 font-medium tabular-nums text-ink">{pctFmt(v.percent)}</span>
        </div>
      )}
      {v.basis !== null && (
        <p className="mt-0.5 flex items-center gap-0.5 text-ink/70">
          <span className="break-keep">{v.basis}</span>
          {v.basisHelp !== null && (
            <HelpTip label="진행률 기준 설명" head={v.basis} help={v.basisHelp} className="-my-1" />
          )}
        </p>
      )}
      {v.facts.length > 0 && (
        // 조각마다 inline-block -- 줄이 모자라면 조각 단위로 넘긴다(「평균 510.4 / 파일/s」처럼 한 조각이 쪼개지지 않게). 한 줄보다
        // 긴 조각만 그 안에서 꺾인다(max-w-full).
        <p className="mt-1 text-ink/70 tabular-nums break-keep [overflow-wrap:anywhere]">
          {v.facts.map((f, i) => (
            <Fragment key={i}>{i > 0 ? " · " : null}<span className="inline-block max-w-full">{f}</span></Fragment>
          ))}
        </p>
      )}
      {v.note !== null && (
        <p className={`mt-1 flex items-start gap-1.5 ${v.note.tone === "attn" ? "text-attn" : "text-ink/70"}`}>
          {v.note.tone === "attn"
            ? <TriangleAlert aria-hidden className="mt-px h-3.5 w-3.5 shrink-0" />
            : <Info aria-hidden className="mt-px h-3.5 w-3.5 shrink-0" />}
          <span className="sr-only">{v.note.tone === "attn" ? "주의" : "안내"}</span>
          <span className="min-w-0 break-keep [overflow-wrap:anywhere]">{v.note.text}</span>
        </p>
      )}
    </div>
  );
}
