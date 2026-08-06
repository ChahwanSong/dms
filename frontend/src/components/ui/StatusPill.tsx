import { pillVariant } from "../../lib/jobState";
import type { PillVariant } from "../../lib/jobState";

const CLS = {
  ok: "text-ok bg-okbg", bad: "text-bad bg-badbg",
  busy: "text-busy bg-busybg", neutral: "text-muted bg-canvas",
} as const;

// variant를 명시하면(예: 빌드 화면의 buildPillVariant) 잡/요청 공용 pillVariant
// 대신 그걸 쓴다 -- 공유 매핑 자체를 고치면 다른 도메인의 배지까지 바뀌기 때문(M5).
export function StatusPill({ state, variant }: { state: string; variant?: PillVariant }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ${CLS[variant ?? pillVariant(state)]}`}>
      {state}
    </span>
  );
}
