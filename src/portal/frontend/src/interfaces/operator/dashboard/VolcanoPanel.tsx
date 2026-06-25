import { useEffect, useState } from "react";
import { operatorApi, type VolcanoStatus } from "../../../api";
import Section from "./Section";

const TERMINAL = new Set(["Completed", "Succeeded", "Failed", "Aborted", "Terminated"]);

function healthCls(ready?: boolean | null, phase?: string): string {
  if (ready) return "san-ready";
  if (phase === "Succeeded") return "san-ready";
  return "san-failed";
}

// Volcano scheduler view (DMS control cluster): queues, active VolcanoJobs, and
// the volcano-system component health. Read-only via DMS /operations/volcano.
export default function VolcanoPanel() {
  const [v, setV] = useState<VolcanoStatus | null>(null);
  useEffect(() => {
    operatorApi.dashboard.volcano().then(setV).catch(() => setV(null));
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
