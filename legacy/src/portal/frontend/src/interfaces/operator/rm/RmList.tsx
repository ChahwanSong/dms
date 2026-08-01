import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { operatorApi, type RmJob } from "../../../api";
import JobDetailModal from "../components/JobDetailModal";
import { errMsg } from "../backup/BackupBatches";
import {
  RM_STATE_LABEL,
  RM_TERMINAL,
  rmStateTone,
  fmtAgo,
  fmtBytes,
  fmtTime,
} from "./helpers";

const PAGE = 100;

function target(j: RmJob): string {
  return `${j.target_storage}:${j.target_path}`;
}

// 요청된 삭제 작업 리스트 (탭 하단). Infinite scroll (offset paging + Intersection
// Observer); a periodic refresh of the first page keeps in-flight job states live.
// Single-shot: NO re-run. Actions: 실행(preview_ready) · 취소(in-flight) · 상세 · 삭제.
// Reuses the shared `.sync-*` DM-job table styles (same structure as the sync list;
// keeps the just-fixed mobile card layout consistent).
export default function RmList({ reloadKey }: { reloadKey: number }) {
  const [rows, setRows] = useState<RmJob[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [reachedEnd, setReachedEnd] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [detailJob, setDetailJob] = useState<RmJob | null>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const loadingRef = useRef(false);
  const seqRef = useRef(0);

  // reset + first page (on mount and whenever a new job is submitted).
  useEffect(() => {
    seqRef.current += 1;
    const seq = seqRef.current;
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    setReachedEnd(false);
    operatorApi.rm
      .list({ offset: 0, limit: PAGE })
      .then((r) => {
        if (seq !== seqRef.current) return;
        setRows(r.items);
        setTotal(r.total);
        setReachedEnd(r.items.length < PAGE);
      })
      .catch((e) => {
        if (seq !== seqRef.current) return;
        setRows([]);
        setError(errMsg(e));
        setReachedEnd(true);
      })
      .finally(() => {
        if (seq === seqRef.current) {
          loadingRef.current = false;
          setLoading(false);
        }
      });
  }, [reloadKey]);

  const loadMore = useCallback(() => {
    if (loadingRef.current || reachedEnd) return;
    const seq = seqRef.current;
    loadingRef.current = true;
    setLoading(true);
    operatorApi.rm
      .list({ offset: rows.length, limit: PAGE })
      .then((r) => {
        if (seq !== seqRef.current) return;
        setRows((prev) => {
          const seen = new Set(prev.map((x) => x.id));
          return [...prev, ...r.items.filter((x) => !seen.has(x.id))];
        });
        setReachedEnd(r.items.length < PAGE);
      })
      .catch(() => { if (seq === seqRef.current) setReachedEnd(true); })
      .finally(() => {
        if (seq === seqRef.current) {
          loadingRef.current = false;
          setLoading(false);
        }
      });
  }, [rows.length, reachedEnd]);

  // infinite scroll: auto-load the next page as the sentinel nears the viewport.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || reachedEnd) return;
    const io = new IntersectionObserver(
      (entries) => { if (entries[0]?.isIntersecting) loadMore(); },
      { rootMargin: "400px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [loadMore, reachedEnd]);

  // poll the first page while any loaded job is non-terminal, merging fresh state
  // into the loaded rows (in-flight jobs are newest -> page 1).
  const anyInflight = rows.some((r) => !RM_TERMINAL.has(r.state));
  useEffect(() => {
    if (!anyInflight) return;
    const t = setInterval(() => {
      operatorApi.rm
        .list({ offset: 0, limit: PAGE })
        .then((r) => {
          const byId = new Map(r.items.map((x) => [x.id, x]));
          setRows((prev) => prev.map((row) => byId.get(row.id) ?? row));
          setTotal(r.total);
        })
        .catch(() => {});
    }, 4000);
    return () => clearInterval(t);
  }, [anyInflight]);

  async function act(id: number, fn: () => Promise<unknown>, after: "refresh" | "drop") {
    setBusyId(id);
    setError(null);
    try {
      await fn();
      if (after === "drop") {
        setRows((prev) => prev.filter((r) => r.id !== id));
        setTotal((t) => (t == null ? t : Math.max(0, t - 1)));
      } else {
        const fresh = await operatorApi.rm.get(id);
        setRows((prev) => prev.map((r) => (r.id === id ? fresh : r)));
      }
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusyId(null);
    }
  }

  const approve = (j: RmJob) => {
    const pv = j.preview;
    const n = pv?.files != null ? `${pv.files.toLocaleString()}개 파일` : "대상";
    if (
      !window.confirm(
        `정말 실행합니까?\nPreview에서 확인된 ${n}이(가) «${target(j)}»에서 영구 삭제되며 되돌릴 수 없습니다.`,
      )
    )
      return;
    act(j.id, () => operatorApi.rm.approve(j.id), "refresh");
  };
  const cancel = (j: RmJob) => {
    if (!window.confirm("이 삭제 작업을 취소할까요?")) return;
    act(j.id, () => operatorApi.rm.cancel(j.id), "refresh");
  };
  const remove = (j: RmJob) => {
    if (!window.confirm("이 삭제 작업 기록을 삭제할까요?")) return;
    act(j.id, () => operatorApi.rm.remove(j.id), "drop");
  };

  const shown = rows.length;

  return (
    <div className="sync-list">
      <section className="ui-card">
        <div className="ui-card-hd">
          <h3>
            요청된 작업
            <span className="hd-cnt">
              {shown}
              {total != null && total > shown ? ` / ${total}` : ""}
            </span>
          </h3>
        </div>
        <div className="ui-card-bd">
          <div className="ui-card-div" />
      {error && <div className="banner err">{error}</div>}

      <table className="grid sync-grid">
        <thead>
          <tr>
            <th>대상</th>
            <th>상태</th>
            <th>정보</th>
            <th>시각</th>
            <th>조치</th>
          </tr>
        </thead>
        <tbody>
          {shown === 0 && !loading ? (
            <tr>
              <td colSpan={5} className="muted reqa-empty">
                {error ? "불러오지 못했습니다." : "요청된 삭제 작업이 없습니다."}
              </td>
            </tr>
          ) : (
            rows.map((j) => {
              const busy = busyId === j.id;
              const pv = j.preview;
              return (
                <Fragment key={j.id}>
                  <tr className={RM_TERMINAL.has(j.state) ? "" : "sync-live"}>
                    <td data-label="대상" className="mono small sync-target" title={target(j)}>
                      {target(j)}
                      {j.memo && <span className="muted small sync-memo"> · {j.memo}</span>}
                    </td>
                    <td data-label="상태">
                      <span className={`reqa-badge ${rmStateTone(j.state)}`}>
                        {RM_STATE_LABEL[j.state] || j.state}
                      </span>
                    </td>
                    <td data-label="정보" className="small sync-info">
                      {j.state === "preview_ready" && pv ? (
                        pv.target_absent ? (
                          <span className="muted">대상 없음 (삭제할 것 없음)</span>
                        ) : (
                          <span className="err-num">
                            삭제 예정 {pv.files != null ? `${pv.files.toLocaleString()}개` : "—"}
                            {pv.bytes != null ? ` · ${fmtBytes(pv.bytes)}` : ""}
                          </span>
                        )
                      ) : j.error ? (
                        <span className="err-num" title={j.error}>{j.error}</span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td data-label="시각" className="muted small sync-when" title={fmtTime(j.created_at)}>
                      {fmtAgo(j.created_at)}
                    </td>
                    <td data-label="조치" className="sync-actions">
                      {j.state === "preview_ready" && (
                        <button className="mini danger" disabled={busy} onClick={() => approve(j)}>
                          실행
                        </button>
                      )}
                      {!RM_TERMINAL.has(j.state) && (
                        <button className="mini" disabled={busy} onClick={() => cancel(j)}>
                          취소
                        </button>
                      )}
                      <button className="ghost mini" disabled={busy} onClick={() => setDetailJob(j)}>
                        상세
                      </button>
                      {RM_TERMINAL.has(j.state) && (
                        <button className="mini danger" disabled={busy} onClick={() => remove(j)}>
                          삭제
                        </button>
                      )}
                    </td>
                  </tr>
                </Fragment>
              );
            })
          )}
        </tbody>
      </table>

      {/* infinite-scroll footer */}
      {shown > 0 && (
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
            <span className="muted small">전체 {shown}건 표시 · 끝</span>
          )}
        </div>
      )}
        </div>
      </section>

      {detailJob && (
        <JobDetailModal kind="rm" request={detailJob} onClose={() => setDetailJob(null)} />
      )}
    </div>
  );
}
