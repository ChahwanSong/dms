import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";
import {
  CELL_META, REASON_TEXT, SHARED_META, TONE_CLASS, cellHelp, cellLabel, cellMounted, cellSub, roleLabel, sharedHelp,
  sharedSub, usageDetail, usageText, writableText,
} from "./mountTerms";
import type { CellState, MountCellData, SharedCellData, SharedState } from "./types";

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

  test("모르는 사유는 원문, 사유도 없으면 원문 status", () => {
    expect(cellLabel(cell({ state: "broken", status: "Missing", reason: "brand_new_reason" }))).toBe("brand_new_reason");
    expect(cellLabel(cell({ state: "broken", status: "Error", reason: null }))).toBe("Error");
    expect(cellLabel(cell({ state: "broken", status: null, reason: null }))).toBe("마운트 문제");
    expect(cellHelp(cell({ state: "broken", reason: "brand_new_reason" }), CTX)).toMatch(/알 수 없는 사유 코드/);
  });
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
  expect(roleLabel("api_pod")).toBe("api_pod");
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
