// rm 배치 겹침 즉답(2026-10-21) — 서버 domain._reject_overlapping_rm_targets 와 같은 판정 규칙을 고정한다
// (tests/test_domain_batch.py 의 겹침·비겹침 표와 같은 경우들).
import { test, expect } from "vitest";
import { normalizedRmTarget, rmTargetOverlaps } from "./rmTargets";

const rows = (...targets: string[]) => targets.map((t, i) => ({ label: `${i + 1}행`, target: t }));

test("정규화는 normpath 와 같다 — 빈 성분·「.」 제거, 형식 오류·빈값은 판정 밖(null)", () => {
  expect(normalizedRmTarget("a//b/./c/")).toBe("a/b/c");
  expect(normalizedRmTarget("./a")).toBe("a");
  for (const bad of ["", "  ", ".", "./", "/abs", "a/../b", ".."]) expect(normalizedRmTarget(bad)).toBeNull();
  expect(normalizedRmTarget("a b")).toBe("a b");                  // 공백은 이름의 일부(서버도 다듬지 않는다)
});

test.each([
  [["a", "a"]], [["a", "a/"]], [["a/./b", "a/b"]], [["a", "a/b"]], [["a/b/c", "x", "a"]],
  [["team/old", "team/old/sub/deeper"]],
])("겹침 %j", (targets) => {
  expect(rmTargetOverlaps(rows(...targets))).toHaveLength(1);
});

test.each([[["a/b", "a/c"]], [["a", "ab", "a b", "a.b"]], [["a/b", "b/a"]], [["..", ".", "", "/abs", "a"]]])(
  "겹치지 않음 %j", (targets) => {
    expect(rmTargetOverlaps(rows(...targets))).toEqual([]);
  });

test("문장은 풀어야 할 행(뒤의 중복·자손)이 주어이고, 상대 행 이름과 경로를 말한다", () => {
  expect(rmTargetOverlaps(rows("a/b", "x", "a", "x/"))).toEqual([
    "1행: 3행의 경로(a) 아래입니다 — 상위 경로를 지우면 함께 지워지니 하나만 남기세요",
    "4행: 같은 경로(x)가 2행에도 있습니다",
  ]);
});

test("focus: 그 항목이 낀 겹침만 — 기존 항목끼리의 옛 겹침은 항목 추가 화면이 말하지 않는다", () => {
  const entries = [{ label: "기존 #0", target: "a" }, { label: "기존 #1", target: "a/b" },
                   { label: "추가할 경로", target: "z" }];
  expect(rmTargetOverlaps(entries, 2)).toEqual([]);
  expect(rmTargetOverlaps([...entries.slice(0, 2), { label: "추가할 경로", target: "a/c" }], 2)).toEqual([
    "추가할 경로: 기존 #0의 경로(a) 아래입니다 — 상위 경로를 지우면 함께 지워지니 하나만 남기세요"]);
  expect(rmTargetOverlaps([{ label: "기존 #0", target: "q/r" }, { label: "추가할 경로", target: "q" }], 1)).toEqual([
    "기존 #0: 추가할 경로의 경로(q) 아래입니다 — 상위 경로를 지우면 함께 지워지니 하나만 남기세요"]);
});
