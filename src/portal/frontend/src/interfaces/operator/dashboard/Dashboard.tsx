import { useCallback, useEffect, useState } from "react";
import { operatorApi, type DashboardSummary } from "../../../api";
import { fmtTime } from "./helpers";
import StatusCards from "./StatusCards";
import NodesTable from "./NodesTable";
import RunsTable from "./RunsTable";
import RequestsTable from "./RequestsTable";
import AttentionPanel from "./AttentionPanel";

const POLL_MS = 7000;

export default function Dashboard() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<string>("");
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
  }, [auto, reload]);

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
      <AttentionPanel />
      <NodesTable />
      <RunsTable />
      <RequestsTable />
    </div>
  );
}
