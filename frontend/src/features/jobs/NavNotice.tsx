import { useLocation, useNavigate } from "react-router-dom";
import { CircleCheck } from "lucide-react";

// 다른 화면이 이동하면서 남긴 한 줄 알림(navigate(path, { state: { notice } })) — 2026-10-26 요청 상세의 단건 삭제가 첫
// 사용처다(지운 요청의 상세는 404 라 머물 수 없어 목록으로 옮기며 「삭제했다」를 말한다). 히스토리 state 라 새로 고쳐도 남을
// 수 있어 「닫기」가 state 를 비운다(replace — 뒤로 가기 기록을 늘리지 않는다). 모양이 아닌 state(DB 밖 — 다른 화면이 넣은
// 값)는 무시한다: 문자열 notice 만 그린다.
export interface NavNoticeState { notice?: string }

export function NavNotice() {
  const loc = useLocation();
  const nav = useNavigate();
  const notice = (loc.state as NavNoticeState | null)?.notice;
  if (typeof notice !== "string" || notice === "") return null;
  return (
    <div role="status" className="flex flex-wrap items-center gap-2 rounded-lg border border-ok/30 bg-okbg px-3 py-2 text-sm text-ink">
      <CircleCheck className="h-4 w-4 shrink-0 text-ok" aria-hidden />
      <p className="min-w-0 flex-1 break-keep">{notice}</p>
      <button type="button" className="shrink-0 text-accent underline"
              onClick={() => nav(`${loc.pathname}${loc.search}`, { replace: true, state: null })}>닫기</button>
    </div>
  );
}
