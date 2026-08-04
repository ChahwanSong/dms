import { pillVariant } from "../../lib/jobState";

const CLS = {
  ok: "text-ok bg-okbg", bad: "text-bad bg-badbg",
  busy: "text-busy bg-busybg", neutral: "text-muted bg-canvas",
} as const;

export function StatusPill({ state }: { state: string }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ${CLS[pillVariant(state)]}`}>
      {state}
    </span>
  );
}
