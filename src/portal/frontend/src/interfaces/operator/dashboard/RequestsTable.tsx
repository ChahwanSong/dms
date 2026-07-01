import { useEffect, useRef, useState } from "react";
import { operatorApi, type DashRequest } from "../../../api";
import { stateCls, REQUEST_STATE, fmtAgo } from "./helpers";
import Section from "./Section";

const STATES = ["", "Running", "Pending", "ConfirmPending", "Succeeded", "Failed"];
const OPS = ["", "data.sync", "data.scan", "data.rm"];
// Data jobs are growing history — fetch a capped page; the backend reports the
// exact total + a truncated flag (never a fetch-all).
const LIMIT = 500;

export default function RequestsTable(
  { defaultOpen = false, focusRequestId }: { defaultOpen?: boolean; focusRequestId?: string },
) {
  const [rows, setRows] = useState<DashRequest[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [state, setState] = useState("");
  const [op, setOp] = useState("");
  const focusRef = useRef<HTMLTableRowElement | null>(null);
  useEffect(() => {
    operatorApi.dashboard.requests({ state: state || undefined, operation: op || undefined, limit: LIMIT })
      .then((r) => { setRows(r.jobs); setTotal(r.total); setTruncated(r.truncated); })
      .catch(() => { setRows([]); setTotal(null); setTruncated(false); });
  }, [state, op]);
  // deep-link from 조치 필요 "상세": scroll the focused request row into view.
  useEffect(() => {
    if (focusRequestId && focusRef.current) {
      focusRef.current.scrollIntoView({ block: "center" });
    }
  }, [focusRequestId, rows]);
  const countLabel = total != null ? `표시 ${rows.length} / 전체 ${total}` : `표시 ${rows.length}`;
  return (
    <Section
      title="요청"
      badge={
        <span className="muted small">
          ({countLabel}){truncated && <>{" "}<span className="chip tone-warn">일부만 표시</span></>}
        </span>
      }
      defaultOpen={defaultOpen}
    >
      <div className="inv-actions dash-filters">
        <select value={state} onChange={(e) => setState(e.target.value)}>
          {STATES.map((s) => <option key={s} value={s}>{s || "모든 상태"}</option>)}
        </select>
        <select value={op} onChange={(e) => setOp(e.target.value)}>
          {OPS.map((o) => <option key={o} value={o}>{o || "모든 op"}</option>)}
        </select>
      </div>
      {truncated && (
        <div className="muted small">최신 {rows.length}건만 표시 — 상태/op 필터로 좁혀 보세요.</div>
      )}
      <table className="grid"><thead><tr>
        <th>job</th><th>op</th><th>storage</th><th>상태</th><th>tool</th><th>갱신</th>
      </tr></thead><tbody>
        {rows.length === 0 ? <tr><td colSpan={6} className="muted">없음</td></tr> :
          rows.map((j) => {
            const focused = !!focusRequestId && j.request_id === focusRequestId;
            return (
            <tr key={j.job_id} ref={focused ? focusRef : undefined}
              className={focused ? "row-focus" : undefined}>
              <td data-label="job" className="mono small">{j.job_id.slice(0, 12)}…</td>
              <td data-label="op">{j.operation}</td>
              <td data-label="storage" className="small">{j.storage_name}</td>
              <td data-label="상태"><span className={`san ${stateCls(REQUEST_STATE, j.state)}`}>{j.state}</span></td>
              <td data-label="tool" className="small">{j.selected_tool || "—"}</td>
              <td data-label="갱신" className="muted small">{fmtAgo(j.updated_at)}</td>
            </tr>
          );})}
      </tbody></table>
    </Section>
  );
}
