export const TERMINAL_STATES = new Set([
  "Succeeded", "Failed", "Rejected", "Cancelled", "PreviewExpired",
]);
export const isTerminal = (s: string) => TERMINAL_STATES.has(s);

export type PillVariant = "ok" | "bad" | "busy" | "neutral";
export function pillVariant(state: string): PillVariant {
  if (state === "Succeeded") return "ok";
  if (["Failed", "Rejected", "Cancelled", "PreviewExpired"].includes(state)) return "bad";
  if (["Executing", "ConfirmPending", "Planning", "Scheduled"].includes(state)) return "busy";
  return "neutral";
}
