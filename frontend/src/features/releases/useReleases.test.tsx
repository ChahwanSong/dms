import { describe, it, expect } from "vitest";
import { RELEASE_ACTIVE_STATES, RELEASE_POLL_MS, releasePillVariant,
         releaseRefetchInterval } from "./useReleases";
import type { Releases } from "../../lib/types";

const row = (state: string) => ({
  id: 1, component: "dms-api", image: "i", tag: "t", digest: null,
  state, reason_code: null, actor: "ops", applied_at: "2026-08-06T00:00:00Z",
});

const data = (...states: string[]): Releases =>
  ({ current: {}, history: states.map(row) });

describe("releaseRefetchInterval", () => {
  // 설계 §8: 진행 중이면 폴링, 종단이면 정지. 두 방향을 다 고정하지 않으면
  // "폴링이 아예 시작되지 않는" 회귀도 "영원히 멈추지 않는" 회귀도 통과한다.
  it("진행 중인 행이 있으면 폴링 간격을 준다", () => {
    expect(releaseRefetchInterval(data("Applied", "Applying"))).toBe(RELEASE_POLL_MS);
    expect(releaseRefetchInterval(data("Pending"))).toBe(RELEASE_POLL_MS);
    expect(RELEASE_POLL_MS).toBeGreaterThan(0);
  });

  it("전부 종단이면 폴링을 멈춘다", () => {
    expect(releaseRefetchInterval(data("Applied", "Failed"))).toBe(false);
  });

  it("아직 데이터가 없거나 배열이 아니면 폴링하지 않는다", () => {
    expect(releaseRefetchInterval(undefined)).toBe(false);
    expect(releaseRefetchInterval({ current: {}, history: null } as unknown as Releases))
      .toBe(false);
  });
});

describe("RELEASE_ACTIVE_STATES", () => {
  // jobState.ts의 isTerminal은 Applied를 모른다 -- 그걸 쓰면 Applied가 비종단으로
  // 읽혀 폴링이 영원히 안 멈춘다. 릴리스 전용 집합의 경계를 못박는다.
  it("Pending/Applying만 비종단이다", () => {
    expect([...RELEASE_ACTIVE_STATES].sort()).toEqual(["Applying", "Pending"]);
    expect(RELEASE_ACTIVE_STATES.has("Applied")).toBe(false);
    expect(RELEASE_ACTIVE_STATES.has("Failed")).toBe(false);
  });

  it("배지 색은 상태별로 갈린다", () => {
    expect(releasePillVariant("Applied")).toBe("ok");
    expect(releasePillVariant("Failed")).toBe("bad");
    expect(releasePillVariant("Applying")).toBe("busy");
    expect(releasePillVariant("Pending")).toBe("busy");
  });
});
