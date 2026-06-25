import { useCallback, useEffect, useRef, useState } from "react";
import { operatorApi, type DashboardSummary } from "../../../api";
import { fmtTime } from "./helpers";
import StatusCards from "./StatusCards";

const POLL_MS = 7000;

export default function Dashboard() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<string>("");
  const tick = useRef(0);

  const reload = useCallback(async () => {
    try {
      const s = await operatorApi.dashboard.summary();
      setSummary(s);
      setError(null);
      setUpdatedAt(new Date().toISOString());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    if (!auto) return;
    const id = setInterval(reload, POLL_MS);
    return () => clearInterval(id);
  }, [auto, reload, tick.current]);

  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>종합 대시보드</h2>
        <div className="inv-actions">
          <span className="muted small">갱신 {fmtTime(updatedAt)}</span>
          <button className="ghost" onClick={() => setAuto((v) => !v)}>
            {auto ? "자동새로고침 ⏸" : "자동새로고침 ▶"}
          </button>
          <button className="ghost" onClick={reload}>새로고침</button>
        </div>
      </div>
      {error && <div className="banner err">{error}</div>}
      <StatusCards summary={summary} />
      {/* Task 6: NodesTable / RunsTable / JobsTable / AttentionPanel */}
    </div>
  );
}
