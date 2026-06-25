import { useEffect, useState } from "react";
import { operatorApi, type AgentReport } from "../../../api";
import { fmtAgo, summarize } from "./helpers";
import Section from "./Section";

// usage %: color amber/red as it climbs; "—" when the metric is absent.
function pctCell(v?: number | null) {
  if (v == null) return <span className="muted">—</span>;
  const cls = v >= 90 ? "err-num" : v >= 75 ? "" : "ok-num";
  return <b className={cls}>{v}%</b>;
}

export default function NodesTable() {
  const [rows, setRows] = useState<AgentReport[]>([]);
  const [fresh, setFresh] = useState<string>("");
  useEffect(() => {
    operatorApi.dashboard.nodes(fresh || undefined).then(setRows).catch(() => setRows([]));
  }, [fresh]);
  return (
    <Section title="워커 노드" badge={<span className="muted small">({rows.length})</span>}>
      <div className="inv-actions dash-filters">
        <select value={fresh} onChange={(e) => setFresh(e.target.value)}>
          <option value="">전체</option><option value="Fresh">Fresh</option><option value="Stale">Stale</option>
        </select>
      </div>
      <table className="grid"><thead><tr>
        <th>클러스터</th><th>노드</th><th>역할</th><th>상태</th>
        <th>CPU</th><th>메모리</th><th>load</th><th>디스크</th>
        <th>마운트</th><th>툴</th><th>보고</th>
      </tr></thead><tbody>
        {rows.length === 0 ? <tr><td colSpan={11} className="muted">없음</td></tr> :
          rows.map((r) => {
            const os = r.os_metrics || {};
            return (
            <tr key={r.report_id}>
              <td data-label="클러스터">{r.cluster_name}</td>
              <td data-label="노드" className="mono small">{r.node_name}</td>
              <td data-label="역할">{r.worker_role}</td>
              <td data-label="상태"><span className={`san ${r.freshness_status === "Fresh" ? "san-ready" : "san-failed"}`}>{r.freshness_status}</span></td>
              <td data-label="CPU">{pctCell(os.cpu?.percent)}{os.cpu?.cores ? <span className="muted small"> /{os.cpu.cores}c</span> : null}</td>
              <td data-label="메모리">{pctCell(os.memory?.used_pct)}</td>
              <td data-label="load" className="small">{os.load?.load1 != null ? os.load.load1 : "—"}</td>
              <td data-label="디스크">{pctCell(os.disk?.used_pct)}{os.disk?.total_gb ? <span className="muted small"> /{os.disk.total_gb}G</span> : null}</td>
              <td data-label="마운트" className="small">{summarize(r.capability_summary?.mounts)}</td>
              <td data-label="툴" className="small">{summarize(r.capability_summary?.tools)}</td>
              <td data-label="보고" className="muted small">{fmtAgo(r.reported_at)}</td>
            </tr>
            );
          })}
      </tbody></table>
    </Section>
  );
}
