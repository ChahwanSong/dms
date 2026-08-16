import { describe, it, expect } from "vitest";
import { parseTs, spanMs, formatDuration } from "./duration";

describe("parseTs", () => {
  it("백엔드 타임스탬프(초 단위 UTC)를 읽는다", () => {
    expect(parseTs("2026-08-06T00:00:00Z")).toBe(Date.UTC(2026, 7, 6, 0, 0, 0));
  });
  it("모름(null/undefined)과 쓰레기 문자열은 null 이다 — 0 으로 뭉개지 않는다", () => {
    expect(parseTs(null)).toBeNull();
    expect(parseTs(undefined)).toBeNull();
    expect(parseTs("어제")).toBeNull();
  });
  it("epoch ms 숫자는 그대로 통과시킨다(now 를 그대로 넘길 수 있게)", () => {
    expect(parseTs(1_700_000_000_000)).toBe(1_700_000_000_000);
  });
});

describe("spanMs", () => {
  it("두 시각의 간격을 ms 로 준다", () => {
    expect(spanMs("2026-08-06T00:00:00Z", "2026-08-06T00:10:00Z")).toBe(600_000);
  });
  it("한쪽이라도 모르면 null 이다(지어내지 않는다)", () => {
    expect(spanMs(null, "2026-08-06T00:10:00Z")).toBeNull();
    expect(spanMs("2026-08-06T00:00:00Z", null)).toBeNull();
  });
  it("음수(시계 어긋남)는 null 이다 — '-3초 경과'를 보여주지 않는다", () => {
    expect(spanMs("2026-08-06T00:10:00Z", "2026-08-06T00:00:00Z")).toBeNull();
  });
  it("0 은 정상값이라 null 이 아니다", () => {
    expect(spanMs("2026-08-06T00:00:00Z", "2026-08-06T00:00:00Z")).toBe(0);
  });
});

describe("formatDuration", () => {
  it("1분 미만은 초만", () => expect(formatDuration(45_000)).toBe("45초"));
  it("0 은 '0초'다(모름이 아니다)", () => expect(formatDuration(0)).toBe("0초"));
  it("1시간 미만은 분·초", () => expect(formatDuration(192_000)).toBe("3분 12초"));
  it("1일 미만은 시간·분", () => expect(formatDuration(3_900_000)).toBe("1시간 5분"));
  it("하루를 넘기면 일·시간", () => expect(formatDuration(90_000_000)).toBe("1일 1시간"));
});
