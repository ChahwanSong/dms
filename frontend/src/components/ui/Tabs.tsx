import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";

// 공용 탭(2026-10-20, 사용량 분석 스토리지 탭에서 시작). WAI-ARIA tablist 를 **수동 활성**으로 둔다: ←/→(양 끝에서 순환)·
// Home/End 는 포커스만 옮기고 Enter/Space(버튼의 기본 클릭)가 고른다. 탭을 고를 때마다 서버 요청이 나가는 화면(사용량 분석은
// 지표 정렬이면 스토리지별 창을 새로 만든다)에서 화살표 한 번마다 요청을 쏘지 않기 위해서다 -- APG 도 패널 표시에 지연이 있으면
// 수동 활성을 권한다. tabIndex 는 roving: 선택 탭 하나만 0 이라 Tab 키는 탭 줄을 한 번에 지나간다(값이 아직 목록에 없으면
// 첫 탭이 받는다 -- 탭 줄이 키보드로 닿지 않는 일이 없게).
//
// 375 폭: 줄바꿈 없이 가로 스크롤한다(모바일 전용 select 를 따로 두지 않는다 -- 두 벌의 DOM 은 jsdom 에서 둘 다 잡히고, 탭의
// 건수·표시도 select 엔 못 싣는다). 고른 탭이 화면 밖이면 **가로로만** 끌어온다: scrollIntoView 는 세로로도 움직여, 모바일에서
// 사이드바 아래(첫 화면 밖)에 있는 탭 줄로 페이지 전체를 내려 버린다. 줄의 바닥선은 inset 그림자다 -- 스크롤 상자의 테두리에
// 탭 밑줄을 -mb-px 로 겹치면 상자가 그 1px 를 잘라 세로 스크롤이 생긴다.
//
// 탭 안의 sr-only 노드(건수 낭독 등)는 relative 탭에 가둔다: 가로 스크롤 줄의 화면 밖 sr-only 가 문서 폭을 넓힌 실사고가 있다
// (StatusPill 의 e2e L1 실측 619/375).

export interface TabItem {
  id: string;
  content: ReactNode;
  // 이 탭 앞에 얇은 세로 구분선(묶음 경계 -- 사용량 분석의 등록 | 미등록).
  separatorBefore?: boolean;
}

export function Tabs({ label, items, value, onSelect, idPrefix, children, panelClassName = "" }: {
  label: string; items: TabItem[]; value: string | null; onSelect: (id: string) => void;
  idPrefix: string; children: ReactNode; panelClassName?: string;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const selected = items.findIndex((t) => t.id === value);
  const tabbable = selected >= 0 ? selected : 0;
  const tabId = (i: number) => `${idPrefix}-tab-${i}`;
  const panelId = `${idPrefix}-panel`;

  useEffect(() => {
    const list = listRef.current, tab = tabRefs.current[selected];
    if (!list || !tab) return;
    const left = tab.offsetLeft, right = left + tab.offsetWidth;
    if (left < list.scrollLeft) list.scrollLeft = left;
    else if (right > list.scrollLeft + list.clientWidth) list.scrollLeft = right - list.clientWidth;
  }, [selected, items.length]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const n = items.length;
    const at = tabRefs.current.findIndex((el) => el === document.activeElement);
    if (n === 0 || at < 0) return;
    const next = e.key === "ArrowRight" ? (at + 1) % n
      : e.key === "ArrowLeft" ? (at - 1 + n) % n
      : e.key === "Home" ? 0
      : e.key === "End" ? n - 1
      : null;
    if (next === null) return;
    e.preventDefault();
    tabRefs.current[next]?.focus();
  };

  return (
    <>
      <div ref={listRef} role="tablist" aria-label={label} onKeyDown={onKeyDown}
           className="relative flex items-stretch overflow-x-auto whitespace-nowrap
                      shadow-[inset_0_-1px_0_theme(colors.line)] [scrollbar-width:thin]">
        {items.map((t, i) => {
          const on = i === selected;
          return [
            t.separatorBefore && (
              <span key={`${t.id}-sep`} aria-hidden className="mx-2 h-4 w-px shrink-0 self-center bg-line" />
            ),
            <button key={t.id} ref={(el) => { tabRefs.current[i] = el; }}
                    type="button" role="tab" id={tabId(i)}
                    aria-selected={on} aria-controls={panelId} tabIndex={i === tabbable ? 0 : -1}
                    onClick={() => onSelect(t.id)}
                    className={`relative inline-flex shrink-0 items-center gap-1.5 rounded-t-md border-b-2 px-3 py-2
                                text-sm leading-6 transition-colors focus-visible:outline-none focus-visible:ring-2
                                focus-visible:ring-inset focus-visible:ring-accent ${
                      on ? "border-accent font-medium text-accent"
                         : "border-transparent text-muted hover:border-line hover:text-ink"}`}>
              {t.content}
            </button>,
          ];
        })}
      </div>
      <div role="tabpanel" id={panelId} className={panelClassName}
           {...(selected >= 0 ? { "aria-labelledby": tabId(selected) } : { "aria-label": label })}>
        {children}
      </div>
    </>
  );
}
