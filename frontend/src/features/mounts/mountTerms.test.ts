import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";
import {
  CELL_META, REASON_TEXT, SHARED_META, SHARED_PROBLEM_STATES, TONE_CLASS, absentNowhere, cellHelp, cellLabel, cellMounted, cellProblem,
  cellSub, cellView, columnFacts, columnNameLines, columnPathTail, columnProblem, columnRuleText, columnSummaryText, filterColumns,
  isPodRole, podCheckText, podFailedHelp, podFailedOn, podFailedRoles, podLine, podTag, podsOn, roleLabel, sharedDisplay, sharedHelp,
  sharedSub, usageDetail, usageText, writableText,
} from "./mountTerms";
import { LIVE, NOWHERE, VARIETY, absent, column, pod, ready, row as mkRow, sharedOk, wide } from "./mountFixtures";
import type { CellState, MountCellData, MountStatusResponse, SharedCellData, SharedState } from "./types";

const cell = (over: Partial<MountCellData> = {}): MountCellData => ({
  state: "ready", last: null, pending: null, status: "Ready", reason: null, reported_path: "/cephfs", exists: true,
  is_mountpoint: true, readable: true, writable: true, disk: null, ...over,
});
const shared = (over: Partial<SharedCellData> = {}): SharedCellData => ({
  state: "ok", last: null, reported_path: "/s/dms/artifacts", exists: true, writable: true, mount_point: "/s",
  fstype: "nfs4", ...over,
});
const CTX = { mountPath: "/cephfs", ago: "12분 전" };

const CELL_STATES: CellState[] = ["ready", "read_only", "absent", "broken", "pending", "stale"];
const SHARED_STATES: SharedState[] = ["ok", "ok_unverified", "local", "differs", "missing", "read_only", "pending", "stale"];

test("모든 칸·공용 상태에 글자·아이콘·톤·설명이 있다", () => {
  for (const s of CELL_STATES) {
    const m = CELL_META[s];
    expect(m.label, s).not.toBe("");
    expect(m.help, s).not.toBe("");
    expect(m.Icon, s).toBeTruthy();
  }
  for (const s of SHARED_STATES) {
    const m = SHARED_META[s];
    expect(m.label, s).not.toBe("");
    expect(m.help, s).not.toBe("");
    expect(m.Icon, s).toBeTruthy();
  }
});

describe("사유", () => {
  test("다섯 사유의 짧은·긴 문구", () => {
    expect(Object.keys(REASON_TEXT).sort()).toEqual(
      ["host_root_missing", "missing_mount_path", "not_a_mountpoint", "not_readable", "propagation_stale"]);
    expect(cellLabel(cell({ state: "broken", status: "Missing", reason: "not_a_mountpoint" }))).toBe("마운트 빠짐");
    expect(cellLabel(cell({ state: "broken", status: "Missing", reason: "propagation_stale" }))).toBe("전파 안 됨");
    expect(cellLabel(cell({ state: "broken", status: "Missing", reason: "not_readable" }))).toBe("읽기 불가");
    expect(cellLabel(cell({ state: "broken", status: "Missing", reason: "host_root_missing" }))).toBe("점검 불가");
    expect(cellHelp(cell({ state: "broken", reason: "propagation_stale" }), CTX)).toMatch(/dms-agent 파드를 다시 시작/);
  });

  test("끊긴 마운트(broken + missing_mount_path + is_mountpoint true -- 2026-11-27)는 「끊긴 마운트」, 설명은 원인·조치", () => {
    const dead = cell({ state: "broken", status: "Missing", reason: "missing_mount_path", exists: false, is_mountpoint: true,
                        readable: false, writable: false });
    expect(cellLabel(dead)).toBe("끊긴 마운트");
    const help = cellHelp(dead, CTX);
    expect(help).toMatch(/^끊긴 마운트 — 호스트에는 마운트가 있는데 경로에 접근할 수 없습니다\(죽은 FUSE·낡은 NFS 핸들·마운트 전파 끊김\)/);
    expect(help).toMatch(/lazy umount/);
    expect(help).toMatch(/dms-agent 파드를 다시 시작/);
    expect(help).not.toMatch(/붙이지 않은 노드라면 정상/);               // 「없음」 설명(안 붙인 노드)과 섞지 않는다
    expect(cellView(dead).Icon).not.toBe(CELL_META.broken.Icon);         // 사유 아이콘(끊긴 플러그)
    // 리포트 지연 칸의 마지막 값도 같은 글자
    expect(cellHelp(cell({ ...dead, state: "stale", last: "broken" }), CTX)).toContain("마지막 값: 끊긴 마운트");
    // 같은 사유의 「없음」(absent -- 호스트에도 마운트가 없다)은 그대로 「없음」
    expect(cellLabel(cell({ state: "absent", status: "Missing", reason: "missing_mount_path", is_mountpoint: false }))).toBe("없음");
    expect(cellHelp(cell({ state: "stale", last: "absent", status: "Missing", reason: "missing_mount_path" }), CTX))
      .toContain("마지막 값: 없음");
  });

  test("모르는 사유는 원문, 사유도 없으면 원문 status", () => {
    expect(cellLabel(cell({ state: "broken", status: "Missing", reason: "brand_new_reason" }))).toBe("brand_new_reason");
    expect(cellLabel(cell({ state: "broken", status: "Error", reason: null }))).toBe("Error");
    expect(cellLabel(cell({ state: "broken", status: null, reason: null }))).toBe("마운트 문제");
    expect(cellHelp(cell({ state: "broken", reason: "brand_new_reason" }), CTX)).toMatch(/알 수 없는 사유 코드/);
  });
});

test("「없음」 설명은 열에 따라(2026-11-27): 다른 노드엔 붙음 = 정상, 어디에도 없음 = 문제, 모름 = 규칙을 함께", () => {
  const abs = cell({ state: "absent", status: "Missing", reason: "missing_mount_path", exists: false, is_mountpoint: false });
  const elsewhere = cellHelp(abs, { ...CTX, nowhere: false });
  expect(elsewhere).toMatch(/다른 노드에는 마운트돼 있어/);
  expect(elsewhere).not.toMatch(/문제로 셉니다/);
  const nowhere = cellHelp(abs, { ...CTX, nowhere: true });
  expect(nowhere).toMatch(/이 스토리지를 마운트한 노드가 하나도 없습니다/);
  expect(nowhere).toMatch(/마운트 경로 오타/);
  expect(nowhere).toMatch(/문제로 셉니다/);
  const generic = cellHelp(abs, CTX);
  expect(generic).toBe(CELL_META.absent.help);
  expect(generic).toMatch(/어느 노드에도 붙어 있지 않은 스토리지면\(마운트된 노드가 하나도 없음\) 문제로 셉니다/);
  // 칩: 어디에도 없는 열의 없음만 경고 아이콘·빨강(글자는 「없음」 그대로, 낭독이 뜻을 말한다), 다른 상태는 nowhere 와 무관
  expect(cellView(abs, true)).toMatchObject({ label: "없음", tone: "bad", aria: "없음(어느 노드에도 마운트 안 됨)" });
  expect(cellView(abs, false)).toMatchObject({ label: "없음", tone: "muted", aria: "없음" });
  expect(cellView(cell(), true)).toMatchObject({ label: "정상", tone: "ok", aria: "정상" });
  expect(cellView(cell({ state: "pending", pending: "not_reported" }), true).tone).toBe("busy");
});

test("확인 대기 -- 보고 없음과 경로 바뀜", () => {
  expect(cellHelp(cell({ state: "pending", pending: "not_reported" }), CTX)).toMatch(/아직 이 스토리지를 점검/);
  const moved = cell({ state: "pending", pending: "path_changed", reported_path: "/old" });
  expect(cellHelp(moved, CTX)).toContain("옛 마운트 경로(/old)");
  expect(cellHelp(moved, CTX)).toContain("현재 경로(/cephfs)");
  expect(cellSub(moved)).toBe("경로 바뀜");
});

test("모름(리포트 지연)은 마지막 값을 말하고 지금 상태는 단정하지 않는다", () => {
  const stale = cell({ state: "stale", last: "broken", status: "Missing", reason: "not_a_mountpoint" });
  expect(cellLabel(stale)).toBe("모름");
  const help = cellHelp(stale, CTX);
  expect(help).toContain("마지막 보고 12분 전");
  expect(help).toContain("마지막 값: 마운트 빠짐");
  expect(help).toContain("모르며");
  expect(cellSub(stale)).toBe("지연");
  expect(sharedHelp(shared({ state: "stale", last: "local" }), null, "3분 전")).toContain("마지막 값: 로컬 디렉터리");
});

test("정상인데 쓰기 여부 모름(옛 에이전트)은 그렇게 말한다", () => {
  expect(cellHelp(cell({ writable: null }), CTX)).toMatch(/쓰기 가능 여부는 이 에이전트가 보고하지 않습니다/);
  expect(cellHelp(cell(), CTX)).not.toMatch(/쓰기 가능 여부/);
});

test("쓰기·사용량 표기(모름 ≠ 0)", () => {
  expect(writableText(null, true)).toBe("모름");
  expect(writableText(true, true)).toBe("가능");
  expect(writableText(false, true)).toBe("불가(읽기 전용)");
  // 붙지 않은 대상(경로 없음·마운트 빠짐·공용 없음)의 writable=false 는 ro 마운트가 아니다 -- 쓰기 여부를 따질 수 없어 「—」
  expect(writableText(false, false)).toBe("—");
  expect(writableText(true, false)).toBe("—");
  expect(writableText(null, false)).toBe("—");
  expect(usageText(null)).toBeNull();
  expect(usageText({ total_bytes: 0, used_bytes: 0 })).toBeNull();
  expect(usageText({ total_bytes: 1000, used_bytes: 410 })).toBe("사용 41%");
  expect(usageDetail({ total_bytes: 3 * 1024 ** 4, used_bytes: 1024 ** 4 })).toBe("사용 33% · 1.0 TiB / 3.0 TiB");
  expect(cellSub(cell({ disk: { total_bytes: 1000, used_bytes: 410 } }))).toBe("사용 41%");
  expect(cellSub(cell({ state: "read_only", disk: { total_bytes: 10, used_bytes: 5 } }))).toBe("사용 50%");
  expect(cellSub(cell({ state: "absent" }))).toBeNull();
});

test("붙어 있는 칸(cellMounted): 정상·읽기 전용, 지연이면 마지막 값", () => {
  expect(cellMounted(cell())).toBe(true);
  expect(cellMounted(cell({ state: "read_only" }))).toBe(true);
  for (const state of ["absent", "broken", "pending"] as CellState[]) expect(cellMounted(cell({ state })), state).toBe(false);
  expect(cellMounted(cell({ state: "stale", last: "read_only" }))).toBe(true);
  expect(cellMounted(cell({ state: "stale", last: "broken" }))).toBe(false);
  expect(cellMounted(cell({ state: "stale", last: null }))).toBe(false);
});

test("리포트 지연 흐림은 AA 대비(text-ink/70) -- /50 은 2.8:1", () => {
  expect(TONE_CLASS.stale).toContain("text-ink/70");
});

test("공용 디렉터리 보조 줄·설명", () => {
  expect(sharedSub(shared())).toBe("nfs4");
  expect(sharedSub(shared({ state: "ok_unverified", mount_point: null, fstype: null }))).toBe("공유 여부 모름");
  expect(sharedSub(shared({ state: "local" }))).toBeNull();
  expect(sharedHelp(shared(), null, "")).toBe("공유 마운트 nfs4 · /s 에 있고 쓸 수 있습니다.");
  const differs = sharedHelp(shared({ state: "differs", mount_point: "/data", fstype: "xfs" }),
                             { mount_point: "/s", fstype: "nfs4", nodes: 2 }, "");
  expect(differs).toContain("(/data xfs)");
  expect(differs).toContain("(/s nfs4)");
  expect(sharedHelp(shared({ state: "local" }), null, "")).toMatch(/루트 파일시스템/);
});

test("역할 꼬리표: 사이트 덧붙임 > 공용 > 원문", () => {
  expect(roleLabel("build")).toBe("빌드 노드");
  expect(roleLabel("k8s_primary", { k8s_primary: "k8s 호출 주" })).toBe("k8s 호출 주");
  expect(roleLabel("api_pod")).toBe("dms-api");                   // DMS 파드 꼬리표는 공용(두 라인 공용 재료)
  expect(roleLabel("controller_pod")).toBe("컨트롤러");
  expect(roleLabel("someday_pod")).toBe("someday_pod");
  expect([isPodRole("api_pod"), isPodRole("controller_pod"), isPodRole("build"), isPodRole("toString")])
    .toEqual([true, true, false, false]);
});

describe("DMS 파드 꼬리표(podTag)", () => {
  const local = sharedOk({ state: "local", mount_point: "/", fstype: "ext4" });

  test("사고 서명: 공용 디렉터리가 로컬인 노드의 dms-api 는 파드 점검이 정상이어도 경고", () => {
    const v = podTag(mkRow("w1", {}, { shared_dir: local, roles: ["api_pod"] }), "api_pod", [pod("api_pod", "w1")]);
    expect(v).toMatchObject({ role: "api_pod", label: "dms-api", alert: true });
    expect(v.lines[0]).toBe("이 노드의 DMS 공용 디렉터리: 로컬 디렉터리 — 여기 뜬 dms-api 는 다른 노드가 쓴 아티팩트·scan 리포트를 "
      + "읽지 못합니다(포탈에 「공유 스토리지 접근 불가」).");
    expect(v.lines[1]).toBe("dms-api-w1: 아티팩트 경로 점검 정상");
    // 컨트롤러는 영향 문장이 다르다
    const c = podTag(mkRow("w1", {}, { shared_dir: local }), "controller_pod", [pod("controller_pod", "w1")]);
    expect(c.alert).toBe(true);
    expect(c.lines[0]).toMatch(/여기 뜬 컨트롤러는 작업 결과 요약을 읽지 못합니다/);
    expect(c.lines[1]).toBe("컨트롤러: 아티팩트 경로 점검 정상");
  });

  test("공용 디렉터리 문제 넷 모두 경고 -- 다름은 「못 볼 수 있습니다」, 쓰기 불가는 공용 설명", () => {
    for (const state of SHARED_PROBLEM_STATES) {
      const v = podTag(mkRow("w1", {}, { shared_dir: sharedOk({ state }) }), "api_pod", [pod("api_pod", "w1")]);
      expect(v.alert, state).toBe(true);
    }
    const differs = podTag(mkRow("w1", {}, { shared_dir: sharedOk({ state: "differs" }) }), "api_pod", []);
    expect(differs.lines[0]).toMatch(/못 볼 수 있습니다/);
    const ro = podTag(mkRow("w1", {}, { shared_dir: sharedOk({ state: "read_only" }) }), "api_pod", []);
    expect(ro.lines[0]).toContain(SHARED_META.read_only.help);
  });

  test("공용 디렉터리가 정상이어도 파드가 경로를 못 읽으면 경고, 권한 문제·모름은 경고가 아니다", () => {
    const failed = pod("api_pod", "w1", { ok: false, reason: "artifact_base_jobs_missing", read_failed: true,
                                         gate_state: "waiting" });
    const v = podTag(mkRow("w1", {}), "api_pod", [failed]);
    expect(v.alert).toBe(true);
    expect(v.lines).toHaveLength(1);
    expect(v.lines[0]).toMatch(/^dms-api-w1: 아티팩트 경로 점검 실패 — 최근 작업의 기록 디렉터리가 이 경로에 없습니다/);
    expect(v.lines[0]).toMatch(/· 시작 점검에 막혀 Ready 대기 중\(서비스 밖\)$/);
    const posture = pod("api_pod", "w1", { ok: false, reason: "artifact_base_not_owned", read_failed: false });
    expect(podTag(mkRow("w1", {}), "api_pod", [posture]).alert).toBe(false);
    expect(podCheckText(posture)).toMatch(/^아티팩트 경로 권한 문제 — /);
    const unknown = pod("api_pod", "w1", { ok: null });
    expect(podTag(mkRow("w1", {}), "api_pod", [unknown]).alert).toBe(false);
    expect(podCheckText(unknown)).toBe("아티팩트 경로 점검 결과 모름");
    expect(podCheckText(pod("api_pod", "w1", { ok: false, reason: null, read_failed: true })))
      .toBe("아티팩트 경로 점검 실패 — 사유 모름");
  });

  test("리포트 지연 행은 공용 디렉터리의 마지막 값으로 경고하지 않는다(지금 상태 모름)", () => {
    const stale = mkRow("w1", {}, { fresh: false, shared_dir: { ...local, state: "stale", last: "local" } });
    expect(podTag(stale, "api_pod", [pod("api_pod", "w1")]).alert).toBe(false);
    const staleLocal = mkRow("w1", {}, { fresh: false, shared_dir: local });
    expect(podTag(staleLocal, "api_pod", [pod("api_pod", "w1")]).alert).toBe(false);
  });

  test("같은 노드의 dms-api 둘은 꼬리표 하나(×2)에 파드별 줄, 다른 역할 파드는 섞지 않는다", () => {
    const v = podTag(mkRow("w1", {}), "api_pod",
                     [pod("api_pod", "w1", { instance: "a" }), pod("api_pod", "w1", { instance: "b" }),
                      pod("controller_pod", "w1")]);
    expect(v.label).toBe("dms-api ×2");
    expect(v.lines).toEqual(["a: 아티팩트 경로 점검 정상", "b: 아티팩트 경로 점검 정상"]);
    expect(podLine(pod("api_pod", "w1", { gate_state: "started_degraded" })))
      .toBe("dms-api-w1: 아티팩트 경로 점검 정상 · 형제 파드가 없어 점검 실패인 채 서비스 시작");
  });

  test("podsOn: 덧붙임이 없는 응답(control_pods 없음)이면 빈 목록", () => {
    expect(podsOn({}, "w1")).toEqual([]);
    expect(podsOn({ control_pods: [pod("api_pod", "w1"), pod("api_pod", "w2")] }, "w2").map((p) => p.node_name))
      .toEqual(["w2"]);
  });
});

test("화면 문구에 영문 원문 상태(Ready/Missing)를 쓰지 않는다", () => {
  const texts = [
    ...Object.values(CELL_META).flatMap((m) => [m.label, m.help]),
    ...Object.values(SHARED_META).flatMap((m) => [m.label, m.help]),
    ...Object.values(REASON_TEXT).flatMap((r) => [r.short, r.long]),
  ];
  for (const t of texts) expect(t).not.toMatch(/\b(Ready|Missing)\b/);
});

// 이식 경계: 공용 코어 파일은 features/k8s 를 import 하지 않는다(dms-ssc 에는 그 디렉터리가 없다). k8s 덧붙임은 k8sMountParts 하나.
test("공용 코어 파일은 features/k8s 를 import 하지 않는다", () => {
  const dir = dirname(fileURLToPath(import.meta.url));
  const core = ["types.ts", "useMountStatus.ts", "mountTerms.ts", "MountCell.tsx", "MountMatrix.tsx", "NodeMountCards.tsx",
                "NodeMountSheet.tsx", "MountStatusPage.tsx"];
  for (const file of core) {
    const src = readFileSync(join(dir, file), "utf8");
    const imports = [...src.matchAll(/from\s+["']([^"']+)["']/g)].map((m) => m[1]);
    expect(imports.length, file).toBeGreaterThan(0);
    expect(imports.filter((p) => /(^|\/)k8s(\/|$)|k8sMountParts/.test(p)), file).toEqual([]);
  }
});

// 이식 의존 그물(2026-11-25 리뷰): 공용 코어의 바깥 import 는 dms-ssc 에도 있는 모듈(SSC_SHARED)과, dms-ssc 에 없어 이식 때 함께
// 가져갈 파일(PORT_WITH -- 이유와 함께)만. 위 그물은 features/k8s 만 봐서 MountStatusPage 가 dms-ssc 에 없는 ../jobs/searchField 를
// 새로 끌어온 것을 못 잡았다 -- 새 바깥 의존이 생기면 여기서 빨간불이 켜지고, 그 모듈이 dms-ssc 에 있는지 확인해 둘 중 하나에 넣는다.
test("공용 코어 파일의 바깥 import 는 dms-ssc 에도 있는 모듈 + 이식 때 함께 가져갈 목록뿐", () => {
  const dir = dirname(fileURLToPath(import.meta.url));
  const core = ["types", "useMountStatus", "mountTerms", "MountCell", "MountMatrix", "NodeMountCards", "NodeMountSheet",
                "MountStatusPage"];
  const SSC_SHARED = new Set([
    "react", "react-router-dom", "lucide-react", "@tanstack/react-query",
    "../../components/ui/Dialog", "../../components/ui/HoverTip", "../../components/ui/InfoPanel", "../../components/ui/Segmented",
    "../../lib/api", "../../lib/datetime", "../../lib/types", "../../lib/useNow", "../nodes/NodePlacement",
  ]);
  const PORT_WITH = new Set([
    "../jobs/searchField",   // useSearchField(react 만) -- 2026-10-25 dms-k8s 「전체 작업」 검색칸, dms-ssc 에 없다(MountStatusPage 머리 주석)
  ]);
  const files = ["types.ts", "useMountStatus.ts", "mountTerms.ts", "MountCell.tsx", "MountMatrix.tsx", "NodeMountCards.tsx",
                 "NodeMountSheet.tsx", "MountStatusPage.tsx"];
  const stray: string[] = [];
  for (const file of files) {
    const src = readFileSync(join(dir, file), "utf8");
    for (const [, spec] of src.matchAll(/from\s+["']([^"']+)["']/g)) {
      const ok = spec.startsWith("./") ? core.includes(spec.slice(2)) : SSC_SHARED.has(spec) || PORT_WITH.has(spec);
      if (!ok) stray.push(`${file}: ${spec}`);
    }
  }
  expect(stray).toEqual([]);
});

test("공용 칸 표시(sharedDisplay): 파드 점검 실패는 에이전트 정상·있음을 덮고, 이미 문제·지연이면 그대로(68dcbcdb 원칙)", () => {
  const ok = { state: "ok", exists: true, writable: true, reported_path: "/s", mount_point: "/s", fstype: "nfs4" } as never;
  const over = sharedDisplay(ok, true);
  expect(over.overridden).toBe(true);
  expect(over.meta.label).toBe("파드 점검 실패");
  expect(over.meta.tone).toBe("bad");
  expect(over.sub).toBe("에이전트: 정상");
  expect(sharedDisplay(ok, false).meta.label).toBe("정상");
  for (const state of ["local", "missing", "differs", "read_only", "stale"] as const) {
    const d = sharedDisplay({ ...(ok as object), state } as never, true);
    expect(d.overridden).toBe(false);
    expect(d.meta.label).toBe(SHARED_META[state].label);
  }
  const data = { control_pods: [
    { role: "api_pod", instance: "a", node_name: "n1", ok: false, reason: "artifact_base_jobs_missing", read_failed: true,
      checked_at: null, gate_state: null, in_table: true },
    { role: "controller_pod", instance: "c", node_name: "n2", ok: false, reason: "artifact_base_world_writable", read_failed: false,
      checked_at: null, gate_state: null, in_table: true },
  ] } as never;
  expect(podFailedOn(data, "n1")).toBe(true);
  expect(podFailedOn(data, "n2")).toBe(false);   // 권한(posture) 사유만이면 경로 점검 실패가 아니다
  expect(podFailedOn(data, "n3")).toBe(false);
});

test("파드 점검 실패 도움말은 실패한 역할을 이름으로(podFailedRoles·podFailedHelp — d40 실증)", () => {
  const data = { control_pods: [
    { role: "api_pod", instance: "a1", node_name: "n1", ok: false, reason: "artifact_base_jobs_missing", read_failed: true,
      checked_at: null, gate_state: "waiting", in_table: true },
    { role: "api_pod", instance: "a2", node_name: "n1", ok: true, reason: null, read_failed: false,
      checked_at: null, gate_state: "passed", in_table: true },
    { role: "controller_pod", instance: "c", node_name: "n1", ok: true, reason: null, read_failed: false,
      checked_at: null, gate_state: null, in_table: true },
  ] } as never;
  expect(podFailedRoles(data, "n1")).toEqual(["dms-api"]);
  expect(podFailedHelp(podFailedRoles(data, "n1"))).toMatch(/^이 노드의 dms-api 파드가 아티팩트 경로를 점검하지 못했습니다/);
  expect(podFailedHelp([])).toMatch(/^이 노드의 dms-api·컨트롤러 파드가/);
});

// 열 쪽 표기·거르기(2026-11-25 「스토리지가 100개가 될 수도 있어」).
describe("열 거르기·요약(columnProblem·filterColumns·columnSummaryText·columnNameLines·columnPathTail)", () => {
  const ro = (p: string) => ready(p, { state: "read_only", writable: false });

  test("columnProblem: 서버 _ro_outlier_columns 의 열 쪽 거울 -- 마운트 문제, 또는 신선한 ready 가 있는 열의 읽기 전용", () => {
    const c = column("s", "/s");
    expect(columnProblem(column("s", "/s", { broken: 1 }), [])).toBe(true);
    expect(columnProblem(c, [mkRow("a", { s: ro("/s") }), mkRow("b", { s: ready("/s") })])).toBe(true);
    // 모든 신선한 칸이 ro = 의도된 구성(문제 아님)
    expect(columnProblem(c, [mkRow("a", { s: ro("/s") }), mkRow("b", { s: ro("/s") })])).toBe(false);
    // ready 가 리포트 지연 행에만 있으면 세지 않는다(지금 상태 모름)
    expect(columnProblem(c, [mkRow("a", { s: ro("/s") }), mkRow("b", { s: ready("/s") }, { fresh: false })])).toBe(false);
    // ro 가 리포트 지연 행에만 있어도 세지 않는다
    expect(columnProblem(c, [mkRow("a", { s: ro("/s") }, { fresh: false }), mkRow("b", { s: ready("/s") })])).toBe(false);
    expect(columnProblem(c, [mkRow("a", { s: absent("/s") }), mkRow("b", { s: ready("/s") })])).toBe(false);
    expect(columnProblem(c, [])).toBe(false);
  });

  test("columnProblem·cellProblem: 어디에도 마운트되지 않은 열의 없음은 문제(서버 _mounted_columns 거울, 2026-11-27)", () => {
    const c = column("s", "/s");
    const pend = { ...ready("/s"), state: "pending" as const, pending: "not_reported" as const, status: null };
    // 신선한 칸이 모두 없음 -- 문제
    expect(columnProblem(c, [mkRow("a", { s: absent("/s") }), mkRow("b", { s: absent("/s") })])).toBe(true);
    // 읽기 전용도 마운트 -- 없음은 안 붙인 노드(그 ro 도 정상 노드가 없어 바깥값이 아니다)
    expect(columnProblem(c, [mkRow("a", { s: ro("/s") }), mkRow("b", { s: absent("/s") })])).toBe(false);
    // 정상이 리포트 지연 행에만 있으면 근거가 아니다
    expect(columnProblem(c, [mkRow("a", { s: absent("/s") }), mkRow("b", { s: ready("/s") }, { fresh: false })])).toBe(true);
    // 확인 대기는 마운트가 아니다
    expect(columnProblem(c, [mkRow("a", { s: absent("/s") }), mkRow("b", { s: pend })])).toBe(true);
    // 없음이 리포트 지연 행에만 있으면(신선한 칸은 확인 대기뿐) 문제 아님
    expect(columnProblem(c, [mkRow("a", { s: absent("/s") }, { fresh: false }), mkRow("b", { s: pend })])).toBe(false);
    // 칸 판정: 같은 재료에서 없음만 열에 따라 갈린다
    const rows = [mkRow("a", { s: absent("/s"), t: absent("/t") }), mkRow("b", { s: absent("/s"), t: ready("/t") })];
    const facts = columnFacts(rows);
    expect([cellProblem("s", absent("/s"), facts), cellProblem("t", absent("/t"), facts)]).toEqual([true, false]);
    expect([absentNowhere("s", absent("/s"), facts), absentNowhere("t", absent("/t"), facts)]).toEqual([true, false]);
    expect(cellProblem("t", ready("/t"), facts)).toBe(false);
    expect(cellProblem("t", ready("/t", { state: "broken" }), facts)).toBe(true);
  });

  test("NOWHERE 픽스처: 문제 있는 스토리지 = 끊긴 마운트 열 + 어디에도 없는 열 / 손으로 센 픽스처 수 = 거울", () => {
    expect(filterColumns(NOWHERE.storages, NOWHERE.nodes, { q: "", problemsOnly: true }).map((c) => c.storage_name))
      .toEqual(["cephfs-dms", "cephfs-typo"]);
    const facts = columnFacts(NOWHERE.nodes);
    expect(facts.mounted.has("cephfs-typo")).toBe(false);
    expect(facts.mounted.has("cephfs-dms")).toBe(true);                 // w1 은 정상 -- 끊긴 칸은 broken 이라 문제
    // 픽스처의 problem_count 는 서버 규칙으로 손으로 셌다 -- 거울(칸 + 공용 디렉터리 한 칸)이 같은 수를 내야 화면 표시가 두 말을 안 한다
    const mirror = (data: MountStatusResponse) => {
      const f = columnFacts(data.nodes);
      return data.nodes.map((r) => (r.fresh
        ? Object.entries(r.cells).filter(([name, c]) => cellProblem(name, c, f)).length
          + (r.shared_dir !== null && SHARED_PROBLEM_STATES.includes(r.shared_dir.state) ? 1 : 0)
        : null));
    };
    for (const data of [LIVE, NOWHERE, VARIETY]) expect(mirror(data)).toEqual(data.nodes.map((r) => r.problem_count));
  });

  test("filterColumns: 조건 없으면 같은 배열, 이름·경로 부분 일치(대소문자 무시·앞뒤 공백 무시), 문제 열, 둘 다면 AND·순서 유지", () => {
    const cols = LIVE.storages;
    expect(filterColumns(cols, LIVE.nodes, { q: "", problemsOnly: false })).toBe(cols);
    expect(filterColumns(cols, LIVE.nodes, { q: "   ", problemsOnly: false })).toBe(cols);
    const names = (q: string, problemsOnly = false, data = LIVE) =>
      filterColumns(data.storages, data.nodes, { q, problemsOnly }).map((c) => c.storage_name);
    expect(names("PV")).toEqual(["k8s-pv-gpfs", "k8s-pv-nfs", "k8s-pv-onefs"]);
    expect(names("  Secondary ")).toEqual(["cephfs-secondary"]);
    expect(names("/k8s_pv/n")).toEqual(["k8s-pv-nfs"]);                 // 경로로도 맞는다
    expect(names("zzz")).toEqual([]);
    // VARIETY: ceph 는 마운트 문제·읽기 전용 outlier, sec 은 마운트 문제
    expect(names("", true, VARIETY)).toEqual(["ceph", "sec"]);
    expect(names("", true)).toEqual([]);                                 // LIVE 는 없음뿐
    const w = wide(100, 4);
    const problems = filterColumns(w.storages, w.nodes, { q: "", problemsOnly: true });
    expect(problems).toHaveLength(20);
    expect(filterColumns(w.storages, w.nodes, { q: "archive", problemsOnly: false })).toHaveLength(14);
    expect(filterColumns(w.storages, w.nodes, { q: "archive", problemsOnly: true }).map((c) => c.storage_name))
      .toEqual(["stor-056-gpfs-project-archive-cold", "stor-063-gpfs-project-archive-cold"]);
    // 모든 노드가 ro 인 열(i%10==9)은 문제 아님
    expect(problems.some((c) => c.storage_name === "stor-009")).toBe(false);
  });

  test("columnSummaryText: 0 은 빼고 단위(대·칸)를 붙인다, 보고 없음", () => {
    expect(columnSummaryText(VARIETY.storages[0].summary)).toBe("마운트 3/4대 · 마운트 문제 1칸 · 읽기 전용 1칸");
    expect(columnSummaryText(VARIETY.storages[1].summary)).toBe("마운트 1/3대 · 마운트 문제 1칸 · 확인 대기 1칸");
    expect(columnSummaryText(LIVE.storages[1].summary)).toBe("마운트 1/2대 · 없음 1칸");
    expect(columnSummaryText({ ...LIVE.storages[0].summary, ready: 0, total: 0, pending: 2 })).toBe("보고 없음 · 확인 대기 2칸");
    expect(columnRuleText("ready_nodes=3/4")).toMatch(/^기준: 신선한 보고만 — 스토리지 목록의 상태\(ready_nodes=3\/4\)/);
  });

  test("columnNameLines: 13자 이하 한 줄, 26자 이하는 두 조각 다 13자 이하인 마지막 하이픈 뒤(없으면 13자), 넘으면 이름 + 「…」끝 12자", () => {
    expect(columnNameLines("cephfs-third")).toEqual(["cephfs-third"]);
    expect(columnNameLines("x".repeat(13))).toEqual(["x".repeat(13)]);
    expect(columnNameLines("cephfs-secondary")).toEqual(["cephfs-", "secondary"]);
    // 리뷰 실측: 브라우저 자연 줄바꿈이면 「weka-」/「research-」/「archive-01」 세 줄(-01 앞은 못 꺾는다) -- 두 줄 칸에서 끝이 잘렸다
    expect(columnNameLines("weka-research-archive-01")).toEqual(["weka-research", "-archive-01"]);
    expect(columnNameLines("stor-010-nfs-scratch")).toEqual(["stor-010-nfs-", "scratch"]);
    expect(columnNameLines("stor-011-weka-research-01")).toEqual(["stor-011-weka", "-research-01"]);
    expect(columnNameLines("x".repeat(24))).toEqual(["x".repeat(13), "x".repeat(11)]);
    expect(columnNameLines("y".repeat(26))).toEqual(["y".repeat(13), "y".repeat(13)]);
    for (const name of ["cephfs-secondary", "weka-research-archive-01", "a-b-c-d-e-f-g-h-i-j-k-l-m", "z".repeat(26)]) {
      const lines = columnNameLines(name);
      expect(lines.join(""), name).toBe(name);                          // 26자 이하는 두 줄 안에 다(빠지는 글자 없음)
      expect(lines.every((l) => l.length <= 13), name).toBe(true);
    }
    expect(columnNameLines("stor-007-gpfs-project-archive-cold")).toEqual(["stor-007-gpfs-project-archive-cold", "…archive-cold"]);
  });

  test("columnPathTail: 14자 이하는 null(전체), 넘으면 끝 13자 안의 「/」 경계 끝(8자 이상) 또는 끝 13자 -- 같은 접두도 열마다 다르다", () => {
    expect(columnPathTail("/k8s_pv/gpfs")).toBeNull();
    expect(columnPathTail("/x".repeat(7))).toBeNull();                  // 14자
    expect(columnPathTail("/mgmt_storage/gpfs/proj01")).toBe("/gpfs/proj01");
    expect(columnPathTail("/mgmt_storage/onefs/home")).toBe("/onefs/home");
    // 「/」 경계 끝이 짧으면(「/003」) 끝 13자를 그대로 -- 더 많이 보인다
    expect(columnPathTail("/mgmt_storage/very/long/mount/path/for/testing/truncation/003")).toBe("runcation/003");
    expect(columnPathTail("/mgmt_storage/project_archive_cold_tier")).toBe("ive_cold_tier");
    for (const p of ["/mgmt_storage/gpfs/proj01", "/mgmt_storage/onefs/home", "/mgmt_storage/nfs/scratch-long-name"]) {
      const tail = columnPathTail(p)!;
      expect(p.endsWith(tail), p).toBe(true);
      expect([...tail].length, p).toBeLessThanOrEqual(13);               // 「…」 붙여 14자 = 머리 상자 한 줄
    }
  });
});
