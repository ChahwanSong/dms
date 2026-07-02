import { useCallback, useEffect, useState } from "react";
import {
  operatorApi,
  ApiError,
  type OperatorAccount,
  type AccountAdminStatus,
} from "../../../api";
import Loading from "../../../components/Loading";

// friendly Korean messages for the BFF's error detail codes (falls back to raw).
const ERR: Record<string, string> = {
  current_password_incorrect: "현재 비밀번호가 올바르지 않습니다.",
  password_too_short: "비밀번호가 너무 짧습니다 (최소 8자).",
  username_exists: "이미 존재하는 아이디입니다.",
  invalid_username: "아이디 형식이 올바르지 않습니다 (소문자/숫자/밑줄, 'admin_' 접두어 필수).",
  invalid_admin_token: "관리자 토큰이 올바르지 않습니다.",
  admin_token_not_configured: "관리자 토큰이 서버에 설정되어 있지 않습니다.",
  cannot_disable_self: "본인 계정은 비활성화할 수 없습니다.",
  cannot_delete_self: "본인 계정은 삭제할 수 없습니다.",
  cannot_disable_last_active: "마지막 활성 계정은 비활성화할 수 없습니다.",
  cannot_delete_last_active: "마지막 활성 계정은 삭제할 수 없습니다.",
  operator_not_found: "해당 운영자를 찾을 수 없습니다.",
};
function emsg(e: unknown): string {
  if (e instanceof ApiError) {
    const d = typeof e.detail === "string" ? e.detail : e.message;
    for (const k of Object.keys(ERR)) if (d.startsWith(k)) return ERR[k];
    return d;
  }
  return e instanceof Error ? e.message : String(e);
}

export default function AccountsPanel({ me }: { me: string }) {
  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>계정 관리</h2>
      </div>
      <SelfPasswordCard />
      <AdminSection me={me} />
    </div>
  );
}

// ---- self-service: change own password -------------------------------------
function SelfPasswordCard() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const submit = async () => {
    setMsg(null);
    if (next.length < 8) { setMsg({ ok: false, text: "새 비밀번호는 최소 8자입니다." }); return; }
    if (next !== confirm) { setMsg({ ok: false, text: "새 비밀번호 확인이 일치하지 않습니다." }); return; }
    setBusy(true);
    try {
      await operatorApi.accounts.changePassword(cur, next);
      setMsg({ ok: true, text: "비밀번호가 변경되었습니다." });
      setCur(""); setNext(""); setConfirm("");
    } catch (e) {
      setMsg({ ok: false, text: emsg(e) });
    } finally { setBusy(false); }
  };

  return (
    <section className="acct-card">
      <h3>내 비밀번호 변경</h3>
      <div className="acct-form">
        <label>현재 비밀번호
          <input type="password" value={cur} autoComplete="current-password"
            onChange={(e) => setCur(e.target.value)} />
        </label>
        <label>새 비밀번호 <span className="muted small">(최소 8자)</span>
          <input type="password" value={next} autoComplete="new-password"
            onChange={(e) => setNext(e.target.value)} />
        </label>
        <label>새 비밀번호 확인
          <input type="password" value={confirm} autoComplete="new-password"
            onChange={(e) => setConfirm(e.target.value)} />
        </label>
        <div className="acct-actions">
          <button className="primary" disabled={busy || !cur || !next} onClick={submit}>변경</button>
          {msg && <span className={msg.ok ? "acct-ok" : "acct-err"}>{msg.text}</span>}
        </div>
      </div>
    </section>
  );
}

// ---- admin: manage operator accounts (unlocked with PORTAL_ADMIN_TOKEN) -----
function AdminSection({ me }: { me: string }) {
  const [status, setStatus] = useState<AccountAdminStatus | null>(null);
  const [token, setToken] = useState("");
  const [accounts, setAccounts] = useState<OperatorAccount[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // create form
  const [newUser, setNewUser] = useState("admin_");
  const [newPw, setNewPw] = useState("");

  const loadStatus = useCallback(async () => {
    try { setStatus(await operatorApi.accounts.status()); } catch (e) { setErr(emsg(e)); }
  }, []);
  const loadAccounts = useCallback(async () => {
    try { setAccounts(await operatorApi.accounts.list()); } catch (e) { setErr(emsg(e)); }
  }, []);

  useEffect(() => { loadStatus(); }, [loadStatus]);
  useEffect(() => { if (status?.unlocked) loadAccounts(); }, [status?.unlocked, loadAccounts]);

  const run = async (fn: () => Promise<unknown>, after?: () => void) => {
    setErr(null); setBusy(true);
    try { await fn(); after?.(); await loadAccounts(); }
    catch (e) { setErr(emsg(e)); }
    finally { setBusy(false); }
  };

  const unlock = async () => {
    setErr(null); setBusy(true);
    try {
      await operatorApi.accounts.unlock(token);
      setToken("");
      await loadStatus();
    } catch (e) { setErr(emsg(e)); }
    finally { setBusy(false); }
  };
  const lock = async () => {
    await operatorApi.accounts.lock();
    setAccounts([]);
    await loadStatus();
  };

  const create = () =>
    run(() => operatorApi.accounts.create(newUser.trim(), newPw), () => { setNewUser("admin_"); setNewPw(""); });
  const resetPw = (u: string) => {
    const pw = window.prompt(`'${u}' 의 새 비밀번호 (최소 8자):`, "");
    if (!pw) return;
    if (pw.length < 8) { setErr("비밀번호는 최소 8자입니다."); return; }
    run(() => operatorApi.accounts.resetPassword(u, pw));
  };
  const toggleActive = (a: OperatorAccount) => {
    const verb = a.is_active ? "비활성화" : "활성화";
    if (!window.confirm(`'${a.username}' 계정을 ${verb}할까요?`)) return;
    run(() => operatorApi.accounts.setActive(a.username, !a.is_active));
  };
  const remove = (u: string) => {
    if (!window.confirm(`'${u}' 계정을 삭제할까요? (되돌릴 수 없음)`)) return;
    run(() => operatorApi.accounts.remove(u));
  };

  if (status === null) return <section className="acct-card"><Loading rows={2} /></section>;

  if (!status.admin_available) {
    return (
      <section className="acct-card">
        <h3>운영자 관리</h3>
        <p className="muted small">
          관리자 토큰(<code>PORTAL_ADMIN_TOKEN</code>)이 서버에 설정되어 있지 않아 계정 관리를 사용할 수 없습니다.
          관리자에게 Secret 설정을 요청하세요. (내 비밀번호 변경은 위에서 가능합니다.)
        </p>
      </section>
    );
  }

  if (!status.unlocked) {
    return (
      <section className="acct-card">
        <h3>운영자 관리 <span className="chip tone-low">🔒 잠김</span></h3>
        <p className="muted small">
          운영자 계정 생성·초기화·비활성화·삭제는 <b>관리자 토큰</b>으로 잠금해제 후 사용합니다.
          토큰은 서버에서만 검증되며 브라우저에 저장되지 않습니다.
        </p>
        <div className="acct-form acct-inline">
          <input type="password" placeholder="관리자 토큰" value={token}
            autoComplete="off" onChange={(e) => setToken(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && token) unlock(); }} />
          <button className="primary" disabled={busy || !token} onClick={unlock}>잠금해제</button>
        </div>
        {err && <p className="acct-err">{err}</p>}
      </section>
    );
  }

  return (
    <section className="acct-card">
      <div className="acct-card-head">
        <h3>운영자 관리 <span className="chip tone-ok">🔓 관리자 모드</span></h3>
        <button className="ghost mini" onClick={lock}>잠금</button>
      </div>
      {err && <p className="acct-err">{err}</p>}

      <div className="acct-create acct-form acct-inline">
        <input placeholder="아이디 (admin_...)" value={newUser}
          onChange={(e) => setNewUser(e.target.value)} />
        <input type="password" placeholder="비밀번호 (최소 8자)" value={newPw}
          autoComplete="new-password" onChange={(e) => setNewPw(e.target.value)} />
        <button className="primary" disabled={busy || !newUser || !newPw} onClick={create}>운영자 생성</button>
      </div>
      <p className="muted small">신규 아이디는 <code>admin_</code> 접두어(소문자/숫자/밑줄)로 시작해야 합니다.</p>

      <table className="acct-table">
        <thead>
          <tr><th>아이디</th><th>상태</th><th>생성</th><th>작업</th></tr>
        </thead>
        <tbody>
          {accounts.map((a) => {
            const self = a.username === me;
            return (
              <tr key={a.username} className={a.is_active ? "" : "acct-inactive"}>
                <td className="mono">
                  {a.username}
                  {self && <span className="chip tone-low acct-self">나</span>}
                </td>
                <td>
                  <span className={"chip " + (a.is_active ? "tone-ok" : "tone-low")}>
                    {a.is_active ? "활성" : "비활성"}
                  </span>
                </td>
                <td className="muted small">
                  {a.created_by || "—"}
                  {a.created_at ? ` · ${new Date(a.created_at).toLocaleDateString()}` : ""}
                </td>
                <td className="acct-row-actions">
                  <button className="mini" disabled={busy} onClick={() => resetPw(a.username)}>비밀번호 초기화</button>
                  <button className="mini" disabled={busy || self} title={self ? "본인 계정은 불가" : ""}
                    onClick={() => toggleActive(a)}>{a.is_active ? "비활성화" : "활성화"}</button>
                  <button className="mini danger" disabled={busy || self} title={self ? "본인 계정은 불가" : ""}
                    onClick={() => remove(a.username)}>삭제</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
