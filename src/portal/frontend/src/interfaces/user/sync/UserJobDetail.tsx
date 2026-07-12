import { useCallback, useEffect, useState } from "react";
import { userSyncApi, type JobDetail, type JobLogs, type SyncJob } from "../../../api";
import { SpecGrid, type KV } from "../../../components/SpecGrid";
import { fmtTime } from "../../../lib/format";
import { fmtBytes } from "./helpers";

// 사용자 Sync 작업 상세/로그 (읽기 전용). 운영자 JobDetailModal의 축약판 —
// userSyncApi만 호출하며 사용자 인터페이스에 독립적이다. DMS 잡이 비종료 상태인
// 동안 4초마다 폴링한다.
const DMS_TERMINAL = new Set([
  "Succeeded",
  "Failed",
  "PreflightFailed",
  "PreviewExpired",
  "Cancelled",
  "TimedOut",
]);
const IN_FLIGHT_PORTAL = new Set(["preview_pending", "preview_ready", "running"]);

const SUMMARY_LABELS: Record<string, string> = {
  file_count: "파일",
  directory_count: "디렉터리",
  total_bytes: "크기",
  error_count: "오류",
  selected_tool: "도구",
  scan_root: "scan_root",
  operation: "작업",
  dry_run: "dry-run",
};

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

function pickSummary(rs: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!rs) return null;
  const exec = asRecord(rs.execution);
  const prev = asRecord(rs.preview);
  return (
    asRecord(rs.summary) ??
    asRecord(exec?.summary) ??
    asRecord(prev?.summary) ??
    rs
  );
}

function summaryItems(summary: Record<string, unknown>): KV[] {
  const items: KV[] = [];
  for (const [k, v] of Object.entries(summary)) {
    if (v == null || typeof v === "object") continue;
    const label = SUMMARY_LABELS[k] ?? k;
    let value: string;
    if (typeof v === "boolean") value = v ? "켜짐" : "꺼짐";
    else if (typeof v === "number")
      value = k.endsWith("_bytes") ? fmtBytes(v) : v.toLocaleString();
    else value = String(v);
    items.push({ label, value });
  }
  return items;
}

export default function UserJobDetail({
  job,
  onClose,
}: {
  job: SyncJob;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [logs, setLogs] = useState<JobLogs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [d, l] = await Promise.all([
        userSyncApi.job(job.id),
        userSyncApi.logs(job.id),
      ]);
      setDetail(d);
      setLogs(l);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [job.id]);

  useEffect(() => {
    load();
  }, [load]);

  const liveState = typeof detail?.state === "string" ? detail.state : null;
  const polling = liveState
    ? !DMS_TERMINAL.has(liveState)
    : IN_FLIGHT_PORTAL.has(job.state);
  useEffect(() => {
    if (!polling) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [polling, load]);

  const subtitle = `${job.src_storage}:${job.src_path} → ${job.dst_storage}:${job.dst_path}`;

  const live: KV[] = [];
  live.push({ label: "상태 (live)", value: liveState ?? job.state, mono: true });
  if (detail?.selected_tool) live.push({ label: "도구", value: detail.selected_tool });
  if (job.dms_job_id) live.push({ label: "job id", value: job.dms_job_id, mono: true, span: true });
  if (detail?.created_at) live.push({ label: "생성", value: fmtTime(detail.created_at) });
  if (detail?.started_at) live.push({ label: "시작", value: fmtTime(detail.started_at) });
  if (detail?.finished_at) live.push({ label: "종료", value: fmtTime(detail.finished_at) });

  const preflight = asRecord(detail?.preflight_result);
  const preflightStatus =
    preflight && typeof preflight.status === "string" ? preflight.status : null;
  const preflightReason =
    preflight && typeof preflight.reason === "string" ? preflight.reason : null;
  const preflightPassed =
    preflightStatus === "Ready" ||
    (preflightStatus == null && !!preflightReason && preflightReason.endsWith("_passed"));

  const rs = asRecord(detail?.result_summary);
  const summary = pickSummary(rs);
  const summaryKv = summary ? summaryItems(summary) : [];
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

          <section className="job-sec">
            <h4>로그 (launcher pod tail)</h4>
            {logs?.pods && logs.pods.length > 0 && (
              <div className="job-pods">
                {logs.pods.map((p, i) => (
                  <span key={i} className="chip" title={p.node_name || undefined}>
                    {p.name || "pod"}
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
