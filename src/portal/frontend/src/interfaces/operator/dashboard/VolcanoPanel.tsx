import { useEffect, useState } from "react";
import { operatorApi, type VolcanoStatus, type VolcanoMetrics, type VolWindow } from "../../../api";
import Section from "./Section";

const TERMINAL = new Set(["Completed", "Succeeded", "Failed", "Aborted", "Terminated"]);
const WINDOWS = ["1h", "6h", "24h", "72h"];
const STAGES: [keyof VolWindow["latency"], string][] = [
  ["job_to_pod_s", "Job→Pod 생성"],
  ["pod_to_sched_s", "Pod→Scheduled"],
  ["run_s", "실행(시작→완료)"],
];

function healthCls(ready?: boolean | null, phase?: string): string {
  if (ready) return "san-ready";
  if (phase === "Succeeded") return "san-ready";
  return "san-failed";
}

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

// Volcano scheduler view (DMS control cluster): throughput/latency/top-offenders +
// queues, active VolcanoJobs, component health. Read-only via DMS /operations/volcano(+/metrics).
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
  const phaseCounts: Record<string, number> = {};
  for (const j of jobs) phaseCounts[j.phase || "?"] = (phaseCounts[j.phase || "?"] || 0) + 1;
  const active = jobs.filter((j) => !TERMINAL.has(j.phase || ""));
  const badge = <span className="muted small">(큐 {v.queues?.length ?? 0} · 잡 {jobs.length})</span>;
  const wd = m?.windows?.[win];
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
          <table className="grid"><thead><tr>
            <th>단계</th><th>평균</th><th>p50</th><th>p95</th><th>p99</th><th>n</th>
          </tr></thead><tbody>
            {STAGES.map(([key, label]) => {
              const s = wd.latency[key];
              return (
                <tr key={key}>
                  <td data-label="단계">{label}</td>
                  <td data-label="평균">{fmtDur(s.mean)}</td>
                  <td data-label="p50">{fmtDur(s.p50)}</td>
                  <td data-label="p95">{fmtDur(s.p95)}</td>
                  <td data-label="p99">{fmtDur(s.p99)}</td>
                  <td data-label="n" className="muted small">{s.n}</td>
                </tr>
              );
            })}
          </tbody></table>
        </>
      )}

      {/* Top offenders */}
      {m && (
        <>
          <h4 className="dash-sub">Top offenders</h4>
          <div className="top-offenders">
            <div>
              <div className="to-title">최장 Pending</div>
              {m.top.longest_pending.length ? m.top.longest_pending.map((j) => (
                <div key={j.name} className="to-row">
                  <span className="mono small">{j.name}</span>
                  <span className="err-num small">{fmtDur(j.pending_s)}</span>
                </div>
              )) : <div className="muted small">없음</div>}
            </div>
            <div>
              <div className="to-title">최다 실패</div>
              {m.top.most_failed.length ? m.top.most_failed.map((j) => (
                <div key={j.name} className="to-row">
                  <span className="mono small">{j.name}</span>
                  <span className="err-num small">실패 {j.failed}</span>
                </div>
              )) : <div className="muted small">없음</div>}
            </div>
            <div>
              <div className="to-title">최대 리소스 요청</div>
              {m.top.most_resources.length ? m.top.most_resources.map((j) => (
                <div key={j.name} className="to-row">
                  <span className="mono small">{j.name}</span>
                  <span className="muted small">{j.cpu_cores}c · {fmtGB(j.mem_bytes)} · {j.pods}p</span>
                </div>
              )) : <div className="muted small">없음</div>}
            </div>
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

      <h4 className="dash-sub">스케줄러 컴포넌트</h4>
      <table className="grid"><thead><tr>
        <th>pod</th><th>phase</th><th>ready</th><th>재시작</th>
      </tr></thead><tbody>
        {v.scheduler?.length ? v.scheduler.map((p) => (
          <tr key={p.name}>
            <td data-label="pod" className="mono small">{p.name}</td>
            <td data-label="phase"><span className={`san ${healthCls(p.ready, p.phase)}`}>{p.phase || "—"}</span></td>
            <td data-label="ready">{p.ready == null ? "—" : p.ready ? "✓" : "✗"}</td>
            <td data-label="재시작" className={p.restarts ? "err-num" : ""}>{p.restarts ?? 0}</td>
          </tr>
        )) : <tr><td colSpan={4} className="muted">없음</td></tr>}
      </tbody></table>

      <h4 className="dash-sub">
        잡 (활성 {active.length} / 전체 {jobs.length})
        <span className="muted small"> — {Object.entries(phaseCounts).map(([p, n]) => `${p}:${n}`).join(" · ") || "없음"}</span>
      </h4>
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
