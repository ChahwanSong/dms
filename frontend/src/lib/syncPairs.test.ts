import { expect, test } from "vitest";
import { pairAllowed, syncChoices } from "./syncPairs";
import type { UserStorage } from "./types";

const S = (n: string): UserStorage => ({ storage_name: n, backend_type: "cephfs", status: "Ready" });
const ALL = [S("a"), S("b"), S("c"), S("d")];
const PAIRS = [
  { source_storage: "a", destination_storage: "b" },
  { source_storage: "a", destination_storage: "c" },
  { source_storage: "c", destination_storage: "c" },
];
const names = (xs: UserStorage[]) => xs.map((s) => s.storage_name);

test("아무것도 안 골랐으면 어느 쌍에든 나오는 스토리지만 -- 쌍이 없는 d 는 양쪽에서 빠진다", () => {
  const { sources, destinations } = syncChoices(ALL, PAIRS, "", "");
  expect(names(sources)).toEqual(["a", "c"]);
  expect(names(destinations)).toEqual(["b", "c"]);
});

test("소스를 고르면 목적지는 그 소스의 허용 목적지만(방향이 있다)", () => {
  expect(names(syncChoices(ALL, PAIRS, "a", "").destinations)).toEqual(["b", "c"]);
  expect(names(syncChoices(ALL, PAIRS, "c", "").destinations)).toEqual(["c"]);
});

test("목적지를 고르면 소스는 그 목적지로 허용된 소스만", () => {
  expect(names(syncChoices(ALL, PAIRS, "", "c").sources)).toEqual(["a", "c"]);
  expect(names(syncChoices(ALL, PAIRS, "", "b").sources)).toEqual(["a"]);
});

test("허용 목록에서 빠진 현재 선택도 선택지에 남는다(비울 수 있게) -- 조합 판정은 pairAllowed", () => {
  const { sources, destinations } = syncChoices(ALL, PAIRS, "d", "b");
  expect(names(sources)).toEqual(["a", "d"]);
  expect(names(destinations)).toEqual(["b"]);
  expect(pairAllowed(PAIRS, "d", "b")).toBe(false);
  expect(pairAllowed(PAIRS, "a", "b")).toBe(true);
  expect(pairAllowed(PAIRS, "b", "a")).toBe(false);
});

test("허용 쌍이 없으면 선택지가 비고 모든 조합이 불가", () => {
  const { sources, destinations } = syncChoices(ALL, [], "", "");
  expect(sources).toEqual([]);
  expect(destinations).toEqual([]);
  expect(pairAllowed([], "a", "a")).toBe(false);
});
