import { NavLink } from "react-router-dom";

// 빌드의 하위 내비게이션. 사이드바 항목은 「빌드」 하나로 두고(navigation.ts),
// 「빌드하기 / 빌드 이력」은 여기 탭으로 가른다 -- 사이드바에 둘을 나란히 두면
// 운영 그룹이 길어지고, "빌드"라는 한 가지 일이 두 개의 일처럼 읽힌다.
//
// 셸의 aside 가 아니라 main 안이라 e2e L4(`aside a` 셀렉터)의 사정권 밖이다.
// 그래도 leading-6 을 준다: 링크 높이 규칙을 화면마다 다르게 두지 않는다.
//
// end 는 「빌드하기」에만 필요하다 -- /admin/builds 는 /admin/builds/history 의
// 접두라, end 가 없으면 이력에서도 두 탭이 함께 켜져 "지금 어디"를 말하지 못한다.
const TABS = [
  { to: "/admin/builds", label: "빌드하기", end: true },
  { to: "/admin/builds/history", label: "빌드 이력", end: false },
] as const;

const tabCls = ({ isActive }: { isActive: boolean }) =>
  `-mb-px border-b-2 px-3 py-2 text-sm leading-6 ${
    isActive ? "border-accent text-accent font-medium" : "border-transparent text-muted hover:text-ink"}`;

/** 「빌드하기 | 빌드 이력」 탭. 현재 위치는 NavLink 가 aria-current="page" 로 알린다
    -- 색(accent)만으로 말하면 스크린리더·색각 이상 사용자에게는 아무 표시가 없다. */
export function BuildTabs() {
  return (
    <nav aria-label="빌드 하위 메뉴" className="flex gap-1 border-b border-line">
      {TABS.map((t) => (
        <NavLink key={t.to} to={t.to} end={t.end} className={tabCls}>{t.label}</NavLink>
      ))}
    </nav>
  );
}
