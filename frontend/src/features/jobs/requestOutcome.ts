// 요청 상세 맨 위 결과 배너(OutcomeCard)와 지표 4칸(KPI)의 순수 판정(2026-10-08 재설계). 배너는 첫 화면에서
// "무슨 일이 있었나 · 왜 · 무엇을 대상으로 · 다음에 무엇을 하나" 를 답한다 -- 그 문장을 컴포넌트에 흩지 않고 여기
// 한 곳에서 정한다(잡 카드의 단계 모델 stageModel 과 같은 근거를 읽어 둘이 서로 다른 이야기를 하지 않게).
//
// 제목·문장은 한국어만 쓴다. 영문 상태 문자열은 StatusPill 의 몫이다(e2e E6 의 Succeeded 배지 개수 계약, 스펙 C1).
import { REQUEST_TERMINAL_STATES, isTerminal } from "../../lib/jobState";
import { kstStamp } from "../../lib/datetime";
import type { DataJob, RequestDetail } from "../../lib/types";
import { countText, finiteOrNull, humanBytes, isPlainObject, msText, spanText } from "./format";
import { STEP_COPY, isEstimated, normTransitions, type JobStagesModel, type StageId, type StepId } from "./stageModel";

export type Tone = "ok" | "bad" | "busy" | "action" | "neutral";
export type OutcomeIcon = "check" | "x" | "clock" | "loader" | "bell" | "ban" | "hourglass" | "help";
// confirm = 배너의 「컨펌하러 가기」(그 잡 관문 줄의 「작업 컨펌」으로 스크롤·포커스). 컨펌 창 자체는 배너에 없다.
export type NextAction = "confirm" | "showPreview" | "liveLog" | "failLog" | "newJob" | "cancelRequest";
export interface NavTarget { jobId: string; stage: StageId; key: string }

export interface Outcome {
  tone: Tone; icon: OutcomeIcon; title: string; subtitle: string | null;
  focus: number | null;
  next: {
    text: string | null;
    batchNotice: boolean;     // 배치 자식 ConfirmPending: 기존 배치 안내 <p> 그대로(스펙 C10)
    expiry: boolean;          // 단건 ConfirmPending: 유효기간 줄(지금 시각이 필요해 컴포넌트가 그린다)
    groupWarning: boolean;    // 보조 그룹 삭제 경고(ConfirmDialog.groupDeleteWarning 과 같은 판정)
    actions: NextAction[];
  } | null;
  // 「작업 컨펌」 버튼(ConfirmDialog)을 누가 갖나: 비배치 ConfirmPending 잡이 하나라도 있으면 **늘 각 잡의 관문 줄**
  // (잡마다 정확히 하나), 배치 자식이거나 컨펌 대기 잡이 없으면 아무도(서버 409 batch_child_confirm_via_batch -- 배치
  // 확인 1회가 자식 전부를 대표한다). 배너는 컨펌 창을 갖지 않는다(리뷰 N5): 예전엔 초점(다른 잡의 상태)에 따라 창이
  // 배너↔관문 줄로 옮겨 다녀, 폴링으로 다른 잡이 실패하면 열어 둔 컨펌 창이 사라졌다. 단건 초점이면 배너는 그 관문
  // 줄로 데려가는 「컨펌하러 가기」(NextAction "confirm")만 둔다.
  confirmOwner: "gate" | "none";
  liveTarget: NavTarget | null;
  failTarget: NavTarget | null;
}

const FAIL_STATES = new Set(["Failed", "Rejected", "TimedOut"]);
const VERB: Record<string, string> = {
  Failed: "실패했습니다", Rejected: "거부되었습니다", TimedOut: "시간이 초과되었습니다",
};
// 「…을/를 시작하지 못했습니다」의 목적격.
const START_OBJ: Record<StepId, string> = {
  preflight: "사전 점검을", preview: "미리보기를", confirm: "작업 컨펌을",
  exec_preflight: "실행 직전 재점검을", execution: "실행을",
};

type Cat = "fail" | "confirm" | "live" | "cancel" | "expired" | "ok";
function categoryOf(state: string): Cat {
  if (FAIL_STATES.has(state)) return "fail";
  if (state === "ConfirmPending") return "confirm";
  if (state === "Cancelled") return "cancel";
  if (state === "PreviewExpired") return "expired";
  if (state === "Succeeded") return "ok";
  return "live";                 // 비종단 + 모르는 상태(거짓 종단 금지)
}
// 잡이 여럿이면 사람이 가장 먼저 봐야 할 잡을 초점으로: 실패 > 컨펌 대기 > 진행 > 취소 > 만료 > 성공.
const CAT_ORDER: Cat[] = ["fail", "confirm", "live", "cancel", "expired", "ok"];
export function focusJob(jobs: DataJob[]): number | null {
  if (jobs.length === 0) return null;
  for (const c of CAT_ORDER) {
    const i = jobs.findIndex((j) => categoryOf(j.state) === c);
    if (i !== -1) return i;
  }
  return 0;
}

const isRequestTerminal = (s: string) => REQUEST_TERMINAL_STATES.has(s);
const lastTerminalAt = (job: DataJob): string | null => {
  const t = [...normTransitions(job.transitions)].reverse().find((x) => isTerminal(x.to_state));
  return t && typeof t.at === "string" ? t.at : null;
};

function groupWarningOf(job: DataJob): boolean {
  if (job.worker_pool?.identity?.supplementary_gids_status !== "applied") return false;
  return job.operation === "rm" || job.options?.delete === true;
}

// jobsLoading: 잡 목록을 아직 모르는데 실패 문구도 없다(캐시에 데이터 없는 실패가 남은 채 다시 들어와 재조회 중 --
// RequestDetail 의 잡 구획이 중립 골격 「작업 정보를 불러오는 중…」을 그리는 바로 그 경우). 이때 배너가 「불러오지
// 못해…」라고 하면 한 화면이 실패와 로딩을 동시에 말한다(리뷰 3차) -- 중립 문구로 바꾼다.
export function deriveOutcome(req: RequestDetail, jobs: DataJob[] | null, models: JobStagesModel[],
                              opts: { jobsLoading?: boolean } = {}): Outcome {
  const base = { focus: null, next: null, confirmOwner: "none" as const, liveTarget: null, failTarget: null, subtitle: null };
  // 잡 모름(잡 조회 실패·재조회 중): 요청 상태만으로 말하고 단계 이야기는 하지 않는다.
  if (jobs === null) {
    const subtitle = opts.jobsLoading === true ? "작업 정보를 불러오는 중입니다"
      : "작업 정보를 불러오지 못해 단계별 결과를 보일 수 없습니다";
    if (!isRequestTerminal(req.state)) return { ...base, tone: "busy", icon: "clock", title: "요청을 처리하고 있습니다", subtitle };
    if (req.state === "Succeeded") return { ...base, tone: "ok", icon: "check", title: "요청이 완료되었습니다", subtitle };
    if (req.state === "Cancelled") return { ...base, tone: "neutral", icon: "ban", title: "요청이 취소되었습니다", subtitle };
    if (req.state === "Conflict") return { ...base, tone: "bad", icon: "x", title: "요청이 다른 요청과 겹쳐 실행되지 않았습니다", subtitle };
    return { ...base, tone: "bad", icon: "x", title: `요청이 ${VERB[req.state] ?? "끝났습니다"}`, subtitle };
  }
  const next = (text: string | null, actions: NextAction[], extra: Partial<NonNullable<Outcome["next"]>> = {}) =>
    ({ text, actions, batchNotice: false, expiry: false, groupWarning: false, ...extra });

  if (jobs.length === 0) {
    if (!isRequestTerminal(req.state)) {
      return { ...base, tone: "busy", icon: "clock", title: "작업을 준비하고 있습니다",
        next: next("플래너가 실행 노드를 고르면 작업이 만들어집니다 — 이 화면은 자동으로 갱신됩니다.", ["cancelRequest"]) };
    }
    if (req.state === "Rejected") {
      return { ...base, tone: "bad", icon: "x", title: "계획 단계에서 거부되었습니다",
        next: next("사유를 해소한 뒤 다시 제출하세요.", ["newJob"]) };
    }
    if (req.state === "Conflict") {
      return { ...base, tone: "bad", icon: "x", title: "같은 대상의 다른 요청과 겹쳐 실행되지 않았습니다",
        next: next("진행 중인 요청이 끝난 뒤 다시 제출하세요.", ["newJob"]) };
    }
    if (req.state === "Cancelled") {
      return { ...base, tone: "neutral", icon: "ban", title: "작업이 만들어지기 전에 취소되었습니다" };
    }
    return { ...base, tone: "neutral", icon: "help", title: "작업 없이 종료되었습니다" };
  }

  const fi = focusJob(jobs)!;
  const job = jobs[fi];
  const m = models[fi];
  const cat = categoryOf(job.state);
  const sameCat = jobs.filter((j) => categoryOf(j.state) === cat).length;
  const tail = jobs.length >= 2 ? ` (작업 ${jobs.length}개 중 ${sameCat}개)` : "";
  const op = req.operation;
  const scan = m.flow === "scan";
  const endAt = lastTerminalAt(job) ?? req.completed_at ?? null;
  const target = (stage: StageId, phase: string): NavTarget => ({ jobId: job.job_id, stage, key: `log:${phase}` });
  const running = m.steps.find((s) => s.status === "running" && s.phase !== null && s.logAvailable);
  const liveTarget = running && running.stage !== "gate" ? target(running.stage, running.phase!) : null;
  const failTarget = m.autoOpen ? { jobId: job.job_id, ...m.autoOpen } : null;
  const waitText = scan ? "사전 점검이 끝나면 바로 실행됩니다 — 이 화면은 자동으로 갱신됩니다."
    : "미리보기가 끝나면 컨펌을 요청합니다 — 이 화면은 자동으로 갱신됩니다.";
  const execText = "실행이 끝나면 결과가 여기에 표시됩니다 — 이 화면은 자동으로 갱신됩니다.";
  const withLive = (acts: NextAction[]) => (liveTarget ? ["liveLog" as const, ...acts] : acts);
  // 「작업 컨펌」 소유는 초점 잡의 갈래와 무관하다(리뷰 V2·N5) -- 실패 > 컨펌 대기 순이라 실패 잡이 초점이어도 컨펌을
  // 기다리는 잡은 자기 관문 줄에서 컨펌할 수 있어야 하고, 초점이 바뀌어도 그 버튼(과 열린 창)이 옮겨 가면 안 된다.
  const pendingConfirm = req.batch_id ? 0 : jobs.filter((j) => j.state === "ConfirmPending").length;
  const confirmOwner: Outcome["confirmOwner"] = pendingConfirm === 0 ? "none" : "gate";
  const o = { ...base, focus: fi, liveTarget, failTarget, confirmOwner };

  if (!m.known) {
    return { ...o, tone: "neutral", icon: "help", title: `알 수 없는 상태입니다${tail}` };
  }
  // 다음 단계 제출 보류(D2, LDAP 재확인 불가). 상태는 그대로라 "사전 점검 중" 처럼 끝난 단계가 도는 것으로 말하면
  // 거짓이다(리뷰 V9). 진행 중인 로그도 없다(앞 파드는 끝났고 다음 파드는 아직 없다).
  if (m.hold) {
    const n = m.hold.attempt !== null && m.hold.max !== null ? ` (LDAP 재확인 불가 ${m.hold.attempt}/${m.hold.max})` : "";
    return { ...o, tone: "busy", icon: "clock", title: `${STEP_COPY[m.hold.phase]} 제출을 보류하고 있습니다${n}${tail}`,
      next: next("LDAP 에 연결되지 않아 보조 그룹 권한을 다시 확인하지 못했습니다 — 약 1분 간격으로 다시 시도하며, "
        + "모두 실패하면 작업이 중단됩니다. 이 화면은 자동으로 갱신됩니다.", []) };
  }
  switch (job.state) {
    case "Pending":
      return { ...o, tone: "busy", icon: "loader", title: `작업이 실행을 기다리고 있습니다${tail}`, next: next(waitText, []) };
    case "Preflight":
      return { ...o, tone: "busy", icon: "loader", title: `사전 점검 중입니다${tail}`, next: next(waitText, withLive([])) };
    case "PreviewRunning":
      return { ...o, tone: "busy", icon: "loader", title: `미리보기(dry-run)를 실행하고 있습니다${tail}`,
        next: next(waitText, withLive([])) };
    case "Executing":
    case "Running": {
      // 실행 vcjob 이 Volcano 큐·gang 대기 중(RUNNING 관측 전) -- 「실행 중」이라고 하면 큐 시간이 실행 시간처럼 읽힌다(리뷰 V8).
      if (m.steps.find((s) => s.id === "execution")?.queued) {
        return { ...o, tone: "busy", icon: "clock", title: `실행 대기열에서 기다리고 있습니다${tail}`,
          next: next("실행 파드들이 함께 배치될 자원을 기다리고 있습니다(스케줄 대기) — 배치되면 실행이 시작되고, "
            + "이 화면은 자동으로 갱신됩니다.", []) };
      }
      const recheck = m.steps.find((s) => s.id === "exec_preflight")?.status === "running";
      return { ...o, tone: "busy", icon: "loader",
        title: `${recheck ? "실행 직전 재점검 중입니다" : "실행 중입니다"}${tail}`, next: next(execText, withLive([])) };
    }
    case "ConfirmPending": {
      if (req.batch_id) {
        return { ...o, tone: "action", icon: "bell", title: `컨펌을 기다리고 있습니다${tail}`,
          next: next(null, ["showPreview"], { batchNotice: true }) };
      }
      if (sameCat >= 2) {
        return { ...o, tone: "action", icon: "bell", title: `컨펌을 기다리는 작업이 ${sameCat}개 있습니다`,
          next: next("각 작업의 「작업 컨펌」 줄에서 진행하세요.", []) };
      }
      // "confirm" = 「컨펌하러 가기」(관문 줄의 「작업 컨펌」으로 이동). 창은 관문 줄에만 있다(confirmOwner 참고).
      return { ...o, tone: "action", icon: "bell", title: `컨펌을 기다리고 있습니다${tail}`,
        next: next("미리보기 결과를 검토한 뒤 컨펌하면 실제 실행이 시작됩니다.", ["confirm", "showPreview"],
          { expiry: true, groupWarning: groupWarningOf(job) }) };
    }
    case "Succeeded":
      return { ...o, tone: "ok", icon: "check", title: `작업이 완료되었습니다${tail}`,
        subtitle: `${endAt ? `${kstStamp(endAt)} 완료 · ` : ""}${scan
          ? "사전 점검 → 실행을 마쳤습니다" : "사전 점검 → 미리보기 → 컨펌 → 실행을 모두 마쳤습니다"}` };
    case "PreviewExpired":
      return { ...o, tone: "action", icon: "hourglass", title: `미리보기가 만료되어 실행되지 않았습니다${tail}`,
        next: next("같은 조건으로 다시 제출하세요 — 데이터는 변경되지 않았습니다.", ["newJob"]) };
  }
  const ended = endAt ? `${kstStamp(endAt)} 종료` : null;
  const f = m.failure;
  if (job.state === "Cancelled") {
    const where = !f ? "작업이 취소되었습니다"
      : f.queued ? `${STEP_COPY[f.step]} 대기 중에 취소되었습니다`
      : f.notStarted ? (f.step === "preflight" ? "시작 전에 취소되었습니다" : `${STEP_COPY[f.step]} 전에 취소되었습니다`)
      : f.step === "confirm" ? "컨펌 전에 취소되었습니다"
      : `${STEP_COPY[f.step]} 중에 취소되었습니다`;
    // 스케줄 대기 중 취소: 서버가 마지막으로 본 것은 대기였지만 폴링 사이에 시작됐을 수 있다 -- "시작됐다면" 까지만 말한다.
    const text = f?.queued
      ? (op === "scan" ? "스케줄 대기 중에 취소되었습니다 — scan 은 데이터를 바꾸지 않습니다."
        : `스케줄 대기 중에 취소되었습니다 — 취소 직전에 실행이 시작됐다면 일부 파일이 이미 ${op === "rm" ? "삭제" : "복사"}됐을 수 있습니다.`)
      : m.executionStarted
      ? (op === "rm" ? "실행 중에 취소되어 일부 파일이 이미 삭제됐을 수 있습니다."
        : op === "sync" ? "실행 중에 취소되어 일부 파일이 이미 복사됐을 수 있습니다."
        : "실행 중에 취소되었습니다 — scan 은 데이터를 바꾸지 않습니다.")
      : "실행 전에 취소되어 데이터는 변경되지 않았습니다.";
    return { ...o, tone: "neutral", icon: "ban", title: `${where}${tail}`, subtitle: ended, next: next(text, ["newJob"]) };
  }
  // 실패류(Failed·Rejected·TimedOut).
  const verb = VERB[job.state] ?? "실패했습니다";
  const failLog: NextAction[] = failTarget ? ["failLog"] : [];
  // 실패 잡이 초점을 가져가도 다른 잡이 컨펌을 기다리면 그 일을 배너에서 놓치지 않게 덧붙인다(버튼은 각 관문 줄).
  const others = confirmOwner === "gate" && job.state !== "ConfirmPending"
    ? ` 다른 작업 ${pendingConfirm}개가 컨펌을 기다립니다 — 각 작업의 「작업 컨펌」 줄에서 진행하세요.` : "";
  const bad = (title: string, text: string, actions: NextAction[]): Outcome =>
    ({ ...o, tone: "bad", icon: "x", title: `${title}${tail}`, subtitle: ended, next: next(`${text}${others}`, actions) });
  // 관문 문구는 파드 로그가 아니라 사유와 진단 이벤트를 가리킨다 -- 그 단계 파드는 돌지 않았고(_fail_closed 는 박제하지
  // 않는다) 막힌 이유(노드·신원·권한)는 이벤트에 남는다.
  const gateNext = "사유와 아래 「진단 이벤트」를 확인한 뒤 다시 제출하세요. 데이터는 변경되지 않았습니다.";
  if (f?.evidence === "gate_ambiguous") {
    // 노드 제외는 앞 파드의 스케줄 대기 재검사에서도, 다음 제출 직전에서도 나온다 -- 단계를 단정하지 않는다.
    return bad(`작업이 ${verb}`, `실행 노드 점검에서 막혀 중단되었습니다 — ${gateNext}`, ["newJob"]);
  }
  if (f?.evidence === "base_ambiguous") {
    // 그룹 잡의 작업 기록 저장소(base) 권한 거부 -- 사전 점검 파드 마커인지 다음 제출 직전 관문인지 기록만으로 모른다
    // (관문 이벤트가 아직 안 왔거나 보존 기한에 지워졌다). 단계·로그를 가리키지 않고, 고칠 사람(관리자)을 말한다.
    return bad(`작업이 ${verb}`,
      "작업 기록(artifact) 저장소 권한 점검에서 막혀 중단되었습니다 — 사유를 관리자에게 전달한 뒤 다시 제출하세요. 데이터는 변경되지 않았습니다.",
      ["newJob"]);
  }
  if (!f || isEstimated(f.evidence)) {
    // 추정이면 단계를 단정하지 않는다(전이 기록이 없어 "OO 단계에서 중단" 이 거짓일 수 있다).
    return bad(`작업이 ${verb}`, "아래 단계별 로그에서 원인을 찾으세요.", []);
  }
  if (f.submitFailed) {
    return bad(`${START_OBJ[f.step]} 시작하지 못했습니다`,
      `작업 파드를 만들지 못했습니다 — 「${f.step} 로그」의 거부 원문을 관리자에게 전달하세요. 데이터는 변경되지 않았습니다.`,
      failLog);
  }
  if (f.queued) {
    // 스케줄 대기 중 관문(노드 제외·신원 변경)이 끊었다 -- 파드가 돈 적이 없어 「실행 로그·stderr.log」도, "이미
    // 복사/삭제됐을 수 있다" 도 거짓이다(리뷰 V8).
    return bad(`${STEP_COPY[f.step]} 대기 중에 ${verb}`,
      `${STEP_COPY[f.step]} 파드가 스케줄되기 전에 점검에서 막혀 중단되었습니다 — ${gateNext}`, ["newJob"]);
  }
  if (f.notStarted) {
    if (f.step === "preflight") {
      return bad(`시작 전에 ${verb}`, "사유를 해소한 뒤 다시 제출하세요. 데이터는 변경되지 않았습니다.", ["newJob"]);
    }
    // 앞 단계 파드는 통과했고(또는 컨펌 직후) 다음 파드를 만들기 전에 막혔다 -- 통과한 단계를 탓하지 않고, 없는 로그를
    // 가리키지 않는다(리뷰 V6·V7).
    const why = f.step === "exec_preflight"
      ? "컨펌 뒤 재점검 파드를 만들기 전에 중단되어 이 단계의 로그는 없습니다"
      : "앞 단계는 통과했지만 다음 단계를 제출하기 전 점검에서 막혔습니다";
    return bad(`${START_OBJ[f.step]} 시작하기 전에 ${verb}`, `${why} — ${gateNext}`, ["newJob"]);
  }
  switch (f.step) {
    case "preflight":
      return bad(`사전 점검 단계에서 ${verb}`,
        "사전 점검 로그에서 실패한 검사를 확인하고 고친 뒤 다시 제출하세요. 데이터는 변경되지 않았습니다.",
        [...failLog, "newJob"]);
    case "preview":
      return bad(`미리보기 단계에서 ${verb}`,
        "미리보기 로그와 stderr.log 에서 원인을 찾으세요. 미리보기는 dry-run 이라 데이터는 변경되지 않았습니다.",
        [...failLog, "newJob"]);
    case "confirm":
      return bad(`작업 컨펌 단계에서 ${verb}`,
        "사유를 해소한 뒤 다시 제출하세요. 실행 전이라 데이터는 변경되지 않았습니다.", ["newJob"]);
    case "exec_preflight":
      return bad(`실행 직전 재점검 단계에서 ${verb}`,
        "미리보기 이후 경로나 권한이 바뀌었을 수 있습니다 — 재점검 로그를 보고 다시 제출하세요. 데이터는 변경되지 않았습니다.",
        [...failLog, "newJob"]);
    case "execution":
      return bad(`실행 단계에서 ${verb}`,
        op === "scan" ? "실행 로그에서 원인을 찾으세요. scan 은 데이터를 바꾸지 않습니다."
          : `실행 로그와 stderr.log 에서 원인을 찾으세요. 실패 전에 일부 파일이 이미 ${op === "rm" ? "삭제" : "복사"}됐을 수 있습니다.`,
        failLog);
  }
}

// --- KPI ------------------------------------------------------------------------------------------------------------
export interface KpiTile {
  key: string; label: string; value: string; sub: string | null;
  // 비종단 수행시간: 컴포넌트가 이 시각부터 1초 틱으로 다시 센다(value 는 nowMs 기준 첫 값).
  elapsedFrom?: string;
}

// 실행 결과(result_summary)의 수치. 결과가 ② 실행 구획 몫일 때만 센다 -- 미리보기 실패 때의 result_summary 는
// 미리보기 summary 라(stepper _surface_failed_artifact) "복사한 파일" 로 세면 거짓이다.
function execVal(job: DataJob, m: JobStagesModel, key: "files" | "bytes"): number | null {
  if (m.resultStage !== "exec" || !isPlainObject(job.result_summary)) return null;
  return finiteOrNull(job.result_summary[key]);
}
function previewVal(job: DataJob, key: "files" | "bytes"): number | null {
  return isPlainObject(job.preview_summary) ? finiteOrNull(job.preview_summary[key]) : null;
}
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

export function deriveKpi(req: RequestDetail, jobs: DataJob[] | null, models: JobStagesModel[], nowMs: number): KpiTile[] {
  const tiles: KpiTile[] = [];
  const op = req.operation;
  const list = jobs ?? [];
  if (list.length > 0 && (op === "sync" || op === "rm" || op === "scan")) {
    const fmt = { files: countText, bytes: (n: number) => humanBytes(n) };
    const labels = op === "sync" ? { files: "복사한 파일", bytes: "복사한 크기" }
      : op === "rm" ? { files: "삭제한 항목", bytes: "크기" }
      : { files: "스캔한 파일", bytes: "사용량" };
    const preLabels = op === "rm" ? { files: "삭제 대상", bytes: "대상 크기" } : { files: "복사 대상", bytes: "대상 크기" };
    const single = list.length === 1;
    const anyLive = list.some((j) => !isTerminal(j.state));
    const anyStarted = models.some((m) => m.executionStarted);
    for (const key of ["files", "bytes"] as const) {
      const vals = list.map((j, i) => execVal(j, models[i], key));
      const known = vals.filter((v): v is number => v !== null);
      const pvals = list.map((j) => previewVal(j, key));
      const pAll = pvals.every((v) => v !== null) ? sum(pvals as number[]) : null;
      if (known.length === vals.length) {
        tiles.push({ key, label: labels[key], value: fmt[key](sum(known)),
          sub: pAll !== null && op !== "scan" ? `미리보기 ${fmt[key](pAll)}` : null });
        continue;
      }
      // 실행 전 단건: 미리보기 값을 "대상" 라벨로(실행 결과인 척하지 않는다).
      if (single && op !== "scan" && !isTerminal(list[0].state) && !models[0].executionStarted && pvals[0] !== null) {
        tiles.push({ key, label: preLabels[key], value: fmt[key](pvals[0]!), sub: "미리보기 기준 · 실행 전" });
        continue;
      }
      // rm 은 도구가 바이트를 보고하지 않아(bytes: null 이 정상) 모르는 크기 칸을 아예 두지 않는다.
      if (op === "rm" && key === "bytes") continue;
      tiles.push({ key, label: labels[key], value: "—",
        sub: !single && known.length > 0 ? "일부 작업은 아직 집계 전"
          : anyLive ? "실행 후 집계"
          : !anyStarted ? "실행되지 않음" : null });
    }
  }
  // 수행시간: 종단이면 created_at → 마지막 요청 전이(없으면 updated_at), 비종단이면 지금까지 경과.
  const tr = normTransitions(req.transitions);
  const terminal = isRequestTerminal(req.state);
  const single = list.length === 1 ? models[0] : null;
  const execSpan = single ? spanText(single.exec.start, single.exec.end) : null;
  // 컨펌 대기 중엔 기계가 돌지 않는다 -- 「진행 중」이라 하면 사람이 기다린 시간을 실행 시간처럼 읽힌다(d164 실 화면).
  const awaitingHuman = list.some((j) => j.state === "ConfirmPending");
  const durSub = execSpan !== null ? `실행 단계 소요 ${execSpan}` : terminal ? null
    : awaitingHuman ? "컨펌 대기 중 (대기 시간 포함)" : "진행 중";
  if (terminal) {
    const end = tr.length ? tr[tr.length - 1].at : req.updated_at;
    tiles.push({ key: "duration", label: "수행시간", value: spanText(req.created_at, end) ?? "—", sub: durSub });
  } else {
    const ms = Date.parse(req.created_at);
    const d = Number.isNaN(ms) ? null : msText(nowMs - ms);
    // 시계가 서버보다 뒤라 지금은 음수여도 기준 시각은 넘긴다 -- 틱이 시계를 따라잡으면 그때부터 센다.
    tiles.push({ key: "duration", label: "수행시간", value: d === null ? "—" : `${d}째`, sub: durSub,
      elapsedFrom: Number.isNaN(ms) ? undefined : req.created_at });
  }
  // 제출 대기(슬라이스 17 정정): 요청 Pending → 첫 비-Pending 전이(플래너 픽업)의 지연이지 Volcano 큐 대기가 아니다.
  const pickup = tr.find((t) => t.to_state !== "Pending");
  tiles.push({ key: "submit_wait", label: "제출 대기", value: spanText(req.created_at, pickup?.at) ?? "—",
    sub: "플래너 배정까지" });
  return tiles;
}
