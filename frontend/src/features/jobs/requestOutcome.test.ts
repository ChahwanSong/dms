import { expect, test } from "vitest";
import type { DataJob, RequestDetail, Transition } from "../../lib/types";
import { deriveJobStages } from "./stageModel";
import { deriveKpi, deriveOutcome, focusJob, toolNeverStarted } from "./requestOutcome";

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
  // sync 는 복사만이 아니라 삭제·교체도 이미 일어났을 수 있다(괴리 [C1] -- d189-C13 은 복사 0, 삭제 5 뒤 실패)
  expect(cancelled.next?.text).toBe("실행 중에 취소되어 일부 항목이 이미 복사·삭제됐거나 형식이 바뀌어 교체됐을 수 있습니다.");
  // 요약 v2 가 없는 sync 실행 실패도 같은 말 + 원인은 stdout.log(stderr.log 가 아니다, 괴리 [19])
  const sync = outcome({ state: "Failed" }, [job({ state: "Failed", reason_code: "execution_failed:x", phase_refs: { execution: "v" } })]);
  expect(sync.next?.text).toBe("실패 전에 일부 항목이 이미 복사·삭제됐거나 형식이 바뀌어 교체됐을 수 있습니다. "
    + "실행 로그와 실행 출력(stdout.log)에서 원인을 찾으세요.");
  expect(sync.next?.text).not.toContain("stderr.log");
  expect(rm.next?.text).not.toContain("stderr.log");
});

// ---- d192 summary v2 · 도구 실패 사유(포탈 괴리 [2]·[4]·[10]·[11]·[12]·[16]·[19]·[C1]) ---------------------------------
// 실 도구 출력(mpifileutils 7a3189c, mfu-tests/samples)을 summary v2 계약대로 옮긴 모양(StageResults.test 와 같은 출처).
const V2 = { detail_version: 2 };
const NSYNC_FAILED = { ...V2, returncode: 1, files: 23, bytes: 100, tool: "nsync", dryrun: false,
  planned: { copy: 25, mkdir: 2, symlink_update: 0, meta_update: 1, remove: 0, skipped_dst_only: 0 },
  done: { copy: 20, mkdir: 2, symlink_update: 0, meta_update: 1, remove: 0 }, copied_bytes: 100,
  failures: { total: 5, by_op: { copy: 5 } },
  failure_samples: ["r1", "r2", "r3", "r4", "r5"].map((n) => ({ op: "copy", path: `ro/${n}`, errno: 13 })),
  reason: "sync_items_failed" };
const NSYNC_PREVIEW = { ...V2, returncode: 0, files: 25 + 2 + 1, bytes: 125, tool: "nsync", dryrun: true, planned_bytes: 125,
  planned: { copy: 25, mkdir: 2, symlink_update: 0, meta_update: 1, remove: 0, skipped_dst_only: 0 } };
const EXEC_TR = [...SYNC_OK_TR.slice(0, 6), t("Executing", "Failed", 37)];
const ALL = { preflight: "a", preview: "b", exec_preflight: "c", execution: "v" };

test("실행 실패(도구 사유 + v2): 실제로 반영된 것을 말하고, 원인은 결과의 「실패 항목」과 stdout.log", () => {
  const o = outcome({ state: "Failed", reason_code: "sync_items_failed" }, [job({ state: "Failed", tool: "nsync",
    reason_code: "sync_items_failed", phase_refs: ALL, transitions: EXEC_TR, result_summary: NSYNC_FAILED,
    preview_summary: NSYNC_PREVIEW })]);
  expect(o.title).toBe("실행 단계에서 실패했습니다");
  expect(o.next?.text).toBe("실패로 끝나기 전까지 복사 20개·디렉토리 생성 2개·속성 갱신 1개가 반영됐습니다. "
    + "원인은 아래 실행 결과의 「실패 항목」과 실행 출력(stdout.log)에 있습니다.");
  expect(o.next?.actions).toEqual(["failLog"]);
  // 반영이 0 이면 그렇게 말한다(「됐을 수 있습니다」로 얼버무리지 않는다 -- 0 은 정상값)
  const zero = outcome({ state: "Failed" }, [job({ state: "Failed", tool: "nsync", reason_code: "sync_stopped_max_errors",
    phase_refs: ALL, transitions: EXEC_TR, result_summary: { ...NSYNC_FAILED,
      done: { copy: 0, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 0 }, files: 0 } })]);
  expect(zero.next?.text).toMatch(/^실패로 끝나기 전까지 반영된 항목은 없습니다\. /);
  // dsync: 복사·삭제 수
  const ds = outcome({ state: "Failed" }, [job({ state: "Failed", tool: "dsync", reason_code: "sync_items_failed",
    phase_refs: ALL, transitions: EXEC_TR, result_summary: { ...V2, returncode: 1, files: 1, bytes: 3, tool: "dsync",
      copied_items: 1, copy_failed: 1, removed: 2, copied_bytes: 3 } })]);
  expect(ds.next?.text).toMatch(/^실패로 끝나기 전까지 복사 1개·삭제 2개가 반영됐습니다\. /);
  // drm: 지운 수 + 못 지운 수
  const rm = outcome({ state: "Failed", operation: "rm" }, [job({ operation: "rm", state: "Failed", tool: "drm",
    reason_code: "rm_items_failed", phase_refs: ALL, transitions: EXEC_TR,
    result_summary: { ...V2, returncode: 1, files: 4, bytes: null, tool: "drm", removed: 4, remove_failed: 5 } })]);
  expect(rm.next?.text).toBe("실패로 끝나기 전까지 4개를 삭제했습니다. 5개는 지우지 못했습니다. "
    + "원인은 아래 실행 결과의 「실패 항목」과 실행 출력(stdout.log)에 있습니다.");
});

test("MPI 중단: 계획 수를 반영으로 말하지 않고(「됐을 수 있습니다」), 원인은 stderr 쪽(ABORT 줄은 stdout.log 에 없다)", () => {
  // d192 리뷰 portal-jobs-12·16: 중단된 dsync 는 마지막 요약 블록이 없다 -- 남은 「Items: 1」은 복사 전 계획 줄이었다. 러너는
  // 이제 수를 null 로 두지만(D6), 옛 값이 실려 와도 aborted 면 반영 수로 읽지 않는다.
  for (const result_summary of [
    { ...V2, returncode: 134, files: null, bytes: null, tool: "dsync", copy_failed: 0, aborted: true, reason: "tool_aborted" },
    { ...V2, returncode: 1, files: 1, bytes: 4194304, tool: "dsync", copied_items: 1, copy_failed: 0, aborted: true },
  ]) {
    const o = outcome({ state: "Failed" }, [job({ state: "Failed", tool: "dsync", reason_code: "tool_aborted", phase_refs: ALL,
      transitions: EXEC_TR, result_summary })]);
    expect(o.next?.text).toBe("실패 전에 일부 항목이 이미 복사·삭제됐거나 형식이 바뀌어 교체됐을 수 있습니다. "
      + "도구가 비정상 종료했습니다 — 원인은 실행 로그의 stderr 발췌나 stderr.log 의 ABORT 줄에 있습니다.");
    expect(o.next?.text).not.toContain("stdout.log");
    expect(o.next?.text).not.toContain("반영됐습니다");
  }
  // rm 도 같다(도구 이름을 「복사 도구」라 하지 않는다 -- 사유 문구는 api.ts 의 몫)
  const rm = outcome({ state: "Failed", operation: "rm" }, [job({ operation: "rm", state: "Failed", tool: "drm",
    reason_code: "tool_aborted", phase_refs: ALL, transitions: EXEC_TR,
    result_summary: { ...V2, returncode: 134, files: null, bytes: null, tool: "drm", aborted: true, reason: "tool_aborted" } })]);
  expect(rm.next?.text).toBe("실패 전에 일부 파일이 이미 삭제됐을 수 있습니다. "
    + "도구가 비정상 종료했습니다 — 원인은 실행 로그의 stderr 발췌나 stderr.log 의 ABORT 줄에 있습니다.");
  // 미리보기 중단도 stderr 쪽을 가리킨다
  const PRE_FAIL_TR = [...SYNC_OK_TR.slice(0, 3), t("PreviewRunning", "Failed", 12)];
  const pv = outcome({ state: "Failed" }, [job({ state: "Failed", tool: "dsync", reason_code: "tool_aborted",
    phase_refs: { preflight: "a", preview: "b" }, transitions: PRE_FAIL_TR,
    result_summary: { ...V2, returncode: 134, files: null, bytes: null, tool: "dsync", dryrun: true, aborted: true } })]);
  expect(pv.next?.text).toBe("도구가 비정상 종료했습니다 — 원인은 미리보기 로그의 stderr 발췌나 stderr.log 의 ABORT 줄에 있습니다. "
    + "미리보기는 dry-run 이라 데이터는 변경되지 않았습니다.");
});

test("v2 실패지만 실패 항목이 없으면 없는 「실패 항목」을 가리키지 않는다", () => {
  const o = outcome({ state: "Failed" }, [job({ state: "Failed", tool: "dsync", reason_code: "execution_failed:rc1", phase_refs: ALL,
    transitions: EXEC_TR, result_summary: { ...V2, returncode: 1, files: 1, bytes: 4, tool: "dsync", copied_items: 1,
      copy_failed: 0 } })]);
  expect(o.next?.text).toBe("실패로 끝나기 전까지 복사 1개가 반영됐습니다. 실행 로그와 실행 출력(stdout.log)에서 원인을 찾으세요.");
});

test("dsync 삭제 켬 + 지운 수 모름: 「반영된 항목은 없습니다」라 하지 않는다(null≠0, 리뷰 portal-jobs-19)", () => {
  const mk = (summary: object, options: Record<string, unknown>) => outcome({ state: "Failed" }, [job({ state: "Failed", tool: "dsync",
    reason_code: "sync_items_failed", options, phase_refs: ALL, transitions: EXEC_TR,
    result_summary: { ...V2, returncode: 1, files: 0, bytes: 0, tool: "dsync", copy_failed: 1, ...summary } })]).next?.text;
  // 복사 0 + 지운 수 모름(Removed 줄이 버려졌다) -- 아는 반영이 없으니 「됐을 수 있습니다」
  expect(mk({ copied_items: 0 }, { delete: true }))
    .toMatch(/^실패 전에 일부 항목이 이미 복사·삭제됐거나 형식이 바뀌어 교체됐을 수 있습니다\. /);
  // 복사 3 + 지운 수 모름 -- 아는 것만 말하고 모르는 것을 밝힌다
  expect(mk({ copied_items: 3 }, { delete: true }))
    .toMatch(/^실패로 끝나기 전까지 복사 3개가 반영됐습니다\(삭제 수는 알 수 없습니다\)\. /);
  // 삭제를 끈 실행은 Removed 줄이 없으면 지울 목록이 없었다 -- 0 으로 말해도 된다
  expect(mk({ copied_items: 0 }, { delete: false })).toMatch(/^실패로 끝나기 전까지 반영된 항목은 없습니다\. /);
  // 복사 수를 모르면(마지막 블록 없음) 지운 수만으로 「없습니다」라 하지 않는다
  expect(mk({ removed: 0 }, { delete: true }))
    .toMatch(/^실패 전에 일부 항목이 이미 복사·삭제됐거나 형식이 바뀌어 교체됐을 수 있습니다\. /);
  expect(mk({ removed: 2 }, { delete: true }))
    .toMatch(/^실패로 끝나기 전까지 삭제 2개가 반영됐습니다\(복사 수는 알 수 없습니다\)\. /);
});

test("quiet 실행 실패: 로그에 원인이 없다는 사실과 다시 실행하는 방법", () => {
  const o = outcome({ state: "Failed" }, [job({ state: "Failed", tool: "nsync", reason_code: "execution_failed",
    options: { quiet: true }, phase_refs: ALL, transitions: EXEC_TR,
    result_summary: { ...V2, returncode: 1, files: null, bytes: null, tool: "nsync", quiet: true } })]);
  expect(o.next?.text).toBe("실패 전에 일부 항목이 이미 복사·삭제됐거나 형식이 바뀌어 교체됐을 수 있습니다. "
    + "quiet 로 실행돼 도구가 오류·실패 항목을 남기지 않았습니다 — 원인을 보려면 quiet 를 끄고 다시 실행하세요.");
});

test("도구 시작 전 사유(실행 단계): 「이미 복사·삭제됐을 수 있다」 대신 데이터 무변경 + 새 작업", () => {
  for (const [op, reason] of [["sync", "destination_path_redirected"], ["sync", "source_path_redirected"],
    ["rm", "target_path_redirected"], ["sync", "workers_unreachable"], ["rm", "identity_groups_not_applied"],
    // d194: 러너가 mpirun 전에 미리보기와 대상 종류가 달라 거부(링크였던 경로가 디렉토리로 바뀜) -- drm 은 돌지 않았다
    ["rm", "target_changed_since_preview"]] as const) {
    const o = outcome({ state: "Failed", operation: op }, [job({ operation: op, state: "Failed", reason_code: reason,
      phase_refs: ALL, transitions: EXEC_TR, result_summary: { returncode: null, files: null, bytes: null } })]);
    expect(o.title).toBe("실행 단계에서 실패했습니다");
    expect(o.next?.text).toBe("도구가 시작되기 전에 멈춰 데이터는 바뀌지 않았습니다 — 사유를 확인한 뒤 다시 제출하세요.");
    expect(o.next?.actions).toEqual(["failLog", "newJob"]);
  }
});

test("미리보기 실패: 도구 사유 + 실패 항목이 있으면 결과의 「실패 항목」과 stdout.log, 도구 시작 전 사유면 그 사실", () => {
  const PRE_FAIL_TR = [...SYNC_OK_TR.slice(0, 3), t("PreviewRunning", "Failed", 12)];
  const pf = outcome({ state: "Failed" }, [job({ state: "Failed", tool: "nsync", reason_code: "source_scan_incomplete",
    phase_refs: { preflight: "a", preview: "b" }, transitions: PRE_FAIL_TR,
    result_summary: { ...V2, returncode: 1, files: 2, bytes: 0, tool: "nsync", dryrun: true, scan_errors: 1,
      failures: { total: 1, by_op: { scan: 1 } }, failure_samples: [{ op: "scan", path: "locked", errno: 13 }] } })]);
  expect(pf.title).toBe("미리보기 단계에서 실패했습니다");
  expect(pf.next?.text).toBe("원인은 미리보기 결과의 「실패 항목」과 미리보기 출력(stdout.log)에 있습니다. "
    + "미리보기는 dry-run 이라 데이터는 변경되지 않았습니다.");
  const old = outcome({ state: "Failed" }, [job({ state: "Failed", reason_code: "preview_failed:rc2",
    phase_refs: { preflight: "a", preview: "b" }, transitions: PRE_FAIL_TR, result_summary: { returncode: 2, files: null, bytes: null } })]);
  expect(old.next?.text).toBe("미리보기 로그와 미리보기 출력(stdout.log)에서 원인을 찾으세요. 미리보기는 dry-run 이라 데이터는 변경되지 않았습니다.");
  const wu = outcome({ state: "Failed" }, [job({ state: "Failed", reason_code: "workers_unreachable",
    phase_refs: { preflight: "a", preview: "b" }, transitions: PRE_FAIL_TR })]);
  expect(wu.next?.text).toBe("도구가 시작되기 전에 멈췄습니다 — 사유를 확인한 뒤 다시 제출하세요. 미리보기는 dry-run 이라 데이터는 변경되지 않았습니다.");
});

test("toolNeverStarted: 큐 대기 관문은 RUNNING 관측 전에만 도구 시작 전이다", () => {
  const mk = (reason: string, sched: number | null) => {
    const j = job({ state: "Failed", reason_code: reason, phase_refs: ALL, transitions: EXEC_TR, sched_wait_seconds: sched,
      exec_submitted_at: "2026-10-08T03:15:27Z" });
    return toolNeverStarted(j, deriveJobStages(j));
  };
  expect(mk("destination_path_redirected", 3)).toBe(true);
  expect(mk("workers_unreachable", 3)).toBe(true);
  expect(mk("execution_submit_failed:422", null)).toBe(true);
  expect(mk("node_excluded_at_step", null)).toBe(true);
  expect(mk("node_excluded_at_step", 3)).toBe(false);           // 한 번 RUNNING 이었다 -- 도구가 돌았을 수 있다
  expect(mk("identity_changed_at_step", 0)).toBe(false);        // 0 은 「같은 틱에 스케줄됨」(정상값) -- 관측됐다
  // 노드 CPU 재검사(_raise_if_cpu_short)도 실행 vcjob 의 PENDING 분기 관문이다(d192 리뷰 portal-jobs-24) -- 파드 재시작으로
  // 다시 PENDING 이면 도구가 이미 돌았을 수 있다. identity_missing_at_step(_note_queued_recheck 의 모양 검사)도 같다.
  expect(mk("node_cpu_insufficient_at_step", null)).toBe(true);
  expect(mk("node_cpu_insufficient_at_step", 3)).toBe(false);
  expect(mk("identity_missing_at_step", 3)).toBe(false);
  // 제출 직전에만 나오는 관문은 RUNNING 관측과 무관하게 도구 시작 전이다
  expect(mk("storage_missing_at_step", 3)).toBe(true);
  expect(mk("sync_items_failed", null)).toBe(false);
  expect(mk("execution_failed", null)).toBe(false);
});

test("컨펌 대기 배너: 미리보기가 아는 위험이 있으면 그 수를 먼저 말한다(괴리 [6])", () => {
  const tc = { ...V2, returncode: 0, files: 3, bytes: 16, tool: "nsync", dryrun: true, planned_bytes: 16,
    planned: { copy: 2, mkdir: 0, symlink_update: 0, meta_update: 0, remove: 1, skipped_dst_only: 1 },
    type_changes: { to_dir: 0, from_dir: 1 }, type_change_needs_delete: true };
  const o = outcome({ state: "Planned" }, [job({ state: "ConfirmPending", tool: "nsync", preview_summary: tc })]);
  expect(o.next?.text).toBe("미리보기에 컨펌 전에 확인할 점이 2가지 있습니다 — 「미리보기 결과 보기」에서 확인하세요. "
    + "미리보기 결과를 검토한 뒤 컨펌하면 실제 실행이 시작됩니다. 실행하지 않으려면 컨펌하지 않고 취소하세요.");
  const plain = outcome({ state: "Planned" }, [job({ state: "ConfirmPending", tool: "nsync", preview_summary: NSYNC_PREVIEW })]);
  expect(plain.next?.text).toBe("미리보기 결과를 검토한 뒤 컨펌하면 실제 실행이 시작됩니다. 실행하지 않으려면 컨펌하지 않고 취소하세요.");
  // d194: rm 대상이 심볼릭 링크면 「링크만 지운다」가 컨펌 전 확인할 점이다(같은 previewWarnings) -- 디렉토리 대상은 없음
  const rmLink = { ...V2, returncode: 0, files: 1, bytes: null, tool: "drm", dryrun: true, target_items: 1, target_kind: "link" };
  expect(outcome({ state: "Planned", operation: "rm" }, [job({ operation: "rm", state: "ConfirmPending", tool: "drm",
    preview_summary: rmLink })]).next?.text).toMatch(/^미리보기에 컨펌 전에 확인할 점이 1가지 있습니다/);
  expect(outcome({ state: "Planned", operation: "rm" }, [job({ operation: "rm", state: "ConfirmPending", tool: "drm",
    preview_summary: { ...rmLink, target_kind: "dir", files: 5, target_items: 5 } })]).next?.text).toMatch(/^미리보기 결과를 검토한 뒤/);
  // 삭제 옵션이 꺼진 nsync 의 타입 교체 삭제에도 보조 그룹 경고(rm·delete 만 보던 것을 넓혔다)
  expect(outcome({ state: "Planned" }, [job({ state: "ConfirmPending", tool: "nsync", preview_summary: tc,
    worker_pool: { identity: { supplementary_gids_status: "applied" } } as never })]).next?.groupWarning).toBe(true);
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

test("「작업 컨펌」 소유: 비배치 컨펌 대기가 있으면 늘 관문 줄(단건도 -- 배너는 「컨펌하러 가기」만), 배치 자식은 아무도", () => {
  const cp = job({ state: "ConfirmPending" });
  const single = outcome({ state: "Planned" }, [cp]);
  // 리뷰 N5: 단건이 초점이어도 창은 관문 줄에 있다(초점이 바뀌어도 열린 창이 옮겨 가지 않게). "confirm" 은 이동 버튼이다.
  expect(single.confirmOwner).toBe("gate");
  // 2026-10-12 사용자 요청: 컨펌 대기에도 취소 -- 「컨펌하지 않고 취소」(cancelJob, 배치 자식은 아래처럼 없음)
  expect(single.next?.actions).toEqual(["confirm", "showPreview", "cancelJob"]);
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

test("잡 Pending 배너: 단건은 「작업 취소」(cancelJob), 배치 자식은 없음(2026-10-12 사용자 요청)", () => {
  const pending = job({ state: "Pending" });
  expect(outcome({ state: "Planned" }, [pending]).next?.actions).toEqual(["cancelJob"]);
  expect(outcome({ state: "Planned", batch_id: "b77" }, [pending]).next?.actions).toEqual([]);
  // 진행 중 단계는 종전 그대로(잡 카드에 「취소」가 있다)
  expect(outcome({ state: "Planned" }, [job({ state: "Preflight", phase_refs: { preflight: "p" } })]).next?.actions)
    .not.toContain("cancelJob");
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

test("N3 artifact_base_* 파드 마커 거부는 그 파드 단계를 탓하고 로그로 데려간다, 관문 이벤트가 있으면 지금까지처럼 다음 단계 시작 전", () => {
  const pre = job({ state: "Rejected", reason_code: "artifact_base_not_traversable", phase_refs: { preflight: "pod/p" },
    transitions: [...PRE_TR, t("Preflight", "Rejected", 9)] });
  const marker = outcome({ state: "Rejected", events: [] }, [pre]);
  expect(marker.title).toBe("사전 점검 단계에서 거부되었습니다");
  expect(marker.next?.actions).toEqual(["failLog", "newJob"]);
  expect(marker.failTarget).toEqual({ jobId: "j1", stage: "pre", key: "log:preflight" });
  expect(marker.next?.text).not.toContain("「진단 이벤트」");
  const ex = job({ state: "Rejected", reason_code: "artifact_base_group_writable",
    phase_refs: { preflight: "a", preview: "b", exec_preflight: "c" }, transitions: [...TO_EXEC, t("Executing", "Rejected", 25)] });
  const exo = outcome({ state: "Rejected", events: [] }, [ex]);
  expect(exo.title).toBe("실행 직전 재점검 단계에서 거부되었습니다");
  expect(exo.failTarget).toEqual({ jobId: "j1", stage: "exec", key: "log:exec_preflight" });
  const gateEvents = [{ id: 1, component: "stepper", severity: "error", event_type: "artifact_base_unsafe_at_step",
    message: "artifact_base_not_traversable job=j1", at: "2026-10-08T03:15:09Z",
    payload: { job_id: "j1", problem: "artifact_base_not_traversable" } }];
  const gate = outcome({ state: "Rejected", events: gateEvents }, [pre]);
  expect(gate.title).toBe("미리보기를 시작하기 전에 거부되었습니다");
  expect(gate.next?.text).toContain("「진단 이벤트」");
  expect(gate.failTarget).toBeNull();
});

test("3차 N3 그룹 잡의 artifact_base_* 거부에 관문 이벤트가 없으면: 단계·로그를 단정하지 않고 관리자에게 전달하라고 말한다", () => {
  const pre = job({ state: "Rejected", reason_code: "artifact_base_not_traversable", phase_refs: { preflight: "pod/p" },
    worker_pool: { identity: { supplementary_gids: [10010], supplementary_gids_status: "applied" } },
    transitions: [...PRE_TR, t("Preflight", "Rejected", 9)] });
  const amb = outcome({ state: "Rejected", events: [] }, [pre]);
  expect(amb.title).toBe("작업이 거부되었습니다");
  expect(amb.next?.text).toBe("작업 기록(artifact) 저장소 권한 점검에서 막혀 중단되었습니다 — 사유를 관리자에게 전달한 뒤 다시 제출하세요. 데이터는 변경되지 않았습니다.");
  expect(amb.next?.actions).toEqual(["newJob"]);
  expect(amb.failTarget).toBeNull();
  // 관문 이벤트가 오면 지금까지처럼 「미리보기를 시작하기 전에 거부되었습니다」
  const gateEvents = [{ id: 1, component: "stepper", severity: "error", event_type: "artifact_base_unsafe_at_step",
    message: "artifact_base_not_traversable job=j1", at: "2026-10-08T03:15:09Z",
    payload: { job_id: "j1", problem: "artifact_base_not_traversable" } }];
  expect(outcome({ state: "Rejected", events: gateEvents }, [pre]).title).toBe("미리보기를 시작하기 전에 거부되었습니다");
});

test("3차 잡 모름 배너 부제: 실패 문구 없이 재조회 중이면(잡 구획이 중립 골격) 「불러오는 중」, 실패면 「불러오지 못해」", () => {
  const fail = "작업 정보를 불러오지 못해 단계별 결과를 보일 수 없습니다";
  expect(outcome({ state: "Planned" }, null).subtitle).toBe(fail);
  const loading = deriveOutcome({ ...REQ, state: "Planned" }, null, [], { jobsLoading: true });
  expect([loading.title, loading.subtitle]).toEqual(["요청을 처리하고 있습니다", "작업 정보를 불러오는 중입니다"]);
  expect(deriveOutcome({ ...REQ, state: "Failed" }, null, [], { jobsLoading: true }).subtitle).toBe("작업 정보를 불러오는 중입니다");
  // 잡을 알면 무관하다
  expect(deriveOutcome(REQ, [job({ state: "Succeeded", transitions: SYNC_OK_TR })], [deriveJobStages(job({ state: "Succeeded", transitions: SYNC_OK_TR }))],
    { jobsLoading: true }).title).toBe("작업이 완료되었습니다");
});

test("N4 다음 제출 보류 중 취소: 통과한 단계 중이 아니라 다음 단계 전에 취소, 데이터 변경 없음", () => {
  const deferred = (phase: string, n: number) => ({ id: n, component: "stepper", severity: "warning",
    event_type: "identity_recheck_deferred", message: null, at: `2026-10-08T03:15:${20 + n}Z`,
    payload: { job_id: "j1", phase, attempt: n, max_attempts: 4 } });
  const pre = job({ state: "Cancelled", reason_code: "cancelled_by_user", phase_refs: { preflight: "pod/p" },
    transitions: [...PRE_TR, t("Preflight", "Cancelled", 58)] });
  const a = outcome({ state: "Cancelled", events: [deferred("preview", 1), deferred("preview", 2)] }, [pre]);
  expect(a.title).toBe("미리보기 전에 취소되었습니다");
  expect(a.next?.text).toBe("실행 전에 취소되어 데이터는 변경되지 않았습니다.");
  const scan = outcome({ state: "Cancelled", operation: "scan", events: [deferred("execution", 1)] }, [{ ...pre, operation: "scan" }]);
  expect(scan.title).toBe("실행 전에 취소되었습니다");
  const ex = job({ state: "Cancelled", reason_code: "cancelled_by_user", phase_refs: { preflight: "a", preview: "b", exec_preflight: "c" },
    transitions: [...TO_EXEC, t("Executing", "Cancelled", 59)] });
  const b = outcome({ state: "Cancelled", events: [deferred("execution", 1)] }, [ex]);
  expect(b.title).toBe("실행 전에 취소되었습니다");
  expect(b.next?.text).toBe("실행 전에 취소되어 데이터는 변경되지 않았습니다.");
  // 보류 기록이 없으면 지금까지 그대로
  expect(outcome({ state: "Cancelled", events: [] }, [pre]).title).toBe("사전 점검 중에 취소되었습니다");
  expect(outcome({ state: "Cancelled", events: [] }, [ex]).title).toBe("실행 직전 재점검 중에 취소되었습니다");
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
  // 옛 3키 요약(상세 없음) -- 미리보기 보조 줄은 뜻을 단정하지 않는 「미리보기」, 라벨도 중립(「항목 수」·「크기」 -- 실행 결과
  // 타일과 같은 이름, 옛 nsync files 는 계획 합이라 「반영한」이 아니다, 리뷰 portal-jobs-20·contract-37)
  expect(tiles.map((x) => [x.label, x.value, x.sub])).toEqual([
    ["항목 수", "120개", "미리보기 2개"],
    ["크기", "456 B", "미리보기 10 B"],
    ["수행시간", "40초", "실행 단계 소요 19초"],
    ["제출 대기", "2초", "플래너 배정까지"],
  ]);
  expect(kpi({}, [{ ...done, result_summary: { files: 0, bytes: 0 } }])[0].value).toBe("0개");
});

test("KPI v2 sync: 「반영한 항목」 + 종류별 내역 + 계획(nsync) / 소스 전체(dsync -- 크기 미리보기 없음)", () => {
  const ns = job({ state: "Failed", tool: "nsync", reason_code: "sync_items_failed", phase_refs: ALL, transitions: EXEC_TR,
    result_summary: NSYNC_FAILED, preview_summary: NSYNC_PREVIEW });
  expect(kpi({ state: "Failed" }, [ns]).slice(0, 2).map((x) => [x.label, x.value, x.sub])).toEqual([
    ["반영한 항목", "23개", "복사 20 · 디렉토리 생성 2 · 속성 갱신 1 (계획 28개)"],
    ["복사한 크기", "100 B", "계획 125 B"],
  ]);
  const ds = job({ state: "Succeeded", tool: "dsync", transitions: SYNC_OK_TR,
    result_summary: { ...V2, returncode: 0, files: 1, bytes: 2, tool: "dsync", copied_items: 1, copied_bytes: 2, removed: 3 },
    preview_summary: { ...V2, returncode: 0, files: 6, bytes: null, tool: "dsync", dryrun: true, source_items: 6 } });
  expect(kpi({}, [ds]).slice(0, 2).map((x) => [x.label, x.value, x.sub])).toEqual([
    ["반영한 항목", "1개", "복사 기준 · 삭제 3개 별도 (소스 전체 6개)"],
    ["복사한 크기", "2 B", null],
  ]);
  // 여러 잡의 미리보기 뜻이 다르면(nsync 계획 + dsync 소스 전체) 합을 보조 줄로 붙이지 않는다
  const mixed = kpi({}, [{ ...ns, job_id: "a", state: "Succeeded", transitions: SYNC_OK_TR }, { ...ds, job_id: "b" }]);
  expect(mixed[0]).toMatchObject({ label: "반영한 항목", value: "24개", sub: null });
  // 옛 요약이 하나라도 섞이면(롤아웃 중 옛 러너) 합계 라벨은 중립 -- 그 수는 「반영한」 뜻이 아니다
  const old = job({ job_id: "c", state: "Succeeded", transitions: SYNC_OK_TR, result_summary: { returncode: 0, files: 4, bytes: 5 } });
  expect(kpi({}, [{ ...ds, job_id: "b" }, old]).slice(0, 2).map((x) => [x.label, x.value]))
    .toEqual([["항목 수", "5개"], ["크기", "7 B"]]);
});

test("KPI v2 rm: 「삭제한 항목」 + 「못 지운 항목」(아는 잡이 있을 때만 -- 성공한 drm 은 칸 없음)", () => {
  const part = job({ operation: "rm", state: "Failed", tool: "drm", reason_code: "rm_items_failed", phase_refs: ALL,
    transitions: EXEC_TR, preview_summary: { ...V2, returncode: 0, files: 9, bytes: null, tool: "drm", target_items: 9 },
    result_summary: { ...V2, returncode: 1, files: 4, bytes: null, tool: "drm", removed: 4, remove_failed: 5 } });
  expect(kpi({ state: "Failed", operation: "rm" }, [part]).map((x) => [x.label, x.value, x.sub])).toEqual([
    ["삭제한 항목", "4개", "삭제 대상 9개"],
    ["못 지운 항목", "5개", "결과의 「실패 항목」 참고"],
    ["수행시간", "40초", "실행 단계 소요 19초"],
    ["제출 대기", "2초", "플래너 배정까지"],
  ]);
  const ok = { ...part, state: "Succeeded", reason_code: null, transitions: SYNC_OK_TR,
    result_summary: { ...V2, returncode: 0, files: 9, bytes: null, tool: "drm", removed: 9 } };
  expect(kpi({ operation: "rm" }, [ok]).map((x) => x.label)).toEqual(["삭제한 항목", "수행시간", "제출 대기"]);
});

test("KPI rm 대상이 링크(d194 target_kind): 실행 전 「삭제 대상」·실행 뒤 「삭제한 항목」 아래 「링크만 삭제」", () => {
  const LINK_PREVIEW = { ...V2, returncode: 0, files: 1, bytes: null, tool: "drm", dryrun: true, target_items: 1, target_kind: "link" };
  const cp = job({ operation: "rm", state: "ConfirmPending", tool: "drm", preview_summary: LINK_PREVIEW });
  expect(kpi({ state: "Planned", operation: "rm" }, [cp])[0])
    .toMatchObject({ label: "삭제 대상", value: "1개", sub: "심볼릭 링크 · 링크만 삭제 (미리보기)" });
  const done = job({ operation: "rm", state: "Succeeded", tool: "drm", transitions: SYNC_OK_TR, preview_summary: LINK_PREVIEW,
    result_summary: { ...V2, returncode: 0, files: 1, bytes: null, tool: "drm", removed: 1, target_kind: "link" } });
  expect(kpi({ operation: "rm" }, [done])[0])
    .toMatchObject({ label: "삭제한 항목", value: "1개", sub: "심볼릭 링크 · 링크만 삭제 (삭제 대상 1개)" });
  // 실행 summary 에 종류가 없으면(옛 러너 -- 묶기도 없었다) 미리보기 것으로 메우지 않는다: 그 사이 디렉토리로 바뀌어 트리를
  // 지웠을 수 있으니 「링크만」이라 말할 근거가 없다(d194 리뷰)
  const oldExec = { ...done, result_summary: { ...V2, returncode: 0, files: 1, bytes: null, tool: "drm", removed: 1 } };
  expect(kpi({ operation: "rm" }, [oldExec])[0].sub).toBe("삭제 대상 1개");
  // 디렉토리 대상은 예전 그대로
  const dir = { ...done, preview_summary: { ...LINK_PREVIEW, files: 4, target_items: 4, target_kind: "dir" },
    result_summary: { ...V2, returncode: 0, files: 4, bytes: null, tool: "drm", removed: 4, target_kind: "dir" } };
  expect(kpi({ operation: "rm" }, [dir])[0]).toMatchObject({ label: "삭제한 항목", value: "4개", sub: "삭제 대상 4개" });
});

test("KPI null 전파: 한 잡이라도 모르면 합계를 지어내지 않는다", () => {
  const a = job({ job_id: "a", state: "Succeeded", result_summary: { files: 1, bytes: 1 } });
  const b = job({ job_id: "b", state: "Succeeded", result_summary: { files: null, bytes: 2 } });
  const tiles = kpi({}, [a, b]);
  expect(tiles[0]).toMatchObject({ label: "항목 수", value: "—", sub: "일부 작업은 아직 집계 전" });
  expect(tiles[1]).toMatchObject({ label: "크기", value: "3 B" });
});

test("KPI 실행 전 단건: 미리보기 값을 그 뜻의 라벨로(nsync 변경 예정·dsync 소스 전체·모름 미리보기 항목, rm 「삭제 대상」)", () => {
  const cp = job({ state: "ConfirmPending", preview_summary: { files: 1204, bytes: 3435973837, returncode: 0 } });
  const tiles = kpi({ state: "Planned" }, [cp]);
  expect(tiles.slice(0, 2).map((x) => [x.label, x.value, x.sub])).toEqual([
    ["미리보기 항목", "1,204개", "미리보기 기준 · 실행 전"],
    ["대상 크기", "3.2 GiB", "미리보기 기준 · 실행 전"],
  ]);
  expect(kpi({ state: "Planned" }, [{ ...cp, tool: "nsync", preview_summary: NSYNC_PREVIEW }]).slice(0, 2)
    .map((x) => [x.label, x.value, x.sub])).toEqual([
    ["변경 예정", "28개", "복사 25 · 디렉토리 생성 2 · 속성 갱신 1 (미리보기)"],
    ["복사할 크기", "125 B", "미리보기 기준 · 실행 전"],
  ]);
  expect(kpi({ state: "Planned" }, [{ ...cp, tool: "dsync",
    preview_summary: { ...V2, returncode: 0, files: 6, bytes: null, tool: "dsync", dryrun: true, source_items: 6 } }]).slice(0, 2)
    .map((x) => [x.label, x.value, x.sub])).toEqual([
    ["소스 전체", "6개", "변경 수는 실행 후 확정"],
    ["복사한 크기", "—", "실행 후 집계"],
  ]);
  const rmTiles = kpi({ state: "Planned", operation: "rm" }, [{ ...cp, operation: "rm", preview_summary: { files: 5, bytes: null } }]);
  expect(rmTiles.map((x) => x.label)).toEqual(["삭제 대상", "수행시간", "제출 대기"]);
  expect(rmTiles[0]).toMatchObject({ value: "5개", sub: "미리보기 기준 · 실행 전" });
  // rm 미리보기 0 = 대상 경로를 찾지 못함(drm walk 는 빈 디렉터리도 대상 자신을 세 1, 2026-10-12)
  const rm0 = kpi({ state: "Planned", operation: "rm" }, [{ ...cp, operation: "rm", preview_summary: { files: 0, bytes: null } }]);
  expect(rm0[0]).toMatchObject({ label: "삭제 대상", value: "0개", sub: "미리보기: 대상 경로를 찾지 못함" });
  // 비종단 수행시간은 "째" + 1초 틱용 기준 시각. 컨펌 대기 중이면 사람이 기다린 시간이 섞였다고 말한다.
  expect(tiles[2]).toMatchObject({ label: "수행시간", value: "1분째", sub: "컨펌 대기 중 (대기 시간 포함)",
    elapsedFrom: "2026-10-08T03:15:00Z" });
  // 기계가 도는 중이면 「진행 중」
  const pv = job({ state: "PreviewRunning", phase_refs: { preflight: "a", preview: "b" } });
  expect(kpi({ state: "Planned" }, [pv]).find((x) => x.key === "duration")?.sub).toBe("진행 중");
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

test("Succeeded 부제는 끝난 시각만(단계 나열은 단계 구획이 말한다) -- 끝난 시각을 모르면 null", () => {
  // 잡의 마지막 종단 전이 시각 → 없으면 요청 completed_at → 그것도 없으면 부제 없음.
  expect(outcome({}, [job({ state: "Succeeded", transitions: [...SYNC_OK_TR.slice(0, 6), t("Executing", "Succeeded", 40)] })])
    .subtitle).toBe("2026-10-08 12:15:40 KST 완료");
  expect(outcome({ completed_at: "2026-10-08T03:15:40Z" }, [job({ state: "Succeeded" })]).subtitle)
    .toBe("2026-10-08 12:15:40 KST 완료");
  expect(outcome({}, [job({ state: "Succeeded" })]).subtitle).toBeNull();
});
