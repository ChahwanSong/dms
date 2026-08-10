import { NavLink, useLocation } from "react-router-dom";
import { useMe, useLogout } from "../features/auth/useAuth";
import { ErrorBoundary } from "./ErrorBoundary";
const linkCls = ({ isActive }: { isActive: boolean }) =>
  `block rounded-lg px-3 py-2 text-sm ${isActive ? "bg-accent text-white" : "text-ink hover:bg-black/5"}`;
export function AppShell({ children }: { children: React.ReactNode }) {
  const me = useMe(); const logout = useLogout(); const isAdmin = me.data?.role === "admin";
  const { pathname } = useLocation();
  return (
    <div className="min-h-full md:flex">
      {/* shrink-0: 넓은 표(계정·잡·릴리스…)가 있는 화면에서 사이드바가 쪼그라들어
          메뉴 글자가 줄바꿈되던 것을 막는다 -- 폭 15rem 은 고정이어야 한다. */}
      <aside className="md:w-60 md:shrink-0 md:min-h-full bg-surface md:shadow-soft p-3 space-y-1">
        <div className="px-3 py-2 font-semibold">DMS</div>
        <NavLink to="/jobs" className={linkCls}>내 작업</NavLink>
        <NavLink to="/jobs/new" className={linkCls}>작업 제출</NavLink>
        <NavLink to="/scan-paths" className={linkCls}>내 스캔 경로</NavLink>
        {isAdmin && <NavLink to="/admin/scan" className={linkCls}>scan 실행</NavLink>}
        {isAdmin && <NavLink to="/admin/storages" className={linkCls}>스토리지</NavLink>}
        {isAdmin && <NavLink to="/admin/dashboard" className={linkCls}>대시보드</NavLink>}
        {isAdmin && <NavLink to="/admin/batches" className={linkCls}>배치 작업</NavLink>}
        {isAdmin && <NavLink to="/admin/audit" className={linkCls}>감사 로그</NavLink>}
        {isAdmin && <NavLink to="/admin/policies" className={linkCls}>정책</NavLink>}
        {isAdmin && <NavLink to="/admin/denylist" className={linkCls}>denylist</NavLink>}
        {isAdmin && <NavLink to="/admin/control" className={linkCls}>컨트롤 상태</NavLink>}
        {isAdmin && <NavLink to="/admin/artifact-base" className={linkCls}>아티팩트 경로</NavLink>}
        {isAdmin && <NavLink to="/admin/accounts" className={linkCls}>계정</NavLink>}
        {isAdmin && <NavLink to="/admin/nodes" className={linkCls}>노드</NavLink>}
        {isAdmin && <NavLink to="/admin/builds" className={linkCls}>빌드</NavLink>}
        {isAdmin && <NavLink to="/admin/releases" className={linkCls}>릴리스</NavLink>}
      </aside>
      {/* min-w-0: flex 자식의 기본 min-width 는 auto 라 콘텐츠보다 좁아지지 못한다.
          그래서 안쪽 표의 overflow-x-auto 가 발동하지 못하고 레이아웃 전체가 넓어져
          사이드바를 밀어냈다 -- 이 한 줄이 표를 자기 컨테이너 안에서 스크롤하게 만든다. */}
      <div className="flex-1 min-w-0">
        <header className="flex items-center justify-between px-5 h-14 bg-surface shadow-soft">
          <div className="text-sm text-muted">{me.data?.actor} · {me.data?.role}</div>
          <button className="text-sm text-accent" onClick={() => logout.mutate()}>로그아웃</button>
        </header>
        <main className="p-5">
          {/* key 가 없으면 AppShell 은 모든 보호 라우트에서 같은 위치의 같은 컴포넌트라
              한 번 에러 상태에 빠지면 화면을 옮겨도 풀리지 않는다. */}
          <ErrorBoundary key={pathname}>{children}</ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
