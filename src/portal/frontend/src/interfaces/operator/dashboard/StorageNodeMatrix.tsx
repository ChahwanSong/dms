import { useEffect, useMemo, useState } from "react";
import { operatorApi, type SnmCell, type SnmCluster, type SnmNode, type StorageNodeMatrix } from "../../../api";
import Section from "./Section";
import Loading from "../../../components/Loading";
import InfoHint from "../../../components/InfoHint";

const POLL_MS = 12000;

// A cell's visual kind, derived from the probed mount status. Designed to scale to
// ~100 nodes × ~50 storages: cells are plain colored tiles + a title tooltip (no
// per-cell React state), so the grid stays cheap even when fully populated.
type Kind = "ready" | "ro" | "missing" | "warn" | "none";
function kindOf(c?: SnmCell): Kind {
  if (!c || !c.status) return "none";           // storage not configured/reported on this node
  if (c.status === "Ready") return c.writable === false ? "ro" : "ready";
  if (c.status === "Missing") return "missing";
  return "warn";                                 // Degraded / Unreadable / anything else
}
const GLYPH: Record<Kind, string> = { ready: "✓", ro: "R", missing: "✗", warn: "!", none: "·" };
const isConfigured = (c?: SnmCell) => !!c && !!c.status;
const isReady = (c?: SnmCell) => c?.status === "Ready";

function cellTitle(storage: string, node: string, c?: SnmCell): string {
  if (!c || !c.status) return `${storage} @ ${node}\n미구성/미보고 — 이 노드는 해당 스토리지를 보고하지 않음`;
  const rw = `${c.readable ? "읽기✓" : "읽기✗"}·${c.writable ? "쓰기✓" : "쓰기✗"}`;
  const mp = c.is_mountpoint ? "마운트포인트" : "마운트 아님";
  return (
    `${storage} @ ${node}\n` +
    `상태: ${c.status}${c.mount_path ? ` · ${c.mount_path}` : ""}\n` +
    `${mp} · ${c.filesystem_type || "?"} · ${rw}` +
    (c.reason ? `\n사유: ${c.reason}` : "")
  );
}

function ratioClass(ready: number, total: number): string {
  if (total === 0) return "r-none";
  if (ready === total) return "r-ok";
  if (ready === 0) return "r-bad";
  return "r-partial";
}

// one node's role badges + freshness dot (compact, sticky first column)
function NodeMeta({ node }: { node: SnmNode }) {
  const stale = node.freshness && node.freshness !== "Fresh";
  return (
    <span className="snm-node-meta">
      {node.roles.map((r) => (
        <span key={r} className="snm-role" title={`${r} 에이전트`}>{r}</span>
      ))}
      <span className={`snm-fresh ${stale ? "stale" : "fresh"}`}
        title={`agent report: ${node.freshness || "?"}`}>{stale ? "○" : "●"}</span>
    </span>
  );
}

function ClusterMatrix({ cluster, showClusterName }: { cluster: SnmCluster; showClusterName: boolean }) {
  const [q, setQ] = useState("");
  const [issuesOnly, setIssuesOnly] = useState(false);

  const nodeHasIssue = (n: SnmNode) =>
    (n.freshness && n.freshness !== "Fresh") ||
    cluster.storages.some((s) => {
      const c = n.cells[s.storage_name];
      return isConfigured(c) && !isReady(c);
    });

  // rows filtered by node search + (optionally) issues-only
  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return cluster.nodes.filter((n) => {
      if (ql && !n.node_name.toLowerCase().includes(ql)) return false;
      if (issuesOnly && !nodeHasIssue(n)) return false;
      return true;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cluster, q, issuesOnly]);

  // columns: when issues-only, keep only storages that have an issue in the visible rows
  const cols = useMemo(() => {
    if (!issuesOnly) return cluster.storages;
    return cluster.storages.filter((s) =>
      rows.some((n) => {
        const c = n.cells[s.storage_name];
        return isConfigured(c) && !isReady(c);
      }),
    );
  }, [cluster.storages, rows, issuesOnly]);

  // per-storage ready ratio (over nodes that have it configured)
  const storageRatio = useMemo(() => {
    const m: Record<string, { ready: number; total: number }> = {};
    for (const s of cluster.storages) {
      let ready = 0, total = 0;
      for (const n of cluster.nodes) {
        const c = n.cells[s.storage_name];
        if (isConfigured(c)) { total++; if (isReady(c)) ready++; }
      }
      m[s.storage_name] = { ready, total };
    }
    return m;
  }, [cluster]);

  // overall issue count (mounts that should be Ready but aren't)
  const issueCount = useMemo(() => {
    let n = 0;
    for (const nd of cluster.nodes)
      for (const s of cluster.storages) {
        const c = nd.cells[s.storage_name];
        if (isConfigured(c) && !isReady(c)) n++;
      }
    return n;
  }, [cluster]);

  const filtered = rows.length !== cluster.nodes.length || cols.length !== cluster.storages.length;

  return (
    <div className="snm-cluster">
      <div className="snm-toolbar">
        {showClusterName && <span className="snm-cluster-name">클러스터 <b>{cluster.cluster_name}</b></span>}
        <span className="muted small">
          노드 {cluster.nodes.length} · 스토리지 {cluster.storages.length}
          {issueCount > 0
            ? <> · <span className="err-num">마운트 이슈 {issueCount}</span></>
            : <> · <span className="ok-num">전부 정상 ✅</span></>}
          {filtered && <> · 표시 {rows.length}×{cols.length}</>}
        </span>
        <span className="snm-toolbar-spacer" />
        <input className="snm-search" placeholder="노드 검색…" value={q}
          onChange={(e) => setQ(e.target.value)} />
        <button className={`attn-chip ${issuesOnly ? "on dom" : ""}`}
          onClick={() => setIssuesOnly((v) => !v)}
          title="이슈(미마운트/이상/stale)가 있는 노드·스토리지만 표시">이슈만</button>
      </div>

      {cols.length === 0 ? (
        <p className="muted small">표시할 스토리지가 없습니다{issuesOnly ? " (이슈 없음 ✅)" : ""}.</p>
      ) : rows.length === 0 ? (
        <p className="muted small">표시할 노드가 없습니다.</p>
      ) : (
        <div className="snm-scroll">
          <table className="snm">
            <thead>
              <tr>
                <th className="snm-corner">노드 \ 스토리지</th>
                {cols.map((s, i) => {
                  const r = storageRatio[s.storage_name];
                  return (
                    <th key={s.storage_name} className="snm-scol" title={`${s.storage_name} · ${s.backend_type}`}
                      // left columns paint above right ones so a rotated label that
                      // overflows right isn't covered by the next cell's opaque bg.
                      style={{ zIndex: cols.length - i + 5 }}>
                      <div className="snm-scol-box">
                        <span className="snm-slabel">{s.storage_name}</span>
                      </div>
                      <div className={`snm-sratio ${ratioClass(r.ready, r.total)}`}
                        title={`${r.ready}/${r.total} 노드 Ready · ${s.backend_type}`}>
                        {r.ready}/{r.total}
                      </div>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((n) => {
                let ready = 0, conf = 0;
                cluster.storages.forEach((s) => {
                  const c = n.cells[s.storage_name];
                  if (isConfigured(c)) { conf++; if (isReady(c)) ready++; }
                });
                const stale = n.freshness && n.freshness !== "Fresh";
                return (
                  <tr key={n.node_name} className={stale ? "snm-node-stale" : undefined}>
                    <th className="snm-ncol">
                      <span className="snm-nname mono" title={n.node_name}>{n.node_name}</span>
                      <NodeMeta node={n} />
                      <span className={`snm-nratio ${ratioClass(ready, conf)}`}
                        title={`이 노드: ${ready}/${conf} 스토리지 Ready`}>{ready}/{conf}</span>
                    </th>
                    {cols.map((s) => {
                      const c = n.cells[s.storage_name];
                      const k = kindOf(c);
                      return (
                        <td key={s.storage_name} className={`snm-cell k-${k}`}
                          title={cellTitle(s.storage_name, n.node_name, c)}>
                          {GLYPH[k]}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function StorageNodeMatrix() {
  const [data, setData] = useState<StorageNodeMatrix | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      operatorApi.dashboard.storageNodeMatrix()
        .then((d) => { if (alive) { setData(d); setErr(null); } })
        .catch((e) => { if (alive) setErr(e instanceof Error ? e.message : String(e)); })
        .finally(() => { if (alive) setLoading(false); });
    load();
    const id = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const clusters = data?.clusters || [];
  const nodeTotal = clusters.reduce((a, c) => a + c.nodes.length, 0);
  const stgTotal = clusters.reduce((a, c) => a + c.storages.length, 0);
  const badge = (
    <span className="muted small">
      {loading && !data ? "" : `(노드 ${nodeTotal} · 파일시스템 ${stgTotal})`}
    </span>
  );

  return (
    <Section
      title="스토리지 마운트 매트릭스 (파일시스템)"
      badge={
        <span className="snm-badge">
          {badge}
          <InfoHint label="매트릭스 설명">
            각 <strong>워커 노드</strong>에서 등록된 <strong>파일시스템 스토리지</strong>(cephfs/gpfs/wekafs)가
            제대로 <strong>마운트(Ready)</strong>됐는지 보여줍니다. CSI(agentless) 스토리지는 노드 마운트가
            없으므로 제외됩니다. 데이터는 노드 agent report의 마운트 프로브 결과(노드별 최신)입니다.
          </InfoHint>
        </span>
      }
      defaultOpen
    >
      {loading && !data ? (
        <Loading rows={4} />
      ) : err && !data ? (
        <div className="banner err">{err}</div>
      ) : clusters.length === 0 ? (
        <p className="muted">표시할 파일시스템 스토리지가 없습니다. (등록된 매핑이 없거나 모두 CSI)</p>
      ) : (
        <>
          <div className="snm-legend">
            <span className="snm-leg"><i className="snm-sw k-ready">✓</i> Ready</span>
            <span className="snm-leg"><i className="snm-sw k-ro">R</i> 읽기전용</span>
            <span className="snm-leg"><i className="snm-sw k-missing">✗</i> Missing</span>
            <span className="snm-leg"><i className="snm-sw k-warn">!</i> 이상</span>
            <span className="snm-leg"><i className="snm-sw k-none">·</i> 미구성</span>
          </div>
          {clusters.map((c) => (
            <ClusterMatrix key={c.cluster_name} cluster={c} showClusterName={clusters.length > 1} />
          ))}
          {(data?.errors?.reports || data?.errors?.mappings) && (
            <p className="muted small">일부 데이터 로드 실패: {data?.errors?.reports || data?.errors?.mappings}</p>
          )}
        </>
      )}
    </Section>
  );
}
