import { useEffect, useState } from "react";
import { operatorApi, type ControlHost } from "../../../api";
import Section from "./Section";

function yn(v?: boolean | null): string {
  return v == null ? "?" : v ? "✓" : "✗";
}

// CSI (agentless) storage mappings + their ResourceQuota mutation transport:
// the control host the RM worker reaches via (ssh-)kubectl, and whether it's
// reachable / permitted. Data from DMS sanity (mutation_observed) — read-only.
export default function ControlHostsTable() {
  const [rows, setRows] = useState<ControlHost[]>([]);
  useEffect(() => {
    operatorApi.dashboard.controlHosts().then(setRows).catch(() => setRows([]));
  }, []);
  return (
    <Section title="CSI control host" badge={<span className="muted small">({rows.length})</span>}>
      <table className="grid">
        <thead>
          <tr>
            <th>스토리지</th><th>클러스터</th><th>backend</th><th>mode</th>
            <th>control host</th><th>도달</th><th>변경권한 (can-i)</th><th>비고</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={8} className="muted">CSI 매핑이 없습니다.</td></tr>
          ) : (
            rows.map((r) => {
              const p = r.permissions || {};
              return (
                <tr key={`${r.cluster_name}/${r.storage_name}`}>
                  <td data-label="스토리지" className="mono small">{r.storage_name}</td>
                  <td data-label="클러스터">{r.cluster_name || "—"}</td>
                  <td data-label="backend" className="small">{r.backend_type || "—"}</td>
                  <td data-label="mode">{r.mode || "—"}</td>
                  <td data-label="control host" className="mono small">
                    {r.control_host || "— (local)"}
                  </td>
                  <td data-label="도달">
                    <span className={`san ${r.reachable ? "san-ready" : "san-failed"}`}>
                      {r.reachable == null ? "?" : r.reachable ? "Ready" : "Failed"}
                    </span>
                  </td>
                  <td data-label="변경권한">
                    <span className={`san ${r.can_mutate ? "san-ready" : "san-failed"}`}>
                      c{yn(p.create)} p{yn(p.patch)} d{yn(p.delete)}
                    </span>
                  </td>
                  <td data-label="비고" className="muted small">{r.detail || "—"}</td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </Section>
  );
}
