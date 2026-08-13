import { buildPillVariant, isTerminal, pillVariant, storagePillVariant,
         REQUEST_TERMINAL_STATES, TERMINAL_STATES } from "./jobState";
import { test, expect } from "vitest";

test("terminal states", () => {
  ["Succeeded", "Failed", "Rejected", "Cancelled", "PreviewExpired"]
    .forEach((s) => expect(isTerminal(s)).toBe(true));
  expect(isTerminal("Executing")).toBe(false);
  expect(TERMINAL_STATES.has("Succeeded")).toBe(true);
  // 요청 전용 셋: Conflict 는 요청에만 있는 종단이다(domain.TERMINAL_REQUEST_STATES).
  // 공유 TERMINAL_STATES 에 넣으면 잡 화면의 종단 판정까지 바뀌므로 분리(M5 관례).
  expect(REQUEST_TERMINAL_STATES.has("Conflict")).toBe(true);
  expect(REQUEST_TERMINAL_STATES.has("Succeeded")).toBe(true);
  expect(TERMINAL_STATES.has("Conflict")).toBe(false);  // 잡 셋은 불변(회귀 못)
});

test("pill variant mapping: green=ok, red=bad, violet=busy", () => {
  expect(pillVariant("Succeeded")).toBe("ok");
  expect(pillVariant("Failed")).toBe("bad");
  expect(pillVariant("Rejected")).toBe("bad");
  expect(pillVariant("Cancelled")).toBe("bad");
  expect(pillVariant("Executing")).toBe("busy");
  expect(pillVariant("ConfirmPending")).toBe("busy");
  expect(pillVariant("Pending")).toBe("neutral");
});

test("storagePillVariant: Ready=ok, Degraded=busy(주의), 그 외(Unknown 포함)=neutral", () => {
  expect(storagePillVariant("Ready")).toBe("ok");
  // Degraded 는 bad(적색)가 아니라 busy(황색 주의)다 -- planner 는 Degraded
  // 스토리지에도 잡을 보내므로(planner.py:149) "죽음"으로 칠하면 거짓말이 된다.
  expect(storagePillVariant("Degraded")).toBe("busy");
  expect(storagePillVariant("Unknown")).toBe("neutral");
  expect(storagePillVariant("NotAStatus")).toBe("neutral");
  // 공유 pillVariant 에 Ready 를 추가하는 잘못을 막는 못 -- 잡/요청 배지는 불변이다.
  expect(pillVariant("Ready")).toBe("neutral");
});

test("M5: buildPillVariant marks Pending/Running as busy without touching job pillVariant", () => {
  // 빌드 상태(Pending/Running/Succeeded/Failed)만 다루는 별도 함수다 -- 공유
  // pillVariant를 바꾸면 요청/잡 화면의 Pending/Running(위 테스트가 고정한 neutral)까지
  // 같이 바뀌므로 건드리지 않는다.
  expect(buildPillVariant("Pending")).toBe("busy");
  expect(buildPillVariant("Running")).toBe("busy");
  expect(buildPillVariant("Succeeded")).toBe("ok");
  expect(buildPillVariant("Failed")).toBe("bad");
  // 잡/요청 상태의 pillVariant는 그대로다(회귀 없음).
  expect(pillVariant("Pending")).toBe("neutral");
  expect(pillVariant("Running")).toBe("neutral");
});

test("잔여 상태 매핑(슬라이스 1~4 부채): PreviewExpired=bad, Planning/Scheduled=busy", () => {
  // PreviewExpired 는 isTerminal 로만 단언돼 있었고 배지색은 무그물이었다.
  // Planning/Scheduled 는 jobState.ts:10 이 다루는데 단언이 전무했다.
  expect(pillVariant("PreviewExpired")).toBe("bad");
  expect(pillVariant("Planning")).toBe("busy");
  expect(pillVariant("Scheduled")).toBe("busy");
});
