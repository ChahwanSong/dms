import { test, expect } from "vitest";
import { absolutePath, absSummary, pathSummary } from "./storagePaths";

test("절대경로 조합: 뿌리 + 상대경로, 슬래시 중복 없이", () => {
  expect(absolutePath("/cephfs/dms", "team/alpha")).toBe("/cephfs/dms/team/alpha");
  expect(absolutePath("/cephfs/dms/", "/team")).toBe("/cephfs/dms/team");
  expect(absolutePath("/", "team")).toBe("/team");
});

test("빈 상대경로는 뿌리 자신이다(정상값 — 모름으로 뭉개지 않는다)", () => {
  expect(absolutePath("/cephfs/dms", "")).toBe("/cephfs/dms");
  expect(absolutePath("/", "")).toBe("/");
});

test("뿌리를 모르면 null — 거짓 경로를 지어내지 않는다", () => {
  expect(absolutePath(undefined, "team")).toBeNull();
  expect(absolutePath(null, "team")).toBeNull();
  expect(absolutePath("", "team")).toBeNull();
  // payload 결손·오염(문자열 아님)도 조합 불가
  expect(absolutePath("/cephfs/dms", undefined)).toBeNull();
  expect(absolutePath("/cephfs/dms", 42)).toBeNull();
});

const ROOTS = { "cephfs-dms": "/cephfs/dms", "gpfs-dms": "/gpfs/dms" };

test("scan/rm 요약: storage:target 과 절대경로", () => {
  const p = { storage: "cephfs-dms", target: "team" };
  expect(pathSummary("scan", p)).toBe("cephfs-dms:team");
  expect(absSummary("scan", p, ROOTS)).toBe("/cephfs/dms/team");
  // 모르는 스토리지는 조합 불가
  expect(absSummary("scan", { storage: "other", target: "team" }, ROOTS)).toBeNull();
});

test("sync 요약: 출발 → 도착, 둘 다 알 때만 절대경로", () => {
  const p = { source_storage: "cephfs-dms", source: "team",
              destination_storage: "gpfs-dms", destination: "backup" };
  expect(pathSummary("sync", p)).toBe("cephfs-dms:team → gpfs-dms:backup");
  expect(absSummary("sync", p, ROOTS)).toBe("/cephfs/dms/team → /gpfs/dms/backup");
  expect(absSummary("sync", { ...p, destination_storage: "other" }, ROOTS)).toBeNull();
});

test("빈 맵(비관리자·조회 실패)에선 절대경로가 아예 없다", () => {
  expect(absSummary("scan", { storage: "cephfs-dms", target: "team" }, {})).toBeNull();
  expect(absSummary("sync", { source_storage: "cephfs-dms", source: "a",
                              destination_storage: "gpfs-dms", destination: "b" }, {}))
    .toBeNull();
});
