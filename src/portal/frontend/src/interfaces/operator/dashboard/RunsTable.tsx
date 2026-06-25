import { useEffect, useState } from "react";
import { operatorApi, type RunRow } from "../../../api";
import { stateCls, RUN_STATE } from "./helpers";
import Section from "./Section";

export default function RunsTable() {
  const [active, setActive] = useState<RunRow[]>([]);
  const [stale, setStale] = useState<RunRow[]>([]);
  useEffect(() => {
    operatorApi.dashboard.runs().then((r) => {
      setActive(r.active.data || []);
      setStale(r.stale.data || []);
    }).catch(() => { setActive([]); setStale([]); });
  }, []);
  const rows = [...stale, ...active];
  const badge = stale.length > 0
    ? <span className="err-num">(stale {stale.length})</span>
    : <span className="muted small">({rows.length})</span>;
  return (
    <Section title="스케줄러 활동" badge={badge}>
      <table className="grid"><thead><tr>
        <th>run</th><th>worker</th><th>역할</th><th>상태</th><th>lease 남음</th><th>리소스</th>
      </tr></thead><tbody>
        {rows.length === 0 ? <tr><td colSpan={6} className="muted">활성 run 없음</td></tr> :
          rows.map((r) => (
            <tr key={r.run_id}>
              <td data-label="run" className="mono small">{r.run_id.slice(0, 12)}…</td>
              <td data-label="worker" className="mono small">{r.worker_id || "—"}</td>
              <td data-label="역할">{r.worker_role || "—"}</td>
              <td data-label="상태"><span className={`san ${stateCls(RUN_STATE, r.state)}`}>{r.state}</span></td>
              <td data-label="lease" className={r.lease_expiring_soon ? "err-num" : ""}>{r.lease_seconds_remaining ?? "—"}</td>
              <td data-label="리소스" className="mono small">{r.resource_key || "—"}</td>
            </tr>
          ))}
      </tbody></table>
    </Section>
  );
}
