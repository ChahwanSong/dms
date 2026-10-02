import { expect, test } from "vitest";
import { csvCell, toCsv } from "./csvExport";

test("csvCell: 따옴표 규칙(RFC 4180) -- 쉼표·따옴표·줄바꿈만 감싼다", () => {
  expect(csvCell("plain")).toBe("plain");
  expect(csvCell("a,b")).toBe('"a,b"');
  expect(csvCell('say "hi"')).toBe('"say ""hi"""');
  expect(csvCell("line1\nline2")).toBe('"line1\nline2"');
});

test("csvCell: 수식 주입 방어는 문자열에만 -- 숫자(음수 증감 포함)는 그대로", () => {
  expect(csvCell("=HYPERLINK(\"x\")")).toBe("\"'=HYPERLINK(\"\"x\"\")\"");
  expect(csvCell("+cmd")).toBe("'+cmd");
  expect(csvCell("-rf")).toBe("'-rf");
  expect(csvCell("@sum")).toBe("'@sum");
  expect(csvCell(-123)).toBe("-123");
  expect(csvCell(0)).toBe("0");                 // 0 은 정상값
});

test("csvCell: null·undefined·비유한수 = 빈 칸(모름), 불리언은 글자", () => {
  expect(csvCell(null)).toBe("");
  expect(csvCell(undefined)).toBe("");
  expect(csvCell(Number.NaN)).toBe("");
  expect(csvCell(true)).toBe("true");
});

test("toCsv: 헤더 + 열 순서대로, CRLF 줄끝, 없는 키는 빈 칸", () => {
  expect(toCsv(["a", "b"], [{ a: 1, b: "x" }, { a: 2 }])).toBe("a,b\r\n1,x\r\n2,\r\n");
});
