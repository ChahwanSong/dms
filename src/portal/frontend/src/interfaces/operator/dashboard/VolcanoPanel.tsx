import { useEffect, useState } from "react";
import { operatorApi, type VolcanoStatus, type VolcanoMetrics, type VolStageStat } from "../../../api";
import Section from "./Section";

const TERMINAL = new Set(["Completed", "Succeeded", "Failed", "Aborted", "Terminated"]);
const WINDOWS = ["1h", "6h", "24h", "72h"];
const STAGES: [keyof VolcanoMetrics["windows"][string]["latency"], string][] = [
  ["job_to_pod_s", "Job→Pod 생성"],
  ["pod_to_sched_s", "Pod→Scheduled"],
  ["run_s", "실행 (시작→완료)"],
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

// Friendly identity for an opaque vcjob name like "dms-sync-execution-2377087d3d8a":
// an operation label + a short id tail, instead of the raw name.
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

// One stage's latency distribution as a single bar (0→p50 solid, p50→p95 mid,
// p95→p99 light), normalized to that stage's own p99 — so the bar shows the spread/
// tail, while the numbers carry absolute time. Mean is in the tooltip.
function LatBar({ label, s }: { label: string; s: VolStageStat }) {
  if (!s || s.n === 0 || s.p99 == null) {
    return (
      <div className="lat-row">
        <div className="lat-label">{label}</div>
        <div className="lat-bar lat-empty" />
        <div className="lat-vals muted small">데이터 없음</div>
      </div>
    );
  }
  const p50 = s.p50 ?? 0, p95 = s.p95 ?? 0, p99 = s.p99 ?? 0;
  const denom = p99 > 0 ? p99 : 1;
  const w = (a: number, b: number) => `${Math.max(0, ((b - a) / denom) * 100)}%`;
  return (
    <div className="lat-row">
      <div className="lat-label">{label}</div>
      <div
        className="lat-bar"
        title={`p50 ${fmtDur(p50)} · p95 ${fmtDur(p95)} · p99 ${fmtDur(p99)} · 평균 ${fmtDur(s.mean)} (n${s.n})`}
      >
        <span className="seg p50" style={{ width: w(0, p50) }} />
        <span className="seg p95" style={{ width: w(p50, p95) }} />
        <span className="seg p99" style={{ width: w(p95, p99) }} />
      </div>
      <div className="lat-vals">
        <b>{fmtDur(p50)}</b>
        <span className="muted small"> · {fmtDur(p95)} · {fmtDur(p99)}</span>
      </div>
    </div>
  );
}

// A ranked top-offenders list: rank · job identity · magnitude bar · value.
function Offenders({
  title,
  rows,
  empty,
}: {
  title: string;
  rows: { name: string; queue?: string; value: number; barTone: string; valueText: string }[];
  empty: string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="off-card">
      <div className="off-title">{title}</div>
      {rows.length ? rows.map((r, i) => {
        const { type, short } = jobLabel(r.name);
        return (
          <div key={r.name} className="off-row">
            <span className="off-rank">{i + 1}</span>
            <div className="off-main">
              <div className="off-job">
                <span className="off-type">{type}</span>
                <span className="off-id">·{short}</span>
                {r.queue && <span className="off-q">{r.queue}</span>}
              </div>
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

// Volcano scheduler view (DMS control cluster): throughput/latency/top-offenders +
// queues and active VolcanoJobs. Component (pod) health lives in the summary cards.
export default function VolcanoPanel() {
  const [v, setV] = useState<VolcanoStatus | null>(null);
  const [m, setM] = useState<VolcanoMetrics | null>(null);
  const [win, setWin] = useState<string>("6h");
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
  const wd = m?.windows?.[win];
  const pendRows = (m?.top.longest_pending || []).map((j) => ({
    name: j.name, queue: j.queue, value: j.pending_s, barTone: "warn",
    valueText: fmtDur(j.pending_s),
  }));
  const anyCpu = (m?.top.most_resources || []).some((j) => (j.cpu_cores || 0) > 0);
  const resRows = (m?.top.most_resources || []).map((j) => ({
    name: j.name, value: anyCpu ? j.cpu_cores || 0 : j.pods || 0, barTone: "accent",
    valueText: [
      `${j.pods}p`,
      j.cpu_cores ? `${j.cpu_cores}c` : null,
      j.mem_bytes ? fmtGB(j.mem_bytes) : null,
    ].filter(Boolean).join(" · "),
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

      {/* 처리량 · 지연 (윈도우 선택) */}
      <div className="vol-metrics-head">
        <h4 className="dash-sub">처리량 · 지연</h4>
        <div className="win-toggle">
          {WINDOWS.map((w) => (
            <button key={w} className={`mini ${win === w ? "primary" : "ghost"}`} onClick={() => setWin(w)}>
              {w}
            </button>
          ))}
        </div>
      </div>
      {!m ? (
        <p className="muted small">불러오는 중…</p>
      ) : !wd ? (
        <p className="muted small">데이터 없음</p>
      ) : (
        <>
          <p className="muted small">
            최근 {win} 완료 <b>{wd.throughput.completed}</b> (성공 {wd.throughput.succeeded} · 실패{" "}
            <b className={wd.throughput.failed ? "err-num" : ""}>{wd.throughput.failed}</b>)
          </p>
          <div className="lat-list">
            {STAGES.map(([key, label]) => <LatBar key={key} label={label} s={wd.latency[key]} />)}
          </div>
          <div className="lat-legend muted small">
            막대 = p50 → p95 → p99 분포 (단계별 자체 p99 기준) · 값 = p50 · p95 · p99 · 평균은 hover
          </div>
        </>
      )}

      {/* Top offenders (랭킹) */}
      {m && (
        <>
          <h4 className="dash-sub">Top offenders</h4>
          <div className="off-grid">
            <Offenders title="최장 Pending" rows={pendRows} empty="대기 잡 없음" />
            <Offenders title="최대 리소스 요청" rows={resRows} empty="없음" />
          </div>
        </>
      )}

      <h4 className="dash-sub">큐</h4>
      <table className="grid"><thead><tr>
        <th>큐</th><th>상태</th><th>running</th><th>pending</th><th>inqueue</th>
      </tr></thead><tbody>
        {v.queues?.length ? v.queues.map((q) => (
          <tr key={q.name}>
            <td data-label="큐" className="mono small">{q.name}</td>
            <td data-label="상태"><span className={`san ${q.state === "Open" ? "san-ready" : "san-degraded"}`}>{q.state || "—"}</span></td>
            <td data-label="running">{q.running ?? 0}</td>
            <td data-label="pending" className={q.pending ? "err-num" : ""}>{q.pending ?? 0}</td>
            <td data-label="inqueue">{q.inqueue ?? 0}</td>
          </tr>
        )) : <tr><td colSpan={5} className="muted">큐 없음</td></tr>}
      </tbody></table>

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
