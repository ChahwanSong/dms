import { useEffect, useState } from "react";
import { operatorApi, type VolcanoStatus, type VolcanoMetrics, type VolStageStat } from "../../../api";
import Section from "./Section";

const TERMINAL = new Set(["Completed", "Succeeded", "Failed", "Aborted", "Terminated"]);
const WINDOWS = ["1h", "6h", "24h", "72h"];
type LatKey = keyof VolcanoMetrics["windows"][string]["latency"];
const STAGES: [LatKey, string][] = [
  ["job_to_pod_s", "Job→Pod 생성"],
  ["pod_to_sched_s", "Pod→Scheduled"],
  ["run_s", "실행 (시작→완료)"],
];
const STATS: [keyof VolStageStat, string][] = [
  ["p50", "p50"],
  ["p95", "p95"],
  ["p99", "p99"],
  ["mean", "평균"],
];

// seconds → compact human duration.
function fmtDur(s: number | null): string {
  if (s == null) return "—";
  if (s < 90) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
  if (s < 5400) return `${(s / 60).toFixed(1)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}
function fmtGB(b: number): string {
  if (!b) return "0";
  const gb = b / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)}G` : `${Math.round(b / 1024 ** 2)}M`;
}

// Friendly identity for an opaque vcjob name like "dms-sync-execution-2377087d3d8a".
function jobLabel(name: string): { type: string; short: string } {
  const n = name || "";
  const parts = n.split("-");
  const short = (parts[parts.length - 1] || n).slice(0, 6);
  let type: string;
  if (n.includes("sync-execution")) type = "Sync 실행";
  else if (n.includes("sync-preview")) type = "Sync Preview";
  else if (n.includes("scan")) type = "Scan";
  else if (n.includes("-rm-") || n.includes("remove")) type = "Remove";
  else type = parts.slice(0, -1).join("-") || n;
  return { type, short };
}

// A ranked top-offenders list: rank · job identity · magnitude bar · value. Each row
// carries a detailed hover tooltip. `hint` clarifies what the value column means.
function Offenders({
  title,
  hint,
  rows,
  empty,
}: {
  title: string;
  hint?: string;
  rows: { name: string; sub?: string; tip?: string; value: number; barTone: string; valueText: string }[];
  empty: string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="off-card">
      <div className="off-title">{title}{hint && <span className="off-hint"> {hint}</span>}</div>
      {rows.length ? rows.map((r, i) => {
        const { type, short } = jobLabel(r.name);
        return (
          <div key={r.name} className="off-row" title={r.tip || r.name}>
            <span className="off-rank">{i + 1}</span>
            <div className="off-main">
              <div className="off-job">
                <span className="off-type">{type}</span>
                <span className="off-id">·{short}</span>
              </div>
              {r.sub && <div className="off-sub">{r.sub}</div>}
              <div className="off-bar">
                <span className={r.barTone} style={{ width: `${(r.value / max) * 100}%` }} />
              </div>
            </div>
            <span className="off-val">{r.valueText}</span>
          </div>
        );
      }) : <div className="muted small">{empty}</div>}
    </div>
  );
}

// Volcano scheduler view: throughput/latency (stacked by stage, rows per window) +
// top offenders + queues + active jobs. Component health lives in the summary cards.
export default function VolcanoPanel() {
  const [v, setV] = useState<VolcanoStatus | null>(null);
  const [m, setM] = useState<VolcanoMetrics | null>(null);
  const [stat, setStat] = useState<keyof VolStageStat>("p50");
  useEffect(() => {
    operatorApi.dashboard.volcano().then(setV).catch(() => setV(null));
    operatorApi.dashboard.volcanoMetrics().then(setM).catch(() => setM(null));
  }, []);
  if (!v) {
    return (
      <Section title="Volcano 스케줄러">
        <p className="muted">불러오는 중…</p>
      </Section>
    );
  }
  const jobs = v.jobs || [];
  const active = jobs.filter((j) => !TERMINAL.has(j.phase || ""));
  const badge = <span className="muted small">(큐 {v.queues?.length ?? 0} · 잡 {jobs.length})</span>;

  // queues: one combined running/pending/inqueue bar per queue (shared axis).
  const queues = v.queues || [];
  const qTotal = (q: (typeof queues)[number]) => (q.running || 0) + (q.pending || 0) + (q.inqueue || 0);
  const qMax = Math.max(1, ...queues.map(qTotal));

  // latency: one stacked bar per window, segments = the 3 stages for the chosen stat.
  const winRows = WINDOWS.map((w) => {
    const wd = m?.windows?.[w];
    const vals = STAGES.map(([key]) => (wd ? (wd.latency[key][stat] as number | null) ?? 0 : 0));
    const tp = wd?.throughput;
    return { w, vals, total: vals.reduce((a, b) => a + b, 0), tp };
  });
  const maxTotal = Math.max(1, ...winRows.map((r) => r.total));

  // storage route ("src→dst") + a multi-line hover detail for an offender job.
  type JobInfo = {
    name: string; tool?: string | null; queue?: string | null; phase?: string | null;
    src_storage?: string | null; dst_storage?: string | null;
    src_path?: string | null; dst_path?: string | null;
  };
  const stPath = (s?: string | null, p?: string | null) =>
    s ? `${s}${p && p !== "." ? `:${p}` : ""}` : null;
  const route = (j: JobInfo) =>
    j.src_storage || j.dst_storage ? `${j.src_storage || "?"} → ${j.dst_storage || "?"}` : null;
  const detailTip = (j: JobInfo) =>
    [
      j.name,
      j.tool ? `도구 ${j.tool}` : null,
      j.src_storage || j.dst_storage
        ? `경로 ${stPath(j.src_storage, j.src_path) || "?"} → ${stPath(j.dst_storage, j.dst_path) || "?"}`
        : null,
      j.queue ? `큐 ${j.queue}` : null,
      j.phase ? `상태 ${j.phase}` : null,
    ].filter(Boolean).join("\n");

  const pendRows = (m?.top.longest_pending || []).map((j) => ({
    name: j.name, sub: route(j) || j.queue || undefined, value: j.pending_s, barTone: "warn",
    valueText: fmtDur(j.pending_s),
    tip: `${detailTip(j)}\n대기 ${fmtDur(j.pending_s)}`,
  }));
  const anyCpu = (m?.top.most_resources || []).some((j) => (j.cpu_cores || 0) > 0);
  const resRows = (m?.top.most_resources || []).map((j) => ({
    name: j.name, sub: route(j) || j.queue || undefined,
    value: anyCpu ? j.cpu_cores || 0 : j.pods || 0, barTone: "accent",
    valueText: [
      `파드 ${j.pods}`,
      j.cpu_cores ? `vCPU ${j.cpu_cores}` : null,
      j.mem_bytes ? `메모리 ${fmtGB(j.mem_bytes)}` : null,
    ].filter(Boolean).join(" · "),
    tip: `${detailTip(j)}\n요청 리소스 — 파드 ${j.pods}${j.cpu_cores ? ` · vCPU ${j.cpu_cores}` : ""}${j.mem_bytes ? ` · 메모리 ${fmtGB(j.mem_bytes)}` : ""}`,
  }));

  return (
    <Section title="Volcano 스케줄러" badge={badge}>
      {(v.errors?.queues || v.errors?.jobs || v.errors?.scheduler) && (
        <div className="banner err">
          {["queues", "jobs", "scheduler"]
            .map((k) => (v.errors as Record<string, string | null | undefined>)[k] && `${k}: ${(v.errors as Record<string, string>)[k]}`)
            .filter(Boolean)
            .join(" · ")}
        </div>
      )}

      {/* 처리량 · 지연 — stacked stage bar per window; stat selector */}
      <div className="vol-metrics-head">
        <h4 className="dash-sub">처리량 · 지연</h4>
        <div className="win-toggle">
          {STATS.map(([k, label]) => (
            <button key={k} className={`mini ${stat === k ? "primary" : "ghost"}`} onClick={() => setStat(k)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {!m ? (
        <p className="muted small">불러오는 중…</p>
      ) : (
        <>
          <div className="lat-legend2">
            {STAGES.map(([key, label], i) => (
              <span key={key} className="leg"><span className={`leg-sw s${i}`} />{label}</span>
            ))}
          </div>
          <div className="lat-list">
            {winRows.map((r) => (
              <div key={r.w} className="lat-row2">
                <div className="lat-win">{r.w}</div>
                <div
                  className="lat-bar2"
                  title={STAGES.map(([, label], i) => `${label} ${fmtDur(r.vals[i])}`).join(" · ")}
                >
                  {r.total > 0 ? r.vals.map((val, i) => (
                    val > 0 ? <span key={i} className={`lat-seg s${i}`} style={{ width: `${(val / maxTotal) * 100}%` }} /> : null
                  )) : <span className="lat-seg-empty" />}
                </div>
                <div className="lat-vals">
                  총 <b>{fmtDur(r.total)}</b>
                  <span className="muted small">
                    {" · 완료 "}{r.tp?.completed ?? 0}
                    {r.tp?.failed ? <span className="err-num"> (실패 {r.tp.failed})</span> : null}
                  </span>
                </div>
              </div>
            ))}
          </div>
          <div className="lat-legend muted small">
            막대 = 잡 1건 생애주기 단계별 {STATS.find((s) => s[0] === stat)?.[1]} (공통 축) · hover로 단계값
          </div>
        </>
      )}

      {/* Top offenders (랭킹) */}
      {m && (
        <>
          <h4 className="dash-sub">Top offenders</h4>
          <div className="off-grid">
            <Offenders title="최장 Pending" hint="(대기 시간)" rows={pendRows} empty="대기 잡 없음" />
            <Offenders title="최대 리소스 요청" hint="(파드 · vCPU · 메모리)" rows={resRows} empty="없음" />
          </div>
        </>
      )}

      {/* 큐 — running/pending/inqueue combined in one bar per queue */}
      <h4 className="dash-sub">큐</h4>
      <div className="q-legend">
        <span className="leg"><span className="q-sw run" />running (실행 중)</span>
        <span className="leg"><span className="q-sw pend" />pending (대기)</span>
        <span className="leg"><span className="q-sw inq" />inqueue (입큐)</span>
      </div>
      <div className="q-list">
        {queues.length ? queues.map((q) => {
          const r = q.running || 0, p = q.pending || 0, iq = q.inqueue || 0, tot = r + p + iq;
          return (
            <div key={q.name} className="q-row">
              <div className="q-head">
                <span className="q-name mono" title={q.name}>{q.name}</span>
                <span className={`san ${q.state === "Open" ? "san-ready" : "san-degraded"}`}>{q.state || "—"}</span>
              </div>
              <div className="q-bar" title={`running ${r} · pending ${p} · inqueue ${iq}`}>
                {tot > 0 ? (
                  <>
                    {r > 0 && <span className="q-seg run" style={{ width: `${(r / qMax) * 100}%` }} />}
                    {p > 0 && <span className="q-seg pend" style={{ width: `${(p / qMax) * 100}%` }} />}
                    {iq > 0 && <span className="q-seg inq" style={{ width: `${(iq / qMax) * 100}%` }} />}
                  </>
                ) : <span className="q-seg-empty" />}
              </div>
              <div className="q-counts">
                <span className="q-c run"><b>{r}</b>running</span>
                <span className="q-c pend"><b>{p}</b>pending</span>
                <span className="q-c inq"><b>{iq}</b>inqueue</span>
              </div>
            </div>
          );
        }) : <div className="muted small">큐 없음</div>}
      </div>

      <h4 className="dash-sub">활성 잡 ({active.length})</h4>
      <table className="grid"><thead><tr>
        <th>job</th><th>큐</th><th>phase</th><th>running</th><th>pending</th>
      </tr></thead><tbody>
        {active.length ? active.map((j) => (
          <tr key={j.name}>
            <td data-label="job" className="mono small">{j.name}</td>
            <td data-label="큐" className="small">{j.queue || "—"}</td>
            <td data-label="phase"><span className="san san-degraded">{j.phase || "—"}</span></td>
            <td data-label="running">{j.running ?? 0}</td>
            <td data-label="pending" className={j.pending ? "err-num" : ""}>{j.pending ?? 0}</td>
          </tr>
        )) : <tr><td colSpan={5} className="muted">활성 잡 없음</td></tr>}
      </tbody></table>
    </Section>
  );
}
