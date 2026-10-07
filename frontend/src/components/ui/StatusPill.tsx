import { pillVariant } from "../../lib/jobState";
import type { PillVariant } from "../../lib/jobState";

const CLS = {
  ok: "text-ok bg-okbg", bad: "text-bad bg-badbg",
  busy: "text-busy bg-busybg", neutral: "text-muted bg-canvas",
  // 운영자 행동이 필요한 상태(배치 「확인 대기」) -- busy 의 연파랑·primary 버튼의 accent 와 모두 구별되는 주의색
  // (accent 실색이면 버튼처럼 보였다 -- tailwind.config 확정값 ⑤).
  action: "text-attn bg-attnbg",
} as const;

// variant를 명시하면(예: 빌드 화면의 buildPillVariant) 잡/요청 공용 pillVariant
// 대신 그걸 쓴다 -- 공유 매핑 자체를 고치면 다른 도메인의 배지까지 바뀌기 때문(M5).
// label 을 주면 그 문자열을 보이고 서버 상태 원문은 title 로 남긴다(예: 배치 PreviewReady → 「확인 대기」).
export function StatusPill({ state, variant, label }: { state: string; variant?: PillVariant; label?: string }) {
  const text = label ?? state;
  return (
    <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ${CLS[variant ?? pillVariant(state)]}`}
          title={text !== state ? state : undefined}>
      {text}
    </span>
  );
}
