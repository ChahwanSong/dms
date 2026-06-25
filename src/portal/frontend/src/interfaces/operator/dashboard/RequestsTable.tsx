import { useEffect, useState } from "react";
import { operatorApi, type DashRequest } from "../../../api";
import { stateCls, REQUEST_STATE, fmtAgo } from "./helpers";
import Section from "./Section";

const STATES = ["", "Running", "Pending", "ConfirmPending", "Succeeded", "Failed"];
const OPS = ["", "data.sync", "data.scan", "data.rm"];

export default function RequestsTable() {
  const [rows, setRows] = useState<DashRequest[]>([]);
  const [state, setState] = useState("");
  const [op, setOp] = useState("");
  useEffect(() => {
    operatorApi.dashboard.requests({ state: state || undefined, operation: op || undefined, limit: 100 })
      .then(setRows).catch(() => setRows([]));
  }, [state, op]);
  return (
    <Section title="요청" badge={<span className="muted small">({rows.length})</span>}>
      <div className="inv-actions dash-filters">
        <select value={state} onChange={(e) => setState(e.target.value)}>
          {STATES.map((s) => <option key={s} value={s}>{s || "모든 상태"}</option>)}
        </select>
        <select value={op} onChange={(e) => setOp(e.target.value)}>
          {OPS.map((o) => <option key={o} value={o}>{o || "모든 op"}</option>)}
        </select>
      </div>
      <table className="grid"><thead><tr>
        <th>job</th><th>op</th><th>storage</th><th>상태</th><th>tool</th><th>갱신</th>
      </tr></thead><tbody>
        {rows.length === 0 ? <tr><td colSpan={6} className="muted">없음</td></tr> :
          rows.map((j) => (
            <tr key={j.job_id}>
              <td data-label="job" className="mono small">{j.job_id.slice(0, 12)}…</td>
              <td data-label="op">{j.operation}</td>
              <td data-label="storage" className="small">{j.storage_name}</td>
              <td data-label="상태"><span className={`san ${stateCls(REQUEST_STATE, j.state)}`}>{j.state}</span></td>
              <td data-label="tool" className="small">{j.selected_tool || "—"}</td>
              <td data-label="갱신" className="muted small">{fmtAgo(j.updated_at)}</td>
            </tr>
          ))}
      </tbody></table>
    </Section>
  );
}
