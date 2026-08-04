import { isTerminal, pillVariant, TERMINAL_STATES } from "./jobState";
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
