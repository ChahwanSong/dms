// 슬라이스 32: 스토리지가 배치 레벨로 올라가 CSV 는 경로만 나른다.
// scan = 행당 1열(target), sync = 행당 2열(source,destination).
import { parseItemsCsv, serializeItemsCsv } from "./csv";
import { test, expect } from "vitest";

test("scan: 헤더 없는 경로 목록", () => {
  const { rows, errors } = parseItemsCsv("scan", "a/b\nc");
  expect(errors).toEqual([]);
  expect(rows).toEqual([{ target: "a/b" }, { target: "c" }]);
});

test("scan: 헤더(target) 있는 경우 스킵", () => {
  const { rows, errors } = parseItemsCsv("scan", "target\na/b");
  expect(errors).toEqual([]);
  expect(rows).toEqual([{ target: "a/b" }]);
});

test("scan: path 헤더 토큰도 인정", () => {
  const { rows } = parseItemsCsv("scan", "path\na");
  expect(rows).toEqual([{ target: "a" }]);
});

test("sync: 2열 파싱(헤더 유/무)", () => {
  expect(parseItemsCsv("sync", "a,b\nc,d").rows).toEqual([
    { source: "a", destination: "b" },
    { source: "c", destination: "d" },
  ]);
  expect(parseItemsCsv("sync", "source,destination\na,b").rows).toEqual([
    { source: "a", destination: "b" },
  ]);
});

test("헤더 토큰은 대소문자 무시", () => {
  expect(parseItemsCsv("scan", "Target\na").rows).toEqual([{ target: "a" }]);
  expect(parseItemsCsv("sync", "SOURCE,Destination\na,b").rows).toEqual([
    { source: "a", destination: "b" },
  ]);
});

test("헤더가 아닌 첫 줄은 데이터로 취급", () => {
  // "target" 이 아닌 일반 경로면 스킵하지 않는다
  const { rows } = parseItemsCsv("scan", "data/target1\nb");
  expect(rows).toEqual([{ target: "data/target1" }, { target: "b" }]);
});

test("빈 줄 무시·셀 trim", () => {
  const { rows, errors } = parseItemsCsv("sync", "\n a , b \n\nc,d\n");
  expect(errors).toEqual([]);
  expect(rows).toEqual([
    { source: "a", destination: "b" },
    { source: "c", destination: "d" },
  ]);
});

test("열 수 불일치는 행 번호와 함께 오류(조용한 드랍 금지)", () => {
  const { rows, errors } = parseItemsCsv("sync", "a,b\nc\ne,f");
  expect(rows).toEqual([
    { source: "a", destination: "b" },
    { source: "e", destination: "f" },
  ]);
  expect(errors.length).toBe(1);
  expect(errors[0]).toMatch(/^2행:/);
});

test("빈 셀은 오류", () => {
  const { errors } = parseItemsCsv("sync", "a,\n,b");
  expect(errors.length).toBe(2);
  expect(errors[0]).toMatch(/^1행:/);
  expect(errors[1]).toMatch(/^2행:/);
});

test("빈 입력은 오류", () => {
  const { rows, errors } = parseItemsCsv("scan", "  \n ");
  expect(rows).toEqual([]);
  expect(errors.length).toBeGreaterThan(0);
});

test("scan: 경로에 콤마가 든 행은 오류(1열 계약 — 침묵 분할 금지)", () => {
  const { rows, errors } = parseItemsCsv("scan", "a,b");
  expect(rows).toEqual([]);
  expect(errors.length).toBe(1);
  expect(errors[0]).toMatch(/^1행:/);
});

test("왕복: parse(serialize(rows)) == rows", () => {
  const scanRows = [{ target: "a/b" }, { target: "c" }];
  const scanText = serializeItemsCsv("scan", scanRows);
  expect(scanText.split(/\r?\n/)[0]).toBe("target"); // 헤더 포함 내보내기
  expect(parseItemsCsv("scan", scanText).rows).toEqual(scanRows);

  const syncRows = [
    { source: "a", destination: "b" },
    { source: "c/d", destination: "e" },
  ];
  const syncText = serializeItemsCsv("sync", syncRows);
  expect(syncText.split(/\r?\n/)[0]).toBe("source,destination");
  expect(parseItemsCsv("sync", syncText).rows).toEqual(syncRows);
});
