// 요청 상세의 작은 상호작용 요소 클래스(2026-10-08 재설계). 공용 Button 은 다른 화면에 영향이 있어 이번에 건드리지
// 않는다(focus-visible 링이 없다 -- BACKLOG 후보). 이 화면에서 새로 만든 칩·아이콘 버튼·소형 버튼만 링을 단다.
//
// bg-surface 를 쓰지 않는다: RequestDetail.test 가 job_id 에서 closest(".bg-surface") 로 잡 Card 를 찾는다 -- 칩·
// 버튼은 job_id 의 조상이 아니라 무해하지만, 이 화면 요소의 배경은 투명(Card 흰색이 비친다)으로 통일해 둔다.
export const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1";

// 소형 고스트 버튼(「다시 시도」·「로그 저장」·「모두 표시」). 모바일 h-8(32px) -- WCAG 2.2 2.5.8(24px) 이상.
export const SMALL_BTN =
  `inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs text-ink hover:bg-panel disabled:opacity-50 sm:h-7 ${FOCUS_RING}`;

// 아이콘만 있는 버튼(「줄바꿈」·「출력 닫기」). aria-label 과 title 에 같은 문구를 준다.
export const ICON_BTN =
  `inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-ink/70 hover:bg-panel hover:text-ink aria-pressed:bg-infobg aria-pressed:text-accent ${FOCUS_RING}`;

// 「새 작업 제출」처럼 버튼 모양의 링크(공용 Button ghost 와 같은 모양 + 포커스 링).
export const LINK_BTN =
  `inline-flex items-center justify-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-sm font-medium text-ink hover:bg-panel ${FOCUS_RING}`;

// 작은 테두리 배지(「저장된 사본」·「실시간」·「launcher」·「뒷부분만 표시」).
export const BADGE =
  "inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-[11px] text-ink/70";
