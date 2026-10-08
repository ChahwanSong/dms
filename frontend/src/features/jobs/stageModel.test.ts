import { expect, test } from "vitest";
import type { DataJob, Transition } from "../../lib/types";
import {
  annotationsFor, deriveJobStages, outputsByStep, stageAnnouncement, stageSnapshot, stepDuration, submitFailedPhase,
  type StepId, type StepStatus,
} from "./stageModel";

// 단계 모델 표 테스트(2026-10-08 요청 상세 재설계 §11.3). 실패 지점 판정의 규칙 순서(제출 접두 → 사유 접두 →
// 마지막 종단 전이의 from_state → 가장 깊은 ref → 흐름 첫 단계)가 바뀌면 결과가 엉뚱한 구획에 그려진다.

const t = (from: string | null, to: string, sec: number, extra: Partial<Transition> = {}): Transition =>
  ({ from_state: from, to_state: to, at: `2026-10-08T03:15:${String(sec).padStart(2, "0")}Z`, ...extra });
const job = (over: Partial<DataJob>): DataJob => ({
  job_id: "j1", request_id: "r1", operation: "sync", state: "Pending", reason_code: null,
  preview_fingerprint: null, preview_expires_at: null, result_summary: null,
  transitions: [], artifact_uri: null, phase_refs: {}, ...over,
});
const statuses = (j: DataJob) => Object.fromEntries(deriveJobStages(j).steps.map((s) => [s.id, s.status])) as
  Partial<Record<StepId, StepStatus>>;
const step = (j: DataJob, id: StepId) => deriveJobStages(j).steps.find((s) => s.id === id)!;

const SYNC_OK_TR = [
  t(null, "Pending", 0), t("Pending", "Preflight", 2), t("Preflight", "PreviewRunning", 7),
  t("PreviewRunning", "ConfirmPending", 17), t("ConfirmPending", "Executing", 18, { actor: "alice" }),
  t("Executing", "Executing", 27), t("Executing", "Succeeded", 37),
];
const ALL_REFS = { preflight: "pod/a", preview: "pod/b", exec_preflight: "pod/c", execution: "vcjob/j1" };

test("scan 성공: 관문 없음, 사전 점검·실행 둘 다 완료, 실행 시작 = exec_submitted_at ?? Running 도달", () => {
  const base = job({ operation: "scan", state: "Succeeded", phase_refs: { preflight: "pod/a", execution: "vcjob/j1" },
    transitions: [t(null, "Pending", 0), t("Pending", "Preflight", 1), t("Preflight", "Running", 5), t("Running", "Succeeded", 59)] });
  const m = deriveJobStages(base);
  expect(m.flow).toBe("scan");
  expect(m.gate).toBeNull();
  expect(m.steps.map((s) => [s.id, s.status])).toEqual([["preflight", "done"], ["execution", "done"]]);
  expect(step(base, "execution").start).toBe("2026-10-08T03:15:05Z");
  expect(step({ ...base, exec_submitted_at: "2026-10-08T03:15:06Z" }, "execution").start).toBe("2026-10-08T03:15:06Z");
  expect(m.exec.end).toBe("2026-10-08T03:15:59Z");
  expect(m.executionStarted).toBe(true);
});

test("dsync 성공(7개 전이): 재점검 끝 = 자기 전이 = 실행 시작, 관문 actor, ① 끝 = 컨펌 대기 도달", () => {
  const j = job({ state: "Succeeded", phase_refs: ALL_REFS, transitions: SYNC_OK_TR, sched_wait_seconds: 0 });
  const m = deriveJobStages(j);
  expect(Object.values(statuses(j))).toEqual(["done", "done", "done", "done", "done"]);
  expect(step(j, "exec_preflight").end).toBe("2026-10-08T03:15:27Z");
  expect(step(j, "execution").start).toBe("2026-10-08T03:15:27Z");
  expect(m.gate?.actor).toBe("alice");
  expect(m.pre.end).toBe("2026-10-08T03:15:17Z");
  expect(m.pre.start).toBe("2026-10-08T03:15:02Z");
  expect(m.exec.start).toBe("2026-10-08T03:15:18Z");
  expect(m.exec.end).toBe("2026-10-08T03:15:37Z");
  // 0 은 정상값(스케줄 대기 없음) -- null(모름)과 다르다
  expect(step(j, "execution").schedWaitSec).toBe(0);
  expect(step({ ...j, sched_wait_seconds: null }, "execution").schedWaitSec).toBeNull();
  expect(m.failure).toBeNull();
  expect(m.autoOpen).toBeNull();
});

test("ConfirmPending: 관문 awaiting, ② 대기, 자동 열림 없음", () => {
  const j = job({ state: "ConfirmPending", phase_refs: { preflight: "pod/a", preview: "pod/b" }, transitions: SYNC_OK_TR.slice(0, 4) });
  const m = deriveJobStages(j);
  expect(statuses(j)).toEqual({ preflight: "done", preview: "done", confirm: "awaiting", exec_preflight: "waiting", execution: "waiting" });
  expect(m.exec.status).toBe("waiting");
  expect(m.pre.status).toBe("done");
  expect(m.autoOpen).toBeNull();
});

test("PreviewExpired: 관문 만료, ② 실행 안 됨, 근거 state", () => {
  const j = job({ state: "PreviewExpired", phase_refs: { preflight: "pod/a", preview: "pod/b" },
    transitions: [...SYNC_OK_TR.slice(0, 4), t("ConfirmPending", "PreviewExpired", 40)] });
  const m = deriveJobStages(j);
  expect(m.gate?.status).toBe("expired");
  expect(m.exec.status).toBe("skipped");
  expect(m.failure?.evidence).toBe("state");
  expect(m.autoOpen).toBeNull();
});

test("사전 점검 거부(마커 승격 사유, from Preflight): preflight rejected, 근거 from_state, preflight 로그 자동", () => {
  const j = job({ state: "Rejected", reason_code: "destination_not_writable", phase_refs: { preflight: "pod/a" },
    transitions: [t(null, "Pending", 0), t("Pending", "Preflight", 2), t("Preflight", "Rejected", 5)] });
  const m = deriveJobStages(j);
  expect(statuses(j)).toEqual({ preflight: "rejected", preview: "skipped", confirm: "skipped", exec_preflight: "skipped", execution: "skipped" });
  expect(m.failure).toMatchObject({ step: "preflight", evidence: "from_state", stage: "pre" });
  expect(m.autoOpen).toEqual({ stage: "pre", key: "log:preflight" });
  expect(step(j, "preflight").end).toBe("2026-10-08T03:15:05Z");
  expect(m.pre.status).toBe("rejected");
  expect(m.exec.status).toBe("skipped");
});

test("Pending → Rejected(ldap_unavailable): 시작 전, 시작 시각 없음, 로그 없으니 자동 열림 없음", () => {
  const j = job({ state: "Rejected", reason_code: "ldap_unavailable",
    transitions: [t(null, "Pending", 0), t("Pending", "Rejected", 3)] });
  const s = step(j, "preflight");
  expect(s.status).toBe("rejected");
  expect(s.notStarted).toBe(true);
  expect(s.start).toBeNull();
  expect(s.end).toBe("2026-10-08T03:15:03Z");
  expect(deriveJobStages(j).autoOpen).toBeNull();
});

test("preview 제출 실패(Preflight→Failed 로 남는다): 제출 접두가 from_state 보다 먼저 -- preview 실패, 시작 모름", () => {
  const j = job({ state: "Failed", reason_code: "preview_submit_failed:submit_failed", phase_refs: { preflight: "pod/a" },
    transitions: [t(null, "Pending", 0), t("Pending", "Preflight", 2), t("Preflight", "Failed", 6)] });
  const m = deriveJobStages(j);
  expect(m.failure).toMatchObject({ step: "preview", evidence: "submit_prefix", submitFailed: true });
  const p = step(j, "preview");
  expect(p.status).toBe("failed");
  expect(p.start).toBeNull();
  expect(p.logAvailable).toBe(true);             // 박제 사본(submit:preview)
  expect(m.autoOpen).toEqual({ stage: "pre", key: "log:preview" });
  expect(submitFailedPhase("preview_submit_failed:x")).toBe("preview");
  expect(submitFailedPhase("preflight_failed:x")).toBeNull();
});

test("scan 실행 제출 실패(Preflight→Failed): execution 실패", () => {
  const j = job({ operation: "scan", state: "Failed", reason_code: "execution_submit_failed:quota", phase_refs: { preflight: "pod/a" },
    transitions: [t(null, "Pending", 0), t("Pending", "Preflight", 1), t("Preflight", "Failed", 5)] });
  expect(statuses(j)).toEqual({ preflight: "done", execution: "failed" });
  expect(deriveJobStages(j).executionStarted).toBe(false);
});

test("재점검 실패: 사유 접두 execution_recheck_failed, 또는 마커 사유 + from Executing + execution ref 없음", () => {
  const tr = [...SYNC_OK_TR.slice(0, 5), t("Executing", "Rejected", 22)];
  const a = job({ state: "Rejected", reason_code: "execution_recheck_failed:x", phase_refs: { preflight: "a", preview: "b", exec_preflight: "c" }, transitions: tr });
  expect(deriveJobStages(a).failure).toMatchObject({ step: "exec_preflight", evidence: "reason_prefix" });
  const b = job({ state: "Rejected", reason_code: "destination_not_writable", phase_refs: { preflight: "a", preview: "b", exec_preflight: "c" }, transitions: tr });
  expect(deriveJobStages(b).failure).toMatchObject({ step: "exec_preflight", evidence: "from_state" });
  expect(statuses(b)).toMatchObject({ exec_preflight: "rejected", execution: "skipped", confirm: "done" });
  expect(deriveJobStages(b).autoOpen).toEqual({ stage: "exec", key: "log:exec_preflight" });
});

test("실행 시간 초과(TimedOut + execution_failed): execution timed_out", () => {
  const j = job({ state: "TimedOut", reason_code: "execution_failed:deadline", phase_refs: ALL_REFS,
    transitions: [...SYNC_OK_TR.slice(0, 6), t("Executing", "TimedOut", 50)] });
  const m = deriveJobStages(j);
  expect(m.terminal).toBe(true);
  expect(step(j, "execution").status).toBe("timed_out");
  expect(step(j, "execution").end).toBe("2026-10-08T03:15:50Z");
  expect(m.exec.status).toBe("timed_out");
  expect(m.autoOpen).toEqual({ stage: "exec", key: "log:execution" });
});

test("컨펌 대기 중 취소: 관문 cancelled, ② 실행 안 됨, 실행 시작 안 함, 자동 열림 없음", () => {
  const j = job({ state: "Cancelled", phase_refs: { preflight: "a", preview: "b" },
    transitions: [...SYNC_OK_TR.slice(0, 4), t("ConfirmPending", "Cancelled", 30)] });
  const m = deriveJobStages(j);
  expect(m.gate?.status).toBe("cancelled");
  expect(m.exec.status).toBe("skipped");
  expect(m.executionStarted).toBe(false);
  expect(m.autoOpen).toBeNull();
});

test("실행 중 취소: execution cancelled, 실행 시작함", () => {
  const j = job({ state: "Cancelled", phase_refs: ALL_REFS,
    transitions: [...SYNC_OK_TR.slice(0, 6), t("Executing", "Cancelled", 31)] });
  const m = deriveJobStages(j);
  expect(step(j, "execution").status).toBe("cancelled");
  expect(m.executionStarted).toBe(true);
  expect(m.exec.status).toBe("cancelled");
  expect(m.autoOpen).toBeNull();
});

test("RequestDetail.test 기본 픽스처(Failed, from Executing, refs preflight 만, 접두 없는 사유) = 재점검 · 결과는 ②", () => {
  // 이 판정이 「미리보기 실패」로 바뀌면 결과가 한국어 미리보기 타일로 그려져 RequestDetail.test 의 원 키 단언 3건이 깨진다.
  const j = job({ state: "Failed", reason_code: "no_eligible_nodes", result_summary: { files: 120, bytes: 456 },
    phase_refs: { preflight: "pod/p1" },
    transitions: [t(null, "Executing", 10), t("Executing", "Failed", 30)] });
  const m = deriveJobStages(j);
  expect(m.failure).toMatchObject({ step: "exec_preflight", evidence: "from_state" });
  expect(m.resultStage).toBe("exec");
  expect(m.autoOpen).toBeNull();                 // 실패 지점에 로그 ref 가 없다
});

test("전이 없음 + refs {preview} + Failed: 가장 깊은 ref 로 **추정**, 자동 열림 없음", () => {
  const j = job({ state: "Failed", phase_refs: { preflight: "a", preview: "b" } });
  const m = deriveJobStages(j);
  expect(m.failure).toMatchObject({ step: "preview", evidence: "deepest_ref" });
  expect(m.autoOpen).toBeNull();
});

test("transitions·phase_refs 가 엉뚱한 모양이어도 죽지 않는다(근거 none)", () => {
  for (const [tr, refs] of [[null, null], [{ oops: 1 }, "pod/x"], [[1, null, { to_state: 5 }], [1, 2]]] as const) {
    const j = job({ state: "Failed", transitions: tr as unknown as Transition[], phase_refs: refs as unknown as Record<string, string> });
    const m = deriveJobStages(j);
    expect(m.failure).toMatchObject({ step: "preflight", evidence: "none" });
    expect(m.refs).toEqual({});
  }
});

test("모르는 상태: known=false, terminal=false(거짓 종단 금지 -- 폴링 유지), 전부 unknown", () => {
  const m = deriveJobStages(job({ state: "Weird" }));
  expect(m.known).toBe(false);
  expect(m.terminal).toBe(false);
  expect(m.steps.every((s) => s.status === "unknown")).toBe(true);
  expect(m.pre.status).toBe("unknown");
});

test("미리보기 실패 + result_summary: 결과는 ①(미리보기 summary 다 -- stepper _surface_failed_artifact)", () => {
  const j = job({ state: "Failed", reason_code: "preview_failed:rc2", result_summary: { files: 3, bytes: 9, returncode: 2 },
    phase_refs: { preflight: "a", preview: "b" },
    transitions: [t(null, "Pending", 0), t("Pending", "Preflight", 1), t("Preflight", "PreviewRunning", 3), t("PreviewRunning", "Failed", 9)] });
  const m = deriveJobStages(j);
  expect(m.resultStage).toBe("pre");
  expect(m.failure?.evidence).toBe("reason_prefix");
  expect(m.autoOpen).toEqual({ stage: "pre", key: "log:preview" });
});

test("비종단 진행 단계: Executing 은 execution ref 로 재점검/실행을 가른다", () => {
  const tr = SYNC_OK_TR.slice(0, 5);
  expect(statuses(job({ state: "Executing", phase_refs: { preflight: "a", preview: "b", exec_preflight: "c" }, transitions: tr })))
    .toMatchObject({ exec_preflight: "running", execution: "waiting", confirm: "done" });
  expect(statuses(job({ state: "Executing", phase_refs: ALL_REFS, transitions: tr })))
    .toMatchObject({ exec_preflight: "done", execution: "running" });
  expect(statuses(job({ state: "Preflight" }))).toMatchObject({ preflight: "running", preview: "waiting" });
  expect(deriveJobStages(job({ state: "PreviewRunning" })).pre.status).toBe("running");
});

test("소요: 0초는 \"0초\"(null 아님), 시각 모름·거꾸로면 null", () => {
  expect(stepDuration({ start: "2026-10-08T00:00:00Z", end: "2026-10-08T00:00:00Z" })).toBe("0초");
  expect(stepDuration({ start: null, end: "2026-10-08T00:00:00Z" })).toBeNull();
  expect(stepDuration({ start: "2026-10-08T00:00:09Z", end: "2026-10-08T00:00:00Z" })).toBeNull();
  expect(stepDuration({ start: "2026-10-08T00:00:00Z", end: "2026-10-08T00:01:30Z" })).toBe("1분 30초");
});

test("출력 묶기: 로그 먼저, 아티팩트는 정해진 순서 → 이름순, 흐름 밖 phase 는 기타", () => {
  const m = deriveJobStages(job({ state: "Succeeded", phase_refs: { preflight: "a", execution: "v", weird: "x" } }));
  const out = outputsByStep(m, [
    { phase: "execution", name: "zz.txt", size: 1, modified_at: 0 },
    { phase: "execution", name: "mpi-hostfile", size: 2, modified_at: 0 },
    { phase: "execution", name: "stdout.log", size: "?" as unknown as number, modified_at: 0 },
    { phase: "execution", name: "aa.txt", size: 0, modified_at: 0 },
    { phase: "foo", name: "x", size: 1, modified_at: 0 },
    "garbage",
  ]);
  expect(out.byStep.execution?.map((i) => i.key)).toEqual([
    "log:execution", "artifact:execution/stdout.log", "artifact:execution/mpi-hostfile",
    "artifact:execution/aa.txt", "artifact:execution/zz.txt",
  ]);
  const stdout = out.byStep.execution?.find((i) => i.key === "artifact:execution/stdout.log");
  expect(stdout && stdout.kind === "artifact" ? stdout.size : "x").toBeNull();   // 숫자가 아니면 크기 모름
  const aa = out.byStep.execution?.find((i) => i.key === "artifact:execution/aa.txt");
  expect(aa && aa.kind === "artifact" ? aa.size : "x").toBe(0);                  // 0 은 정상값
  expect(out.byStep.preflight?.map((i) => i.key)).toEqual(["log:preflight"]);
  expect(out.other.map((g) => g.phase)).toEqual(["foo", "weird"]);
  // 목록이 배열이 아니어도 로그 칩은 남는다
  expect(outputsByStep(m, undefined).byStep.preflight?.map((i) => i.key)).toEqual(["log:preflight"]);
});

test("주석: scan 에서는 흐름에 없는 phase 를 가장 가까운 단계로 접는다", () => {
  const scan = job({ operation: "scan" });
  const ev = { id: 1, component: "stepper", severity: "info", event_type: "x", message: null, at: "2026-10-08T00:00:00Z",
    payload: { job_id: "j1", phase: "exec_preflight" } };
  expect(Object.keys(annotationsFor([ev], scan, 1, "scan"))).toEqual(["execution"]);
  expect(annotationsFor({ not: "array" }, scan, 1, "scan")).toEqual({});
});

test("화면 낭독 문구: 첫 로드는 조용히, 바뀐 구획만 한국어로(영문 상태·job_id 없음)", () => {
  const a = stageSnapshot(deriveJobStages(job({ state: "Executing", phase_refs: ALL_REFS, transitions: SYNC_OK_TR.slice(0, 6) })));
  const b = stageSnapshot(deriveJobStages(job({ state: "Succeeded", phase_refs: ALL_REFS, transitions: SYNC_OK_TR })));
  expect(stageAnnouncement(undefined, b, "previewed")).toBeNull();
  expect(stageAnnouncement(a, b, "previewed")).toBe("실행 단계: 완료");
  expect(stageAnnouncement(b, b, "previewed")).toBeNull();
});

// ---- 2026-10-08 리뷰 반영: 백엔드(stepper) 실제 경로 대 단계 모델 -------------------------------------------------------
// 각 행은 stepper 가 실제로 남기는 전이·phase_refs·reason_code 모양이다(review/stage·gatefix·hold 프로브 이식).

const PRE_TR = [t(null, "Pending", 0), t("Pending", "Preflight", 2)];
const TO_EXEC = [...PRE_TR, t("Preflight", "PreviewRunning", 7), t("PreviewRunning", "ConfirmPending", 17),
  t("ConfirmPending", "Executing", 18, { actor: "alice" })];
const ev = (event_type: string, payload: object, sec: number, id = sec) => ({
  id, component: "stepper", severity: "warning", event_type, message: null, payload,
  at: `2026-10-08T03:15:${String(sec).padStart(2, "0")}Z`,
});
const deferredEv = (phase: string, attempt: number, sec: number) =>
  ev("identity_recheck_deferred", { job_id: "j1", phase, attempt, max_attempts: 4, attempted_at_epoch: 1 }, sec);

test("V6 A: preflight 파드 통과 뒤 미리보기 제출 관문(identity_changed_at_step, Preflight→Rejected) = 미리보기 시작 전 거부", () => {
  // stepper._poll_preflight SUCCEEDED → _submit_preview → _build_spec(preview) → IdentityChangedAtStep → _fail_closed.
  const j = job({ state: "Rejected", reason_code: "identity_changed_at_step", phase_refs: { preflight: "pod/p" },
    transitions: [...PRE_TR, t("Preflight", "Rejected", 30)] });
  const m = deriveJobStages(j);
  expect(m.failure).toMatchObject({ step: "preview", evidence: "gate", notStarted: true, queued: false });
  expect(statuses(j)).toMatchObject({ preflight: "done", preview: "rejected", confirm: "skipped" });
  expect(m.autoOpen).toBeNull();                      // 통과한 preflight 로그를 실패 로그로 열지 않는다
  const pf = step(j, "preflight");
  expect(pf.start).toBe("2026-10-08T03:15:02Z");
  expect(pf.end).toBeNull();                          // 보류 시간까지 섞인 "소요" 를 지어내지 않는다
  const pv = step(j, "preview");
  expect([pv.start, pv.end]).toEqual([null, "2026-10-08T03:15:30Z"]);
  // 백엔드 이벤트(payload.phase=preview)가 붙는 행 = 거부로 칠한 행(카드가 스스로 모순되지 않는다)
  const e = ev("identity_changed_at_step", { job_id: "j1", phase: "preview", queued: false }, 30);
  expect(Object.keys(annotationsFor([e], j, 1, m.flow))).toEqual(["preview"]);
});

test("V6 A2·A3: ldap_unavailable(보류 소진)·scan 실행 제출 관문, Pending 의 관문은 지금까지처럼 사전 점검 시작 전", () => {
  const a2 = job({ state: "Rejected", reason_code: "ldap_unavailable", phase_refs: { preflight: "pod/p" },
    transitions: [...PRE_TR, t("Preflight", "Rejected", 59)] });
  expect(deriveJobStages(a2).failure).toMatchObject({ step: "preview", evidence: "gate", notStarted: true });
  const a3 = job({ operation: "scan", state: "Rejected", reason_code: "storage_missing_at_step", phase_refs: { preflight: "pod/p" },
    transitions: [...PRE_TR, t("Preflight", "Rejected", 9)] });
  expect(statuses(a3)).toEqual({ preflight: "done", execution: "rejected" });
  expect(step(a3, "preflight").end).toBeNull();
  expect(deriveJobStages(a3).autoOpen).toBeNull();
  const pend = job({ state: "Rejected", reason_code: "ldap_unavailable", transitions: [t(null, "Pending", 0), t("Pending", "Rejected", 3)] });
  expect(deriveJobStages(pend).failure).toMatchObject({ step: "preflight", evidence: "from_state", notStarted: true });
});

test("V6 node_excluded(from Preflight)는 모호 -- 추정, 다음 제출 보류 기록이 있으면 미리보기 시작 전으로 확정", () => {
  const base = { state: "Rejected", reason_code: "node_excluded_at_step", phase_refs: { preflight: "pod/p" },
    transitions: [...PRE_TR, t("Preflight", "Rejected", 9)] };
  const amb = deriveJobStages(job(base));
  expect(amb.failure).toMatchObject({ step: "preflight", evidence: "gate_ambiguous" });
  expect(amb.autoOpen).toBeNull();
  // preview 제출 보류가 있었다 = preflight 파드는 이미 SUCCEEDED(보류는 그 뒤에만 생긴다)
  const sure = deriveJobStages(job(base), [deferredEv("preview", 1, 5)]);
  expect(sure.failure).toMatchObject({ step: "preview", evidence: "gate", notStarted: true });
  // 다른 잡의 보류 기록은 근거가 아니다
  const other = deriveJobStages(job(base), [ev("identity_recheck_deferred", { job_id: "j9", phase: "preview" }, 5)]);
  expect(other.failure?.evidence).toBe("gate_ambiguous");
});

test("V6 B: 재점검 파드 통과 뒤 실행 제출 관문(Executing→Failed, execution ref 없음) = 실행 시작 전, 재점검은 완료", () => {
  const j = job({ state: "Failed", reason_code: "identity_changed_at_step",
    phase_refs: { preflight: "pod/a", preview: "vcjob/b", exec_preflight: "pod/c" },
    transitions: [...TO_EXEC, t("Executing", "Failed", 40)] });
  const m = deriveJobStages(j);
  expect(m.failure).toMatchObject({ step: "execution", evidence: "gate", notStarted: true });
  expect(statuses(j)).toMatchObject({ exec_preflight: "done", execution: "failed" });
  expect(step(j, "exec_preflight").end).toBeNull();
  expect(step(j, "execution").start).toBeNull();
  expect(m.autoOpen).toBeNull();
  expect(m.executionStarted).toBe(false);
  // node_excluded 는 재점검 파드 PENDING 재검사에서도 나온다 -- 추정
  const amb = deriveJobStages({ ...j, reason_code: "node_excluded_at_step" });
  expect(amb.failure).toMatchObject({ step: "exec_preflight", evidence: "gate_ambiguous" });
});

test("V7 C: 컨펌 직후 재점검 파드를 만들기 전 관문(refs 에 exec_preflight 없음) = 재점검 시작 전, 소요 없음", () => {
  const j = job({ state: "Failed", reason_code: "privilege_not_requested", phase_refs: { preflight: "pod/a", preview: "vcjob/b" },
    transitions: [...TO_EXEC, t("Executing", "Failed", 22)] });
  const m = deriveJobStages(j);
  expect(m.failure).toMatchObject({ step: "exec_preflight", notStarted: true });
  const ep = step(j, "exec_preflight");
  expect([ep.start, ep.end, ep.logAvailable]).toEqual([null, "2026-10-08T03:15:22Z", false]);
  expect(stepDuration(ep)).toBeNull();                // 예전: 컨펌 → 종단 「소요 4초」
  expect(m.autoOpen).toBeNull();
  expect(m.gate?.status).toBe("done");                // 사람 관문 시각은 그대로
  expect(m.gate?.end).toBe("2026-10-08T03:15:18Z");
  // 관문 사유가 아니어도(취소) 같은 자리에서 끝났으면 시작 전이다
  const c = job({ state: "Cancelled", reason_code: "cancelled_by_user", phase_refs: { preflight: "a", preview: "b" },
    transitions: [...TO_EXEC, t("Executing", "Cancelled", 19)] });
  expect(deriveJobStages(c).failure).toMatchObject({ step: "exec_preflight", evidence: "from_state", notStarted: true });
  expect(stepDuration(step(c, "exec_preflight"))).toBeNull();
});

test("V8: 실행 vcjob 큐 대기 중(sched_wait 없음) 관문 종단 = 대기 중 실패, 실행 안 함, 자동 열림 없음", () => {
  const D = job({ operation: "rm", state: "Failed", reason_code: "node_excluded_at_step", sched_wait_seconds: null,
    exec_submitted_at: "2026-10-08T03:15:27Z", phase_refs: ALL_REFS,
    transitions: [...TO_EXEC, t("Executing", "Executing", 27), t("Executing", "Failed", 50)] });
  const m = deriveJobStages(D);
  expect(m.failure).toMatchObject({ step: "execution", evidence: "gate", queued: true });
  expect(m.executionStarted).toBe(false);
  expect(m.autoOpen).toBeNull();
  expect(step(D, "execution")).toMatchObject({ status: "failed", queued: true });
  // sched_wait 가 0(같은 틱에 스케줄됨 -- 정상값)이면 실행된 적이 있다: 지금까지 그대로
  const ran = deriveJobStages({ ...D, sched_wait_seconds: 0 });
  expect(ran.failure).toMatchObject({ step: "execution", evidence: "from_state", queued: false });
  expect(ran.executionStarted).toBe(true);
  // 실행이 실패한 사유(execution_failed)·관문 아닌 사유(unknown_tool)는 지금까지 그대로
  expect(deriveJobStages({ ...D, reason_code: "execution_failed:rc1" }).failure)
    .toMatchObject({ evidence: "reason_prefix", queued: false });
  expect(deriveJobStages({ ...D, reason_code: "unknown_tool" }).failure).toMatchObject({ evidence: "from_state", queued: false });
  // 앵커(exec_submitted_at)가 없는 옛 잡은 모른다 -- 지금까지 그대로
  expect(deriveJobStages({ ...D, exec_submitted_at: null }).failure).toMatchObject({ evidence: "from_state", queued: false });
});

test("V8: 미리보기 vcjob 큐 대기 중 관문(PreviewRunning→Rejected) = 미리보기 대기 중 거부, 로그 자동 열림 없음", () => {
  const j = job({ state: "Rejected", reason_code: "identity_changed_at_step", phase_refs: { preflight: "a", preview: "vcjob/b" },
    transitions: [...PRE_TR, t("Preflight", "PreviewRunning", 7), t("PreviewRunning", "Rejected", 30)] });
  const m = deriveJobStages(j);
  expect(m.failure).toMatchObject({ step: "preview", evidence: "gate", queued: true });
  expect(m.autoOpen).toBeNull();
});

test("V8: 스케줄 대기 중 취소는 queued 로 보이되 확정하지 않는다(실행 시작 가능성 유지)", () => {
  const j = job({ state: "Cancelled", reason_code: "cancelled_by_user", sched_wait_seconds: null,
    exec_submitted_at: "2026-10-08T03:15:27Z", phase_refs: ALL_REFS,
    transitions: [...TO_EXEC, t("Executing", "Executing", 27), t("Executing", "Cancelled", 29)] });
  const m = deriveJobStages(j);
  expect(m.failure).toMatchObject({ step: "execution", evidence: "from_state", queued: true });
  expect(m.executionStarted).toBe(true);
});

test("V8 비종단: 실행 vcjob 이 큐에서 기다리면 실행 행은 「진행 중」이 아니라 대기(queued), 첫 RUNNING 관측 뒤 진행 중", () => {
  const q = job({ state: "Executing", sched_wait_seconds: null, exec_submitted_at: "2026-10-08T03:15:27Z", phase_refs: ALL_REFS,
    transitions: [...TO_EXEC, t("Executing", "Executing", 27)] });
  const m = deriveJobStages(q);
  expect(step(q, "execution")).toMatchObject({ status: "waiting", queued: true, start: "2026-10-08T03:15:27Z" });
  expect(step(q, "exec_preflight").status).toBe("done");
  expect(m.exec.status).toBe("running");
  expect(m.executionStarted).toBe(false);
  expect(step({ ...q, sched_wait_seconds: 4 }, "execution")).toMatchObject({ status: "running", queued: false });
  // 앵커 없는 옛 잡: 지금까지처럼 진행 중
  expect(step({ ...q, exec_submitted_at: null }, "execution")).toMatchObject({ status: "running", queued: false });
  // scan(Running)도 같다
  const scan = job({ operation: "scan", state: "Running", sched_wait_seconds: null, exec_submitted_at: "2026-10-08T03:15:05Z",
    phase_refs: { preflight: "a", execution: "vcjob/j1" }, transitions: [...PRE_TR, t("Preflight", "Running", 5)] });
  expect(step(scan, "execution")).toMatchObject({ status: "waiting", queued: true });
});

test("V9 제출 보류: 끝난 앞 단계는 완료, 보류 단계는 대기 + held(시도/상한), 진행 중 단계 없음", () => {
  const j = job({ state: "Preflight", phase_refs: { preflight: "pod/p" }, transitions: PRE_TR });
  const events = [deferredEv("preview", 1, 30), deferredEv("preview", 2, 40), deferredEv("preview", 3, 50)];
  const m = deriveJobStages(j, events);
  expect(m.hold).toEqual({ phase: "preview", attempt: 3, max: 4 });
  expect(m.steps.map((s) => [s.id, s.status])).toEqual([
    ["preflight", "done"], ["preview", "waiting"], ["confirm", "waiting"], ["exec_preflight", "waiting"], ["execution", "waiting"],
  ]);
  expect(m.steps[0].end).toBeNull();
  expect(m.steps.find((s) => s.id === "preview")?.held).toEqual({ attempt: 3, max: 4 });
  expect(m.steps.some((s) => s.status === "running")).toBe(false);
  // 재확인 통과 기록이 뒤에 오면 보류가 끝났다 -- 지금까지처럼 진행 중
  const passed = deriveJobStages(j, [...events, ev("identity_groups_checked", { job_id: "j1", phase: "preview" }, 55)]);
  expect(passed.hold).toBeNull();
  expect(passed.steps[0].status).toBe("running");
  // 마지막 전이보다 오래된 기록·지금 상태의 다음 제출이 아닌 phase·다른 잡 → 무시
  expect(deriveJobStages(j, [deferredEv("preview", 1, 1)]).hold).toBeNull();
  expect(deriveJobStages(j, [deferredEv("execution", 1, 30)]).hold).toBeNull();
  expect(deriveJobStages(j, [ev("identity_recheck_deferred", { job_id: "j9", phase: "preview" }, 30)]).hold).toBeNull();
  // 횟수를 모르면 null(지어내지 않는다), events 가 배열이 아니어도 죽지 않는다
  expect(deriveJobStages(j, [ev("identity_recheck_deferred", { job_id: "j1", phase: "preview" }, 30)]).hold)
    .toEqual({ phase: "preview", attempt: null, max: null });
  expect(deriveJobStages(j, { not: "array" }).hold).toBeNull();
});

test("V9 제출 보류: 재점검 통과 뒤 실행 제출 · 재점검 파드 전 · Pending 의 사전 점검 제출 · scan", () => {
  const exec = job({ state: "Executing", phase_refs: { preflight: "a", preview: "b", exec_preflight: "c" }, transitions: TO_EXEC });
  expect(statuses(exec)).toMatchObject({ exec_preflight: "running" });           // 보류 기록이 없으면 지금까지 그대로
  const m = deriveJobStages(exec, [deferredEv("execution", 1, 40)]);
  expect(m.steps.filter((s) => s.stage === "exec").map((s) => [s.id, s.status, s.held !== null]))
    .toEqual([["exec_preflight", "done", false], ["execution", "waiting", true]]);
  // 재점검 파드 ref 전: 그 단계 자체가 보류 중(상태는 그대로, held 만)
  const pre = job({ state: "Executing", phase_refs: { preflight: "a", preview: "b" }, transitions: TO_EXEC });
  const mp = deriveJobStages(pre, [deferredEv("exec_preflight", 2, 30)]);
  expect(mp.steps.find((s) => s.id === "exec_preflight")).toMatchObject({ status: "running", held: { attempt: 2, max: 4 } });
  const pend = job({ state: "Pending", transitions: [t(null, "Pending", 0)] });
  expect(deriveJobStages(pend, [deferredEv("preflight", 1, 10)]).steps[0])
    .toMatchObject({ status: "waiting", held: { attempt: 1, max: 4 } });
  const scan = job({ operation: "scan", state: "Preflight", phase_refs: { preflight: "a" }, transitions: PRE_TR });
  const ms = deriveJobStages(scan, [deferredEv("execution", 1, 30)]);
  expect(ms.steps.map((s) => [s.id, s.status])).toEqual([["preflight", "done"], ["execution", "waiting"]]);
  // 종단 잡에는 보류가 없다
  expect(deriveJobStages({ ...exec, state: "Succeeded" }, [deferredEv("execution", 1, 40)]).hold).toBeNull();
});

// ---- 2026-10-08 리뷰 2차 N3·N4 --------------------------------------------------------------------------------------

test("N3 artifact_base_* 사유가 preflight·exec_preflight 파드 마커로 왔으면(관문 이벤트 없음) 그 파드 단계가 실패, 로그 자동 열림", () => {
  // execution_manifests 의 preflight 스크립트가 DMS_PREFLIGHT_REASON=artifact_base_* 를 찍는다 → stepper._preflight_reason
  // 승격 → _finalize(REJECTED). 관문(_step_one → _fail_closed)이 아니라 이벤트 artifact_base_unsafe_at_step 이 없다.
  for (const code of ["artifact_base_not_traversable", "artifact_base_group_writable"]) {
    const pre = job({ state: "Rejected", reason_code: code, phase_refs: { preflight: "pod/p" },
      transitions: [...PRE_TR, t("Preflight", "Rejected", 9)] });
    const m = deriveJobStages(pre, []);
    expect(m.failure, code).toMatchObject({ step: "preflight", evidence: "from_state", notStarted: false });
    expect(statuses(pre)).toMatchObject({ preflight: "rejected", preview: "skipped" });
    expect(m.autoOpen).toEqual({ stage: "pre", key: "log:preflight" });
    expect(step(pre, "preflight").end).toBe("2026-10-08T03:15:09Z");
    // 다른 잡의 관문 이벤트는 근거가 아니다
    const other = deriveJobStages(pre, [ev("artifact_base_unsafe_at_step", { job_id: "j9", problem: code }, 9)]);
    expect(other.failure).toMatchObject({ step: "preflight", evidence: "from_state" });
    // scan 도 같다(사전 점검 파드 실패)
    const scan = job({ ...pre, operation: "scan" });
    expect(deriveJobStages(scan, []).failure).toMatchObject({ step: "preflight", evidence: "from_state" });

    const exec = job({ state: "Rejected", reason_code: code, phase_refs: { preflight: "a", preview: "vcjob/b", exec_preflight: "pod/c" },
      transitions: [...TO_EXEC, t("Executing", "Rejected", 25)] });
    const me = deriveJobStages(exec, []);
    expect(me.failure, code).toMatchObject({ step: "exec_preflight", evidence: "from_state", notStarted: false });
    expect(statuses(exec)).toMatchObject({ exec_preflight: "rejected", execution: "skipped" });
    expect(me.autoOpen).toEqual({ stage: "exec", key: "log:exec_preflight" });
  }
});

test("N3 artifact_base_* 에 이 잡의 관문 이벤트가 있으면(또는 실행 상태에서 Failed 면) 지금까지처럼 다음 단계 시작 전 관문", () => {
  for (const code of ["artifact_base_not_traversable", "artifact_base_group_writable"]) {
    const gateEv = [ev("artifact_base_unsafe_at_step", { job_id: "j1", problem: code }, 30)];
    const pre = job({ state: "Rejected", reason_code: code, phase_refs: { preflight: "pod/p" },
      transitions: [...PRE_TR, t("Preflight", "Rejected", 30)] });
    const m = deriveJobStages(pre, gateEv);
    expect(m.failure, code).toMatchObject({ step: "preview", evidence: "gate", notStarted: true });
    expect(m.autoOpen).toBeNull();
    expect(m.steps.find((s) => s.id === "preflight")).toMatchObject({ status: "done", end: null });
    expect(deriveJobStages({ ...pre, operation: "scan" }, gateEv).failure).toMatchObject({ step: "execution", evidence: "gate" });

    // _fail_closed 는 Executing 에서 Failed -- 이벤트와 함께
    const exec = job({ state: "Failed", reason_code: code, phase_refs: { preflight: "a", preview: "vcjob/b", exec_preflight: "pod/c" },
      transitions: [...TO_EXEC, t("Executing", "Failed", 40)] });
    const me = deriveJobStages(exec, [ev("artifact_base_unsafe_at_step", { job_id: "j1", problem: code }, 40)]);
    expect(me.failure, code).toMatchObject({ step: "execution", evidence: "gate", notStarted: true });
    expect(me.autoOpen).toBeNull();
    // 이벤트가 요청 이벤트 창(최신 100건) 밖으로 밀려도 Executing→Failed 는 마커 길(늘 Rejected)일 수 없다 -- 관문
    expect(deriveJobStages(exec, []).failure).toMatchObject({ step: "execution", evidence: "gate", notStarted: true });
    // 컨펌 직후 재점검 파드를 만들기 전 관문
    const before = job({ ...exec, phase_refs: { preflight: "a", preview: "vcjob/b" } });
    expect(deriveJobStages(before, []).failure).toMatchObject({ step: "exec_preflight", evidence: "gate", notStarted: true });
  }
});

test("N4 다음 제출 보류 중 취소(Preflight→Cancelled): 통과한 사전 점검은 완료(소요 없음), 미리보기가 시작 전 취소", () => {
  const evs = [deferredEv("preview", 1, 20), deferredEv("preview", 2, 40), deferredEv("preview", 3, 55)];
  const j = job({ state: "Cancelled", reason_code: "cancelled_by_user", phase_refs: { preflight: "pod/p" },
    transitions: [...PRE_TR, t("Preflight", "Cancelled", 58)] });
  const m = deriveJobStages(j, evs);
  expect(m.failure).toMatchObject({ step: "preview", evidence: "from_state", notStarted: true, queued: false });
  expect(m.steps.map((s) => [s.id, s.status])).toEqual([
    ["preflight", "done"], ["preview", "cancelled"], ["confirm", "skipped"], ["exec_preflight", "skipped"], ["execution", "skipped"],
  ]);
  const pf = m.steps[0];
  expect([pf.start, pf.end]).toEqual(["2026-10-08T03:15:02Z", null]);   // 보류 시간을 사전 점검 소요로 세지 않는다
  expect(stepDuration(pf)).toBeNull();
  const pv = m.steps[1];
  expect([pv.start, pv.end, pv.notStarted]).toEqual([null, "2026-10-08T03:15:58Z", true]);
  expect(m.executionStarted).toBe(false);
  expect(m.autoOpen).toBeNull();
  // 보류 기록이 주석으로 붙는 행 = 취소로 칠한 행(카드가 스스로 모순되지 않는다)
  expect(Object.keys(annotationsFor(evs, j, 1, m.flow))).toEqual(["preview"]);
  // scan: 다음 제출은 실행
  const scan = job({ ...j, operation: "scan" });
  const ms = deriveJobStages(scan, [deferredEv("execution", 1, 30)]);
  expect(ms.steps.map((s) => [s.id, s.status])).toEqual([["preflight", "done"], ["execution", "cancelled"]]);
  expect(ms.failure).toMatchObject({ step: "execution", notStarted: true });
  expect(ms.steps[0].end).toBeNull();
  // 보류 기록이 없으면(또는 다른 잡·다른 phase 것이면) 지금까지처럼 사전 점검 중 취소
  for (const other of [[], [ev("identity_recheck_deferred", { job_id: "j9", phase: "preview" }, 30)], [deferredEv("execution", 1, 30)]]) {
    const mo = deriveJobStages(j, other);
    expect(mo.failure).toMatchObject({ step: "preflight", evidence: "from_state", notStarted: false });
    expect(mo.steps[0].end).toBe("2026-10-08T03:15:58Z");
  }
});

test("N4 재점검 통과 뒤 실행 제출 보류 중 취소(Executing→Cancelled, execution ref 없음): 재점검 완료, 실행이 시작 전 취소", () => {
  const j = job({ state: "Cancelled", reason_code: "cancelled_by_user",
    phase_refs: { preflight: "a", preview: "vcjob/b", exec_preflight: "pod/c" },
    transitions: [...TO_EXEC, t("Executing", "Cancelled", 59)] });
  const m = deriveJobStages(j, [deferredEv("execution", 1, 30), deferredEv("execution", 2, 45)]);
  expect(m.failure).toMatchObject({ step: "execution", evidence: "from_state", notStarted: true, queued: false });
  expect(m.steps.filter((s) => s.stage === "exec").map((s) => [s.id, s.status])).toEqual([
    ["exec_preflight", "done"], ["execution", "cancelled"],
  ]);
  const ep = m.steps.find((s) => s.id === "exec_preflight")!;
  expect([ep.start, ep.end]).toEqual(["2026-10-08T03:15:18Z", null]);
  expect(stepDuration(ep)).toBeNull();
  expect(m.steps.find((s) => s.id === "execution")).toMatchObject({ start: null, end: "2026-10-08T03:15:59Z" });
  expect(m.executionStarted).toBe(false);
  // 보류 기록이 없으면 지금까지처럼 재점검 중 취소
  expect(deriveJobStages(j, []).failure).toMatchObject({ step: "exec_preflight", evidence: "from_state", notStarted: false });
});

// ---- 2026-10-08 리뷰 3차 -------------------------------------------------------------------------------------------

test("3차 N3: 그룹 잡의 Preflight→Rejected artifact_base_* 는 관문 이벤트가 없으면 추정(base_ambiguous) -- 자동 열림 없음", () => {
  // stage/skew.test.tsx(잡 폴링이 요청 폴링보다 먼저 종단을 본다)·model.exp.ts A1(이벤트가 보존 기한에 지워졌다).
  // 관문(_raise_if_base_unsafe_for_groups)은 _build_spec 이 보조 gid 가 실린 잡에서만 돌린다.
  const groups = { identity: { username: "alice", uid: 1000, gid: 1000, supplementary_gids: [10010],
    supplementary_gids_status: "applied" } };
  for (const code of ["artifact_base_not_traversable", "artifact_base_group_writable"]) {
    const g = job({ state: "Rejected", reason_code: code, phase_refs: { preflight: "pod/p" }, worker_pool: groups,
      transitions: [...PRE_TR, t("Preflight", "Rejected", 9)] });
    const m = deriveJobStages(g, []);
    expect(m.failure, code).toMatchObject({ step: "preflight", evidence: "base_ambiguous", notStarted: false });
    expect(m.autoOpen).toBeNull();                       // 통과했을 수도 있는 파드 로그를 실패 로그로 열지 않는다
    expect(statuses(g)).toMatchObject({ preflight: "rejected", preview: "skipped" });
    // 이 잡의 관문 이벤트가 오면 확정(관문) -- 다른 잡의 이벤트는 근거가 아니다
    expect(deriveJobStages(g, [ev("artifact_base_unsafe_at_step", { job_id: "j1", problem: code }, 9)]).failure)
      .toMatchObject({ step: "preview", evidence: "gate", notStarted: true });
    expect(deriveJobStages(g, [ev("artifact_base_unsafe_at_step", { job_id: "j9", problem: code }, 9)]).failure?.evidence)
      .toBe("base_ambiguous");
    // 다음 제출의 보류 기록이 있으면 사전 점검 파드는 이미 통과했다 -- 마커일 수 없으니 관문(이벤트가 아직 안 와도)
    const held = deriveJobStages(g, [deferredEv("preview", 1, 5)]);
    expect(held.failure).toMatchObject({ step: "preview", evidence: "gate", notStarted: true });
    expect(held.steps[0]).toMatchObject({ status: "done", end: null });
    // scan 도 같다
    expect(deriveJobStages({ ...g, operation: "scan" }, []).failure).toMatchObject({ step: "preflight", evidence: "base_ambiguous" });
    // 그룹 없는 잡(키 부재·null·[]·모양이 틀림)은 관문이 돌지 않는다 -- 마커 확정, 로그 자동 열림(2차 N3 그대로)
    for (const wp of [undefined, null, { identity: { supplementary_gids: [] } }, { identity: { supplementary_gids: null } },
      { identity: {} }, { identity: { supplementary_gids: "10010" } }, "junk"]) {
      const n = deriveJobStages({ ...g, worker_pool: wp as never }, []);
      expect(n.failure, JSON.stringify(wp)).toMatchObject({ step: "preflight", evidence: "from_state" });
      expect(n.autoOpen).toEqual({ stage: "pre", key: "log:preflight" });
    }
    // 실행 상태는 대상 상태가 가른다(그룹이어도): Rejected = 재점검 파드 마커, Failed = 관문
    const ex = job({ state: "Rejected", reason_code: code, worker_pool: groups,
      phase_refs: { preflight: "a", preview: "vcjob/b", exec_preflight: "pod/c" }, transitions: [...TO_EXEC, t("Executing", "Rejected", 25)] });
    expect(deriveJobStages(ex, []).failure).toMatchObject({ step: "exec_preflight", evidence: "from_state" });
    expect(deriveJobStages({ ...ex, state: "Failed", transitions: [...TO_EXEC, t("Executing", "Failed", 25)] }, []).failure)
      .toMatchObject({ step: "execution", evidence: "gate", notStarted: true });
  }
  // 다른 관문 사유는 그룹 여부와 무관하게 지금까지 그대로
  const other = job({ state: "Rejected", reason_code: "identity_changed_at_step", phase_refs: { preflight: "pod/p" }, worker_pool: groups,
    transitions: [...PRE_TR, t("Preflight", "Rejected", 9)] });
  expect(deriveJobStages(other, []).failure).toMatchObject({ step: "preview", evidence: "gate" });
});

// ---- 2026-10-08 리뷰 4차 -------------------------------------------------------------------------------------------

test("4차: 그룹 잡의 마커 길 artifact_base_* 는 종단 요청 응답 + 이 잡의 사전 점검 확인 이벤트가 있으면 확정(로그 자동 열림)", () => {
  // get_request 는 요청 행 → 이벤트 순으로 읽고 stepper 는 관문 이벤트를 요청 종단보다 먼저 남긴다. 사전 점검 확인 이벤트는
  // 관문 이벤트보다 오래돼 그것이 남았으면 관문 이벤트도 남았다(보존 삭제·100건 창 모두 오래된 것부터).
  const groups = { identity: { username: "alice", uid: 1000, gid: 1000, supplementary_gids: [10010],
    supplementary_gids_status: "applied" } };
  const checked = (phase: string, jobId = "j1") => ev("identity_groups_checked", { job_id: jobId, gids: [10010], phase }, 3);
  for (const code of ["artifact_base_not_traversable", "artifact_base_group_writable"]) {
    const g = job({ state: "Rejected", reason_code: code, phase_refs: { preflight: "pod/p" }, worker_pool: groups,
      transitions: [...PRE_TR, t("Preflight", "Rejected", 9)] });
    const sure = deriveJobStages(g, [checked("preflight")], { requestTerminal: true });
    expect(sure.failure, code).toMatchObject({ step: "preflight", evidence: "from_state", notStarted: false });
    expect(sure.autoOpen).toEqual({ stage: "pre", key: "log:preflight" });
    // 하나라도 모자라면 지금까지처럼 추정
    expect(deriveJobStages(g, [checked("preflight")]).failure?.evidence, "요청 응답이 아직 비종단").toBe("base_ambiguous");
    expect(deriveJobStages(g, [checked("preflight")], { requestTerminal: false }).failure?.evidence).toBe("base_ambiguous");
    expect(deriveJobStages(g, [], { requestTerminal: true }).failure?.evidence, "이벤트가 지워짐").toBe("base_ambiguous");
    expect(deriveJobStages(g, [checked("preflight", "j9")], { requestTerminal: true }).failure?.evidence, "다른 잡")
      .toBe("base_ambiguous");
    expect(deriveJobStages(g, [checked("preview")], { requestTerminal: true }).failure?.evidence, "다른 phase")
      .toBe("base_ambiguous");
    // 관문 이벤트가 같이 있으면 관문(확정 규칙이 관문을 덮지 않는다)
    expect(deriveJobStages(g, [checked("preflight"), ev("artifact_base_unsafe_at_step", { job_id: "j1", problem: code }, 9)],
      { requestTerminal: true }).failure).toMatchObject({ step: "preview", evidence: "gate", notStarted: true });
    // scan 도 같다
    expect(deriveJobStages({ ...g, operation: "scan" }, [checked("preflight")], { requestTerminal: true }).failure)
      .toMatchObject({ step: "preflight", evidence: "from_state" });
  }
});

test("4차: 다음 단계 ref 가 남은 Preflight→Cancelled(제출과 상태 이동 사이 취소)는 그 단계가 제출된 것 -- 시작 전이라 하지 않는다", () => {
  // _submit_execution 이 set_phase_ref·mark_exec_submitted 뒤 상태를 옮기기 전에 취소가 끼면(보류가 막 풀린 틱) 전이는
  // Preflight→Cancelled 인데 execution ref 가 있다 -- 배너 「실행 전에 취소」와 executionStarted 가 엇갈리면 안 된다.
  const scan = job({ operation: "scan", state: "Cancelled", phase_refs: { preflight: "pod/a", execution: "vcjob/j1" },
    exec_submitted_at: "2026-10-08T03:15:20Z", sched_wait_seconds: null,
    transitions: [...PRE_TR, t("Preflight", "Cancelled", 21)] });
  const evs = [deferredEv("execution", 1, 8), ev("identity_groups_checked", { job_id: "j1", phase: "execution" }, 19)];
  const m = deriveJobStages(scan, evs);
  expect(m.failure).toMatchObject({ step: "execution", evidence: "from_state", notStarted: false, queued: true });
  expect(m.executionStarted).toBe(true);
  // RUNNING 을 본 적이 있으면 대기 중이라 하지 않는다
  expect(deriveJobStages({ ...scan, sched_wait_seconds: 3 }, evs).failure).toMatchObject({ step: "execution", queued: false });
  // sync 의 미리보기도 같다(미리보기 파드 ref 가 있으면 제출됐다)
  const sync = job({ state: "Cancelled", phase_refs: { preflight: "pod/a", preview: "pod/b" },
    transitions: [...PRE_TR, t("Preflight", "Cancelled", 21)] });
  expect(deriveJobStages(sync, [deferredEv("preview", 1, 8)]).failure)
    .toMatchObject({ step: "preview", evidence: "from_state", notStarted: false });
  // ref 가 없으면 4차 이전 그대로(보류 중 취소 = 시작 전, 보류 없음 = 사전 점검 취소)
  const noRef = { ...sync, phase_refs: { preflight: "pod/a" } };
  expect(deriveJobStages(noRef, [deferredEv("preview", 1, 8)]).failure).toMatchObject({ step: "preview", notStarted: true });
  expect(deriveJobStages(noRef, []).failure).toMatchObject({ step: "preflight", evidence: "from_state" });
});

// 분 단위 전이(보류는 분 단위다). model.exp.ts C1·C2·C3·B6 이식.
const tm = (from: string | null, to: string, min: number, sec: number, extra: Partial<Transition> = {}): Transition =>
  ({ from_state: from, to_state: to, at: `2026-10-08T03:${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}Z`, ...extra });
const evm = (event_type: string, payload: object, min: number, sec: number) => ({
  id: min * 100 + sec, component: "stepper", severity: "warning", event_type, message: null, payload,
  at: `2026-10-08T03:${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}Z`,
});
const deferredM = (phase: string, n: number, min: number, sec: number) =>
  evm("identity_recheck_deferred", { job_id: "j1", phase, attempt: n, max_attempts: 4 }, min, sec);
const checkedM = (phase: string, min: number, sec: number) => evm("identity_groups_checked", { job_id: "j1", phase }, min, sec);

test("3차 보류 소요: 다음 제출 보류가 풀린 뒤 성공해도 앞 파드 단계의 「소요」에 보류가 섞이지 않는다(끝 모름)", () => {
  const PRE = [tm(null, "Pending", 15, 0), tm("Pending", "Preflight", 15, 2)];
  // C1: preflight 파드는 몇 초 만에 통과, 미리보기 제출이 2분 넘게 보류됐다가 풀렸다
  const c1 = job({ state: "Succeeded", exec_submitted_at: "2026-10-08T03:18:30Z", sched_wait_seconds: 2,
    phase_refs: { preflight: "pod/a", preview: "vcjob/b", exec_preflight: "pod/c", execution: "vcjob/d" },
    transitions: [...PRE, tm("Preflight", "PreviewRunning", 17, 20), tm("PreviewRunning", "ConfirmPending", 17, 30),
      tm("ConfirmPending", "Executing", 18, 0, { actor: "alice" }), tm("Executing", "Executing", 18, 30), tm("Executing", "Succeeded", 18, 50)] });
  const evs1 = [deferredM("preview", 1, 15, 10), deferredM("preview", 2, 16, 12), checkedM("preview", 17, 19)];
  const m1 = deriveJobStages(c1, evs1);
  const pf = m1.steps.find((s) => s.id === "preflight")!;
  expect([pf.status, pf.start, pf.end]).toEqual(["done", "2026-10-08T03:15:02Z", null]);   // 예전: → 17:20 「소요 2분 18초」
  expect(stepDuration(pf)).toBeNull();
  expect(m1.steps.find((s) => s.id === "preview")).toMatchObject({ start: "2026-10-08T03:17:20Z", end: "2026-10-08T03:17:30Z" });
  // 구획 범위는 벽시계 그대로(보류는 ① 안의 일이고 주석도 ① 미리보기 행에 있다)
  expect([m1.pre.start, m1.pre.end]).toEqual(["2026-10-08T03:15:02Z", "2026-10-08T03:17:30Z"]);
  // 보류 기록이 없으면 지금까지 그대로
  expect(deriveJobStages(c1, []).steps[0].end).toBe("2026-10-08T03:17:20Z");

  // C2: 재점검 파드 통과 뒤 실행 제출이 보류됐다가 풀렸다 -- 재점검의 끝을 모른다, ② 범위는 그대로
  const c2 = job({ ...c1, transitions: [...PRE, tm("Preflight", "PreviewRunning", 15, 7), tm("PreviewRunning", "ConfirmPending", 15, 17),
    tm("ConfirmPending", "Executing", 15, 30, { actor: "alice" }), tm("Executing", "Executing", 18, 30), tm("Executing", "Succeeded", 18, 50)] });
  const m2 = deriveJobStages(c2, [deferredM("execution", 1, 15, 40), deferredM("execution", 2, 16, 45), checkedM("execution", 18, 29)]);
  const ep = m2.steps.find((s) => s.id === "exec_preflight")!;
  expect([ep.start, ep.end]).toEqual(["2026-10-08T03:15:30Z", null]);                       // 예전: → 18:30 「소요 3분」
  expect(m2.steps.find((s) => s.id === "execution")).toMatchObject({ start: "2026-10-08T03:18:30Z", end: "2026-10-08T03:18:50Z" });
  expect([m2.exec.start, m2.exec.end]).toEqual(["2026-10-08T03:15:30Z", "2026-10-08T03:18:50Z"]);
  expect(m2.steps[0].end).toBe("2026-10-08T03:15:07Z");                                     // 다른 phase 보류는 무관

  // B6: 재점검 파드 제출이 보류됐다가 풀린 뒤 재점검 중 취소 -- 시작(파드 제출 시각)을 모른다, ② 시작은 컨펌 시각 그대로
  const b6 = job({ state: "Cancelled", reason_code: "cancelled_by_user",
    phase_refs: { preflight: "pod/a", preview: "vcjob/b", exec_preflight: "pod/c" },
    transitions: [...PRE, tm("Preflight", "PreviewRunning", 15, 7), tm("PreviewRunning", "ConfirmPending", 15, 17),
      tm("ConfirmPending", "Executing", 15, 30, { actor: "alice" }), tm("Executing", "Cancelled", 18, 0)] });
  const m6 = deriveJobStages(b6, [deferredM("exec_preflight", 1, 15, 35), checkedM("exec_preflight", 16, 40)]);
  const ep6 = m6.steps.find((s) => s.id === "exec_preflight")!;
  expect([ep6.status, ep6.start, ep6.end]).toEqual(["cancelled", null, "2026-10-08T03:18:00Z"]);  // 예전: 컨펌부터 「소요 2분 30초」
  expect(stepDuration(ep6)).toBeNull();
  expect(m6.exec.start).toBe("2026-10-08T03:15:30Z");
  expect(deriveJobStages(b6, []).steps.find((s) => s.id === "exec_preflight")?.start).toBe("2026-10-08T03:15:30Z");

  // C3: 보류 뒤 미리보기 실패 -- 사전 점검 소요 없음, 실패 단계(미리보기)의 시각은 그대로
  const c3 = job({ state: "Failed", reason_code: "preview_failed", phase_refs: { preflight: "pod/a", preview: "vcjob/b" },
    transitions: [...PRE, tm("Preflight", "PreviewRunning", 17, 20), tm("PreviewRunning", "Failed", 17, 50)] });
  const m3 = deriveJobStages(c3, [deferredM("preview", 1, 15, 10), checkedM("preview", 17, 19)]);
  expect(m3.steps[0].end).toBeNull();
  expect(stepDuration(m3.steps[1])).toBe("30초");
  // scan: 실행 제출 보류 뒤 → 사전 점검의 끝 모름
  const scan = job({ operation: "scan", state: "Succeeded", exec_submitted_at: "2026-10-08T03:17:20Z", sched_wait_seconds: 0,
    phase_refs: { preflight: "pod/a", execution: "vcjob/d" },
    transitions: [...PRE, tm("Preflight", "Running", 17, 20), tm("Running", "Succeeded", 18, 0)] });
  expect(deriveJobStages(scan, [deferredM("execution", 1, 15, 10), checkedM("execution", 17, 19)]).steps[0].end).toBeNull();
  expect(deriveJobStages(scan, []).steps[0].end).toBe("2026-10-08T03:17:20Z");
});
