import { useEffect, useState } from "react";
import { auth, ApiError, type User } from "../api";

type Tab = "operator" | "user";
// operator tab sub-modes: normal login, or (token-gated) create account / reset pw.
type OpMode = "login" | "register" | "reset";

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
};
function emsg(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    const d = typeof e.detail === "string" ? e.detail : e.message;
    for (const k of Object.keys(ERR)) if (d.startsWith(k)) return ERR[k];
    return d;
  }
  return e instanceof Error ? e.message : fallback;
}

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
  const [opMode, setOpMode] = useState<OpMode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  // 임시 더미 AD 로그인용 아이디(비우면 서버가 ad-user). 실제 AD 연동 시 제거/대체.
  const [adUser, setAdUser] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // whether the operator-token account flows are configured server-side.
  const [tokenFlows, setTokenFlows] = useState(false);

  useEffect(() => {
    auth.accountTokenRequired().then((r) => setTokenFlows(r.available)).catch(() => {});
  }, []);

  function pickTab(t: Tab) {
    setTab(t);
    setError(null);
    setOk(null);
  }
  function switchMode(m: OpMode) {
    setOpMode(m);
    setError(null);
    setPassword("");
    setNewPassword("");
    setToken("");
    if (m !== "login") setOk(null);
  }

  async function submitLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setBusy(true);
    try {
      onLoggedIn((await auth.login(username, password)).user);
    } catch (err) { setError(emsg(err, "로그인 실패")); } finally { setBusy(false); }
  }
  async function submitAd() {
    setError(null); setBusy(true);
    try {
      onLoggedIn((await auth.loginAd(adUser.trim() || undefined)).user);
    } catch (err) { setError(emsg(err, "로그인 실패")); } finally { setBusy(false); }
  }
  async function submitRegister(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setOk(null);
    if (password.length < 8) { setError("비밀번호는 최소 8자입니다."); return; }
    setBusy(true);
    try {
      await auth.register(username.trim(), password, token);
      setOk(`계정 '${username.trim()}' 을(를) 만들었습니다. 로그인하세요.`);
      setOpMode("login"); setPassword(""); setToken("");
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
      setOpMode("login"); setNewPassword(""); setToken("");
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

        {/* role: end users authenticate via AD; operators via id/password. */}
        <div className="login-roles" role="tablist" aria-label="로그인 방식">
          <button role="tab" aria-selected={tab === "user"}
            className={"login-role" + (tab === "user" ? " active" : "")}
            onClick={() => pickTab("user")}>
            <span className="login-role-t">사용자</span>
            <span className="login-role-s">회사 AD 계정</span>
          </button>
          <button role="tab" aria-selected={tab === "operator"}
            className={"login-role" + (tab === "operator" ? " active" : "")}
            onClick={() => pickTab("operator")}>
            <span className="login-role-t">운영자</span>
            <span className="login-role-s">ID · 비밀번호</span>
          </button>
        </div>

        {tab === "user" ? (
          <div className="login-form">
            <p className="login-note">
              일반 사용자는 <strong>회사 AD 계정</strong>으로 로그인합니다. 포탈에서 사용자 계정을
              따로 만들지 않습니다.{" "}
              <span className="login-dim">(현재는 <strong>임시 더미 로그인</strong> — 추후 AD 연동)</span>
            </p>
            <label className="login-field">
              <span className="login-lbl">
                아이디
                <span className="login-hint">임시: 원하는 사용자 아이디 (비우면 ad-user)</span>
              </span>
              <input
                value={adUser}
                autoComplete="off"
                placeholder="ad-user"
                onChange={(e) => setAdUser(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !busy) submitAd();
                }}
              />
            </label>
            <button className="login-primary" onClick={submitAd} disabled={busy}>
              {busy ? "로그인 중…" : "임시 더미 로그인"}
            </button>
          </div>
        ) : (
          <>
            <div className="login-subtabs">
              {(["login", "register", "reset"] as OpMode[]).map((m) => (
                <button key={m} className={"login-subtab" + (opMode === m ? " active" : "")}
                  onClick={() => switchMode(m)}>
                  {m === "login" ? "로그인" : m === "register" ? "계정 만들기" : "비밀번호 재설정"}
                </button>
              ))}
            </div>

            {opMode === "login" && (
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

            {opMode === "register" && (
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

            {opMode === "reset" && (
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

        {ok && <p className="login-ok">{ok}</p>}
        {error && <p className="login-error">{error}</p>}

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
