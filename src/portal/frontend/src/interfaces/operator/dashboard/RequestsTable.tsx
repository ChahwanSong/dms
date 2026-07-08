import {
  Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode,
} from "react";
import { operatorApi, type RequestActivity, type RequestDetail } from "../../../api";
import { fmtAgo, fmtTime } from "./helpers";
import Section from "./Section";
import StatusPill from "./StatusPill";
import { isStuck, lifecycleLabel, lifecycleTextClass } from "./statusMeta";

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
// GROWING history — never fetch-all. Load one page, then more on scroll (infinite).
// lifecycle status → Korean label + tone + pill live in ./statusMeta / ./StatusPill.
const PAGE = 500;

// Wrap occurrences of `needle` (case-insensitive) in <mark> — shows the operator
// exactly WHERE their query matched (requester or target), reinforcing that both
// are searched server-side.
function highlight(text: string, needle: string): ReactNode {
  const n = needle.trim().toLowerCase();
  if (!n || !text) return text;
  const lower = text.toLowerCase();
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;
  for (;;) {
    const idx = lower.indexOf(n, i);
    if (idx < 0) { out.push(text.slice(i)); break; }
    if (idx > i) out.push(text.slice(i, idx));
    out.push(<mark key={key++} className="reqa-hl">{text.slice(idx, idx + n.length)}</mark>);
    i = idx + n.length;
  }
  return out;
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
    <div className="reqa-detail">
      <dl className="spec-grid">
        <Kv label="요청 ID" v={req.request_id} mono />
        <Kv label="작업" v={req.operation} />
        <Kv label="리소스 종류" v={req.resource_kind} />
        <Kv label="상태" v={lifecycleLabel(str(req.status))} cls={lifecycleTextClass(str(req.status) || "")} />
        <Kv label="요청자" v={req.requester_id} />
        <Kv label="실행자" v={req.actor} />
        <Kv label="우선순위" v={req.priority} />
        <Kv label="요청 시각" v={str(req.requested_at) ? fmtTime(str(req.requested_at)) : null} />
        <Kv label="갱신" v={str(req.updated_at) ? fmtTime(str(req.updated_at)) : null} />
        {str(req.source_request_id) && <Kv label="출처 요청" v={req.source_request_id} mono />}
        {str(plan.status) && <Kv label="플랜 상태" v={lifecycleLabel(str(plan.status))} />}
        <Kv label="리소스 키" v={req.resource_key} mono span />
        <Kv label="요청 내용" v={req.payload_summary} mono span />
      </dl>

      {results.length > 0 && (
        <div className="reqa-block">
          <div className="reqa-block-h">결과</div>
          {results.map((raw, i) => {
            const r = asRec(raw);
            const vs = asRec(r.verification_summary);
            return (
              <div key={i} className="reqa-result small">
                <b className={lifecycleTextClass(str(r.terminal_status) || "")}>{lifecycleLabel(str(r.terminal_status)) || "—"}</b>
                {str(r.message) && <span className="muted"> · {str(r.message)}</span>}
                {str(vs.reason) && <span className="muted"> · 사유: {str(vs.reason)}</span>}
                {str(r.error_category) && <span className="muted"> · {str(r.error_category)}</span>}
              </div>
            );
          })}
        </div>
      )}

      {transitions.length > 0 && (
        <div className="reqa-block">
          <div className="reqa-block-h">상태 변화 <span className="muted small">({transitions.length})</span></div>
          <ul className="reqa-tl">
            {transitions.slice(-15).map((t, i) => (
              <li key={i}>
                <span className="reqa-tl-state mono small">
                  {lifecycleLabel(t.from_state) || "·"} → <b className={lifecycleTextClass(t.to_state || "")}>{lifecycleLabel(t.to_state)}</b>
                </span>
                {t.reason && <span className="muted small reqa-tl-reason">{t.reason}</span>}
                <span className="muted small reqa-tl-t" title={fmtTime(t.created_at || undefined)}>
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
  const [loading, setLoading] = useState(false);   // any fetch in flight
  const [reachedEnd, setReachedEnd] = useState(false);
  const [error, setError] = useState(false);
  const [rkind, setRkind] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");                  // live search box value
  const [debouncedQ, setDebouncedQ] = useState(""); // committed to the server
  const [showHidden, setShowHidden] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [details, setDetails] = useState<Record<string, RequestDetail | "loading" | "error">>({});
  const focusRef = useRef<HTMLTableRowElement | null>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  // seq invalidates in-flight pages when filters/search change; loadingRef gates
  // concurrent fetches (first-page effect + scroll-triggered loadMore).
  const seqRef = useRef(0);
  const loadingRef = useRef(false);

  // debounce the search box → only hit the server after typing settles.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  // filters/search changed → reset to the first page. GROWING history: server-side
  // search spans ALL requests (requester + target), newest-first, PAGE at a time.
  useEffect(() => {
    seqRef.current += 1;
    const seq = seqRef.current;
    loadingRef.current = true;
    setLoading(true);
    setError(false);
    setReachedEnd(false);
    operatorApi.dashboard
      .requestActivity({
        resource_kind: rkind || undefined,
        status: status || undefined,
        search: debouncedQ || undefined,
        limit: PAGE,
        offset: 0,
      })
      .then((r) => {
        if (seq !== seqRef.current) return;
        setRows(r.requests);
        setReachedEnd(r.requests.length < PAGE);
      })
      .catch(() => {
        if (seq !== seqRef.current) return;
        setRows([]); setError(true); setReachedEnd(true);
      })
      .finally(() => {
        if (seq === seqRef.current) { loadingRef.current = false; setLoading(false); }
      });
  }, [rkind, status, debouncedQ]);

  // append the next page (offset = current row count). De-dupe by request_id to be
  // safe if new requests shifted the window between pages.
  const loadMore = useCallback(() => {
    if (loadingRef.current || reachedEnd) return;
    const seq = seqRef.current;
    loadingRef.current = true;
    setLoading(true);
    operatorApi.dashboard
      .requestActivity({
        resource_kind: rkind || undefined,
        status: status || undefined,
        search: debouncedQ || undefined,
        limit: PAGE,
        offset: rows.length,
      })
      .then((r) => {
        if (seq !== seqRef.current) return;
        setRows((prev) => {
          const seen = new Set(prev.map((x) => x.request_id));
          return [...prev, ...r.requests.filter((x) => !seen.has(x.request_id))];
        });
        setReachedEnd(r.requests.length < PAGE);
      })
      .catch(() => { if (seq === seqRef.current) setReachedEnd(true); })
      .finally(() => {
        if (seq === seqRef.current) { loadingRef.current = false; setLoading(false); }
      });
  }, [rows.length, rkind, status, debouncedQ, reachedEnd]);

  // infinite scroll: auto-load the next page as the sentinel nears the viewport.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || reachedEnd) return;
    const io = new IntersectionObserver(
      (entries) => { if (entries[0]?.isIntersecting) loadMore(); },
      { rootMargin: "500px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [loadMore, reachedEnd]);

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

  // requests hidden via 조치 필요 (dismiss/ack) — default-hidden here too for
  // consistency, revealed by the toggle. A hidden focus target is always shown.
  const hiddenInView = rows.filter((r) => r._hidden).length;
  const visible = useMemo(
    () => (showHidden ? rows : rows.filter((r) => !r._hidden || r.request_id === focusRequestId)),
    [rows, showHidden, focusRequestId],
  );

  // deep-link: scroll to + auto-expand the focused request.
  useEffect(() => {
    if (!focusRequestId) return;
    if (focusRef.current) focusRef.current.scrollIntoView({ block: "center" });
    if (!expanded.has(focusRequestId) && rows.some((r) => r.request_id === focusRequestId)) {
      setExpanded((prev) => new Set(prev).add(focusRequestId));
      if (!details[focusRequestId]) loadDetail(focusRequestId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusRequestId, rows]);

  const stuckCount = visible.filter((r) => isStuck(r.status)).length;
  const badge = (
    <span className="muted small">
      (표시 {visible.length}{reachedEnd ? "" : "+"})
      {stuckCount > 0 && <>{" "}<span className="tone-warn-text">· 정체 {stuckCount}</span></>}
    </span>
  );
  const initialLoading = loading && rows.length === 0;

  return (
    <Section title="요청 (전체)" badge={badge} defaultOpen={defaultOpen}>
      <div className="reqa-toolbar">
        <div className="reqa-search">
          <svg className="reqa-search-ic" viewBox="0 0 24 24" width="15" height="15" aria-hidden>
            <path fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
              d="M10.5 3a7.5 7.5 0 1 0 4.55 13.46l4.24 4.25 1.42-1.42-4.25-4.24A7.5 7.5 0 0 0 10.5 3Z" />
          </svg>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="요청자 · 대상(경로/스토리지) · ID 검색" aria-label="요청 검색" />
          {q && (
            <button className="reqa-search-x" onClick={() => setQ("")} title="지우기" aria-label="검색어 지우기">×</button>
          )}
        </div>
        <select className="reqa-filter" value={rkind} onChange={(e) => setRkind(e.target.value)} title="리소스 종류">
          {RKINDS.map((k) => <option key={k} value={k}>{k ? RKIND_LABEL[k] : "모든 종류"}</option>)}
        </select>
        <select className="reqa-filter" value={status} onChange={(e) => setStatus(e.target.value)} title="상태">
          {STATUSES.map((s) => <option key={s} value={s}>{s ? lifecycleLabel(s) : "모든 상태"}</option>)}
        </select>
        {hiddenInView > 0 && (
          <button className={`reqa-toggle${showHidden ? " on" : ""}`} onClick={() => setShowHidden((v) => !v)}
            title="조치 필요에서 숨김/확인한 요청 — 여기서도 기본 가림">
            {showHidden ? `숨긴 요청 가리기 (${hiddenInView})` : `숨긴 요청 보기 (${hiddenInView})`}
          </button>
        )}
        <span className="reqa-toolbar-sp" />
        <span className="reqa-count muted small">
          {loading && <span className="reqa-spin" aria-hidden />}
          표시 {visible.length}건{!reachedEnd && rows.length > 0 ? " +" : ""}
        </span>
      </div>

      <table className="grid reqa-grid"><thead><tr>
        <th>종류</th><th>상태</th><th>요청자</th><th>대상</th><th>시각</th><th>조치</th>
      </tr></thead><tbody>
        {initialLoading ? (
          <tr><td colSpan={6} className="muted reqa-empty">불러오는 중…</td></tr>
        ) : visible.length === 0 ? (
          <tr><td colSpan={6} className="muted reqa-empty">
            {error ? "요청을 불러오지 못했습니다." : debouncedQ ? `“${debouncedQ}”에 해당하는 요청 없음` : "요청 없음"}
          </td></tr>
        ) : (
          visible.map((r) => {
            const focused = !!focusRequestId && r.request_id === focusRequestId;
            const stuck = isStuck(r.status);
            const isOpen = expanded.has(r.request_id);
            const det = details[r.request_id];
            const tgt = targetOf(r);
            return (
              <Fragment key={r.request_id}>
                <tr ref={focused ? focusRef : undefined}
                  className={`reqa-row${focused ? " row-focus" : ""}${isOpen ? " reqa-open" : ""}${r._hidden ? " reqa-hidden" : ""}`}
                  onClick={() => toggle(r.request_id)}>
                  <td data-label="종류">
                    <span className="reqa-caret" aria-hidden>{isOpen ? "▾" : "▸"}</span>
                    <span className="reqa-op mono">{r.operation}</span>
                    {r.resource_kind && (
                      <span className="reqa-rk">{RKIND_LABEL[r.resource_kind] || r.resource_kind}</span>
                    )}
                    {r._hidden && <span className="chip tone-low reqa-hidden-chip" title="조치 필요에서 숨김/확인됨">숨김</span>}
                  </td>
                  <td data-label="상태">
                    <StatusPill state={r.status} />
                  </td>
                  <td data-label="요청자" className="reqa-requester small">
                    {r.requester_id ? highlight(r.requester_id, debouncedQ) : "—"}
                  </td>
                  <td data-label="대상" className="mono small req-target" title={tgt}>
                    {highlight(tgt, debouncedQ)}
                  </td>
                  <td data-label="시각" className="muted small reqa-when" title={fmtTime(r.requested_at || undefined)}>
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
          })
        )}
      </tbody></table>

      {/* infinite-scroll footer: sentinel (auto-load) + manual fallback + end marker.
          Keyed on rows (not visible) so paging continues even if a page is all-hidden. */}
      {!initialLoading && rows.length > 0 && (
        <div className="reqa-foot">
          {!reachedEnd ? (
            <>
              <div ref={sentinelRef} className="reqa-sentinel" aria-hidden />
              {loading ? (
                <span className="muted small"><span className="reqa-spin" aria-hidden /> 더 불러오는 중…</span>
              ) : (
                <button className="reqa-more-btn" onClick={loadMore}>더 불러오기 (+{PAGE})</button>
              )}
            </>
          ) : (
            <span className="muted small">전체 {visible.length}건 표시 · 끝</span>
          )}
        </div>
      )}
    </Section>
  );
}
