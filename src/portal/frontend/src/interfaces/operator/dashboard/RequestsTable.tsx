import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { operatorApi, type RequestActivity, type RequestDetail } from "../../../api";
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

// ---- expanded detail rendering ----
function fmtVal(v: unknown): ReactNode {
  if (v == null || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
function Kv({ label, v, mono, span, cls }: {
  label: string; v: unknown; mono?: boolean; span?: boolean; cls?: string;
}) {
  if (v == null || v === "") return null;
  return (
    <div className={`spec-kv${span ? " span" : ""}`}>
      <dt>{label}</dt>
      <dd className={[mono ? "mono" : "", cls || ""].join(" ").trim() || undefined}>{fmtVal(v)}</dd>
    </div>
  );
}

function RequestDetailView({ d }: { d: RequestDetail }) {
  const req = asRec(d.request);
  const plan = asRec(d.plan);
  const results = d.results || [];
  const transitions = d.transitions || [];
  return (
    <div className="req-detail">
      <dl className="spec-grid">
        <Kv label="요청 ID" v={req.request_id} mono />
        <Kv label="작업" v={req.operation} />
        <Kv label="리소스 종류" v={req.resource_kind} />
        <Kv label="상태" v={req.status} cls={statusClass(str(req.status) || "")} />
        <Kv label="요청자" v={req.requester_id} />
        <Kv label="실행자" v={req.actor} />
        <Kv label="우선순위" v={req.priority} />
        <Kv label="요청 시각" v={str(req.requested_at) ? fmtTime(str(req.requested_at)) : null} />
        <Kv label="갱신" v={str(req.updated_at) ? fmtTime(str(req.updated_at)) : null} />
        {str(req.source_request_id) && <Kv label="출처 요청" v={req.source_request_id} mono />}
        {str(plan.status) && <Kv label="플랜 상태" v={plan.status} />}
        <Kv label="리소스 키" v={req.resource_key} mono span />
        <Kv label="요청 내용" v={req.payload_summary} mono span />
      </dl>

      {results.length > 0 && (
        <div className="req-block">
          <div className="req-block-h">결과</div>
          {results.map((raw, i) => {
            const r = asRec(raw);
            const vs = asRec(r.verification_summary);
            return (
              <div key={i} className="req-result-line small">
                <b className={statusClass(str(r.terminal_status) || "")}>{str(r.terminal_status) || "—"}</b>
                {str(r.message) && <span className="muted"> · {str(r.message)}</span>}
                {str(vs.reason) && <span className="muted"> · 사유: {str(vs.reason)}</span>}
                {str(r.error_category) && <span className="muted"> · {str(r.error_category)}</span>}
              </div>
            );
          })}
        </div>
      )}

      {transitions.length > 0 && (
        <div className="req-block">
          <div className="req-block-h">상태 변화 <span className="muted small">({transitions.length})</span></div>
          <ul className="req-tl">
            {transitions.slice(-15).map((t, i) => (
              <li key={i}>
                <span className="req-tl-state mono small">
                  {t.from_state || "·"} → <b className={statusClass(t.to_state || "")}>{t.to_state}</b>
                </span>
                {t.reason && <span className="muted small req-tl-reason">{t.reason}</span>}
                <span className="muted small req-tl-t" title={fmtTime(t.created_at || undefined)}>
                  {fmtAgo(t.created_at || undefined)}{t.actor ? ` · ${t.actor}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
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
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [details, setDetails] = useState<Record<string, RequestDetail | "loading" | "error">>({});
  const focusRef = useRef<HTMLTableRowElement | null>(null);

  useEffect(() => {
    operatorApi.dashboard
      .requestActivity({ resource_kind: rkind || undefined, status: status || undefined, limit: LIMIT })
      .then((r) => { setRows(r.requests); setTruncated(r.truncated); })
      .catch(() => { setRows([]); setTruncated(false); });
  }, [rkind, status]);

  const loadDetail = (id: string) => {
    setDetails((prev) => (prev[id] && prev[id] !== "error" ? prev : { ...prev, [id]: "loading" }));
    operatorApi.dashboard.requestDetail(id)
      .then((d) => setDetails((p) => ({ ...p, [id]: d })))
      .catch(() => setDetails((p) => ({ ...p, [id]: "error" })));
  };
  const toggle = (id: string) => {
    const willOpen = !expanded.has(id);
    setExpanded((prev) => {
      const n = new Set(prev);
      willOpen ? n.add(id) : n.delete(id);
      return n;
    });
    if (willOpen && (!details[id] || details[id] === "error")) loadDetail(id);
  };

  // client-side text search across requester / resource_key / request_id / target.
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((r) =>
      [r.requester_id, r.resource_key, r.request_id, targetOf(r)]
        .some((v) => (v || "").toLowerCase().includes(needle)),
    );
  }, [rows, q]);

  // deep-link: scroll to + auto-expand the focused request.
  useEffect(() => {
    if (!focusRequestId) return;
    if (focusRef.current) focusRef.current.scrollIntoView({ block: "center" });
    if (!expanded.has(focusRequestId) && rows.some((r) => r.request_id === focusRequestId)) {
      setExpanded((prev) => new Set(prev).add(focusRequestId));
      if (!details[focusRequestId]) loadDetail(focusRequestId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
        <span className="muted small">행을 클릭하면 상세가 열립니다</span>
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
            const isOpen = expanded.has(r.request_id);
            const det = details[r.request_id];
            return (
              <Fragment key={r.request_id}>
                <tr ref={focused ? focusRef : undefined}
                  className={`req-row${focused ? " row-focus" : ""}${isOpen ? " req-open" : ""}`}
                  onClick={() => toggle(r.request_id)}>
                  <td data-label="종류">
                    <span className="req-caret" aria-hidden>{isOpen ? "▾" : "▸"}</span>
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
                      <button className="mini" onClick={(e) => { e.stopPropagation(); onNavigate("dashboard-attention"); }}
                        title="조치 필요에서 진단·조치">조치 필요 →</button>
                    ) : <span className="muted small">—</span>}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="detail-row">
                    <td colSpan={6}>
                      {det === "loading" ? <span className="muted small">상세 불러오는 중…</span>
                        : det === "error" ? <span className="err-num small">상세를 불러오지 못했습니다.</span>
                          : det ? <RequestDetailView d={det} /> : null}
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
      </tbody></table>
    </Section>
  );
}
