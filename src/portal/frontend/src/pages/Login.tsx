import { useEffect, useState } from "react";
import { auth, ApiError, type User } from "../api";

type Tab = "operator" | "user";
// operator tab sub-modes: normal login, or (token-gated) create account / reset pw.
type OpMode = "login" | "register" | "reset";

// friendly Korean messages for the BFF's error detail codes.
const ERR: Record<string, string> = {
  invalid_credentials: "아이디 또는 비밀번호가 올바르지 않습니다.",
  invalid_token: "비밀 토큰이 올바르지 않습니다.",
  account_token_not_configured: "계정 토큰이 서버에 설정되어 있지 않습니다. 관리자에게 문의하세요.",
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

export default function Login({ onLoggedIn }: { onLoggedIn: (u: User) => void }) {
  const [tab, setTab] = useState<Tab>("user");
  const [opMode, setOpMode] = useState<OpMode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // whether the token-gated create/reset flows are available (PORTAL_ADMIN_TOKEN set)
  const [tokenFlows, setTokenFlows] = useState(false);

  useEffect(() => {
    auth.accountTokenRequired().then((r) => setTokenFlows(r.available)).catch(() => {});
  }, []);

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
      const res = await auth.login(username, password);
      onLoggedIn(res.user);
    } catch (err) {
      setError(emsg(err, "로그인 실패"));
    } finally { setBusy(false); }
  }

  async function submitAd() {
    setError(null); setBusy(true);
    try {
      const res = await auth.loginAd();
      onLoggedIn(res.user);
    } catch (err) {
      setError(emsg(err, "로그인 실패"));
    } finally { setBusy(false); }
  }

  async function submitRegister(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setOk(null);
    if (password.length < 8) { setError("비밀번호는 최소 8자입니다."); return; }
    setBusy(true);
    try {
      await auth.register(username.trim(), password, token);
      setOk(`계정 '${username.trim()}' 이(가) 생성되었습니다. 로그인하세요.`);
      setOpMode("login");
      setPassword(""); setToken("");
    } catch (err) {
      setError(emsg(err, "계정 생성 실패"));
    } finally { setBusy(false); }
  }

  async function submitReset(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setOk(null);
    if (newPassword.length < 8) { setError("새 비밀번호는 최소 8자입니다."); return; }
    setBusy(true);
    try {
      await auth.resetPassword(username.trim(), newPassword, token);
      setOk(`'${username.trim()}' 의 비밀번호가 재설정되었습니다. 로그인하세요.`);
      setOpMode("login");
      setNewPassword(""); setToken("");
    } catch (err) {
      setError(emsg(err, "비밀번호 재설정 실패"));
    } finally { setBusy(false); }
  }

  return (
    <div className="centered">
      <div className="card">
        <h1 className="brand">DMS Portal</h1>

        <div className="tabs">
          <button className={tab === "user" ? "tab active" : "tab"}
            onClick={() => { setTab("user"); setError(null); setOk(null); }}>
            사용자 (AD)
          </button>
          <button className={tab === "operator" ? "tab active" : "tab"}
            onClick={() => { setTab("operator"); setError(null); }}>
            운영자 (ID/PW)
          </button>
        </div>

        {tab === "user" ? (
          <div className="form">
            <p className="muted">
              일반 사용자는 회사 AD 계정으로 로그인합니다. 현재는 더미 구현이며 추후
              연동 예정입니다.
            </p>
            <button className="primary" onClick={submitAd} disabled={busy}>
              {busy ? "로그인 중…" : "AD 계정으로 로그인 (더미)"}
            </button>
          </div>
        ) : (
          <>
            {/* sub-mode switch — create/reset only when the secret-token flows exist */}
            <div className="subtabs">
              <button className={"subtab" + (opMode === "login" ? " active" : "")}
                onClick={() => switchMode("login")}>로그인</button>
              {tokenFlows && (
                <>
                  <button className={"subtab" + (opMode === "register" ? " active" : "")}
                    onClick={() => switchMode("register")}>계정 만들기</button>
                  <button className={"subtab" + (opMode === "reset" ? " active" : "")}
                    onClick={() => switchMode("reset")}>비밀번호 재설정</button>
                </>
              )}
            </div>

            {opMode === "login" && (
              <form onSubmit={submitLogin} className="form">
                <p className="notice">
                  ID / 비밀번호 로그인은 <strong>운영자(operator) 전용</strong>입니다.
                  {tokenFlows && " 계정 생성·비밀번호 재설정은 비밀 토큰이 필요합니다."}
                </p>
                <label>아이디
                  <input autoFocus value={username} autoComplete="username"
                    onChange={(e) => setUsername(e.target.value)} />
                </label>
                <label>비밀번호
                  <input type="password" value={password} autoComplete="current-password"
                    onChange={(e) => setPassword(e.target.value)} />
                </label>
                <button className="primary" type="submit" disabled={busy}>
                  {busy ? "로그인 중…" : "운영자 로그인"}
                </button>
              </form>
            )}

            {opMode === "register" && tokenFlows && (
              <form onSubmit={submitRegister} className="form">
                <p className="notice">
                  <strong>운영용 비밀 토큰</strong>을 입력하면 새 운영자 계정을 만들 수 있습니다.
                  아이디는 <code>admin_</code> 로 시작해야 합니다 (예: admin_ops).
                </p>
                <label>아이디 (admin_...)
                  <input autoFocus value={username} autoComplete="off"
                    onChange={(e) => setUsername(e.target.value)} />
                </label>
                <label>비밀번호 <span className="muted">(최소 8자)</span>
                  <input type="password" value={password} autoComplete="new-password"
                    onChange={(e) => setPassword(e.target.value)} />
                </label>
                <label>운영용 비밀 토큰
                  <input type="password" value={token} autoComplete="off"
                    onChange={(e) => setToken(e.target.value)} />
                </label>
                <button className="primary" type="submit" disabled={busy || !username || !password || !token}>
                  {busy ? "생성 중…" : "계정 만들기"}
                </button>
              </form>
            )}

            {opMode === "reset" && tokenFlows && (
              <form onSubmit={submitReset} className="form">
                <p className="notice">
                  비밀번호는 복구할 수 없으므로 <strong>운영용 비밀 토큰</strong>으로
                  <strong> 재설정</strong>합니다. 아이디와 새 비밀번호를 입력하세요.
                </p>
                <label>아이디
                  <input autoFocus value={username} autoComplete="username"
                    onChange={(e) => setUsername(e.target.value)} />
                </label>
                <label>새 비밀번호 <span className="muted">(최소 8자)</span>
                  <input type="password" value={newPassword} autoComplete="new-password"
                    onChange={(e) => setNewPassword(e.target.value)} />
                </label>
                <label>운영용 비밀 토큰
                  <input type="password" value={token} autoComplete="off"
                    onChange={(e) => setToken(e.target.value)} />
                </label>
                <button className="primary" type="submit" disabled={busy || !username || !newPassword || !token}>
                  {busy ? "재설정 중…" : "비밀번호 재설정"}
                </button>
              </form>
            )}
          </>
        )}

        {ok && <p className="success">{ok}</p>}
        {error && <p className="error">{error}</p>}
      </div>
    </div>
  );
}
