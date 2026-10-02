import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

// 즉시 툴팁(2026-10-02 사용자 요청 "막대에 마우스 올리면 바로 내용 뜨도록 -- 지금은 몇 초 기다려야 뜬다").
// 브라우저 기본 title 툴팁은 표시 지연(~1초 이상)이 있고 모양을 못 꾸민다 -- 마우스 진입 즉시 이 컴포넌트를 그린다.
//
// - document.body 포털 + position:fixed: 차트가 표 래퍼(overflow-x-auto)·카드 안에 있어도 잘리지 않는다.
// - 자리는 앵커(막대·열)의 화면 사각형 기준: place 함수가 정한다(기본 placeAbove = 위, 공간 없으면 아래).
//   첫 렌더는 크기를 재려고 숨겨 두고(visibility hidden) 레이아웃 직후 자리를 잡는다 -- 같은 프레임이라 지연이 없다.
// - 스크롤하면 고정 좌표가 앵커에서 떨어지므로 onClose 를 부른다(다시 올리면 새 자리).
// - pointer-events-none: 툴팁이 앵커의 mouseleave 를 가로채 깜빡이지 않게.

export interface Rect { left: number; right: number; top: number; bottom: number }
export type Place = (anchor: Rect, w: number, h: number, vw: number, vh: number) => { left: number; top: number };

const EDGE = 8;

// 앵커 위 가운데(가로는 화면 안으로 클램프). 위 공간이 모자라면 앵커 아래.
export const placeAbove: Place = (a, w, h, vw) => {
  const left = Math.max(EDGE, Math.min((a.left + a.right) / 2 - w / 2, vw - w - EDGE));
  const top = a.top - h - 6;
  return { left, top: top >= EDGE ? top : a.bottom + 6 };
};

// 앵커 옆(오른쪽 우선, 안 되면 왼쪽, 둘 다 안 되면 아래) -- 키가 큰 툴팁(사용량 분석 온도 범례)용.
export const placeBeside: Place = (a, w, h, vw, vh) => {
  const gap = 8;
  const clampTop = (t: number) => Math.max(EDGE, Math.min(t, vh - h - EDGE));
  if (a.right + gap + w <= vw - EDGE) return { left: a.right + gap, top: clampTop(a.top) };
  if (a.left - gap - w >= EDGE) return { left: a.left - gap - w, top: clampTop(a.top) };
  // 좁은 화면: 아래, 아래가 모자라면 위, 둘 다 모자라면 화면 안으로 클램프(리뷰: 375x667 에서 범례 아래쪽이
  // 화면 밖에 그려졌고, 스크롤하면 닫혀서 끝내 볼 수 없었다).
  const left = Math.max(EDGE, Math.min(a.left, vw - w - EDGE));
  const below = a.bottom + 4;
  if (below + h <= vh - EDGE) return { left, top: below };
  const above = a.top - 4 - h;
  if (above >= EDGE) return { left, top: above };
  return { left, top: clampTop(below) };
};

export function HoverTip({ anchor, onClose, place = placeAbove, className = "", children }: {
  anchor: Rect; onClose: () => void; place?: Place; className?: string; children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  useLayoutEffect(() => {
    const r = ref.current?.getBoundingClientRect();
    setPos(place(anchor, r?.width ?? 0, r?.height ?? 0, window.innerWidth, window.innerHeight));
  }, [anchor, place]);
  // 스크롤 닫기는 **다음 프레임부터** 건다 -- 키보드로 화면 밖 열에 포커스하면 브라우저가 scroll-into-view 를
  // 포커스 직후에 하는데, 그 스크롤에 툴팁이 열리자마자 닫혔다(리뷰).
  useEffect(() => {
    const id = requestAnimationFrame(() => window.addEventListener("scroll", onClose, true));
    return () => {
      cancelAnimationFrame(id);
      window.removeEventListener("scroll", onClose, true);
    };
  }, [onClose]);
  return createPortal(
    <div ref={ref} role="tooltip"
         className={`pointer-events-none fixed z-50 max-w-[calc(100vw-1rem)] rounded-lg border border-line
                     bg-surface px-2.5 py-1.5 text-xs text-ink shadow-lg ${className}`}
         style={{ left: pos?.left ?? 0, top: pos?.top ?? 0, visibility: pos ? "visible" : "hidden" }}>
      {children}
    </div>,
    document.body);
}

// 앵커 상태 훅: 마우스 진입·포커스 = 즉시 열기(그 요소의 화면 사각형), 이탈·블러 = 닫기(다른 앵커로 이미 옮겼으면 무시).
export function useHoverAnchor<K>() {
  const [active, setActive] = useState<{ key: K; rect: Rect } | null>(null);
  const [close] = useState(() => () => setActive(null));
  const bind = (key: K) => ({
    onMouseEnter: (e: React.SyntheticEvent<HTMLElement>) =>
      setActive({ key, rect: e.currentTarget.getBoundingClientRect() }),
    onFocus: (e: React.SyntheticEvent<HTMLElement>) =>
      setActive({ key, rect: e.currentTarget.getBoundingClientRect() }),
    onMouseLeave: () => setActive((a) => (a?.key === key ? null : a)),
    onBlur: () => setActive((a) => (a?.key === key ? null : a)),
  });
  return { active, bind, close };
}
