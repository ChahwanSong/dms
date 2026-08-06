import { buildPillVariant, isTerminal, pillVariant, TERMINAL_STATES } from "./jobState";
import { test, expect } from "vitest";

test("terminal states", () => {
  ["Succeeded", "Failed", "Rejected", "Cancelled", "PreviewExpired"]
    .forEach((s) => expect(isTerminal(s)).toBe(true));
  expect(isTerminal("Executing")).toBe(false);
  expect(TERMINAL_STATES.has("Succeeded")).toBe(true);
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
