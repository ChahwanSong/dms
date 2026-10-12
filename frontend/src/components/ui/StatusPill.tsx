import { useId } from "react";
import { pillVariant } from "../../lib/jobState";
import type { PillVariant } from "../../lib/jobState";
import { HoverTip } from "./HoverTip";
import { TipBody, useTip } from "./HelpTip";

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
// help(2026-10-12 단계·상태 설명)를 주면 배지가 설명 툴팁의 앵커가 된다 -- 마우스·포커스(Tab)·클릭으로 열고 Esc 로 닫는다.
// 버튼이 아니라 포커스 가능한 span 이다(표 칸의 버튼 줄바꿈 불변식 L4·버튼 안의 배지와 충돌하지 않게). 설명은 호출처가 도메인
// 함수(lib/stateHelp)로 골라 넘긴다 -- 상태 문자열로 자동 조회하면 빌드의 Pending 에 요청 설명이 뜬다. 버튼 안의 배지에는 넘기지
// 마라(중첩 포커스). help 가 있으면 title 대신 툴팁 첫 줄이 상태 코드를 말한다(브라우저 툴팁과 겹쳐 뜨지 않게).
export function StatusPill({ state, variant, label, help }: {
  state: string; variant?: PillVariant; label?: string; help?: string | null;
}) {
  const text = label ?? state;
  const cls = `inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ${CLS[variant ?? pillVariant(state)]}`;
  if (!help) {
    return <span className={cls} title={text !== state ? state : undefined}>{text}</span>;
  }
  return <HelpPill cls={cls} text={text} state={state} help={help} />;
}

function HelpPill({ cls, text, state, help }: { cls: string; text: string; state: string; help: string }) {
  const id = useId();
  const { ref, anchor, close, handlers } = useTip<HTMLSpanElement>();
  // 설명 노드는 hidden(HelpTip 머리 주석) -- sr-only 였을 땐 가로 스크롤 표(375)의 화면 밖 칸에서 문서 전체를 넓혔다(e2e L1
  // 실측 619/375)·표 텍스트에 섞였다.
  return (
    <>
      <span ref={ref} tabIndex={0} aria-describedby={id} {...handlers}
            className={`${cls} cursor-help whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent`}>
        {text}
      </span>
      <span id={id} hidden>{help}</span>
      {/* 첫 줄은 한 텍스트 노드(「상태 코드 X」) -- 정확 일치 getByText("Succeeded") 개수(e2e E6)에 끼지 않는다. */}
      {anchor && <HoverTip anchor={anchor} onClose={close}><TipBody head={`상태 코드 ${state}`} help={help} /></HoverTip>}
    </>
  );
}
