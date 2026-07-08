import { useEffect, useMemo, useState } from "react";
import { operatorApi, type AgentReport, type NodeMetrics } from "../../../api";
import { fmtAgo } from "./helpers";
import Section from "./Section";
import Sparkline from "./Sparkline";

// One row per worker NODE. RM/DM are separate agents on the same host, so we merge
// a node's role-reports into one row (RM/DM status badges) and attach its node-level
// resource time-series from /node-metrics. Resources are host-level (RM/DM 무관).
interface NodeView {
  cluster_name: string;
  node_name: string;
  rm?: string; // freshness of the RM agent report (undefined = no RM agent)
  dm?: string; // freshness of the DM agent report
  mounts: string[];
  reported_at?: string;
  metrics?: NodeMetrics;
}

function joinAll(list?: string[]): string {
  return list && list.length ? list.join(", ") : "—";
}

function roleBadge(label: string, status?: string) {
  if (!status)
    return (
      <span className="role-badge role-off" title={`${label} 에이전트 없음`}>
        {label}<span className="role-dot">—</span>
      </span>
    );
  const fresh = status === "Fresh";
  return (
    <span className={`role-badge ${fresh ? "role-fresh" : "role-stale"}`} title={`${label}: ${status}`}>
      {label}<span className="role-dot">{fresh ? "●" : "○"}</span>
    </span>
  );
}

// current value %: amber/red as it climbs; "—" when absent.
function pct(v?: number | null) {
  if (v == null) return <span className="muted">—</span>;
  const cls = v >= 90 ? "err-num" : v >= 75 ? "" : "ok-num";
  return <b className={cls}>{v}%</b>;
}

function buildNodes(reports: AgentReport[], metrics: NodeMetrics[]): NodeView[] {
  const byNode = new Map<string, NodeView>();
  for (const r of reports || []) {
    const key = `${r.cluster_name}/${r.node_name}`;
    let n = byNode.get(key);
    if (!n) {
      n = { cluster_name: r.cluster_name, node_name: r.node_name, mounts: [] };
      byNode.set(key, n);
    }
    if (r.worker_role === "RM") n.rm = r.freshness_status;
    else if (r.worker_role === "DM") n.dm = r.freshness_status;
    for (const m of r.capability_summary?.mounts || []) if (!n.mounts.includes(m)) n.mounts.push(m);
    if (!n.reported_at || (r.reported_at || "") > n.reported_at) n.reported_at = r.reported_at;
  }
  const mByNode = new Map(metrics.map((m) => [`${m.cluster_name}/${m.node_name}`, m]));
  for (const [key, n] of byNode) n.metrics = mByNode.get(key);
  return [...byNode.values()].sort((a, b) =>
    (a.cluster_name + a.node_name).localeCompare(b.cluster_name + b.node_name),
  );
}

export default function NodesTable() {
  const [reports, setReports] = useState<AgentReport[]>([]);
  const [metrics, setMetrics] = useState<NodeMetrics[]>([]);
  const [fresh, setFresh] = useState<string>("");
  useEffect(() => {
    operatorApi.dashboard.nodes().then(setReports).catch(() => setReports([]));
    operatorApi.dashboard
      .nodeMetrics()
      .then((r) => setMetrics(r.nodes))
      .catch(() => setMetrics([]));
  }, []);

  const nodes = useMemo(() => buildNodes(reports, metrics), [reports, metrics]);
  const shown = fresh ? nodes.filter((n) => n.rm === fresh || n.dm === fresh) : nodes;

  return (
    <Section title="워커 노드" badge={<span className="muted small">({nodes.length})</span>} defaultOpen>
      <div className="inv-actions dash-filters">
        <select value={fresh} onChange={(e) => setFresh(e.target.value)}>
          <option value="">전체</option>
          <option value="Fresh">Fresh</option>
          <option value="Stale">Stale</option>
        </select>
        <span className="muted small">CPU·메모리: 최근 6시간 (1분 간격)</span>
      </div>
      <table className="grid node-grid">
        <thead>
          <tr>
            <th>노드</th>
            <th>RM</th>
            <th>DM</th>
            <th>CPU</th>
            <th>메모리</th>
            <th>load</th>
            <th>디스크</th>
            <th>마운트</th>
            <th>보고</th>
          </tr>
        </thead>
        <tbody>
          {shown.length === 0 ? (
            <tr>
              <td colSpan={9} className="muted">없음</td>
            </tr>
          ) : (
            shown.map((n) => {
              const c = n.metrics?.current || {};
              const gb = c.mem_total_kb ? Math.round(c.mem_total_kb / 1024 / 1024) : null;
              return (
                <tr key={`${n.cluster_name}/${n.node_name}`}>
                  <td data-label="노드">
                    <div className="node-id">
                      <span className="mono">{n.node_name}</span>
                      <span className="muted small">{n.cluster_name}</span>
                    </div>
                  </td>
                  <td data-label="RM">{roleBadge("RM", n.rm)}</td>
                  <td data-label="DM">{roleBadge("DM", n.dm)}</td>
                  <td data-label="CPU">
                    <div className="metric-cell">
                      <Sparkline series={n.metrics?.cpu_series || []} current={c.cpu_percent} />
                      <span className="metric-now">
                        {pct(c.cpu_percent)}
                        {c.cpu_cores ? <span className="muted small"> /{c.cpu_cores}c</span> : null}
                      </span>
                    </div>
                  </td>
                  <td data-label="메모리">
                    <div className="metric-cell">
                      <Sparkline series={n.metrics?.mem_series || []} current={c.mem_used_pct} />
                      <span className="metric-now">
                        {pct(c.mem_used_pct)}
                        {gb ? <span className="muted small"> /{gb}G</span> : null}
                      </span>
                    </div>
                  </td>
                  <td data-label="load" className="small">{c.load1 != null ? c.load1 : "—"}</td>
                  <td data-label="디스크">{pct(c.disk_used_pct)}</td>
                  <td data-label="마운트" className="small muted node-mounts" title={joinAll(n.mounts)}>{joinAll(n.mounts)}</td>
                  <td data-label="보고" className="muted small node-report">{fmtAgo(c.reported_at || n.reported_at)}</td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </Section>
  );
}
