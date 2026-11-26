// sync 진행률(2026-11-26) 한 칸의 순수 모델 -- 요청 상세 잡 카드의 「진행 중」 실행·미리보기 행 아래(JobProgress)가 이 결과만
// 그린다. 입력은 GET /api/user/jobs/{job_id}/progress 응답(lib/types JobProgressResponse -- 계약 lib/progressContract.json)과 그
// 행의 단계 모델(stageModel StepModel)이다.
//
// 규약:
// - **도구가 준 값만 그린다.** 막대(percent)는 아래 우선순위의 첫 근거 하나이고, 근거가 없으면 null(막대 없이 수치만) -- 비율을
//   지어내지 않는다. 키 없음 = 모름이라 그 조각을 빼고, 0 은 정상값이라 「0 항목」으로 그린다(null≠0).
// - **남은 시간은 어떤 경로로도 그리지 않는다**(스키마에도 수치·원문이 없다). 모든 출력 문자열에 계약 ui_forbidden_substrings
//   (「남은」·「left」)가 없다는 것을 시험이 고정한다.
// - 진행 값은 **표시 전용**이다 -- 확인 게이트·지문·통계·사유 어디에도 쓰지 않는다(진행 줄은 경로 이름으로 위조될 수 있다 --
//   흔들리는 것은 자기 잡 표시뿐이어야 한다).
// - available:false 는 **단정하지 않는다**(모름을 실패로 둔갑시키지 않는다): Volcano Pending 과 진행 기록을 남기지 않는 이전 잡
//   이미지를 화면이 가릴 수 없다 -- 늘 info 톤, 「~일 수 있습니다」.
// - 단계 제목은 STATUS_LABEL(「진행 중」 등)·단계 배지 글자와 겹치지 않는다(e2e 정확 일치 단언). 미지 phase 도 「진행 단계 알 수
//   없음」이다. phase 는 소문자 정규식만 통과하지만 「constructor」처럼 Object.prototype 이름과 겹칠 수 있어 자기 키만 찾는다.
import { reasonText } from "../../lib/api";
import { readSummary } from "../../lib/toolSummary";
import type { JobProgressResponse, ProgressSnapshot, ToolSummary } from "../../lib/types";
import { finiteOrNull, humanBytes, isPlainObject, msText } from "./format";

export interface ProgressNote { tone: "info" | "attn"; text: string }
export interface ProgressView {
  /** 「진행 · {title}」의 단계 이름. null = 단계를 모른다(응답 없음·available:false·오류) -- 머리는 「진행」만. */
  title: string | null;
  /** 0..100. null = 막대 없음(근거가 없으면 지어내지 않는다). */
  percent: number | null;
  /** 막대의 기준 문구(percent 가 있을 때만). */
  basis: string | null;
  /** 기준의 설명(HelpTip). */
  basisHelp: string | null;
  /** 사실 조각(「생성 38,308 / 100,002 항목」…) -- 화면이 「 · 」로 잇는다. 모르는 조각은 없다. */
  facts: string[];
  /** 「갱신 12초 전」(스냅숏이 있을 때만). */
  fresh: string | null;
  note: ProgressNote | null;
}

/** 이 칸이 읽는 단계 모델 조각(stageModel StepModel 이 만족한다). */
export interface ProgressStepInfo { id: string; start: string | null; schedWaitSec: number | null }
/** 이 칸이 읽는 잡 조각(DataJob 이 만족한다). */
export interface ProgressJobInfo { tool?: string | null; preview_summary?: ToolSummary | null }

// 계약 snapshot.phases 전부(시험이 대조한다). batches 의 미리보기 제목은 PHASE_TITLE_PREVIEW.
export const PHASE_TITLE: Readonly<Record<string, string>> = {
  workers: "워커 준비 대기", start: "도구 시작",
  walk_src: "소스 탐색", walk_dst: "목적지 탐색",
  compare: "변경 비교(크기·수정 시각)", compare_contents: "내용 비교",
  delete: "목적지에서 삭제", copy_prep: "복사 준비",
  mkdir: "디렉토리 생성", create: "파일 생성",
  copy: "데이터 복사", metadata: "소유자·권한·시각 적용",
  metadata_dirs: "디렉토리 속성 적용", timestamps: "시각 마무리",
  done: "마무리 중",
  scan: "스캔·배치 준비", batches: "배치 실행", finishing: "결과 정리",
};
const PHASE_TITLE_PREVIEW: Readonly<Record<string, string>> = { batches: "배치 계획" };
export const ENDED_TITLE = "도구 종료 · 결과 정리 중";
export const UNKNOWN_PHASE_TITLE = "진행 단계 알 수 없음";

// 러너 심장박동(30초)의 3배를 넘게 스냅숏이 낡으면 정체 경고(설계 §5.3 안내 4).
export const STALE_AFTER_S = 90;
// 화면이 아직 아무 스냅숏도 못 받은 동안 「기다리는 중」이라고만 말하는 시간(실행 시작 기준).
export const WAIT_GRACE_S = 60;
const NSYNC_BATCH_HINT_AFTER_S = 30;
const DSYNC_QUIET_LINE_AFTER_S = 60;

// dsync 단계 % 를 막대로 쓰는 단계(도구가 % 를 찍는 진행 줄이 있는 단계).
const DSYNC_STEP_PERCENT_PHASES = new Set(["create", "mkdir", "copy", "compare_contents", "delete"]);
// dsync 가 진행 줄을 내지 않는 단계(실측: lite 비교·복사 준비·디렉토리 속성·시각 마무리).
const DSYNC_SILENT_PHASES = new Set(["compare", "copy_prep", "metadata_dirs", "timestamps"]);

export const NOTE = {
  loading: "진행 정보를 불러오는 중…",
  waiting: "진행 정보를 기다리는 중…",
  noneRunning: "진행 정보가 없습니다 — 진행 기록을 남기지 않는 이전 잡 이미지로 실행 중이거나, 기록을 쓰지 못했을 수 있습니다",
  noneYet: "진행 정보가 아직 없습니다 — 파드가 스케줄을 기다리는 중이거나, 진행 기록을 남기지 않는 이전 잡 이미지일 수 있습니다",
  quiet: "quiet 옵션으로 실행 중이라 도구가 진행 정보를 내지 않습니다",
  workers: "워커 파드가 준비되기를 기다리는 중입니다",
  nsyncSingleBatch: "nsync 는 배치가 끝날 때만 진행을 알립니다 — 이 작업은 배치가 1개라 끝날 때 한 번 표시됩니다",
  nsyncPerBatch: "배치가 끝날 때마다 갱신됩니다",
  dsyncSilentPhase: "이 단계는 도구가 진행 수치를 내지 않습니다",
} as const;
export const staleText = (d: string) => `진행 기록이 ${d} 동안 갱신되지 않았습니다`;
export const quietLineText = (d: string) =>
  `도구가 ${d} 동안 새 진행 줄을 내지 않았습니다 — 큰 파일을 복사 중이거나 저장소 응답이 느릴 수 있습니다`;

export const BASIS = {
  batch: "배치 기준(끝난 배치 수)",
  overall: "전체 항목 기준",
  step: "현재 단계 기준",
  walkEstimate: "미리보기 항목 수 기준 · 추정",
} as const;
const BASIS_HELP = {
  batch: "nsync 는 배치가 하나 끝날 때마다 진행을 알립니다 — 배치 안의 진행은 보이지 않습니다.",
  overall: "dsync 가 배치(--batch-files)가 끝날 때마다 찍는 누적 항목 수의 비율입니다.",
  step: "도구가 지금 단계에 대해 찍은 비율입니다 — 전체 작업의 비율이 아닙니다.",
  copy: "복사 단계의 비율은 바이트 기준이고 배치 안의 값입니다 — 파일 크기가 섞여 있으면 실제 소요 시간과 다를 수 있습니다.",
  walkEstimate: "소스 탐색 수를 미리보기에서 센 소스 항목 수로 나눈 추정입니다 — 그사이 소스가 바뀌면 다를 수 있어 99% 에서 멈춥니다.",
} as const;

const own = (o: Readonly<Record<string, string>>, k: string): string | undefined =>
  Object.prototype.hasOwnProperty.call(o, k) ? o[k] : undefined;

/** 유한한 0 이상 수만 수로(서버가 거른 값이어도 한 번 더 -- 수치를 지어내지 않는다). */
const num = (v: unknown): number | null => {
  const n = finiteOrNull(v);
  return n !== null && n >= 0 ? n : null;
};
const pct = (v: unknown): number | null => {
  const n = num(v);
  return n !== null && n <= 100 ? n : null;
};
const obj = (v: unknown): Record<string, unknown> | null => (isPlainObject(v) ? v : null);

export const countFmt = (n: number) => n.toLocaleString("ko-KR");
const rateFmt = (n: number) => n.toLocaleString("ko-KR", { maximumFractionDigits: 1 });
/** % 표기는 소수 1자리 **버림** -- 99.96 을 「100%」로 올려 끝난 것처럼 보이지 않게. */
export const pctFmt = (p: number) => `${(Math.floor(p * 10) / 10).toLocaleString("ko-KR")}%`;

/** 실행 시작(ms) = 단계 시작 + 스케줄 대기(StepMeta 「HH:MM 시작」과 같은 식). 모르면 null. */
function runStartMs(step: ProgressStepInfo): number | null {
  if (step.start === null) return null;
  const ms = Date.parse(step.start);
  if (Number.isNaN(ms)) return null;
  return ms + (step.schedWaitSec ?? 0) * 1000;
}

function errorCode(error: unknown): string | null {
  if (error === null || error === undefined) return null;
  if (isPlainObject(error) && typeof error.code === "string" && error.code !== "") return error.code;
  return error instanceof Error ? error.message : String(error);
}

const EMPTY_VIEW: ProgressView = { title: null, percent: null, basis: null, basisHelp: null, facts: [], fresh: null, note: null };

/** 스냅숏이 없을 때(응답 전·available:false) -- 단정하지 않는 문구(늘 info). */
function waitingView(step: ProgressStepInfo, nowMs: number, answered: boolean): ProgressView {
  if (!answered) return { ...EMPTY_VIEW, note: { tone: "info", text: NOTE.loading } };
  const start = runStartMs(step);
  if (start !== null && nowMs - start < WAIT_GRACE_S * 1000) return { ...EMPTY_VIEW, note: { tone: "info", text: NOTE.waiting } };
  // 실행 행이고 RUNNING 이 관측됐다(schedWaitSec 있음 -- launcher 가 떠 있다) -- Pending 은 아니다. 그 밖(미리보기는 스케줄 대기를
  // 따로 보지 않는다·대기를 모르는 옛 잡)은 Pending 일 수도 있다.
  const running = step.id === "execution" && step.schedWaitSec !== null;
  return { ...EMPTY_VIEW, note: { tone: "info", text: running ? NOTE.noneRunning : NOTE.noneYet } };
}

interface Bar { percent: number; basis: string; help: string }

function barOf(tool: string | null, snap: ProgressSnapshot, phase: string, job: ProgressJobInfo,
               step: ProgressStepInfo): Bar | null {
  if (tool === "nsync") {
    const b = obj(snap.batch);
    const count = b ? num(b.count) : null;
    if (b && count !== null && count >= 1) {
      // 도구 % 가 없으면(Progress logging enabled 직후 -- done 0) 도구가 준 두 수(끝난 배치·배치 수)의 비율이다.
      const done = num(b.done);
      const p = pct(b.percent) ?? (done !== null && done <= count ? (done / count) * 100 : null);
      if (p !== null) return { percent: p, basis: BASIS.batch, help: BASIS_HELP.batch };
    }
    return null;
  }
  if (tool !== "dsync") return null;
  const o = obj(snap.overall);
  const op = o ? pct(o.percent) : null;
  if (op !== null) return { percent: op, basis: BASIS.overall, help: BASIS_HELP.overall };
  const s = obj(snap.step);
  const sp = s ? pct(s.percent) : null;
  if (sp !== null && DSYNC_STEP_PERCENT_PHASES.has(phase)) {
    return { percent: sp, basis: BASIS.step, help: phase === "copy" ? BASIS_HELP.copy : BASIS_HELP.step };
  }
  // 소스 탐색 추정: 미리보기가 센 소스 전체 walk 수(dsync preview source_items)가 분모다. 생성·복사 단계의 분모가 아니다(재동기화는
  // 바꿀 항목이 일부뿐) -- 탐색에만 쓴다. 미리보기 행 자신은 아직 그 수가 없다(실행 행만).
  if (phase === "walk_src" && step.id === "execution") {
    const items = s ? num(s.items) : null;
    const src = readSummary(job.preview_summary ?? null).sourceItems;
    if (items !== null && src !== null && src > 0) {
      return { percent: Math.min(99, (items / src) * 100), basis: BASIS.walkEstimate, help: BASIS_HELP.walkEstimate };
    }
  }
  return null;
}

function dsyncFacts(snap: ProgressSnapshot, phase: string, bar: Bar | null, job: ProgressJobInfo): string[] {
  const out: string[] = [];
  const s = obj(snap.step);
  const items = s ? num(s.items) : null;
  const total = s ? num(s.total) : null;
  const o = obj(snap.overall);
  if (o) {
    const oi = num(o.items);
    const ot = num(o.total);
    if (oi !== null && ot !== null) out.push(`전체 ${countFmt(oi)} / ${countFmt(ot)} 항목`);
    else if (oi !== null) out.push(`전체 ${countFmt(oi)} 항목 반영`);
  }
  // 막대가 전체 기준이면 단계 % 는 사실 줄로(막대와 다른 값이다).
  const sp = s ? pct(s.percent) : null;
  if (bar?.basis === BASIS.overall && sp !== null) out.push(`현재 단계 ${pctFmt(sp)}`);
  switch (phase) {
    case "walk_src":
    case "walk_dst":
      if (items !== null) out.push(`탐색 ${countFmt(items)} 항목`);
      if (bar?.basis === BASIS.walkEstimate) {
        const src = readSummary(job.preview_summary ?? null).sourceItems;
        if (src !== null) out.push(`미리보기 소스 ${countFmt(src)} 항목`);
      }
      break;
    case "create":
    case "mkdir": {
      const unit = phase === "mkdir" ? "디렉토리" : "항목";
      if (items !== null) out.push(total !== null ? `생성 ${countFmt(items)} / ${countFmt(total)} ${unit}` : `생성 ${countFmt(items)} ${unit}`);
      else if (total !== null) out.push(`대상 ${countFmt(total)} ${unit}`);
      break;
    }
    case "copy":
    case "compare_contents": {
      const bytes = s ? num(s.bytes) : null;
      const rate = s ? num(s.rate_bps) : null;
      if (bytes !== null) out.push(`${phase === "copy" ? "복사" : "비교"} 약 ${humanBytes(bytes)}`);
      if (rate !== null) out.push(`평균 ${humanBytes(rate)}/s`);
      if (phase === "compare_contents" && total !== null) out.push(`대상 ${countFmt(total)} 항목`);
      break;
    }
    case "compare":
      if (total !== null) out.push(`대상 ${countFmt(total)} 항목`);
      break;
    case "metadata":
    case "metadata_dirs":
      if (items !== null) out.push(`적용 ${countFmt(items)} 항목`);
      break;
    case "delete":
      if (items !== null) out.push(total !== null ? `삭제 ${countFmt(items)} / ${countFmt(total)} 항목` : `삭제 ${countFmt(items)} 항목`);
      else if (total !== null) out.push(`삭제 대상 ${countFmt(total)} 항목`);
      break;
  }
  return out;
}

function nsyncFacts(snap: ProgressSnapshot, phase: string, dryrun: boolean, job: ProgressJobInfo): string[] {
  const out: string[] = [];
  const b = obj(snap.batch);
  const sc = obj(snap.scan);
  if (phase === "scan" && sc) {
    const src = num(sc.src_items);
    const dst = num(sc.dst_items);
    if (src !== null && dst !== null) out.push(`스캔 소스 ${countFmt(src)} · 목적지 ${countFmt(dst)} 항목`);
    else if (src !== null) out.push(`스캔 소스 ${countFmt(src)} 항목`);
    const bc = num(sc.batch_count);
    if (bc !== null) out.push(`배치 ${countFmt(bc)}개`);
  }
  if (!b) return out;
  const done = num(b.done);
  const count = num(b.count);
  if (phase !== "scan" && done !== null && count !== null) out.push(`끝난 배치 ${countFmt(done)} / ${countFmt(count)}`);
  if (dryrun) {
    const acts = num(b.planned_actions);
    const files = num(b.planned_copy_files);
    const bytes = num(b.planned_bytes);
    if (acts !== null) out.push(`계획 동작 ${countFmt(acts)}`);
    if (files !== null) out.push(`복사 계획 ${countFmt(files)} 파일`);
    if (bytes !== null) out.push(`데이터 약 ${humanBytes(bytes)}`);
    return out;
  }
  // 미리보기 계획은 「미리보기 계획」이라 이름 붙인다(미리보기와 실행 사이에 소스가 바뀔 수 있다). 비율은 만들지 않는다 -- 계획
  // 0 이면 「미리보기 계획 0」 그대로, 모르면 그 조각을 뺀다(0 B 로 그리지 않는다).
  const ps = readSummary(job.preview_summary ?? null);
  const plannedCopy = ps.planned?.copy ?? null;
  const files = num(b.copied_files);
  const bytes = num(b.copied_bytes);
  const avg = num(b.avg_files_per_s);
  if (files !== null) {
    out.push(`복사 파일 ${countFmt(files)}${plannedCopy !== null ? ` · 미리보기 계획 ${countFmt(plannedCopy)}` : ""}`);
  }
  if (bytes !== null) {
    out.push(`데이터 약 ${humanBytes(bytes)}${ps.plannedBytes !== null ? ` · 미리보기 계획 ${humanBytes(ps.plannedBytes)}` : ""}`);
  }
  if (avg !== null) out.push(`평균 ${rateFmt(avg)} 파일/s`);
  return out;
}

function noteOf(tool: string | null, snap: ProgressSnapshot, phase: string, ageNow: number): ProgressNote | null {
  if (snap.quiet === true) return { tone: "info", text: NOTE.quiet };
  // 워커 준비(최대 300초 + hostfile 대기) 동안은 러너 tick 이 없어 심장박동이 없다 -- 정체 경고를 끈다.
  if (phase === "workers") return { tone: "info", text: NOTE.workers };
  // 끝 스냅숏: 러너가 더 쓰지 않는다(스테퍼 전이를 기다리는 몇 초~수십 초) -- 경고 없음.
  if (snap.ended === true) return null;
  if (ageNow > STALE_AFTER_S) return { tone: "attn", text: staleText(msText(ageNow * 1000) ?? `${Math.round(ageNow)}초`) };
  const sinceLine = num(snap.since_line_s);
  if (tool === "nsync" && phase === "batches") {
    const b = obj(snap.batch);
    const count = b ? num(b.count) : null;
    const done = b ? num(b.done) : null;
    if (count === 1 && done === 0) return { tone: "info", text: NOTE.nsyncSingleBatch };
    if (count !== null && count > 1 && sinceLine !== null && sinceLine + ageNow > NSYNC_BATCH_HINT_AFTER_S) {
      return { tone: "info", text: NOTE.nsyncPerBatch };
    }
    return null;
  }
  if (tool === "dsync") {
    if (DSYNC_SILENT_PHASES.has(phase)) return { tone: "info", text: NOTE.dsyncSilentPhase };
    if (sinceLine !== null && sinceLine + ageNow > DSYNC_QUIET_LINE_AFTER_S) {
      const d = msText((sinceLine + ageNow) * 1000);
      if (d !== null) return { tone: "info", text: quietLineText(d) };
    }
  }
  return null;
}

/**
 * 응답·오류·단계 → 그릴 것. resp = 마지막 성공 응답(없으면 undefined), error = 마지막 조회 오류(TanStack 은 재조회가 실패해도 마지막
 * 성공 data 를 남긴다 -- 그때는 스냅숏을 그대로 그리고 안내를 오류로 바꾼다), fetchedAtMs = 그 응답을 받은 시각(dataUpdatedAt).
 * 신선도 = 서버가 잰 나이(age_seconds -- 브라우저 시계 무관) + 받은 뒤 흐른 시간.
 */
export function progressView(resp: JobProgressResponse | undefined, error: unknown, job: ProgressJobInfo,
                             step: ProgressStepInfo, nowMs: number, fetchedAtMs: number): ProgressView {
  const code = errorCode(error);
  const errNote: ProgressNote | null = code !== null ? { tone: "attn", text: reasonText(code) } : null;
  const snap = resp && resp.available === true && isPlainObject(resp.progress) ? resp.progress : null;
  if (snap === null) {
    if (errNote) return { ...EMPTY_VIEW, note: errNote };
    return waitingView(step, nowMs, resp !== undefined);
  }
  const tool = typeof snap.tool === "string" ? snap.tool : typeof job.tool === "string" ? job.tool : null;
  const phase = typeof snap.phase === "string" ? snap.phase : "";
  const dryrun = snap.dryrun === true;
  const title = snap.ended === true ? ENDED_TITLE
    : (dryrun ? own(PHASE_TITLE_PREVIEW, phase) : undefined) ?? own(PHASE_TITLE, phase) ?? UNKNOWN_PHASE_TITLE;
  const age = num(resp?.age_seconds) ?? 0;
  const ageNow = age + Math.max(0, nowMs - fetchedAtMs) / 1000;
  const bar = barOf(tool, snap, phase, job, step);
  const facts = tool === "nsync" ? nsyncFacts(snap, phase, dryrun, job)
    : tool === "dsync" ? dsyncFacts(snap, phase, bar, job) : [];
  const fresh = msText(ageNow * 1000);
  return {
    title,
    percent: bar?.percent ?? null,
    basis: bar?.basis ?? null,
    basisHelp: bar?.help ?? null,
    facts,
    fresh: fresh !== null ? `갱신 ${fresh} 전` : null,
    note: errNote ?? noteOf(tool, snap, phase, ageNow),
  };
}

/** 이 칸을 그릴 행인가(설계 §5.1): 진행 중인 실행·미리보기 행, sync 도구(dsync·nsync), 그 단계 vcjob 제출 기록(ref)이 있다. */
export function showsProgress(step: { id: string; status: string; phase: string | null }, terminal: boolean,
                              tool: string | null | undefined, refs: Record<string, string>): boolean {
  return !terminal && step.status === "running"
    && (step.id === "execution" || step.id === "preview")
    && (tool === "dsync" || tool === "nsync")
    && step.phase !== null && Object.prototype.hasOwnProperty.call(refs, step.phase);
}
