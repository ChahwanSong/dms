import { useCallback, useEffect, useRef, useState } from "react";
import { operatorApi, ApiError, type AgentRolloutStatus } from "../../../api";

// Restart the RM·DM agent DaemonSets + watch the rollout. Agents read storages.json
// once at startup, so after adding/changing a storage they must restart to pick it
// up. DMS owns the agents and drives the rollout (in-cluster); the portal proxies.
const POLL_MS = 3000;
const MAX_TICKS = 40; // ~2min safety cap on the status poll

function converged(s: AgentRolloutStatus | null): boolean {
  return (
    !!s && s.daemonsets.length > 0 && s.daemonsets.every((d) => !d.rolling && !d.error)
  );
}
const emsg = (e: unknown) => (e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e));

export default function AgentRollout() {
  const [status, setStatus] = useState<AgentRolloutStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [rolling, setRolling] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const clearTimer = () => {
    if (timer.current) { window.clearInterval(timer.current); timer.current = null; }
  };
  const fetchStatus = useCallback(async () => {
    try {
      const s = await operatorApi.agents.rolloutStatus();
      setStatus(s);
      setErr(null);
      return s;
    } catch (e) {
      setErr(emsg(e));
      return null;
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    return clearTimer;
  }, [fetchStatus]);

  const restart = async () => {
    if (!window.confirm(
      "RM·DM 에이전트 DaemonSet을 재시작할까요?\n" +
      "스토리지 추가/변경을 에이전트가 반영하려면 필요합니다 (에이전트는 시작 시 목록을 한 번만 읽음)."
    )) return;
    setErr(null); setMsg(null); setBusy(true);
    try {
      const res = await operatorApi.agents.rolloutRestart();
      const failed = Object.keys(res.errors || {});
      setMsg(`재시작 요청됨: ${res.restarted.join(", ") || "-"}`);
      if (failed.length) setErr(`일부 DaemonSet 실패: ${failed.join(", ")}`);
      // watch until the rollout converges (or the safety cap)
      setRolling(true);
      clearTimer();
      let ticks = 0;
      timer.current = window.setInterval(async () => {
        ticks += 1;
        const s = await fetchStatus();
        if (converged(s) || ticks >= MAX_TICKS) {
          clearTimer();
          setRolling(false);
          if (converged(s)) setMsg("에이전트 재시작 완료.");
        }
      }, POLL_MS);
    } catch (e) {
      setErr(emsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="agent-rollout">
      <div className="agent-rollout-bar">
        <div className="agent-rollout-label">
          <b>에이전트 (RM · DM)</b>
          <span className="muted small"> 스토리지 추가/변경 후 재시작해야 에이전트가 새 목록을 반영합니다</span>
        </div>
        <div className="agent-rollout-actions">
          <button className="ghost mini" onClick={() => fetchStatus()} disabled={busy}>
            상태 새로고침
          </button>
          <button className="primary mini" onClick={restart} disabled={busy || rolling}>
            {rolling ? "재시작 중…" : "에이전트 재시작"}
          </button>
        </div>
      </div>

      {status && (
        <div className="agent-ds-grid">
          {status.daemonsets.map((d) => (
            <div key={d.name}
              className={"agent-ds " + (d.error ? "err" : d.rolling ? "rolling" : "ok")}>
              <span className="agent-ds-name mono">{d.name}</span>
              {d.error ? (
                <span className="agent-ds-err" title={d.error}>{d.error}</span>
              ) : (
                <>
                  <span className="agent-ds-stat">
                    준비 <b>{d.ready}/{d.desired}</b> · 갱신 <b>{d.updated}/{d.desired}</b>
                  </span>
                  <span className={"agent-ds-badge " + (d.rolling ? "rolling" : "ok")}>
                    {d.rolling ? "롤링 중…" : "완료"}
                  </span>
                </>
              )}
            </div>
          ))}
        </div>
      )}
      {msg && <p className="agent-rollout-msg">{msg}</p>}
      {err && <p className="agent-rollout-err">{err}</p>}
    </div>
  );
}
