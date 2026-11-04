import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  useEffect(() => {
    // dms:unauthorized(모든 401) → me만 무효화(제거/clear 아님). clear()는 관찰 중인 me를
    // pending으로 리셋해 RequireRole이 계속 loading을 띄우고 재요청→401→clear 무한 루프가 된다.
    // invalidate면 me가 error로 남아 RequireRole이 /login으로 이동(관찰자 언마운트)→루프 없음.
    // 교차사용자 캐시 누수는 useLogin의 qc.clear()(로그인 성공 시)가 막는다.
    // cancelRefetch:false(2026-11-04 기능 점검 #2 수리 중 발견): 이 이벤트는 me 조회 **자신의** 401 응답 안에서도(request() 가
    // 던지기 직전) 쏜다. 기본값(true)이면 확인된 세션(me 데이터 있음)의 진행 중 me 조회를 조용히 취소하고 새로 묻는다 -- 그 401 이
    // 또 이벤트 → 또 취소 → … me 가 영영 error 로 정착하지 못해 /login 이동 없이 /me 만 무한히 두드린다(세션 만료·session_revoked
    // 를 화면을 연 채 맞은 경우). false 면 진행 중 조회에 합류해 그 401 이 error 로 정착하고 RequireRole 이 /login 으로 보낸다.
    const h = () => qc.invalidateQueries({ queryKey: ["auth", "me"] }, { cancelRefetch: false });
    window.addEventListener("dms:unauthorized", h);
    return () => window.removeEventListener("dms:unauthorized", h);
  }, [qc]);
  return <>{children}</>;
}
