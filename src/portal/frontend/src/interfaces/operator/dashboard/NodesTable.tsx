import { useEffect, useMemo, useState } from "react";
import { operatorApi, type AgentReport } from "../../../api";
import { fmtAgo } from "./helpers";
import Section from "./Section";

// One entry per worker NODE. RM/DM are separate agents on the same host, so we merge a
// node's role-reports into one record and surface only agent freshness — the "status".
// The detailed per-node resource graphs (CPU/메모리/네트워크) live in the dedicated
// 워커 노드 tab, so this dashboard section stays a brief status board: no metric
// columns, just each node's RM/DM health and how recently it reported.
interface NodeView {
  cluster_name: string;
  node_name: string;
  rm?: string; // freshness of the RM agent report (undefined = no RM agent)
  dm?: string; // freshness of the DM agent report
  reported_at?: string;
}

type Health = "ok" | "warn" | "off";

// node health rollup from its role freshness: ok = every present role Fresh,
// warn = some role Stale, off = no agent reporting at all.
function health(n: NodeView): Health {
  const roles = [n.rm, n.dm].filter(Boolean) as string[];
  if (roles.length === 0) return "off";
  return roles.every((s) => s === "Fresh") ? "ok" : "warn";
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

function buildNodes(reports: AgentReport[]): NodeView[] {
  const byNode = new Map<string, NodeView>();
  for (const r of reports || []) {
    const key = `${r.cluster_name}/${r.node_name}`;
    let n = byNode.get(key);
    if (!n) {
      n = { cluster_name: r.cluster_name, node_name: r.node_name };
      byNode.set(key, n);
    }
    if (r.worker_role === "RM") n.rm = r.freshness_status;
    else if (r.worker_role === "DM") n.dm = r.freshness_status;
    if (!n.reported_at || (r.reported_at || "") > n.reported_at) n.reported_at = r.reported_at;
  }
  return [...byNode.values()].sort((a, b) =>
    (a.cluster_name + a.node_name).localeCompare(b.cluster_name + b.node_name),
  );
}

export default function NodesTable({ onNavigate }: { onNavigate?: (section: string) => void }) {
  const [reports, setReports] = useState<AgentReport[]>([]);
  useEffect(() => {
    operatorApi.dashboard.nodes().then(setReports).catch(() => setReports([]));
  }, []);

  const nodes = useMemo(() => buildNodes(reports), [reports]);
  const ok = nodes.filter((n) => health(n) === "ok").length;
  const warn = nodes.filter((n) => health(n) === "warn").length;
  const off = nodes.filter((n) => health(n) === "off").length;

  const badge = (
    <span className="dash-section-actions">
      <span className="small nsc-summary">
        <b>{nodes.length}</b> 노드
        {ok > 0 && <> · <span className="ok-num">정상 {ok}</span></>}
        {warn > 0 && <> · <span className="warn-num">지연 {warn}</span></>}
        {off > 0 && <> · <span className="muted">없음 {off}</span></>}
      </span>
      {onNavigate && (
        <button type="button" className="linklike nsc-more" onClick={() => onNavigate("dashboard-nodes")}>
          자세히 →
        </button>
      )}
    </span>
  );

  return (
    <Section title="워커 노드" badge={badge} defaultOpen>
      {nodes.length === 0 ? (
        <p className="muted small">보고 중인 워커 노드가 없습니다.</p>
      ) : (
        <div className="node-status-grid">
          {nodes.map((n) => (
            <div key={`${n.cluster_name}/${n.node_name}`} className={`nsc nsc-${health(n)}`}>
              <span className="nsc-id">
                <span className="nsc-name mono">{n.node_name}</span>
                <span className="muted small nsc-sub">
                  {n.cluster_name}{n.reported_at ? ` · ${fmtAgo(n.reported_at)}` : ""}
                </span>
              </span>
              <span className="nsc-roles">
                {roleBadge("RM", n.rm)}
                {roleBadge("DM", n.dm)}
              </span>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}
