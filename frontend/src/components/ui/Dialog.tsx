import { useRef } from "react";
import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
// size: 기본 md(폼 다이얼로그). 표·요약이 여럿인 상세 창(노드 상세 등)은 xl 로 넓힌다 --
// md 폭에선 표가 가로 스크롤로 잘린다(2026-09-30 노드 상세 모달화).
const SIZES = { md: "max-w-md", lg: "max-w-2xl", xl: "max-w-5xl" } as const;
// onCloseAutoFocus(선택): 닫힐 때 Radix 는 트리거로 포커스를 돌려준다 -- 닫히는 동작이 트리거를 disabled 로 만들면
// (작업 삭제: 성공하면 선택이 비어 「선택 삭제」가 잠긴다) 포커스가 <body> 로 떨어진다. 그런 창은 e.preventDefault() 후
// 늘 있는 다른 자리로 직접 옮긴다(2026-10-09).
// busy(선택): 창이 서버 응답을 기다리는 동안 aria-busy -- 진행 중 Esc·X 를 무시하는 창(작업 삭제)이 보조기기에
// 「반응 없이 안 닫히는 창」으로만 보이지 않게(2026-10-09 검증 지적). 진행 문구(role=status)는 창 내용이 직접 그린다.
//
// side="right"(2026-10-29 사용량 분석 경로 상세 시트): 같은 **모달**(덮개·포커스 가둠·Esc·바깥 클릭 닫기·뒤 화면 스크롤 잠금은
// Radix 그대로)의 측면 모양. md 이상은 오른쪽 고정 640px(최대 화면 − 32px), 그 아래는 전체 화면, 높이 100dvh. 머리(제목 +
// subtitle + actions + 닫기)와 banner 는 고정이고 본문(children)만 스크롤한다(data-sheet-body -- 내용을 바꾸는 호출측이 맨 위로
// 되돌릴 때 찾는 표지). 열면 포커스는 제목(h2, tabIndex -1)으로 간다 -- 첫 버튼(이전·다음)에 두면 Enter 한 번에 내용이 바뀐다.
// 닫기 버튼은 머리 안 제자리(DOM 순서 = 보이는 순서)이고 이름은 「닫기」다(가운데 창의 「창 닫기」는 폼 하단 「닫기」와 겹치지
// 않게 지은 이름 -- 시트엔 폼 버튼이 없다). 트리거 없이 open 으로만 여닫는 창은 trigger 를 생략한다(URL 이 여는 시트).
// 열 때 오른쪽에서 150ms 미끄러진다(motion-safe -- 움직임 줄이기면 그 자리에 바로 선다). 닫을 때는 움직임 없이 바로 사라진다.
export function Dialog({
  trigger, title, children, open, onOpenChange, size = "md", onCloseAutoFocus, busy,
  side, titleClassName = "", subtitle, actions, banner,
}: {
  trigger?: React.ReactNode; title: string; children: React.ReactNode;
  open?: boolean; onOpenChange?: (o: boolean) => void; size?: keyof typeof SIZES;
  onCloseAutoFocus?: (e: Event) => void; busy?: boolean;
  side?: "right"; titleClassName?: string; subtitle?: React.ReactNode; actions?: React.ReactNode; banner?: React.ReactNode;
}) {
  const titleRef = useRef<HTMLHeadingElement>(null);
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      {trigger !== undefined && <D.Trigger asChild>{trigger}</D.Trigger>}
      <D.Portal>
        <D.Overlay className="fixed inset-0 bg-black/30" />
        {side === "right" ? (
          <D.Content aria-describedby={undefined} onCloseAutoFocus={onCloseAutoFocus} aria-busy={busy || undefined}
                     onOpenAutoFocus={(e) => { e.preventDefault(); titleRef.current?.focus({ preventScroll: true }); }}
                     className="fixed inset-y-0 right-0 flex h-[100dvh] w-full flex-col bg-surface shadow-soft
                                md:w-[640px] md:max-w-[calc(100vw-2rem)] md:border-l md:border-line
                                motion-safe:animate-sheet-in">
            <div className="flex items-start gap-3 border-b border-line px-4 py-3 md:px-5">
              <div className="min-w-0 flex-1 space-y-0.5">
                <D.Title ref={titleRef} tabIndex={-1}
                         className={`text-base font-semibold text-ink [overflow-wrap:anywhere] focus:outline-none ${titleClassName}`}>
                  {title}
                </D.Title>
                {subtitle && <p className="text-xs text-muted [overflow-wrap:anywhere]">{subtitle}</p>}
              </div>
              {/* 닫기는 호출측 버튼 묶음(이전·다음)과 띄운다(2026-10-29 2차 리뷰, 375 실측): 36px 버튼이 4px 간격이면 엄지로 [다음]을
                  누르려다 [닫기]를 눌러 시트가 닫히고 히스토리가 뒤로 갔다. 묶음 안은 그대로 붙여 둔다(gap-1). */}
              <div className="flex shrink-0 items-center gap-3">
                {actions !== undefined && <div className="flex items-center gap-1">{actions}</div>}
                <D.Close asChild>
                  <button type="button" aria-label="닫기" className={SHEET_ICON_BUTTON}>
                    <X className="h-4 w-4" aria-hidden />
                  </button>
                </D.Close>
              </div>
            </div>
            {banner}
            <div data-sheet-body className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-8 pt-4 md:px-5">
              {children}
            </div>
          </D.Content>
        ) : (
          // max-h+overflow: 필드가 많은 다이얼로그(정책 9필드)가 낮은 화면에서
          // 제목·저장 버튼째 뷰포트 밖으로 잘리던 결함 — 넘치면 내부 스크롤로
          <D.Content aria-describedby={undefined} onCloseAutoFocus={onCloseAutoFocus} aria-busy={busy || undefined}
                     className={`fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-surface rounded-card shadow-soft p-5 w-[calc(100%-2rem)] ${SIZES[size]} max-h-[85vh] overflow-y-auto`}>
            <D.Title className="text-base font-semibold mb-3 pr-8">{title}</D.Title>
            {children}
            {/* 창 닫기(X, 2026-09-30): 넓은 상세 창(노드 상세)은 Esc·바깥 클릭만으로 닫는다는 걸
                알기 어렵다. **DOM 순서상 맨 끝**(화면은 우상단 absolute) -- Radix 는 열릴 때 첫 포커스
                대상에 포커스하므로, 앞에 두면 폼 다이얼로그의 첫 포커스가 입력칸이 아니라 X 가 되어
                Enter 한 번에 창이 닫힌다(리뷰). 접근성 이름은 "창 닫기" -- 폼 하단 "닫기" 와 겹치지 않게. */}
            <D.Close asChild>
              <button type="button" aria-label="창 닫기"
                      className="absolute right-3 top-3 rounded-lg p-1 text-muted hover:bg-panel hover:text-ink">
                <X className="h-4 w-4" aria-hidden />
              </button>
            </D.Close>
          </D.Content>
        )}
      </D.Portal>
    </D.Root>
  );
}

// 시트 머리의 아이콘 버튼(닫기 + 호출측 actions 의 이전·다음 등) -- 터치 36px(포탈 관용 = WCAG 2.2 2.5.8 의 24px 이상 -- jobs/ui
// SMALL_BTN·ICON_BTN 은 32px; 닫기와 actions 묶음 사이는 12px), 비활성은 흐린 글자. 비활성 모양은 aria-disabled 에도
// 준다: 누른 버튼이 그 동작으로 비활성이 되는 자리(이전·다음이 목록 끝에 닿음)는 disabled 대신 aria-disabled 로 둔다 -- disabled 면
// 브라우저가 포커스를 <body> 로 떨어뜨리고 모달의 포커스 가둠도 되돌리지 않는다(2026-10-29 리뷰, 실 Chrome).
export const SHEET_ICON_BUTTON = "grid h-9 w-9 place-items-center rounded-lg border border-line bg-surface text-ink "
  + "hover:bg-panel focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent "
  + "disabled:cursor-not-allowed disabled:text-muted/50 disabled:hover:bg-surface "
  + "aria-disabled:cursor-not-allowed aria-disabled:text-muted/50 aria-disabled:hover:bg-surface";
