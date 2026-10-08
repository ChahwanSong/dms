import { test, expect } from "vitest";
import { absolutePath, absParts, absSummary, destinationParent, pathParts, pathSummary, relativePathProblem } from "./storagePaths";

test("destinationParent: sync 목적지의 상위 디렉토리 절대경로(쓰기 권한이 필요한 곳)", () => {
  expect(destinationParent("/cephfs/managed", "dms_test/dst")).toBe("/cephfs/managed/dms_test");
  expect(destinationParent("/cephfs/managed", "dst")).toBe("/cephfs/managed");
  expect(destinationParent("/cephfs/managed", "/a/b/c/")).toBe("/cephfs/managed/a/b");
  expect(destinationParent("/cephfs/managed", "")).toBe("/cephfs");       // 목적지 = 관리 디렉토리
  expect(destinationParent("/data", "")).toBe("/");
  expect(destinationParent(undefined, "a/b")).toBeNull();                // 뿌리 모름 -- 지어내지 않음
  expect(destinationParent("/cephfs/managed", undefined)).toBeNull();
});

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

test("조각(pathParts·absParts)은 구조로 가른다 -- 경로 이름의 「 → 」에서 갈리지 않는다", () => {
  const p = { source_storage: "cephfs-dms", source: "dir → x", destination_storage: "gpfs-dms", destination: "b" };
  expect(pathParts("sync", p)).toEqual(["cephfs-dms:dir → x", "gpfs-dms:b"]);
  expect(absParts("sync", p, ROOTS)).toEqual(["/cephfs/dms/dir → x", "/gpfs/dms/b"]);
  expect(pathParts("scan", { storage: "cephfs-dms", target: "a → b" })).toEqual(["cephfs-dms:a → b"]);
  expect(absParts("scan", { storage: "other", target: "t" }, ROOTS)).toBeNull();
});

test("문자열이 아닌 경로 값(DB 변조)은 「?」 -- [object Object]·a,b 를 내지 않는다, null·부재는 「—」", () => {
  expect(pathSummary("sync", { source_storage: { a: 1 }, source: ["x", "y"], destination_storage: 5, destination: null }))
    .toBe("?:? → ?:—");
  expect(pathSummary("scan", {})).toBe("—:—");
  expect(pathSummary("scan", { storage: "s", target: "" })).toBe("s:");   // 빈 경로 = 뿌리(정상값)
});

test("빈 맵(비관리자·조회 실패)에선 절대경로가 아예 없다", () => {
  expect(absSummary("scan", { storage: "cephfs-dms", target: "team" }, {})).toBeNull();
  expect(absSummary("sync", { source_storage: "cephfs-dms", source: "a",
                              destination_storage: "gpfs-dms", destination: "b" }, {}))
    .toBeNull();
});

test("relativePathProblem: domain.validate_relative_path(422 unsafe_path) 의 즉답 미러", () => {
  // 통과: 상대경로·중복 슬래시·끝 슬래시·"./" 접두(정규화하면 하위 경로)
  for (const ok of ["a", "a/b", "a//b/", "./a", "team/data.v2", "a/.../b"])
    expect(relativePathProblem(ok)).toBeNull();
  // 미입력은 sanity(호출측) 몫 -- 여기선 문제 아님
  expect(relativePathProblem("")).toBeNull();
  expect(relativePathProblem("   ")).toBeNull();
  // 공백은 거부가 아니다(서버는 다듬지 않고 그대로 이름으로 쓴다)
  expect(relativePathProblem("backup/x ")).toBeNull();
  // 거부: "/" 시작, ".." 구성요소, 관리 디렉토리 자신(정규화 ".")
  expect(relativePathProblem("/team/data")).toMatch(/"\/" 로 시작할 수 없습니다/);
  expect(relativePathProblem("a/../b")).toMatch(/"\.\." 은 쓸 수 없습니다/);
  expect(relativePathProblem("..")).toMatch(/"\.\." 은 쓸 수 없습니다/);
  expect(relativePathProblem(".")).toMatch(/관리 디렉토리 자신/);
  expect(relativePathProblem("./")).toMatch(/관리 디렉토리 자신/);
  expect(relativePathProblem("./.")).toMatch(/관리 디렉토리 자신/);
});
