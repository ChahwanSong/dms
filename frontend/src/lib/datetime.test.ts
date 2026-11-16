import { expect, test } from "vitest";
import { kstStamp, kstStampBare, kstStampOrDash, kstStampEpoch, kstDay, untilText } from "./datetime";

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

test("kstStampBare: kstStamp 와 같은 벽시계에서 「 KST」 접미사만 없다(시간대는 표 머리줄이 말한다)", () => {
  for (const iso of ["2026-08-04T01:12:42Z", "2026-08-05T15:00:00Z", "2026-08-05T22:00:00Z"]) {
    expect(kstStampBare(iso)).toBe(kstStamp(iso).replace(/ KST$/, ""));
  }
  expect(kstStampBare("2026-08-05T15:00:00Z")).toBe("2026-08-06 00:00:00");
  expect(kstStampBare("nonsense")).toBe("nonsense");      // 지어내지 않는다
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

test("untilText: 미래까지 남은 시간(내림) — 곧 / N분 후 / N시간 후 / N일 후", () => {
  const now = Date.parse("2026-11-16T00:00:00Z");
  const at = (sec: number) => new Date(now + sec * 1000).toISOString();
  expect(untilText(at(30), now)).toBe("곧");                   // 1분 미만
  expect(untilText(at(60), now)).toBe("1분 후");
  expect(untilText(at(59 * 60 + 59), now)).toBe("59분 후");    // 내림
  expect(untilText(at(3600), now)).toBe("1시간 후");
  expect(untilText(at(2 * 3600 - 1), now)).toBe("1시간 후");   // 1시간 59분 59초 → 내림
  expect(untilText(at(23 * 3600 + 59 * 60), now)).toBe("23시간 후");
  expect(untilText(at(24 * 3600), now)).toBe("1일 후");
  expect(untilText(at(1500 * 86400), now)).toBe("1,500일 후");
});

test("untilText: 이미 지난 시각(틱 대기)은 「곧」, 모르는 값은 — (지어내지 않는다)", () => {
  const now = Date.parse("2026-11-16T00:00:00Z");
  expect(untilText("2026-11-15T23:00:00Z", now)).toBe("곧");
  expect(untilText(null, now)).toBe("—");
  expect(untilText(undefined, now)).toBe("—");
  expect(untilText("", now)).toBe("—");
  expect(untilText("nonsense", now)).toBe("—");
});
