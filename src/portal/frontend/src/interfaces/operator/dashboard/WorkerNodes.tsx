import { useCallback, useEffect, useState } from "react";
import { operatorApi, type AgentReport, type NodeSeries } from "../../../api";
import { fmtAgo } from "./helpers";
import MetricChart from "./MetricChart";
import InfoHint from "../../../components/InfoHint";

const WINDOWS: { label: string; sec: number }[] = [
  { label: "1시간", sec: 3600 },
  { label: "6시간", sec: 21600 },
  { label: "24시간", sec: 86400 },
  { label: "7일", sec: 604800 },
  { label: "30일", sec: 2592000 },
];

interface NodeMeta { rm?: string; dm?: string; mounts: string[]; reported_at?: string }

// merge a node's RM/DM role-reports into one meta record (roles + mounts + reported).
function metaByNode(reports: AgentReport[]): Map<string, NodeMeta> {
  const m = new Map<string, NodeMeta>();
  for (const r of reports || []) {
    const key = `${r.cluster_name}/${r.node_name}`;
    let n = m.get(key);
    if (!n) { n = { mounts: [] }; m.set(key, n); }
    if (r.worker_role === "RM") n.rm = r.freshness_status;
    else if (r.worker_role === "DM") n.dm = r.freshness_status;
    for (const mt of r.capability_summary?.mounts || []) if (!n.mounts.includes(mt)) n.mounts.push(mt);
    if (!n.reported_at || (r.reported_at || "") > n.reported_at) n.reported_at = r.reported_at;
  }
  return m;
}

function RoleBadge({ label, status }: { label: string; status?: string }) {
  if (!status) return <span className="role-badge role-off" title={`${label} 에이전트 없음`}>{label}<span className="role-dot">—</span></span>;
  const fresh = status === "Fresh";
  return (
    <span className={`role-badge ${fresh ? "role-fresh" : "role-stale"}`} title={`${label}: ${status}`}>
      {label}<span className="role-dot">{fresh ? "●" : "○"}</span>
    </span>
  );
}

function StatTile({ label, value, unit, digits = 0, hint }: {
  label: string; value?: number | null; unit: string; digits?: number; hint?: string;
}) {
  const cls = unit === "%" && value != null ? (value >= 90 ? "err-num" : value >= 75 ? "warn-num" : "ok-num") : "";
  return (
    <div className="wn-stat" title={hint}>
      <div className="wn-stat-v"><b className={cls}>{value != null ? value.toFixed(digits) : "—"}</b><span className="wn-stat-u">{unit}</span></div>
      <div className="wn-stat-l">{label}</div>
    </div>
  );
}

// bytes/sec → human bandwidth string (includes the /s unit).
function fmtBw(v: number): string {
  if (v < 1024) return `${Math.round(v)} B/s`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(v < 10240 ? 1 : 0)} KB/s`;
  if (v < 1024 * 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)} MB/s`;
  return `${(v / 1024 / 1024 / 1024).toFixed(2)} GB/s`;
}

function NetTile({ rx, tx }: { rx?: number | null; tx?: number | null }) {
  return (
    <div className="wn-stat" title="네트워크 대역폭 (수신↓ / 송신↑, 초당). 노드 물리 인터페이스 합계.">
      <div className="wn-net-v"><span style={{ color: "var(--accent)" }}>↓</span>{rx != null ? fmtBw(rx) : "—"}</div>
      <div className="wn-net-v"><span style={{ color: "var(--teal)" }}>↑</span>{tx != null ? fmtBw(tx) : "—"}</div>
      <div className="wn-stat-l">네트워크</div>
    </div>
  );
}

export default function WorkerNodes() {
  const [sec, setSec] = useState(21600);
  const [nodes, setNodes] = useState<NodeSeries[]>([]);
  const [meta, setMeta] = useState<Map<string, NodeMeta>>(new Map());
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const loadTs = useCallback(async (s: number) => {
    try {
      const r = await operatorApi.dashboard.nodeTimeseries(s);
      setNodes(r.nodes);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => { loadTs(sec); }, [sec, loadTs]);
  useEffect(() => {
    operatorApi.dashboard.nodes().then((r) => setMeta(metaByNode(r))).catch(() => {});
  }, []);

  return (
    <div className="inventory">
      <div className="inv-head">
        <h2 className="title-with-hint">
          워커 노드
          <InfoHint label="워커 노드 지표 설명">
            <strong className="hint-title">노드별 시스템 지표 추이</strong>
            <p className="hint-lead">각 노드의 아래 지표를 선택한 기간(1시간~30일)으로 보여줍니다.</p>
            <ul className="hint-list">
              <li><b>CPU · 메모리</b> — 사용률 (%)</li>
              <li><b>Load 1m</b> — 1분 load average. <em>백분율이 아니며</em>, CPU 코어 수 대비로 해석(코어 수보다 크면 과부하)</li>
              <li><b>네트워크</b> — 대역폭 <span className="mono">↓</span>수신 / <span className="mono">↑</span>송신 (초당 바이트)</li>
            </ul>
            <p className="hint-foot muted small">데이터는 포탈 배포 시점부터 최대 31일간 수집됩니다.</p>
          </InfoHint>
        </h2>
        <div className="inv-actions">
          <div className="ts-pills">
            {WINDOWS.map((w) => (
              <button key={w.sec} className={sec === w.sec ? "on" : ""} onClick={() => setSec(w.sec)}>{w.label}</button>
            ))}
          </div>
          <button className="ghost" onClick={() => loadTs(sec)}>새로고침</button>
        </div>
      </div>
      <p className="wn-sub muted small">
        노드별 <b>CPU·메모리</b>(%) · <b>Load</b>(1분) · <b>네트워크 대역폭</b>
        (<span className="mono">↓</span>수신 <span className="mono">↑</span>송신) 추이
        · 기간 {WINDOWS[0].label}~{WINDOWS[WINDOWS.length - 1].label} — 자세한 해석은 제목 옆 ⓘ
      </p>
      {err && <div className="banner err">{err}</div>}
      <div className="wn-grid">
        {nodes.map((n) => {
          const key = `${n.cluster_name}/${n.node_name}`;
          const md = meta.get(key);
          const c = n.current;
          return (
            <section key={key} className="ui-card wn-card">
              <div className="ui-card-hd">
                <h3><span className="mono">{n.node_name}</span> <span className="muted small">{n.cluster_name}</span></h3>
                <span className="wn-roles">
                  <RoleBadge label="RM" status={md?.rm} />
                  <RoleBadge label="DM" status={md?.dm} />
                  <span className="muted small">{md?.reported_at ? fmtAgo(md.reported_at) : ""}</span>
                </span>
              </div>
              <div className="ui-card-bd">
                <div className="wn-stats">
                  <StatTile label="CPU" value={c.cpu_percent} unit="%" hint="CPU 사용률 (%)" />
                  <StatTile label="메모리" value={c.mem_used_pct} unit="%" hint="메모리 사용률 (%)" />
                  <StatTile label="Load 1m" value={c.load1} unit="" digits={2} hint="1분 load average — CPU 코어 수 대비로 해석(백분율 아님)." />
                  <NetTile rx={c.rx_rate} tx={c.tx_rate} />
                </div>
                <div className="wn-charts">
                  <MetricChart series={n.cpu_series} label="CPU 사용률" unit="%" color="var(--accent)" sec={sec} max100
                    hint="CPU 사용률 (%)" />
                  <MetricChart series={n.mem_series} label="메모리 사용률" unit="%" color="var(--teal)" sec={sec} max100
                    hint="메모리 사용률 (%)" />
                  <MetricChart series={n.load_series} label="Load (1분)" unit="" color="var(--warn)" sec={sec} digits={2}
                    hint="1분 load average — 실행/대기 프로세스 평균 수. CPU 코어 수보다 크면 과부하(백분율 아님)." />
                  <MetricChart series={n.rx_series} series2={n.tx_series} label="네트워크 (↓수신 ↑송신)" unit=""
                    color="var(--accent)" color2="var(--teal)" sec={sec} fmt={fmtBw}
                    hint="노드 물리 인터페이스 네트워크 대역폭. ↓수신(rx)·↑송신(tx), 초당 바이트. 카운터 델타로 계산." />
                </div>
                {md && md.mounts.length > 0 && (
                  <div className="wn-mounts">
                    <div className="card-eyebrow">
                      마운트 <span className="wn-mount-cnt">{md.mounts.length}</span>
                    </div>
                    <div className="wn-mount-tags">
                      {md.mounts.map((mt) => (
                        <span key={mt} className="wn-mount-tag mono" title={mt}>{mt}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </section>
          );
        })}
        {loaded && nodes.length === 0 && !err && (
          <p className="muted">노드 메트릭 수집 중입니다 — 배포 시점부터 축적되며, 잠시 후 그래프가 나타납니다.</p>
        )}
      </div>
    </div>
  );
}
