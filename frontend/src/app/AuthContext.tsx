import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  useEffect(() => {
    // dms:unauthorized(모든 401) → me만 무효화(제거/clear 아님). clear()는 관찰 중인 me를
    // pending으로 리셋해 RequireRole이 계속 loading을 띄우고 재요청→401→clear 무한 루프가 된다.
    // invalidate면 me가 error로 남아 RequireRole이 /login으로 이동(관찰자 언마운트)→루프 없음.
    // 교차사용자 캐시 누수는 useLogin의 qc.clear()(로그인 성공 시)가 막는다.
    const h = () => qc.invalidateQueries({ queryKey: ["auth", "me"] });
    window.addEventListener("dms:unauthorized", h);
    return () => window.removeEventListener("dms:unauthorized", h);
  }, [qc]);
  return <>{children}</>;
}
