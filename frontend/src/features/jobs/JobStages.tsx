import { Fragment, useEffect, useMemo, useRef, type ReactNode } from "react";
import {
  Ban, CircleAlert, CircleCheck, CircleDashed, CircleHelp, CircleMinus, CirclePause, CircleX, Hourglass,
  Info, LoaderCircle, OctagonAlert, TimerOff, TriangleAlert, type LucideIcon,
} from "lucide-react";
import { reasonText } from "../../lib/api";
import { kstStamp } from "../../lib/datetime";
import { useNow } from "../../lib/useNow";
import { Skeleton } from "../../components/ui/Skeleton";
import type { DataJob, DiagEvent } from "../../lib/types";
import { useArtifacts } from "./useArtifacts";
import { useStageNav, type NavSlot } from "./stageNav";
import { ConfirmDialog } from "./ConfirmDialog";
import { OutputViewer, outputIcon } from "./OutputViewer";
import { ExecutionResult, PreviewResult } from "./StageResults";
import {
  FAILED_LIKE, STATUS_LABEL, STEP_COPY, annotationsFor, deriveJobStages, isEstimated, normTransitions, outputsByStep,
  stageCode, stageTitle, stepDuration, type JobStagesModel, type OutputItem, type StageModel, type StepModel,
  type StepStatus,
} from "./stageModel";
import { humanBytes, kstClock, msText, spanText } from "./format";
import { FOCUS_RING, SMALL_BTN } from "./ui";

// 잡 카드 안의 두 단계 구획(2026-10-08 재설계, 사용자 요청: "preflight(preview)와 execution 으로 분리해서
// 보여지게"). ① 사전 점검·미리보기 → (작업 컨펌 관문) → ② 실행 을 **항상 둘 다 펼쳐** 보이고, 출력(로그·파일)은
// 자기 단계 행 아래 칩으로만 나온다 -- 옛 화면은 한 줄에 칩 15개가 섞여 어느 파일이 미리보기 것이고 어느 것이
// 실행 것인지 이름(phase/) 접두로만 가려야 했다. 판정은 전부 stageModel(순수)이 하고 여기는 그리기만 한다.

const STATUS_ICON: Record<StepStatus, { Icon: LucideIcon; tone: string; spin?: boolean }> = {
  waiting: { Icon: CircleDashed, tone: "text-ink/60" },
  running: { Icon: LoaderCircle, tone: "text-busy", spin: true },
  awaiting: { Icon: CirclePause, tone: "text-attn" },
  done: { Icon: CircleCheck, tone: "text-ok" },
  failed: { Icon: CircleX, tone: "text-bad" },
  rejected: { Icon: CircleX, tone: "text-bad" },
  timed_out: { Icon: TimerOff, tone: "text-bad" },
  // 취소는 의도한 중지라 적색을 피한다(잡 pill 이 이미 적색이다).
  cancelled: { Icon: Ban, tone: "text-ink/70" },
  expired: { Icon: Hourglass, tone: "text-attn" },
  skipped: { Icon: CircleMinus, tone: "text-ink/60" },
  unknown: { Icon: CircleHelp, tone: "text-ink/60" },
};
const BADGE_BG: Record<StepStatus, string> = {
  waiting: "bg-panel", running: "bg-busybg", awaiting: "bg-attnbg", done: "bg-okbg",
  failed: "bg-badbg", rejected: "bg-badbg", timed_out: "bg-badbg", cancelled: "bg-panel",
  expired: "bg-attnbg", skipped: "bg-panel", unknown: "bg-panel",
};
// 배지 글자색. 흐린 상태(대기·실행 안 됨·모름)의 아이콘 톤 ink/60 은 bg-panel 위 12px 글자로 3.6:1 이라 AA(4.5:1)에
// 못 미친다(리뷰 V5) -- 글자는 ink/70(약 4.75:1), 아이콘은 비텍스트 하한(3:1)을 넘는 ink/60 그대로 두어 흐린 느낌을 지킨다.
const LABEL_TONE: Partial<Record<StepStatus, string>> = {
  waiting: "text-ink/70", skipped: "text-ink/70", unknown: "text-ink/70",
};

function StatusIcon({ status, className = "h-4 w-4" }: { status: StepStatus; className?: string }) {
  const { Icon, tone, spin } = STATUS_ICON[status];
  return <Icon aria-hidden className={`${className} shrink-0 ${tone} ${spin ? "motion-safe:animate-spin" : ""}`} />;
}

export function StageBadge({ status }: { status: StepStatus }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${LABEL_TONE[status] ?? STATUS_ICON[status].tone} ${BADGE_BG[status]}`}>
      <StatusIcon status={status} className="h-3.5 w-3.5" />{STATUS_LABEL[status]}
    </span>
  );
}

// 진행 중 경과(「12초째」). 1초 틱은 이 잎 컴포넌트만 다시 그린다. 텍스트는 접두·접미와 한 노드로 -- 「1분 30초」
// 같은 정확 일치 단언(수행시간·제출 대기)과 겹치지 않게 늘 꾸밈을 붙인다.
// 모르면(파싱 불가·브라우저 시계가 서버보다 뒤라 음수) 경과를 지어내지 않는다 -- 접두만 남기거나 fallback("—").
export function Elapsed({ from, prefix = "", suffix = "째", fallback }: {
  from: string; prefix?: string; suffix?: string; fallback?: string;
}) {
  const now = useNow(1000, true);
  const ms = Date.parse(from);
  const d = Number.isNaN(ms) ? null : msText(now - ms);
  if (d === null) return prefix ? <>{prefix.replace(/ · $/, "")}</> : fallback !== undefined ? <>{fallback}</> : null;
  return <>{`${prefix}${d}${suffix}`}</>;
}

function Time({ iso, refIso }: { iso: string; refIso?: string | null }) {
  return <time dateTime={iso} title={kstStamp(iso)}>{kstClock(iso, refIso)}</time>;
}

// 조각을 " · " 로 잇는다. 시각은 <time>(전체 KST 시각은 title), 소요는 늘 「소요 …」처럼 접두를 붙인 한 조각이다 --
// 「1분 30초」 정확 일치(수행시간·제출 대기)와 겹치지 않게(스펙 C6). 모르는 조각은 빠진다(지어내지 않는다).
function joinParts(parts: (ReactNode | null)[]): ReactNode {
  const kept = parts.filter((x) => x !== null && x !== "");
  if (!kept.length) return null;
  return kept.map((x, i) => <Fragment key={i}>{i > 0 ? " · " : null}{x}</Fragment>);
}

// 「위치 추정」 꼬리표의 설명(근거마다 왜 추정인지가 다르다).
const ESTIMATE_WHY: Partial<Record<string, string>> = {
  gate_ambiguous: "실행 노드 점검은 이 단계 파드가 스케줄을 기다리는 동안에도, 다음 단계를 제출하기 직전에도 일어나 기록만으로는 어느 쪽인지 알 수 없습니다",
};
const ESTIMATE_DEFAULT = "전이 기록이 없어 마지막으로 시작된 단계로 추정했습니다";

// 단계 행 메타(시각·소요·상태 단어). done 이 아닌 상태는 글자로도 상태를 말한다(색만으로 구분하지 않는다).
// estimate = 「위치 추정」 꼬리표의 설명(추정이 아니면 null). 추정 행에는 「소요」를 붙이지 않는다 -- 그 단계가 돈
// 시간인지 알 수 없다.
function StepMeta({ step, refIso, hasRef, estimate }: {
  step: StepModel; refIso?: string | null; hasRef: boolean; estimate: string | null;
}) {
  const time = (iso: string | null) => (iso ? <Time iso={iso} refIso={refIso} /> : null);
  const dur = stepDuration(step);
  // Volcano 스케줄 대기(execution 만). null(모름)이면 생략, 0 은 "0초"(정상값). 「제출 대기」(요청 픽업 지연)와
  // 다른 값이라 이름도 다르다(스펙 C6).
  const sched = step.schedWaitSec !== null ? msText(step.schedWaitSec * 1000) : null;
  const schedPart = sched !== null ? `스케줄 대기 ${sched}` : null;
  const cls = "text-xs text-ink/70 tabular-nums";
  let text: ReactNode = null;
  if (step.held) {
    // 제출 보류(D2) -- 끝난 앞 단계의 경과를 세지 않고, 무엇을 왜 기다리는지 말한다. 횟수를 모르면 생략(지어내지 않는다).
    const n = step.held.attempt !== null && step.held.max !== null ? ` ${step.held.attempt}/${step.held.max}` : "";
    text = `제출 보류 — LDAP 재확인 불가${n}`;
  } else switch (step.status) {
    case "done":
      text = joinParts([time(step.start), dur !== null ? `소요 ${dur}` : null, schedPart]);
      break;
    case "running":
      // 파드가 아직 없으면(Executing 인데 재점검 ref 전) "시작 대기" -- 경과를 세면 파드가 도는 것처럼 읽힌다.
      text = !hasRef ? "시작 대기"
        : step.start ? <Elapsed from={step.start} prefix={`${kstClock(step.start, refIso)} 시작 · `}
                                suffix={`째${schedPart ? ` · ${schedPart}` : ""}`} />
        : "진행 중";
      break;
    case "waiting":
      // 스케줄 대기(실행 vcjob 제출 뒤 RUNNING 관측 전): 제출 시각부터의 대기 경과 -- 「시작 · N째」(실행 중)와 다른 말이다.
      text = !step.queued ? "대기"
        : step.start ? <Elapsed from={step.start} prefix={`${kstClock(step.start, refIso)} 제출 · 스케줄 대기 중 · `} />
        : "스케줄 대기 중";
      break;
    case "skipped":
      text = "실행 안 됨";
      break;
    case "unknown":
      text = "모름";
      break;
    default: {
      const label = STATUS_LABEL[step.status];
      const head = step.submitFailed ? "시작 못 함(제출 실패)"
        : step.queued ? `스케줄 대기 중 ${label}`
        : step.notStarted ? `시작 전 ${label}` : label;
      // 제출 실패·스케줄 대기 중 종단·추정 행은 「소요」가 실행 시간이 아니다(파드가 안 돌았거나 어느 단계인지 모른다).
      const showDur = dur !== null && !step.submitFailed && !step.queued && estimate === null;
      text = joinParts([head, time(step.end), showDur ? `소요 ${dur}` : null]);
    }
  }
  return (
    <>
      {text !== null && <span className={cls}>{text}</span>}
      {estimate !== null && (
        <span className="rounded border border-dashed border-line px-1.5 py-0.5 text-[11px] text-ink/70"
              title={estimate}>위치 추정</span>
      )}
    </>
  );
}

const SEVERITY: Record<string, { Icon: LucideIcon; tone: string; word: string }> = {
  error: { Icon: OctagonAlert, tone: "text-bad", word: "오류" },
  warning: { Icon: TriangleAlert, tone: "text-ink/70", word: "경고" },
  info: { Icon: Info, tone: "text-muted", word: "정보" },
};
const NOTE_CAP = 3;
// phase 범위 진단 이벤트를 그 단계 행의 주석으로(payload 는 그리지 않는다 -- 원본은 「진단 이벤트」 카드에 남는다).
function StepAnnotations({ events, refIso }: { events: DiagEvent[]; refIso?: string | null }) {
  if (!events.length) return null;
  const shown = events.slice(0, NOTE_CAP);
  return (
    <ul className="mt-1 space-y-0.5 text-xs text-ink/70">
      {shown.map((e, i) => {
        const sev = SEVERITY[e.severity] ?? SEVERITY.info;
        const at = typeof e.at === "string" ? ` · ${kstClock(e.at, refIso)}` : "";
        return (
          <li key={`${e.id}-${i}`} className="flex items-start gap-1.5">
            <sev.Icon aria-hidden className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${sev.tone}`} />
            <span className="sr-only">{sev.word}</span>
            <span className="min-w-0 [overflow-wrap:anywhere]">{`${e.message ?? e.event_type}${at}`}</span>
          </li>
        );
      })}
      {events.length > NOTE_CAP && (
        <li className="pl-5">{`외 ${events.length - NOTE_CAP}건 — 아래 「진단 이벤트」`}</li>
      )}
    </ul>
  );
}

function OutputChips({ label, items, slot, selectedKey, live, onSelect, chipRef, viewerId }: {
  label: string; items: OutputItem[]; slot: NavSlot; selectedKey: string | null; live: boolean;
  onSelect: (slot: NavSlot, key: string) => void;
  chipRef: (key: string, el: HTMLButtonElement | null) => void; viewerId: string;
}) {
  return (
    <div role="group" aria-label={`${label} 출력`} className="mt-2 flex flex-wrap gap-1.5">
      {items.map((item) => {
        const sel = selectedKey === item.key;
        const Icon = outputIcon(item);
        return (
          <button key={item.key} type="button" ref={(el) => chipRef(item.key, el)}
                  aria-pressed={sel} aria-controls={sel ? viewerId : undefined}
                  // 접근성 이름은 옛 JobViewer 탭 이름 그대로(「execution/stdout.log」·「preflight 로그」) -- 보이는
                  // 글자(「stdout.log」·「로그」)가 이름 안에 들어 있어 WCAG 2.5.3 을 지킨다.
                  aria-label={item.kind === "log" ? `${item.phase} 로그` : `${item.phase}/${item.name}`}
                  // 눌린 칩을 다시 눌러도 아무 일 없다(닫기는 뷰어의 ✕) -- 토글로 두면 실수 한 번에 읽던 로그가 사라진다.
                  onClick={() => { if (!sel) onSelect(slot, item.key); }}
                  className={`inline-flex h-8 max-w-full items-center gap-1.5 rounded-full border border-line px-2.5 text-xs text-ink hover:bg-panel aria-pressed:border-accent aria-pressed:bg-infobg aria-pressed:text-accent sm:h-7 ${FOCUS_RING}`}>
            <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
            <span className="min-w-0 truncate">{item.kind === "log" ? "로그" : item.name}</span>
            {item.kind === "artifact" && item.size !== null && (
              <span className="shrink-0 tabular-nums text-ink/60">{humanBytes(item.size)}</span>
            )}
            {live && item.kind === "log" && (
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-busy motion-safe:animate-pulse" aria-hidden />
            )}
          </button>
        );
      })}
    </div>
  );
}

interface RowCtx {
  job: DataJob; model: JobStagesModel; refIso?: string | null;
  selectedKey: string | null; slot: NavSlot; viewerId: string;
  entrySize: (item: OutputItem) => number | null;
  listLoading: boolean;
  onSelect: (slot: NavSlot, key: string) => void;
  onClose: (slot: NavSlot) => void;
  chipRef: (key: string, el: HTMLButtonElement | null) => void;
}

function StepRow({ step, items, notes, last, ctx }: {
  step: StepModel; items: OutputItem[]; notes: DiagEvent[]; last: boolean; ctx: RowCtx;
}) {
  const { job, model } = ctx;
  const isFailure = model.failure?.step === step.id;
  const estimate = isFailure && model.failure && isEstimated(model.failure.evidence)
    ? ESTIMATE_WHY[model.failure.evidence] ?? ESTIMATE_DEFAULT : null;
  const live = !model.terminal && step.status === "running";
  const selected = items.find((i) => i.key === ctx.selectedKey) ?? null;
  const ended = step.status === "done" || FAILED_LIKE.has(step.status) || step.status === "cancelled";
  return (
    <li className={`relative grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-3 py-2 ${last ? ""
      : "before:absolute before:bottom-0 before:left-[0.6rem] before:top-7 before:w-px before:bg-line"}`}>
      <span className="pt-0.5"><StatusIcon status={step.status} /></span>
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="text-sm font-medium">{STEP_COPY[step.id]}</span>
          <span className="sr-only">{`: ${STATUS_LABEL[step.status]}`}</span>
          {step.phase && <span className="hidden font-mono text-xs text-muted sm:inline">{step.phase}</span>}
          <StepMeta step={step} refIso={ctx.refIso} hasRef={step.phase !== null && step.phase in model.refs}
                    estimate={estimate} />
        </div>
        {/* 실패 행의 사유 문장. 「사유」 라벨은 배너 dl 에만 둔다(스펙 C5: "사유" 정확 일치는 한 곳). */}
        {isFailure && job.reason_code && (
          <p className="mt-1 text-sm text-bad break-keep">{reasonText(job.reason_code)}</p>
        )}
        <StepAnnotations events={notes} refIso={ctx.refIso} />
        {items.length > 0 ? (
          <OutputChips label={STEP_COPY[step.id]} items={items} slot={ctx.slot} selectedKey={ctx.selectedKey}
                       live={live} onSelect={ctx.onSelect} chipRef={ctx.chipRef} viewerId={ctx.viewerId} />
        ) : ended && !ctx.listLoading ? (
          <p className="mt-1 text-xs text-ink/70">출력 없음</p>
        ) : null}
      </div>
      {selected && (
        <OutputViewer key={selected.key} jobId={job.job_id} item={selected} entrySize={ctx.entrySize(selected)}
                      live={live && selected.kind === "log"} viewerId={ctx.viewerId}
                      onClose={() => ctx.onClose(ctx.slot)} />
      )}
    </li>
  );
}

const STAGE_BORDER = (s: StepStatus) =>
  s === "running" ? "border-busy/40" : FAILED_LIKE.has(s) ? "border-bad/40"
    : s === "skipped" ? "border-dashed border-line" : "border-line";

function StageTime({ stage, refIso }: { stage: StageModel; refIso?: string | null }) {
  const cls = "basis-full text-xs text-ink/70 tabular-nums sm:ml-auto sm:basis-auto";
  if (stage.status === "running" && stage.start) {
    return <span className={cls}><Elapsed from={stage.start} prefix={`${kstClock(stage.start, refIso)} 시작 · `} /></span>;
  }
  const dur = spanText(stage.start, stage.end);
  if (stage.start && stage.end) {
    // 좁은 화면에선 시작 → 끝을 접고 소요만 남긴다(둘째 줄).
    return (
      <span className={cls}>
        <span className="hidden sm:inline">
          <Time iso={stage.start} refIso={refIso} />{" → "}<Time iso={stage.end} refIso={refIso} />{" · "}
        </span>
        {dur !== null ? `소요 ${dur}` : null}
      </span>
    );
  }
  return dur !== null ? <span className={cls}>{`소요 ${dur}`}</span> : null;
}

function StageSection({ n, stage, model, op, children, titleId, refIso }: {
  n: number; stage: StageModel; model: JobStagesModel; op: string; children: ReactNode; titleId: string;
  refIso?: string | null;
}) {
  const title = stageTitle(stage.id, model.flow);
  return (
    <section aria-labelledby={titleId} className={`rounded-lg border ${STAGE_BORDER(stage.status)}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-3 py-2.5 sm:px-4">
        <span aria-hidden className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-infobg text-xs font-bold text-accent">{n}</span>
        <h4 className="text-sm font-semibold"><span id={titleId}>{title}</span></h4>
        <span className="hidden font-mono text-xs text-muted sm:inline">{stageCode(stage.id, model.flow)}</span>
        <StageBadge status={stage.status} />
        <StageTime stage={stage} refIso={refIso} />
      </div>
      <div className="space-y-3 px-3 py-3 sm:px-4">
        {/* 보류·스케줄 대기 행이 있으면 그 행이 무엇을 기다리는지 말한다 -- 「사전 점검이 끝나면…」은 이미 끝난 일이라 뺀다. */}
        {stage.id === "exec" && stage.status === "waiting" && !stage.steps.some((s) => s.held || s.queued) && (
          <p className="text-sm text-ink/70 break-keep">{model.flow === "scan"
            ? "사전 점검이 끝나면 바로 실행됩니다." : "작업 컨펌 후 실행 직전 재점검을 거쳐 실행됩니다."}</p>
        )}
        {stage.id === "exec" && stage.status === "skipped" && (
          <p className="text-sm text-ink/70 break-keep">{model.flow === "scan" ? "실행되지 않았습니다."
            : op === "rm" ? "실행 단계가 시작되지 않아 삭제는 일어나지 않았습니다."
            : "실행 단계가 시작되지 않아 데이터는 변경되지 않았습니다."}</p>
        )}
        {children}
      </div>
    </section>
  );
}

// 컨펌 관문 줄(sync·rm). 사람이 기다린 시간은 여기서만 말한다(기계 소요에 섞지 않는다).
function GateRow({ job, model, refIso, batchChild, showConfirm }: {
  job: DataJob; model: JobStagesModel; refIso?: string | null; batchChild: boolean; showConfirm: boolean;
}) {
  const gate = model.gate!;
  const now = useNow(30_000, gate.status === "awaiting");
  let text: string;
  switch (gate.status) {
    case "done": {
      const wait = spanText(gate.start, gate.end);
      const parts = [gate.actor, gate.end ? kstClock(gate.end, refIso) : null,
        wait !== null ? `미리보기 후 대기 ${wait}` : null].filter(Boolean);
      text = ["작업 컨펌", ...parts].join(" · ");
      break;
    }
    case "awaiting": {
      if (batchChild) { text = "작업 컨펌 · 컨펌 대기 — 배치 상세의 「배치 확인」으로 진행됩니다"; break; }
      const exp = job.preview_expires_at ? Date.parse(job.preview_expires_at) : NaN;
      const left = Number.isNaN(exp) ? null : exp - now;
      text = left === null ? "작업 컨펌 · 컨펌 대기"
        : left <= 0 ? "작업 컨펌 · 컨펌 대기 — 유효기간이 지났습니다"
        : `작업 컨펌 · 컨펌 대기 — 유효기간 ${msText(left)} 남음`;
      break;
    }
    case "waiting": text = "작업 컨펌 · 미리보기가 끝나면 요청됩니다"; break;
    case "cancelled": text = "작업 컨펌 · 컨펌 전에 취소됨"; break;
    case "expired":
      text = job.preview_expires_at
        ? `작업 컨펌 · 만료 — ${kstStamp(job.preview_expires_at)}까지 컨펌되지 않았습니다` : "작업 컨펌 · 만료";
      break;
    case "skipped": text = "작업 컨펌 · 실행 안 됨"; break;
    default: text = `작업 컨펌 · ${STATUS_LABEL[gate.status]}`;
  }
  return (
    <div className="ml-3 flex flex-wrap items-start gap-2 border-l-2 border-dotted border-line py-2 pl-4 text-sm">
      <StatusIcon status={gate.status} className="mt-0.5 h-4 w-4" />
      <span className="sr-only">{`작업 컨펌: ${STATUS_LABEL[gate.status]}`}</span>
      <span className={`min-w-0 flex-1 break-keep ${gate.status === "awaiting" ? "font-medium text-attn" : "text-ink/70"}`}>{text}</span>
      {showConfirm && gate.status === "awaiting" && <ConfirmDialog job={job} />}
    </div>
  );
}

export function JobStages({ job, events, jobCount = 1, refIso, batchChild = false, confirmOnGate = false }: {
  job: DataJob; events?: unknown; jobCount?: number; refIso?: string | null;
  batchChild?: boolean; confirmOnGate?: boolean;
}) {
  // 이벤트도 넘긴다 -- 제출 보류(identity_recheck_deferred)·관문 모호성 해소가 이 잡의 이벤트를 본다(RequestDetail 의
  // 배너용 모델과 같은 입력이라 둘이 같은 이야기를 한다).
  const model = useMemo(() => deriveJobStages(job, events), [job, events]);
  // 러너는 phase 가 끝날 때 파일을 쓴다 -- 상태나 ref 가 바뀌면 목록을 다시 읽는다(폴링 없음).
  const refreshKey = `${job.state}|${Object.keys(model.refs).sort().join(",")}`;
  const artifacts = useArtifacts(job.job_id, refreshKey);
  const nav = useStageNav(job.job_id);
  const outputs = useMemo(() => outputsByStep(model, artifacts.data?.entries), [model, artifacts.data]);
  const notes = useMemo(() => annotationsFor(events, job, jobCount, model.flow), [events, job, jobCount, model.flow]);
  const chips = useRef(new Map<string, HTMLButtonElement>());
  const chipRef = (key: string, el: HTMLButtonElement | null) => {
    if (el) chips.current.set(key, el); else chips.current.delete(key);
  };
  const viewerId = (slot: NavSlot) => `viewer-${job.job_id}-${slot}`;
  const previewHeadingId = `preview-result-${job.job_id}`;

  // 자동 열림(종단 실패의 실패 단계 로그 1건). 사용자가 한 번이라도 고르거나 닫았으면 덮어쓰지 않는다.
  const auto = model.autoOpen;
  const { autoSelect, touched, selected } = nav;
  useEffect(() => {
    if (auto && !touched && selected[auto.stage] === null) autoSelect(auto.stage, auto.key);
  }, [auto?.stage, auto?.key, touched, selected, autoSelect]);

  // 배너 CTA(「실패 지점 로그 보기」 등)의 이동 요청: 선택이 그려진 다음 프레임에 뷰어로 스크롤 + 포커스.
  const lastReveal = useRef(0);
  const reveal = nav.reveal;
  useEffect(() => {
    if (!reveal || reveal.token === lastReveal.current) return;
    lastReveal.current = reveal.token;
    const id = reveal.target === "previewResult" ? previewHeadingId : viewerId(reveal.target);
    const reduced = typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const raf = requestAnimationFrame(() => {
      const el = document.getElementById(id);
      if (!el) return;
      el.scrollIntoView?.({ block: "start", behavior: reduced ? "auto" : "smooth" });
      el.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(raf);
  }, [reveal?.token]);

  const entries = Array.isArray(artifacts.data?.entries) ? artifacts.data!.entries : [];
  const entrySize = (item: OutputItem) => {
    if (item.kind !== "artifact") return null;
    const e = entries.find((x) => x.phase === item.phase && x.name === item.name);
    return e && typeof e.size === "number" && Number.isFinite(e.size) ? e.size : null;
  };
  const listLoading = artifacts.isLoading;
  const onClose = (slot: NavSlot) => {
    const key = nav.selected[slot];
    nav.close(slot);
    // 닫은 뒤 포커스를 원래 칩으로(뷰어가 사라지면 포커스가 body 로 떨어진다).
    if (key) chips.current.get(key)?.focus();
  };
  const ctxFor = (slot: NavSlot): RowCtx => ({
    job, model, refIso, slot, selectedKey: nav.selected[slot], viewerId: viewerId(slot),
    entrySize, listLoading, onSelect: nav.select, onClose, chipRef,
  });

  const renderSteps = (stage: StageModel) => {
    const ctx = ctxFor(stage.id);
    return (
      <ol aria-label={`${stageTitle(stage.id, model.flow)} 단계`}>
        {stage.steps.map((s, i) => (
          <StepRow key={s.id} step={s} items={outputs.byStep[s.id] ?? []} notes={notes[s.id] ?? []}
                   last={i === stage.steps.length - 1} ctx={ctx} />
        ))}
      </ol>
    );
  };

  const previewReached = "preview" in model.refs
    || normTransitions(job.transitions).some((t) => t.to_state === "PreviewRunning");
  const preResult = model.flow === "previewed" ? (
    model.resultStage === "pre"
      ? <PreviewResult job={job} summary={job.result_summary} failedAt headingId={previewHeadingId} />
      : job.preview_summary != null
        ? <PreviewResult job={job} summary={job.preview_summary} failedAt={false} headingId={previewHeadingId} />
        // 요약이 없어도 「미리보기 결과 보기」(배너)의 포커스 대상은 남긴다(같은 id).
        : previewReached ? (
          <p id={previewHeadingId} tabIndex={-1}
             className="scroll-mt-4 text-sm text-ink/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
            이 잡은 미리보기 요약이 저장되지 않았습니다
          </p>
        ) : null
  ) : null;

  const listErr = artifacts.isError;
  const truncated = artifacts.data?.truncated === true;
  const otherCtx = ctxFor("other");
  const otherSelected = outputs.other.flatMap((g) => g.items).find((i) => i.key === nav.selected.other) ?? null;

  return (
    <div className="mt-4">
      <StageSection n={1} stage={model.pre} model={model} op={job.operation} titleId={`stage-pre-${job.job_id}`}
                    refIso={refIso}>
        {preResult}
        {renderSteps(model.pre)}
      </StageSection>
      {model.flow === "previewed" && model.gate ? (
        <GateRow job={job} model={model} refIso={refIso} batchChild={batchChild}
                 showConfirm={confirmOnGate && !batchChild} />
      ) : (
        <div className="ml-3 border-l-2 border-dotted border-line py-2 pl-4 text-xs text-ink/70">
          미리보기·컨펌 없이 바로 실행됩니다
        </div>
      )}
      <StageSection n={2} stage={model.exec} model={model} op={job.operation} titleId={`stage-exec-${job.job_id}`}
                    refIso={refIso}>
        {model.resultStage === "exec" && <ExecutionResult summary={job.result_summary} />}
        {renderSteps(model.exec)}
      </StageSection>
      {outputs.other.length > 0 && (
        // PHASES 밖·흐름 밖 phase(방어). 조용히 버리지 않는다.
        <div className="mt-3 min-w-0 rounded-lg border border-dashed border-line px-3 py-2 sm:px-4">
          {outputs.other.map((g) => (
            <div key={g.phase} className="min-w-0">
              <p className="text-xs text-ink/70">{`기타 출력 · ${g.phase}`}</p>
              <OutputChips label={`기타 출력 ${g.phase}`} items={g.items} slot="other" selectedKey={nav.selected.other}
                           live={false} onSelect={nav.select} chipRef={chipRef} viewerId={otherCtx.viewerId} />
            </div>
          ))}
          {otherSelected && (
            <OutputViewer key={otherSelected.key} jobId={job.job_id} item={otherSelected}
                          entrySize={entrySize(otherSelected)} live={false} viewerId={otherCtx.viewerId}
                          onClose={() => onClose("other")} />
          )}
        </div>
      )}
      {listLoading && (
        <div className="mt-3 flex items-center gap-1.5">
          <Skeleton className="h-7 w-24 rounded-full" />
          <Skeleton className="h-7 w-24 rounded-full" />
          <span className="sr-only">출력 목록을 불러오는 중…</span>
        </div>
      )}
      {listErr && (
        // 목록이 실패해도 로그 칩(phase_refs 만으로 그린다)은 그대로 쓸 수 있다 -- 화면 전체를 오류로 바꾸지 않는다.
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <CircleAlert className="h-4 w-4 shrink-0 text-bad" aria-hidden />
          <p className="min-w-0 flex-1 text-bad break-keep">
            {`출력 파일 목록을 불러오지 못했습니다 — ${artifacts.error instanceof Error ? artifacts.error.message : String(artifacts.error)}`}
          </p>
          <button type="button" className={SMALL_BTN} onClick={() => { void artifacts.refetch(); }}>다시 시도</button>
        </div>
      )}
      {truncated && <p className="mt-2 text-xs text-ink/70">일부 항목만 표시됩니다</p>}
    </div>
  );
}

