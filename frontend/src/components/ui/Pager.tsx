import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { Button } from "./Button";

// 번호 쪽 넘김(2026-11-16 공용 -- 사용량 분석 대상 목록·배치 상세 항목). 무한 스크롤(작업 목록·감사 기록)을 쓰지 않는 이유:
// 쪽이 쌓이면 수천 개의 <tr>(사용량 행은 행마다 3.6KB 요약)가 다시 메모리에 올라가 버벅임이 돌아오고, 지표 정렬처럼 서버가
// 파이썬에서 정렬하는 목록엔 키셋 커서가 어색하다 -- 쪽 번호면 아무 쪽으로나 바로 간다.
// - page 는 1부터. pageCount ≤ 1 이면 그리지 않는다(넘길 쪽이 없는데 「1 / 1쪽」 줄을 세우지 않는다).
// - 버튼은 아이콘 + sr-only 이름(처음·이전·다음·마지막) -- 375 폭에서 한 줄(flex-nowrap, 글자 줄은 whitespace-nowrap). 경계에서
//   disabled(첫 쪽의 처음·이전, 마지막 쪽의 다음·마지막) -- 눌러도 아무 일 없는 버튼을 살려 두지 않는다.
// - 표(가로 스크롤 래퍼) **밖**에 둔다(호출측) -- 래퍼 안이면 표를 가로로 밀 때 쪽 버튼도 화면 밖으로 밀린다.
// - 가운데 정렬은 여기서 한다(배치 상세·사용량 목록이 같은 자리 모양). 쪽 번호를 지금 범위 안으로 맞추는 일(목록이 줄어
//   page > pageCount)은 호출측 몫이다 -- 여기서 고쳐 그리면 화면과 상태가 갈린다.
export function Pager({ page, pageCount, onPage, label }: {
  page: number; pageCount: number; onPage: (p: number) => void; label: string;
}) {
  if (pageCount <= 1) return null;
  const first = page <= 1, last = page >= pageCount;
  const btn = (name: string, icon: React.ReactNode, to: number, disabled: boolean) => (
    <Button type="button" variant="ghost" className="px-2 py-1.5" title={name} disabled={disabled}
            onClick={() => onPage(to)}>
      {icon}<span className="sr-only">{name}</span>
    </Button>
  );
  const ico = "h-4 w-4";
  return (
    <nav aria-label={label} className="flex flex-nowrap items-center justify-center gap-1.5 whitespace-nowrap">
      {btn("처음", <ChevronsLeft aria-hidden className={ico} />, 1, first)}
      {btn("이전", <ChevronLeft aria-hidden className={ico} />, page - 1, first)}
      <span className="whitespace-nowrap px-1.5 text-sm tabular-nums text-ink/80">{`${page} / ${pageCount}쪽`}</span>
      {btn("다음", <ChevronRight aria-hidden className={ico} />, page + 1, last)}
      {btn("마지막", <ChevronsRight aria-hidden className={ico} />, pageCount, last)}
    </nav>
  );
}
