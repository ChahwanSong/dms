// 잡 한 개를 「① 사전 점검·미리보기 → (작업 컨펌) → ② 실행」 두 단계 구획으로 읽는 순수 모델(2026-10-08 요청 상세
// 재설계). 화면(JobStages)은 이 모델만 그린다 -- 상태·시각·실패 지점 판정을 컴포넌트 곳곳에 흩으면 같은 잡이
// 배너와 단계 행에서 서로 다른 이야기를 한다.
//
// 근거는 전부 **서버가 이미 보내는 값**이다: job.state·transitions·phase_refs·reason_code·exec_submitted_at·
// sched_wait_seconds, 그리고 요청 이벤트 중 이 잡의 것(payload.job_id). 모르는 것은 null 로 둔다(시각을 지어내지
// 않는다, 0초를 지어내지 않는다 -- null≠0). DB 가 신뢰 경계라 transitions·phase_refs·events 는 배열·객체가 아닐 수도
// 있다는 전제로 정규화한다(옛 M5 사고: 무방어 인덱싱이 화면을 죽였다).
//
// 파일 이름이 stageModel 인 이유(2026-10-08 리뷰): 예전 이름 jobStages.ts 는 컴포넌트 JobStages.tsx 와 대소문자만
// 달라, 대소문자를 가리지 않는 파일시스템(macOS·Windows 기본)에서 `import "./JobStages"` 가 이 순수 모듈(.ts 가 .tsx
// 보다 먼저 풀린다)로 잘못 풀렸다.
import { isTerminal } from "../../lib/jobState";
import type { ArtifactEntry, DataJob, DiagEvent, Transition } from "../../lib/types";
import { finiteOrNull, isPlainObject, spanText } from "./format";

export type StageId = "pre" | "exec";
export type StepId = "preflight" | "preview" | "confirm" | "exec_preflight" | "execution";
export type StepStatus =
  | "waiting" | "running" | "awaiting" | "done"
  | "failed" | "rejected" | "timed_out" | "cancelled" | "expired"
  | "skipped" | "unknown";
// 실패 지점을 무엇으로 알았나. deepest_ref·none·gate_ambiguous·base_ambiguous 는 **추정**이라 화면이 단계를 단정하지
// 않는다. gate = 단계 사이 관문(stepper._build_spec 의 제출 전 검사, 스케줄 전 재검사)의 fail-closed 사유로 정한 지점이다
// -- 그 단계의 파드는 돌지 않았으니 로그를 자동으로 열지 않는다(_fail_closed 가 ref 파드를 지우고 박제도 하지 않는다).
// base_ambiguous = 그룹 잡의 artifact_base_* 거부가 사전 점검 파드 마커인지 다음 제출 직전 관문인지 기록만으로 모른다
// (ALSO_POD_MARKER 주석).
export type Evidence =
  | "submit_prefix" | "reason_prefix" | "gate" | "gate_ambiguous" | "base_ambiguous" | "from_state" | "state"
  | "deepest_ref" | "none";
export const isEstimated = (e: Evidence): boolean =>
  e === "deepest_ref" || e === "none" || e === "gate_ambiguous" || e === "base_ambiguous";
export type Flow = "scan" | "previewed";

export interface StepModel {
  id: StepId; stage: StageId | "gate"; phase: string | null;        // confirm = null(파드가 없는 사람 관문)
  status: StepStatus;
  start: string | null; end: string | null;                         // ISO. 모르면 null
  actor: string | null;                                             // confirm 만: 컨펌한 주체
  logAvailable: boolean;
  submitFailed: boolean; notStarted: boolean;
  // 스케줄 대기: 제출됐지만 RUNNING 이 관측되기 전(비종단 실행), 또는 그 상태에서 끝났다(종단). 대기 시간을 실행
  // 소요로 세지 않는다.
  queued: boolean;
  // 제출 보류(D2: LDAP 재확인 불가로 이 단계 제출을 미루는 중). attempt·max 는 모르면 null(지어내지 않는다).
  held: { attempt: number | null; max: number | null } | null;
  schedWaitSec: number | null;                                      // execution 만. null=모름, 0=정상값
}
export interface StageModel {
  id: StageId; steps: StepModel[]; status: StepStatus;
  start: string | null; end: string | null;
}
export interface FailurePoint {
  step: StepId; stage: StageId | "gate"; evidence: Evidence; submitFailed: boolean; notStarted: boolean;
  // 그 단계의 파드·vcjob 이 스케줄 전(PENDING)에 멈췄다. 관문 사유(evidence gate)면 확정이고, 취소면 "서버가 마지막으로
  // 본 상태" 일 뿐이다(폴링 사이에 시작됐을 수 있다) -- 배너 문구가 둘을 가른다.
  queued: boolean;
}
// 다음 단계 제출 보류(stepper IdentityRecheckHeld). phase = 제출하려던 phase.
export interface SubmitHold { phase: Phase; attempt: number | null; max: number | null }
export interface JobStagesModel {
  flow: Flow;
  steps: StepModel[];
  pre: StageModel; gate: StepModel | null; exec: StageModel;        // scan 이면 gate = null
  terminal: boolean; known: boolean;
  failure: FailurePoint | null;
  hold: SubmitHold | null;
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

// 단계 사이 관문의 fail-closed 사유(stepper._step_one 의 except 들 → _fail_closed). 전부 _build_spec 이 **다음 단계를
// 제출하기 직전**에 던진다 -- 그때 마지막 종단 전이의 from_state 단계(그 파드)는 이미 SUCCEEDED 였다. from_state 를
// 그대로 쓰면 통과한 단계를 실패로 칠하고 그 단계 로그를 실패 로그로 연다(2026-10-08 리뷰 V6). ldap_unavailable 도
// 여기다: 재확인 소진(IdentityRecheckExhausted)·카운터 기록 실패·보류 중 파드 소실(_preflight_reason 의
// held_pod_vanished) 모두 "앞 파드 통과 → 다음 제출 보류" 끝에서만 나온다. unknown_tool 은 넣지 않는다(어느 상태에서나
// 단계와 무관하게 끊는 변조 행 가드라 from_state 그대로가 맞다).
const SUBMIT_GATE = new Set([
  "identity_changed_at_step", "identity_missing_at_step", "ldap_unavailable", "ldap_not_configured",
  "privilege_not_requested", "chown_name_not_supported", "storage_missing_at_step",
  "artifact_base_not_traversable", "artifact_base_group_writable", "node_excluded_at_step",
]);
// 관문 사유이면서 preflight·exec_preflight 파드 스크립트의 마커(DMS_PREFLIGHT_REASON=, execution_manifests.PREFLIGHT_REASONS)
// 이기도 한 둘(2026-10-08 리뷰 N3). 마커 길은 그 파드가 **실패한** 것이라(_preflight_reason → _finalize(REJECTED)) 실패
// 지점은 from_state 그 단계이고 그 로그(박제 사본)에 마커가 있다. 관문 길(_step_one 의 ArtifactBaseUnsafeAtStep →
// _fail_closed)만 artifact_base_unsafe_at_step 이벤트(payload.job_id)를 남긴다 -- 그 이벤트가 있으면 관문이다.
// Executing 에선 대상 상태도 둘을 가른다: _fail_closed 는 실행 상태에서 Failed, 마커 길은 늘 Rejected(이벤트가
// 요청 이벤트 창(최신 100건) 밖으로 밀려도 Failed 면 관문이다).
// Preflight→Rejected 에서 이벤트가 없을 때(리뷰 3차): 관문은 _build_spec 이 **보조 gid 가 실린 잡에서만**
// (_raise_if_base_unsafe_for_groups) 돌린다 -- 그룹 없는 잡(키 부재·null·[])이면 마커 길이 확실하다. 그룹 잡이면 모른다:
// 이벤트가 아직 안 왔거나(잡 2초·요청 3초 폴링이 따로 돌아 잡 종단이 먼저 보인다) 보존 기한(DMS_EVENT_RETENTION_DAYS,
// 기본 30일)에 지워졌거나 창 밖으로 밀렸을 수 있다 -- 단계를 단정하지 않고 로그도 자동으로 열지 않는다(base_ambiguous).
// 단 둘이 다 맞으면 마커 길로 확정한다(markerSure, 리뷰 4차): (1) 요청 상세 응답의 요청이 종단이다 -- get_request 는
// 요청 행을 먼저 읽고 이벤트를 나중에 읽고, stepper 는 관문 이벤트를 _fail_closed(→ 요청 종단)보다 먼저 남긴다. 그러니
// 종단 요청 응답엔 관문 이벤트가 이미 커밋돼 있다(실시간 틈이 닫힌다). (2) 이 잡의 identity_groups_checked
// phase=preflight 가 응답에 있다 -- 그룹 잡의 사전 점검 제출 때 남고 관문 이벤트보다 **오래됐다**. 보존 삭제(나이순)·
// 최신 100건 창(오래된 쪽부터 버림) 모두 오래된 것부터 지우니, 그것이 남았으면 그보다 새 관문 이벤트도 남았다.
// 남는 한계(수용): observability.record_event 는 예외를 삼킨다 -- 관문 이벤트 INSERT 만 조용히 실패하고 바로 뒤 종단
// UPDATE 는 성공한 드문 경우엔 관문을 마커로 단정한다(통과한 사전 점검 로그가 열린다; 사유 문구는 같다).
const ALSO_POD_MARKER = new Set(["artifact_base_not_traversable", "artifact_base_group_writable"]);
// 이 잡에 보조 gid 가 실렸나(stepper._build_spec 의 `if gids:` 와 같은 판정 -- None·[] 은 없음). DB 가 신뢰 경계라 모양을
// 가정하지 않는다(배열이 아니면 없음 -- 백엔드는 모양이 틀린 신원을 base 관문 전에 identity_missing_at_step 으로 끊는다).
function hasSupplementaryGids(job: DataJob): boolean {
  const wp: unknown = job.worker_pool;
  if (!isPlainObject(wp) || !isPlainObject(wp.identity)) return false;
  const gids = wp.identity.supplementary_gids;
  return Array.isArray(gids) && gids.length > 0;
}
// 제출된 파드·vcjob 이 아직 스케줄 전(PENDING)일 때 폴링이 던지는 사유(_raise_if_blocked·_note_queued_recheck·
// _run_queued_rechecks). node_excluded_at_step 은 제출 전 관문과 둘 다라, from_state 만으로는 어느 쪽인지 모른다.
const QUEUE_GATE = new Set(["node_excluded_at_step", "identity_changed_at_step", "identity_missing_at_step"]);

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

interface LocateCtx {
  execQueued: boolean;               // 실행 vcjob 이 제출됐지만 RUNNING 관측 전(sched_wait_seconds 가 아직 null)
  deferred: ReadonlySet<string>;     // 이 잡에 identity_recheck_deferred(제출 보류)가 남은 phase
  baseGate: boolean;                 // 이 잡에 artifact_base_unsafe_at_step(제출 전 base 정적 관문)이 남았다
  groups: boolean;                   // 보조 gid 가 실린 잡(base 정적 관문이 도는 잡)
  markerSure: boolean;               // 관문 이벤트가 없다는 사실을 믿을 수 있다(ALSO_POD_MARKER 주석)
}

// 종단 실패의 실패 지점(위에서부터 처음 걸리는 규칙).
function locateFailure(job: DataJob, tr: Transition[], refs: Record<string, string>, flow: Flow,
                       ctx: LocateCtx): FailurePoint {
  const mk = (step: StepId, evidence: Evidence, extra: Partial<FailurePoint> = {}): FailurePoint => {
    const s = fold(step, flow);
    return { step: s, stage: stageOfStep(s), evidence, submitFailed: false, notStarted: false, queued: false, ...extra };
  };
  if (job.state === "PreviewExpired") return mk("confirm", "state");
  // 제출 접두가 먼저다: preview 제출 실패는 Preflight→Failed 전이로 남고 scan 의 execution 제출 실패도 from_state 가
  // Preflight 라, from_state 만 보면 한 칸 앞을 가리킨다.
  const sfp = submitFailedPhase(job.reason_code);
  if (sfp !== null) return mk(sfp, "submit_prefix", { submitFailed: true });
  const prefix = typeof job.reason_code === "string" ? job.reason_code.split(":")[0] : "";
  if (REASON_PREFIX_STEP[prefix]) return mk(REASON_PREFIX_STEP[prefix], "reason_prefix");
  const terminalT = [...tr].reverse().find((t) => isTerminal(t.to_state)) ?? null;
  const from = terminalT?.from_state;
  // 파드 마커와 겹치는 base 사유는 관문 이벤트(또는 실행 상태의 Failed)가 있을 때만 관문이다(리뷰 N3) -- 아니면 그
  // 파드가 실패한 것이라 아래 from_state 로 간다(그 단계가 실패, 마커가 든 로그 자동 열림).
  // 다음 제출의 보류 기록(identity_recheck_deferred)이 있으면 앞 파드는 이미 SUCCEEDED 였다(보류는 그 뒤에만 생긴다) --
  // 그 파드의 마커일 수 없으니 관문이다(base 관문은 보류 중에도 매 틱 재확인보다 먼저 돈다).
  const preNextHeld = from === "Preflight" && ctx.deferred.has(flow === "scan" ? "execution" : "preview");
  const isGate = SUBMIT_GATE.has(prefix) && (!ALSO_POD_MARKER.has(prefix) || ctx.baseGate || preNextHeld
    || ((from === "Executing" || from === "Running") && job.state === "Failed"));
  // 그룹 잡의 Preflight→Rejected 는 이벤트 없이는 마커인지 관문인지 모른다(ALSO_POD_MARKER 주석) -- 사전 점검 행에 추정으로.
  if (ALSO_POD_MARKER.has(prefix) && !isGate && from === "Preflight" && ctx.groups && !ctx.markerSure) {
    return mk("preflight", "base_ambiguous");
  }
  // 단계 사이 관문(SUBMIT_GATE)은 from_state 보다 먼저다(리뷰 V6·V7·V8): 앞 단계 파드는 통과했고 막힌 것은 다음 제출이다.
  if (isGate) {
    // node_excluded_at_step 만 모호하다 -- 앞 단계 파드의 스케줄 대기(PENDING) 재검사도 같은 사유를 던진다. 다음 제출의
    // 보류 기록(identity_recheck_deferred)이 있으면 앞 파드는 이미 SUCCEEDED 였으니 모호하지 않다(보류는 그 뒤에만 생긴다).
    const queueOrSubmit = (next: StepId) => prefix === "node_excluded_at_step" && !ctx.deferred.has(next);
    if (from === "Preflight") {
      const next: StepId = flow === "scan" ? "execution" : "preview";
      return queueOrSubmit(next) ? mk("preflight", "gate_ambiguous") : mk(next, "gate", { notStarted: true });
    }
    if (from === "PreviewRunning" && QUEUE_GATE.has(prefix)) return mk("preview", "gate", { queued: true });
    if (from === "Executing" && !has(refs, "execution")) {
      // 재점검 파드 ref 도 없으면 컨펌 직후 그 파드를 만들기 전에 막혔다.
      if (!has(refs, "exec_preflight")) return mk("exec_preflight", "gate", { notStarted: true });
      return queueOrSubmit("execution") ? mk("exec_preflight", "gate_ambiguous") : mk("execution", "gate", { notStarted: true });
    }
    // 실행 vcjob 큐 대기 중 재검사가 끊었다 -- 앵커(exec_submitted_at)가 있고 RUNNING 관측(sched_wait_seconds)이 아직
    // 없을 때만 "대기 중" 이라고 단정한다. 앵커가 없는 옛 잡·한 번이라도 RUNNING 이었던 잡은 아래 from_state 그대로.
    if ((from === "Executing" || from === "Running") && has(refs, "execution") && QUEUE_GATE.has(prefix) && ctx.execQueued) {
      return mk("execution", "gate", { queued: true });
    }
  }
  // 다음 제출 보류(identity_recheck_deferred) 중의 취소(리뷰 N4): 보류는 앞 파드가 SUCCEEDED 한 뒤 다음 제출 직전에만
  // 생기므로, from_state 단계를 「취소됨」으로 칠하면 통과한 파드를 탓하고 보류 시간(분 단위)을 그 소요로 센다. 취소된
  // 것은 아직 제출되지 않은 다음 단계다(시작 전).
  const cancelledInHold = (next: StepId) => job.state === "Cancelled" && ctx.deferred.has(next);
  switch (from) {
    case "Pending": return mk("preflight", "from_state", { notStarted: true });
    case "Preflight": {
      const next: StepId = flow === "scan" ? "execution" : "preview";
      // 다음 단계 ref 가 이미 있으면 그 제출은 끝났다(_submit_* 가 set_phase_ref 뒤에 상태를 옮긴다 -- 그 틈에 취소가
      // 끼면 Preflight→Cancelled 에 ref 가 남는다, 리뷰 4차). 시작 전이라 말하면 executionStarted 와 배너가 엇갈린다.
      if (has(refs, next)) {
        return mk(next, "from_state", { queued: next === "execution" && job.state === "Cancelled" && ctx.execQueued });
      }
      return cancelledInHold(next) ? mk(next, "from_state", { notStarted: true }) : mk("preflight", "from_state");
    }
    case "PreviewRunning": return mk("preview", "from_state");
    case "ConfirmPending": return mk("confirm", "from_state");
    case "Executing":
    case "Running":
      // 스케줄 대기 중의 취소는 queued 로 표시하되 확정하지 않는다(배너가 "시작됐다면…" 으로 말한다).
      if (from === "Running" || has(refs, "execution")) {
        return mk("execution", "from_state", { queued: job.state === "Cancelled" && ctx.execQueued });
      }
      // 재점검 파드 ref 도 없으면 파드가 만들어지기 전에 끝났다(Pending 규칙의 짝, 리뷰 V7) -- 「소요」·로그를 지어내지 않게.
      if (!has(refs, "exec_preflight")) return mk("exec_preflight", "from_state", { notStarted: true });
      return cancelledInHold("execution") ? mk("execution", "from_state", { notStarted: true })
        : mk("exec_preflight", "from_state");
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

// 요청 이벤트 중 이 잡의 것(payload.job_id 로 한정 -- 같은 요청의 다른 잡 이벤트를 섞지 않는다). 서버는 오름차순이다.
interface JobEvent { event_type: string; at: unknown; payload: Record<string, unknown> }
function jobEvents(events: unknown, jobId: string): JobEvent[] {
  if (!Array.isArray(events)) return [];
  return events.filter((e): e is JobEvent =>
    isPlainObject(e) && typeof e.event_type === "string" && isPlainObject(e.payload) && e.payload.job_id === jobId);
}

// 상태별 다음 제출 phase(stepper._dispatch). 제출 보류(D2)는 이 제출의 _build_spec 에서만 생긴다.
function nextSubmit(state: string, refs: Record<string, string>, flow: Flow): Phase | null {
  switch (state) {
    case "Pending": return "preflight";
    case "Preflight": return flow === "scan" ? "execution" : "preview";
    case "Executing": return has(refs, "execution") ? null : has(refs, "exec_preflight") ? "execution" : "exec_preflight";
    default: return null;
  }
}

// 지금 다음 단계 제출을 보류 중인가(리뷰 V9). 보류(IdentityRecheckHeld)는 상태를 바꾸지 않아(touch 만) 전이만 보면
// 끝난 파드가 계속 "진행 중" 으로 보인다. 서버가 이미 보내는 이벤트로 판정한다(best-effort -- 계수하지 않는 보류
// (spacing·circuit·budget)는 이벤트가 없어 지금까지처럼 보인다):
//   - 이 잡의 identity_recheck_deferred / identity_groups_checked 중 마지막이 deferred 이고(통과 기록은 _judge 가
//     제출 직전에 남긴다 -- 그 뒤면 보류가 끝났다),
//   - 그 시각이 마지막 전이 이후이며(전이가 나중이면 낡은 기록이다),
//   - 그 phase 가 지금 상태의 다음 제출 phase 일 때.
function submitHold(state: string, tr: Transition[], refs: Record<string, string>, flow: Flow,
                    evs: JobEvent[]): SubmitHold | null {
  const want = nextSubmit(state, refs, flow);
  if (want === null) return null;
  const last = [...evs].reverse()
    .find((e) => e.event_type === "identity_recheck_deferred" || e.event_type === "identity_groups_checked");
  if (!last || last.event_type !== "identity_recheck_deferred" || last.payload.phase !== want) return null;
  const evAt = typeof last.at === "string" ? Date.parse(last.at) : NaN;
  if (Number.isNaN(evAt)) return null;
  const trAt = tr.map((t) => (typeof t.at === "string" ? Date.parse(t.at) : NaN)).filter((x) => !Number.isNaN(x));
  if (trAt.length > 0 && evAt < Math.max(...trAt)) return null;
  // 시도 횟수·상한은 서버 payload 그대로(상한을 4 로 박지 않는다 -- _LDAP_RECHECK_RETRIES 가 바뀌어도 맞게).
  return { phase: want, attempt: finiteOrNull(last.payload.attempt), max: finiteOrNull(last.payload.max_attempts) };
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

// events = 요청 상세 응답의 events(요청 단위, 최신 100건 오름차순). 이 잡의 것만 쓴다(제출 보류·관문 모호성 해소).
// opts.requestTerminal = **같은 응답**의 요청 상태가 종단인가(관문 이벤트 부재를 믿는 조건 -- ALSO_POD_MARKER 주석).
export function deriveJobStages(job: DataJob, events?: unknown,
                                opts: { requestTerminal?: boolean } = {}): JobStagesModel {
  const flow: Flow = job.operation === "scan" ? "scan" : "previewed";
  const order = orderOf(flow);
  const tr = normTransitions(job.transitions);
  const refs = normRefs(job.phase_refs);
  const terminal = isTerminal(job.state);
  const known = KNOWN_STATES.has(job.state);
  const evs = jobEvents(events, job.job_id);
  const execSubmitted = typeof job.exec_submitted_at === "string" && job.exec_submitted_at !== ""
    ? job.exec_submitted_at : null;
  // 실행 vcjob 이 제출됐지만 RUNNING 이 아직 한 번도 관측되지 않았다(sched_wait_seconds 는 _poll_execution 의 첫 RUNNING
  // 관측에서만 쓰인다 -- data_jobs.record_sched_wait). Volcano 큐·gang 대기는 길 수 있어 이 시간을 "실행 중" 으로 세면
  // 거짓이다(리뷰 V8). 앵커(exec_submitted_at)가 없는 옛 잡은 sched_wait 가 영영 null 이라 모른다 -- 지금까지처럼 다룬다.
  // 비교는 finiteOrNull === null(0 은 "같은 틱에 스케줄됨" 이라는 정상값이다).
  const execQueued = has(refs, "execution") && execSubmitted !== null && finiteOrNull(job.sched_wait_seconds) === null;
  const deferred = new Set<string>(evs.filter((e) => e.event_type === "identity_recheck_deferred")
    .map((e) => e.payload.phase).filter(isPhase));
  const baseGate = evs.some((e) => e.event_type === "artifact_base_unsafe_at_step");
  const failure = terminal && job.state !== "Succeeded"
    ? locateFailure(job, tr, refs, flow, {
      execQueued, deferred, baseGate, groups: hasSupplementaryGids(job),
      markerSure: opts.requestTerminal === true && evs.some((e) =>
        e.event_type === "identity_groups_checked" && e.payload.phase === "preflight"),
    }) : null;
  const hold = !terminal && known ? submitHold(job.state, tr, refs, flow, evs) : null;
  const idx = (id: StepId) => order.indexOf(id);

  const cur = !terminal && known ? currentStep(job.state, refs) : null;
  const curFolded = cur === null ? null : fold(cur, flow);
  const queuedNow = curFolded === "execution" && execQueued;
  // 보류 중인 제출이 지금 단계 다음이면 지금 단계는 끝났다(보류는 앞 파드가 SUCCEEDED 한 뒤에만 생긴다) -- 끝난 파드를
  // "진행 중" 으로 그리고 3초 라이브 로그를 돌리지 않게(리뷰 V9).
  const heldStep = hold === null ? null : fold(hold.phase, flow);
  const heldAfterCur = heldStep !== null && curFolded !== null && idx(heldStep) === idx(curFolded) + 1;
  const statusOf = (id: StepId): StepStatus => {
    if (job.state === "Succeeded") return "done";
    if (failure) {
      const d = idx(id) - idx(failure.step);
      return d < 0 ? "done" : d === 0 ? FAIL_STATUS[job.state] ?? "failed" : "skipped";
    }
    if (!known) return "unknown";
    if (curFolded === null) return "waiting";
    const d = idx(id) - idx(heldAfterCur ? heldStep! : curFolded);
    if (d < 0) return "done";
    if (d > 0 || heldAfterCur) return "waiting";
    if (id === "confirm") return "awaiting";
    return queuedNow ? "waiting" : "running";
  };

  // 시각(§4.5). 자기 전이(Executing→Executing)는 "재점검 통과 → 실행 vcjob 제출" 의 흔적이다.
  const firstTo = (s: string) => atOf(tr.find((t) => t.to_state === s && t.from_state !== s));
  const firstFrom = (s: string) => atOf(tr.find((t) => t.from_state === s && t.to_state !== s));
  const selfExec = atOf(tr.find((t) => t.from_state === "Executing" && t.to_state === "Executing"));
  const confirmT = tr.find((t) => t.from_state === "ConfirmPending" && t.to_state === "Executing") ?? null;
  const terminalT = [...tr].reverse().find((t) => isTerminal(t.to_state)) ?? null;
  const execStart = execSubmitted ?? (flow === "scan" ? firstTo("Running") : selfExec);
  const endIfFailedHere = (id: StepId) => (failure?.step === id ? atOf(terminalT) : null);
  const sfp = submitFailedPhase(job.reason_code);
  // 다음 단계가 시작 전에 끝났으면(관문이 제출을 막았거나 제출 보류 중 취소됐으면 -- notStarted) 앞 파드 단계가 언제
  // 끝났는지 모른다 -- 그 상태를 떠난 시각은 종단 전이라 보류 시간(≥180초일 수 있다)까지 소요에 섞인다(리뷰 V6·N4).
  // 사람 관문(confirm)은 파드가 아니라 그대로 둔다(confirm 의 끝은 gatedPrev 를 보지 않는다).
  const gatedPrev = failure?.notStarted === true ? order[idx(failure.step) - 1] ?? null : null;
  // 다음 제출이 보류됐던 적이 있으면(identity_recheck_deferred -- 보류가 풀려 다음 단계가 돌았어도) 앞 파드 단계의 끝을
  // 모른다(리뷰 3차, N4·V6·V9 의 형제): 그 상태를 떠난 시각은 보류가 풀린 뒤의 제출 시각이라 「소요」에 보류(분 단위)가
  // 섞이고, 보류 주석은 다음 단계 행에 붙는다 -- 카드가 기다린 시간을 통과한 파드의 기계 시간으로 탓한다. 같은 이유로
  // 재점검 파드 제출이 보류됐으면 재점검의 시작(파드 제출 시각은 기록되지 않는다)도 모른다. 구획 범위·「실행 단계 소요」
  // 는 벽시계 그대로다(보류는 그 구획 안의 일이다 -- 아래 stage()).
  // 그 파드 단계가 실패 지점이면(보류 뒤에는 생기지 않는 모양이지만) 끝은 종단 시각 그대로 둔다.
  const preNext: Phase = flow === "scan" ? "execution" : "preview";
  const preflightEndHeld = deferred.has(preNext) && failure?.step !== "preflight";
  const execPreflightStartHeld = flow === "previewed" && deferred.has("exec_preflight");
  const execPreflightEndHeld = flow === "previewed" && deferred.has("execution") && failure?.step !== "exec_preflight";

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
        end = gatedPrev === id || preflightEndHeld ? null : firstFrom("Preflight") ?? endIfFailedHere(id);
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
        start = execPreflightStartHeld ? null : atOf(confirmT);
        end = gatedPrev === id || execPreflightEndHeld ? null : execStart ?? endIfFailedHere(id);
        break;
      case "execution":
        start = execStart;
        end = ENDED.has(status) ? atOf(terminalT) : null;
        break;
    }
    // 파드가 없었던 단계(제출 실패·시작 전 종단) -- 시작 시각은 모름, 끝은 종단 전이. 「소요」를 지어내지 않는다
    // (리뷰 V7: 컨펌 시각 → 종단 시각을 재점검 소요로 그렸다).
    if (isSubmitFailed || isNotStarted) { start = null; end = atOf(terminalT); }
    // 아직 끝나지 않은 단계의 끝 시각은 그리지 않는다(다음 단계로 넘어간 흔적만으로 "끝" 을 단정하지 않게).
    if (!ENDED.has(status)) end = null;
    return {
      id, stage: stageOfStep(id), phase: id === "confirm" ? null : id,
      status, start, end, actor,
      logAvailable: id !== "confirm" && (has(refs, id) || sfp === id),
      submitFailed: isSubmitFailed, notStarted: isNotStarted,
      queued: (id === "execution" && queuedNow) || (failure?.queued === true && failure.step === id),
      held: hold !== null && heldStep === id ? { attempt: hold.attempt, max: hold.max } : null,
      schedWaitSec: id === "execution" ? finiteOrNull(job.sched_wait_seconds) : null,
    };
  });

  const stage = (sid: StageId): StageModel => {
    const ss = steps.filter((s) => s.stage === sid);
    const status = aggregate(ss.map((s) => s.status));
    // 구획 범위: 첫 단계의 시작 → 실제로 돈 마지막 단계의 끝. 사람이 컨펌을 기다린 시간은 관문에 있어 빠진다.
    // ② 의 시작은 컨펌 시각 그대로다 -- 재점검 제출 보류로 재점검 행의 시작만 지웠을 때도(보류는 ② 안의 일이다).
    const ran = ss.filter((s) => ENDED.has(s.status));
    const endStep = ran.length ? ran[ran.length - 1] : null;
    const first = ss[0]?.start ?? null;
    return {
      id: sid, steps: ss, status,
      start: first === null && sid === "exec" && execPreflightStartHeld && !ss[0]?.notStarted ? atOf(confirmT) : first,
      end: ENDED.has(status) || status === "skipped" ? endStep?.end ?? null : null,
    };
  };

  // "실행이 데이터를 만졌을 수 있다" 의 근거. 스케줄 대기 중(비종단)이거나 대기 중 관문이 끊었으면(확정) 실행 파드가
  // 돈 적이 없다. 대기 중 **취소**는 확정이 아니라(폴링 사이에 시작됐을 수 있다) 시작한 쪽으로 둔다.
  const neverRan = queuedNow || (failure?.queued === true && failure.evidence === "gate");
  const executionStarted = !neverRan && (job.state === "Succeeded" || has(refs, "execution") || execSubmitted !== null
    || selfExec !== null || (flow === "scan" && firstTo("Running") !== null));
  const resultStage: StageId | null = job.result_summary == null ? null
    : failure && failure.stage === "pre" ? "pre" : "exec";
  const failStep = failure ? steps.find((s) => s.id === failure.step) : undefined;
  // gate 근거는 화이트리스트 밖이다 -- 그 단계 파드는 돌지 않았고(통과한 앞 파드는 실패 지점이 아니다), _fail_closed 는
  // ref 파드를 지우면서 박제하지 않아 열어도 보일 로그가 없다.
  const autoOpen = failure && failStep && failStep.phase !== null && failStep.logAvailable
    && (failure.evidence === "submit_prefix" || failure.evidence === "reason_prefix" || failure.evidence === "from_state")
    && (job.state === "Failed" || job.state === "Rejected" || job.state === "TimedOut")
    && failure.stage !== "gate"
    ? { stage: failure.stage as StageId, key: `log:${failStep.phase}` } : null;

  return {
    flow, steps,
    pre: stage("pre"), gate: steps.find((s) => s.id === "confirm") ?? null, exec: stage("exec"),
    terminal, known, failure, hold, executionStarted, resultStage, autoOpen, refs,
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
