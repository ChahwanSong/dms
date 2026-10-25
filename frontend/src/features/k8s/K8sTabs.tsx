import { NavLink } from "react-router-dom";

// k8s 연동의 하위 내비게이션(BuildTabs 선례). 사이드바 항목은 「k8s 연동」 하나(navigation.ts)이고 다섯 화면은 여기 탭으로
// 가른다 -- 사이드바에 다섯을 늘어놓으면 스토리지 그룹이 길어지고 「k8s 연동」이라는 한 가지 일이 다섯 일처럼 읽힌다.
// end 는 「개요」에만: /admin/k8s 는 나머지 넷의 접두라 end 가 없으면 하위 화면에서도 개요가 함께 켜진다.
// nav 라벨은 이 하나뿐(고유) -- 화면의 다른 nav·구획에는 aria-label 을 달지 않는다(Playwright getByLabel 부분 일치 사고 예방).
export const K8S_TABS = [
  { to: "/admin/k8s", label: "개요", end: true },
  { to: "/admin/k8s/storageclasses", label: "StorageClass", end: false },
  { to: "/admin/k8s/volumes", label: "PV 탐색", end: false },
  { to: "/admin/k8s/access", label: "사용자 권한", end: false },
  { to: "/admin/k8s/history", label: "동기화 이력", end: false },
] as const;

const tabCls = ({ isActive }: { isActive: boolean }) =>
  `-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm leading-6 ${
    isActive ? "border-accent font-medium text-accent" : "border-transparent text-ink/70 hover:text-ink"}`;

/** 현재 위치는 NavLink 의 aria-current="page" 가 알린다(색만으로 말하지 않는다). 좁은 폭에선 가로로 스크롤한다. */
export function K8sTabs() {
  return (
    <nav aria-label="k8s 연동 하위 메뉴" className="flex gap-1 overflow-x-auto border-b border-line">
      {K8S_TABS.map((t) => (
        <NavLink key={t.to} to={t.to} end={t.end} className={tabCls}>{t.label}</NavLink>
      ))}
    </nav>
  );
}

/** 하위 화면 공용 머리(h1 + 한 줄 설명 + 오른쪽 동작 + 탭). h1 은 화면에 하나 — 개요는 사이드바 라벨(「k8s 연동」), 하위 화면은
 *  탭·브레드크럼 라벨과 같은 문자열이다(빌드 이력 선례 — router.test 가 짝을 고정한다). */
export function K8sHeader({ title, description, actions }: {
  title: string; description: React.ReactNode; actions?: React.ReactNode;
}) {
  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold">{title}</h1>
          <p className="mt-1 max-w-3xl text-sm text-ink/70">{description}</p>
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      <K8sTabs />
    </>
  );
}
