import { useEffect, useState } from "react";
import { operatorApi, type ControlHost } from "../../../api";
import Section from "./Section";
import InfoHint from "../../../components/InfoHint";

function yn(v?: boolean | null): string {
  return v == null ? "?" : v ? "✓" : "✗";
}

// CSI (agentless) storage mappings + their ResourceQuota mutation transport: the
// control host the RM worker reaches via (ssh-)kubectl, and whether it's reachable /
// permitted. Status-only (no resources). The transport explanation lives in the (i)
// next to 도달; per-row detail is on hover. Data from DMS sanity (mutation_observed).
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
            <th>control host</th>
            <th>
              도달{" "}
              <InfoHint label="도달 설명">
                <strong>ResourceQuota mutation transport</strong> — RM 워커가 (ssh-)kubectl로
                control host에 도달해 네임스페이스 ResourceQuota를 적용할 수 있는지를 나타냅니다.
                <br />도달(reachable)=경로 연결, 변경권한(can-i)=create/patch/delete 권한.
                각 행의 상세 사유는 <em>도달</em> 상태에 마우스를 올리면 표시됩니다.
              </InfoHint>
            </th>
            <th>변경권한 (can-i)</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={7} className="muted">CSI 매핑이 없습니다.</td></tr>
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
                    <span
                      className={`san ${r.reachable ? "san-ready" : "san-failed"}`}
                      title={r.detail || undefined}
                    >
                      {r.reachable == null ? "?" : r.reachable ? "Ready" : "Failed"}
                    </span>
                  </td>
                  <td data-label="변경권한">
                    <span className={`san ${r.can_mutate ? "san-ready" : "san-failed"}`}>
                      c{yn(p.create)} p{yn(p.patch)} d{yn(p.delete)}
                    </span>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </Section>
  );
}
