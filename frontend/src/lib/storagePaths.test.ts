import { test, expect } from "vitest";
import {
  absolutePath, absParts, absSummary, destinationParent, normalizedRelativePath, pathParts, pathSummary, relativePathProblem,
  RM_PATH_LINK_RULE,
} from "./storagePaths";

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

test("relativePathProblem: 제어 문자(C0·DEL)는 거부 -- 서버 validate_relative_path 와 같은 unsafe_path(d194)", () => {
  // 셸 검사의 명령 치환이 이름 끝 줄바꿈을 지워 「x\n」 링크 성분이 봉쇄 검사를 빠져나갔다 -- 서버가 받지 않으니 화면도 잠근다
  for (const bad of ["a\nb", "link\n/x", "a\tb", "a\u0000b", "a\u0001", "a\u001fb", "a\u007fb", "x\r"]) {
    expect([JSON.stringify(bad), relativePathProblem(bad)])
      .toEqual([JSON.stringify(bad), "제어 문자(줄바꿈·탭 등)는 쓸 수 없습니다 — 경로에 보이지 않는 문자가 섞였는지 확인하세요"]);
  }
  // 제어 문자가 아닌 것은 그대로 통과(공백·한글·DEL 바로 다음 문자·「→」)
  for (const ok of ["a b", "팀/데이터", "a\u0080b", "dir → x", "a~b"]) expect(relativePathProblem(ok)).toBeNull();
  // 「/」 시작이 먼저다(같은 입력에 한 사유)
  expect(relativePathProblem("/a\nb")).toMatch(/"\/" 로 시작할 수 없습니다/);
});

test("normalizedRelativePath: 서버가 저장할 모양(normpath) -- 끝 「/」·「//」·「.」 제거, 공백은 그대로, 형식 오류는 null", () => {
  expect(normalizedRelativePath("team/link/")).toBe("team/link");
  expect(normalizedRelativePath("a//b/./c/")).toBe("a/b/c");
  expect(normalizedRelativePath("./a")).toBe("a");
  expect(normalizedRelativePath("a/b")).toBe("a/b");
  expect(normalizedRelativePath("backup/x ")).toBe("backup/x ");
  expect(normalizedRelativePath(" a")).toBe(" a");
  for (const bad of ["", "   ", ".", "./", "/abs", "a/../b", "a\nb"]) expect(normalizedRelativePath(bad)).toBeNull();
});

test("RM_PATH_LINK_RULE(d194): 중간 단계 링크만 거부, 경로 자신이 링크면 링크만 지운다 -- 옛 「대상 자신 포함 거부」를 말하지 않는다", () => {
  expect(RM_PATH_LINK_RULE).toBe("삭제할 경로의 중간 단계(관리 디렉토리 아래)가 심볼릭 링크면 거부됩니다 — 링크를 따라가 지우지 "
    + "않습니다. 경로 자신(마지막 이름)이 링크면 링크만 지우고, 링크가 가리키는 곳은 지우지 않습니다.");
  expect(RM_PATH_LINK_RULE).not.toMatch(/대상 자신 포함|링크 자체를 지우지도/);
});
