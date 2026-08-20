import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useLogin, useRequestCode, useSignup, usePasswordReset } from "./useAuth";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ApiError } from "../../lib/api";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

function errText(e: unknown, fallback: string): string {
  // fetch 네트워크 단절은 TypeError 로 reject 된다 -- 무가드 캐스트는
  // 영어 원문("Failed to fetch")을 그대로 노출했다.
  return e instanceof ApiError ? e.message : fallback;
}

/** 계정 생성·비밀번호 변경 공용 폼(2026-08-20, 사용자 결정): 흐름이 동일하다 --
    아이디 입력 → 인증번호 받기(4자리·5분, 사내 이메일 <아이디>@도메인) →
    인증번호 + 새 비밀번호 → 완료. 이메일 전송은 지금 stub 이라(사내 메일 연동
    불가) 발급 응답의 stub_code 를 화면에 안내한다 -- 실메일 전환 시 이 안내는
    서버가 stub_code 를 빼는 것만으로 함께 사라진다. */
function VerifiedForm({ purpose, submitLabel, doneText, onDone }: {
  purpose: "signup" | "password_reset";
  submitLabel: string; doneText: string; onDone: () => void;
}) {
  const [username, setUsername] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const request = useRequestCode();
  const signup = useSignup();
  const reset = usePasswordReset();
  const submit = purpose === "signup" ? signup : reset;
  const issued = request.data;
  return (
    <form className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            submit.mutate({ username, password, code }, { onSuccess: onDone });
          }}>
      <label className="block text-sm">회사 아이디
        <input aria-label="회사 아이디" className={field} value={username}
               placeholder="예: cocoa.song"
               onChange={(e) => { setUsername(e.target.value); request.reset(); submit.reset(); }} />
      </label>
      <div className="flex items-end gap-2">
        <p className="text-xs text-muted flex-1 min-w-0 truncate">
          인증번호가 {username.trim() === "" ? "회사 이메일" : `${username.trim()}@samsung.com`} 로 전송됩니다
        </p>
        <Button type="button" variant="outline" className="shrink-0"
                disabled={request.isPending || username.trim() === ""}
                onClick={() => request.mutate({ username: username.trim(), purpose })}>
          인증번호 받기
        </Button>
      </div>
      {request.isError && <p className="text-bad text-sm">{errText(request.error, "인증번호 요청에 실패했습니다 — 네트워크 상태를 확인하세요")}</p>}
      {issued && (
        <p className="text-ok text-sm">
          {`${issued.email} 로 인증번호를 보냈습니다 (유효 ${Math.round(issued.expires_in_seconds / 60)}분)`}
          {issued.stub_code !== undefined && (
            // 사내 메일 연동 전 임시 안내 -- 서버 메일러가 stub 일 때만 온다
            <span className="block text-muted">개발용 안내: 인증번호 {issued.stub_code}</span>
          )}
        </p>
      )}
      <label className="block text-sm">인증번호 (4자리)
        <input aria-label="인증번호" className={field} value={code} inputMode="numeric"
               onChange={(e) => setCode(e.target.value)} />
      </label>
      <label className="block text-sm">{purpose === "signup" ? "비밀번호" : "새 비밀번호"}
        <input aria-label={purpose === "signup" ? "비밀번호" : "새 비밀번호"} type="password"
               className={field} value={password}
               onChange={(e) => setPassword(e.target.value)} />
      </label>
      {submit.isError && <p className="text-bad text-sm">{errText(submit.error, `${submitLabel} 요청에 실패했습니다 — 네트워크 상태를 확인하세요`)}</p>}
      {submit.isSuccess && <p className="text-ok text-sm">{doneText}</p>}
      <Button type="submit" className="w-full mt-2"
              disabled={submit.isPending || username.trim() === "" || code === "" || password === ""}>
        {submitLabel}
      </Button>
    </form>
  );
}

type Mode = "login" | "signup" | "reset";
const TABS: { mode: Mode; label: string }[] = [
  { mode: "login", label: "로그인" },
  { mode: "signup", label: "계정 생성" },
  { mode: "reset", label: "비밀번호 변경" },
];

export function Login() {
  const [mode, setMode] = useState<Mode>("login");
  const [username, setU] = useState(""); const [password, setP] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const login = useLogin();
  const nav = useNavigate();
  return (
    <div className="min-h-full grid place-items-center p-6">
      <div className="w-full max-w-sm">
        {/* 브랜드 블록은 div 다 -- h1 은 화면이 소유하고 브랜드가 h1 이 되면
            접근성 트리가 흐려진다. */}
        <div className="rounded-t-card bg-navy px-5 py-4 text-white text-sm font-semibold">
          AI Storage Portal
        </div>
        {/* rounded-t-none: 브랜드 블록과 한 덩어리로 붙인다(사이 여백·이중 라운드 제거). */}
        <Card className="rounded-t-none">
          {/* h1 은 "로그인"(사용자 결정 2026-08-20 -- 구 "DMS 로그인") */}
          <h1 className="text-2xl font-bold mb-4">
            {TABS.find((t) => t.mode === mode)?.label}
          </h1>
          <div role="tablist" className="flex gap-1 mb-5 border-b border-line">
            {TABS.map((t) => (
              <button key={t.mode} role="tab" type="button"
                      aria-selected={mode === t.mode}
                      className={`px-3 py-2 text-sm -mb-px border-b-2 ${
                        mode === t.mode
                          ? "border-accent text-accent font-medium"
                          : "border-transparent text-muted hover:text-ink"}`}
                      onClick={() => { setMode(t.mode); setNotice(null); }}>
                {t.label}
              </button>
            ))}
          </div>
          {notice && <p className="text-ok text-sm mb-3">{notice}</p>}
          {mode === "login" && (
            <form onSubmit={(e) => {
                    e.preventDefault();
                    login.mutate({ username, password }, { onSuccess: () => nav("/") });
                  }}
                  className="space-y-3">
              <label className="block text-sm">사용자명
                <input aria-label="사용자명" className={field}
                       value={username} onChange={(e) => setU(e.target.value)} />
              </label>
              <label className="block text-sm">비밀번호
                <input aria-label="비밀번호" type="password" className={field}
                       value={password} onChange={(e) => setP(e.target.value)} />
              </label>
              {login.isError && (
                <p className="text-bad text-sm">
                  {errText(login.error, "로그인 요청에 실패했습니다 — 네트워크 상태를 확인하세요")}
                </p>
              )}
              {/* 버튼 위 여백 한 단계(mt-2): 인풋 묶음과 제출 행동을 시각 분리. */}
              <Button type="submit" className="w-full mt-2" disabled={login.isPending}>로그인</Button>
            </form>
          )}
          {mode === "signup" && (
            <VerifiedForm purpose="signup" submitLabel="계정 생성"
                          doneText="계정이 생성됐습니다 — 로그인 탭에서 로그인하세요"
                          onDone={() => { setMode("login"); setNotice("계정이 생성됐습니다 — 로그인하세요"); }} />
          )}
          {mode === "reset" && (
            <VerifiedForm purpose="password_reset" submitLabel="비밀번호 변경"
                          doneText="비밀번호가 변경됐습니다 — 로그인 탭에서 로그인하세요"
                          onDone={() => { setMode("login"); setNotice("비밀번호가 변경됐습니다 — 새 비밀번호로 로그인하세요"); }} />
          )}
        </Card>
      </div>
    </div>
  );
}
