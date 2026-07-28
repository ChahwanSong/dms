import { useEffect, useRef, useState } from "react";
import { auth, ApiError, type User, type UserAuthConfig, type VerifyPurpose } from "../api";

type Tab = "operator" | "user";
// Both tabs share the same three modes; the operator tab gates them behind the
// shared admin token, the user tab behind an emailed verification code.
type Mode = "login" | "register" | "reset";
// The user tab's create/reset flows are two steps: enter the id (+password), then
// enter the code that was mailed to <id>@<domain>.
type Step = "form" | "code";

// friendly Korean messages for the BFF's error detail codes.
const ERR: Record<string, string> = {
  invalid_credentials: "아이디 또는 비밀번호가 올바르지 않습니다.",
  invalid_token: "운영자 토큰이 올바르지 않습니다.",
  account_token_not_configured: "운영자 토큰이 서버에 설정되어 있지 않습니다. 관리자에게 문의하세요.",
  portal_db_not_configured: "포탈 DB가 설정되어 있지 않아 계정 생성/재설정을 사용할 수 없습니다.",
  username_exists: "이미 존재하는 아이디입니다.",
  invalid_username: "아이디는 'admin_' 접두어 + 소문자/숫자/밑줄이어야 합니다 (예: admin_ops).",
  password_too_short: "비밀번호는 최소 8자입니다.",
  operator_not_found: "해당 아이디를 찾을 수 없습니다.",
  // --- end-user (email-verified) flows ---
  invalid_local_part: "아이디는 회사 메일 주소의 @ 앞부분입니다 (소문자/숫자로 시작·끝, '.', '_', '-' 사용 가능).",
  invalid_email_domain: "회사 메일 도메인의 주소만 사용할 수 있습니다.",
  username_reserved: "사용할 수 없는 아이디입니다 (시스템 예약어).",
  signup_not_allowed: "가입이 허용된 아이디가 아닙니다. 관리자에게 문의하세요.",
  signup_disabled: "현재 사용자 계정 생성이 비활성화되어 있습니다.",
  invalid_purpose: "잘못된 요청입니다.",
  invalid_code: "인증번호가 올바르지 않거나 만료되었습니다.",
  code_attempts_exhausted: "인증번호 입력 횟수를 초과했습니다. 인증번호를 다시 요청하세요.",
  resend_too_soon: "재발송 간격 제한입니다. 잠시 후(요청이 반복된 경우 최대 1시간 후) 다시 시도하세요.",
  send_quota_exceeded: "메일 발송 한도를 초과했습니다. 잠시 후 다시 시도하세요.",
  email_not_configured: "메일 발송이 설정되어 있지 않습니다. 관리자에게 문의하세요.",
  user_not_found: "계정을 찾을 수 없거나 비활성 상태입니다.",
};

function errCode(e: unknown): string {
  if (!(e instanceof ApiError)) return "";
  const d = e.detail;
  if (typeof d === "string") return d;
  if (d && typeof d === "object" && typeof (d as { code?: string }).code === "string") {
    return (d as { code: string }).code;
  }
  return e.message;
}
function emsg(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    const d = errCode(e);
    if (ERR[d]) return ERR[d];
    // The BFF sends `snake_case_code (한국어 힌트)`, so match on the leading token
    // rather than a prefix scan — otherwise a short code would shadow a longer one
    // that starts with the same text.
    const token = d.split(/[\s(]/)[0];
    if (ERR[token]) return ERR[token];
    return d || e.message;
  }
  return e instanceof Error ? e.message : fallback;
}

const mmss = (s: number) =>
  `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

// Storage stack (disk-cylinder) mark — DMS manages storage backends, so the brand
// glyph is a storage stack rather than a generic logo.
function StorageMark() {
  return (
    <svg className="login-glyph" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <ellipse cx="12" cy="5" rx="7.5" ry="2.8" stroke="url(#lg)" strokeWidth="1.6" />
      <path d="M4.5 5v6.5c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8V5" stroke="url(#lg)" strokeWidth="1.6" />
      <path d="M4.5 11.5V18c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8v-6.5" stroke="url(#lg)" strokeWidth="1.6" opacity="0.55" />
      <defs>
        <linearGradient id="lg" x1="4" y1="3" x2="20" y2="21" gradientUnits="userSpaceOnUse">
          <stop stopColor="#7dd3fc" />
          <stop offset="1" stopColor="#3b82f6" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export default function Login({ onLoggedIn }: { onLoggedIn: (u: User) => void }) {
  const [tab, setTab] = useState<Tab>("user");
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // whether the operator-token account flows are configured server-side.
  const [tokenFlows, setTokenFlows] = useState(false);
  // end-user email verification
  const [cfg, setCfg] = useState<UserAuthConfig | null>(null);
  const [step, setStep] = useState<Step>("form");
  const [code, setCode] = useState("");
  // Absolute epochs, not counters: a background tab throttles setInterval to ~1/min,
  // so anything that decrements drifts badly exactly when the user is away reading
  // the mail. Storing the deadline and recomputing keeps the display honest.
  const [expiresAt, setExpiresAt] = useState(0);
  const [resendAt, setResendAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const codeRef = useRef<HTMLInputElement | null>(null);
  // Bumped on every mode/tab change and every send; an in-flight request whose
  // generation is stale must not touch state (see sendCode).
  const flowGen = useRef(0);

  useEffect(() => {
    auth.accountTokenRequired().then((r) => setTokenFlows(r.available)).catch(() => {});
    // Do NOT swallow this: without the domain the hint would render "undefined" and
    // the request would fail anyway, so fail visibly instead.
    auth
      .userAuthConfig()
      .then(setCfg)
      .catch(() =>
        setCfg({
          available: false, email_domain: "", code_length: 6, code_ttl_seconds: 600,
          resend_cooldown_seconds: 60, min_password_len: 8, signup_enabled: false,
          email_delivery: "none",
        }),
      );
  }, []);

  // 1Hz tick only while something is actually counting down.
  useEffect(() => {
    if (step !== "code") return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    const onVis = () => setNow(Date.now());
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [step]);

  const domain = cfg?.email_domain ?? "";
  const codeLen = cfg?.code_length ?? 6;
  const minPw = cfg?.min_password_len ?? 8;
  const uname = username.trim();
  const mailTo = uname && domain ? `${uname}@${domain}` : "";
  const remain = Math.max(0, Math.ceil((expiresAt - now) / 1000));
  const resendIn = Math.max(0, Math.ceil((resendAt - now) / 1000));
  const expired = step === "code" && remain <= 0;
  // Client-side gate only; the server is the authority.
  const unameOk = /^[a-z0-9](?:[a-z0-9._-]{0,30}[a-z0-9])?$/.test(uname);

  function pickTab(t: Tab) {
    setTab(t);
    setMode("login");
    resetFlow();
    setError(null);
    setOk(null);
  }
  function resetFlow() {
    flowGen.current++;  // invalidate any in-flight request-code response
    setStep("form");
    setCode("");
    setExpiresAt(0);
    setResendAt(0);
  }
  function switchMode(m: Mode) {
    setMode(m);
    setError(null);
    setPassword("");
    setNewPassword("");
    setToken("");
    resetFlow();
    if (m !== "login") setOk(null);
  }

  // Paste of a full address is trimmed to the local part as you type.
  function onUsername(raw: string) {
    setUsername(raw.split("@")[0].replace(/\s+/g, "").toLowerCase());
  }

  async function submitLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setBusy(true);
    try {
      onLoggedIn((await auth.login(username, password)).user);
    } catch (err) { setError(emsg(err, "로그인 실패")); } finally { setBusy(false); }
  }
  async function submitUserLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setBusy(true);
    try {
      onLoggedIn((await auth.loginUser(uname, password)).user);
    } catch (err) { setError(emsg(err, "로그인 실패")); } finally { setBusy(false); }
  }

  async function sendCode(purpose: VerifyPurpose) {
    setError(null); setOk(null); setBusy(true);
    // The request is in flight while the user can still click another sub-tab. Without
    // this guard the response would push them into the code step for the mode they
    // just left — with a code minted for a different purpose.
    const gen = ++flowGen.current;
    try {
      const r = await auth.requestCode(purpose, uname);
      if (gen !== flowGen.current) return;
      setExpiresAt(Date.now() + r.expires_in * 1000);
      setResendAt(Date.now() + r.resend_after * 1000);
      setNow(Date.now());
      setStep("code");
      setCode("");
      setOk(null);
      window.setTimeout(() => codeRef.current?.focus(), 0);
    } catch (err) {
      if (gen === flowGen.current) setError(emsg(err, "인증번호 요청 실패"));
    } finally {
      if (gen === flowGen.current) setBusy(false);
    }
  }

  // Reading the mail means leaving this page, and a refresh/reopen drops the React
  // state — without a way back the user would have to burn another send (5/hour) for a
  // code they already have. Enters the code step without mailing anything.
  function resumeCodeStep() {
    setError(null); setOk(null);
    setExpiresAt(Date.now() + (cfg?.code_ttl_seconds ?? 600) * 1000);
    setResendAt(Date.now() + (cfg?.resend_cooldown_seconds ?? 60) * 1000);
    setNow(Date.now());
    setCode("");
    setStep("code");
    window.setTimeout(() => codeRef.current?.focus(), 0);
  }

  async function submitRegister(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setOk(null);
    if (password.length < 8) { setError("비밀번호는 최소 8자입니다."); return; }
    setBusy(true);
    try {
      await auth.register(username.trim(), password, token);
      setOk(`계정 '${username.trim()}' 을(를) 만들었습니다. 로그인하세요.`);
      setMode("login"); setPassword(""); setToken("");
    } catch (err) { setError(emsg(err, "계정 생성 실패")); } finally { setBusy(false); }
  }
  async function submitReset(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setOk(null);
    if (newPassword.length < 8) { setError("새 비밀번호는 최소 8자입니다."); return; }
    setBusy(true);
    try {
      await auth.resetPassword(username.trim(), newPassword, token);
      setOk(`'${username.trim()}' 의 비밀번호를 재설정했습니다. 로그인하세요.`);
      setMode("login"); setNewPassword(""); setToken("");
    } catch (err) { setError(emsg(err, "비밀번호 재설정 실패")); } finally { setBusy(false); }
  }

  async function submitUserRegister(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setBusy(true);
    try {
      await auth.registerUser(uname, password, code);
      setOk(`계정 '${uname}' 을(를) 만들었습니다. 로그인하세요.`);
      setMode("login"); setPassword(""); resetFlow();
    } catch (err) { setError(emsg(err, "계정 생성 실패")); } finally { setBusy(false); }
  }
  async function submitUserReset(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setBusy(true);
    try {
      await auth.resetUserPassword(uname, newPassword, code);
      setOk(`'${uname}' 의 비밀번호를 재설정했습니다. 로그인하세요.`);
      setMode("login"); setNewPassword(""); resetFlow();
    } catch (err) { setError(emsg(err, "비밀번호 재설정 실패")); } finally { setBusy(false); }
  }

  const tokenField = (
    <label className="login-field login-field--secret">
      <span className="login-lbl">
        <LockIcon /> 운영자 토큰
        <span className="login-hint">운영자 전용 · 계정 생성/재설정에 필요</span>
      </span>
      <input type="password" value={token} autoComplete="off" placeholder="운영자에게 발급받은 토큰"
        onChange={(e) => setToken(e.target.value)} />
    </label>
  );

  // Not decoration: mistyping the id sends a real colleague's mailbox a code, so the
  // address is shown before the request, and again (past tense) after it.
  const mailtoHint =
    !cfg ? <p className="login-mailto is-empty">도메인을 불러오는 중…</p> :
    !domain ? <p className="login-mailto is-bad">메일 도메인이 설정되지 않았습니다. 관리자에게 문의하세요.</p> :
    <p className="login-mailto" id="login-mailto">
      {mailTo ? <><strong>{mailTo}</strong> 으로 인증메일이 발송됩니다.</> :
        <>아이디를 입력하면 <strong>아이디@{domain}</strong> 으로 인증메일이 발송됩니다.</>}
    </p>;

  const usernameField = (autoFocus: boolean) => (
    <label className="login-field">
      <span className="login-lbl">
        아이디
        <span className="login-hint">회사 메일 주소의 @ 앞부분 (예: test1.user)</span>
      </span>
      <input autoFocus={autoFocus} value={username} autoComplete="username"
        placeholder="test1.user" aria-describedby="login-mailto"
        onChange={(e) => onUsername(e.target.value)} />
    </label>
  );

  const codeStep = (purpose: VerifyPurpose, submitLabel: string) => (
    <>
      <p className="login-mailto is-sent" aria-live="polite">
        <strong>{mailTo}</strong> 으로 인증메일을 보냈습니다. 메일의 {codeLen}자리 인증번호를 입력하세요.
      </p>
      <div className="login-summary">
        <span className="login-summary-k">아이디</span>
        <span className="login-summary-v">{uname}</span>
        <button type="button" className="login-linkbtn" onClick={() => { resetFlow(); setError(null); }}>
          수정
        </button>
      </div>
      <div className="login-field">
        <label className="login-lbl" htmlFor="login-code">인증번호</label>
        <div className="login-code-row">
          <input
            id="login-code" ref={codeRef} className="login-code-input"
            type="text" inputMode="numeric" pattern="[0-9]*" autoComplete="one-time-code"
            maxLength={codeLen} value={code} placeholder={"0".repeat(codeLen)}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, codeLen))}
          />
          {/* type="button" matters: Enter inside the form must submit, not re-send mail. */}
          <button type="button" className="login-resend" disabled={busy || resendIn > 0}
            onClick={() => sendCode(purpose)}>
            {resendIn > 0 ? `재발송 ${resendIn}초` : "재발송"}
          </button>
        </div>
        <p className={"login-timer" + (expired ? " is-expired" : remain <= 60 ? " is-warn" : "")}>
          {expired ? "인증번호가 만료되었습니다. 재발송하세요." : `남은 시간 ${mmss(remain)}`}
        </p>
      </div>
      <button className="login-primary" type="submit"
        disabled={busy || expired || code.length < codeLen}>
        {busy ? "확인 중…" : submitLabel}
      </button>
    </>
  );

  const canRequest = !busy && unameOk && !!domain && (cfg?.available ?? false);

  return (
    <div className="login-page">
      <div className="login-card">
        <header className="login-head">
          <div className="login-brand">
            <StorageMark />
            <span className="login-word">DMS<span className="login-word-sub">Portal</span></span>
          </div>
          <p className="login-eyebrow">DATA MANAGEMENT · CONTROL PLANE</p>
        </header>

        {/* role: end users self-register with a company-mail code; operators are issued accounts. */}
        <div className="login-roles" role="tablist" aria-label="로그인 방식">
          <button role="tab" aria-selected={tab === "user"} type="button"
            className={"login-role" + (tab === "user" ? " active" : "")}
            onClick={() => pickTab("user")}>
            <span className="login-role-t">사용자</span>
            <span className="login-role-s">회사메일 인증 계정</span>
          </button>
          <button role="tab" aria-selected={tab === "operator"} type="button"
            className={"login-role" + (tab === "operator" ? " active" : "")}
            onClick={() => pickTab("operator")}>
            <span className="login-role-t">운영자</span>
            <span className="login-role-s">운영자 발급 계정</span>
          </button>
        </div>

        <div className="login-subtabs" role="tablist" aria-label="작업 선택">
          {(["login", "register", "reset"] as Mode[]).map((m) => (
            <button key={m} type="button" role="tab" aria-selected={mode === m}
              className={"login-subtab" + (mode === m ? " active" : "")}
              onClick={() => switchMode(m)}>
              {m === "login" ? "로그인" : m === "register" ? "계정 만들기" : "비밀번호 재설정"}
            </button>
          ))}
        </div>

        {tab === "user" ? (
          <>
            {cfg && !cfg.available && mode !== "login" && (
              <p className="login-warn">
                메일 인증이 설정되지 않아 계정 생성/재설정을 사용할 수 없습니다. 관리자에게
                <code> PORTAL_SMTP_HOST</code> · <code>PORTAL_EMAIL_DOMAIN</code> 설정을 요청하세요.
              </p>
            )}
            {cfg && cfg.available && !cfg.signup_enabled && mode === "register" && (
              <p className="login-warn">현재 사용자 계정 생성이 비활성화되어 있습니다.</p>
            )}

            {mode === "login" && (
              <form onSubmit={submitUserLogin} className="login-form">
                {usernameField(true)}
                <label className="login-field">
                  <span className="login-lbl">비밀번호</span>
                  <input type="password" value={password} autoComplete="current-password"
                    onChange={(e) => setPassword(e.target.value)} />
                </label>
                <button className="login-primary" type="submit" disabled={busy || !uname || !password}>
                  {busy ? "로그인 중…" : "로그인"}
                </button>
                <p className="login-foot-note">
                  계정이 없다면 <strong>계정 만들기</strong>에서 회사 메일 인증으로 직접 만들 수 있습니다.
                </p>
              </form>
            )}

            {mode === "register" && (step === "form" ? (
              <form className="login-form" onSubmit={(e) => { e.preventDefault(); sendCode("register"); }}>
                {usernameField(true)}
                {mailtoHint}
                <label className="login-field">
                  <span className="login-lbl">비밀번호 <span className="login-hint">최소 {minPw}자</span></span>
                  <input type="password" value={password} autoComplete="new-password"
                    onChange={(e) => setPassword(e.target.value)} />
                </label>
                <button className="login-primary" type="submit"
                  disabled={!canRequest || password.length < minPw || !(cfg?.signup_enabled ?? false)}>
                  {busy ? "인증번호 발송 중…" : "인증요청"}
                </button>
                <p className="login-foot-note">
                  이미 인증번호를 받았다면{" "}
                  <button type="button" className="login-linkbtn" disabled={!unameOk}
                    onClick={resumeCodeStep}>인증번호 입력하기</button>
                </p>
              </form>
            ) : (
              <form className="login-form" onSubmit={submitUserRegister}>
                {codeStep("register", "계정 만들기")}
              </form>
            ))}

            {mode === "reset" && (step === "form" ? (
              <form className="login-form" onSubmit={(e) => { e.preventDefault(); sendCode("reset"); }}>
                <p className="login-note">
                  비밀번호는 복구할 수 없어 <strong>재설정</strong>합니다. 회사 메일로 받은 인증번호로
                  본인 확인 후 새 비밀번호를 설정합니다.
                </p>
                {usernameField(true)}
                {mailtoHint}
                <label className="login-field">
                  <span className="login-lbl">새 비밀번호 <span className="login-hint">최소 {minPw}자</span></span>
                  <input type="password" value={newPassword} autoComplete="new-password"
                    onChange={(e) => setNewPassword(e.target.value)} />
                </label>
                <button className="login-primary" type="submit"
                  disabled={!canRequest || newPassword.length < minPw}>
                  {busy ? "인증번호 발송 중…" : "인증요청"}
                </button>
                <p className="login-foot-note">
                  이미 인증번호를 받았다면{" "}
                  <button type="button" className="login-linkbtn" disabled={!unameOk}
                    onClick={resumeCodeStep}>인증번호 입력하기</button>
                </p>
              </form>
            ) : (
              <form className="login-form" onSubmit={submitUserReset}>
                {codeStep("reset", "비밀번호 재설정")}
              </form>
            ))}
          </>
        ) : (
          <>
            {mode === "login" && (
              <form onSubmit={submitLogin} className="login-form">
                <label className="login-field">
                  <span className="login-lbl">아이디</span>
                  <input autoFocus value={username} autoComplete="username"
                    onChange={(e) => setUsername(e.target.value)} />
                </label>
                <label className="login-field">
                  <span className="login-lbl">비밀번호</span>
                  <input type="password" value={password} autoComplete="current-password"
                    onChange={(e) => setPassword(e.target.value)} />
                </label>
                <button className="login-primary" type="submit" disabled={busy || !username || !password}>
                  {busy ? "로그인 중…" : "운영자 로그인"}
                </button>
                <p className="login-foot-note">
                  계정 생성·비밀번호 재설정은 위 탭에서 <strong>운영자 토큰</strong>으로 진행합니다.
                </p>
              </form>
            )}

            {mode === "register" && (
              <form onSubmit={submitRegister} className="login-form">
                {!tokenFlows && (
                  <p className="login-warn">
                    운영자 토큰(<code>PORTAL_ADMIN_TOKEN</code>)이 아직 설정되지 않아 계정 생성이
                    비활성입니다. 관리자에게 토큰 설정을 요청하세요.
                  </p>
                )}
                <label className="login-field">
                  <span className="login-lbl">아이디 <span className="login-hint">admin_ 로 시작 (예: admin_ops)</span></span>
                  <input autoFocus value={username} autoComplete="off" placeholder="admin_"
                    onChange={(e) => setUsername(e.target.value)} />
                </label>
                <label className="login-field">
                  <span className="login-lbl">비밀번호 <span className="login-hint">최소 8자</span></span>
                  <input type="password" value={password} autoComplete="new-password"
                    onChange={(e) => setPassword(e.target.value)} />
                </label>
                {tokenField}
                <button className="login-primary" type="submit"
                  disabled={busy || !username || !password || !token}>
                  {busy ? "생성 중…" : "운영자 계정 만들기"}
                </button>
              </form>
            )}

            {mode === "reset" && (
              <form onSubmit={submitReset} className="login-form">
                {!tokenFlows && (
                  <p className="login-warn">
                    운영자 토큰(<code>PORTAL_ADMIN_TOKEN</code>)이 아직 설정되지 않아 비밀번호
                    재설정이 비활성입니다. 관리자에게 토큰 설정을 요청하세요.
                  </p>
                )}
                <p className="login-note">
                  비밀번호는 복구할 수 없어 <strong>재설정</strong>합니다. 아이디와 새 비밀번호,
                  운영자 토큰을 입력하세요.
                </p>
                <label className="login-field">
                  <span className="login-lbl">아이디</span>
                  <input autoFocus value={username} autoComplete="username"
                    onChange={(e) => setUsername(e.target.value)} />
                </label>
                <label className="login-field">
                  <span className="login-lbl">새 비밀번호 <span className="login-hint">최소 8자</span></span>
                  <input type="password" value={newPassword} autoComplete="new-password"
                    onChange={(e) => setNewPassword(e.target.value)} />
                </label>
                {tokenField}
                <button className="login-primary" type="submit"
                  disabled={busy || !username || !newPassword || !token}>
                  {busy ? "재설정 중…" : "비밀번호 재설정"}
                </button>
              </form>
            )}
          </>
        )}

        {ok && <p className="login-ok" role="status">{ok}</p>}
        {error && <p className="login-error" role="alert">{error}</p>}

        {/* signature: the storage backends DMS manages, as a quiet status strip */}
        <footer className="login-foot">
          {["cephfs", "gpfs", "wekafs", "k8s-quota"].map((s) => (
            <span key={s} className="login-chip"><i className="login-chip-dot" />{s}</span>
          ))}
        </footer>
      </div>
    </div>
  );
}

function LockIcon() {
  return (
    <svg className="login-ic" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="5" y="10.5" width="14" height="9.5" rx="2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}
