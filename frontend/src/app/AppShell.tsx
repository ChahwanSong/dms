import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { ChevronDown, HardDrive } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useMe } from "../features/auth/useAuth";
import { NAVIGATION, activeNavPath, groupLabelFor } from "./navigation";
import type { NavGroup, NavSection } from "./navigation";
import { UserPanel } from "./UserPanel";
import { Breadcrumb } from "./Breadcrumb";
import { ErrorBoundary } from "./ErrorBoundary";

// L4(e2e layout.ts): 링크 높이 < 2×line-height. text-sm(20px)이면 한계 40px 라
// DS 의 44px 항목이 위반이다 -- leading-6(24px)으로 한계를 48px 로 올리고
// py-2.5(10px×2)+24px=44px 로 DS 높이와 L4 를 동시에 만족시킨다.
const linkCls = (active: boolean) =>
  `flex items-center gap-2 rounded-lg px-3 py-2.5 text-sm leading-6 ${
    active ? "bg-infobg text-accent font-medium" : "text-ink hover:bg-panel"}`;

/** 사이드바 항목 링크 -- 아이콘은 16px 인라인이라 L4 높이에 영향이 없다.
 *
 *  활성 판정은 NavLink 의 isActive(접두 일치)가 아니라 activeNavPath(최장 일치
 *  하나)다: /jobs/new 에서 /jobs(내 작업)까지 함께 음영되던 결함(사용자 보고)의
 *  수리이고, 상세 경로(/jobs/:id 등)에서는 여전히 부모 항목이 켜진다. */
function NavItemLink({ path, label, icon: Icon, active }: {
  path: string; label: string; icon: LucideIcon; active: boolean;
}) {
  return (
    <NavLink to={path} className={linkCls(active)}>
      <Icon className="h-4 w-4 shrink-0" aria-hidden />
      {label}
    </NavLink>
  );
}

function Group({ group, collapsed, onToggle, activePath }: {
  group: NavGroup; collapsed: boolean; onToggle: () => void; activePath: string | null;
}) {
  return (
    <div>
      {/* 그룹 헤더는 <a> 가 아니라 <button> -- e2e L4 의 `aside a` 셀렉터를
          오염시키지 않으면서 접기 토글을 제공한다. */}
      <button type="button" onClick={onToggle} aria-expanded={!collapsed}
              className="w-full flex items-center justify-between px-3 pt-3 pb-1 text-xs font-medium text-muted">
        {group.label}
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${collapsed ? "-rotate-90" : ""}`} aria-hidden />
      </button>
      {!collapsed && group.items.map((item) => (
        <NavItemLink key={item.path} {...item} active={item.path === activePath} />
      ))}
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const { pathname } = useLocation();
  // 사이드바 활성 항목(최장 일치 하나) -- NavItemLink 주석 참고.
  const activePath = activeNavPath(pathname);
  // 접힘 규칙(사용자 결정 2026-08-19 재조정): 그룹 토글은 **서로 독립**이고,
  // 열어둔 그룹은 화면을 이동해도 유지된다. 남는 규칙 둘: ① 첫 진입(새 탭)엔
  // 현재 경로가 속한 그룹만 열려 있다(로그인 직후 운영자 홈 = 대시보드 → 운영만).
  // ② 경로 이동은 그 화면의 그룹을 **열기만** 한다 -- 이 자동 펼침이 없으면
  // 접힘이 "사이드바 링크를 못 찾는" 사고가 된다(e2e 04 가 잡 화면에서 링크를
  // 클릭한다).
  //
  // sessionStorage 인 이유: AppRouter 가 <ErrorBoundary key={pathname}> 로 경로마다
  // 셸을 **통째로 리마운트**하므로(에러 상태 리셋용 -- router.tsx 주석) useState 만
  // 으로는 이동할 때마다 접힘이 초기화된다(사용자 보고: "클릭하면 나머지가 자동으로
  // 접힌다"). 컴포넌트 밖에 남겨야 리마운트를 넘고, 탭 단위(session)라 새 접속의
  // 초기 규칙 ①도 산다 -- localStorage 면 ①이 죽는다.
  // 상태 키는 렌더와 같은 `${section.label}:${group.label}` 이다 -- 맨 라벨을
  // 쓰면 초기화가 렌더 조건과 어긋나 조용히 무시된다(실제로 겪었다).
  const keysOf = (label: string | null) =>
    NAVIGATION.flatMap((s) => (s.groups ?? [])
      .filter((g) => g.label === label).map((g) => `${s.label}:${g.label}`));
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem("dms.nav.collapsed") ?? "");
      if (saved && typeof saved === "object") return saved as Record<string, boolean>;
    } catch { /* 저장분 없음/파싱 불가/스토리지 차단 -- 초기 규칙으로 */ }
    const activeKeys = new Set(keysOf(groupLabelFor(pathname)));
    const init: Record<string, boolean> = {};
    for (const section of NAVIGATION)
      for (const group of section.groups ?? []) {
        const key = `${section.label}:${group.label}`;
        if (!activeKeys.has(key)) init[key] = true;
      }
    return init;
  });
  useEffect(() => {
    try { sessionStorage.setItem("dms.nav.collapsed", JSON.stringify(collapsed)); }
    catch { /* 스토리지 차단 환경이면 유지 없이 초기 규칙만 -- 기능은 산다 */ }
  }, [collapsed]);
  useEffect(() => {
    for (const key of keysOf(groupLabelFor(pathname)))
      setCollapsed((prev) => (prev[key] ? { ...prev, [key]: false } : prev));
    // keysOf 는 NAVIGATION(모듈 상수) 파생이라 pathname 만 의존성이면 충분하다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  // adminOnly 는 그룹·항목 양쪽에서 걸러낸다. 항목이 다 걸러진 그룹은 헤더째
  // 숨긴다 -- 비관리자에게 빈 그룹 껍데기를 보이지 않기 위해서다(표시 게이트일
  // 뿐, 진짜 차단은 RequireRole 과 서버가 한다 -- navigation.ts 주석).
  const visibleGroups = (section: NavSection): NavGroup[] =>
    (section.groups ?? [])
      .filter((g) => isAdmin || g.adminOnly !== true)
      .map((g) => ({ ...g, items: g.items.filter((it) => isAdmin || it.adminOnly !== true) }))
      .filter((g) => g.items.length > 0);

  return (
    <div className="min-h-full md:flex">
      {/* 사이드바(2026-08-19 개편, 사용자 결정): 구 TopBar 를 없애고 브랜드는
          사이드바 상단에 크게, 사용자·로그아웃은 하단(UserPanel)에 둔다.
          md:h-screen + sticky: 본문이 길어도 사이드바(와 하단 사용자 패널)는
          화면에 고정 -- nav 만 자체 스크롤한다.
          shrink-0: 넓은 표(계정·잡·릴리스…)가 있는 화면에서 사이드바가 쪼그라들어
          메뉴 글자가 줄바꿈되던 것을 막는다 -- 폭 15rem 은 고정이어야 한다.
          (md:w-60 = 240px 는 e2e L3 의 상수다 -- 바꾸면 SIDEBAR_WIDTH_PX 도 함께.) */}
      <aside className="md:w-60 md:shrink-0 bg-surface md:border-r md:border-line
                        flex flex-col md:h-screen md:sticky md:top-0">
        {/* 브랜드 블록 -- div 다: 셸에 h1 을 두면 e2e visit() 의 heading level:1
            매칭이 흐려진다(화면이 h1 을 소유한다, 전제 #4). */}
        <div className="bg-navy text-white px-5 py-4 shrink-0">
          <div className="flex items-center gap-2.5">
            <HardDrive className="h-6 w-6 shrink-0 text-white/85" aria-hidden />
            <div className="min-w-0">
              <div className="text-base font-bold tracking-wide leading-tight whitespace-nowrap">AI Storage Portal</div>
              <div className="text-[11px] text-white/60 leading-tight mt-0.5">Data Management System</div>
            </div>
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto p-3 space-y-1">
          {NAVIGATION.map((section) =>
            section.path !== undefined ? (
              <NavItemLink key={section.label} path={section.path} label={section.label}
                           icon={section.icon} active={section.path === activePath} />
            ) : (
              <div key={section.label}>
                {/* 섹션 헤더도 div -- 셸에 h1 금지(전제 #4), a 금지(L4 셀렉터). */}
                <div className="flex items-center gap-2 px-3 py-2 font-semibold text-ink">
                  <section.icon className="h-4 w-4 shrink-0" aria-hidden />
                  {section.label}
                </div>
                {visibleGroups(section).map((group) => {
                  const key = `${section.label}:${group.label}`;
                  return (
                    <Group key={key} group={group} collapsed={collapsed[key] === true}
                           activePath={activePath}
                           onToggle={() => setCollapsed((c) => ({ ...c, [key]: c[key] !== true }))} />
                  );
                })}
              </div>
            ))}
        </nav>
        <UserPanel />
      </aside>
      {/* min-w-0: flex 자식의 기본 min-width 는 auto 라 콘텐츠보다 좁아지지 못한다.
          그래서 안쪽 표의 overflow-x-auto 가 발동하지 못하고 레이아웃 전체가 넓어져
          사이드바를 밀어냈다 -- 이 한 줄이 표를 자기 컨테이너 안에서 스크롤하게 만든다. */}
      <div className="flex-1 min-w-0">
        <main className="p-5">
          <Breadcrumb />
          {/* key 가 없으면 AppShell 은 모든 보호 라우트에서 같은 위치의 같은 컴포넌트라
              한 번 에러 상태에 빠지면 화면을 옮겨도 풀리지 않는다. */}
          <ErrorBoundary key={pathname}>{children}</ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
