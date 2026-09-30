import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
// size: 기본 md(폼 다이얼로그). 표·요약이 여럿인 상세 창(노드 상세 등)은 xl 로 넓힌다 --
// md 폭에선 표가 가로 스크롤로 잘린다(2026-09-30 노드 상세 모달화).
const SIZES = { md: "max-w-md", lg: "max-w-2xl", xl: "max-w-5xl" } as const;
export function Dialog({ trigger, title, children, open, onOpenChange, size = "md" }: {
  trigger: React.ReactNode; title: string; children: React.ReactNode;
  open?: boolean; onOpenChange?: (o: boolean) => void; size?: keyof typeof SIZES;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Trigger asChild>{trigger}</D.Trigger>
      <D.Portal>
        <D.Overlay className="fixed inset-0 bg-black/30" />
        {/* max-h+overflow: 필드가 많은 다이얼로그(정책 9필드)가 낮은 화면에서
            제목·저장 버튼째 뷰포트 밖으로 잘리던 결함 — 넘치면 내부 스크롤로 */}
        <D.Content aria-describedby={undefined}
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
      </D.Portal>
    </D.Root>
  );
}
