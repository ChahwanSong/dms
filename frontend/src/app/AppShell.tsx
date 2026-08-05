import { NavLink } from "react-router-dom";
import { useMe, useLogout } from "../features/auth/useAuth";
const linkCls = ({ isActive }: { isActive: boolean }) =>
  `block rounded-lg px-3 py-2 text-sm ${isActive ? "bg-accent text-white" : "text-ink hover:bg-black/5"}`;
export function AppShell({ children }: { children: React.ReactNode }) {
  const me = useMe(); const logout = useLogout(); const isAdmin = me.data?.role === "admin";
  return (
    <div className="min-h-full md:flex">
      <aside className="md:w-60 md:min-h-full bg-surface md:shadow-soft p-3 space-y-1">
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
      </aside>
      <div className="flex-1">
        <header className="flex items-center justify-between px-5 h-14 bg-surface shadow-soft">
          <div className="text-sm text-muted">{me.data?.actor} · {me.data?.role}</div>
          <button className="text-sm text-accent" onClick={() => logout.mutate()}>로그아웃</button>
        </header>
        <main className="p-5">{children}</main>
      </div>
    </div>
  );
}
