import { describe, expect, test } from "vitest";
import type { DataJob, RequestDetail } from "../../lib/types";
import {
  deriveRequestSpec, nodesText, normalizeOptions, priorityText, rawText, resolvePrivilege, type PrivilegeInput,
} from "./requestSpec";
import { PRIV_ROOT_HEAD, PRIV_USER, SYNC_OPTION_HELP } from "./optionRules";

// 「요청 내용」 카드 판정(requestSpec, 순수) 표 테스트(2026-10-08). 입력은 DB 신뢰 경계 밖이라 변조 모양도 함께 본다.

const SYNC_PAYLOAD = { source_storage: "cephfs-dms", source: "ldap-e2e/proj-shared",
  destination_storage: "cephfs-dms", destination: "dms_test/out", options: { batch_files: 1000000, bufsize: 4194304 } };
const REQ = (over: Record<string, unknown> = {}) => ({
  request_id: "r1", operation: "sync", requester_id: "alice", resource_key: "k", priority: "mid", state: "Succeeded",
  created_at: "2026-10-08T03:15:00Z", updated_at: "2026-10-08T03:16:00Z", commit_order: 1, transitions: [],
  payload: SYNC_PAYLOAD, ...over,
}) as unknown as RequestDetail;
const ALICE = { username: "alice", uid: 10001, gid: 10000, groups: ["dmsproj", "dmsusers"], privileged: false,
  supplementary_gids: [10010], supplementary_gids_status: "applied", supplementary_gids_excluded: [], supplementary_gids_found: 1 };
const ROOT_IDENT = { username: "mason", uid: 0, gid: 0, groups: [], privileged: true, supplementary_gids: [],
  supplementary_gids_status: "privileged" };
const JOB = (over: Record<string, unknown> = {}) => ({
  job_id: "j1", request_id: "r1", operation: "sync", state: "Succeeded", reason_code: null, preview_fingerprint: null,
  preview_expires_at: null, result_summary: null, transitions: [], artifact_uri: null, tool: "dsync",
  source_storage: "cephfs-dms", destination_storage: "cephfs-dms", storage_name: null,
  worker_pool: { tool: "dsync", node_count: 4, process_count: 8, queue: "dms-data", priority_class: "dms-mid",
    candidates: { primary: ["dms-w2", "dms-w3", "dms-w4", "dms-w5"] }, identity: ALICE },
  ...over,
}) as unknown as DataJob;
const spec = (req: Record<string, unknown> = {}, jobs: DataJob[] | null = [JOB()]) => deriveRequestSpec(REQ(req), jobs, {});
// 고정 단위는 줄바꿈 없는 공백(U+00A0)으로 묶여 있다 -- 글자 비교는 보통 공백으로 접어서 한다.
const sp = (s: string) => s.replace(/ /g, " ");
const USER_HEAD = `root 아님 — ${PRIV_USER}`;

// ---- 1·2 권한 판정표 + 배지 -------------------------------------------------------------------------------------
const P = (over: Partial<PrivilegeInput>): PrivilegeInput => ({
  op: "sync", planned: null, askedRoot: false, batch: false, jobs: [], reqTerminal: false, authMethod: "session", ...over });
const B1 = "배치 항목 — 배치를 만든 관리자에게 root 자격이 있으면 root(특권), 없으면 실행 신원 권한으로 실행됩니다(계획 단계에서 확정)";
const LEGACY_NOTE = "요청 기록에는 root 지정이 없습니다 — 2026-09-30 규칙 변경(root 는 명시 요청만) 전에 계획된 작업이거나 비정상 기록입니다.";
const LEGACY_LIVE = "요청 기록에는 root 지정이 없습니다 — 2026-09-30 규칙 변경 전에 계획된 작업이거나 비정상 기록이며, 이 상태의 root 실행은 서버가 다음 단계 제출 전에 거부합니다.";

describe("resolvePrivilege", () => {
  test.each<[string, Partial<PrivilegeInput>, string, string, "run" | "requested" | null]>([
    // 실행 권한 칸은 root 라는 사실만 -- sync 의 소유 결과는 같은 카드의 「목적지 소유」 행이 말한다(반복 없음).
    ["R1 sync", { planned: true, askedRoot: true, jobs: [{}] }, PRIV_ROOT_HEAD, "bad", "run"],
    ["R1 scan", { op: "scan", planned: true, askedRoot: true, jobs: [{}] }, PRIV_ROOT_HEAD, "bad", "run"],
    ["R1 rm", { op: "rm", planned: true, askedRoot: true, jobs: [{}] }, PRIV_ROOT_HEAD, "bad", "run"],
    // 「요청됨」은 lead 만 빨강(badLead) -- 「실행되지 않았습니다」 같은 설명까지 경보색으로 칠하지 않는다.
    ["R2 잡 모름", { askedRoot: true, jobs: null }, "root(특권) 요청됨", "badLead", "requested"],
    ["R3 계획 전", { askedRoot: true, jobs: [] }, "root(특권) 요청됨 — 작업이 계획되면 확정됩니다", "badLead", "requested"],
    ["R4 잡 없이 종단", { askedRoot: true, jobs: [], reqTerminal: true },
      "root(특권) 요청됨 — 작업이 만들어지지 않아 실행되지 않았습니다", "badLead", "requested"],
    ["R5 잡에 권한 정보 없음", { askedRoot: true, jobs: [{}] }, "root(특권) 요청됨 — 작업 기록에 권한 정보가 없습니다", "badLead", "requested"],
    // 비 root 는 「root 아님」으로 확정한다(배지는 없다 -- 카드 문장만).
    ["U 계획됨", { planned: false, jobs: [{}] }, USER_HEAD, "plain", null],
    ["U 단건 의도(계획 전)", { jobs: [] }, USER_HEAD, "plain", null],
    ["B1 잡 모름(비종단)", { batch: true, jobs: null }, B1, "plain", null],
    ["B1 계획 전", { batch: true, jobs: [] }, B1, "plain", null],
    ["B 잡 모름 + 종단 = 확인 불가(미래 시제 금지)", { batch: true, jobs: null, reqTerminal: true },
      "배치 항목 — 작업 기록을 불러오지 못해 실행 권한을 확인할 수 없습니다", "plain", null],
    ["B2 잡 없이 종단", { batch: true, jobs: [], reqTerminal: true }, "배치 항목 — 작업이 만들어지지 않아 실행되지 않았습니다", "plain", null],
    ["B3 잡에 권한 정보 없음", { batch: true, jobs: [{}] }, "배치 항목 — 작업 기록에 권한 정보가 없습니다", "plain", null],
  ])("%s", (_n, over, head, tone, badge) => {
    const v = resolvePrivilege(P(over));
    expect(v.privilege.head).toEqual({ text: head, tone });
    expect(v.badge).toBe(badge);
  });

  test("rm root 는 빨간 보조 줄 「삭제가 root 권한으로 수행됩니다」", () => {
    expect(resolvePrivilege(P({ op: "rm", planned: true, askedRoot: true, jobs: [{}] })).privilege.subs)
      .toEqual([{ text: "삭제가 root 권한으로 수행됩니다", tone: "bad" }]);
  });

  test("배치 자식: root 로 계획됨·강등의 보조 줄", () => {
    const g = resolvePrivilege(P({ batch: true, planned: true, jobs: [{}] }));
    expect(g.privilege.head.text).toBe(PRIV_ROOT_HEAD);
    expect(g.privilege.subs).toEqual([{ text: "배치 항목 — 배치를 만든 관리자의 root 자격이 적용됐습니다.", tone: "sub" }]);
    expect(g.badge).toBe("run");
    const h = resolvePrivilege(P({ batch: true, planned: false, jobs: [{}], authMethod: "token" }));
    expect(h.privilege.head.text).toBe(USER_HEAD);
    expect(h.privilege.subs).toEqual([
      { text: "배치 항목 — 배치를 만든 관리자에게 root 자격이 없어 실행 신원 권한이 적용됐습니다.", tone: "sub" },
      { text: "공유 토큰(API)으로 제출 — root 실행 불가", tone: "sub" },
    ]);
  });

  test("공유 토큰 배치는 계획 전에도 비 root 확정 -- 「자격이 있으면 root」와 「root 실행 불가」가 한 칸에서 다투지 않는다", () => {
    for (const jobs of [[], null, [{}]]) {
      const v = resolvePrivilege(P({ batch: true, jobs, authMethod: "token" }));
      expect(v.kind).toBe("user");
      expect(v.privilege.head).toEqual({ text: USER_HEAD, tone: "plain" });
      expect(v.privilege.subs).toEqual([{ tone: "sub",
        text: "배치 항목 — 공유 토큰(API)으로 만든 배치라 root 자격이 없어 실행 신원 권한으로 실행됩니다." }]);
      expect(v.badge).toBeNull();
    }
    // run_as_root 를 명시한 토큰 배치 자식은 「요청됨」(planner 가 privileged_not_authorized 로 거부한다)
    expect(resolvePrivilege(P({ batch: true, askedRoot: true, jobs: [], authMethod: "token" })).badge).toBe("requested");
  });

  test("2026-09-30 이전 관리자 행(run_as_root 없이 root 로 계획됨): 비정상이라 단정하지 않고, 「서버가 거부」는 끝나지 않은 작업에만", () => {
    // 끝난 작업 -- 그때 규칙대로 정상 실행된 기록일 수 있다(설명 줄, 경보색 아님)
    const done = resolvePrivilege(P({ planned: true, jobs: [{}], jobTerminal: true }));
    expect(done.privilege.subs).toEqual([{ tone: "sub", text: LEGACY_NOTE }]);
    expect(done.badge).toBe("run");
    // 아직 도는 작업 -- stepper 가 다음 제출 직전에 끊는다(PrivilegeNotRequestedAtStep)
    for (const jobTerminal of [false, null, undefined]) {
      expect(resolvePrivilege(P({ planned: true, jobs: [{}], jobTerminal })).privilege.subs)
        .toEqual([{ tone: "attn", text: LEGACY_LIVE }]);
    }
    expect(resolvePrivilege(P({ planned: false, askedRoot: true, jobs: [{}] })).privilege.subs).toEqual([{ tone: "attn",
      text: "요청은 root(특권)였지만 작업은 실행 신원 권한으로 계획됐습니다 — 비정상 기록입니다." }]);
  });

  test("레거시 행 전체(Succeeded, 관리자 mason → alice 신원, payload 에 run_as_root 없음)", () => {
    const s = spec({ requester_id: "mason", created_at: "2026-09-20T01:00:00Z",
      payload: { ...SYNC_PAYLOAD, owner_username: "alice" } },
    [JOB({ worker_pool: { tool: "dsync", node_count: 4, process_count: 8,
      identity: { username: "alice", uid: 0, gid: 0, groups: [], privileged: true } } })]);
    expect(s.badge).toBe("run");
    expect(s.privilege).toEqual({ head: { text: PRIV_ROOT_HEAD, tone: "bad" }, subs: [{ tone: "sub", text: LEGACY_NOTE }] });
    expect(s.privilege.subs.map((l) => l.text).join("")).not.toMatch(/거부/);
    expect(s.ownership).toBe("소스의 소유자·그룹 그대로(root 실행)");   // 실제로 root 로 돌았다
  });

  test("token 줄은 계획된 배치 자식(강등)에서만 -- 단건·root 배치·세션 배치에는 없다", () => {
    expect(resolvePrivilege(P({ planned: false, jobs: [{}], authMethod: "token" })).privilege.subs).toEqual([]);
    expect(resolvePrivilege(P({ batch: true, planned: true, jobs: [{}], authMethod: "token" })).privilege.subs.map((l) => l.text))
      .toEqual(["배치 항목 — 배치를 만든 관리자의 root 자격이 적용됐습니다."]);
    for (const a of ["session", null, undefined, "weird"])
      expect(resolvePrivilege(P({ batch: true, jobs: [], authMethod: a })).privilege.subs).toEqual([]);
  });

  test("identity.privileged 가 문자열 \"true\" 면 모름 -- 요청 의도(단건 비 root)로 떨어진다", () => {
    const s = spec({}, [JOB({ worker_pool: { identity: { ...ALICE, privileged: "true", uid: 0, gid: 0 } } })]);
    expect(s.privilege.head.text).toBe(USER_HEAD);
    expect(s.badge).toBeNull();
  });

  test("배치 판정은 서버 `if req.get(\"batch_id\")`(파이썬 truthy) 미러 -- 공백 id 도 배치(계획 전엔 미정)", () => {
    const pending = (batch_id: unknown) => spec({ state: "Pending", requester_id: "mason", batch_id }, []);
    for (const id of ["b1", " ", "0", [1], { a: 1 }, 5]) {
      const s = pending(id);
      expect(s.privilege.head.text).toBe(B1);
      expect(s.ownership).toBeNull();   // root 여부 미정 -- 소유를 단정하지 않는다
    }
    for (const id of [null, undefined, "", 0, false, [], {}]) expect(pending(id).privilege.head.text).toBe(USER_HEAD);
  });
});

// ---- 3 실행 신원 --------------------------------------------------------------------------------------------------
describe("실행 신원", () => {
  test("이름 우선순위: identity → precondition.owner → owner_username → requester", () => {
    const pl = { ...SYNC_PAYLOAD, owner_username: "carol" };
    expect(spec({ payload: pl }, [JOB({ precondition: { owner: "bob" } })]).runAs.name).toBe("alice");
    expect(spec({ payload: pl }, [JOB({ worker_pool: null, precondition: { owner: "bob" } })]).runAs.name).toBe("bob");
    expect(spec({ payload: pl }, [JOB({ worker_pool: null, precondition: 5 })]).runAs.name).toBe("carol");
    expect(spec({}, []).runAs.name).toBe("alice");
    const none = spec({ requester_id: "", payload: {} }, []);
    expect([none.runAs.name, none.runAs.relation]).toEqual(["—", ""]);
  });

  test("relation 두 문형 + trim 으로 본인 판정", () => {
    expect(spec({}, [JOB()]).runAs.relation).toBe(" (요청자 본인)");
    expect(spec({ requester_id: "mason" }, [JOB()]).runAs.relation).toBe(" (관리자가 지정한 다른 사용자)");
    const trimmed = spec({ requester_id: "mason", payload: { storage: "s", target: "t", owner_username: " mason " }, operation: "scan" }, []);
    expect([trimmed.runAs.name, trimmed.runAs.relation]).toEqual(["mason", " (요청자 본인)"]);
  });

  test("root 꼬리: 계획됨은 비 root 와 같은 모양(uid · gid), 남의 신원을 지정했을 때만 「이름만 기록되고 권한은 root」", () => {
    const plan = spec({ requester_id: "mason", payload: { ...SYNC_PAYLOAD, run_as_root: true, owner_username: "mason" } },
      [JOB({ worker_pool: { identity: ROOT_IDENT } })]);
    // root 라는 사실은 「실행 권한」이 말한다 -- 본인이면 꼬리에 되풀이하지 않는다
    expect([plan.runAs.name, plan.runAs.relation, sp(plan.runAs.tail)])
      .toEqual(["mason", " (요청자 본인)", " · uid 0 · gid 0"]);
    const other = spec({ requester_id: "mason", payload: { ...SYNC_PAYLOAD, run_as_root: true, owner_username: "alice" } },
      [JOB({ worker_pool: { identity: { ...ROOT_IDENT, username: "alice" } } })]);
    expect([other.runAs.relation, sp(other.runAs.tail)])
      .toEqual([" (관리자가 지정한 다른 사용자)", " · uid 0 · gid 0 — 이름만 기록되고 권한은 root"]);
    expect(plan.runAs.tail).toContain("uid 0");   // 고정 단위는 줄바꿈 없이 묶인다
    const req = spec({ requester_id: "mason", state: "Rejected", payload: { ...SYNC_PAYLOAD, run_as_root: true } }, []);
    expect(sp(req.runAs.tail)).toBe(" — 이름만 기록되며 root(uid 0)로 요청됨");
  });

  test("uid 0 도 그대로 찍고, gid 가 없으면 「gid 모름」, privileged 키가 없어도 찍는다", () => {
    const tail = (identity: object) => sp(spec({}, [JOB({ worker_pool: { identity } })]).runAs.tail);
    expect(tail({ username: "alice", uid: 0, gid: 0, privileged: false })).toBe(" · uid 0 · gid 0(주 그룹)");
    expect(tail({ username: "alice", uid: 10001, gid: null })).toBe(" · uid 10001 · gid 모름(주 그룹)");
    expect(tail({ username: "alice", uid: 1000, gid: 1000 })).toBe(" · uid 1000 · gid 1000(주 그룹)");
    expect(sp(spec().runAs.tail)).toBe(" · uid 10001 · gid 10000(주 그룹)");
    expect(spec().runAs.tail.startsWith("\u00a0· ")).toBe(true);   // 「·」는 앞 글자에 묶여 줄 첫머리로 가지 않는다
  });

  test("uid/gid 를 모를 때의 꼬리: 잡 0개(비종단/종단)·잡 조회 실패·계획 중·기록 없음", () => {
    expect(spec({ state: "Pending" }, []).runAs.tail).toBe(" · uid/gid 는 계획 단계에서 정해집니다");
    expect(spec({ state: "Rejected" }, []).runAs.tail).toBe(" · 계획되지 않아 uid/gid 가 정해지지 않았습니다");
    expect(spec({ state: "Pending" }, null).runAs.tail).toBe("");
    expect(spec({ state: "Planned" }, [JOB({ state: "Pending", worker_pool: null })]).runAs.tail)
      .toBe(" · uid/gid 는 계획 단계에서 정해집니다");
    expect(spec({}, [JOB({ worker_pool: { node_count: 1 } })]).runAs.tail).toBe(" · uid/gid 기록 없음");
  });
});

// ---- 4·5 옵션 -----------------------------------------------------------------------------------------------------
const ok = (v: ReturnType<typeof normalizeOptions>) => {
  if (v.state !== "ok") throw new Error(`state ${v.state}`);
  return v;
};
const userCtx = { kind: "user" as const, reqState: "Succeeded" };

describe("normalizeOptions", () => {
  test("sync 서버 기본만 있으면 칩 0개 + 기본값 2개", () => {
    const v = ok(normalizeOptions("sync", { payload: { batch_files: 1000000, bufsize: 4194304 } }, userCtx));
    expect(v.set).toEqual([]);
    expect(v.defaults).toEqual(["batch_files=1000000", "bufsize=4194304(4.0 MiB)"]);   // 기본값 줄에도 사람 단위
    expect(v.unknown).toEqual([]);
  });

  test("delete true 는 칩 + 제출 폼 설명, false 는 버린다", () => {
    const v = ok(normalizeOptions("sync", { payload: { delete: true, open_noatime: false } }, userCtx));
    expect(v.set).toEqual([{ token: "delete", note: SYNC_OPTION_HELP.delete }]);
  });

  test("batch_files 0 = 「배칭 끔」, bufsize 는 사람 표기, 키가 없으면 건너뛴다(unknown 아님)", () => {
    const v = ok(normalizeOptions("sync", { payload: { batch_files: 0, bufsize: 1048576 } }, userCtx));
    expect(v.set).toEqual([{ token: "batch_files=0", note: "배칭 끔" }, { token: "bufsize=1048576", note: "1.0 MiB" }]);
    const none = ok(normalizeOptions("sync", { payload: { contents: true } }, userCtx));
    expect([none.defaults, none.unknown]).toEqual([[], []]);
    const scan = ok(normalizeOptions("scan", { payload: { batch_files: 0, broken_limit: 0, verbose: true } }, userCtx));
    expect(scan.set).toEqual([{ token: "verbose", note: null }, { token: "batch_files=0", note: "배칭 안 함" },
      { token: "broken_limit=0", note: "파손 경로 표본을 보관하지 않음(총계는 정확)" }]);
  });

  test("모양이 틀린 값·카탈로그 밖 키는 원문으로(적용 안 됨 꼬리는 bool 만)", () => {
    const v = ok(normalizeOptions("sync", { payload: { delete: "yes", foo: 1, batch_files: "5", chown: 7, chmod: "" } }, userCtx));
    expect(v.set).toEqual([]);
    expect(v.unknown).toEqual(['delete="yes" (적용 안 됨)', 'batch_files="5"', "chown=7", "foo=1"]);
  });

  test("카탈로그 밖 키가 많아도(변조) 12개 + 「외 N개」 -- 글자 수에 상한", () => {
    const many = Object.fromEntries(Array.from({ length: 5000 }, (_, i) => [`k${i}`, i]));
    const v = ok(normalizeOptions("sync", { payload: many }, userCtx));
    expect(v.unknown).toHaveLength(13);
    expect(v.unknown.slice(0, 2)).toEqual(["k0=0", "k1=1"]);
    expect(v.unknown[12]).toBe("외 4988개");
    // 카탈로그 쪽 원문과 합쳐도 같은 상한
    const mixed = ok(normalizeOptions("sync", { payload: { delete: "y", ...Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`x${i}`, 1])) } }, userCtx));
    expect(mixed.unknown).toHaveLength(13);
    expect(mixed.unknown[0]).toBe('delete="y" (적용 안 됨)');
    expect(mixed.unknown[12]).toBe("외 1개");
    // 딱 12개면 꼬리 없음
    expect(ok(normalizeOptions("weird", { payload: Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`y${i}`, 1])) }, userCtx))
      .unknown).toHaveLength(12);
  });

  test("text 키(chmod·chown)는 비어 있지 않은 문자열이면 칩", () => {
    const v = ok(normalizeOptions("sync", { payload: { chmod: "D755,F644", chown: "10001:10010" } }, userCtx));
    expect(v.set.map((s) => s.token)).toEqual(["chmod=D755,F644", "chown=10001:10010"]);
  });

  test("options 가 문자열·배열이면 malformed, 둘 다 없으면 missing, payload 에 없으면 잡 쪽을 쓴다", () => {
    expect(normalizeOptions("sync", { payload: "x" }, userCtx)).toEqual({ state: "malformed" });
    expect(normalizeOptions("sync", { payload: [1] }, userCtx)).toEqual({ state: "malformed" });
    expect(normalizeOptions("sync", { payload: undefined }, userCtx)).toEqual({ state: "missing" });
    expect(normalizeOptions("sync", { payload: null, job: null }, userCtx)).toEqual({ state: "missing" });
    expect(normalizeOptions("sync", { payload: null, job: "x" }, userCtx)).toEqual({ state: "malformed" });
    expect(ok(normalizeOptions("sync", { payload: undefined, job: { quiet: true } }, userCtx)).set)
      .toEqual([{ token: "quiet", note: null }]);
  });

  test("rm recursive true 는 생략(동의 게이트), false 는 원문(서버가 받지 않는 비정상 기록)", () => {
    const t = ok(normalizeOptions("rm", { payload: { recursive: true, stat: true } }, userCtx));
    expect([t.set, t.unknown]).toEqual([[{ token: "stat", note: null }], []]);
    const f = ok(normalizeOptions("rm", { payload: { recursive: false } }, userCtx));
    expect([f.set, f.defaults, f.unknown]).toEqual([[], [], ["recursive=false"]]);
    expect(ok(normalizeOptions("rm", { payload: { recursive: "y" } }, userCtx)).unknown).toEqual(['recursive="y" (적용 안 됨)']);
  });

  test("원문은 60자에서 자르고, 직렬화 불가(BigInt)는 ?", () => {
    const long = rawText("foo", "x".repeat(100));
    expect(long).toHaveLength(60);
    expect(long.endsWith("…")).toBe(true);
    expect(rawText("big", BigInt(1))).toBe("big=?");
    expect(rawText("u", undefined)).toBe("u=undefined");
  });

  test("모르는 연산이면 키 전부가 원문", () => {
    expect(ok(normalizeOptions("weird", { payload: { delete: true, x: null } }, userCtx)).unknown).toEqual(["delete=true", "x=(값 없음)"]);
  });

  test("noatimeWarn: 비 root + 실패는 참, 비 root + 성공·root 는 거짓", () => {
    const o = { open_noatime: true };
    expect(ok(normalizeOptions("sync", { payload: o }, { kind: "user", reqState: "Failed" })).noatimeWarn).toBe(true);
    expect(ok(normalizeOptions("sync", { payload: o }, { kind: "user", reqState: "Succeeded" })).noatimeWarn).toBe(false);
    expect(ok(normalizeOptions("sync", { payload: o }, { kind: "root", reqState: "Failed" })).noatimeWarn).toBe(false);
  });
});

// ---- 6 우선순위 ---------------------------------------------------------------------------------------------------
test.each<[unknown, unknown, string]>([
  ["mid", "dms-mid", "mid"],
  ["high", "dms-mid", "high 요청 → mid 적용(정책 상한)"],
  ["low", "dms-mid", "low 요청 → mid 적용"],
  ["high", "weird", "high"],
  ["high", undefined, "high"],
  [5, undefined, "모름"],
  [null, "dms-low", "low 적용"],
])("priorityText(%s, %s) = %s", (r, c, want) => {
  expect(priorityText(r, c)).toBe(want);
});

// ---- 7 실행 도구 --------------------------------------------------------------------------------------------------
describe("실행 도구", () => {
  test("잡 1개 dsync: 요약·프로세스 꼬리·실행 노드(테스트베드 표본 4노드 8프로세스)", () => {
    const t = spec().tool;
    expect(t).toEqual({ summary: "dsync · 4 노드", tail: "프로세스 8개(노드당 2)", fallback: null,
      nodes: [{ side: null, names: ["dms-w2", "dms-w3", "dms-w4", "dms-w5"], more: 0 }] });
    expect(nodesText(t?.nodes ?? [])).toBe("실행 노드 dms-w2, dms-w3, dms-w4, dms-w5");
  });

  test("노드 CPU 상한(2026-10-16): 요약이 노드당 값·클램프를 말하면 꼬리는 총 프로세스 수만", () => {
    const t = spec({}, [JOB({ worker_pool: { node_count: 4, process_count: 4, procs_per_node: 1, procs_per_node_requested: 2,
      candidates: { primary: ["a", "b", "c", "d"] } } })]).tool;
    expect([t?.summary, t?.tail]).toEqual(["dsync · 4 노드 × 1 프로세스 (요청 2 → 노드 CPU 상한 1)", "프로세스 4개"]);
  });

  test("나누어떨어지지 않으면 (노드당 …) 없음, nsync 는 소스·목적지 노드", () => {
    expect(spec({}, [JOB({ worker_pool: { node_count: 4, process_count: 30 } })]).tool?.tail).toBe("프로세스 30개");
    const ns = spec({}, [JOB({ tool: "nsync", worker_pool: { source_count: 2, destination_count: 1, node_count: 3, process_count: 12,
      candidates: { source: ["a1", "a2"], destination: ["b1"] } } })]).tool;
    expect([ns?.summary, ns?.tail]).toEqual(["nsync · 소스 2 + 목적지 1 노드", "프로세스 12개(노드당 4)"]);
    expect(nodesText(ns?.nodes ?? [])).toBe("실행 노드 소스 a1, a2 · 목적지 b1");
  });

  test("13대면 앞 12대 + 「외 1대」", () => {
    const many = Array.from({ length: 13 }, (_, i) => `n${i + 1}`);
    const nodes = spec({}, [JOB({ worker_pool: { node_count: 13, candidates: { primary: many } } })]).tool?.nodes ?? [];
    expect(nodes).toEqual([{ side: null, names: many.slice(0, 12), more: 1 }]);
    expect(nodesText(nodes)).toBe(`실행 노드 ${many.slice(0, 12).join(", ")} 외 1대`);
  });

  test("도구 null: 비종단은 「계획 중 — …」, 종단은 「기록 없음」(도구 이름으로 시작하지 않는다)", () => {
    expect(spec({}, [JOB({ tool: null, state: "Pending", worker_pool: null })]).tool)
      .toEqual({ summary: null, tail: null, fallback: "계획 중 — 실행 노드가 정해지면 표시됩니다", nodes: null });
    expect(spec({}, [JOB({ tool: null, state: "Failed" })]).tool?.fallback).toBe("기록 없음");
  });

  test("잡 0개·조회 실패면 행 없음, 잡 2개면 행 없음 + multiJob", () => {
    expect(spec({}, []).tool).toBeNull();
    expect(spec({}, null).tool).toBeNull();
    const two = spec({}, [JOB(), JOB({ job_id: "j2" })]);
    expect([two.tool, two.multiJob]).toEqual([null, true]);
    expect(spec().multiJob).toBe(false);
  });
});

// ---- 8 목적지 소유 ------------------------------------------------------------------------------------------------
describe("목적지 소유", () => {
  const rootReq = { requester_id: "mason", payload: { ...SYNC_PAYLOAD, run_as_root: true } };
  const rootJob = JOB({ worker_pool: { identity: ROOT_IDENT } });
  test("root 계획됨: 소스 소유 그대로, chown 지정이면 그 값", () => {
    expect(spec(rootReq, [rootJob]).ownership).toBe("소스의 소유자·그룹 그대로(root 실행)");
    expect(spec({ ...rootReq, payload: { ...SYNC_PAYLOAD, run_as_root: true, options: { chown: "10001:10010" } } }, [rootJob]).ownership)
      .toBe("chown 지정값 10001:10010");
  });
  test("root + PV 목적지: 점검이 잰 목적지 PV 루트 소유자(worker_pool.dest_pv_owner), 재기 전에는 「점검 뒤 확정」", () => {
    const pvReq = { ...rootReq, payload: { ...SYNC_PAYLOAD, run_as_root: true,
      destination_pv: { cluster: "dmsk8s", namespace: "team-a", pvc: "models", storage: "cephfs-dms", root: "pv" } } };
    expect(spec(pvReq, [rootJob]).ownership).toBe("목적지 PV 루트의 소유자·그룹(점검 뒤 확정)");
    const measured = JOB({ worker_pool: { identity: ROOT_IDENT, dest_pv_owner: "1000:100" } });
    expect(spec(pvReq, [measured]).ownership).toBe("목적지 PV 소유자 1000:100(PV 루트 기준)");
    const tampered = JOB({ worker_pool: { identity: ROOT_IDENT, dest_pv_owner: 7 } });   // DB 신뢰 경계 -- 문자열만
    expect(spec(pvReq, [tampered]).ownership).toBe("목적지 PV 루트의 소유자·그룹(점검 뒤 확정)");
  });
  test("비 root 자동 chown 은 「실행 신원 소유 — uid:gid」(이름을 되풀이하지 않는다), 비 root chown 은 비 root 꼬리", () => {
    expect(sp(spec().ownership ?? "")).toBe("실행 신원 소유 — 10001:10000(주 그룹)");
    expect(sp(spec({ requester_id: "mason", payload: { ...SYNC_PAYLOAD, owner_username: "alice" } }).ownership ?? ""))
      .toBe("실행 신원 소유 — 10001:10000(주 그룹)");
    expect(spec({ payload: { ...SYNC_PAYLOAD, options: { chown: "10001:10010" } } }).ownership)
      .toBe("chown 지정값 10001:10010 — 비 root: uid 는 본인, gid 는 주 그룹만(아니면 실패)");
  });
  test("배치 계획 전·scan·옵션 기록 없음·chown 이 문자열 아님이면 행 없음", () => {
    expect(spec({ batch_id: "b1" }, []).ownership).toBeNull();
    expect(spec({ operation: "scan", payload: { storage: "s", target: "t", options: {} } }).ownership).toBeNull();
    expect(spec({ payload: { ...SYNC_PAYLOAD, options: undefined } }, [JOB({ options: undefined })]).ownership).toBeNull();
    expect(spec({ payload: { ...SYNC_PAYLOAD, options: { chown: 10001 } } }).ownership).toBeNull();
  });
  test("실행되지 않은(또는 root 여부가 정해지지 않은) 요청엔 소유를 단정하지 않는다", () => {
    const asked = { ...SYNC_PAYLOAD, run_as_root: true };
    // root 요청됨: 계획 단계 거부(R4) · 계획 전(R3) · 잡 조회 실패(R2) · 잡에 권한 정보 없음(R5)
    expect(spec({ requester_id: "mason", state: "Rejected", payload: asked }, []).ownership).toBeNull();
    expect(spec({ requester_id: "mason", state: "Pending", payload: asked }, []).ownership).toBeNull();
    expect(spec({ requester_id: "mason", state: "Pending", payload: asked }, null).ownership).toBeNull();
    expect(spec({ requester_id: "mason", payload: asked }, [JOB({ worker_pool: { identity: { username: "mason", uid: 0, gid: 0 } } })]).ownership)
      .toBeNull();
    // 비 root 인데 작업 없이 끝남 · 작업 조회 실패 + 종단(돌았는지 모름)
    expect(spec({ state: "Rejected" }, []).ownership).toBeNull();
    expect(spec({ state: "Failed" }, null).ownership).toBeNull();
    // 비 root 계획 전(비종단) -- 규칙은 확정이라 보인다(숫자는 계획 뒤에)
    expect(spec({ state: "Pending" }, []).ownership).toBe("요청자 본인(alice)의 uid:gid(주 그룹)");
  });
});

// ---- 10 노드 지정 -------------------------------------------------------------------------------------------------
test.each<[unknown, unknown, string | null]>([
  [4, 8, "노드 4 · 노드당 프로세스 8 — 정책 상한까지만 적용"],
  [4, undefined, "노드 4 — 정책 상한까지만 적용"],
  [undefined, 8, "노드당 프로세스 8 — 정책 상한까지만 적용"],
  [undefined, undefined, null],
  ["4", 1.5, null],
  [0, -1, null],
])("노드 지정 dsync(node_count=%s, procs_per_node=%s)", (nc, ppn, want) => {
  expect(spec({ batch_id: "b1", payload: { ...SYNC_PAYLOAD, node_count: nc, procs_per_node: ppn } }).nodeRequest).toBe(want);
});

test("노드 지정: nsync 는 출발·목적지 각각(총 최대 2배), 도구를 모르는 sync 는 조건문, scan 은 그대로", () => {
  const pl = { ...SYNC_PAYLOAD, node_count: 4 };
  const nsyncJob = JOB({ tool: "nsync", worker_pool: { tool: "nsync", source_count: 4, destination_count: 4, node_count: 8,
    process_count: 32, identity: ROOT_IDENT } });
  expect(spec({ batch_id: "b1", payload: pl }, [nsyncJob]).nodeRequest).toBe("노드 4(출발·목적지 각각) — 정책 상한까지만 적용");
  // 모양만으로도(source_count) -- tool 이 비어 있는 변조·옛 행
  expect(spec({ batch_id: "b1", payload: pl }, [JOB({ tool: null, worker_pool: { source_count: 2, destination_count: 2 } })]).nodeRequest)
    .toBe("노드 4(출발·목적지 각각) — 정책 상한까지만 적용");
  for (const jobs of [[], null, [JOB({ tool: null, state: "Pending", worker_pool: null })]]) {
    expect(spec({ batch_id: "b1", state: "Pending", payload: { ...pl, procs_per_node: 8 } }, jobs).nodeRequest)
      .toBe("노드 4(nsync 로 돌면 출발·목적지 각각) · 노드당 프로세스 8 — 정책 상한까지만 적용");
  }
  expect(spec({ batch_id: "b1", operation: "scan", payload: { storage: "s", target: "t", node_count: 4 } }, []).nodeRequest)
    .toBe("노드 4 — 정책 상한까지만 적용");
});

// ---- 경로 조각 ----------------------------------------------------------------------------------------------------
describe("대상·절대경로 조각", () => {
  test("sync 는 [출발, 도착], scan 은 [대상] -- 경로 이름의 「 → 」에서 갈리지 않는다", () => {
    const s = deriveRequestSpec(REQ({ payload: { source_storage: "a", source: "dir → x", destination_storage: "b", destination: "y" } }),
      [], { a: "/A", b: "/B" });
    expect(s.target).toEqual(["a:dir → x", "b:y"]);
    expect(s.abs).toEqual(["/A/dir → x", "/B/y"]);
    expect(spec({ operation: "scan", payload: { storage: "s", target: "t" } }, []).target).toEqual(["s:t"]);
  });
  test("경로 값이 문자열이 아니면(변조) 「?」 -- [object Object] 를 내지 않는다", () => {
    const s = spec({ payload: { source_storage: { a: 1 }, source: ["x", "y"], destination_storage: 5, destination: null } }, []);
    expect(s.target).toEqual(["?:?", "?:—"]);
    expect(s.abs).toBeNull();
  });
});

// ---- 11 변조 입력 -------------------------------------------------------------------------------------------------
describe("변조 입력(DB 신뢰 경계)", () => {
  test("payload 문자열·worker_pool 문자열·options 배열에서도 던지지 않고 기대 문구", () => {
    const s = spec({ payload: "x" }, [JOB({ worker_pool: "oops", options: [1], state: "Failed" })]);
    expect(s.target).toEqual(["—:—", "—:—"]);
    expect(s.abs).toBeNull();
    expect(s.options).toEqual({ state: "malformed" });   // 잡 쪽 options 가 배열 -- 기록은 있는데 읽을 수 없다
    expect(s.privilege.head.text).toBe(USER_HEAD);
    expect(s.runAs).toEqual({ name: "alice", relation: " (요청자 본인)", tail: " · uid/gid 기록 없음" });
    expect(s.tool?.summary).toBe("dsync");
    const m = spec({ payload: { ...SYNC_PAYLOAD, options: [1] } }, [JOB({ worker_pool: { identity: [], candidates: "x" } })]);
    expect(m.options).toEqual({ state: "malformed" });
    expect(m.tool?.nodes).toBeNull();
  });

  test("precondition 숫자·잡 원소 null·우선순위 숫자에도 죽지 않는다", () => {
    expect(() => spec({ priority: 5 }, [JOB({ precondition: 5 })])).not.toThrow();
    expect(() => spec({}, [null as unknown as DataJob])).not.toThrow();
    expect(spec({ priority: 5 }, [JOB({ worker_pool: null })]).priority).toBe("모름");
  });
});

// ---- 다듬기(2026-10-08 검증 2차 low·cosmetic) --------------------------------------------------------------------
describe("다듬기", () => {
  test("서버가 root 실행을 막은 작업(privilege_not_requested)은 「root 실행」 배지·목적지 소유를 말하지 않는다", () => {
    const stopped = spec({ requester_id: "mason", state: "Failed", payload: SYNC_PAYLOAD },
      [JOB({ state: "Rejected", reason_code: "privilege_not_requested", worker_pool: { identity: ROOT_IDENT } })]);
    expect(stopped.badge).toBeNull();
    expect(stopped.privilege.head).toEqual({ tone: "badLead",
      text: "root(특권)로 계획됨 — 요청에 root 지정이 없어 서버가 실행을 중단했습니다" });
    expect(stopped.privilege.subs).toEqual([]);   // 옛 행 안내와 겹치지 않는다
    expect(stopped.ownership).toBeNull();
    // 사유가 다르면(정상 종료한 옛 root 행) 지금까지처럼 「실행」 + 옛 행 안내
    const legacy = spec({ requester_id: "mason", payload: SYNC_PAYLOAD }, [JOB({ worker_pool: { identity: ROOT_IDENT } })]);
    expect(legacy.badge).toBe("run");
    expect(legacy.privilege.subs).toEqual([{ text: LEGACY_NOTE, tone: "sub" }]);
  });

  test("단건인데 작업 기록을 못 읽으면 「root 아님」을 단정하지 않는다(2026-09-30 전 관리자 행은 지정 없이 root 였다)", () => {
    expect(resolvePrivilege(P({ jobs: null })).privilege.head)
      .toEqual({ tone: "plain", text: "root 지정 없음 — 작업 기록을 불러오지 못해 실제 실행 권한은 확인하지 못했습니다" });
    expect(resolvePrivilege(P({ jobs: null })).badge).toBeNull();
    // 공유 토큰 배치는 root 자격이 없어 확정 그대로, 작업 없이 끝났으면 과거형
    expect(resolvePrivilege(P({ batch: true, authMethod: "token", jobs: null })).privilege.head.text).toBe(USER_HEAD);
    expect(resolvePrivilege(P({ batch: true, authMethod: "token", jobs: [], reqTerminal: true })).privilege.subs).toEqual([{
      tone: "sub", text: "배치 항목 — 작업이 만들어지지 않아 실행되지 않았습니다(공유 토큰 배치라 root 자격 없음)." }]);
  });

  test("문자열 옵션 칩은 60자에서 자른다(변조 행이 칩 하나에 수 KB 를 싣지 않게)", () => {
    const v = normalizeOptions("sync", { payload: { chmod: "u+rwx,".repeat(50) } }, userCtx);
    expect(v.state === "ok" && v.set[0].token.length).toBe(60);
    expect(v.state === "ok" && v.set[0].token.endsWith("…")).toBe(true);
  });
});


// ---- PV 주소 요청(dms-k8s 설계 §7) -------------------------------------------------------------------------------
describe("PV 주소 요청: 대상은 PV 표기, PV 행(클러스터 · 볼륨 · StorageClass), 「대상 사용자」", () => {
  const PV = { cluster: "dmsk8s", namespace: "team-a", pvc: "models", volume: "pvc-52c6", storage_class: "tb-gpfs",
               root: "pvc-52c6/pvc-52c6-data", path: "sample" };
  test("scan + 면제 관리자의 권한 기준 사용자", () => {
    const s = spec({ operation: "scan", requester_id: "root",
                     payload: { storage: "k8s-pv-gpfs", target: "pvc-52c6/pvc-52c6-data/sample", pv: PV,
                                owner_username: "alice", run_as_root: true, options: {} } }, null);
    expect(s.target).toEqual(["models — team-a (tb-gpfs) / sample"]);
    expect(s.pv).toEqual([{ side: null, text: "dmsk8s · pvc-52c6 · tb-gpfs" }]);
    expect(s.targetUser).toBe("alice");
  });
  test("sync PV ↔ 직접 경로 혼합: PV 쪽만 행이 있다, owner 없으면 대상 사용자 행 없음", () => {
    const s = spec({ payload: { ...SYNC_PAYLOAD, source_storage: "k8s-pv-gpfs", source: "pvc-52c6/pvc-52c6-data",
                                source_pv: { ...PV, path: "" }, run_as_root: true } }, null);
    expect(s.target).toEqual(["models — team-a (tb-gpfs) / (PV 전체)", "cephfs-dms:dms_test/out"]);
    expect(s.pv).toEqual([{ side: "소스", text: "dmsk8s · pvc-52c6 · tb-gpfs" }]);
    expect(s.targetUser).toBeNull();
  });
  test("PV 주소가 아닌 요청은 PV 행·대상 사용자 행이 없다(옛 행의 실행 신원 지정은 「실행 신원」 행이 말한다)", () => {
    const s = spec({ payload: { ...SYNC_PAYLOAD, owner_username: "alice" } });
    expect(s.pv).toEqual([]);
    expect(s.targetUser).toBeNull();
  });
});

// ---- 8b 목적지 소유 -- 결과 소유 규칙(2026-10-26: destination_pv.owner_rule) -----------------------------------------
describe("목적지 소유 -- 결과 소유 규칙(owner_rule)", () => {
  const rootJob = JOB({ worker_pool: { identity: ROOT_IDENT } });
  const measured = JOB({ worker_pool: { identity: ROOT_IDENT, dest_pv_owner: "1000:100" } });
  const pvReq = (dest: Record<string, unknown>, options: Record<string, unknown> = SYNC_PAYLOAD.options) => ({
    requester_id: "mason", payload: { ...SYNC_PAYLOAD, run_as_root: true, options,
      destination_pv: { cluster: "dmsk8s", namespace: "team-a", pvc: "models", storage: "k8s-pv-nfs", root: "pv", ...dest } } });
  test("storage: 제출 때 박힌 목적지 스토리지 기본 소유자(0:0 은 「0:0(root)」 -- 실제 값), 잰 PV 루트 소유자는 쓰지 않는다", () => {
    expect(spec(pvReq({ owner_rule: "storage", owner: "1500:1500" }), [measured]).ownership)
      .toBe("목적지 스토리지 기본 소유자 1500:1500");
    expect(spec(pvReq({ owner_rule: "storage", owner: "0:0" }), [rootJob]).ownership)
      .toBe("목적지 스토리지 기본 소유자 0:0(root)");
    // 운영자 chown 이 있으면 그 값이 이긴다(storage 규칙 + owner 는 함께 기록돼 있다)
    expect(spec(pvReq({ owner_rule: "storage", owner: "1500:1500" }, { chown: "7:7" }), [rootJob]).ownership)
      .toBe("chown 지정값 7:7");
  });
  test("pv_root(운영자 지정): 잰 PV 루트 소유자 + 「운영자 지정」, 재기 전에는 「점검 뒤 확정」", () => {
    expect(spec(pvReq({ owner_rule: "pv_root" }), [measured]).ownership)
      .toBe("목적지 PV 소유자 1000:100(PV 루트 기준 — 운영자 지정)");
    expect(spec(pvReq({ owner_rule: "pv_root" }), [rootJob]).ownership)
      .toBe("목적지 PV 루트의 소유자·그룹(점검 뒤 확정 — 운영자 지정)");
  });
  test("옛 행(owner_rule 없음·null): 종전 문구 그대로 -- 부재를 오류로 읽지 않는다", () => {
    expect(spec(pvReq({}), [measured]).ownership).toBe("목적지 PV 소유자 1000:100(PV 루트 기준)");
    expect(spec(pvReq({ owner_rule: null }), [measured]).ownership).toBe("목적지 PV 소유자 1000:100(PV 루트 기준)");
  });
  test("변조·비정상 기록(모르는 규칙 값, storage 인데 owner 형식 오류)은 소유를 단정하지 않는다(행 생략)", () => {
    for (const dest of [{ owner_rule: "nobody" }, { owner_rule: 1 }, { owner_rule: "storage" },
                        { owner_rule: "storage", owner: "x:y" }, { owner_rule: "storage", owner: 1500 },
                        { owner_rule: "storage", owner: "4294967295:0" }])
      expect(spec(pvReq(dest), [measured]).ownership).toBeNull();
  });
});
