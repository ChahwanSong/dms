import { useEffect, useState, type ReactNode } from "react";
import {
  operatorApi,
  type VolcanoStatus,
  type VolcanoMetrics,
  type VolStageStat,
  type VolJobCard,
} from "../../../api";
import { fmtTime } from "./helpers";
import Section from "./Section";
import Loading from "../../../components/Loading";

const TERMINAL = new Set(["Completed", "Succeeded", "Failed", "Aborted", "Terminated"]);
const WINDOWS = ["1h", "6h", "24h", "72h"];
type LatKey = keyof VolcanoMetrics["windows"][string]["latency"];
const STAGES: [LatKey, string][] = [
  ["job_to_pod_s", "Job→Pod 생성"],
  ["pod_to_sched_s", "Pod→Scheduled"],
  ["sched_to_start_s", "Scheduled→실행시작 (이미지풀)"],
  ["run_s", "실행 (시작→완료)"],
];
const STATS: [keyof VolStageStat, string][] = [
  ["p50", "p50"],
  ["p95", "p95"],
  ["p99", "p99"],
  ["mean", "평균"],
];
const TOOL_KIND: Record<string, string> = {
  dsync: "Sync", nsync: "Sync", drm: "Remove", scan: "Scan",
};

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

// "storage:path" (path omitted when "." / empty).
const stPath = (s?: string | null, p?: string | null) =>
  s ? `${s}${p && p !== "." ? `:${p}` : ""}` : "—";

// 요청 종류: tool + phase (preview/execution).
function kindLabel(j: VolJobCard): string {
  const base = TOOL_KIND[j.tool || ""] || j.tool || "?";
  const tool = j.tool ? ` (${j.tool})` : "";
  const ph = j.phase_kind === "preview" ? "미리보기" : j.phase_kind === "execution" ? "실행" : null;
  return [base + tool, ph].filter(Boolean).join(" · ");
}

// Short route shown on the collapsed row (sync = src→dst storage; scan/rm = target).
function collapsedRoute(j: VolJobCard): string | null {
  if (j.src_storage || j.dst_storage) return `${j.src_storage || "?"} → ${j.dst_storage || "?"}`;
  if (j.scan_storage || j.scan_path) return `scan ${stPath(j.scan_storage, j.scan_path)}`;
  if (j.rm_storage || j.rm_path) return `rm ${stPath(j.rm_storage, j.rm_path)}`;
  return j.queue || null;
}

function Kv({ label, children, span, mono }: {
  label: string; children: ReactNode; span?: boolean; mono?: boolean;
}) {
  return (
    <div className={`spec-kv${span ? " span" : ""}`}>
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{children}</dd>
    </div>
  );
}

// Expanded detail for one offender job: requester, kind, route/target, resources, ids, timing.
function JobDetail({ job }: { job: VolJobCard }) {
  const hasSync = !!(job.src_storage || job.dst_storage);
  const hasScan = !!(job.scan_storage || job.scan_path);
  const hasRm = !!(job.rm_storage || job.rm_path);
  const res = [
    job.req_pods != null ? `파드 ${job.req_pods}` : null,
    job.req_cpu_cores ? `vCPU ${job.req_cpu_cores}` : null,
    job.req_mem_bytes ? fmtGB(job.req_mem_bytes) : null,
  ].filter(Boolean).join(" · ") || "—";
  return (
    <dl className="spec-grid off2-detail">
      <Kv label="요청자">{job.requester || "—"}</Kv>
      <Kv label="요청 종류">{kindLabel(job)}</Kv>
      <Kv label="큐">{job.queue || "—"}</Kv>
      <Kv label="상태">{job.phase || "—"}</Kv>
      {hasSync && <Kv label="원본" mono span>{stPath(job.src_storage, job.src_path)}</Kv>}
      {hasSync && <Kv label="대상" mono span>{stPath(job.dst_storage, job.dst_path)}</Kv>}
      {hasScan && <Kv label="대상 경로" mono span>{stPath(job.scan_storage, job.scan_path)}</Kv>}
      {hasRm && <Kv label="삭제 대상" mono span>{stPath(job.rm_storage, job.rm_path)}</Kv>}
      <Kv label="리소스">{res}</Kv>
      <Kv label="생성">{fmtTime(job.created_at || "")}</Kv>
      {job.started_at && <Kv label="시작">{fmtTime(job.started_at)}</Kv>}
      {job.finished_at && <Kv label="완료">{fmtTime(job.finished_at)}</Kv>}
      <Kv label="요청 ID" mono span>{job.request_id || "—"}</Kv>
      <Kv label="작업 ID" mono span>{job.data_job_id || "—"}</Kv>
    </dl>
  );
}

// A full-width offender list: each row collapses to rank · identity · route · value,
// and expands to the full job detail. `valueOf` maps a job to its magnitude + label.
function OffenderList<T extends VolJobCard>({ title, hint, items, valueOf, empty }: {
  title: string;
  hint: string;
  items: T[];
  valueOf: (j: T) => { text: string; v: number; tone: string; badge?: string };
  empty: string;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const vals = items.map(valueOf);
  const max = Math.max(1, ...vals.map((x) => x.v));
  return (
    <div className="off2">
      <div className="off2-title">{title}<span className="off-hint"> {hint}</span></div>
      {items.length ? items.map((j, i) => {
        const { type, short } = jobLabel(j.name);
        const val = vals[i];
        const isOpen = open === j.name;
        const sub = collapsedRoute(j);
        return (
          <div key={j.name} className={`off2-item${isOpen ? " open" : ""}`}>
            <button type="button" className="off2-row" aria-expanded={isOpen}
              onClick={() => setOpen(isOpen ? null : j.name)}>
              <span className="off2-rank">{i + 1}</span>
              <span className="off2-main">
                <span className="off2-head">
                  <span className="off2-type">{type}</span>
                  <span className="off2-id">·{short}</span>
                  {val.badge && <span className="chip tone-ok">{val.badge}</span>}
                </span>
                {sub && <span className="off2-sub">{sub}</span>}
                <span className="off2-bar">
                  <span className={val.tone} style={{ width: `${(val.v / max) * 100}%` }} />
                </span>
              </span>
              <span className="off2-val">{val.text}</span>
              <span className="off2-caret" aria-hidden="true">{isOpen ? "▾" : "▸"}</span>
            </button>
            {isOpen && <JobDetail job={j} />}
          </div>
        );
      }) : <div className="muted small">{empty}</div>}
    </div>
  );
}

// Volcano scheduler view: throughput/latency (stacked by stage, rows per window),
// longest-pending / longest-running offenders, queues, active jobs. Component health
// lives in the summary cards.
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
      <Section title="Volcano 스케줄러" defaultOpen>
        <Loading rows={3} />
      </Section>
    );
  }
  const jobs = v.jobs || [];
  const active = jobs.filter((j) => !TERMINAL.has(j.phase || ""));

  // queues: one combined running/pending/inqueue bar per queue (shared axis).
  const queues = v.queues || [];
  const qTotal = (q: (typeof queues)[number]) => (q.running || 0) + (q.pending || 0) + (q.inqueue || 0);
  const qMax = Math.max(1, ...queues.map(qTotal));

  // latency: one stacked bar per window, segments = the 4 stages for the chosen stat.
  const winRows = WINDOWS.map((w) => {
    const wd = m?.windows?.[w];
    const vals = STAGES.map(([key]) => (wd ? (wd.latency[key][stat] as number | null) ?? 0 : 0));
    const tp = wd?.throughput;
    return { w, vals, total: vals.reduce((a, b) => a + b, 0), tp };
  });
  const maxTotal = Math.max(1, ...winRows.map((r) => r.total));

  return (
    <Section title="Volcano 스케줄러" defaultOpen>
      {(v.errors?.queues || v.errors?.jobs || v.errors?.scheduler) && (
        <div className="banner err">
          {["queues", "jobs", "scheduler"]
            .map((k) => (v.errors as Record<string, string | null | undefined>)[k] && `${k}: ${(v.errors as Record<string, string>)[k]}`)
            .filter(Boolean)
            .join(" · ")}
        </div>
      )}

      <div className="vol-grid">
      {/* 처리량 · 지연 — stacked stage bar per window; stat selector */}
      <div className="vol-block">
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
          <Loading />
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
      </div>

      {/* 최장 Pending / 최장 Running — each its own expandable offender card */}
      {m && (
        <>
          <div className="vol-block">
            <OffenderList
              title="최장 Pending" hint="(가장 오래 대기한 잡 · 펼치면 상세)"
              items={m.top.longest_pending}
              valueOf={(j) => ({ text: fmtDur(j.pending_s), v: j.pending_s, tone: "warn" })}
              empty="대기 잡 없음"
            />
          </div>
          <div className="vol-block">
            <OffenderList
              title="최장 Running" hint="(가장 오래 실행된 잡 · 펼치면 상세)"
              items={m.top.longest_running}
              valueOf={(j) => ({
                text: fmtDur(j.running_s), v: j.running_s,
                tone: j.active ? "live" : "", badge: j.active ? "진행 중" : undefined,
              })}
              empty="실행 잡 없음"
            />
          </div>
        </>
      )}

      {/* Scheduler Queue — running/pending/in-queue combined in one bar per queue */}
      <div className="vol-block">
        <h4 className="dash-sub">Scheduler Queue</h4>
        <div className="q-legend">
          <span className="leg"><span className="q-sw run" />running</span>
          <span className="leg"><span className="q-sw pend" />pending</span>
          <span className="leg"><span className="q-sw inq" />in-queue</span>
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
                  <span className="q-c inq"><b>{iq}</b>in-queue</span>
                </div>
              </div>
            );
          }) : <div className="muted small">큐 없음</div>}
        </div>
      </div>

      <div className="vol-block vol-full">
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
      </div>
      </div>
    </Section>
  );
}
