import { useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useMe } from "../features/auth/useAuth";
import { Button } from "../components/ui/Button";
import { ApiError } from "../lib/api";

// 보호 화면 진입 규칙(2026-11-04 기능 점검 #2 -- Home(router.tsx)의 §2.4-5 규칙을 모든 보호 화면에): /login 으로 보내는 것은
// **401 뿐**이다(not_authenticated·session_revoked -- api.ts request() 가 401 에서만 dms:unauthorized 를 쏜다). 5xx·네트워크
// 오류에 /login 으로 보내면 세션이 살아 있는 사용자가 「로그아웃됐다」로 오독하고 다시 로그인한다(라이브: DB 전환 ~35초 동안
// /me 500 → 딥링크마다 안내 없는 로그인 폼). 그런 오류는 이 자리(URL 그대로)에 오류 문구 + 다시 시도를 그린다. 401 에서 오류
// 화면을 띄우면 문구가 거짓말이 되고 dms:unauthorized → me 무효화 → 재조회 401 루프가 된다(AuthContext.tsx) -- 401 은 리다이렉트로
// 관찰자를 언마운트한다.
export function RequireRole({ role, children }: { role?: "admin"; children: React.ReactNode }) {
  const me = useMe();
  // /login 으로 보낼 때 원래 주소를 state.from 에 싣는다(2026-11-11) -- 로그인 성공 뒤 그 화면으로 돌아간다(navigation.loginReturnPath).
  const loc = useLocation();
  const toLogin = <Navigate to="/login" replace state={{ from: loc.pathname + loc.search + loc.hash }} />;
  // 사용자가 누른 「다시 시도」가 도는 동안은 오류 패널을 그대로 둔다. 데이터 없는 쿼리의 재조회는 status 를 pending·error 를 null
  // 로 되돌려(TanStack fetchState) 그냥 두면 「불러오는 중…」으로 바뀌며 버튼이 언마운트되고 키보드 포커스가 body 로 떨어진다 --
  // 같은 버튼 노드를 남겨 포커스를 지킨다(그래서 disabled 가 아니라 aria-disabled: disabled 버튼은 포커스를 놓는다).
  const [retrying, setRetrying] = useState(false);
  // isPending = 아직 데이터도 오류도 없음: 첫 조회와 그 재시도 대기(useMe 의 5xx 재시도) + 오프라인 일시정지(networkMode online
  // -- 연결이 돌아오면 이어서 조회한다). 예전 isLoading 은 일시정지에서 거짓이라 아래 「데이터 없음 → /login」으로 샜다.
  if (me.isPending && !retrying) return <div className="p-6 text-muted">불러오는 중…</div>;
  if (me.isError && me.error instanceof ApiError && me.error.status === 401) return toLogin;
  // 데이터가 있는데 재조회(창 포커스·화면 이동 재마운트)만 실패한 경우는 화면을 그대로 둔다 -- 마지막으로 확인된 세션이고, 화면을
  // 걷으면 작성 중이던 입력이 사라진다. 인가는 서버가 요청마다 다시 판정한다(화면 조회들은 자기 오류를 보인다).
  if ((me.isError || retrying) && me.data === undefined) {
    return (
      <div className="p-6 space-y-3">
        <p role="alert" className="text-bad">세션 확인에 실패했습니다 — 서버 오류이거나 네트워크 문제일 수 있습니다</p>
        <Button aria-disabled={retrying || undefined} className="aria-disabled:opacity-50"
                onClick={() => {
                  if (retrying) return;
                  setRetrying(true);
                  void me.refetch().finally(() => setRetrying(false));
                }}>
          {retrying ? "다시 확인하는 중…" : "다시 시도"}
        </Button>
      </div>
    );
  }
  if (!me.data) return toLogin;
  if (role === "admin" && me.data.role !== "admin") return <Navigate to="/jobs" replace />;
  return <>{children}</>;
}
