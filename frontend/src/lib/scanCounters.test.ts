import { expect, test } from "vitest";
import { exactBytes, invalidNamesText, scanCount, SUMMARY_KEYS_SHOWN_ELSEWHERE } from "./scanCounters";

test("scanCount: 안전 정수 ≥0 만 값, 나머지(부재·null·음수·실수·문자열·bool·NaN)는 모름(null) -- 0 은 값이다", () => {
  expect(scanCount(0)).toBe(0);
  expect(scanCount(15)).toBe(15);
  for (const bad of [undefined, null, -1, 1.5, "15", true, Number.NaN, Number.POSITIVE_INFINITY, 2 ** 53]) {
    expect(scanCount(bad)).toBeNull();
  }
});

test("invalidNamesText: 「잘못된 인코딩 이름 N개」(천 단위), 모르면 「기록 없음(구형 리포트)」", () => {
  expect(invalidNamesText(15)).toBe("잘못된 인코딩 이름 15개");
  expect(invalidNamesText(0)).toBe("잘못된 인코딩 이름 0개");
  expect(invalidNamesText(12345)).toBe("잘못된 인코딩 이름 12,345개");
  expect(invalidNamesText(undefined)).toBe("잘못된 인코딩 이름: 기록 없음(구형 리포트)");
  expect(invalidNamesText(null)).toBe("잘못된 인코딩 이름: 기록 없음(구형 리포트)");
});

test("exactBytes: 정확한 바이트 표기 · 화면이 따로 말하는 summary 키", () => {
  expect(exactBytes(1074750158)).toBe("1,074,750,158 B");
  expect(exactBytes(0)).toBe("0 B");
  expect(SUMMARY_KEYS_SHOWN_ELSEWHERE).toEqual(["total_bytes", "invalid_name_entries"]);
});
