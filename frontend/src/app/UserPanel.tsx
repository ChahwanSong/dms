import { useNavigate } from "react-router-dom";
import { useMe, useLogout } from "../features/auth/useAuth";

// 사이드바 하단 사용자 패널(2026-08-19, 사용자 결정): 구 TopBar 우측에 있던
// 아이디·로그아웃을 왼쪽 하단으로 옮긴 것 -- 로그아웃 로직·계약은 그대로다.
export function UserPanel() {
  const me = useMe();
  const logout = useLogout();
  const nav = useNavigate();
  return (
    <div className="border-t border-line p-3 flex items-center justify-between gap-2">
      {/* min-w-0 + truncate: 사용자명이 길면 말줄임 -- 패널이 늘어나면 사이드바
          240px(e2e L3)이 밀린다. */}
      <div className="text-sm text-muted truncate min-w-0">{me.data?.actor} · {me.data?.role}</div>
      {/* 슬라이스 29(§2.2 🔴): qc.clear()(useLogout onSettled, 유지)는 me 쿼리를
          제거해 관찰자 재조회·폴링이 전부 멈춘다 -- RequireRole 이 401 을 볼
          통로가 없어 명시 nav 가 유일한 전환 수단이다. nav 가 훅이 아니라 여기
          (컴포넌트)에 있는 이유: useAuth.test 는 Router 없이 훅을 렌더한다.
          onSettled 인 이유: POST 실패여도 캐시는 이미 비어(훅 onSettled, 실패
          시에도 -- 박제됨) 관리자 화면 잔류가 최악이다 -- 떠나려는 의도대로
          보낸다. /login 은 쿼리 관찰자 0 이라 재조회 루프가 성립하지 않는다.
          접근성 이름 "로그아웃"은 e2e 01·router.test 와 삼중 계약 -- 위치를
          옮겨도 이름은 불변이다. */}
      <button className="shrink-0 text-sm leading-6 text-accent border border-accent rounded-full px-3 py-1 hover:bg-infobg"
              onClick={() => logout.mutate(undefined,
                { onSettled: () => nav("/login", { replace: true }) })}>로그아웃</button>
    </div>
  );
}
