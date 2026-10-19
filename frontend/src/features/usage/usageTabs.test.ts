import { expect, test } from "vitest";
import { buildStorageTabs, defaultStorage } from "./usageTabs";
import type { Storage, UnregisteredStorage, UsageStorageSummary } from "../../lib/types";

const st = (name: string): Storage => ({ storage_name: name, mount_path: `/${name}`, managed_root: `/${name}/m`,
                                         backend_type: "cephfs", enabled: 1, status: "Ready", status_detail: null });
const sum = (name: string, over: Partial<UsageStorageSummary> = {}): UsageStorageSummary => ({
  storage_name: name, registered: true, source: "registered", backend_type: "cephfs", mount_path: `/${name}`,
  target_count: 1, succeeded_scan_count: 1, last_scan_at: "2026-10-01T00:00:00Z", ...over });
const un = (name: string, target_count = 1): UnregisteredStorage => ({
  storage_name: name, target_count, succeeded_scan_count: target_count, last_scan_at: null, single_count: 1,
  batches: [], request_count: 1, mixed_batches: [], last_known: null });

test("탭 순서: 등록 이름순(코드포인트) → 미등록 이름순, 원천 셋의 합집합", () => {
  const tabs = buildStorageTabs({
    registered: [st("b"), st("a-2"), st("a10")],
    summary: [sum("a-2"), sum("b"), sum("zz-old", { registered: false })],
    unregistered: [un("zz-old"), un("failed-only", 0)],
  });
  expect(tabs.map((t) => [t.name, t.registered])).toEqual([
    ["a-2", true], ["a10", true], ["b", true], ["failed-only", false], ["zz-old", false]]);
});

test("대상 수: 요약 > 미등록 행 > 모름(null) -- 요약이 없으면 0 으로 메우지 않는다", () => {
  const before = buildStorageTabs({ registered: [st("a")] });
  expect(before[0]).toMatchObject({ name: "a", registered: true, targetCount: null, hasTargets: null });
  const after = buildStorageTabs({ registered: [st("a")], summary: [sum("a", { target_count: 0 })],
                                   unregistered: [un("old", 4)] });
  expect(after.map((t) => [t.name, t.targetCount, t.hasTargets])).toEqual([["a", 0, false], ["old", 4, true]]);
});

test("등록 여부: 요약 > 미등록 목록 > 등록 목록 -- 등록 목록을 모르면 scan 기록 이름을 「미등록」이라 하지 않는다", () => {
  expect(buildStorageTabs({ scanNames: ["x"] })[0].registered).toBe(true);
  expect(buildStorageTabs({ registered: [], scanNames: ["x"] })[0].registered).toBe(false);
  expect(buildStorageTabs({ registered: [st("x")], unregistered: [un("x")] })[0].registered).toBe(false);
  expect(buildStorageTabs({ registered: [], summary: [sum("x")] })[0].registered).toBe(true);
});

test("요약 실패 대체: scan 기록 이름으로 기록 여부만 안다(대상 수는 모름)", () => {
  const tabs = buildStorageTabs({ registered: [st("a"), st("b")], scanNames: ["b"] });
  expect(tabs.map((t) => [t.name, t.targetCount, t.hasTargets])).toEqual([["a", null, false], ["b", null, true]]);
  expect(defaultStorage(tabs)).toBe("b");
});

test("기본 탭: 기록 있는 등록 → 기록 있는 미등록 → 첫 탭 → 없음", () => {
  const t = (registered: Storage[], summary: UsageStorageSummary[], unregistered: UnregisteredStorage[] = []) =>
    defaultStorage(buildStorageTabs({ registered, summary, unregistered }));
  expect(t([st("a"), st("b")], [sum("a", { target_count: 0 }), sum("b")])).toBe("b");
  expect(t([st("a")], [sum("a", { target_count: 0 }), sum("old", { registered: false })], [un("old")])).toBe("old");
  expect(t([st("a")], [sum("a", { target_count: 0 })], [un("ghost", 0)])).toBe("a");
  expect(t([], [], [un("ghost", 0)])).toBe("ghost");
  expect(t([], [])).toBeNull();
});
