import { useEffect, useMemo, useRef, useState } from "react";
import { operatorApi, type RequestActivity } from "../../../api";
import { fmtAgo, fmtTime } from "./helpers";
import Section from "./Section";

// resource_kind filter (server-side) + friendly labels.
const RKINDS = ["", "filesystem", "kubernetes_namespace_quota", "data_job", "identity"] as const;
const RKIND_LABEL: Record<string, string> = {
  filesystem: "파일시스템", kubernetes_namespace_quota: "쿼터",
  data_job: "데이터 잡", identity: "identity",
};
// lifecycle statuses worth filtering by (server-side, exact).
const STATUSES = [
  "", "Running", "Blocked", "Conflict", "StaleClaim", "RecoveryNeeded",
  "Succeeded", "Failed", "Cancelled", "UnknownAfterSideEffect", "BackendApplyFailed",
];
// attention (needs-action) vs failed vs done — for status coloring.
const STUCK = new Set([
  "Blocked", "Conflict", "StaleClaim", "RecoveryNeeded",
  "UnknownAfterSideEffect", "BackendApplyFailed", "VerificationFailed",
]);
const FAIL = new Set(["Failed", "Rejected", "TimedOut", "AuthenticationRejected", "AuthorizationFailed"]);
// Data jobs are growing history — capped page + truncated flag (never fetch-all).
const LIMIT = 500;

function statusClass(s: string): string {
  if (s === "Succeeded") return "ok-num";
  if (FAIL.has(s)) return "err-num";
  if (STUCK.has(s)) return "tone-warn-text";
  return "";
}

function str(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}
function asRec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}
// A concise "what this request is about" from the payload_summary / resource_key.
function targetOf(r: RequestActivity): string {
  const p = asRec(r.payload_summary);
  const src = asRec(p.source), dst = asRec(p.destination), tgt = asRec(p.target);
  const sp = str(src.path), dp = str(dst.path);
  if (sp || dp) return `${sp ?? "?"} → ${dp ?? "?"}`;
  if (str(tgt.path)) return `${str(tgt.storage_name) ? str(tgt.storage_name) + ":" : ""}${str(tgt.path)}`;
  const ns = str(p.namespace_name) || str(p.namespace);
  if (ns) return `${str(p.cluster_name) ? str(p.cluster_name) + "/" : ""}${ns}`;
  const st = str(p.storage_name), dir = str(p.directory_name) || str(p.path);
  if (st && dir) return `${st}:${dir}`;
  return str(r.resource_key) || "—";
}

export default function RequestsTable({ defaultOpen = false, focusRequestId, onNavigate }: {
  defaultOpen?: boolean;
  focusRequestId?: string;
  onNavigate?: (section: string) => void;
}) {
  const [rows, setRows] = useState<RequestActivity[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [rkind, setRkind] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const focusRef = useRef<HTMLTableRowElement | null>(null);

  useEffect(() => {
    operatorApi.dashboard
      .requestActivity({ resource_kind: rkind || undefined, status: status || undefined, limit: LIMIT })
      .then((r) => { setRows(r.requests); setTruncated(r.truncated); })
      .catch(() => { setRows([]); setTruncated(false); });
  }, [rkind, status]);

  // client-side text search across requester / resource_key / request_id / target.
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((r) =>
      [r.requester_id, r.resource_key, r.request_id, targetOf(r)]
        .some((v) => (v || "").toLowerCase().includes(needle)),
    );
  }, [rows, q]);

  useEffect(() => {
    if (focusRequestId && focusRef.current) focusRef.current.scrollIntoView({ block: "center" });
  }, [focusRequestId, filtered]);

  const stuckCount = filtered.filter((r) => STUCK.has(r.status)).length;
  const badge = (
    <span className="muted small">
      (표시 {filtered.length})
      {stuckCount > 0 && <>{" "}<span className="tone-warn-text">· 정체 {stuckCount}</span></>}
      {truncated && <>{" "}<span className="chip tone-warn">일부만 표시</span></>}
    </span>
  );

  return (
    <Section title="요청 (전체)" badge={badge} defaultOpen={defaultOpen}>
      <div className="inv-actions dash-filters">
        <select value={rkind} onChange={(e) => setRkind(e.target.value)} title="리소스 종류">
          {RKINDS.map((k) => <option key={k} value={k}>{k ? RKIND_LABEL[k] : "모든 종류"}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} title="상태">
          {STATUSES.map((s) => <option key={s} value={s}>{s || "모든 상태"}</option>)}
        </select>
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="요청자 / 경로 / ID 검색" />
      </div>
      {truncated && (
        <div className="muted small">최신 {rows.length}건만 표시 — 종류/상태/검색으로 좁혀 보세요.</div>
      )}
      <table className="grid"><thead><tr>
        <th>종류</th><th>상태</th><th>요청자</th><th>대상</th><th>시각</th><th>조치</th>
      </tr></thead><tbody>
        {filtered.length === 0 ? <tr><td colSpan={6} className="muted">요청 없음</td></tr> :
          filtered.map((r) => {
            const focused = !!focusRequestId && r.request_id === focusRequestId;
            const stuck = STUCK.has(r.status);
            return (
              <tr key={r.request_id} ref={focused ? focusRef : undefined}
                className={focused ? "row-focus" : undefined}>
                <td data-label="종류">
                  <span className="mono small">{r.operation}</span>
                  {r.resource_kind && (
                    <span className="muted small"> · {RKIND_LABEL[r.resource_kind] || r.resource_kind}</span>
                  )}
                </td>
                <td data-label="상태"><b className={statusClass(r.status)}>{r.status}</b></td>
                <td data-label="요청자" className="small">{r.requester_id || "—"}</td>
                <td data-label="대상" className="mono small req-target" title={targetOf(r)}>{targetOf(r)}</td>
                <td data-label="시각" className="muted small" title={fmtTime(r.requested_at || undefined)}>
                  {fmtAgo(r.requested_at || undefined)}
                </td>
                <td data-label="조치">
                  {stuck && onNavigate ? (
                    <button className="mini" onClick={() => onNavigate("dashboard-attention")}
                      title="조치 필요에서 진단·조치">조치 필요 →</button>
                  ) : <span className="muted small">—</span>}
                </td>
              </tr>
            );
          })}
      </tbody></table>
    </Section>
  );
}
