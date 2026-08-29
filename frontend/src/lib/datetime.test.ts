import { expect, test } from "vitest";
import { kstStamp, kstStampOrDash, kstStampEpoch, kstDay } from "./datetime";

test("kstStamp: UTC ISO -> KST 벽시계(+9h) + KST 라벨", () => {
  expect(kstStamp("2026-08-04T01:12:42Z")).toBe("2026-08-04 10:12:42 KST");
  expect(kstStamp("2026-08-15T03:04:05Z")).toBe("2026-08-15 12:04:05 KST");
});

test("kstStamp: UTC 15:00 이후는 KST 다음날로 날짜 경계가 넘어간다", () => {
  expect(kstStamp("2026-08-05T22:00:00Z")).toBe("2026-08-06 07:00:00 KST");
  expect(kstStamp("2026-08-05T15:00:00Z")).toBe("2026-08-06 00:00:00 KST");
});

test("kstStamp: 파싱 불가면 원문 그대로(지어내지 않는다)", () => {
  expect(kstStamp("nonsense")).toBe("nonsense");
});

test("kstStampOrDash: null/undefined/빈 문자열은 —", () => {
  expect(kstStampOrDash(null)).toBe("—");
  expect(kstStampOrDash(undefined)).toBe("—");
  expect(kstStampOrDash("")).toBe("—");
  expect(kstStampOrDash("2026-08-04T01:12:42Z")).toBe("2026-08-04 10:12:42 KST");
});

test("kstStampEpoch: epoch(초) -> KST (utcStamp 국소 사본 대체)", () => {
  // 1785805962 = 2026-08-04T01:12:42Z (구 utcStamp 픽스처) -> +9h
  expect(kstStampEpoch(1785805962)).toBe("2026-08-04 10:12:42 KST");
});

test("kstDay: epoch -> KST MM-DD (날짜 경계 반영)", () => {
  expect(kstDay(Date.parse("2026-08-04T01:12:42Z") / 1000)).toBe("08-04");
  expect(kstDay(Date.parse("2026-08-05T22:00:00Z") / 1000)).toBe("08-06");
});
