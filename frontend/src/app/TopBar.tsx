import { useNavigate } from "react-router-dom";
import { useMe, useLogout } from "../features/auth/useAuth";

// 전역 톱바(슬라이스 31 T2). 브랜드 블록은 **div** 다 -- 셸에 h1 을 두면 e2e visit()
// 의 heading level:1 매칭이 흐려진다(화면이 h1 을 소유한다, 전제 재확인 #4).
export function TopBar() {
  const me = useMe();
  const logout = useLogout();
  const nav = useNavigate();
  return (
    <header className="h-14 flex items-center justify-between gap-3 bg-surface border-b border-line">
      {/* md:w-60 은 사이드바(240px)와 같은 폭 -- 네이비 블록이 사이드바 기둥과
          정렬돼 보이게 한다. 모바일에선 내용 폭 shrink-0(줄바꿈 금지). */}
      <div className="self-stretch flex items-center px-5 bg-navy text-white text-sm font-semibold shrink-0 md:w-60">
        AI Storage Portal
      </div>
      {/* min-w-0 + truncate: 375px 뷰포트에서 사용자명이 길면 말줄임한다 -- 여기가
          늘어나면 문서 전체가 넓어져 e2e L1(가로 오버플로 금지)이 문다. */}
      <div className="flex items-center gap-3 min-w-0 pr-4 md:pr-5">
        <div className="text-sm text-muted truncate min-w-0">{me.data?.actor} · {me.data?.role}</div>
        {/* 슬라이스 29(§2.2 🔴): qc.clear()(useLogout onSettled, 유지)는 me 쿼리를
            제거해 관찰자 재조회·폴링이 전부 멈춘다 -- RequireRole 이 401 을 볼
            통로가 없어 명시 nav 가 유일한 전환 수단이다. nav 가 훅이 아니라 여기
            (컴포넌트)에 있는 이유: useAuth.test 는 Router 없이 훅을 렌더한다.
            onSettled 인 이유: POST 실패여도 캐시는 이미 비어(훅 onSettled, 실패
            시에도 -- 박제됨) 관리자 화면 잔류가 최악이다 -- 떠나려는 의도대로
            보낸다. /login 은 쿼리 관찰자 0 이라 재조회 루프가 성립하지 않는다. */}
        <button className="shrink-0 text-sm leading-6 text-accent border border-accent rounded-full px-4 py-1 hover:bg-infobg"
                onClick={() => logout.mutate(undefined,
                  { onSettled: () => nav("/login", { replace: true }) })}>로그아웃</button>
      </div>
    </header>
  );
}
