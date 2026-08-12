import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useLogin } from "./useAuth";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ApiError } from "../../lib/api";

export function Login() {
  const [username, setU] = useState(""); const [password, setP] = useState("");
  const login = useLogin();
  const nav = useNavigate();
  return (
    <div className="min-h-full grid place-items-center p-6">
      <div className="w-full max-w-sm">
        {/* 브랜드 블록은 div 다(톱바와 동일) -- h1 은 화면이 소유하고("DMS 로그인"
            유지, 열린 질문 ② 확정) 브랜드가 h1 이 되면 접근성 트리가 흐려진다. */}
        <div className="rounded-t-card bg-navy px-5 py-4 text-white text-sm font-semibold">
          AI Storage Portal
        </div>
        {/* rounded-t-none: 브랜드 블록과 한 덩어리로 붙인다(사이 여백·이중 라운드 제거). */}
        <Card className="rounded-t-none">
          <h1 className="text-2xl font-bold mb-5">DMS 로그인</h1>
          <form onSubmit={(e) => {
                  e.preventDefault();
                  login.mutate({ username, password }, { onSuccess: () => nav("/") });
                }}
                className="space-y-3">
            <label className="block text-sm">사용자명
              <input aria-label="사용자명" className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2"
                     value={username} onChange={(e) => setU(e.target.value)} />
            </label>
            <label className="block text-sm">비밀번호
              <input aria-label="비밀번호" type="password" className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2"
                     value={password} onChange={(e) => setP(e.target.value)} />
            </label>
            {login.isError && (
              <p className="text-bad text-sm">
                {/* fetch 네트워크 단절은 TypeError 로 reject 된다 -- 무가드 캐스트는
                    영어 원문("Failed to fetch")을 그대로 노출했다. */}
                {login.error instanceof ApiError
                  ? login.error.message
                  : "로그인 요청에 실패했습니다 — 네트워크 상태를 확인하세요"}
              </p>
            )}
            {/* 버튼 위 여백 한 단계(mt-2): 인풋 묶음과 제출 행동을 시각 분리(T5 배치 여백만). */}
            <Button type="submit" className="w-full mt-2" disabled={login.isPending}>로그인</Button>
          </form>
        </Card>
      </div>
    </div>
  );
}
