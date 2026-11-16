import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { Button } from "./Button";

// 쪽 넘김(2026-11-16 공용 -- 사용량 대상 목록·배치 상세 항목). page 는 1부터. 한 쪽뿐이면(pageCount <= 1) 아무것도 그리지
// 않는다 -- 누를 데 없는 버튼 다섯 개는 소음이다. 버튼은 아이콘 + sr-only 라벨(보조기기·테스트가 「처음」「이전」…으로 부른다),
// 경계에서는 disabled. 375px 에서 한 줄(whitespace-nowrap + 좁은 패딩) -- 버튼 넷과 「12 / 345쪽」이 접히지 않게.
// 쪽 번호를 지금 범위 안으로 맞추는 일(목록이 줄어 page > pageCount)은 호출측 몫이다 -- 여기서 고쳐 그리면 화면과 상태가 갈린다.
const NAV_BTN = "px-2 py-1.5";
const ICON = "h-4 w-4";
export function Pager({ page, pageCount, onPage, label }: {
  page: number; pageCount: number; onPage: (p: number) => void; label: string;
}): JSX.Element | null {
  if (pageCount <= 1) return null;
  const atFirst = page <= 1;
  const atLast = page >= pageCount;
  return (
    <nav aria-label={label} className="flex items-center justify-center gap-1 whitespace-nowrap">
      <Button type="button" variant="ghost" className={NAV_BTN} title="처음" disabled={atFirst} onClick={() => onPage(1)}>
        <ChevronsLeft aria-hidden className={ICON} /><span className="sr-only">처음</span>
      </Button>
      <Button type="button" variant="ghost" className={NAV_BTN} title="이전" disabled={atFirst}
              onClick={() => onPage(page - 1)}>
        <ChevronLeft aria-hidden className={ICON} /><span className="sr-only">이전</span>
      </Button>
      <span className="px-2 text-sm tabular-nums text-muted">{`${page} / ${pageCount}쪽`}</span>
      <Button type="button" variant="ghost" className={NAV_BTN} title="다음" disabled={atLast}
              onClick={() => onPage(page + 1)}>
        <ChevronRight aria-hidden className={ICON} /><span className="sr-only">다음</span>
      </Button>
      <Button type="button" variant="ghost" className={NAV_BTN} title="마지막" disabled={atLast}
              onClick={() => onPage(pageCount)}>
        <ChevronsRight aria-hidden className={ICON} /><span className="sr-only">마지막</span>
      </Button>
    </nav>
  );
}
