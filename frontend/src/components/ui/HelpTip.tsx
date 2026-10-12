import { useCallback, useEffect, useId, useRef, useState } from "react";
import { CircleHelp } from "lucide-react";
import { HoverTip, type Rect } from "./HoverTip";

// 설명 툴팁(2026-10-12 사용자 요청 -- 작업 단계·상태 설명). HoverTip(포털·fixed -- 표 래퍼·카드에 잘리지 않는다)을 마우스·
// 키보드·터치 모두에서 연다: 마우스 진입·포커스·클릭(탭)에 열고, 이탈·블러·Esc 에 닫는다. 클릭은 **열기만** 한다 -- 토글이면
// 한 번의 클릭·탭이 내는 mouseenter·focus(열기) 직후의 click 이 방금 연 것을 닫아, ? 를 누르면 사라지고 터치 첫 탭엔 안
// 떴다(2026-10-12 검증 지적). 설명은 hidden 노드를 aria-describedby 로 가리킨다 -- 직접 참조된 hidden 노드도 설명으로 계산되므로
// 포커스만으로 읽히고, 표 텍스트(복사·낭독기 읽기 모드)에 섞이거나 가로 스크롤 표 밖으로 새지 않는다(sr-only 는 둘 다 했다).
// 툴팁 본문은 열렸을 때만 그린다(정확 일치 텍스트 단언과 겹치지 않게).

export function useTip<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [anchor, setAnchor] = useState<Rect | null>(null);
  const open = useCallback(() => {
    const r = ref.current?.getBoundingClientRect();
    if (r) setAnchor({ left: r.left, right: r.right, top: r.top, bottom: r.bottom });
  }, []);
  const close = useCallback(() => setAnchor(null), []);
  useEffect(() => {
    if (!anchor) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [anchor, close]);
  const handlers = {
    onMouseEnter: open, onMouseLeave: close, onFocus: open, onBlur: close,
    onClick: open,
  };
  return { ref, anchor, close, handlers };
}

/** 열린 툴팁 본문: 첫 줄은 무엇에 대한 설명인지(한 텍스트 노드), 둘째 줄이 설명. */
export function TipBody({ head, help }: { head: string; help: string }) {
  return (
    <div className="max-w-xs space-y-0.5">
      <p className="font-semibold">{head}</p>
      <p className="text-ink/80 break-keep">{help}</p>
    </div>
  );
}

/** 물음표 아이콘 버튼 + 설명 툴팁 -- 단계 머리·관문 줄처럼 글자 옆에 설명을 달 때. */
export function HelpTip({ label, head, help, className = "" }: {
  label: string; head: string; help: string; className?: string;
}) {
  const id = useId();
  const { ref, anchor, close, handlers } = useTip<HTMLButtonElement>();
  return (
    <>
      <button ref={ref} type="button" aria-label={label} aria-describedby={id} {...handlers}
              className={`inline-grid h-6 w-6 shrink-0 cursor-help place-items-center rounded-full text-ink/60
                          hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${className}`}>
        <CircleHelp aria-hidden className="h-4 w-4" />
      </button>
      <span id={id} hidden>{help}</span>
      {anchor && <HoverTip anchor={anchor} onClose={close}><TipBody head={head} help={help} /></HoverTip>}
    </>
  );
}
