// 배치 항목 CSV 의 얇은 클라이언트(2026-10-26 — 해석은 서버 batch_csv, 여기는 오류 문구·직렬화·주소만).
import { test, expect } from "vitest";
import {
  batchCsvTemplateUrl, batchItemsCsvUrl, csvErrorLines, csvErrorText, itemsToRows, serializeItemsCsv,
} from "./csv";
import { reasonText } from "./api";

const err = (over: Partial<Parameters<typeof csvErrorText>[0]> = {}) => ({
  line: 3, row: 2, reason_code: "unsafe_path", message: "unsafe_path: '/x'", related_line: null, ...over });

test("오류 문구: 물리 줄 번호 + 사유 코드 문구(reasonText) — 서버 message(기술 상세)는 쓰지 않는다", () => {
  expect(csvErrorText(err())).toBe(`3번째 줄: ${reasonText("unsafe_path")}`);
});

test("rm 겹침은 상대 줄까지", () => {
  expect(csvErrorText(err({ reason_code: "batch_rm_targets_overlap", related_line: 2 })))
    .toBe(`3번째 줄: ${reasonText("batch_rm_targets_overlap")} (2번째 줄과 겹침)`);
});

test("파일 단위 사유(줄 없음)는 접두 없이", () => {
  expect(csvErrorText(err({ line: null, row: null, reason_code: "empty_batch" }))).toBe(reasonText("empty_batch"));
});

test("서버가 상한까지만 실은 오류는 「외 N개」로 말한다(조용한 절단 금지)", () => {
  expect(csvErrorLines({ errors: [err()], error_count: 1 })).toHaveLength(1);
  const lines = csvErrorLines({ errors: [err(), err({ line: 4 })], error_count: 250 });
  expect(lines).toHaveLength(3);
  expect(lines[2]).toBe("외 248개 오류");
});

test("새 CSV 사유 코드는 전부 문구가 있다(원시 코드가 보이지 않는다)", () => {
  for (const code of ["batch_csv_bad_row", "batch_csv_malformed", "batch_csv_too_large", "batch_csv_too_many_rows"])
    expect(reasonText(code)).not.toBe(code);
});

test("항목 → 표의 경로 짝", () => {
  expect(itemsToRows("scan", [{ storage: "s1", target: "a" }])).toEqual([{ target: "a" }]);
  expect(itemsToRows("rm", [{ target: "x" }])).toEqual([{ target: "x" }]);
  expect(itemsToRows("sync", [{ source_storage: "s1", source: "a", destination_storage: "s2", destination: "b" }]))
    .toEqual([{ source: "a", destination: "b" }]);
});

test("직렬화: 헤더 포함, 평범한 경로는 그대로(예전 모양)", () => {
  expect(serializeItemsCsv("scan", [{ target: "a/b" }, { target: "c" }])).toBe("target\na/b\nc");
  expect(serializeItemsCsv("rm", [{ target: "old/a" }, { target: "old/b" }])).toBe("target\nold/a\nold/b");
  expect(serializeItemsCsv("sync", [{ source: "a", destination: "b" }])).toBe("source,destination\na,b");
});

test("직렬화: 콤마·따옴표는 RFC 4180 따옴표, 수식 시작 문자는 ' 방어(서버 해석이 걷는다)", () => {
  expect(serializeItemsCsv("scan", [{ target: "a,b" }, { target: 'q"t' }, { target: "=x" }]))
    .toBe('target\n"a,b"\n"q""t"\n\'=x');
  expect(serializeItemsCsv("sync", [{ source: "-a", destination: "b,c" }])).toBe("source,destination\n'-a,\"b,c\"");
});

test("다운로드 주소", () => {
  expect(batchCsvTemplateUrl("sync")).toBe("/api/admin/batches/csv-template?operation=sync");
  expect(batchItemsCsvUrl("b1")).toBe("/api/admin/batches/b1/items.csv");
});
