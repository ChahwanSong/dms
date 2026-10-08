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
