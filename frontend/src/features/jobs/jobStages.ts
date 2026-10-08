// 잡 한 개를 「① 사전 점검·미리보기 → (작업 컨펌) → ② 실행」 두 단계 구획으로 읽는 순수 모델(2026-10-08 요청 상세
// 재설계). 화면(JobStages)은 이 모델만 그린다 -- 상태·시각·실패 지점 판정을 컴포넌트 곳곳에 흩으면 같은 잡이
// 배너와 단계 행에서 서로 다른 이야기를 한다.
//
// 근거는 전부 **서버가 이미 보내는 값**이다: job.state·transitions·phase_refs·reason_code·exec_submitted_at.
// 모르는 것은 null 로 둔다(시각을 지어내지 않는다, 0초를 지어내지 않는다 -- null≠0). DB 가 신뢰 경계라
// transitions·phase_refs 는 배열·객체가 아닐 수도 있다는 전제로 정규화한다(옛 M5 사고: 무방어 인덱싱이 화면을 죽였다).
import { isTerminal } from "../../lib/jobState";
import type { ArtifactEntry, DataJob, DiagEvent, Transition } from "../../lib/types";
import { finiteOrNull, isPlainObject, spanText } from "./format";

export type StageId = "pre" | "exec";
export type StepId = "preflight" | "preview" | "confirm" | "exec_preflight" | "execution";
export type StepStatus =
  | "waiting" | "running" | "awaiting" | "done"
  | "failed" | "rejected" | "timed_out" | "cancelled" | "expired"
  | "skipped" | "unknown";
// 실패 지점을 무엇으로 알았나. deepest_ref·none 은 **추정**이라 화면이 단계를 단정하지 않는다.
export type Evidence = "submit_prefix" | "reason_prefix" | "from_state" | "state" | "deepest_ref" | "none";
export type Flow = "scan" | "previewed";

export interface StepModel {
  id: StepId; stage: StageId | "gate"; phase: string | null;        // confirm = null(파드가 없는 사람 관문)
  status: StepStatus;
  start: string | null; end: string | null;                         // ISO. 모르면 null
  actor: string | null;                                             // confirm 만: 컨펌한 주체
  logAvailable: boolean;
  submitFailed: boolean; notStarted: boolean;
  schedWaitSec: number | null;                                      // execution 만. null=모름, 0=정상값
}
export interface StageModel {
  id: StageId; steps: StepModel[]; status: StepStatus;
  start: string | null; end: string | null;
}
export interface FailurePoint {
  step: StepId; stage: StageId | "gate"; evidence: Evidence; submitFailed: boolean; notStarted: boolean;
}
export interface JobStagesModel {
  flow: Flow;
  steps: StepModel[];
  pre: StageModel; gate: StepModel | null; exec: StageModel;        // scan 이면 gate = null
  terminal: boolean; known: boolean;
  failure: FailurePoint | null;
  executionStarted: boolean;
  resultStage: StageId | null;
  autoOpen: { stage: StageId; key: string } | null;                 // key = "log:<phase>"
  refs: Record<string, string>;
}
export type OutputItem =
  | { kind: "log"; key: `log:${string}`; phase: string }
  | { kind: "artifact"; key: `artifact:${string}/${string}`; phase: string; name: string; size: number | null };

// 백엔드 artifact_files.PHASES 와 같은 넷.
export const PHASES = ["preflight", "preview", "exec_preflight", "execution"] as const;
export type Phase = (typeof PHASES)[number];
const isPhase = (p: unknown): p is Phase => typeof p === "string" && (PHASES as readonly string[]).includes(p);
export const STAGE_OF_PHASE: Record<Phase, StageId> = {
  preflight: "pre", preview: "pre", exec_preflight: "exec", execution: "exec",
};
export const STEP_COPY: Record<StepId, string> = {
  preflight: "사전 점검", preview: "미리보기", confirm: "작업 컨펌",
  exec_preflight: "실행 직전 재점검", execution: "실행",
};
// 상태 라벨은 한국어만 -- 영문 상태 문자열("Succeeded"·"Failed"·"Cancelled")은 StatusPill 의 몫이다. 단계 배지가
// 영문을 쓰면 e2e E6(Succeeded 배지 = 요청 1 + 잡 N)과 정확 일치 단언들이 깨진다(스펙 C1).
export const STATUS_LABEL: Record<StepStatus, string> = {
  waiting: "대기", running: "진행 중", awaiting: "컨펌 대기", done: "완료",
  failed: "실패", rejected: "거부됨", timed_out: "시간 초과", cancelled: "취소됨",
  expired: "만료", skipped: "실행 안 됨", unknown: "모름",
};
export const FAILED_LIKE: ReadonlySet<StepStatus> = new Set(["failed", "rejected", "timed_out"]);

export function stageTitle(stage: StageId | "gate", flow: Flow): string {
  if (stage === "gate") return "작업 컨펌";
  if (stage === "exec") return "실행";
  return flow === "scan" ? "사전 점검" : "사전 점검·미리보기";
}
export function stageCode(stage: StageId, flow: Flow): string {
  if (stage === "pre") return flow === "scan" ? "preflight" : "preflight · preview";
  return flow === "scan" ? "execution" : "exec_preflight · execution";
}

// 제출 자체가 실패한 잡은 phase_refs 가 비어 있다(파드가 안 만들어졌다). stepper 는 그 원문을 diag_logs 에 합성
// 항목으로 박제하고 API 는 ref 없이도 박제 사본을 돌려주므로(2026-09-15 프로덕션 사고: apiserver 422 사유가 포탈
// 어디에도 없었다) reason_code 의 `<phase>_submit_failed:` 접두사에서 그 phase 를 되살려 로그 칩을 만든다.
// (JobViewer 에서 같은 이름으로 옮겨 왔다.)
const SUBMIT_FAILED_PHASE: Record<string, Phase> = {
  preflight_submit_failed: "preflight",
  execution_submit_failed: "execution",
  preview_submit_failed: "preview",
  execution_recheck_submit_failed: "exec_preflight",
};
export function submitFailedPhase(reasonCode: string | null | undefined): Phase | null {
  if (!reasonCode) return null;
  const head = reasonCode.split(":")[0];
  return SUBMIT_FAILED_PHASE[head] ?? null;
}

// 사유 접두 → 단계(reasonCodes.json 에 있는 코드만). 마커 승격 사유(destination_not_writable 등)는 phase 접두를
// 잃으므로 여기 없다 -- 그건 전이의 from_state 로 판정한다.
const REASON_PREFIX_STEP: Record<string, StepId> = {
  preflight_failed: "preflight",
  preview_failed: "preview", preview_timed_out: "preview", empty_preview: "preview",
  execution_recheck_failed: "exec_preflight",
  execution_failed: "execution",
  preview_expired: "confirm",
};

const KNOWN_STATES = new Set([
  "Pending", "Preflight", "PreviewRunning", "ConfirmPending", "Executing", "Running",
  "Succeeded", "Failed", "TimedOut", "Cancelled", "Rejected", "PreviewExpired",
]);
const FAIL_STATUS: Record<string, StepStatus> = {
  Failed: "failed", Rejected: "rejected", TimedOut: "timed_out",
  Cancelled: "cancelled", PreviewExpired: "expired",
};

export function normTransitions(raw: unknown): Transition[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((t): t is Transition => isPlainObject(t) && typeof t.to_state === "string");
}
export function normRefs(raw: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!isPlainObject(raw)) return out;
  for (const [k, v] of Object.entries(raw)) if (typeof v === "string" && v !== "") out[k] = v;
  return out;
}
const has = (refs: Record<string, string>, p: string) => Object.prototype.hasOwnProperty.call(refs, p);
const atOf = (t: Transition | null | undefined): string | null =>
  t && typeof t.at === "string" ? t.at : null;

function orderOf(flow: Flow): StepId[] {
  return flow === "scan" ? ["preflight", "execution"]
    : ["preflight", "preview", "confirm", "exec_preflight", "execution"];
}
// scan 에 없는 단계가 판정되면 가장 가까운 실재 단계로 접는다(미리보기·컨펌 → 사전 점검, 재점검 → 실행).
function fold(step: StepId, flow: Flow): StepId {
  if (flow === "previewed") return step;
  if (step === "preview" || step === "confirm") return "preflight";
  if (step === "exec_preflight") return "execution";
  return step;
}
function stageOfStep(id: StepId): StageId | "gate" {
  return id === "confirm" ? "gate" : STAGE_OF_PHASE[id];
}

// 종단 실패의 실패 지점(위에서부터 처음 걸리는 규칙).
function locateFailure(job: DataJob, tr: Transition[], refs: Record<string, string>, flow: Flow): FailurePoint {
  const mk = (step: StepId, evidence: Evidence, extra: Partial<FailurePoint> = {}): FailurePoint => {
    const s = fold(step, flow);
    return { step: s, stage: stageOfStep(s), evidence, submitFailed: false, notStarted: false, ...extra };
  };
  if (job.state === "PreviewExpired") return mk("confirm", "state");
  // 제출 접두가 먼저다: preview 제출 실패는 Preflight→Failed 전이로 남고 scan 의 execution 제출 실패도 from_state 가
  // Preflight 라, from_state 만 보면 한 칸 앞을 가리킨다.
  const sfp = submitFailedPhase(job.reason_code);
  if (sfp !== null) return mk(sfp, "submit_prefix", { submitFailed: true });
  const prefix = typeof job.reason_code === "string" ? job.reason_code.split(":")[0] : "";
  if (REASON_PREFIX_STEP[prefix]) return mk(REASON_PREFIX_STEP[prefix], "reason_prefix");
  const terminalT = [...tr].reverse().find((t) => isTerminal(t.to_state)) ?? null;
  switch (terminalT?.from_state) {
    case "Pending": return mk("preflight", "from_state", { notStarted: true });
    case "Preflight": return mk("preflight", "from_state");
    case "PreviewRunning": return mk("preview", "from_state");
    case "ConfirmPending": return mk("confirm", "from_state");
    case "Executing": return mk(has(refs, "execution") ? "execution" : "exec_preflight", "from_state");
    case "Running": return mk("execution", "from_state");
  }
  for (const p of ["execution", "exec_preflight", "preview", "preflight"] as const) {
    if (has(refs, p)) return mk(p, "deepest_ref");
  }
  return mk(orderOf(flow)[0], "none");
}

// 비종단 잡의 "지금 서 있는 단계". 앞은 done, 뒤는 waiting.
function currentStep(state: string, refs: Record<string, string>): StepId | null {
  switch (state) {
    case "Preflight": return "preflight";
    case "PreviewRunning": return "preview";
    case "ConfirmPending": return "confirm";
    case "Executing": return has(refs, "execution") ? "execution" : "exec_preflight";
    case "Running": return "execution";
    default: return null;   // Pending(전부 대기)
  }
}

function aggregate(statuses: StepStatus[]): StepStatus {
  if (statuses.includes("unknown")) return "unknown";
  for (const s of ["failed", "rejected", "timed_out"] as const) if (statuses.includes(s)) return s;
  if (statuses.includes("cancelled")) return "cancelled";
  if (statuses.includes("expired")) return "expired";
  if (statuses.includes("running") || statuses.includes("awaiting")) return "running";
  if (statuses.every((s) => s === "done")) return "done";
  if (statuses.every((s) => s === "skipped")) return "skipped";
  if (statuses.includes("done") && statuses.every((s) => s === "done" || s === "waiting")) return "running";
  if (statuses.includes("done") && statuses.every((s) => s === "done" || s === "skipped")) return "done";
  return "waiting";
}
const ENDED: ReadonlySet<StepStatus> = new Set(["done", "failed", "rejected", "timed_out", "cancelled", "expired"]);

export function deriveJobStages(job: DataJob): JobStagesModel {
  const flow: Flow = job.operation === "scan" ? "scan" : "previewed";
  const order = orderOf(flow);
  const tr = normTransitions(job.transitions);
  const refs = normRefs(job.phase_refs);
  const terminal = isTerminal(job.state);
  const known = KNOWN_STATES.has(job.state);
  const failure = terminal && job.state !== "Succeeded" ? locateFailure(job, tr, refs, flow) : null;
  const idx = (id: StepId) => order.indexOf(id);

  const cur = !terminal && known ? currentStep(job.state, refs) : null;
  const curFolded = cur === null ? null : fold(cur, flow);
  const statusOf = (id: StepId): StepStatus => {
    if (job.state === "Succeeded") return "done";
    if (failure) {
      const d = idx(id) - idx(failure.step);
      return d < 0 ? "done" : d === 0 ? FAIL_STATUS[job.state] ?? "failed" : "skipped";
    }
    if (!known) return "unknown";
    if (curFolded === null) return "waiting";
    const d = idx(id) - idx(curFolded);
    return d < 0 ? "done" : d > 0 ? "waiting" : id === "confirm" ? "awaiting" : "running";
  };

  // 시각(§4.5). 자기 전이(Executing→Executing)는 "재점검 통과 → 실행 vcjob 제출" 의 흔적이다.
  const firstTo = (s: string) => atOf(tr.find((t) => t.to_state === s && t.from_state !== s));
  const firstFrom = (s: string) => atOf(tr.find((t) => t.from_state === s && t.to_state !== s));
  const selfExec = atOf(tr.find((t) => t.from_state === "Executing" && t.to_state === "Executing"));
  const confirmT = tr.find((t) => t.from_state === "ConfirmPending" && t.to_state === "Executing") ?? null;
  const terminalT = [...tr].reverse().find((t) => isTerminal(t.to_state)) ?? null;
  const execSubmitted = typeof job.exec_submitted_at === "string" && job.exec_submitted_at !== ""
    ? job.exec_submitted_at : null;
  const execStart = execSubmitted ?? (flow === "scan" ? firstTo("Running") : selfExec);
  const endIfFailedHere = (id: StepId) => (failure?.step === id ? atOf(terminalT) : null);
  const sfp = submitFailedPhase(job.reason_code);

  const steps: StepModel[] = order.map((id) => {
    const status = statusOf(id);
    const isSubmitFailed = failure?.submitFailed === true && failure.step === id;
    const isNotStarted = failure?.notStarted === true && failure.step === id;
    let start: string | null = null;
    let end: string | null = null;
    let actor: string | null = null;
    switch (id) {
      case "preflight":
        start = isNotStarted ? null : firstTo("Preflight");
        end = firstFrom("Preflight") ?? endIfFailedHere(id);
        break;
      case "preview":
        start = firstTo("PreviewRunning");
        end = firstFrom("PreviewRunning") ?? endIfFailedHere(id);
        break;
      case "confirm":
        start = firstTo("ConfirmPending");
        end = firstFrom("ConfirmPending");
        actor = confirmT && typeof confirmT.actor === "string" ? confirmT.actor : null;
        break;
      case "exec_preflight":
        start = atOf(confirmT);
        end = execStart ?? endIfFailedHere(id);
        break;
      case "execution":
        start = execStart;
        end = ENDED.has(status) ? atOf(terminalT) : null;
        break;
    }
    // 제출 실패 단계는 파드가 없었다 -- 시작 시각은 모름, 끝은 종단 전이.
    if (isSubmitFailed) { start = null; end = atOf(terminalT); }
    // 아직 끝나지 않은 단계의 끝 시각은 그리지 않는다(다음 단계로 넘어간 흔적만으로 "끝" 을 단정하지 않게).
    if (!ENDED.has(status)) end = null;
    return {
      id, stage: stageOfStep(id), phase: id === "confirm" ? null : id,
      status, start, end, actor,
      logAvailable: id !== "confirm" && (has(refs, id) || sfp === id),
      submitFailed: isSubmitFailed, notStarted: isNotStarted,
      schedWaitSec: id === "execution" ? finiteOrNull(job.sched_wait_seconds) : null,
    };
  });

  const stage = (sid: StageId): StageModel => {
    const ss = steps.filter((s) => s.stage === sid);
    const status = aggregate(ss.map((s) => s.status));
    // 구획 범위: 첫 단계의 시작 → 실제로 돈 마지막 단계의 끝. 사람이 컨펌을 기다린 시간은 관문에 있어 빠진다.
    const ran = ss.filter((s) => ENDED.has(s.status));
    const endStep = ran.length ? ran[ran.length - 1] : null;
    return {
      id: sid, steps: ss, status,
      start: ss[0]?.start ?? null,
      end: ENDED.has(status) || status === "skipped" ? endStep?.end ?? null : null,
    };
  };

  const executionStarted = job.state === "Succeeded" || has(refs, "execution") || execSubmitted !== null
    || selfExec !== null || (flow === "scan" && firstTo("Running") !== null);
  const resultStage: StageId | null = job.result_summary == null ? null
    : failure && failure.stage === "pre" ? "pre" : "exec";
  const failStep = failure ? steps.find((s) => s.id === failure.step) : undefined;
  const autoOpen = failure && failStep && failStep.phase !== null && failStep.logAvailable
    && (failure.evidence === "submit_prefix" || failure.evidence === "reason_prefix" || failure.evidence === "from_state")
    && (job.state === "Failed" || job.state === "Rejected" || job.state === "TimedOut")
    && failure.stage !== "gate"
    ? { stage: failure.stage as StageId, key: `log:${failStep.phase}` } : null;

  return {
    flow, steps,
    pre: stage("pre"), gate: steps.find((s) => s.id === "confirm") ?? null, exec: stage("exec"),
    terminal, known, failure, executionStarted, resultStage, autoOpen, refs,
  };
}

// 단계 소요(같은 텍스트 노드에 접두를 붙여 쓰는 쪽은 호출측). 0초는 정상값, 모르면 null.
export function stepDuration(s: { start: string | null; end: string | null }): string | null {
  return spanText(s.start, s.end);
}

// 출력 묶기: 단계마다 [로그(있을 때)] + [그 phase 의 아티팩트(정해진 순서 → 나머지 이름순)].
// 출력은 **상태와 무관하게** 데이터가 있으면 그 단계 행에 그린다(사전 점검이 거부된 잡에 execution/* 가 남아
// 있으면 ② 실행 행에 보인다 -- 숨기면 남은 파일이 화면에서 사라진다). 흐름에 없는 phase·PHASES 밖 phase 는
// 「기타 출력」으로 모은다(조용히 버리지 않는다).
const ARTIFACT_ORDER = ["stdout.log", "stderr.log", "summary.json", "dscan-report.json", "rank.sh", "mpi-hostfile"];
function artifactRank(name: string): number {
  const i = ARTIFACT_ORDER.indexOf(name);
  return i === -1 ? ARTIFACT_ORDER.length : i;
}
export interface StepOutputs {
  byStep: Partial<Record<StepId, OutputItem[]>>;
  other: { phase: string; items: OutputItem[] }[];
}
export function normEntries(raw: unknown): ArtifactEntry[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((e): e is ArtifactEntry =>
    isPlainObject(e) && typeof e.phase === "string" && typeof e.name === "string");
}
export function outputsByStep(model: JobStagesModel, rawEntries: unknown): StepOutputs {
  const entries = normEntries(rawEntries);
  const flowSteps = new Set<string>(model.steps.map((s) => s.phase).filter((p): p is string => p !== null));
  const buckets = new Map<string, OutputItem[]>();
  const push = (phase: string, item: OutputItem) => {
    const list = buckets.get(phase) ?? [];
    list.push(item);
    buckets.set(phase, list);
  };
  for (const s of model.steps) {
    if (s.phase !== null && s.logAvailable) push(s.phase, { kind: "log", key: `log:${s.phase}`, phase: s.phase });
  }
  // 흐름 밖 phase 의 ref 도 로그 칩으로 남긴다(옛 JobViewer 는 ref 가 있는 모든 phase 에 탭을 만들었다).
  for (const p of Object.keys(model.refs)) {
    if (!flowSteps.has(p)) push(p, { kind: "log", key: `log:${p}`, phase: p });
  }
  const sorted = [...entries].sort((a, b) =>
    artifactRank(a.name) - artifactRank(b.name) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  for (const e of sorted) {
    push(e.phase, {
      kind: "artifact", key: `artifact:${e.phase}/${e.name}`, phase: e.phase, name: e.name,
      size: finiteOrNull(e.size),
    });
  }
  const byStep: StepOutputs["byStep"] = {};
  const other: StepOutputs["other"] = [];
  for (const [phase, items] of buckets) {
    if (flowSteps.has(phase)) byStep[phase as StepId] = items;
    else other.push({ phase, items });
  }
  other.sort((a, b) => (a.phase < b.phase ? -1 : a.phase > b.phase ? 1 : 0));
  return { byStep, other };
}

// phase 범위 진단 이벤트 → 그 단계 행의 주석. payload.job_id 가 이 잡이면 붙이고, job_id 없이 phase 만 있으면 요청의
// 잡이 정확히 1개일 때만 붙인다(어느 잡의 일인지 모르는 이벤트를 아무 잡에나 붙이지 않는다).
export function annotationsFor(events: unknown, job: DataJob, jobCount: number, flow: Flow): Partial<Record<StepId, DiagEvent[]>> {
  const out: Partial<Record<StepId, DiagEvent[]>> = {};
  if (!Array.isArray(events)) return out;
  for (const e of events) {
    if (!isPlainObject(e) || !isPlainObject(e.payload)) continue;
    const p = e.payload;
    if (!isPhase(p.phase)) continue;
    const jid = p.job_id;
    if (jid !== undefined && jid !== null) {
      if (jid !== job.job_id) continue;
    } else if (jobCount !== 1) continue;
    const step = fold(p.phase, flow);
    (out[step] ??= []).push(e as unknown as DiagEvent);
  }
  return out;
}

// 화면 낭독(live region)용 구획 상태 스냅숏과 변화 문구. 첫 로드에는 알리지 않고 바뀔 때만 말한다.
// job_id·영문 상태 원문은 넣지 않는다(스펙 C1·C4: 정확 일치 단언과 e2e 개수 계약).
export interface StageSnapshot { pre: StepStatus; gate: StepStatus | null; exec: StepStatus }
export function stageSnapshot(m: JobStagesModel): StageSnapshot {
  return { pre: m.pre.status, gate: m.gate?.status ?? null, exec: m.exec.status };
}
export function stageAnnouncement(prev: StageSnapshot | undefined, next: StageSnapshot, flow: Flow): string | null {
  if (prev === undefined) return null;
  // 실행 쪽 변화를 먼저 말한다(사용자가 기다리는 쪽이다).
  if (prev.exec !== next.exec) return `${stageTitle("exec", flow)} 단계: ${STATUS_LABEL[next.exec]}`;
  if (next.gate !== null && prev.gate !== next.gate) return `${stageTitle("gate", flow)} 단계: ${STATUS_LABEL[next.gate]}`;
  if (prev.pre !== next.pre) return `${stageTitle("pre", flow)} 단계: ${STATUS_LABEL[next.pre]}`;
  return null;
}
