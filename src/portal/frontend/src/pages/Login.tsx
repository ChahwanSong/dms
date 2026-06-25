import { useState } from "react";
import { auth, type User } from "../api";

type Tab = "operator" | "user";

export default function Login({ onLoggedIn }: { onLoggedIn: (u: User) => void }) {
  const [tab, setTab] = useState<Tab>("user");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submitOperator(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await auth.login(username, password);
      onLoggedIn(res.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "로그인 실패");
    } finally {
      setBusy(false);
    }
  }

  async function submitAd() {
    setError(null);
    setBusy(true);
    try {
      const res = await auth.loginAd();
      onLoggedIn(res.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "로그인 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="centered">
      <div className="card">
        <h1 className="brand">DMS Portal</h1>

        <div className="tabs">
          <button
            className={tab === "user" ? "tab active" : "tab"}
            onClick={() => setTab("user")}
          >
            사용자 (AD)
          </button>
          <button
            className={tab === "operator" ? "tab active" : "tab"}
            onClick={() => setTab("operator")}
          >
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
          <form onSubmit={submitOperator} className="form">
            <p className="notice">
              ID / 비밀번호 로그인은 <strong>운영자(operator) 전용</strong>입니다.
              운영자는 여러 개의 ID / 비밀번호를 가질 수 있습니다.
            </p>
            <label>
              아이디
              <input
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
              />
            </label>
            <label>
              비밀번호
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
            <button className="primary" type="submit" disabled={busy}>
              {busy ? "로그인 중…" : "운영자 로그인"}
            </button>
          </form>
        )}

        {error && <p className="error">{error}</p>}
      </div>
    </div>
  );
}
