import { Navigate } from "react-router-dom";
import { useMe } from "../features/auth/useAuth";
export function RequireRole({ role, children }: { role?: "admin"; children: React.ReactNode }) {
  const me = useMe();
  if (me.isLoading) return <div className="p-6 text-muted">불러오는 중…</div>;
  if (me.isError || !me.data) return <Navigate to="/login" replace />;
  if (role === "admin" && me.data.role !== "admin") return <Navigate to="/jobs" replace />;
  return <>{children}</>;
}
