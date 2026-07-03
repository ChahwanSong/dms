import { useCallback, useEffect, useState } from "react";
import {
  operatorApi,
  type BackupRequest,
  type JobDetail,
  type JobLogs,
  type RmJob,
  type ScanRequest,
  type SyncJob,
} from "../../../api";
import { SpecGrid, type KV } from "../../../components/SpecGrid";

// Per-item live detail + log-tail popup (backup + scan + sync). Given a portal
// request/job row it fetches the FULLER live DMS job dict (state, result_summary
// incl file_size_histogram, preflight, identifiers, artifact/log URIs) and tails
// the launcher pod logs, polling every 4s WHILE the job is non-terminal. Read-only;
// reuses the shared .modal / SpecGrid chrome. Backup/scan key off a (batchId,
// request); sync/rm jobs are top-level (job id only).

type Props =
  | { kind: "backup"; batchId: string; request: BackupRequest; onClose: () => void }
  | { kind: "scan"; batchId: string; request: ScanRequest; onClose: () => void }
  | { kind: "sync"; request: SyncJob; onClose: () => void }
  | { kind: "rm"; request: RmJob; onClose: () => void };

// DMS DataJobState values that are settled (stop polling). Mirrors the
// orchestrators' terminal sets.
const DMS_TERMINAL = new Set([
  "Succeeded",
  "Failed",
  "PreflightFailed",
  "PreviewExpired",
  "Cancelled",
  "TimedOut",
]);
// Portal request states that are still in flight (a job may appear / keep moving)
// even before the orchestrator reconciles a dms_job_id onto the row.
const IN_FLIGHT_PORTAL = new Set([
  "preview_pending",
  "preview_ready",
  "approved",
  "running",
]);

const SUMMARY_LABELS: Record<string, string> = {
  file_count: "파일",
  directory_count: "디렉터리",
  total_bytes: "크기",
  error_count: "오류",
  selected_tool: "도구",
  process_count: "프로세스",
  worker_pod_count: "워커 파드",
  processes_per_node: "노드당 프로세스",
  source_node_count: "출발 노드",
  destination_node_count: "대상 노드",
  scan_root: "scan_root",
  operation: "작업",
  dry_run: "dry-run",
};

function fmtBytes(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || Number.isInteger(v) ? 0 : 1)} ${units[i]}`;
}

function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

// DMS volcano_job_ref is a STRUCTURED ref ({adapter, job_ref}), not a bare
// string. Render the job_ref URI (the meaningful part); never the raw object.
function volcanoRefStr(v: unknown): string | null {
  if (v == null || v === "") return null;
  if (typeof v === "string") return v;
  const r = asRecord(v);
  if (r) return typeof r.job_ref === "string" ? r.job_ref : JSON.stringify(r);
  return String(v);
}

// result_summary nesting varies by tool: dscan/dsync nest scalar metrics under
// `summary`; nsync executions nest under `execution.summary`. Fall back to the
// top-level record so something is always shown.
function pickSummary(
  rs: Record<string, unknown> | null | undefined,
): Record<string, unknown> | null {
  if (!rs) return null;
  const exec = asRecord(rs.execution);
  return asRecord(rs.summary) ?? asRecord(exec?.summary) ?? rs;
}

function pickHistogram(
  rs: Record<string, unknown> | null | undefined,
): Record<string, unknown>[] | null {
  if (!rs) return null;
  const summary = pickSummary(rs);
  const candidates = [summary?.file_size_histogram, rs.file_size_histogram];
  for (const c of candidates) {
    if (Array.isArray(c) && c.length) {
      return c.map((x) => asRecord(x) ?? {}).filter((x) => Object.keys(x).length > 0);
    }
  }
  return null;
}

function summaryItems(summary: Record<string, unknown>): KV[] {
  const items: KV[] = [];
  for (const [k, v] of Object.entries(summary)) {
    if (v == null) continue;
    if (typeof v === "object") continue; // arrays / nested objects rendered elsewhere
    const label = SUMMARY_LABELS[k] ?? k;
    let value: string;
    if (typeof v === "boolean") value = v ? "켜짐" : "꺼짐";
    else if (typeof v === "number")
      value = k.endsWith("_bytes") ? fmtBytes(v) : v.toLocaleString();
    else value = String(v);
    const span = typeof v === "string" && value.length > 24;
    items.push({ label, value, mono: span, span });
  }
  return items;
}

function histLabel(b: Record<string, unknown>): string {
  if (typeof b.bucket === "string") return b.bucket;
  const lo = b.min_bytes ?? b.min;
  const hi = b.max_bytes ?? b.max;
  if (typeof lo === "number" || typeof hi === "number")
    return `${typeof lo === "number" ? fmtBytes(lo) : "0"} – ${
      typeof hi === "number" ? fmtBytes(hi) : "∞"
    }`;
  return "—";
}

export default function JobDetailModal(props: Props) {
  const { kind, request, onClose } = props;
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [logs, setLogs] = useState<JobLogs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [d, l] =
        props.kind === "sync"
          ? await Promise.all([
              operatorApi.sync.job(props.request.id),
              operatorApi.sync.logs(props.request.id),
            ])
          : props.kind === "rm"
          ? await Promise.all([
              operatorApi.rm.job(props.request.id),
              operatorApi.rm.logs(props.request.id),
            ])
          : await Promise.all([
              (props.kind === "backup" ? operatorApi.backup.job : operatorApi.scan.job)(
                props.batchId,
                props.request.id,
              ),
              (props.kind === "backup" ? operatorApi.backup.logs : operatorApi.scan.logs)(
                props.batchId,
                props.request.id,
              ),
            ]);
      setDetail(d);
      setLogs(l);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [props]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while the live job is non-terminal (or the portal row is still in flight
  // and a job has not yet been reconciled). Stops on terminal / settled.
  const liveState = typeof detail?.state === "string" ? detail.state : null;
  const polling = liveState
    ? !DMS_TERMINAL.has(liveState)
    : IN_FLIGHT_PORTAL.has(request.state);
  useEffect(() => {
    if (!polling) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [polling, load]);

  const subtitle =
    kind === "scan"
      ? `${request.storage}:${request.path}`
      : kind === "rm"
      ? `${request.target_storage}:${request.target_path}`
      : `${request.src_storage}:${request.src_path} → ${request.dst_storage}:${request.dst_path}`;

  // --- live identifiers / state grid ---
  const live: KV[] = [];
  live.push({
    label: "상태 (live)",
    value: liveState ?? request.state,
    mono: true,
  });
  if (detail?.selected_tool) live.push({ label: "도구", value: detail.selected_tool });
  if (request.dms_job_id)
    live.push({ label: "job id", value: request.dms_job_id, mono: true, span: true });
  if (request.dms_request_id)
    live.push({
      label: "request id",
      value: request.dms_request_id,
      mono: true,
      span: true,
    });
  const volcanoRef = volcanoRefStr(detail?.volcano_job_ref);
  if (volcanoRef)
    live.push({
      label: "volcano_job_ref",
      value: volcanoRef,
      mono: true,
      span: true,
    });
  if (detail?.artifact_uri)
    live.push({ label: "artifact", value: detail.artifact_uri, mono: true, span: true });
  if (detail?.log_uri)
    live.push({ label: "log_uri", value: detail.log_uri, mono: true, span: true });
  if (detail?.created_at) live.push({ label: "생성", value: fmtTime(detail.created_at) });
  if (detail?.started_at) live.push({ label: "시작", value: fmtTime(detail.started_at) });
  if (detail?.finished_at) live.push({ label: "종료", value: fmtTime(detail.finished_at) });

  const preflight = asRecord(detail?.preflight_result);
  const preflightStatus =
    preflight && typeof preflight.status === "string" ? preflight.status : null;
  const preflightReason =
    preflight && typeof preflight.reason === "string" ? preflight.reason : null;
  // DMS preflight: status "Ready" (reason *_passed) means it PASSED — show it
  // green/neutral, not the alarming red error box. Anything else is a real
  // rejection and stays red.
  const preflightPassed =
    preflightStatus === "Ready" ||
    (preflightStatus == null && !!preflightReason && preflightReason.endsWith("_passed"));

  const rs = asRecord(detail?.result_summary);
  const summary = pickSummary(rs);
  const summaryKv = summary ? summaryItems(summary) : [];
  const hist = pickHistogram(rs);
  const noJob = detail?.available === false;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>
            작업 상세 / 로그
            {polling && <span className="chip tone-low">실시간</span>}
          </h3>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button className="ghost mini" onClick={load} disabled={loading}>
              새로고침
            </button>
            <button className="ghost" onClick={onClose}>
              닫기
            </button>
          </div>
        </div>

        <div className="job-modal-body">
          <div className="req-route" style={{ marginBottom: "0.75rem" }}>
            <code className="small">{subtitle}</code>
          </div>

          {error && <div className="banner err">{error}</div>}
          {loading && !detail && <div className="muted small">불러오는 중…</div>}

          {noJob && (
            <div className="banner">
              {detail?.note || "아직 DMS 작업이 시작되지 않았습니다."}
            </div>
          )}

          <section className="job-sec">
            <h4>실행 상태</h4>
            <SpecGrid items={live} />
          </section>

          {(preflightStatus || preflightReason) && (
            <section className="job-sec">
              <h4>
                Preflight
                {preflightStatus && (
                  <span className={`chip ${preflightPassed ? "tone-ok" : "tone-danger"}`}>
                    {preflightPassed ? "통과" : preflightStatus}
                  </span>
                )}
              </h4>
              {preflightReason &&
                (preflightPassed ? (
                  <div className="muted small">{preflightReason}</div>
                ) : (
                  <div className="req-error">{preflightReason}</div>
                ))}
            </section>
          )}

          {summaryKv.length > 0 && (
            <section className="job-sec">
              <h4>result_summary</h4>
              <SpecGrid items={summaryKv} />
            </section>
          )}

          {hist && hist.length > 0 && (
            <section className="job-sec">
              <h4>파일 크기 분포 (file_size_histogram)</h4>
              <ul className="hist-list">
                {hist.map((b, i) => {
                  const count = typeof b.count === "number" ? b.count : null;
                  const bytes = typeof b.bytes === "number" ? b.bytes : null;
                  return (
                    <li key={i}>
                      <span className="mono small">{histLabel(b)}</span>
                      <span className="muted small">
                        {count != null ? `${count.toLocaleString()}개` : ""}
                        {count != null && bytes != null ? " · " : ""}
                        {bytes != null ? fmtBytes(bytes) : ""}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}

          {rs && (
            <details className="job-raw">
              <summary>전체 result_summary (원본 JSON)</summary>
              <pre className="log-panel">{JSON.stringify(rs, null, 2)}</pre>
            </details>
          )}

          <section className="job-sec">
            <h4>로그 (launcher pod tail)</h4>
            {logs?.pods && logs.pods.length > 0 && (
              <div className="job-pods">
                {logs.pods.map((p, i) => (
                  <span key={i} className="chip" title={p.node_name || undefined}>
                    {p.name || "pod"}
                    {p.role ? ` · ${p.role}` : ""}
                    {p.phase ? ` · ${p.phase}` : ""}
                  </span>
                ))}
              </div>
            )}
            {logs && logs.available === false ? (
              <div className="muted small">
                {logs.note || "로그 없음 (스케줄 전이거나 파드가 종료됨)."}
              </div>
            ) : logs && logs.logs ? (
              <pre className="log-panel">{logs.logs}</pre>
            ) : (
              <div className="muted small">로그가 비어 있습니다.</div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
