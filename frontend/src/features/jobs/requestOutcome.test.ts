import { expect, test } from "vitest";
import type { DataJob, RequestDetail, Transition } from "../../lib/types";
import { deriveJobStages } from "./stageModel";
import { deriveKpi, deriveOutcome, focusJob } from "./requestOutcome";

// 결과 배너·KPI 표 테스트(2026-10-08 요청 상세 재설계 §11.4). 「완료됐지만 0건」 주의는 넣지 않았다 -- 미리보기
// summary 와 실행 summary 의 files 가 같은 뜻이 아니다(dsync dry-run 은 훑은 소스 항목 수, nsync 는 계획된 변경 수,
// 실행은 처리 수) -- 둘을 비교하면 거짓 경보가 난다(오케스트레이터 결정).

const t = (from: string | null, to: string, sec: number, extra: Partial<Transition> = {}): Transition =>
  ({ from_state: from, to_state: to, at: `2026-10-08T03:15:${String(sec).padStart(2, "0")}Z`, ...extra });
const REQ: RequestDetail = {
  request_id: "r1", operation: "sync", requester_id: "alice", resource_key: "k", priority: "mid", state: "Succeeded",
  created_at: "2026-10-08T03:15:00Z", updated_at: "2026-10-08T03:16:00Z", payload: {}, commit_order: 1,
  transitions: [t(null, "Pending", 0), t("Pending", "Planned", 2), t("Planned", "Succeeded", 40)],
};
const job = (over: Partial<DataJob>): DataJob => ({
  job_id: "j1", request_id: "r1", operation: "sync", state: "Succeeded", reason_code: null,
  preview_fingerprint: null, preview_expires_at: null, result_summary: null,
  transitions: [], artifact_uri: null, phase_refs: {}, ...over,
});
const SYNC_OK_TR = [
  t(null, "Pending", 0), t("Pending", "Preflight", 2), t("Preflight", "PreviewRunning", 7),
  t("PreviewRunning", "ConfirmPending", 17), t("ConfirmPending", "Executing", 18, { actor: "alice" }),
  t("Executing", "Executing", 27), t("Executing", "Succeeded", 37),
];
const outcome = (req: Partial<RequestDetail>, jobs: DataJob[] | null) =>
  deriveOutcome({ ...REQ, ...req }, jobs, (jobs ?? []).map((j) => deriveJobStages(j, req.events)));
const kpi = (req: Partial<RequestDetail>, jobs: DataJob[] | null, now = Date.parse("2026-10-08T03:16:00Z")) =>
  deriveKpi({ ...REQ, ...req }, jobs, (jobs ?? []).map((j) => deriveJobStages(j)), now);

test("배너 제목·톤 표", () => {
  const cases: [Partial<RequestDetail>, DataJob[] | null, string, string][] = [
    [{}, [job({ state: "Succeeded", transitions: SYNC_OK_TR })], "작업이 완료되었습니다", "ok"],
    [{ state: "Failed" }, [job({ state: "Failed", reason_code: "execution_failed:rc1", phase_refs: { execution: "v" } })],
      "실행 단계에서 실패했습니다", "bad"],
    [{ state: "Rejected" }, [job({ state: "Rejected", reason_code: "destination_not_writable", phase_refs: { preflight: "p" },
      transitions: [t(null, "Pending", 0), t("Pending", "Preflight", 1), t("Preflight", "Rejected", 3)] })],
      "사전 점검 단계에서 거부되었습니다", "bad"],
    [{ state: "Planned" }, [job({ state: "ConfirmPending" })], "컨펌을 기다리고 있습니다", "action"],
    [{ state: "Failed" }, [job({ state: "PreviewExpired" })], "미리보기가 만료되어 실행되지 않았습니다", "action"],
    [{ state: "Cancelled" }, [job({ state: "Cancelled", transitions: [...SYNC_OK_TR.slice(0, 4), t("ConfirmPending", "Cancelled", 20)] })],
      "컨펌 전에 취소되었습니다", "neutral"],
    [{ state: "Rejected" }, [], "계획 단계에서 거부되었습니다", "bad"],
    [{ state: "Pending" }, [], "작업을 준비하고 있습니다", "busy"],
    [{ state: "Conflict" }, [], "같은 대상의 다른 요청과 겹쳐 실행되지 않았습니다", "bad"],
    [{ state: "Cancelled" }, [], "작업이 만들어지기 전에 취소되었습니다", "neutral"],
    [{ state: "Planned" }, null, "요청을 처리하고 있습니다", "busy"],
    [{ state: "Failed" }, null, "요청이 실패했습니다", "bad"],
    [{ state: "Planned" }, [job({ state: "Weird" })], "알 수 없는 상태입니다", "neutral"],
    [{ state: "Planned" }, [job({ state: "Preflight", phase_refs: { preflight: "p" } })], "사전 점검 중입니다", "busy"],
    [{ state: "Failed" }, [job({ state: "TimedOut", reason_code: "preview_timed_out", phase_refs: { preflight: "a", preview: "b" } })],
      "미리보기 단계에서 시간이 초과되었습니다", "bad"],
    [{ state: "Failed" }, [job({ state: "Failed", reason_code: "preflight_submit_failed:422" })],
      "사전 점검을 시작하지 못했습니다", "bad"],
    [{ state: "Rejected" }, [job({ state: "Rejected", reason_code: "ldap_unavailable", transitions: [t(null, "Pending", 0), t("Pending", "Rejected", 2)] })],
      "시작 전에 거부되었습니다", "bad"],
  ];
  for (const [req, jobs, title, tone] of cases) {
    const o = outcome(req, jobs);
    expect([o.title, o.tone], `${req.state} / ${jobs?.map((j) => j.state).join(",") ?? "모름"}`).toEqual([title, tone]);
  }
});

test("추정 근거(가장 깊은 ref·없음)면 단계를 단정하지 않는다", () => {
  const o = outcome({ state: "Failed" }, [job({ state: "Failed", phase_refs: { preflight: "a", preview: "b" } })]);
  expect(o.title).toBe("작업이 실패했습니다");
  expect(o.next?.actions).toEqual([]);
});

test("실패 지점 로그 보기는 로그가 있는 강한 근거에만, 제출 실패 문구는 phase 로그를 가리킨다", () => {
  const withLog = outcome({ state: "Rejected" }, [job({ state: "Rejected", reason_code: "destination_not_writable",
    phase_refs: { preflight: "p" }, transitions: [t("Pending", "Preflight", 1), t("Preflight", "Rejected", 3)] })]);
  expect(withLog.next?.actions).toEqual(["failLog", "newJob"]);
  expect(withLog.failTarget).toEqual({ jobId: "j1", stage: "pre", key: "log:preflight" });
  const noLog = outcome({ state: "Rejected" }, [job({ state: "Rejected", reason_code: "preflight_failed:x" })]);
  expect(noLog.next?.actions).toEqual(["newJob"]);
  const sub = outcome({ state: "Failed" }, [job({ state: "Failed", reason_code: "execution_recheck_submit_failed:422" })]);
  expect(sub.title).toBe("실행 직전 재점검을 시작하지 못했습니다");
  expect(sub.next?.text).toContain("「exec_preflight 로그」");
});

test("실행 중 실패·취소 문구는 연산별로 데이터가 바뀌었을 가능성을 말한다", () => {
  const rm = outcome({ state: "Failed", operation: "rm" }, [job({ operation: "rm", state: "Failed", reason_code: "execution_failed:x", phase_refs: { execution: "v" } })]);
  expect(rm.next?.text).toContain("이미 삭제됐을 수 있습니다");
  const scan = outcome({ state: "Failed", operation: "scan" }, [job({ operation: "scan", state: "Failed", reason_code: "execution_failed:x", phase_refs: { execution: "v" } })]);
  expect(scan.next?.text).toContain("scan 은 데이터를 바꾸지 않습니다");
  const cancelled = outcome({ state: "Cancelled" }, [job({ state: "Cancelled", phase_refs: { preflight: "a", preview: "b", exec_preflight: "c", execution: "v" },
    transitions: [...SYNC_OK_TR.slice(0, 6), t("Executing", "Cancelled", 30)] })]);
  expect(cancelled.title).toBe("실행 중에 취소되었습니다");
  expect(cancelled.next?.text).toBe("실행 중에 취소되어 일부 파일이 이미 복사됐을 수 있습니다.");
});

test("초점 잡 우선순위: 실패 > 컨펌 대기 > 진행 > 취소 > 만료 > 성공, 여럿이면 제목에 (작업 n개 중 k개)", () => {
  const ok = job({ job_id: "a", state: "Succeeded" });
  const live = job({ job_id: "b", state: "Executing" });
  const cp = job({ job_id: "c", state: "ConfirmPending" });
  const bad = job({ job_id: "d", state: "Failed", reason_code: "execution_failed:x", phase_refs: { execution: "v" } });
  expect(focusJob([ok, live, cp, bad])).toBe(3);
  expect(focusJob([ok, live, cp])).toBe(2);
  expect(focusJob([ok, live])).toBe(1);
  expect(focusJob([ok, job({ job_id: "e", state: "Cancelled" })])).toBe(1);
  expect(focusJob([])).toBeNull();
  expect(outcome({ state: "Failed" }, [ok, bad, { ...bad, job_id: "f" }]).title).toBe("실행 단계에서 실패했습니다 (작업 3개 중 2개)");
});

test("「작업 컨펌」 소유: 단건은 배너, 비배치 2개 이상은 관문 줄, 배치 자식은 아무도", () => {
  const cp = job({ state: "ConfirmPending" });
  const single = outcome({ state: "Planned" }, [cp]);
  expect(single.confirmOwner).toBe("banner");
  expect(single.next?.actions).toEqual(["confirm", "showPreview"]);
  expect(single.next?.expiry).toBe(true);
  const many = outcome({ state: "Planned" }, [cp, { ...cp, job_id: "j2" }]);
  expect(many.confirmOwner).toBe("gate");
  expect(many.title).toBe("컨펌을 기다리는 작업이 2개 있습니다");
  expect(many.next?.actions).toEqual([]);
  const batch = outcome({ state: "Planned", batch_id: "b77" }, [cp]);
  expect(batch.confirmOwner).toBe("none");
  expect(batch.next?.batchNotice).toBe(true);
  expect(batch.next?.actions).toEqual(["showPreview"]);
});

test("V2 「작업 컨펌」 소유는 초점이 아니라 비배치 ConfirmPending 수로 -- 실패 잡이 초점이어도 관문 줄이 갖는다", () => {
  const rej = job({ job_id: "a", state: "Rejected", reason_code: "destination_not_writable", phase_refs: { preflight: "p" },
    transitions: [t(null, "Pending", 0), t("Pending", "Preflight", 1), t("Preflight", "Rejected", 3)] });
  const cp = job({ job_id: "b", state: "ConfirmPending" });
  const o = outcome({ state: "Planned" }, [rej, cp]);
  expect(o.focus).toBe(0);
  expect(o.confirmOwner).toBe("gate");
  expect(o.next?.actions).not.toContain("confirm");
  expect(o.next?.text).toContain("다른 작업 1개가 컨펌을 기다립니다");
  expect(outcome({ state: "Planned", batch_id: "b77" }, [rej, cp]).confirmOwner).toBe("none");
  const failed = job({ job_id: "c", state: "Failed", reason_code: "execution_failed:x", phase_refs: { execution: "v" } });
  expect(outcome({ state: "Planned" }, [failed, cp, { ...cp, job_id: "d" }]).confirmOwner).toBe("gate");
});

// ---- 리뷰 V6~V9: 관문·스케줄 대기·제출 보류의 배너는 통과한 단계를 탓하지 않고 없는 로그를 가리키지 않는다 ----------
const PRE_TR = [t(null, "Pending", 0), t("Pending", "Preflight", 2)];
const TO_EXEC = [...PRE_TR, t("Preflight", "PreviewRunning", 7), t("PreviewRunning", "ConfirmPending", 17),
  t("ConfirmPending", "Executing", 18, { actor: "alice" })];
const ALL_REFS = { preflight: "pod/a", preview: "vcjob/b", exec_preflight: "pod/c", execution: "vcjob/d" };

test("V6·V7 관문(시작 전) 배너: 다음 단계 이름 + 사유·진단 이벤트, 로그 버튼 없음", () => {
  const a = outcome({ state: "Rejected" }, [job({ state: "Rejected", reason_code: "identity_changed_at_step",
    phase_refs: { preflight: "pod/p" }, transitions: [...PRE_TR, t("Preflight", "Rejected", 30)] })]);
  expect(a.title).toBe("미리보기를 시작하기 전에 거부되었습니다");
  expect(a.next?.text).toContain("「진단 이벤트」");
  expect(a.next?.text).not.toContain("로그");
  expect(a.next?.actions).toEqual(["newJob"]);
  expect(a.failTarget).toBeNull();
  const b = outcome({ state: "Failed" }, [job({ state: "Failed", reason_code: "identity_changed_at_step",
    phase_refs: { preflight: "a", preview: "b", exec_preflight: "c" }, transitions: [...TO_EXEC, t("Executing", "Failed", 40)] })]);
  expect(b.title).toBe("실행을 시작하기 전에 실패했습니다");
  expect(b.next?.text).toContain("데이터는 변경되지 않았습니다");
  const c = outcome({ state: "Failed" }, [job({ state: "Failed", reason_code: "privilege_not_requested",
    phase_refs: { preflight: "a", preview: "b" }, transitions: [...TO_EXEC, t("Executing", "Failed", 22)] })]);
  expect(c.title).toBe("실행 직전 재점검을 시작하기 전에 실패했습니다");
  expect(c.next?.text).toContain("이 단계의 로그는 없습니다");
  expect(c.next?.text).not.toContain("재점검 로그를 보고");
  expect(c.next?.actions).toEqual(["newJob"]);
  // 모호한 노드 제외는 단계를 단정하지 않는다
  const amb = outcome({ state: "Rejected" }, [job({ state: "Rejected", reason_code: "node_excluded_at_step",
    phase_refs: { preflight: "pod/p" }, transitions: [...PRE_TR, t("Preflight", "Rejected", 9)] })]);
  expect(amb.title).toBe("작업이 거부되었습니다");
  expect(amb.next?.actions).toEqual(["newJob"]);
  // 재점검 파드 전 취소
  const cx = outcome({ state: "Cancelled" }, [job({ state: "Cancelled", reason_code: "cancelled_by_user",
    phase_refs: { preflight: "a", preview: "b" }, transitions: [...TO_EXEC, t("Executing", "Cancelled", 19)] })]);
  expect(cx.title).toBe("실행 직전 재점검 전에 취소되었습니다");
  expect(cx.next?.text).toBe("실행 전에 취소되어 데이터는 변경되지 않았습니다.");
});

test("V8 스케줄 대기: 비종단은 「실행 대기열」, 대기 중 관문 종단은 데이터 변경 경고 없음, 대기 중 취소는 「시작됐다면」까지만", () => {
  const queued = { sched_wait_seconds: null, exec_submitted_at: "2026-10-08T03:15:27Z", phase_refs: ALL_REFS };
  const live = outcome({ state: "Planned" }, [job({ ...queued, state: "Executing", transitions: [...TO_EXEC, t("Executing", "Executing", 27)] })]);
  expect(live.title).toBe("실행 대기열에서 기다리고 있습니다");
  expect(live.next?.actions).toEqual([]);                  // 「진행 중인 로그 보기」 없음(실행 파드가 아직 없다)
  const rm = outcome({ state: "Failed", operation: "rm" }, [job({ ...queued, operation: "rm", state: "Failed",
    reason_code: "node_excluded_at_step", transitions: [...TO_EXEC, t("Executing", "Executing", 27), t("Executing", "Failed", 50)] })]);
  expect(rm.title).toBe("실행 대기 중에 실패했습니다");
  expect(rm.next?.text).not.toContain("삭제됐을 수");
  expect(rm.next?.text).not.toContain("stderr.log");
  expect(rm.next?.text).toContain("데이터는 변경되지 않았습니다");
  expect(rm.next?.actions).toEqual(["newJob"]);
  const cancel = outcome({ state: "Cancelled", operation: "rm" }, [job({ ...queued, operation: "rm", state: "Cancelled",
    reason_code: "cancelled_by_user", transitions: [...TO_EXEC, t("Executing", "Executing", 27), t("Executing", "Cancelled", 29)] })]);
  expect(cancel.title).toBe("실행 대기 중에 취소되었습니다");
  expect(cancel.next?.text).toBe("스케줄 대기 중에 취소되었습니다 — 취소 직전에 실행이 시작됐다면 일부 파일이 이미 삭제됐을 수 있습니다.");
  // 한 번이라도 RUNNING 이 관측됐으면 지금까지 그대로(실행 중 실패 문구)
  const ran = outcome({ state: "Failed", operation: "rm" }, [job({ ...queued, sched_wait_seconds: 3, operation: "rm", state: "Failed",
    reason_code: "node_excluded_at_step", transitions: [...TO_EXEC, t("Executing", "Executing", 27), t("Executing", "Failed", 50)] })]);
  expect(ran.next?.text).toContain("이미 삭제됐을 수 있습니다");
});

test("V9 제출 보류 배너: 끝난 단계가 도는 것처럼 말하지 않고, 시도 횟수와 다음에 일어날 일을 말한다", () => {
  const j = job({ state: "Preflight", phase_refs: { preflight: "pod/p" }, transitions: PRE_TR });
  const events = [1, 2, 3].map((n) => ({ id: n, component: "stepper", severity: "warning", event_type: "identity_recheck_deferred",
    message: `LDAP 재확인 불가 -- preview 제출 보류 ${n}/4`, at: `2026-10-08T03:16:0${n}Z`,
    payload: { job_id: "j1", phase: "preview", attempt: n, max_attempts: 4 } }));
  const o = outcome({ state: "Planned", events }, [j]);
  expect(o.title).toBe("미리보기 제출을 보류하고 있습니다 (LDAP 재확인 불가 3/4)");
  expect(o.tone).toBe("busy");
  expect(o.liveTarget).toBeNull();
  expect(o.next?.actions).toEqual([]);
  expect(o.next?.text).toContain("모두 실패하면 작업이 중단됩니다");
  // 보류 기록이 없으면 지금까지 그대로
  expect(outcome({ state: "Planned" }, [j]).title).toBe("사전 점검 중입니다");
});

test("보조 그룹 삭제 경고는 applied + (rm 또는 delete) 일 때만", () => {
  const ident = { supplementary_gids_status: "applied" };
  const warn = (o: Partial<DataJob>) => outcome({ state: "Planned" }, [job({ state: "ConfirmPending", ...o })]).next?.groupWarning;
  expect(warn({ operation: "rm", worker_pool: { identity: ident } })).toBe(true);
  expect(warn({ options: { delete: true }, worker_pool: { identity: ident } })).toBe(true);
  expect(warn({ worker_pool: { identity: ident } })).toBe(false);
  expect(warn({ operation: "rm", worker_pool: { identity: { supplementary_gids_status: "none" } } })).toBe(false);
});

test("요청 취소는 잡 0개 + 비종단에서만 배너 동작이다(잡 모름이면 없다)", () => {
  expect(outcome({ state: "Pending" }, []).next?.actions).toEqual(["cancelRequest"]);
  expect(outcome({ state: "Pending" }, null).next).toBeNull();
  expect(outcome({ state: "Rejected" }, []).next?.actions).toEqual(["newJob"]);
});

test("KPI: 결과 수치는 단위를 붙이고, 0 은 「0개」, 미리보기 값은 보조 줄", () => {
  const done = job({ state: "Succeeded", transitions: SYNC_OK_TR, result_summary: { files: 120, bytes: 456 },
    preview_summary: { files: 2, bytes: 10, returncode: 0 } });
  const tiles = kpi({}, [done]);
  expect(tiles.map((x) => [x.label, x.value, x.sub])).toEqual([
    ["복사한 파일", "120개", "미리보기 2개"],
    ["복사한 크기", "456 B", "미리보기 10 B"],
    ["수행시간", "40초", "실행 단계 소요 19초"],
    ["제출 대기", "2초", "플래너 배정까지"],
  ]);
  expect(kpi({}, [{ ...done, result_summary: { files: 0, bytes: 0 } }])[0].value).toBe("0개");
});

test("KPI null 전파: 한 잡이라도 모르면 합계를 지어내지 않는다", () => {
  const a = job({ job_id: "a", state: "Succeeded", result_summary: { files: 1, bytes: 1 } });
  const b = job({ job_id: "b", state: "Succeeded", result_summary: { files: null, bytes: 2 } });
  const tiles = kpi({}, [a, b]);
  expect(tiles[0]).toMatchObject({ label: "복사한 파일", value: "—", sub: "일부 작업은 아직 집계 전" });
  expect(tiles[1]).toMatchObject({ label: "복사한 크기", value: "3 B" });
});

test("KPI 실행 전 단건: 라벨이 「복사 대상」·「대상 크기」(rm 「삭제 대상」)로 바뀌고 보조 줄이 기준을 말한다", () => {
  const cp = job({ state: "ConfirmPending", preview_summary: { files: 1204, bytes: 3435973837, returncode: 0 } });
  const tiles = kpi({ state: "Planned" }, [cp]);
  expect(tiles.slice(0, 2).map((x) => [x.label, x.value, x.sub])).toEqual([
    ["복사 대상", "1,204개", "미리보기 기준 · 실행 전"],
    ["대상 크기", "3.2 GiB", "미리보기 기준 · 실행 전"],
  ]);
  const rmTiles = kpi({ state: "Planned", operation: "rm" }, [{ ...cp, operation: "rm", preview_summary: { files: 5, bytes: null } }]);
  expect(rmTiles.map((x) => x.label)).toEqual(["삭제 대상", "수행시간", "제출 대기"]);
  // 비종단 수행시간은 "째" + 1초 틱용 기준 시각
  expect(tiles[2]).toMatchObject({ label: "수행시간", value: "1분째", sub: "진행 중", elapsedFrom: "2026-10-08T03:15:00Z" });
});

test("KPI rm: 도구가 바이트를 보고하지 않으면(null) 크기 칸 자체가 없다, 숫자면 「크기」", () => {
  const rm = (bytes: number | null) => job({ operation: "rm", state: "Succeeded", result_summary: { files: 3, bytes } });
  expect(kpi({ operation: "rm" }, [rm(null)]).map((x) => x.label)).toEqual(["삭제한 항목", "수행시간", "제출 대기"]);
  expect(kpi({ operation: "rm" }, [rm(12)]).map((x) => x.label)).toEqual(["삭제한 항목", "크기", "수행시간", "제출 대기"]);
});

test("KPI 실행되지 않은 종단·잡 없음·미리보기 실패의 result_summary", () => {
  const rejected = job({ state: "Rejected", reason_code: "destination_not_writable", phase_refs: { preflight: "p" },
    transitions: [t("Pending", "Preflight", 1), t("Preflight", "Rejected", 3)] });
  expect(kpi({ state: "Rejected" }, [rejected])[0]).toMatchObject({ value: "—", sub: "실행되지 않음" });
  expect(kpi({ state: "Rejected" }, []).map((x) => x.label)).toEqual(["수행시간", "제출 대기"]);
  expect(kpi({ state: "Rejected" }, null).map((x) => x.label)).toEqual(["수행시간", "제출 대기"]);
  // 미리보기 실패 때의 result_summary 는 미리보기 summary 라 "복사한 파일" 로 세지 않는다
  const pf = job({ state: "Failed", reason_code: "preview_failed:rc", result_summary: { files: 9, bytes: 9 },
    phase_refs: { preflight: "a", preview: "b" } });
  expect(kpi({ state: "Failed" }, [pf])[0]).toMatchObject({ value: "—", sub: "실행되지 않음" });
  // 제출 대기: 첫 비-Pending 전이가 없으면 "—"
  expect(kpi({ state: "Pending", transitions: [t(null, "Pending", 0)] }, [])
    .find((x) => x.key === "submit_wait")?.value).toBe("—");
});
